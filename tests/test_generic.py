from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scada_schema import read_parquet
from scada_schema.cli import main
from scada_schema.loaders.generic import ImportSpec, import_table, linear, load_spec

ROOT = Path(__file__).resolve().parents[1]


def starts_utc(n=60, start="2021-10-30 20:00"):
    return pd.date_range(start, periods=n, freq="10min", tz="UTC")


def local_end_stamps(starts: pd.DatetimeIndex, tz="Europe/Warsaw") -> list[str]:
    """What a logger in local time writes when it stamps the END of each interval (the repeated autumn hour appears twice)."""
    return [t.tz_convert(tz).strftime("%d.%m.%Y %H:%M") for t in starts + pd.Timedelta("10min")]


def write_stacked(path: Path, starts, devices=("T01", "T02")) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    rows = []
    for d in devices:
        ws_kmh = 30 + rng.normal(0, 3, len(starts))
        rows.append(pd.DataFrame({"Time": local_end_stamps(starts), "Turbine": d, "Active power [W]": (ws_kmh * 40_000).round(0), "Wind [km/h]": ws_kmh,
                                  "Wind std [km/h]": 1.8, "Ambient [F]": 50.0, "Status": "0.0"}))
    df = pd.concat(rows, ignore_index=True)
    df["Active power [W]"] = df["Active power [W]"].map(lambda x: f"{x:.0f}")
    out = df.copy()
    for c in ("Wind [km/h]", "Wind std [km/h]", "Ambient [F]"):
        out[c] = out[c].map(lambda x: f"{x:.2f}".replace(".", ","))
    out.to_csv(path, sep=";", index=False, encoding="utf-8")
    return df


SPEC = {
    "name": "my_farm", "profile": "wind", "interval": "10min", "files": ["export/*.csv"],
    "read": {"sep": ";", "decimal": ","},
    "timestamp": {"column": "Time", "timezone": "Europe/Warsaw", "label": "end", "format": "%d.%m.%Y %H:%M"},
    "device_column": "Turbine",
    "columns": {
        "Active power [W]": {"signal": "p_ac", "unit": "W"}, "Wind [km/h]": {"signal": "wind_speed", "unit": "km/h"},
        "Wind std [km/h]": {"signal": "wind_speed", "unit": "km/h", "stat": "std"}, "Ambient [F]": {"signal": "t_amb", "unit": "degF"},
        "Status": {"signal": "status_code"}},
    "devices": {"T01": {"type": "turbine", "rated_kw": 2050}, "T02": {"type": "turbine", "rated_kw": 2050}},
}


@pytest.fixture
def stacked(tmp_path):
    (tmp_path / "export").mkdir()
    starts = starts_utc()
    raw = write_stacked(tmp_path / "export" / "a.csv", starts)
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    return tmp_path, starts, raw


def test_stacked_export_with_units_dst_and_end_label(stacked):
    d, starts, raw = stacked
    L = import_table(d / "mapping.yaml")
    t = L.table[L.table.device_id == "T01"].reset_index(drop=True)
    # the 02:00-03:00 hour of 31 Oct 2021 occurs twice in the file; in UTC the series is contiguous and starts where it should
    assert (t["timestamp"] == starts).all() and not L.table.duplicated(["device_id", "timestamp"]).any()
    r = raw[raw.Turbine == "T01"].reset_index(drop=True)
    np.testing.assert_allclose(t["p_ac"], r["Active power [W]"].astype(float) / 1000, rtol=1e-6)  # W to kW
    np.testing.assert_allclose(t["wind_speed"], r["Wind [km/h]"] / 3.6, rtol=1e-4, atol=0.01)  # km/h to m/s (the file has two decimals)
    assert t["wind_speed__std"].iloc[0] == pytest.approx(1.8 / 3.6, abs=0.01)
    assert t["t_amb"].iloc[0] == pytest.approx(10.0, abs=1e-5)  # 50 F
    assert t["status_code"].iloc[0] == "0"
    assert L.meta.source_timezone == "Europe/Warsaw" and L.meta.source_label == "end" and L.meta.device("T01").rated_kw == 2050
    assert any("W -> kW" in c for c in L.meta.conversions) and any("degF -> degC" in c for c in L.meta.conversions)


