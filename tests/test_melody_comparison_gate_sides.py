"""tests/test_melody_comparison_gate_sides.py — R1-4 (Codex round1 P2) 対応の
`melody/comparison.py::compare_melodies` additive `observability_gate_sides`
引数のテスト。

`tests/test_melody_comparison.py` は凍結境界のため無改変——本ファイルは新規
小ファイルとして分離する（PR #237 round1 実装契約）。

厳守事項の確認軸:
1. 既定値（引数省略）は旧来の両側ゲート挙動と完全一致する（挙動保存）。
2. ``observability_gate_sides=("b",)`` は参照側（"a"）の M1 観測ゲート
   insufficient を無視し、テイク側（"b"）へは引き続き課す（層分離）。
3. M3 被覆下限（``config.coverage.floor``）は本引数の影響を受けず、常に
   両側で評価される（sided 化しない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import yaml

from svp_rpe.melody.comparison import compare_melodies
from svp_rpe.melody.observability import MelodyNote, MelodyObservation, ObservabilityThresholds
from svp_rpe.melody.representation import M3ComparisonConfig, load_m3_registry

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
M1_REGISTRY_PATH = BENCH_DIR / "registry.yaml"
M3_REGISTRY_PATH = BENCH_DIR / "m3_comparison_registry.yaml"


def _default_config() -> M3ComparisonConfig:
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def _default_thresholds() -> ObservabilityThresholds:
    mapping = yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))
    return ObservabilityThresholds.from_registry(mapping["observation_gate"])


def _note(pitch_midi: float, start_sec: float, end_sec: float, confidence: float = 0.9) -> MelodyNote:
    return MelodyNote(
        start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=confidence
    )


def _observation_from_notes(notes: List[MelodyNote], route: str = "test_route") -> MelodyObservation:
    return MelodyObservation(route=route, source_model="test:fake", notes=tuple(notes))


# registry.yaml: min_note_count=8 / min_phrase_count=2 を満たす 2 フレーズ・
# 10 ノート（`tests/test_melody_comparison.py::_good_notes` と同型パターン）。
def _good_notes() -> List[MelodyNote]:
    phrase1_pitches = [60, 62, 64, 65, 67]
    phrase2_pitches = [69, 67, 65, 64, 62]
    notes: List[MelodyNote] = []
    t = 0.0
    for p in phrase1_pitches:
        notes.append(_note(p, t, t + 0.25))
        t += 0.3
    t += 1.0  # フレーズ境界（phrase_gap_sec=0.6 を超えるギャップ）
    for p in phrase2_pitches:
        notes.append(_note(p, t, t + 0.25))
        t += 0.3
    return notes


def _sparse_notes(count: int) -> List[MelodyNote]:
    """観測ゲートを通さない（note_count 不足の）旋律。"""
    notes: List[MelodyNote] = []
    t = 0.0
    for i in range(count):
        notes.append(_note(60 + i, t, t + 0.05))
        t += 0.05
    return notes


# --------------------------------------------------------------------------- #
# 1) 既定値 = 挙動保存
# --------------------------------------------------------------------------- #
def test_default_gate_sides_matches_omitted_argument() -> None:
    """引数省略と明示的な既定値 ``("a", "b")`` は完全に同じ結果を返す
    （additive seam の既定値保存の直接証明）。"""
    config = _default_config()
    thresholds = _default_thresholds()
    good = _observation_from_notes(_good_notes(), route="good")

    report_omitted = compare_melodies(
        good, good, observability_thresholds=thresholds, config=config
    )
    report_explicit = compare_melodies(
        good,
        good,
        observability_thresholds=thresholds,
        config=config,
        observability_gate_sides=("a", "b"),
    )

    assert report_omitted.to_dict() == report_explicit.to_dict()


def test_default_gate_sides_still_gates_both_sides() -> None:
    """既定値は旧来どおり両側を M1 ゲートへ課す（回帰確認 — `tests/
    test_melody_comparison.py::test_compare_melodies_rejects_when_one_side_insufficient`
    と同型の対照）。"""
    config = _default_config()
    thresholds = _default_thresholds()
    good = _observation_from_notes(_good_notes(), route="good")
    insufficient = _observation_from_notes(_sparse_notes(3), route="sparse")

    report = compare_melodies(
        insufficient, good, observability_thresholds=thresholds, config=config
    )

    assert report.evidence == "not_comparable"
    assert any(r.startswith("observation_gate_insufficient_a:") for r in report.reasons)


# --------------------------------------------------------------------------- #
# 2) 側指定: 参照側 ("a") のみ skip
# --------------------------------------------------------------------------- #
def test_gate_sides_b_only_ignores_side_a_insufficiency_but_still_gates_side_b() -> None:
    """``observability_gate_sides=("b",)`` は側 "a" の insufficient を無視する
    （score_reference の免除そのもの）が、側 "b" の insufficient は引き続き
    `not_comparable` へ落とす（sided gating の非対称性を両方向とも確認）。"""
    config = _default_config()
    thresholds = _default_thresholds()
    insufficient_a = _observation_from_notes(_sparse_notes(3), route="ref")
    insufficient_b = _observation_from_notes(_sparse_notes(3), route="take")

    report = compare_melodies(
        insufficient_a,
        insufficient_b,
        observability_thresholds=thresholds,
        config=config,
        observability_gate_sides=("b",),
    )

    assert report.evidence == "not_comparable"
    assert any(r.startswith("observation_gate_insufficient_b:") for r in report.reasons)
    assert not any(r.startswith("observation_gate_insufficient_a:") for r in report.reasons)


def test_gate_sides_b_only_allows_comparable_result_despite_side_a_insufficiency() -> None:
    """側 "a" が M1 観測ゲートで本来 insufficient になる短い記号旋律でも、
    ``observability_gate_sides=("b",)`` なら比較まで到達できる（M3 被覆下限は
    両側のまま維持されるため、被覆が十分な組み合わせで確認する）。"""
    config = _default_config()
    thresholds = _default_thresholds()

    # b: 2 フレーズ・8 ノート（min_note_count=8 ちょうど・M1 ゲート通過）。
    phase1_pitches = [60, 62, 64, 65]
    phase2_pitches = [67, 69, 67, 65]
    notes_b: List[MelodyNote] = []
    t = 0.0
    for p in phase1_pitches:
        notes_b.append(_note(p, t, t + 0.25))
        t += 0.3
    t += 1.0
    for p in phase2_pitches:
        notes_b.append(_note(p, t, t + 0.25))
        t += 0.3

    # a: b のフレーズ1と全く同じ 4 ノート（単一フレーズ・4 ノート —
    # min_note_count=8/min_phrase_count=2 未満で本来 insufficient）。
    notes_a: List[MelodyNote] = [
        _note(p, start, start + 0.25) for p, start in zip(phase1_pitches, [0.0, 0.3, 0.6, 0.9])
    ]

    # 両側とも "a" は正真正銘 insufficient であることを確認（対照条件）。
    both_sides_report = compare_melodies(
        _observation_from_notes(notes_a, route="ref"),
        _observation_from_notes(notes_b, route="take"),
        observability_thresholds=thresholds,
        config=config,
    )
    assert both_sides_report.evidence == "not_comparable"
    assert any(r.startswith("observation_gate_insufficient_a:") for r in both_sides_report.reasons)

    report = compare_melodies(
        _observation_from_notes(notes_a, route="ref"),
        _observation_from_notes(notes_b, route="take"),
        observability_thresholds=thresholds,
        config=config,
        observability_gate_sides=("b",),
    )

    assert report.evidence != "not_comparable"
    assert not any(r.startswith("observation_gate_insufficient") for r in report.reasons)
    # M3 被覆下限は sided 化されない——両側とも floor 以上であることを直接確認する。
    assert report.coverage["aligned_note_fraction_a"] >= config.coverage.floor
    assert report.coverage["aligned_note_fraction_b"] >= config.coverage.floor
