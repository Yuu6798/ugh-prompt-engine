"""WI2 identity-rank instrument: per-axis distance/rank of a take against a
reference list (canonical score / destroyed-anchor decoys / another work).

Design memo: ``wi2_preregistration_design.md`` section C (the distance
formulas below follow it literally). Like every other module in
``svp_rpe.roundtrip`` (and ``arrange/observe.py``), this is a **descriptive
instrument, not a verdict**: there is no threshold, no pass/fail field, and
no aggregate "closest reference" pick. It reports, per axis (structure /
harmony / key / bpm / brightness), each reference's distance from the
observed take and the resulting rank (ties broken by ascending ``ref_id`` —
the same stable tie-break ``roundtrip/repetition.py``'s
``RepetitionRankingEntry`` ranking uses), plus which references (or axes)
could not be observed and why. Each ``RankEntry`` also carries ``tied_with``
— the other references sharing its exact distance — so a same-distance
rank1 (a tie-break pick) is legible in the report itself instead of reading
as an undisputed win.

Axes and their exact distance formulas (design memo section C):

- **structure**: ``1 - position_match_rate(observed_sections,
  reference_sections)`` after normalizing both sides the same way
  ``arrange/observe.py``'s ``_observe_structure`` does (canonical side:
  lowercase only; observed side: lowercase + strip a trailing digit only off
  the ``verse`` stem). Those normalizers are module-private in
  ``observe.py``; this module does not reach into them (the same
  private-helper-avoidance convention ``observe.py``'s own
  ``_collapse_adjacent`` docstring documents for its relationship to
  ``roundtrip.compare``) and instead re-implements the identical rule here
  as new public functions (``normalize_reference_section_label`` /
  ``normalize_observed_section_label``).
- **harmony**: ``1 - repeated_chord_sequence_match_rate(reference_progression,
  observed_chord_sequence)``, reusing ``roundtrip.compare``'s public
  function directly (no reimplementation). References with no
  ``progression_path`` are ``not_observed`` on this axis.
- **key**: ``1 - weighted_key_score(reference_key, observed_key_label).score``,
  reusing ``keys.py`` directly. ``reference_key`` is the reference score's
  single ``physical.key`` string, already in mir_eval's ``"<tonic> <mode>"``
  form (e.g. ``"C minor"``). The bundle stores the same information as two
  *separate* fields (``physical.key`` = bare tonic, ``physical.mode`` =
  ``"major"``/``"minor"``/``None``), so ``observed_key_label`` is built by
  joining them (``f"{key} {mode}"``) before comparison — passing the bare
  tonic alone to ``weighted_key_score`` is not a valid mir_eval key label and
  silently scores 0.0 (distance 1.0) for every reference, regardless of
  actual closeness.
- **bpm**: ``abs(observed_bpm - reference_bpm) / reference_bpm``. The
  bundle's ``bpm_octave_ambiguous`` flag is carried at the report's top
  level alongside this axis, not folded into the distance.
- **brightness**: the reference's declared class (``dark`` / ``neutral`` /
  ``bright``) is mapped to the same B-3 spectral_centroid band
  ``semantic_ci.core.neutral_band_bounds`` derives from
  ``config/semantic_rules.yaml`` (dark <= 1200 Hz / neutral in between /
  bright >= 2500 Hz at current calibration). Distance is 0 when the
  observed ``spectral_centroid`` falls inside the declared band, otherwise
  the gap to the nearest band edge divided by that edge value. References
  whose declared class does not resolve to one of the three labels are
  ``not_observed`` on this axis.

Beyond the design memo's own two named ``not_observed`` conditions (harmony
without a progression, brightness without a resolvable declared class), this
module also degrades to ``not_observed`` — rather than fabricating a
distance — when the *bundle* itself lacks a sensor reading needed for an
axis (``physical.bpm is None`` / ``physical.key is None`` / ``physical.key``
present but ``physical.mode is None``, since no mir_eval-comparable key
label can be built from a tonic alone) or when a reference's own field
cannot be read as a number (an unresolved
``TODO(transcribe):`` bpm sentinel, or a reference bpm of exactly 0). These
are defensive extensions of the same "not_observed over guessing" posture
the design memo's two named conditions already establish, not new distance
math — the formulas above are unchanged whenever the inputs they need are
actually present.

melody / lyrics / clap are out of scope for this WI2 v0 instrument entirely
(not attempted, not just unobserved for a particular take) — see
``EXCLUDED_DOMAINS``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.compose.loader import load_composition_score
from svp_rpe.compose.models import ChordSpec, CompositionScore
from svp_rpe.keys import weighted_key_score
from svp_rpe.roundtrip.compare import repeated_chord_sequence_match_rate
from svp_rpe.rpe.models import RPEBundle
from svp_rpe.semantic_ci.core import neutral_band_bounds
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.utils.config_loader import load_config

IDENTITY_RANK_REPORT_SCHEMA_VERSION = "identity-rank-report/0.1"
REFERENCE_LIST_SCHEMA_VERSION = "wi2-references/0.1"

Axis = Literal["structure", "harmony", "key", "bpm", "brightness"]
ExcludedDomainName = Literal["melody", "lyrics", "clap"]

_CHORD_SEQUENCE_ARTIFACT_SCHEMA = "chord-sequence/0.1"


# --- reference list input schema --------------------------------------------


class ReferenceEntry(BaseModel):
    """One reference in a WI2 ``references`` YAML.

    ``score_path`` / ``progression_path`` are resolved relative to the
    references YAML file's own directory — the same "paths are relative to
    the manifest that declares them" convention
    ``arrange/identity.py``'s ``IdentityAnchor.artifact`` resolution uses.
    ``progression_path`` is optional: a reference with no authored harmony
    anchor simply reports ``not_observed`` on the harmony axis rather than
    failing the whole run.
    """

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    score_path: str
    progression_path: Optional[str] = None


class ReferenceList(BaseModel):
    """A WI2 reference list: the canonical score, destroyed-anchor decoys,
    and/or another work to rank a take's closeness against."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["wi2-references/0.1"]
    references: list[ReferenceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_ref_ids(self) -> "ReferenceList":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in self.references:
            if entry.ref_id in seen:
                duplicates.add(entry.ref_id)
            seen.add(entry.ref_id)
        if duplicates:
            raise ValueError(
                f"duplicate ref_id(s) in WI2 reference list: {', '.join(sorted(duplicates))}"
            )
        return self


def load_reference_list(path: Path | str) -> ReferenceList:
    """Load and validate a WI2 ``references`` YAML file."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"WI2 reference list must be a mapping: {path}")
    return ReferenceList.model_validate(data)


# --- report schema ------------------------------------------------------------


class RankEntry(BaseModel):
    """One reference's position within a single axis's ranking.

    ``rank`` is the entry's 1-based sequential position after sorting by
    ``(distance, ref_id)`` ascending — ties are broken by ascending
    ``ref_id``, the same stable tie-break
    ``roundtrip/repetition.py``'s ``RepetitionRankingEntry`` ranking uses.
    References ``not_observed`` on this axis are excluded from ``ranking``
    entirely (see ``AxisRanking.not_observed``), so ``rank`` numbers a
    reference among only the other references actually ranked on this axis.

    ``tied_with`` makes the ``ref_id`` tie-break visible in the report
    itself rather than letting a same-distance rank1 read as a genuine
    discrimination: it lists the other ``ref_id``\\ s that share this
    entry's exact (already-rounded) ``distance``, in ascending ``ref_id``
    order, excluding this entry's own ``ref_id``. Empty when this entry's
    distance is unique among the axis's ranking (a real, undisputed win).
    Does not change ``rank`` numbering, the distance formulas, or the
    ``ref_id`` tie-break order above — purely additive reporting of a
    condition the rank/distance fields already imply but do not state.
    """

    model_config = ConfigDict(extra="forbid")

    rank: int
    ref_id: str
    distance: float
    tied_with: list[str] = Field(default_factory=list)


class NotObservedReference(BaseModel):
    """One reference excluded from one axis's ranking, and why."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    reason: str


class AxisRanking(BaseModel):
    """One axis's full ranking + coverage detail. Not a verdict: there is no
    threshold or pass/fail field, only distances and their resulting order."""

    model_config = ConfigDict(extra="forbid")

    axis: Axis
    ranking: list[RankEntry] = Field(default_factory=list)
    not_observed: list[NotObservedReference] = Field(default_factory=list)


class ExcludedDomain(BaseModel):
    """A domain WI2 v0 does not attempt to measure at all (distinct from a
    per-reference ``not_observed`` entry, which is measured-but-absent)."""

    model_config = ConfigDict(extra="forbid")

    domain: ExcludedDomainName
    reason: str


#: Domains this WI2 v0 instrument never attempts to measure, with why —
#: distinct from the per-axis `not_observed` coverage detail above (which
#: records specific references that *were* attempted but lacked the data).
EXCLUDED_DOMAINS: tuple[ExcludedDomain, ...] = (
    ExcludedDomain(domain="melody", reason="#199 により WI2 v0 の計測対象から除外"),
    ExcludedDomain(domain="lyrics", reason="instrumental 入力につき歌詞センサー対象外"),
    ExcludedDomain(
        domain="clap",
        reason="semantic-embed extra (laion-clap) 未導入につき対象外",
    ),
)


class IdentityRankReport(BaseModel):
    """WI2 identity-rank report for a single extracted take.

    Descriptive instrument only: no verdict, threshold, or pass/fail field
    anywhere in this schema (matching ``ScoreAdherenceReport`` /
    ``eval/lyrics_match.match_lyrics``'s docstring contract).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["identity-rank-report/0.1"]
    audio_file: str
    bpm_octave_ambiguous: bool
    reference_ids: list[str] = Field(default_factory=list)
    axes: list[AxisRanking] = Field(default_factory=list)
    excluded_domains: list[ExcludedDomain] = Field(default_factory=list)


# --- structure axis: independent public reimplementation of observe.py's rule -


_TRAILING_DIGITS_PATTERN = re.compile(r"\d+$")
_OBSERVED_NUMBERED_STEM = "verse"


def normalize_reference_section_label(label: str) -> str:
    """Normalize a reference score's ``structure`` section name.

    Same rule as ``arrange/observe.py``'s (module-private)
    ``_normalize_canonical_section_label``: lowercase only, no digit
    stripping — an author-written label is kept verbatim (``verse2`` stays
    ``verse2``). Reimplemented here as a new public function rather than
    importing the private helper (this module does not reach into
    ``observe.py``'s private API, the same restraint ``observe.py`` itself
    documents for its relationship to ``roundtrip.compare``).
    """

    return label.lower()


def normalize_observed_section_label(label: str) -> str:
    """Normalize an observed ``PhysicalRPE.structure`` section label.

    Same rule as ``arrange/observe.py``'s (module-private)
    ``_normalize_observed_section_label``: lowercase, then strip a trailing
    digit only when the lowercased stem is exactly ``verse`` (the only base
    word the extractor's ``assign_labels`` ever numbers). Reimplemented here
    as a new public function for the same reason
    ``normalize_reference_section_label`` is.
    """

    lowered = label.lower()
    stripped = _TRAILING_DIGITS_PATTERN.sub("", lowered)
    if stripped != lowered and stripped == _OBSERVED_NUMBERED_STEM:
        return stripped
    return lowered


def position_match_rate(reference_sections: list[str], observed_sections: list[str]) -> float:
    """Position-aligned match rate between two already-normalized section
    label sequences: matches at the same index / max(len(a), len(b)).

    Same computation ``arrange/observe.py``'s ``_observe_structure`` applies
    (0.0 when both sequences are empty, matching that module's convention).
    """

    max_length = max(len(reference_sections), len(observed_sections))
    if max_length == 0:
        return 0.0
    matches = sum(
        1
        for reference_label, observed_label in zip(reference_sections, observed_sections)
        if reference_label == observed_label
    )
    return matches / max_length


# --- harmony axis: chord-sequence/0.1 reference progression loader ----------


def _load_reference_chord_progression(path: Path) -> list[tuple[str, str]]:
    """Read a WI2 reference's harmony anchor (``chord-sequence/0.1`` JSON)
    into a ``(root, quality)`` sequence.

    Independent reimplementation of ``arrange/observe.py``'s (module-private)
    ``_load_chord_progression`` — same schema, same ``ChordSpec`` reuse for
    root/quality validation, but reading the file directly instead of taking
    already-hashed bytes: WI2 references are not ``IdentityManifest``
    anchors, so there is no upstream sha256 pin to reuse here.
    """

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"chord sequence reference could not be read: {path}: {exc}") from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"chord sequence reference is not valid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"chord sequence reference must be a mapping with a 'chords' key: {path}"
        )
    schema = payload.get("schema")
    if schema != _CHORD_SEQUENCE_ARTIFACT_SCHEMA:
        raise ValueError(
            f"chord sequence reference has unsupported schema {schema!r} "
            f"(expected {_CHORD_SEQUENCE_ARTIFACT_SCHEMA!r}): {path}"
        )
    chords_field = payload.get("chords")
    if not isinstance(chords_field, list):
        raise ValueError(f"chord sequence reference 'chords' must be a list: {path}")
    chords = [ChordSpec.model_validate(item) for item in chords_field]
    return [(chord.root, chord.quality) for chord in chords]


# --- brightness axis: dark/neutral/bright band bounds -----------------------


def _brightness_band_rules() -> list[dict[str, Any]]:
    """Extract the ``dark``/``bright`` ``spectral_centroid`` rules from
    ``config/semantic_rules.yaml`` in the shape ``neutral_band_bounds``
    expects (``{"labels": [...], "bounds": {"min": ..., "max": ...}}``).

    Independent reimplementation of ``semantic_ci.core``'s (module-private)
    ``_semantic_rules_for_feature("spectral_centroid")`` — same config
    source, but narrowed to only the ``perceptual`` layer this axis needs,
    to avoid reaching into that module's private helper. The public
    ``neutral_band_bounds`` this module calls with the result is the actual
    shared helper reused per the design memo.
    """

    try:
        config = load_config("semantic_rules")
    except FileNotFoundError:
        return []
    rules: list[dict[str, Any]] = []
    for raw_rule in config.get("perceptual", []):
        condition = raw_rule.get("condition", {})
        bounds: dict[str, float] = {}
        if "spectral_centroid_min" in condition:
            bounds["min"] = float(condition["spectral_centroid_min"])
        if "spectral_centroid_max" in condition:
            bounds["max"] = float(condition["spectral_centroid_max"])
        if not bounds:
            continue
        labels = [
            item.get("label") if isinstance(item, dict) else item
            for item in raw_rule.get("labels", [])
        ]
        rules.append({"labels": [label for label in labels if label], "bounds": bounds})
    return rules


def _brightness_band_bounds() -> Optional[tuple[float, float]]:
    """``(dark_max, bright_min)`` from ``config/semantic_rules.yaml`` via the
    shared ``neutral_band_bounds`` helper, or ``None`` if either rule is
    absent from config."""

    return neutral_band_bounds(_brightness_band_rules())


# --- reference resolution ----------------------------------------------------


class _ResolvedReference:
    """A reference entry with its score (and optional progression) loaded."""

    __slots__ = ("ref_id", "score", "progression")

    def __init__(
        self,
        ref_id: str,
        score: CompositionScore,
        progression: Optional[list[tuple[str, str]]],
    ) -> None:
        self.ref_id = ref_id
        self.score = score
        self.progression = progression


def _resolve_references(
    references: ReferenceList, *, base_dir: Path
) -> list[_ResolvedReference]:
    resolved: list[_ResolvedReference] = []
    for entry in references.references:
        score = load_composition_score(base_dir / entry.score_path)
        progression: Optional[list[tuple[str, str]]] = None
        if entry.progression_path is not None:
            progression = _load_reference_chord_progression(base_dir / entry.progression_path)
        resolved.append(_ResolvedReference(entry.ref_id, score, progression))
    return resolved


# --- per-axis ranking --------------------------------------------------------


def _rank_axis(
    axis: Axis,
    distances: dict[str, float],
    not_observed_reasons: dict[str, str],
) -> AxisRanking:
    ordered = sorted(distances.items(), key=lambda item: (item[1], item[0]))
    # Group ref_ids sharing the exact same (already-rounded) distance, in the
    # same ascending-ref_id order `ordered` already establishes, so each
    # entry's `tied_with` can be read off without a second sort.
    peers_by_distance: dict[float, list[str]] = {}
    for ref_id, distance in ordered:
        peers_by_distance.setdefault(distance, []).append(ref_id)
    ranking = [
        RankEntry(
            rank=index + 1,
            ref_id=ref_id,
            distance=distance,
            tied_with=[peer for peer in peers_by_distance[distance] if peer != ref_id],
        )
        for index, (ref_id, distance) in enumerate(ordered)
    ]
    not_observed = [
        NotObservedReference(ref_id=ref_id, reason=reason)
        for ref_id, reason in sorted(not_observed_reasons.items())
    ]
    return AxisRanking(axis=axis, ranking=ranking, not_observed=not_observed)


def _structure_axis(bundle: RPEBundle, refs: list[_ResolvedReference]) -> AxisRanking:
    observed_sections = [
        normalize_observed_section_label(marker.label) for marker in bundle.physical.structure
    ]
    distances: dict[str, float] = {}
    for ref in refs:
        reference_sections = [
            normalize_reference_section_label(section.section) for section in ref.score.structure
        ]
        rate = position_match_rate(reference_sections, observed_sections)
        distances[ref.ref_id] = round(1.0 - rate, 4)
    return _rank_axis("structure", distances, {})


def _harmony_axis(bundle: RPEBundle, refs: list[_ResolvedReference]) -> AxisRanking:
    observed_sequence = [(event.root, event.quality) for event in bundle.physical.chord_events]
    distances: dict[str, float] = {}
    not_observed: dict[str, str] = {}
    for ref in refs:
        if ref.progression is None:
            not_observed[ref.ref_id] = (
                "reference declares no progression_path (no harmony anchor to compare)"
            )
            continue
        rate = repeated_chord_sequence_match_rate(ref.progression, observed_sequence)
        distances[ref.ref_id] = round(1.0 - rate, 4)
    return _rank_axis("harmony", distances, not_observed)


def _key_axis(bundle: RPEBundle, refs: list[_ResolvedReference]) -> AxisRanking:
    observed_key = bundle.physical.key
    observed_mode = bundle.physical.mode
    distances: dict[str, float] = {}
    not_observed: dict[str, str] = {}
    if observed_key is None:
        for ref in refs:
            not_observed[ref.ref_id] = "bundle.physical.key is None (key not detected in extraction)"
    elif observed_mode is None:
        for ref in refs:
            not_observed[ref.ref_id] = (
                "bundle.physical.mode is None (key mode not detected in extraction; "
                "a bare tonic is not a valid mir_eval key label)"
            )
    else:
        # The bundle carries tonic and mode as separate fields; the reference
        # score's physical.key is already a single "<tonic> <mode>" string
        # (mir_eval's format). Join the two before scoring so a genuine
        # exact match (e.g. observed "C"/"minor" vs reference "C minor")
        # actually compares as equal instead of failing mir_eval's
        # "(key) (mode)" validation and silently falling back to 0.0.
        observed_key_label = f"{observed_key} {observed_mode}"
        for ref in refs:
            score = weighted_key_score(ref.score.physical.key, observed_key_label).score
            distances[ref.ref_id] = round(1.0 - score, 4)
    return _rank_axis("key", distances, not_observed)


def _bpm_axis(bundle: RPEBundle, refs: list[_ResolvedReference]) -> AxisRanking:
    observed_bpm = bundle.physical.bpm
    distances: dict[str, float] = {}
    not_observed: dict[str, str] = {}
    if observed_bpm is None:
        for ref in refs:
            not_observed[ref.ref_id] = "bundle.physical.bpm is None (bpm not detected in extraction)"
    else:
        for ref in refs:
            reference_bpm_raw = ref.score.physical.bpm
            if is_todo_sentinel(reference_bpm_raw):
                not_observed[ref.ref_id] = (
                    "reference bpm is an unresolved TODO(transcribe) sentinel"
                )
                continue
            reference_bpm = float(reference_bpm_raw)
            if reference_bpm == 0.0:
                not_observed[ref.ref_id] = "reference bpm is zero (distance is undefined)"
                continue
            distances[ref.ref_id] = round(abs(observed_bpm - reference_bpm) / reference_bpm, 4)
    return _rank_axis("bpm", distances, not_observed)


_BRIGHTNESS_LABELS = ("dark", "neutral", "bright")


def _brightness_axis(bundle: RPEBundle, refs: list[_ResolvedReference]) -> AxisRanking:
    observed_centroid = bundle.physical.spectral_centroid
    bounds = _brightness_band_bounds()
    distances: dict[str, float] = {}
    not_observed: dict[str, str] = {}
    for ref in refs:
        declared_raw = ref.score.physical.brightness
        declared = declared_raw.strip().lower()
        if bounds is None:
            not_observed[ref.ref_id] = (
                "brightness band bounds unavailable "
                "(config/semantic_rules.yaml is missing dark/bright rules)"
            )
            continue
        if declared not in _BRIGHTNESS_LABELS:
            not_observed[ref.ref_id] = (
                f"reference declares brightness={declared_raw!r}, "
                "not one of dark/neutral/bright"
            )
            continue
        dark_max, bright_min = bounds
        if declared == "dark":
            distance = (
                0.0
                if observed_centroid <= dark_max
                else round((observed_centroid - dark_max) / dark_max, 4)
            )
        elif declared == "bright":
            distance = (
                0.0
                if observed_centroid >= bright_min
                else round((bright_min - observed_centroid) / bright_min, 4)
            )
        else:  # "neutral"
            if dark_max <= observed_centroid <= bright_min:
                distance = 0.0
            elif observed_centroid < dark_max:
                distance = round((dark_max - observed_centroid) / dark_max, 4)
            else:
                distance = round((observed_centroid - bright_min) / bright_min, 4)
        distances[ref.ref_id] = distance
    return _rank_axis("brightness", distances, not_observed)


# --- top-level entry points --------------------------------------------------


def run_identity_rank(
    bundle: RPEBundle, references: ReferenceList, *, references_dir: Path
) -> IdentityRankReport:
    """Build the WI2 identity-rank report for one extracted take against
    ``references`` (resolved relative to ``references_dir``)."""

    resolved = _resolve_references(references, base_dir=references_dir)
    axes = [
        _structure_axis(bundle, resolved),
        _harmony_axis(bundle, resolved),
        _key_axis(bundle, resolved),
        _bpm_axis(bundle, resolved),
        _brightness_axis(bundle, resolved),
    ]
    return IdentityRankReport(
        schema_version=IDENTITY_RANK_REPORT_SCHEMA_VERSION,
        audio_file=bundle.audio_file,
        bpm_octave_ambiguous=bundle.physical.bpm_octave_ambiguous,
        reference_ids=[ref.ref_id for ref in resolved],
        axes=axes,
        excluded_domains=list(EXCLUDED_DOMAINS),
    )


def identity_rank_from_paths(bundle_path: Path | str, references_path: Path | str) -> IdentityRankReport:
    """CLI entry point: load an extracted RPE bundle JSON + a WI2 references
    YAML from disk and build the report. References resolve relative to
    ``references_path``'s own directory (see ``ReferenceEntry``)."""

    bundle = RPEBundle.model_validate(
        json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    )
    references = load_reference_list(references_path)
    return run_identity_rank(bundle, references, references_dir=Path(references_path).parent)


def render_identity_rank_text(report: IdentityRankReport) -> str:
    """Render a compact markdown report for CLI ``--format text`` output."""

    lines = [
        f"# Identity Rank - {report.audio_file}",
        "",
        f"bpm_octave_ambiguous: {report.bpm_octave_ambiguous}  "
        f"references: {', '.join(report.reference_ids)}",
    ]
    for axis in report.axes:
        lines += [
            "",
            f"## {axis.axis}",
            "",
            "| rank | ref_id | distance | tied_with |",
            "|---:|---|---:|---|",
        ]
        for entry in axis.ranking:
            tied_with = ", ".join(entry.tied_with) if entry.tied_with else "-"
            lines.append(
                f"| {entry.rank} | {entry.ref_id} | {entry.distance} | {tied_with} |"
            )
        if axis.not_observed:
            lines.append("")
            lines.append("not_observed:")
            for item in axis.not_observed:
                lines.append(f"- {item.ref_id}: {item.reason}")

    lines += ["", "## excluded domains (not measured by WI2 v0)", ""]
    for item in report.excluded_domains:
        lines.append(f"- {item.domain}: {item.reason}")

    return "\n".join(lines) + "\n"
