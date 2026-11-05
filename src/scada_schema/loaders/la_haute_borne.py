"""La Haute Borne wind farm (4 x Senvion MM82, 2.05 MW, France), 10-minute SCADA of 2014 and 2015, published by ENGIE.

ENGIE published the data on its open data platform (opendata-renewables.engie.com); this loader reads the copy that OpenOA ships as
example data (``examples/data/la_haute_borne.zip`` in the OpenOA repository, 36 762 939 bytes), because the platform's download needs an API
session. The licence is the one ENGIE states for the dataset (Etalab open licence); the project did not re-verify it against the platform.

The file has one row per turbine and interval: ``Wind_turbine_name``, ``Date_time`` (ISO 8601 **with the UTC offset**, so no daylight saving
guesswork), ``Ba_avg`` (pitch, deg), ``P_avg`` (active power, kW), ``Ws_avg`` (wind speed, m/s), ``Va_avg`` (vane relative to the nacelle, deg),
``Ot_avg`` (outdoor temperature, degC), ``Ya_avg`` (nacelle position, deg), ``Wa_avg`` (absolute wind direction, deg). The asset table gives positions,
ratings and dimensions. The platform does not say which instant of the interval a time stamp marks; the series starts at 2014-01-01 00:00 UTC, which
is what a start stamp gives, so the loader treats them as interval starts and says so in the notes.

Usage:
    fetch("data/la_haute_borne")
    loaded = load("data/la_haute_borne")
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

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


def load(data_dir: str | Path | None = None, devices: list[str] | None = None) -> Loaded:
    """La Haute Borne SCADA of both years in the canonical wind profile."""
    path = raw_dir(NAME, data_dir) / ZIP
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; fetch the data first (scada-schema fetch {NAME})")
    with zipfile.ZipFile(path) as z:
        raw = pd.read_csv(io.BytesIO(z.read(SCADA_FILE)))
        asset = pd.read_csv(io.BytesIO(z.read(ASSET_FILE)))
    ids = {n: f"T{i:02d}" for i, n in enumerate(sorted(asset["Wind_turbine_name"]), 1)}
    out = pd.DataFrame({"timestamp": pd.to_datetime(raw["Date_time"], utc=True).dt.as_unit("ns"), "device_id": raw["Wind_turbine_name"].map(ids)})
    for src, dst in COLUMNS.items():
        out[dst] = raw[src].to_numpy(dtype="float64")
    out = out.dropna(subset=["device_id"])
    if devices is not None:
        out = out[out["device_id"].isin(devices)]
    out = out.drop_duplicates(["device_id", "timestamp"])
    table = finalise(out.dropna(axis=1, how="all"))
    have = set(table["device_id"])
    devs = [Device(id=ids[r.Wind_turbine_name], type="turbine", rated_kw=float(r.Rated_power), lat=float(r.Latitude), lon=float(r.Longitude),
                   hub_height_m=float(r.Hub_height_m), rotor_diameter_m=float(r.Rotor_diameter_m))
            for r in asset.itertuples() if ids[r.Wind_turbine_name] in have]
    meta = DatasetMeta(
        name=NAME, profile="wind", interval="10min", source_timezone="Europe/Paris", source_label="start", devices=sorted(devs, key=lambda d: d.id), source=SOURCE,
        conversions=["time stamps: ISO 8601 with the UTC offset (+01:00 / +02:00) read exactly and converted to UTC",
                     "turbine names (R80711, ...) replaced by T01 to T04 in the order of the names; no unit conversion: kW, m/s, deg, degC"],
        notes=["The source does not say which instant of the interval a stamp marks; the series begins at 2014-01-01 00:00 UTC, consistent with interval starts.",
               "Ba_avg is taken as the mean blade pitch angle.",
               "At both autumn daylight saving changes (2014-10-26, 2015-10-25) one hour is missing in UTC, 00:00 to 01:00 UTC, the first occurrence of 02:00 to 03:00 local time: "
               "the source holds only one of the two repeated hours."],
    )
    return Loaded(table, meta)
