"""Configuration, findings and the runner of the quality checks."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from scada_schema._time import to_ns
from scada_schema.model import DatasetMeta

__all__ = ["CHECKS", "Finding", "QCConfig", "QCReport", "Ctx", "register", "run"]

SEVERITIES = ("info", "warning", "error")


class QCConfig(BaseModel):
    """Thresholds of the checks. Defaults are conservative and meant to be tightened per site."""

    model_config = ConfigDict(extra="forbid")

    # flat lines: hours a sensor must repeat one value to count as stuck
    flatline_hours: dict[str, float] = Field(default_factory=lambda: {
        "wind_speed": 3.0, "wind_speed_2": 3.0, "wind_dir": 6.0, "wind_dir_rel": 6.0, "t_amb": 12.0, "t_nacelle": 12.0,
        "t_gearbox_oil": 12.0, "t_gearbox_bearing": 12.0, "t_gen_winding": 12.0, "t_gen_bearing": 12.0,
        "t_main_bearing": 12.0, "t_transformer": 12.0, "t_inverter": 12.0, "t_module": 12.0, "humidity": 24.0,
        "pressure": 24.0, "poa": 3.0, "ghi": 3.0, "dni": 3.0, "dhi": 3.0, "v_ac": 12.0, "power_factor": 24.0,
        "p_ac": 6.0, "p_ac_rel": 6.0, "p_dc": 6.0, "v_dc": 6.0, "rotor_speed": 6.0})
    flatline_ignore_zero: list[str] = Field(default_factory=lambda: [
        "p_ac", "p_ac_rel", "p_dc", "poa", "ghi", "dni", "dhi", "rotor_speed", "wind_speed", "wind_speed_2", "v_dc", "wind_dir_rel"])
    # spikes (Hampel filter) on slowly varying signals
    spike_signals: list[str] = Field(default_factory=lambda: [
        "t_amb", "t_nacelle", "t_gearbox_oil", "t_gearbox_bearing", "t_gen_winding", "t_gen_bearing", "t_main_bearing",
        "t_transformer", "t_inverter", "t_module", "v_ac", "pressure", "humidity"])
    spike_window: int = 7
    spike_k: float = 8.0
    spike_min_scale: float = 0.5
    # wind direction
    direction_offset_deg: float = 10.0
    direction_min_pairs: int = 200
    direction_consistency_deg: float = 45.0
    # energy
    counter_tolerance_kwh: float = 0.5
    counter_tolerance_rel: float = 0.02
    energy_power_rel: float = 0.25
    # icing
    icing_temp_max: float = 3.0
    icing_temp_min: float = -15.0
    icing_min_hours: float = 1.0
    icing_power_ratio: float = 0.3
    icing_min_wind: float = 6.0
    # power-curve outliers
    curve_bin_ms: float = 0.5
    curve_k: float = 5.0
    # units
    unit_jump_decades: float = 0.8
    # time: zone whose daylight saving changes are looked for (default: the source time zone)
    dst_zone: str | None = None
    # report: episodes kept per finding
    max_episodes: int = 5


@dataclass
class Finding:
    """One result of one check for one signal of one device."""

    check: str
    severity: str
    message: str
    device: str | None = None
    signal: str | None = None
    n: int = 0
    total: int = 0
    value: float | None = None
    first: pd.Timestamp | None = None
    last: pd.Timestamp | None = None
    episodes: list[tuple[pd.Timestamp, pd.Timestamp, int]] = field(default_factory=list)
    rows: np.ndarray | None = field(default=None, repr=False)

    @property
    def share(self) -> float:
        return self.n / self.total if self.total else float("nan")

    def to_dict(self) -> dict:
        return {
            "check": self.check, "severity": self.severity, "device": self.device, "signal": self.signal, "n": self.n,
            "total": self.total, "share": None if not self.total else self.n / self.total, "value": self.value,
            "first": None if self.first is None else self.first.isoformat(),
            "last": None if self.last is None else self.last.isoformat(),
            "episodes": [(a.isoformat(), b.isoformat(), k) for a, b, k in self.episodes], "message": self.message,
        }


@dataclass
class Ctx:
    """What a check sees: the (sorted) table, the metadata, the configuration and shared helper arrays."""

    df: pd.DataFrame
    meta: DatasetMeta
    cfg: QCConfig
    ts: np.ndarray  # timestamp, int64 ns
    dev: np.ndarray  # device_id, object
    step_ns: int
    types: dict[str, str]
    segments: list[tuple[str, int, int]]  # (device, start row, end row) in the sorted table
    raw: tuple[np.ndarray, np.ndarray] | None = None  # (timestamp ns, device) of the table as given, before sorting
    events: pd.DataFrame | None = None

    def rated(self, device: str) -> float | None:
        try:
            return self.meta.device(device).rated_kw
        except KeyError:
            return None

    def has(self, col: str) -> bool:
        return col in self.df.columns and bool(self.df[col].notna().any())

    def values(self, col: str) -> np.ndarray:
        return self.df[col].to_numpy(dtype="float64", na_value=np.nan)

    def finding(self, check: str, severity: str, message: str, rows: np.ndarray | None = None, **kw) -> Finding:
        f = Finding(check=check, severity=severity, message=message, rows=rows, **kw)
        if rows is not None and len(rows):
            f.n = len(rows) if not f.n else f.n
            f.first, f.last = pd.Timestamp(self.ts[rows[0]], tz="UTC"), pd.Timestamp(self.ts[rows[-1]], tz="UTC")
            f.episodes = episodes(self, rows, self.cfg.max_episodes)
        return f


def episodes(ctx: Ctx, rows: np.ndarray, top: int) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """The longest runs (same device, consecutive intervals) among flagged ``rows`` (sorted positions)."""
    if not len(rows):
        return []
    brk = np.r_[True, (np.diff(rows) != 1) | (ctx.dev[rows[1:]] != ctx.dev[rows[:-1]])
                | (np.diff(ctx.ts[rows]) != ctx.step_ns)]
    starts = np.flatnonzero(brk)
    ends = np.r_[starts[1:], len(rows)] - 1
    lengths = ends - starts + 1
    pick = np.argsort(-lengths, kind="stable")[:top]
    pick = pick[np.argsort(starts[pick])]
    return [(pd.Timestamp(ctx.ts[rows[starts[i]]], tz="UTC"),
             pd.Timestamp(ctx.ts[rows[ends[i]]] + ctx.step_ns, tz="UTC"), int(lengths[i])) for i in pick]


CHECKS: dict[str, Callable[[Ctx], list[Finding]]] = {}


def register(name: str):
    def deco(fn):
        CHECKS[name] = fn
        fn.check_name = name
        return fn
    return deco


class QCReport:
    """Result of [`run`][scada_schema.qc.core.run]: findings, per-device coverage and the sorted table they refer to."""

    def __init__(self, meta: DatasetMeta, cfg: QCConfig, findings: list[Finding], table: pd.DataFrame,
                 coverage: pd.DataFrame, checks_run: list[str], checks_skipped: dict[str, str]):
        self.meta, self.cfg, self.findings, self.table = meta, cfg, findings, table
        self.coverage, self.checks_run, self.checks_skipped = coverage, checks_run, checks_skipped

    def to_frame(self) -> pd.DataFrame:
        cols = ["check", "severity", "device", "signal", "n", "total", "share", "value", "first", "last", "message"]
        if not self.findings:
            return pd.DataFrame(columns=cols)
        d = pd.DataFrame([f.to_dict() for f in self.findings])
        return d[cols]

    def summary(self) -> pd.DataFrame:
        """One row per (check, signal): devices affected, rows flagged, share and the worst severity."""
        f = self.to_frame()
        if f.empty:
            return f
        f["signal"] = f["signal"].fillna("")
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        g = f.groupby(["check", "signal"], sort=False)
        out = g.agg(devices=("device", "nunique"), n=("n", "sum"), total=("total", "sum"),
                    severity=("severity", lambda s: max(s, key=rank.__getitem__))).reset_index()
        out["share"] = out["n"] / out["total"].replace(0, np.nan)
        return out

    def counts(self) -> dict[str, int]:
        """Number of findings per severity."""
        return {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITIES}

    def flag_frame(self) -> pd.DataFrame:
        """``timestamp``, ``device_id`` and one bit-flag column per checked signal (bit i = i-th check that flagged the row).

        Only checks that flag individual rows contribute. ``self.checks_run`` gives the bit order.
        """
        out = self.table[["timestamp", "device_id"]].copy()
        bit = {c: 1 << i for i, c in enumerate(self.checks_run)}
        for f in self.findings:
            if f.rows is None or f.signal is None or not len(f.rows):
                continue
            col = f"flag_{f.signal}"
            if col not in out:
                out[col] = np.uint32(0)
            out.loc[out.index[f.rows], col] = out.iloc[f.rows][col].to_numpy() | np.uint32(bit[f.check])
        return out

    def to_json(self, path: str | None = None) -> str:
        doc = {
            "dataset": self.meta.name, "profile": self.meta.profile, "interval": self.meta.interval,
            "rows": int(len(self.table)), "devices": int(self.table["device_id"].nunique()),
            "first": self.table["timestamp"].min().isoformat() if len(self.table) else None,
            "last": self.table["timestamp"].max().isoformat() if len(self.table) else None,
            "checks_run": self.checks_run, "checks_skipped": self.checks_skipped, "counts": self.counts(),
            "coverage": self.coverage.to_dict(orient="records"), "findings": [f.to_dict() for f in self.findings],
        }
        text = json.dumps(doc, indent=1, default=str)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def to_html(self, path: str | None = None) -> str:
        from scada_schema.qc.report import render_html

        html = render_html(self)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
        return html


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    key = np.lexsort((to_ns(df["timestamp"]), df["device_id"].astype(str).to_numpy()))
    if (key == np.arange(len(df))).all():
        return df.reset_index(drop=True)
    return df.iloc[key].reset_index(drop=True)


def coverage_table(ctx: Ctx) -> pd.DataFrame:
    """Per device: rows, first and last time stamp, expected intervals, share present, share of values present per signal."""
    sig = [c for c in ctx.df.columns if c not in ("timestamp", "device_id") and "__" not in c and not c.startswith("x_")]
    rows = []
    for dev, a, b in ctx.segments:
        t = ctx.ts[a:b]
        expected = int((t[-1] - t[0]) // ctx.step_ns) + 1
        sub = ctx.df.iloc[a:b]
        rec = {"device_id": dev, "type": ctx.types.get(dev, "?"), "rows": b - a, "first": pd.Timestamp(t[0], tz="UTC"),
               "last": pd.Timestamp(t[-1], tz="UTC"), "expected": expected, "present": min(1.0, len(np.unique(t)) / expected)}
        for c in sig:
            if sub[c].notna().any():
                rec[c] = float(sub[c].notna().mean())
        rows.append(rec)
    return pd.DataFrame(rows)


def run(df: pd.DataFrame, meta: DatasetMeta, config: QCConfig | None = None, checks: list[str] | None = None,
        events: pd.DataFrame | None = None) -> QCReport:
    """Run the quality checks on a canonical table.

    Parameters
    ----------
    df : pandas.DataFrame
        The table (``timestamp`` UTC, ``device_id`` and signals). It may have duplicate or unsorted rows: the time checks
        report them and the other checks work on a sorted copy (``report.table``).
    meta : DatasetMeta
    config : QCConfig, optional
    checks : list of str, optional
        Names from ``CHECKS``; all by default. A check whose inputs are absent is skipped and listed in
        ``report.checks_skipped``.
    events : pandas.DataFrame, optional
        Event log; reserved for checks that use it.

    Returns
    -------
    QCReport
    """
    from scada_schema.qc import checks as _  # noqa: F401 - registers the checks

    cfg = config or QCConfig()
    raw_ts = to_ns(df["timestamp"])
    raw_dev = df["device_id"].astype(str).to_numpy()
    table = _sorted(df)
    ts = to_ns(table["timestamp"])
    dev = table["device_id"].astype(str).to_numpy()
    bounds = np.flatnonzero(np.r_[True, dev[1:] != dev[:-1]]) if len(dev) else np.array([], dtype=int)
    ends = np.r_[bounds[1:], len(dev)]
    segments = [(str(dev[a]), int(a), int(b)) for a, b in zip(bounds, ends, strict=True)]
    ctx = Ctx(table, meta, cfg, ts, dev, meta.step.value, meta.device_types(), segments, (raw_ts, raw_dev), events)
    findings: list[Finding] = []
    ran, skipped = [], {}
    for name in checks or list(CHECKS):
        try:
            out = CHECKS[name](ctx)
        except _Skip as s:
            skipped[name] = str(s)
            continue
        ran.append(name)
        findings.extend(out)
    return QCReport(meta, cfg, findings, table, coverage_table(ctx), ran, skipped)


class _Skip(Exception):
    """Raised by a check whose inputs are not in the table."""


def skip(reason: str):
    raise _Skip(reason)
