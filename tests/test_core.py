from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from scada_schema import DatasetMeta, Device, SchemaError, read_meta, read_parquet, validate, write_parquet
from scada_schema.signals import SIGNALS, split_column, stat_column


def test_registry_is_consistent():
    for name, s in SIGNALS.items():
        assert s.name == name
        assert s.profiles and s.devices
        if s.lo is not None and s.hi is not None:
            assert s.lo < s.hi
    assert stat_column("p_ac", "std") == "p_ac__std"
    assert split_column("p_ac__std") == ("p_ac", "std")
    with pytest.raises(ValueError):
        stat_column("p_ac", "median")


def test_meta_rejects_bad_input():
    with pytest.raises(ValidationError):
        DatasetMeta(name="x", profile="wind", interval="7min")
    with pytest.raises(ValidationError):
        DatasetMeta(name="x", profile="wind", interval="10min", devices=[Device(id="A", type="inverter")])
    with pytest.raises(ValidationError):
        DatasetMeta(name="x", profile="wind", interval="10min", devices=[Device(id="A", type="turbine"), Device(id="A", type="turbine")])
    with pytest.raises(ValidationError):
        Device(id="A", type="turbine", rated_kw=-1)


def test_meta_json_roundtrip(wind):
    _, meta = wind
    assert DatasetMeta.from_json(meta.to_json()) == meta


def test_valid_tables_pass(wind, pv):
    for df, meta in (wind, pv):
        rep = validate(df, meta)
        assert rep.ok, rep.to_frame()


def test_parquet_roundtrip_keeps_data_and_meta(tmp_path, wind):
    df, meta = wind
    path = write_parquet(df, meta, tmp_path / "w.parquet")
    back, meta2 = read_parquet(path)
    assert meta2 == meta == read_meta(path)
    pd.testing.assert_frame_equal(back, df)


def test_write_refuses_invalid(tmp_path, wind):
    df, meta = wind
    bad = df.rename(columns={"p_ac": "power"})
    with pytest.raises(SchemaError, match="unknown_column"):
        write_parquet(bad, meta, tmp_path / "x.parquet")


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d.drop(columns="timestamp"), "missing_key_column"),
    (lambda d: d.assign(timestamp=d["timestamp"].dt.tz_localize(None)), "timestamp_not_utc"),
    (lambda d: pd.concat([d, d.iloc[:3]], ignore_index=True), "duplicate_key"),
    (lambda d: d.iloc[::-1].reset_index(drop=True), "not_sorted"),
    (lambda d: d.assign(timestamp=d["timestamp"] + pd.Timedelta("1min")), "off_grid"),
    (lambda d: d.assign(device_id="ZZ"), "unknown_device"),
    (lambda d: d.assign(p_ac=d["p_ac"].astype(str)), "bad_dtype"),
    (lambda d: d.assign(poa=1.0), "signal_not_allowed"),
    (lambda d: d.drop(columns="wind_speed"), "missing_required"),
    (lambda d: d.assign(iec_category="sunny"), "bad_category"),
    (lambda d: d.assign(x_ok=1.0, p_ac__bogus=1.0), "unknown_column"),
])
def test_validate_catches(wind, mutate, code):
    df, meta = wind
    rep = validate(mutate(df), meta)
    assert code in rep.codes(), rep.to_frame()
    assert not rep.ok


def test_extension_columns_and_stats_allowed(wind):
    df, meta = wind
    df = df.assign(x_note=1.0, p_ac__std=1.0, wind_speed__max=9.0)
    assert validate(df, meta).ok
    # a stat of a signal that has none is not allowed
    assert "unknown_column" in validate(df.assign(shutdown_s__std=1.0), meta).codes()


def test_pv_needs_power_or_irradiance(pv):
    df, meta = pv
    rep = validate(df.drop(columns=["poa", "p_ac", "p_dc"]), meta)
    assert "missing_required" in rep.codes()


def test_missing_rating_is_a_warning(wind):
    df, meta = wind
    meta2 = DatasetMeta(**{**meta.model_dump(), "devices": [Device(id=d.id, type="turbine") for d in meta.devices]})
    rep = validate(df, meta2)
    assert rep.ok and "missing_rating" in rep.codes()


def test_signals_must_belong_to_the_profile_of_the_meta(wind):
    df, meta = wind
    assert validate(df, meta).ok
    assert "signal_not_in_profile" in validate(df.assign(poa=1.0), meta).codes()  # irradiance is not a wind signal
    pv = DatasetMeta(name="p", profile="pv", interval="10min", devices=[Device(id=d.id, type="inverter") for d in meta.devices])
    rep = validate(df[["timestamp", "device_id", "rotor_speed", "p_ac"]], pv)
    assert "signal_not_in_profile" in rep.codes()  # rotor speed is not a PV signal
    # a met station of a PV plant carries wind speed
    assert "signal_not_in_profile" not in validate(df[["timestamp", "device_id", "wind_speed"]].assign(poa=1.0), DatasetMeta(
        name="p", profile="pv", interval="10min", devices=[Device(id=d.id, type="met") for d in meta.devices])).codes()


def test_a_turbine_may_carry_scaled_power_instead_of_kw(wind):
    df, meta = wind
    scaled = df.rename(columns={"p_ac": "p_ac_rel"}).assign(p_ac_rel=(df["p_ac"] / 2050).astype("float32"))
    assert validate(scaled, meta).ok
    assert "missing_required" in validate(scaled.drop(columns="p_ac_rel"), meta).codes()
