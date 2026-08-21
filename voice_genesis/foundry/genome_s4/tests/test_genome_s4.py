"""test_genome_s4.py — 設計書 v1.0 §23 の最低要件をそのまま並べる。

unit は実コーパスを必要としない（判定ロジックだけを対象にする）。
実素材が要る経路は `planb_real/results/ladder_manifest.json` と corpus が
揃っているときだけ走る（無ければ skip）。

実行:
    python -m pytest voice_genesis/foundry/genome_s4/tests -q
"""
from __future__ import annotations

import importlib.util
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

# 判定ロジック（spec / gates / blind）は **WORLD 非依存**にしてある。ここを
# `importorskip` で括ると、`pyworld` を入れていない CI で不変条件テストが丸ごと
# skip され、「緑なのに何も検査していない」状態になる（PR #299 レビュー P2）。
import s4_blind as sb  # noqa: E402
import s4_gates as sg  # noqa: E402
import s4_spec as sp  # noqa: E402
from s4_spec import Gene, PairVerdict, S4Stop  # noqa: E402

#: 生成・公開経路（`s4_runner` / `s4_report`）だけが WORLD（pyworld）を要する。
#: **collection 時に import してはならない。** `pyworld` は `pkg_resources` を読み、
#: `sys.meta_path` へ `VendorImporter` を挿す。本ファイルが testpaths に入ると
#: collection 時点でそれが起き、`tests/test_m2_accuracy_harness.py::
#: test_non_standard_import_hooks_clean_in_real_environment`（非標準 import hook は
#: AssertionRewritingHook のみ、という repo の forensics 不変条件）を壊す。
#: そのため実 import は**テスト実行時**まで遅らせる。
_HAVE_WORLD = importlib.util.find_spec("pyworld") is not None
requires_world = pytest.mark.skipif(not _HAVE_WORLD, reason="pyworld 未導入")


#: プロキシが実モジュールへ転送してよい dunder。ここに無い dunder は
#: 型判定プローブとみなして AttributeError にする（`__bases__` 等）。
_FORWARDED_DUNDERS = frozenset({"__file__", "__name__", "__doc__"})


class _LazyModule:
    """属性アクセス時に初めて import するプロキシ（collection では読まない）。

    `monkeypatch.setattr(sr, ...)` が実モジュールへ届くよう set/del も委譲する。
    """

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def _load(self):
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, attr):
        # pytest の collection は module 大域に `issubclass(obj, TestCase)` を掛け、
        # その過程で `__bases__` 等を引く。ここで転送すると **collection 時に**
        # import が起きてしまい、遅延させた意味が消える（実測: pyworld が読まれて
        # meta_path が汚れる）。型判定プローブには素直に AttributeError を返す。
        if attr.startswith("__") and attr.endswith("__") \
                and attr not in _FORWARDED_DUNDERS:
            raise AttributeError(attr)
        return getattr(self._load(), attr)

    def __setattr__(self, attr, value):
        setattr(self._load(), attr, value)

    def __delattr__(self, attr):
        delattr(self._load(), attr)


sr = _LazyModule("s4_runner")
srep = _LazyModule("s4_report")


#: 実素材が要る経路は manifest が揃っているときだけ走る。
#: `sr` を触ると import が起きるので、パスは直接組む。
_HAVE_MATERIAL = bool(_HAVE_WORLD and (
    _FOUNDRY / "planb_real" / "results" / "ladder_manifest.json").exists())


@pytest.fixture(autouse=True)
def _reset_caches():
    if not _HAVE_WORLD:
        yield
        return
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
@requires_world
def test_01_s3_not_pass_is_blocked():
    sr.gate_s3(_s3_doc())                       # PASS は通る
    with pytest.raises(S4Stop):
        sr.gate_s3(_s3_doc(overall="FAIL"))
    with pytest.raises(S4Stop):
        sr.gate_s3(_s3_doc(f0="UNSUPPORTED"))
    with pytest.raises(S4Stop):
        sr.gate_s3(_s3_doc(duration="NOT_EVALUABLE"))


@requires_world
def test_02_s35_not_ready_is_blocked():
    sr.gate_s35(_s35_doc())
    with pytest.raises(S4Stop):
        sr.gate_s35(_s35_doc(overall="S4_NOT_READY"))
    with pytest.raises(S4Stop):
        sr.gate_s35(_s35_doc(gene="NOT_ESTABLISHED"))


@requires_world
def test_03_sha_chain_mismatch_is_blocked():
    s3 = _s3_doc()
    sr.gate_chain("s" * 64, _s35_doc(), "m" * 64, s3)      # 一致
    with pytest.raises(S4Stop):
        sr.gate_chain("z" * 64, _s35_doc(), "m" * 64, s3)  # s35 -> s3 不一致
    with pytest.raises(S4Stop):
        sr.gate_chain("s" * 64, _s35_doc(), "z" * 64, s3)  # s3 -> manifest 不一致


