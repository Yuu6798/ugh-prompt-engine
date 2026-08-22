"""test_s7_gate_synth_dur_hook.py — Stage 1 フックと 8-0b 診断ローダの番人。

run 8 の校正レンダのために `run_pipeline` へ足した `final_phone_dur_override`
（既定 None）が、**off のとき本番経路を 1 命令も変えない**ことと、on のときの
不変量だけを見る。`onnxruntime` を import するため既定 testpaths には入れない
（`voice_genesis/foundry/tests/` の既存 gate_synth テストと同じ扱い）。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_gate_synth_dur_hook.py -q`
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")

_FOUNDRY = Path(__file__).resolve().parent.parent
GATE_SYNTH_PATH = _FOUNDRY / "s1_gate" / "gate_synth.py"


def _load_gate_synth():
    spec = importlib.util.spec_from_file_location("gate_synth", GATE_SYNTH_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_synth"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gs():
    return _load_gate_synth()


def _reference_final_phone_dur(ph_dur_pred, note_phone_counts, note_target_frames):
    """フック導入**前**の `run_pipeline` Stage 1 に書かれていた算術の写し。

    抽出（`compute_final_phone_dur`）で丸め方・残差の寄せ先が変わっていない
    ことを、独立実装との一致で拘束する。
    """
    final_phone_dur = []
    offset = 1
    for count, target in zip(note_phone_counts, note_target_frames):
        pred_slice = ph_dur_pred[offset: offset + count]
        pred_sum = float(pred_slice.sum())
        if pred_sum <= 0:
            rescaled = [target / count] * count
        else:
            ratio = target / pred_sum
            rescaled = [float(x) * ratio for x in pred_slice]
        rounded = [int(round(x)) for x in rescaled]
        resid = target - sum(rounded)
        rounded[-1] += resid
        final_phone_dur.extend(rounded)
        offset += count
    return final_phone_dur


CASES = [
    ([0.5, 3.1, 7.9, 2.0, 6.0], [2, 2], [11, 8]),
    ([0.5, 1.0], [1], [108]),
    ([0.5, 0.0, 0.0], [2], [40]),          # 予測和 0 -> 均等割り
    ([0.5, 9.0, 1.0, 1.0], [3], [7]),
]


@pytest.mark.parametrize("pred,counts,targets", CASES)
def test_extracted_helper_matches_pre_hook_arithmetic(gs, pred, counts, targets):
    arr = np.asarray(pred, dtype=np.float64)
    got = gs.compute_final_phone_dur(arr, counts, targets)
    assert got == _reference_final_phone_dur(arr, counts, targets)
    assert sum(got) == sum(targets)


def test_override_defaults_to_none(gs):
    sig = inspect.signature(gs.run_pipeline)
    assert sig.parameters["final_phone_dur_override"].default is None


def test_override_is_the_only_added_parameter(gs):
    """本番呼び出し（`synth_song`）が渡す引数は増えていない。"""
    src = inspect.getsource(gs.synth_song)
    assert "final_phone_dur_override" not in src


def test_strict_sum_assert_still_guards_the_production_path(gs):
    """override 無しの経路では総和一致 assert が残っていること（規律の明文化）。"""
    src = inspect.getsource(gs.resolve_final_phone_dur)
    assert "if override is None:" in src
    assert "assert sum(final_phone_dur) == sum(note_target_frames)" in src


# --- default-off path が介入を受けないことのコードレベル拘束 -----------------
# User 裁定 2026-08-21 STEP 6:「gate_sakura_n4.wav の単発 SHA 一致だけでなく、
# default-off path が intervention を受けないことをコードレベルでも assert する」


def _fn_ast(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _binding_nodes(fn_ast, name):
    """`name` を束縛する代入（通常 / 拡張 / 注釈 / for / with as / walrus）を集める。"""
    out = []
    for node in ast.walk(fn_ast):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.For):
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Name) and sub.id == name:
                    out.append(node)
                elif isinstance(sub, ast.Subscript):
                    base = sub.value
                    if isinstance(base, ast.Name) and base.id == name:
                        out.append(node)
    return out


def test_run_pipeline_binds_final_phone_dur_exactly_once(gs):
    """`run_pipeline` 内で `final_phone_dur` を作るのは 1 箇所だけ。

    ここが 1 箇所である限り、override=None の戻り値（= 予測どおりの配分）が
    後段で書き換えられる経路は構文上存在しない。
    """
    fn = _fn_ast(gs.run_pipeline)
    bindings = _binding_nodes(fn, "final_phone_dur")
    assert len(bindings) == 1, [ast.dump(b)[:120] for b in bindings]
    node = bindings[0]
    assert isinstance(node, ast.Assign)
    assert isinstance(node.value, ast.Call)
    assert isinstance(node.value.func, ast.Name)
    assert node.value.func.id == "resolve_final_phone_dur"


def test_default_off_returns_prediction_untouched_and_writes_no_record(gs):
    """override=None のとき、戻り値は予測そのもので `record` は空のまま。"""
    pred = np.asarray([0.5, 3.1, 7.9, 2.0, 6.0], dtype=np.float64)
    counts, targets, phones = [2, 2], [11, 8], ["r", "i", "s", "a"]
    record = {}
    got = gs.resolve_final_phone_dur(pred, counts, targets, phones, 11.6, record, None)
    assert got == gs.compute_final_phone_dur(pred, counts, targets)
    assert record == {}


def test_override_on_is_recorded_as_calibration_only_intervention(gs):
    pred = np.asarray([0.5, 3.1, 7.9], dtype=np.float64)
    counts, targets, phones = [2], [11], ["r", "i"]
    record = {}
    got = gs.resolve_final_phone_dur(
        pred, counts, targets, phones, 11.6, record, lambda d, ctx: [4, 7]
    )
    assert got == [4, 7]
    assert record["stage1_calibration_only_intervention"] is True
    assert record["stage1_final_phone_dur_predicted"] == gs.compute_final_phone_dur(
        pred, counts, targets
    )
    assert record["stage1_final_phone_dur"] == [4, 7]


def test_override_rejects_bad_shapes(gs):
    pred = np.asarray([0.5, 3.1, 7.9], dtype=np.float64)
    counts, targets, phones = [2], [11], ["r", "i"]
    with pytest.raises(ValueError):
        gs.resolve_final_phone_dur(pred, counts, targets, phones, 11.6, {}, lambda d, c: [11])
    with pytest.raises(ValueError):
        gs.resolve_final_phone_dur(pred, counts, targets, phones, 11.6, {}, lambda d, c: [0, 11])


# --- PR-2: 診断スコアモジュールのローダ拡張（既存 sakura/umi は不変） --------


def test_sakura_and_umi_loading_is_unchanged(gs, tmp_path):
    """既存 2 曲は返り値の形も provenance sha も従来どおり。"""
    singer = Path(gs.DEFAULT_SINGER_DIR)
    build, b2s, tempo, path, shas = gs.load_song_module("sakura", singer)
    assert path == (singer / "score.py").resolve()
    assert tempo == 72.0
    assert set(shas) == {"score_module_sakura", "score_module_sakura_dep_phoneme_jp"}
    assert len(build()) > 0
    build_u, _, tempo_u, path_u, shas_u = gs.load_song_module("umi", singer)
    assert path_u == (singer / "score_umi.py").resolve()
    assert tempo_u == 88.0
    assert set(shas_u) == {
        "score_module_umi",
        "score_module_umi_dep_score",
        "score_module_umi_dep_phoneme_jp",
    }
    assert len(build_u()) > 0
    for _, (p, sha) in list(shas.items()) + list(shas_u.items()):
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha


def test_diagnostic_song_loads_a_single_cell(gs):
    singer = Path(gs.DEFAULT_SINGER_DIR)
    song = gs.DIAGNOSTIC_SONG_PREFIX + "P-RI-FINAL|low|b4"
    build, b2s, tempo, path, shas = gs.load_song_module(song, singer)
    assert path.name == gs.DIAGNOSTIC_SCORE_MODULE
    assert tempo == 72.0
    notes = build()
    assert len(notes) == 1
    assert notes[0].midi == 57.0 and notes[0].duration_beats == 4.0
    assert notes[0].mora.onset == "r" and notes[0].mora.vowel == "i"
    # provenance のキー命名は既存規約と同型で、実体と一致する
    assert f"score_module_{song}" in shas
    for _, (p, sha) in shas.items():
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha


def test_unknown_song_and_unknown_cell_are_rejected(gs):
    singer = Path(gs.DEFAULT_SINGER_DIR)
    with pytest.raises(ValueError):
        gs.load_song_module("kagome", singer)
    with pytest.raises(ValueError):
        gs.load_song_module(gs.DIAGNOSTIC_SONG_PREFIX + "NOT-A-CELL", singer)
    with pytest.raises(ValueError):
        gs.load_song_module(gs.DIAGNOSTIC_SONG_PREFIX, singer)


def test_measurement_out_defaults_to_none(gs):
    sig = inspect.signature(gs.run_pipeline)
    assert sig.parameters["measurement_out"].default is None
    src = inspect.getsource(gs.synth_song)
    assert "measurement_out" not in src
