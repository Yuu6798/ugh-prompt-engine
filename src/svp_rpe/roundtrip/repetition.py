"""R3 stochastic performer repetition harness (R3-1/R3-2/R3-3 instruments).

This module bundles a batch of *n* independently generated takes of the same
``CompositionScore`` (R3-1: a physical-fixed / semantic-swapped manifest of
takes), measures the per-field roundtrip preservation rate across the batch
(R3-2: ``n>1`` field-level repetition rate), and mechanically identifies the
take that agrees with the score on the most fields (R3-3: rejection
sampling — "selection as control").

Like every other module in ``svp_rpe.roundtrip``, this is a **measurement
instrument, not a verdict**. Reports never carry ``verdict``/``passed``/
``pass``/``failed``/``fail``/``ok``/``loss`` keys. ``SelectionResult`` in
particular is a mechanical "closest take to the score" pick under a named,
inspectable basis (``preserved_field_count``) — it is not a claim that the
selected take is good, or that MusicGen/Suno "worked". Rejection sampling
here means only: given N stochastic takes, pick the one whose measured
fields most closely match the authored score, which is a control mechanism
available even for control channels whose grip is weak or unproven.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.compose.models import CompositionScore
from svp_rpe.perform import sha256_bytes
from svp_rpe.roundtrip.diagnose import GripRecord, diagnose_roundtrip
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.transcribe import draft_score

#: R3 の rejection sampling が既定で数える「信頼できる送出ノブ」。
#: roadmap_goal2.md R3 の完了基準（送出かつ計器が信頼できる物理ノブ =
#: 最低 key / brightness）に従う。bpm は R2 closeout（2026-06-18）で
#: R3 信頼ノブから明示除外されているため既定に含めない。計測
#: （FieldRepetitionSummary）は全フィールドを報告し続ける — スコープは
#: 選択の基準にのみ適用される。
R3_SELECTION_FIELDS: tuple[str, ...] = ("key", "brightness")


class TakeFieldObservation(BaseModel):
    """One field's roundtrip diagnosis for a single take."""

    model_config = ConfigDict(extra="forbid")

    field: str
    expected_value: Any
    observed_value: Any
    diagnosis: str
    preserved: bool


class RepetitionTakeResult(BaseModel):
    """Descriptive per-take roundtrip result within a repetition batch."""

    model_config = ConfigDict(extra="forbid")

    take_id: str
    audio_sha256: str
    fields: list[TakeFieldObservation] = Field(default_factory=list)
    preserved_count: int


class FieldRepetitionSummary(BaseModel):
    """Aggregated per-field repetition statistics across all takes (R3-2)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    n: int
    preserved_count: int
    preserved_rate: float
    diagnosis_counts: dict[str, int] = Field(default_factory=dict)
    observed_values: list[Any] = Field(default_factory=list)


class RepetitionRankingEntry(BaseModel):
    """One take's rank within the rejection-sampling ranking (R3-3).

    ``preserved_count`` counts preserved fields **within the selection
    scope** (``SelectionResult.selection_fields``), not across every
    diagnosed field — untrusted or auxiliary sensors must not dominate the
    rejection-sampling pick.
    """

    model_config = ConfigDict(extra="forbid")

    take_id: str
    preserved_count: int


class SelectionResult(BaseModel):
    """Mechanical "closest take to the score" pick (R3-3).

    ``basis`` names the mechanism; ``selection_fields`` names the exact
    field scope the count ran over (default: ``R3_SELECTION_FIELDS``);
    ``ranking`` is sorted descending by ``preserved_count`` with ties broken
    by ascending ``take_id`` for determinism. ``selected_take_id`` is the
    first ranking entry. This is a take *identification* mechanism, not a
    quality judgment.
    """

    model_config = ConfigDict(extra="forbid")

    basis: str = "preserved_field_count"
    selection_fields: list[str] = Field(default_factory=list)
    ranking: list[RepetitionRankingEntry] = Field(default_factory=list)
    selected_take_id: str


class RepetitionReport(BaseModel):
    """Descriptive R3 repetition-batch report. Not a pass/fail gate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    score_ref: str
    generator: str
    n_takes: int
    fields: list[FieldRepetitionSummary] = Field(default_factory=list)
    takes: list[RepetitionTakeResult] = Field(default_factory=list)
    selection: SelectionResult


