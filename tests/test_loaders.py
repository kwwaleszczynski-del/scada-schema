"""Loaders on small files in the original formats, built here so the tests need no download."""

from __future__ import annotations

import hashlib
import re
import zipfile

import numpy as np
import pandas as pd
import pytest
from conftest import greenbyte_csv

from scada_schema import validate
from scada_schema.loaders import _common, care, greenbyte, hill_of_towie, kelmarsh, penmanshiel, pvdaq
from scada_schema.states import attach_states

# ----------------------------------------------------------------------------------------------- Greenbyte

def test_greenbyte_column_map_handles_stats_and_unitless_titles():
    header = ["# Date and time", "Wind speed (m/s)", "Wind speed, Standard deviation (m/s)", "Wind speed, Minimum (m/s)",
              "Wind speed Sensor 2 (m/s)", "Power factor (cosphi)", "Power factor (cosphi), Max", "Power (kW)", "Power, Maximum (kW)",
              "Energy Export (kWh)", "Lost Production to Downtime (kWh)"]
    m = greenbyte._column_map(header)
    assert m["Wind speed (m/s)"] == "wind_speed" and m["Wind speed, Standard deviation (m/s)"] == "wind_speed__std"
    assert m["Wind speed, Minimum (m/s)"] == "wind_speed__min" and m["Wind speed Sensor 2 (m/s)"] == "wind_speed_2"
    assert m["Power factor (cosphi), Max"] == "power_factor__max" and m["Power, Maximum (kW)"] == "p_ac__max"
    assert "Lost Production to Downtime (kWh)" not in m  # results of the operator's system are not measurements


def test_kelmarsh_loader(kelmarsh_dir):
    L = kelmarsh.load(kelmarsh_dir, years=[2019])
    t, meta = L.table, L.meta
    assert validate(t, meta).ok
    assert set(t.device_id) == {"T01", "T02"} and len(t) == 2 * 144
    assert str(t["timestamp"].dt.tz) == "UTC" and t["timestamp"].iloc[0] == pd.Timestamp("2019-01-01", tz="UTC")
    assert {"p_ac", "p_ac__max", "wind_speed", "wind_speed__std", "wind_dir", "wind_dir_rel", "nacelle_dir", "e_export",
            "e_export_counter", "rotor_speed", "t_amb", "pitch", "power_factor", "power_factor__max"} <= set(t.columns)
    assert not any("Lost" in c for c in t.columns)
    first = t[t.device_id == "T01"].iloc[0]
    assert first["pitch"] == pytest.approx(0.0)  # mean of the blades A, B, C: -1, 0, 1
    assert np.isnan(t[t.device_id == "T01"]["wind_speed"].iloc[3])  # "NaN" stays missing
    assert t["e_export_counter"].dtype == "float64" and t["p_ac"].dtype == "float32"
    assert [d.id for d in meta.devices] == ["T01", "T02"] and meta.device("T01").rated_kw == 2050
    assert meta.source.doi == "10.5281/zenodo.5841833" and meta.source.license == "CC-BY-4.0"


def test_kelmarsh_status_log_and_states(kelmarsh_dir):
    L = kelmarsh.load(kelmarsh_dir, years=[2019])
    ev = L.events
    t1 = ev[ev.device_id == "T01"]
    assert list(t1["code"]) == ["25", "100", "9000", "1"]  # "100.0" is read as "100"
    assert list(t1["category"]) == ["scheduled_maintenance", "forced_outage", "requested", "full_performance"]
    assert t1["end"].isna().sum() == 1  # "-" = still open
    attach_states(L)
    t = L.table[L.table.device_id == "T01"].set_index("timestamp")
    # interval 00:20 and 00:30 have their midpoints inside the first stop (00:20-00:50); 00:40 too
    assert list(t.loc["2019-01-01 00:10":"2019-01-01 00:50", "iec_category"]) == [
        "full_performance", "scheduled_maintenance", "scheduled_maintenance", "scheduled_maintenance", "full_performance"]
    assert t.loc["2019-01-01 00:20", "status_code"] == "25"
    # warnings are not stops: the open "requested" warning does not label the intervals after 02:00
    assert t.loc["2019-01-01 05:00", "iec_category"] == "full_performance"
    assert validate(L.table, L.meta).ok


