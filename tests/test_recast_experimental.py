"""tests/test_recast_experimental.py — recast/experimental.py (M4a/M4b) のテスト。

Design Memo M4 §7 の受け入れ条件（表駆動）に対応する:
- M4a: 写像規則の全分岐 + axis_policy 検証 fail-closed + ゲート順序（G1〜G3）
- M4b: score_reference の決定論導出（手計算一致・TODO bpm・artifact 不在）

CI 安全（重依存なし・実抽出器を一切呼ばない）: `evaluate_melody_experimental_anchor`
のテイク側抽出は常に `route_runner` 注入（fake extractor）で置き換える
（`scripts/run_melody_comparison.py` の route_runner パターンと同型）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

from svp_rpe.arrange.contract import ContractAnchor
from svp_rpe.compose.models import CompositionScore
from svp_rpe.melody.comparison import MelodyComparisonReport
from svp_rpe.melody.observability import MelodyNote, MelodyObservation
from svp_rpe.recast.experimental import (
    ExperimentalAnchorEntry,
    derive_score_reference_observation,
    evaluate_melody_experimental_anchor,
    map_axis_policy_to_adherence,
)
from svp_rpe.recast.models import MelodyObservationConfig, RecastError

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
REAL_M3_UNCALIBRATED_REGISTRY = BENCH_DIR / "m3_comparison_registry.yaml"
REAL_M1_REGISTRY = BENCH_DIR / "registry.yaml"
SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"

VALID_SHA256 = "0" * 64


# --------------------------------------------------------------------------- #
# ヘルパーファクトリ
# --------------------------------------------------------------------------- #
def _score(*, bpm: "int | str" = 60) -> CompositionScore:
    data = yaml.safe_load(SCORE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = bpm
    return CompositionScore.model_validate(data)


def _anchor(
    axis_policy: Optional[Dict[str, str]], *, anchor_id: str = "melody-1"
) -> ContractAnchor:
    return ContractAnchor(
        anchor_id=anchor_id,
        domain="melody",
        mode="free",
        allow=[],
        artifact="melody_notes.json",
        artifact_sha256=VALID_SHA256,
        axis_policy=axis_policy,
    )


def _melody_config(
    *,
    reference: str = "score",
    reference_audio: Optional[str] = None,
    route: str = "pyin_direct",
) -> MelodyObservationConfig:
    return MelodyObservationConfig(
        reference=reference,
        reference_audio=reference_audio,
        comparison_registry="m3_comparison_registry.yaml",
        m1_registry="registry.yaml",
        route=route,
    )


_REGISTRY_BASE: Dict[str, Any] = {
    "schema": "m3-comparison/0.1",
    "registered_utc": "2026-07-31T00:00:00Z",
    "representation": {
        "pitch_quantization_semitones": 1,
        "contour_small_max_semitones": 2,
        "ioi_ratio_log2_step": 0.25,
        "duration_ratio_log2_step": 0.25,
        "chroma_fold_semitones": 12,
        "octave_artifact_divergence": 0.10,
    },
    "alignment": {
        "match_score": 1.0,
        "mismatch_score": -1.0,
        "gap_open": -1.0,
        "gap_extend": -0.5,
        "traceback_preference": ["diag", "up", "left"],
        "phrase_gap_sec": 0.6,
        "phrase_gap_score": 0.25,
    },
    "coverage": {"floor": 0.5, "floor_status": "frozen"},
    "separation_margin": {"min_same_minus_cross_margin": 0.15},
}


def _frozen_m3_registry_path(
    tmp_path: Path, *, axes: Tuple[str, ...] = ("contour", "interval", "rhythm")
) -> Path:
    """テスト専用の frozen m3_comparison_registry.yaml を tmp_path に生成する
    （Design Memo M4 §6: 凍結 registry 実ファイルは不変更・不複製改変。
    `M3ComparisonConfig.from_registry` の検証を通る形にする）。"""
    mapping = dict(_REGISTRY_BASE)
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {axis: {"strong_min": 0.8, "none_max": 0.3} for axis in axes},
    }
    path = tmp_path / "m3_comparison_registry.yaml"
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return path


def _note_events_bytes(notes: List[Tuple[float, str, float]]) -> bytes:
    payload = {
        "schema": "note-events/0.1",
        "notes": [
            {"start_beat": start, "pitch": pitch, "duration_beats": duration}
            for start, pitch, duration in notes
        ],
    }
    return json.dumps(payload).encode("utf-8")


# 2 フレーズ・10 ノートの旋律（`tests/test_melody_comparison.py::_good_notes` と
# 同じ形。bpm=60 なので beat==秒 で `_good_notes()` と数値まで一致させられる）。
_REFERENCE_NOTES_JSON: List[Tuple[float, str, float]] = [
    (0.0, "C4", 0.25),
    (0.3, "D4", 0.25),
    (0.6, "E4", 0.25),
    (0.9, "F4", 0.25),
    (1.2, "G4", 0.25),
    (2.45, "A4", 0.25),
    (2.75, "G4", 0.25),
    (3.05, "F4", 0.25),
    (3.35, "E4", 0.25),
    (3.65, "D4", 0.25),
]


def _reference_artifact_bytes() -> bytes:
    return _note_events_bytes(_REFERENCE_NOTES_JSON)


def _take_notes(pitches_midi: List[float], *, confidence: float = 0.9) -> MelodyObservation:
    """`_REFERENCE_NOTES_JSON` と同じタイミング（start_sec/end_sec）を使い、
    pitch だけ差し替えたテイク側観測を組む（bpm=60 の score_reference と秒単位で
    直接比較できるように、reference と同一の 10 ノート・タイミングを踏襲する）。"""
    assert len(pitches_midi) == len(_REFERENCE_NOTES_JSON)
    notes = tuple(
        MelodyNote(
            start_sec=start_beat,
            end_sec=start_beat + duration,
            pitch_midi=pitch,
            confidence=confidence,
        )
        for (start_beat, _pitch, duration), pitch in zip(_REFERENCE_NOTES_JSON, pitches_midi)
    )
    return MelodyObservation(route="fake_take", source_model="test:fake", notes=notes)


_REFERENCE_PITCHES = [60.0, 62.0, 64.0, 65.0, 67.0, 69.0, 67.0, 65.0, 64.0, 62.0]


def _make_route_runner(observation: MelodyObservation):
    def _runner(audio_path: str) -> Tuple[MelodyObservation, Dict[str, Any]]:
        return observation, {}

    return _runner


# --------------------------------------------------------------------------- #
# M4a — 写像規則（§3・純関数）: 表駆動
# --------------------------------------------------------------------------- #
def _report(
    axis_evidence: Dict[str, str],
    *,
    axes: Optional[Dict[str, Optional[float]]] = None,
    octave_artifact_suspected: bool = False,
    reasons: Optional[List[str]] = None,
) -> MelodyComparisonReport:
    return MelodyComparisonReport(
        axes=axes or {"contour": 0.9, "interval": 0.9, "rhythm": 0.9},
        coverage={
            "aligned_note_fraction_a": 1.0,
            "aligned_note_fraction_b": 1.0,
            "phrase_coverage_a": 1.0,
            "phrase_coverage_b": 1.0,
        },
        octave_artifact_suspected=octave_artifact_suspected,
        evidence="strong",
        axis_evidence=axis_evidence,
        reasons=list(reasons or []),
        provenance={},
    )


def test_mapping_all_strong_is_preserved() -> None:
    policy = {"contour": "hard", "interval": "elastic", "rhythm": "elastic"}
    report = _report({"contour": "strong", "interval": "strong", "rhythm": "strong"})
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "preserved"
    assert reasons == []


def test_mapping_hard_axis_none_is_changed_outside_policy() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report({"contour": "none", "interval": "strong"})
    status, _reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "changed_outside_policy"


def test_mapping_hard_axis_weak_is_not_observed_insufficient_evidence() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report({"contour": "weak", "interval": "strong"})
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "not_observed"
    assert "insufficient_evidence" in reasons


def test_mapping_elastic_axis_weak_is_changed_within_policy() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report({"contour": "strong", "interval": "weak"})
    status, _reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "changed_within_policy"


def test_mapping_elastic_axis_none_is_changed_within_policy() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report({"contour": "strong", "interval": "none"})
    status, _reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "changed_within_policy"


def test_mapping_defensive_uncalibrated_axis_value_is_not_observed() -> None:
    policy = {"contour": "hard"}
    report = _report({"contour": "uncalibrated"})
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "not_observed"
    assert "comparator_uncalibrated(axis=contour)" in reasons


def test_mapping_defensive_missing_axis_evidence_is_not_observed() -> None:
    """axis_evidence dict に該当軸が丸ごと欠落（status='frozen' で bounds 欠落等）
    しているケースも「uncalibrated」と同一視して防御分岐へ落ちる。"""
    policy = {"contour": "hard", "rhythm": "free"}
    report = _report({"contour": "strong"})  # rhythm 欠落
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "not_observed"
    assert "comparator_uncalibrated(axis=rhythm)" in reasons


def test_mapping_free_axis_does_not_participate() -> None:
    """free 軸の evidence が悪くても判定に影響しない（報告のみ）。"""
    policy = {"contour": "hard", "interval": "elastic", "rhythm": "free"}
    report = _report({"contour": "strong", "interval": "strong", "rhythm": "none"})
    status, _reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "preserved"


def test_mapping_transcribes_octave_artifact_reason() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    octave_reason = "octave_artifact_suspected(folded=0.9000, raw=0.5000)"
    report = _report(
        {"contour": "strong", "interval": "strong"},
        octave_artifact_suspected=True,
        reasons=[octave_reason],
    )
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "preserved"
    assert octave_reason in reasons


def test_mapping_transcribes_report_reasons_regardless_of_branch() -> None:
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report(
        {"contour": "none", "interval": "strong"},
        reasons=["axes_disagree(contour=none, interval=strong)"],
    )
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "changed_outside_policy"
    assert "axes_disagree(contour=none, interval=strong)" in reasons


# --------------------------------------------------------------------------- #
# M4a — axis_policy 検証 fail-closed（ContractAnchor 層は test_preservation_contract.py
# 側で網羅済み。ここでは G1/G3 と絡む「frozen 部分集合」との突合のみ扱う）。
# --------------------------------------------------------------------------- #
def test_axis_policy_referencing_axis_outside_frozen_subset_raises_recast_error(
    tmp_path: Path,
) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path, axes=("contour", "interval"))
    anchor = _anchor({"contour": "hard", "rhythm": "elastic"})
    with pytest.raises(RecastError, match="not calibrated in the frozen"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
            route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
        )


# --------------------------------------------------------------------------- #
# M4a — ゲート順序 G1/G2 + config 不在（短絡・not_observed）
# --------------------------------------------------------------------------- #
def test_missing_melody_config_is_not_observed() -> None:
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=None,
        score=_score(),
        m3_registry_path=REAL_M3_UNCALIBRATED_REGISTRY,
        m1_registry_path=REAL_M1_REGISTRY,
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["melody_config_missing"]
    assert entry.provenance == {}
    assert entry.axes == {"contour": None}


def test_uncalibrated_registry_is_not_observed_g1(tmp_path: Path) -> None:
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(),
        m3_registry_path=REAL_M3_UNCALIBRATED_REGISTRY,
        m1_registry_path=REAL_M1_REGISTRY,
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["comparator_uncalibrated"]
    # 短絡時も判明済みの registry sha256 は provenance に載る。
    assert "m3_registry_sha256" in entry.provenance
    assert "m1_registry_sha256" in entry.provenance


def test_band_out_of_validation_when_take_band_none(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band=None,
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["band_out_of_validation(declared=none)"]


def test_band_out_of_validation_when_take_band_uncalibrated(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="full_mix",
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["band_out_of_validation(declared=full_mix)"]


def test_audio_reference_without_reference_audio_is_author_input_missing(
    tmp_path: Path,
) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        reference_audio_path=None,
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["author_input_missing"]


# --------------------------------------------------------------------------- #
# M4b — score_reference 決定論導出
# --------------------------------------------------------------------------- #
def test_score_reference_hand_calc_matches() -> None:
    artifact = _note_events_bytes([(0.0, "C4", 1.0), (1.0, "E4", 1.0)])
    score = _score(bpm=120)
    observation, reason = derive_score_reference_observation(score, artifact)
    assert reason is None
    assert observation is not None
    assert observation.route == "score_reference"
    assert observation.source_model == "symbolic:note-events/0.1"
    assert len(observation.notes) == 2
    first, second = observation.notes
    assert first.start_sec == pytest.approx(0.0)
    assert first.end_sec == pytest.approx(0.5)
    assert first.pitch_midi == pytest.approx(60.0)
    assert first.confidence == pytest.approx(1.0)
    assert second.start_sec == pytest.approx(0.5)
    assert second.end_sec == pytest.approx(1.0)
    assert second.pitch_midi == pytest.approx(64.0)
    assert observation.total_duration_sec == pytest.approx(1.0)


def test_score_reference_onset_ordering_uses_start_beat_not_json_order() -> None:
    # JSON 記載順は敢えて逆順にし、start_beat 昇順に並べ替えられることを確認する。
    artifact = _note_events_bytes([(1.0, "D4", 0.5), (0.0, "C4", 0.5)])
    observation, reason = derive_score_reference_observation(_score(bpm=60), artifact)
    assert reason is None
    assert observation is not None
    assert [round(n.pitch_midi) for n in observation.notes] == [60, 62]


def test_score_reference_todo_bpm_is_author_input_missing() -> None:
    artifact = _reference_artifact_bytes()
    score = _score(bpm="TODO(transcribe): bpm undetected")
    observation, reason = derive_score_reference_observation(score, artifact)
    assert observation is None
    assert reason == "author_input_missing"


def test_score_reference_missing_artifact_is_author_input_missing() -> None:
    observation, reason = derive_score_reference_observation(_score(), None)
    assert observation is None
    assert reason == "author_input_missing"


def test_score_reference_malformed_schema_raises_value_error() -> None:
    bad = json.dumps({"schema": "note-events/0.2", "notes": []}).encode("utf-8")
    with pytest.raises(ValueError, match="unsupported schema"):
        derive_score_reference_observation(_score(), bad)


def test_score_reference_pitch_parsing_matches_observe_module() -> None:
    from svp_rpe.arrange.observe import _note_name_to_midi

    for pitch in ("C4", "Eb4", "F#3", "A0", "G♯5", "B♭2"):
        artifact = _note_events_bytes([(0.0, pitch, 1.0)])
        observation, reason = derive_score_reference_observation(_score(bpm=60), artifact)
        assert reason is None
        assert observation is not None
        assert observation.notes[0].pitch_midi == pytest.approx(float(_note_name_to_midi(pitch)))


def test_orchestrator_via_score_reference_matches_derive_score_reference_observation(
    tmp_path: Path,
) -> None:
    """`evaluate_melody_experimental_anchor` の score_reference 参照側が
    `derive_score_reference_observation` と同じ値を使っていることを、G1 不成立
    （比較まで進まない）短絡越しに provenance の melody_artifact_sha256 経由で
    間接確認する（直接比較する内部状態が無いため、公開経路の author_input_missing
    分岐で一致を確認する）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm="TODO(transcribe): bpm undetected"),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["author_input_missing"]


