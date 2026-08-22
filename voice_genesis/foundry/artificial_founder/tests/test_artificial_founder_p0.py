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


def _control_row(family: str, **over: Any) -> Dict[str, Any]:
    """凍結契約どおりの control 行（family / metric / patch_paths / verdict）。"""
    want = af_gates.REQUIRED_CONTROL_CONTRACT[family]
    row = {"family": family, "metric": want["metric"],
           "unit_alias": want["unit_alias"], "min_separation": want["min_separation"],
           "low_patch": dict(want["low"]), "high_patch": dict(want["high"]),
           "patch_paths": sorted(set(want["low"]) | set(want["high"])),
           "verdict": "PASS"}
    row.update(over)
    return row


def _passing_controls() -> Dict[str, Any]:
    return {"families": [_control_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]}


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
        "G5": af_gates.gate_meter_control(_passing_controls()),
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
    """`convert_ritsu.py` の公開 API が残っている（AF0 変換器は別ファイル）。

    import ではなく **静的検査** で見る。`convert_ritsu` は `donor_bank_utau` ->
    `donor_bank` -> `pyworld` を引くため、import すると pyworld 未導入環境で
    この非回帰テストごと落ちる（検査したいのは「既存ファイルを壊していないか」
    であって WORLD の有無ではない）。
    """
    import ast

    src = _PKG.parents[1] / "foundry" / "s1_dataprep" / "convert_ritsu.py"
    assert src.exists()
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"convert", "build_segment_for_wav"} <= names
    assert convert_founder.__file__ != str(src)


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


@requires_world
def test_oto_parser_matches_adapter(genome, body):
    """依存フリーの oto パーサが共有実装と同値であることを pin する。

    `af_utau.parse_oto_text` は `adapter/donor_bank_utau.parse_oto_text` の写しで、
    G3 を WORLD 非依存に保つためだけに分離している。規則が食い違うと G3 の
    malformed 行検出（黙殺の検出）が共有実装とずれるので、pyworld 導入環境で
    同値を固定する。
    """
    from donor_bank_utau import decode_oto_bytes as adapter_decode
    from donor_bank_utau import parse_oto_text as adapter_parse

    raw = (body / genome.pitch_dir / "oto.ini").read_bytes()
    assert af_utau.decode_oto_bytes(raw) == adapter_decode(raw)

    cases = [
        af_utau.decode_oto_bytes(raw),
        "a.wav=\u3042,20,0,80,0,0\r\nbroken.wav=x,1,2\r\n",
        "no_equals_line\r\n\r\nb.wav=,,,,,\r\n",
        "c.wav=x,nan,inf,-inf,0,0\r\nd.wav=y,1,2,3,4,5,6\r\n",
    ]
    for text in cases:
        mine = af_utau.parse_oto_text(text)
        theirs = adapter_parse(text)
        assert len(mine) == len(theirs)
        for a, b in zip(mine, theirs):
            assert (a.wav_filename, a.alias) == (b.wav_filename, b.alias)
            assert repr(a.offset_ms) == repr(b.offset_ms)
            assert repr(a.consonant_ms) == repr(b.consonant_ms)
            assert repr(a.blank_ms) == repr(b.blank_ms)
            assert repr(a.preutterance_ms) == repr(b.preutterance_ms)
            assert repr(a.overlap_ms) == repr(b.overlap_ms)


# ---------------------------------------------------------------------------
# PR #301 Codex レビュー第 2/3 巡（P1 x6 + P2 x1）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_alias_inventory_must_match_the_frozen_set():
    """部分集合の inventory を G1 で弾く（縮小 Body が旧 bundle を置換しうる）。"""
    spec = _spec()
    spec["inventory"]["aliases"] = spec["inventory"]["aliases"][:10]
    errs = af_schema.validate_founder_spec(spec)
    assert any("frozen AF-P0 inventory" in e for e in errs)

    spec = _spec()
    spec["inventory"]["aliases"] = list(reversed(spec["inventory"]["aliases"]))
    assert any("frozen AF-P0 inventory" in e for e in af_schema.validate_founder_spec(spec))

    spec = _spec()
    spec["inventory"]["aliases"][0] = "ん"
    assert any("frozen AF-P0 inventory" in e for e in af_schema.validate_founder_spec(spec))


