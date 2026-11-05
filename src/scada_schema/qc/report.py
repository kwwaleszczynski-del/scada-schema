"""A self-contained HTML quality report: one file, no scripts, no external resources, light and dark, usable on a phone."""

from __future__ import annotations

import html

import numpy as np
import pandas as pd

__all__ = ["render_html"]

CSS = """
:root { color-scheme: light; --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.12); --accent:#2a78d6; --info:#2a78d6; --warning:#b8860b; --error:#c0504d; --ok:#2e8b57; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19;
  --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781; --grid:#2c2c2a; --border:rgba(255,255,255,.12); --accent:#3987e5; --info:#3987e5;
  --warning:#d4a017; --error:#d9625e; --ok:#3fae74; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1100px; margin:0 auto; padding:16px; }
h1 { font-size:1.5rem; margin:.2rem 0 .3rem; } h2 { font-size:1.15rem; margin:2rem 0 .6rem; } h3 { font-size:1rem; margin:1.2rem 0 .4rem; }
p, li { color:var(--ink2); } .meta { color:var(--muted); font-size:.9rem; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:14px 0; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 12px; }
.tile b { display:block; font-size:1.35rem; color:var(--ink); } .tile span { color:var(--muted); font-size:.82rem; }
.scroll { overflow-x:auto; border:1px solid var(--border); border-radius:8px; background:var(--surface); }
table { border-collapse:collapse; width:100%; font-size:.88rem; } th, td { padding:6px 10px; text-align:left; border-bottom:1px solid var(--grid); vertical-align:top; }
th { color:var(--muted); font-weight:600; white-space:nowrap; } td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr:last-child td { border-bottom:0; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:.78rem; border:1px solid currentColor; }
.badge.info { color:var(--info); } .badge.warning { color:var(--warning); } .badge.error { color:var(--error); } .badge.ok { color:var(--ok); }
.bar { height:8px; background:var(--grid); border-radius:4px; min-width:70px; overflow:hidden; } .bar i { display:block; height:100%; background:var(--accent); }
.heat { display:grid; gap:2px; align-items:center; }
.heat .lab { font-size:.78rem; color:var(--muted); white-space:nowrap; padding-right:8px; }
.heat .cell { height:14px; border-radius:2px; background:var(--accent); }
details { margin:.35rem 0; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:6px 10px; }
summary { cursor:pointer; color:var(--ink); } code { font-size:.85em; }
.small { font-size:.82rem; color:var(--muted); }
@media (max-width:600px) { main { padding:12px; } h1 { font-size:1.25rem; } th, td { padding:5px 7px; } }
"""


def _e(x) -> str:
    return html.escape(str(x))


def _pct(x) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.2g} %" if x < 0.001 else f"{100 * x:.1f} %"


def _badge(sev: str) -> str:
    return f'<span class="badge {sev}">{sev}</span>'


def _bar(share: float) -> str:
    share = 0.0 if share is None or np.isnan(share) else max(0.0, min(1.0, share))
    return f'<div class="bar"><i style="width:{share * 100:.1f}%"></i></div>'


