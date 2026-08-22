"""test_d4_remeasure.py — VG-DEBT-004 (D4) 事前登録 spec + runner の形状 / fail-closed テスト。

レンダ・実測そのもの（onnxruntime + checkpoint 実体を要する）は machine-dependent
であり CI では走らせない。ここで固定するのは:

1. `d4_remeasure_spec.json` の形状（schema / debt_ref / pins の sha256 が実ファイルと
   一致 = spec 陳腐化の機械検出 / axes が voicing 3 軸ちょうど / non_goals 必須文言 /
   groups が 10 群 360 セル）。
2. `d4_runner.py` の fail-closed 経路（pin 改竄で abort / pins キー集合の欠落で abort /
   `--spec-sha256` の期待値照合 / `--out` 衝突拒否 / render 群 JSON の事前登録照合
   〔群 ID・36 セル規定数・cell_id 集合の欠落・重複〕）。
3. `d4_runner.py` の測定ロジック（`enumerate_candidates_12` 経由の候補解決 + 1.2
   選定候補での voicing 3 軸算出）を、合成 WAV に対して実行し、
   `s7_b1_v12.measure_candidate_12` の実インターフェースと噛み合っていることを
   確かめる。**セル毎に軸値が実際に異なることの回帰テストを含む**（セルフレビュー
   #1: 測定キャッシュがセルをまたいで使い回され、全セルが 1 セル目の値を再利用
   していた致命バグの再発防止）。
4. セル単位の測定失敗が隔離され、他セルの測定が完走したうえで `d4_results.json`
   が全セル分書き切られ、exit が非ゼロになること（セルフレビュー #6）。

（`s7_b1_v12.py` / `s1_gate/gate_synth.py` はレンダ以外では改変しない。）

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
from typing import Any, Dict, List, Optional

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
import s7_b1_v12 as v12  # noqa: E402
import s7_io  # noqa: E402

GROUPS_DIR = _FOUNDRY / "results_s7" / "probe_0b_groups"
REPO_ROOT = _FOUNDRY.parents[1]

BOUNDARIES = {
    "note_onset_s": 0.1, "commanded_note_end_s": 1.0,
    "score_boundary_s": 1.0, "tail_window_ms": 300.0,
}


@pytest.fixture(scope="module")
def spec_and_sha():
    return d4.load_and_verify_d4_spec()


@pytest.fixture(scope="module")
def raw_spec() -> Dict[str, Any]:
    return json.loads(d4.SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def probe_spec_doc(raw_spec: Dict[str, Any]) -> Dict[str, Any]:
    cds = raw_spec["pins"]["cell_definition_source"]
    return json.loads((REPO_ROOT / cds["path"]).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_cell_ids(probe_spec_doc: Dict[str, Any]) -> List[str]:
    return [str(c["cell_id"]) for c in probe_spec_doc["cells"]]


@pytest.fixture(scope="module")
def valid_group_ids(raw_spec: Dict[str, Any]):
    return frozenset(g["group_id"] for g in raw_spec["groups"])


@pytest.fixture(scope="module")
def expected_cell_ids(all_cell_ids: List[str]):
    return frozenset(all_cell_ids)


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


def test_all_cell_ids_count_is_36(all_cell_ids: List[str]) -> None:
    assert len(all_cell_ids) == 36
    assert len(set(all_cell_ids)) == 36


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


def test_load_and_verify_rejects_axis_candidate_diverging_from_frozen_1_2(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    """D4 spec の selected_candidate が凍結済み trf_measurement_spec_1_2.json と
    逐語一致しないと abort する（セルフレビュー #3: spec 側 typo 検出）。"""
    tampered = copy.deepcopy(raw_spec)
    tampered["axes"]["tail_f0_persistence"]["selected_candidate"] = (
        "S_melshape_core_distance|thr0.2|win100|hop5"  # 本来は hop10
    )
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="typo"):
        d4.load_and_verify_d4_spec(path)


