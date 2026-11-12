"""Canonical wind table to the input of [wind-up](https://github.com/resgroup/wind-up) (RES, v0.5; the input of v1 is not yet fixed).

wind-up reads a SCADA frame indexed by ``TimeStamp_StartFormat`` (UTC, start of the interval) with a ``TurbineName`` column
and the columns of ``wind_up.constants.DataColumns``; the turbine types, the rotor and the permitted pitch and generator speed
ranges go into its ``WindUpConfig``. This module produces both from a canonical table.

============================  ======================================  =================================================
wind-up column                canonical signal                        note
============================  ======================================  =================================================
``ActivePowerMean``           ``p_ac``                                kW
``ActivePowerSD``             ``p_ac__std``                           kW
``ReactivePowerMean``         ``q_ac``                                kvar
``WindSpeedMean``             ``wind_speed``                          m/s
``WindSpeedSD``               ``wind_speed__std``                     m/s
``YawAngleMean``              ``nacelle_dir``                         deg from north (wind-up calls the nacelle position yaw)
``YawAngleMin``, ``Max``      ``nacelle_dir__min``, ``__max``         deg
``PitchAngleMean``            ``pitch``                               deg
``GenRpmMean``                ``gen_speed``                           rpm; ``rotor_speed`` is not a substitute
``AmbientTemp``               ``t_amb``                               degC
``ShutdownDuration``          ``shutdown_s``                          s of the interval; see ``shutdown_from``
============================  ======================================  =================================================

wind-up filters downtime on ``ShutdownDuration``. Where the source has none, the converter derives it from ``iec_category``
(a whole interval, 600 s, in the categories listed in ``STOP_CATEGORIES``) or, with neither, sets zero and says so in the
returned notes: with zero the downtime filter removes nothing and wind-up's other filters (power, wind speed, pitch, rpm)
carry the cleaning.

Usage:
    scada, notes = to_windup_scada(df, meta)
    types = windup_turbine_types(df, meta, turbine_type="MM92")      # TurbineType kwargs, ranges estimated from the data
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scada_schema.model import DatasetMeta

__all__ = ["COLUMN_MAP", "STOP_CATEGORIES", "to_multiindex", "to_windup_scada", "windup_config", "windup_turbine_types", "windup_turbines"]

TIMESTAMP_COL = "TimeStamp_StartFormat"
COLUMN_MAP = {
    "p_ac": "ActivePowerMean", "p_ac__std": "ActivePowerSD", "q_ac": "ReactivePowerMean",
    "wind_speed": "WindSpeedMean", "wind_speed__std": "WindSpeedSD",
    "nacelle_dir": "YawAngleMean", "nacelle_dir__min": "YawAngleMin", "nacelle_dir__max": "YawAngleMax",
    "pitch": "PitchAngleMean", "gen_speed": "GenRpmMean", "t_amb": "AmbientTemp",
}
STOP_CATEGORIES = ("technical_standby", "environmental", "requested", "electrical", "scheduled_maintenance",
                   "planned_corrective", "forced_outage")
REQUIRED = ("p_ac", "wind_speed", "nacelle_dir", "pitch", "gen_speed")


def to_windup_scada(df: pd.DataFrame, meta: DatasetMeta, turbines: list[str] | None = None,
                    shutdown_from: str = "auto") -> tuple[pd.DataFrame, list[str]]:
    """The SCADA frame wind-up expects, and notes on what was derived or missing.

    Parameters
    ----------
    df : pandas.DataFrame
        Canonical wind table.
    meta : DatasetMeta
    turbines : list of str, optional
        Turbine ids to include (all turbines by default).
    shutdown_from : {"auto", "column", "category", "zero"}
        Source of ``ShutdownDuration``: the ``shutdown_s`` column, the ``iec_category`` column, or zero; ``auto`` takes the
        first that exists.

    Returns
    -------
    (pandas.DataFrame, list of str)
        The frame (index ``TimeStamp_StartFormat``, column ``TurbineName`` first) and the notes.

    Raises
    ------
    ValueError
        If a signal wind-up needs (``REQUIRED``) is absent.
    """
    if meta.profile != "wind":
        raise ValueError("wind-up needs a wind table")
    miss = [c for c in REQUIRED if c not in df.columns or not df[c].notna().any()]
    if "p_ac" in miss and "p_ac_rel" in df.columns:
        raise ValueError("wind-up needs the active power in kW (p_ac); this table has only p_ac_rel, a number scaled by the source, and the rated power is not known")
    if miss:
        raise ValueError(f"wind-up needs {list(REQUIRED)}; missing {miss}"
                         + (" (rotor_speed is not generator speed; supply gen_speed)" if "gen_speed" in miss else ""))
    ids = turbines or [d.id for d in meta.devices if d.type == "turbine"]
    sub = df[df["device_id"].isin(ids)]
    out = pd.DataFrame({"TurbineName": sub["device_id"].to_numpy()}, index=pd.DatetimeIndex(sub["timestamp"], name=TIMESTAMP_COL))
    for src, dst in COLUMN_MAP.items():
        if src in sub.columns and sub[src].notna().any():
            out[dst] = sub[src].to_numpy(dtype="float64", na_value=np.nan)
    notes = []
    how = shutdown_from
    if how == "auto":
        how = "column" if "shutdown_s" in sub.columns and sub["shutdown_s"].notna().any() else \
            "category" if "iec_category" in sub.columns and sub["iec_category"].notna().any() else "zero"
    if how == "column":
        out["ShutdownDuration"] = sub["shutdown_s"].to_numpy(dtype="float64", na_value=np.nan)
    elif how == "category":
        stop = sub["iec_category"].isin(STOP_CATEGORIES).to_numpy()
        out["ShutdownDuration"] = np.where(stop, float(meta.step.total_seconds()), 0.0)
        notes.append(f"ShutdownDuration derived from iec_category: {meta.interval} for {', '.join(STOP_CATEGORIES)}, else 0")
    else:
        out["ShutdownDuration"] = 0.0
        notes.append("ShutdownDuration is 0: the source has neither shutdown_s nor iec_category, so wind-up's downtime filter removes nothing")
    return out, notes


def windup_turbine_types(df: pd.DataFrame, meta: DatasetMeta, turbine_type: str, cutout_ws_mps: float = 25.0) -> dict:
    """Keyword arguments for wind-up's ``TurbineType``, with the data-dependent ranges estimated from the table.

    The rated power and rotor diameter come from the first turbine of the metadata. ``normal_operation_pitch_range`` and
    ``normal_operation_genrpm_range`` are **estimates**: the 1st to 99th percentile of pitch and generator speed in the
    intervals above 10 % of rated power. Check them against the turbine's documentation before using
    them in an analysis.
    """
    turbines = [d for d in meta.devices if d.type == "turbine"]
    first = turbines[0]
    if first.rated_kw is None or first.rotor_diameter_m is None:
        raise ValueError("wind-up needs rated_kw and rotor_diameter_m of the turbines in the metadata")
    ids = {d.id for d in turbines}
    sub = df[df["device_id"].isin(ids)]
    run = sub[sub["p_ac"] > 0.1 * first.rated_kw]
    pitch = run["pitch"].dropna() if "pitch" in run else pd.Series(dtype=float)
    rpm = run["gen_speed"].dropna() if "gen_speed" in run else pd.Series(dtype=float)
    if len(pitch) < 100 or len(rpm) < 100:
        raise ValueError("too few operating intervals with pitch and gen_speed to estimate the operating ranges")
    return {
        "turbine_type": turbine_type, "rotor_diameter_m": float(first.rotor_diameter_m), "rated_power_kw": float(first.rated_kw),
        "cutout_ws_mps": cutout_ws_mps,
        "normal_operation_pitch_range": (float(np.floor(pitch.quantile(0.01))), float(np.ceil(pitch.quantile(0.99)))),
        "normal_operation_genrpm_range": (float(np.floor(rpm.quantile(0.01))), float(np.ceil(rpm.quantile(0.99)))),
    }


def windup_turbines(meta: DatasetMeta, turbine_type_kwargs: dict) -> list[dict]:
    """``Turbine`` kwargs (name, type, latitude, longitude) for wind-up's ``Asset.wtgs``, one per turbine of the metadata.

    wind-up's names are at least two characters long.
    """
    return [{"name": d.id, "turbine_type": dict(turbine_type_kwargs),
             **({"latitude": d.lat} if d.lat is not None else {}), **({"longitude": d.lon} if d.lon is not None else {})}
            for d in meta.devices if d.type == "turbine"]


def to_multiindex(scada: pd.DataFrame) -> pd.DataFrame:
    """The ``(TurbineName, TimeStamp_StartFormat)`` multi-index frame that wind-up's filters take (what its own loaders build)."""
    return scada.set_index("TurbineName", append=True).swaplevel(axis=0).sort_index()