def test_penmanshiel_loader_two_digit_ids(tmp_path):
    z = tmp_path / "Penmanshiel_SCADA_2020_WT01-10_3109.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Turbine_Data_Penmanshiel_01_2020-01-01_-_2021-01-01_1.csv", greenbyte_csv(1, start="2020-01-01"))
        zf.writestr("Turbine_Data_Penmanshiel_10_2020-01-01_-_2021-01-01_2.csv", greenbyte_csv(2, start="2020-01-01"))
    L = penmanshiel.load(tmp_path, years=[2020])
    assert set(L.table.device_id) == {"T01", "T10"}
    assert L.meta.source.doi == "10.5281/zenodo.5946807"
    assert len(L.events) == 0
    with pytest.raises(FileNotFoundError):
        penmanshiel.load(tmp_path, years=[1999])


# ----------------------------------------------------------------------------------------------- Hill of Towie

@pytest.fixture
def hot_dir(tmp_path):
    t = pd.date_range("2021-06-01 00:10", periods=60, freq="10min")  # end-of-period stamps
    st = 2304509
    zname = tmp_path / "2021_subset.zip"
    with zipfile.ZipFile(zname, "w") as z:
        for table, cols in {"tblSCTurbine": ["wtc_AcWindSp_mean", "wtc_AcWindSp_stddev", "wtc_NacelPos_mean", "wtc_PitcPosA_mean",
                                             "wtc_PitcPosB_mean", "wtc_PitcPosC_mean", "wtc_GenRpm_mean", "wtc_MainSRpm_mean"],
                            "tblSCTurGrid": ["wtc_ActPower_mean", "wtc_VoltPhR_mean", "wtc_VoltPhS_mean", "wtc_VoltPhT_mean"],
                            "tblSCTurTemp": ["wtc_AmbieTmp_mean", "wtc_Gen1U1Tm_mean", "wtc_Gen1V1Tm_mean", "wtc_Gen1W1Tm_mean"]}.items():
            frames = []
            for k in (1, 2):
                d = pd.DataFrame({"TimeStamp": t.strftime("%Y-%m-%d %H:%M:%S"), "StationId": st + k})
                for i, c in enumerate(cols):
                    d[c] = float(i + k)
                frames.append(d)
            z.writestr(f"{table}_2021_06.csv", pd.concat(frames).to_csv(index=False))
        alarm = pd.DataFrame({"TimeOn": ["2021-06-01 00:20:00", "2021-06-01 00:40:00"], "TimeOff": ["2021-06-01 00:30:00", ""],
                              "StationNr": [st + 1, st + 2], "Alarmcode": [102, 9999]})
        z.writestr("tblAlarmLog_2021_06.csv", alarm.to_csv(index=False))
    (tmp_path / "Hill_of_Towie_alarms_description.csv").write_text(
        "Alarm Code,Description,Stopping\n102,Ice detection:,0\n9999,Something:,1\n", encoding="utf-8")
    (tmp_path / "Hill_of_Towie_turbine_metadata.csv").write_text(
        "Turbine Name,Rated power (kW),Latitude,Longitude,Hub Height (m),Rotor Diameter (m)\nT01,2300,57.4,-3.1,80,93\nT02,2300,57.4,-3.1,80,93\n",
        encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "Hill_of_Towie_ShutdownDuration.zip", "w") as z:
        z.writestr("ShutdownDuration.csv", "TimeStamp_StartFormat,TurbineName,ShutdownDuration\n2021-06-01 00:00:00,T01,600\n2021-06-01 00:10:00,T01,0\n")
    return tmp_path


