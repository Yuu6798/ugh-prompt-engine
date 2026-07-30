"""tests/test_melody_comparison.py — melody/comparison.py（M3c）のテスト。

CI 安全（重依存なし・pytest -m "not slow" に含む）: 拒否経路（観測ゲート不通過 /
被覆不足）、総合スコア不在のスキーマ、自己比較の正直な沈黙、移調不変性、
オクターブ折返しガード、evidence 導出（未校正 / 校正済み・軸が割れるケース）を
手計算・合成 fixture で検証する。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

import svp_rpe.melody.comparison as comparison_mod
from svp_rpe.melody.comparison import MelodyComparisonReport, _derive_evidence, compare_melodies
from svp_rpe.melody.observability import (
    MelodyNote,
    MelodyObservation,
    ObservabilityThresholds,
)
from svp_rpe.melody.representation import (
    EvidenceThresholdsConfig,
    M3ComparisonConfig,
    load_m3_registry,
)

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
M1_REGISTRY_PATH = BENCH_DIR / "registry.yaml"
M3_REGISTRY_PATH = BENCH_DIR / "m3_comparison_registry.yaml"


def _default_config() -> M3ComparisonConfig:
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def _with_evidence_thresholds(
    config: M3ComparisonConfig, evidence_thresholds: EvidenceThresholdsConfig
) -> M3ComparisonConfig:
    return M3ComparisonConfig(
        schema=config.schema,
        registered_utc=config.registered_utc,
        representation=config.representation,
        alignment=config.alignment,
        coverage=config.coverage,
        evidence_thresholds=evidence_thresholds,
        separation_margin=config.separation_margin,
    )


import yaml  # noqa: E402


def _default_thresholds() -> ObservabilityThresholds:
    mapping = yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))
    return ObservabilityThresholds.from_registry(mapping["observation_gate"])


def _note(pitch_midi: float, start_sec: float, end_sec: float, confidence: float = 0.9) -> MelodyNote:
    return MelodyNote(
        start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=confidence
    )


# 観測ゲートを通す 2 フレーズ・10 ノートの旋律（registry.yaml の observation_gate
# 閾値: min_note_count=8 / min_phrase_count=2 / max_octave_jump_rate=0.35 等を満たす）。
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


def _observation_from_notes(notes: List[MelodyNote], route: str = "test_route") -> MelodyObservation:
    return MelodyObservation(route=route, source_model="test:fake", notes=tuple(notes))


# 全フレーズが単ノート(interval 列が全フレーズで空になる縮退 fixture)。M1 観測
# ゲート(min_note_count=8 / min_phrase_count=2)は通すが、フレーズ内ノート数が
# 常に 1 のため `intervals_folded` が全フレーズで空タプルになり、整列 interval
# カラム自体が生成されない——「計算可能な軸が 1 つも無い」縮退を再現する。
def _single_note_phrases() -> List[MelodyNote]:
    notes: List[MelodyNote] = []
    t = 0.0
    duration = 1.0
    gap = 0.65  # phrase_gap_sec(0.6)超のギャップ → 毎ノートが新フレーズになる
    for _ in range(8):
        notes.append(_note(60, t, t + duration))
        t += duration + gap
    return notes


def _sparse_notes(count: int, route_gap: float = 0.05) -> List[MelodyNote]:
    """観測ゲートを通さない（note_count 不足の）旋律。"""
    notes: List[MelodyNote] = []
    t = 0.0
    for i in range(count):
        notes.append(_note(60 + i, t, t + 0.05))
        t += route_gap
    return notes


# --------------------------------------------------------------------------- #
# 拒否経路
# --------------------------------------------------------------------------- #
def test_compare_melodies_rejects_when_one_side_insufficient():
    config = _default_config()
    thresholds = _default_thresholds()
    good = _observation_from_notes(_good_notes(), route="good")
    insufficient = _observation_from_notes(_sparse_notes(3), route="sparse")

    report = compare_melodies(
        insufficient, good, observability_thresholds=thresholds, config=config
    )

    assert report.evidence == "not_comparable"
    assert report.axes == {"contour": None, "interval": None, "rhythm": None}
    assert any(r.startswith("observation_gate_insufficient_a:") for r in report.reasons)
    assert not any(r.startswith("observation_gate_insufficient_b:") for r in report.reasons)


def test_compare_melodies_rejects_insufficient_overlap():
    config = _default_config()
    thresholds = _default_thresholds()

    # A: 手計算どおりの 2 フレーズ・小音程パターン（folded interval = 2,2,1,2 / -2,-2,-1,-2）。
    notes_a = _good_notes()

    # B: A と全く重ならない音程パターン（folded interval が A のどの値とも一致しない）
    # かつオクターブジャンプ閾値（11 半音）未満に抑えた別旋律。
    phrase1_pitches = [60, 65, 62, 56, 52]  # intervals: +5, -3, -6, -4
    phrase2_pitches = [57, 65, 60, 68, 62]  # intervals: +8, -5, +8, -6 (boundary 57-52=5)
    pitches = phrase1_pitches + phrase2_pitches
    notes_b: List[MelodyNote] = []
    t = 0.0
    for i, p in enumerate(pitches):
        notes_b.append(_note(p, t, t + 0.25))
        t += 0.3
        if i == 4:
            t += 1.0

    report = compare_melodies(
        _observation_from_notes(notes_a, route="a"),
        _observation_from_notes(notes_b, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )

    assert report.evidence == "not_comparable"
    assert report.axes == {"contour": None, "interval": None, "rhythm": None}
    assert any(r.startswith("insufficient_overlap(") for r in report.reasons)
    # 被覆自体は観測事実として出す(空にしない)。
    assert report.coverage["aligned_note_fraction_a"] < config.coverage.floor


# --------------------------------------------------------------------------- #
# 全軸算出不能の縮退: not_comparable へ（レビュー対応 2026-07-30 第 21 ラウンド）
# --------------------------------------------------------------------------- #
def test_compare_melodies_returns_not_comparable_when_no_axis_is_computable():
    """全フレーズが単ノートで interval 列が全フレーズで空(=整列 interval カラム
    0)の縮退ケース: M1 ゲート・被覆下限ゲートはともに通過するが、計算可能な軸が
    1 つも無いため、以前の `evidence="none"` + axes 全 `None` ではなく
    `evidence="not_comparable"` + `no_computable_axes` を返す（設計 §8「測れない
    対には正直に沈黙」・evaluate 側の第 16 ラウンド整合検証との round-trip 回復）。
    """
    config = _default_config()
    thresholds = _default_thresholds()
    notes = _single_note_phrases()

    report = compare_melodies(
        _observation_from_notes(notes, route="a"),
        _observation_from_notes(notes, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )

    assert report.evidence == "not_comparable"
    assert report.axes == {"contour": None, "interval": None, "rhythm": None}
    assert report.axis_evidence == {}
    assert "no_computable_axes" in report.reasons
    # 被覆自体は観測事実として出す(not_comparable でも空にしない)。
    assert report.coverage["aligned_note_fraction_a"] == pytest.approx(1.0)
    assert report.coverage["aligned_note_fraction_b"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# スキーマテスト: 総合スコア不在
# --------------------------------------------------------------------------- #
def test_report_schema_has_no_overall_score_keys():
    config = _default_config()
    thresholds = _default_thresholds()
    notes = _good_notes()
    report = compare_melodies(
        _observation_from_notes(notes, route="a"),
        _observation_from_notes(notes, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )
    payload = report.to_dict()
    forbidden_substrings = ("overall", "total_score", "identity", "score")
    for key in payload:
        lowered = key.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), key
    # axes 内部にも紛れ込ませない。
    for key in payload["axes"]:
        lowered = key.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), key


def test_module_has_no_weighted_average_function():
    names = [name for name in dir(comparison_mod) if not name.startswith("__")]
    suspicious = [
        "overall_score",
        "total_score",
        "weighted_average",
        "weighted_score",
        "identity_score",
        "combined_score",
    ]
    for name in names:
        assert name.lower() not in suspicious


# --------------------------------------------------------------------------- #
# 自己比較 = 正直な沈黙
# --------------------------------------------------------------------------- #
def test_self_comparison_all_axes_one_but_evidence_none_when_uncalibrated():
    config = _default_config()
    assert config.evidence_thresholds.status == "uncalibrated"
    thresholds = _default_thresholds()
    notes = _good_notes()
    report = compare_melodies(
        _observation_from_notes(notes, route="a"),
        _observation_from_notes(notes, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )

    assert report.axes["contour"] == pytest.approx(1.0)
    assert report.axes["interval"] == pytest.approx(1.0)
    assert report.axes["rhythm"] == pytest.approx(1.0)
    assert report.coverage["aligned_note_fraction_a"] == pytest.approx(1.0)
    assert report.coverage["aligned_note_fraction_b"] == pytest.approx(1.0)
    assert report.coverage["phrase_coverage_a"] == pytest.approx(1.0)
    assert report.coverage["phrase_coverage_b"] == pytest.approx(1.0)
    assert report.evidence == "none"
    assert report.axis_evidence == {
        "contour": "uncalibrated",
        "interval": "uncalibrated",
        "rhythm": "uncalibrated",
    }
    assert "evidence_thresholds_uncalibrated" in report.reasons
    assert report.octave_artifact_suspected is False


# --------------------------------------------------------------------------- #
# 移調版 E2E
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shift", [3, -4])
def test_transposed_melody_matches_on_all_axes(shift: int):
    config = _default_config()
    thresholds = _default_thresholds()
    notes_a = _good_notes()
    notes_b = [_note(n.pitch_midi + shift, n.start_sec, n.end_sec) for n in notes_a]

    report = compare_melodies(
        _observation_from_notes(notes_a, route="a"),
        _observation_from_notes(notes_b, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )

    assert report.axes["contour"] == pytest.approx(1.0)
    assert report.axes["interval"] == pytest.approx(1.0)
    assert report.axes["rhythm"] == pytest.approx(1.0)
    assert report.octave_artifact_suspected is False


# --------------------------------------------------------------------------- #
# オクターブ折返しガード
# --------------------------------------------------------------------------- #
def test_octave_error_injection_flags_artifact():
    config = _default_config()
    thresholds = _default_thresholds()
    notes_a = _good_notes()
    notes_b = list(notes_a)
    # index 2 (phrase1 内)を +12 (1 オクターブ) だけ誤って高く観測したことにする。
    injected = notes_b[2]
    notes_b[2] = _note(injected.pitch_midi + 12, injected.start_sec, injected.end_sec)

    report = compare_melodies(
        _observation_from_notes(notes_a, route="a"),
        _observation_from_notes(notes_b, route="b"),
        observability_thresholds=thresholds,
        config=config,
    )

    assert report.evidence != "not_comparable"
    # 折返し後は完全一致(オクターブは mod 12 で相殺される)が、生音程は一致しない。
    assert report.axes["interval"] == pytest.approx(1.0)
    assert report.octave_artifact_suspected is True
    assert any(r.startswith("octave_artifact_suspected(") for r in report.reasons)


# --------------------------------------------------------------------------- #
# evidence 導出: uncalibrated / 校正済み(strong/weak/none, 割れ)
# --------------------------------------------------------------------------- #
def test_derive_evidence_uncalibrated_reports_none_with_reason():
    axis_evidence, evidence, reasons = _derive_evidence(
        {"contour": 1.0, "interval": 1.0, "rhythm": 1.0},
        EvidenceThresholdsConfig(status="uncalibrated", axes=None),
    )
    assert evidence == "none"
    assert axis_evidence == {"contour": "uncalibrated", "interval": "uncalibrated", "rhythm": "uncalibrated"}
    assert "evidence_thresholds_uncalibrated" in reasons


def test_derive_evidence_calibrated_strong_weak_none_and_disagreement():
    calibrated = EvidenceThresholdsConfig(
        status="frozen",
        axes={
            "contour": {"strong_min": 0.9, "none_max": 0.2},
            "interval": {"strong_min": 0.9, "none_max": 0.2},
            "rhythm": {"strong_min": 0.9, "none_max": 0.2},
        },
    )
    axis_evidence, evidence, reasons = _derive_evidence(
        {"contour": 1.0, "interval": 0.5, "rhythm": 0.05}, calibrated
    )
    assert axis_evidence == {"contour": "strong", "interval": "weak", "rhythm": "none"}
    # 総合は軸別 verdict の最強値(strong > weak > none)。
    assert evidence == "strong"
    assert any(r.startswith("axes_disagree(") for r in reasons)


def test_derive_evidence_calibrated_agreement_has_no_disagree_reason():
    calibrated = EvidenceThresholdsConfig(
        status="frozen",
        axes={
            "contour": {"strong_min": 0.9, "none_max": 0.2},
            "interval": {"strong_min": 0.9, "none_max": 0.2},
            "rhythm": {"strong_min": 0.9, "none_max": 0.2},
        },
    )
    axis_evidence, evidence, reasons = _derive_evidence(
        {"contour": 1.0, "interval": 1.0, "rhythm": 1.0}, calibrated
    )
    assert axis_evidence == {"contour": "strong", "interval": "strong", "rhythm": "strong"}
    assert evidence == "strong"
    assert not any(r.startswith("axes_disagree(") for r in reasons)


def test_derive_evidence_missing_axis_value_excluded_with_reason():
    calibrated = EvidenceThresholdsConfig(
        status="frozen",
        axes={
            "contour": {"strong_min": 0.9, "none_max": 0.2},
            "interval": {"strong_min": 0.9, "none_max": 0.2},
            "rhythm": {"strong_min": 0.9, "none_max": 0.2},
        },
    )
    axis_evidence, evidence, reasons = _derive_evidence(
        {"contour": 1.0, "interval": None, "rhythm": 1.0}, calibrated
    )
    assert "interval" not in axis_evidence
    assert axis_evidence == {"contour": "strong", "rhythm": "strong"}
    assert evidence == "strong"
    assert "axis_not_computable:interval" in reasons


def test_compare_melodies_end_to_end_with_calibrated_thresholds():
    """校正済み thresholds を注入した compare_melodies 全体の evidence 導出。"""
    config = _default_config()
    calibrated = _with_evidence_thresholds(
        config,
        EvidenceThresholdsConfig(
            status="frozen",
            axes={
                "contour": {"strong_min": 0.9, "none_max": 0.2},
                "interval": {"strong_min": 0.9, "none_max": 0.2},
                "rhythm": {"strong_min": 0.9, "none_max": 0.2},
            },
        ),
    )
    thresholds = _default_thresholds()
    notes = _good_notes()
    report = compare_melodies(
        _observation_from_notes(notes, route="a"),
        _observation_from_notes(notes, route="b"),
        observability_thresholds=thresholds,
        config=calibrated,
    )
    assert report.axis_evidence == {"contour": "strong", "interval": "strong", "rhythm": "strong"}
    assert report.evidence == "strong"


# --------------------------------------------------------------------------- #
# provenance_extra のマージ
# --------------------------------------------------------------------------- #
def test_provenance_extra_is_merged_into_report():
    config = _default_config()
    thresholds = _default_thresholds()
    notes = _good_notes()
    report = compare_melodies(
        _observation_from_notes(notes, route="a"),
        _observation_from_notes(notes, route="b"),
        observability_thresholds=thresholds,
        config=config,
        provenance_extra={"m3_registry_sha256": "deadbeef", "m1_registry_sha256": "cafef00d"},
    )
    assert report.provenance["m3_registry_sha256"] == "deadbeef"
    assert report.provenance["m1_registry_sha256"] == "cafef00d"
    assert report.provenance["route_a"] == "a"
    assert report.provenance["route_b"] == "b"


def test_report_is_frozen_dataclass():
    report = MelodyComparisonReport(
        axes={"contour": None, "interval": None, "rhythm": None},
        coverage={},
        octave_artifact_suspected=False,
        evidence="not_comparable",
        axis_evidence={},
        reasons=["x"],
        provenance={},
    )
    with pytest.raises(Exception):
        report.evidence = "strong"  # type: ignore[misc]
