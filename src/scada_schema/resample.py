"""Move a canonical table to a coarser interval (10 min to 15 min is not possible, 10 min to 30 min or 1 h and 15 min to 30 min or 1 h are).

How an interval's values combine depends on what the signal is (``Signal.kind``):

==============  ================================================================================================
kind            rule
==============  ================================================================================================
``mean``        mean of the source intervals
``circular``    mean of the unit vectors (350 and 10 degrees give 0, not 180); ``__min`` and ``__max`` of angles are dropped
``sum``         sum (energy, seconds stopped)
``counter``     last reading (a cumulative meter)
``instant``     last value
``text``        most frequent value (status code); ``category`` the same
``__min/__max`` minimum of the minima, maximum of the maxima
``__std``       pooled standard deviation of the source intervals: the mean of the variances plus the variance of the means
==============  ================================================================================================

A target interval is filled only when at least ``min_coverage`` of its source intervals have a value (default 0.8); otherwise it is missing, so
that a sum over three of six intervals is not mistaken for an hour of production. Target intervals start at multiples of the target length in UTC.

Usage:
    hourly, meta_h = resample(df, meta, "1h")
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scada_schema._time import to_ns
from scada_schema.io import finalise
from scada_schema.model import INTERVALS, DatasetMeta
from scada_schema.signals import SIGNALS, split_column

__all__ = ["resample"]


def resample(df: pd.DataFrame, meta: DatasetMeta, to: str, min_coverage: float = 0.8) -> tuple[pd.DataFrame, DatasetMeta]:
    """The table on a coarser interval, and its metadata (``interval`` changed, the step recorded in ``conversions``).

    Raises
    ------
    ValueError
        If ``to`` is not an interval of the schema, is not longer than the table's, or is not a multiple of it.
    """
    if to not in INTERVALS:
        raise ValueError(f"interval {to!r} not supported; use one of {INTERVALS}")
    src, dst = meta.step, pd.Timedelta(to)
    if dst <= src or dst % src != pd.Timedelta(0):
        raise ValueError(f"cannot go from {meta.interval} to {to}: the target must be a longer multiple of the source interval")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")
    n_src = int(dst // src)
    ns = to_ns(df["timestamp"])
    key = pd.DataFrame({"device_id": df["device_id"].to_numpy(), "bin": ns // dst.value})
    work = pd.DataFrame(index=df.index)
    plan: dict[str, str] = {}  # output column -> rule
    count_of: dict[str, str] = {}  # output column -> work column whose non-missing count decides the coverage
    base_mean = {c for c in df.columns if split_column(c)[1] is None and split_column(c)[0] in SIGNALS}
    dropped = []
    for col in df.columns:
        if col in ("timestamp", "device_id") or col.startswith("x_"):
            continue
        base, stat = split_column(col)
        sig = SIGNALS.get(base)
        if sig is None:
            continue
        v = df[col]
        if sig.kind in ("text", "category"):
            work[col], plan[col] = v, "mode"
        elif stat in ("min", "max"):
            if sig.kind == "circular":
                dropped.append(col)
                continue
            work[col], plan[col], count_of[col] = v, stat, col
        elif stat == "std":
            work[col + "__var"] = v.astype("float64") ** 2
            if base in base_mean:
                work[col + "__m"] = df[base].astype("float64")
                work[col + "__m2"] = work[col + "__m"] ** 2
            plan[col], count_of[col] = "std", col + "__var"
        elif sig.kind == "circular":
            r = np.deg2rad(v.astype("float64"))
            work[col + "__s"], work[col + "__c"] = np.sin(r), np.cos(r)
            plan[col], count_of[col] = "circ", col + "__s"
        elif sig.kind == "sum":
            work[col], plan[col], count_of[col] = v, "sum", col
        elif sig.kind in ("counter", "instant"):
            work[col], plan[col] = v, "last"
        else:
            work[col], plan[col], count_of[col] = v, "mean", col
    grouped = work.groupby([key["device_id"], key["bin"]], sort=True)
    out = pd.DataFrame(index=grouped.size().index)
    for col, how in plan.items():
        if how == "mode":
            out[col] = grouped[col].agg(lambda s: s.mode().iloc[0] if s.notna().any() else None)
        elif how == "circ":
            sn, cs = grouped[col + "__s"].mean(), grouped[col + "__c"].mean()
            out[col] = (np.rad2deg(np.arctan2(sn, cs)) % 360.0).where(sn.notna() & cs.notna())
        elif how == "std":
            var = grouped[col + "__var"].mean()
            if col + "__m" in work:
                var = var + (grouped[col + "__m2"].mean() - grouped[col + "__m"].mean() ** 2).clip(lower=0)
            out[col] = np.sqrt(var)
        elif how == "last":
            out[col] = grouped[col].agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan)
        else:
            out[col] = getattr(grouped[col], how)()
    # coverage: a target interval with too few source values is missing (the last reading of a counter is a reading however many came before)
    for col, wc in count_of.items():
        out[col] = out[col].where(grouped[wc].count() >= min_coverage * n_src)
    out = out.reset_index()
    out["timestamp"] = pd.to_datetime(out["bin"].astype("int64") * dst.value, unit="ns", utc=True).dt.as_unit("ns")
    out = out.drop(columns="bin")
    res = finalise(out.dropna(axis=1, how="all"))
    m = meta.model_copy(deep=True)
    m.interval = to
    note = f"resampled from {meta.interval} to {to} (rules by signal kind, at least {min_coverage:.0%} of the source intervals required)"
    if dropped:
        note += f"; minimum and maximum of angles dropped: {', '.join(dropped)}"
    m.conversions = [*meta.conversions, note]
    return res, m
