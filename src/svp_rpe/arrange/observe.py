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

D-4: 本 PR で実配線するセンサーは harmony のみ。センサーは domain 単独ではなく
**(domain, artifact_type) の対**に結び付く（2026-07-17 round 5）:
``domain == "harmony" and artifact_type == "chord_sequence_json"`` の場合のみ
実配線し、それ以外（domain=harmony だが artifact_type が異なる anchor —
identity schema 上は合法な組み合わせ、例 ``audio_excerpt``）は run を落とさず
no_sensor 扱いにする（``available=false`` + artifact_type を含む reason）。
実センサーは ``compute_chord_events``（ルールベース・依存ゼロ）で生成音声から
和声を実測し、``IdentityManifest`` の harmony anchor（正典進行）と突き合わせる。
measurements には従来の生値
（``chord_sequence_match_rate`` / ``repeated_chord_sequence_match_rate``。
どちらも frame 単位の生 chord_events 列に対する位置整合比較で、進行が繰り返し
演奏される前提の元では低く出やすい — 透明性のため残すが D-1 の恒等判定の
根拠には使わない）に加え、繰り返しを織り込んだ **collapsed cycle-alignment**
の系列を記録する: 生 chord_events を隣接重複 collapse した列
（``collapsed_observed_length``）を、正典進行の無限交代列（サイクル境界の
隣接重複 collapse を織り込み済み）と先頭から位置整合させ、最初に食い違うまでの
長さを ``matched_cycle_prefix_length`` として記録する。D-1 の恒等判定は
**この prefix が collapsed 列の全長と一致し、かつ最低 1 完全サイクル分を
観測できているか**（``collapsed_observed_length > 0 and
matched_cycle_prefix_length == collapsed_observed_length and full_cycles >= 1``）
で行う —「作品の和声的同一性 = 繰り返される正典進行」という計器意味論に合わせた
基準であり、frame 単位の生 match_rate はもはや恒等判定の根拠にしない。
``full_cycles >= 1`` を要求するのは、ドローン・切断された出力が正典進行の
proper prefix（例: 1 コードのみの collapsed 列）に完全一致してしまうケースを
「保存成功」と誤認しないため（2026-07-17 round 3: 正典 1 サイクルにも満たない
観測は、prefix が完全一致していても deferred のまま — 未観測に近い状態を
"preserved" と偽称しない）。

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


CHORD_SEQUENCE_ARTIFACT_SCHEMA = "chord-sequence/0.1"


