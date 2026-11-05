"""The study: schema validation, quality control, IEC state mapping and converter parity on the open datasets.

``python -m scada_schema study`` (or ``scada-schema study``) loads every dataset of ``SPECS`` from its original files,
validates it against the schema, runs the quality checks, maps the event logs to IEC 61400-26 categories and runs the
parity checks of the converters; it writes ``results/study.json`` (every number the README and the project page show) and
one HTML quality report per dataset in ``results/qc``. Nothing in the README is typed by hand: ``tests/test_docs.py`` renders
the README from ``study.json`` and fails when they differ.

The raw files are looked for as described in ``scada_schema.loaders._paths`` (``$SCADA_SCHEMA_<NAME>_DIR``, then
``$SCADA_SCHEMA_DATA/<name>``, then ``./data/<name>``); fetch them first with ``scada-schema fetch``.

Usage:
    python -m scada_schema study --out results
    python -m scada_schema study --only kelmarsh pvdaq_34 --skip-converters
"""

from __future__ import annotations

import json
import logging
import platform
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from scada_schema import __version__, qc
from scada_schema._steps import daily_log_ratio
from scada_schema.loaders import care, hill_of_towie, kelmarsh, la_haute_borne, penmanshiel, pvdaq
from scada_schema.loaders._paths import raw_dir
from scada_schema.states import attach_states, availability_from_table, code_table_coverage, load_code_table, map_codes
from scada_schema.validate import validate

log = logging.getLogger(__name__)

__all__ = ["SPECS", "Spec", "run"]


@dataclass(frozen=True)
class Spec:
    name: str
    loader: str  # module name in scada_schema.loaders
    kwargs: dict = field(default_factory=dict)
    states: str | None = None  # code table of the controller family, for datasets with an event log
    dst_zone: str | None = None  # site time zone to look for daylight saving artefacts in UTC time stamps
    operator_labels: bool = False  # the event log carries the operator's own IEC category per event


SPECS: list[Spec] = [
    Spec("kelmarsh_2019", "kelmarsh", {"years": [2019]}, "senvion_mm", "Europe/London", True),
    Spec("penmanshiel_2020", "penmanshiel", {"years": [2020]}, "senvion_mm", "Europe/London", True),
    Spec("hill_of_towie_2021", "hill_of_towie", {"years": [2021]}, "siemens_swt_hot", "Europe/London"),
    Spec("la_haute_borne", "la_haute_borne", {}, None, "Europe/Paris"),
    Spec("care_A", "care", {"farm": "A", "event_id": None}),
    Spec("care_B", "care", {"farm": "B", "event_id": None}),
    Spec("care_C", "care", {"farm": "C", "event_id": None}),
    Spec("pvdaq_34", "pvdaq", {"system": 34, "years": [2016, 2017, 2018]}),
    Spec("pvdaq_1276", "pvdaq", {"system": 1276, "years": [2016, 2017, 2018]}),
    Spec("pvdaq_1278", "pvdaq", {"system": 1278, "years": [2016, 2017, 2018]}),
    Spec("pvdaq_35", "pvdaq", {"system": 35, "years": [2016, 2017, 2018]}),
    Spec("pvdaq_34_raw_units", "pvdaq", {"system": 34, "years": [2016, 2017, 2018], "fix_units": False}),
]
MODULES = {"kelmarsh": kelmarsh, "penmanshiel": penmanshiel, "hill_of_towie": hill_of_towie, "la_haute_borne": la_haute_borne, "care": care, "pvdaq": pvdaq}
SEVERITIES = ("info", "warning", "error")


def _first_event(farm: str) -> int:
    return int(care.list_datasets(None, farm)["event_id"].iloc[0])


def _worst(sevs: list[str]) -> str:
    return max(sevs, key=SEVERITIES.index) if sevs else "none"


