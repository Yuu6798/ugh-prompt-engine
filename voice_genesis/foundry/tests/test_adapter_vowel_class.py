"""test_adapter_vowel_class.py — VG-F1.1-A 母音クラス選択の検証。
合成 sp（既知の形状: ガウス型フォルマントピーク 2 個を持つ envelope）で
高速・実 vocadito 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "singer"))

import numpy as np
import pytest

import donor_bank as db
import phoneme_jp as pj
import units as un
import vowel_class as vc

SR = 24000
N_BINS = 513  # cheaptrick 相当のビン数感覚（テストでは値自体は任意）


def _synthetic_sp_row(f1_hz: float, f2_hz: float, n_bins: int = N_BINS, sr: int = SR) -> np.ndarray:
    """F1/F2 に相当する 2 ピークを持つ合成 sp 行（線形パワー領域）を作る。"""
    freqs = np.linspace(0.0, sr / 2.0, n_bins)

    def _peak(center: float, width_hz: float = 80.0) -> np.ndarray:
        return np.exp(-0.5 * ((freqs - center) / width_hz) ** 2)

    row = 0.05 + 3.0 * _peak(f1_hz) + 2.0 * _peak(f2_hz) + 0.3 * _peak(3200.0, 200.0)
    return row


def _synthetic_sp_block(f1_hz: float, f2_hz: float, n_frames: int = 20) -> np.ndarray:
    row = _synthetic_sp_row(f1_hz, f2_hz)
    return np.tile(row[None, :], (n_frames, 1))


def test_estimate_f1_f2_recovers_known_peaks() -> None:
    sp = _synthetic_sp_block(f1_hz=500.0, f2_hz=2100.0)
    f1, f2 = vc.estimate_f1_f2(sp, SR)
    assert f1 is not None and f2 is not None
    assert f1 == pytest.approx(500.0, abs=40.0)
    assert f2 == pytest.approx(2100.0, abs=40.0)


def test_estimate_f1_f2_empty_returns_none() -> None:
    sp = np.zeros((0, N_BINS))
    f1, f2 = vc.estimate_f1_f2(sp, SR)
    assert f1 is None and f2 is None


def test_estimate_f1_f2_respects_f2_gap_constraint() -> None:
    # F1, F2 を非常に近接させて構成しても F2 探索は F1+150Hz 以上に制約される
    sp = _synthetic_sp_block(f1_hz=800.0, f2_hz=850.0)
    f1, f2 = vc.estimate_f1_f2(sp, SR)
    assert f1 is not None and f2 is not None
    assert f2 >= f1 + vc.F2_F1_MIN_GAP_HZ - 1.0


@pytest.mark.parametrize("vowel", list(vc.VOWEL_PROTOTYPES.keys()))
def test_classify_vowel_recovers_prototype_label(vowel: str) -> None:
    f1_proto, f2_proto = vc.VOWEL_PROTOTYPES[vowel]
    result = vc.classify_vowel(f1_proto, f2_proto)
    assert result.label == vowel


def test_classify_vowel_low_confidence_when_margin_small() -> None:
    # 2 母音の中間点は margin が小さくなり "x" になるはず
    a_f1, a_f2 = vc.VOWEL_PROTOTYPES["a"]
    o_f1, o_f2 = vc.VOWEL_PROTOTYPES["o"]
    mid_f1 = float(np.exp((np.log(a_f1) + np.log(o_f1)) / 2.0))
    mid_f2 = float(np.exp((np.log(a_f2) + np.log(o_f2)) / 2.0))
    result = vc.classify_vowel(mid_f1, mid_f2)
    assert result.label == vc.LOW_CONFIDENCE_LABEL


def test_classify_vowel_none_input_is_low_confidence() -> None:
    result = vc.classify_vowel(None, None)
    assert result.label == vc.LOW_CONFIDENCE_LABEL
    assert result.best_distance is None
    assert result.margin is None


def _make_unit(index: int, start: int, end: int) -> db.DonorUnit:
    n = db.N_LOG_BANDS
    return db.DonorUnit(
        index=index, start_frame=start, end_frame=end, median_f0=220.0,
        duration_s=(end - start) * 5.0 / 1000.0, head_log_bands=np.zeros(n), tail_log_bands=np.zeros(n),
    )


class _FakeBank:
    def __init__(self, sp: np.ndarray, units):
        self.sp = sp
        self.sr = SR
        self.units = units


def test_classify_donor_units_uses_center_50pct_and_distribution_non_empty() -> None:
    # unit0: 先頭/末尾はノイズ(別母音相当)、中央 50% は "a" 形状 -> 中央を見れば a と分類されるはず
    a_row = _synthetic_sp_row(*vc.VOWEL_PROTOTYPES["a"])
    i_row = _synthetic_sp_row(*vc.VOWEL_PROTOTYPES["i"])
    n = 40
    sp = np.tile(i_row[None, :], (n, 1))
    sp[10:30] = a_row  # 中央 50% (n//4=10 .. n-n//4=30) を a 形状にする
    units = [_make_unit(0, 0, n)]
    bank = _FakeBank(sp, units)
    results = vc.classify_donor_units(bank)
    assert results[0].label == "a"


def test_classify_donor_units_degenerate_unit_is_low_confidence() -> None:
    units = [_make_unit(0, 5, 5)]  # 空区間 (end<=start)
    bank = _FakeBank(np.zeros((10, N_BINS)), units)
    results = vc.classify_donor_units(bank)
    assert results[0].label == vc.LOW_CONFIDENCE_LABEL


def test_vowel_distribution_counts_all_labels() -> None:
    results = {
        0: vc.VowelClassResult("a", 800.0, 1500.0, 0.1, 0.2),
        1: vc.VowelClassResult("a", 800.0, 1500.0, 0.1, 0.2),
        2: vc.VowelClassResult(vc.LOW_CONFIDENCE_LABEL, None, None, None, None),
    }
    dist = vc.vowel_distribution(results)
    assert dist["a"] == 2
    assert dist["x"] == 1
    assert dist["i"] == 0
    assert set(dist.keys()) == set(vc.VOWELS_5) | {vc.LOW_CONFIDENCE_LABEL}


# --- units.py 拡張: mora_to_vowel_target + 選択への母音制約 ---


def test_mora_to_vowel_target_plain_vowel() -> None:
    mora = pj.Mora(kana="か", onset="k", vowel="a")
    assert un.mora_to_vowel_target(mora) == "a"


def test_mora_to_vowel_target_moraic_nasal_is_unconstrained() -> None:
    mora = pj.Mora(kana="ん", onset=None, vowel="N", is_moraic_nasal=True)
    assert un.mora_to_vowel_target(mora) is None


def test_select_units_prefers_vowel_matching_candidate() -> None:
    # pitch/duration/concat が同等な 2 候補のうち、母音が一致する方を選ぶはず
    units = [_make_unit(0, 0, 100), _make_unit(1, 100, 200)]
    unit_vowels = {0: "o", 1: "a"}
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5, vowel_target="a")]
    sel, stats = un.select_units(targets, units, unit_vowels=unit_vowels)
    assert sel[0].unit.index == 1
    assert sel[0].vowel_selected == "a"
    assert stats["n_vowel_exact"] == 1
    assert stats["n_vowel_mismatch"] == 0


def test_select_units_near_vowel_fallback_records_event() -> None:
    # target="i" だが donor 側に i ラベルが皆無、e ラベルのみ存在 -> 近縁フォールバック
    units = [_make_unit(0, 0, 100), _make_unit(1, 100, 200)]
    unit_vowels = {0: "o", 1: "e"}
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5, vowel_target="i")]
    sel, stats = un.select_units(targets, units, unit_vowels=unit_vowels)
    assert sel[0].unit.index == 1
    assert sel[0].vowel_fallback is True
    assert stats["n_vowel_near_fallback"] == 1


def test_select_units_vowel_none_target_is_unconstrained_and_backward_compatible() -> None:
    units = [_make_unit(0, 0, 100)]
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5)]  # vowel_target 省略 -> None
    sel, stats = un.select_units(targets, units)  # unit_vowels 省略 -> 既存 v0 呼び出しと同一
    assert sel[0].vowel_target is None
    assert stats["n_vowel_unconstrained"] == 1
    assert stats["n_vowel_exact"] == 0


def test_select_units_expands_range_when_vowel_absent_in_initial_candidates() -> None:
    # 初期 3 半音レンジ内には母音不一致の unit しかないが、レンジを広げれば一致する unit がある
    near_unit = _make_unit(0, 0, 100)
    n = db.N_LOG_BANDS
    far_matching_unit = db.DonorUnit(
        index=1, start_frame=100, end_frame=200, median_f0=220.0 * 2 ** (10.0 / 12.0), duration_s=0.5,
        head_log_bands=np.zeros(n), tail_log_bands=np.zeros(n),
    )
    units = [near_unit, far_matching_unit]
    unit_vowels = {0: "u", 1: "a"}  # "u" は "a" の近縁フォールバック対象外（本当の不一致）
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5, vowel_target="a")]
    sel, stats = un.select_units(targets, units, unit_vowels=unit_vowels)
    assert sel[0].unit.index == 1
    assert sel[0].expanded is True
    assert stats["n_vowel_exact"] == 1
