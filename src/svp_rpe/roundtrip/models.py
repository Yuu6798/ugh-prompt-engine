"""Roundtrip preservation report models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RoundtripDiagnosis = Literal[
    "preserved",
    "knob_dead",
    "sensor_blind",
    "calibration_disagreement",
]
SensorState = Literal["working", "blind"]


class RoundtripField(BaseModel):
    """One physical score field compared through score -> audio -> draft score."""

    model_config = ConfigDict(extra="forbid")

    field: str
    source_value: Any
    transcribed_value: Any
    diagnosis: RoundtripDiagnosis
    grip: float | None = None
    grip_class: str | None = None
    sensor: str
    sensor_state: SensorState
    note: str | None = None


class RoundtripReport(BaseModel):
    """Descriptive roundtrip preservation report.

    This is intentionally not a pass/fail object. Consumers should inspect each
    field needle and follow-up calibration notes instead of treating the report
    as a verdict.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    source_id: str
    transcribed_id: str
    fields: list[RoundtripField] = Field(default_factory=list)
