"""Parity checks of the converters: the same input through this library and by hand into wind-up, OpenOA and pvlib.

Each function builds the target tool's input **twice from the same source file**: once through the library (loader, schema,
converter) and once by hand, with plain pandas and the target tool's own column names and units, written independently of the
library's mapping tables. The target tool's own functions then run on both and the outputs are compared. The functions return
the numbers; the tests set the tolerances and the study (``scada-schema study``) records the numbers.

* [`windup_parity`][scada_schema.parity.windup_parity]: wind-up's ``filter_scada_df`` and its power curve function;
* [`openoa_parity`][scada_schema.parity.openoa_parity]: OpenOA's ``PlantData`` and ``power_curve.IEC``, and the agreement of
  that curve with the bin method of ``res_asset_kpi``;
* [`pvlib_parity`][scada_schema.parity.pvlib_parity]: pvlib's ``ModelChain`` (PVWatts) against the same formulas called directly.

The tools are imported inside the functions: install ``scada-schema[converters]``.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from scada_schema.model import DatasetMeta

__all__ = ["direct_greenbyte", "direct_windup_frame", "openoa_parity", "pvlib_parity", "windup_parity"]

PITCH = [f"Blade angle (pitch position) {b} (°)" for b in "ABC"]


def direct_greenbyte(text: str) -> pd.DataFrame:
    """A Greenbyte export read with plain pandas and its own column names; adds ``time`` (UTC) and ``pitch`` (mean of the blades)."""
    lines = text.splitlines()
    skip = next(i for i, line in enumerate(lines) if line.startswith("# Date and time"))
    raw = pd.read_csv(io.StringIO(text), skiprows=skip, na_values=["NaN"])
    raw["time"] = pd.to_datetime(raw["# Date and time"]).dt.tz_localize("UTC")
    raw["pitch"] = raw[PITCH].mean(axis=1)
    return raw


def direct_windup_frame(direct: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The wind-up SCADA frame built by hand from direct Greenbyte readings (turbine name to frame)."""
    out = []
    for name, raw in direct.items():
        out.append(pd.DataFrame({
            "TimeStamp_StartFormat": raw["time"], "TurbineName": name, "ActivePowerMean": raw["Power (kW)"],
            "WindSpeedMean": raw["Wind speed (m/s)"], "WindSpeedSD": raw["Wind speed, Standard deviation (m/s)"],
            "YawAngleMean": raw["Nacelle position (°)"], "PitchAngleMean": raw["pitch"], "GenRpmMean": raw["Generator RPM (RPM)"],
            "AmbientTemp": raw["Nacelle ambient temperature (°C)"], "ShutdownDuration": 0.0}))
    return pd.concat(out).set_index("TimeStamp_StartFormat")


def _max_abs_diff(a: pd.DataFrame, b: pd.DataFrame, cols: list[str]) -> float:
    d = (a[cols].to_numpy(dtype="float64") - b[cols].to_numpy(dtype="float64"))
    return float(np.nanmax(np.abs(d))) if d.size else 0.0


def windup_parity(table: pd.DataFrame, meta: DatasetMeta, texts: dict[str, str], turbine_type: str = "MM92") -> dict:
    """wind-up on the library's input and on a hand-built input from the same Greenbyte exports.

    Parameters
    ----------
    table, meta
        Canonical table (and metadata) loaded from the same exports.
    texts : dict
        Turbine id to the text of its Greenbyte ``Turbine_Data`` export.
    """
    from wind_up.models import PlotConfig
    from wind_up.scada_funcs import filter_scada_df
    from wind_up.scada_power_curve import calc_pc_and_rated_ws_one_ttype

    from scada_schema.convert import windup as wu

    ids = list(texts)
    scada, _ = wu.to_windup_scada(table, meta, turbines=ids)
    hand = direct_windup_frame({i: direct_greenbyte(t) for i, t in texts.items()})
    common = [c for c in hand.columns if c in scada.columns]
    scada, hand = scada[common].sort_values("TurbineName", kind="stable"), hand[common].sort_values("TurbineName", kind="stable")
    value_cols = [c for c in common if c != "TurbineName"]
    same_index = bool((scada.index == hand.index).all() and (scada["TurbineName"].to_numpy() == hand["TurbineName"].to_numpy()).all())
    out = {"turbines": len(ids), "rows": int(len(scada)), "columns": value_cols, "same_rows": same_index,
           "input_max_abs_diff": _max_abs_diff(scada, hand, value_cols)}
    with tempfile.TemporaryDirectory() as tmp:
        cfg = wu.windup_config(table[table["device_id"].isin(ids)], meta, turbine_type, Path(tmp) / "out",
                               test_turbines=ids[:1], ref_turbines=ids[1:])
        plot = PlotConfig(show_plots=False, save_plots=False, skip_per_turbine_plots=True, plots_dir=Path(tmp) / "plots")
        got = filter_scada_df(wu.to_multiindex(scada), cfg, plot)
        want = filter_scada_df(wu.to_multiindex(hand), cfg, plot)
    out["filtered_same_missing"] = bool((got.isna() == want.isna()).all().all())
    out["filtered_max_abs_diff"] = _max_abs_diff(got, want, [c for c in got.columns if c in want.columns and got[c].dtype.kind == "f"])
    out["removed_share"] = float(got["ActivePowerMean"].isna().mean() - scada["ActivePowerMean"].isna().mean())
    # wind-up computes its power curve from filtered data (the curve must rise monotonically)
    pc_a, rated_a = calc_pc_and_rated_ws_one_ttype(got.dropna(subset=["WindSpeedMean", "ActivePowerMean"]), "WindSpeedMean", "ActivePowerMean", 0.5)
    pc_b, rated_b = calc_pc_and_rated_ws_one_ttype(want.dropna(subset=["WindSpeedMean", "ActivePowerMean"]), "WindSpeedMean", "ActivePowerMean", 0.5)
    out["power_curve_max_abs_diff_kw"] = float(np.nanmax(np.abs(pc_a["ActivePowerMean"].to_numpy() - pc_b["ActivePowerMean"].to_numpy())))
    out["rated_ws_diff"] = float(abs(rated_a - rated_b))
    return out


