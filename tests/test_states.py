from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from res_asset_kpi.wind.availability import availability, category_totals

from scada_schema import states
from scada_schema.states import (
    CODE_TABLES,
    IEC_CATEGORIES,
    availability_from_table,
    categories_for_intervals,
    code_table_coverage,
    load_code_table,
    map_codes,
    map_labels,
    project_events,
)


def test_vocabulary_is_the_ten_iec_categories():
    assert len(IEC_CATEGORIES) == 10
    assert {"full_performance", "forced_outage", "scheduled_maintenance", "no_info", "electrical"} <= set(IEC_CATEGORIES)


@pytest.mark.parametrize("name", CODE_TABLES)
def test_shipped_code_tables_are_clean(name):
    t = load_code_table(name)
    assert t["code"].is_unique and len(t) > 100
    assert set(t["category"]) - {""} <= set(IEC_CATEGORIES)
    assert set(t["stopping"]) <= {"0", "1", ""}
    assert t["category"].ne("").any()


def test_map_codes_reads_numbers_and_floats_alike():
    s = pd.Series(["21", "21.0", 21, 21.0, "nonsense", None])
    got = map_codes(s, "senvion_mm")
    assert list(got[:4]) == ["forced_outage"] * 4  # 21 is a remote manual stop in the table
    assert got[4] is None and got[5] is None
    # warnings and information carry no category
    assert map_codes(pd.Series(["0"]), "senvion_mm").iloc[0] is None


def test_map_codes_with_a_custom_table():
    t = pd.DataFrame({"code": ["7", "8"], "category": ["environmental", ""]})
    assert list(map_codes(pd.Series([7, 8, 9]), t)) == ["environmental", None, None]


def test_map_labels_for_greenbyte_and_care():
    assert list(map_labels(pd.Series(["Forced outage", "Requested Shutdown", "Full Performance", "unknown label"]), "greenbyte")) == [
        "forced_outage", "requested", "full_performance", None]
    assert list(map_labels(pd.Series([0, 1, 2, 3, 4, 5]), "care")) == [
        "full_performance", "partial_performance", "technical_standby", "scheduled_maintenance", "forced_outage", "no_info"]
    with pytest.raises(KeyError):
        map_labels(pd.Series(["x"]), "nonexistent")


def test_coverage_of_a_code_table():
    c = code_table_coverage(pd.Series(["21", "21", "0", "99999"]), "senvion_mm")
    assert c["rows"] == 4 and c["in_table"] == pytest.approx(0.75) and c["with_category"] == pytest.approx(0.5)


def grid(device="T01", n=12):
    return pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=n, freq="10min", tz="UTC"), "device_id": device})


def test_project_events_midpoint_rule_and_last_start_wins():
    t = grid()
    ev = pd.DataFrame({
        "device_id": "T01",
        "start": pd.to_datetime(["2021-01-01 00:20", "2021-01-01 00:35"], utc=True),
        "end": pd.to_datetime(["2021-01-01 00:50", "2021-01-01 01:00"], utc=True),
        "category": ["scheduled_maintenance", "forced_outage"], "code": ["20", "100"]})
    got = project_events(ev, t, "10min", ("category", "code"))
    cats = list(got["category"])
    # intervals start 00:00, 00:10, ... midpoints 00:05, 00:15, 00:25, 00:35, 00:45, 00:55
    assert cats[:2] == [None, None]
    assert cats[2] == "scheduled_maintenance"  # midpoint 00:25 in the first event only
    assert cats[3] == "forced_outage" and cats[4] == "forced_outage"  # midpoint 00:35 and 00:45: the later start wins
    assert cats[5] == "forced_outage"  # midpoint 00:55 < 01:00
    assert cats[6] is None and list(got["code"][2:6]) == ["20", "100", "100", "100"]


