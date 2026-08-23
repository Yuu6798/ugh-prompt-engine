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
5. `cmd_render` の staging → atomic 公開（PR #306 レビュー第9巡 P1）。
   `probe0b.run_group` / `xm.verify_export_manifest` / `probe0b.load_gate_synth`
   を monkeypatch で差し替え、`cmd_render` 自体の制御フロー（衝突検査 →
   staging 作成 → run_group → doc 補正 → 一括公開）だけを実 checkpoint 実体
   なしに検査する（下記 onnxruntime 非依存の原則は維持する）。

（`s7_b1_v12.py` / `s1_gate/gate_synth.py` はレンダ以外では改変しない。）

依存は numpy + librosa + soundfile（すべて本体の必須依存）のみで、onnxruntime は
要らない（`d4_runner` はレンダ実行時にしか `s7_0b_probe.load_gate_synth()` を
呼ばないため import 時点では触れない。上記 5 の `cmd_render` テストも
`load_gate_synth` 自体を monkeypatch するため、この原則を破らない）。

実行: `python -m pytest voice_genesis/foundry/tests/test_d4_remeasure.py -q`
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import importlib.metadata as _md
import json
import sys
import types
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

# `d4_runner` は pre-bind 原則（PR #306 レビュー第2巡 P2・第3巡 P2）により、
# pinned module（`d4.b1` 等 = `s7_b1_calibration`/`s7_b1_v12`/
# `s7_export_manifest`/`s7_io`/`s7_trf`/`s7_0b_probe`）を import 時点では
# 束縛しない — pin 検証を通過した後に `load_and_verify_d4_spec()` の内部で
# だけ束縛する。しかも第3巡 P2 で「束縛前に既に sys.modules にある」と
# fail-closed で abort するようになった。本テストモジュールは下で
# `s7_0b_probe` / `s7_b1_v12` / `s7_io` を直接 import する（`_write_wav` 等の
# テストヘルパーとして使うため）が、**その import より前**に正規経路
# （`load_and_verify_d4_spec()`）を 1 回明示的に走らせて束縛させる —
# 束縛が先に済んでいれば、下の `import s7_io` 等は sys.modules のキャッシュを
# 再利用するだけで新たな import にならず、preloaded 判定に触れない。
# 収集時点でこれを行うことは、多数のテストが `d4.b1` / `d4.v12` を直接参照
# する前提を満たすこと（委譲先の fixture 順序に依存しない）に加え、collection
# 自体が実 spec の健全性の最初のスモークテストにもなる。
d4.load_and_verify_d4_spec()

import s7_0b_probe as probe0b  # noqa: E402
import s7_b1_v12 as v12  # noqa: E402
import s7_io  # noqa: E402

GROUPS_DIR = _FOUNDRY / "results_s7" / "probe_0b_groups"
REPO_ROOT = _FOUNDRY.parents[1]

BOUNDARIES = {
    "note_onset_s": 0.1, "commanded_note_end_s": 1.0,
    "score_boundary_s": 1.0, "tail_window_ms": 300.0,
}
#: `BOUNDARIES` の `score_boundary_s - note_onset_s`（= 0.9s）と厳密に一致する
#: (duration_beats, tempo_bpm) の組（PR #306 レビュー第3巡 P1: `_measure_group`
#: が測定窓を pinned 定義と照合するようになったため、既存の合成テストが使う
#: `BOUNDARIES` はどの実セルの pinned duration_beats/tempo_bpm とも一致しない
#: — ここでは「テストの合成入力と整合する見せかけの期待値」を作り、window
#: 検証そのものを直接検査する新規テストとは切り離す）。0.9 * 60 / 60 = 0.9。
_PERMISSIVE_DURATION_BEATS = 0.9
_PERMISSIVE_TEMPO_BPM = 60.0


def _real_boundaries_for(cid: str, probe_spec_doc: Dict[str, Any]) -> Dict[str, float]:
    """cell_id の pinned duration_beats/tempo_bpm（`s7_0b_probe_spec.json`）から、
    `_measure_group` の測定窓照合（PR #306 レビュー第3巡 P1）を実際に通す
    input_meta を組み立てる。`cmd_measure` 経由のテスト（`_load_window_
    expectations` が実 spec から window_expectations を計算する）は固定値の
    `BOUNDARIES` では通らないため、cell_id ごとに正しい値が要る。"""
    cell_defs = {c["cell_id"]: c for c in probe_spec_doc["cells"]}
    cdef = cell_defs[cid]
    duration_beats = float(cdef["duration_beats"])
    tempo_bpm = float(cdef["tempo_bpm"])
    onset = 0.1
    span = duration_beats * 60.0 / tempo_bpm
    return {
        "note_onset_s": onset, "commanded_note_end_s": onset + span,
        "score_boundary_s": onset + span, "tail_window_ms": 300.0,
    }


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_shas_for(render_doc_path: Path, group_id: str) -> Dict[str, str]:
    """`render_doc_path` の実バイト sha256 から、`_measure_group` の
    render_manifest digest 束縛（PR #306 第4巡 P1）を通す `manifest_shas` を
    組み立てる（`_measure_group` が直接受け取る dict 形。ファイルとしての
    manifest が要る `cmd_measure` 経由のテストは `_write_render_manifest` を
    使う）。"""
    return {group_id: _sha256_of(render_doc_path)}


def _write_render_manifest(path: Path, entries: Dict[str, Dict[str, Any]]) -> Path:
    """`render_manifest.json`（PR #306 第4巡 P1）と同形の manifest ファイルを
    書く。`entries` は `{group_id: {"render_doc_sha256": ..., "path": ...}}`。"""
    path.write_text(
        json.dumps({"schema": d4.RENDER_MANIFEST_SCHEMA, "groups": entries}),
        encoding="utf-8",
    )
    return path


# --- PR #306 CI 指摘: ANALYSIS_STACK_PIN 不一致環境（例: CI の numba 0.67.0
# vs 宣言 pin 0.66.0）で、実測定を経由するテストだけを skip するための判定 ---
#
# `s7_b1_calibration.verify_analysis_stack` の fail-closed 自体は正しい挙動
# （緩めない）。ただし宣言 pin と違う版がインストールされた CI では、実測定を
# 経由するテストが「テストの不具合」ではなく「計器が意図どおり止まった」こと
# で落ちる。これは実行環境の問題であり、テストの合否から computed 済みの
# skip 条件として切り離す。


