"""La Haute Borne wind farm (4 x Senvion MM82, 2.05 MW, France), 10-minute SCADA of 2014 and 2015, published by ENGIE.

ENGIE published the data on its open data platform (opendata-renewables.engie.com); this loader reads the copy that OpenOA ships as
example data (``examples/data/la_haute_borne.zip`` in the OpenOA repository, 36 762 939 bytes), because the platform's download needs an API
session. The licence is the one ENGIE states for the dataset (Etalab open licence); the project did not re-verify it against the platform.

The file has one row per turbine and interval: ``Wind_turbine_name``, ``Date_time`` (ISO 8601 with a UTC offset), ``Ba_avg`` (pitch, deg),
``P_avg`` (active power, kW), ``Ws_avg`` (wind speed, m/s), ``Va_avg`` (vane relative to the nacelle, deg), ``Ot_avg`` (outdoor temperature, degC),
``Ya_avg`` (nacelle position, deg), ``Wa_avg`` (absolute wind direction, deg). The asset table gives positions, ratings and dimensions.

**The time stamps are not what they look like.** Their offsets follow the daylight saving rules of France, but the series itself does not:
every turbine has exactly 730 days x 144 intervals, the diurnal temperature maximum falls at the same wall-clock hour in summer and in
winter (and 1 to 1.5 hours apart in "UTC" at each change), at the spring change the stamps 03:00 to 03:50 occur twice with different values
and at the autumn change the repeated hour is missing. That is the signature of a clock that ran on standard time (UTC+1) all year and was
then labelled as if it had followed daylight saving time. ``load(time="reconstructed")`` (the default) undoes it: the wall-clock part of every
stamp is taken as UTC+1 all year (UTC = wall clock - 1 h). At each spring change the stamps 03:00 to 03:50 occur twice per turbine; the two copies
are the hours 02:xx and 03:xx of the wall clock, but **which copy is which cannot be told**: the file order is not time order and the continuity of
temperature, wind speed, power and yaw does not decide it (``time_diagnostics``). Those rows are therefore dropped (a gap of two hours per turbine and
spring, recorded in the metadata) rather than guessed; every other row lands on a complete 10-minute grid. The assumption that the clock is UTC+1 is
**not documented by the source**; it is the offset the source prints in winter, and the data cannot tell it from UTC+0 (the temperature maximum would then
be at the upper end of its plausible range). ``time="as_stamped"`` keeps the printed offsets: the autumn hour stays missing and of each repeated spring stamp one
copy is kept (the number is recorded). ``time_diagnostics`` returns the numbers behind these statements.

Usage:
    fetch("data/la_haute_borne")
    loaded = load("data/la_haute_borne")
    time_diagnostics("data/la_haute_borne")
"""

from __future__ import annotations

import io
import itertools
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, download_file
from scada_schema.loaders._paths import raw_dir
from scada_schema.model import DatasetMeta, Device, Source

NAME = "la_haute_borne"
URL = "https://raw.githubusercontent.com/NREL/OpenOA/main/examples/data/la_haute_borne.zip"
ZIP = "la_haute_borne.zip"
SIZE = 36_762_939
SCADA_FILE = "la-haute-borne-data-2014-2015.csv"
ASSET_FILE = "la-haute-borne_asset_table.csv"
HOUR = pd.Timedelta("1h")
SOURCE = Source(
    name="La Haute Borne wind farm data (ENGIE, copy shipped with OpenOA)", url="https://opendata-renewables.engie.com/",
    license="Etalab open licence (as stated by ENGIE, not re-verified)",
    citation="ENGIE, La Haute Borne wind farm open data, https://opendata-renewables.engie.com/ (via OpenOA, https://github.com/NREL/OpenOA)",
)
COLUMNS = {"P_avg": "p_ac", "Ws_avg": "wind_speed", "Wa_avg": "wind_dir", "Va_avg": "wind_dir_rel", "Ya_avg": "nacelle_dir", "Ba_avg": "pitch", "Ot_avg": "t_amb"}


def fetch(data_dir: str | Path | None = None, session=None) -> list[Path]:
    """Download the OpenOA example data zip (size checked; the source publishes no checksum)."""
    dest = raw_dir(NAME, data_dir) / ZIP
    return [download_file({"size": SIZE, "md5": None, "url": URL}, dest, session)]


def _read(data_dir) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = raw_dir(NAME, data_dir) / ZIP
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; fetch the data first (scada-schema fetch {NAME})")
    with zipfile.ZipFile(path) as z:
        return pd.read_csv(io.BytesIO(z.read(SCADA_FILE))), pd.read_csv(io.BytesIO(z.read(ASSET_FILE)))


