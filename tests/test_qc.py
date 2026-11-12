from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scada_schema import DatasetMeta, Device, finalise, qc


def codes(rep, check=None):
    return {(f.check, f.device, f.signal) for f in rep.findings if check is None or f.check == check}


def test_clean_synthetic_table_has_no_errors(wind):
    df, meta = wind
    rep = qc.run(df, meta)
    assert rep.counts()["error"] == 0, rep.to_frame()


def test_duplicates_and_ordering(wind):
    df, meta = wind
    bad = pd.concat([df, df.iloc[:5]], ignore_index=True)
    rep = qc.run(bad, meta, checks=["duplicates", "ordering"])
    assert [(f.check, f.n) for f in rep.findings if f.check == "duplicates"] == [("duplicates", 5)]
    shuffled = df.iloc[::-1].reset_index(drop=True)
    assert any(f.check == "ordering" for f in qc.run(shuffled, meta, checks=["ordering"]).findings)


def test_gaps_and_time_jumps(wind):
    df, meta = wind
    gap = df.drop(index=range(100, 130)).reset_index(drop=True)  # 30 intervals of T01
    rep = qc.run(gap, meta, checks=["gaps"])
    f = [x for x in rep.findings if x.device == "T01"][0]
    assert f.n == 30 and f.value == pytest.approx(5.0)
    skew = df.copy()
    skew.loc[50, "timestamp"] += pd.Timedelta("3min")
    assert any(x.check == "time_jumps" for x in qc.run(skew, meta, checks=["time_jumps"]).findings)


def test_flatline_ignores_zero_power_but_flags_sensor(wind):
    df, meta = wind
    df = df.copy()
    idx = df.index[(df.device_id == "T01")][100:160]  # 10 h
    df.loc[idx, "wind_speed"] = 7.0
    df.loc[idx, "p_ac"] = 0.0
    rep = qc.run(df, meta, checks=["flatline"])
    got = codes(rep)
    assert ("flatline", "T01", "wind_speed") in got
    assert ("flatline", "T01", "p_ac") not in got
    f = next(x for x in rep.findings if x.signal == "wind_speed")
    assert f.n == 60 and f.value == pytest.approx(10.0)


def test_out_of_range_and_unit_error_in_kw(wind):
    df, meta = wind
    df = df.copy()
    df.loc[10, "wind_speed"] = 300.0
    rep = qc.run(df, meta, checks=["out_of_range"])
    assert ("out_of_range", "T01", "wind_speed") in codes(rep)
    big = df.assign(p_ac=df["p_ac"] * 1000)  # W instead of kW
    rep = qc.run(big, meta, checks=["units"])
    f = next(x for x in rep.findings if x.check == "units" and x.signal == "p_ac")
    assert f.severity == "error" and "too large" in f.message


def test_spike_in_temperature(wind):
    df, meta = wind
    df = df.copy()
    df.loc[40, "t_amb"] = 45.0
    rep = qc.run(df, meta, checks=["spikes"])
    assert ("spikes", "T01", "t_amb") in codes(rep)


def test_north_offset_between_turbines():
    rng = np.random.default_rng(3)
    n = 3000
    t = pd.date_range("2021-01-01", periods=n, freq="10min", tz="UTC")
    base = (200 + 40 * np.sin(np.arange(n) / 200)) % 360
    parts = []
    for k, off in enumerate([0.0, 1.0, -1.0, 25.0]):
        parts.append(pd.DataFrame({"timestamp": t, "device_id": f"T{k + 1}", "p_ac": 1500.0, "wind_speed": 9.0,
                                   "nacelle_dir": (base + off + rng.normal(0, 2, n)) % 360}))
    meta = DatasetMeta(name="n", profile="wind", interval="10min",
                       devices=[Device(id=f"T{k + 1}", type="turbine", rated_kw=2000) for k in range(4)])
    rep = qc.run(finalise(pd.concat(parts, ignore_index=True)), meta, checks=["wind_direction"])
    off = {f.device: f.value for f in rep.findings if f.check == "wind_direction"}
    assert off["T4"] == pytest.approx(25.0 * 3 / 4, abs=1.0)  # the fleet mean absorbs a quarter of it
    assert {f.device for f in rep.findings if f.severity == "warning"} == {"T4"}


