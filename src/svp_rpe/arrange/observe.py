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

D-4: センサーは domain 単独ではなく
**(domain, artifact_type) の対**に結び付く（2026-07-17 round 5）。harmony:
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

structure（2026-07-19、AR2-3 解凍条件 (a) — Design Memo
`design_memo_structure_sensor.md`。0.2 対応は本体 AR2-3 — Design Memo
`design_memo_ar2_3.md`）: ``domain == "structure" and artifact_type ==
"section_map"`` の対に加え、anchor の宣言 ``format_version`` が
``section-map/0.1`` / ``section-map/0.2`` のいずれかである場合にのみ実配線する
（`is_structure_sensor_anchor`）。正典は宣言された format_version に対応する
schema で parse する: ``section-map/0.1`` artifact（`sections`: 非空の文字列
リスト、id 概念なし、順序が正典）、または ``section-map/0.2`` artifact
（`sections`: 非空の `{id, label}` オブジェクト列、id は artifact 内一意、
順序が正典）。観測は両形式共通で ``PhysicalRPE.structure``
（``SectionMarker.label`` 列、extract で常に populate 済み）。harmony の
cycle-alignment のような繰り返し折り畳みは行わない（structure には
「繰り返し正典進行」に相当する周期構造の前提が無いため） — 両側を正規化
（lowercase 化 + 末尾数字 strip。それ以外の変換はしない）した上で先頭から
位置整合させ、一致数 / max(正典長, 観測長) を ``position_match_rate`` として
記録する（0 除算は 0.0）。D-1 の恒等判定は
**正規化後の列が長さ・順序とも完全一致するか**
（``sequence_exact_match``）のみで行う — 閾値化した ``position_match_rate``
を恒等判定には使わない。0.2 anchor はさらに ``canonical_section_ids``
（artifact 記載順の id 列・生値）を measurements に記録するが、これは
透明性のための記録であり D-1 の恒等判定には一切使わない（0.1 anchor の
measurements は従来どおり ``canonical_section_ids`` を持たない — 形式間の
measurements shape の違いは format_version 宣言そのものから読み取れる）。
anchor の ``format_version`` 宣言と artifact 内 ``schema_version`` の不一致
（例: 宣言は 0.1 なのに内容は 0.2）は、宣言に対応する schema の parser が
``schema_version`` を Literal で検証するため自然に fail-closed になる
（harmony の ``chord-sequence/0.1`` と同じ posture）。artifact_type が
``section_map`` でない structure anchor（identity schema 上は合法。例
``audio_excerpt``）や、``section-map/0.1``/``section-map/0.2`` のいずれでも
ない format_version（未宣言含む）を持つ structure anchor は harmony 同様
no_sensor のまま（domain 単独ではなく (domain, artifact_type,
format_version) の組でルーティングする一貫性）。

lyrics / melody（WI0-a、2026-07-20）: ``domain == "lyrics" and artifact_type ==
"lyrics_text"`` / ``domain == "melody" and artifact_type == "note_events_json"``
の対にセンサーを実配線した（``is_lyrics_sensor_anchor`` / ``is_melody_sensor_anchor``、
harmony と同型の (domain, artifact_type) 2 要素ゲート — structure と異なり
format_version までは絞らない。他 domain の他形式 anchor 前例が section_map に
あったのと違い、``lyrics_text``/``note_events_json`` にはその種の前例が本リポジトリに
無いため。note-events artifact 自身の ``schema`` フィールドが ``note-events/0.1``
ちょうどでなければパーサ内部で fail-closed になる — harmony の
``chord-sequence/0.1`` と同じ posture）。どちらも optional extra
（``lyrics`` = faster-whisper + Demucs / ``pitch`` = basic-pitch）に依存するため、
未導入環境では呼び出し時に ``LearnedModelUnavailable``（lyrics はボーカル分離の
``SeparatorNotAvailableError`` も）を捕捉して ``available=false`` + reason
（例外メッセージそのもの、インストールヒント込み）へ degrade する — 各アダプタが
実際に送出する専用例外クラスを補足する契約（``cli/roundtrip_cmd.py`` /
``cli/extract_cmd.py`` の既存呼び出し側と同じ例外捕捉の型）。他 domain
（rhythm/motif）は引き続き no_sensor（structure は上記のとおり実配線済み）。

lyrics センサー（``_observe_lyrics``）は ``eval/lyrics_match.match_lyrics``
（learned import ゼロ）をそのまま呼ぶだけで新しい指標は作らない。転写
（faster-whisper。``ensure_lyrics_available()`` で probe してから呼ぶ —
``cli/roundtrip_cmd.py`` の既存呼び出し側と同型。未導入なら Demucs 分離に
入らず即 degrade）は分離ヘルパー ``_transcribe_lyrics_for_observation`` に
隔離する。恒等判定は ``match_lyrics`` が使うのと同じ正規化
（``eval/lyrics_match.char_level_normalize``）を canonical 全文連結と
transcribed 全文それぞれへ適用した上での**文字列等値**で行う（Codex P2,
WI0-a review）。``match_lyrics`` が返す ``overall_similarity`` は同じ
正規化済み文字列同士の非部分一致比率だが 4 桁に丸めて返るため、丸めで
``1.0`` に繰り上がる非同一ペアを exact_match と誤判定し得る — measurements
には温存するが恒等判定の根拠には使わない。per-line ``best_ratio`` は
意図的に順序無視の存在確認読みなので（``match_lyrics`` 自身の docstring）、
これも恒等判定の根拠には使わない。

melody センサー（``_observe_melody``）の正典は ``note-events/0.1``
（``{"schema": "note-events/0.1", "notes": [{"start_beat", "pitch",
"duration_beats"}, ...]}``、beat 単位・音名文字列）— ``start_beat`` 昇順
（onset 順）に並べ替えた上で音名文字列（例 ``"Eb4"``）を MIDI 整数へ変換する
（♯/♭/オクターブ対応の純粋関数 ``_note_name_to_midi`` を本モジュールに新設 —
``perform/performer.py`` の ``PITCH_CLASSES``/``parse_key`` は「ルート音名 +
major/minor」のキー文字列専用で、オクターブ付き音名文字列は扱わないため
再利用不可と確認した上での新設）。観測側は
``rpe/learned/basic_pitch_adapter.extract_basic_pitch_annotations`` の
``LearnedNoteEvent`` 列を ``start_sec`` 昇順に並べた ``pitch_midi`` 整数列 —
**v0 は音高系列のみで拍↔秒の時間整列は行わない**（beat→sec 変換には BPM 推定を
挟む必要があり、それ自体が交絡要因になるため意図的に見送る。時間整列と実推論
精度の実測は WI0-b の対象）。比較は本モジュール新設の LCS（最長共通部分列）
実装 ``_lcs_length`` を pitch 系列・隣接差分（interval）系列の双方に適用し、
``pitch_lcs_ratio`` / ``interval_lcs_ratio`` として記録する — 恒等判定
（``pitch_sequence_exact_match``）は系列の完全一致のみで行う。

