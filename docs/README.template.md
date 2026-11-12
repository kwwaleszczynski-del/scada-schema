# scada-schema: an open data standard for wind and PV SCADA

{{BADGES}}

**Project page with the results: [kwwaleszczynski-del.github.io/scada-schema](https://kwwaleszczynski-del.github.io/scada-schema/)**
- [specification](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/SPEC.md) - [example quality report](https://kwwaleszczynski-del.github.io/scada-schema/qc/pvdaq_34_raw_units.html).

SCADA exports from two wind farms never look alike: columns named after a vendor's tag list, power in hectowatts or watts, time in local
time or at the end of the interval, a status code that only the turbine manual explains. Every analysis tool then starts with its own
cleaning script. This package fixes **one table layout** (names, units, sign and angle conventions, time zone and interval, device types) for
wind turbines and PV plants, reads **open datasets from {{n_sources}} sources** into it from their original files, checks the data, maps status codes to
**IEC 61400-26 categories** and converts the table to the inputs of **wind-up, OpenOA and pvlib**, with a test that each tool gives the same result
on the library's input as on a hand-built one.

It is a proposal, not a standard of any body, and working material: the formulas and category names follow public descriptions, and nothing
here is a certified implementation of an IEC standard.

## What is in it

1. **Schema** ([SPEC.md](https://github.com/kwwaleszczynski-del/scada-schema/blob/main/SPEC.md), generated from the code): {{n_signals}} canonical signals ({{n_wind_signals}} in the wind profile, {{n_pv_signals}} in the PV profile),
   one row per device and interval, `timestamp` in UTC at the start of the interval, canonical units, power positive into the grid, compass
   angles clockwise from true north. Minimum, maximum and standard deviation columns where a source has them. The metadata (profile, interval, the
   time zone and label of the *source*, devices with ratings and positions, licence, every conversion the loader made) are stored inside the
   Parquet file. pydantic describes the metadata; the table is validated by a vectorised check that reports every violation with a count (pandera
   stops at the first failure and adds a dependency, and a per-row quality report is needed anyway).
2. **Loaders** for Kelmarsh, Penmanshiel, Hill of Towie, La Haute Borne, CARE to Compare and NREL PVDAQ. They read the original archives (Zenodo, OEDI) and
   `fetch` downloads them with checksum verification; nothing large is in this repository. Hill of Towie's 1.4 GB yearly archives are read with range
   requests, so only the needed tables are transferred.
3. **Quality control** with an HTML report: {{n_checks}} checks - duplicate and unordered time stamps, time jumps, gaps, daylight saving artefacts, missing values,
   physical limits, flat lines, spikes, power-curve outliers, wind direction (vane, nacelle and north offsets between turbines), energy counters and interval energy,
   icing, and units (order of magnitude and a switch of unit inside a record). The report is one file without scripts.
4. **IEC 61400-26 states**: code tables for the Senvion MM series ({{code_table_senvion}} codes) and the Siemens SWT 2.3 ({{code_table_siemens}} codes) as logged in the open
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

{{n_datasets}} tables from {{n_sources}} sources ({{rows}} rows, {{n_devices}} devices) were loaded from the original files, plus {{n_demo}} demonstration tables that are not counted
(PVDAQ system 34 read as hectowatts throughout, and La Haute Borne with its printed time stamps, to show what the checks find). The schema accepts {{schema_valid}} of the counted tables.

**"Valid" means that the structure follows the schema**: column names, types, time stamps on the interval grid, unique and sorted keys, signals of the profile and of the device type,
required signals. It says nothing about units or values: a demonstration table whose power is a hundredfold too large is valid by construction, and so is CARE's scaled power (stored under its own
signal name, see below). The quality checks look at the values. The `duplicates` and `ordering` checks find nothing in the table below because the loaders sort the rows and drop repeated ones
before the checks run; {{removed_dups}} repeated rows and {{removed_amb}} rows with an ambiguous time stamp were removed in total (column "Removed by the loader").

{{TABLE_DATASETS}}

Number of devices with at least one finding, by check (the quality reports list every finding with its longest episodes):

{{TABLE_CHECKS}}

What the checks found on real data:

- **A unit switch is found where the dataset documents it.** PVDAQ's power channels change from hectowatts to watts in August 2018. Read as hectowatts
  throughout, system 34 shows a step of a factor of {{pvdaq_switch_factor}} in power over irradiance on {{pvdaq_switch_date_qc}}; the loader finds the same switch from the data
  (`{{pvdaq_loader_note}}`), and the check on the corrected table reports {{pvdaq_fixed_switch_findings}} switches.
- **North offsets.** On Hill of Towie the nacelle position of {{hot_north_flagged}} of {{hot_north_turbines}} turbines is more than 10 degrees off the fleet mean direction;
  the largest, {{hot_north_dev}}, is {{hot_north_max}} degrees off. On Kelmarsh the largest offset is {{kel_north_max}} degrees.
- **The La Haute Borne clock is not what its stamps say.** The printed UTC offsets follow French daylight saving time, but the series does not. Each turbine has {{lhb_rows}} rows, exactly
  {{lhb_days}} complete days of ten-minute intervals; the hour of the daily temperature maximum is the same on the wall clock on both sides of the spring and autumn changes ({{lhb_wall}}) but moves by about an
  hour in "UTC" as stamped ({{lhb_utc}}); at the two spring changes {{lhb_repeated}} stamps per turbine occur twice and at the autumn change one hour is missing. That is a clock that ran on standard
  time (UTC+1) all year and was labelled as if it had followed daylight saving time. The loader reads it that way by default (`time="reconstructed"`). Two limits: UTC+1 is an inference (it is the
  offset the file prints in winter, and the data cannot tell it from UTC+0), and the repeated spring stamps cannot be assigned to their hours (the continuity of temperature, wind speed, power
  and yaw gives a clear winner in {{lhb_clear}} of {{lhb_cases}} cases, and the file order is no guide), so {{lhb_dropped}} rows are dropped and the metadata say so. With `time="as_stamped"` the
  `dst` check, run against Europe/Paris, finds {{lhb_dst_hit}} of {{lhb_dst_total}} changes on every turbine (the demonstration table).
- **CARE's power is not in kW.** The feature descriptions say kW and kvar, but the benchmark scales power-related data for anonymisation (Zenodo description of the record) and publishes no
  rated power. The loader stores active and reactive power as `p_ac_rel` and `q_ac_rel`, dimensionless, never as kW; the wind-up and OpenOA converters refuse such a table, and the checks that
  need a power use it as it is without a rating.
- **PVDAQ's unit is decided without a step too.** A record entirely after the switch (or a short window around it) has no step in the power over irradiance. The loader then compares the daily 99th
  percentile of power, read as hectowatts, with the array's rating: more than five times the rating means watts. This was run on windows before, around and after the switch for all four systems.
- **Time elsewhere.** The three UK farms are checked against Europe/London (UTC time stamps, no artefact found); for the others the check does not apply (fixed offset or anonymised time).
  The table above shows what each dataset triggered.

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

{{INSTALL}}

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
- The hand-built inputs of the parity checks are written independently of the library's mapping code, but from the same documentation: they would not catch a column or unit that both
  readings misunderstand. The loaders are tested in CI on slices of the real files (Kelmarsh, Hill of Towie, PVDAQ, La Haute Borne; CARE is not sliced because of its share-alike licence)
  and on synthetic files in the original formats; the numbers of this README come from the full files, which CI does not have.
- The code tables are written from the public code texts of the open datasets; they are not OEM documents. IEC categories are the top levels of the information model, not the whole tree.
- The quality thresholds are defaults chosen to be conservative; they were tuned on these datasets and are not validated on others. Findings describe data, not the condition of a sensor.
- CARE's sensor names are anonymised; the sensor tables of farms A, B and C cover the signals listed in `loaders/care.py` and each entry is checked against the benchmark's own
  description when loading. CARE time stamps are anonymised, so checks that need real time do not apply to it.
- Hill of Towie's SCADA tables used here have no absolute wind direction; only the nacelle position is mapped.
- La Haute Borne's time: see above; the UTC+1 reading is an inference.
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

{{CITATION}}
