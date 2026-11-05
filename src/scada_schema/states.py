"""Turbine states and alarm codes mapped to the information categories of IEC 61400-26.

Three pieces:

1. **The vocabulary**: ten categories (full performance, partial performance, technical standby, out of
   environmental specification, requested shutdown, out of electrical specification, scheduled maintenance, planned
   corrective action, forced outage, information unavailable). They are the top levels of the information model of
   IEC 61400-26-1 as used by [`res_asset_kpi.wind.availability`][res_asset_kpi.wind.availability]; the standard's tree
   is finer, and its definitions and their contractual use must be checked against the standard and the contract.
2. **Code tables** that translate the status codes of a controller family or the status labels of a data source into
   a category: shipped for the Senvion MM series and the Siemens SWT 2.3 as logged in the open datasets, the
   Greenbyte labels and the CARE benchmark status. They were written from the public code texts of the open
   datasets and are a starting point, not an OEM document; codes they do not know stay ``None``.
3. **An event log to interval projection**: a stop event labels every interval whose midpoint it covers.

Usage:
    table = load_code_table("senvion_mm")
    cat = map_codes(df["status_code"], "senvion_mm")           # category per row, None where unknown
    df["iec_category"] = categories_for_intervals(events, df, "10min")
"""

from __future__ import annotations

from importlib import resources

import numpy as np
import pandas as pd
from res_asset_kpi.wind.availability import CATEGORIES as _CATEGORIES
from res_asset_kpi.wind.availability import CONTRACTS, availability, category_totals

from scada_schema._time import to_ns

__all__ = [
    "CODE_TABLES",
    "CONTRACTS",
    "IEC_CATEGORIES",
    "LABEL_SCHEMES",
    "availability_from_table",
    "attach_states",
    "categories_for_intervals",
    "code_table_coverage",
    "load_code_table",
    "map_codes",
    "map_labels",
    "project_events",
]

IEC_CATEGORIES: dict[str, str] = dict(_CATEGORIES)
"""Category name -> description."""

CODE_TABLES = ("senvion_mm", "siemens_swt_hot")
"""Shipped code tables (controller families)."""
LABEL_SCHEMES = ("greenbyte", "care")
"""Shipped label tables (sources that publish a status label per event or interval)."""


def _data(name: str) -> pd.DataFrame:
    path = resources.files("scada_schema") / "data" / name
    with resources.as_file(path) as p:
        return pd.read_csv(p, dtype=str, keep_default_na=False)


def load_code_table(name: str) -> pd.DataFrame:
    """Code table of a controller family: ``code``, ``category``, ``stopping``, ``component``, ``description``.

    ``category`` is empty where a code carries no category (warnings, information).
    """
    if name not in CODE_TABLES:
        raise KeyError(f"unknown code table {name!r}; shipped: {list(CODE_TABLES)}")
    t = _data(f"codes_{name}.csv")
    bad = sorted(set(t["category"]) - set(IEC_CATEGORIES) - {""})
    if bad:
        raise ValueError(f"code table {name}: categories {bad} are not IEC categories")
    return t


def _norm_code(codes: pd.Series) -> pd.Series:
    return codes.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def map_codes(codes: pd.Series, table: str | pd.DataFrame) -> pd.Series:
    """Category of every status code; ``None`` for codes without a category or not in the table.

    Parameters
    ----------
    codes : pandas.Series
        Status or alarm codes as logged (strings or numbers; ``12.0`` is read as ``12``).
    table : str or DataFrame
        A shipped code table name or a frame with ``code`` and ``category`` columns.
    """
    t = load_code_table(table) if isinstance(table, str) else table
    lookup = dict(zip(_norm_code(t["code"]), t["category"].replace("", None), strict=True))
    return _norm_code(codes).map(lookup).astype(object).where(lambda s: s.notna(), None)


def map_labels(labels: pd.Series, scheme: str) -> pd.Series:
    """Category of every status label of a source (``greenbyte`` IEC labels, ``care`` status ids)."""
    if scheme not in LABEL_SCHEMES:
        raise KeyError(f"unknown scheme {scheme!r}; shipped: {list(LABEL_SCHEMES)}")
    t = _data("iec_labels.csv")
    t = t[t["scheme"] == scheme]
    lookup = dict(zip(t["label"], t["category"], strict=True))
    return _norm_code(labels).map(lookup).astype(object).where(lambda s: s.notna(), None)


def code_table_coverage(codes: pd.Series, table: str | pd.DataFrame) -> dict[str, float]:
    """How much of a log a code table explains: share of rows whose code is in the table and share with a category."""
    t = load_code_table(table) if isinstance(table, str) else table
    c = _norm_code(codes.dropna())
    known = c.isin(set(_norm_code(t["code"])))
    cat = map_codes(c, t).notna()
    n = max(len(c), 1)
    return {"rows": float(len(c)), "in_table": float(known.sum()) / n, "with_category": float(cat.sum()) / n}


