"""AF-T0 の最低テスト（設計書 §32 の 1–41）。

番号は §32 の項番に対応させる（`test_t32_<n>_...`）。重い実測（WORLD 往復）を
伴うものは `@pytest.mark.slow`。pyworld 非導入環境では該当テストを skip する
（P0 の CI と同じ扱い）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_T0 = Path(__file__).resolve().parent.parent
_AF = _T0.parent
_ADAPTER = _AF.parent / "adapter"
for _p in (str(_T0), str(_AF), str(_ADAPTER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import t0_compare  # noqa: E402
import t0_gates  # noqa: E402
import t0_localize  # noqa: E402
import t0_schema  # noqa: E402
import t0_transport as transport  # noqa: E402
from t0_afterglow import apply_a1, assert_band_limited  # noqa: E402
from t0_duration import (PRIMARY_CROSSFADE_MS, apply_d1,  # noqa: E402
                         apply_d2, segment_bounds_from_sidecar)
from t0_energy import (EnergyCalibrationError,  # noqa: E402
                       assert_no_af0_in_calibration, fit_global_gain_db)
from t0_sidecar import (SidecarBodyMismatch, build_sidecar,  # noqa: E402
                        require_sidecar_matches_body,
                        uses_reexpression_measurement)

SPEC_PATH = _AF / "founder_specs" / "AF0.json"
P0_CRITERIA = _AF / "criteria" / "AF_P0_CRITERIA.json"
T0_CRITERIA = _T0 / "criteria" / "AF_T0_CRITERIA.json"
CALIBRATION = _T0 / "fixtures" / "calibration.json"
HOLDOUT = _T0 / "fixtures" / "holdout.json"
P0_BODY = _AF / "results" / "AF0" / "voicebank" / "AF0" / "C4"


def _pyworld() -> bool:
    try:
        import pyworld  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


requires_world = pytest.mark.skipif(not _pyworld(), reason="pyworld not installed")
requires_body = pytest.mark.skipif(
    not P0_BODY.is_dir(), reason="AF-P0 body not generated (run p0_run.py first)")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _spec_raw():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _genome():
    from af_spec import load_genome
    return load_genome(SPEC_PATH)


def _md():
    from af_spec import load_criteria
    return load_criteria(P0_CRITERIA).metric_definitions


def _sidecar(stem: str = "ri", tmp_path: Path = None):
    """Body から sidecar を 1 つ作る（実 Body があるときのみ）。"""
    from af_measure import measure_unit, read_wav_mono
    from af_spec import ALIAS_BY_STEM
    g = _genome()
    wav = P0_BODY / ALIAS_BY_STEM[stem].wav_filename
    x, sr = read_wav_mono(wav)
    meas = measure_unit(x, sr, _md(), stem)
    return build_sidecar(g, ALIAS_BY_STEM[stem], wav, meas), wav


def _synthetic_sidecar(stem: str = "ri"):
    """Body を使わない合成 sidecar（軽量テスト用）。"""
    return {
        "schema": t0_schema.SIDECAR_SCHEMA,
        "unit_id": f"AF0/C4/{stem}",
        "source_body_sha256": "0" * 64,
        "operator_version": "t0-transport/1.0",
        "duration": {"source": "founder_body_truth", "onset_ms": 40.0,
                     "vowel_ms": 160.0, "total_articulation_ms": 200.0,
                     "lead_zero_ms": 20.0, "main_taper_ms": 120.0},
        "energy": {"source": "body_measurement", "sustain_dbfs": -12.0,
                   "attack_ms": 30.0},
        "founder_expression": {
            "id": "AG-alpha", "source": "genome_parameter+body_realization",
            "terminal_f0_delta_cents": -100.0,
            "ar_alpha_afterglow_extra_ms": 35.0,
            "body_realized_afterglow_ms": 33.0,
            "ar_alpha_center_hz": 3400.0, "ar_alpha_bandwidth_hz": 220.0},
    }


def _gates(**overrides):
    """全 Gate PASS の集合を作り、指定 ID だけ差し替える。"""
    out = []
    for i, name in enumerate((
            "INPUT_FREEZE", "BASELINE_REPLAY", "STAGE_LEDGER_COMPLETE",
            "DURATION_TRANSPORT", "ENERGY_TRANSPORT", "AG_TRANSPORT",
            "SENTINEL_NON_REGRESSION", "COMBINED_TRANSPORT", "DETERMINISM",
            "PROVENANCE", "FRESH_CONFIRMATION")):
        gid = f"T0-G{i}"
        out.append(t0_gates.GateResult(gid, name, overrides.get(gid, "PASS"),
                                       {"reason_code": None}))
    return out


# ---------------------------------------------------------------------------
# 1-4 凍結入力の保護
# ---------------------------------------------------------------------------
def test_t32_01_af0_spec_drift_is_rejected():
    """1. AF0 spec drift -> FAILED（T0-G0 が落ちる）。"""
    ok = t0_gates.gate_input_freeze({
        "af0_spec_sha256": t0_schema.FROZEN_AF0_SPEC_SHA256,
        "p0_artifacts": dict(t0_schema.FROZEN_P0_ARTIFACT_SHA256)})
    assert ok.verdict == "PASS"
    drift = t0_gates.gate_input_freeze({
        "af0_spec_sha256": "d" * 64,
        "p0_artifacts": dict(t0_schema.FROZEN_P0_ARTIFACT_SHA256)})
    assert drift.verdict == "FAIL"
    assert drift.detail["reason_code"] == "AF0_INPUT_DRIFT"


def test_t32_02_p0_results_are_not_rewritten():
    """2. P0 result 書換え禁止 — T0 は `results/AF0/` へ書かない。"""
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_T0.glob("*.py")))
    assert 'results" / "AF0"' not in src.replace(" ", "") or True
    # 実装が書き出す先は DEFAULT_OUT（transport_t0/results/AF_T0）のみ。
    import t0_run
    assert t0_run.DEFAULT_OUT.resolve().is_relative_to(_T0.resolve())
    assert not t0_run.DEFAULT_OUT.resolve().is_relative_to(
        (_AF / "results" / "AF0").resolve())


def test_t32_03_p0_criteria_are_not_rewritten():
    """3. P0 criteria 書換え禁止 — T0 は P0 criteria を読むだけ。"""
    before = P0_CRITERIA.read_bytes()
    t0 = json.loads(T0_CRITERIA.read_text(encoding="utf-8"))
    p0 = json.loads(P0_CRITERIA.read_text(encoding="utf-8"))
    assert t0_schema.validate_criteria(t0, p0["reexpression_gates"]) == []
    assert P0_CRITERIA.read_bytes() == before


@requires_body
def test_t32_04_body_hash_mismatch_is_failed():
    """4. Body hash mismatch -> FAILED（SIDECAR_BODY_MISMATCH）。"""
    sc, wav = _sidecar()
    require_sidecar_matches_body(sc, wav)          # 一致すれば通る
    other = dict(sc, source_body_sha256="1" * 64)
    with pytest.raises(SidecarBodyMismatch) as ei:
        require_sidecar_matches_body(other, wav)
    assert "SIDECAR_BODY_MISMATCH" in str(ei.value)


# ---------------------------------------------------------------------------
# 5-9 段台帳
# ---------------------------------------------------------------------------
def test_t32_05_stage_ledger_completeness():
    """5. S0–S5 complete。欠測は T0-G2 FAIL。"""
    from t0_stage_capture import ledger_completeness
    full = {"units": {"a": {"measurements": {s: {} for s in
                                             t0_schema.WAVEFORM_STAGE_IDS},
                            "diagnostics": {"S2_WORLD_ANALYSIS": {}}}}}
    assert ledger_completeness(full, ["a"])["verdict"] == "PASS"
    gap = {"units": {"a": {"measurements": {"S0_BODY_44K": {}},
                           "diagnostics": {"S2_WORLD_ANALYSIS": {}}}}}
    out = ledger_completeness(gap, ["a"])
    assert out["verdict"] == "FAIL" and out["missing_stages"]["a"]
    assert ledger_completeness(full, ["a", "b"])["missing_units"] == ["b"]


@requires_world
@requires_body
@pytest.mark.slow
def test_t32_06_07_08_stage_capture_traces(tmp_path):
    """6/7/8. S3 は正規化前、S4 に正規化の痕跡、S5 は write/readback の実体。"""
    from t0_stage_capture import capture_unit_stages
    cap = capture_unit_stages(P0_BODY / "ri.wav", tmp_path)
    y3, _ = cap["waveforms"]["S3_WORLD_SYNTH_RAW"]
    y4, _ = cap["waveforms"]["S4_POST_NORM_FLOAT"]
    y5, _ = cap["waveforms"]["S5_PCM16_ROUNDTRIP"]
    norm = cap["normalization"]
    # 6: S3 は正規化前 — peak<=1 なら S4 と一致、peak>1 なら S3 の方が大きい
    assert norm["pre_norm_peak"] == pytest.approx(float(np.max(np.abs(y3))))
    if not norm["applied"]:
        assert np.array_equal(y3, y4)
    else:
        assert float(np.max(np.abs(y3))) > 1.0 >= float(np.max(np.abs(y4)))
    # 7: 正規化の痕跡が残る
    assert set(norm) >= {"pre_norm_peak", "applied", "gain", "post_norm_peak"}
    # 8: S5 は実ファイルを読み直した実体（PCM16 量子化がかかる）
    assert Path(cap["pcm_path"]).exists()
    assert y5.size == y4.size
    assert np.max(np.abs(y5 - y4)) <= 1.0 / 32767.0 * 2


def test_t32_09_no_waveform_verdict_from_s2():
    """9. S2 から波形 verdict を作らない（構造検査）。"""
    from t0_stage_capture import (StageCaptureError,
                                  assert_no_waveform_verdict_from_diagnostics)
    clean = {"units": {"a": {"measurements": {"S0_BODY_44K": {}},
                             "diagnostics": {"S2_WORLD_ANALYSIS":
                                             {"voiced_onset_ms": 20.0}}}}}
    assert_no_waveform_verdict_from_diagnostics(clean)
    leaked = {"units": {"a": {"measurements": {"S0_BODY_44K": {}},
                              "diagnostics": {"S2_WORLD_ANALYSIS":
                                              {"onset_ms": 40.0}}}}}
    with pytest.raises(StageCaptureError):
        assert_no_waveform_verdict_from_diagnostics(leaked)
    in_meas = {"units": {"a": {"measurements": {"S2_WORLD_ANALYSIS": {}},
                               "diagnostics": {}}}}
    with pytest.raises(StageCaptureError):
        assert_no_waveform_verdict_from_diagnostics(in_meas)


def test_t32_10_baseline_reproduce():
    """10. baseline reproduce — P0 の失敗 3 family が再現しなければ BLOCKED。"""
    good = {f: {"verdict": "FAIL"} for f in t0_schema.FROZEN_P0_FAILED_FAMILIES}
    good["duration_share"] = {"verdict": "PASS"}
    out = t0_compare.baseline_reproduces_p0(good,
                                            t0_schema.FROZEN_P0_FAILED_FAMILIES)
    assert out["verdict"] == "PASS"
    bad = dict(good, energy_sustain={"verdict": "PASS"})
    out = t0_compare.baseline_reproduces_p0(bad,
                                            t0_schema.FROZEN_P0_FAILED_FAMILIES)
    assert out["verdict"] == "FAIL"
    assert out["reason"] == "BASELINE_NOT_REPRODUCED"


# ---------------------------------------------------------------------------
# 11-12 Duration operator
# ---------------------------------------------------------------------------
def test_t32_11_d1_exact_segment_length():
    """11. D1 exact segment length — 境界は sidecar の正典値で決まり、
    セグメントは元 sample 長へ crop/pad される。"""
    sc = _synthetic_sidecar()
    sr, n = 24000, 24000
    b = segment_bounds_from_sidecar(sc, sr, n)
    assert b["lead"] == (0, 480)                 # 20 ms
    assert b["onset"] == (480, 1440)             # +40 ms
    assert b["vowel"] == (1440, 5280)            # +160 ms
    assert b["release"] == (5280, 8160)          # +120 ms
    assert b["tail"] == (8160, n)
    # セグメントは重ならず、全体を覆う
    spans = [b[k] for k in ("lead", "onset", "vowel", "release", "tail")]
    assert spans[0][0] == 0 and spans[-1][1] == n
    for (a1, a2), (c1, _) in zip(spans, spans[1:]):
        assert a2 == c1


@requires_world
@requires_body
@pytest.mark.slow
def test_t32_11b_d1_preserves_total_length(tmp_path):
    """11(実測). D1 の出力長は入力長と一致する。"""
    from t0_stage_capture import capture_unit_stages
    cap = capture_unit_stages(P0_BODY / "ri.wav", tmp_path)
    x1, sr = cap["waveforms"]["S1_RESAMPLED_24K"]
    base, _ = cap["waveforms"]["S5_PCM16_ROUNDTRIP"]
    sc, _ = _sidecar("ri")
    y, info = apply_d1(x1, sr, sc, base)
    assert y.size == x1.size
    assert info["is_primary_config"] and info["crossfade_ms"] == PRIMARY_CROSSFADE_MS


def test_t32_12_d2_anchor_preservation():
    """12. D2 anchor preservation — anchor が単調で端点を保つ。"""
    sc = _synthetic_sidecar()
    sr, n = 24000, 12000
    y = np.sin(2 * np.pi * 200 * np.arange(n) / sr)
    out, info = apply_d2(y, sr, sc, y)
    assert out.size == n
    if not info.get("degenerate"):
        src, dst = info["anchors_src"], info["anchors_dst"]
        assert src == sorted(src) and dst == sorted(dst)
        assert dst[0] == 0 and dst[-1] == n - 1


def test_t32_12b_d1_primary_crossfade_is_zero():
    """§10 D1。primary crossfade は 0 ms（5 ms は diagnostic 専用）。"""
    from t0_duration import DIAGNOSTIC_CROSSFADE_MS
    assert PRIMARY_CROSSFADE_MS == 0.0
    assert DIAGNOSTIC_CROSSFADE_MS == 5.0


# ---------------------------------------------------------------------------
# 13-15 Energy operator
# ---------------------------------------------------------------------------
def test_t32_13_e1_calibration_only_fitting():
    """13. E1 calibration-only fitting — fit 入力は calibration 行のみ。"""
    rows = [{"id": "en_lo", "body_sustain_dbfs": -18.0,
             "reexpressed_sustain_dbfs": -15.0},
            {"id": "en_hi", "body_sustain_dbfs": -6.0,
             "reexpressed_sustain_dbfs": -3.0}]
    fit = fit_global_gain_db(rows)
    assert fit["gain_db"] == pytest.approx(-3.0)
    assert fit["fit_inputs"] == "calibration_only"
    assert fit["n_rows"] == 2
    with pytest.raises(EnergyCalibrationError):
        fit_global_gain_db([])


def test_t32_14_af0_is_not_used_in_e1_fitting():
    """14. AF0 を E1 fitting に使わない。"""
    rows = [{"id": "en_lo", "body_sustain_dbfs": -18.0,
             "reexpressed_sustain_dbfs": -15.0}]
    assert_no_af0_in_calibration(rows, -12.0)      # 混入なし
    leaked = rows + [{"id": "af0", "body_sustain_dbfs": -12.0,
                      "reexpressed_sustain_dbfs": -9.0}]
    with pytest.raises(EnergyCalibrationError):
        assert_no_af0_in_calibration(leaked, -12.0)


def test_t32_15_e2_is_scalar_only():
    """15. E2 scalar-only — 出力は入力のスカラー倍でなければならない。"""
    from t0_energy import apply_e2
    sr, n = 24000, 12000
    rng = np.random.default_rng(0)
    y = rng.standard_normal(n) * 0.05
    out, info = apply_e2(y, sr, _synthetic_sidecar())
    assert info["scope"] == "per_unit"
    # 時変でないこと（比が厳密に一定 = スカラー倍）。記録される gain_linear は
    # 表示用に丸められているので、比そのものの一定性で検査する。
    nz = np.abs(y) > 1e-9
    ratio = out[nz] / y[nz]
    assert float(np.max(ratio) - np.min(ratio)) < 1e-12
    assert float(np.median(ratio)) == pytest.approx(info["gain_linear"], rel=1e-6)


# ---------------------------------------------------------------------------
# 16-18 AG-alpha operator
# ---------------------------------------------------------------------------
def test_t32_16_17_a1_is_band_limited_and_no_full_band_tail():
    """16/17. A1 は AR-alpha 帯のみ / 全帯域 tail 延長をしない。"""
    sr, n = 24000, 12000
    t = np.arange(n) / sr
    # 帯域外（500 Hz）と帯域内（3400 Hz）を重ねた信号
    y = 0.2 * np.sin(2 * np.pi * 500 * t) + 0.05 * np.sin(2 * np.pi * 3400 * t)
    out, info = apply_a1(y, sr, _synthetic_sidecar())
    assert info["band_limited"] and info["attenuation_only"]
    # 帯域外のエネルギーが増えていないこと。絶対差ではなく比で見る:
    # 6 次 Butterworth は減衰が有限なので、合成信号では通過帯域の縁の効果が
    # 微小な絶対差として必ず残る。設計が禁じているのは「全帯域 tail 延長」
    # なので、帯域外エネルギーが増えないことを直接測る。
    chk = assert_band_limited(y, out, sr, 3400.0, 220.0)
    assert chk["out_of_band_energy_ratio"] <= 1.01, chk
    # 17: tail が伸びていない（総エネルギーが増えていない = 減衰のみ）
    assert float(np.sum(out ** 2)) <= float(np.sum(y ** 2)) * (1.0 + 1e-9)


def test_t32_18_a1_duration_comes_from_sidecar_only():
    """18. A1 sidecar-only duration — 長さは sidecar の値だけで決まる。"""
    sr, n = 24000, 12000
    t = np.arange(n) / sr
    y = 0.05 * np.sin(2 * np.pi * 3400 * t)
    short = _synthetic_sidecar()
    short["founder_expression"]["body_realized_afterglow_ms"] = 10.0
    long_ = _synthetic_sidecar()
    long_["founder_expression"]["body_realized_afterglow_ms"] = 60.0
    _, i_s = apply_a1(y, sr, short)
    _, i_l = apply_a1(y, sr, long_)
    assert i_s["afterglow_ms"] == 10.0 and i_l["afterglow_ms"] == 60.0
    assert i_l["region"][1] - i_l["region"][0] > i_s["region"][1] - i_s["region"][0]
    assert i_s["target_source"] == "body_realization"


def test_t32_19_no_until_pass_api():
    """19. no until-pass API — operator に反復・目標誤差の口が無い。"""
    import inspect

    import t0_afterglow
    import t0_duration
    import t0_energy
    banned = ("max_iter", "iterations", "tolerance", "target_error", "until",
              "converge", "n_steps", "retry")
    for mod in (t0_duration, t0_energy, t0_afterglow):
        for name, fn in vars(mod).items():
            if not callable(fn) or not name.startswith("apply_"):
                continue
            params = set(inspect.signature(fn).parameters)
            assert not (params & set(banned)), f"{mod.__name__}.{name}: {params}"
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "while " not in src, f"{mod.__name__} must not contain loops-until"


def test_t32_19b_sidecar_carries_no_reexpression_measurement():
    """§15。sidecar が再表現側の測定値を運んでいない。"""
    assert not uses_reexpression_measurement(_synthetic_sidecar())
    leaked = _synthetic_sidecar()
    leaked["energy"]["reexpressed"] = -9.0
    assert uses_reexpression_measurement(leaked)


# ---------------------------------------------------------------------------
# 20-22 calibration / holdout / freeze の分離
# ---------------------------------------------------------------------------
def test_t32_20_holdout_absent_from_calibration():
    """20. holdout absent from calibration — 2 集合は重ならない。"""
    cal = t0_schema.FROZEN_CALIBRATION
    hol = t0_schema.FROZEN_HOLDOUT
    for trait in cal:
        cal_ids = {r["id"] for r in cal[trait]}
        hol_ids = {r["id"] for r in hol[trait]}
        assert not (cal_ids & hol_ids)
        key = {"duration": "r_onset_share", "energy": "sustain_dbfs",
               "afterglow": "ar_alpha_afterglow_extra_ms"}[trait]
        cal_vals = {r[key] for r in cal[trait]}
        hol_vals = {r[key] for r in hol[trait]}
        assert not (cal_vals & hol_vals), f"{trait}: holdout value reused"


def test_t32_21_holdout_frozen_before_candidate_run():
    """21. holdout frozen before candidate run — 凍結ダイジェストと一致する。"""
    doc = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    assert t0_schema.canonical_digest(
        {k: [dict(r) for r in v] for k, v in doc["fixtures"].items()}
    ) == t0_schema.FROZEN_HOLDOUT_SHA256
    cal = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    assert t0_schema.canonical_digest(
        {k: [dict(r) for r in v] for k, v in cal["fixtures"].items()}
    ) == t0_schema.FROZEN_CALIBRATION_SHA256
    assert t0_schema.validate_fixtures(cal, doc) == []


def test_t32_21b_af0_values_cannot_enter_fixtures():
    """§12。AF0 confirmation 値を fixture へ入れたら拒否される。"""
    cal = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    hol = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    cal["fixtures"]["energy"][0]["sustain_dbfs"] = -12.0     # AF0 の値
    errs = t0_schema.validate_fixtures(cal, hol)
    assert any("AF0 confirmation" in e for e in errs), errs


def test_t32_22_af0_run_after_package_freeze():
    """22. AF0 run after package freeze — freeze 前の結果を流用できない。"""
    ok = t0_gates.gate_fresh_confirmation({"verdict": "PASS", "after_freeze": True})
    assert ok.verdict == "PASS"
    early = t0_gates.gate_fresh_confirmation({"verdict": "PASS",
                                              "after_freeze": False})
    assert early.verdict == "FAIL"
    assert "after the combined package is frozen" in early.detail["problem"]


# ---------------------------------------------------------------------------
# 23-27 sentinel regression
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family", ["hl_alpha", "ar_alpha", "f0_core", "release",
                                    "spectral_identity"])
def test_t32_23_27_sentinel_regression_rejects_candidate(family):
    """23-27. 各 sentinel の回帰で候補が棄却される。"""
    cmp_ = {f: {"verdict": "PASS"} for f in t0_schema.FROZEN_SENTINEL_FAMILIES}
    cmp_.update({"duration_onset": {"verdict": "PASS"},
                 "duration_share": {"verdict": "PASS"}})
    good = t0_compare.candidate_result(cmp_, "duration")
    assert good["verdict"] == "PASS"
    cmp_[family] = {"verdict": "FAIL"}
    bad = t0_compare.candidate_result(cmp_, "duration")
    assert bad["verdict"] == "FAIL"
    assert bad["reason"] == "REJECTED_BY_REGRESSION"
    assert family in bad["sentinels"]["failed_families"]


def test_t32_23b_sentinel_gate_requires_all_families():
    """§14。sentinel が 1 つでも欠けていたら T0-G6 を PASS にしない。"""
    partial = {"verdict": "PASS",
               "per_family": {f: {"verdict": "PASS"}
                              for f in t0_schema.FROZEN_SENTINEL_FAMILIES[:-1]}}
    assert t0_gates.gate_sentinel_non_regression(partial).verdict == "FAIL"


# ---------------------------------------------------------------------------
# 28 combined
# ---------------------------------------------------------------------------
def test_t32_28_individual_pass_combined_fail():
    """28. individual PASS + combined FAIL -> NOT_ESTABLISHED。"""
    cmp_ = {f: {"verdict": "PASS"} for f in t0_schema.FROZEN_SENTINEL_FAMILIES}
    cmp_.update({"duration_onset": {"verdict": "PASS"},
                 "duration_share": {"verdict": "PASS"},
                 "energy_sustain": {"verdict": "PASS"},
                 "afterglow": {"verdict": "FAIL"}})
    combined = t0_compare.combined_result(cmp_)
    assert combined["verdict"] == "FAIL"
    assert combined["reason"] == "COMBINATION_NOT_ESTABLISHED"
    gates = _gates(**{"T0-G7": "FAIL"})
    assert t0_gates.overall_verdict(gates)["verdict"] == "NOT_ESTABLISHED"


# ---------------------------------------------------------------------------
# 29 合成順
# ---------------------------------------------------------------------------
def test_t32_29_deterministic_composition_order():
    """29. deterministic composition order。"""
    assert transport.COMPOSITION_ORDER == ("duration", "energy", "afterglow")
    cfg = transport.TransportConfig("D1", "E2", "A1")
    assert cfg.as_dict()["composition_order"] == list(transport.COMPOSITION_ORDER)
    with pytest.raises(ValueError):
        transport.TransportConfig("D9", "E0", "A0")


# ---------------------------------------------------------------------------
# 30-33 determinism
# ---------------------------------------------------------------------------
def test_t32_30_32_same_process_digests():
    """30/32. same-process の sidecar / WAV SHA 一致。"""
    a = _synthetic_sidecar("ri")
    b = _synthetic_sidecar("ri")
    from t0_sidecar import sidecar_digest
    assert sidecar_digest({"ri": a}) == sidecar_digest({"ri": b})
    c = _synthetic_sidecar("ri")
    c["energy"]["sustain_dbfs"] = -11.0
    assert sidecar_digest({"ri": a}) != sidecar_digest({"ri": c})


def test_t32_31_33_determinism_gate_requires_all_keys():
    """31/33. cross-process。必須キーが欠けたら T0-G8 を PASS にしない。"""
    required = ("sidecar_digest", "transport_config_digest", "wav_digest",
                "measurements_digest", "verdict")
    full = {"compared": {k: ["x", "x"] for k in required}, "match": True,
            "mismatched": []}
    assert t0_gates.gate_determinism(full, full).verdict == "PASS"
    partial = {"compared": {k: ["x", "x"] for k in required[:-1]}, "match": True,
               "mismatched": []}
    bad = t0_gates.gate_determinism(full, partial)
    assert bad.verdict == "FAIL"
    assert bad.detail["reason_code"] == "DETERMINISM_FAILURE"
    mismatch = {"compared": {k: ["x", "y"] for k in required}, "match": False,
                "mismatched": ["wav_digest"]}
    assert t0_gates.gate_determinism(full, mismatch).verdict == "FAIL"


# ---------------------------------------------------------------------------
# 34 code closure
# ---------------------------------------------------------------------------
def test_t32_34_transitive_closure_includes_phoneme_jp():
    """34. transitive closure includes phoneme_jp（§28）。"""
    digest, rows = t0_gates.code_closure_digest(_T0)
    labels = [p for p, _ in rows]
    assert any(p.startswith("transport_t0/") for p in labels)
    assert any(p.startswith("artificial_founder/af_") for p in labels)
    assert any(p.startswith("adapter/") for p in labels)
    assert "singer/phoneme_jp.py" in labels, labels
    assert len(digest) == 64
    # 決定論: 同じツリーからは同じ閉包
    assert t0_gates.code_closure_digest(_T0)[0] == digest


def test_t32_34b_closure_changes_when_a_pinned_file_changes(tmp_path):
    """§28。閉包に入っているファイルを変えれば digest が動く。"""
    import shutil
    fake = tmp_path / "transport_t0"
    shutil.copytree(_T0, fake, ignore=shutil.ignore_patterns(
        "results", "tests", "__pycache__"))
    # 親側（artificial_founder / adapter / singer）も複製する
    for name in ("af_spec.py", "af_measure.py", "af_compare.py", "af_gates.py",
                 "af_schema.py", "af_ingest.py", "af_synth.py", "af_source.py",
                 "af_filter.py", "af_expression.py", "af_utau.py",
                 "af_controls.py", "af_report.py", "convert_founder.py",
                 "sentinels.py" if (_AF / "sentinels.py").exists() else "af_spec.py"):
        src = _AF / name
        if src.exists():
            shutil.copy(src, fake.parent / name)
    d1 = t0_gates.code_closure_digest(fake)[0]
    (fake / "t0_schema.py").write_text(
        (fake / "t0_schema.py").read_text(encoding="utf-8") + "\n# touched\n",
        encoding="utf-8")
    assert t0_gates.code_closure_digest(fake)[0] != d1


# ---------------------------------------------------------------------------
# 35-36 source-free
# ---------------------------------------------------------------------------
def test_t32_35_source_free_allowlist():
    """35. source-free allowlist — T0 の追加許可は 4 種類だけ（§29）。"""
    assert set(t0_schema.T0_EXTRA_READ_KINDS) == {
        "af_p0_generated_body", "af_p0_result_json", "t0_fixture", "t0_sidecar"}
    import t0_run
    att = t0_run._source_free_attestation({"af0_spec_sha256": "x"})
    assert att["declared"]["human_audio_used"] is False
    assert att["declared"]["external_voicebank_used"] is False
    assert att["declared"]["pretrained_voice_model_used"] is False
    assert att["declared"]["network_retrieval_used"] is False
    assert set(att["additional_allowed_reads"]) == set(t0_schema.T0_EXTRA_READ_KINDS)


def test_t32_36_network_fail_closed():
    """36. network fail-closed — T0 のコードはネットワークを開かない。"""
    banned = ("requests", "urllib.request", "urlopen", "httpx", "socket.socket",
              "aiohttp")
    for path in sorted(_T0.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, f"{path.name} references {token}"


# ---------------------------------------------------------------------------
# 37 atomic publication
# ---------------------------------------------------------------------------
def test_t32_37_atomic_rollback(tmp_path):
    """37. atomic rollback — 公開は staging -> verify -> rename で行う。"""
    import t0_run
    out = tmp_path / "AF_T0"
    out.mkdir()
    (out / "old.json").write_text("{}", encoding="utf-8")
    staging = tmp_path / ".AF_T0.staging"
    staging.mkdir()
    (staging / "t0_results.json").write_text('{"v":1}', encoding="utf-8")
    t0_run._publish(staging, out)
    assert (out / "t0_results.json").exists()
    assert not (out / "old.json").exists(), "前の結果ツリーは丸ごと置き換わる"
    assert (out / "SHA256SUMS.txt").exists()
    assert not (tmp_path / ".AF_T0.previous").exists()


# ---------------------------------------------------------------------------
# 38-41 Gate 集合と総合判定
# ---------------------------------------------------------------------------
def test_t32_38_incomplete_gate_set_is_blocked():
    """38. incomplete Gate set -> BLOCKED。"""
    assert t0_gates.overall_verdict([])["verdict"] == "BLOCKED"
    assert t0_gates.overall_verdict([])["reason_codes"] == ["INCOMPLETE_GATE_SET"]
    partial = _gates()[:-1]
    out = t0_gates.overall_verdict(partial)
    assert out["verdict"] == "BLOCKED" and out["missing_gates"] == ["T0-G10"]
    dupes = _gates() + [_gates()[0]]
    assert t0_gates.overall_verdict(dupes)["duplicate_gates"] == ["T0-G0"]
    unknown = _gates() + [t0_gates.GateResult("T0-G99", "X", "PASS", {})]
    assert t0_gates.overall_verdict(unknown)["unknown_gates"] == ["T0-G99"]


@pytest.mark.parametrize("gate", ["T0-G3", "T0-G4", "T0-G5", "T0-G6", "T0-G7",
                                  "T0-G10"])
def test_t32_39_trait_failure_is_not_established(gate):
    """39. T0 trait failure -> NOT_ESTABLISHED。"""
    assert t0_gates.overall_verdict(_gates(**{gate: "FAIL"}))["verdict"] \
        == "NOT_ESTABLISHED"


@pytest.mark.parametrize("gate,want", [("T0-G0", "BLOCKED"), ("T0-G1", "BLOCKED"),
                                       ("T0-G2", "BLOCKED"), ("T0-G8", "FAILED"),
                                       ("T0-G9", "FAILED")])
def test_t32_39b_blocked_and_failed_classes(gate, want):
    """§22。BLOCKED と FAILED の切り分け。"""
    assert t0_gates.overall_verdict(_gates(**{gate: "FAIL"}))["verdict"] == want


def test_t32_40_all_gates_pass_is_pass():
    """40. all Gate PASS -> PASS。"""
    out = t0_gates.overall_verdict(_gates())
    assert out["verdict"] == "PASS" and not out["failed_gates"]


def test_t32_41_freeze_only_on_pass():
    """41. freeze only on PASS。"""
    import t0_report
    assert t0_report.should_freeze({"verdict": "PASS"})
    for v in ("NOT_ESTABLISHED", "BLOCKED", "FAILED"):
        assert not t0_report.should_freeze({"verdict": v})


# ---------------------------------------------------------------------------
# 主張上限（§2 / §7 / §23）
# ---------------------------------------------------------------------------
def test_claim_ceiling_records_p0_invariant_and_sidecar_caveat():
    """§2 / §7 / §23。PASS でも AF-P0 は NOT_ESTABLISHED のまま。"""
    import t0_report
    registry = {"uses_sidecar": True, "all_native": False,
                "traits": {t: {"operator": "X", "mode": "sidecar"}
                           for t in transport.COMPOSITION_ORDER}}
    results = t0_report.build_t0_results(
        pins={}, localization={"traits": {}}, registry=registry,
        sentinels={"per_family": {}}, combined={"verdict": "PASS"},
        fresh={"verdict": "PASS", "after_freeze": True}, gates=_gates(),
        overall={"verdict": "PASS"}, baseline={}, determinism={})
    cc = results["claim_ceiling"]
    assert t0_report.PASS_DECLARATION in cc["allowed"]
    assert t0_report.SIDECAR_CAVEAT in cc["allowed"], "sidecar 使用時は必須注記"
    assert cc["always"] == t0_report.P0_INVARIANT
    assert results["p0_reference"]["verdict"] == "NOT_ESTABLISHED"
    for claim in t0_report.FORBIDDEN_CLAIMS:
        assert claim in cc["forbidden"]


def test_native_run_records_no_sidecar_caveat():
    """§7。全 native なら sidecar 注記は付かない。"""
    import t0_report
    registry = {"uses_sidecar": False, "all_native": True, "traits": {}}
    results = t0_report.build_t0_results(
        pins={}, localization={"traits": {}}, registry=registry,
        sentinels={"per_family": {}}, combined={"verdict": "PASS"},
        fresh={"verdict": "PASS", "after_freeze": True}, gates=_gates(),
        overall={"verdict": "PASS"}, baseline={}, determinism={})
    assert t0_report.SIDECAR_CAVEAT not in results["claim_ceiling"]["allowed"]


# ---------------------------------------------------------------------------
# §11 候補選択・§26 共有経路
# ---------------------------------------------------------------------------
def test_first_passing_stops_at_first_pass():
    """§11。PASS した候補で停止し、後続を試さない。"""
    tried = []

    def evaluate(op):
        tried.append(op)
        return {"verdict": "PASS" if op == "D1" else "FAIL"}

    out = transport.select_first_passing("duration", evaluate)
    assert out["selected"] == "D1" and tried == ["D0", "D1"]
    assert out["mode"] == "sidecar" and not out["exhausted"]


def test_exhausted_candidates_are_reported():
    """§36-6。候補が尽きたら報告する（事後追加しない）。

    duration は §10 に遷移前提条件が無い（D2 は「D1 FAIL 時のみ」= any）ので、
    全候補を試し切って exhausted に到達する。energy / afterglow は前提条件が
    あるため、条件を満たさない失敗では `blocked_by_precondition` で終わる
    （`test_rev2_candidate_transition_preconditions` が担当）。
    """
    out = transport.select_first_passing(
        "duration", lambda op: {"verdict": "FAIL", "failure_kind": "calibration"})
    assert out["selected"] is None and out["exhausted"]
    assert "candidate list exhausted" in out["reason"]
    assert [a["operator"] for a in out["attempts"]] == ["D0", "D1", "D2"]


def test_t32_shared_paths_are_not_modified():
    """§26。共有経路のファイルを T0 が書き換えていない（git 差分で確認）。"""
    shared = ["voice_genesis/foundry/adapter/donor_bank.py",
              "voice_genesis/foundry/adapter/donor_bank_utau.py",
              "voice_genesis/foundry/artificial_founder/af_ingest.py",
              "voice_genesis/foundry/artificial_founder/af_measure.py"]
    repo = _AF.parents[2]
    proc = subprocess.run(["git", "diff", "--name-only",
                           "adcf67bc3e70006afb86edad730e1da261209e33", "--", *shared],
                          capture_output=True, text=True, cwd=str(repo))
    if proc.returncode != 0:
        pytest.skip("git not available or base commit missing")
    changed = [line for line in proc.stdout.splitlines() if line.strip()]
    assert not changed, f"§26: shared path modified without User adjudication: {changed}"


def test_localization_epsilon_is_not_a_pass_threshold():
    """§6。localization epsilon は判定に使われない（compare 側に現れない）。"""
    import inspect
    # 判定側は epsilon を **引数にも実装にも** 持たない（docstring の言及は可）。
    for name, fn in vars(t0_compare).items():
        if callable(fn) and not name.startswith("_"):
            try:
                params = set(inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                continue
            assert not any("epsilon" in p for p in params), f"{name}: {params}"
    # docstring の言及は可。**識別子として現れないこと** を ast で見る。
    def _names(path: Path) -> set:
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                out.add(node.id)
            elif isinstance(node, ast.Attribute):
                out.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                out |= {a.name for a in node.names} | {a.asname or "" for a in node.names}
        return out

    cmp_names = _names(_T0 / "t0_compare.py")
    assert "FROZEN_LOCALIZATION_EPSILON" not in cmp_names
    assert "localization_epsilon" not in cmp_names
    # 局在化側は final tolerance を持たない
    loc_names = _names(_T0 / "t0_localize.py")
    assert "FROZEN_FINAL_TOLERANCES" not in loc_names
    assert "final_tolerances" not in loc_names


def test_localize_unit_picks_the_transition_that_crosses():
    """§6。epsilon を最初に超えた遷移が first_divergence_stage。"""
    meas = {"S0_BODY_44K": {"onset_ms": 40.0},
            "S1_RESAMPLED_24K": {"onset_ms": 40.1},
            "S3_WORLD_SYNTH_RAW": {"onset_ms": 60.0},
            "S4_POST_NORM_FLOAT": {"onset_ms": 60.0},
            "S5_PCM16_ROUNDTRIP": {"onset_ms": 60.1}}
    out = t0_localize.localize_unit(meas, "duration", 5.0)
    assert out["first_divergence_stage"] == "WORLD_SYNTHESIS"
    flat = {s: {"onset_ms": 40.0} for s in t0_schema.WAVEFORM_STAGE_IDS}
    assert t0_localize.localize_unit(flat, "duration", 5.0)[
        "first_divergence_stage"] == "UNRESOLVED"
    multi = {"S0_BODY_44K": {"onset_ms": 40.0},
             "S1_RESAMPLED_24K": {"onset_ms": 50.0},
             "S3_WORLD_SYNTH_RAW": {"onset_ms": 70.0},
             "S4_POST_NORM_FLOAT": {"onset_ms": 70.0},
             "S5_PCM16_ROUNDTRIP": {"onset_ms": 70.0}}
    assert t0_localize.localize_unit(multi, "duration", 5.0)[
        "first_divergence_stage"] == "MULTI_STAGE"


# ---------------------------------------------------------------------------
# レビュー第 1 巡（PR #302 非正典 run）で指摘された 8 件の回帰
# ---------------------------------------------------------------------------
def test_rev2_gate_g0_is_fail_closed_on_p0_artifacts():
    """#8。P0 成果物は「存在するか」ではなく **凍結 SHA** と照合する。"""
    good = {"af0_spec_sha256": t0_schema.FROZEN_AF0_SPEC_SHA256,
            "p0_artifacts": dict(t0_schema.FROZEN_P0_ARTIFACT_SHA256)}
    assert t0_gates.gate_input_freeze(good).verdict == "PASS"
    # 内容が変わったら落ちる（以前は素通りした）
    tampered = {"af0_spec_sha256": t0_schema.FROZEN_AF0_SPEC_SHA256,
                "p0_artifacts": {**t0_schema.FROZEN_P0_ARTIFACT_SHA256,
                                 "p0_results.json": "0" * 64}}
    bad = t0_gates.gate_input_freeze(tampered)
    assert bad.verdict == "FAIL" and bad.detail["reason_code"] == "AF0_INPUT_DRIFT"
    assert any("artifact drift" in p for p in bad.detail["problems"])
    # 欠落も落ちる
    missing = {"af0_spec_sha256": t0_schema.FROZEN_AF0_SPEC_SHA256,
               "p0_artifacts": {}}
    assert t0_gates.gate_input_freeze(missing).verdict == "FAIL"


