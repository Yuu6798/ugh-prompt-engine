"""score_axis_transform.py — RUN9 HARNESS-3c: repo score 変換器（AX-P1/AX-D1）。

設計根拠: 【RUN9 User裁定 — Learning Recipe 残5キー】§1
（`USER_ADJUDICATION_20260827_LEARNING_RECIPE_5KEYS.txt`）— 「RUN9 v1の
探索空間を、Compositionを変更しないscore変換層の次の3軸系へ限定する:
note単位の音高偏差 / phrase内の拍・音価配分 / phrase境界制御。note数・
順序、lyrics/phoneme列、Composition本体を変更してはならない」。

実測正本: `HARNESS3C_AXIS_FEASIBILITY_RECORD.md`（W1: score 変換プロト
タイプ + 11 variant render 実測。W1b: 未検証格子点 render + 変換器レベル
unit 検証17ケース）。本モジュールは workdir プロトタイプ
`scratchpad/harness_work/h3c/score_transform_probe.py`（`validate_ax_p1_
offset()`/`validate_ax_d1_delta_vector()`）の検証済みロジックを repo 品質
へ整備したものであり、変換式・制約自体は workdir 実測から変更していない。

**catalog 消費**: 本モジュールの制約値（range/quantization/min-duration
等）はハードコードせず、呼び出し側が `run9_schema.
load_pinned_score_axis_catalog_manifest()` で取得した凍結済み
`score_axis_catalog_v1.json` を都度渡す設計とする——catalog が改訂されて
凍結し直された場合、本モジュールを変更せずに新しい値が反映される。

**sealed/PJS 非接触**: 本変換器は `ScoreNote` 相当の note spec（kana /
midi / duration_beats / phrase_index / is_phrase_final の平坦表現）のみを
入力・出力とし、sealed_holdout・PJS raw audio・Founder 学習結果・
gate_synth 実行系のいずれにも触れない。render を伴う実証は
`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` に record 済みであり、本モジュール
自体は render を実行しない（純粋な score データ変換 + 検証）。

**族 c（phrase_boundary_control）は本モジュールに実装しない**:
`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 第1部 Step1/Step4 が実測で確認した
とおり、`gate_synth.py::run_pipeline` が内部で構築する `_NoteWithMs` は
`midi`/`mora`/`_dur_ms` の3値のみを保持し `phrase_index`/`is_phrase_final`
をコピーしない（`grep -c 'phrase_index\\|is_phrase_final'
voice_genesis/foundry/s1_gate/gate_synth.py` = 0、静的解析で確認済み。
`voice_genesis/evolution/probes/vgl0_control_axis_probe.py:40-45` の既存
申し送りと整合）。実測でも `phrase_c1`/`phrase_c2` variant は baseline と
byte-for-byte 同一の wav sha256 となり、現行配線では出力へ無効であることを
直接確認した。`score_axis_catalog_v1.json` はこの軸を
`status: "NOT_EXPRESSIBLE_ON_CURRENT_WIRING"`、`axes: []` として宣言する
のみであり、族 c を実効な変換として表現するコードパスは本モジュールに
一切存在しない（配線追加 = 新規注入点の導入は新 design revision を要する
——裁定 §1 逐語）。
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Sequence

# note spec が変換器から見て触れてよいフィールド（族 c を実装しないため
# phrase_index/is_phrase_final はここに含まれない——kana も含まれない、
# lyrics/phoneme 列は本変換器のいかなる経路でも変更されない）。
MUTABLE_NOTE_FIELDS = frozenset({"midi", "duration_beats"})

# note spec の必須フィールド（Composition 不変条件の検証対象。
# phrase_index/is_phrase_final は ScoreNote 型と同型を保つため spec には
# 残すが、本変換器はこれらの値を一切変更しない）。
NOTE_SPEC_REQUIRED_FIELDS = frozenset(
    {"kana", "midi", "duration_beats", "phrase_index", "is_phrase_final"}
)


class ScoreAxisTransformError(ValueError):
    """score 変換器のフォールバック不可能な検査失敗を表す基底例外。"""


class CompositionInvariantViolation(ScoreAxisTransformError):
    """Composition 不変条件（note 数・順序・kana 列・許可フィールド外への
    書込）の違反。"""


class CatalogRejected(ScoreAxisTransformError):
    """catalog 記載の range/quantization/min-duration/NaN・inf 制約への
    違反。"""


def _is_on_grid(value: float, step: float, *, tol: float = 1e-9) -> bool:
    """`value` が `step` の整数倍格子上にあるかを判定する。"""
    ratio = value / step
    return abs(ratio - round(ratio)) <= tol


def _require_finite_number(value: Any, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CatalogRejected(f"{label} must be numeric, got {type(value).__name__}")
    fvalue = float(value)
    if math.isnan(fvalue) or math.isinf(fvalue):
        raise CatalogRejected(f"{label} must not be NaN/inf, got {fvalue}")
    return fvalue


# ---------------------------------------------------------------------------
# Composition 不変条件の機械検証（AX-P1/AX-D1 共通、render 前に必ず通す）
# ---------------------------------------------------------------------------


def verify_composition_invariants(
    baseline_specs: Sequence[Mapping[str, Any]],
    variant_specs: Sequence[Mapping[str, Any]],
    *,
    context: str = "score_axis_transform",
) -> None:
    """`baseline_specs` に対する `variant_specs` の Composition 不変条件を
    fail-closed で検証する（W1 `verify_invariants()` の repo 品質版・同型
    ロジック）。

    検証する不変条件（裁定 §1「note数・順序、lyrics/phoneme列、
    Composition本体を変更してはならない」の機械化）:

    1. note 数が baseline と variant で一致する。
    2. 各 note の `kana`（= mora.kana、lyrics/phoneme 列そのもの）の並びが
       baseline と variant で完全一致する（順序不変も同時に検証される —
       リスト比較は要素の位置も見るため）。
    3. `variant_specs[i]` が baseline から変更してよいフィールドは
       `MUTABLE_NOTE_FIELDS`（`midi`/`duration_beats`）のみであり、
       `phrase_index`/`is_phrase_final` を含むそれ以外のフィールドは
       baseline と厳密一致していなければならない（族 c 非実装の直接的な
       機械強制——このモジュール経由では phrase 境界を一切動かせない）。

    いずれかが崩れたら `CompositionInvariantViolation` を送出する
    （fail-closed）。
    """
    if len(variant_specs) != len(baseline_specs):
        raise CompositionInvariantViolation(
            f"{context}: note数不一致 variant={len(variant_specs)} != "
            f"baseline={len(baseline_specs)}"
        )
    for i, (base, var) in enumerate(zip(baseline_specs, variant_specs)):
        missing_base = NOTE_SPEC_REQUIRED_FIELDS - set(base.keys())
        missing_var = NOTE_SPEC_REQUIRED_FIELDS - set(var.keys())
        if missing_base or missing_var:
            raise CompositionInvariantViolation(
                f"{context}: note[{i}] は必須フィールド {sorted(NOTE_SPEC_REQUIRED_FIELDS)} を"
                f"全て持たなければならない (baseline missing={sorted(missing_base)}, "
                f"variant missing={sorted(missing_var)})"
            )
        if base["kana"] != var["kana"]:
            raise CompositionInvariantViolation(
                f"{context}: note[{i}] の kana（lyrics/phoneme列）が baseline={base['kana']!r} "
                f"から variant={var['kana']!r} へ変更されている — Composition 本体は不変"
            )
        for field in NOTE_SPEC_REQUIRED_FIELDS - MUTABLE_NOTE_FIELDS - {"kana"}:
            if base[field] != var[field]:
                raise CompositionInvariantViolation(
                    f"{context}: note[{i}].{field} は変換対象外のフィールドだが "
                    f"baseline={base[field]!r} から variant={var[field]!r} へ変更されている "
                    "（族 c phrase_boundary_control は本変換器に実装されていない — "
                    "HARNESS3C_AXIS_FEASIBILITY_RECORD.md 参照）"
                )


# ---------------------------------------------------------------------------
# AX-P1 note_pitch_offset_semitones
# ---------------------------------------------------------------------------


def _ax_p1_constraints(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    return catalog["axes"]["AX-P1"]


def validate_ax_p1_offset(offset: Any, *, catalog: Mapping[str, Any]) -> float:
    """AX-P1 `note_pitch_offset_semitones` の catalog 制約を検査する。

    catalog 記載の制約（`score_axis_catalog_v1.json` `axes.AX-P1` から
    都度読む — ハードコードしない）: NaN/inf 禁止・range・quantization。
    違反時は `CatalogRejected` を送出する（fail-closed）。妥当なら
    `float(offset)` を返す。
    """
    fvalue = _require_finite_number(offset, label="AX-P1 offset")
    constraints = _ax_p1_constraints(catalog)
    lo, hi = constraints["range_semitones"]
    if not (lo <= fvalue <= hi):
        raise CatalogRejected(f"AX-P1 offset {fvalue} out of range [{lo}, {hi}]")
    step = constraints["quantization_step_semitones"]
    if not _is_on_grid(fvalue, step):
        raise CatalogRejected(f"AX-P1 offset {fvalue} not on {step}-semitone grid")
    return fvalue


def apply_ax_p1(
    note_specs: Sequence[Mapping[str, Any]],
    *,
    note_index: int,
    offset_semitones: Any,
    catalog: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """`note_specs[note_index].midi` へ AX-P1 の `midi' = midi + offset`
    を適用した variant note spec list を返す（deep copy、入力は変更しない）。

    `verify_composition_invariants()` を内部で通してから返す
    （fail-closed — 呼び出し側が個別に呼ぶ手間を要求しない）。
    """
    offset = validate_ax_p1_offset(offset_semitones, catalog=catalog)
    variant = copy.deepcopy(list(note_specs))
    if not (0 <= note_index < len(variant)):
        raise ScoreAxisTransformError(
            f"AX-P1: note_index {note_index} out of range [0, {len(variant)})"
        )
    original_midi = _require_finite_number(variant[note_index]["midi"], label="original midi")
    variant[note_index]["midi"] = original_midi + offset
    verify_composition_invariants(note_specs, variant, context="apply_ax_p1")
    return variant


# ---------------------------------------------------------------------------
# AX-D1 phrase_duration_redistribution_beats
# ---------------------------------------------------------------------------


def _ax_d1_constraints(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    return catalog["axes"]["AX-D1"]


def validate_ax_d1_delta_vector(
    original_durations_beats: Sequence[Any],
    deltas_beats: Sequence[Any],
    *,
    catalog: Mapping[str, Any],
) -> List[float]:
    """AX-D1 `phrase_duration_redistribution_beats` の catalog 制約を
    検査する。

    catalog 記載の制約（`axes.AX-D1` から都度読む）: quantization（各
    delta が刻み格子上）・合計 0（phrase内の beat 総量保存）・変換後の各
    note duration が catalog `min_duration_beats` 以上。違反時は
    `CatalogRejected` を送出する（fail-closed）。妥当なら
    `[float(d) for d in deltas_beats]` を返す。
    """
    if len(original_durations_beats) != len(deltas_beats):
        raise ScoreAxisTransformError(
            "AX-D1: original_durations_beats と deltas_beats の長さが一致しない "
            f"({len(original_durations_beats)} != {len(deltas_beats)})"
        )
    constraints = _ax_d1_constraints(catalog)
    step = constraints["quantization_step_beats"]
    min_duration = constraints["min_duration_beats"]
    deltas: List[float] = []
    for d in deltas_beats:
        fdelta = _require_finite_number(d, label="AX-D1 delta")
        if not _is_on_grid(fdelta, step):
            raise CatalogRejected(f"AX-D1 delta {fdelta} not on {step}-beat grid")
        deltas.append(fdelta)
    total = sum(deltas)
    if abs(total) > 1e-9:
        raise CatalogRejected(f"AX-D1 delta vector sum must be 0, got {total}")
    for orig, delta in zip(original_durations_beats, deltas):
        forig = _require_finite_number(orig, label="AX-D1 original duration")
        post = forig + delta
        if post < min_duration - 1e-9:
            raise CatalogRejected(
                f"AX-D1 post-transform duration {post} < min {min_duration} beat "
                f"(orig={forig}, delta={delta})"
            )
    return deltas


def apply_ax_d1(
    note_specs: Sequence[Mapping[str, Any]],
    *,
    note_indices: Sequence[int],
    deltas_beats: Sequence[Any],
    catalog: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """`note_specs[note_indices[i]].duration_beats` へ AX-D1 の per-note
    delta（合計 0）を適用した variant note spec list を返す（deep copy、
    入力は変更しない）。

    `verify_composition_invariants()` を内部で通してから返す。
    """
    if len(note_indices) != len(deltas_beats):
        raise ScoreAxisTransformError(
            "AX-D1: note_indices と deltas_beats の長さが一致しない "
            f"({len(note_indices)} != {len(deltas_beats)})"
        )
    variant = copy.deepcopy(list(note_specs))
    original_durations = []
    for idx in note_indices:
        if not (0 <= idx < len(variant)):
            raise ScoreAxisTransformError(
                f"AX-D1: note_index {idx} out of range [0, {len(variant)})"
            )
        original_durations.append(variant[idx]["duration_beats"])
    validated_deltas = validate_ax_d1_delta_vector(
        original_durations, deltas_beats, catalog=catalog
    )
    for idx, orig, delta in zip(note_indices, original_durations, validated_deltas):
        variant[idx]["duration_beats"] = float(orig) + delta
    verify_composition_invariants(note_specs, variant, context="apply_ax_d1")
    return variant