def _analysis_stack_mismatch_detail(prereg: Optional["d4.b1.Prereg"] = None) -> Optional[str]:
    """宣言 ANALYSIS_STACK_PIN と実行時インストール版の不一致detailを返す
    （一致なら None）。`s7_b1_calibration.verify_analysis_stack` と同じ判定を
    **例外を投げずに**行う（skipif 条件の計算はテスト収集時に評価されるため、
    ここで例外を投げると収集そのものが壊れる）。"""
    if prereg is None:
        prereg = d4.b1.load_prereg()
    declared = {
        k: str(v) for k, v in prereg.candidate_space["analysis_stack_pin"].items() if k != "note"
    }
    observed = {pkg: _md.version(pkg) for pkg in sorted(declared)}
    mismatch = {k: (declared[k], observed[k]) for k in declared if declared[k] != observed[k]}
    if not mismatch:
        return None
    return ", ".join(f"{k}: declared {d} != installed {o}" for k, (d, o) in sorted(mismatch.items()))


#: モジュール読み込み時に 1 回だけ評価する（各テストの skipif がここを参照）。
_STACK_MISMATCH_DETAIL = _analysis_stack_mismatch_detail()

requires_pinned_analysis_stack = pytest.mark.skipif(
    _STACK_MISMATCH_DETAIL is not None,
    reason=(
        "ANALYSIS_STACK_PIN 不一致環境のため実測定を伴うテストを skip する"
        f"（fail-closed は正当な挙動・緩めない）: {_STACK_MISMATCH_DETAIL}"
    ),
)


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


@pytest.fixture(scope="module")
def permissive_window_expectations(all_cell_ids: List[str]):
    """`_measure_group` の測定窓照合（PR #306 レビュー第3巡 P1）を、`BOUNDARIES`
    を使う既存の合成テストで常に通過させるための window_expectations。
    実 pinned 値での不一致検出そのものは `test_verify_cell_window_*` 系が直接
    検査する。"""
    expectations = {cid: (_PERMISSIVE_DURATION_BEATS, _PERMISSIVE_TEMPO_BPM) for cid in all_cell_ids}
    return expectations, BOUNDARIES["tail_window_ms"]


# --- 1. spec 形状 ------------------------------------------------------


def test_spec_schema_and_debt_ref(raw_spec: Dict[str, Any]) -> None:
    assert raw_spec["schema"] == "vg-d4-remeasure-spec/0.4"
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


def test_load_and_verify_does_not_import_pinned_modules_before_verification_passes(
    tmp_path: Path, raw_spec: Dict[str, Any], monkeypatch,
) -> None:
    """pre-bind 原則（PR #217 と同型・PR #306 レビュー第2巡 P2）の回帰検査:
    pin を改竄した spec では、pinned module（`s7_b1_calibration` 等）は
    import されない（= `sys.modules` に現れない）ことを確認する。対照として、
    改竄していない実 spec なら検証を通過して import される。

    モジュール収集時点で他のテストが既に `sys.modules` へ登録している
    可能性があるため、`monkeypatch.delitem` で明示的に取り除いてから検査する
    （テスト順序に依存しない）。
    """
    for name in d4._PINNED_MODULE_NAMES:
        monkeypatch.delitem(sys.modules, name, raising=False)

    tampered = copy.deepcopy(raw_spec)
    tampered["pins"]["instrument_sha256"] = "0" * 64
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch):
        d4.load_and_verify_d4_spec(path)

    still_absent = [name for name in d4._PINNED_MODULE_NAMES if name in sys.modules]
    assert not still_absent, (
        f"検証が失敗したにもかかわらず import されたモジュールがある: {still_absent}"
        "（pre-bind 原則違反）"
    )

    # 対照: 改竄していない実 spec は検証を通過し、通過した結果として import
    # される（binding が「検証通過後にのみ」起こることの積極側の確認）。
    d4.load_and_verify_d4_spec()
    now_missing = [name for name in d4._PINNED_MODULE_NAMES if name not in sys.modules]
    assert not now_missing, f"検証通過後も import されていないモジュールがある: {now_missing}"


def test_bind_pinned_modules_rejects_preloaded_module(monkeypatch) -> None:
    """PR #306 レビュー第3巡 P2: pinned module が `_bind_pinned_modules()` の
    import 実行**前**に（本 runner の束縛経路以外から）既に `sys.modules` に
    存在すると fail-closed で abort する（`reason=preloaded_pinned_module`）。

    このプロセスでは既に正規の束縛が完了しているはずなので、`_pinned_modules_
    bound` フラグを「未束縛」へ一時的に巻き戻し（= プロセス内で初めて束縛を
    試みる状況を再現）、その状態で 1 つの pinned module 名を模擬的に
    preload（本来の束縛経路を経ていない `types.ModuleType` のスタブへ差し替え）
    してから `_bind_pinned_modules()` を直接呼ぶ。"""
    monkeypatch.setattr(d4, "_pinned_modules_bound", False)
    fake_module = types.ModuleType("s7_io")
    monkeypatch.setitem(sys.modules, "s7_io", fake_module)

    with pytest.raises(d4.D4SpecMismatch, match="preloaded_pinned_module"):
        d4._bind_pinned_modules()


def test_bind_pinned_modules_rejects_preloaded_transitive_closure_module(monkeypatch) -> None:
    """PR #306 レビュー第5巡 P2 の回帰: preload 検査対象は `_bind_pinned_modules()`
    が直接 `importlib.import_module()` する 6 モジュールの hand-list ではなく、
    `enumerate_repo_import_closure()` が機械列挙した完全な推移閉包（`s7_b1_v11`
    や `s7_spec` のような、直接 import する側の import 文の中に隠れた推移依存を
    含む）から導出する。ここでは閉包メンバーだが直接 import 対象ではない
    `s7_b1_v11` を偽パスで preload し、それでも abort することを確認する
    （hand-list のままなら見逃していたはずの経路）。"""
    assert "s7_b1_v11" in d4._PINNED_MODULE_NAMES
    monkeypatch.setattr(d4, "_pinned_modules_bound", False)
    fake_module = types.ModuleType("s7_b1_v11")
    fake_module.__file__ = "/tmp/not-the-real-s7_b1_v11.py"
    monkeypatch.setitem(sys.modules, "s7_b1_v11", fake_module)

    with pytest.raises(d4.D4SpecMismatch, match="preloaded_pinned_module"):
        d4._bind_pinned_modules()


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


def test_data_inputs_pin_exists_and_matches_expected_keys(raw_spec: Dict[str, Any]) -> None:
    """`pins.data_inputs`（PR #306 レビュー第7巡 P1・データ入力閉包）が存在し、
    全数確認で見つかった 8 点のキー集合と厳密一致することを確認する。"""
    data_inputs = raw_spec["pins"]["data_inputs"]
    assert set(data_inputs) == d4.EXPECTED_DATA_INPUT_KEYS
    for key, entry in data_inputs.items():
        assert isinstance(entry["path"], str) and entry["path"], key
        assert isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64, key


