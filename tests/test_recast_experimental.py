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
import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml
from pydantic import ValidationError

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
    resolve_melody_observation_paths_for_protection,
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
# R5-2 (Codex round5 P2 対応) — ExperimentalAnchorEntry の読み戻し不変条件
# --------------------------------------------------------------------------- #
def _entry_kwargs(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(
        adherence_status="preserved",
        anchor_id="melody-1",
        domain="melody",
        axis_policy={"contour": "hard", "interval": "elastic"},
        axes={"contour": 0.9, "interval": 0.9},
        axis_evidence={"contour": "strong", "interval": "strong"},
        coverage={
            "aligned_note_fraction_a": 1.0,
            "aligned_note_fraction_b": 1.0,
            "phrase_coverage_a": 1.0,
            "phrase_coverage_b": 1.0,
        },
        octave_artifact_suspected=False,
        reasons=[],
        provenance={},
    )
    base.update(overrides)
    return base


def test_entry_readback_accepts_consistent_preserved_entry() -> None:
    """builder（`map_axis_policy_to_adherence`）が実際に出す組み合わせ
    （全 hard/elastic 軸 strong → preserved）は読み戻し validator を素通しする
    ——builder/validator が同じ純関数 `_axis_policy_status` を共有する
    single source であることの直接確認。"""
    entry = ExperimentalAnchorEntry(**_entry_kwargs())
    assert entry.adherence_status == "preserved"


def test_entry_readback_rejects_preserved_with_weak_hard_axis() -> None:
    """矛盾 entry（``adherence_status="preserved"`` なのに hard 軸が
    ``weak`` evidence）は model_validate 時に拒否される——手編集/別経路の
    report がこの矛盾を valid として通すのを防ぐ（R5-2 裁定）。"""
    with pytest.raises(ValidationError, match="does not match the status recomputed"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="preserved",
                axis_evidence={"contour": "weak", "interval": "strong"},
            )
        )


def test_entry_readback_rejects_changed_outside_policy_when_all_axes_strong() -> None:
    """逆方向の矛盾（axis_evidence 的には preserved のはずが
    ``changed_outside_policy`` を主張）も同様に拒否される。"""
    with pytest.raises(ValidationError, match="does not match the status recomputed"):
        ExperimentalAnchorEntry(**_entry_kwargs(adherence_status="changed_outside_policy"))


def test_entry_readback_accepts_changed_within_policy_when_elastic_axis_weak() -> None:
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(
            adherence_status="changed_within_policy",
            axis_evidence={"contour": "strong", "interval": "weak"},
        )
    )
    assert entry.adherence_status == "changed_within_policy"


def test_entry_readback_exempts_not_observed_from_recomputation() -> None:
    """``not_observed`` は再計算免除——短絡 entry は axis_evidence が空でも
    （`_not_observed_entry` の実際の出力どおり）読み戻しが拒否されない。"""
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(adherence_status="not_observed", axis_evidence={}, reasons=["melody_config_missing"])
    )
    assert entry.adherence_status == "not_observed"


# --------------------------------------------------------------------------- #
# R9-1 (Codex round9 P2 対応) — 読み戻し時の axis_policy shape 再検証
# --------------------------------------------------------------------------- #
def test_entry_readback_rejects_empty_axis_policy_even_when_not_observed() -> None:
    """旧実装は ``not_observed`` を丸ごと再計算免除していたため、
    ``axis_policy={}``（契約として空洞化した shape）+ ``axis_evidence={}`` の
    組み合わせが `_axis_policy_status` に到達する前に何の shape 検証も受けず
    valid として通ってしまっていた——`arrange/contract.py::_validate_axis_policy`
    の共有ヘルパーで shape を再適用し、``not_observed`` でも拒否する。"""
    with pytest.raises(ValidationError, match="axis_policy must not be empty"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="not_observed",
                axis_policy={},
                axes={},
                axis_evidence={},
                reasons=["melody_config_missing"],
            )
        )


def test_entry_readback_rejects_all_free_axis_policy_even_when_not_observed() -> None:
    """全軸 ``free`` は「何も約束しない anchor」であり契約として空洞化する
    ——``not_observed`` entry でも拒否する（R9-1）。"""
    with pytest.raises(
        ValidationError, match="must declare at least one 'hard' or 'elastic'"
    ):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="not_observed",
                axis_policy={"contour": "free", "interval": "free"},
                axes={"contour": None, "interval": None},
                axis_evidence={},
                reasons=["melody_config_missing"],
            )
        )


def test_entry_readback_rejects_axis_policy_referencing_unknown_axis() -> None:
    """domain の軸語彙（``DOMAIN_AXIS_VOCAB["melody"]``）外の軸名を持つ
    axis_policy は shape 検証で拒否する（R9-1）。"""
    with pytest.raises(ValidationError, match="outside the domain's vocabulary"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="not_observed",
                axis_policy={"not_a_real_axis": "hard"},
                axes={"not_a_real_axis": None},
                axis_evidence={},
                reasons=["melody_config_missing"],
            )
        )


def test_entry_readback_rejects_axis_policy_for_domain_without_vocab() -> None:
    """`DOMAIN_AXIS_VOCAB` に軸語彙が登録されていない domain（例: ``harmony``）
    への axis_policy 指定は shape 検証で拒否する（R9-1）。"""
    with pytest.raises(ValidationError, match="no axis vocabulary registered"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="not_observed",
                domain="harmony",
                axis_policy={"contour": "hard"},
                axes={"contour": None},
                axis_evidence={},
                reasons=["melody_config_missing"],
            )
        )


def test_entry_readback_accepts_builder_produced_not_observed_entry() -> None:
    """`_not_observed_entry` が実際に組む shape（axis_policy は anchor 由来で
    非空・hard/elastic ≥1・語彙内）は shape 検証後も引き続き読み戻しに成功する
    ——R9-1 は builder 産 entry の読み戻しを壊さない。"""
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(
            adherence_status="not_observed",
            axis_policy={"contour": "hard", "interval": "elastic"},
            axes={"contour": None, "interval": None},
            axis_evidence={},
            reasons=["melody_config_missing"],
        )
    )
    assert entry.adherence_status == "not_observed"