def summarise_report(report: qc.QCReport) -> dict:
    by_check = {}
    for check in report.checks_run:
        fs = [f for f in report.findings if f.check == check]
        by_check[check] = {"findings": len(fs), "devices": len({f.device for f in fs if f.device}), "rows": int(sum(f.n for f in fs)),
                           "worst": _worst([f.severity for f in fs])}
    hl: dict = {}
    sw = [f for f in report.findings if f.check == "units" and "switch" in f.message]
    if sw:
        hl["unit_switch"] = [{"device": f.device, "date": f.first.strftime("%Y-%m-%d"), "factor": round(float(f.value), 3)} for f in sw]
    mag = [f for f in report.findings if f.check == "units" and "times the rating" in f.message]
    if mag:
        hl["power_magnitude"] = [{"device": f.device, "ratio_to_rating": round(float(f.value), 3)} for f in mag]
    north = [f for f in report.findings if f.check == "wind_direction" and "north offset" in f.message]
    if north:
        off = {f.device: float(f.value) for f in north}
        worst = max(off, key=lambda d: abs(off[d]))
        hl["north_offsets"] = {"turbines": len(off), "flagged": sorted(d for d, v in off.items() if abs(v) > report.cfg.direction_offset_deg),
                               "max_abs_deg": round(abs(off[worst]), 1), "max_abs_device": worst,
                               "median_abs_deg": round(float(np.median(np.abs(list(off.values())))), 1),
                               "offsets_deg": {d: round(v, 2) for d, v in sorted(off.items())}}
    for check, key in (("energy_power", "energy_over_power_ratio"), ("energy_counter", "counter_over_energy_ratio")):
        vals = [float(f.value) for f in report.findings if f.check == check and f.value is not None and f.signal == "e_export"]
        if vals:
            hl[key] = {"devices": len(vals), "min": round(min(vals), 4), "max": round(max(vals), 4)}
    dst = [f for f in report.findings if f.check == "dst"]
    hl["dst"] = "not applicable" if "dst" in report.checks_skipped else {
        "findings": len(dst), "transitions_hit": max((f.n for f in dst), default=0), "transitions": max((f.total for f in dst), default=0)}
    gaps = [f for f in report.findings if f.check == "gaps"]
    hl["gaps"] = {"devices_with_gaps": len(gaps), "missing_intervals": int(sum(f.n for f in gaps)),
                  "longest_episodes": [[a.strftime("%Y-%m-%d %H:%M"), b.strftime("%Y-%m-%d %H:%M"), int(k)] for a, b, k in (gaps[0].episodes if gaps else [])]}
    return {"counts": report.counts(), "checks_run": report.checks_run, "checks_skipped": report.checks_skipped, "by_check": by_check, "highlights": hl}


def _load(spec: Spec):
    mod = MODULES[spec.loader]
    kw = dict(spec.kwargs)
    if spec.loader == "care" and kw.get("event_id") is None:
        kw["event_id"] = _first_event(kw["farm"])
    return mod.load(None, **kw), kw


def study_dataset(spec: Spec, out_dir: Path) -> tuple[dict, object]:
    t0 = time.time()
    loaded, kw = _load(spec)
    t = loaded.table
    rep = validate(t, loaded.meta)
    cfg = qc.QCConfig(dst_zone=spec.dst_zone) if spec.dst_zone else None
    report = qc.run(t, loaded.meta, cfg)
    html_dir = out_dir / "qc"
    html_dir.mkdir(parents=True, exist_ok=True)
    report.to_html(html_dir / f"{spec.name}.html")
    sigs = [c for c in t.columns if c not in ("timestamp", "device_id") and "__" not in c and not c.startswith("x_")]
    src = loaded.meta.source
    entry = {
        "name": spec.name, "source": src.name, "doi": src.doi, "license": src.license, "profile": loaded.meta.profile,
        "interval": loaded.meta.interval, "source_timezone": loaded.meta.source_timezone, "source_label": loaded.meta.source_label,
        "args": {k: v for k, v in kw.items()}, "rows": int(len(t)), "devices": int(t["device_id"].nunique()),
        "signals": len(sigs), "signal_names": sigs, "stat_columns": int(sum("__" in c for c in t.columns)),
        "first": t["timestamp"].min().isoformat(), "last": t["timestamp"].max().isoformat(),
        "events": int(len(loaded.events)),
        "validation": {"ok": rep.ok, "errors": len(rep.errors), "warnings": len(rep.warnings), "codes": sorted(rep.codes())},
        "qc": summarise_report(report), "conversions": loaded.meta.conversions,
    }
    log.info("%s: %d rows, %d devices, %s, %.0f s", spec.name, entry["rows"], entry["devices"], entry["qc"]["counts"], time.time() - t0)
    return entry, loaded


