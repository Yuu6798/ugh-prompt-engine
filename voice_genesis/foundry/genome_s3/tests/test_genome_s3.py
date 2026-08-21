"""test_genome_s3.py — 設計書 v1.1 §18 の unit / integration。

unit は実コーパスを必要としない（判定ロジックだけを対象にする）。
integration は `planb_real/results/ladder_manifest.json` と実素材が
揃っているときだけ走り、揃っていなければ skip する（§18）。

実行:
    python -m pytest voice_genesis/foundry/genome_s3/tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent.parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("pyworld")   # 判定ロジックは純粋だが import 閉包が WORLD に触れる

import s3_gates as sg  # noqa: E402
import s3_spec as sp  # noqa: E402
from s3_spec import Gene, GeneVerdict, PairVerdict  # noqa: E402


# ---------------------------------------------------------------------------
# 判定ロジック用の最小スタブ（音を作らずに verdict の分岐だけを踏む）
# ---------------------------------------------------------------------------
class _Cond:
    def __init__(self, sha: str, metrics: Dict[str, Optional[float]],
                 tripwire_status: str = "pass") -> None:
        self.sample_sha256 = sha
        self.metrics = metrics
        self.tripwire_status = tripwire_status
        self.tripwire_accessed: List[str] = []


class _Run:
    """`s3_runner.PairRun` と同じ読み取り面を持つスタブ。"""

    def __init__(self, pair_key: str, context_id: str, *,
                 base: Dict[str, Optional[float]],
                 gene_metrics: Dict[str, Dict[str, Optional[float]]],
                 intervention: Optional[Dict[str, Dict[str, Any]]] = None,
                 payload_1d: bool = True,
                 tripwire_status: str = "pass",
                 same_process_mismatch: str = "",
                 cross_process_mismatch: str = "") -> None:
        self.pair_key = pair_key
        self.context_id = context_id
        self.identity_sha256 = "i" * 64
        self.performance_sha256 = "p" * 64
        self.performance_payload_1d = payload_1d
        self.conditions = {"B0": _Cond("sha-B0", base, tripwire_status)}
        self.repeat_sample_sha256 = {"B0": "sha-B0"}
        for gene, cond in sp.GENE_CONDITION.items():
            sha = f"sha-{cond}"
            self.conditions[cond] = _Cond(sha, gene_metrics[gene.value], tripwire_status)
            self.repeat_sample_sha256[cond] = (
                same_process_mismatch if same_process_mismatch and cond == "F" else sha)
        self.intervention = intervention or {
            g.value: {"amount": 10.0, "unit": "x", "nonzero": True} for g in Gene}
        self._cross_mismatch = cross_process_mismatch

    def cross(self) -> Dict[str, Dict[str, str]]:
        out = {c: co.sample_sha256 for c, co in self.conditions.items()}
        if self._cross_mismatch:
            out["F"] = self._cross_mismatch
        return {self.pair_key: out}


_BASE = {"f0_dev_rmse_cents": 100.0, "note_split_mae_ms": 50.0,
         "energy_corr": 0.10, "taper_rmse_db": 8.0}
#: 全 gene が「良い方向へ動いた」metric セット。
_BETTER = {
    "f0": {**_BASE, "f0_dev_rmse_cents": 40.0},
    "duration": {**_BASE, "note_split_mae_ms": 12.0},
    "energy": {**_BASE, "energy_corr": 0.70},
    "release": {**_BASE, "taper_rmse_db": 3.0},
}
_WORSE = {
    "f0": {**_BASE, "f0_dev_rmse_cents": 180.0},
    "duration": {**_BASE, "note_split_mae_ms": 90.0},
    "energy": {**_BASE, "energy_corr": 0.01},
    "release": {**_BASE, "taper_rmse_db": 12.0},
}


def _run(pair_key: str, ctx: str, *, better: bool = True, **kw) -> _Run:
    return _Run(pair_key, ctx, base=_BASE,
                gene_metrics=_BETTER if better else _WORSE, **kw)


def _verdicts(runs: List[_Run], gene: Gene = Gene.F0) -> List[sg.PairGeneResult]:
    cross: Dict[str, Dict[str, str]] = {}
    for r in runs:
        cross.update(r.cross())
    return [sg.pair_verdict(gene, r, cross) for r in runs]


# ---------------------------------------------------------------------------
# 1–5: 条件ごとのトグル排他（§6）
# ---------------------------------------------------------------------------
def test_1_gene_toggle_exactly_one_true() -> None:
    for gene in Gene:
        tg = sp.toggles_for(gene)
        on = [n for n in ("f0", "duration", "energy", "release") if getattr(tg, n)]
        assert on == [gene.value], f"{gene}: {on}"
        assert sp.only_one_toggle_on(tg, gene)


@pytest.mark.parametrize(
    ("cond", "on"),
    [("F", "f0"), ("D", "duration"), ("E", "energy"), ("R", "release")],
    ids=["2_F", "3_D", "4_E", "5_R"],
)
def test_2_to_5_other_toggles_false(cond: str, on: str) -> None:
    tg = sp.CONDITIONS[cond]
    for name in ("f0", "duration", "energy", "release"):
        assert getattr(tg, name) is (name == on), f"{cond}.{name}"
    assert sp.CONDITIONS["B0"].any_on() is False


# ---------------------------------------------------------------------------
# 6: 2-D payload 拒否
# ---------------------------------------------------------------------------
def test_6_two_dimensional_payload_rejected() -> None:
    import pb_tracks as pbt

    def _track(f0_dev: np.ndarray) -> "pbt.PerformanceTrack":
        return pbt.PerformanceTrack(
            source_id="test",
            f0_dev_cents=f0_dev, f0_dev_unit_index=np.zeros(4, dtype=int),
            f0_dev_unit_pos=np.zeros(4),
            unit_durations_s=np.array([0.2]),
            energy_db=np.zeros(4), energy_unit_index=np.zeros(4, dtype=int),
            energy_unit_pos=np.zeros(4),
            release=pbt.ReleaseSpec(window_frac=0.3, taper_db=np.zeros(4),
                                    hold_core=False))

    good = _track(np.zeros(4))
    pbt.assert_no_spectral_payload(good)   # 1 次元は通る

    bad = _track(np.zeros((4, 3)))
    with pytest.raises(ValueError):
        pbt.assert_no_spectral_payload(bad)

    # 判定側でも P1 が落ちること
    run = _run("pk", "terminal_ri", payload_1d=False)
    assert sg.pair_verdict(Gene.F0, run, run.cross()).verdict == PairVerdict.FAILED.value


# ---------------------------------------------------------------------------
# 7: zero intervention → NOT_EVALUABLE
# ---------------------------------------------------------------------------
def test_7_zero_intervention_is_not_evaluable() -> None:
    zero = {g.value: {"amount": 0.0, "unit": "x", "nonzero": False} for g in Gene}
    run = _run("pk", "terminal_ri", intervention=zero)
    res = sg.pair_verdict(Gene.F0, run, run.cross())
    assert res.verdict == PairVerdict.NOT_EVALUABLE.value
    assert res.p1_structural_isolation is True
    assert res.p2_intervention_nonzero is False


# ---------------------------------------------------------------------------
# 8–9: context_id の扱い
# ---------------------------------------------------------------------------
def test_8_context_id_is_exact_probe_kind() -> None:
    assert sp.context_id({"probe_kind": "terminal_ri"}) == "terminal_ri"
    assert sp.context_id({"probe_kind": "medial_ri"}) == "medial_ri"
    with pytest.raises(KeyError):
        sp.context_id({"probe_kind": ""})
    with pytest.raises(KeyError):
        sp.context_id({})


def test_9_same_context_pairs_count_as_one_context() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"), _run("c", "terminal_ri")]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["evaluable_pairs"] == 3
    assert agg["distinct_evaluable_context_count"] == 1
    assert agg["verdict"] == GeneVerdict.NOT_EVALUABLE.value


# ---------------------------------------------------------------------------
# 10–15: gene-level 集計の境界
# ---------------------------------------------------------------------------
def test_10_two_evaluable_pairs_is_not_evaluable() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_i")]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["evaluable_pairs"] == 2
    assert agg["verdict"] == GeneVerdict.NOT_EVALUABLE.value


def test_11_three_pairs_one_context_is_not_evaluable() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"), _run("c", "terminal_ri")]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["evaluable_pairs"] == 3
    assert agg["distinct_evaluable_context_count"] == 1
    assert agg["verdict"] == GeneVerdict.NOT_EVALUABLE.value


def test_12_three_of_three_two_contexts_is_supported() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"), _run("c", "terminal_i")]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["supported_pairs"] == 3
    assert agg["support_ratio"] == 1.0
    assert agg["distinct_supported_context_count"] == 2
    assert agg["verdict"] == GeneVerdict.SUPPORTED.value


def test_13_ratio_exactly_075_with_two_contexts_is_supported() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"),
            _run("c", "terminal_i"), _run("d", "terminal_N", better=False)]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["evaluable_pairs"] == 4
    assert agg["supported_pairs"] == 3
    assert agg["support_ratio"] == pytest.approx(0.75)
    assert agg["distinct_supported_context_count"] == 2
    assert agg["verdict"] == GeneVerdict.SUPPORTED.value


def test_14_ratio_075_but_one_supported_context_is_unsupported() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"), _run("c", "terminal_ri"),
            _run("d", "terminal_i", better=False)]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["support_ratio"] == pytest.approx(0.75)
    assert agg["distinct_evaluable_context_count"] == 2
    assert agg["distinct_supported_context_count"] == 1
    assert agg["verdict"] == GeneVerdict.UNSUPPORTED.value


def test_15_support_ratio_below_075_is_unsupported() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_i"),
            _run("c", "terminal_N", better=False), _run("d", "medial_ri", better=False)]
    agg = sg.gene_verdict(_verdicts(runs))
    assert agg["support_ratio"] == pytest.approx(0.5)
    assert agg["distinct_supported_context_count"] == 2
    assert agg["verdict"] == GeneVerdict.UNSUPPORTED.value


# ---------------------------------------------------------------------------
# 16–18: FAILED 経路
# ---------------------------------------------------------------------------
def test_16_one_structural_mismatch_fails_the_gene() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_i"),
            _run("c", "terminal_N"), _run("d", "medial_ri", tripwire_status="fail")]
    results = _verdicts(runs)
    assert results[-1].verdict == PairVerdict.FAILED.value
    agg = sg.gene_verdict(results)
    assert agg["structural_failures"] == 1
    assert agg["verdict"] == GeneVerdict.FAILED.value


def test_17_same_process_determinism_mismatch_fails_the_gene() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_i"),
            _run("c", "terminal_N"),
            _run("d", "medial_ri", same_process_mismatch="sha-DIFFERENT")]
    results = _verdicts(runs, Gene.F0)
    bad = results[-1]
    assert bad.p4_determinism is False
    assert bad.detail["P4"]["same_process"] is False
    assert bad.verdict == PairVerdict.UNSUPPORTED.value
    agg = sg.gene_verdict(results)
    assert agg["determinism_failures"] == 1
    assert agg["verdict"] == GeneVerdict.FAILED.value


def test_18_cross_process_determinism_mismatch_fails_the_gene() -> None:
    runs = [_run("a", "terminal_ri"), _run("b", "terminal_i"),
            _run("c", "terminal_N"),
            _run("d", "medial_ri", cross_process_mismatch="sha-OTHER-PROCESS")]
    results = _verdicts(runs, Gene.F0)
    bad = results[-1]
    assert bad.detail["P4"]["same_process"] is True
    assert bad.detail["P4"]["cross_process"] is False
    agg = sg.gene_verdict(results)
    assert agg["determinism_failures"] == 1
    assert agg["verdict"] == GeneVerdict.FAILED.value


# ---------------------------------------------------------------------------
# 19–20: S3 overall
# ---------------------------------------------------------------------------
def test_19_one_supported_gene_is_s3_fail() -> None:
    genes = {"f0": {"verdict": "SUPPORTED"}, "duration": {"verdict": "UNSUPPORTED"},
             "energy": {"verdict": "NOT_EVALUABLE"}, "release": {"verdict": "FAILED"}}
    ov = sg.overall_verdict(genes)
    assert ov["supported_gene_count"] == 1
    assert ov["verdict"] == "FAIL"


def test_20_two_supported_genes_is_s3_pass() -> None:
    genes = {"f0": {"verdict": "SUPPORTED"}, "duration": {"verdict": "SUPPORTED"},
             "energy": {"verdict": "NOT_EVALUABLE"}, "release": {"verdict": "UNSUPPORTED"}}
    ov = sg.overall_verdict(genes)
    assert ov["supported_gene_count"] == 2
    assert ov["supported_genes"] == ["duration", "f0"]
    assert ov["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# 補助: 契約そのものが動かないこと（§19 の閾値凍結）
# ---------------------------------------------------------------------------
def test_frozen_criteria_values() -> None:
    c = sp.Criteria()
    assert c.as_dict() == {"min_evaluable_pairs": 3, "min_evaluable_contexts": 2,
                           "min_supported_contexts": 2, "min_support_ratio": 0.75,
                           "min_supported_genes": 2}
    assert sp.SCHEMA == "voicegenesis-genome-s3/1.1"
    assert sorted(sp.CONDITIONS) == ["B0", "D", "E", "F", "R"]
    assert set(sp.GENE_METRIC) == set(Gene)


def test_report_renders_and_serializes() -> None:
    import s3_report as srep

    runs = [_run("a", "terminal_ri"), _run("b", "terminal_ri"), _run("c", "terminal_i")]
    cross: Dict[str, Dict[str, str]] = {}
    for r in runs:
        cross.update(r.cross())
    genes = {g.value: sg.gene_verdict([sg.pair_verdict(g, r, cross) for r in runs])
             for g in Gene}
    res = {"schema": sp.SCHEMA, "source_commit": "deadbeef",
           "input_manifest_sha256": "cafe", "context_phones": 22,
           "identity_ap_scale": 0.25, "pair_count": len(runs),
           "conditions": sorted(sp.CONDITIONS), "criteria": sp.Criteria().as_dict(),
           "genes": genes, "overall": sg.overall_verdict(genes),
           "out_of_scope_observations": []}
    json.dumps(res, allow_nan=False)          # NaN を書き出さない
    md = srep.render_record(res)
    assert "# S3 RECORD" in md
    assert "Gene-Level" in md


# ---------------------------------------------------------------------------
# integration（実素材があるときのみ）
# ---------------------------------------------------------------------------
def _material_ready() -> bool:
    import s3_runner as sr

    if not sr.FROZEN_MANIFEST.exists():
        return False
    data = json.loads(sr.FROZEN_MANIFEST.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    if not pairs:
        return False
    p = pairs[0]
    return Path(p["ritsu_file"]).exists() and Path(p["pjs_file"]).exists()


@pytest.mark.slow
@pytest.mark.skipif(not _material_ready(), reason="raw corpus / frozen manifest が無い")
def test_integration_single_pair_five_conditions(tmp_path: Path) -> None:
    import s3_report as srep
    import s3_runner as sr

    data = json.loads(sr.FROZEN_MANIFEST.read_text(encoding="utf-8"))
    ctx = int(data["context_phones"])
    pair = data["pairs"][0]
    run = sr.run_pair(pair, ctx)

    assert sorted(run.conditions) == ["B0", "D", "E", "F", "R"]
    for cond, co in run.conditions.items():
        assert len(co.sample_sha256) == 64, cond
        assert len(co.wav_sha256) == 64, cond
        assert co.tripwire_status == "pass", cond
        assert run.repeat_sample_sha256[cond] == co.sample_sha256, cond
        assert set(co.metrics) == {"f0_dev_rmse_cents", "note_split_mae_ms",
                                   "energy_corr", "taper_rmse_db"}
    assert run.performance_payload_1d is True
    # B0 と各 gene 条件は同一 identity から作られる
    assert run.identity_sha256 and run.performance_sha256

    res = srep.build_results([run], {"source_commit": "test", "input_manifest_sha256": "x",
                                     "context_phones": ctx}, {})
    assert res["schema"] == sp.SCHEMA
    assert set(res["genes"]) == {g.value for g in Gene}
    json.dumps(res, allow_nan=False)
    assert (tmp_path / "sentinel").parent.exists()
