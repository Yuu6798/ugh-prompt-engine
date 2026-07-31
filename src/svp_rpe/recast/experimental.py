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
2. registry ロード（sha256 取得）。G1: ``evidence_thresholds.status != "frozen"``
   または axes 空 → ``not_observed(comparator_uncalibrated)``
3. axis_policy 検証: frozen axes に無い軸の指定 → ``RecastError``（fail-closed・
   実行停止。エラーにするのはここだけ — G1〜G3 は「測れない」を正直に
   not_observed へ落とす）
4. 参照側入力: score_reference で artifact 不在 / bpm TODO →
   ``not_observed(author_input_missing)``。audio_reference で
   reference_audio 不在も同上。
5. G2: 帯域宣言が校正済み集合（``{"clear_lead"}``）外 →
   ``not_observed(band_out_of_validation(declared=...))``（audio_reference は
   両側に課す）
6. 抽出（テイク側。audio_reference は両側）→ ``compare_melodies(...)`` 実行
7. G3: ``report.evidence == "not_comparable"`` → ``not_observed``
   （``report.reasons`` を転記）
8. 写像規則適用（本モジュール `map_axis_policy_to_adherence`）

ゲート不成立の短絡時も、判明している provenance（registry sha256 等）は entry
へ載せる（``_not_observed_entry`` が積み上げ済みの ``provenance`` dict をそのまま
使う）。

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
from pydantic import Field

from svp_rpe.arrange.contract import ContractAnchor, PreservationContract
from svp_rpe.arrange.identity import AnchorDomain
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


