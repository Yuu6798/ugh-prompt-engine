"""rpe/models.py — RPE data models (Pydantic)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, field_validator


class SpectralProfile(BaseModel):
    """Spectral frequency distribution."""

    schema_version: str = "1.0"
    centroid: float
    low_ratio: float       # energy ratio below ~300 Hz
    mid_ratio: float       # energy ratio 300-4000 Hz
    high_ratio: float      # energy ratio above 4000 Hz
    brightness: float      # high / (low + mid + high)


class StereoProfile(BaseModel):
    """Stereo field analysis."""

    schema_version: str = "1.0"
    width: float           # stereo width [0, 1]
    correlation: float     # L-R correlation [-1, 1]


class SectionMarker(BaseModel):
    """Audio segment marker."""

    schema_version: str = "1.0"
    label: str             # e.g. "section_01", "section_02"
    start_sec: float
    end_sec: float
    rms_mean: Optional[float] = None


class PhysicalRPE(BaseModel):
    """Physical audio features extracted from waveform."""

    schema_version: str = "1.0"
    bpm: Optional[float] = None
    bpm_confidence: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None          # "major" | "minor" | None
    key_confidence: Optional[float] = None
    duration_sec: float
    sample_rate: int
    time_signature: str = "4/4"
    time_signature_confidence: float = 0.3
    structure: List[SectionMarker]
    rms_mean: float
    peak_amplitude: float
    crest_factor: float
    active_rate: float
    valley_depth: float
    valley_depth_method: str = "rms"
    thickness: float
    spectral_centroid: float
    spectral_profile: SpectralProfile
    stereo_profile: Optional[StereoProfile] = None
    onset_density: float

    @field_validator("structure")
    @classmethod
    def structure_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("structure must contain at least one section")
        return v


class GrvAnchor(BaseModel):
    """Gravity anchor — dominant sonic character."""

    schema_version: str = "1.0"
    primary: str           # e.g. "bass-heavy", "bright"
    secondary: List[str] = []
    confidence: float = 0.5


class DeltaEProfile(BaseModel):
    """Energy transition profile."""

    schema_version: str = "1.0"
    transition_type: str   # e.g. "gradual_build", "sudden_drop", "flat"
    intensity: float       # [0, 1]
    description: str


class SemanticRPE(BaseModel):
    """Semantic layer generated from physical features via rule-based mapping."""

    schema_version: str = "1.0"
    por_core: str                      # core semantic description
    por_surface: List[str]             # surface-level labels
    grv_anchor: GrvAnchor
    delta_e_profile: DeltaEProfile
    cultural_context: List[str]
    instrumentation_summary: str
    production_notes: List[str]
    confidence_notes: List[str]
    estimation_disclaimer: str = (
        "semantic層はルールベース推定であり、意味理解の真値ではない"
    )


class RPEBundle(BaseModel):
    """Complete RPE output: physical + semantic + metadata."""

    schema_version: str = "1.0"
    physical: PhysicalRPE
    semantic: SemanticRPE
    audio_file: str
    audio_duration_sec: float
    audio_sample_rate: int
    audio_channels: int
    audio_format: str      # "wav" | "mp3"
