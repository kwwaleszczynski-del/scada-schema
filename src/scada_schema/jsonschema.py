"""JSON Schema and a column list of the standard, for tools that are not written in Python and for editor completion of mapping files.

``scada-schema jsonschema --out schema`` writes three files; ``tests/test_docs.py`` fails when the committed ones differ:

* ``dataset-meta.schema.json``: the metadata stored in a Parquet file (the ``scada_schema`` key);
* ``mapping.schema.json``: the YAML mapping of ``scada-schema import`` (point a YAML editor at it for completion and checks);
* ``columns.json``: every canonical column (signal, minimum, maximum and standard deviation variants) with type, unit, kind, devices and limits.

Usage:
    python -m scada_schema jsonschema --out schema
"""

from __future__ import annotations

import json
from pathlib import Path

from scada_schema.loaders.generic import ImportSpec
from scada_schema.model import SCHEMA_VERSION, DatasetMeta
from scada_schema.signals import SIGNALS

__all__ = ["build", "write"]


def columns() -> dict:
    cols = [{"name": "timestamp", "dtype": "datetime64[ns, UTC]", "required": True, "description": "start of the averaging interval, UTC"},
            {"name": "device_id", "dtype": "string", "required": True, "description": "device identifier, unique within the dataset"}]
    for s in SIGNALS.values():
        dtype = "string" if s.kind in ("text", "category") else "float64" if s.kind in ("sum", "counter") else "float32"
        base = {"unit": s.unit or None, "kind": s.kind, "dtype": dtype, "profiles": list(s.profiles), "devices": list(s.devices), "min": s.lo, "max": s.hi,
                "required": False}
        cols.append({"name": s.name, **base, "description": s.description})
        if s.stats:
            for st in ("min", "max", "std"):
                cols.append({"name": f"{s.name}__{st}", **base, "kind": f"{st} of the interval", "description": f"{st} of the interval: {s.description}"})
    return {"schema_version": SCHEMA_VERSION, "columns": cols}


def build() -> dict[str, str]:
    """File name to the text of the file."""
    meta = DatasetMeta.model_json_schema()
    meta["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    mapping = ImportSpec.model_json_schema()
    mapping["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    dump = lambda d: json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False) + "\n"  # noqa: E731
    return {"dataset-meta.schema.json": dump(meta), "mapping.schema.json": dump(mapping), "columns.json": dump(columns())}


def write(out: str | Path) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in build().items():
        p = out / name
        p.write_text(text, encoding="utf-8", newline="\n")
        paths.append(p)
    return paths
