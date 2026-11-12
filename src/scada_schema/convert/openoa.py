"""Canonical wind table to an [OpenOA](https://github.com/NREL/OpenOA) ``PlantData`` (v3).

OpenOA's SCADA schema names the columns ``time``, ``asset_id``, ``WTUR_W`` (kW), ``WMET_HorWdSpd`` (m/s),
``WMET_HorWdDir`` (deg), ``WMET_HorWdDirRel`` (deg), ``WTUR_TurSt`` (status, text), ``WROT_BlPthAngVal`` (deg) and
``WMET_EnvTmp`` (degC); the asset table has one row per turbine (``asset_id``, ``latitude``, ``longitude``,
``rated_power`` in kW, ``hub_height``, ``rotor_diameter``, ``elevation``, ``type``).

============================  ==========================  ==============================================
OpenOA column                 canonical signal            note
============================  ==========================  ==============================================
``WTUR_W``                    ``p_ac``                    kW
``WMET_HorWdSpd``             ``wind_speed``              m/s
``WMET_HorWdDir``             ``wind_dir``                absolute, from; absent if the source has none
``WMET_HorWdDirRel``          ``wind_dir_rel``
``WTUR_TurSt``                ``status_code``             text
``WROT_BlPthAngVal``          ``pitch``                   deg
``WMET_EnvTmp``               ``t_amb``                   degC
============================  ==========================  ==============================================

The ``time`` column is the canonical time stamp as naive UTC (OpenOA's schema does not state which instant of the interval
it marks; the canonical table marks the start). Elevation is not part of the schema and is left empty unless given.

Usage:
    plant = to_openoa(df, meta)         # needs openoa
    plant.scada, plant.asset
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scada_schema.model import DatasetMeta

__all__ = ["COLUMN_MAP", "to_openoa", "to_openoa_frames"]

COLUMN_MAP = {
    "p_ac": "WTUR_W", "wind_speed": "WMET_HorWdSpd", "wind_dir": "WMET_HorWdDir", "wind_dir_rel": "WMET_HorWdDirRel",
    "status_code": "WTUR_TurSt", "pitch": "WROT_BlPthAngVal", "t_amb": "WMET_EnvTmp",
}


def to_openoa_frames(df: pd.DataFrame, meta: DatasetMeta, turbines: list[str] | None = None,
                     elevation_m: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The SCADA and asset frames of OpenOA, without needing OpenOA installed."""
    if meta.profile != "wind":
        raise ValueError("OpenOA's PlantData needs a wind table")
    if "p_ac_rel" in df.columns and "p_ac" not in df.columns:
        raise ValueError("OpenOA's SCADA schema needs the active power in kW (p_ac); this table has only p_ac_rel, a number scaled by the source")
    for need in ("p_ac", "wind_speed"):
        if need not in df.columns or not df[need].notna().any():
            raise ValueError(f"OpenOA's SCADA schema needs {need}")
    devs = [d for d in meta.devices if d.type == "turbine" and (turbines is None or d.id in turbines)]
    ids = [d.id for d in devs]
    missing = [d.id for d in devs if d.lat is None or d.lon is None or d.rated_kw is None]
    if missing:
        raise ValueError(f"OpenOA's asset table needs latitude, longitude and rated power; missing for {missing[:5]}")
    sub = df[df["device_id"].isin(ids)]
    scada = pd.DataFrame({"time": sub["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(), "asset_id": sub["device_id"].to_numpy()})
    for src, dst in COLUMN_MAP.items():
        if src in sub.columns and sub[src].notna().any():
            scada[dst] = sub[src].to_numpy(dtype="float64", na_value=np.nan) if src != "status_code" else sub[src].astype(object).to_numpy()
    if "WTUR_TurSt" in scada:
        scada["WTUR_TurSt"] = scada["WTUR_TurSt"].astype(object).where(scada["WTUR_TurSt"].notna(), None)
    asset = pd.DataFrame({
        "asset_id": ids, "latitude": [d.lat for d in devs], "longitude": [d.lon for d in devs],
        "rated_power": [d.rated_kw for d in devs], "hub_height": [d.hub_height_m if d.hub_height_m else np.nan for d in devs],
        "rotor_diameter": [d.rotor_diameter_m if d.rotor_diameter_m else np.nan for d in devs],
        "elevation": [elevation_m if elevation_m is not None else np.nan] * len(devs), "type": "turbine",
    })
    return scada, asset


def to_openoa(df: pd.DataFrame, meta: DatasetMeta, turbines: list[str] | None = None, elevation_m: float | None = None,
              analysis_type: list[str] | None = None):
    """An OpenOA ``PlantData`` from a canonical wind table (needs the ``openoa`` extra)."""
    from openoa import PlantData

    scada, asset = to_openoa_frames(df, meta, turbines, elevation_m)
    md = {"latitude": float(asset["latitude"].mean()), "longitude": float(asset["longitude"].mean()),
          "capacity": float(asset["rated_power"].sum() / 1000.0), "scada": {"frequency": meta.interval}, "asset": {}}
    return PlantData(analysis_type=analysis_type, metadata=md, scada=scada, asset=asset)