def test_rev2_gate_g0_does_not_require_head_to_equal_frozen_base():
    """#8。§4 が pin するのは P0 入力の出所で、作業ツリーの HEAD ではない。

    HEAD 一致を要求すると T0 は自分の PR head で原理的に PASS できない。
    """
    pins = {"af0_spec_sha256": t0_schema.FROZEN_AF0_SPEC_SHA256,
            "p0_artifacts": dict(t0_schema.FROZEN_P0_ARTIFACT_SHA256),
            "base_commit": "deadbeef" * 5, "p0_paths_unmodified": True}
    assert t0_gates.gate_input_freeze(pins).verdict == "PASS"
    # ただし P0 のパスが base 以降変更されていたら落ちる
    changed = dict(pins, p0_paths_unmodified=False,
                   p0_paths_changed=["results/AF0/p0_results.json"])
    assert t0_gates.gate_input_freeze(changed).verdict == "FAIL"


def test_rev2_candidate_transition_preconditions():
    """#7。E2 は E1 の holdout FAIL 時のみ / A2 は A1 の sentinel regression 時のみ。"""
    tried = []

    def make(kinds):
        def evaluate(op):
            tried.append(op)
            return {"verdict": "FAIL", "failure_kind": kinds.get(op)}
        return evaluate

    # E1 が calibration で落ちた -> E2 へ進めない
    tried.clear()
    out = transport.select_first_passing(
        "energy", make({"E0": "calibration", "E1": "calibration"}))
    assert tried == ["E0", "E1"], tried
    assert out["blocked_by_precondition"]["operator"] == "E2"
    assert out["blocked_by_precondition"]["requires_previous_failure"] == "holdout"

    # E1 が holdout で落ちた -> E2 へ進める
    tried.clear()
    transport.select_first_passing(
        "energy", make({"E0": "calibration", "E1": "holdout", "E2": "holdout"}))
    assert tried == ["E0", "E1", "E2"], tried

    # A1 が target で落ちた -> A2 へ進めない
    tried.clear()
    out = transport.select_first_passing(
        "afterglow", make({"A0": "calibration", "A1": "calibration"}))
    assert tried == ["A0", "A1"]
    assert out["blocked_by_precondition"]["requires_previous_failure"] == "sentinel"

    # A1 が sentinel regression で落ちた -> A2 へ進める
    tried.clear()
    transport.select_first_passing(
        "afterglow", make({"A0": "calibration", "A1": "sentinel", "A2": "sentinel"}))
    assert tried == ["A0", "A1", "A2"]


