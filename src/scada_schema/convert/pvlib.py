"""Canonical PV table to [pvlib](https://pvlib-python.readthedocs.io) inputs: weather, location, array geometry, measured power.

pvlib works with instants and its own column names. The converter

* takes the weather from the met device (``met`` or ``plant``) of the table: ``ghi``, ``dni``, ``dhi``, ``poa_global`` (from ``poa``),
  ``temp_air`` (from ``t_amb``), ``wind_speed``, ``temp_module`` (from ``t_module``, the measured back-of-module value);
* indexes it by the **centre of the interval** (``at="center"``, the right instant for a mean over the interval; ``"start"`` and
  ``"end"`` are also available) in UTC;
* gives the location and the array geometry of an inverter (``surface_tilt``, ``surface_azimuth`` clockwise from north, 180 =
  south, ``pdc0`` in W from ``capacity_kwp``);
* returns the measured AC power of the inverter in kW on the same index, to compare with a model.

Units already agree (W/m2, degC, m/s); no conversion happens here.

Usage:
    inputs = to_pvlib(df, meta, inverter="INV")
    loc = pvlib.location.Location(**inputs.location)
    weather, p_meas = inputs.weather, inputs.p_ac_kw
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from scada_schema.model import DatasetMeta

__all__ = ["COLUMN_MAP", "PvlibInputs", "to_pvlib"]

COLUMN_MAP = {"ghi": "ghi", "dni": "dni", "dhi": "dhi", "poa": "poa_global", "t_amb": "temp_air", "wind_speed": "wind_speed",
              "t_module": "temp_module"}
_SHIFT = {"start": 0.0, "center": 0.5, "end": 1.0}


@dataclass
class PvlibInputs:
    weather: pd.DataFrame
    location: dict
    system: dict
    p_ac_kw: pd.Series
    notes: list[str]


def to_pvlib(df: pd.DataFrame, meta: DatasetMeta, inverter: str | None = None, met: str | None = None, at: str = "center") -> PvlibInputs:
    """pvlib inputs for one inverter of a canonical PV table.

    Parameters
    ----------
    df, meta
        Canonical PV table and its metadata.
    inverter : str, optional
        Inverter id (the only one by default).
    met : str, optional
        Met device id (the only ``met`` or ``plant`` device by default).
    at : {"center", "start", "end"}
        Which instant of the interval indexes the result.
    """
    if meta.profile != "pv":
        raise ValueError("to_pvlib needs a PV table")
    if at not in _SHIFT:
        raise ValueError(f"at must be one of {list(_SHIFT)}")
    invs = [d for d in meta.devices if d.type == "inverter"]
    mets = [d for d in meta.devices if d.type in ("met", "plant")]
    inv = _pick(invs, inverter, "inverter")
    met_dev = _pick(mets, met, "met")
    shift = pd.Timedelta(meta.step * _SHIFT[at])
    w = df[df["device_id"] == met_dev.id].set_index("timestamp")
    weather = pd.DataFrame(index=w.index + shift)
    for src, dst in COLUMN_MAP.items():
        if src in w.columns and w[src].notna().any():
            weather[dst] = w[src].to_numpy(dtype="float64", na_value=np.nan)
    weather.index.name = "time"
    p = df[df["device_id"] == inv.id].set_index("timestamp")
    if "p_ac" not in p.columns:
        raise ValueError(f"inverter {inv.id} has no p_ac")
    p_ac = pd.Series(p["p_ac"].to_numpy(dtype="float64", na_value=np.nan), index=p.index + shift, name="p_ac_kw")
    p_ac.index.name = "time"
    loc_src = inv if inv.lat is not None else met_dev
    if loc_src.lat is None or loc_src.lon is None:
        raise ValueError("pvlib needs a latitude and longitude on the inverter or the met device")
    location = {"latitude": loc_src.lat, "longitude": loc_src.lon, "tz": "UTC"}
    system = {k: v for k, v in {"surface_tilt": inv.tilt_deg, "surface_azimuth": inv.azimuth_deg,
                                "pdc0": None if inv.capacity_kwp is None else inv.capacity_kwp * 1000.0}.items() if v is not None}
    notes = [f"index: {at} of the interval (UTC)"]
    return PvlibInputs(weather, location, system, p_ac, notes)


def _pick(devs, wanted, kind):
    if wanted is not None:
        for d in devs:
            if d.id == wanted:
                return d
        raise KeyError(f"no {kind} device {wanted!r}")
    if len(devs) != 1:
        raise ValueError(f"the table has {len(devs)} {kind} devices; name one")
    return devs[0]
