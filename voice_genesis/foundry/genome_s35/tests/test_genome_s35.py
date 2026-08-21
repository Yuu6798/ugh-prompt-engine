"""test_genome_s35.py — 設計書 v1.0 §24 の 20 項目 + 実素材 integration。

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

SALT = bytes(range(32))          # テスト用の固定 salt（本番は CSPRNG）
CONTEXTS = ["terminal_ri", "terminal_i", "terminal_N", "medial_ri"]


# ---------------------------------------------------------------------------
# 合成 S3 正本
# ---------------------------------------------------------------------------
def _pair_key(ctx: str, i: int) -> str:
    return f"{ctx}|src{i}#1{i}|pjs{i:03d}#2{i}"


def make_s3(tmp: Path, *, genes: Optional[Dict[str, int]] = None,
            overall: str = "PASS", supported_count: int = 4,
            wav_bytes: Optional[Dict[Tuple[str, str], bytes]] = None,
            corrupt_wav: bool = False) -> Tuple[Path, Path, Dict[str, Any]]:
    """`genes` = gene -> SUPPORTED pair 数。WAV も実体を作る。"""
    genes = genes or {g: 6 for g in sp.GENE_CONDITION}
    res_dir = tmp / "genome_s3" / "results"
    wav_dir = res_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    gene_blocks: Dict[str, Any] = {}
    repro: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], str] = {}
    for gene, n in genes.items():
        pairs: Dict[str, Any] = {}
        for i in range(n):
            ctx = CONTEXTS[i % len(CONTEXTS)]
            pk = _pair_key(ctx, i)
            pairs[pk] = {"pair_key": pk, "context_id": ctx, "gene": gene,
                         "verdict": "SUPPORTED"}
        # 非候補も混ぜる（§24-3 で選ばれないことを確認する）
        bad = _pair_key("terminal_ri", 90)
        pairs[bad] = {"pair_key": bad, "context_id": "terminal_ri", "gene": gene,
                      "verdict": "UNSUPPORTED"}
        ne = _pair_key("medial_ri", 91)
        pairs[ne] = {"pair_key": ne, "context_id": "medial_ri", "gene": gene,
                     "verdict": "NOT_EVALUABLE"}
        gene_blocks[gene] = {"verdict": "SUPPORTED", "pairs": pairs}

        for pk in pairs:
            for cond in ("B0", sp.GENE_CONDITION[gene]):
                if (pk, cond) in seen:
                    continue
                payload = (wav_bytes or {}).get((pk, cond)) or f"WAV:{pk}:{cond}".encode()
                p = wav_dir / prep.pair_leaf(pk) / f"{cond}.wav"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                seen[(pk, cond)] = digest
                repro.append({"pair_key": pk, "condition": cond, "gene": gene,
                              "wav_sha256": digest, "sample_sha256": digest})

    doc = {"schema": "voicegenesis-genome-s3/1.1",
           "genes": gene_blocks,
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
    """`s35_prepare` の入出力先を tmp へ差し替えるフィクスチャ。"""
    def _setup(**kw):
        s3_path, wav_dir, doc = make_s3(tmp_path, **kw)
        out = tmp_path / "s35"
        monkeypatch.setattr(prep, "S3_RESULTS", s3_path)
        monkeypatch.setattr(prep, "S3_WAV_DIR", wav_dir)
        monkeypatch.setattr(prep, "RESULTS", out)
        monkeypatch.setattr(prep, "PACK_DIR", out / "abx")
        monkeypatch.setattr(prep, "PACK_AUDIO", out / "abx" / "audio")
        monkeypatch.setattr(prep, "BLIND_MANIFEST", out / "blind_manifest.json")
        monkeypatch.setattr(prep, "PRIVATE_KEY", out / "answer_key.private.json")
        monkeypatch.setattr(prep, "ANSWER_SHEET", out / "abx" / "answer_sheet.template.json")
        monkeypatch.setattr(prep, "LISTENING_UI", out / "abx" / "index.html")
        monkeypatch.setattr(scoring, "RESULTS", out)
        monkeypatch.setattr(scoring, "KEY_REVEAL", out / "key_reveal.json")
        monkeypatch.setattr(scoring, "ANSWERS", out / "answers.json")
        monkeypatch.setattr(report, "RESULTS", out)
        monkeypatch.setattr(report, "JSON_PATH", out / "s35_results.json")
        monkeypatch.setattr(report, "RECORD_PATH", out / "S3_5_RECORD.md")
        return out, doc
    return _setup


def _answer_all(out: Path, mode) -> Path:
    """key を読んで回答ファイルを作る（`mode(trial_id, spec) -> "A"/"B"/"UNSURE"`）。"""
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    answers = {tid: mode(tid, spec) for tid, spec in key["trials"].items()}
    p = out / "answers.json"
    p.write_text(json.dumps({"schema": sp.ANSWERS_SCHEMA, "listener_id": "listener-01",
                             "session_id": "s1", "answers": answers},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _all_correct(_tid, spec):
    return spec["correct"]


def _wrong_n(n: int, gene: Optional[str] = None):
    """`gene` 内で先頭 n trial だけ誤答にする（gene=None なら全体で n 件）。"""
    state = {"n": 0}

    def _m(tid, spec):
        if gene is not None and spec["gene"] != gene:
            return spec["correct"]
        if state["n"] < n:
            state["n"] += 1
            return "B" if spec["correct"] == "A" else "A"
        return spec["correct"]
    return _m


# ---------------------------------------------------------------------------
# 1. S3 PASS 以外は BLOCKED
# ---------------------------------------------------------------------------
def test_01_non_pass_s3_is_blocked(s35) -> None:
    for kw in ({"overall": "FAIL"}, {"supported_count": 1}):
        _out, _doc = s35(**kw)
        with pytest.raises(prep.S35Stop) as exc:
            prep.prepare(salt=SALT)
        assert exc.value.as_dict()["status"] == "BLOCKED"


# ---------------------------------------------------------------------------
# 2. S3 WAV SHA mismatch は BLOCKED
# ---------------------------------------------------------------------------
def test_02_wav_sha_mismatch_is_blocked(s35) -> None:
    _out, _doc = s35(corrupt_wav=True)
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare(salt=SALT)
    d = exc.value.as_dict()
    assert d["status"] == "BLOCKED"
    assert "SHA" in d["reason"] or "欠落" in d["reason"]
    assert "再生成" in d["required_action"]


def test_02b_missing_wav_is_blocked(s35, tmp_path: Path) -> None:
    _out, _doc = s35()
    next(prep.S3_WAV_DIR.rglob("*.wav")).unlink()
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare(salt=SALT)
    assert "欠落" in exc.value.as_dict()["reason"]


# ---------------------------------------------------------------------------
# 3. unsupported pair を選ばない
# ---------------------------------------------------------------------------
def test_03_unsupported_pairs_are_never_selected(s35) -> None:
    out, doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    chosen = {s["pair_key"] for s in key["trials"].values()}
    for gene, blk in doc["genes"].items():
        for pk, p in blk["pairs"].items():
            if p["verdict"] != "SUPPORTED":
                assert pk not in chosen, f"{gene}: 非 SUPPORTED の {pk} が選ばれた"


# ---------------------------------------------------------------------------
# 4/5. 4 distinct pair・>=2 context
# ---------------------------------------------------------------------------
def test_04_05_four_distinct_pairs_and_two_contexts(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    per: Dict[str, Dict[str, set]] = {}
    for spec in key["trials"].values():
        g = per.setdefault(spec["gene"], {"pairs": set(), "ctx": set()})
        g["pairs"].add(spec["pair_key"])
        g["ctx"].add(spec["probe_kind"])
    assert per, "gene が 1 つも無い"
    for gene, g in per.items():
        assert len(g["pairs"]) == sp.PAIRS_PER_GENE, gene
        assert len(g["ctx"]) >= sp.MIN_CONTEXTS, gene


def test_05b_insufficient_candidates_is_not_evaluable(s35) -> None:
    """4 pair / 2 context を作れない gene は `NOT_EVALUABLE_S35`（緩めない）。"""
    out, _doc = s35(genes={"f0": 3, "duration": 6, "energy": 6, "release": 6})
    info = prep.prepare(salt=SALT)
    assert "f0" in info["not_evaluable"]
    assert "f0" not in info["genes"]
    ne = [s for s in info["selections"] if s["gene"] == "f0"][0]
    assert ne["verdict"] == sp.GeneVerdict.NOT_EVALUABLE_S35.value


def test_05c_single_context_is_not_evaluable() -> None:
    cands = [(f"terminal_ri|a#{i}|b#{i}", "terminal_ri") for i in range(6)]
    assert sp.select_pairs(cands, "sha", "f0") == ()


# ---------------------------------------------------------------------------
# 6. selection が effect size 非依存
# ---------------------------------------------------------------------------
def test_06_selection_is_independent_of_effect_size(s35) -> None:
    out_a, doc = s35()
    prep.prepare(salt=SALT)
    key_a = json.loads((out_a / "answer_key.private.json").read_text(encoding="utf-8"))
    chosen_a = {(s["gene"], s["pair_key"]) for s in key_a["trials"].values()}

    # selection_hash の入力に metric は含まれないので、metric を動かしても不変
    for gene, blk in doc["genes"].items():
        for i, p in enumerate(blk["pairs"].values()):
            p["detail"] = {"P3": {"delta": -999.0 * (i + 1)}}
            p["effect_size"] = 999.0 * (i + 1)
    s3_sha = "deadbeef"
    for gene in doc["genes"]:
        cands = [(p["pair_key"], p["context_id"]) for p in doc["genes"][gene]["pairs"].values()
                 if p["verdict"] == "SUPPORTED"]
        before = sp.select_pairs(cands, s3_sha, gene)
        after = sp.select_pairs(list(reversed(cands)), s3_sha, gene)
        assert before == after, f"{gene}: 候補の並び順で選択が変わった"
    assert chosen_a  # 選択自体は成立している


# ---------------------------------------------------------------------------
# 7. 同じ S3 SHA なら同じ pair 選択
# ---------------------------------------------------------------------------
def test_07_same_s3_sha_gives_same_selection() -> None:
    cands = [(_pair_key(CONTEXTS[i % 4], i), CONTEXTS[i % 4]) for i in range(8)]
    a = sp.select_pairs(cands, "sha-A", "f0")
    b = sp.select_pairs(cands, "sha-A", "f0")
    c = sp.select_pairs(cands, "sha-B", "f0")
    assert a == b and len(a) == sp.PAIRS_PER_GENE
    assert a != c or True     # 別 SHA で必ず変わるとは限らないが、同 SHA では必ず同一


# ---------------------------------------------------------------------------
# 8. blind filename に gene 情報なし
# ---------------------------------------------------------------------------
def test_08_blind_filenames_carry_no_gene_information(s35) -> None:
    out, doc = s35()
    prep.prepare(salt=SALT)
    names = [p.name for p in (out / "abx" / "audio").glob("*.wav")]
    assert names
    leak = set(doc["genes"]) | {"B0", "F0", "duration", "energy", "release",
                                "terminal_ri", "terminal_i", "terminal_N", "medial_ri",
                                "R0", "PJS", "pjs"}
    for n in names:
        assert re.fullmatch(r"T\d{3}_[ABX]\.wav", n), n
        for token in leak:
            assert token not in n, f"{n} に {token} が漏れている"
    # blind manifest にも gene / pair_key / 正解を入れない。
    # 部分文字列走査は sha256 hex に "f0" が偶然含まれて誤検出するので、
    # **構造ごと**固定して「宣言した以外のものが無い」ことを示す。
    manifest = json.loads((out / "blind_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"protocol_version", "s3_results_sha256", "trial_ids",
                             "audio_sha256", "key_commitment"}
    assert all(re.fullmatch(r"T\d{3}", t) for t in manifest["trial_ids"])
    assert set(manifest["audio_sha256"]) == set(manifest["trial_ids"])
    for tid, per in manifest["audio_sha256"].items():
        assert set(per) == {"A", "B", "X"}, tid
        assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in per.values()), tid
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["key_commitment"])
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["s3_results_sha256"])
    assert manifest["protocol_version"] == sp.PROTOCOL_VERSION


def test_08b_listening_ui_has_no_answer_key(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    html = (out / "abx" / "index.html").read_text(encoding="utf-8")
    for token in ("correct", "x_side", "a_side", "pair_key", "salt"):
        assert token not in html, f"UI に {token} が埋め込まれている"


# ---------------------------------------------------------------------------
# 9/10. 1 pair につき X=B0 / X=gene 各 1・A/B 配置が反転
# ---------------------------------------------------------------------------
def test_09_10_x_balance_and_ab_inversion(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    per: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for spec in key["trials"].values():
        per.setdefault((spec["gene"], spec["pair_key"]), []).append(spec)
    assert per
    for (gene, pk), specs in per.items():
        assert len(specs) == sp.REPEATS_PER_PAIR, (gene, pk)
        assert sorted(s["x_side"] for s in specs) == [sp.SIDE_B0, sp.SIDE_GENE]
        r1 = [s for s in specs if s["repeat"] == 1][0]
        r2 = [s for s in specs if s["repeat"] == 2][0]
        assert r1["a_side"] != r2["a_side"], (gene, pk, "A/B が反転していない")
        assert r1["b_side"] != r2["b_side"]
        for s in specs:
            assert {s["a_side"], s["b_side"]} == {sp.SIDE_B0, sp.SIDE_GENE}
            assert s["correct"] == ("A" if s["a_side"] == s["x_side"] else "B")


def test_10b_trials_are_shuffled_across_genes(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    order = [key["trials"][t]["gene"] for t in sorted(key["trials"])]
    runs = sum(1 for a, b in zip(order, order[1:]) if a != b)
    assert runs > len(set(order)) - 1, "gene ごとに固まっている（shuffle されていない）"


# ---------------------------------------------------------------------------
# 11/12. commitment 一致・key 改変で INVALID
# ---------------------------------------------------------------------------
def test_11_commitment_matches(s35) -> None:
    out, _doc = s35()
    info = prep.prepare(salt=SALT)
    manifest = json.loads((out / "blind_manifest.json").read_text(encoding="utf-8"))
    key_raw = (out / "answer_key.private.json").read_bytes()
    assert manifest["key_commitment"] == hashlib.sha256(key_raw).hexdigest()
    assert manifest["key_commitment"] == info["key_commitment"]
    # 正規形の bytes = ファイルの bytes
    key = json.loads(key_raw.decode("utf-8"))
    assert prep.canonical_bytes(key) == key_raw

    _answer_all(out, _all_correct)
    res = scoring.run(out / "answers.json")
    assert res["commitment_verified"] is True
    assert (out / "key_reveal.json").exists()


def test_12_tampered_key_yields_invalid(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    _answer_all(out, _all_correct)
    # 回答後に正解を書き換える = commitment が合わなくなる
    kp = out / "answer_key.private.json"
    key = json.loads(kp.read_text(encoding="utf-8"))
    tid = sorted(key["trials"])[0]
    key["trials"][tid]["correct"] = "B" if key["trials"][tid]["correct"] == "A" else "A"
    kp.write_bytes(prep.canonical_bytes(key))

    res = scoring.run(out / "answers.json")
    assert res["commitment_verified"] is False
    assert all(v["verdict"] == sp.GeneVerdict.INVALID.value
               for v in res["genes"].values())
    assert res["overall"]["verdict"] == "S4_NOT_READY"


# ---------------------------------------------------------------------------
# 13/14/15/16. 採点閾値と UNSURE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("correct", "expected"), [
    (8, sp.GeneVerdict.PERCEPTIBLE), (7, sp.GeneVerdict.PERCEPTIBLE),
    (6, sp.GeneVerdict.NOT_ESTABLISHED), (0, sp.GeneVerdict.NOT_ESTABLISHED),
], ids=["13_8of8", "14_7of8", "15_6of8", "15b_0of8"])
def test_13_to_15_gene_gate_threshold(correct: int, expected) -> None:
    assert sp.gene_verdict(correct, 8, 4, 2, commitment_verified=True,
                           audio_verified=True) is expected


def test_16_unsure_counts_as_incorrect(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    _answer_all(out, lambda t, s: "UNSURE")
    res = scoring.run(out / "answers.json")
    for gene, v in res["genes"].items():
        assert v["correct"] == 0, gene
        assert v["unsure"] == sp.TRIALS_PER_GENE, gene
        assert v["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value
    assert res["overall"]["verdict"] == "S4_NOT_READY"


# ---------------------------------------------------------------------------
# 17/18. S4 Gate
# ---------------------------------------------------------------------------
def test_17_two_perceptible_genes_is_s4_ready() -> None:
    assert sp.s4_gate(2) == "S4_READY"
    assert sp.s4_gate(4) == "S4_READY"


def test_18_one_perceptible_gene_is_s4_not_ready() -> None:
    assert sp.s4_gate(1) == "S4_NOT_READY"
    assert sp.s4_gate(0) == "S4_NOT_READY"


def test_17b_end_to_end_gate_from_answers(s35) -> None:
    """2 gene だけ正答させると `S4_READY`、1 gene だけなら `S4_NOT_READY`。"""
    out, _doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    genes = sorted({s["gene"] for s in key["trials"].values()})

    def mode(winners):
        def _m(tid, spec):
            if spec["gene"] in winners:
                return spec["correct"]
            return "B" if spec["correct"] == "A" else "A"
        return _m

    _answer_all(out, mode(set(genes[:2])))
    res = scoring.run(out / "answers.json")
    assert res["overall"]["perceptible_gene_count"] == 2
    assert res["overall"]["verdict"] == "S4_READY"

    _answer_all(out, mode(set(genes[:1])))
    res = scoring.run(out / "answers.json")
    assert res["overall"]["perceptible_gene_count"] == 1
    assert res["overall"]["verdict"] == "S4_NOT_READY"


def test_18b_one_wrong_still_perceptible_two_wrong_is_not(s35) -> None:
    """7/8 は PERCEPTIBLE、6/8 は NOT_ESTABLISHED（実データ経路で確認）。"""
    out, _doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    target = sorted({s["gene"] for s in key["trials"].values()})[0]

    _answer_all(out, _wrong_n(1, target))          # 対象 gene だけ 7/8
    res = scoring.run(out / "answers.json")
    assert res["genes"][target]["correct"] == 7
    assert res["genes"][target]["verdict"] == sp.GeneVerdict.PERCEPTIBLE.value

    _answer_all(out, _wrong_n(2, target))          # 対象 gene だけ 6/8
    res = scoring.run(out / "answers.json")
    assert res["genes"][target]["correct"] == 6
    assert res["genes"][target]["verdict"] == sp.GeneVerdict.NOT_ESTABLISHED.value
    # 他 gene は 8/8 のまま = 閾値が gene 単位であることの確認
    for g, v in res["genes"].items():
        if g != target:
            assert v["correct"] == 8 and v["verdict"] == sp.GeneVerdict.PERCEPTIBLE.value


# ---------------------------------------------------------------------------
# 19. audio byte を変更しない
# ---------------------------------------------------------------------------
def test_19_audio_bytes_are_copied_verbatim(s35) -> None:
    out, doc = s35()
    prep.prepare(salt=SALT)
    key = json.loads((out / "answer_key.private.json").read_text(encoding="utf-8"))
    src_sha = {(r["pair_key"], r["condition"]): r["wav_sha256"]
               for r in doc["reproducibility"]}
    for tid, spec in key["trials"].items():
        for slot in ("A", "B", "X"):
            side = spec["x_side"] if slot == "X" else spec[f"{slot.lower()}_side"]
            cond = "B0" if side == sp.SIDE_B0 else sp.GENE_CONDITION[spec["gene"]]
            got = hashlib.sha256((out / "abx" / "audio" / f"{tid}_{slot}.wav").read_bytes())
            assert got.hexdigest() == src_sha[(spec["pair_key"], cond)], (tid, slot)
    # X は A か B と byte-identical（§7）
    for tid in key["trials"]:
        a = (out / "abx" / "audio" / f"{tid}_A.wav").read_bytes()
        b = (out / "abx" / "audio" / f"{tid}_B.wav").read_bytes()
        x = (out / "abx" / "audio" / f"{tid}_X.wav").read_bytes()
        assert x == a or x == b, tid


def test_19b_copy_sha_verified_before_and_after(s35) -> None:
    """コピー前後で SHA が変われば BLOCKED（正規化・変換の混入検出）。"""
    out, _doc = s35()
    import shutil as _sh
    orig = prep.shutil.copyfile

    def bad_copy(src, dst):
        orig(src, dst)
        Path(dst).write_bytes(Path(dst).read_bytes() + b"\x00")   # 静かに改変

    prep.shutil = type("S", (), {"copyfile": staticmethod(bad_copy),
                                 "rmtree": staticmethod(_sh.rmtree)})()
    try:
        with pytest.raises(prep.S35Stop) as exc:
            prep.prepare(salt=SALT)
        assert "byte copy" in exc.value.as_dict()["reason"]
    finally:
        prep.shutil = _sh


# ---------------------------------------------------------------------------
# 20. WAV を git 管理しない
# ---------------------------------------------------------------------------
def test_20_wavs_and_private_key_are_gitignored() -> None:
    gi = (_HERE / "results" / ".gitignore").read_text(encoding="utf-8")
    for pat in ("abx/", "*.wav", "answer_key.private.json", "answers.json"):
        assert pat in gi, pat
    repo = _HERE.parent.parent.parent
    for probe in ("voice_genesis/foundry/genome_s35/results/abx/audio/T001_A.wav",
                  "voice_genesis/foundry/genome_s35/results/answer_key.private.json"):
        r = subprocess.run(["git", "check-ignore", "-q", probe], cwd=str(repo))
        assert r.returncode == 0, f"{probe} が git 管理外になっていない"
    tracked = subprocess.run(["git", "ls-files", "voice_genesis/foundry/genome_s35"],
                             cwd=str(repo), capture_output=True, text=True).stdout
    assert ".wav" not in tracked
    assert "answer_key.private.json" not in tracked


# ---------------------------------------------------------------------------
# 補助: 凍結定数・停止規則・回答検証
# ---------------------------------------------------------------------------
def test_frozen_constants() -> None:
    assert (sp.PAIRS_PER_GENE, sp.REPEATS_PER_PAIR, sp.TRIALS_PER_GENE) == (4, 2, 8)
    assert (sp.MIN_CONTEXTS, sp.PERCEPTIBLE_CORRECT) == (2, 7)
    assert (sp.MIN_PERCEPTIBLE_GENES_FOR_S4, sp.MAX_REPLAYS_PER_CLIP) == (2, 3)
    assert sp.PAIRS_PER_GENE * sp.REPEATS_PER_PAIR == sp.TRIALS_PER_GENE
    assert sp.SCHEMA == "voicegenesis-genome-s35/1.0"
    assert sp.SELECTION_DOMAIN == "voicegenesis-s35-v1"
    assert {v.value for v in sp.GeneVerdict} == {
        "PERCEPTIBLE", "NOT_ESTABLISHED", "NOT_EVALUABLE_S35", "INVALID"}


def test_gene_condition_matches_s3() -> None:
    """`GENE_CONDITION` は S3 の写し。S3 側と一致していることを検査する。"""
    s3_spec_path = _HERE.parent / "genome_s3" / "s3_spec.py"
    if not s3_spec_path.exists():
        pytest.skip("genome_s3 が無い")
    sys.path.insert(0, str(_HERE.parent / "genome_s3"))
    pytest.importorskip("pyworld")
    import s3_spec  # noqa: PLC0415
    assert {g.value: c for g, c in s3_spec.GENE_CONDITION.items()} == sp.GENE_CONDITION


def test_partial_or_invalid_answers_are_blocked(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    p = _answer_all(out, _all_correct)
    doc = json.loads(p.read_text(encoding="utf-8"))

    for mutate, needle in (
        (lambda d: d["answers"].update({sorted(d["answers"])[0]: ""}), "未回答"),
        (lambda d: d["answers"].update({sorted(d["answers"])[0]: "MAYBE"}), "UNSURE 以外"),
        (lambda d: d["answers"].update({"T999": "A"}), "manifest に無い"),
        (lambda d: d.update({"schema": "wrong"}), "schema"),
    ):
        d = json.loads(json.dumps(doc))
        mutate(d)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(prep.S35Stop) as exc:
            scoring.run(p)
        assert needle in exc.value.as_dict()["reason"], needle
        assert exc.value.as_dict()["status"] == "BLOCKED"


def test_existing_private_key_blocks_regeneration(s35) -> None:
    """既存 session を黙って上書きしない（blind の破壊）。"""
    _out, _doc = s35()
    prep.prepare(salt=SALT)
    with pytest.raises(prep.S35Stop) as exc:
        prep.prepare(salt=SALT)
    assert "private key" in exc.value.as_dict()["reason"]


def test_tampered_pack_audio_is_invalid(s35) -> None:
    """配布後に pack の WAV が変わったら audio_verified=False → INVALID。"""
    out, _doc = s35()
    prep.prepare(salt=SALT)
    _answer_all(out, _all_correct)
    victim = sorted((out / "abx" / "audio").glob("*.wav"))[0]
    victim.write_bytes(victim.read_bytes() + b"\x00")
    res = scoring.run(out / "answers.json")
    assert res["audio_verified"] is False
    assert all(v["verdict"] == sp.GeneVerdict.INVALID.value for v in res["genes"].values())


def test_report_renders_and_serializes(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    _answer_all(out, _all_correct)
    rc = report.main(out / "answers.json")
    assert rc == 0
    res = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    assert res["schema"] == sp.SCHEMA
    assert res["overall"]["verdict"] == "S4_READY"
    assert res["protocol"] == sp.Protocol().as_dict()
    assert res["out_of_scope_observations"]          # §16 記録のみ
    json.dumps(res, allow_nan=False)
    md = (out / "S3_5_RECORD.md").read_text(encoding="utf-8")
    assert "# S3.5 RECORD" in md
    assert "S4_READY" in md
    assert "主張禁止" in md


def test_report_blocked_path(s35) -> None:
    out, _doc = s35()
    prep.prepare(salt=SALT)
    rc = report.main(out / "answers.json")      # 回答ファイルがまだ無い
    assert rc == 3
    res = json.loads((out / "s35_results.json").read_text(encoding="utf-8"))
    assert res["status"] == "BLOCKED"
    assert "BLOCKED" in (out / "S3_5_RECORD.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# integration（実 S3 正本があるときのみ）
# ---------------------------------------------------------------------------
def _real_s3_ready() -> bool:
    if not prep.S3_RESULTS.exists() or not prep.S3_WAV_DIR.exists():
        return False
    return any(prep.S3_WAV_DIR.rglob("*.wav"))


@pytest.mark.skipif(not _real_s3_ready(), reason="S3 実正本 / WAV が無い")
def test_integration_real_s3_selection_is_deterministic() -> None:
    """実 S3 正本で pair 選択が決定論・§6 条件を満たすことを確認する（副作用なし）。"""
    res, s3_sha = prep.load_s3_results()
    prep.gate_s3(res)
    genes = prep.candidate_genes(res)
    assert genes
    first = {g: prep.selection_for_gene(res, s3_sha, g) for g in genes}
    second = {g: prep.selection_for_gene(res, s3_sha, g) for g in genes}
    assert first == second
    for g, sel in first.items():
        if not sel.get("selected"):
            continue
        assert sel["distinct_pairs"] == sp.PAIRS_PER_GENE, g
        assert len(sel["contexts"]) >= sp.MIN_CONTEXTS, g
        sup = {pk for pk, _ in prep.candidate_pairs(res, g)}
        for e in sel["selected"]:
            assert e["pair_key"] in sup, g
