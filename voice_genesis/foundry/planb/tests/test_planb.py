"""test_planb.py — Plan B PoC ハーネスの単体検証。

軽量テスト（合成ミニ bank）は `singer/` のレンダを呼ばないため数秒で終わる。
実素材ラダーの E2E は `@pytest.mark.slow`。

実行:
    python -m pytest voice_genesis/foundry/planb/tests -q
（WORLD 解析を呼ぶテストだけは pyworld 未導入時に skip する）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pb_compose as pc  # noqa: E402
import pb_extract as px  # noqa: E402
import pb_gates as pg  # noqa: E402
import pb_metrics as pm  # noqa: E402
import pb_tracks as pt  # noqa: E402
import pb_world as pbw  # noqa: E402

SR = 16000
FP = 5.0
requires_pyworld = pytest.mark.skipif(pbw.pw is None, reason="pyworld is not installed")


def _fft_bins(sr: int) -> int:
    # 合成 bank テストは WORLD の FFT 選択ではなく、2-D spectral
    # payload の形状契約だけを使う。16 kHz の従来値を固定する。
    assert sr == SR
    return 513


def test_world_calls_fail_explicitly_when_pyworld_is_missing(monkeypatch):
    monkeypatch.setattr(pbw, "pw", None)
    with pytest.raises(ModuleNotFoundError) as exc_info:
        pbw._require_pyworld()
    assert exc_info.value.name == "pyworld"
    runtime = object()
    monkeypatch.setattr(pbw, "pw", runtime)
    assert pbw._require_pyworld() is runtime


def _mini_bank(*, n_units: int = 3, frames: int = 40) -> pt.IdentityBank:
    """合成ミニ IdentityBank（レンダ非依存・決定論）。"""
    f = _fft_bins(SR)
    rng = np.random.default_rng(1234)
    units, sps, aps, f0s, cores = [], [], [], [], []
    t = 0.0
    for i in range(n_units):
        dur = frames * FP / 1000.0
        units.append(pt.Unit(
            label=f"u{i}", onset="n" if i == 0 else None, vowel="aiu"[i % 3],
            start_s=t, end_s=t + dur,
            core_start_s=t + 0.35 * dur, core_end_s=t + 0.85 * dur,
            is_terminal=(i == n_units - 1),
        ))
        base = np.exp(-np.linspace(0.0, 6.0, f)) * (1.0 + 0.2 * i)
        sp = np.tile(base, (frames, 1)) * (1.0 + 0.01 * rng.standard_normal((frames, f)))
        sps.append(np.maximum(sp, 1e-12))
        aps.append(np.full((frames, f), 0.1))
        f0s.append(np.full(frames, 200.0 + 20.0 * i))
        cores.append((int(0.35 * frames), int(0.85 * frames)))
        t += dur
    return pt.IdentityBank(
        source_id="mini", sr=SR, frame_period_ms=FP, units=tuple(units),
        sp=tuple(sps), ap=tuple(aps), f0=tuple(f0s), core_slices=tuple(cores),
        nasal_envelope=np.exp(-np.linspace(0.0, 3.0, f)),
        nasal_envelope_source="synthetic:test",
    )


def _mini_performance(bank: pt.IdentityBank) -> pt.PerformanceTrack:
    n = bank.n_units
    per_unit = 40
    idx = np.repeat(np.arange(n, dtype=np.float64), per_unit)
    pos = np.tile((np.arange(per_unit) + 0.5) / per_unit, n)
    return pt.PerformanceTrack(
        source_id="mini_perf",
        f0_dev_cents=30.0 * np.sin(2 * np.pi * 3 * pos),
        f0_dev_unit_index=idx, f0_dev_unit_pos=pos,
        unit_durations_s=np.array([u.duration_s * 1.3 for u in bank.units]),
        energy_db=2.0 * np.cos(2 * np.pi * pos), energy_unit_index=idx, energy_unit_pos=pos,
        release=pt.ReleaseSpec(window_frac=0.35,
                               taper_db=np.linspace(0.0, -12.0, 16), hold_core=True),
    )


# ---------------------------------------------------------------------------
# warp: コア保存の伸縮
# ---------------------------------------------------------------------------
def test_warp_unit_preserves_head_and_tail_length():
    arr = np.arange(100, dtype=np.float64).reshape(100, 1)
    out, core = pc.warp_unit(arr, (30, 80), 160)
    assert out.shape[0] == 160
    assert np.allclose(out[:30, 0], arr[:30, 0])          # 頭は実尺のまま
    assert np.allclose(out[-20:, 0], arr[-20:, 0])        # 尾も実尺のまま
    assert core == (30, 140)


def test_warp_unit_degrades_gracefully_when_target_too_short():
    arr = np.arange(100, dtype=np.float64).reshape(100, 1)
    out, core = pc.warp_unit(arr, (30, 80), 10)
    assert out.shape[0] == 10
    assert core == (0, 10)


def test_warp_voicing_never_invents_voiced_frames():
    f0 = np.array([0.0] * 10 + [200.0] * 10 + [0.0] * 10)
    v = pc._warp_voicing(f0, (10, 20), 60)
    assert v.dtype == bool
    assert not v[0] and not v[-1]
    assert v.sum() > 0


# ---------------------------------------------------------------------------
# criterion 6: 構造ゲート + tripwire
# ---------------------------------------------------------------------------
def test_structural_gate_passes_for_valid_track():
    bank = _mini_bank()
    res = pg.gate_structural(_mini_performance(bank))
    assert res.status == "pass"


def test_structural_gate_rejects_two_dimensional_payload():
    bank = _mini_bank()
    perf = _mini_performance(bank)
    import dataclasses
    bad = dataclasses.replace(perf, energy_db=np.zeros((4, 4)))
    with pytest.raises(ValueError):
        pt.assert_no_spectral_payload(bad)
    assert pg.gate_structural(bad).status == "fail"


@requires_pyworld
def test_tripwire_records_only_declared_fields():
    bank = _mini_bank()
    perf = _mini_performance(bank)
    res = pg.gate_tripwire(pc.compose, bank, perf,
                           pt.PerformanceToggles(True, True, True, True))
    assert res.status == "pass"
    accessed = set(res.evidence["accessed"])
    assert "unit_durations_s" in accessed and "release.taper_db" in accessed
    assert not any(a.startswith("sp") or a.startswith("ap") for a in accessed)


def test_tripwire_fails_when_two_dimensional_value_is_reachable():
    bank = _mini_bank()
    perf = _mini_performance(bank)

    class _Leaky:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name == "unit_durations_s":
                return np.zeros((3, 3))
            return getattr(self._inner, name)

    res = pg.gate_tripwire(pc.compose, bank, _Leaky(perf),
                           pt.PerformanceToggles(True, True, True, True))
    assert res.status == "fail"


# ---------------------------------------------------------------------------
# criterion 5: 決定論
# ---------------------------------------------------------------------------
@requires_pyworld
def test_compose_is_deterministic():
    bank = _mini_bank()
    perf = _mini_performance(bank)
    tg = pt.PerformanceToggles(True, True, True, True)
    a = pc.compose(bank, perf, tg)
    b = pc.compose(bank, perf, tg)
    assert a.sha256() == b.sha256()


@requires_pyworld
def test_ladder_rungs_are_distinct():
    bank = _mini_bank()
    perf = _mini_performance(bank)
    shas = {name: pc.compose(bank, perf, tg).sha256() for name, tg in pt.LADDER}
    assert len(set(shas.values())) == len(shas)


@requires_pyworld
def test_duration_toggle_changes_length_and_others_do_not():
    bank = _mini_bank()
    perf = _mini_performance(bank)
    n0 = pc.compose(bank, perf, pt.PerformanceToggles()).wav.shape[0]
    n_f0 = pc.compose(bank, perf, pt.PerformanceToggles(f0=True)).wav.shape[0]
    n_dur = pc.compose(bank, perf, pt.PerformanceToggles(duration=True)).wav.shape[0]
    assert n_f0 == n0
    assert n_dur > n0


def test_compose_rejects_unit_count_mismatch():
    bank = _mini_bank(n_units=3)
    perf = _mini_performance(_mini_bank(n_units=2))
    with pytest.raises(ValueError):
        pc.compose(bank, perf, pt.PerformanceToggles(duration=True))


def test_toggles_on_without_performance_is_an_error():
    bank = _mini_bank()
    with pytest.raises(ValueError):
        pc.compose(bank, None, pt.PerformanceToggles(f0=True))


# ---------------------------------------------------------------------------
# release skill: identity 側テクスチャのみで構成される
# ---------------------------------------------------------------------------
def test_release_skill_holds_core_envelope():
    f = _fft_bins(SR)
    sp = np.tile(np.exp(-np.linspace(0.0, 6.0, f)), (40, 1))
    sp[26:] = np.tile(np.exp(-np.linspace(0.0, 1.0, f)), (14, 1))  # 終端で別包絡へドリフト
    ap = np.full((40, f), 0.1)
    out_sp, _ = pc._apply_release_skill(
        sp, ap, (14, 34), 0.35, np.zeros(8), True)
    core_ref = np.mean(sp[14:24], axis=0)
    # 窓末尾はコア参照へ収束している（ドリフト先ではなく）
    assert pbw.log_spectral_distance_db(out_sp[-1], core_ref) < \
        pbw.log_spectral_distance_db(sp[-1], core_ref)


def test_release_taper_reduces_terminal_energy():
    f = _fft_bins(SR)
    sp = np.tile(np.exp(-np.linspace(0.0, 6.0, f)), (40, 1))
    ap = np.full((40, f), 0.1)
    out_sp, _ = pc._apply_release_skill(sp, ap, (14, 34), 0.35,
                                        np.linspace(0.0, -20.0, 8), False)
    assert np.sum(out_sp[-1]) < np.sum(sp[-1]) * 0.1


# ---------------------------------------------------------------------------
# 事前登録ゲート
# ---------------------------------------------------------------------------
def _metrics_stub(nasal: float, drift: float = 1.0, etail: float = -3.0):
    trf = (pm.TrfAxes(unit_index=1, label="り", nasal_gain_db=nasal, drift_db=drift,
                      f0_sag_cents=0.0, energy_tail_db=etail,
                      nasal_gain_shape_db=nasal, drift_shape_db=drift),)
    return pm.RungMetrics(rung="x", toggles="t", wav_sha256="0" * 64, duration_s=1.0,
                          trf=trf, identity_core_lsd_db=1.0, donor_texture_lsd_db=5.0,
                          f0_dev_rmse_cents=1.0, dur_mae_ms=1.0, energy_corr=0.5,
                          release_taper_rmse_db=1.0)


def test_frozen_gate_declares_not_evaluable_without_baseline_failure():
    m0 = _metrics_stub(0.2)
    frozen = pg.freeze_trf_gate(m0, "り@1", [])
    assert frozen["failure_present"] is False
    prim, _ = pg.gate_trf(frozen, m0, _metrics_stub(-3.0))
    assert prim.status == "not_evaluable"


def test_frozen_gate_passes_only_with_sufficient_improvement():
    m0 = _metrics_stub(4.0)
    frozen = pg.freeze_trf_gate(m0, "り@1", [])
    assert frozen["required_improvement_db"] == pytest.approx(1.0)
    assert pg.gate_trf(frozen, m0, _metrics_stub(3.5))[0].status == "fail"
    assert pg.gate_trf(frozen, m0, _metrics_stub(2.0))[0].status == "pass"


def test_frozen_gate_fails_on_secondary_regression():
    m0 = _metrics_stub(4.0, drift=1.0)
    frozen = pg.freeze_trf_gate(m0, "り@1", [])
    assert pg.gate_trf(frozen, m0, _metrics_stub(1.0, drift=5.0))[0].status == "fail"


def test_identity_gate_uses_margin_not_absolute_distance():
    good = {"R0": _metrics_stub(0.0)}
    assert pg.gate_identity_preservation(good).status == "pass"
    bad = pm.RungMetrics(rung="x", toggles="t", wav_sha256="0" * 64, duration_s=1.0,
                         trf=(), identity_core_lsd_db=5.0, donor_texture_lsd_db=5.2,
                         f0_dev_rmse_cents=1.0, dur_mae_ms=1.0, energy_corr=0.5,
                         release_taper_rmse_db=1.0)
    assert pg.gate_identity_preservation({"R0": bad}).status == "fail"


# ---------------------------------------------------------------------------
# extract: Performance は 1 次元のみ / identity は 2 次元を持つ
# ---------------------------------------------------------------------------
def test_extract_round_trip_shapes():
    pytest.importorskip("pyworld")
    sr = SR
    t = np.arange(int(sr * 1.2)) / sr
    x = 0.2 * sum(np.sin(2 * np.pi * 180 * k * t) / k for k in (1, 2, 3, 4, 5))
    a = pbw.analyze(x, sr, frame_period_ms=FP)
    units = pt.units_from_boundaries(
        [("a", None, "a"), ("i", None, "i")], [(0.0, 0.6), (0.6, 1.2)],
        terminal_flags=[False, True])
    bank = px.build_identity_bank(a, units, source_id="t")
    perf = px.build_performance_track(a, units, source_id="t")
    assert bank.sp[0].ndim == 2 and bank.ap[0].ndim == 2
    pt.assert_no_spectral_payload(perf)
    assert perf.n_units == 2
    assert bank.note_target_hz(0) > 0.0


def test_f0_deviation_is_clamped():
    pytest.importorskip("pyworld")
    sr = SR
    t = np.arange(int(sr * 0.8)) / sr
    x = 0.2 * sum(np.sin(2 * np.pi * 150 * k * t) / k for k in (1, 2, 3))
    a = pbw.analyze(x, sr, frame_period_ms=FP)
    units = pt.units_from_boundaries([("a", None, "a")], [(0.0, 0.8)], terminal_flags=[True])
    perf = px.build_performance_track(a, units, source_id="t")
    assert np.all(np.abs(perf.f0_dev_cents) <= px.F0_DEV_CLAMP_CENTS + 1e-9)


# ---------------------------------------------------------------------------
# E2E（実素材レンダを回すため slow）
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_substitute_ladder_end_to_end(tmp_path):
    pytest.importorskip("pyworld")
    import pb_ladder as pl
    rec = pl.run(pl.RunConfig(write_wav=False), tmp_path)
    status = {g["gate"]: g["status"] for g in rec["gates"]}
    assert status["G1"] == "pass"
    assert status["G2"] == "pass"
    assert status["G3"] == "pass"
    assert status["G7"] == "pass"        # 陽性対照: 故障ありなら改善が立つ
    assert status["G-ear"] == "blocked"  # 耳は常に blocked（成功に数えない）


@pytest.mark.slow
def test_negative_control_is_not_evaluable(tmp_path):
    pytest.importorskip("pyworld")
    import pb_ladder as pl
    rec = pl.run(pl.RunConfig(fault=False, write_wav=False), tmp_path)
    status = {g["gate"]: g["status"] for g in rec["gates"]}
    assert status["G7"] == "not_evaluable"  # 故障が無ければ改善は主張できない
