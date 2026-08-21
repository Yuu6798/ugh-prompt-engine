"""AF-P0 の最低テスト要件（設計書 §29 の 1〜61）。

テスト名の末尾 `t<N>` が §29 の番号に対応する。番号を落とさないこと。

重いもの（Body 全合成 / cross-process / meter control / WORLD）は
`@pytest.mark.slow` を付け、日常は `pytest -m "not slow"` で外せるようにする
（CI は全件実行する = CLAUDE.md Testing 節）。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent
for _p in (_PKG, _PKG.parent / "adapter", _PKG.parent / "s1_dataprep"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import af_compare  # noqa: E402
import af_expression as ex  # noqa: E402
import af_filter as filt  # noqa: E402
import af_gates  # noqa: E402
import af_measure  # noqa: E402
import af_report  # noqa: E402
import af_schema  # noqa: E402
import af_source as src  # noqa: E402
import af_spec  # noqa: E402
import af_synth  # noqa: E402
import af_utau  # noqa: E402
import convert_founder  # noqa: E402

SPEC_PATH = _PKG / "founder_specs" / "AF0.json"
CRITERIA_PATH = _PKG / "criteria" / "AF_P0_CRITERIA.json"
CONTROLS_PATH = _PKG / "controls" / "AF_P0_CONTROLS.json"
PROBES_PATH = _PKG / "probes" / "AF_P0_PROBES.json"


def _spec() -> Dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def genome() -> af_spec.FounderGenome:
    return af_spec.load_genome(SPEC_PATH)


@pytest.fixture(scope="module")
def criteria() -> af_spec.Criteria:
    return af_spec.load_criteria(CRITERIA_PATH)


@pytest.fixture(scope="module")
def body(tmp_path_factory, genome) -> Path:
    root = tmp_path_factory.mktemp("af0_body") / "AF0"
    af_utau.write_body(genome, root)
    return root


def _has_pyworld() -> bool:
    try:
        import pyworld  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


requires_world = pytest.mark.skipif(not _has_pyworld(),
                                    reason="pyworld is not installed (G4 -> BLOCKED)")


# ---------------------------------------------------------------------------
# Schema / founder traits (1-8)
# ---------------------------------------------------------------------------
def test_t1_unknown_key_rejected():
    spec = _spec()
    spec["generator"]["unexpected_field"] = 1.0
    errs = af_schema.validate_founder_spec(spec)
    assert any("unknown key" in e for e in errs)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_t2_nan_inf_rejected(bad):
    spec = _spec()
    spec["identity_signature"]["founder_resonances"]["AR-alpha"]["center_hz"] = bad
    errs = af_schema.validate_founder_spec(spec)
    assert any("non-finite" in e for e in errs)


def test_t3_invalid_formants_rejected():
    spec = _spec()
    spec["identity_signature"]["vowels"]["a"] = [1350.0, 720.0, 2600.0]
    errs = af_schema.validate_founder_spec(spec)
    assert any("ascending" in e for e in errs)


def test_t4_ar_alpha_nyquist_violation_rejected():
    spec = _spec()
    spec["identity_signature"]["founder_resonances"]["AR-alpha"]["center_hz"] = 30000.0
    errs = af_schema.validate_founder_spec(spec)
    assert any("Nyquist" in e for e in errs)


def test_t5_ar_alpha_nonpositive_bandwidth_rejected():
    spec = _spec()
    spec["identity_signature"]["founder_resonances"]["AR-alpha"]["bandwidth_hz"] = 0.0
    errs = af_schema.validate_founder_spec(spec)
    assert any("AR-alpha.bandwidth_hz" in e for e in errs)


@pytest.mark.parametrize("ratio", [0.0, -0.2, 1.5])
def test_t6_beta_alpha_ratio_range_rejected(ratio):
    spec = _spec()
    spec["identity_signature"]["founder_resonances"]["AR-beta"]["beta_alpha_energy_ratio"] = ratio
    errs = af_schema.validate_founder_spec(spec)
    assert any("beta_alpha_energy_ratio" in e for e in errs)


@pytest.mark.parametrize("field", ["odd_multiplier", "even_multiplier"])
def test_t7_odd_even_multiplier_nonpositive_rejected(field):
    spec = _spec()
    spec["founder_source_traits"]["HL-alpha"][field] = 0.0
    errs = af_schema.validate_founder_spec(spec)
    assert any(field in e for e in errs)


def test_t8_negative_afterglow_rejected():
    spec = _spec()
    spec["founder_expression_traits"]["AG-alpha"]["ar_alpha_afterglow_extra_ms"] = -1.0
    errs = af_schema.validate_founder_spec(spec)
    assert any("ar_alpha_afterglow_extra_ms" in e for e in errs)


def test_spec_as_shipped_is_valid():
    assert af_schema.validate_founder_spec(_spec()) == []


# ---------------------------------------------------------------------------
# Source (9-13)
# ---------------------------------------------------------------------------
def test_t9_fixed_harmonic_vector():
    amps = src.harmonic_amplitudes(6, 1.15, 1.0, 0.72)
    expected = np.array([h ** -1.15 * (1.0 if h % 2 else 0.72) for h in range(1, 7)])
    assert np.allclose(amps, expected, rtol=0, atol=1e-15)


def test_t10_hl_alpha_odd_even_reference():
    amps = src.harmonic_amplitudes(40, 1.15, 1.0, 0.72)
    h = np.arange(1, 41)
    ratio = (amps[h % 2 == 0] * (h[h % 2 == 0] ** 1.15)) / (amps[0] * 1.0)
    assert np.allclose(ratio, 0.72, atol=1e-12)


def test_t11_fixed_prng_vector():
    a = src.xorshift64star(0x0123456789ABCDEF, 8)
    b = src.xorshift64star(0x0123456789ABCDEF, 8)
    assert np.array_equal(a, b)
    assert np.all(np.abs(a) <= 1.0)
    # 領域分離: unit / component が違えば別系列。
    n1 = src.deterministic_noise("AF0", "あ", "breath", 32)
    n2 = src.deterministic_noise("AF0", "い", "breath", 32)
    n3 = src.deterministic_noise("AF0", "あ", "onset:r", 32)
    assert not np.array_equal(n1, n2) and not np.array_equal(n1, n3)
    assert np.array_equal(n1, src.deterministic_noise("AF0", "あ", "breath", 32))


def test_t12_jitter_zero_reference():
    f0 = src.f0_trajectory(1000, 44100, 261.625565, 10_000, 20_000, -100.0)
    assert np.allclose(f0, 261.625565, atol=1e-12)


def test_t13_terminal_minus_100_cent_trajectory():
    n, start, end = 4000, 1000, 3000
    f0 = src.f0_trajectory(n, 44100, 261.625565, start, end, -100.0)
    assert f0[start - 1] == pytest.approx(261.625565)
    assert 1200 * math.log2(f0[-1] / 261.625565) == pytest.approx(-100.0, abs=1e-6)
    assert np.all(np.diff(f0[start:end]) <= 1e-12)


# ---------------------------------------------------------------------------
# Filter (14-18)
# ---------------------------------------------------------------------------
def test_t14_vowel_peak_reference():
    sr = 44100
    freqs = np.linspace(100.0, 4000.0, 4000)
    mag = np.abs(filt.formant_parallel_response(freqs, (720.0, 1350.0, 2600.0),
                                                (70.0, 95.0, 140.0), sr))
    for target in (720.0, 1350.0, 2600.0):
        band = (freqs > target - 60) & (freqs < target + 60)
        peak = freqs[band][int(np.argmax(mag[band]))]
        assert abs(peak - target) < 25.0


def test_t15_ar_alpha_center_reference():
    sr = 44100
    gain = filt.solve_branch_gain_for_peak_db(3400.0, 220.0, sr, 6.0)
    at_center = abs(1.0 + gain * filt.modal_branch_response(np.array([3400.0]), 3400.0,
                                                            220.0, sr)[0])
    assert 20 * math.log10(at_center) == pytest.approx(6.0, abs=1e-6)


def test_t16_ar_beta_center_reference():
    sr = 44100
    ga = filt.solve_branch_gain_for_peak_db(3400.0, 220.0, sr, 6.0)
    gb, detail = filt.calibrate_ar_beta_gain(ga, 3400.0, 220.0, 5100.0, 300.0, sr, 0.35)
    assert gb > 0.0
    freqs = np.linspace(4000.0, 6500.0, 2500)
    resp = 20 * np.log10(np.abs(1.0 + gb * filt.modal_branch_response(freqs, 5100.0, 300.0, sr)))
    assert abs(freqs[int(np.argmax(resp))] - 5100.0) < 15.0


def test_t17_beta_alpha_relation():
    sr = 44100
    ga = filt.solve_branch_gain_for_peak_db(3400.0, 220.0, sr, 6.0)
    _, detail = filt.calibrate_ar_beta_gain(ga, 3400.0, 220.0, 5100.0, 300.0, sr, 0.35)
    realized = 10 ** ((detail["beta_prominence_db"] - detail["alpha_prominence_db"]) / 10)
    assert realized == pytest.approx(0.35, abs=1e-6)


def test_t18_filter_stability():
    sr = 44100
    x = np.zeros(4096)
    x[0] = 1.0
    for center, bw in ((280.0, 70.0), (3400.0, 220.0), (5100.0, 300.0)):
        y = filt.apply_resonator(x, center, bw, sr)
        assert np.all(np.isfinite(y))
        assert abs(y[-1]) < abs(y[:64]).max() * 1e-6
    branch = filt.modal_branch(x, 3400.0, 220.0, sr, 1.0)
    assert np.all(np.isfinite(branch.signal))
    assert abs(branch.signal[-1]) < 1e-9


# ---------------------------------------------------------------------------
# Expression (19-22)
# ---------------------------------------------------------------------------
def test_t19_attack_reference(genome):
    unit = af_spec.ALIAS_BY_STEM["a"]
    tl = af_spec.timeline_for(genome, unit)
    env = ex.build_envelopes(genome, unit, tl)
    ramp = env.main[tl.lead:env.sustain_start]
    t10 = int(np.argmax(ramp >= 0.1))
    t90 = int(np.argmax(ramp >= 0.9))
    assert (t90 - t10) * 1000.0 / tl.sr == pytest.approx(genome.attack_ms, abs=0.5)


def test_t20_120ms_taper_reference(genome):
    unit = af_spec.ALIAS_BY_STEM["a"]
    tl = af_spec.timeline_for(genome, unit)
    env = ex.build_envelopes(genome, unit, tl)
    taper = env.main[tl.art_end:tl.main_end]
    assert taper.size * 1000.0 / tl.sr == pytest.approx(genome.main_taper_ms, abs=0.03)
    ideal = ex.half_cosine_taper(taper.size)
    assert np.allclose(taper, ideal, atol=1e-12)
    # 区間の最終サンプルは -140 dB 以下、その次（= main_end）が厳密に 0。
    assert taper[-1] < 1e-6
    assert env.main[tl.main_end] == 0.0


def test_t21_ar_alpha_afterglow_extra_35ms(genome):
    unit = af_spec.ALIAS_BY_STEM["a"]
    tl = af_spec.timeline_for(genome, unit)
    env = ex.build_envelopes(genome, unit, tl)
    # main は main_end で 0、AR-α はそこから 35 ms 生き残る（§10）。
    assert env.main[tl.main_end - 1] < 1e-6
    assert np.all(env.main[tl.main_end:] == 0.0)
    assert env.ar_alpha[tl.main_end] > 0.0
    assert np.all(env.ar_alpha[tl.ag_end:] == 0.0)
    extra = (tl.ag_end - tl.main_end) * 1000.0 / tl.sr
    assert extra == pytest.approx(genome.ar_alpha_afterglow_extra_ms, abs=0.03)


def test_t22_final_digital_zero(genome):
    su = af_synth.synthesize_unit(genome, af_spec.ALIAS_BY_STEM["a"])
    tail = int(round(genome.tail_zero_ms * genome.sample_rate_hz / 1000.0))
    assert np.all(su.pcm16[-tail:] == 0)
    assert su.pcm16[-1] == 0
    assert su.truth.peak_abs < 1.0  # PCM16 でクリップしない


# ---------------------------------------------------------------------------
# UTAU (23-30)
# ---------------------------------------------------------------------------
def test_t23_25_aliases(genome, body):
    detail = af_utau.validate_body(genome, body)
    assert detail["n_entries"] == 25
    assert sorted(detail["aliases"]) == sorted(u.alias for u in genome.units)


def test_t24_25_wav(genome, body):
    assert af_utau.validate_body(genome, body)["n_wav"] == 25


def test_t25_malformed_oto_rejected(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    oto = root / genome.pitch_dir / "oto.ini"
    text = oto.read_bytes().decode(af_utau.OTO_ENCODING)
    oto.write_bytes((text + "broken.wav=x,1,2\r\n").encode(af_utau.OTO_ENCODING))
    detail = af_utau.validate_body(genome, root)
    assert detail["n_malformed_lines"] == 1
    assert detail["verdict"] == "FAIL"


def test_t26_missing_wav_rejected(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    (root / genome.pitch_dir / "a.wav").unlink()
    detail = af_utau.validate_body(genome, root)
    assert detail["missing_wav"] == ["a.wav"]
    assert detail["verdict"] == "FAIL"


def test_t27_orphan_wav_rejected(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    shutil.copy2(root / genome.pitch_dir / "a.wav", root / genome.pitch_dir / "zz.wav")
    detail = af_utau.validate_body(genome, root)
    assert detail["orphan_wav"] == ["zz.wav"]
    assert detail["verdict"] == "FAIL"


def test_t28_nested_reference_rejected(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    oto = root / genome.pitch_dir / "oto.ini"
    text = oto.read_bytes().decode(af_utau.OTO_ENCODING)
    oto.write_bytes(text.replace("a.wav=", "../a.wav=", 1).encode(af_utau.OTO_ENCODING))
    detail = af_utau.validate_body(genome, root)
    assert detail["nested_wav_refs"]
    assert detail["verdict"] == "FAIL"


def test_t29_identity_hash_detects_wav_substitution(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    before = af_utau.check_sha256sums(root)
    assert before["verdict"] == "PASS"
    shutil.copy2(root / genome.pitch_dir / "i.wav", root / genome.pitch_dir / "a.wav")
    after = af_utau.check_sha256sums(root)
    assert after["verdict"] == "FAIL"
    assert f"{genome.pitch_dir}/a.wav" in after["mismatched"]


def test_t30_identity_hash_detects_oto_substitution(genome, body, tmp_path):
    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    oto = root / genome.pitch_dir / "oto.ini"
    oto.write_bytes(oto.read_bytes() + b"\r\n")
    after = af_utau.check_sha256sums(root)
    assert after["verdict"] == "FAIL"
    assert f"{genome.pitch_dir}/oto.ini" in after["mismatched"]


# ---------------------------------------------------------------------------
# Determinism (31-34)
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_t31_same_process_sha(genome, tmp_path):
    a = af_utau.write_body(genome, tmp_path / "a" / "AF0")
    b = af_utau.write_body(genome, tmp_path / "b" / "AF0")
    assert a.identity_digest == b.identity_digest
    assert a.sha_rows == b.sha_rows


@pytest.mark.slow
def test_t32_cross_process_sha(genome, tmp_path):
    build = af_utau.write_body(genome, tmp_path / "parent" / "AF0")
    proc = subprocess.run(
        [sys.executable, str(_PKG / "p0_run.py"), "--compile-only", "--spec", str(SPEC_PATH),
         "--out", str(tmp_path / "child" / "AF0")],
        capture_output=True, text=True, cwd=str(_PKG))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["digest"] == build.identity_digest


@pytest.mark.slow
def test_t33_manifest_reproducibility(genome, tmp_path):
    a = af_utau.write_body(genome, tmp_path / "a" / "AF0")
    b = af_utau.write_body(genome, tmp_path / "b" / "AF0")
    assert (a.root / "founder_manifest.json").read_bytes() == \
        (b.root / "founder_manifest.json").read_bytes()
    assert (a.root / "unit_truth.json").read_bytes() == (b.root / "unit_truth.json").read_bytes()


@pytest.mark.slow
def test_t34_one_field_change_changes_hash(genome, tmp_path):
    base = af_utau.write_body(genome, tmp_path / "base" / "AF0")
    mutated = af_spec.genome_from_dict(
        af_spec.apply_patch(_spec(),
                            {"identity_signature.founder_resonances.AR-alpha.center_hz": 3401.0}))
    other = af_utau.write_body(mutated, tmp_path / "mut" / "AF0")
    assert mutated.sha256 != genome.sha256
    assert other.identity_digest != base.identity_digest


# ---------------------------------------------------------------------------
# Ingestion (35-40)
# ---------------------------------------------------------------------------
@requires_world
@pytest.mark.slow
def test_t35_t40_ingestion(genome, criteria, body, tmp_path):
    import af_ingest
    bank, unit_vowels, clips, stats = af_ingest.build_bank(genome, body, criteria.ingestion)
    health = af_ingest.bank_health(bank, unit_vowels, clips, stats, criteria.ingestion)
    # t35: all vowel units / t36: required onsets
    assert set(criteria.ingestion["required_vowels"]).issubset(set(unit_vowels.values()))
    assert set(criteria.ingestion["required_onsets"]).issubset({k for k, v in clips.items() if v})
    assert stats["n_wav_files_analyzed"] == criteria.ingestion["expected_wav_count"]
    # t37: WORLD finite
    assert health["world_f0_finite"] and health["world_sp_finite"] and health["world_ap_finite"]
    assert health["verdict"] == "PASS"
    # t38: re-expression finite
    re = af_ingest.reexpress_body(genome, body, tmp_path / "re")
    assert re["all_finite"]
    # t39: join smoke
    join = af_ingest.join_smoke(genome, bank, unit_vowels,
                                criteria.ingestion["join_smoke_sequence"])
    assert join["verdict"] == "PASS"
    # t40: dataset conversion
    ds = convert_founder.convert_from_genome(genome, body, tmp_path / "dataset")
    assert ds["verdict"] == "PASS" and ds["n_items"] == 25


def test_dataset_ph_dur_sums_to_wav_length(genome):
    for unit in genome.units:
        tl = af_spec.timeline_for(genome, unit)
        seq, dur = convert_founder.phoneme_segments(genome, unit, tl)
        assert len(seq) == len(dur)
        assert sum(dur) == pytest.approx(tl.total / tl.sr, abs=1e-9)
        assert all(d > 0 for d in dur)


# ---------------------------------------------------------------------------
# Meter (41-49)
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_t41_t48_meter_controls(criteria):
    import af_controls
    controls, _ = af_spec.load_controls(CONTROLS_PATH)
    result = af_controls.run_controls(_spec(), controls, criteria.metric_definitions)
    families = {f["family"]: f for f in result["families"]}
    expected = {"hl_even_odd_ratio", "ar_alpha_center", "beta_alpha_ratio",
                "terminal_f0_delta", "duration_r_share", "energy_sustain", "release",
                "afterglow"}
    assert expected.issubset(families)
    failed = [k for k, v in families.items() if v["verdict"] != "PASS"]
    assert not failed, f"METER_NOT_CALIBRATED: {failed}"


def test_t49_af_measure_cannot_read_ground_truth():
    # 1) 明示的な read tripwire
    for forbidden in (SPEC_PATH, CRITERIA_PATH, CONTROLS_PATH, PROBES_PATH):
        with pytest.raises(af_measure.MeterTripwire):
            af_measure.guard_read_path(forbidden)
    with pytest.raises(af_measure.MeterTripwire):
        af_measure.read_wav_mono(_PKG / "founder_specs" / "AF0.json")
    # 2) import 閉包に AF0 の設計値を持つモジュールが入っていない
    source = (_PKG / "af_measure.py").read_text(encoding="utf-8")
    for banned in ("import af_spec", "import af_synth", "import af_compare",
                   "from af_spec", "from af_synth", "from af_compare"):
        assert banned not in source


# ---------------------------------------------------------------------------
# Verdict (50-56)
# ---------------------------------------------------------------------------
def _all_pass_body() -> Dict[str, Any]:
    keys = ("identity", "hl_alpha", "ar_alpha", "ar_beta", "f0_core", "terminal_f0",
            "duration_onset", "duration_share", "energy_sustain", "energy_attack",
            "release", "afterglow", "terminal_zero")
    return {k: {"verdict": "PASS"} for k in keys}


def _all_pass_reexp() -> Dict[str, Any]:
    keys = ("spectral_identity", "hl_alpha", "ar_alpha", "ar_beta", "f0_core", "terminal_f0",
            "duration_onset", "duration_share", "energy_sustain", "release", "afterglow")
    return {k: {"verdict": "PASS"} for k in keys}


def _gates(body: Dict[str, Any], reexp: Dict[str, Any], **overrides: str):
    base = {
        "G0": af_gates.gate_source_free(
            {"human_audio_used": False, "speaker_specific_parameters_used": False,
             "pretrained_voice_model_used": False, "external_voicebank_used": False},
            {"violations": [], "network_attempts": [], "n_reads": 3}),
        "G1": af_gates.gate_spec_valid([]),
        "G2": af_gates.gate_determinism({"match": True}, {"match": True}),
        "G3": af_gates.gate_utau_body({"verdict": "PASS"}, {"verdict": "PASS"}),
        "G4": af_gates.gate_ingestion({"verdict": "PASS"}, {"verdict": "PASS"},
                                      {"all_finite": True}, {"verdict": "PASS"}),
        "G5": af_gates.gate_meter_control({"families": [{"family": "x", "verdict": "PASS"}]}),
        "G14": af_gates.gate_provenance(
            {k: "x" for k in ("spec_sha256", "criteria_sha256", "controls_sha256",
                              "probes_sha256", "code_closure_sha256", "body_identity_digest")},
            {"published": "p", "bundle_verified": True, "partial_artifacts": []}),
    }
    for gid, verdict in overrides.items():
        base[gid] = af_gates.GateResult(gid, base[gid].name, verdict, base[gid].detail)
    gates = list(base.values()) + af_gates.trait_gates(body, reexp)
    return sorted(gates, key=lambda g: int(g.gate_id[1:]))


def test_all_pass_is_overall_pass():
    verdict = af_gates.overall_verdict(_gates(_all_pass_body(), _all_pass_reexp()))
    assert verdict["verdict"] == "PASS"


def test_t50_ar_alpha_fail_is_founder_identity_not_established():
    body = _all_pass_body()
    body["ar_alpha"] = {"verdict": "FAIL"}
    v = af_gates.overall_verdict(_gates(body, _all_pass_reexp()))
    assert v["verdict"] == "NOT_ESTABLISHED"
    assert "FOUNDER_IDENTITY_NOT_ESTABLISHED" in v["reason_codes"]


def test_t51_hl_fail_is_founder_source_not_established():
    body = _all_pass_body()
    body["hl_alpha"] = {"verdict": "FAIL"}
    v = af_gates.overall_verdict(_gates(body, _all_pass_reexp()))
    assert v["verdict"] == "NOT_ESTABLISHED"
    assert "FOUNDER_SOURCE_NOT_ESTABLISHED" in v["reason_codes"]


def test_t52_ag_fail_is_founder_expression_not_established():
    reexp = _all_pass_reexp()
    reexp["afterglow"] = {"verdict": "FAIL"}
    v = af_gates.overall_verdict(_gates(_all_pass_body(), reexp))
    assert v["verdict"] == "NOT_ESTABLISHED"
    assert "AFTERGLOW_NOT_ESTABLISHED" in v["reason_codes"]


def test_t53_source_free_violation_is_failed():
    v = af_gates.overall_verdict(_gates(_all_pass_body(), _all_pass_reexp(), G0="FAIL"))
    assert v["verdict"] == "FAILED"


def test_t54_determinism_violation_is_failed():
    gates = _gates(_all_pass_body(), _all_pass_reexp())
    gates = [af_gates.gate_determinism({"match": True}, {"match": False})
             if g.gate_id == "G2" else g for g in gates]
    v = af_gates.overall_verdict(gates)
    assert v["verdict"] == "FAILED"


def test_t55_meter_fail_is_blocked():
    gates = _gates(_all_pass_body(), _all_pass_reexp())
    gates = [af_gates.gate_meter_control({"families": [{"family": "hl", "verdict": "FAIL"}]})
             if g.gate_id == "G5" else g for g in gates]
    v = af_gates.overall_verdict(gates)
    assert v["verdict"] == "BLOCKED"
    assert "METER_NOT_CALIBRATED" in v["reason_codes"]


def test_t55b_missing_dependency_is_blocked():
    gates = _gates(_all_pass_body(), _all_pass_reexp())
    gates = [af_gates.gate_ingestion({"verdict": "SKIPPED"}, {"verdict": "SKIPPED"},
                                     {"all_finite": False}, {"verdict": "SKIPPED"})
             if g.gate_id == "G4" else g for g in gates]
    assert af_gates.overall_verdict(gates)["verdict"] == "BLOCKED"


def test_t56_freeze_only_on_pass(genome, tmp_path):
    results = {"overall": {"verdict": "NOT_ESTABLISHED"}, "pins": {}}
    with pytest.raises(ValueError):
        af_report.write_freeze(tmp_path, genome, results)
    results = {"overall": {"verdict": "PASS"}, "pins": {"spec_sha256": genome.sha256}}
    af_report.write_freeze(tmp_path, genome, results)
    assert (tmp_path / "freeze" / "ARTIFICIAL_FOUNDER_AF0_FREEZE.json").exists()
    assert (tmp_path / "freeze" / "ARTIFICIAL_FOUNDER_AF0_FREEZE.md").exists()


def test_claim_ceiling_is_recorded():
    """§4: PASS でも言ってはいけない主張を record が必ず併記する。"""
    for forbidden in ("人工生命が成立", "世代遺伝が成立", "crossover 成立"):
        assert forbidden in af_report.FORBIDDEN_CLAIMS
    assert "Artificial Founder AF0 ESTABLISHED" in af_report.PASS_DECLARATION


# ---------------------------------------------------------------------------
# Regression / discipline (57-61)
# ---------------------------------------------------------------------------
@requires_world
@pytest.mark.slow
def test_t57_existing_donor_bank_utau_normal_flow(genome, criteria, body):
    """既存 adapter を **無改変** の通常フローで呼べる（AF0 専用分岐を作らない）。"""
    from donor_bank_utau import build_donor_bank_utau

    bank, unit_vowels, clips, stats = build_donor_bank_utau(
        str(body), pitch_dirs=[genome.pitch_dir], max_wav_files=25, min_units_per_vowel=5)
    assert len(bank.units) == 25
    assert stats["n_pitch_dirs"] == 1


def test_t58_existing_adapter_modules_untouched():
    """P0 は既存 Ritsu/PJS ロジックを変更しない（§12）。

    git 追跡下の adapter / s1_dataprep が本 PR で変更されていないことを確認する。
    """
    repo = _PKG.parents[2]
    proc = subprocess.run(["git", "diff", "--name-only", "origin/main...HEAD"],
                          capture_output=True, text=True, cwd=str(repo))
    if proc.returncode != 0:
        pytest.skip("git history unavailable in this environment")
    changed = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    protected = [c for c in changed
                 if c.startswith("voice_genesis/foundry/adapter/")
                 or c.startswith("voice_genesis/foundry/s1_dataprep/")]
    assert protected == [], f"P0 must not modify existing ingestion code: {protected}"


def test_t59_convert_ritsu_unchanged():
    """`convert_ritsu.py` の公開 API が残っている（AF0 変換器は別ファイル）。"""
    import convert_ritsu  # noqa: E402  (s1_dataprep)

    assert hasattr(convert_ritsu, "convert")
    assert hasattr(convert_ritsu, "build_segment_for_wav")
    assert convert_founder.__file__ != convert_ritsu.__file__


@pytest.mark.slow
def test_t60_producer_verifier_happy_path(genome, criteria, body):
    """compile -> validate -> measure -> compare が Body で一貫して通る。"""
    assert af_utau.validate_body(genome, body)["verdict"] == "PASS"
    stem = "a"
    m = af_measure.measure_wav_file(body / genome.pitch_dir / f"{stem}.wav",
                                    criteria.metric_definitions, stem)
    assert m["core_f0_hz"] == pytest.approx(genome.core_f0_hz, rel=1e-4)
    assert m["sustain_dbfs"] == pytest.approx(genome.sustain_dbfs, abs=0.5)
    assert m["ar_alpha_center_hz"] == pytest.approx(genome.ar_alpha_center_hz, abs=50.0)
    assert m["afterglow_extra_ms"] == pytest.approx(genome.ar_alpha_afterglow_extra_ms, abs=5.0)
    cmp_body = af_compare.compare_body(genome, criteria, {stem: m})
    assert cmp_body["ar_alpha"]["rows"][0]["error"] is not None


def test_t61_artifact_rollback(genome, tmp_path, monkeypatch):
    """§28: 公開が途中で失敗しても旧 valid bundle が残る。"""
    published = tmp_path / "published"
    first = tmp_path / "stage1" / "AF0"
    first.mkdir(parents=True)
    (first / "marker.txt").write_text("v1", encoding="utf-8")
    af_utau.publish_atomically(first, published)
    assert (published / "marker.txt").read_text(encoding="utf-8") == "v1"

    second = tmp_path / "stage2" / "AF0"
    second.mkdir(parents=True)
    (second / "marker.txt").write_text("v2", encoding="utf-8")

    real_rename = af_utau.os.rename
    calls = {"n": 0}

    def flaky_rename(a, b):  # 2 段目（staging -> published）だけ落とす
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated publication failure")
        return real_rename(a, b)

    monkeypatch.setattr(af_utau.os, "rename", flaky_rename)
    with pytest.raises(OSError):
        af_utau.publish_atomically(second, published)
    assert (published / "marker.txt").read_text(encoding="utf-8") == "v1"


def test_publish_rejects_missing_staging(tmp_path):
    with pytest.raises(af_spec.AFStop) as excinfo:
        af_utau.publish_atomically(tmp_path / "nope", tmp_path / "pub")
    assert excinfo.value.status == "FAILED"


# ---------------------------------------------------------------------------
# PR #301 Codex レビュー第 1 巡（P1 x7）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_overall_verdict_rejects_incomplete_gate_set():
    """欠けた Gate 集合を PASS にしない（`overall_verdict([])` は偽の成功だった）。"""
    v = af_gates.overall_verdict([])
    assert v["verdict"] == "BLOCKED"
    assert "INCOMPLETE_GATE_SET" in v["reason_codes"]
    assert v["gate_set_error"]["missing"] == list(af_gates.ALL_GATE_IDS)

    full = _gates(_all_pass_body(), _all_pass_reexp())
    assert af_gates.overall_verdict(full)["verdict"] == "PASS"
    # 1 つ落とすだけで判定不能になる
    partial = [g for g in full if g.gate_id != "G7"]
    assert af_gates.overall_verdict(partial)["verdict"] == "BLOCKED"
    # 重複も判定不能
    assert af_gates.overall_verdict(list(full) + [full[0]])["verdict"] == "BLOCKED"


def test_skipped_gate_is_not_pass():
    full = _gates(_all_pass_body(), _all_pass_reexp())
    skipped = [af_gates.GateResult(g.gate_id, g.name, "SKIPPED", {}) if g.gate_id == "G5" else g
               for g in full]
    assert af_gates.overall_verdict(skipped)["verdict"] == "BLOCKED"


def test_code_closure_covers_transitive_modules():
    """取り込み経路が実行する `singer/phoneme_jp.py` まで pin する。"""
    digest, rows = af_gates.code_closure_digest(_PKG)
    labels = {label for label, _ in rows}
    assert "adapter/donor_bank_utau.py" in labels
    assert "singer/phoneme_jp.py" in labels, "transitive import must be pinned"
    assert "artificial_founder/af_measure.py" in labels
    assert digest == af_gates.code_closure_digest(_PKG)[0]  # 決定論


def test_source_free_audit_fails_closed_on_unknown_reads(tmp_path):
    """denylist に無い拡張子でも、allowlist の外なら violation にする。"""
    staging = tmp_path / "staging"
    staging.mkdir()
    audit = af_gates.SourceFreeAudit(allowed_roots=[_PKG], staging_roots=[staging],
                                     pinned_inputs=[SPEC_PATH], runtime_roots=[])
    audit.record(staging / "AF0" / "a.wav")          # 生成物 -> 許可
    audit.record(SPEC_PATH)                          # pinned input -> 許可
    audit.record(_PKG / "af_source.py")              # 自パッケージのソース -> 許可
    audit.record(tmp_path / "speaker.npy")           # 未知拡張子 + 範囲外 -> 拒否
    audit.record(tmp_path / "model.pkl")             # 同上
    audit.record(tmp_path / "external.wav")          # denylist 拡張子 -> 拒否
    paths = {v["path"] for v in audit.violations()}
    assert str(tmp_path / "speaker.npy") in paths
    assert str(tmp_path / "model.pkl") in paths
    assert str(tmp_path / "external.wav") in paths
    assert str(_PKG / "af_source.py") not in paths
    assert str(SPEC_PATH) not in paths
    assert af_gates.gate_source_free(
        {"human_audio_used": False, "speaker_specific_parameters_used": False,
         "pretrained_voice_model_used": False, "external_voicebank_used": False},
        audit.as_dict()).verdict == "FAIL"


def test_prepare_output_tree_clears_stale_artifacts_but_keeps_voicebank(tmp_path):
    """PASS 実行の freeze が後続実行に生き残らない。旧 voicebank は §28 で保持。"""
    import p0_run

    out = tmp_path / "AF0"
    (out / "freeze").mkdir(parents=True)
    (out / "freeze" / "ARTIFICIAL_FOUNDER_AF0_FREEZE.json").write_text("{}", encoding="utf-8")
    (out / "AF_P0_RECORD.md").write_text("stale", encoding="utf-8")
    (out / "voicebank" / "AF0").mkdir(parents=True)
    (out / "voicebank" / "AF0" / "marker.txt").write_text("v1", encoding="utf-8")

    detail = p0_run.prepare_output_tree(out)
    assert detail["cleared"] and detail["preserved_voicebank"]
    assert not (out / "freeze").exists()
    assert not (out / "AF_P0_RECORD.md").exists()
    assert (out / "voicebank" / "AF0" / "marker.txt").read_text(encoding="utf-8") == "v1"


def test_meter_failure_blocks_before_compiling_af0(tmp_path, monkeypatch):
    """§17: control が落ちたら AF0 を compile / 測定せず BLOCKED で止まる。"""
    import af_controls
    import p0_run

    monkeypatch.setattr(af_controls, "run_controls", lambda *a, **k: {
        "families": [{"family": "hl_even_odd_ratio", "metric": "hl_even_odd_ratio",
                      "verdict": "FAIL"}], "n_failed": 1})

    def _explode(*args, **kwargs):
        raise AssertionError("AF0 must not be compiled while the meter is uncalibrated")

    monkeypatch.setattr(p0_run, "compile_body", _explode)
    out = tmp_path / "AF0"
    code = p0_run.run(SPEC_PATH, CRITERIA_PATH, CONTROLS_PATH, PROBES_PATH, out)
    assert code == af_spec.EXIT_CODES["BLOCKED"]
    results = json.loads((out / "p0_results.json").read_text(encoding="utf-8"))
    assert results["overall"]["verdict"] == "BLOCKED"
    assert "METER_NOT_CALIBRATED" in results["overall"]["reason_codes"]
    assert not (out / "voicebank").exists()
    assert not (out / "freeze").exists()
    # 未評価の Gate は欠落ではなく SKIPPED で並ぶ（集合は常に G0-G14）。
    verdicts = {g["gate"]: g["verdict"] for g in results["gates"]}
    assert set(verdicts) == set(af_gates.ALL_GATE_IDS)
    assert verdicts["G5"] == "FAIL" and verdicts["G0"] == "SKIPPED"


def test_publication_withheld_when_prerequisites_fail():
    """G0/G2 が落ちた候補で canonical 公開場所を上書きしない（§28）。"""
    import p0_run

    assert p0_run.PUBLICATION_PREREQUISITES == ("G0", "G1", "G2", "G3")
    withheld = af_gates.GateResult("G14", "PROVENANCE_AND_PUBLICATION", "SKIPPED",
                                   {"reason_code": "PUBLICATION_WITHHELD"})
    gates = [g for g in _gates(_all_pass_body(), _all_pass_reexp(), G0="FAIL")
             if g.gate_id != "G14"] + [withheld]
    v = af_gates.overall_verdict(gates)
    assert v["verdict"] == "FAILED"  # G0 違反が verdict を決める（G14 は SKIPPED）
