"""A loader driven by a YAML file: turn your own SCADA export (CSV, Excel or Parquet) into a canonical table without writing code.

The mapping says where the files are, how to read them, which column holds the time stamp (and in which time zone and
with which label), which column is which canonical signal in which unit, and what the devices are:

    name: my_farm
    profile: wind
    interval: 10min
    files: ["export/*.csv"]                 # relative to the YAML file
    read: {sep: ";", decimal: ",", encoding: utf-8}
    timestamp: {column: Time, timezone: Europe/Warsaw, label: end, format: "%d.%m.%Y %H:%M"}
    device_column: Turbine                  # or: device: T01  /  device_regex: "WT(\\d+)" with device_format: "T{:02d}"
    columns:
      "Active power [W]":   {signal: p_ac, unit: W}
      "Wind [km/h]":        {signal: wind_speed, unit: km/h}
      "Wind std [km/h]":    {signal: wind_speed, unit: km/h, stat: std}
      "Ambient [F]":        {signal: t_amb, unit: degF}
      "Status":             {signal: status_code}
    devices:
      T01: {type: turbine, rated_kw: 2050, lat: 52.4, lon: -0.9}

Three layouts are supported: **stacked** (one file or many, a ``device_column`` names the device of each row), **one file per
device** (``device`` fixed, or ``device_regex`` applied to the file name) and **wide** (every column names its own ``device``).

Time stamps are converted to UTC with the daylight saving rules of the given zone (the repeated autumn hour is resolved by order),
moved to the start of the interval according to ``label`` and must then lie on the interval grid (``round: true`` rounds
stamps that are a few seconds off). Values are converted to the canonical unit of the signal; ``scale``, ``offset`` and ``sign``
apply after the unit conversion. Every conversion is written into the metadata of the result.

Usage:
    spec = load_spec("mapping.yaml")
    loaded = import_table(spec)                 # a Loaded: table, meta, events
    # or: scada-schema import mapping.yaml --out my_farm.parquet
"""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from res_asset_kpi import timeseries
from res_asset_kpi import units as kpi_units

from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, empty_events
from scada_schema.model import DatasetMeta, Device, Source
from scada_schema.signals import SIGNALS
from scada_schema.validate import validate

__all__ = ["ColumnSpec", "ImportSpec", "import_table", "load_spec"]

# units of the schema that the unit registry of res_asset_kpi does not know: (from, to) -> factor
_OWN = {("var", "kvar"): 1e-3, ("mvar", "kvar"): 1e3, ("v", "v"): 1.0, ("kv", "v"): 1e3, ("a", "a"): 1.0, ("ka", "a"): 1e3,
        ("nm", "knm"): 1e-3, ("knm", "knm"): 1.0, ("mm/s", "mm/s"): 1.0, ("m/s", "mm/s"): 1e3, ("1", "1"): 1.0, ("%", "1"): 0.01,
        ("hz", "hz"): 1.0, ("kvar", "kvar"): 1.0, ("s", "s"): 1.0}


def _norm(u: str) -> str:
    return u.strip().lower().replace("°", "deg").replace("²", "2").replace(" ", "")


def linear(from_unit: str, to_unit: str) -> tuple[float, float]:
    """``(scale, offset)`` with ``to = from * scale + offset``; raises ``ValueError`` when the units measure different things."""
    a, b = _norm(from_unit), _norm(to_unit)
    if a == b:
        return 1.0, 0.0
    if (a, b) in _OWN:
        return _OWN[(a, b)], 0.0
    try:
        off = float(kpi_units.convert(0.0, from_unit, to_unit))
        return float(kpi_units.convert(1.0, from_unit, to_unit)) - off, off
    except (ValueError, KeyError) as e:
        raise ValueError(f"cannot convert {from_unit!r} to {to_unit!r}: {e}") from None


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(_M):
    """What one column holds."""

    signal: str
    unit: str | None = Field(default=None, description="unit of the column; the canonical unit of the signal when omitted")
    stat: Literal["min", "max", "std"] | None = Field(default=None, description="the column is the minimum, maximum or standard deviation of the interval")
    scale: float = 1.0
    offset: float = 0.0
    sign: Literal[1, -1] = 1
    device: str | None = Field(default=None, description="wide layout: the device this column belongs to")

    @field_validator("signal")
    @classmethod
    def _signal(cls, v: str) -> str:
        if v not in SIGNALS:
            raise ValueError(f"{v!r} is not a canonical signal; known: {sorted(SIGNALS)}")
        return v

    @model_validator(mode="after")
    def _check(self) -> ColumnSpec:
        sig = SIGNALS[self.signal]
        if self.stat and not sig.stats:
            raise ValueError(f"{self.signal} has no min, max or std columns")
        if sig.kind in ("text", "category"):
            if self.unit or self.stat or self.scale != 1.0 or self.offset != 0.0 or self.sign != 1:
                raise ValueError(f"{self.signal} is text: unit, stat, scale, offset and sign do not apply")
        elif self.unit:
            try:
                linear(self.unit, sig.unit)
            except ValueError as e:
                raise ValueError(str(e)) from None
        return self