class ExperimentalAnchorEntry(RecastReportModel):
    """1 melody（将来他ドメインも）experimental anchor 分の翻訳結果（Design Memo M4 §4）。

    本会計（`RecastReport.coverage` の verified/violated/not_observed 分母）には
    一切算入しない——`RecastReport` への統合（`experimental_anchors` 節への
    追加・会計分離の徹底）は M4c（本フェーズの担当外）で行う。
    """

    adherence_status: ExperimentalAdherenceStatus
    anchor_id: str
    domain: AnchorDomain
    axis_policy: Dict[str, str]
    axes: Dict[str, Optional[float]]
    axis_evidence: Dict[str, str]
    coverage: Dict[str, float]
    octave_artifact_suspected: bool
    reasons: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)


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
    """
    reasons: List[str] = list(report.reasons)
    hard_axes = [axis for axis, mode in axis_policy.items() if mode == "hard"]
    elastic_axes = [axis for axis, mode in axis_policy.items() if mode == "elastic"]

    # R2-5 (Codex round2 P2): uncalibrated 前置チェックは判定参加軸
    # （hard/elastic）のみを対象にする——free 軸は設計書 §3 により判定に
    # 不参加のため、free 軸の evidence 欠落だけで not_observed に落としては
    # ならない（free 軸は呼び出し側が axes/axis_evidence へ転記するだけで、
    # 欠落していればそのまま欠落を報告する——捏造しない）。
    for axis in sorted(hard_axes + elastic_axes):
        verdict = report.axis_evidence.get(axis, "uncalibrated")
        if verdict == "uncalibrated":
            reasons.append(f"comparator_uncalibrated(axis={axis})")
            return "not_observed", reasons

    if any(report.axis_evidence[axis] == "none" for axis in hard_axes):
        return "changed_outside_policy", reasons
    if any(report.axis_evidence[axis] == "weak" for axis in hard_axes):
        reasons.append("insufficient_evidence")
        return "not_observed", reasons
    if any(report.axis_evidence[axis] in ("none", "weak") for axis in elastic_axes):
        return "changed_within_policy", reasons
    return "preserved", reasons


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


def _load_m1_registry(path: "str | Path") -> Tuple[ObservabilityThresholds, str]:
    """M1 `registry.yaml` の ``observation_gate`` 節をロードする（sha256 込み・
    single read）。`tests/test_melody_comparison.py` の loader パターンと同型。

    R2-2 (Codex round2 P2): 構造検証を fail-closed で行う——空/スカラー YAML
    や `observation_gate` 節欠落を素通しすると、直後の dict 添字/`from_registry`
    の `set(mapping)` が `TypeError`/`KeyError` を未捕捉のまま送出し、CLI が
    traceback 付きで落ちる。ここで actionable な `RecastError` に変換して
    既存の捕捉経路（呼び出し側の `except (..., RecastError, ...)`）に乗せる。
    """
    data = Path(path).read_bytes()
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
    m3_config, m3_sha256, g1_reason = _load_m3_registry_with_gate(m3_registry_path)
    provenance["m3_registry_sha256"] = m3_sha256
    m1_thresholds, m1_sha256 = _load_m1_registry(m1_registry_path)
    provenance["m1_registry_sha256"] = m1_sha256
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
        if reference_audio_path is None:
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
    value: str, project_dir: Path, *, target: str
) -> Path:
    """`observation.melody` 配下の project 相対パス参照を解決する
    （`recast/loader.py:_resolve_sidecar_reference` の封じ込めパターン踏襲:
    絶対パス/`../` traversal/symlink 脱出を拒否し、実在も検証する）。"""
    try:
        validate_relative_locator(value)
        resolved = resolve_confined(value, project_dir)
    except PathConfinementError as exc:
        raise RecastError(f"{target} reference {value!r} is invalid: {exc}") from exc
    if not resolved.is_file():
        raise RecastError(f"{target} reference {value!r} does not exist at {resolved}")
    return resolved


def resolve_melody_observation_paths(
    *, project_dir: Path, melody_config: MelodyObservationConfig
) -> Tuple[Path, Path, Optional[Path]]:
    """``melody_config`` の project 相対パス群（`comparison_registry` /
    `m1_registry` / `reference_audio`）を実パスへ解決する
    （``(m3_registry_path, m1_registry_path, reference_audio_path)``、
    `reference_audio_path` は `reference == "score"` のとき常に ``None``）。
    不在/封じ込め違反は ``RecastError`` で fail-closed（他の recast 参照解決と
    同じ posture — 誤設定を no-op の not_observed に握りつぶさない）。"""
    m3_path = _resolve_project_relative_melody_reference(
        melody_config.comparison_registry, project_dir, target="observation.melody.comparison_registry"
    )
    m1_path = _resolve_project_relative_melody_reference(
        melody_config.m1_registry, project_dir, target="observation.melody.m1_registry"
    )
    reference_audio_path: Optional[Path] = None
    if melody_config.reference_audio is not None:
        reference_audio_path = _resolve_project_relative_melody_reference(
            melody_config.reference_audio, project_dir, target="observation.melody.reference_audio"
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
        m3_registry_path, m1_registry_path, reference_audio_path = resolve_melody_observation_paths(
            project_dir=project_dir, melody_config=melody_config
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
    機械判定できる 3 条件（config 不在 / G1 校正 / G2 帯域）のみを診断する。
    ``reference == "audio"`` の場合は原曲側の G2（``reference_band``）も
    診断に含める（R2-6・Codex round2 P2）——テイク側が校正済みでも原曲側が
    未校正なら実行時 ``not_observed(band_out_of_validation)`` に落ちるため、
    plan 時点でも同じ主因を先出しする。

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
    """
    melody_anchors = _filter_melody_anchors_by_observation_scope(
        _melody_anchors_with_axis_policy(contract), observation_anchor_scope
    )
    if not melody_anchors:
        return []

    diagnosis: Optional[str] = None
    if melody_config is not None:
        m3_registry_path, _m1_registry_path, _reference_audio_path = resolve_melody_observation_paths(
            project_dir=project_dir, melody_config=melody_config
        )
        _config, _sha256, g1_reason = _load_m3_registry_with_gate(m3_registry_path)
        if g1_reason is not None:
            diagnosis = "not expected (comparator_uncalibrated)"
        else:
            band_reason = _check_melody_take_band(backend_ref.melody_take_band)
            # R2-6: audio_reference は原曲側 G2 も先出しする（テイク側が
            # 先に不成立なら主因はテイク側のまま — band_reason を優先評価）。
            reference_band_reason = (
                _check_melody_take_band(melody_config.reference_band)
                if melody_config.reference == "audio"
                else None
            )
            if band_reason is not None or reference_band_reason is not None:
                diagnosis = "not expected (band_out_of_validation)"
            else:
                diagnosis = "ok"
    else:
        diagnosis = "not expected (melody_config_missing)"

    return [
        f"melody anchor '{anchor.anchor_id}': experimental observability — {diagnosis}"
        for anchor in melody_anchors
    ]
