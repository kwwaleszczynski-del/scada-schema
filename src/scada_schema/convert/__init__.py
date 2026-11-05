"""Converters from the canonical table to the inputs of wind-up, OpenOA and pvlib.

Each module imports its target lazily, so the package works without them; install ``scada-schema[converters]``.
"""

from scada_schema.convert import openoa, pvlib, windup

__all__ = ["openoa", "pvlib", "windup"]
