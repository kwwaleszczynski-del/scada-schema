"""Kelmarsh wind farm (6 x Senvion MM92, 2.05 MW, England), 2016-2021, 10-minute SCADA and status logs.

C. Plumley, Kelmarsh wind farm data, Zenodo, doi:10.5281/zenodo.5841833 (concept DOI; the files used here are in record
16807551), licence CC BY 4.0. Format: [`greenbyte`][scada_schema.loaders.greenbyte].

Usage:
    fetch("data/kelmarsh", years=[2019])
    loaded = load("data/kelmarsh", years=[2019])
    loaded.table.head(); loaded.events.head()
"""

from __future__ import annotations

from pathlib import Path

from scada_schema.loaders import greenbyte
from scada_schema.loaders._common import Loaded, download_file, zenodo_files
from scada_schema.loaders._paths import raw_dir
from scada_schema.model import Source

NAME = "kelmarsh"
RECORD = "16807551"
SOURCE = Source(
    name="Kelmarsh wind farm data (Cubico Sustainable Investments)", doi="10.5281/zenodo.5841833",
    url=f"https://zenodo.org/records/{RECORD}", license="CC-BY-4.0",
    citation="C. Plumley, Kelmarsh wind farm data, Zenodo, https://doi.org/10.5281/zenodo.5841833",
)
ARCHIVE = r"Kelmarsh_SCADA_(\d{4})_\d+\.zip"
NOTES = [
    "Commissioning data of 2016 before the commercial operation date (2016-04-15) are included; filter them for operational analyses.",
    "wind_speed is the 'Wind speed (m/s)' signal as exported; its definition changed during the dataset, wind_speed_2 is anemometer sensor 2.",
]


def fetch(data_dir: str | Path | None = None, years: list[int] | None = None, session=None) -> list[Path]:
    """Download the static files and the yearly archives (all years by default) with checksum verification."""
    import re

    dest = raw_dir(NAME, data_dir)
    files = zenodo_files(RECORD, session)
    rx = re.compile(ARCHIVE)
    wanted = [n for n in files if n.startswith("Kelmarsh_WT_static") or (
        (m := rx.fullmatch(n)) and (years is None or int(m.group(1)) in years))]
    return [download_file(files[n], dest / n, session) for n in sorted(wanted)]


def load(data_dir: str | Path | None = None, years: list[int] | None = None, devices: list[str] | None = None) -> Loaded:
    """Kelmarsh SCADA (and status log) for the years asked, from archives in ``data_dir``."""
    d = raw_dir(NAME, data_dir)
    archives = greenbyte.year_archives(d, ARCHIVE, years)
    if not archives:
        raise FileNotFoundError(f"no Kelmarsh SCADA archives for {years or 'any year'} in {d}; fetch the data first (scada-schema fetch <dataset>)")
    return greenbyte.build(NAME, archives, r"_Kelmarsh_(\d+)_", d / "Kelmarsh_WT_static.csv", SOURCE, NOTES, devices)