def test_data_inputs_pin_sha256_matches_real_files(raw_spec: Dict[str, Any]) -> None:
    """`pins.data_inputs` の各エントリの sha256 が実ファイルの実バイトと一致する
    （`test_spec_verifies_against_live_files` が起動経路で既に確認しているが、
    ここでは `_sha_file` を直接使い pin 対象そのものを単体で確認する）。"""
    data_inputs = raw_spec["pins"]["data_inputs"]
    for key, entry in data_inputs.items():
        got = d4._sha_file(d4._REPO_ROOT / entry["path"])
        assert got == entry["sha256"], key


def test_load_and_verify_rejects_tampered_data_input(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    """`pins.data_inputs` の 1 エントリの sha256 を改竄すると abort する。"""
    tampered = copy.deepcopy(raw_spec)
    tampered["pins"]["data_inputs"]["s7_exporter_input_pins"]["sha256"] = "2" * 64
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="s7_exporter_input_pins"):
        d4.load_and_verify_d4_spec(path)


def test_load_and_verify_rejects_missing_data_input_key(
    tmp_path: Path, raw_spec: Dict[str, Any]
) -> None:
    """`pins.data_inputs` からキーが 1 つでも欠ければ（キー集合の厳密検査）abort
    する（新規追加データ入力の取りこぼしを構造的に防止する）。"""
    tampered = copy.deepcopy(raw_spec)
    del tampered["pins"]["data_inputs"]["trf_measurement_spec_1_0"]
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(d4.D4SpecMismatch, match="data_inputs"):
        d4.load_and_verify_d4_spec(path)


# --- 2b. データ入力の検証→消費 TOCTOU 閉塞（PR #306 レビュー第8巡 P2） ----------


def test_verify_consumed_digests_passes_when_matching() -> None:
    """方式 (a) 単体テスト: loader が返す digest が pin と一致すれば通過する。"""
    data_inputs = {"my_key": {"path": "some/path.json", "sha256": "a" * 64}}
    d4._verify_consumed_digests({"my_key": data_inputs["my_key"]}, {"path.json": "a" * 64}, source="test")


def test_verify_consumed_digests_ignores_unrelated_filenames() -> None:
    """`consumed` に data_inputs 対象外のファイル名が混ざっていても無視する
    （loader が周辺ファイルの digest も一緒に返す場合の誤検出防止）。"""
    data_inputs = {"my_key": {"path": "some/path.json", "sha256": "a" * 64}}
    d4._verify_consumed_digests(data_inputs, {"unrelated.json": "b" * 64}, source="test")


def test_verify_consumed_digests_detects_mismatch() -> None:
    """方式 (a) 単体テスト: loader が返す digest が pin と食い違えば abort する
    （検証時点と消費時点で読んだバイトが違う = TOCTOU）。"""
    data_inputs = {"my_key": {"path": "some/path.json", "sha256": "a" * 64}}
    with pytest.raises(d4.D4SpecMismatch, match="TOCTOU"):
        d4._verify_consumed_digests(data_inputs, {"path.json": "b" * 64}, source="test")


def test_reverify_data_inputs_after_consumption_detects_file_swapped_after_verification(
    tmp_path: Path, monkeypatch,
) -> None:
    """方式 (b) 単体テスト・コーディネータ指定シナリオ: 検証時点で計算した
    sha256 を pin として与えた**後**にファイルを差し替え、消費直後の再検査が
    それを検出して abort することを確認する（検証→消費 TOCTOU の実演）。"""
    rel_path = "fake_data_inputs/probe.json"
    real_path = tmp_path / rel_path
    real_path.parent.mkdir(parents=True)
    real_path.write_text('{"v": 1}', encoding="utf-8")
    verified_sha256 = d4._sha_file(real_path)  # 検証時点（差し替え前）の digest。
    data_inputs = {"fake_key": {"path": rel_path, "sha256": verified_sha256}}
    monkeypatch.setattr(d4, "_REPO_ROOT", tmp_path)

    # 検証通過後・消費前にファイルが差し替わる（TOCTOU のシミュレーション）。
    real_path.write_text('{"v": 2}', encoding="utf-8")

    with pytest.raises(d4.D4SpecMismatch, match="TOCTOU"):
        d4._reverify_data_inputs_after_consumption(data_inputs, ["fake_key"], source="test")


def test_reverify_data_inputs_after_consumption_passes_when_unchanged(
    tmp_path: Path, monkeypatch,
) -> None:
    """対照: 消費までファイルが変わらなければ再検査は通過する。"""
    rel_path = "fake_data_inputs/probe.json"
    real_path = tmp_path / rel_path
    real_path.parent.mkdir(parents=True)
    real_path.write_text('{"v": 1}', encoding="utf-8")
    data_inputs = {"fake_key": {"path": rel_path, "sha256": d4._sha_file(real_path)}}
    monkeypatch.setattr(d4, "_REPO_ROOT", tmp_path)

    d4._reverify_data_inputs_after_consumption(data_inputs, ["fake_key"], source="test")


def test_verify_consumed_cell_definition_digest_detects_file_swapped_after_verification(
    tmp_path: Path,
) -> None:
    """検証→消費 TOCTOU 閉塞の回帰（`cell_definition_source`。第8巡の自主検出
    残件を先回りで閉じる）: 起動時検証で計算した sha256 を pin として与えた
    **後**に probe spec が差し替わり、`cmd_render` が消費時に読んだ digest が
    それと食い違えば abort する（spec 検証後に probe spec を差し替える
    シナリオをそのまま再現する）。"""
    real_path = tmp_path / "probe_spec.json"
    real_path.write_text('{"v": 1}', encoding="utf-8")
    verified_sha256 = d4._sha_file(real_path)  # 起動時検証（差し替え前）の digest。
    spec = {"pins": {"cell_definition_source": {
        "path": "probe_spec.json", "sha256": verified_sha256,
    }}}

    # 検証通過後・消費前に probe spec が差し替わる（TOCTOU のシミュレーション）。
    real_path.write_text('{"v": 2}', encoding="utf-8")
    consumed_sha256 = d4._sha_file(real_path)  # cmd_render が実際に消費時に読む digest。

    with pytest.raises(d4.D4SpecMismatch, match="TOCTOU"):
        d4._verify_consumed_cell_definition_digest(spec, consumed_sha256)


def test_verify_consumed_cell_definition_digest_passes_when_matching(
    raw_spec: Dict[str, Any],
) -> None:
    """対照: 消費時に読んだ digest が pin と一致すれば通過する。"""
    real_sha = raw_spec["pins"]["cell_definition_source"]["sha256"]
    d4._verify_consumed_cell_definition_digest(raw_spec, real_sha)


