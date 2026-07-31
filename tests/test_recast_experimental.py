"""tests/test_recast_experimental.py — recast/experimental.py (M4a/M4b/M4c) のテスト。

Design Memo M4 §7 の受け入れ条件（表駆動）に対応する:
- M4a: 写像規則の全分岐 + axis_policy 検証 fail-closed + ゲート順序（G1〜G3）
- M4b: score_reference の決定論導出（手計算一致・TODO bpm・artifact 不在）
- M4c: DD-10b (`reference_band` 配線) + ingest/plan orchestration
  （`collect_melody_experimental_anchors` / `melody_experimental_plan_warnings` /
  `resolve_melody_observation_paths`）

CI 安全（重依存なし・実抽出器を一切呼ばない）: `evaluate_melody_experimental_anchor`
のテイク側抽出は常に `route_runner` 注入（fake extractor）で置き換える
（`scripts/run_melody_comparison.py` の route_runner パターンと同型）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

from svp_rpe.arrange.contract import ContractAnchor, ContractInputs, InputHash, PreservationContract
from svp_rpe.compose.models import CompositionScore
from svp_rpe.melody.comparison import MelodyComparisonReport
from svp_rpe.melody.observability import MelodyNote, MelodyObservation
from svp_rpe.recast.experimental import (
    ExperimentalAnchorEntry,
    collect_melody_experimental_anchors,
    derive_score_reference_observation,
    evaluate_melody_experimental_anchor,
    map_axis_policy_to_adherence,
    melody_experimental_anchor_ids,
    melody_experimental_plan_warnings,
    resolve_main_observation_anchor_scope,
    resolve_melody_observation_paths,
)
from svp_rpe.recast.models import BackendRef, MelodyObservationConfig, RecastError

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
    reference_band: Optional[str] = None,
    route: str = "crepe_direct",
) -> MelodyObservationConfig:
    return MelodyObservationConfig(
        reference=reference,
        reference_audio=reference_audio,
        reference_band=reference_band,
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


def _write_audio_file(path: Path, content: bytes = b"fake-audio-bytes") -> str:
    """R2-3 (Codex round2 P2・TOCTOU 封鎖) 対応後、`evaluate_melody_experimental_
    anchor` は抽出直前に take/reference 音声を実際に読んで凍結コピーする
    （`route_runner` 注入時も含む——凍結コピーのパスが注入 runner にも渡る）
    ため、``take_audio_path``/``reference_audio_path`` は実在するファイルで
    なければならない（プレースホルダ文字列は使えなくなった）。このヘルパーは
    ``path`` に最小のダミー音声バイト列を書き、``str(path)`` を返す。"""
    path.write_bytes(content)
    return str(path)


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
    しているケースも「uncalibrated」と同一視して防御分岐へ落ちる——判定参加軸
    （hard/elastic）限定（R2-5・Codex round2 P2 で free 軸はこの対象から
    除外された。free 軸の欠落は `test_mapping_free_axis_missing_evidence_
    does_not_block_judgment` 参照）。"""
    policy = {"contour": "hard", "interval": "elastic"}
    report = _report({"contour": "strong"})  # interval 欠落
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "not_observed"
    assert "comparator_uncalibrated(axis=interval)" in reasons


def test_mapping_free_axis_does_not_participate() -> None:
    """free 軸の evidence が悪くても判定に影響しない（報告のみ）。"""
    policy = {"contour": "hard", "interval": "elastic", "rhythm": "free"}
    report = _report({"contour": "strong", "interval": "strong", "rhythm": "none"})
    status, _reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "preserved"


