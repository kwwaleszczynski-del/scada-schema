from __future__ import annotations

import json

import pandas as pd
import pytest

from scada_schema import read_parquet
from scada_schema.cli import build_parser, main


def test_signals_listing(capsys):
    assert main(["signals", "--profile", "pv", "--format", "csv"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("signal,unit,kind") and "poa" in out and "wind_dir_rel" not in out


def test_help_lists_every_command():
    text = build_parser().format_help()
    for cmd in ("signals", "fetch", "load", "validate", "qc", "convert", "study"):
        assert cmd in text


def test_load_validate_qc_roundtrip(kelmarsh_dir, tmp_path, capsys):
    out = tmp_path / "k.parquet"
    assert main(["load", "kelmarsh", "--data-dir", str(kelmarsh_dir), "--years", "2019", "--states", "--out", str(out)]) == 0
    df, meta = read_parquet(out)
    assert meta.name == "kelmarsh" and "iec_category" in df.columns and set(df["iec_category"].dropna()) >= {"full_performance", "forced_outage"}
    assert out.with_suffix(".events.parquet").exists()
    assert main(["validate", str(out)]) == 0
    assert "valid" in capsys.readouterr().out
    html, js = tmp_path / "qc.html", tmp_path / "qc.json"
    assert main(["qc", str(out), "--html", str(html), "--json", str(js), "--checks", "gaps", "flatline", "units"]) == 0
    assert "Quality report" in html.read_text(encoding="utf-8")
    assert json.loads(js.read_text(encoding="utf-8"))["checks_run"] == ["gaps", "flatline", "units"]


def test_qc_fail_on_error(tmp_path, wind):
    from scada_schema import write_parquet

    df, meta = wind
    df = df.assign(p_ac=df["p_ac"] * 1000)  # watts: the units check reports an error
    p = write_parquet(df, meta, tmp_path / "w.parquet")
    assert main(["qc", str(p), "--checks", "units", "--fail-on-error"]) == 1
    assert main(["qc", str(p), "--checks", "units"]) == 0


def test_validate_returns_one_for_an_invalid_file(tmp_path, wind):
    from scada_schema import write_parquet

    df, meta = wind
    p = write_parquet(df, meta, tmp_path / "ok.parquet")
    assert main(["validate", str(p)]) == 0
    bad = pd.read_parquet(p).assign(x=1).rename(columns={"x": "not_a_signal"})
    bad_path = tmp_path / "bad.parquet"
    import pyarrow as pa
    import pyarrow.parquet as pq

    tab = pa.Table.from_pandas(bad, preserve_index=False).replace_schema_metadata(pq.read_schema(p).metadata)
    pq.write_table(tab, bad_path)
    assert main(["validate", str(bad_path)]) == 1


def test_missing_data_is_a_clean_error(tmp_path, caplog):
    assert main(["load", "kelmarsh", "--data-dir", str(tmp_path), "--out", str(tmp_path / "x.parquet")]) == 2
    assert "scada-schema fetch" in caplog.text


def test_convert_windup_and_openoa_and_pvlib(tmp_path, pvdaq_dir):
    pytest.importorskip("pvlib")
    from scada_schema import write_parquet
    from scada_schema.loaders import pvdaq

    L = pvdaq.load(pvdaq_dir, system=34, years=[2018])
    p = write_parquet(L.table, L.meta, tmp_path / "pv.parquet", check=False)
    assert main(["convert", str(p), "--to", "pvlib", "--out", str(tmp_path / "pv"), "--at", "center"]) == 0
    spec = json.loads((tmp_path / "pv" / "pvlib.json").read_text(encoding="utf-8"))
    assert spec["system"]["pdc0"] == 146_600.0 and (tmp_path / "pv" / "weather.parquet").exists()


def test_convert_windup(tmp_path):
    import sys

    sys.path.insert(0, str(tmp_path.parent))
    from conftest import greenbyte_csv

    from scada_schema import DatasetMeta, Device, finalise, write_parquet
    from scada_schema.loaders import greenbyte

    ids = ["T01", "T02"]
    df = finalise(pd.concat([greenbyte.parse_turbine_csv(greenbyte_csv(k, n=6 * 24 * 10), i) for k, i in enumerate(ids, 1)], ignore_index=True))
    meta = DatasetMeta(name="w", profile="wind", interval="10min", devices=[
        Device(id=i, type="turbine", rated_kw=2050, rotor_diameter_m=92) for i in ids])
    p = write_parquet(df, meta, tmp_path / "w.parquet")
    assert main(["convert", str(p), "--to", "windup", "--out", str(tmp_path / "wu"), "--turbine-type", "MM92"]) == 0
    spec = json.loads((tmp_path / "wu" / "windup.json").read_text(encoding="utf-8"))
    assert spec["turbine_type"]["rated_power_kw"] == 2050 and len(spec["turbines"]) == 2
    scada = pd.read_parquet(tmp_path / "wu" / "scada.parquet")
    assert {"TurbineName", "ActivePowerMean", "GenRpmMean"} <= set(scada.columns)
    # OpenOA needs positions: refused cleanly
    assert main(["convert", str(p), "--to", "openoa", "--out", str(tmp_path / "oa")]) == 2
