"""recast/experimental.py — M4: melody anchor の Recast 配線（experimental）。

上位設計書 `docs/DESIGN_M4_recast_melody_anchor.md` と実装契約
（Design Memo M4）の実装。**M4 は翻訳器である**——M3 比較器（`melody/comparison.py`）
の出力（軸別 evidence）を、契約の `axis_policy`（`arrange/contract.py` の
`ContractAnchor.axis_policy`）に照らして D-1 語彙
（``preserved`` / ``changed_within_policy`` / ``changed_outside_policy`` /
``not_observed``）へ機械的に写す。新しい数値・閾値・重み・スコア合成は一切
作らない。単一同一性スコアは恒久禁止（報告層含む）。

M0〜M3 の凍結値・既存スキーマは変更しない: 本モジュールは ``melody/`` 配下を
**import のみ**で使う（`melody.representation.load_m3_registry` /
`melody.comparison.compare_melodies` / `melody.routing.select_routes` /
`melody.extractors.observe_via_route_with_provenance` / `melody.observability.
MelodyNote` / `MelodyObservation` / `ObservabilityThresholds`）。melody 専用の
判断ロジックをここへ足すことも禁止 — 写像関数は axis_policy と evidence 辞書を
受ける汎用シグネチャで、melody にハードコードしない（帯域集合・軸語彙は
ドメイン別データ定数として分離する。歌詞/和声 anchor への拡張は本 M4 実装の
スコープ外だが、配線パターンは踏襲できる形にしておく——上位設計書 §6）。

ゲート評価順序（決定論・短絡、Design Memo M4 §2）:

1. ``observation.melody`` 設定不在 → ``not_observed(melody_config_missing)``
2. M3 comparison registry ロード（sha256 取得）。G1: ``evidence_thresholds.
   status != "frozen"`` または axes 空 → ``not_observed(comparator_uncalibrated)``
   （M1 registry はまだ読まない — R4-2・Codex round4 P2 対応。G1 を最優先
   短絡にするため、M1 のロードは 6 まで遅延する）
3. axis_policy 検証: frozen axes に無い軸の指定 → ``RecastError``（fail-closed・
   実行停止。エラーにするのはここだけ — G1〜G3 は「測れない」を正直に
   not_observed へ落とす）
4. 参照側入力: score_reference で artifact 不在 / bpm TODO →
   ``not_observed(author_input_missing)``。audio_reference で
   reference_audio 不在も同上。
5. G2: 帯域宣言が校正済み集合（``{"clear_lead"}``）外 →
   ``not_observed(band_out_of_validation(declared=...))``（audio_reference は
   両側に課す）
6. M1 registry ロード（sha256 取得。実際に `observability_thresholds` として
   必要になる直前）→ 抽出（テイク側。audio_reference は両側）→
   ``compare_melodies(...)`` 実行
7. G3: ``report.evidence == "not_comparable"`` → ``not_observed``
   （``report.reasons`` を転記）
8. 写像規則適用（本モジュール `map_axis_policy_to_adherence`）

ゲート不成立の短絡時も、判明している provenance（registry sha256 等）は entry
へ載せる（``_not_observed_entry`` が積み上げ済みの ``provenance`` dict をそのまま
使う）。ただし M1 registry sha256（``provenance["m1_registry_sha256"]``）は
6 に到達した run にしか載らない — 1〜5 の短絡（G1 短絡を含む）は M1 を読んで
いないため、その事実を捏造せず provenance から単純に省く（R4-2）。

score_reference の導出（Design Memo DD-1）: CompositionScore に旋律フィールドは
無いため、記号旋律の正典は identity sidecar の melody anchor artifact
（``artifact_type == "note_events_json"``, schema ``note-events/0.1``）とする。
``sec = start_beat * 60 / bpm``（``bpm = score.physical.bpm``）で `MelodyNote`
列を決定論導出する。pitch 文字列→MIDI 変換は `arrange/observe.py` の既存純関数
``_note_name_to_midi`` をそのまま再利用する（重複実装禁止・DD-1/DD-9）。
artifact 自体の JSON 構造検証（schema 一致・notes リスト・start_beat/
duration_beats の数値/有限性チェック）は本モジュールに独立実装を持つ——
`arrange/observe.py::_load_note_events_artifact` は pitch のみを返し
（v0 の恒等判定が pitch 系列限定のため beat/duration を意図的に捨てる）、M4 の
score_reference は beat→秒変換に beat/duration の値そのものを要るため、その値を
保持したまま返す別実装が必要になる（両実装とも "note events artifact に含まれる
値" という同じ入力を検証しているが、pitch 文字列→MIDI という「複製すると
ドリフトしうるロジック」自体は import で共有し重複させない、という DD-1/DD-9 の
要求を満たす）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import Field, model_validator

from svp_rpe.arrange.contract import ContractAnchor, PreservationContract
from svp_rpe.arrange.identity import AnchorDomain
from svp_rpe.arrange.models import PreservationMode
from svp_rpe.arrange.observe import NOTE_EVENTS_ARTIFACT_SCHEMA, _note_name_to_midi
from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined, validate_relative_locator
from svp_rpe.compose.models import CompositionScore
from svp_rpe.melody.comparison import MelodyComparisonReport, compare_melodies
from svp_rpe.melody.extractors import observe_via_route_with_provenance
from svp_rpe.melody.observability import (
    MelodyNote,
    MelodyObservation,
    ObservabilityThresholds,
)
from svp_rpe.melody.representation import M3ComparisonConfig, load_m3_registry
from svp_rpe.melody.routing import MelodyRoute, select_routes
from svp_rpe.recast.models import (
    CALIBRATION_BOUND_ROUTES,
    BackendRef,
    MelodyObservationConfig,
    RecastError,
)
from svp_rpe.recast.report_base import RecastReportModel
from svp_rpe.sentinels import is_todo_sentinel

__all__ = [
    "CALIBRATED_MELODY_TAKE_BANDS",
    "RouteRunner",
    "ExperimentalAdherenceStatus",
    "ExperimentalAnchorEntry",
    "derive_score_reference_observation",
    "map_axis_policy_to_adherence",
    "evaluate_melody_experimental_anchor",
    "resolve_melody_observation_paths",
    "resolve_melody_observation_paths_for_protection",
    "collect_melody_experimental_anchors",
    "melody_experimental_plan_warnings",
    "melody_experimental_anchor_ids",
    "resolve_main_observation_anchor_scope",
]

# M4 DD-4: 校正済み帯域は現状「単離済み clean lead」のみ。stem 帯（vocal_track/
# full_mix）解禁は別の一頁実測設計（M2e）で行う——ここで先取りして緩めない
# （上位設計書 §1 G2 の含意）。
CALIBRATED_MELODY_TAKE_BANDS: frozenset[str] = frozenset({"clear_lead"})

RouteRunner = Callable[[str], Tuple[MelodyObservation, Dict[str, Any]]]

ExperimentalAdherenceStatus = Literal[
    "preserved", "changed_within_policy", "changed_outside_policy", "not_observed"
]

# R5-2 (Codex round5 P2・読み戻し不変条件): axis_policy / axis_evidence の値語彙を
# Literal 化する——無制約 str 辞書のままだと、手編集/別経路の report が
# 「preserved なのに hard 軸が weak evidence」のような矛盾を素通しできてしまう
# （語彙自体の検証はここで、値の組み合わせの整合性は下記
# `_axis_policy_status` 併用の model_validator で担保する）。
# `AxisEvidenceVerdict` は `melody/comparison.py::_derive_evidence` が実際に
# 生成する語彙と同一（当該モジュールは凍結対象のため Literal 化はここで完結
# させ、`melody/` 側へは一切手を入れない）。
AxisEvidenceVerdict = Literal["strong", "weak", "none", "uncalibrated"]


def _axis_policy_status(
    axis_policy: Dict[str, str], axis_evidence: Dict[str, str]
) -> Tuple[ExperimentalAdherenceStatus, List[str]]:
    """`map_axis_policy_to_adherence`（M4a §3・写像規則）の非 report 依存コア:
    ``(axis_policy, axis_evidence)`` の組から D-1 語彙への純写像だけを行う。
    分岐そのものの意味論は `map_axis_policy_to_adherence` の docstring を参照
    （本関数はそこから ``report`` 依存部分——``report.reasons`` の転記——を
    除いた同一ロジック）。

    戻り値の ``reasons`` は本関数由来の追加理由のみ
    （``comparator_uncalibrated(axis=X)`` / ``insufficient_evidence``）——
    ``report.reasons``（比較器由来の転記）は含まない。呼び出し側が必要に応じて
    連結する。

    R5-2 (Codex round5 P2): `ExperimentalAnchorEntry` の読み戻し validator
    （`_validate_adherence_status_matches_axis_evidence`）が
    `map_axis_policy_to_adherence`（builder が使う写像関数）と本関数を共有する
    ——builder/validator の二重実装を禁止する single source。両者は
    ``adherence_status`` の再計算にのみ同じ関数を使い、``reasons`` の再計算
    一致までは要求しない（線引きは validator の docstring 参照）。
    """
    reasons: List[str] = []
    hard_axes = [axis for axis, mode in axis_policy.items() if mode == "hard"]
    elastic_axes = [axis for axis, mode in axis_policy.items() if mode == "elastic"]

    for axis in sorted(hard_axes + elastic_axes):
        verdict = axis_evidence.get(axis, "uncalibrated")
        if verdict == "uncalibrated":
            reasons.append(f"comparator_uncalibrated(axis={axis})")
            return "not_observed", reasons

    if any(axis_evidence[axis] == "none" for axis in hard_axes):
        return "changed_outside_policy", reasons
    if any(axis_evidence[axis] == "weak" for axis in hard_axes):
        reasons.append("insufficient_evidence")
        return "not_observed", reasons
    if any(axis_evidence[axis] in ("none", "weak") for axis in elastic_axes):
        return "changed_within_policy", reasons
    return "preserved", reasons


class ExperimentalAnchorEntry(RecastReportModel):
    """1 melody（将来他ドメインも）experimental anchor 分の翻訳結果（Design Memo M4 §4）。

    本会計（`RecastReport.coverage` の verified/violated/not_observed 分母）には
    一切算入しない——`RecastReport` への統合（`experimental_anchors` 節への
    追加・会計分離の徹底）は M4c（本フェーズの担当外）で行う。
    """

    adherence_status: ExperimentalAdherenceStatus
    anchor_id: str
    domain: AnchorDomain
    axis_policy: Dict[str, PreservationMode]
    axes: Dict[str, Optional[float]]
    axis_evidence: Dict[str, AxisEvidenceVerdict]
    coverage: Dict[str, float]
    octave_artifact_suspected: bool
    reasons: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_adherence_status_matches_axis_evidence(self) -> "ExperimentalAnchorEntry":
        """R5-2 (Codex round5 P2・読み戻し不変条件): ``adherence_status`` が
        確定値（``preserved`` / ``changed_within_policy`` /
        ``changed_outside_policy``）のとき、entry 自身の ``axis_policy`` +
        ``axis_evidence`` へ builder と同一の純関数 `_axis_policy_status`
        （`map_axis_policy_to_adherence` のコア、二重実装禁止）を再適用し、
        再計算結果と ``adherence_status`` の一致を要求する——手編集や別経路で
        生成された report が「preserved なのに hard 軸が weak evidence」の
        ような矛盾を valid として通すのを防ぐ（repo の読み戻し安全網パターン
        = `RecastReportAnchor._validate_coverage_matches_adherence_status` と
        同型）。

        **``not_observed`` は再計算免除する**: 短絡 entry（config 不在・
        G1/axis_policy/author_input/G2 不成立・G3 not_comparable 等）は
        axis_evidence を持たない場合がある（`_not_observed_entry` は
        ``axis_evidence={}`` のまま entry を組む）——`_axis_policy_status` を
        素直に適用すると axis_evidence 欠落から `comparator_uncalibrated`
        判定に強制的に倒れてしまうが、それは短絡理由（G1/G2/author_input 等）
        を再現できないだけで矛盾ではない。``not_observed`` は元々「測れない」
        という保守側方向の結論であり、その内部理由の細分まで読み戻し時に
        再現できる必要はないため免除する。

        **``reasons`` の再計算一致までは要求しない**（線引き）: ``reasons`` は
        ``report.reasons``（比較器由来の転記 — ``axes_disagree(...)`` や
        octave artifact 理由等）を含み、決定論的だが provenance 的な自由文字列
        を含むため `_axis_policy_status` の純粋な出力だけでは再現できない。
        本 validator は ``adherence_status`` の整合性のみを不変条件とする。
        """
        if self.adherence_status == "not_observed":
            return self
        expected_status, _extra_reasons = _axis_policy_status(self.axis_policy, self.axis_evidence)
        if expected_status != self.adherence_status:
            raise ValueError(
                f"ExperimentalAnchorEntry '{self.anchor_id}': adherence_status "
                f"{self.adherence_status!r} does not match the status recomputed from "
                f"axis_policy + axis_evidence via the D-1 mapping ({expected_status!r}) "
                "(R5-2 readback invariant)"
            )
        return self


# --------------------------------------------------------------------------- #
# score_reference 導出（M4b, DD-1）
# --------------------------------------------------------------------------- #
def _parse_note_events_with_timing(
    raw_bytes: bytes, *, artifact_path: "str | Path" = "<melody artifact>"
) -> List[Tuple[float, float, str]]:
    """``note-events/0.1`` artifact (JSON) を onset 順の ``(start_beat,
    duration_beats, pitch)`` 列へ変換する。

    `arrange/observe.py::_load_note_events_artifact` と同じ構造検証（schema 一致
    ・notes 非空リスト・各エントリの pitch/start_beat/duration_beats 必須・数値/
    有限性/非負チェック）を行うが、pitch 文字列を MIDI へは変換せず beat 値も
    保持したまま返す——score_reference の beat→秒変換に beat/duration の値
    そのものが要るため（`_load_note_events_artifact` は v0 の pitch-only 比較の
    ために意図的にこれらを捨てている）。onset 順の並べ替え規則（``(start_beat,
    元の artifact 内 index)`` によるタイブレーク）も同一に保つ。
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

    entries: List[Tuple[float, int, float, str]] = []
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
        if not math.isfinite(start_beat):
            raise ValueError(
                "note events artifact note 'start_beat' must be finite "
                f"(not NaN/Infinity): {item!r} ({artifact_path})"
            )
        if not isinstance(duration_beats, (int, float)) or isinstance(duration_beats, bool):
            raise ValueError(
                "note events artifact note 'duration_beats' must be numeric: "
                f"{item!r} ({artifact_path})"
            )
        if not math.isfinite(duration_beats):
            raise ValueError(
                "note events artifact note 'duration_beats' must be finite "
                f"(not NaN/Infinity): {item!r} ({artifact_path})"
            )
        if duration_beats < 0:
            raise ValueError(
                "note events artifact note 'duration_beats' must be non-negative: "
                f"{item!r} ({artifact_path})"
            )
        entries.append((float(start_beat), index, float(duration_beats), pitch))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [(start_beat, duration_beats, pitch) for start_beat, _, duration_beats, pitch in entries]


