"""Hill of Towie wind farm (21 x Siemens SWT-2.3-93, 2.3 MW, Scotland), 10-minute SCADA, alarm log, shutdown duration.

RES, Hill of Towie wind farm open dataset, Zenodo, doi:10.5281/zenodo.14870021 (concept DOI; v2.1.0 is record
22662930), licence CC BY 4.0. A yearly zip holds monthly CSV tables per subsystem (``tblSCTurbine``, ``tblSCTurGrid``,
``tblSCTurTemp``) and the alarm log (``tblAlarmLog``). ``fetch`` copies only these tables out of the 1.4 GB archives.

* Time stamps in the tables mark the **end** of the 10-minute period, UTC; the loader moves them to the start.
* ``StationId`` minus 2304509 is the turbine number.
* ``ShutdownDuration`` (a separate file) is the operator's own count of seconds stopped in each interval; it becomes
  ``shutdown_s``.
* The tables used here have no absolute wind direction: ``wind_dir`` is absent, ``nacelle_dir`` is the nacelle
  position relative to north.

Usage:
    loaded = load("data/hill_of_towie", years=[2021])
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, download_file, make_events, new_session, zenodo_files
from scada_schema.loaders._paths import raw_dir
from scada_schema.loaders._remotezip import extract_members
from scada_schema.model import DatasetMeta, Device, Source
from scada_schema.states import map_codes

NAME = "hill_of_towie"
RECORD = "22662930"
STATION_OFFSET = 2304509
RATED_KW = 2300.0
SOURCE = Source(
    name="Hill of Towie wind farm open dataset (RES)", doi="10.5281/zenodo.14870021",
    url=f"https://zenodo.org/records/{RECORD}", license="CC-BY-4.0",
    citation="RES, Hill of Towie wind farm open dataset, Zenodo, https://doi.org/10.5281/zenodo.14870021",
)
STATIC = ("Hill_of_Towie_turbine_metadata.csv", "Hill_of_Towie_alarms_description.csv", "Hill_of_Towie_ShutdownDuration.zip")
TABLES = ("tblSCTurbine", "tblSCTurGrid", "tblSCTurTemp", "tblAlarmLog")
STEP = pd.Timedelta("10min")

# canonical signal -> (table, column prefix); the prefix has _mean, _min, _max and _stddev columns
MEAN = {
    "p_ac": ("tblSCTurGrid", "wtc_ActPower"),
    "q_ac": ("tblSCTurGrid", "wtc_ReactPwr"),
    "power_factor": ("tblSCTurGrid", "wtc_CosPhi"),
    "wind_speed": ("tblSCTurbine", "wtc_AcWindSp"),
    "wind_speed_2": ("tblSCTurbine", "wtc_SeWindSp"),
    "nacelle_dir": ("tblSCTurbine", "wtc_NacelPos"),
    "rotor_speed": ("tblSCTurbine", "wtc_MainSRpm"),
    "gen_speed": ("tblSCTurbine", "wtc_GenRpm"),
    "t_amb": ("tblSCTurTemp", "wtc_AmbieTmp"),
    "t_nacelle": ("tblSCTurTemp", "wtc_NacelTmp"),
    "t_gearbox_oil": ("tblSCTurTemp", "wtc_GeOilTmp"),
    "t_gearbox_bearing": ("tblSCTurTemp", "wtc_HSGenTmp"),
    "t_gen_bearing": ("tblSCTurTemp", "wtc_GenBeRTm"),
    "t_main_bearing": ("tblSCTurTemp", "wtc_MainBTmp"),
}
STATS = {"min": "min", "max": "max", "std": "stddev"}
MEANS_OF = {  # signals that are the mean of several columns
    "pitch": ("tblSCTurbine", ["wtc_PitcPosA_mean", "wtc_PitcPosB_mean", "wtc_PitcPosC_mean"]),
    "v_ac": ("tblSCTurGrid", ["wtc_VoltPhR_mean", "wtc_VoltPhS_mean", "wtc_VoltPhT_mean"]),
    "i_ac": ("tblSCTurGrid", ["wtc_AmpPhR_mean", "wtc_AmpPhS_mean", "wtc_AmpPhT_mean"]),
    "t_gen_winding": ("tblSCTurTemp", ["wtc_Gen1U1Tm_mean", "wtc_Gen1V1Tm_mean", "wtc_Gen1W1Tm_mean"]),
}
SINGLE = {"setpoint": ("tblSCTurbine", "wtc_PowerRef_endvalue")}


def _wanted(table: str) -> dict[str, str]:
    """Source column -> canonical column for one table (the columns to be averaged map to themselves)."""
    cols: dict[str, str] = {}
    for sig, (t, prefix) in MEAN.items():
        if t != table:
            continue
        cols[f"{prefix}_mean"] = sig
        for stat, suffix in STATS.items():
            cols[f"{prefix}_{suffix}"] = f"{sig}__{stat}"
    for sig, (t, name) in SINGLE.items():
        if t == table:
            cols[name] = sig
    for _, (t, names) in MEANS_OF.items():
        if t == table:
            cols.update({n: n for n in names})
    return cols


def _turbine_id(station: pd.Series) -> pd.Series:
    return "T" + (station.astype(int) - STATION_OFFSET).astype(int).map("{:02d}".format)


def _read_table(z: zipfile.ZipFile, table: str) -> pd.DataFrame:
    cols = _wanted(table)
    parts = []
    for name in sorted(n for n in z.namelist() if re.match(rf"{table}_\d{{4}}_\d{{2}}\.csv$", n)):
        with z.open(name) as fh:
            parts.append(pd.read_csv(fh, usecols=lambda c: c in ("TimeStamp", "StationId", *cols)))
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df.pop("TimeStamp"), utc=True).dt.as_unit("ns") - STEP  # end -> start
    df["device_id"] = _turbine_id(df.pop("StationId"))
    out = df[["timestamp", "device_id"]].copy()
    for src, dst in cols.items():
        if src in df:
            out[dst] = df[src].to_numpy(dtype="float64")
    for sig, (t, names) in MEANS_OF.items():
        if t == table:
            have = [n for n in names if n in out]
            if have:
                out[sig] = out[have].mean(axis=1)
            out = out.drop(columns=have)
    res = out.drop_duplicates(["device_id", "timestamp"]).set_index(["device_id", "timestamp"])
    res.attrs["dropped"] = len(out) - len(res)
    return res


def _alarms(z: zipfile.ZipFile, description: Path) -> pd.DataFrame:
    parts = [pd.read_csv(z.open(n)) for n in sorted(z.namelist()) if n.startswith("tblAlarmLog_")]
    if not parts:
        return make_events(pd.DataFrame(columns=["device_id", "start", "end"]))
    a = pd.concat(parts, ignore_index=True)
    desc = pd.read_csv(description, encoding="latin-1").set_index("Alarm Code")
    code = a["Alarmcode"].astype(int)
    stopping = code.map(desc["Stopping"])
    ev = pd.DataFrame({
        "device_id": _turbine_id(a["StationNr"]),
        "start": pd.to_datetime(a["TimeOn"], utc=True),
        "end": pd.to_datetime(a["TimeOff"], utc=True, errors="coerce"),
        "status": np.where(stopping.eq(1), "Stop", np.where(stopping.eq(0), "Informational", "Unknown")),
        "code": code.astype(str),
        "message": code.map(desc["Description"].str.strip(" :")),
    })
    ev["category"] = map_codes(ev["code"], "siemens_swt_hot")
    return make_events(ev)


def _devices(path: Path) -> list[Device]:
    m = pd.read_csv(path, encoding="utf-8-sig")
    return sorted((Device(id=str(r["Turbine Name"]), type="turbine", rated_kw=float(r["Rated power (kW)"]),
                          lat=float(r["Latitude"]), lon=float(r["Longitude"]), hub_height_m=float(r["Hub Height (m)"]),
                          rotor_diameter_m=float(r["Rotor Diameter (m)"])) for _, r in m.iterrows()), key=lambda d: d.id)


def fetch(data_dir: str | Path | None = None, years: list[int] | None = None, session=None) -> list[Path]:
    """Download the metadata files and, per year, a zip with only the needed tables (range requests)."""
    dest = raw_dir(NAME, data_dir)
    s = session or new_session()
    files = zenodo_files(RECORD, s)
    out = [download_file(files[n], dest / n, s) for n in STATIC]
    rx = re.compile(rf"^({'|'.join(TABLES)})_\d{{4}}_\d{{2}}\.csv$")
    for y in years or []:
        out.append(extract_members(s, files[f"{y}.zip"], rx, dest / f"{y}_subset.zip"))
    return out


def load(data_dir: str | Path | None = None, years: list[int] | None = None, devices: list[str] | None = None) -> Loaded:
    """Hill of Towie SCADA, alarm log and shutdown duration for the years asked."""
    d = raw_dir(NAME, data_dir)
    archives = {}
    for p in sorted(d.glob("*.zip")):
        m = re.fullmatch(r"(\d{4})(?:_subset)?\.zip", p.name)
        if m and (years is None or int(m.group(1)) in years):
            archives[int(m.group(1))] = p
    if not archives:
        raise FileNotFoundError(f"no Hill of Towie yearly archives for {years or 'any year'} in {d}; fetch the data first (scada-schema fetch <dataset>)")
    frames, alarms = [], []
    removed = 0
    for y, path in archives.items():
        with zipfile.ZipFile(path) as z:
            tables = [_read_table(z, t) for t in TABLES[:3]]
            removed += tables[0].attrs.get("dropped", 0)  # the three tables hold the same turbine-intervals: count them once
            joined = tables[0].join(tables[1:], how="outer").reset_index()
            frames.append(joined[joined["timestamp"].dt.year == y])
            alarms.append(_alarms(z, d / STATIC[1]))
    table = pd.concat(frames, ignore_index=True)
    sd_path = d / STATIC[2]
    if sd_path.exists():
        with zipfile.ZipFile(sd_path) as z:
            sd = pd.read_csv(io.BytesIO(z.read("ShutdownDuration.csv")))
        sd = pd.DataFrame({"timestamp": pd.to_datetime(sd["TimeStamp_StartFormat"], utc=True).dt.as_unit("ns"),
                           "device_id": sd["TurbineName"].astype(str), "shutdown_s": sd["ShutdownDuration"].astype("float64")})
        table = table.merge(sd, on=["device_id", "timestamp"], how="left")
    if devices is not None:
        table = table[table["device_id"].isin(devices)]
    table = finalise(table.dropna(axis=1, how="all"))
    have = set(table["device_id"])
    inv = [x for x in _devices(d / STATIC[0]) if x.id in have] or [Device(id=i, type="turbine", rated_kw=RATED_KW) for i in sorted(have)]
    meta = DatasetMeta(
        name=NAME, profile="wind", interval="10min", source_timezone="UTC", source_label="end", devices=inv, source=SOURCE,
        conversions=["time stamps moved from the end to the start of the interval (-10 min)",
                     "pitch: mean of blades A, B, C; v_ac and i_ac: mean of the three phases; "
                     "t_gen_winding: mean of the windings 1U1, 1V1, 1W1",
                     "shutdown_s: ShutdownDuration of the operator's file, seconds out of 600",
                     "setpoint: the value at the end of the interval (wtc_PowerRef_endvalue), not a mean",
                     *([f"{removed} rows with a repeated (device, time stamp) dropped, the first kept"] if removed else [])],
        notes=["No absolute wind direction in the SCADA tables used; nacelle_dir is the nacelle position relative to north.",
               "wind_speed is the wind speed of the active wind sensor (wtc_AcWindSp)."],
    )
    ev = pd.concat(alarms, ignore_index=True).drop_duplicates() if alarms else None
    if ev is None or ev.empty:
        ev = make_events(pd.DataFrame(columns=["device_id", "start", "end"]))
    return Loaded(table, meta, ev, removed={"duplicate_rows": removed})
