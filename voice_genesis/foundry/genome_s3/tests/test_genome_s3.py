"""test_genome_s3.py — 設計書 v1.1 §18 の unit / integration。

unit は実コーパスを必要としない（判定ロジックだけを対象にする）。
integration は `planb_real/results/ladder_manifest.json` と実素材が
揃っているときだけ走り、揃っていなければ skip する（§18）。

実行:
    python -m pytest voice_genesis/foundry/genome_s3/tests -q
"""
from __future__ import annotations

import json
import os
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


@pytest.fixture(autouse=True)
def _reset_frozen_cache():
    """`s3_runner` の凍結キャッシュをテスト間で持ち越さない。"""
    import s3_runner as sr
    sr._FROZEN = None
    yield
    sr._FROZEN = None


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
                 cross_process_mismatch: str = "",
                 b0_same_process_mismatch: str = "",
                 b0_cross_process_mismatch: str = "") -> None:
        self.pair_key = pair_key
        self.context_id = context_id
        self.identity_sha256 = "i" * 64
        self.performance_sha256 = "p" * 64
        self.performance_payload_1d = payload_1d
        self.conditions = {"B0": _Cond("sha-B0", base, tripwire_status)}
        self.repeat_sample_sha256 = {"B0": b0_same_process_mismatch or "sha-B0"}
        self._b0_cross_mismatch = b0_cross_process_mismatch
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
        if self._b0_cross_mismatch:
            out["B0"] = self._b0_cross_mismatch
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


# ---------------------------------------------------------------------------
# PR #295 レビュー（Codex）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_baseline_b0_instability_fails_determinism() -> None:
    """B0 が非決定論なら、gene 条件が再現しても P4 は落ちる。

    P3 は B0 との比較なので、揺れている baseline に対する SUPPORTED は
    偽の S3 PASS になりうる。
    """
    for kw in ({"b0_same_process_mismatch": "sha-B0-DIFFERENT"},
               {"b0_cross_process_mismatch": "sha-B0-OTHER-PROCESS"}):
        run = _run("a", "terminal_ri", **kw)
        res = sg.pair_verdict(Gene.F0, run, run.cross())
        assert res.p4_determinism is False, kw
        assert res.detail["P4"]["gene_condition"]["pass"] is True, kw
        assert res.detail["P4"]["baseline_B0"]["pass"] is False, kw
        assert res.verdict == PairVerdict.UNSUPPORTED.value, kw
        assert sg.gene_verdict([res])["verdict"] == GeneVerdict.FAILED.value, kw


def _write_manifest(tmp_path: Path, pairs: List[Dict[str, Any]],
                    **extra: Any) -> Path:
    m = tmp_path / "ladder_manifest.json"
    body: Dict[str, Any] = {"context_phones": 22, "identity_ap_scale": 0.25,
                            "pairs": pairs}
    body.update(extra)
    m.write_text(json.dumps(body), encoding="utf-8")
    return m


def _pair(key: str, kind: str = "terminal_ri") -> Dict[str, Any]:
    return {"pair_key": key, "probe_kind": kind, "ritsu_file": "/x/a.lab",
            "pjs_file": "/x/b.lab", "identity_sha256": "i" * 64,
            "performance_sha256": "p" * 64}


def test_malformed_frozen_input_becomes_blocked(tmp_path: Path, monkeypatch) -> None:
    """probe_kind 欠落・pair_key 重複・必須欠落は BLOCKED（exit 3）であって
    未処理トレースバックではない。"""
    import s3_runner as sr

    cases = [
        ([{**_pair("a"), "probe_kind": ""}], "probe_kind"),
        ([_pair("a"), _pair("a", "terminal_i")], "重複"),
        ([{k: v for k, v in _pair("a").items() if k != "identity_sha256"}], "必須"),
        ([], "pairs"),
    ]
    for pairs, needle in cases:
        monkeypatch.setattr(sr, "FROZEN_MANIFEST", _write_manifest(tmp_path, pairs))
        with pytest.raises(sr.S3Stop) as exc:
            sr.load_frozen()
        assert needle in exc.value.cause, (needle, exc.value.cause)
        assert exc.value.as_dict()["status"] == "BLOCKED"


def test_pin_mismatch_stops_before_composing() -> None:
    """再構築ハッシュが凍結 pin と違えば、合成へ進まず止まる。"""
    import s3_runner as sr

    with pytest.raises(sr.S3Stop) as exc:
        sr._assert_matches_pin("pk", "identity", "a" * 64, "b" * 64)
    assert "pin" in exc.value.cause
    assert "provenance" in exc.value.impact
    sr._assert_matches_pin("pk", "identity", "a" * 64, "a" * 64)   # 一致なら素通り


