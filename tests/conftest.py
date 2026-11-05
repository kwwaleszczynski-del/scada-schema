"""Shared fixtures: small synthetic canonical tables for each profile."""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from scada_schema import DatasetMeta, Device, finalise


def wind_frame(n_turbines: int = 2, n: int = 6 * 24 * 3, seed: int = 0) -> tuple[pd.DataFrame, DatasetMeta]:
    rng = np.random.default_rng(seed)
    t = pd.date_range("2021-03-01", periods=n, freq="10min", tz="UTC")
    parts = []
    for k in range(n_turbines):
        ws = np.clip(8 + 3 * np.sin(np.arange(n) / 40) + rng.normal(0, 0.6, n), 0, None)
        p = np.clip(2050 * ((ws - 3) / 9) ** 3, 0, 2050)
        parts.append(pd.DataFrame({
            "timestamp": t, "device_id": f"T{k + 1:02d}", "p_ac": p, "wind_speed": ws,
            "wind_dir": (240 + rng.normal(0, 8, n)) % 360, "nacelle_dir": (240 + rng.normal(0, 4, n)) % 360,
            "rotor_speed": np.clip(ws * 1.6, 0, 16), "pitch": np.where(p >= 2050, 4.0, 0.0),
            "t_amb": 8 + rng.normal(0, 0.3, n).cumsum() * 0.05,
        }))
    meta = DatasetMeta(name="synthetic-wind", profile="wind", interval="10min", devices=[
        Device(id=f"T{k + 1:02d}", type="turbine", rated_kw=2050, hub_height_m=80, rotor_diameter_m=82) for k in range(n_turbines)])
    return finalise(pd.concat(parts, ignore_index=True), meta), meta


def pv_frame(n_days: int = 4, seed: int = 1) -> tuple[pd.DataFrame, DatasetMeta]:
    rng = np.random.default_rng(seed)
    t = pd.date_range("2021-06-01", periods=96 * n_days, freq="15min", tz="UTC")
    h = (t.hour + t.minute / 60).to_numpy()
    sun = np.clip(np.sin((h - 5) / 15 * np.pi), 0, None)
    poa = 950 * sun * (1 + rng.normal(0, 0.01, len(t)))
    inv = pd.DataFrame({"timestamp": t, "device_id": "INV1", "p_ac": 0.9 * 100 * poa / 1000, "p_dc": 0.93 * 100 * poa / 1000,
                        "t_inverter": 30 + 10 * sun})
    met = pd.DataFrame({"timestamp": t, "device_id": "MET", "poa": poa, "t_amb": 15 + 8 * sun, "t_module": 18 + 25 * sun,
                        "wind_speed": 3 + rng.random(len(t))})
    meta = DatasetMeta(name="synthetic-pv", profile="pv", interval="15min", devices=[
        Device(id="INV1", type="inverter", rated_kw=100, capacity_kwp=110), Device(id="MET", type="met")])
    return finalise(pd.concat([inv, met], ignore_index=True), meta), meta


@pytest.fixture
def wind():
    return wind_frame()


@pytest.fixture
def pv():
    return pv_frame()


# ----------------------------------------------------------------------------------------------- Greenbyte files

NL = chr(10)


def greenbyte_csv(turbine: int, n: int = 6 * 24, start: str = "2019-01-01") -> str:
    """A Greenbyte 10-minute export with the columns the loader reads (and some it must leave out)."""
    t = pd.date_range(start, periods=n, freq="10min")
    rng = np.random.default_rng(turbine)
    ws = np.clip(8 + 3 * np.sin(np.arange(n) / 300) + rng.normal(0, 1.5, n), 0.5, 30)
    p = np.clip(2050 * ((ws - 3) / 9) ** 3, 0, 2050)
    head = NL.join(["# This file was exported by Greenbyte.", "#", f"# Turbine: Kelmarsh {turbine}", "# Time zone: UTC", "#", "#", "#", "#", "#"]) + NL
    cols = ["# Date and time", "Wind speed (m/s)", "Wind speed, Standard deviation (m/s)", "Wind speed, Minimum (m/s)",
            "Wind direction (°)", "Nacelle position (°)", "Vane position 1+2 (°)", "Energy Export (kWh)", "Energy Export counter (kWh)",
            "Power (kW)", "Power, Maximum (kW)", "Power factor (cosphi)", "Power factor (cosphi), Max",
            "Lost Production to Downtime (kWh)", "Rotor speed (RPM)", "Nacelle ambient temperature (°C)",
            "Blade angle (pitch position) A (°)", "Blade angle (pitch position) B (°)", "Blade angle (pitch position) C (°)",
            "Generator RPM (RPM)"]
    yaw = (240 + rng.normal(0, 5, n)) % 360
    pitch = np.where(ws > 12, (ws - 12) * 3, -1.0)
    rows = pd.DataFrame({
        cols[0]: t.strftime("%Y-%m-%d %H:%M:%S"), cols[1]: ws, cols[2]: 0.5, cols[3]: ws - 1, cols[4]: yaw + 5, cols[5]: yaw,
        cols[6]: 5.0, cols[7]: p / 6, cols[8]: (p / 6).cumsum() + 100, cols[9]: p, cols[10]: p + 10, cols[11]: 1.0, cols[12]: 1.0,
        cols[13]: 3.0, cols[14]: np.clip(ws, 0, 17), cols[15]: 6.0, cols[16]: pitch, cols[17]: pitch + 1, cols[18]: pitch + 2,
        cols[19]: np.clip(ws * 100, 0, 1750)})
    rows.loc[3, cols[1]] = np.nan
    buf = io.StringIO()
    rows.to_csv(buf, index=False, na_rep="NaN")
    return head + buf.getvalue()


