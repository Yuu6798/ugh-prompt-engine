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


# --- PR#261 レビュー R32: quick_s5() の f0_pass 不完全証拠 PASS 修正 --------


class _FakeNote:
    def __init__(self, phrase_index):
        self.phrase_index = phrase_index


class _FakeSegment:
    def __init__(self, phrase_index):
        self.note = _FakeNote(phrase_index)


class _FakeRenderResult:
    """`quick_s5()` が要求する最小属性（segments/wav/sr）だけを持つダミー。
    実音声合成を回避し、f0_pass ロジックだけを高速に検証する。"""

    def __init__(self, n_notes):
        self.segments = [_FakeSegment(0) for _ in range(n_notes)]
        self.wav = np.zeros(4)
        self.sr = 44100


def _patch_quick_s5_periphery(monkeypatch):
    """quick_s5() の f0_pass 以外の部分（periodicity/aliasing）を軽量ダミーに
    差し替え、実音声合成なしで f0_pass ロジックだけを検証できるようにする。"""
    monkeypatch.setattr(
        gv.rs, "render_sustained_vowel", lambda genome, midi, vowel="a", duration_sec=0.6: np.zeros(4)
    )
    monkeypatch.setattr(
        gv.m3,
        "periodicity_track_v3",
        lambda wav, sr, fmin, fmax: gv.m3.PeriodicityTrackV3(
            r_median=0.9, periodicity_db_median=0.0, n_frames=1, n_voiced_frames=1
        ),
    )
    monkeypatch.setattr(gv.gc, "gate5_aliasing", lambda wav, sr: {"pass": True, "energy_ratio_db": -50.0})


def test_quick_s5_f0_pass_fails_when_one_of_four_notes_is_non_finite(monkeypatch):
    """QUICK_N_NOTES（4音）のうち 1 音の cents_err が非有限（未測定）でも、
    旧実装は「非有限を除外済みリストの非空判定」のみで f0_pass を決めていた
    ため、残り 3 音が健全であれば黙って PASS し得た（4 音全てを測ったつもり
    が実は 3 音しか測れていない不完全証拠 PASS。PR#261 R32）。
    """
    _patch_quick_s5_periphery(monkeypatch)

    rows = [
        gv.gc.NoteMeasurement(
            note_index=0, kana="あ", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=1.0, r_median=0.9
        ),
        gv.gc.NoteMeasurement(
            note_index=1, kana="い", true_midi=60.0, true_hz=261.6, est_hz=261.6,
            cents_err=float("nan"), r_median=0.9,
        ),  # 1 音欠測（未測定）
        gv.gc.NoteMeasurement(
            note_index=2, kana="う", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=2.0, r_median=0.9
        ),
        gv.gc.NoteMeasurement(
            note_index=3, kana="え", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=1.5, r_median=0.9
        ),
    ]
    monkeypatch.setattr(gv.gc, "measure_notes", lambda result: rows)

    result = _FakeRenderResult(n_notes=len(rows))
    q = gv.quick_s5(genome_in=None, full_result=result)

    assert q.f0_pass is False
    assert q.overall_pass is False


def test_quick_s5_f0_pass_true_when_all_four_notes_finite_and_within_threshold_non_regression(monkeypatch):
    """非退行確認: 4 音全てが有限かつ閾値内なら従来どおり f0_pass=True になる。"""
    _patch_quick_s5_periphery(monkeypatch)

    rows = [
        gv.gc.NoteMeasurement(
            note_index=i, kana="あ", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=1.0 + i, r_median=0.9
        )
        for i in range(4)
    ]
    monkeypatch.setattr(gv.gc, "measure_notes", lambda result: rows)

    result = _FakeRenderResult(n_notes=len(rows))
    q = gv.quick_s5(genome_in=None, full_result=result)

    assert q.f0_pass is True


# --- PR#261 レビュー R36: quick_s5() の f0_pass に音数そのものの退行チェックを追加 ---


def test_quick_s5_f0_pass_fails_when_only_three_of_four_notes_measured(monkeypatch):
    """phrase0 のノート数が退行で 3 音しか無い場合、`phrase0_rows`/`raw_errs`
    は静かに 3 要素になるだけで例外にならない。R32 の `all_finite` は「取れた
    音は全部有限か」しか見ておらず「そもそも 4 音取れたか」を見ていないため、
    3 音全てが有限かつ閾値内なら旧実装は f0_pass=True になり得た（1 音が
    測定すらされていない、R32 よりさらに手前の不完全証拠 PASS。PR#261 R36）。
    """
    _patch_quick_s5_periphery(monkeypatch)

    rows = [
        gv.gc.NoteMeasurement(
            note_index=i, kana="あ", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=1.0 + i, r_median=0.9
        )
        for i in range(3)  # 4 音のはずが 3 音しか返らない退行
    ]
    monkeypatch.setattr(gv.gc, "measure_notes", lambda result: rows)

    result = _FakeRenderResult(n_notes=len(rows))
    q = gv.quick_s5(genome_in=None, full_result=result)

    assert q.f0_pass is False
    assert q.overall_pass is False


def test_quick_s5_f0_pass_true_when_exactly_four_notes_measured_non_regression(monkeypatch):
    """非退行確認: phrase0 に (QUICK_N_NOTES を超える) 5 音あっても
    `[:QUICK_N_NOTES]` で先頭 4 音に切り詰められ、4 音全てが有限かつ閾値内
    なら従来どおり f0_pass=True になる（R36 の音数チェックは
    `len(raw_errs) == QUICK_N_NOTES` であり、スライス後の 4 音と一致する）。
    """
    _patch_quick_s5_periphery(monkeypatch)

    rows = [
        gv.gc.NoteMeasurement(
            note_index=i, kana="あ", true_midi=60.0, true_hz=261.6, est_hz=261.6, cents_err=1.0 + i, r_median=0.9
        )
        for i in range(5)  # phrase0 に 5 音（QUICK_N_NOTES=4 より多い）
    ]
    monkeypatch.setattr(gv.gc, "measure_notes", lambda result: rows)

    result = _FakeRenderResult(n_notes=len(rows))
    q = gv.quick_s5(genome_in=None, full_result=result)

    assert q.f0_pass is True