def test_mapping_free_axis_missing_evidence_does_not_block_judgment() -> None:
    """R2-5 (Codex round2 P2): free 軸の evidence が report に丸ごと欠落して
    いても、hard/elastic 軸が strong なら判定をブロックしない
    （設計書 §3「free 軸は判定に不参加」）。回帰前は defensive uncalibrated
    分岐（`test_mapping_defensive_missing_axis_evidence_is_not_observed` と
    同型のガード）が free 軸にも誤って適用され、この欠落だけで
    `not_observed(comparator_uncalibrated(axis=rhythm))` に落ちていた。"""
    policy = {"contour": "hard", "interval": "elastic", "rhythm": "free"}
    report = _report({"contour": "strong", "interval": "strong"})  # rhythm 丸ごと欠落
    status, reasons = map_axis_policy_to_adherence(policy, report)
    assert status == "preserved"
    assert not any("rhythm" in reason for reason in reasons)


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


# --------------------------------------------------------------------------- #
# R2-2 (Codex round2 P2) — M1 registry の構造検証 fail-closed
# --------------------------------------------------------------------------- #
def test_m1_registry_empty_yaml_raises_recast_error(tmp_path: Path) -> None:
    """空 YAML（`yaml.safe_load` は `None` を返す）を素通しすると直後の
    `mapping["observation_gate"]` が `TypeError` を未捕捉のまま送出していた
    ——actionable な `RecastError` に変換されることを確認する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "empty_registry.yaml"
    m1_path.write_text("", encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="must be a YAML mapping"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
        )


def test_m1_registry_scalar_yaml_raises_recast_error(tmp_path: Path) -> None:
    """トップレベルがスカラー（マッピングでない）YAML も同様に fail-closed。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "scalar_registry.yaml"
    m1_path.write_text("just-a-string\n", encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="must be a YAML mapping"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
        )


def test_m1_registry_missing_observation_gate_section_raises_recast_error(
    tmp_path: Path,
) -> None:
    """`observation_gate` 節が丸ごと欠落した YAML も fail-closed。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "no_gate_registry.yaml"
    m1_path.write_text(yaml.safe_dump({"unrelated": {"a": 1}}), encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="observation_gate"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
        )


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
# M4c (DD-10b) — audio_reference の reference_melody_band 配線
# --------------------------------------------------------------------------- #
def test_audio_reference_without_reference_band_is_band_out_of_validation(
    tmp_path: Path,
) -> None:
    """`reference_melody_band` 未指定（既定 None）は audio_reference の原曲側
    G2 を通過できない——DD-10b 追加前と同じ安全側の帰結（回帰確認）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        reference_audio_path="ref.wav",
        take_audio_path="take.wav",
        route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["band_out_of_validation(declared=none)"]


def test_audio_reference_with_calibrated_reference_band_passes_g2(tmp_path: Path) -> None:
    """DD-10b: `reference_melody_band="clear_lead"` を渡すと audio_reference の
    原曲側 G2 が通過し、両側抽出（route_runner/reference_route_runner 注入）を
    経て写像規則まで到達する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    take = _take_notes(_REFERENCE_PITCHES)
    reference_take = _take_notes(_REFERENCE_PITCHES)
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        reference_audio_path=_write_audio_file(tmp_path / "ref.wav", b"reference-bytes"),
        reference_melody_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav", b"take-bytes"),
        route_runner=_make_route_runner(take),
        reference_route_runner=_make_route_runner(reference_take),
    )
    assert entry.adherence_status == "preserved"
    assert entry.provenance.get("reference") == "audio"
    assert entry.provenance.get("extractor_injected") is True
    assert entry.provenance.get("reference_audio_sha256") == hashlib.sha256(
        b"reference-bytes"
    ).hexdigest()


# --------------------------------------------------------------------------- #
# R1-2 (Codex round1 P2) — 抽出器 provenance の名前空間統合
# --------------------------------------------------------------------------- #
def test_injected_route_runner_extra_provenance_is_not_merged_into_namespace(
    tmp_path: Path,
) -> None:
    """注入 runner（テスト用 fake extractor）が extra provenance を返しても
    take_extractor/reference_extractor 名前空間へは載せない——偽の pin を
    刻まない（`extractor_injected` フラグのみで判別可能な既存契約の確認）。"""

    def _runner(audio_path: str):
        return _take_notes(_REFERENCE_PITCHES), {"fake_pin": "should-not-be-recorded"}

    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
        route_runner=_runner,
    )
    assert "take_extractor" not in entry.provenance
    assert entry.provenance.get("extractor_injected") is True


def test_non_injected_take_extraction_merges_provenance_under_take_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-2: `route_runner` 非注入（既定の実抽出器経路）時は
    `observe_via_route_with_provenance` の第 2 戻り値（code/weights pin 等）を
    `provenance["take_extractor"]` へ保存する（従来は破棄していた）。実抽出器を
    呼ばないよう `svp_rpe.recast.experimental.observe_via_route_with_provenance`
    を monkeypatch する。"""
    import svp_rpe.recast.experimental as experimental_module

    fake_extra = {"extractor_weights_sha256": "take-pin-0000"}

    def _fake_observe(audio_path: str, route):
        assert route.name == "crepe_direct"
        return _take_notes(_REFERENCE_PITCHES), dict(fake_extra)

    monkeypatch.setattr(experimental_module, "observe_via_route_with_provenance", _fake_observe)

    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
    )
    assert entry.provenance.get("take_extractor") == fake_extra
    assert "extractor_injected" not in entry.provenance
    assert entry.adherence_status == "preserved"