@requires_world
def test_04_source_pin_mismatch_is_blocked():
    pins = {"identity_sha256": "i" * 64, "performance_sha256": "p" * 64}
    sr.assert_pins("pk", "i" * 64, "p" * 64, pins)
    with pytest.raises(S4Stop):
        sr.assert_pins("pk", "z" * 64, "p" * 64, pins)
    with pytest.raises(S4Stop):
        sr.assert_pins("pk", "i" * 64, "z" * 64, pins)
    with pytest.raises(S4Stop):
        sr.assert_pins("pk", "i" * 64, "p" * 64, {})       # pin 自体が無い


@requires_world
def test_05_missing_candidate_pairs_is_blocked():
    assert len(sr.candidate_pairs(_s3_doc(pairs=6))) == 6
    with pytest.raises(S4Stop):
        sr.candidate_pairs(_s3_doc(pairs=3))               # < MIN_CANDIDATE_PAIRS
    # context が 1 種類しか無い母集団も §4 の必須条件に満たない
    one_ctx = _s3_doc(pairs=6)
    for gene in ("f0", "duration"):
        for k, v in one_ctx["genes"][gene]["pairs"].items():
            v["context_id"] = "terminal_i"
    with pytest.raises(S4Stop):
        sr.candidate_pairs(one_ctx)


@requires_world
def test_05b_candidate_is_intersection_only():
    """§4: F0 と Duration の **両方**が SUPPORTED の pair だけを採る。"""
    s3 = _s3_doc(pairs=6)
    keys = list(s3["genes"]["duration"]["pairs"])
    s3["genes"]["duration"]["pairs"][keys[0]]["verdict"] = "UNSUPPORTED"
    s3["genes"]["f0"]["pairs"][keys[1]]["verdict"] = "NOT_EVALUABLE"
    got = {pk for pk, _c in sr.candidate_pairs(s3)}
    assert keys[0] not in got and keys[1] not in got and len(got) == 4


@requires_world
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


@requires_world
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


@requires_world
def test_07b_missing_or_broken_input_is_blocked(tmp_path):
    with pytest.raises(S4Stop):
        sr.read_once(tmp_path / "nope.json", "doc", "fix")
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{not json")
    with pytest.raises(S4Stop):
        sr.read_once(bad, "doc", "fix")
    arr = tmp_path / "arr.json"
    arr.write_bytes(b"[1,2]")
    with pytest.raises(S4Stop):
        sr.read_once(arr, "doc", "fix")


@requires_world
def test_08_dirty_worktree_rejects_canonical_publish(monkeypatch):
    monkeypatch.setattr(sr, "worktree_state",
                        lambda *a, **k: {"clean": False, "entries": [" M x.py"],
                                         "excluded": []})
    sr.reset_code_state()
    with pytest.raises(S4Stop):
        sr.code_state(require_clean=True)
    sr.reset_code_state()
    assert sr.code_state(require_clean=False)["worktree"]["clean"] is False
    # git が使えない = clean を偽らない
    monkeypatch.setattr(sr, "worktree_state",
                        lambda *a, **k: {"clean": None, "entries": [], "excluded": []})
    sr.reset_code_state()
    with pytest.raises(S4Stop):
        sr.code_state(require_clean=True)


@requires_world
def test_08b_worktree_excludes_only_s4_results():
    assert sr.WORKTREE_EXCLUDE_PREFIXES == (
        "voice_genesis/foundry/genome_s4/results/",)


@requires_world
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
    with pytest.raises(S4Stop):
        sr.closure_digest()


def test_09b_closure_covers_more_than_four_files():
    """§5 G0-5「4 ファイルだけの部分 pin で済ませない」。"""
    assert len(sp.MIN_CLOSURE_FILES) >= 12
    for required in ("genome_s3/s3_runner.py", "planb/pb_compose.py",
                     "planb_real/pr_identity.py", "planb_real/pr_ladder.py"):
        assert required in sp.MIN_CLOSURE_FILES


@requires_world
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


@requires_world
@pytest.mark.parametrize("cond", ["B0", "F", "D"])
def test_16_17_18_replay_sha_match(cond):
    run = _replay_run()
    canonical = {c: f"sha-{c}" for c in sp.REPLAY_CONDITIONS}
    rep = sr.replay_check(run, canonical)
    assert rep[cond]["match"] is True
    assert sg.replay_gate(run.__class__(**{**run.__dict__, "s3_replay": rep}))["pass"]


@requires_world
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


def test_28e_failed_pair_forces_overall_failure():
    """FAILED は evaluable の分母から外れる。数えないと 5/5 = 1.0 で偽 PASS になる。"""
    runs = _pop(5, n_total=5) + [
        _Run("z|a#1|b#2", "medial_ri",
             metrics={"B0": _B0, "F": {**_F, "f0_dev_rmse_cents": 160.0},
                      "D": _D, "FD": _FD})]
    results = _verdicts(runs)
    assert [r.verdict for r in results].count(PairVerdict.FAILED.value) == 1
    agg = sg.overall_gate(results)
    assert agg["evaluable_pairs"] == 5 and agg["combinable_pairs"] == 5
    assert agg["support_ratio"] == 1.0          # 分母から外れるので比は 1.0 になる
    assert agg["failed_pairs"] == 1 and agg["s3_contradictions"] == 1
    assert agg["checks"]["no_failed_pairs"] is False
    assert agg["verdict"] == "FAIL" and agg["hard_failure"] is True
    assert sg.s4_overall(agg["verdict"], None, None, hard_failure=True) == "FAILED"


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
    with pytest.raises(S4Stop):
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
# レビュー由来の追加検査（§25 A / B / E — verdict が誤る具体経路）
# ===========================================================================
def test_44_answers_are_bound_to_the_pack_they_came_from():
    """Phase A を回し直すと salt と正解対応が変わる。古い回答を流用させない。"""
    trials, selected = _trials()
    _k, raw = sb.build_private_key(trials, b"\x01" * 32, _S3SHA, _S35SHA, selected)
    manifest = sb.build_blind_manifest(trials, {}, _S3SHA, _S35SHA,
                                       sb.sha256_bytes(raw))
    tmpl = sb.answers_template(trials, manifest["key_commitment"])
    assert tmpl["key_commitment"] == manifest["key_commitment"]
    sb.verify_answer_binding(tmpl, manifest)                 # 同じ pack は通る

    # 別 salt で作り直した pack = 別 commitment。trial_id は同じでも通さない
    trials2, _sel2 = _trials(salt=b"\x02" * 32)
    _k2, raw2 = sb.build_private_key(trials2, b"\x02" * 32, _S3SHA, _S35SHA, selected)
    manifest2 = sb.build_blind_manifest(trials2, {}, _S3SHA, _S35SHA,
                                        sb.sha256_bytes(raw2))
    assert manifest2["key_commitment"] != manifest["key_commitment"]
    assert [t["trial_id"] for t in trials2] == [t["trial_id"] for t in trials]
    with pytest.raises(S4Stop):
        sb.verify_answer_binding(tmpl, manifest2)
    with pytest.raises(S4Stop):
        sb.verify_answer_binding({"answers": {}}, manifest)  # 識別子なしは通さない


def test_44b_load_answers_returns_doc_and_rejects_malformed(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"key_commitment": "c" * 64,
                                "answers": {"T001": "A"}}), encoding="utf-8")
    doc, ans, sha = sb.load_answers(path)
    assert doc["key_commitment"] == "c" * 64 and ans == {"T001": "A"} and len(sha) == 64
    for bad in (b"[1,2]", b"{}", b"{not json"):
        path.write_bytes(bad)
        with pytest.raises(S4Stop):
            sb.load_answers(path)


