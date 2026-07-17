"""ObservationReport: 生成後の anchor 観測を記録する sidecar 計器（AR4）。

Design Memo D-1 の裁定を貫く: 本モジュールは **計器であって verdict ではない**。
判定条件・閾値は実測後の別 Design Memo が固定するまで存在しない。ここで
`adherence_status` に設定できるのは以下 3 分岐のみ（それ以外の値・閾値判定を
書き加えてはならない）:

- センサー不在（optional extra 未導入・依存ゼロの実装が未配線）→
  ``not_observed`` + ``determination="no_sensor"``
- 完全一致（測定値が恒等 1.0。閾値ではなく「同じか違うか」の事実判定）→
  ``preserved`` + ``determination="exact_match"``
- それ以外（測定はしたが完全一致でない）→ ``not_observed`` のまま
  （``changed_within_policy`` / ``changed_outside_policy`` は本 PR の管轄外）+
  ``determination="deferred"``。生測定値は ``measurements`` に full 記録する

``sensor_blind``（構造的に信号が存在しないと判明している場合専用）は本 PR では
自動判定を配線しない（D-1: 生成器知識の体系化は将来の Design Memo）。

D-2: package（``PerformancePackage``）は書き換えない。observation は新規
sidecar として発行する — package 自身の ``observation.status``
（``not_observed`` 固定）は本モジュールの対象外のまま不変。

D-4: 本 PR で実配線するセンサーは harmony のみ。``compute_chord_events``
（ルールベース・依存ゼロ）で生成音声から和声を実測し、``IdentityManifest`` の
harmony anchor（正典進行）と突き合わせる。measurements には従来の生値
（``chord_sequence_match_rate`` / ``repeated_chord_sequence_match_rate``。
どちらも frame 単位の生 chord_events 列に対する位置整合比較で、進行が繰り返し
演奏される前提の元では低く出やすい — 透明性のため残すが D-1 の恒等判定の
根拠には使わない）に加え、繰り返しを織り込んだ **collapsed cycle-alignment**
の系列を記録する: 生 chord_events を隣接重複 collapse した列
（``collapsed_observed_length``）を、正典進行の無限交代列（サイクル境界の
隣接重複 collapse を織り込み済み）と先頭から位置整合させ、最初に食い違うまでの
長さを ``matched_cycle_prefix_length`` として記録する。D-1 の恒等判定は
**この prefix が collapsed 列の全長と一致するか**（``collapsed_observed_length
> 0 and matched_cycle_prefix_length == collapsed_observed_length``）で行う —
「作品の和声的同一性 = 繰り返される正典進行」という計器意味論に合わせた基準
であり、frame 単位の生 match_rate はもはや恒等判定の根拠にしない。

lyrics / melody は optional extra 依存（faster-whisper / basic-pitch）のため
センサー本体を配線せず、``available=false`` + reason を記録する（将来の接続点は
``eval/lyrics_match.py`` / ``rpe/learned/lyrics_adapter.py`` と
``rpe/learned/basic_pitch_adapter.py``）。他の domain（rhythm/structure/motif）
も同様に no_sensor。

harmony measurements の note は事実（一致した cycle 数・食い違った tail の
長さ）のみを記述し、食い違いの原因についての解釈（例: ドローン区間のセンサー
雑音）は書かない — 解釈は docs 側の役割（``docs/cli.md`` の `observe` 節 /
`docs/arrangement_identity_planning.md`）。

再観測についての可変性の規律: sidecar ファイルの出力先が既存ファイルであれば
上書きしてよい（observation report は「今 measured したもの」を表す再観測可能な
値であり、package/report の byte-pin 不変性とは別の性質を持つ）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.arrange.identity import (
    AnchorDomain,
    IdentityAnchor,
    IdentityManifest,
    _resolve_confined,
)
from svp_rpe.arrange.models import JsonValue
from svp_rpe.arrange.package import AdherenceStatus, PerformancePackage
from svp_rpe.compose.models import ChordSpec
from svp_rpe.roundtrip.compare import (
    chord_sequence_match_rate,
    repeated_chord_sequence_match_rate,
)
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.rpe.models import RPEBundle

OBSERVATION_REPORT_SCHEMA_VERSION = "observation-report/0.1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

Determination = Literal["exact_match", "deferred", "no_sensor"]

# D-4: lyrics / melody はこの PR ではセンサー本体を配線しない（optional extra
# 依存）。sensor 名 + 不在理由（将来の接続点込み）のみ固定で記録する。
_NO_SENSOR_INFO: dict[str, tuple[str, str]] = {
    "lyrics": (
        "lyrics_transcription",
        "lyrics sensor not wired in this PR (requires the 'lyrics' extra / "
        "faster-whisper); future connection point: eval/lyrics_match.py + "
        "rpe/learned/lyrics_adapter.py",
    ),
    "melody": (
        "note_events",
        "melody sensor not wired in this PR (requires the 'basic-pitch' extra); "
        "future connection point: rpe/learned/basic_pitch_adapter.py",
    ),
}


class ObserveModel(BaseModel):
    """observe 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class SensorRecord(ObserveModel):
    """1 anchor に配線されている（またはいない）センサーの自己記述。"""

    name: str
    available: bool
    reason: Optional[str] = None