harmony / structure の measurements の note は事実（一致した cycle 数・食い違った
tail の長さ、あるいは正規化後の列が一致したかどうか）のみを記述し、食い違いの
原因についての解釈（例: ドローン区間のセンサー雑音）は書かない — 解釈は docs
側の役割（``docs/cli.md`` の `observe` 節 / `docs/arrangement_identity_planning.md`）。

再観測についての可変性の規律: sidecar ファイルの出力先が既存ファイルであれば
上書きしてよい（observation report は「今 measured したもの」を表す再観測可能な
値であり、package/report の byte-pin 不変性とは別の性質を持つ）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.arrange.identity import (
    AnchorDomain,
    IdentityAnchor,
    IdentityManifest,
    _resolve_confined,
)
from svp_rpe.arrange.models import JsonValue
from svp_rpe.arrange.package import PerformancePackage
from svp_rpe.arrange.section_map import (
    SECTION_MAP_ARTIFACT_SCHEMA_0_1,
    SECTION_MAP_ARTIFACT_SCHEMA_0_2,
    parse_section_map_artifact_0_1,
    parse_section_map_artifact_0_2,
)
from svp_rpe.compose.models import ChordSpec
from svp_rpe.eval.lyrics_match import char_level_normalize, match_lyrics
from svp_rpe.roundtrip.compare import (
    chord_sequence_match_rate,
    repeated_chord_sequence_match_rate,
)
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.rpe.models import RPEBundle

OBSERVATION_REPORT_SCHEMA_VERSION = "observation-report/0.1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

Determination = Literal["exact_match", "deferred", "no_sensor"]

# observation-report/0.1 versioning policy (PR #187 review round 12): this
# schema version only narrows to the 2 status values D-1's 3 branches can
# actually emit today. `package.py`'s `AdherenceStatus` (5 values:
# preserved / changed_within_policy / changed_outside_policy / sensor_blind /
# not_observed) is the wider *generation-delivery* vocabulary — reusing it
# here left 3 values this instrument never publishes (changed_within_policy,
# changed_outside_policy, sensor_blind) sitting dead in the schema, readable
# as if a fixture-tampered or hand-authored sidecar could legitimately carry
# them. `changed_*` is out of scope until a future threshold Design Memo
# defines it (D-1), and `sensor_blind`'s automatic determination is likewise
# deferred (D-1) — both are reserved for a future `observation-report/0.2`
# once that Memo lands, not silently accepted now. `package.py`'s own
# `AdherenceStatus` is unaffected — this narrowing is local to the sidecar.
ObservationAdherenceStatus = Literal["preserved", "not_observed"]

