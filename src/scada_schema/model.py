"""Dataset metadata and profiles (pydantic models).

A canonical table is a pandas data frame; the facts that make it interpretable travel with it as a
[`DatasetMeta`][scada_schema.model.DatasetMeta], embedded in the Parquet file (see [`io`][scada_schema.io]):
profile, interval, the time zone and label convention of the *source*, the devices with their ratings and
positions, where the data came from and which conversions the loader applied.

A [`Profile`][scada_schema.model.Profile] says which devices and signals a table of that kind must have.

Usage:
    meta = DatasetMeta(name="demo", profile="wind", interval="10min",
                       devices=[Device(id="T01", type="turbine", rated_kw=2050)])
    meta.device_types()        # {'T01': 'turbine'}
"""

from __future__ import annotations

import json
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from res_asset_kpi.timeseries import SUPPORTED_STEPS

from scada_schema.signals import PV, WIND

__all__ = [
    "DEVICE_TYPES",
    "INTERVALS",
    "PROFILES",
    "SCHEMA_VERSION",
    "Device",
    "DatasetMeta",
    "Profile",
    "Source",
]

SCHEMA_VERSION = "1.0"
INTERVALS = ("5min", "10min", "15min", "30min", "1h")
assert tuple(pd.Timedelta(i) for i in INTERVALS) == SUPPORTED_STEPS
DEVICE_TYPES = ("turbine", "inverter", "string", "met", "meter", "plant")
Label = Literal["start", "end", "center", "instant"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Device(_Model):
    """One device of a plant: a turbine, an inverter, a string, a met station, a meter or the plant itself."""

    id: str
    type: Literal["turbine", "inverter", "string", "met", "meter", "plant"]
    rated_kw: float | None = Field(default=None, gt=0, description="AC rating (turbine, inverter, meter)")
    capacity_kwp: float | None = Field(default=None, gt=0, description="DC rating (inverter, string, plant)")
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    hub_height_m: float | None = Field(default=None, gt=0)
    rotor_diameter_m: float | None = Field(default=None, gt=0)
    tilt_deg: float | None = Field(default=None, ge=0, le=90)
    azimuth_deg: float | None = Field(default=None, ge=0, le=360, description="clockwise from north, 180 = south")
    parent: str | None = Field(default=None, description="e.g. the inverter of a string")


class Source(_Model):
    """Where the data came from."""

    name: str
    doi: str | None = None
    url: str | None = None
    license: str | None = None
    citation: str | None = None


class DatasetMeta(_Model):
    """Everything needed to interpret a canonical table.

    Attributes
    ----------
    profile : {"wind", "pv"}
    interval : str
        Length of the averaging interval: one of 5min, 10min, 15min, 30min, 1h.
    source_timezone : str
        IANA time zone the *source* used for its time stamps (``UTC`` or a fixed offset such as ``Etc/GMT+8``
        mean no daylight saving time). The table itself is always UTC.
    source_label : {"start", "end", "center", "instant"}
        Which instant of its interval a source time stamp marked. The table always marks the start.
    devices : list of Device
    source : Source
    conversions : list of str
        Unit and scale conversions applied by the loader, in words (provenance).
    notes : list of str
    """

    schema_version: str = SCHEMA_VERSION
    name: str
    profile: Literal["wind", "pv"]
    interval: str
    source_timezone: str = "UTC"
    source_label: Label = "start"
    devices: list[Device] = Field(default_factory=list)
    source: Source | None = None
    conversions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("interval")
    @classmethod
    def _interval(cls, v: str) -> str:
        if v not in INTERVALS:
            raise ValueError(f"interval {v!r} not supported; use one of {INTERVALS}")
        return v

    @model_validator(mode="after")
    def _devices(self) -> DatasetMeta:
        ids = [d.id for d in self.devices]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicated device ids: {dup}")
        allowed = PROFILES[self.profile].device_types
        bad = sorted({d.type for d in self.devices} - set(allowed))
        if bad:
            raise ValueError(f"device types {bad} are not part of the {self.profile} profile ({list(allowed)})")
        return self

    @property
    def step(self) -> pd.Timedelta:
        return pd.Timedelta(self.interval)

    def device_types(self) -> dict[str, str]:
        return {d.id: d.type for d in self.devices}

    def device(self, device_id: str) -> Device:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(device_id)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), sort_keys=True)

    @classmethod
    def from_json(cls, text: str | bytes) -> DatasetMeta:
        return cls.model_validate(json.loads(text))


class Profile(_Model):
    """What a table of one kind must contain.

    ``required`` lists, per device type, signals that must be columns whenever the table has a device of that type;
    ``any_of`` lists groups of which at least one signal must be present (e.g. an inverter needs AC or DC power).
    """

    name: str
    device_types: tuple[str, ...]
    required: dict[str, tuple[str, ...]]
    any_of: dict[str, tuple[tuple[str, ...], ...]] = Field(default_factory=dict)
    description: str = ""


PROFILES: dict[str, Profile] = {
    WIND: Profile(
        name=WIND,
        device_types=("turbine", "met", "plant"),
        required={"turbine": ("p_ac", "wind_speed")},
        description="wind turbines (and optionally met masts and the plant meter): power, wind, nacelle, pitch, state, temperatures",
    ),
    PV: Profile(
        name=PV,
        device_types=("inverter", "string", "met", "meter", "plant"),
        required={},
        any_of={"inverter": (("p_ac", "p_dc"),), "met": (("poa", "ghi"),), "string": (("i_dc", "p_dc"),)},
        description="PV plants: inverters, strings, meteorological stations and meters: power, DC values, irradiance, temperatures, energy, state",
    ),
}