def test_entry_axis_policy_rejects_unknown_mode_literal() -> None:
    """axis_policy の値は ``hard``/``elastic``/``free`` の Literal 語彙に限定
    される——未知の値は pydantic の型検証で拒否される（R5-2 の型面）。"""
    with pytest.raises(ValidationError):
        ExperimentalAnchorEntry(**_entry_kwargs(axis_policy={"contour": "bogus"}))


def test_entry_axis_evidence_rejects_unknown_verdict_literal() -> None:
    """axis_evidence の値は ``strong``/``weak``/``none``/``uncalibrated`` の
    Literal 語彙に限定される（R5-2 の型面）。"""
    with pytest.raises(ValidationError):
        ExperimentalAnchorEntry(**_entry_kwargs(axis_evidence={"contour": "bogus", "interval": "strong"}))


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
    # R4-2 (Codex round4 P2 対応): G1 短絡は M1 registry ロードより前に短絡する
    # ため、M1 は一切読まれない——「判明分のみ・捏造禁止」の原則どおり
    # provenance に m1_registry_sha256 は載らない（旧仕様は G1 判定前に M1 を
    # 読んでいたため常に載っていた）。
    assert "m1_registry_sha256" not in entry.provenance


# --------------------------------------------------------------------------- #
# R7-1 (Codex round7 P2 対応) — M3 loader 失敗の RecastError 翻訳
# --------------------------------------------------------------------------- #
def _broken_m3_registry_path(tmp_path: Path, *, alignment_value: Any, name: str) -> Path:
    """``alignment`` 節を壊した m3_comparison_registry.yaml を生成する
    （R7-1）。`M3ComparisonConfig.from_registry` 内部の
    ``dict(mapping["alignment"])`` が非 mapping 値で `TypeError` を送出する
    経路を再現する（他の節は `_frozen_m3_registry_path` と同じ有効な frozen
    形にしておく——``alignment`` の破損だけを単離する）。"""
    mapping = dict(_REGISTRY_BASE)
    mapping["alignment"] = alignment_value
    mapping["evidence_thresholds"] = {
        "status": "frozen",
        "axes": {axis: {"strong_min": 0.8, "none_max": 0.3} for axis in ("contour", "interval", "rhythm")},
    }
    path = tmp_path / name
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return path


def test_m3_registry_alignment_null_raises_recast_error(tmp_path: Path) -> None:
    """R7-1: ``alignment: null`` は `M3ComparisonConfig.from_registry` 内部の
    ``dict(mapping["alignment"])`` で `TypeError` を送出していた——旧実装は
    `_load_m3_registry_with_gate` がこれをそのまま逃がし、CLI ハンドラ
    （ValueError/RecastError 捕捉）を素通りして traceback していた。
    actionable な `RecastError` に翻訳されることを確認する。"""
    m3_path = _broken_m3_registry_path(
        tmp_path, alignment_value=None, name="alignment_null_registry.yaml"
    )
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="failed to load"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
        )


def test_m3_registry_alignment_scalar_raises_recast_error(tmp_path: Path) -> None:
    """R7-1: ``alignment: 1``（スカラー）も同様に `TypeError` → `RecastError`。"""
    m3_path = _broken_m3_registry_path(
        tmp_path, alignment_value=1, name="alignment_scalar_registry.yaml"
    )
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="failed to load"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
        )


def test_m3_registry_non_mapping_section_raises_recast_error(tmp_path: Path) -> None:
    """R7-1: 節が非 mapping（リスト）でも同様に `TypeError` → `RecastError`
    （``alignment`` に限らず「非 mapping 節」一般の経路を確認する）。"""
    m3_path = _broken_m3_registry_path(
        tmp_path, alignment_value=[1, 2, 3], name="alignment_list_registry.yaml"
    )
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="failed to load"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            m3_registry_path=m3_path,
            m1_registry_path=REAL_M1_REGISTRY,
        )


# --------------------------------------------------------------------------- #
# R5-1 (Codex round5 P2 対応) — パス解決も G1 短絡に従わせる
# --------------------------------------------------------------------------- #
def test_g1_short_circuit_precedes_missing_m1_registry(tmp_path: Path) -> None:
    """M3 未校正（G1 不成立）+ M1 registry パス欠落の組み合わせでも、G1 が
    最優先短絡するため raise せず ``not_observed(comparator_uncalibrated)`` へ
    落ちる（旧実装は `resolve_melody_observation_paths` が M1 の実在まで
    先行検証していたため、ここで `RecastError` になっていた）。"""
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(),
        m3_registry_path=REAL_M3_UNCALIBRATED_REGISTRY,
        m1_registry_path=tmp_path / "missing_registry.yaml",
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["comparator_uncalibrated"]
    assert "m1_registry_sha256" not in entry.provenance


def test_calibrated_registry_with_missing_m1_registry_raises_recast_error(
    tmp_path: Path,
) -> None:
    """M3 校正済み（G1 成立）+ M1 registry パス欠落は、G1 通過後の
    `_load_m1_registry`（step 6）で `RecastError` になる——設定/インフラ欠陥は
    ここまで到達して初めて大声で失敗するのが正しい（R4-2 裁定と同線）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="could not be read"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=tmp_path / "missing_registry.yaml",
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


def test_audio_reference_with_nonexistent_reference_audio_file_is_author_input_missing(
    tmp_path: Path,
) -> None:
    """R5-1: `reference_audio_path` が（require_exists=False 解決後などで）
    実在しないファイルを指していても、``author_input_missing`` へ倒れる
    （raise しない）——実在検証は step 4 のこの一箇所に集約する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        reference_audio_path=str(tmp_path / "does_not_exist.wav"),
    )
    assert entry.adherence_status == "not_observed"
    assert entry.reasons == ["author_input_missing"]