def test_validate_body_expects_the_frozen_unit_count(genome, body, tmp_path):
    """期待数は凍結インベントリ由来（縮小 genome に合わせて緩まない）。"""
    detail = af_utau.validate_body(genome, body)
    assert detail["expected_units"] == len(af_schema.FROZEN_ALIASES) == 25
    assert detail["verdict"] == "PASS"

    root = tmp_path / "AF0"
    shutil.copytree(body, root)
    oto = root / genome.pitch_dir / "oto.ini"
    text = oto.read_bytes().decode(af_utau.OTO_ENCODING)
    kept = [ln for ln in text.splitlines() if not ln.startswith("ro.wav=")]
    oto.write_bytes(("\r\n".join(kept) + "\r\n").encode(af_utau.OTO_ENCODING))
    (root / genome.pitch_dir / "ro.wav").unlink()
    trimmed = af_utau.validate_body(genome, root)
    assert trimmed["n_entries"] == 24 and trimmed["verdict"] == "FAIL"


def test_meter_control_requires_every_family():
    """部分集合の controls で G5 PASS を作れない。"""
    full = [_control_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    assert af_gates.gate_meter_control({"families": full}).verdict == "PASS"

    partial = af_gates.gate_meter_control({"families": full[:1]})
    assert partial.verdict == "FAIL"
    assert partial.detail["missing_families"] == list(af_gates.REQUIRED_CONTROL_FAMILIES[1:])
    assert partial.detail["reason_code"] == "METER_NOT_CALIBRATED"

    unknown = af_gates.gate_meter_control(
        {"families": full + [{"family": "made_up", "metric": "x", "patch_paths": [],
                              "verdict": "PASS"}]})
    assert unknown.verdict == "FAIL" and unknown.detail["unknown_families"] == ["made_up"]

    dup = af_gates.gate_meter_control({"families": full + [full[0]]})
    assert dup.verdict == "FAIL" and dup.detail["duplicated_families"] == [full[0]["family"]]


@pytest.mark.parametrize(("key", "gate_id"),
                         [("energy_attack", "G11"), ("terminal_zero", "G13")])
def test_body_only_comparisons_are_gated(key, gate_id):
    """§18.1 にしか行が無い比較も Gate へ配線されている。"""
    body = _all_pass_body()
    body[key] = {"verdict": "FAIL"}
    gates = {g.gate_id: g for g in af_gates.trait_gates(body, _all_pass_reexp())}
    assert gates[gate_id].verdict == "FAIL"
    assert gates[gate_id].detail["body"][key] == "FAIL"
    v = af_gates.overall_verdict(_gates(body, _all_pass_reexp()))
    assert v["verdict"] == "NOT_ESTABLISHED"


def test_provenance_paths_are_checkout_relative(tmp_path):
    """絶対パスを provenance へ書かない（checkout ごとにバイト列が変わる）。"""
    assert af_gates.repo_relative(_PKG / "af_gates.py") == \
        "voice_genesis/foundry/artificial_founder/af_gates.py"
    external = af_gates.repo_relative(tmp_path / "x" / "y.txt")
    assert external.startswith("<external>/") and str(tmp_path) not in external

    audit = af_gates.SourceFreeAudit(allowed_roots=[_PKG], staging_roots=[tmp_path],
                                     pinned_inputs=[SPEC_PATH], runtime_roots=[])
    audit.record(tmp_path / "speaker.npy")
    payload = json.dumps(audit.as_dict(), ensure_ascii=False)
    assert str(_PKG) not in payload
    assert str(tmp_path) not in payload
    assert "voice_genesis/foundry/artificial_founder" in payload


def test_reject_output_collision_guards_destructive_out(tmp_path):
    """`--out` が入力・パッケージ・リポジトリを消す構成を削除前に拒否する。"""
    import p0_run

    protected = [SPEC_PATH, CRITERIA_PATH, CONTROLS_PATH, PROBES_PATH, _PKG, _PKG.parents[2]]
    # 既定の出力先（パッケージ配下だが何も内包しない）は通る
    p0_run.reject_output_collision(_PKG / "results" / "AF0", protected)
    p0_run.reject_output_collision(tmp_path / "anywhere", protected)
    for bad in (SPEC_PATH.parent, _PKG, _PKG.parents[2], _PKG / "criteria"):
        with pytest.raises(p0_run.OutputCollisionError):
            p0_run.reject_output_collision(bad, protected)


@pytest.mark.slow
def test_t32b_cross_process_covers_dataset(genome, tmp_path):
    """§19 G2 の対象は Body だけでなく dataset も含む。"""
    import p0_run

    parent = p0_run.compile_artifacts(genome, tmp_path / "parent" / "AF0")
    assert parent["dataset_digest"] and parent["dataset_n_files"] > 0
    proc = subprocess.run(
        [sys.executable, str(_PKG / "p0_run.py"), "--compile-only", "--spec", str(SPEC_PATH),
         "--out", str(tmp_path / "child" / "AF0")],
        capture_output=True, text=True, cwd=str(_PKG))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["digest"] == parent["digest"]
    assert payload["dataset_digest"] == parent["dataset_digest"]

    # 親側の配管（`cross_process_digest`）も通す。ここでキーを詰め替えていると
    # dataset のダイジェストが黙って落ちて G2 が判定できなくなる。
    relayed = p0_run.cross_process_digest(SPEC_PATH, tmp_path / "relayed" / "AF0")
    assert relayed["digest"] == parent["digest"]
    assert relayed["dataset_digest"] == parent["dataset_digest"]


def test_committed_results_pin_the_current_code_closure():
    """コミット済み結果の `code_closure_sha256` が作業ツリーと一致する。

    コードを直してから canonical run を回し直さないと、結果が「レビュー中の実装
    とは別のコード」に帰属したまま G14 PASS として残る。ここで固定して、
    再実行漏れを CI の赤で検出する。
    """
    results_dir = _PKG / "results" / "AF0"
    closure = results_dir / "code_closure.json"
    if not closure.exists():
        pytest.skip("no committed canonical run in this checkout")
    recorded = json.loads(closure.read_text(encoding="utf-8"))
    current, rows = af_gates.code_closure_digest(_PKG)
    assert recorded["digest"] == current, (
        "results/AF0 was produced by a different code closure; re-run p0_run.py "
        "after changing any pinned module")
    pins = json.loads((results_dir / "input_pins.json").read_text(encoding="utf-8"))
    assert pins["code_closure_sha256"] == current
    assert pins["spec_sha256"] == af_spec.load_genome(SPEC_PATH).sha256
    assert len(rows) == len(recorded["files"])
    # provenance へ絶対パスを残さない（checkout ごとにバイト列が変わる）。
    root = str(af_gates.repo_root())
    for name in ("p0_results.json", "source_free_attestation.json", "comparison.json",
                 "measurements/ingestion.json"):
        text = (results_dir / name).read_text(encoding="utf-8")
        assert root not in text, f"{name} pins an absolute checkout path"


# ---------------------------------------------------------------------------
# PR #301 Codex レビュー第 4 巡（P1 x3 + P2 x1）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_meter_control_binds_family_to_its_metric_contract():
    """family ラベルだけでなく「何を動かして何を測ったか」を検査する。"""
    _row = _control_row
    full = [_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    assert af_gates.gate_meter_control({"families": full}).verdict == "PASS"

    # afterglow と名乗りながら energy を測る行（8 行そろっても PASS にしない）
    mislabelled = [_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    mislabelled[-1] = _row("afterglow", metric="sustain_dbfs",
                           low_patch={"performance_genes.energy.sustain_dbfs": -18.0},
                           high_patch={"performance_genes.energy.sustain_dbfs": -6.0})
    result = af_gates.gate_meter_control({"families": mislabelled})
    assert result.verdict == "FAIL"
    violation = result.detail["contract_violations"][0]
    assert violation["family"] == "afterglow"
    assert violation["observed"]["metric"] == "sustain_dbfs"
    assert {"metric", "low", "high"} <= set(violation["mismatched_fields"])

    # metric は正しいが patch する genome パスが違う行も拒否する
    wrong_patch = [_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    wrong_patch[0] = _row("hl_even_odd_ratio",
                          low_patch={"performance_genes.energy.sustain_dbfs": -18.0},
                          high_patch={"performance_genes.energy.sustain_dbfs": -6.0})
    assert af_gates.gate_meter_control({"families": wrong_patch}).verdict == "FAIL"


def test_shipped_controls_satisfy_the_frozen_contract():
    """同梱 controls が凍結契約どおりの family / metric / patch パスを持つ。"""
    controls, _ = af_spec.load_controls(CONTROLS_PATH)
    got = {c["family"]: (c["metric"],
                         tuple(sorted(set(c["low"]["patch"]) | set(c["high"]["patch"]))))
           for c in controls["controls"]}
    assert set(got) == set(af_gates.REQUIRED_CONTROL_FAMILIES)
    for family, want in af_gates.REQUIRED_CONTROL_CONTRACT.items():
        want_paths = tuple(sorted(set(want["low"]) | set(want["high"])))
        assert got[family] == (want["metric"], want_paths)


def test_g13_covers_terminal_fall():
    """§19 G13 は Afterglow + terminal fall の複合形質。"""
    body = _all_pass_body()
    body["terminal_f0"] = {"verdict": "FAIL"}
    gates = {g.gate_id: g for g in af_gates.trait_gates(body, _all_pass_reexp())}
    assert gates["G13"].verdict == "FAIL"
    assert gates["G9"].verdict == "FAIL"
    reexp = _all_pass_reexp()
    reexp["terminal_f0"] = {"verdict": "FAIL"}
    gates = {g.gate_id: g for g in af_gates.trait_gates(_all_pass_body(), reexp)}
    assert gates["G13"].verdict == "FAIL"


def test_publication_rolls_back_when_post_publish_verification_fails(tmp_path):
    """公開後検証が落ちたら旧 valid bundle を復元する（未検証 bundle を残さない）。"""
    published = tmp_path / "published"
    first = tmp_path / "stage1" / "AF0"
    first.mkdir(parents=True)
    (first / "marker.txt").write_text("v1", encoding="utf-8")
    af_utau.publish_atomically(first, published)
    assert (published / "marker.txt").read_text(encoding="utf-8") == "v1"

    second = tmp_path / "stage2" / "AF0"
    second.mkdir(parents=True)
    (second / "marker.txt").write_text("v2", encoding="utf-8")
    pub = af_utau.publish_atomically(second, published, keep_rollback=True)
    assert pub["rollback_path"] and Path(pub["rollback_path"]).exists()
    assert (published / "marker.txt").read_text(encoding="utf-8") == "v2"

    # 検証失敗 -> 旧世代へ戻す
    assert af_utau.rollback_publication(published, pub["rollback_path"]) is True
    assert (published / "marker.txt").read_text(encoding="utf-8") == "v1"
    assert not Path(pub["rollback_path"]).exists()


def test_publication_commit_drops_the_snapshot(tmp_path):
    published = tmp_path / "published"
    first = tmp_path / "s1" / "AF0"
    first.mkdir(parents=True)
    (first / "m.txt").write_text("v1", encoding="utf-8")
    af_utau.publish_atomically(first, published)
    second = tmp_path / "s2" / "AF0"
    second.mkdir(parents=True)
    (second / "m.txt").write_text("v2", encoding="utf-8")
    pub = af_utau.publish_atomically(second, published, keep_rollback=True)
    assert af_utau.commit_publication(pub["rollback_path"]) is True
    assert not Path(pub["rollback_path"]).exists()
    assert (published / "m.txt").read_text(encoding="utf-8") == "v2"


def test_failed_run_restores_the_previous_result_tree(tmp_path, monkeypatch):
    """run が途中で落ちても直前の成果物ツリーを失わない。"""
    import p0_run

    out = tmp_path / "AF0"
    (out / "measurements").mkdir(parents=True)
    (out / "AF_P0_RECORD.md").write_text("previous record", encoding="utf-8")
    (out / "measurements" / "body.json").write_text("{}", encoding="utf-8")
    (out / "voicebank" / "AF0").mkdir(parents=True)
    (out / "voicebank" / "AF0" / "marker.txt").write_text("v1", encoding="utf-8")

    boom = RuntimeError("simulated mid-run failure")

    def _explode(*args, **kwargs):
        raise boom

    monkeypatch.setattr(p0_run.af_controls, "run_controls", _explode)
    with pytest.raises(RuntimeError):
        p0_run.run(SPEC_PATH, CRITERIA_PATH, CONTROLS_PATH, PROBES_PATH, out)

    assert (out / "AF_P0_RECORD.md").read_text(encoding="utf-8") == "previous record"
    assert (out / "measurements" / "body.json").exists()
    assert (out / "voicebank" / "AF0" / "marker.txt").read_text(encoding="utf-8") == "v1"
    assert not (out.parent / f".{out.name}.previous").exists()


# ---------------------------------------------------------------------------
# PR #301 Codex レビュー第 5 巡（P1 x3）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_output_guard_and_snapshot_share_one_path_definition(tmp_path):
    """削除ガードと実装が同じ派生パス定義を使う（名前がずれると保護が外れる）。"""
    import p0_run

    out = tmp_path / "AF0"
    snapshot = p0_run.output_snapshot_path(out)
    assert snapshot == tmp_path / ".AF0.previous"

    # `--spec` がスナップショット位置にある構成は削除前に拒否される。
    with pytest.raises(p0_run.OutputCollisionError):
        p0_run.reject_output_collision(out, [snapshot])

    # 実装が実際に作るスナップショットもこの位置。
    out.mkdir(parents=True)
    (out / "marker.txt").write_text("v1", encoding="utf-8")
    detail = p0_run.prepare_output_tree(out)
    assert detail["snapshot"] == str(snapshot)
    assert snapshot.exists()
    p0_run.discard_output_snapshot(detail["snapshot"])


def test_withdraw_publication_removes_unverified_first_generation(tmp_path):
    """初回公開で検証が落ちたら、戻す先が無くても未検証 bundle を残さない。"""
    published = tmp_path / "published"
    staging = tmp_path / "stage" / "AF0"
    staging.mkdir(parents=True)
    (staging / "marker.txt").write_text("v1", encoding="utf-8")
    pub = af_utau.publish_atomically(staging, published, keep_rollback=True)
    assert pub["rollback_path"] is None  # 初回なので退避世代は無い
    assert published.exists()

    outcome = af_utau.withdraw_publication(published, pub.get("rollback_path"))
    assert outcome["rolled_back"] is False
    assert outcome["removed_unverified"] is True
    assert not published.exists(), "unverified bundle must not stay at the canonical path"

    # 何も公開されていない状態での取り下げは no-op（例外にしない）。
    again = af_utau.withdraw_publication(published, None)
    assert again["removed_unverified"] is False


def test_withdraw_publication_prefers_restoring_the_previous_generation(tmp_path):
    published = tmp_path / "published"
    first = tmp_path / "s1" / "AF0"
    first.mkdir(parents=True)
    (first / "m.txt").write_text("v1", encoding="utf-8")
    af_utau.publish_atomically(first, published)
    second = tmp_path / "s2" / "AF0"
    second.mkdir(parents=True)
    (second / "m.txt").write_text("v2", encoding="utf-8")
    pub = af_utau.publish_atomically(second, published, keep_rollback=True)
    outcome = af_utau.withdraw_publication(published, pub["rollback_path"])
    assert outcome["rolled_back"] is True and outcome["removed_unverified"] is False
    assert (published / "m.txt").read_text(encoding="utf-8") == "v1"


def test_meter_control_freezes_values_and_separation():
    """low = high や min_separation = 0 の controls で G5 PASS を作れない。"""
    full = [_control_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    assert af_gates.gate_meter_control({"families": full}).verdict == "PASS"

    degenerate = [_control_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    want = af_gates.REQUIRED_CONTROL_CONTRACT["afterglow"]
    degenerate[-1] = _control_row("afterglow", low_patch=dict(want["high"]),
                                  min_separation=0.0)
    result = af_gates.gate_meter_control({"families": degenerate})
    assert result.verdict == "FAIL"
    violation = result.detail["contract_violations"][0]
    assert violation["family"] == "afterglow"
    assert set(violation["mismatched_fields"]) == {"low", "min_separation"}

    # 別 unit で測った行も拒否する
    wrong_unit = [_control_row(f) for f in af_gates.REQUIRED_CONTROL_FAMILIES]
    wrong_unit[0] = _control_row("hl_even_odd_ratio", unit_alias="ro")
    assert af_gates.gate_meter_control({"families": wrong_unit}).verdict == "FAIL"


def test_shipped_controls_match_the_frozen_values():
    """同梱 controls の unit / 値 / 分離幅が §17 の凍結契約と一致する。"""
    controls, _ = af_spec.load_controls(CONTROLS_PATH)
    for c in controls["controls"]:
        want = af_gates.REQUIRED_CONTROL_CONTRACT[c["family"]]
        assert c["unit_alias"] == want["unit_alias"]
        assert c["metric"] == want["metric"]
        assert float(c["min_separation"]) == want["min_separation"]
        assert c["low"]["patch"] == want["low"]
        assert c["high"]["patch"] == want["high"]
