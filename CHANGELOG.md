# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

## [0.1.1] - 2026-11-12

Corrections found by an independent review of 0.1.0. Several numbers in the README and on the project page change because of them (the table counts, the availability
figures, the La Haute Borne findings); the README is regenerated from `results/study.json`.

### Fixed

- **CARE:** active and reactive power were stored as `p_ac` in kW and `q_ac` in kvar, but the benchmark scales power-related data for anonymisation and publishes no rated power
  (values around 1, not kW). They are now stored as `p_ac_rel` and `q_ac_rel` (new dimensionless signals), the conversions say so, the wind-up and OpenOA converters refuse such a table,
  and a turbine in the wind profile may carry either `p_ac` or `p_ac_rel`.
- **PVDAQ:** the hectowatt-to-watt decision needed a step in the record. A record entirely after the switch, or a window of about two weeks around it, was read as hectowatts and the
  power came out 100 times too large. The loader now also decides from the level against the array's rating (checked on windows before, around and after the switch for all four
  systems), and the metadata give the start of the first interval in watts and the resulting 99th percentile against the rating.
- **La Haute Borne:** 0.1.0 called the time "exact". The printed offsets follow daylight saving time but the series is a clock on standard time (UTC+1) labelled as if it followed it: 48 repeated
  spring stamps were dropped without a trace and one hour is missing in each autumn. The loader now reconstructs the clock by default (`time="reconstructed"`; UTC+1 is an inference),
  drops the 96 spring rows whose hour cannot be told, records what it dropped, and keeps the old reading as `time="as_stamped"`.
- **Importer (`scada-schema import`) with `code_table`:** codes the table lists without a category (information, warnings) were left empty and counted as forced outages by the availability
  calculation, so a healthy turbine had an availability of zero. They now read as `full_performance`; `availability_from_table` leaves intervals without a category out and says how many.
  A missing device column is a clear error instead of a `KeyError`.
- **`validate`:** with a `meta`, signals that do not belong to the profile are now an error (`signal_not_in_profile`); `wind_speed` belongs to the PV profile too (met stations).
- **Resampling:** sums over partly covered intervals are recorded (and can be scaled with `scale_sums`), counters and instantaneous values need the last source interval, `wind_dir_rel` stays in
  -180 to 180, opposite directions give no mean direction instead of 180 degrees, extension columns are dropped with a note. The docstring no longer promises more than the code does.
- **pvlib converter:** the measured module temperature is `module_temperature`, the name `ModelChain` reads, instead of `temp_module`, which it ignored; the parity check now covers it.
- **States:** intervals whose row holds no measurement are `no_info` instead of `full_performance`.
- Smaller: the wind-up turbine-type docstring (10 % of rated power, as the code); Hill of Towie's set point is the value at the end of the interval and the metadata say so; the PVDAQ unit
  note gives interval starts, not end stamps.

### Changed

- The study does not count the demonstration tables (PVDAQ read as hectowatts, La Haute Borne as stamped) in its totals, shows the rows each loader removed, and says that schema validity is
  structural. Loaders record the repeated rows they drop (`Loaded.removed` and the metadata).

### Added

- Tests on slices of the real files (Kelmarsh, Hill of Towie, PVDAQ, La Haute Borne; `tests/data/real`, with sources and licences), tests for each fix above, and a reproducibility test of the study
  numbers for every dataset (needs the full files).

## [0.1.0] - 2026-11-06

First release.

### Added

- The schema (version 1.0, wind and PV profiles), `SPEC.md` generated from the code, JSON Schema files and a column list (`schema/`).
- Loaders for Kelmarsh, Penmanshiel, Hill of Towie, La Haute Borne, CARE to Compare and NREL PVDAQ (with `fetch` and checksum verification).
- A loader driven by a YAML mapping (`scada-schema import`) and resampling to a coarser interval (`scada-schema resample`).
- 15 quality checks with a self-contained HTML report.
- IEC 61400-26 state mapping (code tables, label tables, event projection) and time-based availability through res-asset-kpi.
- Converters to wind-up, OpenOA and pvlib with parity checks.
- Command line interface, the study that produces every number of the README, tests on Linux and Windows, CITATION.cff and Zenodo metadata, and a release workflow for PyPI Trusted Publishing (disabled until `PYPI_PUBLISH` is set).

[0.1.0]: https://github.com/kwwaleszczynski-del/scada-schema/releases/tag/v0.1.0