class TimeSpec(_M):
    column: str | list[str]
    timezone: str = "UTC"
    label: Literal["start", "end", "center"] = "start"
    format: str | None = None
    round: bool = False


class ReadSpec(_M):
    format: Literal["csv", "excel", "parquet"] = "csv"
    sep: str = ","
    decimal: str = "."
    thousands: str | None = None
    encoding: str = "utf-8-sig"
    skiprows: int = 0
    sheet: str | int | None = None
    na_values: list[str] = Field(default_factory=lambda: ["NaN", "nan", "", "NA", "N/A", "-", "null"])


class ImportSpec(_M):
    """A mapping file (see the module description)."""

    name: str
    profile: Literal["wind", "pv"]
    interval: str
    files: list[str]
    timestamp: TimeSpec
    columns: dict[str, ColumnSpec]
    read: ReadSpec = Field(default_factory=ReadSpec)
    device_column: str | None = None
    device: str | None = None
    device_regex: str | None = None
    device_format: str = "{}"
    default_device_type: str | None = None
    devices: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: Source | None = None
    notes: list[str] = Field(default_factory=list)
    code_table: str | None = Field(default=None, description="map status_code to iec_category with a shipped code table (senvion_mm, siemens_swt_hot)")
    base_dir: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _layout(self) -> ImportSpec:
        in_cols = [c.device for c in self.columns.values() if c.device]
        modes = [bool(self.device_column), bool(self.device), bool(self.device_regex), bool(in_cols)]
        if sum(modes) != 1:
            raise ValueError("name the devices in exactly one way: device_column, device, device_regex or a device on every column")
        if in_cols and len(in_cols) != len(self.columns):
            raise ValueError("wide layout: every column needs a device")
        if self.interval not in ("5min", "10min", "15min", "30min", "1h"):
            raise ValueError(f"interval {self.interval!r} not supported")
        return self


def load_spec(source: str | Path | dict) -> ImportSpec:
    """Read a mapping from a YAML file (paths in it are relative to the file) or a dictionary."""
    if isinstance(source, dict):
        return ImportSpec.model_validate(source)
    p = Path(source)
    spec = ImportSpec.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
    spec.base_dir = p.parent
    return spec


def _read(path: Path, rd: ReadSpec) -> pd.DataFrame:
    if rd.format == "parquet":
        return pd.read_parquet(path)
    if rd.format == "excel":
        return pd.read_excel(path, sheet_name=rd.sheet or 0, skiprows=rd.skiprows)
    return pd.read_csv(path, sep=rd.sep, decimal=rd.decimal, thousands=rd.thousands, encoding=rd.encoding, skiprows=rd.skiprows,
                       na_values=rd.na_values, keep_default_na=False)


def _timestamps(raw: pd.DataFrame, ts: TimeSpec, groups: pd.Series | None) -> pd.Series:
    cols = [ts.column] if isinstance(ts.column, str) else ts.column
    missing = [c for c in cols if c not in raw.columns]
    if missing:
        raise ValueError(f"time stamp column {missing} not in the file; columns are {list(raw.columns)[:12]}")
    text = raw[cols[0]].astype(str) if len(cols) == 1 else raw[cols].astype(str).agg(" ".join, axis=1)
    return timeseries.to_utc(text, ts.timezone, groups=groups, fmt=ts.format)


def _device_of(path: Path, spec: ImportSpec) -> str | None:
    if spec.device:
        return spec.device
    if spec.device_regex:
        m = re.search(spec.device_regex, path.name)
        if not m:
            raise ValueError(f"device_regex {spec.device_regex!r} does not match the file name {path.name!r}")
        g = m.group(1) if m.groups() else m.group(0)
        return spec.device_format.format(int(g) if g.isdigit() else g)
    return None


def _values(raw: pd.DataFrame, col: str, c: ColumnSpec) -> np.ndarray | pd.Series:
    sig = SIGNALS[c.signal]
    if sig.kind in ("text", "category"):
        s = raw[col].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        return s.astype(object).where(s.notna() & (s != ""), None)
    x = pd.to_numeric(raw[col], errors="coerce").to_numpy(dtype="float64")
    a, b = linear(c.unit, sig.unit) if c.unit else (1.0, 0.0)
    return (x * a + b) * c.scale * c.sign + c.offset