def test_collect_g1_short_circuit_when_m1_and_reference_audio_files_missing(
    tmp_path: Path,
) -> None:
    """R5-1: `collect_melody_experimental_anchors` 経由でも、m3 registry が
    未校正 + m1_registry/reference_audio の実ファイルが project 内に存在しない
    という組み合わせで raise せず、G1 短絡の not_observed へ落ちる（旧実装は
    `resolve_melody_observation_paths` の先行解決で `RecastError` になって
    いた）。project_dir には comparison_registry のみ用意し、m1_registry/
    reference_audio は意図的に欠落させる（`_project_dir_with_registries` は
    使わず、config が指す 3 参照のうち m3 のみ実ファイルを置く）。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M3_UNCALIBRATED_REGISTRY, project_dir / "m3_comparison_registry.yaml")
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        score=_score(),
        channel_artifact_bytes={},
        take_audio_path=None,
    )
    assert len(entries) == 1
    assert entries[0].adherence_status == "not_observed"
    assert entries[0].reasons == ["comparator_uncalibrated"]


# --------------------------------------------------------------------------- #
# R2-2 (Codex round2 P2) — M1 registry の構造検証 fail-closed
# --------------------------------------------------------------------------- #
def test_m1_registry_empty_yaml_raises_recast_error(tmp_path: Path) -> None:
    """空 YAML（`yaml.safe_load` は `None` を返す）を素通しすると直後の
    `mapping["observation_gate"]` が `TypeError` を未捕捉のまま送出していた
    ——actionable な `RecastError` に変換されることを確認する。

    R4-2 (Codex round4 P2 対応): M1 ロードは G1〜G2/axis_policy/author_input
    を通過した run のみが到達する（6 の抽出直前）ため、これらのゲートを
    実際に通す最小限の引数（melody_artifact_bytes/melody_take_band/
    take_audio_path）を渡す——M1 registry の RecastError は抽出（route_runner
    呼び出し）より前で送出されるため、``take_audio_path`` はファイルとして
    実在しなくてよい（ダミー文字列で足りる）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "empty_registry.yaml"
    m1_path.write_text("", encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="must be a YAML mapping"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


def test_m1_registry_scalar_yaml_raises_recast_error(tmp_path: Path) -> None:
    """トップレベルがスカラー（マッピングでない）YAML も同様に fail-closed。
    （R4-2: M1 ロードは 6 まで遅延するため G1〜G2 を通す引数が必要——上記
    `test_m1_registry_empty_yaml_raises_recast_error` docstring 参照）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "scalar_registry.yaml"
    m1_path.write_text("just-a-string\n", encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="must be a YAML mapping"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


def test_m1_registry_missing_observation_gate_section_raises_recast_error(
    tmp_path: Path,
) -> None:
    """`observation_gate` 節が丸ごと欠落した YAML も fail-closed。
    （R4-2: M1 ロードは 6 まで遅延するため G1〜G2 を通す引数が必要——
    `test_m1_registry_empty_yaml_raises_recast_error` docstring 参照）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "no_gate_registry.yaml"
    m1_path.write_text(yaml.safe_dump({"unrelated": {"a": 1}}), encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="observation_gate"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


# --------------------------------------------------------------------------- #
# R3-4 (Codex round3 P2) — M1 registry フィールドレベル検証も RecastError へ翻訳
# --------------------------------------------------------------------------- #
def test_m1_registry_empty_observation_gate_mapping_raises_recast_error(
    tmp_path: Path,
) -> None:
    """R2-2 の構造検証（top-level / `observation_gate` 節の存在）は
    `observation_gate: {}` を通過させてしまい、直後の
    `ObservabilityThresholds.from_registry` の必須引数欠落 `TypeError` が
    未捕捉のまま observed 記録後に traceback していた——ここで actionable な
    `RecastError` に翻訳されることを確認する。（R4-2: M1 ロードは 6 まで
    遅延するため G1〜G2 を通す引数が必要——
    `test_m1_registry_empty_yaml_raises_recast_error` docstring 参照）"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "empty_gate_registry.yaml"
    m1_path.write_text(yaml.safe_dump({"observation_gate": {}}), encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="observation_gate"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


def test_m1_registry_unknown_field_in_observation_gate_raises_recast_error(
    tmp_path: Path,
) -> None:
    """`observation_gate` に事前登録外のキー（型不正な設定値）があると
    `ObservabilityThresholds.from_registry` 自身が `ValueError` を送出する
    ——これも同じ try/except で `RecastError` へ翻訳されることを確認する。
    （R4-2: M1 ロードは 6 まで遅延するため G1〜G2 を通す引数が必要——
    `test_m1_registry_empty_yaml_raises_recast_error` docstring 参照。未知
    キー `not_a_real_field` は R4-3 の型検証（既知フィールドのみ対象）では
    捕捉されず、`from_registry` 自身の pre-registration 検査が引き続き
    捕捉する——R4-3 の境界宣言どおり分業を維持）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "unknown_field_registry.yaml"
    gate = {
        "min_voiced_coverage": 0.5,
        "min_note_count": 4,
        "min_phrase_count": 1,
        "min_confidence_mean": 0.5,
        "max_low_confidence_rate": 0.5,
        "max_octave_jump_rate": 0.5,
        "voiced_confidence_floor": 0.3,
        "low_confidence_floor": 0.1,
        "not_a_real_field": "oops",
    }
    m1_path.write_text(yaml.safe_dump({"observation_gate": gate}), encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="observation_gate"):
        evaluate_melody_experimental_anchor(
            anchor=anchor,
            melody_config=_melody_config(),
            score=_score(),
            melody_artifact_bytes=_reference_artifact_bytes(),
            m3_registry_path=m3_path,
            m1_registry_path=m1_path,
            melody_take_band="clear_lead",
            take_audio_path="take.wav",
        )


# --------------------------------------------------------------------------- #
# R4-3 (Codex round4 P2・部分採用+境界宣言) — M1 registry の型/有限性検証
# --------------------------------------------------------------------------- #
def _valid_observation_gate() -> Dict[str, Any]:
    """`ObservabilityThresholds` の必須フィールドのみを満たす、他は既定値の
    最小 valid `observation_gate` マッピング。テストが 1 フィールドだけを
    不正値へ差し替えるためのベース。"""
    return {
        "min_voiced_coverage": 0.5,
        "min_note_count": 4,
        "min_phrase_count": 1,
        "min_confidence_mean": 0.5,
        "max_low_confidence_rate": 0.5,
        "max_octave_jump_rate": 0.5,
        "voiced_confidence_floor": 0.3,
        "low_confidence_floor": 0.1,
    }


def _write_m1_registry(tmp_path: Path, name: str, gate: Dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"observation_gate": gate}), encoding="utf-8")
    return path


