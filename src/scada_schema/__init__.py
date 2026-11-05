"""An open data standard for wind and PV SCADA.

* [`signals`][scada_schema.signals] - the registry of canonical signals (names, units, conventions, limits);
* [`model`][scada_schema.model] - dataset metadata and profiles (wind, PV);
* [`validate`][scada_schema.validate] - is a table in the schema?
* [`io`][scada_schema.io] - self-describing Parquet;
* [`loaders`][scada_schema.loaders] - Kelmarsh, Penmanshiel, Hill of Towie, La Haute Borne, CARE and PVDAQ to the schema;
* [`qc`][scada_schema.qc] - data quality checks and an HTML report;
* [`states`][scada_schema.states] - status codes to IEC 61400-26 categories;
* [`convert`][scada_schema.convert] - wind-up, OpenOA and pvlib.
"""

from scada_schema._version import __version__
from scada_schema.io import finalise, read_meta, read_parquet, write_parquet
from scada_schema.model import DatasetMeta, Device, Source
from scada_schema.signals import SIGNALS
from scada_schema.validate import SchemaError, validate

__all__ = [
    "SIGNALS",
    "DatasetMeta",
    "Device",
    "SchemaError",
    "Source",
    "__version__",
    "finalise",
    "read_meta",
    "read_parquet",
    "validate",
    "write_parquet",
]