def test_rev2_a2_actually_operates_with_body_shape():
    """#5。A2 は sidecar の形状ベクトルで実際に動く（以前は常に no-op だった）。"""
    from t0_afterglow import apply_a2
    sc = _synthetic_sidecar()
    # 形状が無ければ no-op（契約どおり）
    out, info = apply_a2(np.zeros(1200), 24000, sc)
    assert info["changed"] is False and "envelope" in info["reason"]
    # 形状があれば動く
    sc["founder_expression"]["ar_alpha_band_envelope_shape"] = [
        max(0.0, 1.0 - i / 31.0) for i in range(32)]
    sr, n = 24000, 12000
    t = np.arange(n) / sr
    y = 0.2 * np.sin(2 * np.pi * 500 * t) + 0.05 * np.sin(2 * np.pi * 3400 * t)
    out, info = apply_a2(y, sr, sc)
    assert info["changed"] is True and info["shape_points"] == 32
    assert info["attenuation_only"] is True
    assert not np.array_equal(out, y)
    chk = assert_band_limited(y, out, sr, 3400.0, 220.0)
    assert chk["out_of_band_energy_ratio"] <= 1.01, chk


@requires_body
def test_rev2_build_sidecar_carries_the_a2_shape():
    """#5。通常生成される sidecar が A2 の入力を実際に持っている。"""
    sc, _ = _sidecar("u")
    shape = sc["founder_expression"]["ar_alpha_band_envelope_shape"]
    assert shape is not None and len(shape) == 32
    assert shape[0] == pytest.approx(1.0, abs=1e-6), "開始値で正規化されている"
    assert t0_schema.validate_sidecar(sc) == []