def test_resolve_axis_candidates_rejects_consumed_digest_mismatch(
    spec_and_sha, monkeypatch,
) -> None:
    """検証→消費 TOCTOU 閉塞・方式 (a) の end-to-end 回帰: `v12.load_prereg_12()`
    が返す digest が起動時検証済みの `pins.data_inputs` と食い違えば
    `_resolve_axis_candidates` が abort する（消費時点でファイルが差し替わって
    いたことに相当する状況を monkeypatch で再現する）。"""
    spec, _sha = spec_and_sha
    real_cs, real_cal, real_rule, real_pins = d4.v12.load_prereg_12()
    tampered_pins = dict(real_pins)
    a_key = next(iter(tampered_pins))
    tampered_pins[a_key] = "0" * 64
    monkeypatch.setattr(
        d4.v12, "load_prereg_12", lambda: (real_cs, real_cal, real_rule, tampered_pins),
    )
    with pytest.raises(d4.D4SpecMismatch, match="TOCTOU"):
        d4._resolve_axis_candidates(spec)


@pytest.mark.skipif(
    _STACK_MISMATCH_DETAIL is not None,
    reason=(
        "ANALYSIS_STACK_PIN 不一致環境のため実測定を伴うテストを skip する"
        f"（fail-closed は正当な挙動・緩めない）: {_STACK_MISMATCH_DETAIL}"
    ),
)
def test_cmd_measure_rejects_consumed_prereg_digest_mismatch(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc, monkeypatch,
) -> None:
    """検証→消費 TOCTOU 閉塞・方式 (a) の end-to-end 回帰（`cmd_measure` 経由）:
    `b1.load_prereg()` が返す digest が起動時検証済みの `pins.data_inputs` と
    食い違えば `cmd_measure` が abort する。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }
    render_doc = _full_render_doc(out_dir, all_cell_ids, rendered)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    # `cmd_measure` の内部で `load_and_verify_d4_spec` が `_bind_pinned_modules()`
    # を再度呼び、`b1 = importlib.import_module("s7_b1_calibration")` で
    # `sys.modules` から都度再取得する。したがって `d4.b1` オブジェクト自身では
    # なく、`sys.modules["s7_b1_calibration"]`（実際に再取得される canonical な
    # オブジェクト）に対して patch する（`d4.b1` は他テストの sys.modules
    # 操作の影響で一時的に別オブジェクトを指しうるため、`sys.modules` 経由の
    # 方が消費経路と確実に一致する）。
    b1_module = sys.modules["s7_b1_calibration"]
    real_prereg = b1_module.load_prereg()
    tampered_pins = dict(real_prereg.pins)
    a_key = next(iter(tampered_pins))
    tampered_pins[a_key] = "0" * 64
    monkeypatch.setattr(
        b1_module, "load_prereg",
        lambda: dataclasses.replace(real_prereg, pins=tampered_pins),
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
    )
    with pytest.raises(d4.D4SpecMismatch, match="TOCTOU"):
        d4.cmd_measure(ns)
    assert not out_path.exists()


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
    ns = argparse.Namespace(
        render_doc=[], out=str(d4.SPEC_PATH), spec_sha256=sha, render_manifest=[],
        render_manifest_sha256=[],
    )
    with pytest.raises(s7_io.OutputCollisionError):
        d4.cmd_measure(ns)
    # 何も書き込まれていない（spec が壊れていない）ことも確認する。
    d4.load_and_verify_d4_spec()


def test_measure_out_collision_with_referenced_wav_is_rejected(
    tmp_path: Path, spec_and_sha, all_cell_ids,
) -> None:
    """`--out` の保護集合拡張（PR #306 レビュー第9巡 P1）: `--render-doc` が
    参照する WAV そのものを `--out` に指定すると、測定どころか読み込みの前に
    abort する（元データを上書きする経路を閉じる）。全 `--render-doc` を読み、
    参照される全 WAV パスを保護集合へ加えたうえで、実測定ループが始まる前に
    まとめて検査する。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = _write_wav(out_dir, "cell.wav", _sustained_tail_wave())
    wav_path = out_dir / "cell.wav"
    original_bytes = wav_path.read_bytes()
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: dict(wav_meta)})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(wav_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
    )

    with pytest.raises(s7_io.OutputCollisionError):
        d4.cmd_measure(ns)

    assert wav_path.read_bytes() == original_bytes, "WAV が上書きされてはいけない"


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
    runtime_stack: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """36 セル規定数を満たす render 群 JSON を組み立てる（`rendered` に入って
    いないセルは `dropped` として記帳する）。事前登録照合（セルフレビュー #2）
    は cell_id 集合の**厳密一致**を要求するため、テスト用の疑似 doc も実際の
    36 セル全部を持たせる。`runtime_stack` 省略時（既定）は render doc に
    キー自体を持たせない — PR #306 第1巡以前の旧形式 render 出力を模す
    （`render_runtime_stack` 転記テストの対照）。"""
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
    doc = {
        "generation": generation, "speaker": speaker,
        "out_dir": str(out_dir.resolve()),
        "d4_remeasure_spec_sha256": spec_sha256,
        "model_sha256": {"acoustic_onnx": "deadbeef"},
        "aux_sha256": {"gate_synth_py": "deadbeef"},
        "export_binding": {"source_checkpoint_sha256": "deadbeef"},
        "cells": cells,
    }
    if runtime_stack is not None:
        doc["runtime_stack"] = runtime_stack
    return doc


def test_measure_group_computes_voicing_axes_on_synthetic_wav(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    permissive_window_expectations,
) -> None:
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = _write_wav(out_dir, "cell.wav", _sustained_tail_wave())
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: dict(wav_meta)})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        *permissive_window_expectations, manifest_shas,
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

    # render_runtime_stack（PR #306 レビュー第2巡 P2）: 旧形式（runtime_stack
    # キーを持たない render doc）は null + 注記になる。
    assert group_doc["render_runtime_stack"] is None
    assert group_doc["render_runtime_stack_note"]
    assert "旧形式" in group_doc["render_runtime_stack_note"]