# D-4: lyrics / melody sensors (WI0-a) are wired only for the (domain,
# artifact_type) pair `is_lyrics_sensor_anchor` / `is_melody_sensor_anchor`
# actually route — this dict is the fallback for anchors whose domain matches
# but whose artifact_type doesn't (predicate non-match; kept exactly as the
# harmony/structure precedent's `_observe_unavailable` fallback shape). It is
# NOT reached for the dependency-missing case anymore — that now degrades to
# no_sensor from inside `_observe_lyrics`/`_observe_melody` themselves, with
# the adapter's own exception message as `reason` (see the module docstring).
_NO_SENSOR_INFO: dict[str, tuple[str, str]] = {
    "lyrics": (
        "lyrics_transcription",
        "lyrics sensor is wired only for artifact_type == 'lyrics_text' anchors "
        "(requires the 'lyrics' extra / faster-whisper at runtime); "
        "see eval/lyrics_match.py + rpe/learned/lyrics_adapter.py",
    ),
    "melody": (
        "note_events",
        "melody sensor is wired only for artifact_type == 'note_events_json' "
        "anchors (requires the 'basic-pitch' extra at runtime); "
        "see rpe/learned/basic_pitch_adapter.py",
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
    adherence_status: ObservationAdherenceStatus
    determination: Determination
    note: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status_determination_pair(self) -> "AnchorObservation":
        """Fail-closed pairing enforcement (PR #187 review round 12): a
        tampered or hand-authored sidecar (or a future code path) must not be
        able to read back as, say, `adherence_status="preserved"` paired with
        `determination="deferred"` — the two fields must agree on which D-1
        branch produced this observation, on write *and* on read-back
        (`model_validate` re-runs this too, since it's an `AnchorObservation`
        field validator, not a builder-only check)."""
        if self.adherence_status == "preserved" and self.determination != "exact_match":
            raise ValueError(
                "AnchorObservation: adherence_status='preserved' requires "
                f"determination='exact_match', got {self.determination!r}"
            )
        if self.adherence_status == "not_observed" and self.determination not in (
            "no_sensor",
            "deferred",
        ):
            raise ValueError(
                "AnchorObservation: adherence_status='not_observed' requires "
                f"determination in ('no_sensor', 'deferred'), got {self.determination!r}"
            )
        return self


class ObservationReport(ObserveModel):
    """work 単位の ObservationReport。verdict 系・集計系フィールドは持たない。

    ``schema_version`` は ``IdentityManifest`` と同型で default を持たない
    必須入力（PR #187 review round 14）: pydantic default 補完だと欠落
    フィールドの sidecar が黙って「現行 0.1」として読み戻れてしまい、Safety
    Gate の「未知/欠落 schema_version 拒否」原則に反する。発行側
    (`build_observation_report`) は従来どおり ``OBSERVATION_REPORT_SCHEMA_VERSION``
    を明示的に渡す — 変わるのは読み戻し（`model_validate`）の fail-closed 性のみ。
    """

    schema_version: Literal["observation-report/0.1"]
    work_id: str
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    generated_artifact: GeneratedArtifactRef
    anchors: list[AnchorObservation]

    @model_validator(mode="after")
    def _validate_unique_anchor_ids(self) -> "ObservationReport":
        """`IdentityManifest._validate_unique_anchor_ids` と同型（PR #187
        review round 14）: 読み戻し（`model_validate`）でも発火する safety net。"""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for anchor in self.anchors:
            if anchor.anchor_id in seen:
                duplicates.add(anchor.anchor_id)
            seen.add(anchor.anchor_id)
        if duplicates:
            raise ValueError(
                f"duplicate anchor_id(s) in observation report: {', '.join(sorted(duplicates))}"
            )
        return self


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

    round 10 fix: the round-4 trailing skip also needs to stop exactly at a
    completed cycle, not wrap into the *next* one. `matched_raw_index % length
    == 0` means the matches so far already account for a whole number of
    cycles with nothing left over — any further step would consume the first
    raw position of a new cycle, not a trailing duplicate of the one just
    finished. Without this check, a single-chord canonical (`length == 1`,
    e.g. `[C]`) over-swept: every raw position trivially equals `last_chord`
    (there's only one chord to ever produce), so the old bound
    (`trailing_steps < length`, i.e. `< 1`) still let it advance one step past
    an already-complete cycle, inflating `full_cycles` from 1 to 2 for a
    single observed chord. For `length >= 2` this addition changes nothing
    observable: the old bound already prevented sweeping a full extra cycle's
    worth of steps past a genuine match, so `matched_raw_index // length`
    comes out the same whether the sweep stops exactly at the boundary or one
    step short of the next one.
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
        and matched_raw_index % length != 0
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


# AR2-3: SectionMapArtifact / SectionMapArtifactV2 (the section-map/0.1 and
# section-map/0.2 pydantic schemas themselves) now live in `arrange.section_map`
# — shared with `identity.py`'s `section_ref` resolution, which cannot import
# this module (observe.py imports identity.py, not the other way around; see
# `arrange/section_map.py`'s module docstring). `SECTION_MAP_ARTIFACT_SCHEMA_0_1`
# is kept as the name this module has always used at the call sites below.
SECTION_MAP_ARTIFACT_SCHEMA = SECTION_MAP_ARTIFACT_SCHEMA_0_1
_STRUCTURE_SENSOR_FORMAT_VERSIONS = frozenset(
    {SECTION_MAP_ARTIFACT_SCHEMA_0_1, SECTION_MAP_ARTIFACT_SCHEMA_0_2}
)


def _load_section_map(raw_bytes: bytes, *, artifact_path: Path) -> list[str]:
    """``section-map/0.1`` artifact (JSON) を正典セクションラベル列へ変換する。

    ``raw_bytes`` は呼び出し側が既に hash 照合済みの bytes を渡す（本関数は一切
    ファイルを読まない — `_load_chord_progression` と同じ契約）。
    ``artifact_path`` はエラーメッセージの表示にのみ使う。実際の JSON 検証は
    `arrange.section_map.parse_section_map_artifact_0_1` へ委譲する（AR2-3:
    schema 定義の二重化を避けるための共有モジュール切り出し）。
    """
    return list(parse_section_map_artifact_0_1(raw_bytes, artifact_path=artifact_path).sections)


def _load_section_map_v2(
    raw_bytes: bytes, *, artifact_path: Path
) -> tuple[list[str], list[str]]:
    """``section-map/0.2`` artifact (JSON) を (正典セクションラベル列, id 列) へ
    変換する（AR2-3）。両列は artifact 記載順（正典順）を保ち、位置で対応する
    （``labels[i]`` は ``ids[i]`` のラベル）。``ids`` は artifact 内一意であることを
    `SectionMapArtifactV2` 自身の validator が保証済み。契約は `_load_section_map`
    と同型（bytes は呼び出し側が hash 照合済みのものを渡す。ファイルは読まない）。
    """
    artifact = parse_section_map_artifact_0_2(raw_bytes, artifact_path=artifact_path)
    labels = [entry.label for entry in artifact.sections]
    ids = [entry.id for entry in artifact.sections]
    return labels, ids


_TRAILING_DIGITS_PATTERN = re.compile(r"\d+$")

# `assign_labels` (rpe/structure_labels.py) is the sole producer of extractor
# structure labels, but it does NOT auto-number all five base words equally:
# only "Verse" ever gets a numeric suffix (`Verse`, `Verse2`, `Verse3`, ... —
# `_assign_verses`, used to fill however many "leftover" middle sections
# remain once Chorus/Bridge are assigned). Intro/Outro are always emitted
# exactly once (first/last section), Bridge is capped at one occurrence, and
# Chorus is capped at two occurrences but neither Bridge nor Chorus ever
# receives a numeric suffix (`_assign_choruses`/`_assign_bridge` always write
# the bare "Chorus"/"Bridge" string). Trailing-digit stripping on the
# *observed* side is therefore restricted to this single stem, not the full
# five-word vocabulary an earlier revision used (Codex 3R P2,
# https://github.com/Yuu6798/ugh-prompt-engine/pull/192#discussion_r3610383648):
# stripping `chorus`/`bridge`/`intro`/`outro` digits was over-broad, since the
# extractor never emits those numbered — the only real source of a trailing
# digit on the observed side is `VerseN`.
_OBSERVED_NUMBERED_STEM = "verse"


def _normalize_canonical_section_label(label: str) -> str:
    """正典 (`section-map/0.1` アーティファクト) 側の正規化規則（structure
    Design Memo section 3、Codex 3R P2 で非対称化）: lowercase のみを行う。
    作者が明示的に書いた識別子は verbatim で保持し、末尾数字を一切 strip
    しない — ``verse2`` は ``verse2`` のまま、``chorus1``/``chorus2`` もその
    まま区別される。正典側で同種セクションの繰り返しを表現する場合は番号で
    はなく列挙で書く慣行を前提とする（例: ``["verse", "chorus", "verse",
    "chorus"]`` であって ``["verse1", "chorus1", "verse2", "chorus2"]`` では
    ない）。手書き正典が意図的に番号付き識別子（``chorus1``/``chorus2`` 等）
    を使う場合は、それを抽出器の連番慣行に合わせて potentially 別々のセク
    ションを同一視するのではなく、区別する情報としてそのまま尊重する。
    """
    return label.lower()


def _normalize_observed_section_label(label: str) -> str:
    """観測 (`PhysicalRPE.structure` / 抽出器 `assign_labels`) 側の正規化規則
    （structure Design Memo section 3、Codex 3R P2 で非対称化）: lowercase
    した上で、語幹が ``_OBSERVED_NUMBERED_STEM`` (``verse``) の場合のみ末尾
    数字を strip する（例: extractor の ``Verse2`` → ``verse``）。
    `assign_labels` (rpe/structure_labels.py) が連番を発行するのは Verse
    のみで、Chorus/Bridge/Intro/Outro は常に単数形のまま発行されるため
    （`_assign_choruses`/`_assign_bridge` 参照）、strip 対象をこの一語幹に
    限定する — それ以外の語は抽出器がそもそも数字付きで生成しないので strip
    対象にする理由がなく、対象を広げると手書き正典側の識別番号付きラベル
    （例: ``chorus1``/``chorus2``）が誤って同一視されるリスクだけが残る
    （正典側は `_normalize_canonical_section_label` で strip しないため、
    この関数を観測側だけに限定する非対称性そのものがその防御になる）。
    それ以外の変換はしない — 抽出器のラベル連番付与の吸収に限定し、それ以上
    の意味的な同値判断（同義語マージ等）は行わない。
    """
    lowered = label.lower()
    stripped = _TRAILING_DIGITS_PATTERN.sub("", lowered)
    if stripped != lowered and stripped == _OBSERVED_NUMBERED_STEM:
        return stripped
    return lowered


def _observe_structure(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    bundle: RPEBundle,
    artifact_bytes: bytes,
) -> AnchorObservation:
    # Resolved only for error-message display — never read here, same
    # contract `_observe_harmony` follows for its own artifact.
    artifact_path = _resolve_confined(
        anchor.artifact, manifest_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
    )
    # AR2-3: dispatch on the anchor's *declared* format_version, not the
    # artifact's own content — `is_structure_sensor_anchor` already restricted
    # `anchor.format_version` to {section-map/0.1, section-map/0.2} before this
    # function is ever called, so this is an exhaustive 2-way branch, not a
    # fallback chain. Each parser's `schema_version: Literal[...]` still
    # independently enforces that the artifact's *internal* declaration agrees
    # with what was dispatched to (a 0.1-declared anchor whose bytes actually
    # say `section-map/0.2`, or vice versa, fails closed inside the parser —
    # same posture harmony's `chord-sequence/0.1` takes).
    canonical_section_ids_raw: Optional[list[str]] = None
    if anchor.format_version == SECTION_MAP_ARTIFACT_SCHEMA_0_2:
        canonical_sections_raw, canonical_section_ids_raw = _load_section_map_v2(
            artifact_bytes, artifact_path=artifact_path
        )
    else:
        canonical_sections_raw = _load_section_map(artifact_bytes, artifact_path=artifact_path)
    observed_sections_raw = [marker.label for marker in bundle.physical.structure]

    canonical_sections = [
        _normalize_canonical_section_label(label) for label in canonical_sections_raw
    ]
    observed_sections = [
        _normalize_observed_section_label(label) for label in observed_sections_raw
    ]

    canonical_length = len(canonical_sections)
    observed_length = len(observed_sections)
    max_length = max(canonical_length, observed_length)
    position_match_count = sum(
        1
        for canonical_label, observed_label in zip(canonical_sections, observed_sections)
        if canonical_label == observed_label
    )
    position_match_rate = round(position_match_count / max_length, 4) if max_length else 0.0
    sequence_exact_match = canonical_sections == observed_sections

    measurements: dict[str, JsonValue] = {
        "canonical_sections": canonical_sections,
        "canonical_length": canonical_length,
        "observed_sections": observed_sections,
        "observed_sections_raw": observed_sections_raw,
        "observed_length": observed_length,
        "position_match_rate": position_match_rate,
        "sequence_exact_match": sequence_exact_match,
    }
    if canonical_section_ids_raw is not None:
        # AR2-3: section-map/0.2 anchors additionally self-describe their
        # stable section ids (artifact order, unmodified — a raw value, not
        # normalized like `canonical_sections`). Transparency only: the D-1
        # identity gate below still keys off `sequence_exact_match` (the
        # label sequence) alone, exactly as it does for section-map/0.1 —
        # ids are not compared against anything here. 0.1 anchors never get
        # this key at all, not even `null`, so `measurements`' shape itself
        # tells a reader which format produced a given report.
        measurements["canonical_section_ids"] = canonical_section_ids_raw
    sensor = SensorRecord(name="section_sequence_match", available=True, reason=None)
    if sequence_exact_match:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=sensor,
            measurements=measurements,
            adherence_status="preserved",
            determination="exact_match",
            note="normalized canonical and observed section sequences match exactly.",
        )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=sensor,
        measurements=measurements,
        adherence_status="not_observed",
        determination="deferred",
        note=(
            "normalized canonical and observed section sequences do not match "
            "exactly (changed_within_policy/changed_outside_policy classification "
            "is out of scope for this instrument and deferred to a future "
            "threshold Design Memo, D-1)."
        ),
    )


# --- lyrics sensor (WI0-a, 2026-07-20) ---------------------------------------


def _transcribe_lyrics_for_observation(
    audio_path: Path,
) -> tuple[Optional[str], Optional[str]]:
    """Transcribe `audio_path`'s lyrics for the observation sensor, isolating
    the optional `lyrics` extra (faster-whisper + Demucs) from the caller.

    Returns `(transcribed_text, None)` on success, or `(None, reason)` when
    the optional dependency is unavailable — `reason` is the adapter's own
    exception message (install hint included), never fabricated here.

    Probes `ensure_lyrics_available()` (a faster-whisper import check only —
    no model load, no Demucs invocation) BEFORE calling `transcribe_lyrics`
    (Codex P2, WI0-a review): without this, a missing faster-whisper install
    would still pay for `transcribe_lyrics`'s default `separate_vocals=True`
    Demucs pass before hitting the whisper import and raising
    `LearnedModelUnavailable` — wasted work on a path already known to be a
    dead end. `cli/roundtrip_cmd.py`'s `lyrics_adherence_cmd` follows the
    same probe-first shape; this mirrors it so both call sites degrade
    identically without doing the separation work.

    Deviation from the letter of the repo's usual fallback-chain convention
    (CLAUDE.md: "try/except ModuleNotFoundError to detect an optional
    dependency"): `rpe/learned/lyrics_adapter.transcribe_lyrics` does not
    raise `ModuleNotFoundError` itself — it already converts the underlying
    `ImportError` into `LearnedModelUnavailable` (the `rpe/learned` package's
    own "optional dependency missing" exception; checked directly against
    the adapter source for this PR), and its default `separate_vocals=True`
    path can independently raise `SeparatorNotAvailableError` (missing
    Demucs) before ever reaching the faster-whisper import. Both are caught
    here — the same two exception types the existing CLI call sites catch
    for this exact function (`cli/roundtrip_cmd.py`'s `lyrics_adherence_cmd`,
    `cli/extract_cmd.py`'s `--lyrics` path) — so this helper matches actual
    adapter behavior rather than the literal exception-class name.
    """
    from svp_rpe.io.source_separator import SeparatorNotAvailableError
    from svp_rpe.rpe.learned import LearnedModelUnavailable
    from svp_rpe.rpe.learned.lyrics_adapter import (
        ensure_lyrics_available,
        transcribe_lyrics,
    )

    try:
        ensure_lyrics_available()
    except LearnedModelUnavailable as exc:
        return None, str(exc)

    try:
        transcription = transcribe_lyrics(str(audio_path))
    except (LearnedModelUnavailable, SeparatorNotAvailableError) as exc:
        return None, str(exc)
    return transcription.text, None


def _observe_lyrics(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    artifact_bytes: bytes,
    audio_path: Path,
) -> AnchorObservation:
    # Resolved only for error-message display — never read here, same
    # contract `_observe_harmony`/`_observe_structure` follow for their own
    # artifacts.
    artifact_path = _resolve_confined(
        anchor.artifact, manifest_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
    )
    try:
        expected_lines = artifact_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"lyrics artifact is not valid UTF-8 text: {artifact_path}: {exc}"
        ) from exc

    sensor_name = "lyrics_transcription"
    transcribed_text, reason = _transcribe_lyrics_for_observation(audio_path)
    if transcribed_text is None:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=SensorRecord(name=sensor_name, available=False, reason=reason),
            measurements={},
            adherence_status="not_observed",
            determination="no_sensor",
            note=None,
        )

    match_report = match_lyrics(expected_lines, transcribed_text)
    measurements: dict[str, JsonValue] = {
        "canonical_line_count": len(expected_lines),
        # `match_lyrics`'s full report, verbatim and unmodified — this
        # instrument reuses its measurements rather than inventing new ones
        # (WI0-a design contract B).
        "match_lyrics": match_report,
    }
    sensor = SensorRecord(name=sensor_name, available=True, reason=None)
    # The D-1 identity gate is STRING EQUALITY of the two texts after
    # `match_lyrics`'s own char-level normalization — NOT
    # `overall_similarity == 1.0` (Codex P2, WI0-a review): `overall_similarity`
    # is rounded to 4dp before being returned, so a genuinely non-identical
    # pair (e.g. a long transcript with a single mismatched character) can
    # round up to a reported `1.0` and be misclassified `exact_match`. This
    # reuses `char_level_normalize` — the exact normalization `match_lyrics`
    # applies to both sides before computing that same ratio — so the gate
    # stays behaviorally aligned with `overall_similarity`'s definition
    # ("are these two normalized texts the same string") while comparing the
    # unrounded strings directly instead of the rounded scalar.
    canonical_normalized = char_level_normalize("\n".join(expected_lines))
    transcribed_normalized = char_level_normalize(transcribed_text)
    if canonical_normalized == transcribed_normalized:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=sensor,
            measurements=measurements,
            adherence_status="preserved",
            determination="exact_match",
            note=(
                "transcribed lyrics match the canonical lines exactly after "
                "normalization (char_level_normalize string equality, not a "
                "rounded overall_similarity comparison)."
            ),
        )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=sensor,
        measurements=measurements,
        adherence_status="not_observed",
        determination="deferred",
        note=(
            "transcribed lyrics do not match the canonical lines exactly "
            "(changed_within_policy/changed_outside_policy classification is "
            "out of scope for this instrument and deferred to a future "
            "threshold Design Memo, D-1)."
        ),
    )