def test_hill_of_towie_loader(hot_dir):
    L = hill_of_towie.load(hot_dir, years=[2021])
    t = L.table
    assert validate(t, L.meta).ok
    assert t["timestamp"].min() == pd.Timestamp("2021-06-01 00:00", tz="UTC")  # end stamp 00:10 moved to the start
    assert L.meta.source_label == "end" and set(t.device_id) == {"T01", "T02"}
    assert "wind_dir" not in t.columns
    r = t[(t.device_id == "T01") & (t.timestamp == pd.Timestamp("2021-06-01 00:00", tz="UTC"))].iloc[0]
    # each column holds (its position in the table + the turbine number): T01 has k = 1
    assert r["wind_speed"] == pytest.approx(1.0) and r["wind_speed__std"] == pytest.approx(2.0)
    assert r["pitch"] == pytest.approx((4.0 + 5.0 + 6.0) / 3)  # blades A, B, C
    assert r["v_ac"] == pytest.approx((2.0 + 3.0 + 4.0) / 3)  # three phases
    assert r["shutdown_s"] == 600
    assert t.loc[t.device_id == "T02", "shutdown_s"].isna().all()
    ev = L.events
    assert list(ev.sort_values("device_id")["status"]) == ["Informational", "Stop"]
    assert ev["end"].isna().sum() == 1


def test_hill_of_towie_station_mapping(hot_dir):
    L = hill_of_towie.load(hot_dir, years=[2021], devices=["T02"])
    assert set(L.table.device_id) == {"T02"}


# ----------------------------------------------------------------------------------------------- CARE

def care_zip(path, farm="A", event_id=0):
    maps = care.MAPS[farm]
    sensors = {s: phrase for _, (ss, phrase) in maps.items() for s in ss}
    feat = pd.DataFrame({"sensor_name": list(sensors), "statistics_type": "average", "description": [p.capitalize() for p in sensors.values()],
                         "unit": "x", "is_angle": False, "is_counter": False})
    n = 20
    data = pd.DataFrame({"time_stamp": pd.date_range("2022-08-04 06:10", periods=n, freq="10min").strftime("%Y-%m-%d %H:%M:%S"),
                         "asset_id": 7, "id": range(n), "train_test": ["train"] * 15 + ["prediction"] * 5, "status_type_id": [0] * 10 + [4] * 5 + [2] * 5})
    for i, s in enumerate(sensors):
        data[f"{s}_avg"] = float(i)
        data[f"{s}_max"] = float(i) + 1
    info = pd.DataFrame({"event_id": [event_id], "event_label": ["anomaly"], "event_start": ["2022-08-04 08:00:00"],
                         "event_end": ["2022-08-04 09:00:00"], "event_description": ["gearbox bearing"]})
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"CARE_To_Compare/Wind Farm {farm}/feature_description.csv", feat.to_csv(index=False))
        z.writestr(f"CARE_To_Compare/Wind Farm {farm}/event_info.csv", info.to_csv(index=False))
        z.writestr(f"CARE_To_Compare/Wind Farm {farm}/datasets/{event_id}.csv", data.to_csv(index=False))
    return sensors


def test_care_loader(tmp_path):
    sensors = care_zip(tmp_path / "CARE_To_Compare.zip")
    L = care.load(tmp_path, farm="A", event_id=0)
    t = L.table
    assert validate(t, L.meta).warnings and validate(t, L.meta).ok
    assert set(t.device_id) == {"A007"} and L.meta.source.license == "CC-BY-SA-4.0"
    assert t["p_ac"].iloc[0] == pytest.approx(list(sensors).index("power_30"))
    assert "p_ac__max" in t.columns and "p_ac__std" not in t.columns
    assert list(t["status_code"].iloc[[0, 12, 17]]) == ["0", "4", "2"]
    assert list(t["iec_category"].iloc[[0, 12, 17]]) == ["full_performance", "forced_outage", "technical_standby"]
    assert list(t["x_train_test"].iloc[[0, 19]]) == ["train", "prediction"]
    assert L.events.iloc[0]["status"] == "anomaly" and L.events.iloc[0]["message"] == "gearbox bearing"
    assert care.list_datasets(tmp_path, "A")["event_id"].tolist() == [0]


