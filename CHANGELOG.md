# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

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
