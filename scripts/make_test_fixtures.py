"""Cut small slices out of the real open files, in their original formats, as test fixtures (tests/data/real).

The slices are copies of a few rows of the published files, kept with their licences and sources in tests/data/real/README.md. Run it
only to rebuild them; the tests use the committed result.

Usage:
    SCADA_SCHEMA_KELMARSH_DIR=... SCADA_SCHEMA_HILL_OF_TOWIE_DIR=... SCADA_SCHEMA_PVDAQ_DIR=... SCADA_SCHEMA_LA_HAUTE_BORNE_DIR=... \\
        python scripts/make_test_fixtures.py
"""

from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "tests" / "data" / "real"


def env(name: str) -> Path:
    return Path(os.environ[f"SCADA_SCHEMA_{name}_DIR"])


def kelmarsh() -> None:
    src = next(env("KELMARSH").glob("Kelmarsh_SCADA_2019_*.zip"))
    with zipfile.ZipFile(src) as z, zipfile.ZipFile(OUT / "Kelmarsh_SCADA_2019_0.zip", "w", zipfile.ZIP_DEFLATED) as out:
        for name in z.namelist():
            if "_Kelmarsh_1_" not in name and "_Kelmarsh_2_" not in name:
                continue
            text = z.read(name).decode("utf-8-sig").splitlines()
            if name.startswith("Turbine_Data"):
                head = next(i for i, line in enumerate(text) if line.startswith("# Date and time"))
                keep = text[: head + 1] + text[head + 1 + 1000: head + 1 + 1000 + 24]  # 24 rows from the second week of January
            else:
                head = next(i for i, line in enumerate(text) if line.startswith("Timestamp start"))
                keep = text[: head + 1] + text[head + 1: head + 1 + 40]
            out.writestr(name, "\n".join(keep) + "\n")
    shutil.copy(env("KELMARSH") / "Kelmarsh_WT_static.csv", OUT / "Kelmarsh_WT_static.csv")


def hill_of_towie() -> None:
    d = env("HILL_OF_TOWIE")
    with zipfile.ZipFile(d / "2021_subset.zip") as z, zipfile.ZipFile(OUT / "2021_subset.zip", "w", zipfile.ZIP_DEFLATED) as out:
        stations = None
        for table in ("tblSCTurbine", "tblSCTurGrid", "tblSCTurTemp"):
            df = pd.read_csv(z.open(f"{table}_2021_06.csv"))
            if stations is None:
                stations = sorted(df["StationId"].unique())[:2]
                stamps = sorted(df["TimeStamp"].unique())[100:112]
            sub = df[df["StationId"].isin(stations) & df["TimeStamp"].isin(stamps)]
            out.writestr(f"{table}_2021_06.csv", sub.to_csv(index=False))
        al = pd.read_csv(z.open("tblAlarmLog_2021_06.csv"))
        out.writestr("tblAlarmLog_2021_06.csv", al[al["StationNr"].isin(stations)].head(25).to_csv(index=False))
    for f in ("Hill_of_Towie_alarms_description.csv", "Hill_of_Towie_turbine_metadata.csv"):
        shutil.copy(d / f, OUT / f)
    with zipfile.ZipFile(d / "Hill_of_Towie_ShutdownDuration.zip") as z:
        sd = pd.read_csv(z.open("ShutdownDuration.csv"))
    sd["TimeStamp_StartFormat"] = sd["TimeStamp_StartFormat"].astype(str)
    start_stamps = {(pd.Timestamp(s) - pd.Timedelta("10min")).strftime("%Y-%m-%d %H:%M:%S") for s in stamps}
    sd = sd[sd["TimeStamp_StartFormat"].str[:19].isin(start_stamps)]
    with zipfile.ZipFile(OUT / "Hill_of_Towie_ShutdownDuration.zip", "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("ShutdownDuration.csv", sd.to_csv(index=False))


def pvdaq() -> None:
    d = OUT / "pvdaq" / "system_34"
    d.mkdir(parents=True, exist_ok=True)
    for day in ("2018_08_03", "2018_08_04", "2018_08_05"):
        shutil.copy(env("PVDAQ") / "system_34" / f"system_34__date_{day}.csv", d / f"system_34__date_{day}.csv")


def la_haute_borne() -> None:
    src = env("LA_HAUTE_BORNE") / "la_haute_borne.zip"
    with zipfile.ZipFile(src) as z, zipfile.ZipFile(OUT / "la_haute_borne.zip", "w", zipfile.ZIP_DEFLATED) as out:
        raw = pd.read_csv(io.BytesIO(z.read("la-haute-borne-data-2014-2015.csv")))
        names = sorted(raw["Wind_turbine_name"].unique())[:2]
        wall = raw["Date_time"].str[:19]
        keep = raw[raw["Wind_turbine_name"].isin(names) & (wall >= "2014-03-29T12:00:00") & (wall < "2014-03-30T12:00:00")]
        out.writestr("la-haute-borne-data-2014-2015.csv", keep.to_csv(index=False))
        asset = pd.read_csv(io.BytesIO(z.read("la-haute-borne_asset_table.csv")))
        out.writestr("la-haute-borne_asset_table.csv", asset[asset["Wind_turbine_name"].isin(names)].to_csv(index=False))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    kelmarsh()
    hill_of_towie()
    pvdaq()
    la_haute_borne()
    print("written to", OUT)