def test_45_pack_audio_is_rehashed_before_scoring(tmp_path):
    """commitment は正解の差し替えしか守らない。音の差し替え・欠落は別途落とす。"""
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "T001_A.wav").write_bytes(b"aaa")
    (audio / "T001_X.wav").write_bytes(b"xxx")
    key = {"trials": {"T001": {}},
           "audio_sha256": {"T001": {"T001_A.wav": sb.sha256_bytes(b"aaa"),
                                     "T001_X.wav": sb.sha256_bytes(b"xxx")}}}
    sb.verify_pack_audio(key, audio)                         # 一致すれば通る

    (audio / "T001_X.wav").write_bytes(b"swapped")           # 差し替え
    with pytest.raises(S4Stop):
        sb.verify_pack_audio(key, audio)
    (audio / "T001_X.wav").unlink()                          # 欠落
    with pytest.raises(S4Stop):
        sb.verify_pack_audio(key, audio)
    # pin 自体が無いのは検証スキップにせず fail-closed
    for empty in ({"trials": {"T001": {}}},
                  {"trials": {"T001": {}}, "audio_sha256": {}},
                  {"trials": {"T001": {}}, "audio_sha256": {"T001": {}}}):
        with pytest.raises(S4Stop):
            sb.verify_pack_audio(empty, audio)


def test_45c_audio_pins_come_from_the_committed_key_not_the_manifest(tmp_path):
    """可変な manifest ではなく commitment 済み key を期待値の出所にする。"""
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "T001_A.wav").write_bytes(b"replaced")
    key = {"trials": {"T001": {}},
           "audio_sha256": {"T001": {"T001_A.wav": sb.sha256_bytes(b"original")}}}
    # WAV を差し替えて manifest 側の digest を合わせても、key が一致を拒む
    with pytest.raises(S4Stop):
        sb.verify_pack_audio(key, audio)
    # key は build_private_key が封じる（manifest ではない）
    trials, selected = _trials()
    k, _raw = sb.build_private_key(trials, b"\x01" * 32, _S3SHA, _S35SHA, selected,
                                   audio_sha256={t["trial_id"]: {"x.wav": "d" * 64}
                                                 for t in trials},
                                   mechanistic_digest="m" * 64)
    assert set(k["audio_sha256"]) == {t["trial_id"] for t in trials}
    assert k["mechanistic_digest"] == "m" * 64


def test_45d_manifest_must_cover_every_committed_trial():
    """一部だけ載せた manifest/key で「検証済み」を主張させない。"""
    key = {"trials": {"T001": {}, "T002": {}},
           "audio_sha256": {"T001": {"T001_A.wav": "a" * 64}}}
    with pytest.raises(S4Stop):
        sb.expected_audio(key)


