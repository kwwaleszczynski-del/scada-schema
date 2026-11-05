"""Reading and writing canonical tables as self-describing Parquet.

The [`DatasetMeta`][scada_schema.model.DatasetMeta] is stored in the Parquet file's key-value metadata under
``scada_schema``, so one file carries the data and what is needed to interpret it. [`finalise`][scada_schema.io.finalise]
turns a loader's frame into a canonical table (types, order, key checks).

Usage:
    write_parquet(df, meta, "kelmarsh_2019.parquet")
    df, meta = read_parquet("kelmarsh_2019.parquet")
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scada_schema.model import DatasetMeta
from scada_schema.signals import SIGNALS, split_column
from scada_schema.validate import KEY_COLUMNS, SchemaError, validate

__all__ = ["finalise", "read_meta", "read_parquet", "write_parquet"]

META_KEY = b"scada_schema"


def finalise(df: pd.DataFrame, meta: DatasetMeta | None = None) -> pd.DataFrame:
    """Make a loader's frame canonical: key types, float types by signal kind, sorted rows.

    Float32 is used for averaged signals and float64 for energies and counters (a float32 cannot hold a meter
    reading in kWh without losing the kWh). Text signals become ``object`` columns of ``str`` or ``None``.
    Rows are sorted by ``(device_id, timestamp)`` and the columns ordered: keys, then signals in registry order,
    then extension columns.
    """
    out = df.copy()
    out["device_id"] = out["device_id"].astype(str)
    out["timestamp"] = out["timestamp"].dt.as_unit("ns")
    cols = [c for c in out.columns if c not in KEY_COLUMNS]
    order = {n: i for i, n in enumerate(SIGNALS)}

    def rank(c: str):
        base, stat = split_column(c)
        return (0, order[base], stat or "") if base in SIGNALS else (1, 0, c)

    cols.sort(key=rank)
    out = out[[*KEY_COLUMNS, *cols]]
    for c in cols:
        base, _ = split_column(c)
        sig = SIGNALS.get(base)
        if sig is None:
            continue
        if sig.kind in ("text", "category"):
            s = out[c]
            out[c] = s.astype(object).where(s.notna(), None)
        elif sig.kind in ("sum", "counter"):
            out[c] = out[c].astype("float64")
        else:
            out[c] = out[c].astype("float32")
    out = out.sort_values(["device_id", "timestamp"], kind="stable").reset_index(drop=True)
    return out


def write_parquet(df: pd.DataFrame, meta: DatasetMeta, path: str | Path, check: bool = True) -> Path:
    """Write a canonical table with its metadata; by default validate first and refuse a table with errors."""
    if check:
        validate(df, meta).raise_if_errors()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), META_KEY: meta.to_json().encode()})
    pq.write_table(table, path, compression="zstd")
    return path


def read_meta(path: str | Path) -> DatasetMeta:
    """The metadata of a canonical Parquet file, without reading the data."""
    md = pq.read_schema(path).metadata or {}
    if META_KEY not in md:
        raise SchemaError(f"{path}: no scada_schema metadata; not a canonical table")
    return DatasetMeta.from_json(md[META_KEY])


def read_parquet(path: str | Path, columns: list[str] | None = None) -> tuple[pd.DataFrame, DatasetMeta]:
    """Read a canonical Parquet file: ``(table, meta)``."""
    meta = read_meta(path)
    df = pd.read_parquet(path, columns=None if columns is None else list(dict.fromkeys([*KEY_COLUMNS, *columns])))
    for c in df.columns:
        sig = SIGNALS.get(split_column(c)[0])
        if sig is not None and sig.kind in ("text", "category"):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df, meta


def empty_like(meta: DatasetMeta, columns: list[str]) -> pd.DataFrame:
    """An empty canonical frame with the given columns (used by loaders for a missing year)."""
    data = {"timestamp": pd.Series([], dtype="datetime64[ns, UTC]"), "device_id": pd.Series([], dtype=object)}
    data.update({c: pd.Series([], dtype=np.float32) for c in columns})
    return pd.DataFrame(data)