# --- melody sensor (WI0-a, 2026-07-20) ---------------------------------------

NOTE_EVENTS_ARTIFACT_SCHEMA = "note-events/0.1"

_PITCH_CLASS_BASE: dict[str, int] = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}
_NOTE_NAME_PATTERN = re.compile(r"^([A-Ga-g])([#♯]|[b♭])?(-?\d+)$")


def _note_name_to_midi(name: str) -> int:
    """Pure note-name -> MIDI integer conversion (v0, WI0-a).

    Scientific pitch notation (`C4` == MIDI 60, `A4` == MIDI 69 / 440 Hz):
    `midi = (octave + 1) * 12 + pitch_class`. Accepts a single optional
    accidental, ASCII (`#` / `b`) or Unicode (`♯` / `♭`).

    No existing parser in this repo covers this shape (checked per the WI0-a
    design contract): `perform/performer.py`'s `PITCH_CLASSES` / `parse_key`
    pair parses a bare `<letter><accidental> major|minor` KEY string, not an
    octave-qualified note name like `"Eb4"` — a different grammar entirely —
    so this is a new pure function rather than a reuse.

    Octave-crossing accidentals (Codex P2, WI0-a review): `pitch_class` is
    NOT wrapped to `0..11` before combining with the octave term. Wrapping
    it first (the previous implementation's `pitch_class % 12`) silently
    drops the octave carry — `Cb4` (pitch_class -1) would fold to 11 and
    read as the octave-4 pitch class B *within octave 4* (MIDI 71, an
    octave too high) instead of B3 (MIDI 59, one semitone below C4); `B#3`
    (pitch_class 12) would fold to 0 and read as C *within octave 3* (MIDI
    48) instead of C4 (MIDI 60, one semitone above B3). Adding the
    unwrapped (possibly -1 or 12) `pitch_class` straight to `(octave + 1) *
    12` carries the accidental across the octave boundary correctly in
    both directions.

    The result is validated against the MIDI byte range (`0..127`) and
    raises `ValueError` on overflow/underflow — same fail-closed posture as
    the unsupported-note-name branch above, rather than silently returning
    an out-of-range int.
    """
    match = _NOTE_NAME_PATTERN.match(name.strip())
    if match is None:
        raise ValueError(f"unsupported note name: {name!r}")
    letter, accidental, octave_text = match.groups()
    pitch_class = _PITCH_CLASS_BASE[letter.upper()]
    if accidental in ("#", "♯"):
        pitch_class += 1
    elif accidental in ("b", "♭"):
        pitch_class -= 1
    octave = int(octave_text)
    midi = (octave + 1) * 12 + pitch_class
    if not 0 <= midi <= 127:
        raise ValueError(
            f"note name {name!r} resolves to out-of-range MIDI value {midi} "
            "(valid range is 0-127)"
        )
    return midi