def test_45e_incomplete_or_invalid_answers_are_blocked_not_scored():
    """未回答・テンプレート値を「不正解」に化けさせない（偽の実験失敗を作らない）。"""
    trials, _sel = _trials()
    good = _answer_all(trials, None, "YES")
    sb.assert_answers_complete(trials, good)                 # 全問揃えば通る

    missing = dict(good)
    missing.pop(trials[0]["trial_id"])
    with pytest.raises(S4Stop):
        sb.assert_answers_complete(trials, missing)          # 欠落

    extra = dict(good, ZZZ="A")
    with pytest.raises(S4Stop):
        sb.assert_answers_complete(trials, extra)            # 余分

    for bad_value in ("A|B|UNSURE", "YES|NO|UNSURE", "", "maybe"):
        broken = dict(good)
        broken[trials[0]["trial_id"]] = bad_value
        with pytest.raises(S4Stop):
            sb.assert_answers_complete(trials, broken)


@requires_world
def test_45b_phase_c_verifies_audio_and_binding():
    import inspect
    src = inspect.getsource(srep.phase_c)
    for guard in ("verify_pack_audio", "verify_answer_binding",
                  "_assert_mechanistic_binding", "_assert_reveal_idempotent",
                  "assert_answers_complete", "_phase_c_closure"):
        assert guard in src, guard
    # commitment 検証より後、採点より前に置かれている
    assert src.index("verify_commitment") < src.index("verify_pack_audio")
    for guard in ("verify_pack_audio", "verify_answer_binding",
                  "_assert_reveal_idempotent", "assert_answers_complete",
                  "_phase_c_closure"):
        assert src.index(guard) < src.index("scored = sb.score("), guard


@requires_world
def test_46_donor_spectrum_is_pinned_to_frozen_manifest():
    """`identity_margin_db` の入力（donor 側包絡）を来歴へ接続する。

    `identity_sha256` は Ritsu の sp/ap しか覆わず、`performance_sha256` は設計上
    スペクトルを持たない。donor 側が変われば COMBINABLE / PASS が反転しうる。
    """
    pair = {"rungs": {
        "R0": {"toggles": "none", "identity_lsd_db": 3.419, "donor_lsd_db": 18.8708},
        "R1": {"toggles": "f0", "identity_lsd_db": 3.3949, "donor_lsd_db": 19.0232},
        "R2": {"toggles": "duration", "identity_lsd_db": 3.3858,
               "donor_lsd_db": 18.8778},
        "R3": {"toggles": "f0+duration", "identity_lsd_db": 3.3729,
               "donor_lsd_db": 18.8798},
        "R4": {"toggles": "f0+duration+energy+release", "identity_lsd_db": 5.3801,
               "donor_lsd_db": 20.4812}}}
    pins = sr.manifest_rung_pins(pair)
    # rung 名ではなく toggle label で引く（4 条件すべてに pin がある）
    assert set(pins) >= {tg.label for tg in sp.CONDITIONS.values()}

    ok = sr.assert_donor_pin("pk", "FD", "f0+duration",
                             {"identity_lsd_db": 3.372912, "donor_lsd_db": 18.879804},
                             pins)
    assert ok["measured"]["donor_lsd_db"] == 18.8798

    # donor 側だけが動いた場合（1-D performance も identity も同じ）を落とす
    with pytest.raises(S4Stop):
        sr.assert_donor_pin("pk", "FD", "f0+duration",
                            {"identity_lsd_db": 3.3729, "donor_lsd_db": 17.5},
                            pins)
    # identity 側が動いた場合も落とす
    with pytest.raises(S4Stop):
        sr.assert_donor_pin("pk", "FD", "f0+duration",
                            {"identity_lsd_db": 4.0, "donor_lsd_db": 18.8798},
                            pins)
    # pin が無い / 測れないは検証スキップにせず BLOCKED
    with pytest.raises(S4Stop):
        sr.assert_donor_pin("pk", "FD", "f0+duration", {"identity_lsd_db": 3.3729,
                                                        "donor_lsd_db": 18.8798}, {})
    with pytest.raises(S4Stop):
        sr.assert_donor_pin("pk", "FD", "f0+duration",
                            {"identity_lsd_db": None, "donor_lsd_db": 18.8798}, pins)


@requires_world
def test_46b_manifest_rung_pins_ignores_incomplete_rungs():
    pair = {"rungs": {"R0": {"toggles": "none", "identity_lsd_db": 1.0},
                      "R1": {"toggles": "f0", "identity_lsd_db": 1.0,
                             "donor_lsd_db": None},
                      "R2": {"toggles": "duration", "identity_lsd_db": 1.0,
                             "donor_lsd_db": 2.0}}}
    assert set(sr.manifest_rung_pins(pair)) == {"duration"}


@requires_world
@pytest.mark.skipif(not _HAVE_MATERIAL, reason="S3 input manifest が無い")
def test_46c_real_manifest_pins_every_s4_condition():
    """凍結 manifest が S4 の 4 条件すべてに rung metric を持つこと。"""
    manifest, _sha = sr.load_input_manifest()
    wanted = {tg.label for tg in sp.CONDITIONS.values()}
    for pair in manifest["pairs"]:
        assert set(sr.manifest_rung_pins(pair)) >= wanted, pair["pair_key"]


