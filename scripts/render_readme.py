"""Render README.md from docs/README.template.md and results/study.json: every number in the README comes from the study.

Usage:
    python scripts/render_readme.py            # writes README.md
    python scripts/render_readme.py --check    # exit 1 when README.md differs from the rendering
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "README.template.md"
STUDY = ROOT / "results" / "study.json"
README = ROOT / "README.md"
RELEASE = ROOT / "release.json"
REPO = "kwwaleszczynski-del/scada-schema"


def _ds(study: dict, name: str) -> dict:
    return next(d for d in study["datasets"] if d["name"] == name)


def _iec(study: dict, name: str) -> dict:
    return next(d for d in study["iec"] if d["name"] == name)


def pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f} %"


def numbers(study: dict) -> dict[str, str]:
    t, sch = study["totals"], study["schema"]
    n: dict[str, str] = {
        "n_datasets": str(t["datasets"]), "n_demo": str(t["demonstrations"]), "n_sources": str(t["sources"]), "rows_m": f"{t['rows'] / 1e6:.1f}",
        "rows": f"{t['rows']:,}", "n_devices": str(t["devices"]), "schema_valid": f"{t['schema_valid']} of {t['datasets']}",
        "removed_dups": f"{t['removed_duplicate_rows']:,}", "removed_amb": f"{t['removed_ambiguous_time_rows']:,}",
        "n_signals": str(sch["signals"]), "n_wind_signals": str(sch["wind_signals"]), "n_pv_signals": str(sch["pv_signals"]),
        "n_checks": str(sch["checks"]), "n_iec": str(sch["iec_categories"]),
        "code_table_senvion": str(sch["code_tables"]["senvion_mm"]), "code_table_siemens": str(sch["code_tables"]["siemens_swt_hot"]),
    }
    raw = _ds(study, "pvdaq_34_raw_units")["qc"]["highlights"]
    fixed = _ds(study, "pvdaq_34")["qc"]
    sw = raw["unit_switch"][0]
    n["pvdaq_switch_date_qc"] = sw["date"]
    n["pvdaq_switch_factor"] = f"{sw['factor']:.0f}"
    n["pvdaq_fixed_switch_findings"] = str(len(fixed["highlights"].get("unit_switch", [])))
    n["pvdaq_loader_note"] = _ds(study, "pvdaq_34")["conversions"][0]
    hot = _ds(study, "hill_of_towie_2021")["qc"]["highlights"]["north_offsets"]
    n["hot_north_turbines"] = str(hot["turbines"])
    n["hot_north_flagged"] = str(len(hot["flagged"]))
    n["hot_north_max"] = f"{hot['max_abs_deg']:.1f}"
    n["hot_north_dev"] = hot["max_abs_device"]
    lhb = _ds(study, "la_haute_borne")
    td = lhb["time_diagnostics"]
    stamped = _ds(study, "la_haute_borne_as_stamped")["qc"]["highlights"]["dst"]
    n["lhb_dst_hit"], n["lhb_dst_total"] = str(stamped["transitions_hit"]), str(stamped["transitions"])
    n["lhb_rows"], n["lhb_days"] = f"{td['rows_per_turbine']:,}", str(td["rows_per_turbine"] // 144)
    mh = td["temperature_maximum_hour"]
    order = ("before_spring", "after_spring", "before_autumn", "after_autumn")
    n["lhb_wall"] = ", ".join(f"{mh[k]['wall_clock']:.1f}" for k in order)
    n["lhb_utc"] = ", ".join(f"{mh[k]['stamped_utc']:.1f}" for k in order)
    n["lhb_repeated"] = str(td["spring_repeated_stamps_per_turbine"] // 2)
    n["lhb_clear"], n["lhb_cases"] = str(td["spring_pair_assignments"]["clear_winner"]), str(td["spring_pair_assignments"]["cases"])
    n["lhb_dropped"] = f"{lhb['removed']['ambiguous_time_rows']:,}"
    k = _ds(study, "kelmarsh_2019")["qc"]["highlights"]["north_offsets"]
    n["kel_north_max"] = f"{k['max_abs_deg']:.1f}"
    for name, key in (("kelmarsh_2019", "kel"), ("penmanshiel_2020", "pen")):
        i = _iec(study, name)
        n[f"{key}_stop_events"] = f"{i['stop_events']:,}"
        n[f"{key}_agree"] = pct(i["agreement_with_operator"])
        n[f"{key}_compared"] = f"{i['compared_stop_events']:,}"
        n[f"{key}_in_table"] = pct(i["stop_codes_in_table"])
        n[f"{key}_avail_iec"] = pct(i["time_availability_iec_operational"])
        n[f"{key}_avail_oem"] = pct(i["time_availability_oem_warranty"])
    h = _iec(study, "hill_of_towie_2021")
    n["hot_events"] = f"{h['events']:,}"
    n["hot_events_in_table"] = pct(h["events_in_table"])
    n["hot_events_published"] = pct(h["events_with_published_code"])
    n["hot_published_codes"] = str(h["table_published_codes"])
    n["hot_stop_events"] = f"{h['stop_events']:,}"
    n["hot_distinct_codes"] = str(h["distinct_codes"])
    p = study.get("parity")
    if p:
        w, o, v = p["windup"], p["openoa"], p["pvlib"]
        n.update({
            "wu_rows": f"{w['rows']:,}", "wu_input_diff": f"{w['input_max_abs_diff']:.1e}", "wu_same_missing": "identical" if w["filtered_same_missing"] else "DIFFERENT",
            "wu_filtered_diff": f"{w['filtered_max_abs_diff']:.1e}", "wu_removed": pct(w["removed_share"]), "wu_pc_diff": f"{w['power_curve_max_abs_diff_kw']:.1e}",
            "oa_rows": f"{o['rows']:,}", "oa_scada_diff": f"{o['scada_max_abs_diff']:.1e}", "oa_curve_diff": f"{o['iec_curve_max_abs_diff_kw']:.1e}",
            "oa_aligned_diff": f"{o['aligned_bins_vs_res_asset_kpi_max_abs_diff_kw']:.1e}",
            "oa_default_pct": f"{o['default_bins_vs_res_asset_kpi_max_pct_of_rated']:.1f}",
            "pl_intervals": f"{v['intervals']:,}", "pl_daylight": f"{v['daylight_intervals']:,}", "pl_rel": f"{v['max_rel_diff']:.1e}",
            "pl_abs": f"{v['max_abs_diff_w']:.1e}", "pl_pdc": f"{v['pdc0_kw']:.1f}",
        })
    return n


def table_datasets(study: dict) -> str:
    rows = ["| Dataset | Source (licence) | Profile | Interval | Rows | Devices | Signals | Schema | Removed by the loader | QC findings (error / warning / info) |",
            "|---|---|---|---|---:|---:|---:|---|---|---|"]
    for d in study["datasets"]:
        c = d["qc"]["counts"]
        rem = d["removed"]
        removed = ", ".join(f"{v:,} {k.replace('_', ' ')}" for k, v in rem.items() if v) or "none"
        name = f"[{d['name']}](https://kwwaleszczynski-del.github.io/scada-schema/qc/{d['name']}.html)" + (" (demonstration, not counted)" if d["demo"] else "")
        rows.append(f"| {name} | {d['source']} ({d['license']}) | {d['profile']} | "
                    f"{d['interval']} | {d['rows']:,} | {d['devices']} | {d['signals']} (+{d['stat_columns']} statistics) | "
                    f"{'valid' if d['validation']['ok'] else 'INVALID'} | {removed} | {c['error']} / {c['warning']} / {c['info']} |")
    return "\n".join(rows)


def table_checks(study: dict) -> str:
    checks = study["datasets"][0]["qc"]["checks_run"]
    names = [d["name"] for d in study["datasets"]]
    head = "| Check | " + " | ".join(names) + " |"
    rows = [head, "|---|" + "---:|" * len(names)]
    for c in checks:
        cells = []
        for d in study["datasets"]:
            if c in d["qc"]["checks_skipped"]:
                cells.append("skipped")
            else:
                b = d["qc"]["by_check"].get(c, {"devices": 0, "findings": 0})
                cells.append(str(b["devices"]) if b["findings"] else "0")
        rows.append(f"| `{c}` | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def table_iec(study: dict) -> str:
    rows = ["| Dataset | Code table | Events | Events with a code in the table | Stop events | Compared with the operator's category | Agreement | Time-based availability, IEC operational / OEM style |",
            "|---|---|---:|---:|---:|---:|---:|---|"]
    for i in study["iec"]:
        agree = pct(i["agreement_with_operator"]) if i.get("agreement_with_operator") is not None else "no operator category"
        comp = f"{i['compared_stop_events']:,}" if "compared_stop_events" in i else "-"
        av = f"{pct(i['time_availability_iec_operational'])} / {pct(i['time_availability_oem_warranty'])}" if "time_availability_iec_operational" in i else "not computed"
        rows.append(f"| {i['name']} | `{i['table']}` | {i['events']:,} | {pct(i['events_in_table'])} | {i['stop_events']:,} | {comp} | {agree} | {av} |")
    return "\n".join(rows)


def table_parity(study: dict) -> str:
    p = study["parity"]
    w, o, v = p["windup"], p["openoa"], p["pvlib"]
    return "\n".join([
        "| Target | Input | What runs on both inputs | Largest difference |",
        "|---|---|---|---|",
        f"| wind-up 0.5 | {w['dataset']}, {w['rows']:,} rows | the SCADA frame itself | {w['input_max_abs_diff']:.1e} (float32 rounding) |",
        f"| | | `filter_scada_df`: same rows removed ({pct(w['removed_share'])} of power values) | {w['filtered_max_abs_diff']:.1e}; missing pattern {'identical' if w['filtered_same_missing'] else 'DIFFERENT'} |",
        f"| | | `calc_pc_and_rated_ws_one_ttype` power curve | {w['power_curve_max_abs_diff_kw']:.1e} kW |",
        f"| OpenOA 3 | {o['dataset']}, {o['rows']:,} rows | `PlantData.scada` | {o['scada_max_abs_diff']:.1e} |",
        f"| | | `power_curve.IEC` | {o['iec_curve_max_abs_diff_kw']:.1e} kW |",
        f"| pvlib 0.16 | {v['dataset']}, {v['intervals']:,} intervals | PVWatts `ModelChain` (modelled plant up to {v['max_modelled_ac_kw']:.0f} kW) | {v['max_rel_diff']:.1e} relative in daylight ({v['max_abs_diff_w']:.1e} W) |",
        f"| | | cell temperature from the measured `module_temperature` against SAPM's module-to-cell formula called directly | {v['module_temperature_vs_manual_max_abs_diff_k']:.1e} K; the measured module temperature moves the cell temperature by a median of {v['module_temperature_changes_cell_temperature_k']:.1f} K (daylight) against the ambient-air model |",
    ])


def release_blocks(rel: dict) -> dict[str, str]:
    """Badges, install and citation text from release.json (the version, whether it is on PyPI and the DOIs once Zenodo has assigned them)."""
    badges = [f"[![tests](https://github.com/{REPO}/actions/workflows/ci.yml/badge.svg)](https://github.com/{REPO}/actions/workflows/ci.yml)"]
    if rel["published_on_pypi"]:
        badges.append("[![PyPI](https://img.shields.io/pypi/v/scada-schema.svg)](https://pypi.org/project/scada-schema/)")
    if rel["doi_concept"]:
        badges.append(f"[![DOI](https://zenodo.org/badge/DOI/{rel['doi_concept']}.svg)](https://doi.org/{rel['doi_concept']})")
    badges.append("[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/" + REPO + "/blob/main/LICENSE)")
    if rel["published_on_pypi"]:
        install = "```bash\npip install scada-schema                  # the schema, loaders, quality control and states\npip install \"scada-schema[converters]\"   # and wind-up, OpenOA and pvlib\n```"
    else:
        install = (f"Not on PyPI yet; install from GitHub:\n\n```bash\npip install \"scada-schema @ git+https://github.com/{REPO}\"\n"
                   f"pip install \"scada-schema[converters] @ git+https://github.com/{REPO}\"   # with wind-up, OpenOA and pvlib\n```")
    cite = (f"Cite the software through [CITATION.cff](https://github.com/{REPO}/blob/main/CITATION.cff) (GitHub's \"Cite this repository\" reads it). ")
    if rel["doi_concept"]:
        cite += f"DOI of all versions: [{rel['doi_concept']}](https://doi.org/{rel['doi_concept']})"
        if rel["doi_version"]:
            cite += f"; version {rel['version']}: [{rel['doi_version']}](https://doi.org/{rel['doi_version']})"
        cite += "."
    else:
        cite += "A DOI is assigned by Zenodo when the first release is archived; until then cite the repository and the version."
    return {"BADGES": " ".join(badges), "INSTALL": install, "CITATION": cite}


def render(template: str, study: dict) -> str:
    nums = numbers(study)
    blocks = {**release_blocks(json.loads(RELEASE.read_text(encoding="utf-8"))), "TABLE_DATASETS": table_datasets(study), "TABLE_CHECKS": table_checks(study), "TABLE_IEC": table_iec(study)}
    if "parity" in study:
        blocks["TABLE_PARITY"] = table_parity(study)
    out = template
    for k, v in blocks.items():
        out = out.replace("{{" + k + "}}", v)

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in nums:
            raise KeyError(f"README template uses {{{{{key}}}}}, which the study does not provide")
        return nums[key]

    return re.sub(r"\{\{([a-z0-9_]+)\}\}", sub, out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = render(TEMPLATE.read_text(encoding="utf-8"), json.loads(STUDY.read_text(encoding="utf-8")))
    if args.check:
        ok = README.read_text(encoding="utf-8") == text
        print("README.md is up to date" if ok else "README.md differs from the rendering of the template and results/study.json")
        return 0 if ok else 1
    README.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {README}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