def _load_note_events_artifact(raw_bytes: bytes, *, artifact_path: Path) -> list[int]:
    """``note-events/0.1`` artifact (JSON) を onset 順の正典 MIDI 整数列へ変換する。

    `_load_chord_progression` と同じ契約: ``raw_bytes`` は呼び出し側が既に hash
    照合済みの bytes（本関数はファイルを一切読まない）。``artifact_path`` は
    エラーメッセージ表示専用。``schema`` キーは ``NOTE_EVENTS_ARTIFACT_SCHEMA``
    の既知値ちょうどでなければ fail-closed（欠落・未知値どちらも ``ValueError``）。
    各 note エントリは ``pitch``（音名文字列）と ``start_beat``（並べ替えキー。
    onset 順を復元するためだけに使い、拍→秒変換は一切行わない）を必須とする。

    ``duration_beats``（Codex P2, PR #198 review）: v0 の恒等判定
    （``_observe_melody``）は pitch 系列のみを比較し、本関数が返す MIDI 整数列も
    onset 順のピッチのみで duration の値そのものは呼び出し側で一切使わない —
    その挙動（比較ロジック）は変えない。しかし committed fixture
    （``examples/arrangement/midnight_signal/identity/melody_notes.json``）と
    `docs/cli.md` の note-events/0.1 定義は ``duration_beats`` をスキーマの必須
    フィールドとして明記しているため、「値を使わない」ことと「検証しない」ことは
    別問題 — 欠落・型不正（非数値）・負値はいずれも fail-closed で拒否する
    （``_load_chord_progression`` が ``chords`` の型不正を拒否するのと同じ、
    「canonical artifact が壊れていたら黙って通さない」流儀）。検証範囲の拡張のみで
    比較ロジックには一切手を入れていない。
    """
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"note events artifact is not valid JSON: {artifact_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"note events artifact must be a mapping with a 'notes' key: {artifact_path}"
        )
    schema = payload.get("schema")
    if schema != NOTE_EVENTS_ARTIFACT_SCHEMA:
        raise ValueError(
            f"note events artifact has unsupported schema {schema!r} "
            f"(expected {NOTE_EVENTS_ARTIFACT_SCHEMA!r}): {artifact_path}"
        )
    if "notes" not in payload:
        raise ValueError(
            f"note events artifact must be a mapping with a 'notes' key: {artifact_path}"
        )
    notes_field = payload["notes"]
    if not isinstance(notes_field, list):
        raise ValueError(f"note events artifact 'notes' must be a list: {artifact_path}")
    if not notes_field:
        raise ValueError(f"note events artifact 'notes' must not be empty: {artifact_path}")
    entries: list[tuple[float, int, str]] = []
    for index, item in enumerate(notes_field):
        if (
            not isinstance(item, dict)
            or "pitch" not in item
            or "start_beat" not in item
            or "duration_beats" not in item
        ):
            raise ValueError(
                "note events artifact note entry missing "
                "'pitch'/'start_beat'/'duration_beats': "
                f"{item!r} ({artifact_path})"
            )
        pitch = item["pitch"]
        start_beat = item["start_beat"]
        duration_beats = item["duration_beats"]
        if not isinstance(pitch, str):
            raise ValueError(
                f"note events artifact note 'pitch' must be a string: {item!r} ({artifact_path})"
            )
        if not isinstance(start_beat, (int, float)) or isinstance(start_beat, bool):
            raise ValueError(
                "note events artifact note 'start_beat' must be numeric: "
                f"{item!r} ({artifact_path})"
            )
        # Codex P2 (PR #198 review): `duration_beats` is part of the
        # committed fixture's/docs' note-events/0.1 schema even though v0's
        # identity gate never reads its value (pitch-only comparison,
        # unchanged by this validation) — a malformed canonical artifact
        # must fail-closed here rather than silently reporting `preserved`.
        # Same posture/shape as the `start_beat` numeric check above and as
        # `_load_chord_progression`'s type validation for `chords`.
        if not isinstance(duration_beats, (int, float)) or isinstance(duration_beats, bool):
            raise ValueError(
                "note events artifact note 'duration_beats' must be numeric: "
                f"{item!r} ({artifact_path})"
            )
        if duration_beats < 0:
            raise ValueError(
                "note events artifact note 'duration_beats' must be non-negative: "
                f"{item!r} ({artifact_path})"
            )
        entries.append((float(start_beat), index, pitch))
    # Stable sort keyed on (start_beat, original artifact index): the index
    # is an explicit tie-break rather than relying implicitly on
    # `list.sort`'s stability, so onset order is well-defined even if this
    # list is later filtered/reordered upstream.
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [_note_name_to_midi(pitch) for _, _, pitch in entries]