# ===========================================================================
# relocatable rematerialization（User 裁定 2026-08-21）
# ===========================================================================
def _material_tree(tmp_path: Path, name: str = "ritsu_ex") -> Path:
    """`aggregate_extracted_sha256` が拾う拡張子だけを持つ最小の木。"""
    root = tmp_path / name / "DB"
    (root / "2018").mkdir(parents=True)
    (root / "2018" / "2018.lab").write_bytes(b"0 100 a\n")
    (root / "2018" / "2018.wav").write_bytes(b"RIFFfake")
    return tmp_path / name


def _entry_for(root: Path, old_root: str, archive: Path = None) -> dict:
    import pr_manifest
    sha, n = pr_manifest.aggregate_extracted_sha256(root)
    e = {"extracted_path": old_root, "extracted_sha256": sha,
         "extracted_file_count": n}
    if archive is not None:
        e["archive_sha256"] = sr._sha_bytes_of_file(archive)
    return e


@requires_world
def test_47_remap_maps_roots_without_touching_the_frozen_manifest():
    """裁定 3e: 旧 root は書き換えず、走行時メモリ上でだけ写像する。"""
    mapping = {"sources": [
        {"source": "ritsu_singing_db", "old_root": "/old/ritsu_ex/DB",
         "new_root": "/new/ritsu"},
        {"source": "pjs_corpus", "old_root": "/old/pjs_ex/PJS",
         "new_root": "/new/pjs"}]}
    pair = {"pair_key": "k", "ritsu_file": "/old/ritsu_ex/DB/2018/2018.lab",
            "pjs_file": "/old/pjs_ex/PJS/pjs003/pjs003.lab", "probe_kind": "terminal_ri"}
    out = sr.remap_pair(pair, mapping)
    assert out["ritsu_file"] == "/new/ritsu/2018/2018.lab"
    assert out["pjs_file"] == "/new/pjs/pjs003/pjs003.lab"
    assert out["probe_kind"] == "terminal_ri"
    # 元 dict は不変（正本は変更しない = 裁定 3d）
    assert pair["ritsu_file"] == "/old/ritsu_ex/DB/2018/2018.lab"
    # どの old_root にも当てはまらないパスは推測で補完せず止める（裁定 5）
    with pytest.raises(S4Stop):
        sr.remap_pair({"pair_key": "k", "ritsu_file": "/elsewhere/x.lab",
                       "pjs_file": "/new/pjs/a.lab"}, mapping)
    # prefix の部分一致で誤爆しない（/old/ritsu_ex/DB2 は別 root）
    with pytest.raises(S4Stop):
        sr.remap_pair({"pair_key": "k", "ritsu_file": "/old/ritsu_ex/DB2/x.lab",
                       "pjs_file": "/old/pjs_ex/PJS/a.lab"}, mapping)


@requires_world
def test_48_relocation_requires_matching_archive_and_extracted_sha(tmp_path):
    root = _material_tree(tmp_path)
    archive = tmp_path / "ritsu.zip"
    archive.write_bytes(b"archive-bytes")
    entry = _entry_for(root, "/old/ritsu_ex", archive)
    sm = {"entries": {"ritsu_singing_db": entry}}
    cfg = {"schema": sr.MATERIAL_ROOTS_SCHEMA, "sources": {
        "ritsu_singing_db": {"new_root": str(root), "archive_path": str(archive)}}}

    got = sr.resolve_material_roots(cfg, sm)
    row = got["sources"][0]
    assert row["old_root"] == "/old/ritsu_ex" and row["new_root"] == str(root.resolve())
    assert row["archive_verified"] is True and row["extracted_verified"] is True

    # archive SHA 不一致 -> BLOCKED
    bad_sm = {"entries": {"ritsu_singing_db": {**entry, "archive_sha256": "z" * 64}}}
    with pytest.raises(S4Stop):
        sr.resolve_material_roots(cfg, bad_sm)
    # 展開物の 1 ファイル差し替え -> BLOCKED（archive SHA だけでは検出できない経路）
    (root / "DB" / "2018" / "2018.lab").write_bytes(b"0 200 a\n")
    with pytest.raises(S4Stop):
        sr.resolve_material_roots(cfg, sm)


@requires_world
def test_48b_relocation_rejects_repo_internal_and_missing_roots(tmp_path):
    root = _material_tree(tmp_path)
    entry = _entry_for(root, "/old/ritsu_ex")
    sm = {"entries": {"ritsu_singing_db": entry}}

    # raw corpus が repository 内 -> BLOCKED（利用規約 第3条1）
    inside = {"schema": sr.MATERIAL_ROOTS_SCHEMA, "sources": {
        "ritsu_singing_db": {"new_root": str(sr._REPO / "voice_genesis")}}}
    with pytest.raises(S4Stop):
        sr.resolve_material_roots(inside, sm)
    # new_root が無い / 存在しない
    for spec in ({}, {"new_root": str(tmp_path / "nope")}):
        cfg = {"schema": sr.MATERIAL_ROOTS_SCHEMA,
               "sources": {"ritsu_singing_db": spec}}
        with pytest.raises(S4Stop):
            sr.resolve_material_roots(cfg, sm)
    # source_manifest に無い source
    cfg = {"schema": sr.MATERIAL_ROOTS_SCHEMA,
           "sources": {"unknown_source": {"new_root": str(root)}}}
    with pytest.raises(S4Stop):
        sr.resolve_material_roots(cfg, sm)
    # extracted_sha256 が正本に無い -> 検証スキップにせず BLOCKED
    with pytest.raises(S4Stop):
        sr.resolve_material_roots(
            {"schema": sr.MATERIAL_ROOTS_SCHEMA,
             "sources": {"ritsu_singing_db": {"new_root": str(root)}}},
            {"entries": {"ritsu_singing_db": {"extracted_path": "/old"}}})