def _evaluate_with_m1_registry(anchor: ContractAnchor, m3_path: Path, m1_path: Path) -> ExperimentalAnchorEntry:
    """R4-2 対応後、M1 ロードは 6（抽出直前）まで遅延する——G1〜G2/axis_policy/
    author_input を通す最小限の引数を渡す（`take_audio_path` は M1 の
    RecastError が抽出より前に送出されるためファイル実在不要）。"""
    return evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(),
        score=_score(),
        melody_artifact_bytes=_reference_artifact_bytes(),
        m3_registry_path=m3_path,
        m1_registry_path=m1_path,
        melody_take_band="clear_lead",
        take_audio_path="take.wav",
    )


def test_m1_registry_string_value_raises_recast_error(tmp_path: Path) -> None:
    """文字列値は後段 `assess_observability` で未捕捉 `TypeError` になって
    いた——ここで actionable な `RecastError` に変換されることを確認する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    gate = _valid_observation_gate()
    gate["min_voiced_coverage"] = "not-a-number"
    m1_path = _write_m1_registry(tmp_path, "string_value_registry.yaml", gate)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="min_voiced_coverage"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_nan_value_raises_recast_error(tmp_path: Path) -> None:
    """NaN はゲートを静かに弱める（比較が常に False になる）——`math.isfinite`
    検証で `RecastError` として拒否されることを確認する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    gate = _valid_observation_gate()
    gate["min_confidence_mean"] = float("nan")
    m1_path = _write_m1_registry(tmp_path, "nan_value_registry.yaml", gate)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="min_confidence_mean"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_inf_value_raises_recast_error(tmp_path: Path) -> None:
    """Infinity も同じ有限性検証で拒否される。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    gate = _valid_observation_gate()
    gate["max_low_confidence_rate"] = float("inf")
    m1_path = _write_m1_registry(tmp_path, "inf_value_registry.yaml", gate)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="max_low_confidence_rate"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_float_value_for_int_field_raises_recast_error(tmp_path: Path) -> None:
    """int 系フィールド（`note_min_run_frames` 等）は float を許容しない
    （「bool でない int」を要求する——float は型違反）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    gate = _valid_observation_gate()
    gate["min_note_count"] = 4.5
    m1_path = _write_m1_registry(tmp_path, "float_int_field_registry.yaml", gate)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="min_note_count"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_bool_value_for_int_field_raises_recast_error(tmp_path: Path) -> None:
    """``bool`` は Python では ``int`` のサブクラス（``isinstance(True, int)
    is True``）——素の ``isinstance`` 判定だけでは ``True``/``False`` を
    フレーム数として受理してしまう。明示的に bool を除外していることを
    確認する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    gate = _valid_observation_gate()
    gate["note_min_run_frames"] = True
    m1_path = _write_m1_registry(tmp_path, "bool_int_field_registry.yaml", gate)
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="note_min_run_frames"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_optional_field_none_is_accepted(tmp_path: Path) -> None:
    """`min_cross_extractor_agreement`（唯一の Optional フィールド）に
    明示的な ``None``（YAML の ``null``）を宣言しても型検証で拒否されない
    ——`_load_m1_registry` を直接呼び、例外なくロードできることを確認する
    （`evaluate_melody_experimental_anchor` 経由だと抽出器 seam まで進んで
    しまい、型検証だけを独立に確認できないため）。"""
    gate = _valid_observation_gate()
    gate["min_cross_extractor_agreement"] = None
    m1_path = _write_m1_registry(tmp_path, "optional_none_registry.yaml", gate)

    from svp_rpe.recast import experimental as experimental_module

    thresholds, sha256_hex = experimental_module._load_m1_registry(m1_path)
    assert thresholds.min_cross_extractor_agreement is None
    assert isinstance(sha256_hex, str) and len(sha256_hex) == 64


def test_m1_registry_out_of_semantic_range_value_is_not_rejected_by_type_validation(
    tmp_path: Path,
) -> None:
    """R4-3 の境界宣言: フィールド別の値域・意味論検証（例: rate ∈ [0, 1]）は
    本 loader の対象外——型として妥当な負値は `_load_m1_registry` の型検証を
    通過する（M1 registry の統治側の責務であり、ここで再実装しない）。"""
    gate = _valid_observation_gate()
    gate["min_voiced_coverage"] = -1.0  # 型としては妥当な float、意味論的には範囲外。
    m1_path = _write_m1_registry(tmp_path, "negative_value_registry.yaml", gate)
    # `_load_m1_registry` 自体は RecastError を送出しない——`_load_m1_registry`
    # を直接呼んで型検証のみを確認する（G3/extractor 挙動には立ち入らない）。
    from svp_rpe.recast import experimental as experimental_module

    thresholds, sha256_hex = experimental_module._load_m1_registry(m1_path)
    assert thresholds.min_voiced_coverage == -1.0
    assert isinstance(sha256_hex, str) and len(sha256_hex) == 64


# --------------------------------------------------------------------------- #
# R7-2 (Codex round7 P2 対応) — M1 YAML の重複キー拒否
# --------------------------------------------------------------------------- #
def test_m1_registry_observation_gate_duplicate_key_raises_recast_error(
    tmp_path: Path,
) -> None:
    """R7-2: 素の `yaml.safe_load` は重複 mapping キーを「最後の値が勝つ」形で
    静かに受理してしまい、矛盾バイトが正当な sha256 つきでゲート
    （``observation_gate`` の閾値群）を変え得ていた——M3 側
    （`melody.representation._NoDupSafeLoader`・import のみ・重複実装禁止）を
    使うようになったことで、``observation_gate`` 節内の重複閾値キーが
    `RecastError` として拒否されることを確認する。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "duplicate_threshold_key_registry.yaml"
    m1_path.write_text(
        "observation_gate:\n"
        "  min_voiced_coverage: 0.5\n"
        "  min_voiced_coverage: 0.9\n"
        "  min_note_count: 4\n"
        "  min_phrase_count: 1\n"
        "  min_confidence_mean: 0.5\n"
        "  max_low_confidence_rate: 0.5\n"
        "  max_octave_jump_rate: 0.5\n"
        "  voiced_confidence_floor: 0.3\n"
        "  low_confidence_floor: 0.1\n",
        encoding="utf-8",
    )
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="duplicate YAML mapping key"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


