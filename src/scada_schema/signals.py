"""The registry of canonical signals: name, unit, meaning, kind, where it may appear and its physical limits.

Everything else (profiles, validation, loaders, quality control, converters) reads this table, so a signal is
defined once. Columns of a canonical table are the signal names; the minimum, maximum and standard deviation of an
interval, where a source logs them, are stored as ``<signal>__min``, ``<signal>__max`` and ``<signal>__std``.

Conventions that apply to every signal:

* a value is the **mean over the interval** unless ``kind`` says otherwise;
* power and energy flow **out of the asset into the grid are positive** (generator convention); consumption of
  the asset (auxiliary load, night-time inverter draw) is negative;
* angles are **degrees**, compass angles run clockwise from **true north** and give the direction the wind
  comes **from** (meteorological convention);
* temperatures are degrees Celsius, pressure is Pa, speeds are m/s.

``lo`` and ``hi`` are *hard* physical limits in the canonical unit: wide enough that no working sensor leaves them,
so a value outside points to a unit or scaling error. They are not a sensor-health judgement; tighten them per
site in the quality-control configuration.

Usage:
    from scada_schema.signals import SIGNALS, stat_column
    SIGNALS["p_ac"].unit            # 'kW'
    stat_column("wind_speed", "std")  # 'wind_speed__std'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "KINDS",
    "SIGNALS",
    "STATS",
    "Signal",
    "base_signal",
    "split_column",
    "stat_column",
]

Kind = Literal["mean", "circular", "sum", "counter", "instant", "category", "text"]
KINDS = ("mean", "circular", "sum", "counter", "instant", "category", "text")
STATS = ("min", "max", "std")

WIND, PV = "wind", "pv"


@dataclass(frozen=True)
class Signal:
    """One canonical signal.

    Attributes
    ----------
    name : str
        Column name.
    unit : str
        Canonical unit (empty for dimensionless or text signals).
    kind : str
        ``mean`` (interval average), ``circular`` (angle, averaged on the circle), ``sum`` (energy in the
        interval), ``counter`` (cumulative meter reading), ``instant`` (value at the end of the interval),
        ``category`` or ``text`` (state codes and labels).
    description : str
    profiles : tuple of str
        Profiles the signal belongs to (``wind``, ``pv``).
    devices : tuple of str
        Device types that carry it.
    lo, hi : float or None
        Hard physical limits in the canonical unit (inclusive); ``None`` is open.
    stats : bool
        Whether ``__min``, ``__max`` and ``__std`` columns are allowed.
    """

    name: str
    unit: str
    kind: Kind
    description: str
    profiles: tuple[str, ...]
    devices: tuple[str, ...]
    lo: float | None = None
    hi: float | None = None
    stats: bool = False


def _s(name, unit, kind, description, profiles, devices, lo=None, hi=None, stats=False) -> tuple[str, Signal]:
    return name, Signal(name, unit, kind, description, tuple(profiles), tuple(devices), lo, hi, stats)


_W, _P, _WP = (WIND,), (PV,), (WIND, PV)
_TUR, _INV, _STR, _MET = ("turbine",), ("inverter",), ("string",), ("met", "plant")

SIGNALS: dict[str, Signal] = dict(
    [
        # ------------------------------------------------------------------ electrical, wind and PV
        _s("p_ac", "kW", "mean", "active power at the AC terminals, positive = export", _WP, ("turbine", "inverter", "meter", "plant"),
           -20_000, 30_000, True),
        _s("q_ac", "kvar", "mean", "reactive power, positive = export (over-excited)", _WP, ("turbine", "inverter", "meter", "plant"),
           -20_000, 20_000, True),
        _s("power_factor", "1", "mean", "displacement power factor, signed like q_ac", _WP, ("turbine", "inverter", "meter", "plant"),
           -1.0, 1.0, True),
        _s("e_export", "kWh", "sum", "energy exported during the interval", _WP, ("turbine", "inverter", "meter", "plant"),
           -1_000, 100_000),
        _s("e_export_counter", "kWh", "counter", "cumulative export meter reading at the end of the interval", _WP,
           ("turbine", "inverter", "meter", "plant"), 0, None),
        _s("setpoint", "kW", "mean", "active power set point or limit sent to the device", _WP, ("turbine", "inverter", "plant"),
           0, 30_000, True),
        _s("v_ac", "V", "mean", "AC voltage (line-to-line mean)", _WP, ("turbine", "inverter"), 0, 40_000, True),
        _s("i_ac", "A", "mean", "AC current (mean of phases)", _WP, ("turbine", "inverter"), -50_000, 50_000, True),
        _s("p_dc", "kW", "mean", "DC power at the inverter input, positive = generation", _P, ("inverter", "string"), -1_000, 20_000, True),
        _s("v_dc", "V", "mean", "DC voltage", _P, ("inverter", "string"), -50, 2_000, True),
        _s("i_dc", "A", "mean", "DC current", _P, ("inverter", "string"), -50, 20_000, True),
        # ------------------------------------------------------------------ wind turbine mechanics
        _s("wind_speed", "m/s", "mean", "wind speed at the nacelle anemometer", _W, _TUR + _MET, 0, 80, True),
        _s("wind_speed_2", "m/s", "mean", "wind speed of the second (redundant) anemometer", _W, _TUR, 0, 80, True),
        _s("wind_dir", "deg", "circular", "wind direction, from, clockwise from true north", _WP, _TUR + _MET, 0, 360, True),
        _s("wind_dir_rel", "deg", "circular", "wind vane reading relative to the nacelle, positive = wind from the right", _W, _TUR,
           -180, 360, True),
        _s("nacelle_dir", "deg", "circular", "nacelle (yaw) position, clockwise from true north", _W, _TUR, 0, 360, True),
        _s("rotor_speed", "rpm", "mean", "rotor speed", _W, _TUR, -5, 60, True),
        _s("gen_speed", "rpm", "mean", "generator speed", _W, _TUR, -50, 10_000, True),
        _s("pitch", "deg", "mean", "blade pitch angle, 0 = power position, about 90 = feathered (mean of blades)", _W, _TUR,
           -20, 120, True),
        _s("torque", "kNm", "mean", "generator or rotor torque", _W, _TUR, -5_000, 20_000, True),
        _s("shutdown_s", "s", "sum", "seconds of the interval in which the turbine was stopped", _W, _TUR, 0, None),
        _s("vibration", "mm/s", "mean", "tower or drive-train vibration (RMS velocity)", _W, _TUR, 0, 500, True),
        # ------------------------------------------------------------------ temperatures
        _s("t_amb", "degC", "mean", "ambient air temperature", _WP, _TUR + _MET + _INV, -60, 70, True),
        _s("t_nacelle", "degC", "mean", "air temperature inside the nacelle", _W, _TUR, -60, 120, True),
        _s("t_gearbox_oil", "degC", "mean", "gearbox oil temperature", _W, _TUR, -60, 150, True),
        _s("t_gearbox_bearing", "degC", "mean", "gearbox (high speed shaft) bearing temperature", _W, _TUR, -60, 200, True),
        _s("t_gen_winding", "degC", "mean", "generator stator winding temperature", _W, _TUR, -60, 250, True),
        _s("t_gen_bearing", "degC", "mean", "generator bearing temperature", _W, _TUR, -60, 200, True),
        _s("t_main_bearing", "degC", "mean", "main bearing temperature", _W, _TUR, -60, 200, True),
        _s("t_transformer", "degC", "mean", "transformer temperature", _W, _TUR, -60, 250, True),
        _s("t_inverter", "degC", "mean", "inverter heat sink or cabinet temperature", _P, _INV, -60, 150, True),
        _s("t_module", "degC", "mean", "back-of-module temperature", _P, _MET, -60, 120, True),
        # ------------------------------------------------------------------ meteorology (PV, met mast)
        _s("ghi", "W/m2", "mean", "global horizontal irradiance", _P, _MET, -50, 1_800, True),
        _s("poa", "W/m2", "mean", "plane-of-array irradiance", _P, _MET, -50, 1_800, True),
        _s("dni", "W/m2", "mean", "direct normal irradiance", _P, _MET, -50, 1_500, True),
        _s("dhi", "W/m2", "mean", "diffuse horizontal irradiance", _P, _MET, -50, 1_200, True),
        _s("humidity", "%", "mean", "relative humidity", _WP, _MET, 0, 100, True),
        _s("pressure", "Pa", "mean", "air pressure", _WP, _MET + _TUR, 50_000, 110_000, True),
        # ------------------------------------------------------------------ status
        _s("status_code", "", "text", "state or alarm code of the device that dominates the interval, as logged", _WP, ("turbine", "inverter"),),
        _s("iec_category", "", "category", "IEC 61400-26 information category of the interval (see scada_schema.states)", _WP, ("turbine", "inverter")),
    ]
)

PROFILE_SIGNALS: dict[str, tuple[str, ...]] = {
    p: tuple(n for n, s in SIGNALS.items() if p in s.profiles) for p in (WIND, PV)
}


def stat_column(signal: str, stat: str) -> str:
    """Column name of a statistic of a signal (``min``, ``max`` or ``std``)."""
    if stat not in STATS:
        raise ValueError(f"unknown statistic {stat!r}; use one of {STATS}")
    return f"{signal}__{stat}"


def split_column(column: str) -> tuple[str, str | None]:
    """``'p_ac__std'`` -> ``('p_ac', 'std')``; ``'p_ac'`` -> ``('p_ac', None)``."""
    base, sep, stat = column.partition("__")
    return (base, stat) if sep else (column, None)


def base_signal(column: str) -> str:
    """The signal a column belongs to."""
    return split_column(column)[0]