@requires_world
def test_48c_archive_not_supplied_is_recorded_not_silently_passed(tmp_path):
    """archive を出さない運用も「検証していない」ことを記録に残す。"""
    root = _material_tree(tmp_path)
    sm = {"entries": {"ritsu_singing_db": _entry_for(root, "/old/ritsu_ex")}}
    cfg = {"schema": sr.MATERIAL_ROOTS_SCHEMA,
           "sources": {"ritsu_singing_db": {"new_root": str(root)}}}
    row = sr.resolve_material_roots(cfg, sm)["sources"][0]
    assert row["archive_verified"] is False and row["archive_note"]


@requires_world
def test_49_material_roots_schema_and_absence(tmp_path, monkeypatch):
    monkeypatch.setenv("S4_MATERIAL_ROOTS", str(tmp_path / "absent.json"))
    assert sr.load_material_roots() is None          # 無ければ従来どおり
    cfg = tmp_path / "roots.json"
    cfg.write_text(json.dumps({"schema": "wrong/9.9", "sources": {}}),
                   encoding="utf-8")
    monkeypatch.setenv("S4_MATERIAL_ROOTS", str(cfg))
    sr.reset_read_cache()
    with pytest.raises(S4Stop):
        sr.load_material_roots()
    cfg.write_text(json.dumps({"schema": sr.MATERIAL_ROOTS_SCHEMA, "sources": {}}),
                   encoding="utf-8")
    sr.reset_read_cache()
    with pytest.raises(S4Stop):
        sr.load_material_roots()                     # sources 空も通さない


@requires_world
def test_50_missing_material_files_block_without_shrinking_population(tmp_path):
    lab = tmp_path / "a.lab"
    lab.write_bytes(b"x")
    sr.assert_pair_materials({"pair_key": "k", "ritsu_file": str(lab),
                              "pjs_file": str(lab)})
    with pytest.raises(S4Stop) as exc:
        sr.assert_pair_materials({"pair_key": "k", "ritsu_file": str(lab),
                                  "pjs_file": str(tmp_path / "missing.lab")})
    assert "母集団は縮小しない" in exc.value.minimal_fix


@requires_world
def test_51_canonical_change_during_run_is_blocked(tmp_path):
    doc = tmp_path / "s3.json"
    doc.write_bytes(b'{"a": 1}')
    sr.read_once(doc, "doc", "fix")
    sr.assert_canonical_unchanged()                  # 変わっていなければ通る
    doc.write_bytes(b'{"a": 2}')
    with pytest.raises(S4Stop):
        sr.assert_canonical_unchanged()
    doc.unlink()
    with pytest.raises(S4Stop):
        sr.assert_canonical_unchanged()


@requires_world
def test_51b_canonical_label_shim_substitutes_and_restores():
    """凍結 pin は source_file（絶対パス）を含む。bytes は新 root、ラベルは正本。"""
    import pr_performance as prp
    mapping = {"sources": [{"source": "pjs_corpus", "old_root": "/old/pjs",
                            "new_root": "/new/pjs"}]}
    assert sr.to_canonical_path("/new/pjs/a/b.lab", mapping) == "/old/pjs/a/b.lab"
    assert sr.to_canonical_path("/elsewhere/x.lab", mapping) == "/elsewhere/x.lab"
    # prefix の部分一致で誤爆しない
    assert sr.to_canonical_path("/new/pjs2/x.lab", mapping) == "/new/pjs2/x.lab"

    original = prp.extract_performance
    seen = {}

    def _fake(*a, **kw):
        seen.update(kw)
        return "perf"

    prp.extract_performance = _fake
    try:
        with sr.canonical_source_labels(mapping) as applied:
            assert prp.extract_performance is not _fake      # shim が被さっている
            prp.extract_performance(source_file="/new/pjs/a/b.lab", source_id="pjs")
            assert seen["source_file"] == "/old/pjs/a/b.lab"  # ラベルは正本
            assert seen["source_id"] == "pjs"                # 他の引数は素通し
            assert applied == [{"read_from": "/new/pjs/a/b.lab",
                                "labelled_as": "/old/pjs/a/b.lab"}]
        assert prp.extract_performance is _fake              # 必ず元へ戻す
        # 例外が出ても戻す
        with pytest.raises(RuntimeError):
            with sr.canonical_source_labels(mapping):
                raise RuntimeError("boom")
        assert prp.extract_performance is _fake
        # 再配置していないときは何もしない
        with sr.canonical_source_labels(None) as noop:
            assert prp.extract_performance is _fake and noop == []
    finally:
        prp.extract_performance = original


