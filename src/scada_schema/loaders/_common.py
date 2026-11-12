"""Shared pieces of the loaders: the result type, event table layout, downloads with checksums, Zenodo records."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from scada_schema.model import DatasetMeta

__all__ = [
    "EVENT_COLUMNS",
    "Loaded",
    "download_file",
    "empty_events",
    "make_events",
    "zenodo_files",
]

log = logging.getLogger(__name__)

EVENT_COLUMNS = ("device_id", "start", "end", "status", "code", "message", "category")
"""Layout of an event (status or alarm) log: ``device_id``, ``start`` and ``end`` (UTC, ``NaT`` = still open),
``status`` (the source's class: Stop, Warning, Informational, ...), ``code`` (string), ``message`` and ``category``
(IEC 61400-26 category or ``None``)."""

USER_AGENT = "scada-schema (open-source data tooling)"


@dataclass
class Loaded:
    """What a loader returns: the canonical table, its metadata and, if the source has one, the event log."""

    table: pd.DataFrame
    meta: DatasetMeta
    events: pd.DataFrame = field(default_factory=lambda: empty_events())
    removed: dict = field(default_factory=dict)  # rows the loader removed before the table was built, e.g. {"duplicate_rows": 3}


def empty_events() -> pd.DataFrame:
    return pd.DataFrame({
        "device_id": pd.Series([], dtype=object),
        "start": pd.Series([], dtype="datetime64[ns, UTC]"),
        "end": pd.Series([], dtype="datetime64[ns, UTC]"),
        "status": pd.Series([], dtype=object),
        "code": pd.Series([], dtype=object),
        "message": pd.Series([], dtype=object),
        "category": pd.Series([], dtype=object),
    })


def make_events(df: pd.DataFrame) -> pd.DataFrame:
    """Order the columns of an event frame and fix the types."""
    out = df.reindex(columns=list(EVENT_COLUMNS)).copy()
    for c in ("start", "end"):
        out[c] = pd.to_datetime(out[c], utc=True).dt.as_unit("ns")
    for c in ("device_id", "status", "code", "message"):
        out[c] = out[c].astype(object).where(out[c].notna(), None)
    out["category"] = out["category"].astype(object).where(out["category"].notna(), None)
    return out.sort_values(["device_id", "start"], kind="stable").reset_index(drop=True)


def new_session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def zenodo_files(record: str, session=None) -> dict[str, dict]:
    """``{file name: {"size", "md5", "url"}}`` of a Zenodo record (the version the record id points at)."""
    s = session or new_session()
    r = s.get(f"https://zenodo.org/api/records/{record}", timeout=60)
    r.raise_for_status()
    out = {}
    for f in r.json()["files"]:
        algo, _, digest = f["checksum"].partition(":")
        out[f["key"]] = {"size": f["size"], "md5": digest if algo == "md5" else None, "url": f["links"]["self"]}
    return out


def download_file(info: dict, dest: Path, session=None, chunk: int = 1 << 20) -> Path:
    """Stream one file to ``dest`` (through a ``.part`` file); check size and, when given, the MD5 checksum."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size == info["size"]:
        log.info("cached %s", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = session or new_session()
    tmp = dest.with_suffix(dest.suffix + ".part")
    h, done = hashlib.md5(), 0  # noqa: S324 - integrity check against the published checksum, not security
    with s.get(info["url"], stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for part in r.iter_content(chunk):
                fh.write(part)
                h.update(part)
                done += len(part)
    if done != info["size"] or (info.get("md5") and h.hexdigest() != info["md5"]):
        tmp.unlink()
        raise OSError(f"{dest.name}: size or checksum mismatch ({done} bytes, md5 {h.hexdigest()})")
    tmp.replace(dest)
    return dest


def select(names, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return sorted(n for n in names if rx.search(n))
