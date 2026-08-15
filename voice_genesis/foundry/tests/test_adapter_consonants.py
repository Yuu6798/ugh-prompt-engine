"""test_adapter_consonants.py — VG-F1.1-B 子音オンセット加工の検証。
合成 (sp, ap) フレーム列で高速・実 vocadito 非依存。子音種別ごとの形状
（murmur の低域集中・バーストの高域・開放タイミング）と決定論を確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest

import consonants as cons
import vowel_class as vc

SR = 24000
FRAME_PERIOD_MS = 5.0
N_BINS = 257
N_FRAMES = 60  # 60 frames * 5ms = 300ms、全子音種の onset window より十分長い


def _flat_sp_ap(n_frames: int = N_FRAMES, n_bins: int = N_BINS, sp_value: float = 1.0, ap_value: float = 0.1):
    sp = np.full((n_frames, n_bins), sp_value, dtype=np.float64)
    ap = np.full((n_frames, n_bins), ap_value, dtype=np.float64)
    return sp, ap


def _band_energy(sp_row: np.ndarray, lo_hz: float, hi_hz: float, sr: int = SR) -> float:
    freqs = np.linspace(0.0, sr / 2.0, sp_row.shape[0])
    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    return float(np.sum(sp_row[mask]))


def test_apply_consonant_onset_none_onset_is_noop() -> None:
    sp, ap = _flat_sp_ap()
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, None, SR, FRAME_PERIOD_MS)
    assert event is None
    assert np.array_equal(sp_out, sp)
    assert np.array_equal(ap_out, ap)


def test_apply_consonant_onset_unknown_onset_is_noop() -> None:
    sp, ap = _flat_sp_ap()
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, "z", SR, FRAME_PERIOD_MS)
    assert event is None
    assert np.array_equal(sp_out, sp)
    assert np.array_equal(ap_out, ap)


@pytest.mark.parametrize("onset", list(cons.BURST_ONSETS))
def test_burst_ap_goes_to_one_and_duration_matches_source(onset: str) -> None:
    sp, ap = _flat_sp_ap(ap_value=0.05)
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, onset, SR, FRAME_PERIOD_MS)
    assert event is not None
    assert event.consonant_class == "burst"
    expected_n = max(1, min(N_FRAMES, int(round(cons.BURST_DUR_MS[onset] / FRAME_PERIOD_MS))))
    assert event.n_frames_processed == expected_n
    # window 内で ap は 1 に近づく（先頭付近でほぼ 1）
    assert ap_out[0].mean() > 0.9
    # window を過ぎたフレームは無改変
    assert np.array_equal(sp_out[expected_n:], sp[expected_n:])
    assert np.array_equal(ap_out[expected_n:], ap[expected_n:])


def test_burst_s_concentrates_energy_in_high_band_vs_burst_g_low_band() -> None:
    sp, ap = _flat_sp_ap()
    sp_s, _, _ = cons.apply_consonant_onset(sp, ap, "s", SR, FRAME_PERIOD_MS)
    sp_g, _, _ = cons.apply_consonant_onset(sp, ap, "g", SR, FRAME_PERIOD_MS)
    # s は 6000Hz 中心、g は 1800Hz 中心 -> s の方が高域(5000Hz+)エネルギー比率が高いはず
    s_high = _band_energy(sp_s[0], 5000.0, SR / 2.0)
    s_total = float(np.sum(sp_s[0]))
    g_high = _band_energy(sp_g[0], 5000.0, SR / 2.0)
    g_total = float(np.sum(sp_g[0]))
    assert (s_high / s_total) > (g_high / g_total)


def test_flap_amplitude_dip_and_duration() -> None:
    sp, ap = _flat_sp_ap()
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, "r", SR, FRAME_PERIOD_MS)
    assert event is not None and event.consonant_class == "flap"
    expected_n = max(1, min(N_FRAMES, int(round(cons.R_FLAP_DUR_MS / FRAME_PERIOD_MS))))
    assert event.n_frames_processed == expected_n
    # 振幅ディップ: 先頭フレームの総エネルギーは元より明確に小さい
    assert float(np.sum(sp_out[0])) < float(np.sum(sp[0])) * (cons.R_FLAP_AMP_SCALE + 0.1)
    # window を過ぎたフレームは無改変
    assert np.array_equal(sp_out[expected_n:], sp[expected_n:])
    # /r/ は ap を変更しない
    assert np.array_equal(ap_out, ap)


def test_breath_h_lifts_ap_and_preserves_shape() -> None:
    sp, ap = _flat_sp_ap(ap_value=0.1)
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, "h", SR, FRAME_PERIOD_MS)
    assert event is not None and event.consonant_class == "breath"
    expected_n = max(1, min(N_FRAMES, int(round(cons.H_DUR_MS / FRAME_PERIOD_MS))))
    assert event.n_frames_processed == expected_n
    assert ap_out[0].mean() > ap[0].mean()
    # 「後続母音の包絡で整形」= sp の周波数形状（ここでは一定値）は変えず振幅のみ落とす
    assert np.allclose(sp_out[0], sp_out[0, 0])  # 依然として全ビン一定（形状不変）
    assert float(sp_out[0, 0]) < float(sp[0, 0])


def test_nasal_murmur_concentrates_low_frequency_energy() -> None:
    sp, ap = _flat_sp_ap()
    for onset in cons.NASAL_ONSETS:
        sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, onset, SR, FRAME_PERIOD_MS)
        assert event is not None and event.consonant_class == "nasal"
        # murmur 区間の最初のフレームは低域(0-500Hz)比率が母音一定包絡より高いはず
        low_share_out = _band_energy(sp_out[0], 0.0, 500.0) / float(np.sum(sp_out[0]))
        low_share_in = _band_energy(sp[0], 0.0, 500.0) / float(np.sum(sp[0]))
        assert low_share_out > low_share_in


def test_nasal_locus_f2_differs_between_n_and_m() -> None:
    sp, ap = _flat_sp_ap()
    murmur_frames = max(1, min(N_FRAMES, int(round(cons.NASAL_MURMUR_DUR_MS / FRAME_PERIOD_MS))))
    sp_n, _, _ = cons.apply_consonant_onset(sp, ap, "n", SR, FRAME_PERIOD_MS)
    sp_m, _, _ = cons.apply_consonant_onset(sp, ap, "m", SR, FRAME_PERIOD_MS)
    # locus 区間先頭フレーム（フェード窓の重み=1、加工が完全にかかる位置）
    locus_idx = min(murmur_frames, N_FRAMES - 1)
    # n: F2 locus=1700Hz, m: F2 locus=1000Hz -> n の方が 1400-2000Hz 帯のエネルギー比率が高いはず
    n_share = _band_energy(sp_n[locus_idx], 1400.0, 2000.0) / float(np.sum(sp_n[locus_idx]))
    m_share = _band_energy(sp_m[locus_idx], 1400.0, 2000.0) / float(np.sum(sp_m[locus_idx]))
    assert n_share > m_share


def test_glide_starts_from_source_vowel_and_converges_to_original() -> None:
    sp, ap = _flat_sp_ap()
    for onset in cons.GLIDE_ONSETS:
        sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, onset, SR, FRAME_PERIOD_MS)
        assert event is not None and event.consonant_class == "glide"
        expected_n = max(1, min(N_FRAMES, int(round(cons.GLIDE_DUR_MS / FRAME_PERIOD_MS))))
        assert event.n_frames_processed == expected_n
        # window 先頭は加工され、末尾（window の最後のフレーム）はほぼ元に収束
        assert not np.array_equal(sp_out[0], sp[0])
        assert np.allclose(sp_out[expected_n - 1], sp[expected_n - 1], rtol=1e-6, atol=1e-6)
        # window 後は完全に無改変
        assert np.array_equal(sp_out[expected_n:], sp[expected_n:])


def test_glide_y_and_w_differ() -> None:
    sp, ap = _flat_sp_ap()
    sp_y, _, _ = cons.apply_consonant_onset(sp, ap, "y", SR, FRAME_PERIOD_MS)
    sp_w, _, _ = cons.apply_consonant_onset(sp, ap, "w", SR, FRAME_PERIOD_MS)
    assert not np.allclose(sp_y[0], sp_w[0])


@pytest.mark.parametrize("onset", ["s", "k", "t", "g", "r", "h", "m", "n", "y", "w"])
def test_apply_consonant_onset_deterministic_repeat(onset: str) -> None:
    sp, ap = _flat_sp_ap()
    sp1, ap1, ev1 = cons.apply_consonant_onset(sp, ap, onset, SR, FRAME_PERIOD_MS)
    sp2, ap2, ev2 = cons.apply_consonant_onset(sp, ap, onset, SR, FRAME_PERIOD_MS)
    assert np.array_equal(sp1, sp2)
    assert np.array_equal(ap1, ap2)
    assert ev1 == ev2


def test_apply_consonant_onset_empty_sp_returns_none_event() -> None:
    sp = np.zeros((0, N_BINS))
    ap = np.zeros((0, N_BINS))
    sp_out, ap_out, event = cons.apply_consonant_onset(sp, ap, "k", SR, FRAME_PERIOD_MS)
    assert event is None
    assert sp_out.shape == (0, N_BINS)


def test_glide_source_vowel_prototypes_reused_from_vowel_class() -> None:
    assert cons.GLIDE_SOURCE_VOWEL["y"] == "i"
    assert cons.GLIDE_SOURCE_VOWEL["w"] == "u"
    assert cons.GLIDE_SOURCE_VOWEL["y"] in vc.VOWEL_PROTOTYPES
    assert cons.GLIDE_SOURCE_VOWEL["w"] in vc.VOWEL_PROTOTYPES
