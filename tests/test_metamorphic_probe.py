"""Metamorphic property tests for the deterministic RPE pipeline.

`scripts/metamorphic_probe.py` を計器に、決定論パイプラインのメタモルフィック関係
（grip / 直交性 / 不変条件 / 決定論）を Hypothesis で掃引検証する。

注意: 実 extractor を回すため 1 例あたり数百 ms〜数秒かかる。`derandomize=True` で
CI を決定論化し、`max_examples` を絞って総実行時間を抑える（純ヘルパのユニットテストは
抽出を伴わず高速）。連続ノブ bpm の *校正* は合成器が追従しないため（docs 参照）
pass/fail にせず、ここでは「動く/不変/有界」の robust な関係のみ assert する。
"""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import scripts.metamorphic_probe as mp
from svp_rpe.rpe.extractor import extract_physical_from_file
from svp_rpe.rpe.models import PhysicalRPE

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
# T-INV (E2E) — Metrics v2 レベル不変性の実経路掃引（#188 follow-up, 層②）。
# tests/test_metrics_v2.py の純関数掃引（層①）に対し、こちらは
# render_sample → WAV 書き出し → extract_physical_from_file という実抽出経路
# を通しても v2 フィールドがゲインシフト不変であることを実証する。
# ---------------------------------------------------------------------------

gain_db_strategy = st.floats(min_value=-24.0, max_value=6.0)


def _write_and_extract(y: np.ndarray, sr: int = mp.SAMPLE_RATE) -> PhysicalRPE:
    """WAV (FLOAT subtype) 経由で実 extract する。

    設計判断 (a): `synth_extract` の PCM_16 書き出しは深い減衰でビット深度量子化が
    起き、それ自体がレベル不変性を壊してしまう（16bit の量子化ステップは絶対振幅に
    比例するので、-24dB 減衰後の信号は有効ビット数が減り誤差が相対的に拡大する）。
    そのため本ヘルパーは soundfile `subtype="FLOAT"`（32bit float, 量子化なし）で
    書き出す。抽出器（`librosa.load` 経由）が float WAV を問題なく読めることは
    本テストの実行そのもので確認している。
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe_gain.wav"
        sf.write(str(path), y, sr, subtype="FLOAT")
        return extract_physical_from_file(str(path))


@pytest.mark.slow
@settings(
    max_examples=3,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(key=key_strategy, gain_db=gain_db_strategy)
def test_metrics_v2_level_invariance_e2e(key: tuple[str, str], gain_db: float) -> None:
    """T-INV (E2E): 実抽出経路を通しても Metrics v2 の各フィールドはゲインシフト
    不変（#188 T-INV の恒久掃引・層②。層①の純関数版は tests/test_metrics_v2.py）。

    許容誤差は実測で決定した（本テスト作成時に KEYS 全 5 種 × gain
    [-24, -16, -8, 0, ..., 6]dB の 7 点、および本 Hypothesis 設定と同一の
    derandomize=True で選ばれる 3 example を手動実行して比較 — いずれも
    active_rate_v2/fullness/valley_db/crest_factor_robust の差分は厳密に 0
    （crest のみ float64 丸め由来の ~1e-47 の相対誤差）だった。これは代数的にも
    妥当: v2 メトリクスは全て「自身のロバストピークに対する相対ゲート」
    または「パーセンタイル比」であり、ゲイン a を乗じるとゲートも a 倍される
    ため比較結果は不変、リサンプル（librosa.load）も線形演算なのでゲインと
    可換。よってここでの許容誤差は層①より**緩めているのではなく**、実測 0 に
    対する安全マージン（float32 往復 + リサンプル丸め誤差の余裕）として設定
    している。gain 域を [-24, 6] dB に制限しているのは量子化回避（FLOAT
    なので理論上不要）ではなく、音源のピーク余裕（`generate_synth_samples.
    render_sample` は peak-normalize to 0.82）を大きく超えるゲインは検証意図
    （現実的なマスタリング差）から外れるため。
    """
    key_name, mode = key
    spec = mp.build_spec(
        key=key_name, mode=mode, bpm=120.0, brightness_level=0.5, duration_sec=20.0
    )
    audio = mp.g.render_sample(spec).astype(np.float32)
    scaled = (audio.astype(np.float64) * 10 ** (gain_db / 20.0)).astype(np.float32)

    base = _write_and_extract(audio)
    shifted = _write_and_extract(scaled)

    assert base.active_rate_v2 is not None and shifted.active_rate_v2 is not None
    assert base.fullness is not None and shifted.fullness is not None
    assert base.valley_db is not None and shifted.valley_db is not None
    assert base.crest_factor_robust is not None and shifted.crest_factor_robust is not None

    assert shifted.active_rate_v2 == pytest.approx(base.active_rate_v2, abs=0.01)
    assert shifted.fullness == pytest.approx(base.fullness, abs=0.01)
    assert shifted.valley_db == pytest.approx(base.valley_db, abs=0.1)
    assert shifted.crest_factor_robust == pytest.approx(base.crest_factor_robust, rel=0.01)


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
