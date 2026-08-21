"""test_genome_s35.py — S3.5 軽量版（2 段階・最大 8 問）の検証。

unit は実コーパス・実 WAV を必要としない（合成した最小 S3 正本で配管を通す）。
integration は `genome_s3/results/` の実正本が揃っているときだけ走る。

実行:
    python -m pytest voice_genesis/foundry/genome_s35/tests -q
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s35_prepare as prep  # noqa: E402
import s35_report as report  # noqa: E402
import s35_score as scoring  # noqa: E402
import s35_spec as sp  # noqa: E402

SALT = bytes(range(32))
CONTEXTS = ["terminal_ri", "terminal_i", "terminal_N", "medial_ri"]


# ---------------------------------------------------------------------------
# 合成 S3 正本
# ---------------------------------------------------------------------------
def _pair_key(ctx: str, i: int) -> str:
    return f"{ctx}|src{i}#1{i}|pjs{i:03d}#2{i}"


def make_s3(tmp: Path, *, genes: Optional[Dict[str, List[str]]] = None,
            overall: str = "PASS", supported_count: int = 4,
            corrupt_wav: bool = False) -> Tuple[Path, Path, Dict[str, Any]]:
    """`genes` = gene -> SUPPORTED pair の probe_kind 列。"""
    genes = genes or {g: CONTEXTS + CONTEXTS[:2] for g in sp.GENE_CONDITION}
    res_dir = tmp / "genome_s3" / "results"
    wav_dir = res_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    gene_blocks: Dict[str, Any] = {}
    repro: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], str] = {}
    for gene, kinds in genes.items():
        pairs: Dict[str, Any] = {}
        for i, ctx in enumerate(kinds):
            pk = _pair_key(ctx, i)
            pairs[pk] = {"pair_key": pk, "context_id": ctx, "gene": gene,
                         "verdict": "SUPPORTED"}
        # 非候補も混ぜる（選ばれないことを確認する）
        for bad_ctx, idx, verdict in (("terminal_ri", 90, "UNSUPPORTED"),
                                      ("medial_ri", 91, "NOT_EVALUABLE")):
            pk = _pair_key(bad_ctx, idx)
            pairs[pk] = {"pair_key": pk, "context_id": bad_ctx, "gene": gene,
                         "verdict": verdict}
        gene_blocks[gene] = {"verdict": "SUPPORTED", "pairs": pairs}

        for pk in pairs:
            for cond in ("B0", sp.GENE_CONDITION[gene]):
                if (pk, cond) in seen:
                    continue
                payload = f"WAV:{pk}:{cond}".encode()
                p = wav_dir / prep.pair_leaf(pk) / f"{cond}.wav"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                seen[(pk, cond)] = digest
                repro.append({"pair_key": pk, "condition": cond, "gene": gene,
                              "wav_sha256": digest, "sample_sha256": digest})

    doc = {"schema": "voicegenesis-genome-s3/1.1", "genes": gene_blocks,
           "overall": {"verdict": overall, "supported_gene_count": supported_count,
                       "supported_genes": sorted(genes)},
           "reproducibility": repro}
    path = res_dir / "s3_results.json"
    path.write_bytes(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))
    if corrupt_wav:
        victim = next(iter(seen))
        (wav_dir / prep.pair_leaf(victim[0]) / f"{victim[1]}.wav").write_bytes(b"TAMPERED")
    return path, wav_dir, doc


@pytest.fixture()
def s35(tmp_path: Path, monkeypatch):
    def _setup(**kw):
        s3_path, wav_dir, doc = make_s3(tmp_path, **kw)
        out = tmp_path / "s35"
        monkeypatch.setattr(prep, "S3_RESULTS", s3_path)
        monkeypatch.setattr(prep, "S3_WAV_DIR", wav_dir)
        monkeypatch.setattr(prep, "RESULTS", out)
        monkeypatch.setattr(prep, "BLIND_MANIFEST", out / "blind_manifest.json")
        monkeypatch.setattr(prep, "PRIVATE_KEY", out / "answer_key.private.json")
        monkeypatch.setattr(scoring, "RESULTS", out)
        monkeypatch.setattr(scoring, "KEY_REVEAL", out / "key_reveal.json")
        monkeypatch.setattr(report, "RESULTS", out)
        monkeypatch.setattr(report, "JSON_PATH", out / "s35_results.json")
        monkeypatch.setattr(report, "RECORD_PATH", out / "S3_5_RECORD.md")
        monkeypatch.setattr(report, "LAST_ERROR", out / "last_error.json")
        return out, doc
    return _setup


def _key(out: Path) -> Dict[str, Any]:
    return json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))


def _answer(out: Path, stage: int, mode) -> Path:
    """`mode(gene, spec) -> "A"/"B"/"UNSURE"` で stage の回答を作る。"""
    key = _key(out)
    manifest = json.loads((out / "blind_manifest.json").read_text(encoding="utf-8"))
    ids = manifest["stages"][str(stage)]["trial_ids"]
    answers = {tid: mode(key["trials"][tid]["gene"], key["trials"][tid]) for tid in ids}
    p = out / f"answers_stage{stage}.json"
    p.write_text(json.dumps({"schema": sp.ANSWERS_SCHEMA, "stage": stage,
                             "listener_id": "listener-01", "session_id": "s1",
                             "answers": answers}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def _correct(_g, spec):
    return spec["correct"]


def _wrong(_g, spec):
    return "B" if spec["correct"] == "A" else "A"


def _only(genes):
    def _m(g, spec):
        return spec["correct"] if g in genes else ("B" if spec["correct"] == "A" else "A")
    return _m


def _run_full(out: Path, stage1_mode, stage2_mode=_correct) -> Dict[str, Any]:
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, stage1_mode)
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    if adv:
        prep.prepare_stage2(adv)
        _answer(out, 2, stage2_mode)
    return scoring.finalize()


# ---------------------------------------------------------------------------
# 入力ゲート
# ---------------------------------------------------------------------------
def test_non_pass_s3_is_blocked(s35) -> None:
    for kw in ({"overall": "FAIL"}, {"supported_count": 1}):
        _out, _doc = s35(**kw)
        with pytest.raises(prep.S35Stop) as exc:
            prep.prepare_stage1(salt=SALT)
        assert exc.value.as_dict()["status"] == "BLOCKED"


def test_wav_sha_mismatch_is_blocked(s35) -> None:
    _out, _doc = s35(corrupt_wav=True)
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare_stage1(salt=SALT)
    d = exc.value.as_dict()
    assert d["status"] == "BLOCKED"
    assert "SHA" in d["reason"] or "欠落" in d["reason"]
    assert "再生成" in d["required_action"]


def test_missing_wav_is_blocked(s35) -> None:
    _out, _doc = s35()
    # Stage 1 が実際に使う WAV を消す（無関係な pair を消しても検出対象外）
    res, s3_sha = prep.load_s3_results()
    gene = prep.candidate_genes(res)[0]
    pk = prep.plan_gene(res, s3_sha, gene)["stage1"]["pair_key"]
    (prep.S3_WAV_DIR / prep.pair_leaf(pk) / "B0.wav").unlink()
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare_stage1(salt=SALT)
    assert "欠落" in exc.value.as_dict()["reason"]


def test_existing_private_key_blocks_regeneration(s35) -> None:
    _out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare_stage1(salt=SALT)
    assert "private key" in exc.value.as_dict()["reason"]


# ---------------------------------------------------------------------------
# 負担: Stage 1 = gene 数、合計 <= 8
# ---------------------------------------------------------------------------
def test_stage1_is_one_trial_per_gene(s35) -> None:
    out, _doc = s35()
    info = prep.prepare_stage1(salt=SALT)
    assert info["trial_count"] == 4
    key = _key(out)
    s1 = [t for t in key["trials"].values() if t["stage"] == 1]
    assert len(s1) == 4
    assert len({t["gene"] for t in s1}) == 4
    assert len(list((out / "stage1" / "audio").glob("*.wav"))) == 12   # 4 × A/B/X


def test_total_trials_never_exceed_eight(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    key = _key(out)
    assert len(key["trials"]) <= sp.MAX_TOTAL_TRIALS == 8
    assert len([t for t in key["trials"].values() if t["stage"] == 2]) == 4


def test_stage2_only_for_genes_that_passed_stage1(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _only({"f0", "energy"}))
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    assert adv == ["energy", "f0"]
    info = prep.prepare_stage2(adv)
    assert info["trial_count"] == 2
    assert info["genes"] == ["energy", "f0"]
    assert len(list((out / "stage2" / "audio").glob("*.wav"))) == 6


def test_no_stage2_when_nothing_passes(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _wrong)
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    assert adv == []
    assert prep.prepare_stage2(adv)["trial_count"] == 0
    res = scoring.finalize()
    assert all(v["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value
               for v in res["genes"].values())
    assert res["overall"]["verdict"] == "S4_NOT_READY"


# ---------------------------------------------------------------------------
# pair 選択
# ---------------------------------------------------------------------------
def test_unsupported_pairs_are_never_selected(s35) -> None:
    out, doc = s35()
    prep.prepare_stage1(salt=SALT)
    chosen = {t["pair_key"] for t in _key(out)["trials"].values()}
    for gene, blk in doc["genes"].items():
        for pk, p in blk["pairs"].items():
            if p["verdict"] != "SUPPORTED":
                assert pk not in chosen, f"{gene}: 非 SUPPORTED の {pk} が選ばれた"


def test_stage2_uses_different_pair_and_context(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    key = _key(out)
    for gene, plan in key["plans"].items():
        assert plan["stage1"] and plan["stage2"], gene
        assert plan["stage1"]["pair_key"] != plan["stage2"]["pair_key"], gene
        assert plan["stage1"]["probe_kind"] != plan["stage2"]["probe_kind"], gene


def test_selection_is_deterministic_and_effect_size_independent() -> None:
    cands = [(_pair_key(CONTEXTS[i % 4], i), CONTEXTS[i % 4]) for i in range(6)]
    a = sp.select_two_stage_pairs(cands, "sha-A", "f0")
    b = sp.select_two_stage_pairs(list(reversed(cands)), "sha-A", "f0")
    assert a == b                       # 候補の並び順に依存しない
    assert a == sp.select_two_stage_pairs(cands, "sha-A", "f0")
    assert a[0] is not None and a[1] is not None
    assert a[0][1] != a[1][1]


def test_single_context_gene_is_not_evaluable(s35) -> None:
    """Stage 2 用の別 context が無い gene は NOT_EVALUABLE_S35。"""
    out, _doc = s35(genes={"f0": ["terminal_ri"] * 4,
                           "duration": CONTEXTS, "energy": CONTEXTS, "release": CONTEXTS})
    info = prep.prepare_stage1(salt=SALT)
    assert "f0" in info["not_evaluable"]
    key = _key(out)
    assert key["plans"]["f0"]["stage2"] is None
    assert "別 context" in key["plans"]["f0"]["not_evaluable_reason"]

    _answer(out, 1, _correct)
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    prep.prepare_stage2(adv)
    _answer(out, 2, _correct)
    res = scoring.finalize()
    assert res["genes"]["f0"]["verdict"] == sp.GeneVerdict.NOT_EVALUABLE_S35.value
    # f0 が Stage 1 に正解していても PERCEPTIBLE_CANDIDATE にはならない
    assert "f0" not in res["overall"]["perceptible_candidates"]


# ---------------------------------------------------------------------------
# 判定規則
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("s1", "s2", "has2", "expected"), [
    (True, True, True, sp.GeneVerdict.PERCEPTIBLE_CANDIDATE),
    (True, False, True, sp.GeneVerdict.NOT_ESTABLISHED),
    (False, None, True, sp.GeneVerdict.NOT_ESTABLISHED),
    (True, None, False, sp.GeneVerdict.NOT_EVALUABLE_S35),
    (False, None, False, sp.GeneVerdict.NOT_EVALUABLE_S35),   # 構造的欠落が優先
])
def test_gene_verdict_rules(s1, s2, has2, expected) -> None:
    assert sp.gene_verdict(s1, s2, has_stage2_pair=has2) is expected


def test_unsure_counts_as_incorrect(s35) -> None:
    out, _doc = s35()
    res = _run_full(out, lambda g, s: "UNSURE")
    for gene, v in res["genes"].items():
        assert v["stage1"]["answer"] == "UNSURE", gene
        assert v["stage1"]["correct"] is False, gene
        assert v["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value, gene
    assert res["overall"]["verdict"] == "S4_NOT_READY"


def test_unsure_in_stage2_is_not_established(s35) -> None:
    out, _doc = s35()
    res = _run_full(out, _correct, lambda g, s: "UNSURE")
    assert all(v["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value
               for v in res["genes"].values())


def test_both_stages_correct_is_perceptible_candidate(s35) -> None:
    out, _doc = s35()
    res = _run_full(out, _correct, _correct)
    for gene, v in res["genes"].items():
        assert v["verdict"] == sp.GeneVerdict.PERCEPTIBLE_CANDIDATE.value, gene
        assert len(v["contexts"]) == sp.REQUIRED_CONTEXTS, gene
    assert res["overall"]["perceptible_candidate_count"] == 4
    assert res["overall"]["verdict"] == "S4_READY"


# ---------------------------------------------------------------------------
# S4 Gate
# ---------------------------------------------------------------------------
def test_s4_gate_thresholds() -> None:
    assert sp.s4_gate(2) == "S4_READY"
    assert sp.s4_gate(4) == "S4_READY"
    assert sp.s4_gate(1) == "S4_NOT_READY"
    assert sp.s4_gate(0) == "S4_NOT_READY"


def test_two_candidates_is_ready_one_is_not(s35) -> None:
    out, _doc = s35()
    res = _run_full(out, _only({"f0", "energy"}), _correct)
    assert res["overall"]["perceptible_candidates"] == ["energy", "f0"]
    assert res["overall"]["verdict"] == "S4_READY"


def test_one_candidate_is_not_ready(s35) -> None:
    out, _doc = s35()
    res = _run_full(out, _only({"f0"}), _correct)
    assert res["overall"]["perceptible_candidate_count"] == 1
    assert res["overall"]["verdict"] == "S4_NOT_READY"


# ---------------------------------------------------------------------------
# blind
# ---------------------------------------------------------------------------
def test_blind_filenames_and_manifest_carry_no_gene_information(s35) -> None:
    out, doc = s35()
    prep.prepare_stage1(salt=SALT)
    names = [p.name for p in (out / "stage1" / "audio").glob("*.wav")]
    assert names
    leak = set(doc["genes"]) | {"B0", "terminal_ri", "terminal_i", "terminal_N",
                                "medial_ri", "pjs", "src"}
    for n in names:
        assert re.fullmatch(r"S1T\d{2}_[ABX]\.wav", n), n
        for token in leak:
            assert token not in n, f"{n} に {token} が漏れている"
    # manifest は構造ごと固定（sha256 hex に "f0" が偶然含まれるため部分文字列走査は不可）
    manifest = json.loads((out / "blind_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"protocol_version", "s3_results_sha256", "stages",
                             "key_commitment"}
    assert set(manifest["stages"]) == {"1"}          # Stage 2 はまだ載せない
    block = manifest["stages"]["1"]
    assert set(block) == {"trial_ids", "audio_sha256"}
    assert all(re.fullmatch(r"S1T\d{2}", t) for t in block["trial_ids"])
    for tid, per in block["audio_sha256"].items():
        assert set(per) == {"A", "B", "X"}, tid
        assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in per.values()), tid
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["key_commitment"])


def test_listening_ui_has_no_answer_key(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    html = (out / "stage1" / "index.html").read_text(encoding="utf-8")
    key = _key(out)
    # 鍵のフィールド名・gene 名・正解そのものが漏れていないこと。
    # 素朴な部分文字列走査は "voicegenesis" の中の "gene" を誤検出するので、
    # 実際に秘匿すべきトークンだけを挙げる。
    for token in ("correct", "x_side", "a_side", "b_side", "pair_key",
                  "probe_kind", "salt_hex", key["salt_hex"]):
        assert token not in html, f"UI に {token} が埋め込まれている"
    for gene in key["plans"]:
        assert gene not in html, f"UI に gene 名 {gene} が漏れている"
    for spec in key["trials"].values():
        assert spec["pair_key"] not in html


def test_stage2_pairs_are_committed_before_stage1_answers(s35) -> None:
    """Stage 1 の結果を見てから Stage 2 の pair を選べない。"""
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    before = {g: p["stage2"]["pair_key"] for g, p in _key(out)["plans"].items()}
    commitment_before = json.loads(
        (out / "blind_manifest.json").read_text(encoding="utf-8"))["key_commitment"]
    _answer(out, 1, _correct)
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    prep.prepare_stage2(adv)
    after = {g: p["stage2"]["pair_key"] for g, p in _key(out)["plans"].items()}
    assert before == after
    assert json.loads((out / "blind_manifest.json").read_text(
        encoding="utf-8"))["key_commitment"] == commitment_before


def test_commitment_matches_and_tampering_yields_invalid(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    raw = (out / "answer_key.private.json").read_bytes()
    manifest = json.loads((out / "blind_manifest.json").read_text(encoding="utf-8"))
    assert manifest["key_commitment"] == hashlib.sha256(raw).hexdigest()
    assert prep.canonical_bytes(json.loads(raw.decode("utf-8"))) == raw

    _answer(out, 1, _correct)
    adv = scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1))
    prep.prepare_stage2(adv)
    _answer(out, 2, _correct)

    key = json.loads(raw.decode("utf-8"))
    tid = sorted(key["trials"])[0]
    key["trials"][tid]["correct"] = "B" if key["trials"][tid]["correct"] == "A" else "A"
    (out / "answer_key.private.json").write_bytes(prep.canonical_bytes(key))
    res = scoring.finalize()
    assert res["commitment_verified"] is False
    assert all(v["verdict"] == sp.GeneVerdict.INVALID.value for v in res["genes"].values())
    assert res["overall"]["verdict"] == "S4_NOT_READY"


def test_tampered_pack_audio_is_invalid(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _correct)
    victim = sorted((out / "stage1" / "audio").glob("*.wav"))[0]
    victim.write_bytes(victim.read_bytes() + b"\x00")
    s1 = scoring.score_stage(sp.STAGE1)
    assert s1["audio_verified"] is False


# ---------------------------------------------------------------------------
# 音声の byte 同一性
# ---------------------------------------------------------------------------
def test_audio_bytes_are_copied_verbatim(s35) -> None:
    out, doc = s35()
    prep.prepare_stage1(salt=SALT)
    src_sha = {(r["pair_key"], r["condition"]): r["wav_sha256"]
               for r in doc["reproducibility"]}
    key = _key(out)
    for tid, spec in key["trials"].items():
        if spec["stage"] != 1:
            continue
        for slot in ("A", "B", "X"):
            side = spec["x_side"] if slot == "X" else spec[f"{slot.lower()}_side"]
            cond = "B0" if side == sp.SIDE_B0 else sp.GENE_CONDITION[spec["gene"]]
            got = hashlib.sha256(
                (out / "stage1" / "audio" / f"{tid}_{slot}.wav").read_bytes()).hexdigest()
            assert got == src_sha[(spec["pair_key"], cond)], (tid, slot)
        a = (out / "stage1" / "audio" / f"{tid}_A.wav").read_bytes()
        b = (out / "stage1" / "audio" / f"{tid}_B.wav").read_bytes()
        x = (out / "stage1" / "audio" / f"{tid}_X.wav").read_bytes()
        assert a != b and (x == a or x == b), tid


def test_copy_sha_verified_before_and_after(s35) -> None:
    out, _doc = s35()
    import shutil as _sh
    orig = prep.shutil.copyfile

    def bad_copy(src, dst):
        orig(src, dst)
        Path(dst).write_bytes(Path(dst).read_bytes() + b"\x00")

    prep.shutil = type("S", (), {"copyfile": staticmethod(bad_copy),
                                 "rmtree": staticmethod(_sh.rmtree)})()
    try:
        with pytest.raises(prep.S35Stop) as exc:
            prep.prepare_stage1(salt=SALT)
        assert "byte copy" in exc.value.as_dict()["reason"]
    finally:
        prep.shutil = _sh


# ---------------------------------------------------------------------------
# 回答検証
# ---------------------------------------------------------------------------
def test_partial_or_invalid_answers_are_blocked(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    p = _answer(out, 1, _correct)
    doc = json.loads(p.read_text(encoding="utf-8"))
    for mutate, needle in (
        (lambda d: d["answers"].update({sorted(d["answers"])[0]: ""}), "未回答"),
        (lambda d: d["answers"].update({sorted(d["answers"])[0]: "MAYBE"}), "UNSURE 以外"),
        (lambda d: d["answers"].update({"S1T99": "A"}), "manifest に無い"),
        (lambda d: d.update({"schema": "wrong"}), "schema"),
        (lambda d: d.update({"stage": 2}), "stage"),
    ):
        d = json.loads(json.dumps(doc))
        mutate(d)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(prep.S35Stop) as exc:
            scoring.score_stage(sp.STAGE1)
        assert needle in exc.value.as_dict()["reason"], needle
        assert exc.value.as_dict()["status"] == "BLOCKED"


def test_finalize_blocks_when_stage2_owed_but_missing(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _correct)
    with pytest.raises(prep.S35Stop) as exc:
        scoring.finalize()
    assert "Stage 2 pack が無い" in exc.value.as_dict()["reason"]


# ---------------------------------------------------------------------------
# 凍結定数と削除された旧設計
# ---------------------------------------------------------------------------
def test_frozen_constants_and_removed_v1_elements() -> None:
    assert sp.TRIALS_PER_GENE_PER_STAGE == 1
    assert sp.STAGES == 2
    assert sp.MAX_TOTAL_TRIALS == 8
    assert sp.MIN_PERCEPTIBLE_GENES_FOR_S4 == 2
    assert sp.MAX_REPLAYS_PER_CLIP == 3
    assert sp.REQUIRED_CONTEXTS == 2
    assert {v.value for v in sp.GeneVerdict} == {
        "PERCEPTIBLE_CANDIDATE", "NOT_ESTABLISHED", "NOT_EVALUABLE_S35", "INVALID"}
    # 旧 v1 の要素は削除されている
    for gone in ("PAIRS_PER_GENE", "REPEATS_PER_PAIR", "TRIALS_PER_GENE",
                 "PERCEPTIBLE_CORRECT", "MIN_CONTEXTS"):
        assert not hasattr(sp, gone), f"{gone} が残っている"
    assert not hasattr(sp.GeneVerdict, "PERCEPTIBLE")


def test_gene_condition_matches_s3() -> None:
    s3_spec_path = _HERE.parent / "genome_s3" / "s3_spec.py"
    if not s3_spec_path.exists():
        pytest.skip("genome_s3 が無い")
    sys.path.insert(0, str(_HERE.parent / "genome_s3"))
    pytest.importorskip("pyworld")
    import s3_spec  # noqa: PLC0415
    assert {g.value: c for g, c in s3_spec.GENE_CONDITION.items()} == sp.GENE_CONDITION


def test_wavs_and_private_key_are_gitignored() -> None:
    gi = (_HERE / "results" / ".gitignore").read_text(encoding="utf-8")
    for pat in ("*.wav", "answer_key.private.json"):
        assert pat in gi, pat
    repo = _HERE.parent.parent.parent
    for probe in ("voice_genesis/foundry/genome_s35/results/stage1/audio/S1T01_A.wav",
                  "voice_genesis/foundry/genome_s35/results/answer_key.private.json",
                  "voice_genesis/foundry/genome_s35/results/answers_stage1.json"):
        r = subprocess.run(["git", "check-ignore", "-q", probe], cwd=str(repo))
        assert r.returncode == 0, f"{probe} が git 管理外になっていない"
    tracked = subprocess.run(["git", "ls-files", "voice_genesis/foundry/genome_s35"],
                             cwd=str(repo), capture_output=True, text=True).stdout
    assert ".wav" not in tracked
    assert "answer_key.private.json" not in tracked


# ---------------------------------------------------------------------------
# 記録
# ---------------------------------------------------------------------------
def test_report_renders_and_serializes(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _correct)
    prep.prepare_stage2(scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1)))
    _answer(out, 2, _correct)
    assert report.main() == 0
    res = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    assert res["schema"] == sp.SCHEMA
    assert res["protocol"] == sp.Protocol().as_dict()
    assert res["overall"]["verdict"] == "S4_READY"
    assert res["out_of_scope_observations"]
    json.dumps(res, allow_nan=False)
    md = (out / "S3_5_RECORD.md").read_text(encoding="utf-8")
    assert "# S3.5 RECORD" in md
    assert "S4_READY" in md
    assert "統計的有意差・知覚閾値・完全独立は主張しない" in md
    assert (out / "key_reveal.json").exists()


def test_report_blocked_path(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    assert report.main() == 3          # Stage 1 の回答がまだ無い
    res = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    assert res["status"] == "BLOCKED"
    assert "BLOCKED" in (out / "S3_5_RECORD.md").read_text(encoding="utf-8")


def test_key_reveal_only_after_answers(s35) -> None:
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    assert not (out / "key_reveal.json").exists()
    _answer(out, 1, _wrong)            # 全問不正解 = Stage 2 なし
    scoring.finalize()
    reveal = json.loads((out / "key_reveal.json").read_text(encoding="utf-8"))
    assert reveal["commitment_verified"] is True
    assert "1" in reveal["revealed_after_answers_sha256"]


# ---------------------------------------------------------------------------
# integration（実 S3 正本があるときのみ）
# ---------------------------------------------------------------------------
def _real_s3_ready() -> bool:
    if not prep.S3_RESULTS.exists() or not prep.S3_WAV_DIR.exists():
        return False
    return any(prep.S3_WAV_DIR.rglob("*.wav"))


@pytest.mark.skipif(not _real_s3_ready(), reason="S3 実正本 / WAV が無い")
def test_integration_real_s3_plan_is_deterministic() -> None:
    """実 S3 正本で 2 段階 plan が決定論・別 context になることを確認（副作用なし）。"""
    res, s3_sha = prep.load_s3_results()
    prep.gate_s3(res)
    genes = prep.candidate_genes(res)
    assert genes
    first = {g: prep.plan_gene(res, s3_sha, g) for g in genes}
    second = {g: prep.plan_gene(res, s3_sha, g) for g in genes}
    assert first == second
    for g, plan in first.items():
        sup = {pk for pk, _ in prep.candidate_pairs(res, g)}
        assert plan["stage1"] is not None, g
        assert plan["stage1"]["pair_key"] in sup, g
        if plan["stage2"] is not None:
            assert plan["stage2"]["pair_key"] in sup, g
            assert plan["stage1"]["pair_key"] != plan["stage2"]["pair_key"], g
            assert plan["stage1"]["probe_kind"] != plan["stage2"]["probe_kind"], g


# ---------------------------------------------------------------------------
# PR #296 レビュー（Codex）で塞いだ穴の回帰テスト
# ---------------------------------------------------------------------------
def test_rescoring_after_reveal_is_blocked(s35) -> None:
    """正解開示後に回答を書き換えての再採点を拒否する。

    これができると、開示済み session から S4_NOT_READY を S4_READY へ
    作り替えられる（commitment も audio も true のまま）。
    """
    out, _doc = s35()
    # まず 1 gene だけ正解 = S4_NOT_READY で確定・開示させる
    res = _run_full(out, _only({"f0"}), _correct)
    assert res["overall"]["verdict"] == "S4_NOT_READY"
    assert (out / "key_reveal.json").exists()
    before = (out / "key_reveal.json").read_bytes()

    # 開示後に Stage 1 の回答を全問正解へ書き換える
    _answer(out, 1, _correct)
    with pytest.raises(prep.S35Stop) as exc:
        scoring.finalize()
    d = exc.value.as_dict()
    assert d["status"] == "BLOCKED"
    assert "再採点は禁止" in d["reason"]
    assert (out / "key_reveal.json").read_bytes() == before   # 開示物を上書きしない


def test_identical_rerun_after_reveal_is_allowed(s35) -> None:
    """同一回答・同一鍵での再実行は冪等な再描画として通す。"""
    out, _doc = s35()
    first = _run_full(out, _correct, _correct)
    again = scoring.finalize()
    assert again["overall"] == first["overall"]
    assert again["answers_sha256"] == first["answers_sha256"]


def test_tampered_key_after_reveal_is_blocked(s35) -> None:
    out, _doc = s35()
    _run_full(out, _correct, _correct)
    kp = out / "answer_key.private.json"
    key = json.loads(kp.read_text(encoding="utf-8"))
    tid = sorted(key["trials"])[0]
    key["trials"][tid]["correct"] = "B" if key["trials"][tid]["correct"] == "A" else "A"
    kp.write_bytes(prep.canonical_bytes(key))
    with pytest.raises(prep.S35Stop) as exc:
        scoring.finalize()
    assert "private key が変わっている" in exc.value.as_dict()["reason"]


def test_empty_or_partial_audio_manifest_is_not_verified(s35) -> None:
    """被覆が欠けた manifest を "verified" にしない（WAV 全消しでも true が出た）。"""
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    mp = out / "blind_manifest.json"
    full = json.loads(mp.read_text(encoding="utf-8"))
    assert scoring.verify_stage_audio(full, 1)[0] is True

    # (a) audio_sha256 を空にして WAV も全消し
    empty = json.loads(json.dumps(full))
    empty["stages"]["1"]["audio_sha256"] = {}
    for w in (out / "stage1" / "audio").glob("*.wav"):
        w.unlink()
    ok, detail = scoring.verify_stage_audio(empty, 1)
    assert ok is False
    assert "被覆" in detail["problems"][0]

    # (b) trial を 1 つ落とす
    partial = json.loads(json.dumps(full))
    partial["stages"]["1"]["audio_sha256"].pop(full["stages"]["1"]["trial_ids"][0])
    assert scoring.verify_stage_audio(partial, 1)[0] is False

    # (c) slot を 1 つ落とす
    slot = json.loads(json.dumps(full))
    slot["stages"]["1"]["audio_sha256"][full["stages"]["1"]["trial_ids"][0]].pop("X")
    assert scoring.verify_stage_audio(slot, 1)[0] is False

    # (d) trial_ids ごと空
    none = json.loads(json.dumps(full))
    none["stages"]["1"] = {"trial_ids": [], "audio_sha256": {}}
    assert scoring.verify_stage_audio(none, 1)[0] is False


def test_mixed_listener_across_stages_is_blocked(s35) -> None:
    """別人が 1 文脈ずつ答えて PERCEPTIBLE_CANDIDATE を作れないこと。"""
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _correct)
    prep.prepare_stage2(scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1)))
    p2 = _answer(out, 2, _correct)

    clean = p2.read_text(encoding="utf-8")
    for field, value in (("listener_id", "listener-02"), ("session_id", "s2")):
        d = json.loads(clean)          # 毎回きれいな状態から 1 項目だけ壊す
        d[field] = value
        p2.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(prep.S35Stop) as exc:
            scoring.finalize()
        assert field in exc.value.as_dict()["reason"]
        assert not (out / "key_reveal.json").exists()   # 開示まで進まない


# ---------------------------------------------------------------------------
# PR #296 レビュー第 2 巡
# ---------------------------------------------------------------------------
def test_blocked_rerun_does_not_destroy_finalized_record(s35) -> None:
    """開示後の再採点ガードが発火しても、確定済みの記録を潰さない。

    ガードが守ったはずの S4_READY 正本を、ガードの副作用で失わないこと。
    """
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    _answer(out, 1, _correct)
    prep.prepare_stage2(scoring.advancing_from_stage1(scoring.score_stage(sp.STAGE1)))
    _answer(out, 2, _correct)
    assert report.main() == 0
    good_json = (out / "s35_results.json").read_bytes()
    good_md = (out / "S3_5_RECORD.md").read_bytes()

    # 開示後に回答を書き換えて再実行 → BLOCKED だが正本は残る
    _answer(out, 1, _wrong)
    assert report.main() == 3
    assert (out / "s35_results.json").read_bytes() == good_json
    assert (out / "S3_5_RECORD.md").read_bytes() == good_md
    err = json.loads((out / "last_error.json").read_text(encoding="utf-8"))
    assert err["status"] == "BLOCKED"
    assert "再採点は禁止" in err["reason"]


def test_blocked_first_run_still_publishes_blocked_bundle(s35) -> None:
    """確定済み記録が無いときは従来どおり BLOCKED 文書を出す。"""
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    assert report.main() == 3          # 回答がまだ無い
    doc = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    assert doc["status"] == "BLOCKED"
    assert not (out / "last_error.json").exists()


def test_missing_stage2_context_dominates_stage1_answer() -> None:
    """別 context が無い gene は、Stage 1 の正誤に関係なく NOT_EVALUABLE_S35。"""
    for s1 in (True, False, None):
        assert sp.gene_verdict(s1, None, has_stage2_pair=False) is \
            sp.GeneVerdict.NOT_EVALUABLE_S35, s1


def test_single_context_gene_is_not_evaluable_regardless_of_stage1(s35) -> None:
    """実データ経路でも、Stage 1 を外した単一 context gene は NOT_EVALUABLE_S35。"""
    out, _doc = s35(genes={"f0": ["terminal_ri"] * 4,
                           "duration": CONTEXTS, "energy": CONTEXTS, "release": CONTEXTS})
    res = _run_full(out, _wrong)       # f0 も含め全問不正解
    assert res["genes"]["f0"]["verdict"] == sp.GeneVerdict.NOT_EVALUABLE_S35.value
    for g in ("duration", "energy", "release"):
        assert res["genes"][g]["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value


def test_committed_stage2_pair_is_recorded_even_when_not_presented(s35) -> None:
    """出題しなかった Stage 2 pair も記録に残す（記録の自己矛盾を作らない）。"""
    out, _doc = s35()
    res = _run_full(out, _only({"f0", "duration"}), _correct)
    assert report.main() == 0
    doc = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    key = _key(out)
    for gene, v in doc["genes"].items():
        committed = key["plans"][gene]["stage2"]["pair_key"]
        assert v["stage2_committed_pair"] == committed, gene
        assert v["has_stage2_pair"] is True, gene
        if gene in ("energy", "release"):
            assert v["stage2_presented"] is False, gene
            assert v["stage2_presented_pair"] is None, gene
    md = (out / "S3_5_RECORD.md").read_text(encoding="utf-8")
    assert "事前 commit 済み・**出題せず**" in md
    assert res["overall"]["verdict"] == "S4_READY"


@pytest.mark.parametrize("field", ["listener_id", "session_id"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_listener_provenance_is_blocked(s35, field, value) -> None:
    """誰が聴いたか記録に無い session を canonical にしない。"""
    out, _doc = s35()
    prep.prepare_stage1(salt=SALT)
    p1 = _answer(out, 1, _correct)
    d = json.loads(p1.read_text(encoding="utf-8"))
    if value is None:
        d.pop(field)
    else:
        d[field] = value
    p1.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(prep.S35Stop) as exc:
        scoring.score_stage(sp.STAGE1)     # 回答ファイルを読んだ時点で弾く
    assert field in exc.value.as_dict()["reason"]
    assert not (out / "key_reveal.json").exists()