@requires_world
def test_52_material_provenance_is_recorded():
    """裁定 6: archive SHA / 新 root / 写像 / machine 情報を正本へ残す。"""
    none = sr.material_provenance(None)
    assert none["relocated"] is False and none["machine"]["python"]
    mapped = sr.material_provenance({"sources": [
        {"source": "ritsu_singing_db", "old_root": "/old", "new_root": "/new",
         "archive_sha256": "a" * 64, "archive_verified": True,
         "extracted_sha256": "b" * 64, "extracted_file_count": 220}]})
    assert mapped["relocated"] is True
    assert mapped["performance_labels_canonicalized"] == []
    row = mapped["sources"][0]
    assert row["old_root"] == "/old" and row["new_root"] == "/new"
    assert mapped["machine"]["numpy"]

    meta = {"s3_results_sha256": "a" * 64, "s35_results_sha256": "b" * 64,
            "input_manifest_sha256": "c" * 64, "code_state": {},
            "candidate_pairs": [], "material_provenance": mapped}
    res = srep.build_results(meta, sg.overall_gate(_verdicts(_pop(5))))
    assert res["material_provenance"]["relocated"] is True
    assert "relocatable rematerialization" in srep.render_record(res)


# ===========================================================================
# §23 公開
# ===========================================================================
@requires_world
def test_38c_failed_phase_a_does_not_destroy_published_wavs(tmp_path, monkeypatch):
    """publish に到達していない走行が、前回の正当な WAV を消してはならない。"""
    calls: List[Any] = []
    monkeypatch.setattr(srep.sr, "rollback_wav", lambda d, b: calls.append((d, b)))
    srep._rollback_wav_if_published(False, None)     # publish 未到達 -> 触らない
    assert calls == []
    srep._rollback_wav_if_published(True, None)      # 到達済み -> 巻き戻す
    assert len(calls) == 1
    # 実体でも確認: backup=None の rollback は復元できずに消すだけ
    dest = tmp_path / "wav"
    dest.mkdir()
    (dest / "keep.wav").write_bytes(b"x")
    monkeypatch.undo()
    srep._rollback_wav_if_published(False, None)
    assert (dest / "keep.wav").exists()


@requires_world
def test_38d_non_pass_phase_a_removes_outcome_artifacts():
    """機械 FAIL / BLOCKED で、S4 完了を主張する成果物を残さない。"""
    import inspect
    names = {p.name for p in srep.OUTCOME_ARTIFACTS}
    assert names == {"GENOME_ARCHITECTURE_V0_1_FREEZE.json",
                     "GENOME_ARCHITECTURE_V0_1_FREEZE.md", "key_reveal.json",
                     "blind_manifest.json", "answer_key.private.json",
                     "answers.template.json"}
    src = inspect.getsource(srep.phase_a)
    assert "removals += list(OUTCOME_ARTIFACTS)" in src
    assert "blocked_bundle(stop), removals=list(OUTCOME_ARTIFACTS)" in src


@requires_world
def test_38e_reveal_makes_answer_edits_non_scorable(tmp_path, monkeypatch):
    """key 開封後は同じ回答での再実行しか許さない（不成立 -> PASS の反転を塞ぐ）。"""
    reveal = tmp_path / "key_reveal.json"
    monkeypatch.setattr(srep.sb, "KEY_REVEAL", reveal)
    manifest = {"key_commitment": "c" * 64}
    srep._assert_reveal_idempotent("a" * 64, manifest)       # reveal 未生成なら通る
    reveal.write_text(json.dumps({"answers_sha256": "a" * 64,
                                  "key_commitment": "c" * 64}), encoding="utf-8")
    srep._assert_reveal_idempotent("a" * 64, manifest)       # 同じ回答なら冪等に通る
    with pytest.raises(S4Stop):
        srep._assert_reveal_idempotent("b" * 64, manifest)   # 書き換え -> 拒否
    reveal.write_text(json.dumps({"answers_sha256": "a" * 64,
                                  "key_commitment": "d" * 64}), encoding="utf-8")
    with pytest.raises(S4Stop):
        srep._assert_reveal_idempotent("a" * 64, manifest)   # 別 pack -> 拒否


@requires_world
def test_38e2_fresh_pack_scores_without_being_blocked_by_history(tmp_path, monkeypatch):
    """Phase A（PASS）は前回 reveal を消す。それを「開封済み」と読んではならない。

    `key_reveal.json` は追跡下にあり、Phase A の PASS 経路が同 transaction で
    削除する。したがって「HEAD に在るか」を開封の証拠に使うと、**正規の初回
    Phase C が必ず BLOCKED になる**（PR #299 第 10 巡で実測）。この回帰を固定する。
    """
    assert srep.sb.KEY_REVEAL in set(srep.OUTCOME_ARTIFACTS)
    import inspect
    assert "removals += [FREEZE_JSON, FREEZE_MD, sb.KEY_REVEAL]" \
        in inspect.getsource(srep.phase_a)
    reveal = tmp_path / "key_reveal.json"          # Phase A 直後 = 実体が無い
    monkeypatch.setattr(srep.sb, "KEY_REVEAL", reveal)
    srep._assert_reveal_idempotent("a" * 64, {"key_commitment": "c" * 64})