def test_frozen_source_ids_reproduce_the_manifest_pins() -> None:
    """S3 が使う source_id ラベルは推測ではなく表明。

    実素材が無い環境ではラベル定数そのものだけを固定する（`_rebuild` の
    "ritsu"/"pjs" では pin を再現できないことが PR #295 で判明した）。
    """
    import s3_runner as sr

    assert sr.RITSU_SOURCE_ID == "ritsu_singing_db"
    assert sr.PJS_SOURCE_ID == "pjs_corpus"


def test_bundle_publish_is_all_or_nothing(tmp_path: Path) -> None:
    """片方の書き込みが落ちたら、既存の束をそのまま残す。"""
    import s3_report as srep

    a, b = tmp_path / "a.json", tmp_path / "b.md"
    srep.publish_bundle(((a, "old-json"), (b, "old-md")))
    assert (a.read_text(), b.read_text()) == ("old-json", "old-md")

    # 2 つ目の temp を書けなくする（ディスクが埋まった / 途中で落ちた相当）
    blocker = b.with_suffix(b.suffix + ".tmp")
    blocker.mkdir()
    with pytest.raises(OSError):
        srep.publish_bundle(((a, "new-json"), (b, "new-md")))
    assert (a.read_text(), b.read_text()) == ("old-json", "old-md")
    assert not list(tmp_path.glob("*.json.tmp"))
    blocker.rmdir()

    srep.publish_bundle(((a, "new-json"), (b, "new-md")))
    assert (a.read_text(), b.read_text()) == ("new-json", "new-md")


def test_code_state_is_captured_once_and_reports_dirtiness() -> None:
    import s3_runner as sr

    first = sr.code_state()
    assert set(first) == {"commit", "dirty", "dirty_digest", "source_digest"}
    assert first["dirty"] in (True, False, None)
    assert sr.code_state() == first          # 走行中に揺れない
    assert sr.source_commit() == first["commit"]
    if first["dirty"]:
        assert first["dirty_digest"]


# ---------------------------------------------------------------------------
# PR #295 レビュー第 2 巡で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_bundle_rollback_when_a_rename_fails(tmp_path: Path, monkeypatch) -> None:
    """staging は通ったが 2 本目の rename が落ちた場合、旧い束へ巻き戻す。"""
    import s3_report as srep

    a, b = tmp_path / "a.json", tmp_path / "b.md"
    srep.publish_bundle(((a, "old-json"), (b, "old-md")))

    real_replace = srep.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("rename failed midway")
        return real_replace(src, dst)

    monkeypatch.setattr(srep.os, "replace", flaky)
    with pytest.raises(OSError):
        srep.publish_bundle(((a, "new-json"), (b, "new-md")))
    # 1 本目は置き換わっていたが巻き戻る = 新旧が混ざった束を露出しない
    assert (a.read_text(), b.read_text()) == ("old-json", "old-md")
    assert not list(tmp_path.glob("*.tmp"))


def test_bundle_rollback_removes_files_that_did_not_exist(tmp_path: Path, monkeypatch) -> None:
    """旧い束が無かった場合、巻き戻しは「作られた分を消す」。"""
    import s3_report as srep

    a, b = tmp_path / "a.json", tmp_path / "b.md"
    real_replace = srep.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("rename failed midway")
        return real_replace(src, dst)

    monkeypatch.setattr(srep.os, "replace", flaky)
    with pytest.raises(OSError):
        srep.publish_bundle(((a, "new-json"), (b, "new-md")))
    assert not a.exists() and not b.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_manifest_digest_is_of_the_parsed_bytes(tmp_path: Path, monkeypatch) -> None:
    """走行中に manifest が書き換わっても、digest は parse した bytes のもの。"""
    import s3_runner as sr

    monkeypatch.setattr(sr, "_FROZEN", None)
    m = _write_manifest(tmp_path, [_pair("a")])
    monkeypatch.setattr(sr, "FROZEN_MANIFEST", m)
    parsed_digest = __import__("hashlib").sha256(m.read_bytes()).hexdigest()
    sr.load_frozen()
    assert sr.manifest_sha256() == parsed_digest

    m.write_text(json.dumps({"context_phones": 22, "pairs": [_pair("a")], "x": 1}),
                 encoding="utf-8")
    assert sr.manifest_sha256() == parsed_digest      # 読み直さない
    assert sr.load_frozen()["pairs"][0]["pair_key"] == "a"
    monkeypatch.setattr(sr, "_FROZEN", None)