def test_measure_group_transcribes_render_runtime_stack_verbatim(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    permissive_window_expectations,
) -> None:
    """render doc に `runtime_stack` があれば、群結果へ `render_runtime_stack`
    として逐語転記される（PR #306 レビュー第2巡 P2）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = _write_wav(out_dir, "cell.wav", _sustained_tail_wave())
    fake_render_stack = {
        "python": "3.11.15", "numpy": "2.4.6", "onnxruntime": "1.29.0",
        "soundfile": "0.14.0", "PyYAML": "6.0.1",
    }
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {target: dict(wav_meta)}, runtime_stack=fake_render_stack,
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        *permissive_window_expectations, manifest_shas,
    )

    assert group_doc["render_runtime_stack"] == fake_render_stack
    assert group_doc["render_runtime_stack_note"] is None


def test_measure_cell_axes_differ_between_distinct_wavs(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    permissive_window_expectations,
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
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        *permissive_window_expectations, manifest_shas,
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
    permissive_window_expectations,
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
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        *permissive_window_expectations, manifest_shas,
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
    # manifest 束縛（第4巡）は bound_sha 検査より前段のため、この early-abort
    # 経路を試すには manifest 自体は一致させておく必要がある。
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    with pytest.raises(d4.D4SpecMismatch):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            {}, 300.0,  # 早期 abort 経路のため window 引数は未使用（プレースホルダ）
            manifest_shas,
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
    # manifest 束縛（第4巡）は群登録検査より前段のため、捏造した group_id
    # （run5_nonexistent_speaker）に対しても manifest 自体は一致させておく。
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_nonexistent_speaker")

    with pytest.raises(d4.D4SpecMismatch, match="groups"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            {}, 300.0,  # 早期 abort 経路のため window 引数は未使用（プレースホルダ）
            manifest_shas,
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
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    with pytest.raises(d4.D4SpecMismatch, match="セル数"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            {}, 300.0,  # 早期 abort 経路のため window 引数は未使用（プレースホルダ）
            manifest_shas,
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
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    with pytest.raises(d4.D4SpecMismatch, match="重複"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            {}, 300.0,  # 早期 abort 経路のため window 引数は未使用（プレースホルダ）
            manifest_shas,
        )


# --- 2b. 測定窓の pinned 定義束縛（PR #306 レビュー第3巡 P1・「方式 b」） -----


def test_measure_group_accepts_window_matching_pinned_cell_definition(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    probe_spec_doc,
) -> None:
    """render doc の input_meta が pinned cell 定義（duration_beats/tempo_bpm）
    から導出した期待値と一致すれば通常どおり測定される（正規 doc の対照）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    window_expectations, expected_tail_window_ms = d4._load_window_expectations(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    wav_meta["input_meta"] = _real_boundaries_for(target, probe_spec_doc)
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: wav_meta})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        window_expectations, expected_tail_window_ms, manifest_shas,
    )

    entry = group_doc["cells"][target]
    assert entry["outcome"] == "measured"
    assert group_doc["n_error"] == 0


def test_measure_group_rejects_tampered_window(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    probe_spec_doc,
) -> None:
    """render doc の input_meta（score_boundary_s）を直接書き換えて測定窓を
    差し替えると、pinned cell 定義から導出した期待値と食い違い
    `window_mismatch` として隔離される（PR #306 レビュー第3巡 P1: 「doc 編集
    による窓差し替えの偽測定経路」の回帰テスト）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    window_expectations, expected_tail_window_ms = d4._load_window_expectations(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    tampered_meta = dict(_real_boundaries_for(target, probe_spec_doc))
    tampered_meta["score_boundary_s"] += 5.0  # 窓を 5 秒後ろへ差し替える
    wav_meta["input_meta"] = tampered_meta
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: wav_meta})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        window_expectations, expected_tail_window_ms, manifest_shas,
    )

    entry = group_doc["cells"][target]
    assert entry["outcome"] == "error"
    assert entry["status"] == "window_mismatch"
    assert entry["reason"] == "window_mismatch"
    assert group_doc["n_error"] == 1


def test_measure_group_rejects_tampered_tail_window_ms(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    probe_spec_doc,
) -> None:
    """`tail_window_ms` の改竄も window_mismatch として検出する（測定窓は
    score_boundary_s だけでなく tail_window_ms も pinned 定義に束縛する）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    window_expectations, expected_tail_window_ms = d4._load_window_expectations(spec)
    out_dir = tmp_path / "out"
    target = all_cell_ids[0]
    wav_meta = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    tampered_meta = dict(_real_boundaries_for(target, probe_spec_doc))
    tampered_meta["tail_window_ms"] = 900.0  # 3 倍に差し替える
    wav_meta["input_meta"] = tampered_meta
    render_doc = _full_render_doc(out_dir, all_cell_ids, {target: wav_meta})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = _manifest_shas_for(render_doc_path, "run5_pjs")

    group_doc = d4._measure_group(
        render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
        window_expectations, expected_tail_window_ms, manifest_shas,
    )

    entry = group_doc["cells"][target]
    assert entry["outcome"] == "error"
    assert entry["status"] == "window_mismatch"


def test_measure_group_rejects_render_manifest_mismatch(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    permissive_window_expectations,
) -> None:
    """render doc の実バイトが --render-manifest に記載された sha256 と食い違えば
    window 検査より前に abort する（PR #306 レビュー第4巡 P1: render doc 全体を
    改変する経路 — 例えば窓 2 点だけでなく他フィールドも一括改竄する経路 — を
    doc 全体の digest 束縛で終端する）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    render_doc = _full_render_doc(out_dir, all_cell_ids, {})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas = {"run5_pjs": "0" * 64}  # 実バイトと絶対に一致しない偽 sha256

    with pytest.raises(d4.D4SpecMismatch, match="改変された疑い"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            *permissive_window_expectations, manifest_shas,
        )


def test_measure_group_rejects_missing_render_manifest_entry(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, expected_cell_ids,
    permissive_window_expectations,
) -> None:
    """群 ID が --render-manifest に一件も記載されていなければ abort する
    （manifest に無い doc は測らない、という fail-closed 方針の回帰）。"""
    spec, spec_sha = spec_and_sha
    axis_candidates = d4._resolve_axis_candidates(spec)
    out_dir = tmp_path / "out"
    render_doc = _full_render_doc(out_dir, all_cell_ids, {})
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_shas: Dict[str, str] = {}  # run5_pjs のエントリが無い

    with pytest.raises(d4.D4SpecMismatch, match="記載されていない"):
        d4._measure_group(
            render_doc_path, spec_sha, axis_candidates, valid_group_ids, expected_cell_ids,
            *permissive_window_expectations, manifest_shas,
        )


def test_verify_cell_window_matches_all_360_real_cells() -> None:
    """2026-08-22 実測の render doc 360 セル全数で、pinned 定義から導出した
    window 期待値と実測 input_meta が一致することを検査する（`_load_window_
    expectations` の設計根拠（「方式 b」採用）そのものの回帰）。実 render doc
    （数 GB）は repo に同梱されないため、存在するときだけ実行する（無ければ
    machine-dependent として skip）。"""
    render_dir = Path("/home/user/d4work/render")
    if not render_dir.is_dir():
        pytest.skip(f"{render_dir} が無い（実 render doc は repo 非同梱・machine-dependent）")
    spec = json.loads(d4.SPEC_PATH.read_text(encoding="utf-8"))
    window_expectations, expected_tail_window_ms = d4._load_window_expectations(spec)
    n_checked = n_mismatch = 0
    mismatches = []
    for path in sorted(render_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for cell in doc["cells"]:
            if cell.get("outcome") != "rendered":
                continue
            cid = str(cell["cell_id"])
            n_checked += 1
            try:
                d4._verify_cell_window(
                    cid, cell["input_meta"], window_expectations.get(cid), expected_tail_window_ms
                )
            except d4.D4WindowMismatch as exc:
                n_mismatch += 1
                mismatches.append((path.name, cid, str(exc)))
    assert n_checked == 360, f"実測セル数 {n_checked} != 360"
    assert n_mismatch == 0, f"{n_mismatch} 件不一致: {mismatches[:5]}"


# --- 4b. render_manifest 自体の信頼アンカー（PR #306 レビュー第5巡 P1） --------


def test_cmd_measure_rejects_render_manifest_sha256_mismatch(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc,
) -> None:
    """`--render-manifest` の実バイト sha256 が `--render-manifest-sha256`
    （operator 供給の期待値）と食い違えば、doc-vs-manifest 照合よりも前に abort
    する — manifest ファイル自体が render 完了後に差し替えられた経路の検出
    （render 成果物 provenance 連鎖の信頼の根 = operator 供給 sha）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }
    render_doc = _full_render_doc(out_dir, all_cell_ids, rendered)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=["0" * 64],
    )

    with pytest.raises(d4.D4SpecMismatch, match="render-manifest-sha256"):
        d4.cmd_measure(ns)
    assert not out_path.exists()