def openoa_parity(table: pd.DataFrame, meta: DatasetMeta, texts: dict[str, str]) -> dict:
    """OpenOA's ``PlantData`` from the library and from hand-built frames, their IEC power curves, and the agreement of that curve with the bin method of ``res_asset_kpi``."""
    import openoa.utils.power_curve as oa_pc
    from openoa import PlantData
    from res_asset_kpi.wind.power_curve import bin_power_curve

    from scada_schema.convert import openoa as to_oa

    ids = list(texts)
    plant = to_oa.to_openoa(table, meta, turbines=ids)
    direct = {i: direct_greenbyte(t) for i, t in texts.items()}
    scada = pd.concat([pd.DataFrame({
        "time": r["time"].dt.tz_localize(None), "asset_id": i, "WTUR_W": r["Power (kW)"], "WMET_HorWdSpd": r["Wind speed (m/s)"],
        "WMET_HorWdDir": r["Wind direction (°)"], "WMET_HorWdDirRel": r["Vane position 1+2 (°)"], "WROT_BlPthAngVal": r["pitch"],
        "WMET_EnvTmp": r["Nacelle ambient temperature (°C)"]}) for i, r in direct.items()], ignore_index=True)
    devs = {d.id: d for d in meta.devices}
    asset = pd.DataFrame({"asset_id": ids, "latitude": [devs[i].lat for i in ids], "longitude": [devs[i].lon for i in ids],
                          "rated_power": [devs[i].rated_kw for i in ids], "hub_height": [devs[i].hub_height_m for i in ids],
                          "rotor_diameter": [devs[i].rotor_diameter_m for i in ids], "elevation": np.nan, "type": "turbine"})
    hand = PlantData(analysis_type=None, metadata={"latitude": float(asset["latitude"].mean()), "longitude": float(asset["longitude"].mean()),
                                                  "capacity": float(asset["rated_power"].sum() / 1000), "scada": {"frequency": "10min"}, "asset": {}},
                     scada=scada, asset=asset)
    cols = ["WTUR_W", "WMET_HorWdSpd", "WMET_HorWdDir", "WMET_HorWdDirRel", "WROT_BlPthAngVal", "WMET_EnvTmp"]
    a, b = plant.scada[cols].sort_index(), hand.scada[cols].sort_index()
    grid = np.arange(4.0, 12.01, 0.5)
    curve_a = oa_pc.IEC("WMET_HorWdSpd", "WTUR_W", data=plant.scada.reset_index())(grid)
    curve_b = oa_pc.IEC("WMET_HorWdSpd", "WTUR_W", data=hand.scada.reset_index())(grid)
    rated = float(np.mean([devs[i].rated_kw for i in ids]))
    kpi = bin_power_curve(plant.scada["WMET_HorWdSpd"].to_numpy(), plant.scada["WTUR_W"].to_numpy())
    centres = kpi.index.to_numpy()
    window = (centres >= 4.0) & (centres <= 12.0)
    # OpenOA's default bins start at 0 (centres 0.25, 0.75, ...); the bins of IEC 61400-12-1 and res_asset_kpi are centred on
    # multiples of 0.5. With windspeed_start = -0.25 the bins coincide and the two implementations give the same curve.
    aligned = oa_pc.IEC("WMET_HorWdSpd", "WTUR_W", bin_width=0.5, windspeed_start=-0.25, windspeed_end=30.25, data=plant.scada.reset_index())
    default_on_grid = np.interp(centres[window], grid, curve_a)
    return {
        "turbines": len(ids), "rows": int(len(a)), "same_turbines": list(plant.turbine_ids) == list(hand.turbine_ids),
        "scada_max_abs_diff": float(np.nanmax(np.abs(a.to_numpy() - b.to_numpy()))),
        "iec_curve_max_abs_diff_kw": float(np.max(np.abs(curve_a - curve_b))),
        "aligned_bins_vs_res_asset_kpi_max_abs_diff_kw": float(np.max(np.abs(aligned(centres[window]) - kpi["power_kw"].to_numpy()[window]))),
        "default_bins_vs_res_asset_kpi_max_pct_of_rated": float(100 * np.max(np.abs(default_on_grid - kpi["power_kw"].to_numpy()[window])) / rated),
    }