def test_care_wrong_sensor_description_raises(tmp_path):
    care_zip(tmp_path / "CARE_To_Compare.zip")
    feats = pd.DataFrame({"sensor_name": ["power_30"], "description": ["Grid power"]}).set_index("sensor_name")
    with pytest.raises(KeyError):
        care.check_map("A", feats)
    feats = pd.DataFrame({"sensor_name": [s for ss, _ in care.MAPS["A"].values() for s in ss],
                          "description": ["Nothing"] * sum(len(ss) for ss, _ in care.MAPS["A"].values())}).set_index("sensor_name")
    with pytest.raises(ValueError, match="expected it to mention"):
        care.check_map("A", feats)


# ----------------------------------------------------------------------------------------------- PVDAQ

def test_pvdaq_units_and_time(pvdaq_dir):
    L = pvdaq.load(pvdaq_dir, system=34, years=[2018])
    t, meta = L.table, L.meta
    assert validate(t, meta).ok
    inv = t[t.device_id == "INV"].set_index("timestamp")
    # 0.15 * 900 = 135 kW at the peak on both sides of the switch
    before = inv.loc["2018-07-25 18:00":"2018-07-25 22:00", "p_ac"].max()
    after = inv.loc["2018-08-10 18:00":"2018-08-10 22:00", "p_ac"].max()
    assert before == pytest.approx(after, rel=0.05) and 100 < after < 150
    met = t[t.device_id == "MET"].set_index("timestamp")
    assert met["t_amb"].iloc[0] == pytest.approx(30.0)  # 86 F
    assert met["wind_speed"].iloc[0] == pytest.approx(4.4704)
    assert inv["power_factor"].iloc[0] == pytest.approx(0.99)
    # local standard time 00:00 UTC-8 is 08:00 UTC; the stamp marks the end, the table the start: 07:45 UTC
    assert t["timestamp"].min() == pd.Timestamp("2018-07-20 07:45", tz="UTC")
    assert "NOT corrected" not in " ".join(meta.conversions) and "watts to kW" in " ".join(meta.conversions)
    assert inv["status_code"].iloc[0] == "0"


def test_pvdaq_without_unit_fix_keeps_the_artefact(pvdaq_dir):
    raw = pvdaq.load(pvdaq_dir, system=34, years=[2018], fix_units=False)
    inv = raw.table[raw.table.device_id == "INV"].set_index("timestamp")
    ratio = inv.loc["2018-08-10 18:00":"2018-08-10 22:00", "p_ac"].max() / inv.loc["2018-07-25 18:00":"2018-07-25 22:00", "p_ac"].max()
    assert ratio == pytest.approx(100, rel=0.05)  # watts read as hectowatts
    assert "NOT corrected" in " ".join(raw.meta.conversions)


def test_detect_power_unit_switch_none_without_switch():
    t = pd.date_range("2017-01-01", periods=96 * 30, freq="15min", tz="UTC")
    poa = pd.Series(np.clip(800 * np.sin((t.hour + t.minute / 60 - 6) / 12 * np.pi), 0, None), index=t)
    assert pvdaq.detect_power_unit_switch(poa * 0.2, poa) is None


# ----------------------------------------------------------------------------------------------- downloads

class FakeResponse:
    def __init__(self, body: bytes = b"", json=None, status=200):
        self._body, self._json, self.status_code = body, json, status

    def raise_for_status(self):
        pass

    def json(self):
        return self._json

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, record, body):
        self.record, self.body, self.headers = record, body, {}

    def get(self, url, **kw):
        return FakeResponse(json=self.record) if "api/records" in url else FakeResponse(self.body)