def test_non_injected_reference_extraction_merges_provenance_under_reference_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-2: audio_reference の原曲側も非注入時は
    `provenance["reference_extractor"]` へ保存する（take 側と別名前空間）。"""
    import svp_rpe.recast.experimental as experimental_module

    take_extra = {"extractor_weights_sha256": "take-pin"}
    reference_extra = {"extractor_weights_sha256": "ref-pin"}
    take_content = b"take-audio-bytes"
    reference_content = b"reference-audio-bytes"

    def _fake_observe(audio_path: str, route):
        assert route.name == "crepe_direct"
        # R2-3 (Codex round2 P2・TOCTOU 封鎖) 対応後、`audio_path` は凍結コピー
        # の一時パスであり元の "take.wav"/"ref.wav" 文字列とは一致しなくなった
        # ——bytes 内容で take/reference を判別する（`test_m3_comparison_
        # harness.py:_fake_route_runner` の `notes_by_content` 方式と同型）。
        content = Path(audio_path).read_bytes()
        if content == take_content:
            return _take_notes(_REFERENCE_PITCHES), dict(take_extra)
        assert content == reference_content
        return _take_notes(_REFERENCE_PITCHES), dict(reference_extra)

    monkeypatch.setattr(experimental_module, "observe_via_route_with_provenance", _fake_observe)

    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        reference_audio_path=_write_audio_file(tmp_path / "ref.wav", reference_content),
        reference_melody_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav", take_content),
    )
    assert entry.provenance.get("take_extractor") == take_extra
    assert entry.provenance.get("reference_extractor") == reference_extra
    assert "extractor_injected" not in entry.provenance


def test_non_injected_extraction_with_empty_extra_provenance_omits_namespace_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽出器が空の provenance dict を返す場合（例: pyin は重みなし）、
    `take_extractor`/`reference_extractor` キー自体を作らない（空 dict を
    report へ刻まない）。"""
    import svp_rpe.recast.experimental as experimental_module

    def _fake_observe(audio_path: str, route):
        return _take_notes(_REFERENCE_PITCHES), {}

    monkeypatch.setattr(experimental_module, "observe_via_route_with_provenance", _fake_observe)

    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
    )
    assert "take_extractor" not in entry.provenance