def pvlib_parity(table: pd.DataFrame, meta: DatasetMeta, raw: pd.DataFrame, inverter: str = "INV", columns: dict | None = None) -> dict:
    """pvlib's PVWatts ``ModelChain`` on the converted weather against the same formulas on hand-converted raw PVDAQ columns.

    ``raw`` is the concatenated daily PVDAQ files of the system; the conversions (Fahrenheit, mph, the end-of-interval stamp
    to the interval centre) are repeated here independently of the loader.
    """
    import pvlib

    from scada_schema.convert import pvlib as to_pv

    c = {"poa": "poa_irradiance", "t_air": "ambient_temp_f", "wind": "wind_speed_mph", **(columns or {})}
    col = lambda prefix: next(x for x in raw.columns if x.rsplit("__", 1)[0] == prefix)  # noqa: E731
    inp = to_pv.to_pvlib(table, meta, inverter=inverter)
    loc = pvlib.location.Location(**inp.location)
    params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
    pdc0 = inp.system["pdc0"]
    arr = pvlib.pvsystem.Array(pvlib.pvsystem.FixedMount(inp.system["surface_tilt"], inp.system["surface_azimuth"]),
                               module_parameters={"pdc0": pdc0, "gamma_pdc": -0.004}, temperature_model_parameters=params)
    system = pvlib.pvsystem.PVSystem(arrays=[arr], inverter_parameters={"pdc0": pdc0})
    mc = pvlib.modelchain.ModelChain.with_pvwatts(system, loc, aoi_model="no_loss", spectral_model="no_loss", losses_model="no_loss")
    # the measured plane-of-array irradiance is taken as the effective irradiance (no optical or spectral loss on top)
    weather = inp.weather.assign(effective_irradiance=inp.weather["poa_global"])
    mc.run_model_from_effective_irradiance(weather.drop(columns="module_temperature", errors="ignore"))  # cell temperature from the ambient air
    ac = mc.results.ac
    cell_ambient = mc.results.cell_temperature
    stamp = pd.to_datetime(raw["measured_on"]).dt.round("15min").dt.tz_localize("Etc/GMT+8").dt.tz_convert("UTC")
    centre = pd.DatetimeIndex(stamp - pd.Timedelta("7.5min"))
    poa = pd.to_numeric(raw[col(c["poa"])], errors="coerce").to_numpy()
    temp_air = (pd.to_numeric(raw[col(c["t_air"])], errors="coerce").to_numpy() - 32.0) * 5.0 / 9.0
    wind = pd.to_numeric(raw[col(c["wind"])], errors="coerce").to_numpy() * 0.44704
    cell = pvlib.temperature.sapm_cell(poa, temp_air, wind, **params)
    manual = pd.Series(pvlib.inverter.pvwatts(pvlib.pvsystem.pvwatts_dc(poa, cell, pdc0, -0.004), pdc0), index=centre)
    manual = manual[~manual.index.duplicated()]
    both = pd.concat([ac.rename("lib"), manual.rename("manual")], axis=1).dropna()
    daylight = both[both["manual"] > 0.01 * pdc0]
    rel = (daylight["lib"] - daylight["manual"]).abs() / daylight["manual"]
    # the measured module temperature is a different input of the ModelChain: with it the cell temperature must follow SAPM's module-to-cell step
    mc.run_model_from_effective_irradiance(weather)
    t_mod = (pd.to_numeric(raw[col("module_temp_f")], errors="coerce").to_numpy() - 32.0) * 5.0 / 9.0
    manual_cell = pd.Series(pvlib.temperature.sapm_cell_from_module(t_mod, poa, params["deltaT"]), index=centre)
    manual_cell = manual_cell[~manual_cell.index.duplicated()]
    cells = pd.concat([mc.results.cell_temperature.rename("lib"), manual_cell.rename("manual"), cell_ambient.rename("ambient")], axis=1).dropna()
    res = {"module_temperature_vs_manual_max_abs_diff_k": float((cells["lib"] - cells["manual"]).abs().max()),
           # the median over the daylight intervals: single sensor faults (a stuck ambient temperature) would dominate a maximum
           "module_temperature_changes_cell_temperature_k": float((cells["lib"] - cells["ambient"]).abs()[inp.weather["poa_global"].reindex(cells.index) > 200].median())}
    return {**res, **{"intervals": int(len(both)), "daylight_intervals": int(len(daylight)), "pdc0_kw": float(pdc0 / 1000),
            "max_abs_diff_w": float((both["lib"] - both["manual"]).abs().max()), "max_rel_diff": float(rel.max()),
            "max_modelled_ac_kw": float(both["lib"].max() / 1000)}}
