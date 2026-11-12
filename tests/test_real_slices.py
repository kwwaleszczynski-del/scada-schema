"""Loaders on small slices of the REAL open files (tests/data/real, see its README), so that the formats the loaders were written for are tested in CI
without a download: the real column names and units, the real quirks (Greenbyte headers, Hill of Towie table layout, the PVDAQ unit switch, the La Haute
Borne clock). The expected values are pinned from the raw files (they were read from the CSV text, not produced by the loaders)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scada_schema import validate
from scada_schema.loaders import hill_of_towie, kelmarsh, la_haute_borne, pvdaq

REAL = Path(__file__).parent / "data" / "real"


def test_kelmarsh_slice():
    L = kelmarsh.load(REAL, years=[2019])
    assert validate(L.table, L.meta).ok and L.table.shape[0] == 48 and set(L.table.device_id) == {"T01", "T02"}
    first = L.table[L.table.device_id == "T01"].iloc[0]
    assert first["timestamp"] == pd.Timestamp("2019-01-07 22:40", tz="UTC")
    # pinned from the raw file: "Power (kW)" 1855.855..., "Energy Export counter (kWh)" 15023855, "Nacelle position (°)" 292.4578...
    assert first["p_ac"] == pytest.approx(1855.8553, rel=1e-6) and first["e_export_counter"] == 15023855.0 and first["nacelle_dir"] == pytest.approx(292.45782, rel=1e-6)
    with zipfile.ZipFile(next(REAL.glob("Kelmarsh_SCADA_2019_*.zip"))) as z:
        name = next(n for n in z.namelist() if n.startswith("Turbine_Data_Kelmarsh_1_"))
        raw = pd.read_csv(io.StringIO(z.read(name).decode("utf-8-sig")), skiprows=9, na_values=["NaN"])
    np.testing.assert_allclose(L.table[L.table.device_id == "T01"]["p_ac"].to_numpy(dtype=float), raw["Power (kW)"].to_numpy(), rtol=1e-6)
    np.testing.assert_allclose(L.table[L.table.device_id == "T01"]["wind_speed__std"].to_numpy(dtype=float), raw["Wind speed, Standard deviation (m/s)"].to_numpy(), rtol=1e-6)
    assert L.meta.device("T01").rated_kw == 2050 and L.meta.source.license == "CC-BY-4.0"
    ev = L.events
    assert len(ev) == 80 and ev.iloc[0]["status"] == "Stop" and ev.iloc[0]["code"] == "710" and ev.iloc[0]["category"] == "technical_standby"
    assert ev["end"].isna().any()  # events still open at export time


def test_hill_of_towie_slice():
    L = hill_of_towie.load(REAL, years=[2021])
    assert validate(L.table, L.meta).ok and L.table.shape[0] == 24 and list(L.table.device_id.unique()) == ["T01", "T02"]
    assert L.table["timestamp"].min() == pd.Timestamp("2021-06-01 16:30", tz="UTC")  # the table stamps the END of the period: 16:40 in the file
    first = L.table[L.table.device_id == "T01"].iloc[0]
    assert first["p_ac"] == pytest.approx(506.657, abs=1e-3) and first["wind_speed"] == pytest.approx(7.025, abs=1e-3) and first["shutdown_s"] == 0.0
    assert first["t_amb"] == pytest.approx(18.089, abs=1e-3) and first["pitch"] == pytest.approx(-0.951, abs=1e-3)
    assert "wind_dir" not in L.table.columns and len(L.events) == 25 and set(L.events["status"]) <= {"Stop", "Informational", "Unknown"}
    assert any("end of the interval" in c or "end to the start" in c for c in L.meta.conversions) and any("setpoint: the value at the end of the interval" in c for c in L.meta.conversions)


def test_pvdaq_slice_across_the_hectowatt_to_watt_switch():
    L = pvdaq.load(REAL / "pvdaq", system=34)
    assert validate(L.table, L.meta).ok and L.meta.interval == "15min" and L.table.shape[0] == 3 * 96 * 2
    note = L.meta.conversions[0]
    assert "hectowatts to kW (x0.1) before 2018-08-04 19:45 UTC" in note  # found from three real days: the level against the 146.6 kWp rating and the irradiance ratio
    inv = L.table[L.table.device_id == "INV"].set_index("timestamp")
    assert inv["p_ac"].max() < 146.6 * 1.3  # never above the array rating: 100 times that would show a wrong unit
    day3, day5 = inv.loc["2018-08-03", "p_ac"].max(), inv.loc["2018-08-05", "p_ac"].max()
    assert 20 < day3 < 146.6 and 20 < day5 < 146.6  # a day in hectowatts and a day in watts, both plausible kW
    met = L.table[L.table.device_id == "MET"]
    assert met["poa"].max() > 900 and 0 < met["t_module"].max() < 90  # Fahrenheit converted: a module at 60 degC, not 140


def test_la_haute_borne_slice_has_the_clock_of_the_real_file():
    raw = pd.read_csv(io.BytesIO(zipfile.ZipFile(REAL / "la_haute_borne.zip").read(la_haute_borne.SCADA_FILE)))
    assert raw.duplicated(["Wind_turbine_name", "Date_time"]).sum() == 12  # the real file repeats 03:00-03:50 at the spring change
    L = la_haute_borne.load(REAL)
    assert validate(L.table, L.meta).ok and L.removed == {"duplicate_rows": 0, "ambiguous_time_rows": 24}
    t = L.table[L.table.device_id == "T01"]["timestamp"]
    assert t.min() == pd.Timestamp("2014-03-29 11:00", tz="UTC") and not t.duplicated().any()
    steps = t.diff().dropna()
    assert list(steps[steps != pd.Timedelta("10min")]) == [pd.Timedelta("2h10min")]  # the two ambiguous hours, nothing else
    as_stamped = la_haute_borne.load(REAL, time="as_stamped")
    assert as_stamped.removed["duplicate_rows"] == 12 and len(as_stamped.table) == len(raw) - 12