class GeneratedArtifactRef(ObserveModel):
    """観測対象の生成物（音声）の provenance 記録。"""

    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)


class AnchorObservation(ObserveModel):
    """1 anchor 分の観測記録。"""

    anchor_id: str
    domain: AnchorDomain
    sensor: SensorRecord
    measurements: dict[str, JsonValue] = Field(default_factory=dict)
    adherence_status: AdherenceStatus
    determination: Determination
    note: Optional[str] = None


class ObservationReport(ObserveModel):
    """work 単位の ObservationReport。verdict 系・集計系フィールドは持たない。"""

    schema_version: Literal["observation-report/0.1"] = OBSERVATION_REPORT_SCHEMA_VERSION
    work_id: str
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    generated_artifact: GeneratedArtifactRef
    anchors: list[AnchorObservation]


def _load_chord_progression(path: Path) -> list[tuple[str, str]]:
    """harmony anchor artifact (``chord-sequence/0.1``) を (root, quality) 列へ変換する。

    ``ChordSpec`` を再利用して root/quality を検証する（CHORD_NAMES / Literal
    と同じ規約を anchor artifact にも適用し、手書きパースの重複を避ける）。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"chord sequence artifact is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or "chords" not in payload:
        raise ValueError(
            f"chord sequence artifact must be a mapping with a 'chords' key: {path}"
        )
    chords = [ChordSpec.model_validate(item) for item in payload["chords"]]
    return [(chord.root, chord.quality) for chord in chords]


def _collapse_adjacent(sequence: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse immediately-repeated entries (same operation `repeated_chord_sequence_match_rate`
    applies internally; reimplemented locally so this module doesn't reach into
    `roundtrip.compare`'s private helper)."""
    collapsed: list[tuple[str, str]] = []
    for chord in sequence:
        if not collapsed or collapsed[-1] != chord:
            collapsed.append(chord)
    return collapsed


def _cycle_alignment(
    canonical: list[tuple[str, str]],
    collapsed_observed: list[tuple[str, str]],
) -> tuple[int, int]:
    """Match `collapsed_observed`'s prefix against the canonical progression's
    infinite alternation (repeating `canonical` forever, with adjacent-duplicate
    collapse applied across the cycle boundary too — the same collapse rule
    `_collapse_adjacent` applies to the observed side).

    Returns ``(matched_prefix_length, full_cycles_matched)``: the length of the
    longest prefix of `collapsed_observed` that matches the canonical
    alternation continuously from the start (stopping at the first mismatch,
    not resuming past it), and how many full passes through `canonical` that
    prefix consumed (counting the cycle-boundary collapse — i.e. 2 cycles of a
    4-chord canonical progression collapse to 7 entries, not 8, when the first
    and last chord are identical).
    """
    if not canonical or not collapsed_observed:
        return 0, 0
    length = len(canonical)
    raw_index = 0
    last_chord: tuple[str, str] | None = None
    matched = 0
    matched_raw_index = 0
    for observed_chord in collapsed_observed:
        while True:
            expected_chord = canonical[raw_index % length]
            raw_index += 1
            if expected_chord != last_chord:
                break
        if expected_chord != observed_chord:
            break
        last_chord = expected_chord
        matched += 1
        matched_raw_index = raw_index
    return matched, matched_raw_index // length


