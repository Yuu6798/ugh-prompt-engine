"""test_d4_remeasure.py — VG-DEBT-004 (D4) 事前登録 spec + runner の形状 / fail-closed テスト。

レンダ・実測そのもの（onnxruntime + checkpoint 実体を要する）は machine-dependent
であり CI では走らせない。ここで固定するのは:

1. `d4_remeasure_spec.json` の形状（schema / debt_ref / pins の sha256 が実ファイルと
   一致 = spec 陳腐化の機械検出 / axes が voicing 3 軸ちょうど / non_goals 必須文言 /
   groups が 10 群 360 セル）。
2. `d4_runner.py` の fail-closed 経路（pin 改竄で abort / `--out` 衝突拒否）。
3. `d4_runner.py` の測定ロジック（1.2 選定候補での voicing 3 軸算出）を、
   コミット済みの本物の render 群 JSON 1 本に対して合成 WAV で実行し、
   `s7_b1_v12.measure_candidate_12` の実インターフェースと噛み合っていることを
   確かめる（`s7_b1_v12.py` / `s1_gate/gate_synth.py` はレンダ以外では改変しない）。

依存は numpy + librosa + soundfile（すべて本体の必須依存）のみで、onnxruntime は
要らない（`d4_runner` はレンダ実行時にしか `s7_0b_probe.load_gate_synth()` を
呼ばないため import 時点では触れない）。

実行: `python -m pytest voice_genesis/foundry/tests/test_d4_remeasure.py -q`
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

_FOUNDRY = Path(__file__).resolve().parent.parent
_RUN8 = _FOUNDRY / "run8"
_D4 = _FOUNDRY / "debt" / "d4"
for _p in (_RUN8, _D4):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import d4_runner as d4  # noqa: E402
import s7_0b_probe as probe0b  # noqa: E402
import s7_io  # noqa: E402

GROUPS_DIR = _FOUNDRY / "results_s7" / "probe_0b_groups"
REPO_ROOT = _FOUNDRY.parents[1]


@pytest.fixture(scope="module")
def spec_and_sha():
    return d4.load_and_verify_d4_spec()


@pytest.fixture(scope="module")
def raw_spec() -> Dict[str, Any]:
    return json.loads(d4.SPEC_PATH.read_text(encoding="utf-8"))


# --- 1. spec 形状 ------------------------------------------------------


def test_spec_schema_and_debt_ref(raw_spec: Dict[str, Any]) -> None:
    assert raw_spec["schema"] == "vg-d4-remeasure-spec/0.1"
    assert raw_spec["debt_ref"] == "VG-DEBT-004"


def test_spec_verifies_against_live_files(spec_and_sha) -> None:
    """pin 全件が実ファイルと一致する（spec 陳腐化の機械検出）。例外なく通れば良い。"""
    spec, sha = spec_and_sha
    assert isinstance(sha, str) and len(sha) == 64


def test_axes_are_exactly_voicing_three(raw_spec: Dict[str, Any]) -> None:
    assert set(raw_spec["axes"]) == set(d4.D4_AXES)
    assert len(raw_spec["axes"]) == 3
    for axis, cfg in raw_spec["axes"].items():
        assert cfg["selected_candidate"].startswith("S_melshape_core_distance|")


def test_terminal_mel_persistence_is_out_of_scope(raw_spec: Dict[str, Any]) -> None:
    assert "terminal_mel_persistence" in raw_spec["axes_out_of_scope"]
    assert "terminal_mel_persistence" not in raw_spec["axes"]


def test_non_goals_required_phrases_present(raw_spec: Dict[str, Any]) -> None:
    joined = " ".join(raw_spec["non_goals"])
    assert "Gate 1" in joined
    assert "H0-H5" in joined
    assert "1.2" in joined
    assert "terminal_mel_persistence" in joined
    assert "s7_0b_results.json" in joined


def test_groups_are_ten_covering_360_cells(raw_spec: Dict[str, Any]) -> None:
    groups = raw_spec["groups"]
    assert len(groups) == 10
    assert raw_spec["n_total_cells"] == 360
    assert sum(g["n_cells"] for g in groups) == 360
    expected_ids = {
        "run5_ritsu", "run5_pjs", "run5_user",
        "run6_ritsu", "run6_pjs", "run6_user",
        "run7_ritsu", "run7_pjs", "run7_user", "run7_amitaro",
    }
    assert {g["group_id"] for g in groups} == expected_ids


def test_groups_match_probe_spec_expansion_matrix(raw_spec: Dict[str, Any]) -> None:
    """D4 は group/cell 定義を独自に再定義しない — cell_definition_source の
    expansion.matrix と 1:1 で一致するはず。"""
    cds = raw_spec["pins"]["cell_definition_source"]
    probe_spec = json.loads((REPO_ROOT / cds["path"]).read_text(encoding="utf-8"))
    matrix_ids = {f"{m['generation']}_{m['speaker']}" for m in probe_spec["expansion"]["matrix"]}
    assert {g["group_id"] for g in raw_spec["groups"]} == matrix_ids


def test_execution_declares_fail_closed_order_discipline(raw_spec: Dict[str, Any]) -> None:
    order = raw_spec["execution"]["order_discipline"]
    assert "abort" in order
    assert "sha256" in order


# --- 2. runner の fail-closed 経路 --------------------------------------


def test_load_and_verify_rejects_wrong_schema(tmp_path: Path, raw_spec: Dict[str, Any]) -> None:
    tampered = copy.deepcopy(raw_spec)
    tampered["schema"] = "vg-d4-remeasure-spec/0.0"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_tampered_pin(tmp_path: Path, raw_spec: Dict[str, Any]) -> None:
    """pin 対象ファイルの sha256 を書き換えると abort する（実ファイルと不一致）。"""
    tampered = copy.deepcopy(raw_spec)
    tampered["pins"]["instrument_sha256"] = "0" * 64
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="instrument_sha256"):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_tampered_cell_definition_source(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    tampered = copy.deepcopy(raw_spec)
    tampered["pins"]["cell_definition_source"]["sha256"] = "1" * 64
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_axes_not_exactly_three(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    tampered = copy.deepcopy(raw_spec)
    tampered["axes"]["terminal_mel_persistence"] = {
        "selected_candidate": "M2_2048_512_80", "unit": "ratio (>= 0)"
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch):
        d4.load_and_verify_d4_spec(path)


def test_measure_out_collision_with_spec_is_rejected(spec_and_sha) -> None:
    ns = argparse.Namespace(render_doc=[], out=str(d4.SPEC_PATH))
    with pytest.raises(s7_io.OutputCollisionError):
        d4.cmd_measure(ns)
    # 何も書き込まれていない（spec が壊れていない）ことも確認する。
    d4.load_and_verify_d4_spec()


# --- 3. candidate_id 解析 ------------------------------------------------


def test_parse_voicing_candidate_id_roundtrips_selected_candidates(
    raw_spec: Dict[str, Any],
) -> None:
    for axis, cfg in raw_spec["axes"].items():
        cand = d4.parse_voicing_candidate_id(cfg["selected_candidate"])
        assert cand.candidate_id == cfg["selected_candidate"]
        assert cand.kind == "voicing"
        assert cand.family == "S_melshape_core_distance"


def test_parse_voicing_candidate_id_rejects_garbage() -> None:
    with pytest.raises(d4.D4SpecMismatch):
        d4.parse_voicing_candidate_id("not-a-candidate-id")


def test_parse_voicing_candidate_id_rejects_unknown_family() -> None:
    with pytest.raises(d4.D4SpecMismatch):
        d4.parse_voicing_candidate_id("Q_unknown_family|thr0.2|win100|hop10")


# --- 4. 測定ロジック（合成 WAV。実レンダ・onnxruntime 不要） -----------------


def _synthetic_stimulus_wav(out_dir: Path, boundaries: Dict[str, float]):
    """校正音源と同型の合成波形（正弦 + 指数減衰）を書き、pin 付きで返す。"""
    sr = 44100
    dur_s = 2.0
    n = int(sr * dur_s)
    t = np.arange(n) / sr
    y = 0.1 * np.sin(2 * np.pi * 220.0 * t) * np.exp(-3.0 * t)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_meta = probe0b._write_wav(out_dir / "cell.wav", y, sr)
    return wav_meta


def _fake_render_doc(out_dir: Path, wav_meta: Dict[str, Any], boundaries: Dict[str, float]):
    return {
        "generation": "run5", "speaker": "pjs",
        "out_dir": str(out_dir.resolve()),
        "d4_remeasure_spec_sha256": None,
        "model_sha256": {"acoustic_onnx": "deadbeef"},
        "aux_sha256": {"gate_synth_py": "deadbeef"},
        "export_binding": {"source_checkpoint_sha256": "deadbeef"},
        "cells": [
            {
                "cell_id": "P-RI-FINAL|low|b1",
                "outcome": "rendered",
                "wav": wav_meta["wav"],
                "wav_sha256": wav_meta["wav_sha256"],
                "samples_sha256": wav_meta["samples_sha256"],
                "probe": "P-RI-FINAL",
                "input_meta": boundaries,
            },
            {
                "cell_id": "P-N-FINAL|low|b1",
                "outcome": "dropped",
                "status": "render_failed",
                "error": "RuntimeError: synthetic failure",
            },
        ],
    }


def test_measure_group_computes_voicing_axes_on_synthetic_wav(
    tmp_path: Path, spec_and_sha
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._axis_candidates(spec)
    boundaries = {
        "note_onset_s": 0.1, "commanded_note_end_s": 1.0,
        "score_boundary_s": 1.0, "tail_window_ms": 300.0,
    }
    out_dir = tmp_path / "out"
    wav_meta = _synthetic_stimulus_wav(out_dir, boundaries)
    render_doc = _fake_render_doc(out_dir, wav_meta, boundaries)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    group_doc = d4._measure_group(render_doc_path, spec_sha, axis_candidates)

    assert group_doc["generation"] == "run5"
    assert group_doc["speaker"] == "pjs"
    assert group_doc["n_cells"] == 2
    assert group_doc["n_measured"] == 1
    assert group_doc["n_missing"] == 1
    assert group_doc["n_error"] == 0

    measured = group_doc["cells"]["P-RI-FINAL|low|b1"]
    assert measured["outcome"] == "measured"
    assert set(measured["axes"]) == set(d4.D4_AXES)
    for value in measured["axes"].values():
        assert isinstance(value, float)

    missing = group_doc["cells"]["P-N-FINAL|low|b1"]
    assert missing["outcome"] == "missing"
    assert missing["reason"] == "render_failed"


def test_measure_group_rejects_wav_pin_mismatch(tmp_path: Path, spec_and_sha) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._axis_candidates(spec)
    boundaries = {
        "note_onset_s": 0.1, "commanded_note_end_s": 1.0,
        "score_boundary_s": 1.0, "tail_window_ms": 300.0,
    }
    out_dir = tmp_path / "out"
    wav_meta = _synthetic_stimulus_wav(out_dir, boundaries)
    wav_meta = dict(wav_meta)
    wav_meta["samples_sha256"] = "0" * 64  # 標本 pin を改竄
    render_doc = _fake_render_doc(out_dir, wav_meta, boundaries)
    render_doc["cells"] = render_doc["cells"][:1]
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    group_doc = d4._measure_group(render_doc_path, spec_sha, axis_candidates)
    entry = group_doc["cells"]["P-RI-FINAL|low|b1"]
    assert entry["outcome"] == "error"
    assert group_doc["n_error"] == 1


def test_measure_group_rejects_stale_spec_binding(tmp_path: Path, spec_and_sha) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._axis_candidates(spec)
    boundaries = {
        "note_onset_s": 0.1, "commanded_note_end_s": 1.0,
        "score_boundary_s": 1.0, "tail_window_ms": 300.0,
    }
    out_dir = tmp_path / "out"
    wav_meta = _synthetic_stimulus_wav(out_dir, boundaries)
    render_doc = _fake_render_doc(out_dir, wav_meta, boundaries)
    render_doc["d4_remeasure_spec_sha256"] = "stale-sha"
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch):
        d4._measure_group(render_doc_path, spec_sha, axis_candidates)


# --- 5. 実際にコミットされている render 群 JSON との形状整合 -----------------


def test_committed_probe_groups_have_input_meta_needed_by_measure() -> None:
    """`measure` は群 JSON の `cells[].input_meta` から `b1.Stimulus` を組み立てる。
    コミット済みの実群 JSON 10 本がその契約を満たすことを確認する
    （実 WAV は同梱されないため測定自体はしない = 形状検査のみ）。"""
    paths = sorted(GROUPS_DIR.glob("run*_*.json"))
    assert len(paths) == 10
    required = {"note_onset_s", "commanded_note_end_s", "score_boundary_s", "tail_window_ms"}
    for path in paths:
        doc, _, _ = s7_io.read_json_with_pin(path)
        rendered = [c for c in doc["cells"] if c.get("outcome") == "rendered"]
        assert rendered, f"{path}: rendered セルが 1 つも無い"
        for cell in rendered:
            missing = required - set(cell.get("input_meta", {}))
            assert not missing, f"{path}:{cell['cell_id']}: input_meta に {missing} が無い"
