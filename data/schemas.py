"""Pydantic models shared by the generators, the transforms and the ingest code.

Deck p.20 rule 2: "the schema is the same pydantic model the ingest code uses".
Rule 4: a row that fails validation is rejected and counted, never repaired.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BCP47 = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Clinic(_Row):
    clinic_id: str = Field(pattern=r"^(PUN|HYD)-[HC]\d{2}$")
    name: str
    city: Literal["Pune", "Hyderabad"]
    kind: Literal["hospital", "clinic"]
    district: str
    opens_at: time
    closes_at: time
    has_pharmacy: bool
    synthetic: bool = True

    @model_validator(mode="after")
    def _hours(self) -> "Clinic":
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        return self


class Specialty(_Row):
    specialty_id: str = Field(pattern=r"^SP\d{2}$")
    name: str
    hospital_only: bool
    extra_doctors: int = Field(ge=0)


class Doctor(_Row):
    doctor_id: str = Field(pattern=r"^DR\d{4}$")
    name: str = Field(min_length=5)
    gender: Literal["F", "M"]
    specialty_id: str
    clinic_id: str
    languages: tuple[str, ...] = Field(min_length=1)
    years_practice: int = Field(ge=0, le=60)
    synthetic: Literal[True] = True

    @field_validator("languages")
    @classmethod
    def _bcp47(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [x for x in v if not BCP47.match(x)]
        if bad:
            raise ValueError(f"not BCP-47: {bad}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate language")
        return v


class Slot(_Row):
    slot_id: str
    doctor_id: str
    clinic_id: str
    starts_at: datetime
    capacity: int = Field(default=1, ge=1)
    synthetic: Literal[True] = True

    @field_validator("starts_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("starts_at must be timezone-aware")
        return v


class Appointment(_Row):
    """A cleaned row from the No Shows dataset (history, not live bookings)."""
    source_appointment_id: str
    source_patient_id: str
    gender: Literal["F", "M"]
    age: int = Field(ge=0, le=115)
    scheduled_on: date
    appointment_on: date
    lead_days: int = Field(ge=0)
    sms_received: bool
    attended: bool
    clinic_id: str

    @model_validator(mode="after")
    def _order(self) -> "Appointment":
        if self.scheduled_on > self.appointment_on:
            raise ValueError("scheduled after the visit")
        return self