def test_cmd_measure_rejects_render_manifest_sha256_count_mismatch(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc,
) -> None:
    """`--render-manifest` と `--render-manifest-sha256` の件数が食い違えば
    abort する（1 対 1・同順対応の契約違反。数が合わなければどれとどれが
    対応するか決められないため）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }
    render_doc = _full_render_doc(out_dir, all_cell_ids, rendered)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[],
    )

    with pytest.raises(d4.D4SpecMismatch, match="数が違う"):
        d4.cmd_measure(ns)
    assert not out_path.exists()


# --- 5. セル毎例外の隔離 + exit 非ゼロ（cmd_measure 経由の end-to-end） -------


def test_analysis_stack_mismatch_detail_detects_tampered_pin() -> None:
    """skipif 条件の計算ロジック自体の単体テスト（PR #306 対応）: 宣言 pin を
    偽装した場合に不一致を検出できることを、実行環境の実際の版に依存せず
    確認する（CI の numba 0.67.0 のような不一致環境で `requires_pinned_
    analysis_stack` が確実に skip 側へ倒れることの保証）。"""
    prereg = d4.b1.load_prereg()
    tampered_cs = copy.deepcopy(prereg.candidate_space)
    tampered_cs["analysis_stack_pin"]["numba"] = "0.0.0-tampered-for-test"
    tampered_prereg = dataclasses.replace(prereg, candidate_space=tampered_cs)

    detail = _analysis_stack_mismatch_detail(tampered_prereg)
    assert detail is not None
    assert "numba" in detail
    assert "0.0.0-tampered-for-test" in detail

    # 偽装していない実プレレジは、このテスト実行環境の実測とモジュール読み込み
    # 時の記録が一致するはず（環境非依存にするため実値そのものとは比較しない）。
    assert _analysis_stack_mismatch_detail(prereg) == _STACK_MISMATCH_DETAIL


def test_cmd_measure_isolates_broken_cell_and_exits_nonzero_stack_independent(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc, monkeypatch,
) -> None:
    """セルフレビュー #6 の回帰検査を ANALYSIS_STACK_PIN の版に依存せず常時
    実行する変種（PR #306 対応）: `verify_analysis_stack` と実測定候補
    （`measure_candidate_12`）を monkeypatch で置き換え、隔離ロジック・
    exit 非ゼロ・欠測記帳だけを検査する。CI の numba が宣言 pin と違っても
    skip されない。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    ok_id, broken_id = all_cell_ids[0], all_cell_ids[1]
    ok_wav = dict(_write_wav(out_dir, "ok.wav", _sustained_tail_wave()))
    ok_wav["input_meta"] = _real_boundaries_for(ok_id, probe_spec_doc)
    broken_wav = dict(_write_wav(out_dir, "broken.wav", _hard_cut_wave()))
    (out_dir / "broken.wav").unlink()
    render_doc = _full_render_doc(
        out_dir, all_cell_ids, {ok_id: ok_wav, broken_id: broken_wav},
    )
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    fake_stack = {"fake_pkg": "0.0.0"}
    fake_axes = {
        "excess_tail_voiced_ms": 1.0, "release_after_score_boundary_ms": 2.0,
        "tail_f0_persistence": 0.5,
    }
    monkeypatch.setattr(d4.b1, "verify_analysis_stack", lambda prereg: dict(fake_stack))
    monkeypatch.setattr(d4.v12, "measure_candidate_12", lambda cand, stim: dict(fake_axes))

    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
    )
    rc = d4.cmd_measure(ns)

    assert rc != 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["analysis_stack"] == fake_stack
    group = doc["groups"]["run5_pjs"]
    assert group["n_error"] == 1
    assert group["n_measured"] == 1
    ok_entry = group["cells"][ok_id]
    assert ok_entry["outcome"] == "measured"
    assert ok_entry["axes"] == fake_axes
    broken_entry = group["cells"][broken_id]
    assert broken_entry["outcome"] == "error"
    assert broken_entry["status"] == "error"
    assert broken_entry["reason"]
    assert doc["n_total_error"] == 1


@requires_pinned_analysis_stack
def test_cmd_measure_isolates_broken_cell_and_exits_nonzero(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc,
) -> None:
    """セルフレビュー #6: 1 セルの入力破壊（wav 実体の欠落）が他セルの測定を
    止めず、結果に error セルとして記帳されたうえで `cmd_measure` が非ゼロを
    返す。ANALYSIS_STACK_PIN が宣言と一致する環境でのみ実行する（不一致環境
    向けの常時実行変種 = 直前の `..._stack_independent`）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    ok_id, broken_id = all_cell_ids[0], all_cell_ids[1]
    ok_wav = dict(_write_wav(out_dir, "ok.wav", _sustained_tail_wave()))
    ok_wav["input_meta"] = _real_boundaries_for(ok_id, probe_spec_doc)
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

    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
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


# --- 5b. 完全性検査（PR #306 レビュー指摘 #4） ------------------------------
#
# 実測定（ANALYSIS_STACK_PIN）に依存させないため、いずれも `verify_analysis_stack`
# / `measure_candidate_12` を monkeypatch で置き換える（§5 の `..._stack_independent`
# と同じ方式）。


def test_cmd_measure_marks_incomplete_for_single_group_input(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc, monkeypatch,
) -> None:
    """事前登録10群のうち1群だけを渡すと、その1群が規定セル数ぶんエラー無く
    測定できても `complete: false` + exit 非ゼロになる（群が事前登録に満たない）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }
    render_doc = _full_render_doc(out_dir, all_cell_ids, rendered)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    fake_axes = {
        "excess_tail_voiced_ms": 1.0, "release_after_score_boundary_ms": 2.0,
        "tail_f0_persistence": 0.5,
    }
    monkeypatch.setattr(d4.b1, "verify_analysis_stack", lambda prereg: {"fake": "stack"})
    monkeypatch.setattr(d4.v12, "measure_candidate_12", lambda cand, stim: dict(fake_axes))

    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
    )
    rc = d4.cmd_measure(ns)

    assert rc != 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["complete"] is False
    assert doc["n_groups"] == 1
    assert doc["n_total_error"] == 0
    assert doc["groups"]["run5_pjs"]["n_measured"] == 36
    assert "partial" not in doc  # --allow-partial を渡していない既定経路