# --------------------------------------------------------------------------- #
# R2-3 (Codex round2 P2) — 抽出を pin 済み take バイトへ束縛（TOCTOU 封鎖）
# --------------------------------------------------------------------------- #
def test_take_audio_toctou_mismatch_raises_recast_error(tmp_path: Path) -> None:
    """`expected_take_sha256`（collect() 確定済みの外部 pin）とディスク上の
    take の実 sha256 が食い違う場合、`RecastError` を送出する（不一致は
    観測前に検出——呼び出し側は observation_incomplete 経路へ倒す）。"""
    take_path = tmp_path / "take.wav"
    take_path.write_bytes(b"actual-take-bytes")
    wrong_sha256 = hashlib.sha256(b"different-bytes-entirely").hexdigest()
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="does not match its pinned sha256"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(bpm=60),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
            melody_take_band="clear_lead",
            take_audio_path=str(take_path),
            expected_take_sha256=wrong_sha256,
            route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
        )


def test_take_audio_matching_pin_passes_through(tmp_path: Path) -> None:
    """`expected_take_sha256` がディスク上の実 sha256 と一致すれば通常どおり
    評価が進む（正常経路が誤って弾かれないことの機械 assert）。"""
    take_path = tmp_path / "take.wav"
    take_path.write_bytes(b"actual-take-bytes")
    correct_sha256 = hashlib.sha256(b"actual-take-bytes").hexdigest()
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=str(take_path),
        expected_take_sha256=correct_sha256,
        route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
    )
    assert entry.adherence_status == "preserved"


def test_route_runner_receives_frozen_copy_path_not_original(tmp_path: Path) -> None:
    """`route_runner`（注入 seam）に渡される ``audio_path`` は元の
    ``take_audio_path`` そのものではなく、run 出力ディレクトリ外の凍結
    コピーである（同じ bytes を持つが別パス）——抽出直前の TOCTOU 窓を
    実際に塞いでいることの直接確認。"""
    take_path = tmp_path / "take.wav"
    take_path.write_bytes(b"actual-take-bytes")
    seen_paths: List[str] = []

    def _runner(audio_path: str) -> Tuple[MelodyObservation, Dict[str, Any]]:
        seen_paths.append(audio_path)
        return _take_notes(_REFERENCE_PITCHES), {}

    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=str(take_path),
        route_runner=_runner,
    )
    assert len(seen_paths) == 1
    frozen_path = Path(seen_paths[0])
    assert frozen_path != take_path
    assert not frozen_path.exists()  # 呼び出し完了後に後始末（tempdir 解体）済み


def test_audio_reference_freezes_reference_audio_and_records_sha256(tmp_path: Path) -> None:
    """audio_reference 側も凍結コピーされ、resolved 時点の bytes の sha256 が
    provenance の ``reference_audio_sha256`` として記録される（R2-3）。"""
    take_path = tmp_path / "take.wav"
    take_path.write_bytes(b"take-bytes")
    ref_path = tmp_path / "ref.wav"
    ref_path.write_bytes(b"reference-bytes")
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        reference_audio_path=str(ref_path),
        reference_melody_band="clear_lead",
        take_audio_path=str(take_path),
        route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
        reference_route_runner=_make_route_runner(_take_notes(_REFERENCE_PITCHES)),
    )
    assert entry.provenance.get("reference_audio_sha256") == hashlib.sha256(
        b"reference-bytes"
    ).hexdigest()


