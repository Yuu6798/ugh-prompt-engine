"""Pydantic models for per-field measurement reports."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TranscribeModel(BaseModel):
    """Base model for deterministic transcribe outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldMeasurement(TranscribeModel):
    """A measured value for one CompositionScore physical field."""

    score_field: str
    sensor: str
    raw_value: float | str | None
    unit: str | None
    score_value: int | str | None
    calibration: list[str] = Field(default_factory=list)


class MeasurementReport(TranscribeModel):
    """Deterministic measurement report for one audio sample."""

    schema_version: str = "1.0"
    sample_id: str
    duration_sec: float
    measurements: list[FieldMeasurement]