@requires_world
def test_38j_s4b_pins_its_execution_code():
    """S4b も prepare / score の両方で clean worktree と closure を pin する。"""
    import importlib
    import inspect
    s4b = importlib.import_module("s4b_confirm")
    for fn in (s4b.prepare, s4b.score):
        assert "_closure()" in inspect.getsource(fn), fn.__name__
    src = inspect.getsource(s4b._closure)
    assert "clean worktree" in src and "closure_digest" in src


@requires_world
def test_38f_mechanistic_result_is_bound_to_the_pack():
    """verdict 文字列だけでなく、結果 bytes の digest で pack と結び付ける。"""
    raw = b'{"mechanistic": {"verdict": "PASS"}}'
    key = {"mechanistic_digest": srep.sb.sha256_bytes(raw)}
    srep._assert_mechanistic_binding(key, raw)
    with pytest.raises(S4Stop):
        srep._assert_mechanistic_binding(key, b'{"mechanistic": {"verdict": "PASS"} }')
    with pytest.raises(S4Stop):
        srep._assert_mechanistic_binding({}, raw)            # pin 欠落は fail-closed


@requires_world
def test_38g_pr_manifest_is_pinned_before_the_closure_is_frozen():
    """再配置の同一性を決める実装が closure から漏れないこと。"""
    assert "planb_real/pr_manifest.py" in sp.MIN_CLOSURE_FILES
    assert "planb_real/pr_manifest.py" in sr.loaded_foundry_modules()


@requires_world
def test_38h_canonical_is_rechecked_after_cross_process_replay():
    """cross-process 再計算中の正本差し替えを、公開前にもう一度捕まえる。"""
    import inspect
    src = inspect.getsource(srep.phase_a)
    assert src.count("assert_canonical_unchanged") >= 1
    # 再計算の**後**に置かれていること（run_all 内の 1 回だけでは窓が開く）
    assert src.index("cross_process_shas") < src.index("assert_canonical_unchanged")
    assert src.index("assert_canonical_unchanged") < src.index("publish(")


@requires_world
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


@requires_world
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


@requires_world
def test_40b_secret_files_are_not_world_readable(tmp_path):
    key = tmp_path / "answer_key.private.json"
    srep.publish(files=[(key, b"{}")], secret=[key])
    assert (key.stat().st_mode & 0o077) == 0


def test_41_wav_key_and_answers_are_gitignored():
    text = (_HERE / "results" / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.wav", "wav/", "ear_pack/", "answer_key.private.json",
                    "answers.json"):
        assert pattern in text, pattern


@requires_world
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


@requires_world
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


@requires_world
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


@requires_world
def test_43b2_mechanistic_pass_awaiting_listening_is_not_material_blocked():
    """機械 PASS + 耳未了の BLOCKED を「素材不足」と読ませない。"""
    meta = {"s3_results_sha256": "a" * 64, "s35_results_sha256": "b" * 64,
            "input_manifest_sha256": "c" * 64, "code_state": {},
            "candidate_pairs": []}
    res = srep.build_results(meta, sg.overall_gate(_verdicts(_pop(5))))
    assert res["overall"]["verdict"] == "BLOCKED"          # 4 状態語彙は変えない
    assert res["overall"]["awaiting"] == "perceptual_gate"
    md = srep.render_record(res)
    assert "READY_FOR_LISTENING" in md
    assert "入力・素材・正本が不足し、判定を実行できない" not in md
    # 機械 FAIL では awaiting を付けない
    fail = srep.build_results(meta, sg.overall_gate(_verdicts(_pop(4))))
    assert "awaiting" not in fail["overall"]


@requires_world
def test_43c_blocked_bundle_is_renderable():
    stop = S4Stop(cause="c", impact="i", minimal_fix="f")
    files = srep.blocked_bundle(stop)
    body = json.loads(files[0][1].decode("utf-8"))
    assert body["status"] == "BLOCKED" and body["schema"] == sp.SCHEMA
    assert "BLOCKED" in files[1][1].decode("utf-8")


@requires_world
def test_43c2_blocked_record_carries_preflight():
    """BLOCKED 記録は「どこまで進んで何で止まったか」を残す（判定には使わない）。"""
    sr.reset_preflight()
    sr._PREFLIGHT.update({"G0-1_s3_canonical": "pass", "candidate_pairs": 6,
                          "candidate_contexts": ["terminal_i", "terminal_ri"]})
    files = srep.blocked_bundle(S4Stop(cause="c", impact="i", minimal_fix="f"))
    body = json.loads(files[0][1].decode("utf-8"))
    assert body["preflight"]["candidate_pairs"] == 6
    md = files[1][1].decode("utf-8")
    assert "停止までに通過した Gate" in md and "terminal_ri" in md
    # pair_key は `|` を含む。列区切りと衝突させない
    sr._PREFLIGHT["candidate_pair_keys"] = ["terminal_i|a#1|b#2"]
    md2 = srep.blocked_bundle(
        S4Stop(cause="c", impact="i", minimal_fix="f"))[1][1].decode("utf-8")
    row = next(ln for ln in md2.splitlines() if "candidate_pair_keys" in ln)
    assert row.count("|") - row.count("\\|") == 3      # 行頭 / 区切り / 行末のみ
    sr.reset_preflight()
    plain = srep.blocked_bundle(S4Stop(cause="c", impact="i", minimal_fix="f"))
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


@requires_world
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
@requires_world
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