def test_m1_registry_top_level_duplicate_key_raises_recast_error(tmp_path: Path) -> None:
    """R7-2: トップレベルで ``observation_gate`` 節そのものが重複していても
    同様に拒否される（last-wins で先行節が静かに隠れる穴を弾く）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    m1_path = tmp_path / "duplicate_top_level_key_registry.yaml"
    gate_yaml = yaml.safe_dump(
        {"observation_gate": _valid_observation_gate()}, sort_keys=False
    )
    m1_path.write_text(gate_yaml + gate_yaml, encoding="utf-8")
    anchor = _anchor({"contour": "hard"})
    with pytest.raises(RecastError, match="duplicate YAML mapping key"):
        _evaluate_with_m1_registry(anchor, m3_path, m1_path)


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
    G2 を通過できない——DD-10b 追加前と同じ安全側の帰結（回帰確認）。

    R5-1 (Codex round5 P2 対応): step 4 の author-input チェックが
    ``reference_audio_path`` の実在を検証するようになったため、G2（本テストの
    対象）まで到達させるには実在するファイルを渡す必要がある（`_write_audio_
    file` — 実在検証そのものは `test_audio_reference_with_nonexistent_
    reference_audio_file_is_author_input_missing` が別途担保する）。"""
    m3_path = _frozen_m3_registry_path(tmp_path)
    anchor = _anchor({"contour": "hard"})
    entry = evaluate_melody_experimental_anchor(
        anchor=anchor,
        melody_config=_melody_config(reference="audio", reference_audio="ref.wav"),
        score=_score(),
        m3_registry_path=m3_path,
        m1_registry_path=REAL_M1_REGISTRY,
        melody_take_band="clear_lead",
        reference_audio_path=_write_audio_file(tmp_path / "ref.wav"),
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
# R6-1 (Codex round6 P2 対応) — resolve_melody_observation_paths_for_protection
# --------------------------------------------------------------------------- #
def test_resolve_for_protection_does_not_raise_on_missing_registry(tmp_path: Path) -> None:
    """未存在パスでも例外を送出しない（require_exists=False・封じ込め検証の
    みが働く）——protected-input 収集は未存在パスを保護対象に含めても無害。"""
    project_dir = tmp_path / "empty_project"
    project_dir.mkdir()

    m3_path, m1_path, reference_audio_path = resolve_melody_observation_paths_for_protection(
        project_dir=project_dir, melody_config=_melody_config()
    )

    assert m3_path == project_dir / "m3_comparison_registry.yaml"
    assert m1_path == project_dir / "registry.yaml"
    assert reference_audio_path is None


def test_resolve_for_protection_returns_none_for_containment_violation_without_raising(
    tmp_path: Path,
) -> None:
    """1 パス（``comparison_registry``）が封じ込め違反（traversal）でも、例外を
    送出せず当該パスだけ ``None`` を返す——他パス（``m1_registry``）の解決は
    道連れにしない（R6-1 の独立解決保証）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    config = MelodyObservationConfig(
        reference="score",
        comparison_registry="../outside.yaml",
        m1_registry="registry.yaml",
    )

    m3_path, m1_path, reference_audio_path = resolve_melody_observation_paths_for_protection(
        project_dir=project_dir, melody_config=config
    )

    assert m3_path is None
    assert m1_path == project_dir / "registry.yaml"
    assert reference_audio_path is None


def test_resolve_for_protection_returns_none_for_missing_audio_reference_containment_violation(
    tmp_path: Path,
) -> None:
    """``reference_audio`` が封じ込め違反でも m3/m1 の解決は影響を受けない。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    config = MelodyObservationConfig(
        reference="audio",
        reference_audio="/etc/passwd",
        reference_band="clear_lead",
        comparison_registry="m3_comparison_registry.yaml",
        m1_registry="registry.yaml",
    )

    m3_path, m1_path, reference_audio_path = resolve_melody_observation_paths_for_protection(
        project_dir=project_dir, melody_config=config
    )

    assert m3_path == project_dir / "m3_comparison_registry.yaml"
    assert m1_path == project_dir / "registry.yaml"
    assert reference_audio_path is None


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
# R3-1 (Codex round3 P2) — collect_melody_experimental_anchors のスコープ整合
# --------------------------------------------------------------------------- #
def test_collect_skips_melody_anchor_outside_non_empty_scope(tmp_path: Path) -> None:
    """非空 `observation_anchor_scope` が melody anchor を含まない場合、
    experimental 節は空（節ごと省略）で、CREPE 抽出（route_runner）も一切
    呼ばれない——main が `anchor_scope` を尊重するのに experimental だけが
    未要求の行を評価・公開してしまう非対称を防ぐ。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract(
        [_anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})]
    )

    def _unexpected_runner(_path: str) -> Any:
        raise AssertionError("route_runner must not be called when anchor is out of scope")

    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        score=_score(bpm=60),
        channel_artifact_bytes={"melody-1": _reference_artifact_bytes()},
        take_audio_path=Path(_write_audio_file(tmp_path / "take.wav")),
        route_runner=_unexpected_runner,
        observation_anchor_scope={"harmony-1"},
    )
    assert entries == []


def test_collect_evaluates_melody_anchor_within_non_empty_scope(tmp_path: Path) -> None:
    """非空スコープに melody anchor id が含まれていれば従来どおり評価する。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract(
        [_anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})]
    )
    take = _take_notes(_REFERENCE_PITCHES)
    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        score=_score(bpm=60),
        channel_artifact_bytes={"melody-1": _reference_artifact_bytes()},
        take_audio_path=Path(_write_audio_file(tmp_path / "take.wav")),
        route_runner=_make_route_runner(take),
        observation_anchor_scope={"melody-1"},
    )
    assert len(entries) == 1
    assert entries[0].adherence_status == "preserved"