def derive_score_reference_observation(
    score: CompositionScore,
    melody_artifact_bytes: Optional[bytes],
    *,
    artifact_path: "str | Path" = "<melody artifact>",
    route: str = "score_reference",
) -> Tuple[Optional[MelodyObservation], Optional[str]]:
    """記号旋律（note-events/0.1 artifact）+ bpm → `MelodyObservation` を決定論導出する
    （Design Memo M4 DD-1・M4b）。

    Returns ``(observation, None)`` on success, or ``(None, reason)`` when the
    author-side input is missing (`melody_artifact_bytes` が ``None`` ——
    artifact 不在、または ``score.physical.bpm`` が転写 TODO センチネル
    （`sentinels.is_todo_sentinel`）) —— `reason` は常に ``"author_input_missing"``
    （DD-1）。``bpm`` が数値として解決できても非正（``<= 0``）の場合は
    ``"author_input_invalid(bpm=<値>)"``（R2-7・Codex round2 P2）——author 入力は
    存在するが値として使えないという別の事実を、欠落と混同せず正直に報告する。
    artifact が存在するのに内容が壊れている（schema 不一致・型不正等）
    場合は not_observed へ落とさず ``ValueError`` を fail-closed で送出する
    （「入力が無い」と「入力が壊れている」は別の事実であり、後者を正直会計の
    ``not_observed`` で握りつぶさない——`arrange/observe.py` の既存 sensor 群と
    同じ posture）。

    ``sec = start_beat * 60 / bpm``。confidence は記号由来のため常に 1.0
    （抽出誤差の概念が無い＝原曲側の抽出誤差ゼロ、上位設計書 §2）。
    """
    bpm = score.physical.bpm
    if is_todo_sentinel(bpm):
        return None, "author_input_missing"
    if melody_artifact_bytes is None:
        return None, "author_input_missing"

    bpm_value = float(bpm)
    # R2-7 (Codex round2 P2): beat→秒変換（60/bpm）の前に bpm > 0 を検証する。
    # bpm=0 は ZeroDivisionError、負値は負タイムスタンプがそのまま整列へ
    # 流れ込む（`compare_melodies` 側で意味不明な挙動になる）——author_input_
    # missing（入力が無い）とは別の事実（入力はあるが値が壊れている）なので、
    # 専用の理由語彙 `author_input_invalid` で not_observed へ落とす（捏造せず
    # 保守側へ倒す。例外で fail-fast もしない——bpm は author 入力であり、
    # 「入力が無い」と同じ「測れない」事実の一種として扱う）。
    if not bpm_value > 0:
        return None, f"author_input_invalid(bpm={bpm_value:.3f})"
    entries = _parse_note_events_with_timing(melody_artifact_bytes, artifact_path=artifact_path)
    seconds_per_beat = 60.0 / bpm_value
    notes = tuple(
        MelodyNote(
            start_sec=start_beat * seconds_per_beat,
            end_sec=(start_beat + duration_beats) * seconds_per_beat,
            pitch_midi=float(_note_name_to_midi(pitch)),
            confidence=1.0,
        )
        for start_beat, duration_beats, pitch in entries
    )
    total_duration_sec = max((note.end_sec for note in notes), default=0.0)
    observation = MelodyObservation(
        route=route,
        source_model="symbolic:note-events/0.1",
        notes=notes,
        total_duration_sec=total_duration_sec,
    )
    return observation, None