def _load_chord_progression(raw_bytes: bytes, *, artifact_path: Path) -> list[tuple[str, str]]:
    """harmony anchor artifact (``chord-sequence/0.1``) を (root, quality) 列へ変換する。

    ``raw_bytes`` は呼び出し側が既に hash 照合済みの bytes を渡す（本関数は一切
    ファイルを読まない — 二重読み込みを避けるため、PR #187 review round 2）。
    ``artifact_path`` はエラーメッセージの表示にのみ使う。``schema`` キーは
    ``CHORD_SEQUENCE_ARTIFACT_SCHEMA`` の既知値ちょうどでなければ fail-closed で
    拒否する（欠落・未知値のどちらも ``ValueError``。将来 artifact スキーマが
    ``chord-sequence/0.2`` 等へ進む際に、無警告で誤読させないための Safety Gate）。
    ``ChordSpec`` を再利用して root/quality を検証する（CHORD_NAMES / Literal
    と同じ規約を anchor artifact にも適用し、手書きパースの重複を避ける）。
    """
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"chord sequence artifact is not valid JSON: {artifact_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"chord sequence artifact must be a mapping with a 'chords' key: {artifact_path}"
        )
    schema = payload.get("schema")
    if schema != CHORD_SEQUENCE_ARTIFACT_SCHEMA:
        raise ValueError(
            f"chord sequence artifact has unsupported schema {schema!r} "
            f"(expected {CHORD_SEQUENCE_ARTIFACT_SCHEMA!r}): {artifact_path}"
        )
    if "chords" not in payload:
        raise ValueError(
            f"chord sequence artifact must be a mapping with a 'chords' key: {artifact_path}"
        )
    chords_field = payload["chords"]
    if not isinstance(chords_field, list):
        # PR #187 review round 7: without this check, `chords: null` would
        # crash with an uncaught TypeError iterating None (not one of the
        # types the CLI catches), and other non-list values (a mapping, a
        # string — whose characters would silently become bogus per-item
        # validation attempts) would produce a confusing pydantic error
        # instead of a direct, on-topic message.
        raise ValueError(f"chord sequence artifact 'chords' must be a list: {artifact_path}")
    chords = [ChordSpec.model_validate(item) for item in chords_field]
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
    prefix consumed (counting collapse: any run of consecutive raw canonical
    positions holding the same chord — whether that run sits entirely inside
    one nominal cycle, e.g. `canonical=[C, G, G]`, or straddles the
    cycle-boundary repeat, e.g. 2 cycles of a 4-chord progression whose first
    and last chord are identical collapsing to 7 entries, not 8 — collapses to
    a single matched entry and must count as consumed raw positions for
    `full_cycles`, not just the first raw position where that entry was
    found).

    round 4 fix: the match loop's in-loop skip (searching for the *next*
    distinct entry while looking past duplicates of the *previous* matched
    entry) never looks past the *last* matched entry once the loop has
    already stopped (exhausted `collapsed_observed`, or about to mismatch).
    So a canonical progression with a trailing internal duplicate right after
    the last matched position (`[C, G, G]` matched by `[C, G]`) undercounted
    `full_cycles` by leaving those trailing repeats un-consumed in
    `matched_raw_index`. The loop below applies the exact same collapse rule
    once more, after the main loop, starting from wherever it left off —
    bounded to `length` steps so a degenerate all-identical `canonical` can't
    spin forever.

    round 8 fix: the in-loop search for the *next distinct* canonical entry
    (inside the per-observed-chord loop) had no such bound and could hang —
    if `canonical` collapses to a single repeated chord (e.g. `[C, C]`), that
    search never finds anything different from `last_chord` and loops
    forever. It is now bounded to the same `length` steps the round-4
    trailing skip already uses: if no distinct entry turns up within one full
    cycle, the current `observed_chord` cannot possibly be explained by the
    canonical alternation (there is nothing else `canonical` could ever
    produce), so matching stops there and that chord starts the unmatched
    tail — the same outcome an ordinary mismatch produces.
    """
    if not canonical or not collapsed_observed:
        return 0, 0
    length = len(canonical)
    raw_index = 0
    last_chord: tuple[str, str] | None = None
    matched = 0
    matched_raw_index = 0
    for observed_chord in collapsed_observed:
        found_distinct = False
        expected_chord = last_chord
        for _ in range(length):
            expected_chord = canonical[raw_index % length]
            raw_index += 1
            if expected_chord != last_chord:
                found_distinct = True
                break
        if not found_distinct:
            break
        if expected_chord != observed_chord:
            break
        last_chord = expected_chord
        matched += 1
        matched_raw_index = raw_index
    trailing_steps = 0
    while (
        matched > 0
        and trailing_steps < length
        and canonical[matched_raw_index % length] == last_chord
    ):
        matched_raw_index += 1
        trailing_steps += 1
    return matched, matched_raw_index // length


def _observe_harmony(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    bundle: RPEBundle,
    artifact_bytes: bytes,
) -> AnchorObservation:
    # Resolved only for error-message display — never read here. `artifact_bytes`
    # is the same bytes the manifest loader already hashed (PR #187 review
    # round 2: no second read of the artifact file).
    artifact_path = _resolve_confined(
        anchor.artifact, manifest_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
    )
    expected_sequence = _load_chord_progression(artifact_bytes, artifact_path=artifact_path)
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
        # PR #187 review round 9: recorded on both branches — the value that
        # drives the D-1 identity gate (`full_cycles >= 1`) must be
        # independently auditable from the sidecar itself (self-description
        # principle), not only inferable from the note text.
        "full_cycles": full_cycles,
    }
    sensor = SensorRecord(name="chord_sequence_match", available=True, reason=None)
    full_prefix_match = (
        collapsed_observed_length > 0 and matched_prefix_length == collapsed_observed_length
    )
    # round 3: a full prefix match alone is not enough — a drone/truncated
    # output can collapse to a proper prefix of the canonical progression
    # (e.g. a single chord) and match it exactly without ever completing one
    # full cycle. That is not "preserved" (it barely observed anything), so
    # `preserved` additionally requires at least one full canonical cycle.
    if full_prefix_match and full_cycles >= 1:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=sensor,
            measurements=measurements,
            adherence_status="preserved",
            determination="exact_match",
            # round 9: state the fact the identity gate relied on, instead of
            # leaving preserved's basis only in the measurements dict.
            note=(
                "collapsed observed sequence matches the canonical alternation "
                f"exactly ({full_cycles} full cycle(s))."
            ),
        )
    if full_prefix_match:
        note = (
            "collapsed observed sequence matches the canonical progression "
            "exactly, but the matched prefix covers less than one full "
            f"canonical cycle ({matched_prefix_length}/{len(expected_sequence)} "
            "canonical chords); changed_within_policy/changed_outside_policy "
            "classification is out of scope for this instrument and deferred "
            "to a future threshold Design Memo (D-1)."
        )
    else:
        note = (
            f"collapsed observed prefix matches {full_cycles} full canonical "
            f"cycle(s); {unmatched_tail_length} trailing entries fall outside "
            "the canonical alternation. changed_within_policy/"
            "changed_outside_policy classification is out of scope for this "
            "instrument and deferred to a future threshold Design Memo (D-1)."
        )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=sensor,
        measurements=measurements,
        adherence_status="not_observed",
        determination="deferred",
        note=note,
    )


def _observe_unavailable(anchor: IdentityAnchor) -> AnchorObservation:
    if anchor.domain == "harmony":
        # Reached only when domain == "harmony" but artifact_type isn't
        # "chord_sequence_json" (PR #187 review round 5): the harmony sensor
        # is wired to that specific (domain, artifact_type) pair, not to the
        # domain alone. Other harmony-domain artifact types (e.g.
        # audio_excerpt) are legal per the identity schema but have no sensor
        # wired in this PR.
        name = f"{anchor.domain}_sensor"
        reason = (
            f"no sensor wired for harmony anchors with artifact_type "
            f"{anchor.artifact_type!r}"
        )
    else:
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
    artifact_bytes_by_id: dict[str, bytes],
) -> AnchorObservation:
    if anchor.domain == "harmony" and anchor.artifact_type == "chord_sequence_json":
        return _observe_harmony(
            anchor,
            manifest_dir=manifest_dir,
            work_id=work_id,
            bundle=bundle,
            artifact_bytes=artifact_bytes_by_id[anchor.id],
        )
    return _observe_unavailable(anchor)


def build_observation_report(
    *,
    package: PerformancePackage,
    manifest: IdentityManifest,
    manifest_path: Path,
    artifact_bytes: dict[str, bytes],
    audio_path: Path,
    package_sha256: str,
    audio_sha256: str,
    generated_artifact_path: str,
) -> ObservationReport:
    """全 anchor の観測を組み立てる。抽出は音声 1 回のみ（全 anchor で共有）。

    provenance chain の検証（manifest sha256 / anchor artifact hash 照合）は
    呼び出し側（CLI の D-3 実装）の責務であり、本関数は検証済みの
    ``manifest`` / ``package_sha256`` / ``audio_sha256`` を受け取るだけで、
    ここではファイル hash の比較を一切行わない。``artifact_bytes`` も同様に
    呼び出し側が既に hash 照合済みの値（``parse_identity_manifest_with_artifacts``
    の戻り値）を渡す — anchor artifact をここで再度読むことはない
    （harmony センサーが `anchor_id -> bytes` で参照する。PR #187 review
    round 2）。
    """
    bundle = extract_rpe_from_file(str(audio_path))
    manifest_dir = Path(manifest_path).resolve().parent
    anchors = [
        _observe_anchor(
            anchor,
            manifest_dir=manifest_dir,
            work_id=manifest.meta.work_id,
            bundle=bundle,
            artifact_bytes_by_id=artifact_bytes,
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
