"""Finding a step in the level of a series: the common core of the unit-switch check of the quality control and of the
loader that corrects PVDAQ's hectowatt-to-watt change."""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["daily_log_ratio", "level_step"]


def daily_log_ratio(power: pd.Series, reference: pd.Series, min_reference: float = 200.0, min_points: int = 8) -> pd.Series | None:
    """log10 of the daily median of ``power / reference`` where the reference (irradiance) exceeds ``min_reference``.

    Both series are indexed by time stamp; days with fewer than ``min_points`` such intervals are dropped. ``None`` when fewer
    than 14 days remain.
    """
    ref = reference.reindex(power.index)
    r = (power.where(power > 0) / ref.where(ref > min_reference)).dropna()
    if r.empty:
        return None
    g = r.groupby(r.index.floor("D"))
    d, n = g.median(), g.size()
    d = d[(n >= min_points) & (d > 0)]
    return np.log10(d) if len(d) >= 14 else None


def level_step(levels: pd.Series, window: int = 7, min_decades: float = 0.8) -> tuple[pd.Timestamp, float] | None:
    """The largest step of at least ``min_decades`` (log10) in a daily series, as ``(first day at the new level, step)``.

    The step is the difference between the median of the next ``window`` days and of the previous ``window`` days; the median
    windows smear a step over +-``window`` days, so the change is put at the first day past the midpoint level.
    """
    vals = levels.to_numpy(dtype=float)
    n = len(vals)
    if n < 2 * window + 1:
        return None
    jump = np.full(n, np.nan)
    for i in range(window, n - window):
        jump[i] = np.median(vals[i:i + window]) - np.median(vals[i - window:i])
    if not np.isfinite(jump).any():
        return None
    i = int(np.nanargmax(np.abs(jump)))
    step = float(jump[i])
    if abs(step) < min_decades:
        return None
    mid = (np.median(vals[max(i - window, 0):i]) + np.median(vals[i:i + window])) / 2
    span = np.arange(max(i - window, 0), min(i + window, n))
    cross = span[(vals[span] > mid) if step > 0 else (vals[span] < mid)]
    return levels.index[int(cross[0]) if len(cross) else i], step