def test_unreadable_frozen_lab_becomes_blocked(tmp_path: Path, monkeypatch) -> None:
    """凍結 manifest の絶対パスがこの環境に無い場合も BLOCKED（traceback でない）。"""
    import s3_runner as sr

    monkeypatch.setattr(sr, "_FROZEN", None)
    pair = {**_pair("terminal_ri|2018#215|pjs003#65"),
            "ritsu_file": str(tmp_path / "nope" / "2018.lab"),
            "pjs_file": str(tmp_path / "nope" / "pjs003.lab")}
    with pytest.raises(sr.S3Stop) as exc:
        sr.build_inputs(pair, 22)
    assert "lab/wav" in exc.value.cause
    assert exc.value.as_dict()["status"] == "BLOCKED"
    monkeypatch.setattr(sr, "_FROZEN", None)


# ---------------------------------------------------------------------------
# PR #295 レビュー第 3 巡（来歴ファミリの終端）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, 0, -1, "22", 22.0, True],
                         ids=["missing", "zero", "negative", "str", "float", "bool"])
def test_missing_or_invalid_context_phones_is_blocked(tmp_path: Path, monkeypatch,
                                                     bad: Any) -> None:
    """欠落を既定値で埋めない。埋めると凍結入力が記録していない値を主張する。"""
    import s3_runner as sr

    body: Dict[str, Any] = {"identity_ap_scale": 0.25, "pairs": [_pair("a")]}
    if bad is not None:
        body["context_phones"] = bad
    m = tmp_path / "ladder_manifest.json"
    m.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(sr, "FROZEN_MANIFEST", m)
    with pytest.raises(sr.S3Stop) as exc:
        sr.load_frozen()
    assert "context_phones" in exc.value.cause
    assert exc.value.as_dict()["status"] == "BLOCKED"


def test_valid_context_phones_passes(tmp_path: Path, monkeypatch) -> None:
    import s3_runner as sr

    monkeypatch.setattr(sr, "FROZEN_MANIFEST", _write_manifest(tmp_path, [_pair("a")]))
    assert sr.load_frozen()["context_phones"] == 22


def test_dirty_digest_covers_untracked_file_contents(tmp_path: Path, monkeypatch) -> None:
    """未追跡ファイルは **中身**まで digest に入る（パスだけでは同一視される）。

    `git diff HEAD` は未追跡の中身を含まず porcelain はパスしか出さないので、
    中身の違う 2 回の走行が同じ pin を主張しうる。
    """
    import s3_runner as sr

    return _assert_untracked_content_changes_digest(sr, monkeypatch, tmp_path, "helper.py")


def _assert_untracked_content_changes_digest(sr, monkeypatch, tmp_path: Path,
                                             name: str) -> None:
    helper = tmp_path / name
    repo_root = Path(sr._HERE).parent.parent.parent
    # tmp_path はリポジトリ外なので `..` を含む相対パスにする。
    # 本番と同じ「repo_root + porcelain のパス」の連結で解決できること自体も検証する。
    rel = os.path.relpath(helper, repo_root)
    assert Path(os.fsdecode(os.fsencode(repo_root) + b"/" + os.fsencode(rel))).exists() \
        or not helper.exists()
    porcelain = b" M a/b.py\x00?? " + os.fsencode(rel) + b"\x00"

    def fake_git(*args: str) -> bytes:
        if args[0] == "status":
            return porcelain
        if args[0] == "diff":
            return b"diff --git a/b.py b/b.py\n"
        if args[0] == "rev-parse":
            return b"cafe\n"
        return b""

    def run_with(content: str) -> str:
        monkeypatch.setattr(sr, "_CODE_STATE", None)
        helper.write_text(content, encoding="utf-8")
        monkeypatch.setattr(sr.subprocess, "run",
                            lambda cmd, **kw: type("P", (), {"stdout": fake_git(*cmd[1:])})())
        st = sr.code_state()
        assert st["dirty"] is True
        return st["dirty_digest"]

    # porcelain も diff も同じ。違うのは未追跡ファイルの中身だけ。
    assert run_with("VERSION = 1\n") != run_with("VERSION = 2\n")
    assert run_with("VERSION = 1\n") == run_with("VERSION = 1\n")
    monkeypatch.setattr(sr, "_CODE_STATE", None)


# ---------------------------------------------------------------------------
# PR #295 レビュー第 4 巡
# ---------------------------------------------------------------------------
def test_dirty_digest_handles_non_ascii_untracked_paths(tmp_path: Path, monkeypatch) -> None:
    """非 ASCII 名の未追跡ファイルでも中身が digest に入る。

    `--porcelain` は非 ASCII を C クォート (`"\\346\\227\\245…"`) で出すため、
    引用符を剥がすだけでは実パスに戻らない。`-z` で受けることで解決する。
    本リポジトリの Ritsu コーパスのように日本語パスが実在する環境で効く。
    """
    import s3_runner as sr

    _assert_untracked_content_changes_digest(
        sr, monkeypatch, tmp_path, "「波音リツ」ヘルパ.py")


@pytest.mark.parametrize("bad", [None, 1.0, 0.5, "0.25", True],
                         ids=["missing", "one", "half", "str", "bool"])