def test_cmd_measure_allow_partial_records_partial_flag(
    tmp_path: Path, spec_and_sha, all_cell_ids, probe_spec_doc, monkeypatch,
) -> None:
    """`--allow-partial` を渡すと、不完全な結果に `partial: true` が記帳される
    （exit の非ゼロ化自体は変わらない — 既定でも部分実行は abort せず書き切る）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }
    render_doc = _full_render_doc(out_dir, all_cell_ids, rendered)
    render_doc_path = tmp_path / "run5_pjs.json"
    render_doc_path.write_text(json.dumps(render_doc), encoding="utf-8")

    fake_axes = {
        "excess_tail_voiced_ms": 1.0, "release_after_score_boundary_ms": 2.0,
        "tail_f0_persistence": 0.5,
    }
    monkeypatch.setattr(d4.b1, "verify_analysis_stack", lambda prereg: {"fake": "stack"})
    monkeypatch.setattr(d4.v12, "measure_candidate_12", lambda cand, stim: dict(fake_axes))

    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    _write_render_manifest(
        manifest_path,
        {"run5_pjs": {"render_doc_sha256": _sha256_of(render_doc_path), "path": render_doc_path.name}},
    )

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=[str(render_doc_path)], out=str(out_path), spec_sha256=spec_sha,
        allow_partial=True, render_manifest=[str(manifest_path)],
        render_manifest_sha256=[_sha256_of(manifest_path)],
    )
    rc = d4.cmd_measure(ns)

    assert rc != 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["complete"] is False
    assert doc["partial"] is True


def test_cmd_measure_marks_complete_for_all_ten_groups(
    tmp_path: Path, spec_and_sha, all_cell_ids, valid_group_ids, probe_spec_doc, monkeypatch,
) -> None:
    """事前登録の10群すべてを規定セル数（36）ぶん揃えて渡すと `complete: true` +
    exit 0 になる。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    wav = dict(_write_wav(out_dir, "cell.wav", _sustained_tail_wave()))
    rendered = {
        cid: {**wav, "input_meta": _real_boundaries_for(cid, probe_spec_doc)}
        for cid in all_cell_ids
    }

    fake_axes = {
        "excess_tail_voiced_ms": 1.0, "release_after_score_boundary_ms": 2.0,
        "tail_f0_persistence": 0.5,
    }
    monkeypatch.setattr(d4.b1, "verify_analysis_stack", lambda prereg: {"fake": "stack"})
    monkeypatch.setattr(d4.v12, "measure_candidate_12", lambda cand, stim: dict(fake_axes))

    render_doc_paths: List[str] = []
    manifest_entries: Dict[str, Dict[str, Any]] = {}
    for group_id in sorted(valid_group_ids):
        generation, speaker = group_id.split("_", 1)
        render_doc = _full_render_doc(
            out_dir, all_cell_ids, rendered, generation=generation, speaker=speaker,
        )
        path = tmp_path / f"{group_id}.json"
        path.write_text(json.dumps(render_doc), encoding="utf-8")
        render_doc_paths.append(str(path))
        manifest_entries[group_id] = {
            "render_doc_sha256": _sha256_of(path), "path": path.name,
        }
    manifest_path = tmp_path / "render_manifest.json"
    _write_render_manifest(manifest_path, manifest_entries)

    out_path = tmp_path / "d4_results.json"
    ns = argparse.Namespace(
        render_doc=render_doc_paths, out=str(out_path), spec_sha256=spec_sha,
        render_manifest=[str(manifest_path)], render_manifest_sha256=[_sha256_of(manifest_path)],
    )
    rc = d4.cmd_measure(ns)

    assert rc == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["complete"] is True
    assert doc["n_groups"] == 10
    assert doc["n_total_cells"] == 360
    assert doc["n_total_measured"] == 360
    assert doc["n_total_error"] == 0
    assert "partial" not in doc
    # runtime_stack（PR #306 対応 #1）: measure が実行時のパッケージ版を記帳する。
    assert doc["runtime_stack"]["python"]
    assert set(doc["runtime_stack"]) == {"python", *d4._RUNTIME_STACK_PACKAGES}


# --- 5c. render の atomic 公開（PR #306 レビュー第9巡 P1・第10巡 P1 で単一
#         トランザクションへ拡張） ---------------------------------------------
#
# `_new_staging_dir` / `_atomic_publish_render_set` は単体で、staging → 最終
# 位置の `cmd_render` 経由の一括公開は monkeypatch で `xm.verify_export_
# manifest` / `probe0b.load_gate_synth` / `probe0b.run_group` を差し替えて
# 確認する（ファイル冒頭の docstring どおり、本ファイルは onnxruntime 非依存を
# 維持する —`load_gate_synth` を mock するのはこの理由もある。実 checkpoint
# 実体は一切要らない）。


def test_new_staging_dir_creates_unique_sibling_directory(tmp_path: Path) -> None:
    final_dir = tmp_path / "out"
    staging = d4._new_staging_dir(final_dir)
    assert staging.is_dir()
    assert staging.parent == tmp_path
    assert staging.name.startswith(".out.staging-")
    assert staging != final_dir


def test_atomic_publish_render_set_places_all_points_when_finals_absent(
    tmp_path: Path,
) -> None:
    """3点（WAV dir・群 doc・manifest）とも最終物が無ければ、staging 側の
    内容がそのまま3点とも最終位置へ配置される。"""
    wav_dir = tmp_path / "out"
    staging_wav = d4._new_staging_dir(wav_dir)
    (staging_wav / "cell.wav").write_bytes(b"NEW-WAV")
    staging_doc = tmp_path / f"{staging_wav.name}.doc.json"
    staging_doc.write_text('{"v": "new-doc"}', encoding="utf-8")
    staging_manifest = tmp_path / f"{staging_wav.name}.manifest.json"
    staging_manifest.write_text('{"v": "new-manifest"}', encoding="utf-8")
    doc_path = tmp_path / "run5_pjs.json"
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"

    d4._atomic_publish_render_set([
        (staging_wav, wav_dir, "dir"),
        (staging_doc, doc_path, "file"),
        (staging_manifest, manifest_path, "file"),
    ])

    assert (wav_dir / "cell.wav").read_bytes() == b"NEW-WAV"
    assert doc_path.read_text(encoding="utf-8") == '{"v": "new-doc"}'
    assert manifest_path.read_text(encoding="utf-8") == '{"v": "new-manifest"}'
    assert not staging_wav.exists()
    assert not staging_doc.exists()
    assert not staging_manifest.exists()


