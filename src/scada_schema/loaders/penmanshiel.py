"""Penmanshiel wind farm (14 x Senvion MM82, 2.05 MW, Scotland), multi-year, 10-minute SCADA and status logs.

C. Plumley, Penmanshiel wind farm data, Zenodo, doi:10.5281/zenodo.5946807 (concept DOI; files in record 16807304),
licence CC BY 4.0. Same Greenbyte format as Kelmarsh ([`greenbyte`][scada_schema.loaders.greenbyte]); each year comes
in one or two archives (turbines 1-10 and 11-15).

Usage:
    loaded = load("data/penmanshiel", years=[2018, 2020])
"""

from __future__ import annotations

import re
from pathlib import Path

from scada_schema.loaders import greenbyte
from scada_schema.loaders._common import Loaded, download_file, zenodo_files
from scada_schema.loaders._paths import raw_dir
from scada_schema.model import Source

NAME = "penmanshiel"
RECORD = "16807304"
SOURCE = Source(
    name="Penmanshiel wind farm data (Cubico Sustainable Investments)", doi="10.5281/zenodo.5946807",
    url=f"https://zenodo.org/records/{RECORD}", license="CC-BY-4.0",
    citation="C. Plumley, Penmanshiel wind farm data, Zenodo, https://doi.org/10.5281/zenodo.5946807",
)
ARCHIVE = r"^Penmanshiel_SCADA_(\d{4})\w*?WT.*\.zip$"
NOTES = ["The farm has 14 turbines; archives are split into turbines 1-10 and 11-15."]


def fetch(data_dir: str | Path | None = None, years: list[int] | None = None, session=None) -> list[Path]:
    """Download the static file and the SCADA archives of the years asked (all by default)."""
    dest = raw_dir(NAME, data_dir)
    files = zenodo_files(RECORD, session)
    rx = re.compile(ARCHIVE)
    wanted = [n for n in files if n.startswith("Penmanshiel_WT_static") or (
        (m := rx.search(n)) and (years is None or int(m.group(1)) in years))]
    return [download_file(files[n], dest / n, session) for n in sorted(wanted)]


def load(data_dir: str | Path | None = None, years: list[int] | None = None, devices: list[str] | None = None) -> Loaded:
    """Penmanshiel SCADA (and status log) for the years asked, from archives in ``data_dir``."""
    d = raw_dir(NAME, data_dir)
    archives = greenbyte.year_archives(d, ARCHIVE, years)
    if not archives:
        raise FileNotFoundError(f"no Penmanshiel SCADA archives for {years or 'any year'} in {d}; fetch the data first (scada-schema fetch <dataset>)")
    return greenbyte.build(NAME, archives, r"_Penmanshiel_(\d+)_", d / "Penmanshiel_WT_static.csv", SOURCE, NOTES, devices)