# --------------------------------------------------------------------------- #
# 写像規則（M4a §3・純関数）
# --------------------------------------------------------------------------- #
def map_axis_policy_to_adherence(
    axis_policy: Dict[str, str], report: MelodyComparisonReport
) -> Tuple[ExperimentalAdherenceStatus, List[str]]:
    """M3 evidence（`report.axis_evidence`）を `axis_policy` に照らして D-1
    語彙へ写像する（Design Memo M4 §3・純関数）。G1–G3 通過済み前提
    （``report.evidence != "not_comparable"``）。

    分岐（優先順）:

    1. 判定参加軸（hard/elastic のみ——R2-5・Codex round2 P2）に evidence が
       判定不能（"uncalibrated"、または欠落——後者は `_derive_evidence` が
       `status=="frozen"` のとき軸を丸ごと省略しうるため防御的に同一視する）
       → ``not_observed(comparator_uncalibrated(axis=X))``。free 軸は評価対象
       外（欠落していてもこの分岐を起動しない）
    2. hard 軸のどれかが evidence "none" → ``changed_outside_policy``
    3. hard 軸のどれかが evidence "weak" → ``not_observed(insufficient_evidence)``
       （weak evidence から preserved を主張しない・保守側へ倒す）
    4. elastic 軸に "none"/"weak" があれば → ``changed_within_policy``
    5. それ以外（全 hard/elastic 軸 strong）→ ``preserved``

    free 軸は判定に不参加（呼び出し側が `axes`/`axis_evidence` へ全軸を転記する
    ——本関数は判定にのみ関与する）。``report.reasons``（``axes_disagree(...)``・
    octave artifact 理由等）は常に戻り値の reasons へ転記する（隠さない）。

    R5-2 (Codex round5 P2): 分岐そのものの実装は `_axis_policy_status`
    （``report`` に依存しない純コア）へ委譲する——`ExperimentalAnchorEntry`
    の読み戻し validator が同じコアを共有し、builder/validator の二重実装を
    避ける single source にする。本関数は ``report.reasons`` の転記を
    ``_axis_policy_status`` の追加 reasons へ連結するだけの薄い wrapper。
    """
    status, extra_reasons = _axis_policy_status(axis_policy, report.axis_evidence)
    return status, [*report.reasons, *extra_reasons]


# --------------------------------------------------------------------------- #
# 起動ゲート G1–G3 + orchestration（M4a・score_reference 配線は M4b）
# --------------------------------------------------------------------------- #
def _load_m3_registry_with_gate(
    path: "str | Path",
) -> Tuple[Optional[M3ComparisonConfig], str, Optional[str]]:
    """`load_m3_registry` + G1 判定。戻り値は ``(config_or_none, sha256,
    reason_or_none)`` —— G1 不成立時は ``config`` に ``None``、``reason`` に
    ``"comparator_uncalibrated"`` を返す（sha256 は常に返す。短絡時も
    provenance に registry sha256 を載せるため）。"""
    config, sha256_hex = load_m3_registry(path)
    thresholds = config.evidence_thresholds
    if thresholds.status != "frozen" or not thresholds.axes:
        return None, sha256_hex, "comparator_uncalibrated"
    return config, sha256_hex, None


# R4-3 (Codex round4 P2・部分採用+境界宣言): `_load_m1_registry` が構築前の
# mapping 段階で検証する「型 + 有限性」の対象フィールド一覧。
# `ObservabilityThresholds` の dataclass フィールド定義（`melody/observability.py`）
# と同期させる single source（int 系フィールドの一覧はここが正）。
_M1_INT_FIELDS: frozenset[str] = frozenset(
    {"min_note_count", "min_phrase_count", "note_min_run_frames", "median_filter_frames"}
)
# Optional フィールド（``None`` を許容する）。現状 `min_cross_extractor_agreement`
# の 1 件のみ（`ObservabilityThresholds` で唯一の `Optional[float]`）。
_M1_OPTIONAL_FIELDS: frozenset[str] = frozenset({"min_cross_extractor_agreement"})


def _validate_observation_gate_field_types(mapping: Dict[str, Any], *, path: "str | Path") -> None:
    """R4-3 (Codex round4 P2・部分採用+境界宣言): ``observation_gate`` の各値に
    ついて「型 + 有限性」の汎用検証だけを行う——`ObservabilityThresholds.
    from_registry` は素の dataclass 構築で値検証をしないため、文字列値は
    後段 `assess_observability` で未捕捉 `TypeError` になり、負値/NaN は
    ゲートを静かに弱めていた。

    検証内容: int 系フィールド（`_M1_INT_FIELDS` — ``note_min_run_frames`` /
    ``median_filter_frames`` 等）は「bool でない int」、それ以外の既知
    フィールドは「bool でない実数値（int/float）かつ ``math.isfinite``」を
    要求する。``bool`` を明示的に除外するのは Python の ``bool`` が ``int``
    のサブクラスであるため（``isinstance(True, int) is True``）。Optional
    フィールド（`_M1_OPTIONAL_FIELDS`）の ``None`` は許容する。

    **意図的に行わない検証（境界宣言）**: フィールド別の値域・意味論検証
    （例: ``min_voiced_coverage`` ∈ [0, 1] のような rate 制約）はここでは
    行わない——それは M1 registry の統治（凍結・事前登録、
    `docs/melody_observability.md`）側の責務であり、M4（本 loader）が M1 の
    意味論を再実装すると知識の重複と乖離リスクを生む。ここで検証するのは
    「値が計算可能な形をしているか」という M4 自身の実行安全性の話に限定する
    （文字列値の未捕捉 TypeError・NaN/inf によるゲート無力化の防止）。

    未知キー（`ObservabilityThresholds` のフィールドに無いキー）はここでは
    判定しない——`from_registry` 自身の「未知キーは pre-registration
    violation」検査に委ねる（知識の重複を避ける・R3-4 と同じ分業）。
    """
    known_fields = _M1_INT_FIELDS | _M1_OPTIONAL_FIELDS | {
        "min_voiced_coverage",
        "min_confidence_mean",
        "max_low_confidence_rate",
        "max_octave_jump_rate",
        "voiced_confidence_floor",
        "low_confidence_floor",
        "phrase_gap_sec",
        "octave_jump_semitones",
    }
    for key, value in mapping.items():
        if key not in known_fields:
            continue  # 未知キーは from_registry の pre-registration 検査に委ねる。
        if value is None and key in _M1_OPTIONAL_FIELDS:
            continue
        if key in _M1_INT_FIELDS:
            if not isinstance(value, int) or isinstance(value, bool):
                raise RecastError(
                    f"M1 registry at {path}: 'observation_gate.{key}' must be a "
                    f"non-bool int, got {value!r} ({type(value).__name__})"
                )
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RecastError(
                f"M1 registry at {path}: 'observation_gate.{key}' must be a "
                f"non-bool real number (int/float), got {value!r} ({type(value).__name__})"
            )
        if not math.isfinite(value):
            raise RecastError(
                f"M1 registry at {path}: 'observation_gate.{key}' must be finite "
                f"(not NaN/Infinity), got {value!r}"
            )


