"""Metamorphic property tests for the deterministic RPE pipeline.

`scripts/metamorphic_probe.py` を計器に、決定論パイプラインのメタモルフィック関係
（grip / 直交性 / 不変条件 / 決定論）を Hypothesis で掃引検証する。

注意: 実 extractor を回すため 1 例あたり数百 ms〜数秒かかる。`derandomize=True` で
CI を決定論化し、`max_examples` を絞って総実行時間を抑える（純ヘルパのユニットテストは
抽出を伴わず高速）。連続ノブ bpm の *校正* は合成器が追従しないため（docs 参照）
pass/fail にせず、ここでは「動く/不変/有界」の robust な関係のみ assert する。
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import scripts.metamorphic_probe as mp

# 実抽出を伴う Hypothesis テストの共通設定。deadline 無効化（抽出は遅い）、
# derandomize で決定論化、max_examples は総実行時間の予算から逆算して小さく。
EXTRACT_SETTINGS = settings(
    max_examples=4,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

key_strategy = st.sampled_from(mp.KEYS)
bpm_strategy = st.floats(min_value=85.0, max_value=145.0)
level_strategy = st.floats(min_value=0.0, max_value=1.0)


def _assert_invariants(phys) -> None:
    issues = mp.physical_invariants(phys)
    assert issues == [], f"invariant violations: {issues}"


# ---------------------------------------------------------------------------
# 純ヘルパのユニットテスト（抽出なし・高速）
# ---------------------------------------------------------------------------


def test_harmonic_weights_monotone_in_level() -> None:
    lo = mp.harmonic_weights_for_level(0.0)
    hi = mp.harmonic_weights_for_level(1.0)
    # 基音は固定、高次倍音は level とともに単調増加
    assert lo[0] == hi[0] == 1.0
    for lo_w, hi_w in zip(lo[1:], hi[1:]):
        assert hi_w > lo_w
    # クランプ: 範囲外 level は [0,1] に丸められる
    assert mp.harmonic_weights_for_level(-1.0) == mp.harmonic_weights_for_level(0.0)
    assert mp.harmonic_weights_for_level(2.0) == mp.harmonic_weights_for_level(1.0)


def test_grip_summary_detects_span_and_monotone() -> None:
    tight = mp.grip_summary([800.0, 850.0, 900.0, 960.0])
    assert tight["monotone_nondecreasing"] is True
    assert tight["span"] == pytest.approx(160.0)

    dead = mp.grip_summary([900.0, 900.0, 900.0])
    assert dead["span"] == 0.0
    assert dead["monotone_nondecreasing"] is True

    noisy = mp.grip_summary([900.0, 880.0, 910.0])
    assert noisy["monotone_nondecreasing"] is False


def test_key_templates_cover_authored_samples() -> None:
    assert len(mp.KEYS) == 5
    assert ("C", "major") in mp.KEYS
    assert ("F#", "minor") in mp.KEYS


def test_build_spec_applies_knobs() -> None:
    spec = mp.build_spec(key="C", mode="major", bpm=128.0, brightness_level=0.0, seed=42)
    assert spec.bpm == 128.0
    assert spec.seed == 42
    assert spec.harmonic_weights == mp.harmonic_weights_for_level(0.0)


# ---------------------------------------------------------------------------
# メタモルフィック関係（実抽出・Hypothesis 掃引）
# ---------------------------------------------------------------------------


@pytest.mark.slow
@EXTRACT_SETTINGS
@given(key=key_strategy, bpm=bpm_strategy)
def test_centroid_grip_and_key_orthogonality(key: tuple[str, str], bpm: float) -> None:
    """grip: brightness ノブを上げると spectral_centroid は単調非減少で動く。
    直交性: brightness ノブを回しても検出 key は変わらない。"""
    key_name, mode = key
    levels = [0.0, 0.33, 0.66, 1.0]
    centroids: list[float] = []
    detected_keys: set[tuple] = set()
    for level in levels:
        phys = mp.synth_extract(
            mp.build_spec(
                key=key_name, mode=mode, bpm=bpm, brightness_level=level, duration_sec=20.0
            )
        )
        _assert_invariants(phys)
        centroids.append(phys.spectral_centroid)
        detected_keys.add((phys.key, phys.mode))

    # grip（メタモルフィック単調性）: 数値ノイズ許容で非減少
    for earlier, later in zip(centroids, centroids[1:]):
        assert later >= earlier - 1.0, f"centroid not monotone: {centroids}"
    # tight grip: ツマミは実際にセンサーを動かす（dead でない）
    assert centroids[-1] - centroids[0] > 5.0, f"centroid grip too weak: {centroids}"
    # 直交性: brightness ノブは key 検出を反転させない
    assert len(detected_keys) == 1, f"brightness leaked into key: {detected_keys}"


@pytest.mark.slow
@EXTRACT_SETTINGS
@given(key=key_strategy, level=level_strategy)
def test_centroid_orthogonal_to_bpm(key: tuple[str, str], level: float) -> None:
    """直交性: tempo ノブを変えても brightness センサー(centroid)はほぼ不変。"""
    key_name, mode = key
    base = mp.build_spec(
        key=key_name, mode=mode, bpm=90.0, brightness_level=level, duration_sec=20.0
    )
    slow = mp.synth_extract(base)
    fast = mp.synth_extract(replace(base, bpm=140.0))
    _assert_invariants(slow)
    _assert_invariants(fast)
    # tempo を 90→140 に振っても centroid のずれは brightness grip 幅より遥かに小さい
    assert abs(slow.spectral_centroid - fast.spectral_centroid) < 50.0


@pytest.mark.slow
@settings(
    max_examples=3,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(key=key_strategy, bpm=bpm_strategy, level=level_strategy)
def test_extraction_is_deterministic(key: tuple[str, str], bpm: float, level: float) -> None:
    """決定論: 同一 spec → 同一 PhysicalRPE コア（同一入力→同一出力の基盤契約）。"""
    key_name, mode = key
    spec = mp.build_spec(
        key=key_name, mode=mode, bpm=bpm, brightness_level=level, duration_sec=18.0
    )
    first = mp.synth_extract(spec)
    second = mp.synth_extract(spec)
    assert first.spectral_centroid == second.spectral_centroid
    assert first.bpm == second.bpm
    assert (first.key, first.mode) == (second.key, second.mode)
    assert first.spectral_profile.brightness == second.spectral_profile.brightness


# ---------------------------------------------------------------------------
# 計測済み設計知見の回帰ガード（決定論・抽出あり）
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_brightness_high_band_is_blind_for_synth() -> None:
    """計測知見: 合成器の基音は <4kHz に留まるため高域比 brightness センサーは
    倍音を増やしても 0 のまま（センサー盲）。一方で centroid は動く（live sensor）。
    = 「ツマミ死」と「センサー盲」の判別を 1 ケースで固定する。"""
    dark = mp.synth_extract(mp.build_spec(brightness_level=0.0, duration_sec=20.0))
    bright = mp.synth_extract(mp.build_spec(brightness_level=1.0, duration_sec=20.0))
    # センサー盲: 高域比は両端で 0
    assert dark.spectral_profile.high_ratio == 0.0
    assert bright.spectral_profile.high_ratio == 0.0
    # しかし centroid センサーは tight grip で応答する
    assert bright.spectral_centroid > dark.spectral_centroid + 5.0


@pytest.mark.slow
def test_brightness_magnitude_brilliance_is_blind_for_synth() -> None:
    """`high_ratio==0.0` 前提の再点検（Q1-5 Ph2）。

    B-3 で brightness/ジャンル判別器は power `high_ratio` から magnitude
    `spectral_bands.brilliance`（6-20kHz）へ移った。power 盲を別センサーで救えるかを
    確かめると、magnitude brilliance は power と違い **非ゼロ floor**（≈0.02、magnitude は
    スペクトル漏れ/ノイズ床を拾う）を持つが、brightness ノブには **平坦＝grip 死**。
    つまり「power 盲を magnitude に替えてもジャンル brightness は合成器で叩けない」を固定し、
    合成器の倍音は magnitude `mid`（500-2kHz）へ流れ込む（centroid だけが live sensor）。"""
    dark = mp.synth_extract(mp.build_spec(brightness_level=0.0, duration_sec=20.0))
    bright = mp.synth_extract(mp.build_spec(brightness_level=1.0, duration_sec=20.0))
    assert dark.spectral_bands is not None
    assert bright.spectral_bands is not None
    # power high_ratio とは違い magnitude brilliance は非ゼロ floor を持つ
    assert dark.spectral_bands.brilliance > 0.0
    assert bright.spectral_bands.brilliance > 0.0
    # だが grip は死んでいる: ノブ両端で brilliance はほぼ不変（実測 span~7e-4 << 0.01）
    assert abs(bright.spectral_bands.brilliance - dark.spectral_bands.brilliance) < 0.01
    # ノブのエネルギーは brilliance ではなく magnitude mid 帯（500-2kHz）へ流れる
    assert bright.spectral_bands.mid > dark.spectral_bands.mid + 0.02
    # centroid は依然 live sensor（同一ノブで応答）
    assert bright.spectral_centroid > dark.spectral_centroid + 5.0