def reconstruct(raw: pd.DataFrame) -> tuple[pd.Series, int]:
    """UTC start of every row under the fixed UTC+1 reading (``NaT`` for the rows that cannot be placed) and the number of those rows.

    The rows that cannot be placed are the repeated stamps of the spring changes: both copies are real, one belongs to the wall-clock hour 02:xx and one to 03:xx.
    Raises ``ValueError`` when what remains is not a unique 10-minute grid per turbine with gaps only where such rows were removed (the reading would then be wrong).
    """
    wall = pd.to_datetime(raw["Date_time"].str[:19])
    ambiguous = raw.duplicated(["Wind_turbine_name", "Date_time"], keep=False)
    utc = (wall - HOUR).dt.tz_localize("UTC").dt.as_unit("ns").where(~ambiguous)
    for name, s in utc.groupby(raw["Wind_turbine_name"]):
        s = s.dropna()
        full = pd.date_range(s.min(), s.max(), freq="10min")
        missing = full.difference(pd.DatetimeIndex(s))
        n_amb = int(ambiguous[raw["Wind_turbine_name"] == name].sum())
        if s.duplicated().any() or len(missing) != n_amb:
            raise ValueError(f"{name}: the fixed UTC+1 reading does not give a 10-minute grid ({len(s)} rows, {len(missing)} gaps, {n_amb} rows removed as ambiguous)")
    return utc, int(ambiguous.sum())


def _phase(s: pd.DataFrame, col: pd.Series) -> float:
    """Hour of the maximum of the mean daily temperature cycle (first harmonic) of the rows in ``s``, with ``col`` as the clock."""
    h = (col.dt.hour + col.dt.minute / 60).to_numpy()
    y = s["Ot_avg"].to_numpy()
    ok = np.isfinite(y)
    h, y = h[ok], y[ok]
    y = y - pd.Series(y).groupby(col.dt.date.to_numpy()[ok]).transform("mean").to_numpy()  # remove each day's mean: weather drift
    a, b = np.sum(y * np.cos(2 * np.pi * h / 24)), np.sum(y * np.sin(2 * np.pi * h / 24))
    return float((np.degrees(np.arctan2(b, a)) / 15) % 24)


def time_diagnostics(data_dir: str | Path | None = None) -> dict:
    """The numbers behind the statements about the time stamps (module description), for the first turbine."""
    raw, _ = _read(data_dir)
    t = raw[raw["Wind_turbine_name"] == sorted(raw["Wind_turbine_name"].unique())[0]].reset_index(drop=True)
    wall = pd.to_datetime(t["Date_time"].str[:19])
    stamped = pd.to_datetime(t["Date_time"], utc=True)
    out: dict = {"rows_per_turbine": int(len(t)), "days": int(wall.dt.normalize().nunique()), "rows_on_spring_change_day": int((wall.dt.strftime("%Y-%m-%d") == "2014-03-30").sum()),
                 "rows_on_autumn_change_day": int((wall.dt.strftime("%Y-%m-%d") == "2014-10-26").sum()),
                 "repeated_stamps": int(raw.duplicated(["Wind_turbine_name", "Date_time"]).sum()),
                 "missing_hours_stamped_utc": int((len(pd.date_range(stamped.min(), stamped.max(), freq="10min")) - stamped.nunique()) / 6)}
    windows = {"before_spring": ("2014-03-05", "2014-03-29"), "after_spring": ("2014-04-02", "2014-04-26"),
               "before_autumn": ("2014-10-01", "2014-10-25"), "after_autumn": ("2014-10-28", "2014-11-21")}
    out["temperature_maximum_hour"] = {
        lab: {"wall_clock": round(_phase(t[(wall >= a) & (wall < b)], wall[(wall >= a) & (wall < b)]), 2),
              "stamped_utc": round(_phase(t[(stamped >= a) & (stamped < b)], stamped[(stamped >= a) & (stamped < b)]), 2)} for lab, (a, b) in windows.items()}
    out["spring_repeated_stamps_per_turbine"] = int(t.duplicated("Date_time", keep=False).sum())
    # which copy of a repeated spring stamp is the hour 02:xx? try all 2^6 assignments per turbine and spring and keep the one with the smoothest
    # temperature, wind speed, power and yaw; a decision needs a clear margin over the runner-up
    cols = ["Ot_avg", "Ws_avg", "P_avg", "Ya_avg"]
    sd = raw[cols].std().to_numpy()
    cases = clear = same_as_file = 0
    for _, g in raw.groupby("Wind_turbine_name"):
        g = g.reset_index(drop=True)
        stamps = sorted(g.loc[g.duplicated("Date_time", keep=False), "Date_time"].unique())
        for k in range(0, len(stamps), 6):
            block = stamps[k:k + 6]
            rows = [g.index[g["Date_time"] == st].to_numpy() for st in block]
            pre = g.index[g["Date_time"].str.startswith(block[0][:11] + "01:50")][0]
            post = g.index[g["Date_time"].str.startswith(block[0][:11] + "04:00")][0]
            scores = {}
            for ch in itertools.product((0, 1), repeat=len(block)):
                seq = g.loc[np.r_[pre, [rows[i][ch[i]] for i in range(len(block))], [rows[i][1 - ch[i]] for i in range(len(block))], post], cols].to_numpy() / sd
                scores[ch] = np.abs(np.diff(seq, axis=0)).sum()
            ranked = sorted(scores.items(), key=lambda kv: kv[1])
            cases += 1
            clear += ranked[0][1] < 0.8 * ranked[1][1]
            same_as_file += ranked[0][0] == (0,) * len(block)
    out["spring_pair_assignments"] = {"cases": cases, "clear_winner": int(clear), "equal_to_file_order": int(same_as_file)}
    return out


