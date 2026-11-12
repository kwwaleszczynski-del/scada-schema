# Slices of real open files

A few rows of the published files, in their original formats, cut by `scripts/make_test_fixtures.py` so that the loaders are tested on real column
names, units and quirks without a download. Nothing here is modified except the row selection; the full datasets are not redistributed.

| Files | Source | Licence |
|---|---|---|
| `Kelmarsh_SCADA_2019_0.zip`, `Kelmarsh_WT_static.csv` | Kelmarsh wind farm data, Cubico Sustainable Investments, doi:10.5281/zenodo.5841833 (2 turbines, 24 ten-minute rows, 40 status rows) | CC BY 4.0 |
| `2021_subset.zip`, `Hill_of_Towie_*.csv`, `Hill_of_Towie_ShutdownDuration.zip` | Hill of Towie wind farm open dataset, RES, doi:10.5281/zenodo.14870021 (2 turbines, 12 ten-minute rows, 25 alarms) | CC BY 4.0 |
| `pvdaq/system_34/*.csv` | NREL PVDAQ public datasets, doi:10.25984/1846021, system 34, 3 to 5 August 2018 | CC BY 4.0 |
| `la_haute_borne.zip` | La Haute Borne wind farm data, ENGIE open data (copy shipped with OpenOA), 2 turbines, 29 to 30 March 2014 | Etalab open licence, as stated by ENGIE |

CARE to Compare (CC BY-SA 4.0) is deliberately not sliced here: the share-alike condition would extend to this repository's test data.
