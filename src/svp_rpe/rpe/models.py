"""rpe/models.py — RPE data models (Pydantic)."""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


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


class DynamicsSummary(BaseModel):
    """Track-level dynamics descriptor aggregated from the novelty curve.

    Captures how much and where the song's energy/spectral content changes.
    All values are derived deterministically from compute_novelty_curve output.
    """

    schema_version: str = "1.0"
    peak_novelty: float                    # max novelty value [0, ~1]
    mean_novelty: float                    # average novelty
    std_novelty: float                     # variance of novelty
    event_count: int                       # peaks above (mean + 0.5 * std)
    # Front/back energy bias. Ratio of first-half mean novelty to whole-track
    # mean. >1.0 = front-loaded, <1.0 = back-loaded, ≈1.0 = balanced.
    temporal_balance: float


class SectionMarker(BaseModel):
    """Audio segment marker."""

    schema_version: str = "1.0"
    label: str             # e.g. "section_01", "section_02"
    start_sec: float
    end_sec: float
    rms_mean: Optional[float] = None


class ChordEvent(BaseModel):
    """Time-bounded chord estimate."""

    schema_version: str = "1.0"
    chord: str             # e.g. "C major", "A minor"
    root: str              # e.g. "C", "F#"
    quality: Literal["major", "minor"]
    start_sec: float
    end_sec: float
    confidence: float

    @field_validator("confidence")
    @classmethod
    def chord_confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class MelodyContour(BaseModel):
    """Frame-aligned melody pitch contour."""

    schema_version: str = "1.0"
    times: List[float] = Field(default_factory=list)
    frequencies_hz: List[float] = Field(default_factory=list)
    voicing: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def arrays_have_same_length(self) -> "MelodyContour":
        lengths = {len(self.times), len(self.frequencies_hz), len(self.voicing)}
        if len(lengths) != 1:
            raise ValueError("times, frequencies_hz, and voicing must have the same length")
        return self

    @field_validator("voicing")
    @classmethod
    def voicing_in_range(cls, values: List[float]) -> List[float]:
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("voicing values must be between 0.0 and 1.0")
        return values


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
    downbeat_times: List[float] = Field(default_factory=list)
    chord_events: List[ChordEvent] = Field(default_factory=list)
    melody_contour: Optional[MelodyContour] = None
    structure: List[SectionMarker]
    rms_mean: float
    peak_amplitude: float
    crest_factor: float
    loudness_lufs_integrated: Optional[float] = None
    true_peak_dbfs: Optional[float] = None
    active_rate: float
    valley_depth: float
    valley_depth_method: str = "rms"
    thickness: float
    spectral_centroid: float
    spectral_profile: SpectralProfile
    stereo_profile: Optional[StereoProfile] = None
    onset_density: float
    # RMS-based dynamic range descriptor (P95/P10 frame RMS, in dB).
    # NOT EBU R128 LRA — labelled `_db` to avoid being mistaken for it.
    dynamic_range_db: Optional[float] = None
    dynamics_summary: Optional[DynamicsSummary] = None
    stem_rpe: dict[str, "PhysicalRPE"] = Field(default_factory=dict)

    @field_validator("structure")
    @classmethod
    def structure_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("structure must contain at least one section")
        return v

    @model_serializer(mode="wrap")
    def omit_empty_stem_rpe(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not self.stem_rpe:
            data.pop("stem_rpe", None)
        return data


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


class SemanticLabel(BaseModel):
    """Evidence-bearing semantic label emitted by deterministic rules."""

    label: str
    layer: Literal["perceptual", "structural", "semantic_hypothesis"]
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    source_rule: str

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class SemanticRPE(BaseModel):
    """Semantic layer generated from physical features via rule-based mapping."""

    schema_version: str = "2.0"
    por_core: str                      # core semantic description
    por_surface: List[SemanticLabel]   # evidence-bearing labels
    grv_anchor: GrvAnchor
    delta_e_profile: DeltaEProfile
    cultural_context: List[str]
    instrumentation_summary: str
    production_notes: List[str]
    confidence_notes: List[str]
    estimation_disclaimer: str = (
        "semantic層はルールベース推定であり、意味理解の真値ではない"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_schema(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("schema_version") == "1.0":
            raise ValueError("SemanticRPE schema_version 1.0 is unsupported; regenerate RPE")
        return data


class LearnedModelInfo(BaseModel):
    """Provenance metadata for a learned-model adapter that was actually invoked.

    Attached to LearnedAudioAnnotations.enabled_models so downstream consumers
    can audit which models contributed without reloading them.
    """

    name: str
    version: Optional[str] = None
    provider: Optional[str] = None
    task: Literal["tagging", "beat_downbeat", "pitch", "embedding", "other"]
    license: Optional[str] = None
    weights_license: Optional[str] = None


class LearnedAudioLabel(BaseModel):
    """A single learned-model label with confidence and provenance.

    These are model estimates, not rule-based evidence. They MUST NOT be
    written into SemanticRPE.por_surface or any other rule-derived field.
    See docs/learned_models_policy.md.

    `notes` is intentionally NOT named `evidence` — that name is reserved for
    SemanticLabel.evidence, which carries rule-derived measured propositions
    (e.g. "bpm=152 >= 140"). LearnedAudioLabel.notes holds free-form model
    provenance hints (e.g. "top-k tag from AudioSet 527") and carries no
    epistemic weight relative to rule-based evidence.
    """

    label: str
    category: Literal["audioset", "mood", "genre", "instrument", "other"] = "other"
    confidence: float
    source_model: str
    notes: List[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class LearnedEmbedding(BaseModel):
    """Vector embedding emitted by a learned model (e.g. PANNs Cnn14)."""

    source_model: str
    vector: List[float]
    dimensions: int

    @model_validator(mode="after")
    def dimensions_match_vector(self) -> "LearnedEmbedding":
        if self.dimensions != len(self.vector):
            raise ValueError(
                f"dimensions ({self.dimensions}) must match vector length ({len(self.vector)})"
            )
        return self


class LearnedTimeEvent(BaseModel):
    """A time-stamped event emitted by a learned model (beat, downbeat, ...).

    Attached to LearnedAudioAnnotations.time_events. MUST NOT be folded into
    PhysicalRPE.downbeat_times / time_signature — those fields are reserved
    for the deterministic librosa-derived path.
    """

    time_sec: float
    event_type: Literal["beat", "downbeat"]
    confidence: Optional[float] = None
    source_model: str

    @field_validator("time_sec")
    @classmethod
    def time_non_negative(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("time_sec must be non-negative")
        return v

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class LearnedNoteEvent(BaseModel):
    """A pitched note event emitted by a learned transcription model.

    Attached to LearnedAudioAnnotations.note_events. MUST NOT be folded into
    PhysicalRPE.melody_contour or PhysicalRPE.chord_events — those fields
    stay reserved for the deterministic librosa- / pyin-derived path.

    `pitch_midi` uses the standard MIDI note range [0, 127] (where 60 = C4,
    69 = A4 / 440 Hz). `confidence` corresponds to upstream amplitude /
    onset confidence depending on the model; we do not attempt to unify
    semantics across models — consumers should consult `source_model` to
    interpret it.
    """

    start_sec: float
    end_sec: float
    pitch_midi: int
    confidence: float
    source_model: str

    @field_validator("start_sec")
    @classmethod
    def start_non_negative(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("start_sec must be non-negative")
        return v

    @field_validator("pitch_midi")
    @classmethod
    def midi_in_range(cls, v: int) -> int:
        if not 0 <= v <= 127:
            raise ValueError("pitch_midi must be in [0, 127]")
        return v

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def end_at_or_after_start(self) -> "LearnedNoteEvent":
        if self.end_sec < self.start_sec:
            raise ValueError(
                f"end_sec ({self.end_sec}) must be >= start_sec ({self.start_sec})"
            )
        return self


class LearnedAudioAnnotations(BaseModel):
    """Container for learned-model output, isolated from rule-based RPE evidence.

    By design this MUST NOT be merged into PhysicalRPE / SemanticRPE.
    Attached to RPEBundle as a sibling field. See docs/learned_models_policy.md.
    """

    schema_version: str = "1.0"
    enabled_models: List[LearnedModelInfo] = Field(default_factory=list)
    labels: List[LearnedAudioLabel] = Field(default_factory=list)
    embedding: Optional[LearnedEmbedding] = None
    time_events: List[LearnedTimeEvent] = Field(default_factory=list)
    note_events: List[LearnedNoteEvent] = Field(default_factory=list)
    inference_config: dict[str, Any] = Field(default_factory=dict)
    license_metadata: dict[str, str] = Field(default_factory=dict)
    estimation_disclaimer: str = (
        "learned_annotations are model-derived estimates, "
        "not ground-truth music quality labels"
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
    learned_annotations: Optional[LearnedAudioAnnotations] = None

    @model_serializer(mode="wrap")
    def omit_empty_learned_annotations(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.learned_annotations is None:
            data.pop("learned_annotations", None)
        return data