def load(data_dir: str | Path | None = None, devices: list[str] | None = None, time: str = "reconstructed") -> Loaded:
    """La Haute Borne SCADA of both years in the canonical wind profile (see the module description for ``time``)."""
    if time not in ("reconstructed", "as_stamped"):
        raise ValueError("time must be 'reconstructed' or 'as_stamped'")
    raw, asset = _read(data_dir)
    ids = {n: f"T{i:02d}" for i, n in enumerate(sorted(asset["Wind_turbine_name"]), 1)}
    removed = 0
    if time == "reconstructed":
        stamp, ambiguous = reconstruct(raw)
        tz, conv_time = "Etc/GMT-1", [
            "time stamps: the offsets printed in the file are ignored; the wall clock is read as UTC+1 all year (UTC = wall clock - 1 h), because the series follows standard time "
            f"(see the module description); {ambiguous} rows of repeated spring stamps cannot be assigned to the hour 02:xx or 03:xx and were dropped"]
    else:
        stamp = pd.to_datetime(raw["Date_time"], utc=True).dt.as_unit("ns")
        tz, conv_time = "Europe/Paris", ["time stamps: ISO 8601 with the printed UTC offsets read as given and converted to UTC (not reconstructed)"]
    out = pd.DataFrame({"timestamp": stamp, "device_id": raw["Wind_turbine_name"].map(ids)})
    for src, dst in COLUMNS.items():
        out[dst] = raw[src].to_numpy(dtype="float64")
    out = out.dropna(subset=["device_id"])
    ambiguous_rows = int(out["timestamp"].isna().sum())
    out = out.dropna(subset=["timestamp"])
    if devices is not None:
        out = out[out["device_id"].isin(devices)]
    n = len(out)
    out = out.drop_duplicates(["device_id", "timestamp"], keep="last")
    removed = n - len(out)
    dropped = {"duplicate_rows": removed, "ambiguous_time_rows": ambiguous_rows}
    table = finalise(out.dropna(axis=1, how="all"))
    have = set(table["device_id"])
    devs = [Device(id=ids[r.Wind_turbine_name], type="turbine", rated_kw=float(r.Rated_power), lat=float(r.Latitude), lon=float(r.Longitude),
                   hub_height_m=float(r.Hub_height_m), rotor_diameter_m=float(r.Rotor_diameter_m))
            for r in asset.itertuples() if ids[r.Wind_turbine_name] in have]
    conv = [*conv_time, "turbine names (R80711, ...) replaced by T01 to T04 in the order of the names; no unit conversion: kW, m/s, deg, degC"]
    if removed:
        conv.append(f"{removed} rows with a repeated (device, time stamp) dropped, the later one kept")
    notes = ["The source does not say which instant of the interval a stamp marks; the series begins at the start of 2014, consistent with interval starts.",
             "Ba_avg is taken as the mean blade pitch angle."]
    if time == "reconstructed":
        notes.append("The fixed UTC+1 reading is an inference, not a statement of the source: it is the offset printed in winter, and the data cannot tell it from UTC+0.")
    else:
        notes.append("As stamped, the autumn changes (2014-10-26, 2015-10-25) lack one hour in UTC and the repeated spring stamps were reduced to one copy.")
    meta = DatasetMeta(name=NAME, profile="wind", interval="10min", source_timezone=tz, source_label="start", devices=sorted(devs, key=lambda d: d.id), source=SOURCE,
                       conversions=conv, notes=notes)
    return Loaded(table, meta, removed=dropped)