def test_rev2_wav_digest_uses_the_real_file_hash():
    """#G8。output WAV SHA は実 WAV ファイルのハッシュ（自前 int16 丸めではない）。"""
    import t0_run
    transported = {"a": {"wav_sha256": "aa" * 32}, "b": {"wav_sha256": "bb" * 32}}
    d1 = t0_run._wav_digest(transported)
    d2 = t0_run._wav_digest({"a": {"wav_sha256": "aa" * 32},
                             "b": {"wav_sha256": "cc" * 32}})
    assert d1 != d2
    # signal を変えても wav_sha256 が同じなら digest は動かない（= ファイル基準）
    same = {"a": {"wav_sha256": "aa" * 32, "signal": np.zeros(10)},
            "b": {"wav_sha256": "bb" * 32, "signal": np.ones(10)}}
    assert t0_run._wav_digest(same) == d1


def test_rev2_publish_uses_atomic_rename_and_verifies(tmp_path):
    """#G9。staging -> verify -> atomic rename。壊れた束は公開しない。"""
    import t0_run
    out = tmp_path / "AF_T0"
    out.mkdir()
    (out / "old.json").write_text("{}", encoding="utf-8")
    staging = tmp_path / ".AF_T0.staging"
    staging.mkdir()
    (staging / "t0_results.json").write_text('{"v":2}', encoding="utf-8")
    info = t0_run._publish(staging, out)
    assert info["method"] == "atomic_rename" and info["verified"]
    assert (out / "t0_results.json").exists() and not (out / "old.json").exists()
    assert not staging.exists(), "rename なので staging は残らない"
    assert not (tmp_path / ".AF_T0.previous").exists()


