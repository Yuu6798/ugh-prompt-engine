"""test_run9_founder_genome_issuance.py — RUN9-BIRTH-PREP-1 §A: 永続
founder genome 文書発行（`run9_schema.issue_founder_genome_document()`）の
最低テスト。

音声処理・実学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402

CONTRACT_PATH = _RUN_DIR / "RUN9_CONTRACT.yaml"
DOMAIN_DRAFT_PATH = _RUN_DIR / "domains" / "identity_domain_run9_v1.json"
RIGHTS_MANIFEST_PATH = _RUN_DIR / "inputs" / "rights_manifest.json"

EXPECTED_GENOME_IDS = {"R9F-01": "66f420672a154283", "R9F-02": "63f4b8f24b827cd4"}


@pytest.fixture(scope="module")
def domain() -> m.Run9IdentityDomain:
    return m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)


@pytest.fixture()
def rights_manifest() -> Dict[str, Any]:
    return json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract_raw() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 再生成同一性: repo 内 founders/*.json のバイト == 関数出力（両 founder）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_repo_founder_genome_file_matches_issue_function_output(
    founder_id: str, domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    """repo 内 `founders/R9F-0x_genome.json` のバイト列が
    `issue_founder_genome_document()` の出力バイトそのものであることの
    再生成同一性確認（手書き・別直列化ではないことの担保）。"""
    path = m.founder_genome_document_path(founder_id)
    assert path.exists(), f"{path} is not committed"
    on_disk = path.read_bytes()
    regenerated = m.issue_founder_genome_document(
        founder_id, domain=domain, rights_manifest=rights_manifest
    )
    assert on_disk == regenerated


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_issue_founder_genome_document_is_deterministic(
    founder_id: str, domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    a = m.issue_founder_genome_document(founder_id, domain=domain, rights_manifest=rights_manifest)
    b = m.issue_founder_genome_document(founder_id, domain=domain, rights_manifest=rights_manifest)
    assert a == b


def test_issue_founder_genome_document_serialization_is_frozen_form(
    domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    """凍結直列化規約: `json.dumps(genome.to_dict(), ensure_ascii=False,
    indent=2, sort_keys=True) + "\\n"`（UTF-8）と厳密一致する。"""
    genome = m.build_founder(domain, "R9F-01", rights_manifest=rights_manifest)
    expected = (
        json.dumps(genome.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actual = m.issue_founder_genome_document(
        "R9F-01", domain=domain, rights_manifest=rights_manifest
    )
    assert actual == expected
    # UTF-8 でデコードでき、末尾が改行1つであることも直接確認する。
    text = actual.decode("utf-8")
    assert text.endswith("\n") and not text.endswith("\n\n")


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_issue_founder_genome_document_output_json_matches_to_dict(
    founder_id: str, domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    data = json.loads(
        m.issue_founder_genome_document(founder_id, domain=domain, rights_manifest=rights_manifest)
    )
    genome = m.build_founder(domain, founder_id, rights_manifest=rights_manifest)
    assert data == genome.to_dict()


# ---------------------------------------------------------------------------
# founder_genome_from_dict() 通過 + genome_id 厳密一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_repo_founder_genome_document_passes_founder_genome_from_dict(
    founder_id: str, domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    path = m.founder_genome_document_path(founder_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    genome = m.founder_genome_from_dict(data, domain=domain, rights_manifest=rights_manifest)
    assert genome.genome_id == EXPECTED_GENOME_IDS[founder_id]
    assert genome.voice_id == founder_id


def test_founder_genome_ids_are_distinct_between_founders() -> None:
    assert EXPECTED_GENOME_IDS["R9F-01"] != EXPECTED_GENOME_IDS["R9F-02"]


# ---------------------------------------------------------------------------
# 契約照合: founder_genome_shas の pin 値 == 実ファイル raw sha256
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_contract_founder_genome_sha_matches_file_raw_bytes(
    founder_id: str, contract_raw: Dict[str, Any]
) -> None:
    field = contract_raw["founder_genome_shas"][founder_id]
    assert field["status"] == "PINNED"
    path = m.founder_genome_document_path(founder_id)
    assert field["value"] == m.compute_file_sha256(path)


def test_contract_founder_genome_shas_distinct_when_pinned(contract_raw: Dict[str, Any]) -> None:
    r1 = contract_raw["founder_genome_shas"]["R9F-01"]["value"]
    r2 = contract_raw["founder_genome_shas"]["R9F-02"]["value"]
    assert r1 != r2


def test_loaded_contract_founder_genome_shas_are_pinned(contract_raw: Dict[str, Any]) -> None:
    """`load_run9_contract()` を経由しても founder_genome_shas の2欄が
    PINNED として受理される（値整形式検証込みの load 経路での確認）。"""
    contract = m.load_run9_contract(contract_raw)
    for founder_id in m.CONTRACT_FOUNDER_IDS:
        assert m._is_field_pinned(contract.founder_genome_sha(founder_id))  # noqa: SLF001


# ---------------------------------------------------------------------------
# gate_state() は引き続き BLOCKED（誤 READY 化防止の回帰）
# ---------------------------------------------------------------------------


def test_gate_state_still_blocked_after_founder_genome_shas_pinned() -> None:
    """founder_genome_shas が両 founder とも PINNED になっても、dataset/
    config/learning-recipe 等 VG-L0 ハーネス関連欄が PENDING のままである
    限り gate_state() は "BLOCKED" のまま（部分的な pin 進展だけでは READY
    へ到達しないことの機械証明——誤 READY 化防止の回帰テスト）。"""
    contract = m.load_run9_contract_from_yaml_path(CONTRACT_PATH)
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# fail-closed 継承: 取消 manifest / pending 形態 / anchor 不一致 manifest
# → issue_founder_genome_document() が Run9ValidationError
# ---------------------------------------------------------------------------


def _pending_rights_manifest(rights_manifest: Dict[str, Any]) -> Dict[str, Any]:
    tampered = copy.deepcopy(rights_manifest)
    layer = tampered["voice_identity_rights"]
    layer["attestation"] = {
        "attested": False,
        "attested_by": None,
        "attested_at": None,
        "statement": None,
    }
    layer["rights_class"] = "PENDING_USER_ATTESTATION"
    layer["consent_status"] = "PENDING_USER_ATTESTATION"
    layer["usage_grants"] = {
        "run9_identity_anchor": "not_granted",
        "raw_audio_publication": "not_granted",
        "model_general_distribution": "not_granted",
    }
    return tampered


def _revoked_anchor_grant_rights_manifest(rights_manifest: Dict[str, Any]) -> Dict[str, Any]:
    tampered = copy.deepcopy(rights_manifest)
    tampered["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] = "not_granted"
    return tampered


def test_issue_founder_genome_document_rejects_pending_attestation(
    domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    pending = _pending_rights_manifest(rights_manifest)
    with pytest.raises(m.Run9ValidationError, match="attested"):
        m.issue_founder_genome_document("R9F-01", domain=domain, rights_manifest=pending)


def test_issue_founder_genome_document_rejects_revoked_anchor_grant(
    domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    revoked = _revoked_anchor_grant_rights_manifest(rights_manifest)
    with pytest.raises(m.Run9ValidationError, match="run9_identity_anchor"):
        m.issue_founder_genome_document("R9F-01", domain=domain, rights_manifest=revoked)


def test_issue_founder_genome_document_rejects_anchor_hash_mismatch(
    domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    mismatched = copy.deepcopy(rights_manifest)
    mismatched["voice_identity_rights"]["entries"][0]["duration_sec"] = 999.0
    with pytest.raises(m.Run9ValidationError, match="anchor_hashes"):
        m.issue_founder_genome_document("R9F-01", domain=domain, rights_manifest=mismatched)


def test_issue_founder_genome_document_requires_rights_manifest_keyword(
    domain: m.Run9IdentityDomain,
) -> None:
    with pytest.raises(TypeError):
        m.issue_founder_genome_document("R9F-01", domain=domain)  # type: ignore[call-arg]


def test_issue_founder_genome_document_rejects_unknown_founder_id(
    domain: m.Run9IdentityDomain, rights_manifest: Dict[str, Any]
) -> None:
    with pytest.raises(m.Run9ValidationError):
        m.issue_founder_genome_document("R9F-99", domain=domain, rights_manifest=rights_manifest)


def test_founder_genome_document_path_rejects_unknown_founder_id() -> None:
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_document_path("R9F-99")