# --------------------------------------------------------------------------- #
# R1-4 (Codex round1 P2・層分離裁定) — score_reference は参照側の M1 ゲート免除
# --------------------------------------------------------------------------- #
def test_score_reference_gate_only_take_side_allows_sparse_symbolic_reference(
    tmp_path: Path,
) -> None:
    """記号旋律側（参照）が M1 の `min_note_count`/`min_phrase_count` を満たさない
    短い旋律でも、テイク側が十分観測可能なら `not_observed` へ落ちない
    ——`docs/DESIGN_M4_recast_melody_anchor.md` §2 の G2/G3 テイク限定裁定の
    end-to-end 確認（`melody/comparison.py` 単体は
    `tests/test_melody_comparison_gate_sides.py` が別途確認する）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})

    # 参照側（記号旋律）: 4 ノート・単一フレーズ（bpm=60 なので beat==秒 —
    # registry.yaml の min_note_count=8/min_phrase_count=2 を満たさない）。
    reference_notes = [
        (0.0, "C4", 0.25),
        (0.3, "D4", 0.25),
        (0.6, "E4", 0.25),
        (0.9, "F4", 0.25),
    ]
    # テイク側: 参照側と同じ 4 ノート（フレーズ1）+ 追加 4 ノート（フレーズ2）
    # ——2 フレーズ・8 ノートで M1 ゲートを通す。
    take_pitches_phase1 = [60.0, 62.0, 64.0, 65.0]
    take_pitches_phase2 = [67.0, 69.0, 67.0, 65.0]
    take_notes = tuple(
        MelodyNote(start_sec=start, end_sec=start + duration, pitch_midi=pitch, confidence=0.9)
        for (start, _pitch, duration), pitch in zip(reference_notes, take_pitches_phase1)
    ) + tuple(
        MelodyNote(
            start_sec=1.9 + i * 0.3,
            end_sec=1.9 + i * 0.3 + 0.25,
            pitch_midi=pitch,
            confidence=0.9,
        )
        for i, pitch in enumerate(take_pitches_phase2)
    )
    take = MelodyObservation(route="fake_take", source_model="test:fake", notes=take_notes)

    # 対照: 参照側が短くても、比較まで到達し `not_observed` にならないこと。
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(bpm=60),
        melody_artifact_bytes=_note_events_bytes(reference_notes),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
        route_runner=_make_route_runner(take),
    )

    assert entry.adherence_status != "not_observed"
    assert not any("observation_gate_insufficient_a" in r for r in entry.reasons)


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


# --------------------------------------------------------------------------- #
# R2-7 (Codex round2 P2) — 非正 bpm の fail-closed
# --------------------------------------------------------------------------- #
def test_score_reference_zero_bpm_is_author_input_invalid() -> None:
    """bpm=0 は beat→秒変換（60/bpm）の前に検証され、`ZeroDivisionError` では
    なく `author_input_invalid(bpm=...)` の not_observed 理由へ落ちる。"""
    artifact = _reference_artifact_bytes()
    observation, reason = derive_score_reference_observation(_score(bpm=0), artifact)
    assert observation is None
    assert reason == "author_input_invalid(bpm=0.000)"


def test_score_reference_negative_bpm_is_author_input_invalid() -> None:
    """負の bpm も同様に fail-closed（負タイムスタンプが整列へ流れるのを防ぐ）。"""
    artifact = _reference_artifact_bytes()
    observation, reason = derive_score_reference_observation(_score(bpm=-60), artifact)
    assert observation is None
    assert reason == "author_input_invalid(bpm=-60.000)"


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
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
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
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
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
        take_audio_path=_write_audio_file(tmp_path / "take.wav"),
        route_runner=_make_route_runner(take),
    )
    assert entry.adherence_status == "not_observed"
    assert entry.axis_evidence == {}


def test_full_pipeline_is_deterministic(tmp_path: Path) -> None:
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})
    take_audio_path = _write_audio_file(tmp_path / "take.wav")

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
            take_audio_path=take_audio_path,
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


# --------------------------------------------------------------------------- #
# M4c — orchestration ヘルパー
# --------------------------------------------------------------------------- #
def _harmony_anchor(*, anchor_id: str = "harmony-1") -> ContractAnchor:
    """axis_policy 語彙の無い domain の anchor（melody フィルタリングの対照）。"""
    return ContractAnchor(
        anchor_id=anchor_id,
        domain="harmony",
        mode="free",
        allow=[],
        artifact="chord_progression.json",
        artifact_sha256=VALID_SHA256,
        axis_policy=None,
    )


def _contract(anchors: List[ContractAnchor]) -> PreservationContract:
    return PreservationContract(
        work_id="w",
        inputs=ContractInputs(
            identity_manifest=InputHash(sha256=VALID_SHA256),
            arrangement_spec=InputHash(sha256=VALID_SHA256),
        ),
        anchors=anchors,
    )


def _backend_ref(*, melody_take_band: Optional[str] = None) -> BackendRef:
    return BackendRef(
        capability_profile="deterministic",
        invocation="local",
        invocation_mode="prompt_only",
        melody_take_band=melody_take_band,
    )


def _project_dir_with_registries(tmp_path: Path, *, calibrated: bool) -> Path:
    """`_melody_config()` の既定パス（`m3_comparison_registry.yaml` /
    `registry.yaml`、いずれも project_dir 直下）に一致するレジストリ一式を
    tmp_path 配下へ用意する（凍結 registry 実ファイルはコピーのみ・不改変）。"""
    project_dir = tmp_path / f"project_{'calibrated' if calibrated else 'uncalibrated'}"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    if calibrated:
        _frozen_m3_registry_path(project_dir)
    else:
        shutil.copy(REAL_M3_UNCALIBRATED_REGISTRY, project_dir / "m3_comparison_registry.yaml")
    return project_dir


# --------------------------------------------------------------------------- #
# M4c — resolve_melody_observation_paths
# --------------------------------------------------------------------------- #
def test_resolve_melody_observation_paths_score_reference(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    m3_path, m1_path, reference_audio_path = resolve_melody_observation_paths(
        project_dir=project_dir, melody_config=_melody_config()
    )
    assert m3_path == project_dir / "m3_comparison_registry.yaml"
    assert m1_path == project_dir / "registry.yaml"
    assert reference_audio_path is None


def test_resolve_melody_observation_paths_resolves_reference_audio(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    (project_dir / "ref.wav").write_bytes(b"fake-audio")
    _m3, _m1, reference_audio_path = resolve_melody_observation_paths(
        project_dir=project_dir,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
    )
    assert reference_audio_path == project_dir / "ref.wav"


def test_resolve_melody_observation_paths_rejects_missing_registry(tmp_path: Path) -> None:
    project_dir = tmp_path / "empty_project"
    project_dir.mkdir()
    with pytest.raises(RecastError):
        resolve_melody_observation_paths(project_dir=project_dir, melody_config=_melody_config())


def test_resolve_melody_observation_paths_rejects_traversal(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    config = MelodyObservationConfig(
        reference="score",
        comparison_registry="../outside.yaml",
        m1_registry="registry.yaml",
    )
    with pytest.raises(RecastError):
        resolve_melody_observation_paths(project_dir=project_dir, melody_config=config)


# --------------------------------------------------------------------------- #
# M4c — collect_melody_experimental_anchors
# --------------------------------------------------------------------------- #
def test_collect_returns_empty_when_contract_is_none() -> None:
    assert (
        collect_melody_experimental_anchors(
            contract=None,
            melody_config=None,
            project_dir=Path("."),
            backend_ref=_backend_ref(),
            score=None,
            channel_artifact_bytes={},
            take_audio_path=None,
        )
        == []
    )


def test_collect_returns_empty_when_no_melody_axis_policy_anchor() -> None:
    contract = _contract([_harmony_anchor(), _anchor(None, anchor_id="melody-nopolicy")])
    assert (
        collect_melody_experimental_anchors(
            contract=contract,
            melody_config=None,
            project_dir=Path("."),
            backend_ref=_backend_ref(),
            score=None,
            channel_artifact_bytes={},
            take_audio_path=None,
        )
        == []
    )


def test_collect_returns_melody_config_missing_entry_when_config_absent() -> None:
    contract = _contract([_anchor({"contour": "hard"})])
    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=None,
        project_dir=Path("."),
        backend_ref=_backend_ref(),
        score=_score(),
        channel_artifact_bytes={},
        take_audio_path=None,
    )
    assert len(entries) == 1
    assert entries[0].adherence_status == "not_observed"
    assert entries[0].reasons == ["melody_config_missing"]


def test_collect_evaluates_melody_anchor_end_to_end_and_ignores_non_melody_anchors(
    tmp_path: Path,
) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract(
        [_harmony_anchor(), _anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})]
    )
    take = _take_notes(_REFERENCE_PITCHES)
    take_audio_path = Path(_write_audio_file(tmp_path / "take.wav"))
    take_sha256 = hashlib.sha256(take_audio_path.read_bytes()).hexdigest()
    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        score=_score(bpm=60),
        channel_artifact_bytes={"melody-1": _reference_artifact_bytes()},
        take_audio_path=take_audio_path,
        take_sha256=take_sha256,
        route_runner=_make_route_runner(take),
    )
    # harmony anchor は無視され、melody anchor 1 件だけが翻訳される。
    assert len(entries) == 1
    assert entries[0].anchor_id == "melody-1"
    assert entries[0].adherence_status == "preserved"
    assert entries[0].provenance.get("melody_artifact_sha256") == VALID_SHA256


# --------------------------------------------------------------------------- #
# M4c — melody_experimental_plan_warnings
# --------------------------------------------------------------------------- #
def test_plan_warnings_empty_when_contract_is_none() -> None:
    assert (
        melody_experimental_plan_warnings(
            contract=None, melody_config=None, project_dir=Path("."), backend_ref=_backend_ref()
        )
        == []
    )


def test_plan_warnings_empty_when_no_melody_axis_policy_anchor() -> None:
    contract = _contract([_harmony_anchor()])
    assert (
        melody_experimental_plan_warnings(
            contract=contract,
            melody_config=None,
            project_dir=Path("."),
            backend_ref=_backend_ref(),
        )
        == []
    )


def test_plan_warnings_melody_config_missing() -> None:
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=None,
        project_dir=Path("."),
        backend_ref=_backend_ref(),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — not expected (melody_config_missing)"
    ]


def test_plan_warnings_comparator_uncalibrated(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=False)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — not expected (comparator_uncalibrated)"
    ]


def test_plan_warnings_band_out_of_validation(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(),  # melody_take_band 未宣言
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — not expected (band_out_of_validation)"
    ]


# --------------------------------------------------------------------------- #
# R2-6 (Codex round2 P2) — plan warnings に audio 参照側の G2 を含める
# --------------------------------------------------------------------------- #
def test_plan_warnings_band_out_of_validation_for_unset_reference_band(
    tmp_path: Path,
) -> None:
    """reference == "audio" でテイク側は校正済みでも原曲側 reference_band が
    未宣言なら band_out_of_validation を先出しする（実行時ゲート G2 の主因と
    一致させる——`test_audio_reference_without_reference_band_is_band_out_of_
    validation` の実行時挙動と対になる plan 時点の診断）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    (project_dir / "ref.wav").write_bytes(b"fake-audio")
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — not expected (band_out_of_validation)"
    ]


