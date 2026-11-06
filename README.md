# scada-schema: an open data standard for wind and PV SCADA

[![tests](https://github.com/kwwaleszczynski-del/scada-schema/actions/workflows/ci.yml/badge.svg)](https://github.com/kwwaleszczynski-del/scada-schema/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/LICENSE)

**Project page with the results: [kwwaleszczynski-del.github.io/scada-schema](https://kwwaleszczynski-del.github.io/scada-schema/)**
- [specification](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/SPEC.md) - [example quality report](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_34_raw_units.html).

SCADA exports from two wind farms never look alike: columns named after a vendor's tag list, power in hectowatts or watts, time in local
time or at the end of the interval, a status code that only the turbine manual explains. Every analysis tool then starts with its own
cleaning script. This package fixes **one table layout** (names, units, sign and angle conventions, time zone and interval, device types) for
wind turbines and PV plants, reads **open datasets from 6 sources** into it from their original files, checks the data, maps status codes to
**IEC 61400-26 categories** and converts the table to the inputs of **wind-up, OpenOA and pvlib**, with a test that each tool gives the same result
on the library's input as on a hand-built one.

It is a proposal, not a standard of any body, and working material: the formulas and category names follow public descriptions, and nothing
here is a certified implementation of an IEC standard.

## What is in it

1. **Schema** ([SPEC.md](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/SPEC.md), generated from the code): 40 canonical signals (31 in the wind profile, 23 in the PV profile),
   one row per device and interval, `timestamp` in UTC at the start of the interval, canonical units, power positive into the grid, compass
   angles clockwise from true north. Minimum, maximum and standard deviation columns where a source has them. The metadata (profile, interval, the
   time zone and label of the *source*, devices with ratings and positions, licence, every conversion the loader made) are stored inside the
   Parquet file. pydantic describes the metadata; the table is validated by a vectorised check that reports every violation with a count (pandera
   stops at the first failure and adds a dependency, and a per-row quality report is needed anyway).
2. **Loaders** for Kelmarsh, Penmanshiel, Hill of Towie, La Haute Borne, CARE to Compare and NREL PVDAQ. They read the original archives (Zenodo, OEDI) and
   `fetch` downloads them with checksum verification; nothing large is in this repository. Hill of Towie's 1.4 GB yearly archives are read with range
   requests, so only the needed tables are transferred.
3. **Quality control** with an HTML report: 15 checks - duplicate and unordered time stamps, time jumps, gaps, daylight saving artefacts, missing values,
   physical limits, flat lines, spikes, power-curve outliers, wind direction (vane, nacelle and north offsets between turbines), energy counters and interval energy,
   icing, and units (order of magnitude and a switch of unit inside a record). The report is one file without scripts.
4. **IEC 61400-26 states**: code tables for the Senvion MM series (268 codes) and the Siemens SWT 2.3 (146 codes) as logged in the open
   datasets, label tables for Greenbyte and CARE, projection of an event log onto the intervals, and time-based availability through
   [res-asset-kpi](https://github.com/kwwaleszczynski-del/res-asset-kpi) (which also supplies the unit, time-zone and category code used here).
5. **Converters** to wind-up, OpenOA and pvlib and the parity checks below.
6. **Your own data without code**: `scada-schema import mapping.yaml` turns a CSV, Excel or Parquet export into a canonical table from a YAML mapping (columns, units,
   time zone and label, devices); see below.
7. **JSON Schema** of the metadata and of the mapping file, and a list of every canonical column, in [`schema/`](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/schema) for tools that are not written in Python.
8. **Resampling** to a coarser interval with rules by signal kind (mean, vector mean of angles, sum, last meter reading, pooled standard deviation, coverage threshold): `scada-schema resample`.
9. **Command line**: `scada-schema fetch | load | import | resample | validate | qc | convert | jsonschema | spec | study`.

## Results

All numbers on this page come from `python -m scada_schema study`, which writes `results/study.json`; `tests/test_docs.py` renders this README from the
template and that file and fails when they differ.

12 tables from 6 sources (3,870,948 rows, 59 devices) were loaded from the original files; the schema accepts 12 of 12.

| Dataset | Source (licence) | Profile | Interval | Rows | Devices | Signals | Schema | QC findings (error / warning / info) |
|---|---|---|---|---:|---:|---:|---|---|
| [kelmarsh_2019](https://kwwaleszczynski-del.github.io/scada-schema/qc/kelmarsh_2019.html) | Kelmarsh wind farm data (Cubico Sustainable Investments) (CC-BY-4.0) | wind | 10min | 315,360 | 6 | 23 (+39 statistics) | valid | 0 / 19 / 49 |
| [penmanshiel_2020](https://kwwaleszczynski-del.github.io/scada-schema/qc/penmanshiel_2020.html) | Penmanshiel wind farm data (Cubico Sustainable Investments) (CC-BY-4.0) | wind | 10min | 737,856 | 14 | 23 (+60 statistics) | valid | 0 / 359 / 107 |
| [hill_of_towie_2021](https://kwwaleszczynski-del.github.io/scada-schema/qc/hill_of_towie_2021.html) | Hill of Towie wind farm open dataset (RES) (CC-BY-4.0) | wind | 10min | 1,090,921 | 21 | 20 (+42 statistics) | valid | 2 / 115 / 155 |
| [la_haute_borne](https://kwwaleszczynski-del.github.io/scada-schema/qc/la_haute_borne.html) | La Haute Borne wind farm data (ENGIE, copy shipped with OpenOA) (Etalab open licence (as stated by ENGIE, not re-verified)) | wind | 10min | 420,432 | 4 | 7 (+0 statistics) | valid | 0 / 11 / 18 |
| [care_A](https://kwwaleszczynski-del.github.io/scada-schema/qc/care_A.html) | CARE to Compare (CC-BY-SA-4.0) | wind | 10min | 54,358 | 1 | 16 (+15 statistics) | valid | 0 / 3 / 6 |
| [care_B](https://kwwaleszczynski-del.github.io/scada-schema/qc/care_B.html) | CARE to Compare (CC-BY-SA-4.0) | wind | 10min | 56,564 | 1 | 15 (+39 statistics) | valid | 1 / 6 / 5 |
| [care_C](https://kwwaleszczynski-del.github.io/scada-schema/qc/care_C.html) | CARE to Compare (CC-BY-SA-4.0) | wind | 10min | 55,753 | 1 | 8 (+18 statistics) | valid | 0 / 3 / 1 |
| [pvdaq_34](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_34.html) | NREL PVDAQ public datasets (OEDI) (CC-BY-4.0) | pv | 15min | 210,300 | 2 | 15 (+0 statistics) | valid | 2 / 7 / 7 |
| [pvdaq_1276](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_1276.html) | NREL PVDAQ public datasets (OEDI) (CC-BY-4.0) | pv | 15min | 209,162 | 2 | 14 (+0 statistics) | valid | 2 / 11 / 5 |
| [pvdaq_1278](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_1278.html) | NREL PVDAQ public datasets (OEDI) (CC-BY-4.0) | pv | 15min | 315,456 | 3 | 14 (+0 statistics) | valid | 3 / 12 / 10 |
| [pvdaq_35](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_35.html) | NREL PVDAQ public datasets (OEDI) (CC-BY-4.0) | pv | 15min | 194,486 | 2 | 15 (+0 statistics) | valid | 2 / 13 / 7 |
| [pvdaq_34_raw_units](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_34_raw_units.html) | NREL PVDAQ public datasets (OEDI) (CC-BY-4.0) | pv | 15min | 210,300 | 2 | 15 (+0 statistics) | valid | 4 / 7 / 7 |

Number of devices with at least one finding, by check (the quality reports list every finding with its longest episodes):

| Check | kelmarsh_2019 | penmanshiel_2020 | hill_of_towie_2021 | la_haute_borne | care_A | care_B | care_C | pvdaq_34 | pvdaq_1276 | pvdaq_1278 | pvdaq_35 | pvdaq_34_raw_units |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `duplicates` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `ordering` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `time_jumps` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `gaps` | 0 | 0 | 21 | 4 | 1 | 1 | 0 | 2 | 2 | 3 | 2 | 2 |
| `dst` | 0 | 0 | 0 | 4 | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped |
| `missing_values` | 6 | 14 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 1 | 1 |
| `out_of_range` | 5 | 14 | 0 | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| `flatline` | 1 | 13 | 21 | 1 | 1 | 1 | 1 | 2 | 2 | 3 | 2 | 2 |
| `spikes` | 6 | 14 | 21 | 1 | 1 | 1 | 1 | 2 | 2 | 3 | 2 | 2 |
| `power_curve` | 6 | 14 | 21 | 4 | 1 | 1 | 1 | skipped | skipped | skipped | skipped | skipped |
| `wind_direction` | 6 | 14 | 21 | 4 | 0 | 0 | skipped | 0 | 0 | 0 | 0 | 0 |
| `icing` | 1 | 8 | 21 | 4 | 0 | 1 | 0 | skipped | skipped | skipped | skipped | skipped |
| `energy_counter` | 2 | 0 | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped |
| `energy_power` | 0 | 0 | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped | skipped |
| `units` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

What the checks found on real data:

- **A unit switch is found where the dataset documents it.** PVDAQ's power channels change from hectowatts to watts in August 2018. Read as hectowatts
  throughout, system 34 shows a step of a factor of 98 in power over irradiance on 2018-08-05; the loader finds the same switch from the data
  (`p_ac, p_dc: hectowatts to kW (x0.1) until 2018-08-04 20:00 UTC, watts to kW (x0.001) from then on`), and the check on the corrected table reports 0 switches.
- **North offsets.** On Hill of Towie the nacelle position of 12 of 21 turbines is more than 10 degrees off the fleet mean direction;
  the largest, T16, is 84.7 degrees off. On Kelmarsh the largest offset is 3.8 degrees.
- **A real daylight saving artefact.** La Haute Borne stamps its data with the UTC offset, so the time is exact, but at both autumn changes (2014-10-26 and 2015-10-25) one hour is missing in UTC (00:00 to 01:00, the first occurrence of
  the repeated local hour): the source holds only one of the two repeated hours. The `dst` check, run against Europe/Paris, finds 2 of 4 changes on every turbine.
- **Time elsewhere.** The three UK farms are checked against Europe/London (UTC time stamps, no artefact found); for the others the check does not apply (fixed offset or anonymised time).
  The table above shows what each dataset triggered.

### IEC 61400-26 mapping

A stop event labels an interval when the interval's midpoint lies in the event. On the Greenbyte farms the operator also labels each stop with an IEC category,
which tests the code tables:

| Dataset | Code table | Events | Events with a code in the table | Stop events | Compared with the operator's category | Agreement | Time-based availability, IEC operational / OEM style |
|---|---|---:|---:|---:|---:|---:|---|
| kelmarsh_2019 | `senvion_mm` | 59,313 | 100.0 % | 673 | 673 | 100.0 % | 99.5 % / 99.8 % |
| penmanshiel_2020 | `senvion_mm` | 158,509 | 100.0 % | 3,267 | 3,264 | 100.0 % | 97.8 % / 98.1 % |
| hill_of_towie_2021 | `siemens_swt_hot` | 798,406 | 94.9 % | 4,034 | - | no operator category | not computed |

On Kelmarsh the Senvion table agrees with the operator's category on 100.0 % of 673 compared stop events, on Penmanshiel on 100.0 % of 3,264. **This is a
consistency check, not an independent test:** the Senvion table was written from the same open logs. The Hill of Towie alarm log has no operator category; the dataset describes only
12 alarm codes (83.6 % of its 798,406 alarm events), the rest of the Siemens table was inferred from SCADA in
[wind-om-reliability](https://github.com/kwwaleszczynski-del/wind-om-reliability) (column `source` of the table), and 94.9 % of the events have a code in the table. No availability is
computed for it. The availability figures of the Greenbyte farms are what the operator's categories imply under two illustrative treatments
(`iec_operational`: every operative state counts as available; `oem_warranty`: grid, environment and requested stops excluded, 60 h of maintenance excused per turbine and year); they are not a reading of any contract.

### Converters: the same result through the library and by hand

| Target | Input | What runs on both inputs | Largest difference |
|---|---|---|---|
| wind-up 0.5 | Kelmarsh 2019, turbines T01 to T03, 157,680 rows | the SCADA frame itself | 1.2e-04 (float32 rounding) |
| | | `filter_scada_df`: same rows removed (30.2 % of power values) | 1.2e-04; missing pattern identical |
| | | `calc_pc_and_rated_ws_one_ttype` power curve | 6.5e-06 kW |
| OpenOA 3 | Kelmarsh 2019, turbines T01 to T03, 157,680 rows | `PlantData.scada` | 1.2e-04 |
| | | `power_curve.IEC` | 1.6e-02 kW |
| pvlib 0.16 | PVDAQ system 34, 2017, 35,040 intervals | PVWatts `ModelChain` (modelled plant up to 141 kW) | 8.3e-08 relative in daylight (6.7e-03 W) |

OpenOA's default power-curve bins start at 0 (centres 0.25, 0.75, ...) while the bins of IEC 61400-12-1 and res-asset-kpi are centred on multiples of 0.5; with the bins aligned
the two implementations agree to 0.0e+00 kW, with OpenOA's default bins the curves differ by up to 4.8 % of rated power. The converters do not hide that: it is
a property of the tool.

## Install and use

Not on PyPI yet; install from GitHub:

```bash
pip install "scada-schema @ git+https://github.com/kwwaleszczynski-del/scada-schema"
pip install "scada-schema[converters] @ git+https://github.com/kwwaleszczynski-del/scada-schema"   # with wind-up, OpenOA and pvlib
```

From a clone of the repository (development):

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

## Your own data

A loader for a new export is a mapping file, not code (examples: [wind, stacked CSV](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/docs/examples/wind_semicolon.yaml), [PV, wide CSV](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/docs/examples/pv_wide.yaml)):

```yaml
name: my_wind_farm
profile: wind
interval: 10min
files: ["export/*.csv"]
read: {sep: ";", decimal: ",", encoding: utf-8}
timestamp: {column: Time, timezone: Europe/Warsaw, label: end, format: "%d.%m.%Y %H:%M"}
device_column: Turbine
columns:
  "Active power [W]": {signal: p_ac, unit: W}
  "Wind [km/h]": {signal: wind_speed, unit: km/h}
  "Ambient [F]": {signal: t_amb, unit: degF}
  "Status": {signal: status_code}
devices:
  T01: {type: turbine, rated_kw: 2050}
```

```bash
scada-schema import mapping.yaml --out my_farm.parquet
scada-schema qc my_farm.parquet --html my_farm_qc.html
```

Time stamps are converted to UTC with the daylight saving rules of the zone (the repeated autumn hour is resolved by order), moved to the start of the interval and checked against the
grid; units are converted through the registry of res-asset-kpi; every conversion lands in the metadata. A wrong time zone, a missing column or a unit that does not fit the signal stops
the import with a message that names it. [`schema/mapping.schema.json`](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/schema/mapping.schema.json) lets a YAML editor complete and check the mapping.

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

Datasets and licences: La Haute Borne (ENGIE, Etalab open licence as stated by ENGIE, not re-verified; read from the copy shipped with OpenOA, BSD-3-Clause repository), Kelmarsh and Penmanshiel (Cubico Sustainable Investments, CC BY 4.0, doi 10.5281/zenodo.5841833 and 10.5281/zenodo.5946807), Hill of Towie (RES, CC BY 4.0,
doi 10.5281/zenodo.14870021), CARE to Compare (CC BY-SA 4.0, doi 10.5281/zenodo.15846963; the share-alike condition applies to the data and to datasets derived from it, not to this
package's code), NREL PVDAQ (CC BY 4.0, doi 10.25984/1846021). The code is MIT licensed.

## Citation

Cite the software through [CITATION.cff](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/CITATION.cff) (GitHub's "Cite this repository" reads it). A DOI is assigned by Zenodo when the first release is archived; until then cite the repository and the version.