def project_events(
    events: pd.DataFrame,
    table: pd.DataFrame,
    interval: str = "10min",
    columns: tuple[str, ...] = ("category", "code"),
    only: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Project an event log onto the intervals of a canonical table.

    An event labels an interval when the interval's **midpoint** lies in ``[start, end)``; where events overlap, the one
    that started last wins. Rows that no event covers get ``NaN``/``None`` in every column.

    Parameters
    ----------
    events : pandas.DataFrame
        ``device_id``, ``start``, ``end`` (UTC; ``NaT`` = still open) and the columns asked for.
    table : pandas.DataFrame
        The canonical table (``timestamp`` = interval start, ``device_id``).
    interval : str
        Interval length of the table.
    columns : tuple of str
        Event columns to carry onto the rows.
    only : dict, optional
        Keep only events whose column equals the value, e.g. ``{"status": "Stop"}``.

    Returns
    -------
    pandas.DataFrame
        One row per row of ``table`` (same index) with the requested columns.
    """
    half = pd.Timedelta(interval).value // 2
    ev = events
    for col, val in (only or {}).items():
        ev = ev[ev[col] == val]
    ev = ev.sort_values("start", kind="stable")
    if len(ev) and ev["end"].isna().any():
        ev = ev.assign(end=ev["end"].fillna(table["timestamp"].max() + pd.Timedelta(interval)))
    which = np.full(len(table), -1, dtype=np.int64)
    ts_ns = to_ns(table["timestamp"])
    dev = table["device_id"].to_numpy()
    ev = ev.reset_index(drop=True)
    for d, g in ev.groupby("device_id", sort=False):
        rows = np.flatnonzero(dev == d)
        if not len(rows):
            continue
        mid = ts_ns[rows] + half
        order = np.argsort(mid, kind="stable")
        rows, mid = rows[order], mid[order]
        for k, s, e in zip(g.index, to_ns(g["start"]), to_ns(g["end"]), strict=True):
            lo, hi = np.searchsorted(mid, s, side="left"), np.searchsorted(mid, e, side="left")
            if hi > lo:
                which[rows[lo:hi]] = k
    out = {}
    hit = which >= 0
    for c in columns:
        vals = np.full(len(table), None, dtype=object)
        vals[hit] = ev[c].to_numpy(dtype=object)[which[hit]]
        out[c] = vals
    return pd.DataFrame(out, index=table.index, dtype=object)


def categories_for_intervals(events: pd.DataFrame, table: pd.DataFrame, interval: str = "10min", only: dict | None = None) -> pd.Series:
    """IEC category per interval from an event log: the event covering the midpoint, else ``full_performance``.

    A turbine without a stop event is read as operating, which is only as good as the log. Events without a category
    (warnings, information) are ignored.
    """
    ev = events.dropna(subset=["category"])
    cat = project_events(ev, table, interval, ("category",), only)["category"]
    return cat.where(cat.notna(), "full_performance").where(table["timestamp"].notna(), None)


def attach_states(loaded, only: dict | None = None):
    """Add ``status_code`` and ``iec_category`` to a loaded wind dataset from its event log (in place; returns it).

    Uses the stop events (``status == "Stop"``) that carry an IEC category; see
    [`project_events`][scada_schema.states.project_events] for the rule. Intervals without a stop event are
    ``full_performance``.
    """
    only = {"status": "Stop"} if only is None else only
    ev = loaded.events.dropna(subset=["category"])
    hit = project_events(ev, loaded.table, loaded.meta.interval, ("category", "code"), only)
    loaded.table["status_code"] = hit["code"]
    loaded.table["iec_category"] = hit["category"].where(hit["category"].notna(), "full_performance")
    return loaded


def availability_from_table(
    table: pd.DataFrame,
    interval: str,
    contract: str = "iec_operational",
    allowance_h: float = 0.0,
    device: str | None = None,
):
    """Time-based and energy-based availability of a canonical table through ``res_asset_kpi``.

    Needs ``iec_category``; ``e_export`` (kWh per interval) is used as actual energy when present. Lost energy is not
    known here, so the energy-based figure is the share of actual energy in the categories counted available; use
    [`res_asset_kpi.wind.availability`][res_asset_kpi.wind.availability] directly with a lost-energy estimate for contract work.
    """
    t = table if device is None else table[table["device_id"] == device]
    h = pd.Timedelta(interval) / pd.Timedelta("1h")
    actual = t["e_export"] if "e_export" in t.columns else None
    tot = category_totals(t["iec_category"].to_numpy(), h, actual_kwh=None if actual is None else actual.to_numpy())
    n_years = max(len(t) * h / 8766.0, 1e-9)
    return availability(tot, contract, allowance_h=allowance_h, n_turbine_years=n_years)