def test_plan_warnings_ok_for_audio_reference_when_both_bands_calibrated(
    tmp_path: Path,
) -> None:
    """reference == "audio" でテイク側・原曲側の両方が校正済み帯域なら "ok"。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    (project_dir / "ref.wav").write_bytes(b"fake-audio")
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(
            reference="audio", reference_audio="ref.wav", reference_band="clear_lead"
        ),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


def test_plan_warnings_ok_when_calibrated_and_band_declared(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


def test_plan_warnings_one_line_per_melody_anchor(tmp_path: Path) -> None:
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract(
        [
            _anchor({"contour": "hard"}, anchor_id="melody-a"),
            _anchor({"interval": "hard"}, anchor_id="melody-b"),
        ]
    )
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert len(warnings) == 2
    assert all(w.endswith("experimental observability — ok") for w in warnings)
    assert {w.split("'")[1] for w in warnings} == {"melody-a", "melody-b"}


# --------------------------------------------------------------------------- #
# R2-1 (Codex round2 P1・会計分離の実装漏れ) — melody_experimental_anchor_ids /
# resolve_main_observation_anchor_scope
# --------------------------------------------------------------------------- #
def test_melody_experimental_anchor_ids_empty_when_contract_is_none() -> None:
    assert melody_experimental_anchor_ids(None) == frozenset()


def test_melody_experimental_anchor_ids_only_axis_policy_melody_anchors() -> None:
    """domain!=melody・axis_policy 無しの melody anchor は集合に入らない
    ——DD-3 opt-in 判定そのもの。"""
    contract = _contract(
        [
            _harmony_anchor(),
            _anchor({"contour": "hard"}, anchor_id="melody-with-policy"),
            _anchor(None, anchor_id="melody-without-policy"),
        ]
    )
    assert melody_experimental_anchor_ids(contract) == frozenset({"melody-with-policy"})


def test_resolve_main_observation_anchor_scope_unchanged_when_no_melody_axis_policy() -> None:
    """除外対象が無ければ ``observation_anchor_scope`` をそのまま返す
    （``None`` は絞り込みなしの既存契約を保つ）。"""
    contract = _contract([_harmony_anchor()])
    assert (
        resolve_main_observation_anchor_scope(
            manifest_path=Path("/nonexistent/manifest.yaml"),
            contract=contract,
            observation_anchor_scope=None,
        )
        is None
    )
    assert resolve_main_observation_anchor_scope(
        manifest_path=Path("/nonexistent/manifest.yaml"),
        contract=contract,
        observation_anchor_scope={"harmony"},
    ) == frozenset({"harmony"})


def test_resolve_main_observation_anchor_scope_subtracts_from_explicit_scope() -> None:
    """既存の `observation.anchors` 絞り込み（非 None）がある場合は単純な集合差分。"""
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody")])
    result = resolve_main_observation_anchor_scope(
        manifest_path=Path("/nonexistent/manifest.yaml"),
        contract=contract,
        observation_anchor_scope={"melody", "harmony"},
    )
    assert result == frozenset({"harmony"})


def test_resolve_main_observation_anchor_scope_reads_manifest_when_scope_none(
    tmp_path: Path,
) -> None:
    """絞り込みなし（``observation_anchor_scope=None``）で除外対象がある場合は、
    manifest の生 anchor id 集合を読み取り、除外差分を明示的な inclusion set
    へ変換する（`arrange/observe.py` の `anchor_scope` は inclusion-only の
    ため）。"""
    manifest_path = tmp_path / "identity.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema": "identity-manifest/0.1",
                "anchors": [
                    {"id": "melody"},
                    {"id": "harmony"},
                    {"id": "structure"},
                ],
            }
        ),
        encoding="utf-8",
    )
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody")])
    result = resolve_main_observation_anchor_scope(
        manifest_path=manifest_path,
        contract=contract,
        observation_anchor_scope=None,
    )
    assert result == frozenset({"harmony", "structure"})


def test_resolve_main_observation_anchor_scope_defers_to_caller_on_malformed_manifest(
    tmp_path: Path,
) -> None:
    """manifest 構造が壊れている場合はここで判定せず、
    `observe_generated_artifact` 自身の fail-closed 検証へ委ねるため素通しする
    （``observation_anchor_scope`` をそのまま返す——ここでは ``None``）。"""
    manifest_path = tmp_path / "broken.yaml"
    manifest_path.write_text("not-a-mapping-just-a-string\n", encoding="utf-8")
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody")])
    result = resolve_main_observation_anchor_scope(
        manifest_path=manifest_path,
        contract=contract,
        observation_anchor_scope=None,
    )
    assert result is None
