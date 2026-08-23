"""test_d5_claim_strength.py — `debt/d5_claim_strength.yaml` の形状テスト（VG-DEBT-005）。

台帳は run5-7 の 83 claims（因果・効果・比較・裁定・撤回の主張文）へ claim 単位で
`causal_evidence_strength` を付与した記録ファイルであり、
`voice_genesis/foundry/tests/test_debt_ledger_shape.py` の流儀（人手編集ファイルは
構造のみ機械強制し、値の内容は検証しない）を踏襲する。

- schema / debt_ref の pin
- claim_id の一意性・R5/R6/R7 プレフィクス
- causal_evidence_strength は 4 値のみ・旧語彙 "C0"〜"C3" が値として現れない
- claim_class は 5 語彙のみ
- 必須キーの充足
- source.file が実在する
- superseded_by / corrects の参照先 claim_id が実在する
- 件数 = run5:26, run6:27, run7:30, 計 83
- strong ⇒ claim_class == mechanical_verification（裁定1「strong は機械検証のみ」の機械強制）

値そのもの（claim_text の逐語性・rationale の妥当性等）はここでは検証しない
（`s4_record_2026-08-19.md` / `s5_record_2026-08-20.md` / `s6_record_2026-08-20.md`
が記述内容の正本）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

DEBT_DIR = Path(__file__).resolve().parent.parent / "debt"
LEDGER_PATH = DEBT_DIR / "d5_claim_strength.yaml"
REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED_TOP_LEVEL_KEYS = {"schema", "debt_ref", "claims"}

REQUIRED_CLAIM_KEYS = {
    "claim_id", "source", "claim_text", "source_verdict", "claim_class",
    "intervention", "baseline", "causal_evidence_strength", "rationale",
}
REQUIRED_SOURCE_KEYS = {"file", "section", "approx_line"}

VALID_STRENGTH = {"strong", "moderate", "suggestive", "descriptive"}
VALID_CLASS = {
    "intervention_effect", "mechanical_verification", "observation",
    "adjudication", "process",
}
CLAIM_ID_RE = re.compile(r"^R[567]-C\d+$")
FORBIDDEN_C0_C3 = {"C0", "C1", "C2", "C3"}

EXPECTED_COUNTS = {"R5": 26, "R6": 27, "R7": 30}


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    assert LEDGER_PATH.exists(), f"d5_claim_strength.yaml not found: {LEDGER_PATH}"
    with LEDGER_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _claims(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    claims = ledger["claims"]
    assert isinstance(claims, list)
    return claims


# --- トップレベル形状 ----------------------------------------------------


def test_ledger_is_valid_mapping(ledger: Dict[str, Any]) -> None:
    assert isinstance(ledger, dict)


def test_top_level_required_keys_present(ledger: Dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - ledger.keys()
    assert not missing, f"missing top-level keys: {sorted(missing)}"


def test_schema_is_pinned_version(ledger: Dict[str, Any]) -> None:
    assert ledger["schema"] == "vg-d5-claim-strength/0.1"


def test_debt_ref_is_vg_debt_005(ledger: Dict[str, Any]) -> None:
    assert ledger["debt_ref"] == "VG-DEBT-005"


def test_claims_is_nonempty_list(ledger: Dict[str, Any]) -> None:
    assert len(_claims(ledger)) > 0


# --- claim 単位の形状 -----------------------------------------------------


def test_every_claim_has_required_keys(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        assert isinstance(claim, dict)
        missing = REQUIRED_CLAIM_KEYS - claim.keys()
        assert not missing, f"claim {claim.get('claim_id', '<unknown>')} missing keys: {sorted(missing)}"


def test_every_claim_source_has_required_keys(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        source = claim["source"]
        assert isinstance(source, dict)
        missing = REQUIRED_SOURCE_KEYS - source.keys()
        assert not missing, f"claim {claim['claim_id']} source missing keys: {sorted(missing)}"


def test_claim_id_matches_run_prefix_pattern(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        cid = claim["claim_id"]
        assert CLAIM_ID_RE.match(cid), f"claim_id {cid!r} does not match ^R[567]-C\\d+$"


def test_claim_id_is_unique(ledger: Dict[str, Any]) -> None:
    ids = [c["claim_id"] for c in _claims(ledger)]
    assert len(ids) == len(set(ids)), f"duplicate claim_id(s): {sorted(set(x for x in ids if ids.count(x) > 1))}"


def test_causal_evidence_strength_uses_only_valid_vocabulary(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        assert claim["causal_evidence_strength"] in VALID_STRENGTH, (
            f"claim {claim['claim_id']} has invalid causal_evidence_strength "
            f"{claim['causal_evidence_strength']!r}; must be one of {sorted(VALID_STRENGTH)}"
        )


def test_c0_c3_symbols_do_not_appear_as_strength_values(ledger: Dict[str, Any]) -> None:
    """v1.1 裁定1: 旧 C0〜C3 記号は `causal_evidence_strength` の値として禁止
    （`debt_adjudication_v1.1.yaml` の `vocabulary.c0_c3_symbols: prohibited`）。"""
    for claim in _claims(ledger):
        assert claim["causal_evidence_strength"] not in FORBIDDEN_C0_C3


def test_claim_class_uses_only_valid_vocabulary(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        assert claim["claim_class"] in VALID_CLASS, (
            f"claim {claim['claim_id']} has invalid claim_class {claim['claim_class']!r}; "
            f"must be one of {sorted(VALID_CLASS)}"
        )


def test_strong_claims_are_mechanical_verification(ledger: Dict[str, Any]) -> None:
    """裁定1（strong = 機械検証のみ）の機械強制。"""
    for claim in _claims(ledger):
        if claim["causal_evidence_strength"] == "strong":
            assert claim["claim_class"] == "mechanical_verification", (
                f"claim {claim['claim_id']} is strong but claim_class is "
                f"{claim['claim_class']!r}, not mechanical_verification"
            )


def test_source_file_exists(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        rel = claim["source"]["file"]
        path = REPO_ROOT / rel
        assert path.exists(), f"claim {claim['claim_id']}: source.file {rel!r} does not exist"


def test_superseded_by_references_exist(ledger: Dict[str, Any]) -> None:
    ids = {c["claim_id"] for c in _claims(ledger)}
    for claim in _claims(ledger):
        refs = claim.get("superseded_by")
        if refs is None:
            continue
        assert isinstance(refs, list) and refs
        for ref in refs:
            assert ref in ids, f"claim {claim['claim_id']} superseded_by references unknown {ref!r}"


def test_corrects_references_exist(ledger: Dict[str, Any]) -> None:
    ids = {c["claim_id"] for c in _claims(ledger)}
    for claim in _claims(ledger):
        refs = claim.get("corrects")
        if refs is None:
            continue
        assert isinstance(refs, list) and refs
        for ref in refs:
            assert ref in ids, f"claim {claim['claim_id']} corrects references unknown {ref!r}"


def test_source_verdict_is_verbatim_string_or_none(ledger: Dict[str, Any]) -> None:
    for claim in _claims(ledger):
        verdict = claim["source_verdict"]
        assert isinstance(verdict, str) and verdict, f"claim {claim['claim_id']}: source_verdict must be a non-empty string ('none' when absent)"


# --- 件数 ------------------------------------------------------------------


def test_claim_counts_per_run_and_total(ledger: Dict[str, Any]) -> None:
    claims = _claims(ledger)
    assert len(claims) == 83, f"total claims {len(claims)} != 83"
    per_run: Dict[str, int] = {"R5": 0, "R6": 0, "R7": 0}
    for claim in claims:
        prefix = claim["claim_id"].split("-", 1)[0]
        assert prefix in per_run, f"unexpected claim_id prefix in {claim['claim_id']!r}"
        per_run[prefix] += 1
    assert per_run == EXPECTED_COUNTS, per_run


def test_counts_block_matches_actual_claims(ledger: Dict[str, Any]) -> None:
    """`counts` ブロックは記述用の要約であり、実データ（`claims`）とズレたら
    台帳自体が矛盾している——数え直して機械検査する。"""
    claims = _claims(ledger)
    counts = ledger["counts"]
    assert counts["total"] == len(claims)
    per_run = {"run5": 0, "run6": 0, "run7": 0}
    per_strength = {"strong": 0, "moderate": 0, "suggestive": 0, "descriptive": 0}
    for claim in claims:
        prefix = claim["claim_id"].split("-", 1)[0].lower()
        per_run[f"run{prefix[1]}"] += 1
        per_strength[claim["causal_evidence_strength"]] += 1
    assert counts["run5"] == per_run["run5"]
    assert counts["run6"] == per_run["run6"]
    assert counts["run7"] == per_run["run7"]
    for key in per_strength:
        assert counts[key] == per_strength[key], (key, counts[key], per_strength[key])