def test_collect_empty_scope_means_unrestricted_like_main(tmp_path: Path) -> None:
    """空集合スコープは main と同じ「絞り込みなし」の意味論——全 axis_policy
    melody anchor を評価する（既定 `None` と同じ挙動）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract(
        [_anchor({"contour": "hard", "interval": "elastic", "rhythm": "elastic"})]
    )
    take = _take_notes(_REFERENCE_PITCHES)
    entries = collect_melody_experimental_anchors(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        score=_score(bpm=60),
        channel_artifact_bytes={"melody-1": _reference_artifact_bytes()},
        take_audio_path=Path(_write_audio_file(tmp_path / "take.wav")),
        route_runner=_make_route_runner(take),
        observation_anchor_scope=set(),
    )
    assert len(entries) == 1


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
# R3-1 (Codex round3 P2) — plan warnings のスコープ整合（診断パリティ）
# --------------------------------------------------------------------------- #
def test_plan_warnings_skips_melody_anchor_outside_non_empty_scope(tmp_path: Path) -> None:
    """`collect_melody_experimental_anchors` と同じスコープ規則を warnings 側
    にも適用する——非空スコープ外の melody anchor は plan 診断にも出さない
    （実行時に experimental 節から省略される anchor について、plan 時点で
    無関係な「observability 見込み」行を出さない・診断パリティ維持）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        observation_anchor_scope={"harmony-1"},
    )
    assert warnings == []


def test_plan_warnings_empty_scope_means_unrestricted_like_main(tmp_path: Path) -> None:
    """空集合スコープは「絞り込みなし」——既定 `None` と同じく全 melody
    anchor を診断する。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
        observation_anchor_scope=set(),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


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


# --------------------------------------------------------------------------- #
# R9-2 (Codex round9 P2 対応) — plan 診断で audio 参照の実在を検査
# --------------------------------------------------------------------------- #
def test_plan_warnings_flags_author_input_missing_when_reference_audio_file_absent(
    tmp_path: Path,
) -> None:
    """G1/G2（+M1 可用性）を通過しても、``reference == "audio"`` の参照ファイル
    が実在しなければ plan は ``not expected (author_input_missing)`` を先出し
    する——旧実装はここで参照ファイルの実在を一切見ておらず ``ok`` を返して
    いた一方、実行時 (`evaluate_melody_experimental_anchor` step 4) は同じ
    入力から決定論的に ``not_observed(author_input_missing)`` に落ちていた
    （runtime パリティの欠落）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    # 意図的に project_dir 配下へ ref.wav を用意しない。
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(
            reference="audio", reference_audio="ref.wav", reference_band="clear_lead"
        ),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — "
        "not expected (author_input_missing)"
    ]


def test_plan_warnings_ok_when_reference_audio_file_present(tmp_path: Path) -> None:
    """参照ファイルが実在すれば（他ゲートも通過する前提で）"ok" のまま——回帰
    確認（`test_plan_warnings_ok_for_audio_reference_when_both_bands_calibrated`
    と同型だが、本テストは R9-2 の実在検査を明示的な観点として持つ）。"""
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


def test_plan_warnings_score_reference_unaffected_by_audio_check(tmp_path: Path) -> None:
    """``reference == "score"``（既定）は R9-2 の audio 参照実在検査の対象外
    ——回帰確認（既存 ok 挙動を壊さない）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


# --------------------------------------------------------------------------- #
# R8-2 (Codex round8 P2) — G1 通過後の M1 registry 可用性診断
# --------------------------------------------------------------------------- #
def test_plan_warnings_flags_m1_registry_missing_after_g1_pass(tmp_path: Path) -> None:
    """校正済み M3（G1 通過）+ M1 registry ファイル欠落の組では、旧実装は
    存在検証を実行時ゲートまで遅延させていたため plan は "ok" を返し、ingest
    は決定論的に `observation_incomplete` に落ちる不一致があった——plan も
    `not expected (m1_registry_unavailable: ...)` を先出しする。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    (project_dir / "registry.yaml").unlink()
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "melody anchor 'melody-1': experimental observability — "
        "not expected (m1_registry_unavailable:"
    )


def test_plan_warnings_flags_m1_registry_missing_observation_gate_section(
    tmp_path: Path,
) -> None:
    """M1 registry が実在しても構造破損（`observation_gate` 節欠落）なら同様に
    `m1_registry_unavailable` を先出しする（`_load_m1_registry` の構造検証を
    そのまま再利用——知識の重複なし）。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    (project_dir / "registry.yaml").write_bytes(
        yaml.safe_dump({"schema_version": "melody-bench/0.1"}).encode("utf-8")
    )
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "melody anchor 'melody-1': experimental observability — "
        "not expected (m1_registry_unavailable:"
    )
    assert "observation_gate" in warnings[0]


def test_plan_warnings_flags_m1_registry_duplicate_key(tmp_path: Path) -> None:
    """M1 registry の `observation_gate` 節が重複キーを持つ（R7-2 の重複キー
    拒否ロジックが送出する `RecastError`）場合も同様に先出しする。"""
    project_dir = _project_dir_with_registries(tmp_path, calibrated=True)
    duplicate_key_yaml = (
        "observation_gate:\n"
        "  min_voiced_coverage: 0.30\n"
        "  min_note_count: 8\n"
        "  min_phrase_count: 2\n"
        "  min_confidence_mean: 0.45\n"
        "  max_low_confidence_rate: 0.55\n"
        "  max_octave_jump_rate: 0.35\n"
        "  voiced_confidence_floor: 0.30\n"
        "  low_confidence_floor: 0.50\n"
        "  min_cross_extractor_agreement: null\n"
        "  note_min_run_frames: 3\n"
        "  median_filter_frames: 5\n"
        "  phrase_gap_sec: 0.6\n"
        "  octave_jump_semitones: 11.0\n"
        "  octave_jump_semitones: 12.0\n"  # 重複キー
    )
    (project_dir / "registry.yaml").write_text(duplicate_key_yaml, encoding="utf-8")
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "melody anchor 'melody-1': experimental observability — "
        "not expected (m1_registry_unavailable:"
    )


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
# R5-3 (Codex round5 P2 対応) — plan 診断で未校正軸指定を検出
# --------------------------------------------------------------------------- #
def test_plan_warnings_flags_axis_policy_uncalibrated_axes_for_partial_frozen_registry(
    tmp_path: Path,
) -> None:
    """frozen registry が部分軸（``contour`` のみ）しか校正していないとき、
    anchor の axis_policy が ``interval`` を hard/elastic で指定していれば、
    G1（全体としては "frozen"）自体は通過しても plan 診断がその軸差分を
    検出して警告する——旧実装は ``ok`` を返し、実行時 `evaluate_melody_
    experimental_anchor` の axis_policy 検証（fail-closed・``RecastError``）で
    初めて判明していた。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    _frozen_m3_registry_path(project_dir, axes=("contour",))
    contract = _contract(
        [_anchor({"contour": "hard", "interval": "elastic"}, anchor_id="melody-1")]
    )
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — "
        "not expected (axis_policy_uncalibrated_axes: ['interval'])"
    ]


