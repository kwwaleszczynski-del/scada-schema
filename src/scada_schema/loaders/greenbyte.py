"""Greenbyte exports: the Kelmarsh and Penmanshiel open datasets (Cubico Sustainable Investments, CC BY 4.0).

One zip per year (Penmanshiel: per year and turbine group), each with a ``Turbine_Data_*.csv`` (10-minute averages,
UTC, ``# Date and time`` marks the start of the interval) and a ``Status_*.csv`` (event log with the operator's IEC
61400-26 category) per turbine. Columns follow the Greenbyte titles: ``Power (kW)``, ``Wind speed, Standard
deviation (m/s)`` and so on; the minimum, maximum and standard deviation columns become ``__min``, ``__max`` and
``__std`` columns of the same signal. Everything is already in the canonical units; the pitch angle is the mean of the
three blades.

Columns of the export that are not in the schema (lost-production allocations, potential power, availability
indices, hub temperature ...) are left out on purpose: they are results computed by the operator's system, not
measurements, and the schema holds measurements and the state log.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import pandas as pd

from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, make_events
from scada_schema.model import DatasetMeta, Device, Source
from scada_schema.states import map_labels

__all__ = ["GREENBYTE_COLUMNS", "parse_status_csv", "parse_turbine_csv", "read_static", "read_zip"]

log = logging.getLogger(__name__)

TIME_COL = "# Date and time"
# (signal, Greenbyte title, unit suffix of the mean column or None when the title carries none)
GREENBYTE_COLUMNS: list[tuple[str, str, str | None]] = [
    ("p_ac", "Power", "kW"),
    ("q_ac", "Reactive power", "kvar"),
    ("power_factor", "Power factor (cosphi)", None),
    ("e_export", "Energy Export", "kWh"),
    ("e_export_counter", "Energy Export counter", "kWh"),
    ("setpoint", "Turbine Power setpoint", "kW"),
    ("wind_speed", "Wind speed", "m/s"),
    ("wind_speed_2", "Wind speed Sensor 2", "m/s"),
    ("wind_dir", "Wind direction", "°"),
    ("wind_dir_rel", "Vane position 1+2", "°"),
    ("nacelle_dir", "Nacelle position", "°"),
    ("rotor_speed", "Rotor speed", "RPM"),
    ("gen_speed", "Generator RPM", "RPM"),
    ("t_amb", "Nacelle ambient temperature", "°C"),
    ("t_nacelle", "Nacelle temperature", "°C"),
    ("t_gearbox_oil", "Gear oil temperature", "°C"),
    ("t_gen_winding", "Stator temperature 1", "°C"),
    ("t_gen_bearing", "Generator bearing front temperature", "°C"),
    ("t_main_bearing", "Rotor bearing temp", "°C"),
    ("t_transformer", "Transformer temperature", "°C"),
    ("v_ac", "Grid voltage", "V"),
    ("i_ac", "Grid current", "A"),
]
_STAT_WORDS = {"standard deviation": "std", "stddev": "std", "std": "std", "minimum": "min", "min": "min",
               "maximum": "max", "max": "max"}
_PITCH = [f"Blade angle (pitch position) {b} (°)" for b in "ABC"]
STATUS_COLUMNS = ["Timestamp start", "Timestamp end", "Status", "Code", "Message", "IEC category"]


def _column_map(header: list[str]) -> dict[str, str]:
    """Greenbyte column name -> canonical column name, for the columns the schema holds."""
    present = set(header)
    out: dict[str, str] = {}
    for signal, title, unit in GREENBYTE_COLUMNS:
        mean = f"{title} ({unit})" if unit else title
        if mean in present:
            out[mean] = signal
        stat_rx = re.compile(rf"^{re.escape(title)}, ([A-Za-z ]+?)(?: \([^)]*\))?$")
        for col in header:
            m = stat_rx.match(col)
            if m and m.group(1).strip().lower() in _STAT_WORDS:
                out[col] = f"{signal}__{_STAT_WORDS[m.group(1).strip().lower()]}"
    return out


def _header_row(lines: list[str], marker: str) -> int:
    for i, line in enumerate(lines[:60]):
        if line.startswith(marker):
            return i
    raise ValueError(f"header line starting with {marker!r} not found")


def parse_turbine_csv(text: str, device_id: str) -> pd.DataFrame:
    """One Greenbyte 10-minute export to canonical columns (``timestamp`` UTC, ``device_id``, signals)."""
    lines = text.splitlines()
    skip = _header_row(lines, TIME_COL)
    cols = list(pd.read_csv(io.StringIO(text), skiprows=skip, nrows=0).columns)
    cmap = _column_map(cols)
    wanted = {TIME_COL, *cmap, *_PITCH}
    df = pd.read_csv(io.StringIO(text), skiprows=skip, usecols=lambda c: c in wanted, na_values=["NaN"])
    out = pd.DataFrame({
        "timestamp": pd.to_datetime(df[TIME_COL]).dt.tz_localize("UTC").dt.as_unit("ns"),
        "device_id": device_id,
    })
    for src, dst in cmap.items():
        out[dst] = df[src].to_numpy(dtype="float64")
    pitch = [df[c] for c in _PITCH if c in df]
    if pitch:
        out["pitch"] = pd.concat(pitch, axis=1).mean(axis=1).to_numpy(dtype="float64")
    return out


def parse_status_csv(text: str, device_id: str) -> pd.DataFrame:
    """One Greenbyte status log to the event layout (see ``loaders._common.EVENT_COLUMNS``)."""
    lines = text.splitlines()
    skip = _header_row(lines, "Timestamp start")
    df = pd.read_csv(io.StringIO(text), skiprows=skip, usecols=lambda c: c in STATUS_COLUMNS, dtype=str)
    ev = pd.DataFrame({
        "device_id": device_id,
        "start": pd.to_datetime(df["Timestamp start"], format="%Y-%m-%d %H:%M:%S", errors="coerce"),
        "end": pd.to_datetime(df["Timestamp end"], format="%Y-%m-%d %H:%M:%S", errors="coerce"),  # "-" = still open
        "status": df["Status"],
        "code": df["Code"].str.replace(r"\.0$", "", regex=True),
        "message": df["Message"],
        "category": map_labels(df["IEC category"], "greenbyte") if "IEC category" in df else None,
    })
    ev["start"] = ev["start"].dt.tz_localize("UTC")
    ev["end"] = ev["end"].dt.tz_localize("UTC")
    return ev


def read_zip(path: Path, name_regex: str, width: int = 2) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    """All turbine data and status files of one archive; ``name_regex`` captures the turbine number."""
    rx = re.compile(name_regex)
    data, status = [], []
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            base = name.rsplit("/", 1)[-1]
            m = rx.search(base)
            if not m or not base.endswith(".csv"):
                continue
            tid = f"T{int(m.group(1)):0{width}d}"
            text = z.read(name).decode("utf-8-sig")
            if base.startswith("Turbine_Data"):
                data.append(parse_turbine_csv(text, tid))
            elif base.startswith("Status"):
                status.append(parse_status_csv(text, tid))
    return data, status


def read_static(path: Path, width: int = 2) -> list[Device]:
    """Turbine metadata of ``*_WT_static.csv`` as devices."""
    df = pd.read_csv(path, encoding="utf-8-sig").dropna(subset=["Title"])
    devices = []
    for _, r in df.iterrows():
        n = int(re.search(r"(\d+)$", str(r["Title"])).group(1))
        devices.append(Device(
            id=f"T{n:0{width}d}", type="turbine", rated_kw=float(r["Rated power (kW)"]),
            lat=float(r["Latitude"]), lon=float(r["Longitude"]),
            hub_height_m=float(r["Hub Height (m)"]), rotor_diameter_m=float(r["Rotor Diameter (m)"]),
        ))
    return sorted(devices, key=lambda d: d.id)


def build(
    name: str, archives: list[Path], name_regex: str, static: Path | None, source: Source, notes: list[str],
    devices: list[str] | None = None, width: int = 2,
) -> Loaded:
    data, status = [], []
    for a in archives:
        d, s = read_zip(a, name_regex, width)
        data += d
        status += s
    if not data:
        raise FileNotFoundError(f"no turbine data files in {[a.name for a in archives]}")
    table = pd.concat(data, ignore_index=True)
    if devices is not None:
        table = table[table["device_id"].isin(devices)]
    table = table.drop_duplicates(["device_id", "timestamp"])
    table = table.dropna(axis=1, how="all")
    table = finalise(table)
    inv = read_static(static, width) if static is not None and static.exists() else []
    have = set(table["device_id"])
    inv = [d for d in inv if d.id in have] or [Device(id=i, type="turbine") for i in sorted(have)]
    meta = DatasetMeta(
        name=name, profile="wind", interval="10min", source_timezone="UTC", source_label="start",
        devices=inv, source=source, notes=notes,
        conversions=["pitch: mean of blades A, B and C", "no unit conversion: the export is in kW, m/s, deg, degC, V, A, rpm"],
    )
    events = pd.concat(status, ignore_index=True) if status else pd.DataFrame()
    ev = make_events(events.drop_duplicates()) if len(events) else None
    if ev is not None:
        ev = ev[ev["device_id"].isin(have)].reset_index(drop=True)
    return Loaded(table, meta, ev if ev is not None else make_events(pd.DataFrame(columns=["device_id", "start", "end"])))


def year_archives(raw_dir: Path, pattern: str, years: list[int] | None) -> list[Path]:
    """Zip archives in ``raw_dir`` (recursive) whose name matches ``pattern`` with a year group, for the years asked."""
    rx = re.compile(pattern)
    found = []
    for p in sorted(Path(raw_dir).rglob("*.zip")):
        m = rx.search(p.name)
        if m and (years is None or int(m.group(1)) in years):
            found.append(p)
    return found