def test_wrong_timezone_shows_up_as_a_shift_and_dropped_duplicates(stacked):
    d, _, _ = stacked
    wrong = import_table(ImportSpec.model_validate({**SPEC, "timestamp": {**SPEC["timestamp"], "timezone": "UTC"}, "base_dir": d}))
    right = import_table(d / "mapping.yaml")
    assert wrong.table["timestamp"].min() - right.table["timestamp"].min() == pd.Timedelta("2h")  # local stamps read as UTC
    assert any("duplicated" in c for c in wrong.meta.conversions)  # the repeated autumn hour collides: the loader says so


def test_off_grid_stamps_raise_and_round_fixes_them(tmp_path):
    t = pd.date_range("2021-06-01", periods=30, freq="10min") + pd.Timedelta("4s")
    pd.DataFrame({"Time": t.strftime("%Y-%m-%d %H:%M:%S"), "P": 100.0, "W": 5.0}).to_csv(tmp_path / "x.csv", index=False)
    spec = {"name": "g", "profile": "wind", "interval": "10min", "files": ["x.csv"], "device": "T01", "timestamp": {"column": "Time"},
            "columns": {"P": {"signal": "p_ac"}, "W": {"signal": "wind_speed"}}, "devices": {"T01": {"type": "turbine", "rated_kw": 2000}}}
    with pytest.raises(ValueError, match="not on the 10min grid"):
        import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    spec["timestamp"]["round"] = True
    L = import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    assert len(L.table) == 30 and L.table["timestamp"].iloc[0] == pd.Timestamp("2021-06-01", tz="UTC")


def test_one_file_per_device_regex_and_sign(tmp_path):
    for k in (1, 12):
        t = pd.date_range("2021-06-01", periods=20, freq="10min")
        pd.DataFrame({"time": t.strftime("%Y-%m-%d %H:%M"), "kW": -50.0 * k, "ws": 6.0}).to_csv(tmp_path / f"WT{k}_data.csv", index=False)
    spec = {"name": "g", "profile": "wind", "interval": "10min", "files": ["WT*_data.csv"], "device_regex": r"WT(\d+)_", "device_format": "T{:02d}",
            "timestamp": {"column": "time"}, "columns": {"kW": {"signal": "p_ac", "sign": -1}, "ws": {"signal": "wind_speed"}}}
    L = import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    assert sorted(L.table.device_id.unique()) == ["T01", "T12"]
    assert L.table[L.table.device_id == "T12"]["p_ac"].iloc[0] == pytest.approx(600.0)  # sign flipped: consumption convention to generator


def test_wide_layout_every_column_names_its_device(tmp_path):
    t = pd.date_range("2021-06-01", periods=20, freq="15min")
    pd.DataFrame({"time": t.strftime("%Y-%m-%d %H:%M"), "INV1 Pac": 80.0, "INV2 Pac": 90.0, "POA": 700.0, "Tamb": 20.0}).to_csv(tmp_path / "w.csv", index=False)
    spec = {"name": "pvplant", "profile": "pv", "interval": "15min", "files": ["w.csv"], "timestamp": {"column": "time"},
            "columns": {"INV1 Pac": {"signal": "p_ac", "device": "INV1"}, "INV2 Pac": {"signal": "p_ac", "device": "INV2"},
                        "POA": {"signal": "poa", "device": "MET"}, "Tamb": {"signal": "t_amb", "device": "MET"}},
            "devices": {"MET": {"type": "met"}, "INV1": {"type": "inverter", "rated_kw": 100}, "INV2": {"type": "inverter", "rated_kw": 100}}}
    L = import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    assert sorted(L.table.device_id.unique()) == ["INV1", "INV2", "MET"]
    assert L.table[L.table.device_id == "MET"]["poa"].iloc[0] == 700.0 and "poa" not in L.table[L.table.device_id == "INV1"].dropna(axis=1, how="all")