def iec_study(spec: Spec, loaded) -> dict:
    """How the IEC mapping fares on one event log.

    With the operator's own category on every event (Greenbyte) the code table is compared with it; note that the shipped Senvion table was
    written from the same open logs, so that agreement is a consistency check, not an independent test. Without operator labels (Hill of
    Towie's alarm log) only coverage is reported.
    """
    ev = loaded.events
    stops = ev[ev["status"] == "Stop"]
    table = load_code_table(spec.states)
    out: dict = {"name": spec.name, "table": spec.states, "operator_labels": spec.operator_labels, "events": int(len(ev)), "stop_events": int(len(stops)),
                 "distinct_codes": int(ev["code"].nunique()), "distinct_stop_codes": int(stops["code"].nunique())}
    cov_all = code_table_coverage(ev["code"], table)
    cov_stop = code_table_coverage(stops["code"], table)
    out["events_in_table"] = round(cov_all["in_table"], 4)
    out["stop_codes_in_table"] = round(cov_stop["in_table"], 4)
    out["stop_codes_with_category"] = round(cov_stop["with_category"], 4)
    if "source" in table.columns:
        published = set(table.loc[table["source"] == "published", "code"])
        out["table_published_codes"] = len(published)
        out["events_with_published_code"] = round(float(ev["code"].astype(str).isin(published).mean()), 4)
    mapped = map_codes(stops["code"], table)
    if spec.operator_labels:
        both = stops["category"].notna() & mapped.notna()
        out["operator_categories"] = {k: int(v) for k, v in stops["category"].value_counts().items()}
        out["compared_stop_events"] = int(both.sum())
        out["agreement_with_operator"] = round(float((stops["category"][both] == mapped[both]).mean()), 4) if both.any() else None
        attach_states(loaded)
        for contract in ("iec_operational", "oem_warranty"):
            a = availability_from_table(loaded.table, loaded.meta.interval, contract, allowance_h=60.0 if contract == "oem_warranty" else 0.0)
            out[f"time_availability_{contract}"] = round(float(a.time), 4)
        c = loaded.table["iec_category"].value_counts(normalize=True)
        out["interval_shares"] = {k: round(float(v), 4) for k, v in c.items()}
    else:
        out["mapped_categories"] = {k: int(v) for k, v in mapped.value_counts().items()}
    return out


def pvdaq_unit_figure(out: dict) -> dict:
    """Daily level of power over irradiance (log10) for PVDAQ system 34, read as hectowatts throughout and corrected."""
    d = raw_dir("pvdaq")
    series = {}
    for label, fix in (("hectowatts_throughout", False), ("corrected", True)):
        L = pvdaq.load(d, system=34, years=[2016, 2017, 2018], fix_units=fix)
        t = L.table
        p = t[t["device_id"] == "INV"].set_index("timestamp")["p_ac"].astype(float)
        poa = t[t["device_id"] == "MET"].set_index("timestamp")["poa"].astype(float)
        lv = daily_log_ratio(p, poa)
        series[label] = {"days": [x.strftime("%Y-%m-%d") for x in lv.index[::3]], "log10_ratio": [round(float(v), 3) for v in lv.to_numpy()[::3]]}
    return series


