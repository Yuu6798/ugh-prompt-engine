"""test_genesis_v0.py — PR#261 レビュー R21: linkability_audit の非有限値 fail-closed 化。

`linkability_audit()` は事前計算済みの numpy ベクトルのみを扱う純粋関数なので、
曲全体の音声合成を伴わない軽量テストとして書ける（重い `render_sakura` 系
テストは `test_machine_gates.py` 側に集約する運用を踏襲）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import genesis_v0 as gv


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


def _reference_ok():
    return {
        "ref_a": (_vec(1.0, 0.0, 0.0), _vec(0.0, 1.0, 0.0)),
        "ref_b": (_vec(0.0, 1.0, 0.0), _vec(1.0, 0.0, 0.0)),
    }


def test_linkability_audit_passes_for_finite_novel_candidate():
    """非退行確認: 全て有限で参照から十分離れた候補は従来どおり合格する。"""
    candidate = (_vec(-1.0, -1.0, 0.0), _vec(-1.0, -1.0, 0.0))
    result = gv.linkability_audit(candidate, _reference_ok())
    assert result.measurement_valid is True
    assert result.passed is True
    assert np.isfinite(result.margin)


def test_linkability_audit_fails_for_finite_candidate_too_close_to_reference():
    """非退行確認: 参照そのものを候補にすれば不合格になる（監査ロジック自体は不変）。"""
    ref_a_e1, ref_a_e2 = _reference_ok()["ref_a"]
    result = gv.linkability_audit((ref_a_e1, ref_a_e2), _reference_ok())
    assert result.measurement_valid is True
    assert result.passed is False
    assert np.isfinite(result.margin)


def test_linkability_audit_rejects_nan_in_candidate_vector():
    """候補ベクトル自体に NaN が混入していれば、監査不能として即 FAIL する。

    旧実装は `if d1 < best_e1:` のみで最小値を更新するため、NaN 距離は比較が
    常に False になり `best_e1` が初期値 `float("inf")` のまま残り、
    `passed = best_e1 >= THRESHOLD` が `inf >= 0.01` で True になって
    しまっていた（PR#261 R21 の fail-open）。
    """
    candidate = (_vec(float("nan"), 0.0, 0.0), _vec(0.0, 1.0, 0.0))
    result = gv.linkability_audit(candidate, _reference_ok())
    assert result.measurement_valid is False
    assert result.passed is False
    assert result.margin == float("-inf")


def test_linkability_audit_rejects_inf_in_candidate_vector():
    candidate = (_vec(float("inf"), 0.0, 0.0), _vec(0.0, 1.0, 0.0))
    result = gv.linkability_audit(candidate, _reference_ok())
    assert result.measurement_valid is False
    assert result.passed is False
    assert result.margin == float("-inf")


def test_linkability_audit_rejects_nan_in_reference_vector():
    """参照側ベクトルに NaN が混入していても同様に監査不能として即 FAIL する。"""
    candidate = (_vec(-1.0, -1.0, 0.0), _vec(-1.0, -1.0, 0.0))
    reference = {
        "ref_a": (_vec(float("nan"), 0.0, 0.0), _vec(0.0, 1.0, 0.0)),
        "ref_b": (_vec(0.0, 1.0, 0.0), _vec(1.0, 0.0, 0.0)),
    }
    result = gv.linkability_audit(candidate, reference)
    assert result.measurement_valid is False
    assert result.passed is False
    assert result.margin == float("-inf")


def test_linkability_audit_does_not_report_infinite_margin_for_invalid_measurement():
    """旧実装の fail-open: best_e1/best_e2 が inf のまま更新されず margin が
    +inf に張り付き、`select_winner`/`select_final_winner_with_full_gates`
    の「margin 最大優先」ルールで測定不能な個体が最優先に選ばれ得た。

    修正後は margin が明示的な番兵値 -inf（正常などの候補よりも絶対に劣後
    する）になり、+inf には決してならないことを確認する。
    """
    candidate = (_vec(float("nan"), float("nan")), _vec(0.0, 1.0))
    reference = {"ref_a": (_vec(1.0, 0.0), _vec(1.0, 0.0))}
    result = gv.linkability_audit(candidate, reference)
    assert result.margin != float("inf")
    assert result.margin == float("-inf")


# --- 同型掃討: genesis_v1.py / genesis_v2.py は独自実装を持たず gv.linkability_audit を再利用 ---


def test_genesis_v1_reuses_genesis_v0_linkability_audit():
    import genesis_v1 as gv1
    assert gv1.gv.linkability_audit is gv.linkability_audit


def test_genesis_v2_reuses_genesis_v0_linkability_audit():
    import genesis_v2 as gv2
    assert gv2.gv.linkability_audit is gv.linkability_audit
