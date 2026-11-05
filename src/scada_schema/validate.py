"""Structural validation of a canonical table: does it follow the schema?

The check answers a yes/no question about the *form* of the data (columns, types, time stamps, keys, profile
requirements) and returns every problem it finds with a count, not just the first. Questions about the *quality* of
the values (flat lines, outliers, gaps, unit errors) belong to [`scada_schema.qc`][scada_schema.qc].

Rules (each issue has a code):

========================  ==========  ============================================================================
code                      level       meaning
========================  ==========  ============================================================================
missing_key_column        error       ``timestamp`` or ``device_id`` absent
timestamp_not_utc         error       ``timestamp`` is not a time-zone-aware UTC datetime
device_id_not_string      error       ``device_id`` is not a string column
unknown_column            error       column neither a canonical signal (or its ``__min/__max/__std``) nor ``x_*``
bad_dtype                 error       numeric signal not float, ``status_code`` not text, ``iec_category`` not text
duplicate_key             error       more than one row for a (device_id, timestamp)
not_sorted                error       rows not sorted by (device_id, timestamp)
off_grid                  error       time stamps not on the interval grid (needs ``meta``)
unknown_device            error       device in the data but not in ``meta.devices``
signal_not_allowed        error       signal not defined for the type of the device that carries it
missing_required          error       signal required by the profile is absent for a device type
bad_category              error       ``iec_category`` value outside the 10 categories
all_nan_column            warning     a column holds no value at all
missing_rating            warning     a turbine or inverter without ``rated_kw``
========================  ==========  ============================================================================

Usage:
    report = validate(df, meta)
    report.ok                 # True when there is no error
    report.raise_if_errors()  # SchemaError listing every error
    print(report.to_frame())
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from scada_schema._time import to_ns
from scada_schema.model import PROFILES, DatasetMeta
from scada_schema.signals import SIGNALS, split_column
from scada_schema.states import IEC_CATEGORIES

__all__ = ["Issue", "SchemaError", "ValidationReport", "validate"]

KEY_COLUMNS = ("timestamp", "device_id")


class SchemaError(ValueError):
    """A table does not follow the schema."""


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    code: str
    message: str
    count: int = 1
    column: str | None = None
    device: str | None = None


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    n_rows: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [(i.level, i.code, i.column, i.device, i.count, i.message) for i in self.issues],
            columns=["level", "code", "column", "device", "count", "message"],
        )

    def raise_if_errors(self) -> None:
        if self.errors:
            lines = [f"  {i.code}: {i.message}" for i in self.errors]
            raise SchemaError(f"{len(self.errors)} schema error(s):\n" + "\n".join(lines))


def _is_utc(dtype) -> bool:
    return isinstance(dtype, pd.DatetimeTZDtype) and str(dtype.tz) in ("UTC", "datetime.timezone.utc")


def validate(df: pd.DataFrame, meta: DatasetMeta | None = None, profile: str | None = None) -> ValidationReport:
    """Check a table against the schema.

    Parameters
    ----------
    df : pandas.DataFrame
        The canonical table: ``timestamp``, ``device_id`` and signal columns.
    meta : DatasetMeta, optional
        With it the check also covers the grid, the device list, device-type rules and the profile requirements.
    profile : {"wind", "pv"}, optional
        Profile to check without a ``meta`` (rules that need device types are then skipped).
    """
    rep = ValidationReport(n_rows=len(df))
    add = rep.issues.append

    missing = [c for c in KEY_COLUMNS if c not in df.columns]
    if missing:
        add(Issue("error", "missing_key_column", f"missing key column(s) {missing}", len(missing)))
        return rep
    if not _is_utc(df["timestamp"].dtype):
        add(Issue("error", "timestamp_not_utc", f"timestamp must be datetime64[ns, UTC], found {df['timestamp'].dtype}"))
        return rep  # nothing below makes sense without a time axis
    if not (pd.api.types.is_string_dtype(df["device_id"]) or df["device_id"].dtype == object):
        add(Issue("error", "device_id_not_string", f"device_id must be a string column, found {df['device_id'].dtype}"))

    profile = meta.profile if meta is not None else profile

    # ---------------------------------------------------------------- columns and dtypes
    for col in df.columns:
        if col in KEY_COLUMNS or col.startswith("x_"):
            continue
        base, stat = split_column(col)
        sig = SIGNALS.get(base)
        if sig is None or (stat is not None and (not sig.stats or stat not in ("min", "max", "std"))):
            add(Issue("error", "unknown_column", f"column {col!r} is not a canonical signal (extension columns start with 'x_')", 1, col))
            continue
        if sig.kind in ("text", "category"):
            ok = pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object or df[col].isna().all()
            if not ok:
                add(Issue("error", "bad_dtype", f"{col!r} must be text, found {df[col].dtype}", 1, col))
        elif not pd.api.types.is_float_dtype(df[col]):
            add(Issue("error", "bad_dtype", f"{col!r} must be a float column, found {df[col].dtype}", 1, col))
        if len(df) and df[col].isna().all():
            add(Issue("warning", "all_nan_column", f"column {col!r} holds no value", 1, col))

    # ---------------------------------------------------------------- keys
    key = df[["device_id", "timestamp"]]
    n_dup = int(key.duplicated().sum())
    if n_dup:
        add(Issue("error", "duplicate_key", f"{n_dup} rows repeat a (device_id, timestamp)", n_dup))
    if len(df) > 1:
        order = pd.MultiIndex.from_frame(key)
        if not order.is_monotonic_increasing:
            add(Issue("error", "not_sorted", "rows must be sorted by (device_id, timestamp)"))

    if "iec_category" in df.columns:
        bad = df["iec_category"].dropna()
        bad = bad[~bad.isin(IEC_CATEGORIES)]
        if len(bad):
            add(Issue("error", "bad_category", f"{len(bad)} iec_category values outside {sorted(IEC_CATEGORIES)}: {sorted(bad.unique())[:5]}", len(bad), "iec_category"))

    # ---------------------------------------------------------------- with metadata
    if meta is not None:
        step_ns = meta.step.value
        ns = to_ns(df["timestamp"])
        # NaT is int min; treat as off grid
        bad_grid = int((ns % step_ns != 0).sum())
        if bad_grid:
            add(Issue("error", "off_grid", f"{bad_grid} time stamps are not on the {meta.interval} grid (interval start, UTC)", bad_grid, "timestamp"))
        types = meta.device_types()
        ids = pd.Series(df["device_id"].unique())
        unknown = sorted(set(ids) - set(types))
        if unknown:
            add(Issue("error", "unknown_device", f"devices {unknown[:5]} are not listed in meta.devices", len(unknown)))
        signal_cols = [c for c in df.columns if c not in KEY_COLUMNS and not c.startswith("x_") and split_column(c)[0] in SIGNALS]
        dtype_of = df["device_id"].map(types)
        for dtype in sorted(set(dtype_of.dropna())):
            sub = df[dtype_of == dtype]
            present = {c for c in signal_cols if sub[c].notna().any()}
            for c in sorted(present):
                sig = SIGNALS[split_column(c)[0]]
                if dtype not in sig.devices:
                    n = int(sub[c].notna().sum())
                    add(Issue("error", "signal_not_allowed", f"{c!r} is not defined for {dtype} devices ({n} values)", n, c))
            prof = PROFILES[meta.profile]
            for req in prof.required.get(dtype, ()):
                if req not in present:
                    add(Issue("error", "missing_required", f"{dtype} devices need {req!r} in the {meta.profile} profile", 1, req))
            for group in prof.any_of.get(dtype, ()):
                if not present & set(group):
                    add(Issue("error", "missing_required", f"{dtype} devices need one of {list(group)} in the {meta.profile} profile", 1))
        for d in meta.devices:
            if d.type in ("turbine", "inverter") and d.rated_kw is None:
                add(Issue("warning", "missing_rating", f"{d.id}: no rated_kw (unit and power-curve checks are weaker)", 1, device=d.id))
    elif profile is not None:
        allowed = {n for n, s in SIGNALS.items() if profile in s.profiles}
        for col in df.columns:
            b = split_column(col)[0]
            if b in SIGNALS and b not in allowed:
                add(Issue("error", "signal_not_allowed", f"{col!r} is not part of the {profile} profile", 1, col))
    return rep


def is_sorted(df: pd.DataFrame) -> bool:
    key = np.lexsort((to_ns(df["timestamp"]), df["device_id"].astype(str).to_numpy()))
    return bool((key == np.arange(len(df))).all())