def run_repetition_batch(
    score: CompositionScore,
    takes: Sequence[tuple[str, Path]],
    *,
    generator: str,
    score_ref: str,
    grip_map: Mapping[str, GripRecord] | None = None,
    selection_fields: Sequence[str] | None = None,
) -> RepetitionReport:
    """Measure roundtrip preservation across a batch of stochastic takes.

    ``grip_map`` defaults to ``None``, which this function treats as "no
    grip info" (an empty mapping passed to ``diagnose_roundtrip``) rather
    than letting ``diagnose_roundtrip`` fall back to loading the packaged K1
    grip map. The K1 map was measured on the deterministic synth performer;
    reusing it for a stochastic generator (MusicGen/Suno) would mislabel
    disagreements as ``knob_dead`` based on the wrong instrument's
    calibration. With no grip info, unmatched fields are diagnosed as
    ``calibration_disagreement`` instead. Pass an explicit ``grip_map`` once
    a generator-specific grip fixture exists (e.g. a future MusicGen grip
    fixture) — the parameter is kept for that purpose.

    ``selection_fields`` scopes the rejection-sampling count (R3-3). The
    default ``None`` resolves to ``R3_SELECTION_FIELDS`` (key / brightness —
    the R3 trusted send-knobs; bpm is excluded per the R2 closeout). Field
    summaries (R3-2) always report every diagnosed field regardless of this
    scope.
    """

    if len(takes) < 2:
        raise ValueError(
            f"R3 repetition batch requires n>1 takes; got {len(takes)} usable take(s) "
            "(after `excluded` filtering). A single take cannot support the R3-2 "
            "repetition rate or the R3-3 rejection-sampling ranking."
        )
    effective_selection = tuple(selection_fields) if selection_fields else R3_SELECTION_FIELDS
    take_results = [_run_take(take_id, audio_path, score, grip_map) for take_id, audio_path in takes]
    return RepetitionReport(
        score_ref=score_ref,
        generator=generator,
        n_takes=len(take_results),
        fields=_summarize_fields(take_results),
        takes=take_results,
        selection=_select(take_results, effective_selection),
    )


def load_takes_for_repetition(
    manifest: Mapping[str, Any],
    *,
    audio_dir: Path,
) -> list[tuple[str, Path]]:
    """Load ``(take_id, audio_path)`` pairs from a PR A takes manifest.

    Recomputes each sample's ``audio_sha256`` from the bytes on disk and
    fails fast on any mismatch against the manifest's pinned value (same
    contract as ``scripts/collect_clap_fixture.py`` /
    ``scripts/collect_musicgen_takes.py extract_fixture``). Samples with an
    ``excluded`` key set to a truthy value are skipped; samples without the
    key are treated as not excluded.
    """

    takes: list[tuple[str, Path]] = []
    for sample in manifest.get("samples", []):
        if sample.get("excluded"):
            continue
        sample_id = str(sample["sample_id"])
        audio_path = audio_dir / str(sample["audio_path"])
        audio_bytes = audio_path.read_bytes()
        computed_sha256 = sha256_bytes(audio_bytes)
        manifest_sha256 = str(sample["audio_sha256"])
        if computed_sha256 != manifest_sha256:
            raise ValueError(
                f"sample {sample_id!r}: manifest audio_sha256 does not match "
                f"the audio file at {audio_path} "
                f"(manifest={manifest_sha256}, computed={computed_sha256})"
            )
        takes.append((sample_id, audio_path))
    return takes


