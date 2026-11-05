"""Where the raw files of an open dataset live: an explicit folder, ``$SCADA_SCHEMA_DATA/<name>`` or ``./data/<name>``."""

from __future__ import annotations

import os
from pathlib import Path


def raw_dir(name: str, explicit: str | Path | None = None) -> Path:
    """Folder that holds (or will hold) the raw files of dataset ``name``.

    Order: ``explicit``; the environment variable ``SCADA_SCHEMA_<NAME>_DIR``; ``$SCADA_SCHEMA_DATA/<name>``;
    ``./data/<name>``.
    """
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(f"SCADA_SCHEMA_{name.upper()}_DIR")
    if env:
        return Path(env)
    root = os.environ.get("SCADA_SCHEMA_DATA")
    return Path(root) / name if root else Path("data") / name
