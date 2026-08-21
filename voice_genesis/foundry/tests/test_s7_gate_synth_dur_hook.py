"""test_s7_gate_synth_dur_hook.py — Stage 1 フレーム配分フックの番人。

run 8 の校正レンダのために `run_pipeline` へ足した `final_phone_dur_override`
（既定 None）が、**off のとき本番経路を 1 命令も変えない**ことと、on のときの
不変量だけを見る。`onnxruntime` を import するため既定 testpaths には入れない
（`voice_genesis/foundry/tests/` の既存 gate_synth テストと同じ扱い）。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_gate_synth_dur_hook.py -q`
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
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
    src = inspect.getsource(gs.run_pipeline)
    assert "if final_phone_dur_override is None:" in src
    assert "assert sum(final_phone_dur) == sum(note_target_frames)" in src
