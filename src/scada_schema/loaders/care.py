"""CARE to Compare: 95 turbine datasets of three wind farms with labelled anomaly events.

M. Gück, C. Kühne et al., CARE to Compare, Zenodo, doi:10.5281/zenodo.15846963, licence **CC BY-SA 4.0** (the share-alike
condition applies to the data and to datasets derived from it, not to this package's code). Farm A is the open EDP
onshore farm in Portugal, farms B and C are anonymised offshore farms in Germany. Each CSV is one turbine's 10-minute
SCADA (a year of training data and a prediction section), with ``status_type_id`` (0 normal, 1 derated, 2 idling,
3 service, 4 downtime, 5 other) and columns ``<sensor>_avg``, ``_min``, ``_max``, ``_std``.

**Power is not in kW.** The feature descriptions say ``kW`` and ``kvar``, but the benchmark applies an "anonymisation-related scaling of
power-related data" (Zenodo description of the record) and does not publish the rated power: in farm A the grid power of event 0 ranges
from a little below 0 to about 1, in farms B and C the maxima are about 1. The loader therefore stores power and reactive power as ``p_ac_rel`` and
``q_ac_rel`` (dimensionless, scaled by the source) and never as ``p_ac`` in kW; tools that need kW (wind-up, OpenOA) refuse such a table. Temperatures,
speeds, angles and rotor speed are not described as scaled and are taken as given.

The sensors are anonymised per farm (``sensor_12`` ...), so the loader holds a table per farm from the farm's
``feature_description.csv``; at load time every mapped sensor's description is checked against an expected phrase, so a
wrong entry raises instead of silently mislabelling a channel. Farm C is mapped only for power, wind speed, rotor speed,
ambient temperature and the relative wind direction. **Time stamps are anonymised** (shifted per dataset): they carry no
information about the time of day or year, so checks that need real time (daylight, DST, icing season) do not apply.

One call loads one dataset (one turbine, one event); the extension columns ``x_train_test`` and the event table keep what
the benchmark adds.

Usage:
    loaded = load("data/care", farm="A", event_id=0)
    loaded.events          # the labelled event of this dataset
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from scada_schema.io import finalise
from scada_schema.loaders._common import Loaded, make_events, new_session, zenodo_files
from scada_schema.loaders._paths import raw_dir
from scada_schema.loaders._remotezip import extract_members
from scada_schema.model import DatasetMeta, Device, Source
from scada_schema.states import map_labels

NAME = "care"
RECORD = "15846963"
ROOT = "CARE_To_Compare"
FARMS = ("A", "B", "C")
SOURCE = Source(
    name="CARE to Compare", doi="10.5281/zenodo.15846963", url=f"https://zenodo.org/records/{RECORD}", license="CC-BY-SA-4.0",
    citation="M. Gück et al., CARE to Compare, Zenodo, https://doi.org/10.5281/zenodo.15846963",
)

# canonical signal -> (sensor, phrase expected in its description); several sensors for a signal are averaged
MAPS: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "A": {
        "p_ac_rel": (("power_30",), "grid power"), "q_ac_rel": (("sensor_31",), "grid reactive power"),
        "wind_speed": (("wind_speed_3",), "windspeed"), "wind_dir": (("sensor_1",), "wind absolute direction"),
        "wind_dir_rel": (("sensor_2",), "wind relative direction"), "pitch": (("sensor_5",), "pitch angle"),
        "nacelle_dir": (("sensor_42",), "nacelle direction"), "rotor_speed": (("sensor_52",), "rotor rpm"),
        "t_amb": (("sensor_0",), "ambient temperature"), "t_nacelle": (("sensor_43",), "nacelle temperature"),
        "t_gearbox_oil": (("sensor_12",), "oil in gearbox"), "t_gearbox_bearing": (("sensor_11",), "gearbox bearing on high speed"),
        "t_gen_bearing": (("sensor_13",), "generator bearing 2"), "t_gen_winding": (("sensor_15",), "stator windings phase 1"),
    },
    "B": {
        "p_ac_rel": (("power_62",), "active power"), "q_ac_rel": (("reactive_power_11",), "reactive power"),
        "wind_speed": (("wind_speed_61",), "wind speed"), "wind_speed_2": (("wind_speed_60",), "wind speed 2"),
        "wind_dir": (("sensor_4",), "absolute wind direction"), "pitch": (("sensor_10",), "pitch angle"),
        "nacelle_dir": (("sensor_21",), "nacelle direction"), "rotor_speed": (("sensor_25",), "rotor speed"),
        "t_amb": (("sensor_8",), "outside temperature"), "t_gearbox_oil": (("sensor_38",), "gearbox oil inlet"),
        "t_gearbox_bearing": (("sensor_34",), "gearbox bearing temperature 1"),
        "t_gen_bearing": (("sensor_32",), "generator bearing temperature 1"),
        "t_main_bearing": (("sensor_51",), "rotor bearing temperature 1"),
    },
    "C": {
        "p_ac_rel": (("power_6",), "active power hv grid"), "q_ac_rel": (("reactive_power_122",), "reactive power hv grid"),
        "wind_speed": (("wind_speed_236",), "wind speed 1+2"), "wind_dir_rel": (("sensor_123",), "relative wind direction 1+2"),
        "rotor_speed": (("sensor_144",), "rotor speed 1"), "t_amb": (("sensor_7",), "ambient temperature"),
    },
}
STAT = {"avg": "", "min": "__min", "max": "__max", "std": "__std"}


def _zip_path(d: Path) -> Path:
    found = sorted(d.glob("CARE_To_Compare*.zip"))
    if not found:
        raise FileNotFoundError(f"no CARE_To_Compare zip in {d}; fetch the data first (scada-schema fetch <dataset>)")
    return found[0]


def _csv(z: zipfile.ZipFile, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(z.read(name)), sep=None, engine="python", encoding="utf-8", encoding_errors="replace", **kw)


def feature_table(z: zipfile.ZipFile, farm: str) -> pd.DataFrame:
    d = _csv(z, f"{ROOT}/Wind Farm {farm}/feature_description.csv")
    d.columns = [c.strip() for c in d.columns]
    return d.set_index("sensor_name")


def event_info(z: zipfile.ZipFile, farm: str) -> pd.DataFrame:
    return _csv(z, f"{ROOT}/Wind Farm {farm}/event_info.csv")


def list_datasets(data_dir: str | Path | None = None, farm: str = "A") -> pd.DataFrame:
    """The labelled events of one farm (``event_id``, ``event_label``, ``event_start``, ``event_end``, ``event_description``)."""
    with zipfile.ZipFile(_zip_path(raw_dir(NAME, data_dir))) as z:
        return event_info(z, farm)


def check_map(farm: str, features: pd.DataFrame) -> None:
    """Raise when a mapped sensor is not described as expected (guards against a changed or mistaken table)."""
    for sig, (sensors, phrase) in MAPS[farm].items():
        for s in sensors:
            if s not in features.index:
                raise KeyError(f"farm {farm}: sensor {s!r} for {sig} not in feature_description.csv")
            if phrase not in str(features.loc[s, "description"]).lower():
                raise ValueError(f"farm {farm}: {s} is {features.loc[s, 'description']!r}, expected it to mention {phrase!r} for {sig}")


def fetch(data_dir: str | Path | None = None, datasets: dict[str, list[int]] | None = None, session=None) -> Path:
    """Copy the metadata and the chosen datasets (``{"A": [0, 3], "B": [1]}``) out of the benchmark zip by range requests.

    Nothing but the named files is downloaded (the archive is several GB). Without ``datasets`` the files of one dataset per
    farm are fetched.
    """
    s = session or new_session()
    files = zenodo_files(RECORD, s)
    name = next(n for n in files if n.endswith(".zip"))
    dest = raw_dir(NAME, data_dir)
    datasets = datasets or {"A": [0]}
    wanted = [rf"^{ROOT}/Wind Farm {f}/(event_info|feature_description)\.csv$" for f in datasets]
    wanted += [rf"^{ROOT}/Wind Farm {f}/datasets/{e}\.csv$" for f, ids in datasets.items() for e in ids]
    return extract_members(s, files[name], re.compile("|".join(wanted)), dest / "CARE_To_Compare_subset.zip")


def load(data_dir: str | Path | None = None, farm: str = "A", event_id: int = 0) -> Loaded:
    """One CARE dataset (one turbine, one labelled event) in the canonical wind profile."""
    with zipfile.ZipFile(_zip_path(raw_dir(NAME, data_dir))) as z:
        feats = feature_table(z, farm)
        check_map(farm, feats)
        info = event_info(z, farm)
        raw = pd.read_csv(io.BytesIO(z.read(f"{ROOT}/Wind Farm {farm}/datasets/{event_id}.csv")), sep=None, engine="python")
    asset = str(int(raw["asset_id"].iloc[0]))
    device = f"{farm}{int(asset):03d}"
    out = pd.DataFrame({"timestamp": pd.to_datetime(raw["time_stamp"]).dt.tz_localize("UTC").dt.as_unit("ns"), "device_id": device})
    for sig, (sensors, _) in MAPS[farm].items():
        for stat, suffix in STAT.items():
            cols = [f"{s}_{stat}" for s in sensors if f"{s}_{stat}" in raw]
            if cols:
                out[f"{sig}{suffix}"] = raw[cols].mean(axis=1).to_numpy(dtype="float64")
    code = raw["status_type_id"].astype("Int64").astype("string")
    out["status_code"] = code.astype(object).where(code.notna(), None)
    out["iec_category"] = map_labels(code, "care")
    out["x_train_test"] = raw["train_test"].astype(object).to_numpy()
    out = finalise(out.dropna(axis=1, how="all"))
    row = info[info["event_id"] == event_id]
    ev = pd.DataFrame({
        "device_id": device, "start": pd.to_datetime(row["event_start"]).dt.tz_localize("UTC"),
        "end": pd.to_datetime(row["event_end"]).dt.tz_localize("UTC"), "status": row["event_label"].to_numpy(),
        "code": str(event_id), "message": row["event_description"].astype(object).where(row["event_description"].notna(), None).to_numpy(),
        "category": None,
    })
    meta = DatasetMeta(
        name=f"{NAME}_{farm}_{event_id}", profile="wind", interval="10min", source_timezone="UTC", source_label="start",
        devices=[Device(id=device, type="turbine")], source=SOURCE,
        conversions=["sensor_x_avg, _min, _max, _std to <signal>, <signal>__min, __max, __std (mapped sensors only)",
                     "power and reactive power are NOT converted to kW or kvar: the benchmark scales them (anonymisation) and publishes no rated power, "
                     "so they are stored as p_ac_rel and q_ac_rel, dimensionless",
                     "status_type_id to status_code; iec_category from the benchmark's status ids (0 normal, 1 derated, 2 idling, "
                     "3 service, 4 downtime, 5 other), an interpretation the benchmark does not publish as IEC categories"],
        notes=["Active and reactive power are in the benchmark's scaled unit (p_ac_rel, q_ac_rel), not in kW: the feature description says kW, the Zenodo record says the "
               "power-related data are scaled for anonymisation and the rated power is unknown.",
               "Time stamps are anonymised and shifted per dataset: they carry no calendar information.",
               f"farm {farm}, event {event_id}: one turbine's data with a training and a prediction section (x_train_test)."],
    )
    return Loaded(out, meta, make_events(ev), removed={"duplicate_rows": 0})