def test_plan_warnings_ok_when_axis_policy_is_subset_of_partial_frozen_axes(
    tmp_path: Path,
) -> None:
    """anchor の axis_policy（hard/elastic 軸）が frozen axes の部分集合に
    収まっていれば、frozen registry が部分軸のみでも従来どおり "ok"。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    _frozen_m3_registry_path(project_dir, axes=("contour", "interval"))
    contract = _contract([_anchor({"contour": "hard"}, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


def test_plan_warnings_axis_policy_uncalibrated_axes_per_anchor(tmp_path: Path) -> None:
    """複数 anchor がいる場合、軸差分の診断は anchor ごとに独立する
    （一方が uncalibrated 軸を指しても、他方の診断には影響しない）。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    _frozen_m3_registry_path(project_dir, axes=("contour",))
    contract = _contract(
        [
            _anchor({"contour": "hard"}, anchor_id="melody-ok"),
            _anchor({"interval": "hard"}, anchor_id="melody-bad"),
        ]
    )
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    by_anchor = {w.split("'")[1]: w for w in warnings}
    assert by_anchor["melody-ok"].endswith("— ok")
    assert by_anchor["melody-bad"].endswith("axis_policy_uncalibrated_axes: ['interval'])")


# --------------------------------------------------------------------------- #
# R6-2 (Codex round6 P2 対応) — plan 軸突合を全軸キー集合に（runtime とのパリティ）
# --------------------------------------------------------------------------- #
def test_plan_warnings_flags_uncalibrated_free_axis_for_runtime_parity(tmp_path: Path) -> None:
    """R6-2: axis_policy が ``rhythm: free`` のみ frozen axes 集合外を指すとき、
    plan 診断は（R5-3 の hard/elastic 限定突合と異なり）``rhythm`` も対象に
    含め ``not expected`` を出す——runtime `evaluate_melody_experimental_anchor`
    の axis_policy 検証は hard/elastic/free を問わず axis_policy の全キーを
    frozen axes と突合して ``RecastError`` にするため、plan が hard/elastic
    のみ突合していると ``contour: hard`` は frozen で ``ok`` を返す一方、
    ingest は ``rhythm`` の未校正で fail する不一致が生じていた。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    _frozen_m3_registry_path(project_dir, axes=("contour",))
    contract = _contract(
        [_anchor({"contour": "hard", "rhythm": "free"}, anchor_id="melody-1")]
    )
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == [
        "melody anchor 'melody-1': experimental observability — "
        "not expected (axis_policy_uncalibrated_axes: ['rhythm'])"
    ]


def test_plan_warnings_ok_when_free_axis_is_also_calibrated(tmp_path: Path) -> None:
    """R6-2: free 軸も frozen axes 集合に含まれていれば従来どおり ``ok``
    （全軸突合への変更が既存の "全 axis が frozen" ケースを壊さないことの
    回帰確認）。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    _frozen_m3_registry_path(project_dir, axes=("contour", "rhythm"))
    contract = _contract(
        [_anchor({"contour": "hard", "rhythm": "free"}, anchor_id="melody-1")]
    )
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=_melody_config(),
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert warnings == ["melody anchor 'melody-1': experimental observability — ok"]


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


# --------------------------------------------------------------------------- #
# R10-2 (Codex round10 P2 対応) — plan 診断順序 = runtime のゲート順（表駆動）
#
# runtime `evaluate_melody_experimental_anchor` の実際のゲート順（1 設定不在
# → 2 G1 → 3 axis_policy vs frozen axes → 4 参照入力 → 5 G2 帯域 → 6 M1
# ロード → ... → 8 写像）に plan 診断の順序を揃えたことを、複数要因を同時に
# 破壊した組み合わせで確認する——1 要因ずつのケースは R5-3/R8-2/R9-2 が既に
# 個別に担保しているが、本テーブルは「どちらが先に報告されるか」という順序
# そのものを検証する（旧実装は M1 可用性/参照入力を axis_policy より先に
# 見ていたため、(a)/(b) のような組み合わせで誤った診断を先出ししていた）。
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "anchor_axis_policy", "reference", "reference_audio_present", "m1_present", "expected"),
    [
        pytest.param(
            "uncalibrated_axis_and_broken_m1",
            {"contour": "hard", "interval": "elastic"},
            "score",
            None,
            False,
            "not expected (axis_policy_uncalibrated_axes: ['interval'])",
            id="a-uncalibrated-axis-precedes-m1",
        ),
        pytest.param(
            "audio_missing_and_broken_m1",
            {"contour": "hard"},
            "audio",
            False,
            False,
            "not expected (author_input_missing)",
            id="b-audio-missing-precedes-m1",
        ),
        pytest.param(
            "only_m1_broken",
            {"contour": "hard"},
            "score",
            None,
            False,
            "not expected (m1_registry_unavailable:",
            id="c-m1-only-broken",
        ),
        pytest.param(
            "all_gates_pass",
            {"contour": "hard"},
            "score",
            None,
            True,
            "ok",
            id="d-all-ok",
        ),
    ],
)
def test_plan_diagnosis_order_matches_runtime_gate_order(
    tmp_path: Path,
    label: str,
    anchor_axis_policy: Dict[str, str],
    reference: str,
    reference_audio_present: Optional[bool],
    m1_present: bool,
    expected: str,
) -> None:
    """(a) 未校正軸（``interval`` が frozen 対象外）+ M1 破損の組では、旧実装は
    M1 可用性を先に見て ``m1_registry_unavailable`` を報告していた（ユーザーを
    M1 側の誤った修正へ誘導する）が、runtime は axis_policy 検証（M1 を読む前）
    で先に ``RecastError`` になるため、plan も
    ``axis_policy_uncalibrated_axes`` を報告すべき（M1 側の理由を報告しない）。

    (b) audio 参照欠落 + M1 破損の組では、axis_policy が通過した後、runtime は
    参照入力チェック（M1 ロードより前）で ``author_input_missing`` に落ちる
    ため、plan も同じ理由を報告すべき（M1 側の理由を報告しない）。

    (c) M1 のみ破損（他ゲートは全通過）では、M1 可用性チェックまで到達し
    ``m1_registry_unavailable`` を報告する（R8-2 の回帰確認）。

    (d) 全ゲート通過では ``ok``。"""
    project_dir = tmp_path / f"project_{label}"
    project_dir.mkdir()
    _frozen_m3_registry_path(project_dir, axes=("contour",))
    if m1_present:
        shutil.copy(REAL_M1_REGISTRY, project_dir / "registry.yaml")
    # m1_present=False の場合、registry.yaml を意図的に用意しない
    # （`_load_m1_registry` が実在検証で `RecastError` を送出する経路）。

    if reference == "audio":
        if reference_audio_present:
            (project_dir / "ref.wav").write_bytes(b"fake-audio")
        melody_config = _melody_config(
            reference="audio", reference_audio="ref.wav", reference_band="clear_lead"
        )
    else:
        melody_config = _melody_config()

    contract = _contract([_anchor(anchor_axis_policy, anchor_id="melody-1")])
    warnings = melody_experimental_plan_warnings(
        contract=contract,
        melody_config=melody_config,
        project_dir=project_dir,
        backend_ref=_backend_ref(melody_take_band="clear_lead"),
    )
    assert len(warnings) == 1
    assert warnings[0].startswith(
        f"melody anchor 'melody-1': experimental observability — {expected}"
    )