def import_table(spec: ImportSpec | str | Path | dict, validate_result: bool = True) -> Loaded:
    """Read the files of a mapping into a canonical table (and validate it unless told not to).

    Raises
    ------
    FileNotFoundError
        If no file matches ``files``.
    ValueError
        For a column named in the mapping that is not in a file, time stamps off the grid, or a unit that does not fit the signal.
    SchemaError
        If the result violates the schema.
    """
    if not isinstance(spec, ImportSpec):
        spec = load_spec(spec)
    base = spec.base_dir or Path(".")
    paths = sorted({Path(p) for pat in spec.files for p in glob.glob(str(base / pat))})
    if not paths:
        raise FileNotFoundError(f"no file matches {spec.files} (relative to {base.resolve()})")
    step = pd.Timedelta(spec.interval)
    shift = {"start": pd.Timedelta(0), "end": step, "center": step / 2}[spec.timestamp.label]
    wide = any(c.device for c in spec.columns.values())
    frames = []
    for path in paths:
        raw = _read(path, spec.read)
        raw.columns = [str(c).strip() for c in raw.columns]
        absent = [c for c in spec.columns if c not in raw.columns]
        if absent:
            raise ValueError(f"{path.name}: columns {absent} named in the mapping are not in the file; the file has {list(raw.columns)[:15]}")
        fixed = _device_of(path, spec)
        dev_series = raw[spec.device_column].astype(str).str.strip() if spec.device_column else None
        if spec.device_column and spec.device_column not in raw.columns:
            raise ValueError(f"{path.name}: device column {spec.device_column!r} not in the file")
        t = _timestamps(raw, spec.timestamp, dev_series if dev_series is not None else None)
        t = t - shift
        if spec.timestamp.round:
            t = t.dt.round(spec.interval)
        if wide:
            for dev in sorted({c.device for c in spec.columns.values()}):
                part = pd.DataFrame({"timestamp": t, "device_id": dev})
                for col, c in spec.columns.items():
                    if c.device == dev:
                        part[c.signal if not c.stat else f"{c.signal}__{c.stat}"] = _values(raw, col, c)
                frames.append(part)
        else:
            part = pd.DataFrame({"timestamp": t, "device_id": dev_series if dev_series is not None else fixed})
            for col, c in spec.columns.items():
                part[c.signal if not c.stat else f"{c.signal}__{c.stat}"] = _values(raw, col, c)
            frames.append(part)
    table = pd.concat(frames, ignore_index=True)
    bad_t = int(table["timestamp"].isna().sum())
    if bad_t:
        table = table.dropna(subset=["timestamp"])
    table["timestamp"] = table["timestamp"].dt.as_unit("ns")
    off = int((table["timestamp"].astype("int64") % step.value != 0).sum())
    if off:
        raise ValueError(f"{off} time stamps are not on the {spec.interval} grid after the conversion to UTC and the {spec.timestamp.label} label; "
                         "check timezone and label, or set timestamp.round: true for stamps a few seconds off")
    n_dup = int(table.duplicated(["device_id", "timestamp"]).sum())
    if n_dup:
        table = table.drop_duplicates(["device_id", "timestamp"], keep="first")
    table = table.dropna(axis=1, how="all")
    if spec.code_table and "status_code" in table.columns:
        from scada_schema.states import map_codes

        table["iec_category"] = map_codes(table["status_code"], spec.code_table)
    table = finalise(table)
    conv = []
    for col, c in spec.columns.items():
        sig = SIGNALS[c.signal]
        if sig.kind in ("text", "category"):
            conv.append(f"{col} -> {c.signal} (text)")
            continue
        extra = "".join([f", scale {c.scale:g}" if c.scale != 1 else "", f", offset {c.offset:g}" if c.offset else "", ", sign -1" if c.sign == -1 else ""])
        conv.append(f"{col} -> {c.signal}{'__' + c.stat if c.stat else ''}: {c.unit or sig.unit} -> {sig.unit}{extra}")
    conv.append(f"time stamps: {spec.timestamp.timezone}, marked the {spec.timestamp.label} of the interval -> UTC, interval start")
    if bad_t:
        conv.append(f"{bad_t} rows without a readable time stamp dropped (daylight saving gap or repeated hour seen once)")
    if n_dup:
        conv.append(f"{n_dup} duplicated (device, time) rows dropped, first kept")
    if spec.code_table:
        conv.append(f"iec_category from the code table {spec.code_table}")
    dtype = spec.default_device_type or ("turbine" if spec.profile == "wind" else "inverter")
    ids = sorted(table["device_id"].unique())
    devices = []
    for i in ids:
        d = dict(spec.devices.get(i, {}))
        d.setdefault("type", dtype)
        devices.append(Device(id=i, **d))
    meta = DatasetMeta(name=spec.name, profile=spec.profile, interval=spec.interval, source_timezone=spec.timestamp.timezone,
                       source_label=spec.timestamp.label, devices=devices, source=spec.source, conversions=conv, notes=spec.notes)
    if validate_result:
        validate(table, meta).raise_if_errors()
    return Loaded(table, meta, empty_events())