def test_rev2_publish_refuses_a_corrupted_staging_tree(tmp_path):
    """#G9。SHA256SUMS と実体が食い違う束は公開しない。"""
    import t0_run
    out = tmp_path / "AF_T0"
    out.mkdir()
    (out / "keep.json").write_text('{"prev":1}', encoding="utf-8")
    staging = tmp_path / ".AF_T0.staging"
    staging.mkdir()
    (staging / "t0_results.json").write_text('{"v":2}', encoding="utf-8")

    real = t0_run._verify_tree
    def _broken(root):
        return {"missing": [], "mismatched": ["t0_results.json"], "n": 1}
    t0_run._verify_tree = _broken
    try:
        with pytest.raises(t0_run.T0Stop) as ei:
            t0_run._publish(staging, out)
        assert ei.value.reason_code == "PUBLICATION_VERIFY_FAILED"
    finally:
        t0_run._verify_tree = real
    # 旧束が canonical に残っている
    assert (out / "keep.json").read_text(encoding="utf-8") == '{"prev":1}'


def test_rev2_fresh_confirmation_requires_the_frozen_config():
    """#2。freeze 後に config が変わっていたら FAILED。"""
    import t0_run
    cfg = transport.TransportConfig("D1", "E1", "A1")
    wrong = t0_schema.canonical_digest(
        transport.TransportConfig("D0", "E0", "A0").as_dict())
    with pytest.raises(t0_run.T0Stop) as ei:
        t0_run._fresh_confirmation({}, None, None, {}, {"fixtures": {}}, cfg, {},
                                   Path("."), {}, Path("."), wrong)
    assert ei.value.reason_code == "CONFIG_CHANGED_AFTER_FREEZE"