@pytest.mark.parametrize("drop_key", ["instrument_sha256", "cell_definition_source", "sources"])
def test_load_and_verify_rejects_missing_top_level_pin_key(
    tmp_path: Path, raw_spec: Dict[str, Any], drop_key: str
) -> None:
    """pins の必須キーが 1 つでも欠けたら abort する（セルフレビュー #5）。"""
    tampered = copy.deepcopy(raw_spec)
    del tampered["pins"][drop_key]
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="pins"):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_missing_pin_source_key(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    """`pins.sources` の 3 キーのうち 1 つが欠けても abort する（セルフレビュー #5）。"""
    tampered = copy.deepcopy(raw_spec)
    del tampered["pins"]["sources"]["render_harness_sha256"]
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="pins.sources"):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_extra_pin_key(tmp_path: Path, raw_spec: Dict[str, Any]) -> None:
    """pins に余剰キーが混じっても abort する（厳密一致・欠落だけでなく余剰も検査）。"""
    tampered = copy.deepcopy(raw_spec)
    tampered["pins"]["unexpected_extra_key"] = "deadbeef"
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="pins"):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_accepts_matching_operator_spec_sha256(spec_and_sha) -> None:
    """セルフレビュー #4: `--spec-sha256` 相当（`expected_sha256`）が実 sha256 と
    一致すれば通る。"""
    spec, sha = spec_and_sha
    spec2, sha2 = d4.load_and_verify_d4_spec(expected_sha256=sha)
    assert sha2 == sha


def test_load_and_verify_rejects_mismatching_operator_spec_sha256() -> None:
    """セルフレビュー #4: operator が渡した期待 sha256 が実ファイルと違えば abort。"""
    with pytest.raises(d4.D4SpecMismatch, match="spec-sha256"):
        d4.load_and_verify_d4_spec(expected_sha256="0" * 64)


def test_measure_out_collision_with_spec_is_rejected(spec_and_sha) -> None:
    spec, sha = spec_and_sha
    ns = argparse.Namespace(render_doc=[], out=str(d4.SPEC_PATH), spec_sha256=sha)
    with pytest.raises(s7_io.OutputCollisionError):
        d4.cmd_measure(ns)
    # 何も書き込まれていない（spec が壊れていない）ことも確認する。
    d4.load_and_verify_d4_spec()


# --- 3. 候補解決（enumerate_candidates_12 経由） -----------------------------


def test_resolve_axis_candidates_matches_enumerate_candidates_12(
    raw_spec: Dict[str, Any],
) -> None:
    """`s7_0b_remeasure_12.load_winners` と同じ方式（列挙からの id 一致）で
    解決していることを確認する。"""
    cands = d4._resolve_axis_candidates(raw_spec)
    cs, _cal, _rule, _pins = v12.load_prereg_12()
    by_id = {c.candidate_id: c for c in v12.enumerate_candidates_12(cs)}
    for axis, cfg in raw_spec["axes"].items():
        want_id = cfg["selected_candidate"]
        assert cands[axis] == by_id[want_id]
        assert cands[axis].candidate_id == want_id
        assert cands[axis].kind == "voicing"


def test_resolve_axis_candidates_rejects_unknown_candidate_id(raw_spec: Dict[str, Any]) -> None:
    tampered = copy.deepcopy(raw_spec)
    tampered["axes"]["tail_f0_persistence"]["selected_candidate"] = "not-a-real-candidate-id"
    with pytest.raises(d4.D4SpecMismatch, match="候補空間"):
        d4._resolve_axis_candidates(tampered)


# --- 4. 測定ロジック（合成 WAV。実レンダ・onnxruntime 不要） -----------------


def _write_wav(out_dir: Path, name: str, y: np.ndarray, sr: int = 44100) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return probe0b._write_wav(out_dir / name, y, sr)


def _sustained_tail_wave(sr: int = 44100, dur_s: float = 2.0) -> np.ndarray:
    """緩やかな減衰（有声のまま tail window に残る）。"""
    n = int(sr * dur_s)
    t = np.arange(n) / sr
    return 0.1 * np.sin(2 * np.pi * 220.0 * t) * np.exp(-0.5 * t)


