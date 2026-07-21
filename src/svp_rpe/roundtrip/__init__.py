"""Roundtrip preservation diagnostics."""
from __future__ import annotations

from .adherence import (
    ScoreAdherenceReport,
    TightFieldAdherence,
    render_score_adherence_text,
    run_score_adherence,
    score_adherence,
)
from .corpus_batch import (
    CorpusBatchReport,
    CorpusFieldComparison,
    CorpusTakeResult,
    render_corpus_batch_text,
    run_corpus_batch,
)
from .diagnose import GripRecord, diagnose_roundtrip, load_grip_map
from .harness import run_roundtrip
from .identity_rank import (
    AxisRanking,
    ExcludedDomain,
    IdentityRankReport,
    NotObservedReference,
    RankEntry,
    ReferenceEntry,
    ReferenceList,
    identity_rank_from_paths,
    load_reference_list,
    render_identity_rank_text,
    run_identity_rank,
)
from .manifest import (
    FieldIntent,
    RoundtripManifest,
    RoundtripTake,
    classify_take,
    load_manifest,
)
from .models import RoundtripField, RoundtripReport
from .render import render_roundtrip_text
from .repetition import (
    FieldRepetitionSummary,
    RepetitionRankingEntry,
    RepetitionReport,
    RepetitionTakeResult,
    SelectionResult,
    TakeFieldObservation,
    load_takes_for_repetition,
    render_repetition_text,
    run_repetition_batch,
)

__all__ = [
    "AxisRanking",
    "CorpusBatchReport",
    "CorpusFieldComparison",
    "CorpusTakeResult",
    "ExcludedDomain",
    "FieldIntent",
    "FieldRepetitionSummary",
    "GripRecord",
    "IdentityRankReport",
    "NotObservedReference",
    "RankEntry",
    "ReferenceEntry",
    "ReferenceList",
    "RepetitionRankingEntry",
    "RepetitionReport",
    "RepetitionTakeResult",
    "RoundtripManifest",
    "RoundtripTake",
    "RoundtripField",
    "RoundtripReport",
    "ScoreAdherenceReport",
    "SelectionResult",
    "TakeFieldObservation",
    "TightFieldAdherence",
    "classify_take",
    "diagnose_roundtrip",
    "identity_rank_from_paths",
    "load_grip_map",
    "load_manifest",
    "load_reference_list",
    "load_takes_for_repetition",
    "render_corpus_batch_text",
    "render_identity_rank_text",
    "render_repetition_text",
    "render_roundtrip_text",
    "render_score_adherence_text",
    "run_corpus_batch",
    "run_identity_rank",
    "run_repetition_batch",
    "run_roundtrip",
    "run_score_adherence",
    "score_adherence",
]
