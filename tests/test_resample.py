from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scada_schema import DatasetMeta, Device, finalise, read_parquet, validate, write_parquet
from scada_schema.cli import main
from scada_schema.resample import resample


def meta10():
    return DatasetMeta(name="r", profile="wind", interval="10min", devices=[Device(id="T01", type="turbine", rated_kw=2000)])


def frame(n=18, **cols):
    t = pd.date_range("2021-03-01", periods=n, freq="10min", tz="UTC")
    return finalise(pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": 100.0, "wind_speed": 5.0, **cols}))


def test_rules_by_kind():
    n = 18
    df = frame(n, e_export=np.arange(n, dtype="float64"), e_export_counter=1000.0 + np.cumsum(np.arange(n, dtype="float64")),
               status_code=["0"] * 4 + ["100"] * 2 + ["0"] * 12, iec_category=["full_performance"] * 4 + ["forced_outage"] * 2 + ["full_performance"] * 12,
               wind_speed__max=np.arange(n, dtype="float64"), wind_speed__min=np.arange(n, dtype="float64") - 1)
    df["p_ac"] = np.arange(n, dtype="float32")
    out, m = resample(df, meta10(), "1h")
    assert m.interval == "1h" and len(out) == 3 and validate(out, m).ok
    first = out.iloc[0]
    assert first["p_ac"] == pytest.approx(2.5)  # mean of 0..5
    assert first["e_export"] == pytest.approx(15.0)  # sum of 0..5
    assert first["e_export_counter"] == pytest.approx(1000.0 + np.cumsum(np.arange(n))[5])  # last reading
    assert first["status_code"] == "0" and first["iec_category"] == "full_performance"  # the most frequent
    assert first["wind_speed__max"] == 5.0 and first["wind_speed__min"] == -1.0
    assert "resampled from 10min to 1h" in m.conversions[-1]


def test_circular_mean_wraps_around_north():
    t = pd.date_range("2021-03-01", periods=6, freq="10min", tz="UTC")
    df = finalise(pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": 1.0, "wind_speed": 5.0, "wind_dir": [350, 10, 350, 10, 350, 10.0],
                                "wind_dir__min": 340.0, "wind_dir__max": 20.0}))
    out, m = resample(df, meta10(), "1h")
    d = float(out["wind_dir"].iloc[0])
    assert min(d, 360 - d) < 1e-3  # 0 degrees, not 180
    assert "wind_dir__min" not in out.columns and "minimum and maximum of angles dropped" in m.conversions[-1]


def test_pooled_standard_deviation_equals_the_standard_deviation_of_the_raw_data():
    rng = np.random.default_rng(0)
    raw = rng.normal(8, 2, 60 * 3)  # three hours at one-minute resolution
    t10 = pd.date_range("2021-03-01", periods=18, freq="10min", tz="UTC")
    blocks = raw.reshape(18, 10)
    df = finalise(pd.DataFrame({"timestamp": t10, "device_id": "T01", "p_ac": 1.0, "wind_speed": blocks.mean(axis=1), "wind_speed__std": blocks.std(axis=1)}))
    out, _ = resample(df, meta10(), "1h")
    hourly = raw.reshape(3, 60)
    np.testing.assert_allclose(out["wind_speed"], hourly.mean(axis=1), rtol=1e-5)
    np.testing.assert_allclose(out["wind_speed__std"], hourly.std(axis=1), rtol=1e-4)  # population standard deviation, float32 storage


def test_coverage_threshold_and_sums_are_not_partial():
    df = frame(12, e_export=1.0)
    df = df.drop(index=[0, 1, 2]).reset_index(drop=True)  # the first hour has three of six intervals
    out, _ = resample(df, meta10(), "1h", min_coverage=0.8)
    assert np.isnan(out["e_export"].iloc[0]) and out["e_export"].iloc[1] == 6.0
    out2, _ = resample(df, meta10(), "1h", min_coverage=0.5)
    assert out2["e_export"].iloc[0] == 3.0


def test_devices_stay_apart_and_bins_are_on_the_target_grid():
    a, b = frame(12), frame(12)
    b["device_id"] = "T02"
    m = DatasetMeta(name="r", profile="wind", interval="10min", devices=[Device(id="T01", type="turbine"), Device(id="T02", type="turbine")])
    out, mo = resample(finalise(pd.concat([a, b], ignore_index=True)), m, "30min")
    assert len(out) == 8 and set(out.device_id) == {"T01", "T02"} and validate(out, mo).ok
    assert (out["timestamp"].dt.minute % 30 == 0).all()


@pytest.mark.parametrize("to,msg", [("15min", "multiple"), ("10min", "longer"), ("5min", "longer"), ("2h", "not supported")])
def test_impossible_targets_are_refused(to, msg):
    with pytest.raises(ValueError, match=msg):
        resample(frame(12), meta10(), to)


def test_cli_resample(tmp_path):
    p = write_parquet(frame(36), meta10(), tmp_path / "a.parquet")
    assert main(["resample", str(p), "--to", "1h", "--out", str(tmp_path / "h.parquet")]) == 0
    df, meta = read_parquet(tmp_path / "h.parquet")
    assert meta.interval == "1h" and len(df) == 6
    assert main(["resample", str(p), "--to", "15min", "--out", str(tmp_path / "x.parquet")]) == 2


def test_partial_sums_are_recorded_and_not_scaled_unless_asked():
    df = frame(12, e_export=1.0)
    df = df.drop(index=[0]).reset_index(drop=True)  # the first hour has five of six intervals
    out, m = resample(df, meta10(), "1h")
    assert out["e_export"].iloc[0] == 5.0 and out["e_export"].iloc[1] == 6.0
    assert "1 sums cover only part of their interval (not scaled)" in m.conversions[-1]
    out2, m2 = resample(df, meta10(), "1h", scale_sums=True)
    assert out2["e_export"].iloc[0] == pytest.approx(6.0) and "scaled up" in m2.conversions[-1]


def test_counter_needs_the_last_interval_of_the_hour():
    n = 12
    df = frame(n, e_export_counter=1000.0 + np.arange(n, dtype="float64"))
    df.loc[5, "e_export_counter"] = np.nan  # the last reading of the first hour is missing, the ones before it are there
    out, _ = resample(df, meta10(), "1h")
    assert np.isnan(out["e_export_counter"].iloc[0]) and out["e_export_counter"].iloc[1] == 1011.0


def test_vane_angle_stays_in_its_range_and_opposite_directions_cancel():
    t = pd.date_range("2021-03-01", periods=6, freq="10min", tz="UTC")
    df = finalise(pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": 1.0, "wind_speed": 5.0, "wind_dir_rel": [-170.0, 170.0, -170.0, 170.0, -170.0, 170.0]}))
    df2 = finalise(pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": 1.0, "wind_speed": 5.0, "wind_dir": [0.0, 180.0] * 3, "wind_dir_rel": -10.0}))
    out, _ = resample(df, meta10(), "1h")
    assert abs(abs(float(out["wind_dir_rel"].iloc[0])) - 180.0) < 1e-3  # +-180, not 0..360
    out2, _ = resample(df2, meta10(), "1h")
    assert out2["wind_dir_rel"].iloc[0] == pytest.approx(-10.0, abs=1e-4) and "wind_dir" not in out2.columns  # 0 and 180 degrees cancel: no mean direction, the column is empty and left out


def test_extension_columns_are_dropped_and_listed():
    out, m = resample(frame(12, x_note=1.0), meta10(), "1h")
    assert "x_note" not in out.columns and "extension columns dropped: x_note" in m.conversions[-1]
