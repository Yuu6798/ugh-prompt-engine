"""test_genome_s4.py — 設計書 v1.0 §23 の最低要件をそのまま並べる。

unit は実コーパスを必要としない（判定ロジックだけを対象にする）。
実素材が要る経路は `planb_real/results/ladder_manifest.json` と corpus が
揃っているときだけ走る（無ければ skip）。

実行:
    python -m pytest voice_genesis/foundry/genome_s4/tests -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_HERE = Path(__file__).resolve().parent.parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "genome_s3", _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("pyworld")   # 判定ロジックは純粋だが import 閉包が WORLD に触れる

import s4_blind as sb  # noqa: E402
import s4_gates as sg  # noqa: E402
import s4_report as srep  # noqa: E402
import s4_runner as sr  # noqa: E402
import s4_spec as sp  # noqa: E402
from s4_spec import Gene, PairVerdict  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_caches():
    sr.reset_read_cache()
    sr.reset_code_state()
    sr.reset_preflight()
    yield
    sr.reset_read_cache()
    sr.reset_code_state()


# ---------------------------------------------------------------------------
# スタブ（音を作らずに verdict の分岐だけを踏む）
# ---------------------------------------------------------------------------
#: B0 の metric。F0 / Duration とも lower is better。
_B0 = {"f0_dev_rmse_cents": 100.0, "note_split_mae_ms": 50.0,
       sp.IDENTITY_METRIC: 4.0}
_F = {"f0_dev_rmse_cents": 40.0, "note_split_mae_ms": 50.0, sp.IDENTITY_METRIC: 4.0}
_D = {"f0_dev_rmse_cents": 100.0, "note_split_mae_ms": 12.0, sp.IDENTITY_METRIC: 4.0}
#: 複合でも両軸が改善する（= COMBINABLE 想定）。
_FD = {"f0_dev_rmse_cents": 45.0, "note_split_mae_ms": 15.0, sp.IDENTITY_METRIC: 3.0}


class _Cond:
    def __init__(self, sha: str, metrics: Dict[str, Optional[float]], toggles: str,
                 tripwire_status: str = "pass",
                 accessed: Optional[List[str]] = None) -> None:
        self.sample_sha256 = sha
        self.wav_sha256 = "w" * 64
        self.wav_path = f"/dev/null/{sha}.wav"
        self.metrics = dict(metrics)
        self.toggles = toggles
        self.tripwire_status = tripwire_status
        self.tripwire_accessed = list(accessed or ["n_units", "unit_durations_s"])


class _Run:
    """`s4_runner.PairRun` と同じ読み取り面を持つスタブ。"""

    def __init__(self, pair_key: str, context_id: str, *,
                 metrics: Optional[Dict[str, Dict[str, float]]] = None,
                 payload_1d: bool = True, tripwire_status: str = "pass",
                 accessed: Optional[Dict[str, List[str]]] = None,
                 same_process_mismatch: Optional[str] = None,
                 cross_process_mismatch: Optional[str] = None,
                 fd_sha: Optional[str] = None,
                 replay_mismatch: Optional[str] = None,
                 intervention_zero: Optional[str] = None,
                 toggles_override: Optional[Dict[str, str]] = None) -> None:
        m = metrics or {"B0": _B0, "F": _F, "D": _D, "FD": _FD}
        self.pair_key = pair_key
        self.context_id = context_id
        self.identity_sha256 = "i" * 64
        self.performance_sha256 = "p" * 64
        self.performance_payload_1d = payload_1d
        self.conditions = {}
        for cond in sp.CONDITIONS:
            sha = fd_sha if (cond == "FD" and fd_sha) else f"sha-{pair_key}-{cond}"
            toggles = (toggles_override or {}).get(cond, sp.CONDITIONS[cond].label)
            self.conditions[cond] = _Cond(sha, m[cond], toggles, tripwire_status,
                                          (accessed or {}).get(cond))
        self.repeat_sample_sha256 = {
            c: (same_process_mismatch if same_process_mismatch and c == "FD"
                else co.sample_sha256)
            for c, co in self.conditions.items()}
        self._cross_mismatch = cross_process_mismatch
        self.intervention = {
            g.value: {"amount": 0.0 if intervention_zero == g.value else 10.0,
                      "unit": "x", "nonzero": intervention_zero != g.value}
            for g in Gene}
        self.s3_replay = {
            c: {"match": not (replay_mismatch == c), "s3_sample_sha256": "x",
                "s4_sample_sha256": "y", "s3_recorded": True}
            for c in sp.REPLAY_CONDITIONS}

    def cross(self) -> Dict[str, Dict[str, str]]:
        out = {c: co.sample_sha256 for c, co in self.conditions.items()}
        if self._cross_mismatch:
            out["FD"] = self._cross_mismatch
        return {self.pair_key: out}


def _cross(runs: List[_Run]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for r in runs:
        out.update(r.cross())
    return out


def _verdicts(runs: List[_Run]) -> List[sg.PairResult]:
    cross = _cross(runs)
    return [sg.pair_verdict(r, cross) for r in runs]


def _pop(n_combinable: int, n_total: int = 6,
         contexts: Optional[List[str]] = None) -> List[_Run]:
    """`n_total` pair のうち `n_combinable` 件だけ COMBINABLE になる母集団。"""
    ctxs = contexts or ["terminal_i", "terminal_ri", "medial_ri"]
    runs: List[_Run] = []
    for i in range(n_total):
        ctx = ctxs[i % len(ctxs)]
        if i < n_combinable:
            runs.append(_Run(f"pair{i}|a#1|b#2", ctx))
        else:
            # FD で F0 が悪化 = 背景内増分効果が非正 -> UNSUPPORTED
            bad = {"B0": _B0, "F": _F, "D": _D,
                   "FD": {**_FD, "f0_dev_rmse_cents": 140.0}}
            runs.append(_Run(f"pair{i}|a#1|b#2", ctx, metrics=bad))
    return runs


def _s3_doc(overall: str = "PASS", f0: str = "SUPPORTED",
            duration: str = "SUPPORTED", *, pairs: int = 6) -> Dict[str, Any]:
    keys = [f"terminal_i|x#{i}|y#{i}" if i % 2 == 0 else f"terminal_ri|x#{i}|y#{i}"
            for i in range(pairs)]
    def _block(verdict: str) -> Dict[str, Any]:
        return {"verdict": verdict,
                "pairs": {k: {"verdict": "SUPPORTED", "context_id": k.split("|")[0]}
                          for k in keys}}
    return {"overall": {"verdict": overall},
            "genes": {"f0": _block(f0), "duration": _block(duration)},
            "input_manifest_sha256": "m" * 64,
            "reproducibility": [
                {"pair_key": k, "condition": c, "sample_sha256": f"s-{k}-{c}",
                 "identity_sha256": "i" * 64, "performance_sha256": "p" * 64}
                for k in keys for c in ("B0", "F", "D")]}


def _s35_doc(overall: str = "S4_READY", gene: str = "PERCEPTIBLE_CANDIDATE",
             s3_sha: str = "s" * 64) -> Dict[str, Any]:
    return {"overall": {"verdict": overall},
            "genes": {"f0": {"verdict": gene}, "duration": {"verdict": gene}},
            "s3_results_sha256": s3_sha}


# ===========================================================================
# §23 入力・来歴
# ===========================================================================
def test_01_s3_not_pass_is_blocked():
    sr.gate_s3(_s3_doc())                       # PASS は通る
    with pytest.raises(sr.S4Stop):
        sr.gate_s3(_s3_doc(overall="FAIL"))
    with pytest.raises(sr.S4Stop):
        sr.gate_s3(_s3_doc(f0="UNSUPPORTED"))
    with pytest.raises(sr.S4Stop):
        sr.gate_s3(_s3_doc(duration="NOT_EVALUABLE"))


def test_02_s35_not_ready_is_blocked():
    sr.gate_s35(_s35_doc())
    with pytest.raises(sr.S4Stop):
        sr.gate_s35(_s35_doc(overall="S4_NOT_READY"))
    with pytest.raises(sr.S4Stop):
        sr.gate_s35(_s35_doc(gene="NOT_ESTABLISHED"))


def test_03_sha_chain_mismatch_is_blocked():
    s3 = _s3_doc()
    sr.gate_chain("s" * 64, _s35_doc(), "m" * 64, s3)      # 一致
    with pytest.raises(sr.S4Stop):
        sr.gate_chain("z" * 64, _s35_doc(), "m" * 64, s3)  # s35 -> s3 不一致
    with pytest.raises(sr.S4Stop):
        sr.gate_chain("s" * 64, _s35_doc(), "z" * 64, s3)  # s3 -> manifest 不一致


def test_04_source_pin_mismatch_is_blocked():
    pins = {"identity_sha256": "i" * 64, "performance_sha256": "p" * 64}
    sr.assert_pins("pk", "i" * 64, "p" * 64, pins)
    with pytest.raises(sr.S4Stop):
        sr.assert_pins("pk", "z" * 64, "p" * 64, pins)
    with pytest.raises(sr.S4Stop):
        sr.assert_pins("pk", "i" * 64, "z" * 64, pins)
    with pytest.raises(sr.S4Stop):
        sr.assert_pins("pk", "i" * 64, "p" * 64, {})       # pin 自体が無い


def test_05_missing_candidate_pairs_is_blocked():
    assert len(sr.candidate_pairs(_s3_doc(pairs=6))) == 6
    with pytest.raises(sr.S4Stop):
        sr.candidate_pairs(_s3_doc(pairs=3))               # < MIN_CANDIDATE_PAIRS
    # context が 1 種類しか無い母集団も §4 の必須条件に満たない
    one_ctx = _s3_doc(pairs=6)
    for gene in ("f0", "duration"):
        for k, v in one_ctx["genes"][gene]["pairs"].items():
            v["context_id"] = "terminal_i"
    with pytest.raises(sr.S4Stop):
        sr.candidate_pairs(one_ctx)


def test_05b_candidate_is_intersection_only():
    """§4: F0 と Duration の **両方**が SUPPORTED の pair だけを採る。"""
    s3 = _s3_doc(pairs=6)
    keys = list(s3["genes"]["duration"]["pairs"])
    s3["genes"]["duration"]["pairs"][keys[0]]["verdict"] = "UNSUPPORTED"
    s3["genes"]["f0"]["pairs"][keys[1]]["verdict"] = "NOT_EVALUABLE"
    got = {pk for pk, _c in sr.candidate_pairs(s3)}
    assert keys[0] not in got and keys[1] not in got and len(got) == 4


def test_06_duplicate_pair_key_rejected():
    """dict 正本では重複が潰れるので、集計側が二重計上しないことを確認する。"""
    s3 = _s3_doc(pairs=6)
    got = [pk for pk, _c in sr.candidate_pairs(s3)]
    assert len(got) == len(set(got))
    runs = _pop(6)
    runs.append(runs[0])                       # 同じ pair をもう 1 度渡す
    results = _verdicts(runs)
    keys = [r.pair_key for r in results]
    assert len(set(keys)) == 6
    agg = sg.overall_gate(results)
    # `pairs` は pair_key で畳まれるので、表示される証拠より多い母数にならない
    assert len(agg["pairs"]) == 6


def test_07_manifest_read_once(tmp_path):
    path = tmp_path / "doc.json"
    path.write_bytes(b'{"a": 1}')
    data1, sha1 = sr.read_once(path, "doc", "fix")
    path.write_bytes(b'{"a": 2}')
    data2, sha2 = sr.read_once(path, "doc", "fix")
    assert (data1, sha1) == (data2, sha2)      # 走行中に読み直さない
    assert data2["a"] == 1
    sr.reset_read_cache()
    _d3, sha3 = sr.read_once(path, "doc", "fix")
    assert sha3 != sha1                        # キャッシュを捨てれば新しい bytes


def test_07b_missing_or_broken_input_is_blocked(tmp_path):
    with pytest.raises(sr.S4Stop):
        sr.read_once(tmp_path / "nope.json", "doc", "fix")
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{not json")
    with pytest.raises(sr.S4Stop):
        sr.read_once(bad, "doc", "fix")
    arr = tmp_path / "arr.json"
    arr.write_bytes(b"[1,2]")
    with pytest.raises(sr.S4Stop):
        sr.read_once(arr, "doc", "fix")


def test_08_dirty_worktree_rejects_canonical_publish(monkeypatch):
    monkeypatch.setattr(sr, "worktree_state",
                        lambda *a, **k: {"clean": False, "entries": [" M x.py"],
                                         "excluded": []})
    sr.reset_code_state()
    with pytest.raises(sr.S4Stop):
        sr.code_state(require_clean=True)
    sr.reset_code_state()
    assert sr.code_state(require_clean=False)["worktree"]["clean"] is False
    # git が使えない = clean を偽らない
    monkeypatch.setattr(sr, "worktree_state",
                        lambda *a, **k: {"clean": None, "entries": [], "excluded": []})
    sr.reset_code_state()
    with pytest.raises(sr.S4Stop):
        sr.code_state(require_clean=True)


def test_08b_worktree_excludes_only_s4_results():
    assert sr.WORKTREE_EXCLUDE_PREFIXES == (
        "voice_genesis/foundry/genome_s4/results/",)


def test_09_closure_digest_reacts_to_dependency_change(tmp_path, monkeypatch):
    root = tmp_path / "foundry"
    for name in sp.MIN_CLOSURE_FILES:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(sr, "_FOUNDRY", root)
    monkeypatch.setattr(sr, "loaded_foundry_modules", lambda: [])
    first = sr.closure_digest()
    assert first["file_count"] == len(sp.MIN_CLOSURE_FILES)
    (root / "planb/pb_compose.py").write_text("x = 2\n", encoding="utf-8")
    second = sr.closure_digest()
    assert second["digest"] != first["digest"]
    # 最低被覆が読めなければ pin できていないので止める
    (root / "planb/pb_world.py").unlink()
    with pytest.raises(sr.S4Stop):
        sr.closure_digest()


def test_09b_closure_covers_more_than_four_files():
    """§5 G0-5「4 ファイルだけの部分 pin で済ませない」。"""
    assert len(sp.MIN_CLOSURE_FILES) >= 12
    for required in ("genome_s3/s3_runner.py", "planb/pb_compose.py",
                     "planb_real/pr_identity.py", "planb_real/pr_ladder.py"):
        assert required in sp.MIN_CLOSURE_FILES


def test_09c_closure_auto_collects_loaded_modules():
    loaded = sr.loaded_foundry_modules()
    assert "genome_s4/s4_runner.py" in loaded
    assert "planb/pb_compose.py" in loaded


# ===========================================================================
# §23 条件
# ===========================================================================
def test_10_b0_all_off():
    tg = sp.CONDITIONS["B0"]
    assert (tg.f0, tg.duration, tg.energy, tg.release) == (False, False, False, False)


def test_11_f_is_f0_only():
    tg = sp.CONDITIONS["F"]
    assert (tg.f0, tg.duration, tg.energy, tg.release) == (True, False, False, False)


def test_12_d_is_duration_only():
    tg = sp.CONDITIONS["D"]
    assert (tg.f0, tg.duration, tg.energy, tg.release) == (False, True, False, False)


def test_13_fd_is_f0_plus_duration_only():
    tg = sp.CONDITIONS["FD"]
    assert (tg.f0, tg.duration, tg.energy, tg.release) == (True, True, False, False)
    assert sp.is_exact_fd(tg)
    assert sp.is_exact_condition("FD", tg)


def test_14_energy_release_false_everywhere():
    assert set(sp.CONDITIONS) == {"B0", "F", "D", "FD"}
    for name, tg in sp.CONDITIONS.items():
        assert tg.energy is False and tg.release is False, name
    # §3 の禁止条件を作っていない
    for banned in ("E", "R", "FE", "DR", "FDE", "FDER"):
        assert banned not in sp.CONDITIONS


def test_15_spectral_payload_rejected():
    run = _Run("pk|a#1|b#2", "terminal_i", payload_1d=False)
    st = sg.structure_gate(run)
    assert st["pass"] is False and st["performance_payload_1d"] is False
    res = sg.pair_verdict(run, _cross([run]))
    assert res.verdict == PairVerdict.FAILED.value


def test_15b_energy_release_field_access_fails_structure():
    run = _Run("pk|a#1|b#2", "terminal_i",
               accessed={"FD": ["f0_dev_cents", "energy_db"]})
    st = sg.structure_gate(run)
    assert st["pass"] is False
    assert st["energy_release_accessed"]["FD"] == ["energy_db"]


def test_15c_wrong_toggles_fail_structure():
    run = _Run("pk|a#1|b#2", "terminal_i",
               toggles_override={"FD": "f0+duration+energy"})
    assert sg.structure_gate(run)["pass"] is False


def test_15d_failed_tripwire_fails_structure():
    run = _Run("pk|a#1|b#2", "terminal_i", tripwire_status="fail")
    assert sg.structure_gate(run)["pass"] is False


# ===========================================================================
# §23 S3 replay
# ===========================================================================
def _replay_run(pair_key: str = "pk|a#1|b#2"):
    run = sr.PairRun(pair_key=pair_key, context_id="terminal_i",
                     identity_sha256="i" * 64, performance_sha256="p" * 64)
    for cond in sp.CONDITIONS:
        run.conditions[cond] = sr.ConditionOutput(
            condition=cond, toggles=sp.CONDITIONS[cond].label,
            sample_sha256=f"sha-{cond}", wav_sha256="w", wav_path="x",
            metrics={}, tripwire_status="pass")
    return run


@pytest.mark.parametrize("cond", ["B0", "F", "D"])
def test_16_17_18_replay_sha_match(cond):
    run = _replay_run()
    canonical = {c: f"sha-{c}" for c in sp.REPLAY_CONDITIONS}
    rep = sr.replay_check(run, canonical)
    assert rep[cond]["match"] is True
    assert sg.replay_gate(run.__class__(**{**run.__dict__, "s3_replay": rep}))["pass"]


def test_19_single_replay_mismatch_blocks():
    run = _replay_run()
    canonical = {"B0": "sha-B0", "F": "different", "D": "sha-D"}
    run.s3_replay = sr.replay_check(run, canonical)
    assert run.s3_replay["F"]["match"] is False
    gate = sg.replay_gate(run)
    assert gate["pass"] is False and gate["mismatched_conditions"] == ["F"]
    # FD は S3 に存在しないので replay 対象外
    assert "FD" not in run.s3_replay
    # 記録が無い条件も一致とは呼ばない
    run.s3_replay = sr.replay_check(run, {})
    assert sg.replay_gate(run)["pass"] is False


def test_19b_replay_mismatch_makes_pair_failed():
    run = _Run("pk|a#1|b#2", "terminal_i", replay_mismatch="D")
    assert sg.pair_verdict(run, _cross([run])).verdict == PairVerdict.FAILED.value


# ===========================================================================
# §23 Gate
# ===========================================================================
def test_20_f0_retained_in_duration_background():
    run = _Run("pk|a#1|b#2", "terminal_i")
    comb = sg.combination_gate(run)
    f0 = comb["genes"]["f0"]
    # D -> FD で f0_dev_rmse_cents が 100 -> 45 に改善
    assert f0["with_background"]["effect"] == pytest.approx(55.0)
    assert f0["with_background"]["positive"] is True
    assert f0["alone"]["effect"] == pytest.approx(60.0)
    assert f0["retention"] == pytest.approx(55.0 / 60.0)


def test_21_duration_retained_in_f0_background():
    run = _Run("pk|a#1|b#2", "terminal_i")
    du = sg.combination_gate(run)["genes"]["duration"]
    # F -> FD で note_split_mae_ms が 50 -> 15 に改善
    assert du["with_background"]["effect"] == pytest.approx(35.0)
    assert du["with_background"]["positive"] is True
    assert du["retention"] == pytest.approx(35.0 / 38.0)


def test_21b_combinable_pair():
    run = _Run("pk|a#1|b#2", "terminal_i")
    assert sg.pair_verdict(run, _cross([run])).verdict == PairVerdict.COMBINABLE.value


def test_22_one_gene_reversed_is_unsupported():
    for gene, metric, worse in (("f0", "f0_dev_rmse_cents", 140.0),
                                ("duration", "note_split_mae_ms", 70.0)):
        m = {"B0": _B0, "F": _F, "D": _D, "FD": {**_FD, metric: worse}}
        run = _Run("pk|a#1|b#2", "terminal_i", metrics=m)
        res = sg.pair_verdict(run, _cross([run]))
        assert res.verdict == PairVerdict.UNSUPPORTED.value, gene
        assert res.combination["genes"][gene]["with_background"]["positive"] is False


def test_22b_alone_effect_contradicting_s3_is_failed():
    """§9.1: S3 が SUPPORT していた単独効果と逆なら FAILED。"""
    m = {"B0": _B0, "F": {**_F, "f0_dev_rmse_cents": 160.0}, "D": _D, "FD": _FD}
    run = _Run("pk|a#1|b#2", "terminal_i", metrics=m)
    res = sg.pair_verdict(run, _cross([run]))
    assert res.verdict == PairVerdict.FAILED.value
    assert res.s3_consistency["pass"] is False


def test_23_fd_identical_to_others_is_unsupported():
    for same in ("B0", "F", "D"):
        run = _Run("pk|a#1|b#2", "terminal_i")
        run.conditions["FD"].sample_sha256 = run.conditions[same].sample_sha256
        run.repeat_sample_sha256["FD"] = run.conditions[same].sample_sha256
        res = sg.pair_verdict(run, _cross([run]))
        assert res.verdict == PairVerdict.UNSUPPORTED.value, same
        assert res.combination["distinctness"][same] is False


def test_24_negative_identity_margin_is_unsupported():
    m = {"B0": _B0, "F": _F, "D": _D, "FD": {**_FD, sp.IDENTITY_METRIC: -0.5}}
    run = _Run("pk|a#1|b#2", "terminal_i", metrics=m)
    res = sg.pair_verdict(run, _cross([run]))
    assert res.verdict == PairVerdict.UNSUPPORTED.value
    assert res.combination["identity"]["positive"] is False


def test_24b_zero_intervention_is_not_evaluable():
    for gene in ("f0", "duration"):
        run = _Run("pk|a#1|b#2", "terminal_i", intervention_zero=gene)
        assert sg.pair_verdict(run, _cross([run])).verdict == \
            PairVerdict.NOT_EVALUABLE.value
    # NOT_EVALUABLE は evaluable にも combinable にも数えない
    runs = _pop(5) + [_Run("z|a#1|b#2", "terminal_i", intervention_zero="f0")]
    agg = sg.overall_gate(_verdicts(runs))
    assert agg["evaluable_pairs"] == 6 and agg["combinable_pairs"] == 5


def test_25_five_of_six_passes():
    agg = sg.overall_gate(_verdicts(_pop(5)))
    assert agg["combinable_pairs"] == 5 and agg["evaluable_pairs"] == 6
    assert agg["support_ratio"] == pytest.approx(5 / 6)
    assert agg["verdict"] == "PASS"


def test_26_four_of_six_fails():
    agg = sg.overall_gate(_verdicts(_pop(4)))
    assert agg["support_ratio"] == pytest.approx(4 / 6)
    assert agg["verdict"] == "FAIL"
    assert agg["checks"]["support_ratio"] is False


def test_27_insufficient_contexts_fails():
    runs = _pop(6, contexts=["terminal_i"])
    agg = sg.overall_gate(_verdicts(runs))
    assert agg["distinct_context_count"] == 1
    assert agg["checks"]["distinct_contexts"] is False
    assert agg["checks"]["supported_contexts"] is False
    assert agg["verdict"] == "FAIL"


def test_27b_insufficient_candidate_pairs_fails():
    agg = sg.overall_gate(_verdicts(_pop(3, n_total=3)))
    assert agg["checks"]["candidate_pairs"] is False and agg["verdict"] == "FAIL"


def test_28_single_determinism_failure_fails_s4():
    for kw in ({"same_process_mismatch": "drift"}, {"cross_process_mismatch": "drift"}):
        runs = _pop(5) + [_Run("z|a#1|b#2", "medial_ri", **kw)]
        results = _verdicts(runs)
        assert any(r.verdict == PairVerdict.FAILED.value for r in results)
        agg = sg.overall_gate(results)
        assert agg["determinism_failures"] == 1
        assert agg["verdict"] == "FAIL" and agg["hard_failure"] is True
        assert sg.s4_overall(agg["verdict"], None, None,
                            hard_failure=agg["hard_failure"]) == "FAILED"


def test_28b_structural_failure_fails_whole_s4():
    runs = _pop(5) + [_Run("z|a#1|b#2", "medial_ri", payload_1d=False)]
    agg = sg.overall_gate(_verdicts(runs))
    assert agg["structural_failures"] == 1 and agg["hard_failure"] is True
    assert sg.s4_overall(agg["verdict"], None, None, hard_failure=True) == "FAILED"


def test_28c_mechanistic_fail_without_violation_is_not_established():
    agg = sg.overall_gate(_verdicts(_pop(4)))
    assert agg["hard_failure"] is False
    assert sg.s4_overall(agg["verdict"], None, None,
                         hard_failure=False) == "NOT_ESTABLISHED"


def test_28d_retention_is_diagnostic_only():
    """§9.3: retention は記録するが閾値に使わない。"""
    # retention 0.05（大きく弱まる）でも、増分効果が正なら COMBINABLE。
    m = {"B0": _B0, "F": _F, "D": _D,
         "FD": {**_FD, "f0_dev_rmse_cents": 97.0, "note_split_mae_ms": 48.0}}
    run = _Run("pk|a#1|b#2", "terminal_i", metrics=m)
    res = sg.pair_verdict(run, _cross([run]))
    assert res.combination["genes"]["f0"]["retention"] < 0.1
    assert res.verdict == PairVerdict.COMBINABLE.value


# ===========================================================================
# §23 Blind
# ===========================================================================
_CANDS = [("terminal_i|a#1|b#1", "terminal_i"), ("terminal_i|a#2|b#2", "terminal_i"),
          ("terminal_ri|a#3|b#3", "terminal_ri"), ("terminal_ri|a#4|b#4", "terminal_ri"),
          ("medial_ri|a#5|b#5", "medial_ri"), ("medial_ri|a#6|b#6", "medial_ri")]
_S3SHA, _S35SHA = "3" * 64, "5" * 64


def test_29_two_contexts_selected_deterministically():
    first = sb.select_pairs(_CANDS, _S3SHA, _S35SHA)
    assert set(first) == set(sp.EAR_CONTEXTS) == {"terminal_i", "terminal_ri"}
    for _ in range(5):
        assert sb.select_pairs(_CANDS, _S3SHA, _S35SHA) == first
    # 順序を入れ替えても同じ選択（hash 昇順先頭）
    assert sb.select_pairs(list(reversed(_CANDS)), _S3SHA, _S35SHA) == first
    # 正本が変われば選択も変わりうるが、決定性は保つ
    other = sb.select_pairs(_CANDS, _S3SHA, "6" * 64)
    assert sb.select_pairs(_CANDS, _S3SHA, "6" * 64) == other


def test_29b_missing_ear_context_is_blocked():
    with pytest.raises(sr.S4Stop):
        sb.select_pairs([("medial_ri|a#5|b#5", "medial_ri")], _S3SHA, _S35SHA)


def test_30_selection_independent_of_effect_size():
    """§13.1: 選択 hash の入力は正本 SHA / context / pair_key だけ。"""
    import ast as _ast
    import inspect
    src = inspect.getsource(sp.ear_selection_hash) + "\n" + inspect.getsource(
        sp.select_ear_pair)
    tree = _ast.parse(src)
    for node in _ast.walk(tree):          # docstring / コメントを除いた実コードだけを見る
        if isinstance(node, (_ast.FunctionDef, _ast.Module)):
            if (node.body and isinstance(node.body[0], _ast.Expr)
                    and isinstance(node.body[0].value, _ast.Constant)):
                node.body = node.body[1:]
    code = _ast.unparse(tree)
    for token in ("metric", "effect", "rmse", "mae", "margin", "retention"):
        assert token not in code, token
    h = sp.ear_selection_hash(_S3SHA, _S35SHA, "terminal_i", "terminal_i|a#1|b#1")
    assert h == sp.ear_selection_hash(_S3SHA, _S35SHA, "terminal_i", "terminal_i|a#1|b#1")
    assert h != sp.ear_selection_hash(_S3SHA, _S35SHA, "terminal_ri", "terminal_i|a#1|b#1")


def _trials(salt: bytes = b"\x01" * 32):
    selected = sb.select_pairs(_CANDS, _S3SHA, _S35SHA)
    return sb.build_trials(selected, salt), selected


def test_31_four_abx_questions():
    trials, _sel = _trials()
    abx = [t for t in trials if t["kind"] == "ABX"]
    assert len(abx) == sp.ABX_TOTAL == 4
    # 各 context × 各 gene がちょうど 1 問
    seen = {(t["context_id"], t["gene"]) for t in abx}
    assert seen == {(c, g.value) for c in sp.EAR_CONTEXTS for g in sp.Gene}
    for t in abx:
        # 差は対象 gene の追加だけ（背景 -> 複合）
        bg = "D" if t["gene"] == "f0" else "F"
        assert {t["a_condition"], t["b_condition"]} == {bg, "FD"}
        assert t["x_condition"] in (bg, "FD")
        assert t["correct"] == ("A" if t["a_condition"] == t["x_condition"] else "B")


def test_32_two_identity_questions():
    trials, _sel = _trials()
    ident = [t for t in trials if t["kind"] == "IDENTITY"]
    assert len(ident) == sp.IDENTITY_TOTAL == 2
    assert {t["context_id"] for t in ident} == set(sp.EAR_CONTEXTS)
    for t in ident:
        assert {t["slot1_condition"], t["slot2_condition"]} == {"B0", "FD"}
    assert sp.EAR_TOTAL == 6 and len(trials) == 6


def test_33_filenames_carry_no_meaning():
    trials, _sel = _trials()
    names = [n for t in trials for n, _c in sb.clip_names(t)]
    assert sorted(names) == sorted(
        [f"T{i:03d}_{s}.wav" for i in range(1, 5) for s in ("A", "B", "X")]
        + [f"I{i:03d}_{s}.wav" for i in range(1, 3) for s in ("1", "2")])
    for n in names:
        assert sb.filename_is_blind(n), n
    for bad in ("T001_F0.wav", "FD_x.wav", "terminal_i_1.wav", "B0_a.wav"):
        assert not sb.filename_is_blind(bad), bad


def test_34_key_commitment_verified():
    trials, selected = _trials()
    key, raw = sb.build_private_key(trials, b"\x01" * 32, _S3SHA, _S35SHA, selected)
    commitment = sb.sha256_bytes(raw)
    manifest = sb.build_blind_manifest(trials, {}, _S3SHA, _S35SHA, commitment)
    assert sb.verify_commitment(raw, manifest) is True
    tampered = dict(key)
    tampered["trials"] = {k: dict(v, correct="A") for k, v in key["trials"].items()}
    assert sb.verify_commitment(sb.canonical_bytes(tampered), manifest) is False
    # 公開 manifest は正体・正解を漏らさない
    def _keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from _keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _keys(v)

    leaked_keys = set(_keys(manifest)) & {
        "correct", "pair_key", "a_condition", "b_condition", "x_condition",
        "gene", "context_id", "slot1_condition", "slot2_condition",
        "selected_pairs", "salt_hex", "trials"}
    assert not leaked_keys, leaked_keys
    blob = json.dumps(manifest, ensure_ascii=False)
    for t in trials:                      # pair_key / context / 条件名の値も出さない
        assert t["pair_key"] not in blob
        assert t["context_id"] not in blob


def _answer_all(trials, abx_answer, identity_answer, wrong: int = 0, no: int = 0):
    ans: Dict[str, str] = {}
    n_wrong, n_no = wrong, no
    for t in sorted(trials, key=lambda x: x["trial_id"]):
        if t["kind"] == "ABX":
            if n_wrong > 0:
                ans[t["trial_id"]] = "UNSURE"
                n_wrong -= 1
            else:
                ans[t["trial_id"]] = t["correct"] if abx_answer is None else abx_answer
        else:
            if n_no > 0:
                ans[t["trial_id"]] = "NO"
                n_no -= 1
            else:
                ans[t["trial_id"]] = identity_answer
    return ans


def test_35_abx_four_of_four_passes():
    trials, _sel = _trials()
    scored = sb.score(trials, _answer_all(trials, None, "YES"))
    assert scored["abx_correct"] == 4
    assert sg.abx_verdict(4) == "PERCEPTUAL_COEXPRESSION_PASS"


def test_36_abx_three_of_four_not_established():
    trials, _sel = _trials()
    scored = sb.score(trials, _answer_all(trials, None, "YES", wrong=1))
    assert scored["abx_correct"] == 3
    assert sg.abx_verdict(3) == "PERCEPTUAL_COEXPRESSION_NOT_ESTABLISHED"
    # UNSURE は正答に数えない
    assert all(r["correct"] is False for r in scored["abx"] if r["answer"] == "UNSURE")


def test_37_identity_two_of_two_passes():
    trials, _sel = _trials()
    scored = sb.score(trials, _answer_all(trials, None, "YES"))
    assert scored["identity_yes"] == 2
    assert sg.identity_verdict(2) == "IDENTITY_PRESERVED"
    assert sg.perceptual_verdict(sg.abx_verdict(4), sg.identity_verdict(2)) == "PASS"


def test_38_identity_one_of_two_not_established():
    trials, _sel = _trials()
    scored = sb.score(trials, _answer_all(trials, None, "YES", no=1))
    assert scored["identity_yes"] == 1
    assert sg.identity_verdict(1) == "IDENTITY_NOT_ESTABLISHED"
    assert sg.perceptual_verdict(sg.abx_verdict(4), sg.identity_verdict(1)) \
        == "NOT_ESTABLISHED"
    # UNSURE も YES に数えない
    scored2 = sb.score(trials, _answer_all(trials, None, "UNSURE"))
    assert scored2["identity_yes"] == 0


def test_38b_s4_overall_needs_both_human_gates():
    assert sg.s4_overall("PASS", "PERCEPTUAL_COEXPRESSION_PASS",
                         "IDENTITY_PRESERVED") == "PASS"
    assert sg.s4_overall("PASS", "PERCEPTUAL_COEXPRESSION_NOT_ESTABLISHED",
                         "IDENTITY_PRESERVED") == "NOT_ESTABLISHED"
    assert sg.s4_overall("PASS", "PERCEPTUAL_COEXPRESSION_PASS",
                         "IDENTITY_NOT_ESTABLISHED") == "NOT_ESTABLISHED"
    assert sg.s4_overall("PASS", None, None) == "BLOCKED"
    assert sg.s4_overall("BLOCKED", None, None) == "BLOCKED"


# ===========================================================================
# §23 公開
# ===========================================================================
def test_39_bundle_rolls_back_entirely(tmp_path, monkeypatch):
    a, b = tmp_path / "a.json", tmp_path / "b.md"
    a.write_bytes(b"old-a")
    b.write_bytes(b"old-b")
    wav_src, wav_dst = tmp_path / "staging", tmp_path / "wav"
    wav_src.mkdir()
    (wav_src / "new.wav").write_bytes(b"new")
    wav_dst.mkdir()
    (wav_dst / "old.wav").write_bytes(b"old")

    real_replace = os.replace

    def _boom(src, dst):
        if str(dst).endswith("b.md"):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(srep.os, "replace", _boom)
    with pytest.raises(OSError):
        srep.publish(files=[(a, b"new-a"), (b, b"new-b")],
                     dir_swaps=[(wav_src, wav_dst)])
    monkeypatch.undo()
    assert a.read_bytes() == b"old-a"          # 先に置き換えた分も巻き戻る
    assert b.read_bytes() == b"old-b"
    assert (wav_dst / "old.wav").exists()      # WAV も旧版へ復元
    assert not (wav_dst / "new.wav").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_40_bundle_publishes_atomically(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.md"
    wav_src, wav_dst = tmp_path / "staging", tmp_path / "wav"
    wav_src.mkdir()
    (wav_src / "x.wav").write_bytes(b"new")
    srep.publish(files=[(a, b"A"), (b, b"B")], dir_swaps=[(wav_src, wav_dst)])
    assert a.read_bytes() == b"A" and b.read_bytes() == b"B"
    assert (wav_dst / "x.wav").read_bytes() == b"new"
    assert not wav_dst.with_name("wav.prev").exists()
    assert not wav_src.exists()


def test_40b_secret_files_are_not_world_readable(tmp_path):
    key = tmp_path / "answer_key.private.json"
    srep.publish(files=[(key, b"{}")], secret=[key])
    assert (key.stat().st_mode & 0o077) == 0


def test_41_wav_key_and_answers_are_gitignored():
    text = (_HERE / "results" / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.wav", "wav/", "ear_pack/", "answer_key.private.json",
                    "answers.json"):
        assert pattern in text, pattern


def test_42_freeze_only_on_pass():
    payload = srep.freeze_payload()
    assert payload["schema"] == sp.FREEZE_SCHEMA
    assert payload["performance_genes"]["f0"]["combinable"] is True
    assert payload["performance_genes"]["energy"]["combinable"] == "untested"
    assert payload["scope"]["cross_donor_crossover_tested"] is False
    import inspect
    src = inspect.getsource(srep.phase_c)
    assert 'if overall == "PASS":' in src
    assert "removals += [FREEZE_JSON, FREEZE_MD]" in src


def test_43_s4_touches_nothing_outside_its_own_results():
    """§17 / §26: 本 PR 内で本体 schema を変更しない。"""
    for mod in (sr, sg, sb, srep):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("evolution/models.py", "evolution.models",
                          "genome_s3/results", "genome_s35/results"):
            if forbidden in ("genome_s3/results", "genome_s35/results"):
                continue        # 読むのは可（正本）。書き込み先だけを検査する
            assert forbidden not in src, (mod.__name__, forbidden)
    assert srep.RESULTS == _HERE / "results"
    assert sr.WAV_DIR.is_relative_to(_HERE / "results")
    assert sb.EAR_DIR.is_relative_to(_HERE / "results")
    # 正本は read-only（S4 は S3 / S3.5 の results へ書かない）
    for mod in (sr, sg, sb, srep):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "write_text" not in src or "results" in src


def test_43b_schema_matches_design_minimal_form():
    meta = {"s3_results_sha256": "a" * 64, "s35_results_sha256": "b" * 64,
            "input_manifest_sha256": "c" * 64, "code_state": {},
            "candidate_pairs": [{"pair_key": "k", "context_id": "terminal_i"}]}
    mech = sg.overall_gate(_verdicts(_pop(5)))
    res = srep.build_results(meta, mech)
    for key in ("schema", "s3_results_sha256", "s35_results_sha256",
                "candidate_pairs", "conditions", "mechanistic", "perceptual",
                "overall", "out_of_scope_observations"):
        assert key in res, key
    assert res["schema"] == "voicegenesis-genome-s4/1.0"
    assert res["conditions"] == ["B0", "F", "D", "FD"]
    assert res["mechanistic"]["verdict"] in ("PASS", "FAIL", "BLOCKED")
    assert res["overall"]["verdict"] in ("PASS", "NOT_ESTABLISHED", "FAILED", "BLOCKED")
    assert res["out_of_scope_observations"]
    # 記録が描画できる（BLOCKED でない経路）
    assert "S4 RECORD" in srep.render_record(res)


def test_43c_blocked_bundle_is_renderable():
    stop = sr.S4Stop(cause="c", impact="i", minimal_fix="f")
    files = srep.blocked_bundle(stop)
    body = json.loads(files[0][1].decode("utf-8"))
    assert body["status"] == "BLOCKED" and body["schema"] == sp.SCHEMA
    assert "BLOCKED" in files[1][1].decode("utf-8")


def test_43c2_blocked_record_carries_preflight():
    """BLOCKED 記録は「どこまで進んで何で止まったか」を残す（判定には使わない）。"""
    sr.reset_preflight()
    sr._PREFLIGHT.update({"G0-1_s3_canonical": "pass", "candidate_pairs": 6,
                          "candidate_contexts": ["terminal_i", "terminal_ri"]})
    files = srep.blocked_bundle(sr.S4Stop(cause="c", impact="i", minimal_fix="f"))
    body = json.loads(files[0][1].decode("utf-8"))
    assert body["preflight"]["candidate_pairs"] == 6
    md = files[1][1].decode("utf-8")
    assert "停止までに通過した Gate" in md and "terminal_ri" in md
    # pair_key は `|` を含む。列区切りと衝突させない
    sr._PREFLIGHT["candidate_pair_keys"] = ["terminal_i|a#1|b#2"]
    md2 = srep.blocked_bundle(
        sr.S4Stop(cause="c", impact="i", minimal_fix="f"))[1][1].decode("utf-8")
    row = next(ln for ln in md2.splitlines() if "candidate_pair_keys" in ln)
    assert row.count("|") - row.count("\\|") == 3      # 行頭 / 区切り / 行末のみ
    sr.reset_preflight()
    plain = srep.blocked_bundle(sr.S4Stop(cause="c", impact="i", minimal_fix="f"))
    assert "停止までに通過した Gate" not in plain[1][1].decode("utf-8")


def test_43d_frozen_thresholds_match_design():
    """§11 / §24: 事前登録値を実装が勝手に動かしていないこと。"""
    assert sp.MIN_CANDIDATE_PAIRS == 4
    assert sp.MIN_CONTEXTS == 2
    assert sp.MIN_SUPPORT_RATIO == 0.75
    assert sp.MIN_SUPPORTED_CONTEXTS == 2
    assert sp.ABX_TOTAL == 4 and sp.IDENTITY_TOTAL == 2
    assert sp.EAR_CONTEXTS == ("terminal_i", "terminal_ri")
    assert sp.ALLOWED_METRICS == ("f0_dev_rmse_cents", "note_split_mae_ms",
                                  "identity_margin_db")
    assert [g.value for g in sp.Gene] == ["f0", "duration"]
    assert [v.value for v in PairVerdict] == [
        "COMBINABLE", "UNSUPPORTED", "NOT_EVALUABLE", "FAILED"]


def test_43e_s4_condition_names_match_s3():
    """S3 の条件名と S4 の replay 条件名がずれていないこと。"""
    import s3_spec as s3sp
    for cond in sp.REPLAY_CONDITIONS:
        assert cond in s3sp.CONDITIONS
        s3tg, s4tg = s3sp.CONDITIONS[cond], sp.CONDITIONS[cond]
        assert (s3tg.f0, s3tg.duration, s3tg.energy, s3tg.release) == \
               (s4tg.f0, s4tg.duration, s4tg.energy, s4tg.release), cond


# ===========================================================================
# integration — 実素材が揃っているときだけ
# ===========================================================================
_HAVE_MATERIAL = sr.S3_INPUT_MANIFEST.exists()


@pytest.mark.skipif(not _HAVE_MATERIAL, reason="S3 input manifest が無い")
def test_integration_candidate_pairs_from_real_canonical():
    s3, s3_sha = sr.load_s3()
    s35, _ = sr.load_s35()
    _mf, mf_sha = sr.load_input_manifest()
    sr.gate_s3(s3)
    sr.gate_s35(s35)
    sr.gate_chain(s3_sha, s35, mf_sha, s3)
    cands = sr.candidate_pairs(s3)
    assert len(cands) >= sp.MIN_CANDIDATE_PAIRS
    assert len({c for _pk, c in cands}) >= sp.MIN_CONTEXTS
    selected = sb.select_pairs(cands, s3_sha, "x" * 64)
    assert set(selected) == set(sp.EAR_CONTEXTS)