def _lcs_length(a: list[int], b: list[int]) -> int:
    """Longest common subsequence length between integer sequences `a`/`b`
    (standard O(len(a) * len(b)) DP).

    Used by `_observe_melody` for both the pitch sequence and the
    adjacent-interval sequence — no existing repo utility does subsequence
    comparison over pitch/interval integers (`roundtrip/compare.py`'s
    chord-sequence helpers only ever compare `(root, quality)` tuples
    position-aligned/collapsed, not via LCS; `keys.py` /
    `eval/anchor_matcher.py` / `eval/semantic_similarity.py` don't touch
    pitch sequences at all — checked per the WI0 exploration map), so this is
    a new pure function.
    """
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            current[j] = previous[j - 1] + 1 if x == y else max(previous[j], current[j - 1])
        previous = current
    return previous[-1]


def _adjacent_diffs(seq: list[int]) -> list[int]:
    """Consecutive-difference series of `seq` (length `len(seq) - 1`, empty
    if `len(seq) < 2`)."""
    return [second - first for first, second in zip(seq, seq[1:])]


def _extract_melody_for_observation(
    audio_path: Path,
) -> tuple[Optional[list[int]], Optional[str]]:
    """Run basic-pitch on `audio_path` for the observation sensor, isolating
    the optional `pitch` extra from the caller.

    Returns `(observed_midi, None)` on success — `observed_midi` is
    `LearnedNoteEvent.pitch_midi` in `start_sec` ascending order (no time
    alignment against `start_beat`/BPM; see the module docstring's WI0-a
    note) — or `(None, reason)` when the optional dependency is unavailable.

    Same deviation from the literal "except ModuleNotFoundError" convention
    as `_transcribe_lyrics_for_observation`: `basic_pitch_adapter` converts
    the underlying `ImportError` into `LearnedModelUnavailable` itself
    (checked directly against the adapter source), so that is the exception
    actually caught here — matching `cli/extract_cmd.py`'s own `--pitch`
    catch.
    """
    from svp_rpe.rpe.learned import LearnedModelUnavailable
    from svp_rpe.rpe.learned.basic_pitch_adapter import extract_basic_pitch_annotations

    try:
        annotations = extract_basic_pitch_annotations(audio_path)
    except LearnedModelUnavailable as exc:
        return None, str(exc)
    ordered_events = sorted(annotations.note_events, key=lambda event: event.start_sec)
    return [event.pitch_midi for event in ordered_events], None


