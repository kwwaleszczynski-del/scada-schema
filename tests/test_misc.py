from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scada_schema import SchemaError, finalise, read_meta
from scada_schema._steps import level_step
from scada_schema.loaders._paths import raw_dir


def test_level_step_finds_the_day_of_a_step_and_ignores_noise():
    idx = pd.date_range("2020-01-01", periods=60, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    lv = pd.Series(np.where(np.arange(60) < 30, 0.0, 2.0) + rng.normal(0, 0.05, 60), index=idx)
    day, step = level_step(lv, 7, 0.8)
    assert step == pytest.approx(2.0, abs=0.1) and day == idx[30]
    assert level_step(pd.Series(rng.normal(0, 0.05, 60), index=idx), 7, 0.8) is None
    assert level_step(lv.iloc[:10], 7, 0.8) is None  # too short


def test_finalise_orders_columns_and_types():
    df = pd.DataFrame({"device_id": ["B", "A"], "p_ac": [1.0, 2.0], "x_extra": [1, 2], "e_export": [1.5, 2.5], "wind_speed": [3.0, 4.0],
                       "timestamp": pd.to_datetime(["2021-01-01 00:10", "2021-01-01 00:00"], utc=True)})
    out = finalise(df)
    assert list(out.columns) == ["timestamp", "device_id", "p_ac", "e_export", "wind_speed", "x_extra"]
    assert list(out["device_id"]) == ["A", "B"] and out["p_ac"].dtype == "float32" and out["e_export"].dtype == "float64"


def test_read_meta_refuses_plain_parquet(tmp_path):
    p = tmp_path / "plain.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(p)
    with pytest.raises(SchemaError, match="not a canonical table"):
        read_meta(p)


def test_raw_dir_order(monkeypatch, tmp_path):
    monkeypatch.delenv("SCADA_SCHEMA_DATA", raising=False)
    monkeypatch.delenv("SCADA_SCHEMA_KELMARSH_DIR", raising=False)
    assert str(raw_dir("kelmarsh")).replace("\\", "/") == "data/kelmarsh"
    monkeypatch.setenv("SCADA_SCHEMA_DATA", str(tmp_path))
    assert raw_dir("kelmarsh") == tmp_path / "kelmarsh"
    monkeypatch.setenv("SCADA_SCHEMA_KELMARSH_DIR", str(tmp_path / "k"))
    assert raw_dir("kelmarsh") == tmp_path / "k"
    assert raw_dir("kelmarsh", tmp_path / "explicit") == tmp_path / "explicit"