def render_repetition_text(report: RepetitionReport) -> str:
    """Render a compact markdown report for CLI output."""

    lines = [
        f"# Repetition Roundtrip - {report.score_ref}",
        "",
        f"generator: {report.generator}  n_takes: {report.n_takes}  "
        f"selection_basis: {report.selection.basis}  "
        f"selection_fields: {', '.join(report.selection.selection_fields)}  "
        f"selected_take_id: {report.selection.selected_take_id}",
        "",
        "## Field summary",
        "",
        "| field | n | preserved | rate | diagnosis_counts |",
        "|---|---:|---:|---:|---|",
    ]
    for item in report.fields:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(item.diagnosis_counts.items()))
        lines.append(
            "| "
            f"{item.field} | {item.n} | {item.preserved_count} | "
            f"{item.preserved_rate} | {counts} |"
        )

    lines += [
        "",
        "## Ranking",
        "",
        "| take | preserved_count |",
        "|---|---:|",
    ]
    for entry in report.selection.ranking:
        lines.append(f"| {entry.take_id} | {entry.preserved_count} |")

    lines += [
        "",
        "## Takes",
        "",
        "| take | field | expected | observed | diagnosis | preserved |",
        "|---|---|---|---|---|---:|",
    ]
    for take in report.takes:
        for item in take.fields:
            lines.append(
                "| "
                f"{take.take_id} | {item.field} | "
                f"{_format_value(item.expected_value)} | "
                f"{_format_value(item.observed_value)} | "
                f"{item.diagnosis} | {item.preserved} |"
            )

    return "\n".join(lines) + "\n"


def _run_take(
    take_id: str,
    audio_path: Path,
    score: CompositionScore,
    grip_map: Mapping[str, GripRecord] | None,
) -> RepetitionTakeResult:
    audio_sha256 = sha256_bytes(Path(audio_path).read_bytes())
    bundle = extract_rpe_from_file(str(audio_path))
    draft = draft_score(bundle)
    # An explicit empty mapping (rather than None) prevents diagnose_roundtrip
    # from falling back to the packaged K1 grip map, which was measured on
    # the deterministic synth performer and would misclassify stochastic
    # generator disagreements as knob_dead.
    effective_grip_map: Mapping[str, GripRecord] = grip_map if grip_map is not None else {}
    take_report = diagnose_roundtrip(score, draft, grip_map=effective_grip_map)
    fields = [
        TakeFieldObservation(
            field=item.field,
            expected_value=_round_value(item.source_value),
            observed_value=_round_value(item.transcribed_value),
            diagnosis=item.diagnosis,
            preserved=item.diagnosis == "preserved",
        )
        for item in take_report.fields
    ]
    preserved_count = sum(1 for item in fields if item.preserved)
    return RepetitionTakeResult(
        take_id=take_id,
        audio_sha256=audio_sha256,
        fields=fields,
        preserved_count=preserved_count,
    )


def _summarize_fields(takes: list[RepetitionTakeResult]) -> list[FieldRepetitionSummary]:
    if not takes:
        return []
    field_order = [item.field for item in takes[0].fields]
    summaries: list[FieldRepetitionSummary] = []
    for field in field_order:
        observations = [
            observation
            for take in takes
            for observation in take.fields
            if observation.field == field
        ]
        n = len(observations)
        preserved_count = sum(1 for item in observations if item.preserved)
        diagnosis_counts: dict[str, int] = {}
        for item in observations:
            diagnosis_counts[item.diagnosis] = diagnosis_counts.get(item.diagnosis, 0) + 1
        summaries.append(
            FieldRepetitionSummary(
                field=field,
                n=n,
                preserved_count=preserved_count,
                preserved_rate=round(preserved_count / n, 4) if n else 0.0,
                diagnosis_counts=diagnosis_counts,
                observed_values=[item.observed_value for item in observations],
            )
        )
    return summaries


def _select(
    takes: list[RepetitionTakeResult], selection_fields: Sequence[str]
) -> SelectionResult:
    scope = set(selection_fields)
    ranking = sorted(
        (
            RepetitionRankingEntry(
                take_id=take.take_id,
                preserved_count=sum(
                    1 for item in take.fields if item.field in scope and item.preserved
                ),
            )
            for take in takes
        ),
        key=lambda entry: (-entry.preserved_count, entry.take_id),
    )
    selected_take_id = ranking[0].take_id if ranking else ""
    return SelectionResult(
        selection_fields=list(selection_fields),
        ranking=ranking,
        selected_take_id=selected_take_id,
    )


def _round_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 4)
    return value


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