def _observe_melody(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    artifact_bytes: bytes,
    audio_path: Path,
) -> AnchorObservation:
    artifact_path = _resolve_confined(
        anchor.artifact, manifest_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
    )
    canonical_midi = _load_note_events_artifact(artifact_bytes, artifact_path=artifact_path)
    sensor_name = "note_events"

    observed_midi, reason = _extract_melody_for_observation(audio_path)
    if observed_midi is None:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=SensorRecord(name=sensor_name, available=False, reason=reason),
            measurements={},
            adherence_status="not_observed",
            determination="no_sensor",
            note=None,
        )

    canonical_length = len(canonical_midi)
    observed_length = len(observed_midi)
    pitch_lcs = _lcs_length(canonical_midi, observed_midi)
    pitch_lcs_ratio = round(pitch_lcs / canonical_length, 4) if canonical_length else 0.0

    canonical_intervals = _adjacent_diffs(canonical_midi)
    observed_intervals = _adjacent_diffs(observed_midi)
    interval_lcs_ratio: Optional[float]
    if len(canonical_intervals) >= 1:
        interval_lcs = _lcs_length(canonical_intervals, observed_intervals)
        interval_lcs_ratio = round(interval_lcs / len(canonical_intervals), 4)
    else:
        # canonical_length < 2 — no adjacent pair exists to diff, so an
        # interval-LCS ratio is undefined rather than a misleading 0.0/1.0.
        interval_lcs_ratio = None

    pitch_sequence_exact_match = canonical_midi == observed_midi
    measurements: dict[str, JsonValue] = {
        "canonical_length": canonical_length,
        "observed_length": observed_length,
        "pitch_sequence_exact_match": pitch_sequence_exact_match,
        "pitch_lcs_ratio": pitch_lcs_ratio,
        "interval_lcs_ratio": interval_lcs_ratio,
        "observed_head": observed_midi[:8],
    }
    sensor = SensorRecord(name=sensor_name, available=True, reason=None)
    if pitch_sequence_exact_match:
        return AnchorObservation(
            anchor_id=anchor.id,
            domain=anchor.domain,
            sensor=sensor,
            measurements=measurements,
            adherence_status="preserved",
            determination="exact_match",
            note="observed MIDI pitch sequence matches the canonical sequence exactly.",
        )
    return AnchorObservation(
        anchor_id=anchor.id,
        domain=anchor.domain,
        sensor=sensor,
        measurements=measurements,
        adherence_status="not_observed",
        determination="deferred",
        note=(
            "observed MIDI pitch sequence does not match the canonical sequence "
            "exactly (changed_within_policy/changed_outside_policy classification "
            "is out of scope for this instrument and deferred to a future "
            "threshold Design Memo, D-1)."
        ),
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
    elif anchor.domain == "structure":
        # Reached when domain == "structure" but either artifact_type isn't
        # "section_map", or it is "section_map" but the anchor doesn't
        # declare the one format_version the structure sensor actually reads
        # (PR #192 review round 1): unlike harmony, the repo has real
        # pre-existing `section_map` anchors in a *different* format (e.g.
        # `tests/test_identity_manifest.py`'s YAML `media_type` anchor with no
        # format_version, and `tests/test_performance_package.py`'s
        # `format_version="section-map/1"`) that must keep falling back to
        # no_sensor rather than being force-fed into `_observe_structure`'s
        # JSON parser. The reason names the anchor's declared format_version
        # (including the `None` case) so it's clear this is a format gate,
        # not an artifact_type gate alone.
        name = f"{anchor.domain}_sensor"
        if anchor.artifact_type == "section_map":
            reason = (
                "structure sensor reads section-map/0.1 or section-map/0.2 only; "
                f"anchor declares format_version {anchor.format_version!r}"
            )
        else:
            reason = (
                f"no sensor wired for structure anchors with artifact_type "
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


def is_harmony_sensor_anchor(anchor: IdentityAnchor) -> bool:
    """Whether `anchor` is the (domain, artifact_type) pair the harmony
    sensor actually reads content for — `domain == "harmony"` and
    `artifact_type == "chord_sequence_json"` (PR #187 review round 5).

    Exposed (not just inlined in `_observe_anchor`) so callers that need to
    decide *which* anchors' artifact bytes are worth collecting ahead of time
    — e.g. the CLI's `parse_identity_manifest_with_artifacts(..., collect=...)`
    predicate (PR #187 review round 11) — share the exact same routing rule
    instead of maintaining a second copy that could drift out of sync. A
    sibling predicate, `is_structure_sensor_anchor`, follows the same overall
    shape for the (structure, section_map) pair wired 2026-07-19, but also
    gates on `format_version` — see its own docstring for why (PR #192
    review round 1: other-format `section_map` anchor precedent already
    exists in this repo, unlike harmony's `chord_sequence_json`). Both are
    consulted at every sync point (`_observe_anchor`'s dispatch, the
    extraction gate below, and the CLI's `collect=` site) rather than one
    being folded silently into the other. If a future PR wires a sensor for
    another (domain, artifact_type) pair, add a same-shaped predicate and
    update those same sync points — don't special-case it inline.
    """
    return anchor.domain == "harmony" and anchor.artifact_type == "chord_sequence_json"


def is_structure_sensor_anchor(anchor: IdentityAnchor) -> bool:
    """Whether `anchor` is the (domain, artifact_type, format_version) triple
    the structure sensor actually reads content for — `domain == "structure"`,
    `artifact_type == "section_map"`, *and* `format_version` one of
    `{"section-map/0.1", "section-map/0.2"}` (structure Design Memo section 2,
    2026-07-19; format-version gate added PR #192 review round 1; 0.2 added
    AR2-3, `design_memo_ar2_3.md`). Same shape and same rationale as
    `is_harmony_sensor_anchor` for the sync-point list this predicate is also
    consulted at — see that docstring.

    Unlike harmony, this predicate also gates on `format_version`, not just
    `artifact_type`: the repo already has `section_map` anchor precedent in
    other formats before PR #192 wired a JSON sensor for `section-map/0.1`
    specifically (`tests/test_identity_manifest.py` declares a YAML
    `media_type` section_map anchor with no `format_version`, and
    `tests/test_performance_package.py` declares one with
    `format_version="section-map/1"`). Routing on artifact_type alone would
    force those other-format anchors into `_observe_structure`'s JSON
    parser/validator and abort the whole `observe` run instead of the
    `no_sensor` fallback they got before this sensor existed. The
    `format_version` field is the anchor's own self-declared schema id, so
    gating on it is a declared-format check, not content sniffing or
    guessing: an anchor that doesn't declare one of the two known section-map
    format_versions — including one that declares nothing
    (`format_version is None`) — falls through to `_observe_unavailable` no
    matter what its bytes actually contain. This is a routing gate only: an
    anchor that *does* declare a known format_version but whose artifact
    bytes are malformed, schema-invalid, or internally declare a *different*
    `schema_version` than the one this anchor claims still fails closed
    inside `_observe_structure` / `_load_section_map` / `_load_section_map_v2`,
    exactly like harmony's `chord-sequence/0.1` contract — declaring the right
    format is not a promise the content is well-formed or self-consistent,
    only a promise this sensor is the right one to check.
    """
    return (
        anchor.domain == "structure"
        and anchor.artifact_type == "section_map"
        and anchor.format_version in _STRUCTURE_SENSOR_FORMAT_VERSIONS
    )


def is_lyrics_sensor_anchor(anchor: IdentityAnchor) -> bool:
    """Whether `anchor` is the (domain, artifact_type) pair the lyrics
    sensor actually reads content for — `domain == "lyrics"` and
    `artifact_type == "lyrics_text"` (WI0-a, 2026-07-20).

    Same harmony-style 2-tuple shape as `is_harmony_sensor_anchor` — no
    `format_version` gate the way `is_structure_sensor_anchor` adds, because
    (checked per the WI0-a design contract) this repo has no pre-existing
    other-format `lyrics_text` anchor precedent the way `section_map` did for
    structure before PR #192. Consulted at the same sync points harmony's
    predicate is: `_observe_anchor`'s dispatch and the CLI's `collect=` /
    snapshot-gate predicates (`cli/observe_cmd.py`).
    """
    return anchor.domain == "lyrics" and anchor.artifact_type == "lyrics_text"


def is_melody_sensor_anchor(anchor: IdentityAnchor) -> bool:
    """Whether `anchor` is the (domain, artifact_type) pair the melody
    sensor actually reads content for — `domain == "melody"` and
    `artifact_type == "note_events_json"` (WI0-a, 2026-07-20). Same shape
    and rationale as `is_lyrics_sensor_anchor` — see that docstring. The
    note-events artifact's own `schema` field (checked inside
    `_load_note_events_artifact`) is what fails closed on an unsupported
    note-events format version, not this routing predicate.
    """
    return anchor.domain == "melody" and anchor.artifact_type == "note_events_json"


def _observe_anchor(
    anchor: IdentityAnchor,
    *,
    manifest_dir: Path,
    work_id: str,
    bundle: Optional[RPEBundle],
    artifact_bytes_by_id: dict[str, bytes],
    audio_path: Optional[Path] = None,
) -> AnchorObservation:
    if is_harmony_sensor_anchor(anchor):
        # `build_observation_report` only extracts (and passes a non-None
        # bundle) when at least one manifest anchor matches a wired sensor's
        # predicate (PR #187 review round 12's extraction gate, widened
        # 2026-07-19 to also cover `is_structure_sensor_anchor`) — so if
        # we're in this branch, `bundle` is guaranteed to be set.
        assert bundle is not None
        return _observe_harmony(
            anchor,
            manifest_dir=manifest_dir,
            work_id=work_id,
            bundle=bundle,
            artifact_bytes=artifact_bytes_by_id[anchor.id],
        )
    if is_structure_sensor_anchor(anchor):
        assert bundle is not None
        return _observe_structure(
            anchor,
            manifest_dir=manifest_dir,
            work_id=work_id,
            bundle=bundle,
            artifact_bytes=artifact_bytes_by_id[anchor.id],
        )
    if is_lyrics_sensor_anchor(anchor):
        # Lyrics/melody don't use the shared `RPEBundle` extraction at all —
        # they read `audio_path` directly through their own optional-extra
        # adapters (WI0-a), so `build_observation_report` always passes a
        # real `audio_path` regardless of `bundle`/`needs_extraction`.
        assert audio_path is not None
        return _observe_lyrics(
            anchor,
            manifest_dir=manifest_dir,
            work_id=work_id,
            artifact_bytes=artifact_bytes_by_id[anchor.id],
            audio_path=audio_path,
        )
    if is_melody_sensor_anchor(anchor):
        assert audio_path is not None
        return _observe_melody(
            anchor,
            manifest_dir=manifest_dir,
            work_id=work_id,
            artifact_bytes=artifact_bytes_by_id[anchor.id],
            audio_path=audio_path,
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

    抽出は gate 化する（PR #187 review round 12。2026-07-19: structure センサー
    追加に伴い述語を widen）: 配線済みセンサーが実際に測定する anchor
    （``is_harmony_sensor_anchor`` または ``is_structure_sensor_anchor`` —
    `_observe_anchor` の routing と同じ述語。二重定義しない）が manifest 中に
    1 つも無ければ ``extract_rpe_from_file`` を一切呼ばない。音声は provenance
    のため既に hash 済みであり、全 anchor が no_sensor の report にとって
    中身の decode は不要な作業（かつコスト）だからである。

    lyrics / melody（WI0-a）はこの ``bundle``/``extract_rpe_from_file`` の
    共有抽出には乗らない — それぞれ ``audio_path`` を直接読む独自の optional
    extra アダプタ（``transcribe_lyrics`` / ``extract_basic_pitch_annotations``）
    経由で測定するため、``audio_path`` は ``needs_extraction`` の真偽に関わらず
    常に ``_observe_anchor`` へ渡す。実際に転写/推論が走るのは manifest に
    lyrics/melody anchor が存在し、かつ `_observe_anchor` がそれへ routing
    した場合のみ（余計な重処理をしない — WI0-a 設計契約 E）。
    """
    manifest_dir = Path(manifest_path).resolve().parent
    needs_extraction = any(
        is_harmony_sensor_anchor(anchor) or is_structure_sensor_anchor(anchor)
        for anchor in manifest.anchors
    )
    bundle = extract_rpe_from_file(str(audio_path)) if needs_extraction else None
    anchors = [
        _observe_anchor(
            anchor,
            manifest_dir=manifest_dir,
            work_id=manifest.meta.work_id,
            bundle=bundle,
            artifact_bytes_by_id=artifact_bytes,
            audio_path=audio_path,
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