def test_ap_scale_mismatch_is_blocked(tmp_path: Path, monkeypatch, bad: Any) -> None:
    """記録に載る生成設定は、実装が実際に使う値と一致していなければ止める。"""
    import pr_identity
    import s3_runner as sr

    body: Dict[str, Any] = {"context_phones": 22, "pairs": [_pair("a")]}
    if bad is not None:
        body["identity_ap_scale"] = bad
    m = tmp_path / "ladder_manifest.json"
    m.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(sr, "FROZEN_MANIFEST", m)
    with pytest.raises(sr.S3Stop) as exc:
        sr.load_frozen()
    assert "identity_ap_scale" in exc.value.cause
    assert exc.value.as_dict()["status"] == "BLOCKED"
    assert pr_identity.AP_SCALE == 0.25      # 実装値そのものの固定


def test_real_git_porcelain_quoting_is_actually_handled(tmp_path: Path) -> None:
    """実際の git 出力で、非 ASCII 名が `--porcelain` では C クォートされ、
    `-z` では生のまま出ることを確認する（本修正の前提そのものの検証）。"""
    import subprocess

    def git(*args: str, **kw: Any):
        return subprocess.run(["git", *args], cwd=str(tmp_path),
                              capture_output=True, check=False, **kw)

    if git("init", "-q").returncode != 0:
        pytest.skip("git を実行できない")
    name = "「波音リツ」ヘルパ.py"
    (tmp_path / name).write_text("VERSION = 1\n", encoding="utf-8")

    quoted = git("status", "--porcelain", "--untracked-files=all").stdout
    zero = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    encoded = os.fsencode(name)

    # -z なら生のパスが得られる = repo_root と連結して読める
    entry = next(e for e in zero.split(b"\x00") if e.startswith(b"?? "))
    assert entry[3:] == encoded
    assert (tmp_path / os.fsdecode(entry[3:])).is_file()

    # --porcelain 側が C クォートしているなら、引用符を剥がすだけでは戻らない
    if b'"' in quoted:
        naive = quoted.decode("utf-8", "replace").splitlines()[0][3:].strip().strip('"')
        assert not (tmp_path / naive).is_file(), (
            "この環境では引用符剥がしでも解決できてしまう（core.quotePath=false?）")


# ---------------------------------------------------------------------------
# PR #295 レビュー第 5 巡
# ---------------------------------------------------------------------------
def test_source_digest_is_git_independent_and_content_sensitive(tmp_path: Path,
                                                                monkeypatch) -> None:
    """`source_digest` は git object に依らず、実装ファイルの中身に反応する。"""
    import s3_runner as sr

    base = sr.source_digest()
    assert len(base) == 64
    assert base == sr.source_digest()          # 同じ内容なら安定

    # 1 ファイルだけ差し替えた木を作ると digest が変わる
    fake = tmp_path / "src"
    fake.mkdir()
    for name in sr.SOURCE_FILES:
        (fake / name).write_bytes((sr._HERE / name).read_bytes())
    monkeypatch.setattr(sr, "_HERE", fake)
    assert sr.source_digest() == base          # 内容が同じなら場所に依らない
    (fake / "s3_gates.py").write_bytes(b"# tampered\n")
    assert sr.source_digest() != base


def test_wav_publish_is_all_or_nothing(tmp_path: Path) -> None:
    """staging が揃ってから公開する。落ちたら前回の wav がそのまま残る。"""
    import s3_runner as sr

    dest, staging = tmp_path / "wav", tmp_path / "wav.staging"
    (dest / "pairA").mkdir(parents=True)
    (dest / "pairA" / "B0.wav").write_bytes(b"old")

    # staging が無ければ何もしない（= 前回の成果物を壊さない）
    sr.publish_wav(staging, dest)
    assert (dest / "pairA" / "B0.wav").read_bytes() == b"old"

    (staging / "pairA").mkdir(parents=True)
    (staging / "pairA" / "B0.wav").write_bytes(b"new")
    sr.publish_wav(staging, dest)
    assert (dest / "pairA" / "B0.wav").read_bytes() == b"new"
    assert not staging.exists()
    assert not dest.with_name("wav.prev").exists()


def test_run_pair_records_published_path_not_staging(tmp_path: Path) -> None:
    """記録に載る wav_path は公開後の位置（staging の一時パスではない）。"""
    import s3_runner as sr

    leaf = "terminal_ri__x-1__y-2"
    published = sr.WAV_DIR / leaf / "B0.wav"
    # run_pair を呼ばずに規約だけを固定する（実素材が無い環境でも回る）
    assert sr.WAV_STAGING != sr.WAV_DIR
    assert str(published).endswith(f"results/wav/{leaf}/B0.wav")
    assert "wav.staging" not in str(published)