# --------------------------------------------------------------------------- #
# R10-3 (Codex round10 P2 対応) — readback validator: 確定 status では判定
# 参加軸（hard/elastic）に有限数値を要求する
# --------------------------------------------------------------------------- #
def test_entry_readback_rejects_preserved_with_none_value_for_judged_axis() -> None:
    """(a) ``preserved`` + judged 軸（``contour``）の ``axes[axis] is None`` は
    ``axis_evidence`` の再計算一致（R5-2）だけを見ていた旧 validator を素通り
    していた矛盾 entry——``contour=-`` と描画されつつ保存を主張する report を
    拒否する。"""
    with pytest.raises(ValidationError, match="must carry a finite numeric value"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="preserved",
                axes={"contour": None, "interval": 0.9},
            )
        )


def test_entry_readback_rejects_preserved_with_nan_value_for_judged_axis() -> None:
    """(b) judged 軸の値が NaN でも同様に拒否される
    （``None`` と同じ「有限数値でない」不変条件違反）。"""
    with pytest.raises(ValidationError, match="must carry a finite numeric value"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="preserved",
                axes={"contour": float("nan"), "interval": 0.9},
            )
        )


def test_entry_readback_rejects_preserved_with_inf_value_for_judged_axis() -> None:
    """(b) judged 軸の値が +inf でも同様に拒否される。"""
    with pytest.raises(ValidationError, match="must carry a finite numeric value"):
        ExperimentalAnchorEntry(
            **_entry_kwargs(
                adherence_status="changed_within_policy",
                axis_evidence={"contour": "strong", "interval": "weak"},
                axes={"contour": 0.9, "interval": float("inf")},
            )
        )


def test_entry_readback_accepts_confirmed_status_with_missing_free_axis_value() -> None:
    """(c) free 軸（判定不参加）の値欠落は確定 status でも許容される——
    judged 軸（hard/elastic）のみが対象で free 軸には本チェックが適用され
    ない。"""
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(
            adherence_status="preserved",
            axis_policy={"contour": "hard", "interval": "elastic", "rhythm": "free"},
            axes={"contour": 0.9, "interval": 0.9},  # rhythm は丸ごと欠落
            axis_evidence={"contour": "strong", "interval": "strong"},
        )
    )
    assert entry.adherence_status == "preserved"


def test_entry_readback_accepts_confirmed_status_with_none_free_axis_value() -> None:
    """(c) free 軸の値が明示的に ``None`` でも同様に許容される
    （欠落だけでなく明示 ``None`` も本チェック対象外）。"""
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(
            adherence_status="preserved",
            axis_policy={"contour": "hard", "interval": "elastic", "rhythm": "free"},
            axes={"contour": 0.9, "interval": 0.9, "rhythm": None},
            axis_evidence={"contour": "strong", "interval": "strong"},
        )
    )
    assert entry.adherence_status == "preserved"


def test_entry_readback_exempts_not_observed_from_judged_axis_finite_check() -> None:
    """(d) ``not_observed`` は本チェックからも免除される——judged 軸の値が
    軒並み ``None`` でも読み戻しが拒否されない（R9-1 の
    `test_entry_readback_accepts_builder_produced_not_observed_entry` と同型の
    確認だが、本テストは R10-3 チェックの免除を明示的な観点として持つ）。"""
    entry = ExperimentalAnchorEntry(
        **_entry_kwargs(
            adherence_status="not_observed",
            axis_policy={"contour": "hard", "interval": "elastic"},
            axes={"contour": None, "interval": None},
            axis_evidence={},
            reasons=["melody_config_missing"],
        )
    )
    assert entry.adherence_status == "not_observed"


def test_entry_readback_accepts_builder_produced_preserved_entry_with_finite_axes() -> None:
    """(e) builder（`map_axis_policy_to_adherence` 経路）が実際に組む
    preserved entry（全 judged 軸が有限数値を持つ）は読み戻しを引き続き通過
    する——R10-3 が正規経路の entry を拒否しないことの回帰確認
    （`test_entry_readback_accepts_consistent_preserved_entry` と同型だが、
    本テストは新チェック導入後も正常系が壊れていないことを明示する）。"""
    entry = ExperimentalAnchorEntry(**_entry_kwargs())
    assert entry.adherence_status == "preserved"
    assert entry.axes["contour"] is not None and math.isfinite(entry.axes["contour"])
    assert entry.axes["interval"] is not None and math.isfinite(entry.axes["interval"])