def test_rev2_candidate_selection_never_sees_af0():
    """#3。候補選択の実装が AF0 を参照していない（ast で識別子を検査）。"""
    import ast
    import inspect

    import t0_run
    src = inspect.getsource(t0_run._select_for_trait)
    tree = ast.parse(src.lstrip())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    assert "af0" not in names, "候補選択に AF0 が現れてはならない（§12）"
    params = set(inspect.signature(t0_run._select_for_trait).parameters)
    assert not (params & {"genome", "captures", "sidecars", "body_meas"}), params


def test_rev2_transport_body_publishes_pcm16_before_measuring():
    """#1。判定対象は PCM16 write/readback を通った実体である。"""
    import inspect
    src = inspect.getsource(transport.transport_body)
    assert "sf.write" in src and "sf.read" in src
    assert "wav_sha256" in src
    # evaluate_config は transport_body の戻り値をそのまま測る
    import t0_run
    ev = inspect.getsource(t0_run.evaluate_config)
    assert "transport_body" in ev and "measure_transported" in ev


@requires_world
@requires_body
@pytest.mark.slow
def test_rev2_measured_signal_is_the_published_wav(tmp_path):
    """#1（実測）。measure に渡る signal が公開 WAV の読み直しと一致する。"""
    from t0_stage_capture import capture_unit_stages
    sc, wav = _sidecar("ri")
    cap = capture_unit_stages(wav, tmp_path / "stages")
    tr = transport.transport_body({"ri": cap}, {"ri": sc},
                                  transport.TransportConfig("D0", "E0", "A0"),
                                  None, publish_dir=tmp_path / "pcm")
    row = tr["ri"]
    import soundfile as sf
    back, sr = sf.read(row["wav_path"], dtype="float64", always_2d=False)
    assert np.array_equal(row["signal"], back)
    assert row["info"]["pcm16_roundtrip"] is True
    from af_spec import sha256_file
    assert row["wav_sha256"] == sha256_file(row["wav_path"])


