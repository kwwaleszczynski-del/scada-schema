"""Parity of the converters (see ``scada_schema.parity``): the same input through the library and by hand into wind-up, OpenOA and
pvlib gives the same result in the target tool.

The synthetic tests use Greenbyte and PVDAQ files in the original formats built in ``conftest``; the ``data`` tests use the
open datasets (set ``SCADA_SCHEMA_KELMARSH_DIR`` and ``SCADA_SCHEMA_PVDAQ_DIR``). The tools must be installed
(``pip install scada-schema[converters]``); without them the tests are skipped.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import greenbyte_csv

from scada_schema import DatasetMeta, Device, finalise
from scada_schema import parity as par
from scada_schema.convert import openoa as to_oa
from scada_schema.convert import pvlib as to_pv
from scada_schema.convert import windup as to_wu
from scada_schema.loaders import greenbyte, pvdaq

pytestmark = pytest.mark.parity
IDS = ["T01", "T02", "T03"]


def wind_meta(ids: list[str]) -> DatasetMeta:
    return DatasetMeta(name="parity-wind", profile="wind", interval="10min", devices=[
        Device(id=i, type="turbine", rated_kw=2050, rotor_diameter_m=92, hub_height_m=80, lat=52.4, lon=-0.9) for i in ids])


@pytest.fixture(scope="module")
def synthetic():
    texts = {i: greenbyte_csv(k, n=6 * 24 * 40, start="2020-01-01") for k, i in enumerate(IDS, 1)}
    lib = finalise(pd.concat([greenbyte.parse_turbine_csv(t, i) for i, t in texts.items()], ignore_index=True))
    return lib, texts


def test_windup_parity_synthetic(synthetic):
    pytest.importorskip("wind_up")
    lib, texts = synthetic
    r = par.windup_parity(lib, wind_meta(IDS), texts)
    assert r["same_rows"] and r["rows"] == 3 * 6 * 24 * 40
    assert r["input_max_abs_diff"] < 1e-3  # the table holds float32
    assert r["filtered_same_missing"] and r["filtered_max_abs_diff"] < 1e-2  # the same rows removed by wind-up's filters
    assert 0.0 < r["removed_share"] < 0.7  # the filters did remove something
    assert r["power_curve_max_abs_diff_kw"] < 1e-2 and r["rated_ws_diff"] < 1e-4


def test_openoa_parity_synthetic(synthetic):
    pytest.importorskip("openoa")
    lib, texts = synthetic
    r = par.openoa_parity(lib, wind_meta(IDS), texts)
    assert r["same_turbines"] and r["scada_max_abs_diff"] < 1e-3 and r["iec_curve_max_abs_diff_kw"] < 1e-2
    assert r["aligned_bins_vs_res_asset_kpi_max_abs_diff_kw"] < 1e-3  # same bins, same curve
    assert r["default_bins_vs_res_asset_kpi_max_pct_of_rated"] > 1.0  # OpenOA's default bins are offset by a quarter m/s: a different curve
    plant = to_oa.to_openoa(lib, wind_meta(IDS))
    np.testing.assert_allclose(plant.scada["WTUR_SupWh"], plant.scada["WTUR_W"] / 6.0, rtol=1e-6, atol=1e-6)


def test_pvlib_parity_synthetic(pvdaq_dir):
    pytest.importorskip("pvlib")
    load = pvdaq.load(pvdaq_dir, system=34, years=[2018])
    raw = pd.concat([pd.read_csv(f) for f in sorted((pvdaq_dir / "system_34").glob("*.csv"))], ignore_index=True)
    r = par.pvlib_parity(load.table, load.meta, raw)
    assert r["intervals"] == len(raw) and r["max_rel_diff"] < 1e-6 and r["max_abs_diff_w"] < 1e-2 and r["max_modelled_ac_kw"] > 50


def test_pvlib_inputs_carry_measured_power_and_geometry(pvdaq_dir):
    pytest.importorskip("pvlib")
    load = pvdaq.load(pvdaq_dir, system=34, years=[2018])
    inp = to_pv.to_pvlib(load.table, load.meta, at="start")
    assert inp.system == {"surface_tilt": 11.0, "surface_azimuth": 180.0, "pdc0": 146_600.0}
    assert inp.location["latitude"] == pytest.approx(36.1952) and inp.location["tz"] == "UTC"
    assert {"poa_global", "temp_air", "wind_speed", "temp_module"} <= set(inp.weather.columns)
    assert inp.weather.index.equals(inp.p_ac_kw.index) and inp.p_ac_kw.max() > 100
    centre = to_pv.to_pvlib(load.table, load.meta, at="center")
    assert (centre.weather.index - inp.weather.index == pd.Timedelta("7.5min")).all()
    with pytest.raises(ValueError, match="name one"):
        to_pv.to_pvlib(load.table, DatasetMeta(**{**load.meta.model_dump(), "devices": [*load.meta.devices, Device(id="INV2", type="inverter")]}))
    with pytest.raises(ValueError, match="at must be"):
        to_pv.to_pvlib(load.table, load.meta, at="middle")


def test_windup_needs_generator_speed_not_rotor_speed(synthetic):
    lib, _ = synthetic
    with pytest.raises(ValueError, match="gen_speed"):
        to_wu.to_windup_scada(lib.drop(columns="gen_speed"), wind_meta(IDS))


def test_windup_shutdown_from_categories_and_column(synthetic):
    lib, _ = synthetic
    cat = lib.assign(iec_category="full_performance")
    cat.loc[cat.index[:5], "iec_category"] = "forced_outage"
    scada, notes = to_wu.to_windup_scada(cat, wind_meta(IDS))
    assert scada["ShutdownDuration"].sum() == 5 * 600.0 and any("derived from iec_category" in n for n in notes)
    scada, notes = to_wu.to_windup_scada(lib.assign(shutdown_s=np.float64(120.0)), wind_meta(IDS))
    assert (scada["ShutdownDuration"] == 120.0).all() and not notes
    scada, notes = to_wu.to_windup_scada(lib, wind_meta(IDS))
    assert (scada["ShutdownDuration"] == 0.0).all() and any("ShutdownDuration is 0" in n for n in notes)


def test_windup_turbine_types_estimates_ranges(synthetic):
    lib, _ = synthetic
    t = to_wu.windup_turbine_types(lib, wind_meta(IDS), "MM92")
    assert t["rated_power_kw"] == 2050 and t["rotor_diameter_m"] == 92
    lo, hi = t["normal_operation_genrpm_range"]
    assert lo < hi <= 1750 and t["normal_operation_pitch_range"][0] <= 0
    bare = DatasetMeta(name="p", profile="wind", interval="10min", devices=[Device(id=i, type="turbine") for i in IDS])
    with pytest.raises(ValueError, match="rated_kw and rotor_diameter_m"):
        to_wu.windup_turbine_types(lib, bare, "X")


def test_openoa_converter_refuses_what_the_schema_cannot_fill(synthetic):
    lib, _ = synthetic
    no_pos = DatasetMeta(name="p", profile="wind", interval="10min", devices=[Device(id=i, type="turbine", rated_kw=2050) for i in IDS])
    with pytest.raises(ValueError, match="latitude, longitude"):
        to_oa.to_openoa_frames(lib, no_pos)
    with pytest.raises(ValueError, match="needs p_ac"):
        to_oa.to_openoa_frames(lib.drop(columns="p_ac"), wind_meta(IDS))


# ----------------------------------------------------------------------------------------------- real data

def real_dir(name: str) -> Path:
    p = os.environ.get(f"SCADA_SCHEMA_{name.upper()}_DIR")
    if not p or not Path(p).exists():
        pytest.skip(f"set SCADA_SCHEMA_{name.upper()}_DIR to the folder with the raw {name} files")
    return Path(p)


def kelmarsh_texts(d: Path, ids: list[str]) -> dict[str, str]:
    z = next(d.glob("Kelmarsh_SCADA_2019_*.zip"))
    out = {}
    with zipfile.ZipFile(z) as zf:
        for i in ids:
            k = int(i[1:])
            name = next(n for n in zf.namelist() if n.startswith(f"Turbine_Data_Kelmarsh_{k}_"))
            out[i] = zf.read(name).decode("utf-8-sig")
    return out


@pytest.mark.data
def test_windup_and_openoa_parity_on_real_kelmarsh():
    pytest.importorskip("wind_up")
    pytest.importorskip("openoa")
    from scada_schema.loaders import kelmarsh

    d = real_dir("kelmarsh")
    lib = kelmarsh.load(d, years=[2019], devices=IDS)
    texts = kelmarsh_texts(d, IDS)
    w = par.windup_parity(lib.table, lib.meta, texts)
    assert w["same_rows"] and w["input_max_abs_diff"] < 1e-2 and w["filtered_same_missing"] and w["filtered_max_abs_diff"] < 1e-1
    assert w["power_curve_max_abs_diff_kw"] < 1e-2 and 0.0 < w["removed_share"] < 0.9
    o = par.openoa_parity(lib.table, lib.meta, texts)
    assert o["same_turbines"] and o["scada_max_abs_diff"] < 1e-2 and o["iec_curve_max_abs_diff_kw"] < 1e-1
    assert o["aligned_bins_vs_res_asset_kpi_max_abs_diff_kw"] < 1e-3


@pytest.mark.data
def test_pvlib_parity_on_real_pvdaq_34():
    pytest.importorskip("pvlib")
    d = real_dir("pvdaq")
    load = pvdaq.load(d, system=34, years=[2017])
    files = [f for f in sorted((d / "system_34").glob("system_34__date_2017_*.csv")) if f.stat().st_size > 0]
    raw = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    r = par.pvlib_parity(load.table, load.meta, raw)
    assert r["daylight_intervals"] > 10_000 and r["max_rel_diff"] < 1e-5