def test_wind_direction_consistency(wind):
    df, meta = wind
    df = df.assign(wind_dir_rel=(df["wind_dir"] - df["nacelle_dir"]).astype("float32"))
    assert not [f for f in qc.run(df, meta, checks=["wind_direction"]).findings if f.signal == "wind_dir"]
    df2 = df.assign(wind_dir=((df["wind_dir"] + 90) % 360).astype("float32"))
    rep = qc.run(df2, meta, checks=["wind_direction"])
    assert any(f.signal == "wind_dir" and f.severity == "error" for f in rep.findings)


def test_energy_counter_reset_and_consistency(wind):
    df, meta = wind
    h = 1 / 6
    df = df.copy()
    df["e_export"] = (df["p_ac"] * h).astype("float64")
    df["e_export_counter"] = df.groupby("device_id")["e_export"].cumsum() + 1000.0
    ok = qc.run(df, meta, checks=["energy_counter", "energy_power"])
    assert not [f for f in ok.findings if f.severity != "info"], ok.to_frame()
    bad = df.copy()
    i = bad.index[bad.device_id == "T01"][200]
    bad.loc[bad.index[(bad.device_id == "T01") & (bad.index >= i)], "e_export_counter"] -= 800.0
    rep = qc.run(bad, meta, checks=["energy_counter"])
    f = next(x for x in rep.findings if "below the one before" in x.message)
    assert f.n == 1 and f.device == "T01"