def test_zenodo_listing_and_checked_download(tmp_path):
    body = b"x" * 3000
    md5 = hashlib.md5(body).hexdigest()
    rec = {"files": [{"key": "a.zip", "size": len(body), "checksum": f"md5:{md5}", "links": {"self": "https://zenodo.org/files/a.zip"}}]}
    s = FakeSession(rec, body)
    files = _common.zenodo_files("1", s)
    assert files["a.zip"]["md5"] == md5
    p = _common.download_file(files["a.zip"], tmp_path / "a.zip", s)
    assert p.read_bytes() == body and not list(tmp_path.glob("*.part"))
    bad = dict(files["a.zip"], md5="0" * 32)
    (tmp_path / "a.zip").unlink()
    with pytest.raises(OSError, match="checksum"):
        _common.download_file(bad, tmp_path / "a.zip", s)
    assert not (tmp_path / "a.zip").exists() and not list(tmp_path.glob("*.part"))


def test_kelmarsh_fetch_selects_years(tmp_path, monkeypatch):
    names = ["Kelmarsh_SCADA_2018_1.zip", "Kelmarsh_SCADA_2019_2.zip", "Kelmarsh_WT_static.csv", "Other.pdf"]
    files = {n: {"size": 1, "md5": None, "url": f"https://x/{n}"} for n in names}
    got = []
    monkeypatch.setattr(kelmarsh, "zenodo_files", lambda record, session=None: files)
    monkeypatch.setattr(kelmarsh, "download_file", lambda info, dest, session=None: got.append(dest.name) or dest)
    kelmarsh.fetch(tmp_path, years=[2019])
    assert sorted(got) == ["Kelmarsh_SCADA_2019_2.zip", "Kelmarsh_WT_static.csv"]
    assert re.fullmatch(kelmarsh.ARCHIVE, "Kelmarsh_SCADA_2019_3085.zip")


# ----------------------------------------------------------------------------------------------- La Haute Borne

def test_la_haute_borne_loader_reads_utc_offsets_exactly(tmp_path):
    from scada_schema.loaders import la_haute_borne

    rows = []
    for name in ("R80721", "R80711"):
        for stamp in ("2014-10-26T01:50:00+02:00", "2014-10-26T02:00:00+02:00", "2014-10-26T02:00:00+01:00", "2014-10-26T02:10:00+01:00"):
            rows.append(f"{name},{stamp},-1.0,500.0,7.0,0.5,10.0,180.0,182.0")
    scada = "Wind_turbine_name,Date_time,Ba_avg,P_avg,Ws_avg,Va_avg,Ot_avg,Ya_avg,Wa_avg\n" + "\n".join(rows) + "\n"
    asset = ("Wind_turbine_name,Latitude,Longitude,elevation_m,Rated_power,Hub_height_m,Rotor_diameter_m,Manufacturer,Model\n"
             "R80711,48.45,5.58,411,2050,80,82,Senvion,MM82\nR80721,48.44,5.58,411,2050,80,82,Senvion,MM82\n")
    with zipfile.ZipFile(tmp_path / "la_haute_borne.zip", "w") as z:
        z.writestr(la_haute_borne.SCADA_FILE, scada)
        z.writestr(la_haute_borne.ASSET_FILE, asset)
    L = la_haute_borne.load(tmp_path)
    t = L.table[L.table.device_id == "T01"]["timestamp"]
    # the repeated hour 02:00-03:00 of the autumn change is told apart by the offset: 00:00Z and 01:00Z
    assert sorted(t.dt.strftime("%H:%M")) == ["00:00", "01:00", "01:10", "23:50"]
    assert validate(L.table, L.meta).ok and [d.id for d in L.meta.devices] == ["T01", "T02"]
    assert L.meta.device("T01").rated_kw == 2050 and L.meta.device("T01").lat == pytest.approx(48.45)  # R80711 is the first name
    assert L.table["wind_dir_rel"].iloc[0] == pytest.approx(0.5) and L.table["pitch"].iloc[0] == pytest.approx(-1.0)
    with pytest.raises(FileNotFoundError, match="fetch the data first"):
        la_haute_borne.load(tmp_path / "nowhere")