def _hard_cut_wave(sr: int = 44100, dur_s: float = 2.0, cut_s: float = 1.0) -> np.ndarray:
    """`cut_s` 以降を完全な無音にする（tail window に有声が残らない）。"""
    n = int(sr * dur_s)
    t = np.arange(n) / sr
    y = 0.1 * np.sin(2 * np.pi * 220.0 * t) * np.exp(-3.0 * t)
    y[int(cut_s * sr):] = 0.0
    return y


def _full_render_doc(
    out_dir: Path, all_ids: List[str], rendered: Dict[str, Dict[str, Any]],
    *, generation: str = "run5", speaker: str = "pjs",
    spec_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """36 セル規定数を満たす render 群 JSON を組み立てる（`rendered` に入って
    いないセルは `dropped` として記帳する）。事前登録照合（セルフレビュー #2）
    は cell_id 集合の**厳密一致**を要求するため、テスト用の疑似 doc も実際の
    36 セル全部を持たせる。"""
    cells = []
    for cid in all_ids:
        if cid in rendered:
            entry = {
                "cell_id": cid, "outcome": "rendered",
                "probe": cid.split("|", 1)[0],
                "input_meta": BOUNDARIES,
            }
            entry.update(rendered[cid])
            cells.append(entry)
        else:
            cells.append({
                "cell_id": cid, "outcome": "dropped",
                "status": "render_failed", "error": "RuntimeError: not rendered in this test",
            })
    return {
        "generation": generation, "speaker": speaker,
        "out_dir": str(out_dir.resolve()),
        "d4_remeasure_spec_sha256": spec_sha256,
        "model_sha256": {"acoustic_onnx": "deadbeef"},
        "aux_sha256": {"gate_synth_py": "deadbeef"},
        "export_binding": {"source_checkpoint_sha256": "deadbeef"},
        "cells": cells,
    }


def test_measure_group_computes_voicing_axes_on_synthetic_wav(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = _write_wav(out_dir, "cell.wav", _sustained_tail_wave())
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: dict(wav_meta)})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
    )

    assert group_doc["generation"] == "run5"
    assert group_doc["speaker"] == "pjs"
    assert group_doc["n_cells"] == 36
    assert group_doc["n_measured"] == 1
    assert group_doc["n_missing"] == 35
    assert group_doc["n_error"] == 0

    measured = group_doc["cells"][target]
    assert measured["outcome"] == "measured"
    assert set(measured["axes"]) == set(d4.D4_AXES)
    for value in measured["axes"].values():
        assert isinstance(value, float)

    other_id = all_cell_ids[1]
    missing = group_doc["cells"][other_id]
    assert missing["outcome"] == "missing"
    assert missing["reason"] == "render_failed"