def test_open_event_runs_to_the_end_and_devices_do_not_mix():
    t = pd.concat([grid("T01"), grid("T02")], ignore_index=True)
    ev = pd.DataFrame({"device_id": ["T02"], "start": pd.to_datetime(["2021-01-01 01:00"], utc=True), "end": pd.NaT,
                       "category": ["requested"], "code": ["9000"]})
    ev["end"] = pd.to_datetime(ev["end"], utc=True)
    got = categories_for_intervals(ev, t, "10min")
    assert set(got[:12]) == {"full_performance"}
    assert list(got[12 + 6:]) == ["requested"] * 6 and list(got[12:12 + 6]) == ["full_performance"] * 6


def test_only_filter_keeps_warnings_out():
    t = grid()
    ev = pd.DataFrame({"device_id": "T01", "start": pd.to_datetime(["2021-01-01 00:00"], utc=True),
                       "end": pd.to_datetime(["2021-01-01 02:00"], utc=True), "category": ["requested"], "code": ["9000"],
                       "status": ["Warning"]})
    assert set(categories_for_intervals(ev, t, "10min", {"status": "Stop"})) == {"full_performance"}
    assert set(categories_for_intervals(ev, t, "10min", {"status": "Warning"})) == {"requested"}


def test_availability_matches_res_asset_kpi_directly():
    n = 6 * 24 * 10
    cat = np.full(n, "full_performance", dtype=object)
    cat[100:160] = "forced_outage"  # 10 h
    cat[300:330] = "scheduled_maintenance"  # 5 h
    cat[500:520] = "environmental"
    df = pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=n, freq="10min", tz="UTC"), "device_id": "T01", "iec_category": cat,
                       "e_export": 100.0})
    got = availability_from_table(df, "10min", "iec_operational")
    direct = availability(category_totals(cat, 1 / 6), "iec_operational")
    assert got.time == pytest.approx(direct.time)
    hours = n / 6
    assert got.time == pytest.approx(1 - 15 / hours)  # forced outage and scheduled maintenance count against the turbine
    oem = availability_from_table(df, "10min", "oem_warranty", allowance_h=60.0)
    assert oem.time > got.time  # the allowance excuses the maintenance; environmental stops are excluded


def test_module_exports():
    assert {"project_events", "attach_states", "map_codes", "availability_from_table"} <= set(states.__all__)


def test_uncategorised_codes_of_the_table_can_read_as_operating():
    got = map_codes(pd.Series(["0", "21", "99999"]), "senvion_mm", uncategorised="full_performance")
    assert list(got) == ["full_performance", "forced_outage", None]  # 0 "System OK" has no category; 99999 is not in the table
    assert list(map_codes(pd.Series(["0"]), "senvion_mm")) == [None]


def test_rows_without_a_measurement_are_no_info_not_operating():
    t = grid(n=6)
    t["p_ac"] = [1.0, 1.0, np.nan, np.nan, 1.0, 1.0]
    t["wind_speed"] = [5.0, 5.0, np.nan, 5.0, 5.0, 5.0]
    ev = pd.DataFrame({"device_id": "T01", "start": pd.to_datetime(["2021-01-01 00:40"], utc=True), "end": pd.to_datetime(["2021-01-01 00:50"], utc=True),
                       "category": ["forced_outage"], "code": ["1"]})
    got = categories_for_intervals(ev, t, "10min")
    assert list(got) == ["full_performance", "full_performance", "no_info", "full_performance", "forced_outage", "full_performance"]


def test_availability_leaves_out_intervals_without_a_category(caplog):
    cat = np.array(["full_performance"] * 8 + [None] * 2, dtype=object)
    df = pd.DataFrame({"timestamp": pd.date_range("2021-01-01", periods=10, freq="10min", tz="UTC"), "device_id": "T01", "iec_category": cat})
    a = availability_from_table(df, "10min", "iec_operational")
    assert a.time == 1.0 and "left out" in caplog.text  # a healthy turbine is not an outage because the state is unknown