def windup_config(df: pd.DataFrame, meta: DatasetMeta, turbine_type: str, out_dir, test_turbines: list[str],
                  ref_turbines: list[str] | None = None, **overrides):
    """A minimal ``WindUpConfig`` for the table (needs wind-up installed).

    The analysis periods are **placeholders** that cover the table (the first half is "pre", the second half "post"); they let
    wind-up's filters and plots run for a data-quality pass. Replace them, the upgrade date and the turbine type's
    estimated ranges before a real assessment. ``overrides`` are passed on to ``WindUpConfig``.
    """
    from wind_up.models import Asset, Turbine, WindUpConfig

    kwargs = windup_turbine_types(df, meta, turbine_type)
    wtgs = {t["name"]: Turbine(**t) for t in windup_turbines(meta, kwargs)}
    t0, t1 = df["timestamp"].min(), df["timestamp"].max()
    mid = t0 + (t1 - t0) / 2
    cfg = dict(
        assessment_name=meta.name if len(meta.name) > 1 else f"{meta.name}_", out_dir=out_dir,
        test_wtgs=[wtgs[n] for n in test_turbines], ref_wtgs=[wtgs[n] for n in (ref_turbines or []) if n in wtgs],
        upgrade_first_dt_utc_start=mid, analysis_first_dt_utc_start=t0, analysis_last_dt_utc_start=t1,
        lt_first_dt_utc_start=t0, lt_last_dt_utc_start=t1, detrend_first_dt_utc_start=t0, detrend_last_dt_utc_start=t1,
        years_for_lt_distribution=1, years_for_detrend=1, ws_bin_width=1.0, years_offset_for_pre_period=1,
        asset=Asset(name=meta.name if len(meta.name) > 1 else f"{meta.name}_", wtgs=list(wtgs.values())),
        prepost={"pre_first_dt_utc_start": t0, "pre_last_dt_utc_start": mid - meta.step,
                 "post_first_dt_utc_start": mid, "post_last_dt_utc_start": t1},
    )
    cfg.update(overrides)
    return WindUpConfig(**cfg)