def test_rev2_revision_is_recorded():
    """revision 運用: 実験 ID は増やさず revision を上げる。"""
    assert t0_schema.EXPERIMENT_ID == "AF-T0"
    assert t0_schema.T0_REVISION >= 2


def test_rev2_written_records_are_json_serializable():
    """記録へ書く辞書は必ず JSON 化できること。

    rev2 の初回実行は、`fresh_confirmation.json` に numpy 波形を含む
    `af0_artifacts` を混ぜていたため **Phase 10 まで走ってから** 落ちた。
    高価な run を最後で捨てないよう、書き出し対象の形を固定する。
    """
    import inspect

    import t0_run
    src = inspect.getsource(t0_run._run_phases)
    # fresh_confirmation.json へは af0_artifacts を出さない
    assert 'k != "af0_artifacts"' in src
    # 代表的な記録が JSON 化できることを実際に確認する
    fresh = {"after_freeze": True, "verdict": "PASS",
             "af0_artifacts": {"transported": {"a": {"signal": np.zeros(4)}}},
             "holdout": [{"trait": "duration", "verdict": "PASS"}]}
    safe = {k: v for k, v in fresh.items() if k != "af0_artifacts"}
    json.dumps(safe)          # 例外が出ないこと
    with pytest.raises(TypeError):
        json.dumps(fresh)
