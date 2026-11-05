"""The quality checks. Each takes a [`Ctx`][scada_schema.qc.core.Ctx] and returns findings; each is registered under a name.

========================  =====================================================================================
check                     question
========================  =====================================================================================
``duplicates``            are there rows that repeat a (device, time stamp)?
``ordering``              are the rows in time order within a device?
``time_jumps``            do time stamps leave the interval grid or step by less than one interval?
``gaps``                  how many intervals are missing between the first and the last time stamp?
``dst``                   do gaps or repeats of an hour at the daylight saving changes show up?
``missing_values``        how much of each signal is empty?
``out_of_range``          values outside the hard physical limits of the signal (unit and scaling errors)
``flatline``              a sensor repeating one value for hours
``spikes``                single values far from their neighbours (Hampel filter)
``power_curve``           power far from the median power curve of the same turbine at that wind speed
``wind_direction``        vane, nacelle and absolute direction that do not add up; north offsets between turbines
``energy_counter``        meter readings that go backwards or jump; counter steps against interval energy
``energy_power``          interval energy against mean power times the interval length
``icing``                 anemometer reading zero while the rotor turns in the cold; power loss in icing conditions
``units``                 power of the wrong order of magnitude, a switch of unit within a record, hints of other units
========================  =====================================================================================

A finding is a statement about the data, not a verdict on the sensor: a flat line of zeros is normal at night, a power
far below the curve is normal during a stop. Where the schema's own columns can exclude the normal cases (``shutdown_s``,
``iec_category``) the checks use them; otherwise read the findings with the plant in mind.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scada_schema._steps import level_step
from scada_schema.qc.core import Ctx, Finding, register, skip
from scada_schema.signals import SIGNALS

NON_OPERATING = ("scheduled_maintenance", "planned_corrective", "forced_outage", "requested", "electrical", "no_info")


def _sev_by_share(share: float, warn: float = 0.01, err: float = 0.10) -> str:
    return "error" if share >= err else "warning" if share >= warn else "info"


def _by_device(ctx: Ctx, rows: np.ndarray) -> dict[str, np.ndarray]:
    out = {}
    for dev, a, b in ctx.segments:
        lo, hi = np.searchsorted(rows, a, "left"), np.searchsorted(rows, b, "left")
        if hi > lo:
            out[dev] = rows[lo:hi]
    return out


def _signal_columns(ctx: Ctx) -> list[str]:
    return [c for c in ctx.df.columns if c not in ("timestamp", "device_id") and "__" not in c and c in SIGNALS
            and SIGNALS[c].kind not in ("text", "category")]


def _circ(a: np.ndarray) -> np.ndarray:
    """Wrap angles to [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0


# ------------------------------------------------------------------------------------------------ time


@register("duplicates")
def duplicates(ctx: Ctx) -> list[Finding]:
    ts, dev = ctx.raw
    key = pd.MultiIndex.from_arrays([dev, ts])
    dup = key.duplicated(keep="first")
    n = int(dup.sum())
    if not n:
        return []
    by = pd.Series(dev[dup]).value_counts()
    return [Finding("duplicates", "error", f"{int(c)} rows repeat an earlier (device, time stamp)", device=str(d), n=int(c),
                    total=int((dev == d).sum())) for d, c in by.items()]


@register("ordering")
def ordering(ctx: Ctx) -> list[Finding]:
    ts, dev = ctx.raw
    back = (ts[1:] < ts[:-1]) & (dev[1:] == dev[:-1])
    n = int(back.sum())
    if not n:
        return []
    return [Finding("ordering", "error", f"{n} rows are earlier than the row before them within a device", n=n, total=len(ts))]


@register("time_jumps")
def time_jumps(ctx: Ctx) -> list[Finding]:
    out = []
    for dev, a, b in ctx.segments:
        t = np.unique(ctx.ts[a:b])
        d = np.diff(t)
        off = d % ctx.step_ns != 0
        short = d < ctx.step_ns
        n = int((off | short).sum())
        if n:
            out.append(Finding("time_jumps", "error", f"{n} steps are not a whole number of {ctx.meta.interval}", device=dev,
                               n=n, total=len(d), value=float(np.median(d[off | short]) / 6e10)))
    return out


@register("gaps")
def gaps(ctx: Ctx) -> list[Finding]:
    out = []
    h = ctx.step_ns / 3.6e12
    for dev, a, b in ctx.segments:
        t = np.unique(ctx.ts[a:b])
        d = np.diff(t)
        gap = (d > ctx.step_ns) & (d % ctx.step_ns == 0)
        missing = int(((d[gap] // ctx.step_ns) - 1).sum())
        expected = int((t[-1] - t[0]) // ctx.step_ns) + 1
        if not missing:
            continue
        idx = np.flatnonzero(gap)
        n_m = (d[idx] // ctx.step_ns) - 1
        pick = np.argsort(-n_m, kind="stable")[: ctx.cfg.max_episodes]
        eps = sorted((pd.Timestamp(t[idx[i]] + ctx.step_ns, tz="UTC"), pd.Timestamp(t[idx[i] + 1], tz="UTC"), int(n_m[i])) for i in pick)
        f = Finding("gaps", _sev_by_share(missing / expected), f"{missing} of {expected} intervals missing, longest gap {n_m.max() * h:.1f} h",
                    device=dev, n=missing, total=expected, value=float(n_m.max() * h), first=eps[0][0], last=eps[-1][1], episodes=eps)
        out.append(f)
    return out


def _transitions(zone: str, t0: pd.Timestamp, t1: pd.Timestamp) -> list[pd.Timestamp]:
    grid = pd.date_range(t0.floor("D"), t1.ceil("D"), freq="h", tz="UTC")
    local = grid.tz_convert(zone)
    off = (local.tz_localize(None) - grid.tz_localize(None)).to_numpy()
    k = np.flatnonzero(off[1:] != off[:-1]) + 1
    return [grid[i] for i in k]


@register("dst")
def dst(ctx: Ctx) -> list[Finding]:
    zone = ctx.cfg.dst_zone or ctx.meta.source_timezone
    if zone.upper() == "UTC" or zone.startswith("Etc/"):
        skip(f"{zone} has no daylight saving time; set dst_zone to test a site zone")
    out = []
    step = ctx.step_ns
    span = int(2 * 3.6e12 // step) * step
    for dev, a, b in ctx.segments:
        t = ctx.ts[a:b]
        trans = _transitions(zone, pd.Timestamp(t[0], tz="UTC"), pd.Timestamp(t[-1], tz="UTC"))
        hits = 0
        for T in trans:
            T = int(T.value)
            if T - 2 * 86_400_000_000_000 < t[0] or T + 2 * 86_400_000_000_000 > t[-1]:
                continue
            counts = [int(((t >= T - span + k * 86_400_000_000_000) & (t < T + span + k * 86_400_000_000_000)).sum()) for k in (-1, 0, 1)]
            full = span * 2 // step
            dup = len(t[(t >= T - span) & (t < T + span)]) - len(np.unique(t[(t >= T - span) & (t < T + span)]))
            if (counts[1] <= 0.75 * full and counts[0] == full and counts[2] == full) or dup > 0:
                hits += 1
        if hits:
            out.append(Finding("dst", "warning", f"{hits} of {len(trans)} daylight saving changes of {zone} leave a gap or a repeated hour",
                               device=dev, n=hits, total=len(trans)))
    return out


# ------------------------------------------------------------------------------------------------ values


@register("missing_values")
def missing_values(ctx: Ctx) -> list[Finding]:
    out = []
    for c in _signal_columns(ctx):
        for dev, a, b in ctx.segments:
            col = ctx.df[c].iloc[a:b]
            if not col.notna().any():
                continue  # the device does not carry this signal
            n = int(col.isna().sum())
            if n / (b - a) >= 0.05:
                out.append(Finding("missing_values", _sev_by_share(n / (b - a), 0.05, 0.25), f"{c}: {n / (b - a):.1%} of the rows empty",
                                   device=dev, signal=c, n=n, total=b - a))
    return out


@register("out_of_range")
def out_of_range(ctx: Ctx) -> list[Finding]:
    out = []
    for c in _signal_columns(ctx):
        sig = SIGNALS[c]
        if sig.lo is None and sig.hi is None:
            continue
        v = ctx.values(c)
        bad = np.zeros(len(v), bool)
        with np.errstate(invalid="ignore"):
            if sig.lo is not None:
                bad |= v < sig.lo
            if sig.hi is not None:
                bad |= v > sig.hi
        rows = np.flatnonzero(bad)
        for dev, r in _by_device(ctx, rows).items():
            a, b = next((a, b) for d, a, b in ctx.segments if d == dev)
            out.append(ctx.finding("out_of_range", "error" if len(r) / (b - a) >= 0.01 else "warning",
                                   f"{c}: {len(r)} values outside [{sig.lo}, {sig.hi}] {sig.unit}; max {np.nanmax(v[r]):.4g}, min {np.nanmin(v[r]):.4g}",
                                   rows=r, device=dev, signal=c, total=int(np.isfinite(v[a:b]).sum())))
    return out


def _flat_rows(ctx: Ctx, v: np.ndarray, min_run: int, ignore_zero: bool) -> np.ndarray:
    n = len(v)
    if n < 2:
        return np.array([], dtype=int)
    same = np.r_[False, (v[1:] == v[:-1]) & np.isfinite(v[1:])]
    cont = np.r_[False, (ctx.ts[1:] - ctx.ts[:-1]) == ctx.step_ns] & np.r_[False, ctx.dev[1:] == ctx.dev[:-1]]
    new = ~(same & cont)
    rid = np.cumsum(new)
    cnt = np.bincount(rid)[rid]
    flag = (cnt >= min_run) & np.isfinite(v)
    if ignore_zero:
        flag &= v != 0
    return np.flatnonzero(flag)


@register("flatline")
def flatline(ctx: Ctx) -> list[Finding]:
    out = []
    step_h = ctx.step_ns / 3.6e12
    for c in _signal_columns(ctx):
        hours = ctx.cfg.flatline_hours.get(c)
        if hours is None:
            continue
        min_run = max(2, int(np.ceil(hours / step_h)))
        v = ctx.values(c)
        rows = _flat_rows(ctx, v, min_run, c in ctx.cfg.flatline_ignore_zero)
        for dev, r in _by_device(ctx, rows).items():
            a, b = next((a, b) for d, a, b in ctx.segments if d == dev)
            total = int(np.isfinite(v[a:b]).sum())
            f = ctx.finding("flatline", _sev_by_share(len(r) / max(total, 1), 0.01, 0.10),
                            f"{c}: {len(r)} values in runs of at least {hours:g} h with one value", rows=r, device=dev, signal=c, total=total)
            f.value = float(max((e[2] for e in f.episodes), default=0) * step_h)
            out.append(f)
    return out


@register("spikes")
def spikes(ctx: Ctx) -> list[Finding]:
    out = []
    w = ctx.cfg.spike_window
    for c in ctx.cfg.spike_signals:
        if c not in ctx.df.columns or not ctx.has(c):
            continue
        v = ctx.df[c].to_numpy(dtype="float64", na_value=np.nan)
        flagged = []
        for dev, a, b in ctx.segments:
            s = pd.Series(v[a:b])
            med = s.rolling(w, center=True, min_periods=3).median()
            mad = (s - med).abs().rolling(w, center=True, min_periods=3).median() * 1.4826
            scale = np.maximum(mad, ctx.cfg.spike_min_scale)
            bad = ((s - med).abs() > ctx.cfg.spike_k * scale).to_numpy()
            r = np.flatnonzero(bad) + a
            if len(r):
                flagged.append((dev, r, int(np.isfinite(v[a:b]).sum())))
        for dev, r, total in flagged:
            out.append(ctx.finding("spikes", _sev_by_share(len(r) / max(total, 1), 0.001, 0.01),
                                   f"{c}: {len(r)} values more than {ctx.cfg.spike_k:g} robust deviations from the local median",
                                   rows=r, device=dev, signal=c, total=total))
    return out


# ------------------------------------------------------------------------------------------------ wind


def _wind_devices(ctx: Ctx) -> list[tuple[str, int, int]]:
    return [(d, a, b) for d, a, b in ctx.segments if ctx.types.get(d) == "turbine"]


@register("power_curve")
def power_curve(ctx: Ctx) -> list[Finding]:
    if ctx.meta.profile != "wind" or not (ctx.has("p_ac") and ctx.has("wind_speed")):
        skip("needs a wind table with p_ac and wind_speed")
    out = []
    p_all, ws_all = ctx.values("p_ac"), ctx.values("wind_speed")
    bin_w = ctx.cfg.curve_bin_ms
    for dev, a, b in _wind_devices(ctx):
        p, ws = p_all[a:b], ws_all[a:b]
        ok = np.isfinite(p) & np.isfinite(ws) & (ws >= 3.0) & (ws <= 25.0)
        if ok.sum() < 500:
            continue
        rated = ctx.rated(dev) or float(np.nanpercentile(p[ok], 99.5))
        k = np.floor(np.nan_to_num(ws, nan=-1.0) / bin_w).astype(int)
        df = pd.DataFrame({"k": k[ok], "p": p[ok]})
        g = df.groupby("k")["p"]
        med = g.median()
        mad = (df["p"] - df["k"].map(med)).abs().groupby(df["k"]).median() * 1.4826
        n = g.size()
        scale = np.maximum(mad, 0.02 * rated)
        valid = n >= 50
        m = pd.Series(k).map(med.where(valid)).to_numpy()
        s = pd.Series(k).map(scale.where(valid)).to_numpy()
        dev_p = p - m
        with np.errstate(invalid="ignore"):
            high = ok & (dev_p > ctx.cfg.curve_k * s)
            low = ok & (dev_p < -ctx.cfg.curve_k * s)
        rows = np.flatnonzero(high | low) + a
        if len(rows):
            f = ctx.finding("power_curve", _sev_by_share(len(rows) / int(ok.sum()), 0.05, 0.25),
                            f"{int(high.sum())} intervals above and {int(low.sum())} below the median power curve by more than "
                            f"{ctx.cfg.curve_k:g} robust deviations (stops and curtailment are among the low ones)",
                            rows=rows, device=dev, signal="p_ac", total=int(ok.sum()))
            f.value = float(high.sum() / max(ok.sum(), 1))
            out.append(f)
    return out


@register("wind_direction")
def wind_direction(ctx: Ctx) -> list[Finding]:
    out = []
    thr = ctx.cfg.direction_consistency_deg
    if all(ctx.has(c) for c in ("wind_dir", "nacelle_dir", "wind_dir_rel")):
        wd, nd, rel = ctx.values("wind_dir"), ctx.values("nacelle_dir"), ctx.values("wind_dir_rel")
        d = _circ(wd - (nd + rel))
        bad = np.isfinite(d) & (np.abs(d) > thr)
        for dev, a, b in _wind_devices(ctx):
            ok = np.isfinite(d[a:b])
            if ok.sum() < 100:
                continue
            r = np.flatnonzero(bad[a:b]) + a
            if not len(r):
                continue
            f = ctx.finding("wind_direction", _sev_by_share(len(r) / int(ok.sum()), 0.02, 0.2),
                            f"wind_dir differs from nacelle_dir + wind_dir_rel by more than {thr:g} deg in {len(r)} intervals; "
                            f"median difference {np.nanmedian(d[a:b]):.1f} deg", rows=r, device=dev, signal="wind_dir", total=int(ok.sum()))
            f.value = float(np.nanmedian(d[a:b]))
            out.append(f)
    # north offsets between turbines: the same wind seen by neighbours must have the same direction
    col = "wind_dir" if ctx.has("wind_dir") and sum(1 for _ in _wind_devices(ctx)) >= 3 else "nacelle_dir"
    turbines = _wind_devices(ctx)
    if col in ctx.df.columns and ctx.has(col) and len(turbines) >= 3:
        ang = ctx.values(col)
        mask = np.isfinite(ang)
        if ctx.has("wind_speed"):
            mask &= ctx.values("wind_speed") >= 5.0
        if ctx.has("p_ac"):
            rated = np.array([ctx.rated(d) or np.nan for d in ctx.dev])
            with np.errstate(invalid="ignore"):
                mask &= ~(ctx.values("p_ac") <= 0.1 * np.where(np.isfinite(rated), rated, 0.0)) | ~np.isfinite(rated)
        t = pd.DataFrame({"t": ctx.ts[mask], "d": ctx.dev[mask], "a": np.deg2rad(ang[mask])})
        s = t.assign(s=np.sin(t["a"]), c=np.cos(t["a"])).pivot_table(index="t", columns="d", values=["s", "c"], aggfunc="first")
        sin, cos = s["s"], s["c"]
        have = sin.notna().sum(axis=1)
        keep = have >= 3
        ref = np.arctan2(sin[keep].mean(axis=1), cos[keep].mean(axis=1))
        offs = {}
        for d in sin.columns:
            a_d = np.arctan2(sin.loc[keep, d], cos.loc[keep, d])
            diff = pd.Series(np.angle(np.exp(1j * (a_d - ref))), index=a_d.index).dropna()
            if len(diff) >= ctx.cfg.direction_min_pairs:
                offs[d] = (float(np.rad2deg(np.angle(np.exp(1j * diff.to_numpy()).mean()))), len(diff))
        label = {"wind_dir": "wind direction", "nacelle_dir": "nacelle position"}[col]
        for d, (o, n) in sorted(offs.items()):
            sev = "warning" if abs(o) > ctx.cfg.direction_offset_deg else "info"
            out.append(Finding("wind_direction", sev, f"{label} is {o:+.1f} deg off the fleet mean direction (north offset), from {n} concurrent intervals",
                               device=d, signal=col, n=n, total=n, value=o))
    if not out and not (ctx.has("wind_dir") or ctx.has("nacelle_dir")):
        skip("needs wind_dir or nacelle_dir")
    return out


@register("icing")
def icing(ctx: Ctx) -> list[Finding]:
    if ctx.meta.profile != "wind" or not (ctx.has("t_amb") and ctx.has("wind_speed")):
        skip("needs a wind table with t_amb and wind_speed")
    cfg, out = ctx.cfg, []
    t, ws = ctx.values("t_amb"), ctx.values("wind_speed")
    cold = np.isfinite(t) & (t < cfg.icing_temp_max)
    step_h = ctx.step_ns / 3.6e12
    if ctx.has("rotor_speed"):
        rpm = ctx.values("rotor_speed")
        rows = np.flatnonzero(cold & np.isfinite(rpm) & (rpm > 3.0) & np.isfinite(ws) & (ws < 0.5))
        for dev, r in _by_device(ctx, rows).items():
            a, b = next((a, b) for d, a, b in ctx.segments if d == dev)
            out.append(ctx.finding("icing", "warning", f"wind_speed reads under 0.5 m/s while the rotor turns above 3 rpm below {cfg.icing_temp_max:g} degC "
                                   f"in {len(r)} intervals: an iced anemometer", rows=r, device=dev, signal="wind_speed", total=int(cold[a:b].sum())))
    if ctx.has("p_ac"):
        p = ctx.values("p_ac")
        running = np.ones(len(p), bool)
        if ctx.has("shutdown_s"):
            running &= ~(ctx.values("shutdown_s") > 0)
        if "iec_category" in ctx.df.columns:
            running &= ~ctx.df["iec_category"].isin(NON_OPERATING).to_numpy()
        warm = np.isfinite(t) & (t > 5.0) & running
        for dev, a, b in _wind_devices(ctx):
            ok_w = warm[a:b] & np.isfinite(p[a:b]) & np.isfinite(ws[a:b]) & (ws[a:b] >= 3.0)
            if ok_w.sum() < 500:
                continue
            k = np.floor(np.nan_to_num(ws[a:b], nan=-1.0) / cfg.curve_bin_ms).astype(int)
            ref = pd.Series(p[a:b][ok_w]).groupby(k[ok_w]).median()
            cnt = pd.Series(k[ok_w]).value_counts()
            ref = ref[cnt.reindex(ref.index) >= 30]
            expected = pd.Series(k).map(ref).to_numpy()
            frozen = (np.isfinite(t[a:b]) & (t[a:b] < cfg.icing_temp_max) & (t[a:b] > cfg.icing_temp_min) & running[a:b]
                      & np.isfinite(ws[a:b]) & (ws[a:b] >= cfg.icing_min_wind) & np.isfinite(p[a:b]) & np.isfinite(expected)
                      & (p[a:b] < cfg.icing_power_ratio * expected))
            if not frozen.any():
                continue
            min_run = max(2, int(np.ceil(cfg.icing_min_hours / step_h)))
            rid = np.cumsum(np.r_[True, frozen[1:] != frozen[:-1]])
            run_len = np.bincount(rid)[rid]
            keep = frozen & (run_len >= min_run)
            rows = np.flatnonzero(keep) + a
            if len(rows):
                out.append(ctx.finding("icing", "warning", f"power under {cfg.icing_power_ratio:.0%} of the turbine's warm-weather median at the same wind speed, "
                                       f"in {cfg.icing_temp_min:g}..{cfg.icing_temp_max:g} degC for at least {cfg.icing_min_hours:g} h, outside logged stops: "
                                       f"{len(rows)} intervals", rows=rows, device=dev, signal="p_ac", total=int(((t[a:b] < cfg.icing_temp_max) & (t[a:b] > cfg.icing_temp_min)).sum())))
    return out


# ------------------------------------------------------------------------------------------------ energy


def _contiguous(ctx: Ctx) -> np.ndarray:
    """True for rows whose predecessor is the previous interval of the same device."""
    return np.r_[False, (ctx.ts[1:] - ctx.ts[:-1]) == ctx.step_ns] & np.r_[False, ctx.dev[1:] == ctx.dev[:-1]]


@register("energy_counter")
def energy_counter(ctx: Ctx) -> list[Finding]:
    if not ctx.has("e_export_counter"):
        skip("needs e_export_counter")
    cfg, out = ctx.cfg, []
    c = ctx.values("e_export_counter")
    prev = np.r_[np.nan, c[:-1]]
    dc = c - prev
    cont = _contiguous(ctx) & np.isfinite(dc)
    h = ctx.step_ns / 3.6e12
    for dev, a, b in ctx.segments:
        if not np.isfinite(c[a:b]).any():
            continue
        sl = slice(a, b)
        d = dc[sl]
        k = cont[sl]
        rated = ctx.rated(dev)
        resets = np.flatnonzero(k & (d < -cfg.counter_tolerance_kwh)) + a
        limit = 1.5 * rated * h if rated else 10.0 * float(np.nanpercentile(d[k & (d > 0)], 99.9)) if (k & (d > 0)).any() else np.inf
        jumps = np.flatnonzero(k & (d > limit)) + a
        if len(resets):
            out.append(ctx.finding("energy_counter", "warning", f"{len(resets)} readings below the one before (counter reset or rollback)",
                                   rows=resets, device=dev, signal="e_export_counter", total=int(k.sum()), value=float(np.nanmin(d[k]))))
        if len(jumps):
            out.append(ctx.finding("energy_counter", "warning", f"{len(jumps)} steps above {limit:.0f} kWh per interval (a jump, not production)",
                                   rows=jumps, device=dev, signal="e_export_counter", total=int(k.sum()), value=float(np.nanmax(d[k]))))
        if ctx.has("e_export"):
            e = ctx.values("e_export")[sl]
            both = k & np.isfinite(e) & (d >= -cfg.counter_tolerance_kwh) & (d <= limit)
            if both.sum() >= 100:
                tol = np.maximum(cfg.counter_tolerance_kwh, cfg.counter_tolerance_rel * np.abs(e))
                bad = np.flatnonzero(both & (np.abs(d - e) > tol)) + a
                ratio = float(d[both].sum() / e[both].sum()) if e[both].sum() else float("nan")
                if not len(bad) and abs(ratio - 1) < 0.005:
                    continue
                f = ctx.finding("energy_counter", _sev_by_share(len(bad) / int(both.sum()), 0.01, 0.10),
                                f"counter step differs from e_export by more than {cfg.counter_tolerance_rel:.0%} or {cfg.counter_tolerance_kwh:g} kWh in {len(bad)} intervals; "
                                f"sum of counter steps / sum of e_export = {ratio:.4f}", rows=bad, device=dev, signal="e_export", total=int(both.sum()))
                f.value = ratio
                out.append(f)
    return out


@register("energy_power")
def energy_power(ctx: Ctx) -> list[Finding]:
    if not (ctx.has("e_export") and ctx.has("p_ac")):
        skip("needs e_export and p_ac")
    out = []
    h = ctx.step_ns / 3.6e12
    e, p = ctx.values("e_export"), ctx.values("p_ac")
    for dev, a, b in ctx.segments:
        sl = slice(a, b)
        ok = np.isfinite(e[sl]) & np.isfinite(p[sl])
        if ok.sum() < 100:
            continue
        rated = ctx.rated(dev)
        ref = (rated or float(np.nanpercentile(p[sl][ok], 99.5))) * h
        diff = np.abs(e[sl] - p[sl] * h)
        bad = np.flatnonzero(ok & (diff > ctx.cfg.energy_power_rel * ref)) + a
        ratio = float(e[sl][ok].sum() / (p[sl][ok] * h).sum()) if (p[sl][ok] > 0).any() else float("nan")
        off = abs(ratio - 1) if np.isfinite(ratio) else 0.0
        if len(bad) / int(ok.sum()) < 0.005 and off < 0.02:
            continue
        sev = "error" if off > 0.05 else "warning" if off > 0.02 else "info"
        f = ctx.finding("energy_power", sev,
                        f"interval energy differs from power x {h * 60:g} min by more than {ctx.cfg.energy_power_rel:.0%} of the rating in {len(bad)} intervals; "
                        f"sum of energy / sum of power x time = {ratio:.4f}", rows=bad, device=dev, signal="e_export", total=int(ok.sum()))
        f.value = ratio
        out.append(f)
    return out


# ------------------------------------------------------------------------------------------------ units


def _daily_level(ctx: Ctx, dev_rows: slice, poa_by_time: pd.Series | None) -> pd.Series | None:
    """Daily level of power on a log10 scale: relative to irradiance (PV), within a wind speed band (wind), else the 99th percentile."""
    idx = pd.to_datetime(ctx.ts[dev_rows], utc=True)
    p = pd.Series(ctx.values("p_ac")[dev_rows], index=idx)
    p = p.where(p > 0)
    day = p.index.floor("D")
    if poa_by_time is not None:
        poa = poa_by_time.reindex(p.index)
        r = (p / poa.where(poa > 200)).dropna()
        g = r.groupby(r.index.floor("D"))
        d, n = g.median(), g.size()
        d = d[n >= 8]
    elif ctx.meta.profile == "wind":
        if not ctx.has("wind_speed"):
            return None
        ws = ctx.values("wind_speed")[dev_rows]
        sel = p.where((ws >= 8.5) & (ws <= 9.5)).dropna()
        g = sel.groupby(sel.index.floor("D"))
        d, n = g.median(), g.size()
        d = d[n >= 10]
    else:
        g = p.groupby(day)
        d, n = g.quantile(0.99), g.count()
        d = d[n >= 20]
    d = d[d > 0]
    return np.log10(d) if len(d) >= 14 else None


@register("units")
def units(ctx: Ctx) -> list[Finding]:
    out = []
    cfg = ctx.cfg
    # (1) order of magnitude of power against the rating
    if ctx.has("p_ac"):
        p = ctx.values("p_ac")
        for dev, a, b in ctx.segments:
            if ctx.types.get(dev) not in ("turbine", "inverter"):
                continue
            try:
                d = ctx.meta.device(dev)
            except KeyError:
                continue
            ref = d.rated_kw or d.capacity_kwp
            pos = p[a:b][np.isfinite(p[a:b]) & (p[a:b] > 0)]
            if not ref or len(pos) < 100:
                continue
            ratio = float(np.percentile(pos, 99) / ref)
            if ratio > 5:
                out.append(Finding("units", "error", f"p_ac 99th percentile is {ratio:.1f} times the rating: values look about {10 ** round(np.log10(ratio)):.0f} times too large (W instead of kW?)",
                                   device=dev, signal="p_ac", n=len(pos), total=len(pos), value=ratio))
            elif ratio < 0.02:
                out.append(Finding("units", "error", f"p_ac 99th percentile is {ratio:.4f} times the rating: values look about {10 ** round(-np.log10(ratio)):.0f} times too small (MW or a scaled channel?)",
                                   device=dev, signal="p_ac", n=len(pos), total=len(pos), value=ratio))
        # (2) a switch of unit inside the record
        poa_series = None
        if ctx.meta.profile == "pv" and ctx.has("poa"):
            m = ctx.df["poa"].notna().to_numpy()
            poa_df = pd.Series(ctx.values("poa")[m], index=pd.to_datetime(ctx.ts[m], utc=True))
            poa_series = poa_df[~poa_df.index.duplicated()]
        for dev, a, b in ctx.segments:
            if ctx.types.get(dev) not in ("turbine", "inverter"):
                continue
            lv = _daily_level(ctx, slice(a, b), poa_series)
            if lv is None or len(lv) < 15:
                continue
            # in a wind farm, curtailment and stops move the level in a band of wind speed by up to a decade: look for steps of 30x or more
            hit = level_step(lv, 7, max(cfg.unit_jump_decades, 1.5) if ctx.meta.profile == "wind" else cfg.unit_jump_decades)
            if hit is not None:
                day, step = hit
                out.append(Finding("units", "error", f"the level of p_ac{' / poa' if poa_series is not None else ''} changes by a factor of {10 ** step:.3g} on {day:%Y-%m-%d} "
                                   "and stays: a switch of unit or scaling within the record", device=dev, signal="p_ac", n=int((lv.index >= day).sum()),
                                   total=len(lv), value=float(10 ** step), first=day))
    # (3) hints of another unit
    hints = [("humidity", lambda v: np.nanmax(v) <= 1.0, "looks like a fraction (0-1), not percent"),
             ("wind_dir", lambda v: np.nanmax(v) <= 6.3, "looks like radians, not degrees"),
             ("nacelle_dir", lambda v: np.nanmax(v) <= 6.3, "looks like radians, not degrees"),
             ("pressure", lambda v: np.nanmedian(v) < 2000, "looks like hPa, not Pa"),
             ("t_amb", lambda v: np.nanmedian(v) > 45 and np.nanpercentile(v, 1) > 20, "looks like degrees Fahrenheit, not Celsius"),
             ("power_factor", lambda v: np.nanmax(v) > 1.5, "exceeds 1: percent or a scaled value (x100, x1000)")]
    for c, test, msg in hints:
        if ctx.has(c):
            v = ctx.values(c)
            for dev, a, b in ctx.segments:
                x = v[a:b][np.isfinite(v[a:b])]
                if len(x) >= 100 and test(x):
                    out.append(Finding("units", "warning", f"{c} {msg}", device=dev, signal=c, n=len(x), total=len(x)))
    return out