def _table(rows: list[list[str]], head: list[str], num: set[int] | None = None) -> str:
    num = num or set()
    th = "".join(f'<th class="{"num" if i in num else ""}">{_e(h)}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _heat(report) -> str:
    """Share of expected intervals present per device and month."""
    df = report.table
    step = report.meta.step
    months = pd.period_range(df["timestamp"].min().tz_convert(None).to_period("M"), df["timestamp"].max().tz_convert(None).to_period("M"), freq="M")
    if len(months) > 120:
        return '<p class="small">Record longer than 120 months: heat map omitted.</p>'
    per = int(pd.Timedelta("1D") / step)
    ts = df["timestamp"].dt.tz_convert(None)
    keys = pd.DataFrame({"d": df["device_id"].to_numpy(), "m": ts.dt.to_period("M").to_numpy()})
    cnt = keys.groupby(["d", "m"]).size().unstack(fill_value=0).reindex(columns=months, fill_value=0)
    days = np.array([m.days_in_month for m in months])
    share = (cnt.to_numpy() / (days * per)).clip(0, 1)
    cols = len(months)
    out = [f'<div class="heat" style="grid-template-columns:auto repeat({cols},minmax(2px,1fr))">']
    for i, dev in enumerate(cnt.index):
        out.append(f'<div class="lab">{_e(dev)}</div>')
        for j in range(cols):
            s = share[i, j]
            out.append(f'<div class="cell" style="opacity:{max(0.06, s):.2f}" title="{_e(dev)} {months[j]}: {s:.0%}"></div>')
    out.append("</div>")
    out.append(f'<p class="small">Share of the expected intervals present, per device and month, from {months[0]} to {months[-1]}. Pale cells are gaps.</p>')
    return "".join(out)


def render_html(report) -> str:
    meta, df = report.meta, report.table
    f = report.to_frame()
    counts = report.counts()
    summ = report.summary()
    t0, t1 = (df["timestamp"].min(), df["timestamp"].max()) if len(df) else (None, None)

    tiles = [("rows", f"{len(df):,}"), ("devices", str(df["device_id"].nunique())), ("interval", meta.interval),
             ("errors", str(counts["error"])), ("warnings", str(counts["warning"])), ("info", str(counts["info"]))]
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>"
             f"<title>QC report: {_e(meta.name)}</title><style>{CSS}</style></head><body><main>",
             f"<h1>Quality report: {_e(meta.name)}</h1>",
             f'<p class="meta">profile {_e(meta.profile)}, interval {_e(meta.interval)}, source time zone {_e(meta.source_timezone)} '
             f"(stamps marked the {_e(meta.source_label)} of the interval; the table marks the start, UTC)"
             + (f", {t0:%Y-%m-%d} to {t1:%Y-%m-%d}" if t0 is not None else "") + "</p>",
             '<div class="tiles">' + "".join(f'<div class="tile"><b>{v}</b><span>{k}</span></div>' for k, v in tiles) + "</div>"]
    if meta.source:
        lic = f", licence {_e(meta.source.license)}" if meta.source.license else ""
        parts.append(f'<p class="meta">Source: {_e(meta.source.name)}{lic}{" , doi " + _e(meta.source.doi) if meta.source.doi else ""}</p>')

    # ------------------------------------------------------------------ summary
    parts.append("<h2>Findings by check and signal</h2>")
    if summ.empty:
        parts.append('<p><span class="badge ok">clean</span> No check found anything.</p>')
    else:
        rows = [[_badge(r.severity), f"<code>{_e(r.check)}</code>", f"<code>{_e(r.signal)}</code>" if r.signal else "", str(r.devices),
                 f"{int(r.n):,}", _pct(r.share), _bar(r.share)] for r in summ.sort_values(
                     ["severity", "check"], key=lambda s: s.map({"error": 0, "warning": 1, "info": 2}) if s.name == "severity" else s).itertuples()]
        parts.append(_table(rows, ["level", "check", "signal", "devices", "rows", "share", ""], {3, 4, 5}))
    parts.append(f'<p class="small">Checks run: {", ".join(f"<code>{_e(c)}</code>" for c in report.checks_run)}.'
                 + (" Skipped: " + "; ".join(f"<code>{_e(k)}</code> ({_e(v)})" for k, v in report.checks_skipped.items()) + "." if report.checks_skipped else "") + "</p>")

    # ------------------------------------------------------------------ coverage
    parts.append("<h2>Coverage</h2>")
    cov = report.coverage
    sig_cols = [c for c in cov.columns if c not in ("device_id", "type", "rows", "first", "last", "expected", "present")]
    rows = [[_e(r.device_id), _e(r.type), f"{r.rows:,}", f"{r.first:%Y-%m-%d}", f"{r.last:%Y-%m-%d}", _pct(r.present), _bar(r.present)] for r in cov.itertuples()]
    if len(rows) > 60:
        rows = rows[:60]
        parts.append(f'<p class="small">First 60 of {len(cov)} devices shown.</p>')
    parts.append(_table(rows, ["device", "type", "rows", "first", "last", "present", ""], {2, 5}))
    parts.append(_heat(report))
    if sig_cols:
        share = cov[sig_cols].mean().sort_values()
        rows = [[f"<code>{_e(c)}</code>", _pct(float(v)), _bar(float(v))] for c, v in share.items()]
        parts.append("<h3>Share of values present, mean over the devices that carry the signal</h3>" + _table(rows, ["signal", "present", ""], {1}))

    # ------------------------------------------------------------------ details
    parts.append("<h2>Details</h2>")
    if f.empty:
        parts.append("<p>Nothing to show.</p>")
    for check in report.checks_run:
        items = [x for x in report.findings if x.check == check]
        if not items:
            continue
        items.sort(key=lambda x: ({"error": 0, "warning": 1, "info": 2}[x.severity], -x.n))
        parts.append(f"<details><summary><code>{_e(check)}</code>: {len(items)} finding(s)</summary>")
        rows = []
        for x in items[:40]:
            eps = "<br>".join(f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} ({k} intervals)" for a, b, k in x.episodes)
            rows.append([_badge(x.severity), _e(x.device or ""), f"<code>{_e(x.signal)}</code>" if x.signal else "", _e(x.message), eps])
        parts.append(_table(rows, ["level", "device", "signal", "finding", "longest episodes"]))
        if len(items) > 40:
            parts.append(f'<p class="small">{len(items) - 40} more not shown; the JSON export lists all.</p>')
        parts.append("</details>")

    # ------------------------------------------------------------------ provenance
    parts.append("<h2>Provenance</h2><ul>")
    for c in meta.conversions:
        parts.append(f"<li>{_e(c)}</li>")
    for n in meta.notes:
        parts.append(f"<li>{_e(n)}</li>")
    parts.append("</ul>")
    parts.append('<p class="small">Findings describe the data, not the sensors: a long run of zeros is normal at night, a power far below the curve is normal '
                 "during a stop. Thresholds are in <code>QCConfig</code>.</p></main></body></html>")
    return "".join(parts)