def test_measure_cell_axes_differ_between_distinct_wavs(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    """セルフレビュー #1（致命）の回帰テスト: キャッシュがセルをまたいで使い
    回されると、2 セル目以降が 1 セル目の軸値をそのまま再利用してしまう
    （実音源で再現: 300ms voiced tail のセル A の値が 0.9s カットのセル B に
    そのまま記帳された）。ここでは意図的に大きく異なる 2 つの合成 WAV
    （tail window に有声が残る波形 / commanded_note_end で完全に無音化する
    波形）を別々のセルへ割り当て、`_measure_group` を通した結果の軸値が
    実際に異なることを検査する — レビューの再現手順（群 JSON を通した測定）
    と同型にする。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    cell_a, cell_b = all_cell_ids[0], all_cell_ids[1]
    wav_a = _write_wav(out_dir, "a.wav", _sustained_tail_wave())
    wav_b = _write_wav(out_dir, "b.wav", _hard_cut_wave())
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {cell_a: dict(wav_a), cell_b: dict(wav_b)}
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
    )

    axes_a = group_doc["cells"][cell_a]["axes"]
    axes_b = group_doc["cells"][cell_b]["axes"]
    assert axes_a != axes_b, (
        "セル A / B の軸値が同一 — 測定キャッシュがセルをまたいで使い回されて"
        f"いる疑い（axes_a={axes_a}, axes_b={axes_b}）"
    )
    # 具体的な向き: sustain セルは tail window に有声が残るので
    # excess_tail_voiced_ms が cut セルより大きいはず。
    assert axes_a["excess_tail_voiced_ms"] > axes_b["excess_tail_voiced_ms"]


def test_measure_group_rejects_wav_pin_mismatch(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    wav_meta["samples_sha256"] = "0" * 64  # 標本 pin を改竄
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: wav_meta})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
    )
    entry = group_doc["cells"][target]
    assert entry["outcome"] == "error"
    assert entry["status"] == "error"
    assert entry["reason"]
    assert group_doc["n_error"] == 1


def test_measure_group_rejects_stale_spec_binding(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {target: wav_meta}, spec_sha256="stale-sha",
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
        )


def test_measure_group_registration_check_applies_even_without_spec_binding(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    """セルフレビュー #2: `d4_remeasure_spec_sha256` が None（= 生の 8-0b probe
    群 JSON をそのまま渡した想定）でも、群/セル数の事前登録照合は必ず通す。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    # 群 ID を D4 spec に無いものへ差し替える（speaker を捏造）。
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {target: wav_meta}, speaker="nonexistent_speaker",
        spec_sha256=None,
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch, match="groups"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
        )


def test_measure_group_rejects_wrong_cell_count(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    render_doc = _full_render_doc(out_dir, all_cell_ids, {})
    render_doc["cells"] = render_doc["cells"][:35]  # 1 セル欠落
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch, match="セル数"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
        )


def test_measure_group_rejects_duplicate_cell_id(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    render_doc = _full_render_doc(out_dir, all_cell_ids, {})
    # 1 セルを重複させ、代わりに末尾を落として総数は 36 のまま保つ。
    render_doc["cells"] = [render_doc["cells"][0]] + render_doc["cells"][:35]
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch, match="重複"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
        )


# --- 5. セル毎例外の隔離 + exit 非ゼロ（cmd_measure 経由の end-to-end） -------


def test_cmd_measure_isolates_broken_cell_and_exits_nonzero(
    tmp_path: Path, spec_and_sha, all_cell_ids,
) -> None:
    """セルフレビュー #6: 1 セルの入力破壊（wav 実体の欠落）が他セルの測定を
    止めず、結果に error セルとして記帳されたうえで `cmd_measure` が非ゼロを
    返す。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    ok_id, broken_id = all_cell_ids[0], all_cell_ids[1]
    ok_wav = dict(_write_wav(out_dir, "ok.wav", _sustained_tail_wave()))
    broken_wav = dict(_write_wav(out_dir, "broken.wav", _hard_cut_wave()))
    # レンダ済みと記帳しつつ、実体の wav ファイルを消して読み込み時に例外を
    # 起こす（旧実装は WavPinMismatch/PathEscapeError/KeyError/ValueError の
    # 狭いタプルしか捕まえておらず、この種の OSError 系は全滅を招いていた）。
    (out_dir / "broken.wav").unlink()
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {ok_id: ok_wav, broken_id: broken_wav},
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
    )
    rc = d4.cmd_measure(ns)

    assert rc != 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    group = doc["groups"]["run5_pjs"]
    assert group["n_error"] == 1
    assert group["n_measured"] == 1
    ok_entry = group["cells"][ok_id]
    assert ok_entry["outcome"] == "measured"
    broken_entry = group["cells"][broken_id]
    assert broken_entry["outcome"] == "error"
    assert broken_entry["status"] == "error"
    assert broken_entry["reason"]
    assert doc["n_total_error"] == 1


# --- 6. 実際にコミットされている render 群 JSON との形状整合 -----------------


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
