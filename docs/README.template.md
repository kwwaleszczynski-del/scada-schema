# scada-schema: an open data standard for wind and PV SCADA

[![tests](https://github.com/kwwaleszczynski-del/scada-schema/actions/workflows/ci.yml/badge.svg)](https://github.com/kwwaleszczynski-del/scada-schema/actions/workflows/ci.yml)

**Project page with the results: [kwwaleszczynski-del.github.io/scada-schema](https://kwwaleszczynski-del.github.io/scada-schema/)**
- [specification](SPEC.md) - [example quality report](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_34_raw_units.html).

SCADA exports from two wind farms never look alike: columns named after a vendor's tag list, power in hectowatts or watts, time in local
time or at the end of the interval, a status code that only the turbine manual explains. Every analysis tool then starts with its own
cleaning script. This package fixes **one table layout** (names, units, sign and angle conventions, time zone and interval, device types) for
wind turbines and PV plants, reads **five open datasets** into it from their original files, checks the data, maps status codes to
**IEC 61400-26 categories** and converts the table to the inputs of **wind-up, OpenOA and pvlib**, with a test that each tool gives the same result
on the library's input as on a hand-built one.

It is a proposal, not a standard of any body, and working material: the formulas and category names follow public descriptions, and nothing
here is a certified implementation of an IEC standard.

## What is in it

1. **Schema** ([SPEC.md](SPEC.md), generated from the code): {{n_signals}} canonical signals ({{n_wind_signals}} in the wind profile, {{n_pv_signals}} in the PV profile),
   one row per device and interval, `timestamp` in UTC at the start of the interval, canonical units, power positive into the grid, compass
   angles clockwise from true north. Minimum, maximum and standard deviation columns where a source has them. The metadata (profile, interval, the
   time zone and label of the *source*, devices with ratings and positions, licence, every conversion the loader made) are stored inside the
   Parquet file. pydantic describes the metadata; the table is validated by a vectorised check that reports every violation with a count (pandera
   stops at the first failure and adds a dependency, and a per-row quality report is needed anyway).
2. **Loaders** for Kelmarsh, Penmanshiel, Hill of Towie, CARE to Compare and NREL PVDAQ. They read the original archives (Zenodo, OEDI) and
   `fetch` downloads them with checksum verification; nothing large is in this repository. Hill of Towie's 1.4 GB yearly archives are read with range
   requests, so only the needed tables are transferred.
3. **Quality control** with an HTML report: {{n_checks}} checks - duplicate and unordered time stamps, time jumps, gaps, daylight saving artefacts, missing values,
   physical limits, flat lines, spikes, power-curve outliers, wind direction (vane, nacelle and north offsets between turbines), energy counters and interval energy,
   icing, and units (order of magnitude and a switch of unit inside a record). The report is one file without scripts.
4. **IEC 61400-26 states**: code tables for the Senvion MM series ({{code_table_senvion}} codes) and the Siemens SWT 2.3 ({{code_table_siemens}} codes) as logged in the open
   datasets, label tables for Greenbyte and CARE, projection of an event log onto the intervals, and time-based availability through
   [res-asset-kpi](https://github.com/kwwaleszczynski-del/res-asset-kpi) (which also supplies the unit, time-zone and category code used here).
5. **Converters** to wind-up, OpenOA and pvlib and the parity checks below.
6. **Command line**: `scada-schema fetch | load | validate | qc | convert | spec | study`.

## Results

All numbers on this page come from `python -m scada_schema study`, which writes `results/study.json`; `tests/test_docs.py` renders this README from the
template and that file and fails when they differ.

{{n_datasets}} tables from {{n_sources}} sources ({{rows}} rows, {{n_devices}} devices) were loaded from the original files; the schema accepts {{schema_valid}}.

{{TABLE_DATASETS}}

Number of devices with at least one finding, by check (the quality reports list every finding with its longest episodes):

{{TABLE_CHECKS}}

What the checks found on real data:

- **A unit switch is found where the dataset documents it.** PVDAQ's power channels change from hectowatts to watts in August 2018. Read as hectowatts
  throughout, system 34 shows a step of a factor of {{pvdaq_switch_factor}} in power over irradiance on {{pvdaq_switch_date_qc}}; the loader finds the same switch from the data
  (`{{pvdaq_loader_note}}`), and the check on the corrected table reports {{pvdaq_fixed_switch_findings}} switches.
- **North offsets.** On Hill of Towie the nacelle position of {{hot_north_flagged}} of {{hot_north_turbines}} turbines is more than 10 degrees off the fleet mean direction;
  the largest, {{hot_north_dev}}, is {{hot_north_max}} degrees off. On Kelmarsh the largest offset is {{kel_north_max}} degrees.
- **Time.** The daylight saving check runs on the UTC time stamps of the three UK farms (against Europe/London) and on the others it is not applicable
  (fixed offset or anonymised time); the table above shows what it found.

### IEC 61400-26 mapping

A stop event labels an interval when the interval's midpoint lies in the event. On the Greenbyte farms the operator also labels each stop with an IEC category,
which tests the code tables:

{{TABLE_IEC}}

On Kelmarsh the Senvion table agrees with the operator's category on {{kel_agree}} of {{kel_compared}} compared stop events, on Penmanshiel on {{pen_agree}} of {{pen_compared}}. **This is a
consistency check, not an independent test:** the Senvion table was written from the same open logs. The Hill of Towie alarm log has no operator category; the dataset describes only
{{hot_published_codes}} alarm codes ({{hot_events_published}} of its {{hot_events}} alarm events), the rest of the Siemens table was inferred from SCADA in
[wind-om-reliability](https://github.com/kwwaleszczynski-del/wind-om-reliability) (column `source` of the table), and {{hot_events_in_table}} of the events have a code in the table. No availability is
computed for it. The availability figures of the Greenbyte farms are what the operator's categories imply under two illustrative treatments
(`iec_operational`: every operative state counts as available; `oem_warranty`: grid, environment and requested stops excluded, 60 h of maintenance excused per turbine and year); they are not a reading of any contract.

### Converters: the same result through the library and by hand

{{TABLE_PARITY}}

OpenOA's default power-curve bins start at 0 (centres 0.25, 0.75, ...) while the bins of IEC 61400-12-1 and res-asset-kpi are centred on multiples of 0.5; with the bins aligned
the two implementations agree to {{oa_aligned_diff}} kW, with OpenOA's default bins the curves differ by up to {{oa_default_pct}} % of rated power. The converters do not hide that: it is
a property of the tool.

## Install and use

```bash
pip install -e ".[converters]"          # core: pip install -e .
```

```python
from scada_schema.loaders import kelmarsh
from scada_schema import qc, validate
from scada_schema.states import attach_states

loaded = kelmarsh.load("data/kelmarsh", years=[2019])     # fetch first: scada-schema fetch kelmarsh --years 2019
attach_states(loaded)                                      # status_code and iec_category from the event log
assert validate(loaded.table, loaded.meta).ok
report = qc.run(loaded.table, loaded.meta)
report.to_html("kelmarsh_2019_qc.html")

from scada_schema.convert import windup, openoa
scada, notes = windup.to_windup_scada(loaded.table, loaded.meta)
plant = openoa.to_openoa(loaded.table, loaded.meta)
```

```bash
scada-schema load pvdaq --system 34 --years 2016 2017 2018 --out out/pvdaq_34.parquet
scada-schema qc out/pvdaq_34.parquet --html out/pvdaq_34.html
scada-schema convert out/pvdaq_34.parquet --to pvlib --out out/pvlib
```

Raw files are looked for in the folder given with `--data-dir`, then in `$SCADA_SCHEMA_<NAME>_DIR`, `$SCADA_SCHEMA_DATA/<name>` and `./data/<name>`.

## What is not verified

- The wind-up check uses wind-up 0.5.1 (beta; its v1 input is not fixed) and the filter stage with a minimal configuration; the analysis periods in `windup_config` are placeholders
  and the pitch and generator speed ranges are estimated from the data. A full wind-up assessment was not run.
- The code tables are written from the public code texts of the open datasets; they are not OEM documents. IEC categories are the top levels of the information model, not the whole tree.
- The quality thresholds are defaults chosen to be conservative; they were tuned on these datasets and are not validated on others. Findings describe data, not the condition of a sensor.
- CARE's sensor names are anonymised; the sensor tables of farms A, B and C cover the signals listed in `loaders/care.py` and each entry is checked against the benchmark's own
  description when loading. CARE time stamps are anonymised, so checks that need real time do not apply to it.
- Hill of Towie's SCADA tables used here have no absolute wind direction; only the nacelle position is mapped.
- The schema has been applied to open datasets only. Operator exports from other vendors need their own loader (a loader is a column mapping, a unit table and a time convention).
- `pse-opendata` is not used: the schema holds SCADA, and no PSE data are needed for it.

## Reproduce

```bash
scada-schema fetch kelmarsh --years 2019 && scada-schema fetch penmanshiel --years 2020   # and hill_of_towie, care, pvdaq
python -m scada_schema study --out results
python scripts/build_pages.py && python scripts/render_readme.py
pytest -q                                       # tests with real data: set SCADA_SCHEMA_<NAME>_DIR
```

Datasets and licences: Kelmarsh and Penmanshiel (Cubico Sustainable Investments, CC BY 4.0, doi 10.5281/zenodo.5841833 and 10.5281/zenodo.5946807), Hill of Towie (RES, CC BY 4.0,
doi 10.5281/zenodo.14870021), CARE to Compare (CC BY-SA 4.0, doi 10.5281/zenodo.15846963; the share-alike condition applies to the data and to datasets derived from it, not to this
package's code), NREL PVDAQ (CC BY 4.0, doi 10.25984/1846021). The code is MIT licensed.