STATUS = """# Status export
#
#
#
#
#
#
#
#
Timestamp start,Timestamp end,Duration,Status,Code,Message,Comment,Service contract category,IEC category,Global contract category,Custom contract category
2019-01-01 00:20:00,2019-01-01 00:50:00,00:30:00,Stop,25,Manual stop - on site,,,Scheduled Maintenance,,
2019-01-01 01:00:00,2019-01-01 01:30:00,00:30:00,Stop,100.0,Safety chain open,,,Forced outage,,
2019-01-01 02:00:00,-,,Warning,9000,P output externally reduced,,,Requested Shutdown,,
2019-01-01 03:00:00,2019-01-01 03:10:00,00:10:00,Informational,1,Info,,,Full Performance,,
"""

STATIC = ("Title,Alternative Title,Identity,Manufacturer,Model,Rated power (kW),Hub Height (m),Rotor Diameter (m),Latitude,Longitude,"
          "Elevation (m),Country,Commercial Operations Date\n"
          "Kelmarsh 1,,1,Senvion,MM92,2050,68.5,92.5,52.40,-0.94,150,UK,2016-04-15\n"
          "Kelmarsh 2,,2,Senvion,MM92,2050,68.5,92.5,52.41,-0.93,150,UK,2016-04-15\n")


@pytest.fixture
def kelmarsh_dir(tmp_path):
    z = tmp_path / "Kelmarsh_SCADA_2019_3085.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for k in (1, 2):
            zf.writestr(f"Turbine_Data_Kelmarsh_{k}_2019-01-01_-_2020-01-01_22{k}.csv", "﻿" + greenbyte_csv(k))
            zf.writestr(f"Status_Kelmarsh_{k}_2019-01-01_-_2020-01-01_22{k}.csv", "﻿" + STATUS)
    (tmp_path / "Kelmarsh_WT_static.csv").write_text(STATIC, encoding="utf-8-sig")
    return tmp_path




# ----------------------------------------------------------------------------------------------- PVDAQ files

@pytest.fixture
def pvdaq_dir(tmp_path):
    """Two stretches of daily files around the hectowatt to watt switch (here 2018-08-04), UTC-8 stamps."""
    d = tmp_path / "system_34"
    d.mkdir()
    rng = np.random.default_rng(0)
    for day in pd.date_range("2018-07-20", "2018-08-20"):
        t = pd.date_range(day, periods=96, freq="15min")
        h = t.hour + t.minute / 60
        poa = np.clip(900 * np.sin((h - 5.5) / 14 * np.pi), 0, None) * (1 + rng.normal(0, .01, 96))
        kw = 0.15 * poa
        scale = 10.0 if day < pd.Timestamp("2018-08-04") else 1000.0  # hW or W per kW
        df = pd.DataFrame({
            "measured_on": t.strftime("%Y-%m-%d %H:%M:%S"), "ac_power_hw__2695": kw * scale, "dc_power_hw__2694": kw * scale * 1.03,
            "ac_current__2685": kw / 0.28, "ac_voltage__2684": 280.0, "dc_current__2683": kw / 0.3, "dc_voltage__2682": 300.0,
            "inverter_error_code__2690": 0.0, "inverter_temp__2686": 40.0, "module_temp_f__2689": 100.0 + poa / 20,
            "ambient_temp_f__2688": 86.0, "poa_irradiance__2679": poa, "power_factor_1000__2693": 990.0, "relative_humidity__2687": 20.0,
            "wind_direction__2692": 200.0, "wind_speed_mph__2691": 10.0, "system_id": 34})
        (d / f"system_34__date_{day:%Y_%m_%d}.csv").write_text(df.to_csv(index=False))
    return tmp_path


