"""Loaders for open SCADA datasets: each reads the original files and returns a canonical table, its metadata and the
event log.

=============  =======  ===============================================  =============
name           profile  source                                           licence
=============  =======  ===============================================  =============
kelmarsh       wind     Zenodo 5841833 (Cubico)                          CC BY 4.0
penmanshiel    wind     Zenodo 5946807 (Cubico)                          CC BY 4.0
hill_of_towie  wind     Zenodo 14870021 (RES)                            CC BY 4.0
la_haute_borne wind     ENGIE open data, copy in OpenOA                  Etalab (as stated)
care           wind     Zenodo 15846963 (CARE to Compare)                CC BY-SA 4.0
pvdaq          pv       NREL OEDI doi:10.25984/1846021                   CC BY 4.0
=============  =======  ===============================================  =============

Every module has ``fetch`` (download from the original source with checksums) and ``load`` (read to a
[`Loaded`][scada_schema.loaders._common.Loaded]). Large archives are never copied into this repository.
"""

from scada_schema.loaders import care, hill_of_towie, kelmarsh, la_haute_borne, penmanshiel, pvdaq
from scada_schema.loaders._common import EVENT_COLUMNS, Loaded

DATASETS = {"kelmarsh": kelmarsh, "penmanshiel": penmanshiel, "hill_of_towie": hill_of_towie, "la_haute_borne": la_haute_borne, "care": care, "pvdaq": pvdaq}

__all__ = ["DATASETS", "EVENT_COLUMNS", "Loaded", "care", "hill_of_towie", "kelmarsh", "la_haute_borne", "penmanshiel", "pvdaq"]