def test_atomic_publish_render_set_replaces_existing_set_and_removes_backups(
    tmp_path: Path,
) -> None:
    """既存の3点セットがある場合: 各点を退避 → 置換 → 退避削除の3段で公開する。"""
    wav_dir = tmp_path / "out"
    wav_dir.mkdir()
    (wav_dir / "old.wav").write_bytes(b"OLD-WAV")
    doc_path = tmp_path / "run5_pjs.json"
    doc_path.write_text('{"v": "old-doc"}', encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    manifest_path.write_text('{"v": "old-manifest"}', encoding="utf-8")

    staging_wav = d4._new_staging_dir(wav_dir)
    (staging_wav / "new.wav").write_bytes(b"NEW-WAV")
    staging_doc = tmp_path / f"{staging_wav.name}.doc.json"
    staging_doc.write_text('{"v": "new-doc"}', encoding="utf-8")
    staging_manifest = tmp_path / f"{staging_wav.name}.manifest.json"
    staging_manifest.write_text('{"v": "new-manifest"}', encoding="utf-8")

    d4._atomic_publish_render_set([
        (staging_wav, wav_dir, "dir"),
        (staging_doc, doc_path, "file"),
        (staging_manifest, manifest_path, "file"),
    ])

    assert (wav_dir / "new.wav").read_bytes() == b"NEW-WAV"
    assert not (wav_dir / "old.wav").exists()
    assert doc_path.read_text(encoding="utf-8") == '{"v": "new-doc"}'
    assert manifest_path.read_text(encoding="utf-8") == '{"v": "new-manifest"}'
    leftovers = [p for p in tmp_path.iterdir() if ".pre-publish-" in p.name]
    assert leftovers == []


def test_atomic_publish_render_set_rolls_back_all_points_when_one_placement_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    """単一トランザクション公開の回帰（PR #306 レビュー第10巡 P1）:
    3点のうち 1 点（ここでは群 doc）の最終配置が失敗したら、既に配置できて
    しまった点（WAV dir）も staging 側へ戻し、退避しておいた旧3点セットを
    全点ぶん最終位置へ戻す——「全部公開されるか、1 つも公開されないか」の
    2状態しか許さず、部分公開（WAV だけ新しく doc/manifest は古いまま等）を
    起こさない。"""
    wav_dir = tmp_path / "out"
    wav_dir.mkdir()
    (wav_dir / "old.wav").write_bytes(b"OLD-WAV")
    doc_path = tmp_path / "run5_pjs.json"
    doc_path.write_text('{"v": "old-doc"}', encoding="utf-8")
    manifest_path = tmp_path / "run5_pjs_render_manifest.json"
    manifest_path.write_text('{"v": "old-manifest"}', encoding="utf-8")

    staging_wav = d4._new_staging_dir(wav_dir)
    (staging_wav / "new.wav").write_bytes(b"NEW-WAV")
    staging_doc = tmp_path / f"{staging_wav.name}.doc.json"
    staging_doc.write_text('{"v": "new-doc"}', encoding="utf-8")
    staging_manifest = tmp_path / f"{staging_wav.name}.manifest.json"
    staging_manifest.write_text('{"v": "new-manifest"}', encoding="utf-8")

    real_replace = d4.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        # 呼び出し順: 1-3 回目 = 3点の退避（成功させる）。4 回目 = WAV dir の
        # 本番配置（成功させる）。5 回目 = 群 doc の本番配置（ここで失敗させる
        # — コーディネータ指定シナリオ「doc 書込を失敗させる」）。
        if call_count["n"] == 5:
            raise OSError("synthetic doc placement failure")
        return real_replace(src, dst)

    monkeypatch.setattr(d4.os, "replace", _flaky_replace)

    with pytest.raises(OSError, match="synthetic doc placement failure"):
        d4._atomic_publish_render_set([
            (staging_wav, wav_dir, "dir"),
            (staging_doc, doc_path, "file"),
            (staging_manifest, manifest_path, "file"),
        ])

    # 旧3点セットが無傷（部分公開なし）。
    assert (wav_dir / "old.wav").read_bytes() == b"OLD-WAV"
    assert not (wav_dir / "new.wav").exists()
    assert doc_path.read_text(encoding="utf-8") == '{"v": "old-doc"}'
    assert manifest_path.read_text(encoding="utf-8") == '{"v": "old-manifest"}'


def test_cmd_render_leaves_old_out_dir_untouched_when_run_group_raises(
    tmp_path: Path, spec_and_sha, monkeypatch,
) -> None:
    """render の atomic 公開の end-to-end 回帰（PR #306 レビュー第9巡 P1）:
    `probe0b.run_group` が途中で例外を送出しても、staging へ書いているだけ
    なので `--out-dir` の既存セット（前回実行の成果物）は無傷のまま残り、
    群 doc / manifest も一切書かれない（中断時は staging が孤児として残る
    だけ、という設計を実際の `cmd_render` 呼び出し経路で確認する）。"""
    spec, spec_sha = spec_and_sha
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old_wav = out_dir / "old_cell.wav"
    old_wav.write_bytes(b"OLD-WAV-BYTES")

    export_manifest_path = tmp_path / "export.json"
    export_manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        d4.xm, "verify_export_manifest",
        lambda *a, **kw: {"source_checkpoint_sha256": "0" * 64},
    )
    # onnxruntime 非依存を維持する（ファイル冒頭 docstring）ため、実 gate_synth
    # モジュールは import しない — `run_group` 自体を即座に raise させるので、
    # 渡した値が何であるかは関係ない。
    monkeypatch.setattr(d4.probe0b, "load_gate_synth", lambda: object())

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic mid-render failure")

    monkeypatch.setattr(d4.probe0b, "run_group", _boom)

    result_out = tmp_path / "run5_pjs.json"
    ns = argparse.Namespace(
        generation="run5", speaker="pjs",
        acoustic_dir=str(tmp_path / "acoustic"), acoustic_stem="stem",
        export_manifest=str(export_manifest_path),
        canon_model_dir=str(tmp_path / "canon"), vocoder_dir=str(tmp_path / "vocoder"),
        canon_phonemes_txt=str(tmp_path / "canon.txt"),
        out_dir=str(out_dir), result_out=str(result_out),
        spec_sha256=spec_sha,
    )

    with pytest.raises(RuntimeError, match="synthetic mid-render failure"):
        d4.cmd_render(ns)

    assert old_wav.read_bytes() == b"OLD-WAV-BYTES"
    assert not result_out.exists()
    staging_dirs = [
        p for p in out_dir.parent.iterdir() if p.name.startswith(f".{out_dir.name}.staging-")
    ]
    assert len(staging_dirs) == 1, "中断時は staging が孤児として残るはず"


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
