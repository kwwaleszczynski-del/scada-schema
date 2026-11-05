"""Build docs/data.js (the numbers of the project page) from results/study.json and the signal registry, and copy the HTML
quality reports of the study to docs/qc.

Usage:
    python scripts/build_pages.py            # writes docs/data.js and docs/qc/*.html
    python scripts/build_pages.py --check    # exit 1 when they differ from the build
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "results" / "study.json"
DATA_JS = ROOT / "docs" / "data.js"


def build_data(study: dict) -> str:
    from scada_schema.signals import SIGNALS

    keep = ("name", "source", "doi", "license", "profile", "interval", "source_timezone", "source_label", "rows", "devices", "signals",
            "stat_columns", "first", "last", "events", "validation", "qc")
    ds = []
    for d in study["datasets"]:
        q = d["qc"]
        ds.append({**{k: d[k] for k in keep if k != "qc"},
                   "qc": {"counts": q["counts"], "checks_run": q["checks_run"], "checks_skipped": q["checks_skipped"], "by_check": q["by_check"],
                          "highlights": q["highlights"]}})
    payload = {
        "version": study["scada_schema_version"], "totals": study["totals"], "schema": study["schema"], "datasets": ds, "iec": study["iec"],
        "figures": study.get("figures", {}), "parity": study.get("parity", {}),
        "signals": [{"name": s.name, "unit": s.unit, "kind": s.kind, "profiles": list(s.profiles), "devices": list(s.devices), "lo": s.lo, "hi": s.hi,
                     "stats": s.stats, "description": s.description} for s in SIGNALS.values()],
    }
    return "window.DATA = " + json.dumps(payload, separators=(",", ":"), sort_keys=True) + ";\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    study = json.loads(STUDY.read_text(encoding="utf-8"))
    text = build_data(study)
    qc_src, qc_dst = ROOT / "results" / "qc", ROOT / "docs" / "qc"
    if args.check:
        ok = DATA_JS.read_text(encoding="utf-8") == text
        for f in sorted(qc_src.glob("*.html")):
            ok &= (qc_dst / f.name).exists() and (qc_dst / f.name).read_bytes() == f.read_bytes()
        print("docs are up to date" if ok else "docs differ from the build")
        return 0 if ok else 1
    DATA_JS.write_text(text, encoding="utf-8", newline="\n")
    qc_dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(qc_src.glob("*.html")):
        shutil.copyfile(f, qc_dst / f.name)
    print(f"wrote {DATA_JS} and {len(list(qc_src.glob('*.html')))} reports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