def _observe_harmony(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    bundle: RPEBundle,
) -> AnchorObservation:
    artifact_path = _resolve_confined(
        anchor.artifact, manifest_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
    )
    expected_sequence = _load_chord_progression(artifact_path)
    observed_sequence = [
        (event.root, event.quality) for event in bundle.physical.chord_events
    ]
    match_rate = chord_sequence_match_rate(expected_sequence, observed_sequence)
    repeated_match_rate = repeated_chord_sequence_match_rate(
        expected_sequence, observed_sequence
    )
    collapsed_observed = _collapse_adjacent(observed_sequence)
    collapsed_observed_length = len(collapsed_observed)
    matched_prefix_length, full_cycles = _cycle_alignment(expected_sequence, collapsed_observed)
    unmatched_tail = collapsed_observed[matched_prefix_length : matched_prefix_length + 8]
    unmatched_tail_length = collapsed_observed_length - matched_prefix_length
    collapsed_match_fraction = (
        round(matched_prefix_length / collapsed_observed_length, 4)
        if collapsed_observed_length
        else 0.0
    )

    measurements: dict[str, JsonValue] = {
        "chord_sequence_match_rate": round(match_rate, 4),
        "repeated_chord_sequence_match_rate": round(repeated_match_rate, 4),
        "canonical_length": len(expected_sequence),
        "observed_length": len(observed_sequence),
        "collapsed_observed_length": collapsed_observed_length,
        "matched_cycle_prefix_length": matched_prefix_length,
        "collapsed_match_fraction": collapsed_match_fraction,
        "unmatched_tail_length": unmatched_tail_length,
        "unmatched_tail_head": [[root, quality] for root, quality in unmatched_tail],
    }
    sensor = SensorRecord(name="chord_sequence_match", available=True, reason=None)
    if collapsed_observed_length > 0 and matched_prefix_length == collapsed_observed_length:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=sensor,
            measurements=measurements,
            adherence_status="preserved",
            determination="exact_match",
            note=None,
        )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=sensor,
        measurements=measurements,
        adherence_status="not_observed",
        determination="deferred",
        note=(
            f"collapsed observed prefix matches {full_cycles} full canonical "
            f"cycle(s); {unmatched_tail_length} trailing entries fall outside "
            "the canonical alternation. changed_within_policy/"
            "changed_outside_policy classification is out of scope for this "
            "instrument and deferred to a future threshold Design Memo (D-1)."
        ),
    )


def _observe_unavailable(anchor: IdentityAnchor) -> AnchorObservation:
    name, reason = _NO_SENSOR_INFO.get(
        anchor.domain,
        (f"{anchor.domain}_sensor", f"no sensor implemented for domain '{anchor.domain}'"),
    )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=SensorRecord(name=name, available=False, reason=reason),
        measurements={},
        adherence_status="not_observed",
        determination="no_sensor",
        note=None,
    )


def _observe_anchor(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    bundle: RPEBundle,
) -> AnchorObservation:
    if anchor.domain == "harmony":
        return _observe_harmony(anchor, manifest_dir=manifest_dir, work_id=work_id, bundle=bundle)
    return _observe_unavailable(anchor)


def build_observation_report(
    *,
    package: PerformancePackage,
    manifest: IdentityManifest,
    manifest_path: Path,
    audio_path: Path,
    package_sha256: str,
    audio_sha256: str,
    generated_artifact_path: str,
) -> ObservationReport:
    """全 anchor の観測を組み立てる。抽出は音声 1 回のみ（全 anchor で共有）。

    provenance chain の検証（manifest sha256 / anchor artifact hash 照合）は
    呼び出し側（CLI の D-3 実装）の責務であり、本関数は検証済みの
    ``manifest`` / ``package_sha256`` / ``audio_sha256`` を受け取るだけで、
    ここではファイル hash の比較を一切行わない。
    """
    bundle = extract_rpe_from_file(str(audio_path))
    manifest_dir = Path(manifest_path).resolve().parent
    anchors = [
        _observe_anchor(
            anchor, manifest_dir=manifest_dir, work_id=manifest.meta.work_id, bundle=bundle
        )
        for anchor in manifest.anchors
    ]
    return ObservationReport(
        schema_version=OBSERVATION_REPORT_SCHEMA_VERSION,
        work_id=package.work_id,
        package_sha256=package_sha256,
        generated_artifact=GeneratedArtifactRef(
            path=generated_artifact_path, sha256=audio_sha256
        ),
        anchors=anchors,
    )
