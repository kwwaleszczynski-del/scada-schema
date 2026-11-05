"""Time helpers: pandas 3 no longer defaults to nanosecond resolution, the code below does arithmetic on nanoseconds."""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_ns(s: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Time stamps as int64 nanoseconds since the epoch, whatever the resolution of the datetime column."""
    s = pd.Series(s) if isinstance(s, pd.DatetimeIndex) else s
    return s.dt.as_unit("ns").astype("int64").to_numpy()