def test_code_table_adds_iec_category(tmp_path):
    t = pd.date_range("2021-06-01", periods=10, freq="10min")
    pd.DataFrame({"time": t.strftime("%Y-%m-%d %H:%M"), "P": 1.0, "W": 5.0, "S": ["0"] * 5 + ["100"] * 5}).to_csv(tmp_path / "s.csv", index=False)
    spec = {"name": "g", "profile": "wind", "interval": "10min", "files": ["s.csv"], "device": "T01", "timestamp": {"column": "time"},
            "columns": {"P": {"signal": "p_ac"}, "W": {"signal": "wind_speed"}, "S": {"signal": "status_code"}}, "code_table": "senvion_mm",
            "devices": {"T01": {"type": "turbine", "rated_kw": 2000}}}
    L = import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    assert list(L.table["iec_category"].iloc[[0, 9]]) == [None, "forced_outage"]  # 100: safety chain open


@pytest.mark.parametrize("change,message", [
    ({"columns": {"P": {"signal": "power"}}}, "not a canonical signal"),
    ({"columns": {"P": {"signal": "p_ac", "unit": "degC"}}}, "cannot convert"),
    ({"columns": {"P": {"signal": "status_code", "unit": "kW"}}}, "text"),
    ({"columns": {"P": {"signal": "shutdown_s", "stat": "std"}}}, "min, max or std"),
    ({"device": None, "device_column": None}, "exactly one way"),
    ({"device_column": "X"}, "exactly one way"),
    ({"interval": "7min"}, "not supported"),
])
def test_bad_mappings_are_rejected(change, message):
    base = {"name": "g", "profile": "wind", "interval": "10min", "files": ["x.csv"], "device": "T01", "timestamp": {"column": "t"},
            "columns": {"P": {"signal": "p_ac"}}}
    with pytest.raises(Exception, match=message):
        ImportSpec.model_validate({**base, **change})


def test_missing_column_and_missing_file_messages(tmp_path):
    pd.DataFrame({"t": ["2021-06-01 00:00"], "P": [1.0]}).to_csv(tmp_path / "x.csv", index=False)
    spec = {"name": "g", "profile": "wind", "interval": "10min", "files": ["x.csv"], "device": "T01", "timestamp": {"column": "t"},
            "columns": {"P": {"signal": "p_ac"}, "Wind": {"signal": "wind_speed"}}}
    with pytest.raises(ValueError, match=r"columns \['Wind'\] named in the mapping are not in the file"):
        import_table(ImportSpec.model_validate({**spec, "base_dir": tmp_path}))
    with pytest.raises(FileNotFoundError, match="no file matches"):
        import_table(ImportSpec.model_validate({**spec, "files": ["nothing*.csv"], "base_dir": tmp_path}))


def test_linear_units():
    assert linear("W", "kW") == (pytest.approx(1e-3), 0.0)
    a, b = linear("degF", "degC")
    assert a * 212 + b == pytest.approx(100.0)
    assert linear("kV", "V") == (1000.0, 0.0) and linear("Mvar", "kvar") == (1000.0, 0.0)
    assert linear("%", "1") == (pytest.approx(0.01), 0.0)
    with pytest.raises(ValueError):
        linear("kW", "m/s")


def test_cli_import_roundtrip(stacked, tmp_path):
    d, starts, _ = stacked
    out = tmp_path / "farm.parquet"
    assert main(["import", str(d / "mapping.yaml"), "--out", str(out)]) == 0
    df, meta = read_parquet(out)
    assert meta.name == "my_farm" and (df[df.device_id == "T01"]["timestamp"].reset_index(drop=True) == starts).all()


def test_example_mapping_files_are_valid():
    for f in sorted((ROOT / "docs" / "examples").glob("*.yaml")):
        spec = load_spec(f)
        assert spec.columns and spec.name


def test_json_schema_files_are_current():
    from scada_schema import jsonschema

    for name, text in jsonschema.build().items():
        assert (ROOT / "schema" / name).read_text(encoding="utf-8") == text, name
    cols = json.loads((ROOT / "schema" / "columns.json").read_text(encoding="utf-8"))["columns"]
    names = {c["name"] for c in cols}
    assert {"timestamp", "device_id", "p_ac", "p_ac__std", "poa", "iec_category"} <= names and "shutdown_s__std" not in names