def _load_m1_registry(path: "str | Path") -> Tuple[ObservabilityThresholds, str]:
    """M1 `registry.yaml` の ``observation_gate`` 節をロードする（sha256 込み・
    single read）。`tests/test_melody_comparison.py` の loader パターンと同型。

    R2-2 (Codex round2 P2): 構造検証を fail-closed で行う——空/スカラー YAML
    や `observation_gate` 節欠落を素通しすると、直後の dict 添字/`from_registry`
    の `set(mapping)` が `TypeError`/`KeyError` を未捕捉のまま送出し、CLI が
    traceback 付きで落ちる。ここで actionable な `RecastError` に変換して
    既存の捕捉経路（呼び出し側の `except (..., RecastError, ...)`）に乗せる。

    R4-3 (Codex round4 P2・部分採用+境界宣言): `from_registry` 呼び出しの
    **前**に `_validate_observation_gate_field_types` で型 + 有限性を検証する
    （境界宣言はそちらの docstring 参照）。

    R5-1 (Codex round5 P2 対応): ``path`` 自体が読めない場合（欠落・ディレクトリ
    等の `OSError`）も actionable な `RecastError` へ変換する——呼び出し側
    （`resolve_melody_observation_paths` を ``m1_require_exists=False`` で呼ぶ
    `collect_melody_experimental_anchors`/`melody_experimental_plan_warnings`）
    は G1 短絡を先に評価させるため M1 registry の実在検証を意図的に
    ここまで遅延させている。したがって「M1 registry が欠落している」という
    設定エラーは、G1 通過後に到達する本関数が唯一検出する場所になる——
    生の `FileNotFoundError`（他の未捕捉 `OSError` 同様）を CLI まで
    伝播させず、既存の捕捉経路（呼び出し側の
    `except (..., RecastError, ...)`）に乗せる。
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise RecastError(
            f"M1 registry at {path} could not be read: {type(exc).__name__}: {exc}"
        ) from exc
    mapping = yaml.safe_load(data)
    if not isinstance(mapping, dict):
        raise RecastError(
            f"M1 registry at {path} must be a YAML mapping at the top level "
            f"(got {type(mapping).__name__})"
        )
    if "observation_gate" not in mapping:
        raise RecastError(
            f"M1 registry at {path} is missing the required top-level key 'observation_gate'"
        )
    observation_gate = mapping["observation_gate"]
    if not isinstance(observation_gate, dict):
        raise RecastError(
            f"M1 registry at {path}: 'observation_gate' must be a mapping "
            f"(got {type(observation_gate).__name__})"
        )
    # R3-4 (Codex round3 P2 対応): 構造検証（top-level / `observation_gate`
    # 節の存在）は上で fail-closed 済みだが、フィールドレベルの検証
    # （必須キー欠落・型不正等）は `ObservabilityThresholds.from_registry`
    # 自身に委ねている——例えば `observation_gate: {}` は上の 2 検査を
    # 通過してしまい、`from_registry` の必須引数欠落 `TypeError` が未捕捉の
    # まま呼び出し側（observed 記録後）まで伝播し traceback 付きで CLI が
    # 落ちていた。ここで `from_registry` 呼び出しだけを try/except で包み、
    # 元の例外メッセージを含む actionable な `RecastError` へ翻訳する——
    # M1 側の必須フィールド一覧をここへ再実装するのではなく、
    # `from_registry` が既に持つ検証結果をそのまま翻訳するだけ（知識の重複
    # を避ける）。既存の捕捉経路（呼び出し側の
    # `except (..., RecastError, ...)`）にそのまま乗る。
    #
    # R4-3 (Codex round4 P2・部分採用+境界宣言): `from_registry` は素の
    # dataclass 構築で値検証をしないため、文字列値は後段 `assess_
    # observability` で未捕捉 `TypeError`、負値/NaN はゲートを静かに弱める
    # ——ここで型 + 有限性だけを先に検証する（値域・意味論検証は不採用、
    # `_validate_observation_gate_field_types` docstring の境界宣言を参照）。
    _validate_observation_gate_field_types(observation_gate, path=path)
    try:
        thresholds = ObservabilityThresholds.from_registry(observation_gate)
    except (TypeError, ValueError, KeyError) as exc:
        raise RecastError(
            f"M1 registry at {path}: 'observation_gate' failed validation: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return thresholds, hashlib.sha256(data).hexdigest()


def _resolve_clear_lead_route(route_name: str) -> MelodyRoute:
    """``route_name`` に対応する ``clear_lead`` 帯の `MelodyRoute` を返す。

    `MelodyObservationConfig` 自身の validator が既に route 名を
    ``select_routes("clear_lead") ∩ CALIBRATION_BOUND_ROUTES``（R1-1）に
    限定しているため、ここへ来て見つからない/校正済み集合外なのは
    呼び出し契約違反（プログラミングエラー）—— `RecastError` ではなく
    `ValueError` で fail-fast する（defense in depth）。
    """
    if route_name not in CALIBRATION_BOUND_ROUTES:
        raise ValueError(
            f"route {route_name!r} is not calibration-bound "
            "(MelodyObservationConfig.route validation should have rejected this earlier; "
            f"expected one of {sorted(CALIBRATION_BOUND_ROUTES)})"
        )
    for route in select_routes("clear_lead"):
        if route.name == route_name:
            return route
    raise ValueError(
        f"route {route_name!r} is not a 'clear_lead' route "
        "(MelodyObservationConfig.route validation should have rejected this earlier)"
    )


def _default_route_runner(route_name: str) -> RouteRunner:
    """既定の route_runner: 実抽出器（`melody.extractors.
    observe_via_route_with_provenance`）。`scripts/run_melody_comparison.py`
    の ``_default_route_runner`` と同型（route_runner 注入 seam のパターン継承・
    Design Memo DD-5）。"""
    route = _resolve_clear_lead_route(route_name)

    def _runner(audio_path: str) -> Tuple[MelodyObservation, Dict[str, Any]]:
        return observe_via_route_with_provenance(audio_path, route)

    return _runner


def _check_melody_take_band(declared: Optional[str]) -> Optional[str]:
    """G2: 校正済み帯域集合外なら reason 文字列を返す（合格なら ``None``）。"""
    if declared not in CALIBRATED_MELODY_TAKE_BANDS:
        shown = declared if declared is not None else "none"
        return f"band_out_of_validation(declared={shown})"
    return None


def _not_observed_entry(
    *, anchor: ContractAnchor, reason: str, provenance: Dict[str, Any]
) -> ExperimentalAnchorEntry:
    """短絡時の `not_observed` entry を組む。判明済みの axis_policy の軸名は
    ``axes``/``axis_evidence`` の骨組みとして載せるが値は ``None``/欠落のまま
    ——「測れない」を正直に空で表す（捏造しない）。"""
    assert anchor.axis_policy is not None  # opt-in 前提（呼び出し側契約）
    return ExperimentalAnchorEntry(
        adherence_status="not_observed",
        anchor_id=anchor.anchor_id,
        domain=anchor.domain,
        axis_policy=dict(anchor.axis_policy),
        axes={axis: None for axis in anchor.axis_policy},
        axis_evidence={},
        coverage={},
        octave_artifact_suspected=False,
        reasons=[reason],
        provenance=dict(provenance),
    )


def _freeze_audio_copy(audio_path: "str | Path", staging_dir: str) -> Tuple[str, str]:
    """R2-3 (Codex round2 P2・TOCTOU 封鎖): ``audio_path`` の bytes を一度だけ
    読み、sha256 と同一 bytes の凍結コピーパスを返す（``(sha256_digest,
    frozen_path)``）。

    `scripts/run_melody_comparison.py:_freeze_audio_copy` と同型パターン
    （既存の確立済みリポジトリ規約をそのまま踏襲——重複実装ではなく同じ
    設計の 2 箇所目の適用）: sha256 検証後〜再 open までの間に ``audio_path``
    が差し替えられても、抽出器が実際に読むのは「検証した bytes と同一の」
    凍結コピーであることを構造的に保証する。``staging_dir`` は呼び出し側が
    `tempfile.TemporaryDirectory` で管理する run 出力ディレクトリ外の一時
    領域（呼び出し側の `with` ブロック終了時に自動で後始末される）。
    """
    raw_bytes = Path(audio_path).read_bytes()
    sha256_digest = hashlib.sha256(raw_bytes).hexdigest()
    suffix = Path(audio_path).suffix
    fd, tmp_name = tempfile.mkstemp(prefix="recast_melody_freeze_", suffix=suffix, dir=staging_dir)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw_bytes)
    return sha256_digest, tmp_name


def evaluate_melody_experimental_anchor(
    *,
    anchor: ContractAnchor,
    melody_config: Optional[MelodyObservationConfig],
    score: CompositionScore,
    melody_artifact_bytes: Optional[bytes] = None,
    melody_artifact_sha256: Optional[str] = None,
    melody_take_band: Optional[str] = None,
    take_audio_path: Optional[str] = None,
    expected_take_sha256: Optional[str] = None,
    m3_registry_path: "str | Path",
    m1_registry_path: "str | Path",
    reference_audio_path: Optional[str] = None,
    reference_melody_band: Optional[str] = None,
    route_runner: Optional[RouteRunner] = None,
    reference_route_runner: Optional[RouteRunner] = None,
) -> ExperimentalAnchorEntry:
    """melody experimental anchor 1 件を評価する（Design Memo M4 §2 の 8 段ゲート
    + §3 写像の orchestration）。

    呼び出し前提: ``anchor.axis_policy is not None``（opt-in・DD-3。契約に
    axis_policy が無い melody anchor は本関数を呼ばず、`_observe_melody`（LCS・
    本会計）の現行挙動をそのまま使う——呼び出し側 [M4c、本フェーズ担当外] の
    責務）。

    ``melody_take_band`` は ``BackendRef.melody_take_band``（G2、テイク側）。
    ``reference_melody_band`` は ``reference == "audio"`` のときの原曲側帯域
    宣言——`MelodyObservationConfig.reference_band`（DD-10b、additive）から
    呼び出し側が配線する。既定 ``None``（宣言なし）は現行どおり G2 不成立
    （``band_out_of_validation(declared=none)``）に倒れる。``reference ==
    "score"`` では `MelodyObservationConfig` 自身の validator が
    `reference_band` の宣言そのものを拒否するため、呼び出し側は本引数を
    渡さない（score_reference には帯域の概念が無い）。

    ``route_runner`` / ``reference_route_runner`` は抽出器非依存の注入 seam
    （`scripts/run_melody_comparison.py` の route_runner パターンと同型・
    DD-5）: 既定は実抽出器、注入時は provenance に ``extractor_injected: true``
    を刻む。

    ``expected_take_sha256``（R2-3・Codex round2 P2・TOCTOU 封鎖）: 呼び出し側
    が別時点で既に確定させた ``take_audio_path`` の sha256（`recast ingest`
    では `collect()`/`invoke()` が返す `GeneratedTake.sha256`）。抽出直前に
    take を run 出力ディレクトリ外の一時コピーへ凍結し、そのコピーの sha256
    と突き合わせる——不一致は `RecastError`（呼び出し側は
    ``observation_incomplete`` 経路へ倒す）。省略時（``None``）は突合を行わない
    （凍結コピー自体は行う——抽出器が実際に読む bytes を固定する目的は変わらない）。
    原曲側（``reference == "audio"``）の ``reference_audio_path`` にも同様の
    凍結コピーを適用するが、こちらは事前の外部 pin が無いため、凍結時に読んだ
    bytes の sha256 をそのまま provenance の ``reference_audio_sha256`` として
    記録する（それ自体が以降の pin になる）。M3 extractors（`route_runner`
    契約）は無変更——凍結コピーのパスを渡すだけ。
    """
    if anchor.axis_policy is None:
        raise ValueError(
            f"evaluate_melody_experimental_anchor: anchor '{anchor.anchor_id}' has no "
            "axis_policy (M4 experimental evaluation is opt-in; the caller must not "
            "invoke this function for anchors without axis_policy)"
        )
    axis_policy = anchor.axis_policy
    provenance: Dict[str, Any] = {}

    # 1) observation.melody 設定不在。
    if melody_config is None:
        return _not_observed_entry(
            anchor=anchor, reason="melody_config_missing", provenance=provenance
        )

    # 2) registry ロード + G1。
    # R4-2 (Codex round4 P2 対応): M1 registry のロードはここでは行わない
    # ——G1（M3 校正判定）を最優先短絡にするため。旧実装は G1 判定より前に
    # M1 registry を読んでいたため、M3 未校正（本来 dormant not_observed）+
    # M1 破損の組み合わせで ingest が fail していた（設計のゲート順序違反）。
    # M1 ロードは実際に必要になる直前（下記 6 の抽出/compare 直前）へ遅延する
    # ——G1 短絡時は M1 を読まないため、その短絡 entry の provenance に
    # `m1_registry_sha256` は載らない（「判明分のみ・捏造禁止」の原則どおり
    # 正直な帰結）。
    m3_config, m3_sha256, g1_reason = _load_m3_registry_with_gate(m3_registry_path)
    provenance["m3_registry_sha256"] = m3_sha256
    if g1_reason is not None or m3_config is None:
        return _not_observed_entry(anchor=anchor, reason=g1_reason or "comparator_uncalibrated", provenance=provenance)

    # 3) axis_policy vs frozen axes（fail-closed・RecastError）。
    frozen_axes = set((m3_config.evidence_thresholds.axes or {}).keys())
    uncalibrated_axes = sorted(set(axis_policy) - frozen_axes)
    if uncalibrated_axes:
        raise RecastError(
            f"melody anchor '{anchor.anchor_id}': axis_policy declares axis(es) not "
            f"calibrated in the frozen m3 comparison registry: {uncalibrated_axes} "
            f"(frozen axes: {sorted(frozen_axes)})"
        )

    # 4) 参照側入力。
    reference_observation: Optional[MelodyObservation] = None
    if melody_config.reference == "score":
        provenance["reference"] = "score"
        reference_observation, reason = derive_score_reference_observation(
            score, melody_artifact_bytes
        )
        if reference_observation is None:
            return _not_observed_entry(
                anchor=anchor, reason=reason or "author_input_missing", provenance=provenance
            )
        if melody_artifact_sha256 is not None:
            provenance["melody_artifact_sha256"] = melody_artifact_sha256
    else:  # "audio"
        provenance["reference"] = "audio"
        # R5-1 (Codex round5 P2 対応): `resolve_melody_observation_paths` は
        # G1 短絡を先に評価させるため reference_audio を
        # ``require_exists=False`` で解決する（呼び出し側
        # `collect_melody_experimental_anchors`/`melody_experimental_plan_
        # warnings` 参照）——よって ``reference_audio_path`` は「設定は
        # あるが実ファイルが無い」状態でここへ渡り得る。author 入力の欠落
        # （設定なし = ``None`` / 設定はあるが実在しない）はどちらも同じ
        # 「測れない」事実として ``author_input_missing`` へ倒す（ここが
        # 唯一の実在検証位置——G1/axis_policy を通過した run だけがここへ
        # 到達する）。
        if reference_audio_path is None or not Path(reference_audio_path).is_file():
            return _not_observed_entry(
                anchor=anchor, reason="author_input_missing", provenance=provenance
            )

    # 5) G2 帯域宣言。
    take_band_reason = _check_melody_take_band(melody_take_band)
    if take_band_reason is not None:
        return _not_observed_entry(anchor=anchor, reason=take_band_reason, provenance=provenance)
    if melody_config.reference == "audio":
        reference_band_reason = _check_melody_take_band(reference_melody_band)
        if reference_band_reason is not None:
            return _not_observed_entry(
                anchor=anchor, reason=reference_band_reason, provenance=provenance
            )

    # 6) 抽出 + compare_melodies。
    if take_audio_path is None:
        raise ValueError(
            "evaluate_melody_experimental_anchor: take_audio_path is required once "
            "gates G1/axis_policy/author_input/G2 have passed"
        )
    # R4-2 (Codex round4 P2 対応): M1 registry のロードはここまで遅延する
    # ——実際に必要になる直前（`compare_melodies` の `observability_thresholds`
    # 引数）。G1〜G2/axis_policy/author_input を全て通過した run だけが M1 を
    # 読む（`_load_m1_registry` の fail-closed 検証・RecastError もこの時点で
    # 初めて発生しうる）。
    m1_thresholds, m1_sha256 = _load_m1_registry(m1_registry_path)
    provenance["m1_registry_sha256"] = m1_sha256
    # R1-2 (Codex round1 P2 対応): 非注入（実抽出器）時のみ、抽出器 provenance
    # （code/weights pin 等 — `observe_via_route_with_provenance` の第 2 戻り値）
    # を take/reference それぞれの名前空間へ保存する。注入時（テスト等）は
    # 従来どおり `extractor_injected: true` のみ——注入 runner が返す extra は
    # test 値であり、偽の pin として report に刻まない。
    take_injected = route_runner is not None
    take_runner = route_runner or _default_route_runner(melody_config.route)

    reference_injected = False
    # R2-3 (Codex round2 P2・TOCTOU 封鎖): sha256 検証後〜再 open までの窓を
    # 塞ぐため、抽出対象の音声は run 出力ディレクトリ外の一時コピーへ凍結して
    # から読む（`_freeze_audio_copy` docstring 参照）。`route_runner` 注入
    # （テスト seam）にもこの凍結コピーのパスが渡る——既存 fake pattern は
    # bytes 内容で引く方式なら耐性がある（`test_m3_comparison_harness.py`
    # の `notes_by_content` と同型）。
    with tempfile.TemporaryDirectory(prefix="recast_melody_freeze_") as staging_dir:
        take_actual_sha256, frozen_take_path = _freeze_audio_copy(take_audio_path, staging_dir)
        if expected_take_sha256 is not None and take_actual_sha256 != expected_take_sha256:
            raise RecastError(
                f"melody anchor '{anchor.anchor_id}': take audio at {take_audio_path} does "
                "not match its pinned sha256 (TOCTOU): expected "
                f"{expected_take_sha256}, got {take_actual_sha256}"
            )
        take_observation, take_extra_provenance = take_runner(frozen_take_path)
        if not take_injected and take_extra_provenance:
            provenance["take_extractor"] = dict(take_extra_provenance)

        if melody_config.reference == "audio":
            if reference_audio_path is None:  # pragma: no cover - guarded above
                raise ValueError("reference_audio_path is required for reference='audio'")
            reference_actual_sha256, frozen_reference_path = _freeze_audio_copy(
                reference_audio_path, staging_dir
            )
            provenance["reference_audio_sha256"] = reference_actual_sha256
            reference_injected = reference_route_runner is not None
            ref_runner = reference_route_runner or _default_route_runner(melody_config.route)
            reference_observation, reference_extra_provenance = ref_runner(frozen_reference_path)
            if not reference_injected and reference_extra_provenance:
                provenance["reference_extractor"] = dict(reference_extra_provenance)

    assert reference_observation is not None  # both branches set it before reaching here
    if take_injected or reference_injected:
        provenance["extractor_injected"] = True

    # R1-4 (Codex round1 P2・層分離裁定): score_reference は記号旋律の抽出誤差
    # 概念が無いため、M1 観測ゲート（`compare_melodies` 内 `assess_observability`）
    # を参照側（"a"）へ課さない——テイク側（"b"）には引き続き課す。M3 被覆下限
    # （coverage.floor・pair-level 比較整合性）は両側のまま維持（sided 化しない
    # ——additive seam、既定値 ("a","b") は audio_reference で凍結挙動と一致）。
    gate_sides: Tuple[str, ...] = ("b",) if melody_config.reference == "score" else ("a", "b")
    report = compare_melodies(
        reference_observation,
        take_observation,
        observability_thresholds=m1_thresholds,
        config=m3_config,
        provenance_extra=dict(provenance),
        observability_gate_sides=gate_sides,
    )

    # 7) G3: not_comparable。
    if report.evidence == "not_comparable":
        merged_provenance = dict(report.provenance)
        return ExperimentalAnchorEntry(
            adherence_status="not_observed",
            anchor_id=anchor.anchor_id,
            domain=anchor.domain,
            axis_policy=dict(axis_policy),
            axes={axis: None for axis in axis_policy},
            axis_evidence={},
            coverage=dict(report.coverage),
            octave_artifact_suspected=False,
            reasons=list(report.reasons),
            provenance=merged_provenance,
        )

    # 8) 写像規則。
    status, reasons = map_axis_policy_to_adherence(axis_policy, report)
    return ExperimentalAnchorEntry(
        adherence_status=status,
        anchor_id=anchor.anchor_id,
        domain=anchor.domain,
        axis_policy=dict(axis_policy),
        axes=dict(report.axes),
        axis_evidence=dict(report.axis_evidence),
        coverage=dict(report.coverage),
        octave_artifact_suspected=report.octave_artifact_suspected,
        reasons=reasons,
        provenance=dict(report.provenance),
    )


# --------------------------------------------------------------------------- #
# M4c — ingest/observe/report・plan への配線（orchestration・純関数中心）
# --------------------------------------------------------------------------- #
def _melody_anchors_with_axis_policy(
    contract: Optional[PreservationContract],
) -> List[ContractAnchor]:
    """`contract` から domain=="melody" かつ axis_policy 宣言済みの anchor だけを
    宣言順に抽出する（DD-3 opt-in の判定そのもの — axis_policy の無い melody
    anchor は M4 経路を一切トリガーしない）。``contract`` が ``None``（plan が
    未到達等）なら空リスト。"""
    if contract is None:
        return []
    return [
        anchor
        for anchor in contract.anchors
        if anchor.domain == "melody" and anchor.axis_policy is not None
    ]


def _filter_melody_anchors_by_observation_scope(
    anchors: List[ContractAnchor], observation_anchor_scope: Optional[Collection[str]]
) -> List[ContractAnchor]:
    """R3-1 (Codex round3 P2 対応): experimental 経路も main 観測
    （`arrange/observe.py::observe_generated_artifact(anchor_scope=...)`）と
    同じスコープ意味論に揃える——`observation_anchor_scope`（`observation.
    anchors` 宣言由来の非空集合）が渡されたときだけ、その集合外の melody
    anchor をスキップする。空/``None``（絞り込みなし）は現行どおり全件を
    通す。

    従来は `collect_melody_experimental_anchors`/`melody_experimental_
    plan_warnings` のどちらもこのスコープを一切見ておらず、非空
    `observation.anchors` が特定の melody anchor を含まない場合でも契約の
    全 axis_policy melody anchor を評価（CREPE 抽出まで実行）し、未要求の
    experimental 行を公開してしまっていた——main は `anchor_scope` を尊重
    するのに experimental だけスコープを無視する非対称を解消する。"""
    if not observation_anchor_scope:
        return anchors
    scope = set(observation_anchor_scope)
    return [anchor for anchor in anchors if anchor.anchor_id in scope]


def melody_experimental_anchor_ids(contract: Optional[PreservationContract]) -> frozenset[str]:
    """R2-1 (Codex round2 P1・会計分離): axis_policy 付き melody anchor の
    anchor_id 集合を公開する——`_melody_anchors_with_axis_policy` と同じ
    opt-in 判定（DD-3）の結果を、呼び出し側（`cli/recast_cmd.py:
    _observe_and_report`/`resolve_main_observation_anchor_scope`）が本会計
    （`ObservationReport`/`RecastReport.anchors` の観測・coverage 集計）の
    観測スコープから除外するために使う。axis_policy の**無い** melody anchor
    は対象外（DD-3・現行の LCS・本会計挙動を完全維持）。"""
    return frozenset(anchor.anchor_id for anchor in _melody_anchors_with_axis_policy(contract))


def resolve_main_observation_anchor_scope(
    *,
    manifest_path: "str | Path",
    contract: Optional[PreservationContract],
    observation_anchor_scope: Optional[Collection[str]] = None,
) -> Optional[frozenset[str]]:
    """R2-1 (Codex round2 P1・会計分離の実装漏れ対応): axis_policy 付き melody
    anchor を本会計（``ObservationReport``/``RecastReport.anchors`` の観測・
    coverage 集計）の観測スコープから除外した ``anchor_scope`` を組み立てる
    ——`arrange/observe.py::observe_generated_artifact(anchor_scope=...)` に
    そのまま渡せる形（`arrange/observe.py` 自体は変更しない）。

    axis_policy 付き melody anchor は `recast/experimental.py:
    collect_melody_experimental_anchors` が独立に評価・報告する
    （`RecastReport.experimental_anchors`）——legacy の `_observe_melody`
    （LCS）による本会計行を同時に残すと、同じ anchor が二重に報告され、
    かつ legacy 行が coverage 分母を動かしてしまう（設計書 §4 会計分離
    違反）。本関数はそれを「main の観測スコープから該当 anchor_id を除外する」
    形で解決する——`observe_generated_artifact` は `anchor_scope` を manifest
    の生 bytes を parse する前に適用するため、除外された anchor は
    `is_melody_sensor_anchor` の判定にすら到達せず、LCS センサー自体が
    一切実行されない（無駄な basic_pitch 実行の回避）。

    axis_policy の**無い** melody anchor は対象外（DD-3・現行の LCS・本会計
    挙動を完全維持）——除外対象が無ければ ``observation_anchor_scope`` を
    そのまま返す（``None`` は「絞り込みなし」の既存契約を保つ。manifest を
    読みに行かない——既存 golden path のバイト不変を壊さない）。

    ``observation_anchor_scope``（`project.yaml` の `observation.anchors`
    宣言由来、非 `None` なら inclusion のみの既存絞り込み）が非 `None` の
    場合は単純な集合差分で足りる。``None``（絞り込みなし=全 anchor）で除外
    対象がある場合は、`arrange/observe.py` の ``anchor_scope`` が
    inclusion-only（除外を直接表現できない）契約のため、manifest の生
    anchor id 集合を読み取り、除外差分を明示的な inclusion set へ変換する。
    manifest 構造が壊れている場合はここで判定せず、`observe_generated_
    artifact` 自身の fail-closed 検証へ委ねるため素通しする
    （``observation_anchor_scope`` をそのまま返す）。
    """
    excluded = melody_experimental_anchor_ids(contract)
    if not excluded:
        return frozenset(observation_anchor_scope) if observation_anchor_scope is not None else None

    if observation_anchor_scope is not None:
        return frozenset(observation_anchor_scope) - excluded

    try:
        raw_manifest = yaml.safe_load(Path(manifest_path).read_bytes())
    except yaml.YAMLError:
        return None
    if not (isinstance(raw_manifest, dict) and isinstance(raw_manifest.get("anchors"), list)):
        return None
    all_ids = {
        entry.get("id")
        for entry in raw_manifest["anchors"]
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    return frozenset(all_ids) - excluded


def _resolve_project_relative_melody_reference(
    value: str, project_dir: Path, *, target: str, require_exists: bool = True
) -> Path:
    """`observation.melody` 配下の project 相対パス参照を解決する
    （`recast/loader.py:_resolve_sidecar_reference` の封じ込めパターン踏襲:
    絶対パス/`../` traversal/symlink 脱出を拒否し、既定では実在も検証する）。

    ``require_exists=False``（R4-4・Codex round4 P2・lenient-missing digest 用）:
    封じ込め検証（絶対パス/traversal/symlink 脱出の拒否）は常に行うが、実在
    チェック（``is_file()``）はスキップする——`compute_observation_digest` が
    「未使用の melody 参照ファイルの欠落だけで digest 計算を失敗させない」
    ための seam（`resolve_melody_observation_paths` docstring 参照）。設定
    エラー（封じ込め違反）自体は従来どおり fail-closed のまま。"""
    try:
        validate_relative_locator(value)
        resolved = resolve_confined(value, project_dir)
    except PathConfinementError as exc:
        raise RecastError(f"{target} reference {value!r} is invalid: {exc}") from exc
    if require_exists and not resolved.is_file():
        raise RecastError(f"{target} reference {value!r} does not exist at {resolved}")
    return resolved


def resolve_melody_observation_paths(
    *,
    project_dir: Path,
    melody_config: MelodyObservationConfig,
    require_exists: bool = True,
    m1_require_exists: Optional[bool] = None,
    reference_audio_require_exists: Optional[bool] = None,
) -> Tuple[Path, Path, Optional[Path]]:
    """``melody_config`` の project 相対パス群（`comparison_registry` /
    `m1_registry` / `reference_audio`）を実パスへ解決する
    （``(m3_registry_path, m1_registry_path, reference_audio_path)``、
    `reference_audio_path` は `reference == "score"` のとき常に ``None``）。
    封じ込め違反は常に ``RecastError`` で fail-closed（設定エラーは握り
    つぶさない——``require_exists`` の値に関わらず全パス共通）。

    ``require_exists``（既定 ``True``）: ``comparison_registry`` の実在検証、
    および ``m1_require_exists``/``reference_audio_require_exists`` を省略
    （``None``）した場合のそれらのフォールバック値。``True`` のときは不在も
    ``RecastError`` にする（他の recast 参照解決と同じ posture — 誤設定を
    no-op の not_observed に握りつぶさない）。``require_exists=False``
    （R4-4・Codex round4 P2）: 実在チェックをスキップし、解決済みパスを
    そのまま返す——`compute_observation_digest` の lenient-missing 方式
    専用（呼び出し側が個別ファイルの存在を確認し、無ければ決定論的
    センチネルを fold する）。

    ``m1_require_exists`` / ``reference_audio_require_exists``
    （R5-1・Codex round5 P2 対応、既定 ``None`` = ``require_exists`` を
    そのまま使う）: ``comparison_registry``（M3）とは独立に
    ``m1_registry``/``reference_audio`` の実在検証だけを緩められる seam。
    `collect_melody_experimental_anchors`/`melody_experimental_plan_
    warnings` が両方を ``False`` で呼ぶ——G1（M3 校正判定）を最優先短絡に
    するため、M3 registry パスのみ存在検証込みで先行解決し、M1/
    reference_audio は「封じ込め検証は行うが実在は問わない」パスとして
    返す。実在検証はそれぞれ本来のゲート位置（M1 = `_load_m1_registry`・
    G1 通過後、reference_audio = `evaluate_melody_experimental_anchor` の
    step 4 author-input チェック）へ移す——旧実装は本関数がここで全パスの
    実在を先行検証していたため、M3 未校正 + M1/reference_audio 欠落の組で
    G1 短絡より前に `RecastError` を送出していた（設計のゲート順序違反）。
    `recast/run_paths.py`（protected-input 集合の再構成）と
    `recast/plan.py:build_recast_plan_artifacts` の protected_inputs 再構成は
    本関数ではなく `resolve_melody_observation_paths_for_protection`（下記）を
    使う（R6-1・Codex round6 P2 対応: 従来はこの関数を既定値のまま呼んでいた
    ため、3 パスのうち 1 つでも封じ込め違反/未設定で本関数が例外を送出すると、
    残り 2 パスの保護すら得られない all-or-nothing になっていた——protected-
    input 収集の目的では存在検証は不要で、1 パスの違反が他パスの保護を
    道連れにしてはならない）。"""
    m3_path = _resolve_project_relative_melody_reference(
        melody_config.comparison_registry,
        project_dir,
        target="observation.melody.comparison_registry",
        require_exists=require_exists,
    )
    m1_path = _resolve_project_relative_melody_reference(
        melody_config.m1_registry,
        project_dir,
        target="observation.melody.m1_registry",
        require_exists=require_exists if m1_require_exists is None else m1_require_exists,
    )
    reference_audio_path: Optional[Path] = None
    if melody_config.reference_audio is not None:
        reference_audio_path = _resolve_project_relative_melody_reference(
            melody_config.reference_audio,
            project_dir,
            target="observation.melody.reference_audio",
            require_exists=(
                require_exists
                if reference_audio_require_exists is None
                else reference_audio_require_exists
            ),
        )
    return m3_path, m1_path, reference_audio_path


def resolve_melody_observation_paths_for_protection(
    *, project_dir: Path, melody_config: MelodyObservationConfig
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """R6-1 (Codex round6 P2 対応): protected-input 収集専用の resolve。

    `collect_protected_input_paths`（`recast/run_paths.py`）と
    `build_recast_plan_artifacts`（`recast/plan.py`）の protected_inputs 再構成
    が呼ぶ——両者とも publish 直前の衝突ガードとして「``observation.melody``
    の resolved パス 3 種を保護対象に含めたいだけで、実在検証もエラー報告も
    要らない」という protected-input 収集固有の要求を持つ。

    従来は `resolve_melody_observation_paths(...)` を既定値（``require_exists``
    実質 True 相当）のまま呼んでいたため、3 パスのいずれか 1 つ（典型的には
    ``reference_audio`` 未生成）が原因で ``RecastError`` を送出すると、呼び
    出し側の ``except RecastError: pass`` が **3 パス全て**の保護を諦めていた
    ——有効な M3 registry が recast 出力（report 等）と alias していても
    protected_inputs に入らず publish に上書きされ得た（Codex round6 review
    指摘: 評価自体は ``author_input_missing`` として正しく進むのに、保護だけ
    が全滅する不整合）。

    本関数は 3 パスをそれぞれ独立に「封じ込め検証のみ」
    （``require_exists=False``）で解決し、封じ込め違反（絶対パス/traversal/
    symlink 脱出等の設定ミス）が起きたパスは例外を送出せず ``None`` を返す
    ——呼び出し側は non-None のパスだけを protected_inputs へ足すことで、
    1 パスの違反が他パスの保護を道連れにしない。未存在パスを保護対象に
    含めても無害なため実在検証は行わない。実際の melody 評価・エラー報告は
    正規の位置（`evaluate_melody_experimental_anchor`/`melody_experimental_
    plan_warnings`）が行う——本関数はここで新たな失敗点を作らない。"""

    def _resolve_or_none(value: str, *, target: str) -> Optional[Path]:
        try:
            return _resolve_project_relative_melody_reference(
                value, project_dir, target=target, require_exists=False
            )
        except RecastError:
            return None

    m3_path = _resolve_or_none(
        melody_config.comparison_registry, target="observation.melody.comparison_registry"
    )
    m1_path = _resolve_or_none(melody_config.m1_registry, target="observation.melody.m1_registry")
    reference_audio_path: Optional[Path] = None
    if melody_config.reference_audio is not None:
        reference_audio_path = _resolve_or_none(
            melody_config.reference_audio, target="observation.melody.reference_audio"
        )
    return m3_path, m1_path, reference_audio_path


def collect_melody_experimental_anchors(
    *,
    contract: Optional[PreservationContract],
    melody_config: Optional[MelodyObservationConfig],
    project_dir: Path,
    backend_ref: BackendRef,
    score: Optional[CompositionScore],
    channel_artifact_bytes: Dict[str, bytes],
    take_audio_path: Optional[Path],
    take_sha256: Optional[str] = None,
    route_runner: Optional[RouteRunner] = None,
    reference_route_runner: Optional[RouteRunner] = None,
    observation_anchor_scope: Optional[Collection[str]] = None,
) -> List[ExperimentalAnchorEntry]:
    """`RecastReport.experimental_anchors` を組み立てる M4c の ingest/observe
    経路 orchestration（Design Memo M4 §5）。axis_policy 付き melody anchor が
    契約に無ければ即座に空リスト（``main`` の anchors/coverage には一切
    触れない・呼び出し側が別途 `build_recast_report` を通常どおり呼ぶ）。

    ``route_runner`` / ``reference_route_runner``（DD-5 の抽出器注入 seam）は
    本関数のパラメータとしてのみ露出する——CLI からは非公開（呼び出し側の
    `recast_cmd.py` はこれを渡さず、常に実抽出器を使う）。

    ``take_sha256``（R2-3・Codex round2 P2）: 呼び出し側が別時点で既に確定
    させた ``take_audio_path`` の sha256（`recast ingest` では
    `GeneratedTake.sha256`）——`evaluate_melody_experimental_anchor` の
    ``expected_take_sha256`` へそのまま転送し、抽出直前の凍結コピー検証
    （TOCTOU 封鎖）の pin として使う。省略時（``None``）は突合を行わない
    （凍結コピー自体は行う）。

    ``observation_anchor_scope``（R3-1・Codex round3 P2 対応）: `project.yaml`
    の `observation.anchors` 宣言由来の非空集合（呼び出し側 `recast_cmd.py:
    _observe_and_report` の `anchor_scope`、main の `observe_generated_
    artifact(anchor_scope=...)` と同一 single source）。非空のとき、この
    集合外の melody anchor は評価せず（CREPE 抽出も一切実行しない）、
    experimental 節から丸ごと省略する——main が `anchor_scope` を尊重する
    のに experimental だけが未要求の行を評価・公開してしまう非対称を防ぐ
    （`_filter_melody_anchors_by_observation_scope` 参照）。空/``None``
    （既定・絞り込みなし）は現行どおり契約の全 axis_policy melody anchor を
    評価する——既存 project の挙動はバイト不変。

    anchor の順序は ``contract.anchors``（= manifest anchor 宣言順）をそのまま
    保つ（決定論契約）。
    """
    melody_anchors = _filter_melody_anchors_by_observation_scope(
        _melody_anchors_with_axis_policy(contract), observation_anchor_scope
    )
    if not melody_anchors:
        return []

    m3_registry_path: Optional[Path] = None
    m1_registry_path: Optional[Path] = None
    reference_audio_path: Optional[Path] = None
    if melody_config is not None:
        # R5-1 (Codex round5 P2 対応): M3 registry パスのみ存在検証込みで
        # 先行解決し、M1/reference_audio は require_exists=False で解決する
        # （封じ込め検証は維持）——これにより M3 未校正 + M1/reference_audio
        # 欠落の組でも G1 短絡（`evaluate_melody_experimental_anchor` step 2）
        # が先に評価され、G1 通過後の各ゲート位置（M1 = step 6 の
        # `_load_m1_registry`、reference_audio = step 4 の author-input
        # チェック）でのみ実在検証される（`resolve_melody_observation_paths`
        # docstring 参照）。
        m3_registry_path, m1_registry_path, reference_audio_path = resolve_melody_observation_paths(
            project_dir=project_dir,
            melody_config=melody_config,
            m1_require_exists=False,
            reference_audio_require_exists=False,
        )

    entries: List[ExperimentalAnchorEntry] = []
    for anchor in melody_anchors:
        if melody_config is None:
            entries.append(
                _not_observed_entry(anchor=anchor, reason="melody_config_missing", provenance={})
            )
            continue
        assert m3_registry_path is not None and m1_registry_path is not None  # set above
        assert score is not None  # compiled/verified plan always resolves a derived score
        entries.append(
            evaluate_melody_experimental_anchor(
                anchor=anchor,
                melody_config=melody_config,
                score=score,
                melody_artifact_bytes=channel_artifact_bytes.get(anchor.anchor_id),
                melody_artifact_sha256=anchor.artifact_sha256,
                melody_take_band=backend_ref.melody_take_band,
                take_audio_path=str(take_audio_path) if take_audio_path is not None else None,
                expected_take_sha256=take_sha256,
                m3_registry_path=m3_registry_path,
                m1_registry_path=m1_registry_path,
                reference_audio_path=(
                    str(reference_audio_path) if reference_audio_path is not None else None
                ),
                reference_melody_band=melody_config.reference_band,
                route_runner=route_runner,
                reference_route_runner=reference_route_runner,
            )
        )
    return entries


def melody_experimental_plan_warnings(
    *,
    contract: Optional[PreservationContract],
    melody_config: Optional[MelodyObservationConfig],
    project_dir: Path,
    backend_ref: BackendRef,
    observation_anchor_scope: Optional[Collection[str]] = None,
) -> List[str]:
    """`recast plan` の warnings へ積む「observability 見込み」1 行を anchor
    ごとに組み立てる（Design Memo M4 §5）。抽出は行わない——plan 時点で
    機械判定できる条件（config 不在 / G1 校正 / G2 帯域 / axis_policy と
    frozen axes の突合——R5-3）のみを診断する。``reference == "audio"`` の
    場合は原曲側の G2（``reference_band``）も診断に含める
    （R2-6・Codex round2 P2）——テイク側が校正済みでも原曲側が未校正なら
    実行時 ``not_observed(band_out_of_validation)`` に落ちるため、plan 時点
    でも同じ主因を先出しする。

    axis_policy 付き melody anchor が契約に無ければ空リスト（既存 project は
    バイト不変のまま — `RecastPlan` スキーマは変更しない、既存 `warnings`
    リストへ足すだけ）。

    ``observation_anchor_scope``（R3-1・Codex round3 P2 対応）: `collect_
    melody_experimental_anchors` と同じスコープ規則（`_filter_melody_
    anchors_by_observation_scope`）を適用する——診断パリティ維持のため、
    非空スコープ外の melody anchor は plan の warnings にも出さない（実行時
    に experimental 節から省略される anchor について、plan 時点で無関係な
    「observability 見込み」行を出さない）。空/``None``（既定）は現行どおり
    全 axis_policy melody anchor を診断する。

    R5-1 (Codex round5 P2 対応): `resolve_melody_observation_paths` は
    ``collect_melody_experimental_anchors`` と同じく M1/reference_audio を
    ``require_exists=False`` で解決する——本関数はそもそも M1/
    reference_audio ファイルの中身を読まない（G1/G2 診断は m3 registry と
    config 宣言だけで完結する）ため、これらの欠落だけで plan 診断自体が
    ``RecastError`` になるのを避ける（実行時と同じ「G1 短絡を最優先」の
    ゲート順序に診断も揃える）。

    R5-3 (Codex round5 P2 対応): frozen registry が部分軸（例 ``contour``
    のみ）しか校正していない場合、G1（全体の校正状態）自体は "frozen" で
    通過しても、anchor 個別の axis_policy が frozen axes 集合外の軸を
    指定していれば、実行時 `evaluate_melody_experimental_anchor` の
    axis_policy 検証（fail-closed・``RecastError``）で初めて判明していた
    ——plan 時点でも anchor ごとに frozen axes との集合差分を診断し、
    ``not expected (axis_policy_uncalibrated_axes: [...])`` として先出しする
    （plan は診断層のため raise しない——ingest の fail-closed はそのまま
    維持する）。

    R6-2 (Codex round6 P2 対応・runtime とのパリティ): 突合対象は
    axis_policy の**全キー集合**（hard/elastic/free 問わず）とする——
    実行時 `evaluate_melody_experimental_anchor` の axis_policy 検証は
    ``set(axis_policy) - frozen_axes``（全軸）を見て ``RecastError`` にする
    のに対し、R5-3 導入時の plan 診断は hard/elastic のみを突合していた
    （free 軸は判定不参加という `map_axis_policy_to_adherence` の意味論を
    誤って診断側の集合差分にも適用してしまっていた）。この不一致により
    ``contour: hard, rhythm: free`` のような契約で ``rhythm`` が frozen axes
    集合外だと、plan は contour のみ突合して ``ok`` を返す一方、実行時は
    axis_policy 全体を突合して ``RecastError`` になり、plan ok のまま
    ingest が落ちる非対称が生じていた。plan の突合を axis_policy の全キー
    集合に揃えることで、plan の診断結果が実行時の fail-closed 判定と常に
    一致するようにする（runtime 側 `evaluate_melody_experimental_anchor` は
    変更しない）。
    """
    melody_anchors = _filter_melody_anchors_by_observation_scope(
        _melody_anchors_with_axis_policy(contract), observation_anchor_scope
    )
    if not melody_anchors:
        return []

    if melody_config is None:
        return [
            f"melody anchor '{anchor.anchor_id}': experimental observability — "
            "not expected (melody_config_missing)"
            for anchor in melody_anchors
        ]

    m3_registry_path, _m1_registry_path, _reference_audio_path = resolve_melody_observation_paths(
        project_dir=project_dir,
        melody_config=melody_config,
        m1_require_exists=False,
        reference_audio_require_exists=False,
    )
    m3_config, _sha256, g1_reason = _load_m3_registry_with_gate(m3_registry_path)
    if g1_reason is not None:
        return [
            f"melody anchor '{anchor.anchor_id}': experimental observability — "
            "not expected (comparator_uncalibrated)"
            for anchor in melody_anchors
        ]

    band_reason = _check_melody_take_band(backend_ref.melody_take_band)
    # R2-6: audio_reference は原曲側 G2 も先出しする（テイク側が
    # 先に不成立なら主因はテイク側のまま — band_reason を優先評価）。
    reference_band_reason = (
        _check_melody_take_band(melody_config.reference_band)
        if melody_config.reference == "audio"
        else None
    )
    if band_reason is not None or reference_band_reason is not None:
        return [
            f"melody anchor '{anchor.anchor_id}': experimental observability — "
            "not expected (band_out_of_validation)"
            for anchor in melody_anchors
        ]

    # R5-3/R6-2: G1（全体の校正状態）通過後も、anchor ごとの axis_policy が
    # frozen axes 集合に含まれない軸を指定していないか個別に突合する。
    # R6-2 (Codex round6 P2 対応): 突合対象は axis_policy の全キー集合
    # （hard/elastic/free 問わず）——runtime `evaluate_melody_experimental_
    # anchor` の axis_policy 検証（``set(axis_policy) - frozen_axes``）と
    # 揃える（`melody_experimental_plan_warnings` docstring の R6-2 節参照。
    # free 軸は判定（`map_axis_policy_to_adherence`）には不参加のままだが、
    # 「frozen registry で校正されているか」という plan の事前診断は全軸を
    # 対象にしないと runtime の fail-closed と食い違う）。
    assert m3_config is not None  # g1_reason is None means config resolved
    frozen_axes = set((m3_config.evidence_thresholds.axes or {}).keys())
    warnings: List[str] = []
    for anchor in melody_anchors:
        assert anchor.axis_policy is not None  # opt-in filter guarantees this
        judged_axes = set(anchor.axis_policy)
        uncalibrated_axes = sorted(judged_axes - frozen_axes)
        if uncalibrated_axes:
            diagnosis = f"not expected (axis_policy_uncalibrated_axes: {uncalibrated_axes})"
        else:
            diagnosis = "ok"
        warnings.append(
            f"melody anchor '{anchor.anchor_id}': experimental observability — {diagnosis}"
        )
    return warnings
