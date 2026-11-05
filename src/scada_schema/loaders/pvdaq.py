"""NREL PVDAQ (Photovoltaic Data Acquisition) public datasets: rooftop systems of a school campus in Las Vegas.

Source: C. Deline, K. Perry, M. Deceglie, M. Muller, W. Sekulic, D. Jordan, Photovoltaic Data Acquisition (PVDAQ)
Public Datasets, Open Energy Data Initiative, NREL, doi:10.25984/1846021, licence CC BY 4.0. One CSV per system and day
in a public bucket; 15-minute values in **local standard time (UTC-8, no daylight saving)**. Systems used here:
34 (building A, 146.6 kWp), 1276 (B, 68.5 kWp), 1278 (D, two inverters of 85.7 kWp) and 35 (gymnasium G, 121.7 kWp).

What the loader does to the raw columns:

* **Units.** The raw AC and DC power channels are in hectowatts until 2018-08-04 and in watts afterwards; the switch is
  found in the data (power to irradiance ratio, see [`detect_power_unit_switch`][scada_schema.loaders.pvdaq.detect_power_unit_switch])
  and recorded in the metadata. With ``fix_units=False`` the loader reads every power value as hectowatts, which keeps
  the raw artefact so that the quality control can be shown to find it. Temperatures in degrees Fahrenheit become
  degrees Celsius, wind speed in mph becomes m/s, the power factor (x1000) a number.
* **Time.** The stamps are rounded to the 15-minute grid and treated as the **end** of the interval, which is how the
  irradiance channel behaves (``PVDAQ_SITE_NOTES.md`` of pv-outage-classifier: -5.4 min against a 15-minute mean
  stamped at its end); the AC power channel sits about 9 minutes later than the irradiance on the same stamp.
* **Devices.** ``INV`` (or ``INV1`` and ``INV2`` for system 1278) and ``MET``, the met station of the system.

Usage:
    fetch("data/pvdaq", system=34, years=[2016, 2017])
    loaded = load("data/pvdaq", system=34, years=[2016, 2017])
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from res_asset_kpi.timeseries import to_utc

from scada_schema._steps import daily_log_ratio, level_step
from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, new_session
from scada_schema.loaders._paths import raw_dir
from scada_schema.model import DatasetMeta, Device, Source

log = logging.getLogger(__name__)

NAME = "pvdaq"
BASE_URL = "https://oedi-data-lake.s3.amazonaws.com/pvdaq/csv/pvdata"
TZ = "Etc/GMT+8"
LAT, LON = 36.1952, -115.1582
SOURCE = Source(
    name="NREL PVDAQ public datasets (OEDI)", doi="10.25984/1846021", url="https://data.openei.org/submissions/4568",
    license="CC-BY-4.0",
    citation="C. Deline et al., Photovoltaic Data Acquisition (PVDAQ) Public Datasets, OEDI, NREL, https://doi.org/10.25984/1846021",
)


def F_TO_C(f):
    return (f - 32.0) * 5.0 / 9.0


MPH_TO_MS = 0.44704

# inverter id -> {canonical signal: raw column prefix}
_INV_COMMON = {"p_ac": "ac_power_hw", "p_dc": "dc_power_hw", "i_ac": "ac_current", "v_ac": "ac_voltage",
               "t_inverter": "inverter_temp", "status_code": "inverter_error_code"}
SYSTEMS: dict[int, dict] = {
    34: {"capacity": {"INV": 146.6}, "tilt": 11.0, "azimuth": 180.0,
         "inverters": {"INV": {**_INV_COMMON, "i_dc": "dc_current", "v_dc": "dc_voltage", "power_factor": "power_factor_1000"}}},
    1276: {"capacity": {"INV": 68.5}, "tilt": 5.0, "azimuth": 180.0,
           "inverters": {"INV": {**_INV_COMMON, "i_dc": "input_current", "v_dc": "input_voltage"}}},
    1278: {"capacity": {"INV1": 85.7, "INV2": 85.7}, "tilt": None, "azimuth": None, "inverters": {
        f"INV{k}": {"p_ac": f"inv{k}_ac_power_hw", "p_dc": f"inv{k}_dc_power_hw", "i_ac": f"inv{k}_ac_current",
                    "v_ac": f"inv{k}_ac_voltage", "i_dc": f"inv{k}_input_current", "v_dc": f"inv{k}_input_voltage",
                    "t_inverter": f"inv{k}_temp", "status_code": f"inv{k}_error_code"} for k in (1, 2)}},
    35: {"capacity": {"INV": 121.7}, "tilt": None, "azimuth": 270.0,
         "inverters": {"INV": {**_INV_COMMON, "i_dc": "dc_current", "v_dc": "dc_voltage", "power_factor": "power_factor_1000"}}},
}
MET = {"poa": "poa_irradiance", "t_amb": "ambient_temp_f", "t_module": "module_temp_f", "wind_speed": "wind_speed_mph",
       "wind_dir": "wind_direction", "humidity": "relative_humidity"}


def day_url(system: int, day: pd.Timestamp) -> str:
    return (f"{BASE_URL}/system_id={system}/year={day.year}/month={day.month}/day={day.day}/"
            f"system_{system}__date_{day:%Y_%m_%d}.csv")


def fetch(data_dir: str | Path | None = None, system: int = 34, years: list[int] | None = None, workers: int = 8) -> int:
    """Download the missing daily files of one system (about 60 MB for three years); returns the number fetched."""
    dest = raw_dir(NAME, data_dir) / f"system_{system}"
    dest.mkdir(parents=True, exist_ok=True)
    session = new_session()
    days = [d for y in years or [2016] for d in pd.date_range(f"{y}-01-01", f"{y}-12-31")]
    todo = [d for d in days if not (dest / f"system_{system}__date_{d:%Y_%m_%d}.csv").exists()]

    def get(day: pd.Timestamp) -> int:
        r = session.get(day_url(system, day), timeout=60)
        if r.status_code == 404:
            return 0
        r.raise_for_status()
        (dest / f"system_{system}__date_{day:%Y_%m_%d}.csv").write_bytes(r.content)
        return 1

    with ThreadPoolExecutor(workers) as pool:
        return sum(pool.map(get, todo))


def _column(df: pd.DataFrame, prefix: str) -> pd.Series | None:
    cols = [c for c in df.columns if c.rsplit("__", 1)[0] == prefix]
    return pd.to_numeric(df[cols[0]], errors="coerce") if cols else None


def read_system(path: Path, system: int, years: list[int] | None = None) -> pd.DataFrame:
    """The daily files of one system as one frame indexed by UTC end-of-interval time (duplicates dropped)."""
    files = sorted((Path(path) / f"system_{system}").glob(f"system_{system}__date_*.csv"))
    files = [p for p in files if (years is None or int(p.stem.split("_date_")[1][:4]) in years) and p.stat().st_size > 0]
    if not files:
        raise FileNotFoundError(f"no PVDAQ files for system {system} in {path}; fetch the data first (scada-schema fetch <dataset>)")
    frames = [f for f in (pd.read_csv(p) for p in files) if "measured_on" in f.columns]
    raw = pd.concat(frames, ignore_index=True)
    t = to_utc(pd.to_datetime(raw["measured_on"]).dt.round("15min"), TZ)
    raw = raw.set_index(t.rename("timestamp")).drop(columns="measured_on")
    return raw[~raw.index.duplicated()].sort_index()


def detect_power_unit_switch(power: pd.Series, poa: pd.Series, min_decades: float = 1.5) -> pd.Timestamp | None:
    """First time the power channel reports watts instead of hectowatts, found from the power to irradiance ratio.

    The daily median of power over plane-of-array irradiance (daylight only) steps up by a factor of 100 at the switch. The
    day of the step is found as in the quality control ([`level_step`][scada_schema._steps.level_step], a step of at least
    ``min_decades``) and then refined to the first interval whose ratio is above the midpoint level. ``None`` when there is no
    such step.
    """
    lv = daily_log_ratio(power, poa)
    hit = None if lv is None else level_step(lv, 7, min_decades)
    if hit is None or hit[1] < 0:  # only a rise is hectowatts -> watts
        return None
    day, step = hit
    before = lv[lv.index < day].tail(7).median()
    thr = 10 ** (before + step / 2)
    win = power[(power.index >= day - pd.Timedelta("2D")) & (power.index < day + pd.Timedelta("2D"))]
    ratio = (win.where(win > 0) / poa.reindex(win.index).where(poa.reindex(win.index) > 50)).dropna()
    up = (ratio > thr).to_numpy()
    for k in range(len(up)):
        if up[k] and up[k:k + 12].mean() >= 0.9:
            return ratio.index[k]
    return day


def load(data_dir: str | Path | None = None, system: int = 34, years: list[int] | None = None, fix_units: bool = True) -> Loaded:
    """One PVDAQ system as a canonical PV table (inverters and the met station), see the module description."""
    cfg = SYSTEMS[system]
    raw = read_system(raw_dir(NAME, data_dir), system, years)
    poa = _column(raw, MET["poa"])
    parts = []
    switch_dates = {}
    step = pd.Timedelta("15min")
    for inv, cols in cfg["inverters"].items():
        p_raw = _column(raw, cols["p_ac"])
        switch = detect_power_unit_switch(p_raw, poa) if (p_raw is not None and poa is not None) else None
        switch_dates[inv] = switch
        factor = pd.Series(0.1, index=raw.index)  # hectowatts to kW
        if fix_units and switch is not None:
            factor[factor.index >= switch] = 0.001  # watts to kW
        out = pd.DataFrame({"timestamp": raw.index - step, "device_id": inv})
        for sig, prefix in cols.items():
            s = _column(raw, prefix)
            if s is None:
                continue
            if sig in ("p_ac", "p_dc"):
                s = s * factor
            elif sig == "power_factor":
                s = s / 1000.0
            out[sig] = s.to_numpy() if sig != "status_code" else s.astype("Int64").astype("string").to_numpy()
        parts.append(out)
    met = pd.DataFrame({"timestamp": raw.index - step, "device_id": "MET"})
    for sig, prefix in MET.items():
        s = _column(raw, prefix)
        if s is None:
            continue
        if sig in ("t_amb", "t_module"):
            s = F_TO_C(s)
        elif sig == "wind_speed":
            s = s * MPH_TO_MS
        met[sig] = s.to_numpy()
    parts.append(met)
    table = pd.concat(parts, ignore_index=True)
    if "status_code" in table:
        table["status_code"] = table["status_code"].astype(object).where(table["status_code"].notna(), None)
    table = finalise(table.dropna(axis=1, how="all"))
    first = next((v for v in switch_dates.values() if v is not None), None)
    if not fix_units:
        power_note = "p_ac, p_dc: all values read as hectowatts (x0.1 to kW); a switch to watts in the record is NOT corrected"
    elif first is None:
        power_note = "p_ac, p_dc: hectowatts to kW (x0.1); no switch to watts found in the record"
    else:
        power_note = f"p_ac, p_dc: hectowatts to kW (x0.1) until {first:%Y-%m-%d %H:%M} UTC, watts to kW (x0.001) from then on"
    conversions = [
        power_note, "t_amb, t_module: degrees Fahrenheit to degrees Celsius", "wind_speed: mph to m/s",
        "power_factor: raw value / 1000",
        "time stamps: local standard time (UTC-8) to UTC, rounded to 15 min, moved from the end to the start of the interval",
    ]
    devices = [Device(id=i, type="inverter", capacity_kwp=cfg["capacity"][i], lat=LAT, lon=LON, tilt_deg=cfg["tilt"],
                      azimuth_deg=cfg["azimuth"]) for i in cfg["inverters"]]
    devices.append(Device(id="MET", type="met", lat=LAT, lon=LON))
    meta = DatasetMeta(
        name=f"{NAME}_{system}", profile="pv", interval="15min", source_timezone=TZ, source_label="end", devices=devices,
        source=SOURCE, conversions=conversions,
        notes=["The AC power channel is stamped about 9 minutes later than the irradiance channel (PVDAQ_SITE_NOTES.md, pv-outage-classifier).",
               "Ambient temperature, wind speed and wind direction sensors are stuck for long stretches (see the quality report).",
               "Tilt and azimuth are those of the array where known; the irradiance sensor of system 34 has a tilt of 5 degrees."],
    )
    return Loaded(table, meta)