def test_icing_anemometer_and_power_loss():
    rng = np.random.default_rng(5)
    n = 6 * 24 * 40
    t = pd.date_range("2021-01-01", periods=n, freq="10min", tz="UTC")
    ws = np.clip(8 + 3 * np.sin(np.arange(n) / 60) + rng.normal(0, .5, n), 4, None)
    p = np.clip(2000 * ((ws - 3) / 9) ** 3, 0, 2000)
    temp = np.where(np.arange(n) < n // 2, 10.0, -2.0) + rng.normal(0, .2, n)
    ice = slice(n // 2 + 100, n // 2 + 160)  # 10 h
    p2, ws2, rpm = p.copy(), ws.copy(), np.clip(ws * 1.6, 0, 16)
    p2[ice] *= 0.1
    ws2[n // 2 + 300:n // 2 + 330] = 0.0
    df = pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": p2, "wind_speed": ws2, "t_amb": temp, "rotor_speed": rpm})
    meta = DatasetMeta(name="i", profile="wind", interval="10min", devices=[Device(id="T01", type="turbine", rated_kw=2000)])
    rep = qc.run(finalise(df), meta, checks=["icing"])
    msgs = " | ".join(f.message for f in rep.findings)
    assert "iced anemometer" in msgs and "warm-weather median" in msgs
    pl = next(f for f in rep.findings if f.signal == "p_ac")
    assert 40 <= pl.n <= 60
    # a logged stop explains low power: not icing
    df["shutdown_s"] = 0.0
    df.loc[df.index[ice], "shutdown_s"] = 600.0
    rep = qc.run(finalise(df), meta, checks=["icing"])
    assert not [f for f in rep.findings if f.signal == "p_ac"]


def test_power_curve_flags_overproduction():
    from conftest import wind_frame

    df, meta = wind_frame(n=6 * 24 * 20)
    df = df.copy()
    rows = df.index[df.device_id == "T01"][500:520]
    df.loc[rows, "p_ac"] = 2050.0
    df.loc[rows, "wind_speed"] = 4.0  # full power at 4 m/s: the wind speed sensor or the power is wrong
    rep = qc.run(df, meta, checks=["power_curve"])
    f = [x for x in rep.findings if x.device == "T01"]
    assert f and f[0].n >= 15


def test_dst_check_finds_local_time_stored_as_utc():
    local = pd.date_range("2021-03-20", "2021-11-10", freq="10min")  # naive local wall-clock time
    keep = ~((local >= "2021-03-28 02:00") & (local < "2021-03-28 03:00"))  # the skipped spring hour
    t = local[keep].tz_localize("UTC")
    df = pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": 1.0, "wind_speed": 5.0})
    meta = DatasetMeta(name="d", profile="wind", interval="10min", source_timezone="Europe/Warsaw",
                       devices=[Device(id="T01", type="turbine", rated_kw=2000)])
    assert any(f.check == "dst" for f in qc.run(finalise(df), meta, checks=["dst"]).findings)
    clean = pd.DataFrame({"timestamp": pd.date_range("2021-03-20", "2021-11-10", freq="10min", tz="UTC"), "device_id": "T01",
                          "p_ac": 1.0, "wind_speed": 5.0})
    assert not qc.run(finalise(clean), meta, checks=["dst"]).findings


def test_unit_switch_hectowatt_to_watt_in_pv():
    rng = np.random.default_rng(1)
    t = pd.date_range("2018-07-01", "2018-09-01", freq="15min", tz="UTC")
    h = t.hour + t.minute / 60
    poa = np.clip(900 * np.sin((h - 5) / 14 * np.pi), 0, None) * (1 + rng.normal(0, .01, len(t)))
    kw = 0.15 * poa
    raw = np.where(t < pd.Timestamp("2018-08-04", tz="UTC"), kw * 10, kw * 1000)  # hW before, W after
    as_hw = raw * 0.1  # a loader that reads every value as hectowatts
    inv = pd.DataFrame({"timestamp": t, "device_id": "INV", "p_ac": as_hw})
    met = pd.DataFrame({"timestamp": t, "device_id": "MET", "poa": poa})
    meta = DatasetMeta(name="u", profile="pv", interval="15min",
                       devices=[Device(id="INV", type="inverter", capacity_kwp=150), Device(id="MET", type="met")])
    rep = qc.run(finalise(pd.concat([inv, met], ignore_index=True)), meta, checks=["units"])
    f = [x for x in rep.findings if x.check == "units" and "switch" in x.message]
    assert f and f[0].first.strftime("%Y-%m-%d") in ("2018-08-03", "2018-08-04", "2018-08-05")
    assert f[0].value == pytest.approx(100, rel=0.2)


def test_report_outputs(wind, tmp_path):
    df, meta = wind
    df = df.copy()
    df.loc[10:80, "wind_speed"] = 6.0
    rep = qc.run(df, meta)
    html = rep.to_html(tmp_path / "r.html")
    assert "<table" in html and "flatline" in html and "Quality report" in html
    assert "<script" not in html and "src=" not in html and "href=" not in html
    js = rep.to_json(tmp_path / "r.json")
    assert '"findings"' in js
    flags = rep.flag_frame()
    assert "flag_wind_speed" in flags and flags["flag_wind_speed"].max() > 0
    assert set(rep.summary()["check"]) <= set(rep.checks_run)


def test_unit_switch_in_wind_kw_to_w():
    rng = np.random.default_rng(2)
    n = 6 * 24 * 60
    t = pd.date_range("2021-01-01", periods=n, freq="10min", tz="UTC")
    ws = np.clip(8.5 + 2.5 * rng.standard_normal(n), 0, None)
    p = np.clip(2050 * ((ws - 3) / 9) ** 3, 0, 2050)
    p = np.where(np.arange(n) >= n // 2, p * 1000, p)  # logger changes from kW to W half way
    meta = DatasetMeta(name="w", profile="wind", interval="10min", devices=[Device(id="T01", type="turbine")])
    df = pd.DataFrame({"timestamp": t, "device_id": "T01", "p_ac": p, "wind_speed": ws})
    rep = qc.run(finalise(df), meta, checks=["units"])
    f = [x for x in rep.findings if "switch" in x.message]
    assert f and f[0].value == pytest.approx(1000, rel=0.3) and f[0].first.strftime("%Y-%m") == "2021-01"
    assert f[0].first.day in (30, 31, 1) or f[0].first.month == 1


def test_power_checks_work_on_scaled_power():
    from conftest import wind_frame

    df, meta = wind_frame(n=6 * 24 * 20)
    df = df.rename(columns={"p_ac": "p_ac_rel"}).assign(p_ac_rel=lambda d: (d["p_ac_rel"] / 2050).astype("float32"))
    rows = df.index[df.device_id == "T01"][500:520]
    df.loc[rows, "p_ac_rel"] = 1.0
    df.loc[rows, "wind_speed"] = 4.0
    rep = qc.run(df, meta, checks=["power_curve", "flatline", "units"])
    f = [x for x in rep.findings if x.check == "power_curve" and x.device == "T01"]
    assert f and f[0].signal == "p_ac_rel" and f[0].n >= 15
    assert not [x for x in rep.findings if x.check == "units"]  # no kW magnitude judgement on a scaled number