# --------------------------------------------------------------------------- #
# M4a/M4b 統合 — フルパイプライン（route_runner 注入・実抽出器なし）
# --------------------------------------------------------------------------- #
def test_full_pipeline_identical_take_is_preserved(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    take = _take_notes(_REFERENCE_PITCHES)
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        melody_artifact_sha256="a" * 64,
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path="take.wav",
        route_runner=_make_route_runner(take),
    )
    assert entry.adherence_status == "preserved"
    assert entry.axis_evidence["contour"] == "strong"
    assert entry.axis_evidence["interval"] == "strong"
    assert entry.provenance.get("extractor_injected") is True
    assert entry.provenance.get("melody_artifact_sha256") == "a" * 64
    assert entry.provenance.get("reference") == "score"
    assert "m3_registry_sha256" in entry.provenance
    assert "m1_registry_sha256" in entry.provenance


def test_full_pipeline_octave_scaled_take_is_changed_outside_policy(tmp_path: Path) -> None:
    """テイク側の音程を折返し後（chroma_fold_semitones=12）は参照と一致するが
    生の半音幅は 10（`contour_small_max_semitones=2` を大きく超え、かつ
    `octave_jump_semitones=11.0` の M1 ゲート未満）だけずらす——NW 整列は
    `intervals_folded`（折返し済み）でスコアするため高い被覆で整列されるが、
    contour 軸は生の半音幅から輪郭ビンを求めるため折返しでは救えず全滅する
    （interval 軸は strong のまま・hard 軸 contour だけ none になる分岐を狙う）。
    """
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic"})
    reference_notes = [
        (0.0, "C4", 0.25),
        (0.3, "D4", 0.25),
        (0.6, "E4", 0.25),
        (0.9, "F#4", 0.25),
        (1.2, "G#4", 0.25),
        (2.45, "Bb4", 0.25),
        (2.75, "G#4", 0.25),
        (3.05, "F#4", 0.25),
        (3.35, "E4", 0.25),
        (3.65, "D4", 0.25),
    ]
    take_pitches = [60.0, 50.0, 40.0, 30.0, 20.0, 70.0, 80.0, 90.0, 100.0, 110.0]
    take_notes = tuple(
        MelodyNote(start_sec=start, end_sec=start + duration, pitch_midi=pitch, confidence=0.9)
        for (start, _pitch_name, duration), pitch in zip(reference_notes, take_pitches)
    )
    take = MelodyObservation(route="fake_take", source_model="test:fake", notes=take_notes)
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_note_events_bytes(reference_notes),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path="take.wav",
        route_runner=_make_route_runner(take),
    )
    assert entry.adherence_status == "changed_outside_policy"
    assert entry.axis_evidence["contour"] == "none"
    assert entry.axis_evidence["interval"] == "strong"


def test_full_pipeline_sparse_take_is_not_observed_not_comparable(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    sparse_notes = tuple(
        MelodyNote(start_sec=i * 0.05, end_sec=i * 0.05 + 0.04, pitch_midi=60 + i, confidence=0.9)
        for i in range(3)
    )
    take = MelodyObservation(route="fake_sparse", source_model="test:fake", notes=sparse_notes)
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path="take.wav",
        route_runner=_make_route_runner(take),
    )
    assert entry.adherence_status == "not_observed"
    assert entry.axis_evidence == {}


def test_full_pipeline_is_deterministic(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})

    def _run() -> ExperimentalAnchorEntry:
        take = _take_notes(_REFERENCE_PITCHES)
        return evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(bpm=60),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
            route_runner=_make_route_runner(take),
        )

    first = _run()
    second = _run()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_evaluate_requires_axis_policy_opt_in() -> None:
    anchor = _anchor(None)
    with pytest.raises(ValueError, match="axis_policy"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=REAL_M3_UNCALIBRATED_REGISTRY,
            m1_registry_path=REAL_M1_REGISTRY,
        )
