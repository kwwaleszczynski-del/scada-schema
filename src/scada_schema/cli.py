"""Command line interface of scada-schema.

Usage:
    scada-schema signals --profile wind
    scada-schema fetch kelmarsh --years 2019 --data-dir data/kelmarsh
    scada-schema load kelmarsh --years 2019 --data-dir data/kelmarsh --states --out out/kelmarsh_2019.parquet
    scada-schema validate out/kelmarsh_2019.parquet
    scada-schema qc out/kelmarsh_2019.parquet --html out/qc.html --dst-zone Europe/London
    scada-schema convert out/kelmarsh_2019.parquet --to windup --out out/windup
    scada-schema resample plant.parquet --to 1h --out plant_1h.parquet
    scada-schema import mapping.yaml --out my_farm.parquet
    scada-schema jsonschema --out schema
    scada-schema spec > SPEC.md
    scada-schema study --out results
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from scada_schema import __version__
from scada_schema.io import read_parquet, write_parquet
from scada_schema.loaders import DATASETS
from scada_schema.signals import PROFILE_SIGNALS, SIGNALS
from scada_schema.validate import validate

log = logging.getLogger("scada_schema")


def _signals(args) -> int:
    rows = [s for n, s in SIGNALS.items() if args.profile in (None, *s.profiles)]
    df = pd.DataFrame([{"signal": s.name, "unit": s.unit, "kind": s.kind, "profiles": "+".join(s.profiles), "devices": ",".join(s.devices),
                        "lo": s.lo, "hi": s.hi, "stats": s.stats, "description": s.description} for s in rows])
    if args.format == "csv":
        print(df.to_csv(index=False))
    elif args.format == "json":
        print(df.to_json(orient="records", indent=1))
    else:
        print(df.to_string(index=False, max_colwidth=70))
    log.info("%d signals (wind %d, pv %d)", len(df), len(PROFILE_SIGNALS["wind"]), len(PROFILE_SIGNALS["pv"]))
    return 0


def _loader_kwargs(args) -> dict:
    mod = DATASETS[args.dataset]
    kw: dict = {}
    if args.dataset == "pvdaq":
        kw["system"] = args.system
        if args.no_fix_units:
            kw["fix_units"] = False
    elif args.dataset == "care":
        kw.update(farm=args.farm, event_id=args.event)
    if args.years and args.dataset not in ("care", "la_haute_borne"):
        kw["years"] = args.years
    del mod
    return kw


def _fetch(args) -> int:
    mod = DATASETS[args.dataset]
    if args.dataset == "pvdaq":
        n = mod.fetch(args.data_dir, system=args.system, years=args.years or [2016])
        log.info("fetched %d daily files", n)
    elif args.dataset == "care":
        p = mod.fetch(args.data_dir, datasets={args.farm: [args.event]})
        log.info("wrote %s", p)
    elif args.dataset == "la_haute_borne":
        for p in mod.fetch(args.data_dir):
            log.info("ready %s", p)
    else:
        for p in mod.fetch(args.data_dir, years=args.years):
            log.info("ready %s", p)
    return 0


def _load(args) -> int:
    mod = DATASETS[args.dataset]
    loaded = mod.load(args.data_dir, **_loader_kwargs(args))
    if args.states:
        from scada_schema.states import attach_states

        if len(loaded.events):
            attach_states(loaded)
        else:
            log.warning("no event log: --states ignored")
    rep = validate(loaded.table, loaded.meta)
    for i in rep.issues:
        log.log(logging.ERROR if i.level == "error" else logging.WARNING, "%s: %s", i.code, i.message)
    out = write_parquet(loaded.table, loaded.meta, args.out)
    if len(loaded.events):
        loaded.events.to_parquet(Path(args.out).with_suffix(".events.parquet"))
    log.info("wrote %s: %d rows, %d devices, %d events", out, len(loaded.table), loaded.table["device_id"].nunique(), len(loaded.events))
    return 0


def _validate(args) -> int:
    df, meta = read_parquet(args.file)
    rep = validate(df, meta)
    print(rep.to_frame().to_string(index=False) if rep.issues else "no issues")
    print(f"{len(df):,} rows, {df['device_id'].nunique()} devices, profile {meta.profile}, interval {meta.interval}: "
          + ("valid" if rep.ok else f"{len(rep.errors)} error(s)"))
    return 0 if rep.ok else 1


def _qc(args) -> int:
    from scada_schema import qc

    df, meta = read_parquet(args.file)
    cfg = qc.QCConfig(dst_zone=args.dst_zone) if args.dst_zone else None
    report = qc.run(df, meta, cfg, checks=args.checks)
    s = report.summary()
    print(s.to_string(index=False) if len(s) else "no findings")
    print("severity counts:", report.counts(), "| skipped:", report.checks_skipped or "none")
    if args.html:
        report.to_html(args.html)
        log.info("wrote %s", args.html)
    if args.json:
        report.to_json(args.json)
        log.info("wrote %s", args.json)
    return 1 if args.fail_on_error and report.counts()["error"] else 0


def _convert(args) -> int:
    df, meta = read_parquet(args.file)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.to == "windup":
        from scada_schema.convert import windup

        scada, notes = windup.to_windup_scada(df, meta)
        scada.to_parquet(out / "scada.parquet")
        spec = {"notes": notes}
        try:
            spec["turbine_type"] = windup.windup_turbine_types(df, meta, args.turbine_type)
            spec["turbines"] = windup.windup_turbines(meta, spec["turbine_type"])
        except ValueError as e:
            spec["turbine_type_error"] = str(e)
            log.warning("%s", e)
        (out / "windup.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    elif args.to == "openoa":
        from scada_schema.convert import openoa

        scada, asset = openoa.to_openoa_frames(df, meta)
        scada.to_parquet(out / "scada.parquet")
        asset.to_parquet(out / "asset.parquet")
    else:
        from scada_schema.convert import pvlib

        inp = pvlib.to_pvlib(df, meta, inverter=args.inverter, at=args.at)
        inp.weather.to_parquet(out / "weather.parquet")
        inp.p_ac_kw.to_frame().to_parquet(out / "p_ac_kw.parquet")
        (out / "pvlib.json").write_text(json.dumps({"location": inp.location, "system": inp.system, "notes": inp.notes}, indent=1), encoding="utf-8")
    log.info("wrote %s", out)
    return 0


def _resample(args) -> int:
    from scada_schema.resample import resample

    df, meta = read_parquet(args.file)
    out, m = resample(df, meta, args.to, args.min_coverage)
    write_parquet(out, m, args.out)
    log.info("wrote %s: %d rows at %s (from %d at %s)", args.out, len(out), args.to, len(df), meta.interval)
    return 0


def _import(args) -> int:
    from scada_schema.loaders.generic import import_table, load_spec

    loaded = import_table(load_spec(args.mapping), validate_result=not args.no_validate)
    out = write_parquet(loaded.table, loaded.meta, args.out, check=not args.no_validate)
    log.info("wrote %s: %d rows, %d devices", out, len(loaded.table), loaded.table["device_id"].nunique())
    return 0


def _jsonschema(args) -> int:
    from scada_schema import jsonschema

    for p in jsonschema.write(args.out):
        log.info("wrote %s", p)
    return 0


def _spec(args) -> int:
    from scada_schema.spec import render_spec

    sys.stdout.write(render_spec())
    return 0


def _study(args) -> int:
    from scada_schema import study

    study.run(Path(args.out), only=args.only, skip_converters=args.skip_converters)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scada-schema", description="Open data standard for wind and PV SCADA.")
    p.add_argument("--version", action="version", version=f"scada-schema {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("signals", help="list the canonical signals")
    s.add_argument("--profile", choices=["wind", "pv"])
    s.add_argument("--format", choices=["table", "csv", "json"], default="table")
    s.set_defaults(func=_signals)

    def dataset_args(sp):
        sp.add_argument("dataset", choices=sorted(DATASETS))
        sp.add_argument("--data-dir", help="folder with the raw files (default: $SCADA_SCHEMA_<NAME>_DIR, $SCADA_SCHEMA_DATA/<name>, ./data/<name>)")
        sp.add_argument("--years", type=int, nargs="+")
        sp.add_argument("--system", type=int, default=34, choices=[34, 1276, 1278, 35], help="PVDAQ system")
        sp.add_argument("--farm", default="A", choices=["A", "B", "C"], help="CARE wind farm")
        sp.add_argument("--event", type=int, default=0, help="CARE event (dataset) id")

    s = sub.add_parser("fetch", help="download an open dataset from its original source (checksums verified)")
    dataset_args(s)
    s.set_defaults(func=_fetch)

    s = sub.add_parser("load", help="read an open dataset into a canonical Parquet file")
    dataset_args(s)
    s.add_argument("--out", required=True)
    s.add_argument("--states", action="store_true", help="add status_code and iec_category from the event log")
    s.add_argument("--no-fix-units", action="store_true", help="PVDAQ: keep the hectowatt reading of power (for demonstrating the unit check)")
    s.set_defaults(func=_load)

    s = sub.add_parser("validate", help="check a canonical Parquet file against the schema")
    s.add_argument("file")
    s.set_defaults(func=_validate)

    s = sub.add_parser("qc", help="run the quality checks")
    s.add_argument("file")
    s.add_argument("--html")
    s.add_argument("--json")
    s.add_argument("--checks", nargs="+")
    s.add_argument("--dst-zone", help="IANA zone whose daylight saving changes are looked for, e.g. Europe/Warsaw")
    s.add_argument("--fail-on-error", action="store_true", help="exit with 1 when any finding has severity error")
    s.set_defaults(func=_qc)

    s = sub.add_parser("convert", help="convert a canonical file to the input of wind-up, OpenOA or pvlib")
    s.add_argument("file")
    s.add_argument("--to", required=True, choices=["windup", "openoa", "pvlib"])
    s.add_argument("--out", required=True, help="output folder")
    s.add_argument("--turbine-type", default="turbine", help="wind-up turbine type name")
    s.add_argument("--inverter", help="pvlib: inverter id")
    s.add_argument("--at", default="center", choices=["start", "center", "end"], help="pvlib: instant of the interval that indexes the weather")
    s.set_defaults(func=_convert)

    s = sub.add_parser("resample", help="move a canonical file to a coarser interval (rules by signal kind)")
    s.add_argument("file")
    s.add_argument("--to", required=True, choices=["10min", "15min", "30min", "1h"])
    s.add_argument("--min-coverage", type=float, default=0.8, help="share of source intervals needed to fill a target interval")
    s.add_argument("--out", required=True)
    s.set_defaults(func=_resample)

    s = sub.add_parser("import", help="turn your own export into a canonical Parquet file with a YAML mapping")
    s.add_argument("mapping")
    s.add_argument("--out", required=True)
    s.add_argument("--no-validate", action="store_true")
    s.set_defaults(func=_import)

    s = sub.add_parser("jsonschema", help="write JSON Schema files and the column list of the standard")
    s.add_argument("--out", default="schema")
    s.set_defaults(func=_jsonschema)

    s = sub.add_parser("spec", help="print the specification (SPEC.md) generated from the code")
    s.set_defaults(func=_spec)

    s = sub.add_parser("study", help="run the schema, QC, mapping and parity study on the open datasets and write results/")
    s.add_argument("--out", default="results")
    s.add_argument("--only", nargs="+", help="names of the study datasets to run")
    s.add_argument("--skip-converters", action="store_true")
    s.set_defaults(func=_study)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, KeyError) as e:
        log.error("%s", e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
