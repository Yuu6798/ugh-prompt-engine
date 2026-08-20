"""test_planb_real.py — Real-Corpus PoC ハーネスの検証。

**実コーパスは本環境に無い**ため、実コーパスと同じ**構造**（wav + HTS 形式 lab、
100ns 時間単位、probe になる音素列）を持つ極小の合成コーパスを作り、
取得 → census → ラダー → ゲートの鎖を通す。

これで検証できるのは配管（構造・停止規則・軸の動き方）であって、
Ritsu / PJS の実際の音ではない。実素材での判定は G-MATERIAL 以降で行う。

実行:
    python -m pytest voice_genesis/foundry/planb_real/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent.parent
for _p in (_HERE, _HERE.parent / "planb"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("pyworld")
import soundfile as sf  # noqa: E402

import pr_census  # noqa: E402
import pr_gates  # noqa: E402
import pr_identity  # noqa: E402
import pr_lab  # noqa: E402
import pr_ladder  # noqa: E402
import pr_manifest as prm  # noqa: E402
import pr_performance as prp  # noqa: E402
import pr_status as prs  # noqa: E402

SR = 22050

# 音素 -> フォルマント（Hz）。話者差は倍率で作る。
_FORMANTS = {
    "a": (760, 1200, 2500), "i": (300, 2300, 3000), "u": (350, 1250, 2200),
    "e": (500, 1900, 2600), "o": (500, 900, 2400), "N": (280, 1000, 2200),
    "k": (500, 1600, 2500), "g": (450, 1500, 2400), "r": (400, 1400, 2300),
    "m": (300, 1100, 2200), "n": (320, 1300, 2300), "w": (350, 900, 2200),
    "t": (450, 1700, 2600), "s": (600, 2000, 3200), "h": (500, 1500, 2500),
}


def _tone(f0: float, dur_s: float, sr: int, formants, *, unvoiced: bool = False,
          seed: int = 0) -> np.ndarray:
    n = max(1, int(round(dur_s * sr)))
    t = np.arange(n) / sr
    env = np.ones(n)
    fade = max(1, int(0.005 * sr))
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    if unvoiced:
        rng = np.random.default_rng(seed)
        x = rng.standard_normal(n) * 0.05
    else:
        x = np.zeros(n)
        for k in range(1, int(sr / 2 / max(f0, 1.0))):
            fk = f0 * k
            gain = 0.0
            for fc in formants:
                gain += 1.0 / (1.0 + ((fk - fc) / 120.0) ** 2)
            x += (gain / k) * np.sin(2 * np.pi * fk * t)
        x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
    return x * env


def _write_utterance(path: Path, phones, *, sr: int = SR, f0: float = 220.0,
                     formant_scale: float = 1.0, seed: int = 0) -> None:
    """phones = [(label, duration_s), ...] を wav + HTS lab（100ns）で書き出す。"""
    chunks, rows, t = [], [], 0.0
    for i, (label, dur) in enumerate(phones):
        if label in pr_lab.SILENCE_PHONES:
            chunks.append(np.zeros(int(dur * sr)))
        else:
            fm = tuple(f * formant_scale for f in _FORMANTS.get(label, (500, 1500, 2500)))
            chunks.append(_tone(f0, dur, sr, fm, unvoiced=label in ("s", "h"), seed=seed + i))
        rows.append((int(round(t * 1e7)), int(round((t + dur) * 1e7)), label))
        t += dur
    sf.write(path.with_suffix(".wav"), np.concatenate(chunks).astype(np.float32), sr)
    path.with_suffix(".lab").write_text(
        "\n".join(f"{a} {b} {c}" for a, b, c in rows) + "\n", encoding="utf-8")


# 「かぎり」相当（terminal /ri/）/「みわたす」相当（terminal /su/）/「ほん」（terminal /N/）
_KAGIRI = [("sil", 0.15), ("k", 0.06), ("a", 0.30), ("g", 0.05), ("i", 0.28),
           ("r", 0.05), ("i", 0.70), ("sil", 0.25)]
_MIWATASU = [("sil", 0.15), ("m", 0.06), ("i", 0.26), ("w", 0.05), ("a", 0.24),
             ("t", 0.05), ("a", 0.24), ("s", 0.08), ("u", 0.55), ("sil", 0.25)]
_HON = [("sil", 0.12), ("h", 0.06), ("o", 0.30), ("N", 0.45), ("sil", 0.20)]
# 語中 /ri/ を含む発話
_RIKA = [("sil", 0.12), ("r", 0.05), ("i", 0.25), ("k", 0.05), ("a", 0.40), ("sil", 0.20)]


_VOWELS = ("a", "i", "u", "e", "o", "N")


def _scale(phones, factor: float, consonant_factor: float = 1.0):
    """全体テンポ (factor) と、子音だけの伸縮 (consonant_factor) を別に掛ける。

    子音/母音の比が変わらない「一様な伸縮」では §6 の share 転写が no-op に
    なるため、歌い方の差を作るには consonant_factor を変える必要がある。
    """
    out = []
    for lab, dur in phones:
        f = factor * (consonant_factor if lab not in _VOWELS
                      and lab not in pr_lab.SILENCE_PHONES else 1.0)
        out.append((lab, dur * f))
    return out


def build_corpus(root: Path, *, f0: float, formant_scale: float, stretch: float,
                 seed: int, consonant_factor: float = 1.0) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, seq in (("kagiri", _KAGIRI), ("miwatasu", _MIWATASU),
                      ("hon", _HON), ("rika", _RIKA)):
        _write_utterance(root / name, _scale(seq, stretch, consonant_factor), f0=f0,
                         formant_scale=formant_scale, seed=seed)
    return root


@pytest.fixture(scope="module")
def corpora(tmp_path_factory):
    base = tmp_path_factory.mktemp("corpora")
    ritsu = build_corpus(base / "ritsu", f0=246.0, formant_scale=1.00, stretch=1.0, seed=11)
    pjs = build_corpus(base / "pjs", f0=196.0, formant_scale=1.18, stretch=1.35,
                       seed=77, consonant_factor=2.6)
    return ritsu, pjs


# ---------------------------------------------------------------------------
# §1 / §2 資材・許諾ゲート
# ---------------------------------------------------------------------------
def test_material_gate_blocks_without_manifest():
    res = prm.gate_material(prm.SourceManifest(), Path("/nonexistent"))
    assert res.status == prs.BLOCKED
    assert res.next_action


def test_material_gate_rejects_material_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    archive = repo / "corpus.zip"
    archive.write_bytes(b"x" * 32)
    lic = repo / "terms.txt"
    lic.write_text("terms")
    extracted = repo / "ex"
    extracted.mkdir()
    (extracted / "a.wav").write_bytes(b"y")
    m = prm.SourceManifest()
    for src in prm.REQUIRED_SOURCES:
        m.add(prm.build_source_entry(source=src, version="v1", download_origin="http://x",
                                     archive_path=archive, license_document_path=lic,
                                     extracted_path=extracted))
    res = prm.gate_material(m, repo)
    assert res.status == prs.BLOCKED
    assert "非収載違反" in res.detail


def test_material_gate_passes_outside_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    mat = tmp_path / "material"
    mat.mkdir()
    archive = mat / "corpus.zip"
    archive.write_bytes(b"x" * 32)
    lic = mat / "terms.txt"
    lic.write_text("terms")
    extracted = mat / "ex"
    extracted.mkdir()
    (extracted / "a.wav").write_bytes(b"y")
    m = prm.SourceManifest()
    for src in prm.REQUIRED_SOURCES:
        m.add(prm.build_source_entry(source=src, version="v1", download_origin="http://x",
                                     archive_path=archive, license_document_path=lic,
                                     extracted_path=extracted))
    assert prm.gate_material(m, repo).status == prs.PASS


def test_license_gate_is_blocked_until_answered():
    led = prm.blank_ledger()
    assert prm.gate_license(led).status == prs.BLOCKED
    # answered_by と逐語抜粋が無ければ、チェックリストが全て True でも通らない
    for subject, keys in (("ritsu_singing_db", prm.RITSU_CHECKLIST),
                          ("pjs_corpus", prm.PJS_CHECKLIST)):
        r = led.records[subject]
        r.canonical_document, r.document_sha256, r.license_name = "d", "0" * 64, "t"
        r.checklist = {k: True for k in keys}
    assert prm.gate_license(led).status == prs.BLOCKED
    for subject, keys in (("ritsu_singing_db", prm.RITSU_CHECKLIST),
                          ("pjs_corpus", prm.PJS_CHECKLIST)):
        rec = led.records[subject]
        rec.canonical_document = "bundled_terms.txt"
        rec.document_sha256 = "0" * 64
        rec.license_name = "test"
        rec.answered_by = "tester"
        rec.verbatim_excerpts = ["excerpt"]
        rec.checklist = {k: True for k in keys}
    assert prm.gate_license(led).status == prs.PASS


def test_license_gate_fails_closed_on_negative_answer():
    led = prm.blank_ledger()
    for subject, keys in (("ritsu_singing_db", prm.RITSU_CHECKLIST),
                          ("pjs_corpus", prm.PJS_CHECKLIST)):
        rec = led.records[subject]
        rec.canonical_document = "d"
        rec.document_sha256 = "0" * 64
        rec.license_name = "test"
        rec.answered_by = "tester"
        rec.verbatim_excerpts = ["excerpt"]
        rec.checklist = {k: True for k in keys}
    led.records["ritsu_singing_db"].checklist["raw_db_redistribution_avoided"] = False
    assert prm.gate_license(led).status == prs.BLOCKED


# ---------------------------------------------------------------------------
# §3 census
# ---------------------------------------------------------------------------
def test_lab_time_unit_detection_and_probes(corpora):
    ritsu, _pjs = corpora
    lab = pr_lab.read_lab(ritsu / "kagiri.lab")
    assert lab.time_unit == "100ns"
    assert lab.style == "mono"
    assert lab.total_s == pytest.approx(1.84, abs=0.02)
    finals = pr_lab.phrase_final_indices(lab.phones)
    assert lab.phones[finals[-1]].label == "i"


def test_census_counts_probes_on_both_sides(corpora):
    ritsu, pjs = corpora
    inv_r = pr_census.census(ritsu, "ritsu_singing_db")
    inv_p = pr_census.census(pjs, "pjs_corpus")
    for inv in (inv_r, inv_p):
        counts = inv.probe_counts()
        assert counts["terminal_ri"] >= 1
        assert counts["terminal_su"] >= 1
        assert counts["terminal_N"] >= 1
        assert counts["medial_ri"] >= 1
        assert inv.paired_count == 4
        assert inv.structure_warnings == []
    cov = pr_census.coverage_comparison(inv_r, inv_p)
    assert cov["usable_for_ladder"]["terminal_ri"] is True
    assert pr_census.gate_census(inv_r, inv_p, cov).status == prs.PASS


def test_census_gate_blocks_when_primary_probe_absent(corpora, tmp_path):
    ritsu, _pjs = corpora
    empty = tmp_path / "no_ri"
    empty.mkdir()
    _write_utterance(empty / "hon", _HON)
    inv_r = pr_census.census(ritsu, "ritsu")
    inv_p = pr_census.census(empty, "pjs")
    cov = pr_census.coverage_comparison(inv_r, inv_p)
    res = pr_census.gate_census(inv_r, inv_p, cov)
    assert res.status == prs.BLOCKED
    assert "primary probe" in res.detail


def test_moraic_nasal_is_not_confused_with_onset_n():
    assert pr_lab.normalize_vowel("N") == "N"
    assert pr_lab.normalize_vowel("n") is None


# ---------------------------------------------------------------------------
# §4 Performance 表現
# ---------------------------------------------------------------------------
def _build_pair(ritsu: Path, pjs: Path, name: str = "kagiri"):
    r_lab = pr_lab.read_lab(ritsu / f"{name}.lab")
    p_lab = pr_lab.read_lab(pjs / f"{name}.lab")
    r_idx = pr_lab.phrase_final_indices(r_lab.phones)[-1]
    p_idx = pr_lab.phrase_final_indices(p_lab.phones)[-1]
    bank, probe_pos = pr_identity.build_identity_for_probe(
        ritsu / f"{name}.wav", r_lab, r_idx, source_id="ritsu")
    analysis, power_db, offset = pr_identity.analyze_for_performance(pjs / f"{name}.wav")
    phones = pr_identity.shift_phones(p_lab.phones, offset)
    perf = prp.extract_performance(
        f0=analysis.f0, power_db=power_db, frame_period_ms=analysis.frame_period_ms,
        phones=phones, vowel_index=p_idx, source_id="pjs",
        source_file=str(pjs / f"{name}.lab"), probe_kind="terminal_ri")
    donor_core = pr_ladder.donor_core_envelope(
        analysis, phones, p_idx, target_sr=bank.sr,
        target_bins=bank.sp[probe_pos].shape[1])
    return bank, probe_pos, perf, donor_core


def test_performance_track_has_all_required_field_groups(corpora):
    inv = prp.performance_field_inventory()
    assert {"note_duration_s", "phoneme_duration_s", "consonant_duration_s",
            "vowel_duration_s"} <= set(inv["timing"])
    assert {"f0_dev_cents", "onset_glide_cents", "vibrato_rate_hz", "vibrato_depth_cents",
            "terminal_pitch_motion_cents"} <= set(inv["pitch"])
    assert {"energy_db", "attack_db_per_s", "sustain_db", "terminal_decay_db"} <= set(
        inv["dynamics"])
    assert {"voiced_tail_s", "terminal_decay_shape_db", "release_to_silence_s",
            "terminal_f0_persistence"} <= set(inv["release"])


def test_performance_track_rejects_spectral_payload(corpora):
    ritsu, pjs = corpora
    _bank, _pos, perf, _core = _build_pair(ritsu, pjs)
    prp.assert_no_spectral_payload(perf)
    import dataclasses
    bad = dataclasses.replace(perf, pitch=dataclasses.replace(
        perf.pitch, f0_dev_cents=np.zeros((3, 3))))
    with pytest.raises(ValueError):
        prp.assert_no_spectral_payload(bad)


def test_performance_shares_sum_to_one(corpora):
    ritsu, pjs = corpora
    _bank, _pos, perf, _core = _build_pair(ritsu, pjs)
    assert perf.timing.consonant_share + perf.timing.vowel_share == pytest.approx(1.0)
    assert 0.0 < perf.timing.consonant_share < 0.5


# ---------------------------------------------------------------------------
# §6 正規化転写
# ---------------------------------------------------------------------------
def test_transplant_preserves_ritsu_note_total(corpora):
    import pr_match
    ritsu, pjs = corpora
    bank, pos, perf, _core = _build_pair(ritsu, pjs)
    rep = pr_match.matching_report(bank, pos, perf)
    assert sum(rep["transplanted_split_s"]) == pytest.approx(rep["ritsu_note_total_s"], abs=1e-6)
    assert rep["transplanted_split_s"] != rep["ritsu_native_split_s"]
    assert rep["timing_transplant_is_noop"] is False
    assert rep["absolute_hz_copied"] is False
    assert rep["absolute_seconds_copied"] is False


def test_compose_track_only_touches_probe_note(corpora):
    import pr_match
    ritsu, pjs = corpora
    bank, pos, perf, _core = _build_pair(ritsu, pjs)
    track = pr_match.build_compose_track(bank, pos, perf)
    lo, hi = pr_match.note_unit_span(bank, pos)
    for i in range(bank.n_units):
        mask = track.f0_dev_unit_index == i
        if lo <= i <= hi:
            assert np.any(np.abs(track.f0_dev_cents[mask]) > 0.0)
        else:
            assert np.allclose(track.f0_dev_cents[mask], 0.0)


# ---------------------------------------------------------------------------
# §7 / §10 ラダーとゲート
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pair_result(corpora, tmp_path_factory):
    ritsu, pjs = corpora
    bank, pos, perf, donor_core = _build_pair(ritsu, pjs)
    out = tmp_path_factory.mktemp("ladder")
    return pr_ladder.run_probe_pair(
        pair_key="terminal_ri|kagiri|kagiri", probe_kind="terminal_ri", bank=bank,
        probe_pos=pos, perf=perf, donor_core_sp=donor_core,
        ritsu_file=str(ritsu / "kagiri.lab"), pjs_file=str(pjs / "kagiri.lab"),
        out_dir=out)


def test_ladder_produces_all_rungs(pair_result):
    assert set(pair_result.rungs) == {"R0", "R1", "R2", "R3", "R4"}
    for m in pair_result.rungs.values():
        assert Path(m.wav_path).exists()
        assert len(m.wav_file_sha256) == 64


def test_wav_file_hash_matches_written_bytes(pair_result):
    """record の pin が、実際に書かれたバイト列で検算できること。"""
    for m in pair_result.rungs.values():
        assert pr_ladder.sha256_bytes_of_file(Path(m.wav_path)) == m.wav_file_sha256


def test_rungs_are_distinct(pair_result):
    shas = {m.samples_sha256 for m in pair_result.rungs.values()}
    assert len(shas) == 5


def test_duration_toggle_changes_note_split(pair_result):
    assert pair_result.rungs["R2"].note_split_mae_ms < pair_result.rungs["R0"].note_split_mae_ms
    assert pair_result.rungs["R1"].note_split_mae_ms == pytest.approx(
        pair_result.rungs["R0"].note_split_mae_ms, abs=1e-6)


def test_identity_stays_closer_than_donor(pair_result):
    for m in pair_result.rungs.values():
        assert m.identity_margin_db > 0.0
    assert pr_gates.gate_identity([pair_result]).status == prs.PASS


def test_donor_isolation_gate_passes(pair_result):
    assert pr_gates.gate_donor_texture_isolation([pair_result]).status == prs.PASS


def test_determinism_gate(pair_result):
    res = pr_gates.gate_determinism(
        [pair_result], lambda p: [m.samples_sha256 for m in p.rungs.values()])
    assert res.status == prs.PASS
    bad = pr_gates.gate_determinism([pair_result], lambda p: ["deadbeef"] * 5)
    assert bad.status == prs.FAIL


def test_trf_gate_uses_registered_axis_and_keeps_exploratory_separate(pair_result):
    frozen = {pair_result.pair_key: pr_gates.freeze_trf_gate(pair_result)}
    res = pr_gates.gate_trf([pair_result], frozen)
    assert frozen[pair_result.pair_key]["primary_axis"] == "nasal_gain_db"
    item = res.evidence[pair_result.pair_key]
    assert "nasal_gain_shape_db" in item["exploratory"]
    assert item["exploratory"]["nasal_gain_shape_db"]["status"].startswith(
        "CANDIDATE_SECONDARY_AXIS")
    assert res.status in (prs.PASS, prs.FAIL, prs.NOT_EVALUABLE)


def test_ear_gate_blocked_without_answers(pair_result):
    assert pr_gates.gate_ear(None).status == prs.BLOCKED
    assert pr_gates.gate_ear({"Q1": "yes"}).status == prs.BLOCKED
    ok = {k: {"verdict": "yes"} for k, _q in pr_gates.EAR_QUESTIONS}
    ok["Q4"] = {"verdict": "no"}          # Q4 は「PJS へ寄っただけか」なので no が合格側
    assert pr_gates.gate_ear(ok).status == prs.PASS
    # 回答があっても内容が不合格なら FAIL（「回答あり = 合格」にしない）
    ng = dict(ok)
    ng["Q3"] = {"verdict": "no"}
    res = pr_gates.gate_ear(ng)
    assert res.status == prs.FAIL and "Q3=no" in res.detail


def test_success_level_never_claims_more_than_material(pair_result):
    gates = [prs.GateResult("G-MATERIAL", prs.BLOCKED, "x")]
    assert pr_gates.success_level(gates, [pair_result]) == "S0_NOT_REACHED"


def test_structural_separation_gate(corpora, pair_result):
    import pr_match
    ritsu, pjs = corpora
    bank, pos, perf, _core = _build_pair(ritsu, pjs)
    track = pr_match.build_compose_track(bank, pos, perf)
    res = pr_gates.gate_structural_separation([perf], bank, track)
    assert res.status == prs.PASS
    accessed = set(res.evidence["accessed"])
    assert not any(a.startswith("sp") or a.startswith("ap") for a in accessed)


# ---------------------------------------------------------------------------
# 停止規則
# ---------------------------------------------------------------------------
def test_run_ledger_exit_code_is_nonzero_when_blocked():
    led = prs.RunLedger()
    led.add(prs.GateResult("G-X", prs.BLOCKED, "x"))
    assert led.exit_code() == 1
    ok = prs.RunLedger()
    ok.add(prs.GateResult("G-Y", prs.PASS, "y"))
    assert ok.exit_code() == 0


def test_stop_rule_carries_next_action():
    exc = prs.StopRule("G-Z", "cause", "do this")
    res = exc.as_result()
    assert res.status == prs.BLOCKED and res.next_action == "do this"