def parity_study() -> dict:
    from scada_schema import parity

    out: dict = {}
    kd = raw_dir("kelmarsh")
    ids = ["T01", "T02", "T03"]
    loaded = kelmarsh.load(kd, years=[2019], devices=ids)
    z = next(kd.glob("Kelmarsh_SCADA_2019_*.zip"))
    texts = {}
    with zipfile.ZipFile(z) as zf:
        for i in ids:
            name = next(n for n in zf.namelist() if n.startswith(f"Turbine_Data_Kelmarsh_{int(i[1:])}_"))
            texts[i] = zf.read(name).decode("utf-8-sig")
    out["windup"] = parity.windup_parity(loaded.table, loaded.meta, texts, "MM92")
    out["windup"]["dataset"] = "Kelmarsh 2019, turbines T01 to T03"
    out["openoa"] = parity.openoa_parity(loaded.table, loaded.meta, texts)
    out["openoa"]["dataset"] = "Kelmarsh 2019, turbines T01 to T03"
    pd_dir = raw_dir("pvdaq")
    pl = pvdaq.load(pd_dir, system=34, years=[2017])
    files = [f for f in sorted((pd_dir / "system_34").glob("system_34__date_2017_*.csv")) if f.stat().st_size > 0]
    raw = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    out["pvlib"] = parity.pvlib_parity(pl.table, pl.meta, raw)
    out["pvlib"]["dataset"] = "PVDAQ system 34, 2017"
    return out


def run(out_dir: Path, only: list[str] | None = None, skip_converters: bool = False) -> dict:
    """Run the study and write ``study.json`` and the HTML reports into ``out_dir``."""
    import numpy
    import pandas

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [s for s in SPECS if only is None or s.name in only]
    datasets, iec = [], []
    for spec in specs:
        entry, loaded = study_dataset(spec, out_dir)
        datasets.append(entry)
        if spec.states and len(loaded.events):
            iec.append(iec_study(spec, loaded))
        del loaded
    prior = None
    if only is not None and (out_dir / "study.json").exists():
        prior = json.loads((out_dir / "study.json").read_text(encoding="utf-8"))
        names = {d["name"] for d in datasets}
        datasets = [d for d in prior["datasets"] if d["name"] not in names] + datasets
        order = {s.name: i for i, s in enumerate(SPECS)}
        datasets.sort(key=lambda d: order.get(d["name"], 99))
        iec = [i for i in prior["iec"] if i["name"] not in {x["name"] for x in iec}] + iec
    result: dict = {
        "scada_schema_version": __version__, "python": platform.python_version(), "pandas": pandas.__version__, "numpy": numpy.__version__,
        "datasets": datasets, "iec": iec,
    }
    from scada_schema.qc import CHECKS
    from scada_schema.signals import PROFILE_SIGNALS, SIGNALS
    from scada_schema.states import CODE_TABLES, IEC_CATEGORIES, load_code_table

    result["schema"] = {"signals": len(SIGNALS), "wind_signals": len(PROFILE_SIGNALS["wind"]), "pv_signals": len(PROFILE_SIGNALS["pv"]),
                        "checks": len(CHECKS), "iec_categories": len(IEC_CATEGORIES),
                        "code_tables": {t: len(load_code_table(t)) for t in CODE_TABLES}}
    result["totals"] = {
        "datasets": len(datasets), "sources": len({d["source"] for d in datasets}), "rows": int(sum(d["rows"] for d in datasets)),
        "devices": int(sum(d["devices"] for d in datasets)),
        "schema_valid": int(sum(d["validation"]["ok"] for d in datasets)),
        "profiles": sorted({d["profile"] for d in datasets}),
    }
    if prior is not None:  # a partial run keeps the rest of the earlier study
        for key in ("figures", "parity"):
            if key in prior:
                result[key] = prior[key]
    if only is None or "pvdaq_34" in only:
        result["figures"] = {"pvdaq_units": pvdaq_unit_figure(result)}
    if not skip_converters and only is None:
        result["parity"] = parity_study()
    (out_dir / "study.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    log.info("wrote %s", out_dir / "study.json")
    return result
