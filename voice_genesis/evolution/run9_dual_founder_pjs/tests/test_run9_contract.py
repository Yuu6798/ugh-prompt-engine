"""test_run9_contract.py — RUN9 Phase 0 スキャフォールドの最低テスト
（DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §27 の静的検証可能
サブセット）。

各テストの docstring / 名前に §27 の項目番号を対応づける。音声処理・実
学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
import inspect
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
DESIGN_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md"
# 現行 design_revision (0.3) の差分メモ。design_revision_doc_sha256 が
# pin する対象。
REVISION_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.3.md"
# rev 0.2 文書は無改変のまま存続する（design_revision 系譜の1件）。
REVISION_0_2_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.2.md"
POR_ADJUDICATION_PATH = _RUN_DIR / "POR_CONCEPT_ADJUDICATION_20260824.txt"
POR_UPLOAD_SOURCE_PATH = Path(
    "/root/.claude/uploads/e505c1c2-c4ad-588b-a1b2-258051a522de/"
    "4cdd727c-RUN9_v0.2_PoR_Concept_Adjudication_20260824.txt"
)
AF0_ANCHOR_MANIFEST_PATH = _RUN_DIR / "inputs" / "af0_anchor_manifest.json"
RIGHTS_MANIFEST_PATH = _RUN_DIR / "inputs" / "rights_manifest.json"
BACKBONE_BUNDLE_PATH = _RUN_DIR / "inputs" / "backbone_runtime_bundle.json"
_FOUNDRY_DIR = _RUN_DIR.parent.parent / "foundry"
AF0_SPEC_PATH = _FOUNDRY_DIR / "artificial_founder" / "founder_specs" / "AF0.json"
AF0_FOUNDER_MANIFEST_PATH = _FOUNDRY_DIR / "artificial_founder" / "results" / "AF0" / "founder_manifest.json"


# ---------------------------------------------------------------------------
# 共有 fixture: pinned な合成 domain（64hex ダミー値）
# ---------------------------------------------------------------------------


def _pinned_fixture_domain() -> m.Run9IdentityDomain:
    return m.build_run9_identity_domain(
        anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        metric_space_sha="d" * 64,
    )


@pytest.fixture()
def pinned_domain() -> m.Run9IdentityDomain:
    return _pinned_fixture_domain()


@pytest.fixture(scope="module")
def contract_raw() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> m.Run9RunContract:
    return m.load_run9_contract_from_yaml_path(CONTRACT_PATH)


@pytest.fixture(scope="module")
def domain_draft_raw() -> Dict[str, Any]:
    return json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# item 1/2: Run Contract required fields / unknown fields fail-closed
# ---------------------------------------------------------------------------


def test_item01_run_contract_required_fields_complete(contract_raw: Dict[str, Any]) -> None:
    """item 1: RUN9_CONTRACT.yaml が必須欄を全て持つ。"""
    contract = m.load_run9_contract(contract_raw)
    for name in m.CONTRACT_PIN_FIELDS:
        assert name in contract.raw
    assert "founder_genome_shas" in contract.raw
    for founder_id in m.CONTRACT_FOUNDER_IDS:
        assert founder_id in contract.raw["founder_genome_shas"]


def test_item02_unknown_contract_fields_fail_closed(contract_raw: Dict[str, Any]) -> None:
    """item 2: 未知トップレベルキーを混入した fixture は fail-closed で拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["unexpected_extra_field"] = "should not be allowed"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_item02_unknown_pin_field_subkeys_fail_closed(contract_raw: Dict[str, Any]) -> None:
    """item 2 補足: pin 欄内部の未知サブキーも fail-closed で拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["backbone_checkpoint_sha"]["unexpected_subkey"] = "x"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_item02_missing_required_contract_field_fail_closed(contract_raw: Dict[str, Any]) -> None:
    """item 2 補足: 必須欄の欠落も fail-closed で拒否される（デフォルト補完なし）。"""
    tampered = copy.deepcopy(contract_raw)
    del tampered["education_technique_lesson_manifest_sha"]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


# ---------------------------------------------------------------------------
# item 8: identity domain anchor order fixed
# ---------------------------------------------------------------------------


def test_item08_anchor_order_is_fixed(pinned_domain: m.Run9IdentityDomain) -> None:
    """item 8: anchor_order は (af0, ritsu, user) に固定される。"""
    assert pinned_domain.anchor_order == ("af0", "ritsu", "user")
    assert m.RUN9_ANCHOR_ORDER == ("af0", "ritsu", "user")


def test_item08_anchor_order_reordering_rejected() -> None:
    """item 8: 別順序で宣言された domain document は ValueError で拒否される
    （anchor_order は並べ替え不可の不変条件）。"""
    data = {
        "schema": m.SCHEMA_IDENTITY_DOMAIN,
        "domain_id": m.RUN9_DOMAIN_ID,
        "anchor_order": ["ritsu", "af0", "user"],
        "anchor_hashes": {"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        "excluded_teacher_identities": ["pjs"],
        "coordinate_precision": 6,
        "normalization": "largest-component-residual",
        "metric_space_sha": "d" * 64,
    }
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(data)


# ---------------------------------------------------------------------------
# item 9: coords non-negative and sum exactly 1.000000
# ---------------------------------------------------------------------------


def test_item09_coords_sum_to_one_and_nonnegative() -> None:
    coords = m.normalize_run9_coords(0.6, 0.3, 0.1)
    assert coords.af0 >= 0.0
    assert coords.ritsu >= 0.0
    assert coords.user >= 0.0
    assert round(coords.af0 + coords.ritsu + coords.user, 6) == 1.000000


def test_item09_negative_raw_coord_is_clamped_not_rejected() -> None:
    """normalize_run9_coords() は負の生値を0へクランプする射影関数（VG-E0
    simplex.normalize() と同じ契約）。既に正規化済みの負値は
    _validate_run9_coords_value() が拒否する — 下の負例テストで確認する。"""
    coords = m.normalize_run9_coords(-0.1, 0.5, 0.6)
    assert coords.af0 >= 0.0
    assert round(coords.af0 + coords.ritsu + coords.user, 6) == 1.000000


def test_item09_already_normalized_negative_coord_rejected() -> None:
    """item 9 負例: 合計不一致・負値を持つ「既に正規化済み」の fixture は
    `_validate_run9_coords_value()` で拒否される。"""
    bad_negative = m.Run9Coords(af0=-0.1, ritsu=0.5, user=0.6)
    with pytest.raises(m.Run9ValidationError):
        m._validate_run9_coords_value(bad_negative)


def test_item09_sum_mismatch_rejected() -> None:
    """item 9 負例: 合計が1.000000でない fixture は拒否される。"""
    bad_sum = m.Run9Coords(af0=0.5, ritsu=0.3, user=0.3)
    with pytest.raises(m.Run9ValidationError):
        m._validate_run9_coords_value(bad_sum)


# ---------------------------------------------------------------------------
# item 10: PJS coordinate is structurally impossible
# ---------------------------------------------------------------------------


def test_item10_pjs_key_in_anchor_hashes_rejected() -> None:
    with pytest.raises(m.Run9ValidationError):
        m.build_run9_identity_domain(
            anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "pjs": "c" * 64},
            metric_space_sha="d" * 64,
        )


def test_item10_pjs_key_in_anchor_order_document_rejected() -> None:
    data = {
        "schema": m.SCHEMA_IDENTITY_DOMAIN,
        "domain_id": m.RUN9_DOMAIN_ID,
        "anchor_order": ["af0", "pjs", "user"],
        "anchor_hashes": {"af0": "a" * 64, "pjs": "b" * 64, "user": "c" * 64},
        "excluded_teacher_identities": ["pjs"],
        "coordinate_precision": 6,
        "normalization": "largest-component-residual",
        "metric_space_sha": "d" * 64,
    }
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(data)


def test_item10_pjs_key_in_coords_document_rejected() -> None:
    """coords に pjs キーを持つ genome document は構造的に ValueError
    （Run9Coords 自体が af0/ritsu/user の3フィールドしか持てないため、
    build_founder() 経由では原理的に到達不能 — from_dict 読込経路で検証する）。"""
    domain = _pinned_fixture_domain()
    good = m.build_founder(domain, "R9F-01").to_dict()
    tampered = copy.deepcopy(good)
    tampered["coords"] = {"af0": 0.6, "ritsu": 0.3, "pjs": 0.1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(tampered, domain=domain)


# ---------------------------------------------------------------------------
# item 11/12: R9F-01 / R9F-02 weights exactly frozen
# ---------------------------------------------------------------------------


def test_item11_r9f01_weights_exactly_0p6_0p3_0p1(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-01")
    assert (g.coords.af0, g.coords.ritsu, g.coords.user) == (0.6, 0.3, 0.1)
    assert g.profile_label == "AF0_DOMINANT"


def test_item12_r9f02_weights_exactly_0p1_0p3_0p6(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-02")
    assert (g.coords.af0, g.coords.ritsu, g.coords.user) == (0.1, 0.3, 0.6)
    assert g.profile_label == "USER_DOMINANT"


# ---------------------------------------------------------------------------
# item 13: shared performance seed is identical
# ---------------------------------------------------------------------------


def test_item13_shared_performance_seed_identical(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01")
    g2 = m.build_founder(pinned_domain, "R9F-02")
    assert g1.performance_seed == g2.performance_seed == 909001 == m.SHARED_PERFORMANCE_SEED


# ---------------------------------------------------------------------------
# item 14: TRI_CROSSOVER deterministic Genome ID
# ---------------------------------------------------------------------------


def test_item14_genome_id_deterministic_same_input(pinned_domain: m.Run9IdentityDomain) -> None:
    """item 14: 同一 domain + 同一 founder_id → genome_id バイト一致
    （2回呼び出し）。"""
    a = m.build_founder(pinned_domain, "R9F-01")
    b = m.build_founder(pinned_domain, "R9F-01")
    assert a.genome_id == b.genome_id


def test_item14_genome_id_stable_through_canonical_json_roundtrip(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """item 14 補足: 正規形 JSON 経由の再構築（to_dict -> json.dumps ->
    json.loads -> founder_genome_from_dict）でも genome_id が一致する。"""
    original = m.build_founder(pinned_domain, "R9F-01")
    text = json.dumps(original.to_dict(), sort_keys=True)
    reconstructed = m.founder_genome_from_dict(json.loads(text), domain=pinned_domain)
    assert reconstructed.genome_id == original.genome_id


# ---------------------------------------------------------------------------
# item 15: Founder IDs are distinct
# ---------------------------------------------------------------------------


def test_item15_founder_genome_ids_are_distinct(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01")
    g2 = m.build_founder(pinned_domain, "R9F-02")
    assert g1.genome_id != g2.genome_id
    assert g1.voice_id != g2.voice_id


# ---------------------------------------------------------------------------
# item 16: default skill state has no inherited PJS lesson
# ---------------------------------------------------------------------------


def test_item16_skill_state_is_default_neutral(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01")
    g2 = m.build_founder(pinned_domain, "R9F-02")
    assert g1.skill_state == "DEFAULT_NEUTRAL"
    assert g2.skill_state == "DEFAULT_NEUTRAL"


def test_item16_genome_dict_has_no_pjs_lesson_derived_field(pinned_domain: m.Run9IdentityDomain) -> None:
    """item 16 補足: genome dict のフィールド集合に PJS lesson 由来の
    キー（lesson_id / teacher_reference 等）が存在しない（構造的保証 —
    Run9FounderGenome のフィールド定義そのものに含まれない）。"""
    g = m.build_founder(pinned_domain, "R9F-01").to_dict()
    forbidden_substrings = ("lesson", "teacher", "pjs")
    for key in g:
        lowered = key.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, f"genome dict key {key!r} leaks lesson/teacher vocabulary"


# ---------------------------------------------------------------------------
# item 22: no post-listening coordinate adjustment API
# ---------------------------------------------------------------------------


def test_item22_no_public_weight_adjustment_api() -> None:
    """item 22: モジュールの公開名を走査し、weights を外部注入できる公開
    callable が build 系に存在しないことを検査する。`build_founder` は
    `(domain, founder_id)` のみを受け付け、weights/coords を直接渡す公開
    経路は存在しない（凍結重みは `_FOUNDER_TABLE` — 先頭アンダースコアの
    非公開データ — からのみ引かれる）。"""
    public_names = [name for name in dir(m) if not name.startswith("_")]
    build_like = [name for name in public_names if "build" in name.lower() or "founder" in name.lower()]
    assert "build_founder" in build_like

    for name in build_like:
        obj = getattr(m, name)
        if not inspect.isfunction(obj):
            continue
        params = set(inspect.signature(obj).parameters.keys())
        forbidden_params = {"weights", "weight", "w_af0", "w_ritsu", "w_user", "coords"}
        leaked = params & forbidden_params
        assert not leaked, f"public callable {name!r} exposes a coordinate/weight injection param: {leaked}"


def test_item22_build_founder_signature_is_domain_and_founder_id_only() -> None:
    """item 22 直接検査: `build_founder` のシグネチャが (domain, founder_id)
    の2引数のみであること。"""
    params = list(inspect.signature(m.build_founder).parameters.keys())
    assert params == ["domain", "founder_id"]


def test_item22_unknown_founder_id_rejected(pinned_domain: m.Run9IdentityDomain) -> None:
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(pinned_domain, "R9F-03")


# ---------------------------------------------------------------------------
# item 40: no TotalScore field in evaluation/result schema
# ---------------------------------------------------------------------------


def test_item40_contract_has_no_total_score_field(contract_raw: Dict[str, Any]) -> None:
    canonical = json.dumps(contract_raw).lower().replace("_", "")
    assert "totalscore" not in canonical


def test_item40_genome_dict_has_no_total_score_field(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-01").to_dict()
    canonical = json.dumps(g).lower().replace("_", "")
    assert "totalscore" not in canonical


def test_item40_reject_total_score_vocabulary_helper_detects_violation() -> None:
    with pytest.raises(m.Run9ValidationError):
        m._reject_total_score_vocabulary(context="test", names=["total_score"])
    with pytest.raises(m.Run9ValidationError):
        m._reject_total_score_vocabulary(context="test", names=["TotalScore"])
    # 正常系: 通常の軸名は拒否されない。
    m._reject_total_score_vocabulary(context="test", names=["pitch_gain", "identity_delta"])


# ---------------------------------------------------------------------------
# item 49: incomplete Hard Gate set -> BLOCKED
# ---------------------------------------------------------------------------


def test_item49_current_contract_gate_state_is_blocked(contract: m.Run9RunContract) -> None:
    """item 49: 現在の RUN9_CONTRACT.yaml は gate_state() == "BLOCKED"
    （pin 不完全のため）。これが『現状は本学習開始禁止』の機械証明。"""
    assert m.gate_state(contract) == "BLOCKED"


def _synthetic_pin_value(name: str) -> str:
    """CONTRACT_PIN_FIELDS の各欄名に応じた valid-shape なダミー PINNED
    value を返す（Fix 7: `repository_commit_sha` だけ40hex SHA-1形式）。"""
    if name == "repository_commit_sha":
        return "a" * 40
    return "a" * 64


def _fully_pinned_synthetic_contract(contract_raw: Dict[str, Any]) -> Dict[str, Any]:
    fully_pinned = copy.deepcopy(contract_raw)
    for name in m.CONTRACT_PIN_FIELDS:
        if name in m.CONTRACT_POST_RUN_PIN_FIELDS:
            continue
        fully_pinned[name] = {
            "value": _synthetic_pin_value(name),
            "status": "PINNED",
            "source": "synthetic-fixture",
        }
    # Fix 8: 両 founder が PINNED のとき value（genome 文書ファイルの
    # sha256）は相異が必須のため、R9F-01/R9F-02 で異なる値を使う。
    # Fix 15: founder_genome_shas.value は永続 genome 文書ファイルの64hex
    # sha256（16hex genome_id ではない）。
    fully_pinned["founder_genome_shas"]["R9F-01"] = {
        "value": "a" * 64,
        "status": "PINNED",
        "source": "synthetic-fixture",
    }
    fully_pinned["founder_genome_shas"]["R9F-02"] = {
        "value": "b" * 64,
        "status": "PINNED",
        "source": "synthetic-fixture",
    }
    return fully_pinned


def test_item49_fully_pinned_synthetic_contract_is_ready(contract_raw: Dict[str, Any]) -> None:
    """item 49 対照実験: pre-run 欄を全て PINNED にした合成 contract は
    gate_state() == "READY" になる（BLOCKED が「常に BLOCKED を返す壊れた
    実装」でないことの実証）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "READY"


def test_item49_single_pending_pre_run_field_blocks_gate(contract_raw: Dict[str, Any]) -> None:
    """item 49 補足: pre-run 欄が1つでも PENDING/BLOCKED なら gate は
    BLOCKED のまま（post-run 欄 artifact_manifest_sha/cost_record_sha は
    PENDING のままでも gate に影響しないことの対照確認）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    # post-run 欄は PENDING のままでも READY を妨げないはず。
    contract_ready = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract_ready) == "READY"

    # 1つの pre-run 欄を PENDING へ戻すと BLOCKED になる。
    regressed = copy.deepcopy(fully_pinned)
    regressed["education_technique_lesson_manifest_sha"] = {"value": None, "status": "PENDING", "reason": "regressed"}
    contract_blocked = m.load_run9_contract(regressed)
    assert m.gate_state(contract_blocked) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第1巡対応 — Fix 1:
# human_evaluation_protocol_sha の optional 化
# ---------------------------------------------------------------------------


def test_fix1_human_evaluation_protocol_sha_is_declared_optional() -> None:
    assert m.CONTRACT_OPTIONAL_PIN_FIELDS == frozenset({"human_evaluation_protocol_sha"})


def test_fix1_optional_field_pending_does_not_block_ready(contract_raw: Dict[str, Any]) -> None:
    """rev 0.3 改訂F（人間知覚 Gate 非必須化）: `human_evaluation_protocol_sha`
    だけが PENDING でも、他の pre-run 欄が全て PINNED なら gate_state() は
    READY になる（optional 欄は必須判定から除外される — post-run 欄とは
    別の第3分類として扱われることの直接確認）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    fully_pinned["human_evaluation_protocol_sha"] = {
        "value": None,
        "status": "PENDING",
        "reason": "advisory audit not planned for this attempt",
    }
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "READY"


def test_fix1_optional_field_pinned_still_allows_ready(contract_raw: Dict[str, Any]) -> None:
    """対照実験: optional 欄を PINNED にしても（advisory 監査を実施した
    場合）READY を妨げない — optional は「pin してはいけない」欄ではなく
    「pin しなくても良い」欄であることの確認。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    contract = m.load_run9_contract(fully_pinned)
    assert fully_pinned["human_evaluation_protocol_sha"]["status"] == "PINNED"
    assert m.gate_state(contract) == "READY"


def test_fix1_current_contract_still_blocked_by_other_pending_fields(
    contract: m.Run9RunContract,
) -> None:
    """現行 RUN9_CONTRACT.yaml は human_evaluation_protocol_sha の optional
    化だけでは READY にならない（他の多数の pre-run 欄が依然 PENDING —
    User rights attest 待ち・VG-L0 ハーネス未実装のため）。"""
    assert m.gate_state(contract) == "BLOCKED"


def test_fix1_current_contract_human_evaluation_protocol_sha_reason_documents_optional(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["human_evaluation_protocol_sha"]
    assert field["status"] == "PENDING"
    assert "optional" in field["reason"].lower()


def test_fix1_optional_field_excluded_from_pre_run_required_set() -> None:
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    assert "human_evaluation_protocol_sha" not in pre_run_fields
    assert "artifact_manifest_sha" not in pre_run_fields
    assert "cost_record_sha" not in pre_run_fields
    # optional と post-run は互いに素であること（同一欄が両分類に属さない）。
    assert not (m.CONTRACT_POST_RUN_PIN_FIELDS & m.CONTRACT_OPTIONAL_PIN_FIELDS)


# ---------------------------------------------------------------------------
# item 54: no RUN9A/RUN9B/RUN9C IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_run_id", ["RUN9A", "RUN9B", "RUN9C", "RUN9-A", "run9"])
def test_item54_branch_numbered_run_ids_rejected(contract_raw: Dict[str, Any], bad_run_id: str) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["run_id"] = bad_run_id
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_item54_current_contract_run_id_is_exactly_run9(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["run_id"] == "RUN9"


# ---------------------------------------------------------------------------
# 追加: 未 pin domain では build_founder が ValueError / pinned fixture
# domain（ダミー 64hex）では成功
# ---------------------------------------------------------------------------


def test_unpinned_domain_rejects_build_founder(domain_draft_raw: Dict[str, Any]) -> None:
    """domains/identity_domain_run9_v1.json のドラフト（プレースホルダ
    `<PIN_BEFORE_RUN>`）は構造 valid だが is_pinned() == False であり、
    build_founder() は ValueError を送出する
    （DESIGN_RUN9 §22 実行順 step 3→4 の機械強制）。"""
    domain = m.run9_identity_domain_from_dict(domain_draft_raw)
    assert domain.is_pinned() is False
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(domain, "R9F-01")


def test_unpinned_domain_placeholder_is_structurally_valid() -> None:
    """ドラフト domain 自体は構造検証（未知キー・anchor_order 等）は
    通過する — 未 pin は「構造不正」ではなく「pin 未充足」として区別する。"""
    domain = m.run9_identity_domain_from_dict(json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8")))
    assert domain.domain_id == m.RUN9_DOMAIN_ID
    assert domain.anchor_order == ("af0", "ritsu", "user")


def test_pinned_fixture_domain_succeeds(pinned_domain: m.Run9IdentityDomain) -> None:
    assert pinned_domain.is_pinned() is True
    g1 = m.build_founder(pinned_domain, "R9F-01")
    g2 = m.build_founder(pinned_domain, "R9F-02")
    assert g1.genome_id and g2.genome_id


def test_domain_placeholder_values_are_not_valid_sha256() -> None:
    """プレースホルダ `<PIN_BEFORE_RUN>` は 64hex sha256 pattern に一致
    しないことの直接確認（is_pinned() の判定根拠）。"""
    import re

    for value in ("<PIN_BEFORE_RUN>", "<PIN_BEFORE_LEARNING>", "<SEALED_BEFORE_LEARNING>"):
        assert not re.fullmatch(r"[0-9a-f]{64}", value)


# ---------------------------------------------------------------------------
# design_doc_sha256 pin と実ファイルの一致（design_doc_sha256 は task 指示に
# より RUN9_CONTRACT.yaml が real sha256 を PINNED で持つことを要求される）
# ---------------------------------------------------------------------------


def test_design_doc_sha256_pin_matches_actual_copied_file(contract_raw: Dict[str, Any]) -> None:
    import hashlib

    actual = hashlib.sha256(DESIGN_DOC_PATH.read_bytes()).hexdigest()
    assert contract_raw["design_doc_sha256"]["status"] == "PINNED"
    assert contract_raw["design_doc_sha256"]["value"] == actual


# ---------------------------------------------------------------------------
# VG-E0 凍結三角形が変更されていないことの回帰確認（DESIGN_RUN9 §8 の
# 「既存 schema・既存台帳を in-place 変更しない」を run9_schema.py が
# 満たしていることの直接検証 — models.py をモジュールレベルで import
# しない設計そのものを確認する）。
# ---------------------------------------------------------------------------


def test_run9_schema_module_does_not_import_vg_e0_models_at_module_level() -> None:
    import re

    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    forbidden = re.compile(r"^\s*(import\s+(models|simplex)\b|from\s+(models|simplex)\s+import\b)")
    for line in source.splitlines():
        assert not forbidden.match(line), (
            f"run9_schema.py は VG-E0 の models.py/simplex.py をモジュールレベルで import して"
            f"はならない（DESIGN_RUN9 §8: 既存 schema・既存台帳の in-place 変更禁止 — "
            f"run9 domain は独立実装であるべき）: {line.strip()!r}"
        )


def test_vg_e0_frozen_anchor_names_are_unchanged() -> None:
    """VG-E0 の凍結三角形 `models.ANCHOR_NAMES` が RUN9 実装によって変更されて
    いないことの回帰確認（models.py を直接 import して確認する — 本テスト
    ファイル自身がモジュールレベルで依存するわけではない）。"""
    sys.path.insert(0, str(_RUN_DIR.parent))
    import models as vg_e0_models  # noqa: E402

    assert vg_e0_models.ANCHOR_NAMES == ("ritsu", "pjs", "user")
    assert vg_e0_models.VALID_OPERATORS == (
        "founder", "drift", "vertex_pull", "reseed", "edge_walk", "novelty_jump",
    )


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー対応 — Fix 1: PINNED 欄の値整形式強制
# ---------------------------------------------------------------------------


def test_fix1_pinned_field_with_null_value_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 指摘1: status を PINNED に書き換えるだけで
    value が null のままの fixture は load 時に拒否される（『全欄 status
    だけ PINNED にして READY を騙る』経路の閉塞）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["backbone_checkpoint_sha"] = {"value": None, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix1_pinned_sha_field_with_non_hex_value_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 指摘1: `_sha`/`_sha256` で終わる欄が
    PINNED を名乗るのに value が 64hex でなければ拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["backbone_checkpoint_sha"] = {
        "value": "not-a-valid-sha256-value",
        "status": "PINNED",
        "source": "x",
    }
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix1_pinned_attempt_id_placeholder_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 指摘1: `attempt_id` が PINNED を名乗るのに
    プレースホルダ値（`<...>`）のままなら拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["attempt_id"] = {"value": "<PIN_BEFORE_RUN>", "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix1_pinned_founder_genome_sha_wrong_length_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 指摘1（第7巡指摘1で64hex sha256形式へ意味論
    是正済み — Fix 15 参照）: `founder_genome_shas.R9F-0x` は64hex sha256
    形式（永続 genome 文書ファイルのバイト sha256）を要求する — 桁数不足
    （63hex）を入れると拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 63, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix15_founder_genome_sha_requires_64hex_not_16hex_genome_id(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第7巡指摘1: `founder_genome_shas.R9F-0x`
    が PINNED を名乗るとき、旧形式の16hex `genome_id` 値はもはや拒否される
    — 値は永続 genome 文書ファイル（founders/R9F-0x_genome.json）のバイト
    sha256（64hex）でなければならない。genome_id は文書内部のフィールドに
    過ぎず、文書自体のバイト凍結ではないため（R9-G12 replay 照合の対象は
    文書バイトそのもの）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 16, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix15_founder_genome_sha_accepts_64hex(contract_raw: Dict[str, Any]) -> None:
    """対照実験: 64hex sha256 値は PINNED として正しく受理される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 64, "status": "PINNED", "source": "x"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


def test_fix15_current_contract_founder_genome_shas_reason_mentions_genome_document_bytes(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第7巡指摘1: RUN9_CONTRACT.yaml の
    founder_genome_shas.reason が「genome 文書ファイルのバイト sha256」を
    pin する旨へ同期されていることを確認する。"""
    for founder_id in ("R9F-01", "R9F-02"):
        reason = contract_raw["founder_genome_shas"][founder_id]["reason"]
        assert "genome.json" in reason or "sha256" in reason


def test_fix1_pending_field_may_still_have_null_value(contract_raw: Dict[str, Any]) -> None:
    """対照実験: PENDING/BLOCKED は従来どおり value null が許容される
    （Fix 1 は PINNED を名乗る欄だけを対象にする — 正直な未 pin 表現を
    妨げない）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["backbone_checkpoint_sha"] = {"value": None, "status": "PENDING", "reason": "x"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー対応 — Fix 2: is_pinned() に metric_space_sha
# ---------------------------------------------------------------------------


def test_fix2_metric_space_sha_placeholder_blocks_is_pinned_and_build_founder() -> None:
    """Codex bot レビュー PR #315 指摘2: 3 anchor_hashes が pin 済みでも
    `metric_space_sha` がプレースホルダのままなら `is_pinned() == False`
    であり、`build_founder()` も拒否される。`metric_space_sha` は
    `content_digest()` の入力に含まれるため、後から pin し直すと
    genome_id が変わり既発行の成果物を無効化する（将来汚染）。"""
    domain = m.build_run9_identity_domain(
        anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        metric_space_sha="<PIN_BEFORE_RUN>",
    )
    assert domain.is_pinned() is False
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(domain, "R9F-01")


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー対応 — Fix 3: founder_genome_from_dict の
# builder 照合必須化
# ---------------------------------------------------------------------------


def test_fix3_founder_genome_from_dict_requires_domain_keyword() -> None:
    """Codex bot レビュー PR #315 指摘3: `domain` はキーワード専用引数。"""
    params = inspect.signature(m.founder_genome_from_dict).parameters
    assert "domain" in params
    assert params["domain"].kind == inspect.Parameter.KEYWORD_ONLY


def test_fix3_voice_id_coords_mismatch_rejected() -> None:
    """Codex bot レビュー PR #315 指摘3: 『R9F-01 ラベル + R9F-02 座標』の
    ような偽装 genome document は builder 照合（`build_founder(domain,
    voice_id)` との `to_dict()` 完全一致要求）で検出される。"""
    domain = _pinned_fixture_domain()
    r9f02 = m.build_founder(domain, "R9F-02")
    forged = r9f02.to_dict()
    forged["voice_id"] = "R9F-01"  # 座標・profile_label は R9F-02 のまま、ラベルだけ差し替え
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix3_tampered_genome_id_rejected() -> None:
    """Codex bot レビュー PR #315 指摘3: genome_id だけを任意の16hex値へ
    差し替えた genome document は builder 照合で検出される（構造的には
    正規の16hexだが再計算値と不一致）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["genome_id"] = "f" * 16
    assert forged["genome_id"] != genuine.genome_id
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix3_correctly_signed_genome_document_still_roundtrips() -> None:
    """対照実験: 改ざんされていない genome document は builder 照合を通過し、
    正典 Run9FounderGenome と完全一致する（Fix 3 が正常系まで壊していない
    ことの確認）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-02")
    reconstructed = m.founder_genome_from_dict(genuine.to_dict(), domain=domain)
    assert reconstructed.to_dict() == genuine.to_dict()


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第2巡対応 — Fix 4: load 後の raw 直接改変で
# READY を騙る経路の閉塞
# ---------------------------------------------------------------------------


def test_fix4_load_run9_contract_deepcopies_input(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘1(a): `load_run9_contract()` は
    入力 dict を deepcopy する。load 後に呼び出し元が渡した元 dict の
    ネストした pin 欄を書き換えても `Run9RunContract.raw` は影響を受けない
    （浅いコピーだとネスト dict が共有されたままになる）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    fresh_raw["education_technique_lesson_manifest_sha"]["status"] = "PINNED"
    fresh_raw["education_technique_lesson_manifest_sha"]["value"] = "z" * 64  # 非hexだが元dict側だけの改変
    assert contract.raw["education_technique_lesson_manifest_sha"]["status"] == "PENDING"
    assert contract.raw["education_technique_lesson_manifest_sha"] is not fresh_raw["education_technique_lesson_manifest_sha"]


def test_fix4_gate_state_revalidates_and_rejects_direct_raw_tampering(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第2巡指摘1(b): 正常 load 後に
    `contract.raw["education_technique_lesson_manifest_sha"]["status"]` を直接 "PINNED" へ書き換えても
    （value は null のまま）、`gate_state()` は毎回 `contract.raw` を
    `load_run9_contract()` で再検証するため Run9ValidationError を送出する
    （load 済みオブジェクトの raw を直接書き換えて READY を騙る経路の閉塞。
    共有 module fixture の汚染を避けるため、ここではローカルにコピーした
    contract を使う）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    contract.raw["education_technique_lesson_manifest_sha"]["status"] = "PINNED"  # value は null のまま
    with pytest.raises(m.Run9ValidationError):
        m.gate_state(contract)


def test_fix4_gate_state_still_works_on_untampered_contract(contract_raw: Dict[str, Any]) -> None:
    """対照実験: 改変していない contract では `gate_state()` の再検証が
    正常系まで壊していないことの確認（現行 RUN9_CONTRACT.yaml は BLOCKED）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第2巡対応 — Fix 5: 単一介入エッジの凍結値強制
# （rev 0.3 改訂A、PoR §1/§3/§4 により `single_intervention.changed_edge`
# 単数から `interventions.edges`[2] + `control_branch` へ改訂 — 定数名も
# `CHANGED_EDGE` から `INTERVENTION_EDGES`/`CONTROL_BRANCH` へ移行）
# ---------------------------------------------------------------------------


def test_fix5_intervention_edges_constant_matches_contract() -> None:
    assert m.INTERVENTION_EDGES == ("PRACTICE_FROM_AUDIO", "TRANSFER_TECHNIQUE")


def test_fix5_control_branch_constant_matches_contract() -> None:
    assert m.CONTROL_BRANCH == "CONTROL"


def test_fix5_birth_edge_constant_is_inherit_trait() -> None:
    assert m.BIRTH_EDGE == "INHERIT_TRAIT"


def test_fix5_current_contract_intervention_edges_is_frozen_value(
    contract_raw: Dict[str, Any],
) -> None:
    assert tuple(contract_raw["interventions"]["edges"]) == m.INTERVENTION_EDGES


def test_fix5_current_contract_control_branch_is_frozen_value(
    contract_raw: Dict[str, Any],
) -> None:
    assert contract_raw["interventions"]["control_branch"] == m.CONTROL_BRANCH


def test_fix5_intervention_edges_tampering_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘2 の rev 0.3 版: `edges` を
    別のエッジ集合へ差し替えた fixture は拒否される（PoR §3/§4 で凍結
    された二介入エッジの改変防止）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"]["edges"] = ["REPLACE_IDENTITY", "TRANSFER_TECHNIQUE"]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix5_intervention_edges_order_swap_rejected(contract_raw: Dict[str, Any]) -> None:
    """`edges` は要素だけでなく順序も INTERVENTION_EDGES と厳密一致を
    要求する（parent_designs と同型の順序込み終端規律）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"]["edges"] = ["TRANSFER_TECHNIQUE", "PRACTICE_FROM_AUDIO"]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix5_control_branch_tampering_rejected(contract_raw: Dict[str, Any]) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"]["control_branch"] = "NO_INTERVENTION"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix5_blank_description_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘2 補足: `description` も
    非空文字列を強制する（rev 0.3 の `interventions.description`）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"]["description"] = "   "
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix5_old_single_intervention_format_rejected(contract_raw: Dict[str, Any]) -> None:
    """rev 0.3: 旧形式（`single_intervention.changed_edge`）を宣言する
    contract は fail-closed で拒否される。`interventions` キーの欠落
    （未知キー `single_intervention` の混入と対）で拒否される想定。"""
    legacy = copy.deepcopy(contract_raw)
    del legacy["interventions"]
    legacy["single_intervention"] = {
        "description": "legacy single-edge format",
        "changed_edge": "LEARN_PERFORMANCE",
    }
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(legacy)


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第2巡対応 — Fix 6: build_founder の domain
# 不変条件全検証
# ---------------------------------------------------------------------------


def test_fix6_forged_domain_id_rejected_by_build_founder() -> None:
    """Codex bot レビュー PR #315 第2巡指摘3: `run9_identity_domain_from_dict()`
    /`build_run9_identity_domain()` を経由せず `Run9IdentityDomain(...)` を
    直接インスタンス化した偽 domain（dataclass はコンストラクタレベルの
    検証を持たない）は、`anchor_hashes`/`metric_space_sha` が64hex揃いで
    `is_pinned() == True` になっても、`domain_id` 偽装は
    `_validate_domain_invariants()` で検出され `build_founder()` が拒否する。"""
    forged_domain = m.Run9IdentityDomain(
        schema=m.SCHEMA_IDENTITY_DOMAIN,
        domain_id="not-the-real-domain-id/1.0",
        anchor_order=m.RUN9_ANCHOR_ORDER,
        anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        excluded_teacher_identities=m.RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=m.RUN9_COORDINATE_PRECISION,
        normalization=m.RUN9_NORMALIZATION,
        metric_space_sha="d" * 64,
        pin_source_candidates={},
    )
    assert forged_domain.is_pinned() is True  # is_pinned() 単体は形式しか見ないため通る
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(forged_domain, "R9F-01")


def test_fix6_validate_domain_invariants_accepts_genuine_domain(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """対照実験: `build_run9_identity_domain()` が返す正規 domain は
    `_validate_domain_invariants()` を素通りする（正常系まで壊していない
    ことの確認）。"""
    m._validate_domain_invariants(pinned_domain)  # 例外を投げないことの確認
    g = m.build_founder(pinned_domain, "R9F-01")
    assert g.genome_id


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第3巡対応 — Fix 7: repository_commit_sha の
# 40hex（git SHA-1）特例
# ---------------------------------------------------------------------------


def test_fix7_repository_commit_sha_accepts_40hex(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第3巡指摘1: `repository_commit_sha` は
    git commit object ID（40桁小文字hex、SHA-1）を PINNED として受理する
    （64hex 規則の対象から外す — 正直な git sha を PINNED にしても
    contract が構造的に READY へ到達できなくなっていた第1巡修正の不備）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["repository_commit_sha"] = {
        "value": "a" * 40,
        "status": "PINNED",
        "source": "git rev-parse HEAD",
    }
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


@pytest.mark.parametrize("bad_value", ["a" * 64, "a" * 39, "A" * 40, "g" * 40])
def test_fix7_repository_commit_sha_rejects_non_40hex(
    contract_raw: Dict[str, Any], bad_value: str
) -> None:
    """Codex bot レビュー PR #315 第3巡指摘1 負例: 64hex（sha256 と誤って
    揃えた値）・39hex（桁数不足）・大文字・非hex文字はいずれも拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["repository_commit_sha"] = {"value": bad_value, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第3巡対応 — Fix 8: founder_genome_shas の相異強制
# ---------------------------------------------------------------------------


def test_fix8_identical_founder_genome_shas_when_both_pinned_rejected(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第3巡指摘2: R9F-01 と R9F-02 の両方が
    PINNED のとき、同一 genome_id value は拒否される（二体の dual-founder
    比較の前提 — 別々の Genome であること — が崩れるため）。"""
    tampered = copy.deepcopy(contract_raw)
    same_value = "a" * 64  # Fix 15: 64hex sha256 形式（valid shape）で相異判定そのものを検査する
    tampered["founder_genome_shas"]["R9F-01"] = {"value": same_value, "status": "PINNED", "source": "x"}
    tampered["founder_genome_shas"]["R9F-02"] = {"value": same_value, "status": "PINNED", "source": "y"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix8_distinct_founder_genome_shas_when_both_pinned_accepted(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: 相異する value なら両方 PINNED でも通る。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 64, "status": "PINNED", "source": "x"}
    tampered["founder_genome_shas"]["R9F-02"] = {"value": "b" * 64, "status": "PINNED", "source": "y"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


def test_fix8_one_pending_one_pinned_does_not_trigger_distinctness_check(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: 片方だけ PINNED（もう片方 PENDING）の場合は相異判定その
    ものが発火しない — 正直な未 pin 表現を妨げない。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 64, "status": "PINNED", "source": "x"}
    tampered["founder_genome_shas"]["R9F-02"] = {"value": None, "status": "PENDING", "reason": "y"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第3巡対応 — Fix 9: domain ネスト dict の凍結
# ---------------------------------------------------------------------------


def test_fix9_anchor_hashes_is_read_only_mapping(pinned_domain: m.Run9IdentityDomain) -> None:
    """Codex bot レビュー PR #315 第3巡指摘3: 構築済み domain の
    `anchor_hashes` は `types.MappingProxyType` で凍結され、
    `domain.anchor_hashes["af0"] = ...` は TypeError になる（frozen
    dataclass のトップレベル属性再代入禁止だけでは、ネスト dict の
    in-place 書き換えを防げていなかった）。"""
    with pytest.raises(TypeError):
        pinned_domain.anchor_hashes["af0"] = "f" * 64  # type: ignore[index]


def test_fix9_pin_source_candidates_is_read_only_mapping() -> None:
    domain = m.run9_identity_domain_from_dict(
        json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    )
    with pytest.raises(TypeError):
        domain.pin_source_candidates["af0"] = "tampered"  # type: ignore[index]


def test_fix9_directly_instantiated_domain_is_also_frozen() -> None:
    """Fix 9 は `__post_init__` 経由のため、`run9_identity_domain_from_dict()`
    /`build_run9_identity_domain()` を経由しない直接インスタンス化経路
    （Fix 6 のテストが使う手口）でも同様に効く。"""
    domain = m.Run9IdentityDomain(
        schema=m.SCHEMA_IDENTITY_DOMAIN,
        domain_id=m.RUN9_DOMAIN_ID,
        anchor_order=m.RUN9_ANCHOR_ORDER,
        anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        excluded_teacher_identities=m.RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=m.RUN9_COORDINATE_PRECISION,
        normalization=m.RUN9_NORMALIZATION,
        metric_space_sha="d" * 64,
        pin_source_candidates={},
    )
    with pytest.raises(TypeError):
        domain.anchor_hashes["af0"] = "f" * 64  # type: ignore[index]


def test_fix9_build_founder_twice_yields_stable_genome_id_and_is_unaffected_by_mutation_attempt(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """Fix 9 の実効性確認: 同一 domain から2回 `build_founder()` を呼んでも
    genome_id は不変（anchor set を差し替える経路が閉じているため）。"""
    g1 = m.build_founder(pinned_domain, "R9F-01")
    with pytest.raises(TypeError):
        pinned_domain.anchor_hashes["af0"] = "f" * 64  # type: ignore[index]  # 差し替えを試みても失敗する
    g2 = m.build_founder(pinned_domain, "R9F-01")
    assert g1.genome_id == g2.genome_id


def test_fix9_content_digest_and_validators_still_work_with_mapping_proxy(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """対照実験: `content_digest()` / `_validate_domain_invariants()` は
    `MappingProxyType` に対しても従来どおり動作する（`.items()`/`.keys()`
    経由のため互換）。"""
    assert isinstance(pinned_domain.content_digest(), str) and len(pinned_domain.content_digest()) == 64
    m._validate_domain_invariants(pinned_domain)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第4巡対応 — Fix 10: attempt_id の正の文法強制
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sneaky_value",
    [
        " <PIN_BEFORE_RUN> ",  # 前後空白付きプレースホルダ（旧実装は strip() 後だけ比較していたためすり抜けた）
        "<PIN_1>",  # 数字入りプレースホルダ（旧実装のブラックリスト正規表現は大文字+アンダースコアのみ想定）
        "a b",  # 内部に空白を含む値
    ],
)
def test_fix10_attempt_id_rejects_placeholder_variants_via_positive_grammar(
    contract_raw: Dict[str, Any], sneaky_value: str
) -> None:
    """Codex bot レビュー PR #315 第4巡指摘: `attempt_id` の PINNED 値検証を
    ブラックリスト式（非空 + プレースホルダ正規表現不一致）から正の文法
    （`_ATTEMPT_ID_RE`）へ置換した結果、旧実装をすり抜けていたプレース
    ホルダ変種（前後空白付き・数字入り）と、空白を含む一般の不正値の
    いずれも拒否されることを確認する。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["attempt_id"] = {"value": sneaky_value, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix10_attempt_id_accepts_well_formed_value(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第4巡指摘 正例: 先頭英数字・以降
    英数字/`.`/`_`/`-` のみの値は PINNED として受理される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["attempt_id"] = {"value": "attempt-2026-08-24.1", "status": "PINNED", "source": "x"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


def test_fix10_attempt_id_regex_is_positive_grammar_not_blacklist() -> None:
    """`_ATTEMPT_ID_RE` 自体が「先頭英数字・残りは英数字/./_/- のみ」という
    正の文法であり、`<`/`>`/空白を機械的に排除することの直接確認
    （個別プレースホルダ文字列のブラックリスト列挙ではないことの実証）。"""
    assert m._ATTEMPT_ID_RE.match("attempt-2026-08-24.1")
    assert m._ATTEMPT_ID_RE.match("a")
    assert not m._ATTEMPT_ID_RE.match("<PIN_BEFORE_RUN>")
    assert not m._ATTEMPT_ID_RE.match(" <PIN_BEFORE_RUN> ")
    assert not m._ATTEMPT_ID_RE.match("<PIN_1>")
    assert not m._ATTEMPT_ID_RE.match("a b")
    assert not m._ATTEMPT_ID_RE.match("")
    assert not m._ATTEMPT_ID_RE.match("-leading-dash")  # 先頭は英数字必須


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第5巡対応 — Fix 11: coordinate_precision の
# 厳密 int 型検査
# ---------------------------------------------------------------------------


def _domain_document(coordinate_precision: Any) -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_IDENTITY_DOMAIN,
        "domain_id": m.RUN9_DOMAIN_ID,
        "anchor_order": list(m.RUN9_ANCHOR_ORDER),
        "anchor_hashes": {"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        "excluded_teacher_identities": list(m.RUN9_EXCLUDED_TEACHER_IDENTITIES),
        "coordinate_precision": coordinate_precision,
        "normalization": m.RUN9_NORMALIZATION,
        "metric_space_sha": "d" * 64,
    }


def test_fix11_coordinate_precision_float_variant_rejected_by_from_dict() -> None:
    """Codex bot レビュー PR #315 第5巡指摘1: `run9_identity_domain_from_dict()`
    は `coordinate_precision: 6.0`（float）を拒否する。Python の `6.0 == 6`
    は真だが、float のまま通過すると `content_digest()` の JSON 直列化で
    正準 int 6 と異なるバイト列になり、同一のはずの pinned domain から
    異なる domain digest / genome_id が出る決定論欠陥になる。"""
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(_domain_document(6.0))


def test_fix11_coordinate_precision_bool_variant_rejected_by_from_dict() -> None:
    """item 補足: bool も int のサブクラスとして `== 6` へ黙って通り得るため
    明示的に拒否する（`True == 1` であって `True == 6` は偽だが、bool 全般
    が strict int 判定から除外されることを別途確認する）。"""
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(_domain_document(True))


def test_fix11_coordinate_precision_correct_int_accepted_by_from_dict() -> None:
    """対照実験: 正準 int 6 は通過する。"""
    domain = m.run9_identity_domain_from_dict(_domain_document(6))
    assert domain.coordinate_precision == 6
    assert type(domain.coordinate_precision) is int


def test_fix11_directly_instantiated_domain_with_float_precision_rejected_by_build_founder() -> None:
    """Codex bot レビュー PR #315 第5巡指摘1: `run9_identity_domain_from_dict()`
    /`build_run9_identity_domain()` を経由せず `Run9IdentityDomain(...)` を
    直接インスタンス化し `coordinate_precision=6.0`（float）を持つ偽 domain
    は、`anchor_hashes`/`metric_space_sha` が64hex揃いで `is_pinned()==True`
    になっても、`_validate_domain_invariants()` の厳密 int 検査で
    `build_founder()` が拒否する。"""
    forged_domain = m.Run9IdentityDomain(
        schema=m.SCHEMA_IDENTITY_DOMAIN,
        domain_id=m.RUN9_DOMAIN_ID,
        anchor_order=m.RUN9_ANCHOR_ORDER,
        anchor_hashes={"af0": "a" * 64, "ritsu": "b" * 64, "user": "c" * 64},
        excluded_teacher_identities=m.RUN9_EXCLUDED_TEACHER_IDENTITIES,
        coordinate_precision=6.0,  # type: ignore[arg-type]
        normalization=m.RUN9_NORMALIZATION,
        metric_space_sha="d" * 64,
        pin_source_candidates={},
    )
    assert forged_domain.is_pinned() is True
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(forged_domain, "R9F-01")


def test_fix11_is_strict_int_helper_excludes_bool_and_float() -> None:
    assert m._is_strict_int(6) is True
    assert m._is_strict_int(6.0) is False
    assert m._is_strict_int(True) is False
    assert m._is_strict_int(False) is False
    assert m._is_strict_int("6") is False


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第5巡対応 — Fix 12: coords の型強制排除
# ---------------------------------------------------------------------------


def test_fix12_coords_string_value_rejected() -> None:
    """Codex bot レビュー PR #315 第5巡指摘2: coords 値が文字列
    （例 `"0.6"`）の genome document は拒否される。従来の `float(coords_raw[k])`
    は文字列を黙って型正規化して受理してしまい、改ざん検出を掲げる
    `founder_genome_from_dict()` が非正準文書を正典として返す契約矛盾に
    なっていた。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["coords"] = {"af0": "0.6", "ritsu": "0.3", "user": "0.1"}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix12_coords_bool_value_rejected() -> None:
    """Codex bot レビュー PR #315 第5巡指摘2 補足: coords 値が bool の
    genome document も拒否される（bool は int のサブクラスのため、
    `isinstance(value, (int, float))` だけの判定だと素通りしてしまう）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["coords"] = {"af0": True, "ritsu": 0.3, "user": 0.1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix12_coords_int_value_is_accepted_and_converted_to_float() -> None:
    """対照実験: JSON の整数値（例 `0`/`1`）は許容し float へ明示変換する
    （coords の成分は多くが 0.1/0.3/0.6 のような非整数だが、`0`/`1` 自体は
    実際に barycentric coordinate の頂点値として現れうる — 例えば
    af0=1.0/ritsu=0.0/user=0.0 の domain では JSON 上 `0`/`1` の整数
    リテラルとして表現され得る。ここでは `_require_valid_coord_scalar()`
    自体の単体挙動として int 受理を直接確認する）。"""
    assert m._require_valid_coord_scalar(0, "coords.af0") == 0.0
    assert m._require_valid_coord_scalar(1, "coords.user") == 1.0
    assert isinstance(m._require_valid_coord_scalar(1, "coords.user"), float)


def test_fix12_require_valid_coord_scalar_rejects_string_and_bool() -> None:
    with pytest.raises(m.Run9ValidationError):
        m._require_valid_coord_scalar("0.6", "coords.af0")
    with pytest.raises(m.Run9ValidationError):
        m._require_valid_coord_scalar(True, "coords.af0")
    with pytest.raises(m.Run9ValidationError):
        m._require_valid_coord_scalar(False, "coords.af0")
    assert m._require_valid_coord_scalar(0.6, "coords.af0") == 0.6
    assert m._require_valid_coord_scalar(1, "coords.user") == 1.0


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第6巡対応 — Fix 13: parent_designs の系譜整合
# ---------------------------------------------------------------------------

_DESIGN_DOC_SECTION_6_PARENT_DESIGNS = [
    "voice_genesis/evolution/DESIGN_VG_E0.md",
    "voice_genesis/evolution/DESIGN_VG_L0.md",
    "VoiceGenesis Evolution Theory v0.3",
    "VoiceGenesis Singing Baseline v0.1",
    "VoiceGenesis Supplement A / Selection Pressure Routing",
]


def test_fix13_contract_parent_designs_matches_design_doc_section_6(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第6巡指摘1: DESIGN_RUN9 §6 は
    parent_designs を5件宣言する（DESIGN_VG_E0 / DESIGN_VG_L0 /
    VoiceGenesis Evolution Theory v0.3 / VoiceGenesis Singing Baseline
    v0.1 / VoiceGenesis Supplement A・Selection Pressure Routing）。
    旧 §23/旧 contract は3件のみで依存2件が provenance から欠落していた
    — RUN9_CONTRACT.yaml を §6 準拠の5件へ拡張したことを確認する
    （設計書自体は byte-pin 済みのため一切編集しない — erratum は
    contract 側でのみ是正する）。"""
    assert contract_raw["parent_designs"] == _DESIGN_DOC_SECTION_6_PARENT_DESIGNS


def test_fix13_parent_designs_element_type_strictly_validated(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第6巡指摘1 補足: loader の parent_designs
    検証は「全要素が非空 str の非空 list」へ厳密化されている。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["parent_designs"] = ["DESIGN_VG_E0", 123]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)

    tampered_blank = copy.deepcopy(contract_raw)
    tampered_blank["parent_designs"] = ["DESIGN_VG_E0", "   "]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered_blank)

    tampered_dict = copy.deepcopy(contract_raw)
    tampered_dict["parent_designs"] = {"DESIGN_VG_E0": 1}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered_dict)


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第6巡対応 — Fix 14: 型強制/等価比較サイトの
# ファミリー全数掃討
# ---------------------------------------------------------------------------


def test_fix14_excluded_teacher_identities_dict_masquerading_as_list_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `excluded_teacher_identities`
    に `{"pjs": 1}` のような dict を渡すと、旧実装の
    `list(excluded_raw) != list(RUN9_EXCLUDED_TEACHER_IDENTITIES)` は
    `list(dict)` がキー列挙で `["pjs"]` を返すため一致してしまい素通り
    していた。`isinstance(list)` を先行させたことで拒否される。"""
    doc = _domain_document(6)
    doc["excluded_teacher_identities"] = {"pjs": 1}
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(doc)


def test_fix14_parents_dict_masquerading_as_list_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `parents` に
    `{"AF0": 1, "RITSU": 1, "USER_DONOR": 1}` のような dict を渡すと、旧
    実装の `list(parents_raw) != [...]` はキー列挙で一致してしまい素通り
    していた。`isinstance(list)` を先行させたことで拒否される。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["parents"] = {"AF0": 1, "RITSU": 1, "USER_DONOR": 1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix14_performance_seed_float_variant_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `performance_seed` に
    `909001.0`（float）を渡すと拒否される（`909001.0 == 909001` は真だが
    `_is_strict_int()` が float を先に排除する）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["performance_seed"] = 909001.0
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix14_genetic_generation_bool_variant_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `genetic_generation` に
    `True` を渡すと拒否される（`True == 1` は真だが `_is_strict_int()` が
    bool を先に排除する）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01")
    forged = genuine.to_dict()
    forged["genetic_generation"] = True
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain)


def test_fix14_run_id_and_experiment_id_and_claim_strength_require_str(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: `run_id`/`experiment_id`/`claim_strength_target` の
    isinstance(str) 明示が正常系まで壊していないこと。"""
    m.load_run9_contract(contract_raw)  # 例外を投げないことの確認


def test_fix14_anchor_order_non_string_element_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `anchor_order` の要素に非
    文字列が混入すると拒否される（全要素 str の明示検査）。"""
    doc = _domain_document(6)
    doc["anchor_order"] = ["af0", "ritsu", 123]
    with pytest.raises(m.Run9ValidationError):
        m.run9_identity_domain_from_dict(doc)


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第7巡対応 — Fix 15: founder_genome_shas を
# 永続 artifact の64hex sha256に変更（テストは §Fix 15 セクション上部に
# すでに追加済み — test_fix15_* / test_fix1_pinned_founder_genome_sha_*）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第7巡対応 — Fix 16: parent_designs の正典厳密一致
# ---------------------------------------------------------------------------


def test_fix16_parent_designs_constant_matches_current_contract() -> None:
    assert m.PARENT_DESIGNS == tuple(_DESIGN_DOC_SECTION_6_PARENT_DESIGNS)


def test_fix16_parent_designs_unrelated_list_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第7巡指摘2: `parent_designs` を無関係な
    5件へ差し替えると拒否される（第6巡修正は型・非空・件数のみを検査して
    おり、内容が正典と無関係でも通過し得た）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["parent_designs"] = ["unrelated"] * 5
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix16_parent_designs_reordered_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第7巡指摘2: 正しい5件でも順序を入れ替える
    と拒否される（順序込みの完全一致を要求する）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["parent_designs"] = list(reversed(_DESIGN_DOC_SECTION_6_PARENT_DESIGNS))
    assert tampered["parent_designs"] != _DESIGN_DOC_SECTION_6_PARENT_DESIGNS  # 反転していることの前提確認
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix16_parent_designs_missing_one_element_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第7巡指摘2: 5件のうち1件が欠落した4件
    リストは拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["parent_designs"] = _DESIGN_DOC_SECTION_6_PARENT_DESIGNS[:-1]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix16_parent_designs_exact_canonical_list_accepted(contract_raw: Dict[str, Any]) -> None:
    """対照実験: 正典と順序込みで完全一致する現行 contract は通る。"""
    m.load_run9_contract(contract_raw)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第8巡対応 — Fix 17: 重複キーの fail-closed 拒否
# ---------------------------------------------------------------------------


def test_fix17_yaml_duplicate_top_level_key_rejected() -> None:
    """Codex bot レビュー PR #315 第8巡指摘1: PENDING の `education_technique_lesson_manifest_sha` の後に
    PINNED の `education_technique_lesson_manifest_sha` を書き足した手編集 yaml（last-key-wins だと
    後者だけが検証対象になり READY へ到達し得た）は、`_StrictYAMLLoader`
    が重複キー段階で拒否する（構造検証まで到達しない）。"""
    tampered_yaml_text = """
education_technique_lesson_manifest_sha:
  value: null
  status: PENDING
education_technique_lesson_manifest_sha:
  value: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  status: PINNED
"""
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.load_run9_contract_from_yaml_text(tampered_yaml_text)


def test_fix17_yaml_duplicate_nested_key_rejected() -> None:
    """Codex bot レビュー PR #315 第8巡指摘1 補足: pin 欄 dict 内部
    （ネストした mapping ノード）の `status` 重複も検出される —
    `construct_mapping` は全 mapping ノードへ再帰的に呼ばれるため。"""
    tampered_yaml_text = """
education_technique_lesson_manifest_sha:
  value: null
  status: PENDING
  status: PINNED
"""
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.load_run9_contract_from_yaml_text(tampered_yaml_text)


def test_fix17_json_duplicate_key_in_anchor_hashes_rejected() -> None:
    """Codex bot レビュー PR #315 第8巡指摘1: domain JSON の
    `anchor_hashes` 内で `af0` を2回宣言すると拒否される（trailing 値の
    last-key-wins 採用を許さない）。"""
    tampered_json_text = '{"anchor_hashes": {"af0": "' + "a" * 64 + '", "af0": "' + "f" * 64 + '"}}'
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.run9_identity_domain_from_json(tampered_json_text)


def test_fix17_existing_contract_yaml_still_loads() -> None:
    """正例: 重複キーの無い既存 RUN9_CONTRACT.yaml は引き続き load できる
    （strict loader が正常系まで壊していないことの確認）。"""
    contract = m.load_run9_contract_from_yaml_path(CONTRACT_PATH)
    assert m.gate_state(contract) == "BLOCKED"


def test_fix17_existing_domain_draft_json_still_loads() -> None:
    """正例: 重複キーの無い既存 domain draft JSON は引き続き load できる。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.is_pinned() is False


def test_fix17_yaml_loader_comment_declares_models_loads_strict_parity() -> None:
    """VG-E0 `models.loads_strict()` と同型の fail-closed 規約であることの
    宣言がソース中に存在することの直接確認。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "loads_strict" in source


# ---------------------------------------------------------------------------
# User 裁定 2026-08-24（design_revision 0.1 -> 0.2）対応
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_canonical_json(obj: Any) -> str:
    """run9_schema._canonical_json と同じ規約（sort_keys / 区切り固定）で
    dict を正規化した sha256（af0_anchor_manifest.json の
    canonicalization_method フィールドが宣言する規約と同一 — ensure_ascii
    のみ True/False の違いだが本ファイルの内容は非ASCII文字を含まないため
    結果は同じになる）。"""
    import hashlib

    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- item 1: design_revision 0.3 での contract load 成功 / "0.1"/"0.2" 拒否 -


def test_revision02_design_revision_constant_is_0_2() -> None:
    """rev 0.3 では `run9_schema.DESIGN_REVISION` 自体が "0.3" を凍結する
    （テスト名は歴史的に revision02_ prefix のまま — Fix 15 の
    founder_genome_shas 改名前例と同様、rename ではなく assertion のみ
    更新する）。"""
    assert m.DESIGN_REVISION == "0.3"


def test_revision02_current_contract_declares_0_2(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["design_revision"] == "0.3"
    m.load_run9_contract(contract_raw)  # 例外を投げないことの確認


def test_revision02_old_0_1_contract_rejected(contract_raw: Dict[str, Any]) -> None:
    """User 裁定 2026-08-24: design_revision "0.1" を宣言する contract は
    意図どおり拒否される（実装バグではない — DESIGN_RUN9_REVISION_0.2.md
    冒頭に明記）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.1"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_revision03_old_0_2_contract_rejected(contract_raw: Dict[str, Any]) -> None:
    """design_revision 0.2 → 0.3（PoR メモ編入）: 旧 "0.2" を宣言する
    contract も同様に意図どおり拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_revision03_old_0_2_contract_rejection_message_names_current_revision(
    contract_raw: Dict[str, Any],
) -> None:
    """PR #317 Codex bot レビュー第1巡 Fix 2 採用: 拒否メッセージが固定
    ファイル名（例: "DESIGN_RUN9_REVISION_0.2.md"）をハードコードして
    いると、design_revision を上げるたびにメッセージ内のファイル名だけが
    陳腐化する（実際に 0.2 -> 0.3 進行時に発生した不備）。メッセージが
    `DESIGN_REVISION` 定数（現在は "0.3"）から動的に導出されていること
    を、"0.2" 拒否時のメッセージに現行の "0.3" が含まれることで確認する
    — メッセージが旧値のまま固定化されていれば失敗する。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError, match="0.3"):
        m.load_run9_contract(tampered)


def test_revision02_doc_sha256_pin_matches_actual_file(contract_raw: Dict[str, Any]) -> None:
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(REVISION_DOC_PATH)


def test_revision03_por_adjudication_sha256_pin_matches_actual_file(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["por_adjudication_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(POR_ADJUDICATION_PATH)


def test_revision03_rev02_doc_is_byte_unchanged() -> None:
    """rev 0.2 文書（DESIGN_RUN9_REVISION_0.2.md）は rev 0.3 発行後も
    無改変・存続する — DESIGN_RUN9_REVISION_0.3.md「design_revision 系譜」
    表に記録した固定 sha256 と一致することを確認する。"""
    assert _sha256_file(REVISION_0_2_DOC_PATH) == (
        "406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb"
    )


def test_revision03_por_adjudication_byte_identical_to_upload_source() -> None:
    """PoR メモの repo コピーが uploads 原本とバイト同一であること。
    uploads パスはセッション固有・一時的なものであり CI/他環境には存在
    しないため、原本が実在するときだけ検証する（原本非存在時はこの
    テスト自体は無条件 pass — 恒久的な回帰保護は
    `por_adjudication_sha256` の固定 pin 値と本テストファイル中の
    frozen literal（下記 test_revision03_por_adjudication_sha256_is_frozen_value）
    が担う）。

    存在確認は `Path.exists()` の戻り値だけで判定しない（CI runner 実測
    2026-08-24: GitHub Actions 上の `/root/.claude/uploads/...` への
    stat が `PermissionError` を送出し、`exists()` が False を返す経路を
    経由せず素通しで例外が伝播していた — `Path.exists()` は権限拒否時に
    OSError を上げる場合がある。`OSError` を捕捉して「存在しない」扱いに
    正規化してから skip する）。
    """
    try:
        source_available = POR_UPLOAD_SOURCE_PATH.exists()
    except OSError:
        source_available = False
    if not source_available:
        pytest.skip(
            f"upload source not present or not accessible in this environment: "
            f"{POR_UPLOAD_SOURCE_PATH}"
        )
    assert _sha256_file(POR_ADJUDICATION_PATH) == _sha256_file(POR_UPLOAD_SOURCE_PATH)


def test_revision03_por_adjudication_sha256_is_frozen_value() -> None:
    """uploads パスに依存しない恒久的な回帰保護: repo コピーの実測 sha256
    が、編入時に確認した固定値（RUN9_CONTRACT.yaml の
    por_adjudication_sha256 pin と同一）と一致すること。"""
    assert _sha256_file(POR_ADJUDICATION_PATH) == (
        "56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007"
    )


def test_revision02_v0_1_design_doc_is_byte_unchanged(contract_raw: Dict[str, Any]) -> None:
    """v0.1 本文は無改変のまま — design_doc_sha256 pin は据え置きで、実
    ファイルの sha256 と引き続き一致する。"""
    field = contract_raw["design_doc_sha256"]
    assert field["value"] == _sha256_file(DESIGN_DOC_PATH)


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第7巡対応 — Adapter → ControlProfile §対応マップ
# ---------------------------------------------------------------------------


def test_revision02_adapter_to_controlprofile_correspondence_map_present() -> None:
    """Codex bot レビュー PR #316 第7巡指摘（37b6193）+ 第8巡指摘B
    （38c0e0f, 掃討完結、採用）: REVISION_0.2 が v0.1 の Adapter 固有条項
    11項目それぞれについて ControlProfile 等価物への明示的置換表を持つ
    ことのキーワード存在検査（過剰な文言テストは避け、各項目を機械的に
    識別できる語彙が本文に存在することだけを確認する）。rev 0.2 文書自体は
    rev 0.3 発行後も無改変で存続するため、`REVISION_0_2_DOC_PATH`
    （固定 sha256 = `test_revision03_rev02_doc_is_byte_unchanged` が別途
    検証）を対象にする — `REVISION_DOC_PATH` は現行 design_revision
    （rev 0.3）を指すよう rev 0.3 編入時に更新済みのため、本テストの対象を
    誤って rev 0.3 文書へ切り替えない。"""
    doc = REVISION_0_2_DOC_PATH.read_text(encoding="utf-8")
    assert "本表（rev 0.2）が勝つ" in doc  # 前文: v0.1 と矛盾する場合の優先規則
    required_keywords = [
        "BLOCKED_CONTROLPROFILE_ENTRY",  # item 1: Adapter Entry Gate -> ControlProfile Entry Gate
        "Zero ControlProfile",  # item 2: C1
        "CONTROLPROFILE_ENTRY_AND_EQUAL_BUDGET",  # item 3: R9-G8
        "ControlProfile version SHA",  # item 4: R9-G12
        "freeze both ControlProfile versions (r1)",  # item 5: step 12
        "ControlProfile Entry Gate not satisfied",  # item 6: Stop rule 9
        "ControlProfile-01:r0",  # item 7: §13.1 図式
        "ControlProfile 導出手続き",  # item 8: learning_recipe
        "control_profiles/",  # item 9: §25 results バンドルディレクトリ
        "ControlProfiles are independent per Founder",  # item 10: §27 item 30
        "ControlProfile version freeze",  # item 11: §31 実装者役割
    ]
    for keyword in required_keywords:
        assert keyword in doc, f"REVISION_0.2.md に必須キーワードが見つかりません: {keyword!r}"


def test_revision02_adapter_sweep_completeness_declared() -> None:
    """Codex bot レビュー PR #316 第8巡指摘B: v0.1 全文の `grep -in adapter`
    掃討の網羅性宣言がソース中に明文化されていること（rev 0.2 文書対象。
    上のテストと同じ理由で REVISION_0_2_DOC_PATH を使う）。"""
    doc = REVISION_0_2_DOC_PATH.read_text(encoding="utf-8")
    assert "grep -in adapter" in doc
    assert "未マップの実行要件は残っていない" in doc


def test_revision02_v0_1_adapter_line_count_matches_swept_total() -> None:
    """`grep -in adapter` で v0.1 全文を機械的に再走査し、ヒット行数が
    掃討時の実測（22行）と一致することを確認する — v0.1 は byte-pin
    不変のため、この行数はテストとして安定して再現可能（Codex bot
    レビュー PR #316 第8巡指摘B）。行数が変われば v0.1 自体が改変された
    ことの検出にもなる（design_doc_sha256 の pin 検証と相補的）。"""
    v01_doc = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    adapter_lines = [line for line in v01_doc.splitlines() if "adapter" in line.lower()]
    assert len(adapter_lines) == 22, (
        f"v0.1 の 'adapter' 出現行数が掃討時の実測（22）と異なる: {len(adapter_lines)} 行 "
        f"— v0.1 の改変、または本テストの前提が古い可能性がある: {adapter_lines!r}"
    )


def test_revision02_new_pin_fields_get_64hex_format_enforced(contract_raw: Dict[str, Any]) -> None:
    """新欄（design_revision_doc_sha256 / backbone_runtime_bundle_sha /
    por_adjudication_sha256）は欄名が `_sha256`/`_sha` で終わるため、
    `_validate_pin_field_value_shape` の汎用64hexブランチが自動適用される
    （特別扱いの分岐を追加していないことの確認）。"""
    for field_name in (
        "design_revision_doc_sha256",
        "backbone_runtime_bundle_sha",
        "por_adjudication_sha256",
    ):
        assert field_name in m.CONTRACT_PIN_FIELDS
        tampered = copy.deepcopy(contract_raw)
        tampered[field_name] = {"value": "not-a-valid-hex-value", "status": "PINNED", "source": "x"}
        with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
            m.load_run9_contract(tampered)


# --- item 2: af0_anchor_manifest の実在ファイル整合 -------------------------


def test_revision02_af0_anchor_manifest_spec_sha256_matches_canonical_af0_json() -> None:
    """af0_anchor_manifest.json の spec_sha256 は
    founder_specs/AF0.json の**正規形**（af_spec.py canonical_json 規約:
    sort_keys / ensure_ascii=False / 区切り固定 / 末尾改行なし）の sha256 と
    一致する。**これは founder_specs/AF0.json の生バイト列の sha256sum とは
    異なる**（後者は pretty-printed のため） — 両者の関係は manifest 自身の
    `spec_sha256.verification` フィールドが明記する。"""
    manifest = json.loads(AF0_ANCHOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    spec = json.loads(AF0_SPEC_PATH.read_text(encoding="utf-8"))
    canonical_af0 = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    import hashlib

    canonical_sha256 = hashlib.sha256(canonical_af0.encode("utf-8")).hexdigest()
    assert manifest["spec_sha256"]["value"] == canonical_sha256

    # 対照実験: 生バイト列の sha256sum は意図的に異なることを直接確認する
    # （pretty-printed file vs canonical serialization）。
    raw_sha256 = _sha256_file(AF0_SPEC_PATH)
    assert raw_sha256 != canonical_sha256, (
        "founder_specs/AF0.json が canonical と偶然バイト同一になった場合、"
        "spec_sha256.verification の記述（『pretty-printed のため異なる』）"
        "を更新する必要がある"
    )


def test_revision02_af0_anchor_manifest_spec_sha256_matches_founder_manifest_record() -> None:
    """af0_anchor_manifest.json の spec_sha256 は
    results/AF0/founder_manifest.json#spec_sha256 の転記値と一致する。"""
    manifest = json.loads(AF0_ANCHOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    founder_manifest = json.loads(AF0_FOUNDER_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["spec_sha256"]["value"] == founder_manifest["spec_sha256"]


def test_revision02_af0_anchor_manifest_component_hashes_match_actual_files() -> None:
    """founder_manifest_sha256 / ingestion_sha256 / sha256sums_sha256 は、
    それぞれ対応する実ファイルの生バイト sha256sum と一致する。"""
    manifest = json.loads(AF0_ANCHOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    af0_results_dir = _FOUNDRY_DIR / "artificial_founder" / "results" / "AF0"
    assert manifest["founder_manifest_sha256"]["value"] == _sha256_file(
        af0_results_dir / "founder_manifest.json"
    )
    assert manifest["ingestion_sha256"]["value"] == _sha256_file(
        af0_results_dir / "measurements" / "ingestion.json"
    )
    assert manifest["sha256sums_sha256"]["value"] == _sha256_file(af0_results_dir / "SHA256SUMS.txt")


def test_revision02_af0_anchor_manifest_pins_p0_not_established_verdict() -> None:
    """AF-P0 の NOT_ESTABLISHED 判定を manifest が正しく継承していること
    （Duration/Energy/AG-alpha 非保持は不変 — DESIGN_RUN9_REVISION_0.2.md
    改訂2）。"""
    manifest = json.loads(AF0_ANCHOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["p0_verdict"]["overall_verdict"] == "NOT_ESTABLISHED"
    assert set(manifest["p0_verdict"]["failed_gates"]) == {"G10", "G11", "G13"}


def test_revision02_af0_anchor_manifest_sha_matches_domain_af0_pin() -> None:
    """af0_anchor_manifest.json の正規形 sha256（
    canonicalization_method フィールドが宣言する規約で再計算）が、
    domains/identity_domain_run9_v1.json の anchor_hashes.af0 と一致する。"""
    manifest = json.loads(AF0_ANCHOR_MANIFEST_PATH.read_text(encoding="utf-8"))
    recomputed = _sha256_canonical_json(manifest)
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    assert domain_raw["anchor_hashes"]["af0"] == recomputed


def test_revision02_domain_af0_and_ritsu_are_now_pinned_hex64() -> None:
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert m._SHA256_HEX_RE.match(domain.anchor_hashes["af0"])
    assert domain.anchor_hashes["ritsu"] == "88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76"


# --- item 3: backbone_checkpoint_sha PINNED かつ裁定値と一致 ----------------


def test_revision02_backbone_checkpoint_sha_pinned_and_matches_ruling(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["backbone_checkpoint_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == "6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a"


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第8巡対応 — Fix A: bundle sha のハッシュ規約統一
# ---------------------------------------------------------------------------


def test_revision02_compute_file_sha256_matches_actual_bundle_file_bytes() -> None:
    """`run9_schema.compute_file_sha256()` が inputs/backbone_runtime_bundle.json
    の実バイト sha256（`sha256sum` 相当）を正しく計算できること — 現状
    backbone_runtime_bundle_sha は PENDING のままだが、将来 PINNED 化する
    際の照合手順（このヘルパを呼ぶだけ）が実行可能であることを確認する
    （Codex bot レビュー PR #316 第8巡指摘A採用）。"""
    import hashlib

    computed = m.compute_file_sha256(BACKBONE_BUNDLE_PATH)
    expected = hashlib.sha256(BACKBONE_BUNDLE_PATH.read_bytes()).hexdigest()
    assert computed == expected
    assert m._SHA256_HEX_RE.match(computed)


def test_revision02_compute_file_sha256_matches_design_doc_sha256_pin() -> None:
    """対照実験: 既に PINNED 済みの design_doc_sha256 も
    `compute_file_sha256()` で再現できること（design_doc_sha256 と同一
    規約であることの直接確認）。"""
    contract_raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert m.compute_file_sha256(DESIGN_DOC_PATH) == contract_raw["design_doc_sha256"]["value"]
    assert (
        m.compute_file_sha256(REVISION_DOC_PATH)
        == contract_raw["design_revision_doc_sha256"]["value"]
    )


def test_revision02_backbone_runtime_bundle_sha_wording_uses_raw_byte_convention() -> None:
    """Codex bot レビュー PR #316 第8巡指摘A: RUN9_CONTRACT.yaml の
    backbone_runtime_bundle_sha 関連コメント/reason に「正規形 sha256」の
    文言が残っていない（design_doc_sha256 と同一の実バイト規約へ統一
    済み）こと。af0_anchor_manifest.json（意図的な正規形規約の例外）の
    言及自体は許容する。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    lines = contract_text.splitlines()
    for i, line in enumerate(lines):
        if "backbone_runtime_bundle_sha" in line or "backbone" in line.lower():
            # backbone 関連の行/直後の reason 行に「正規形」が単独で出て
            # いないこと（af0 との対比説明の一部として「正規形」の語自体は
            # コメント中に許容するが、"bundle 正規形" のような直結表現は
            # 禁止する）。
            assert "bundle 正規形" not in line, f"line {i}: {line!r}"
    assert "bundle 正規形" not in contract_text


def test_revision02_af0_vs_bundle_hash_convention_difference_documented() -> None:
    """af0（正規形規約）と bundle（実バイト規約）の差異理由が、
    compute_file_sha256() の docstring または contract コメントに
    明記されていることの直接確認。"""
    schema_source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    combined = schema_source + contract_text
    assert "af0" in combined.lower()
    assert "canonicalization_method" in schema_source or "正規形" in contract_text
    assert "compute_file_sha256" in schema_source


def test_revision02_backbone_runtime_bundle_sha_pending_while_render_commit_unconfirmed(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #316 第1巡指摘（P2, 866fcc8, 採用）:
    bundle 内 `render_code_commit` は直接記録ではなく推論値（RUN6 export
    記録 s5_record 自体には commit が明記されていない）のため
    `status: "INFERRED_UNCONFIRMED"` へ降格した。連動して
    `backbone_runtime_bundle_sha` も PINNED から PENDING へ降格している
    （`backbone_checkpoint_sha` は直接記録4件一致のため対象外・PINNED の
    まま — 下の `test_revision02_backbone_checkpoint_sha_pinned_and_matches_ruling`
    が別途確認する）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle["render_code_commit"]["status"] == "INFERRED_UNCONFIRMED"

    field = contract_raw["backbone_runtime_bundle_sha"]
    assert field["status"] == "PENDING"
    assert field["value"] is None
    assert "INFERRED_UNCONFIRMED" in field["reason"] or "render_code_commit" in field["reason"]


def test_revision02_render_code_commit_status_and_bundle_sha_status_are_consistent(
    contract_raw: Dict[str, Any],
) -> None:
    """負例的整合検査: bundle json の `render_code_commit.status` が
    `INFERRED_UNCONFIRMED` である間は、contract の
    `backbone_runtime_bundle_sha.status` が `PINNED` になっていないこと
    （両者の食い違いを機械的に検出する）。将来 render_code_commit が
    確定（direct record または User attestation）して status が変わったら、
    このテストも合わせて更新が必要になる — その追随漏れ自体を検出する
    ためのテストでもある。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    bundle_field_status = contract_raw["backbone_runtime_bundle_sha"]["status"]
    if bundle["render_code_commit"]["status"] == "INFERRED_UNCONFIRMED":
        assert bundle_field_status != "PINNED", (
            "backbone_runtime_bundle_sha は PINNED だが、bundle 内 render_code_commit は "
            "依然 INFERRED_UNCONFIRMED — 未確定の推論値を含む bundle を PINNED として "
            "契約に取り込んでしまっている"
        )


def test_revision02_backbone_runtime_bundle_sha_matches_actual_file_once_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """`backbone_runtime_bundle_sha` の実ファイル照合を、design_doc_sha256
    / design_revision_doc_sha256 の既存テスト（`test_revision02_doc_sha256_pin_matches_actual_file`
    / `test_revision02_compute_file_sha256_matches_design_doc_sha256_pin`）
    と同型で事前配線する（Codex bot レビュー PR #316 第9巡指摘, e490985,
    部分採用）。現状 status は PENDING のため value は None のままである
    ことだけを確認するが、将来 render_code_commit が確定して本欄が
    PINNED へ昇格した瞬間、この同じテストが
    `compute_file_sha256(inputs/backbone_runtime_bundle.json)` との一致を
    自動的に強制するようになる（テストコードの変更を要さない）。

    **層分離の境界宣言（変更しない）**: この照合はテスト層にのみ配線し、
    `load_run9_contract()`/`gate_state()` 側（loader/runtime 層）へは
    配線しない — PR #315 第4巡の境界宣言どおり、contract loader は
    事前登録契約の構造述語（型・整形式・状態の整合）を検査する層であり、
    pin 値と実体ファイルの突合は R9-G1（INPUT_FREEZE_AND_RIGHTS）検証
    ツーリングの職務として分離する。テスト層の事前配線はこの分離を
    崩さない — pin 前は「PENDING であること」だけを検査し、実体突合の
    強制はテストが検出するだけで loader の受理・拒否には影響しない。
    """
    field = contract_raw["backbone_runtime_bundle_sha"]
    if field["status"] == "PINNED":
        assert field["value"] == m.compute_file_sha256(BACKBONE_BUNDLE_PATH), (
            "backbone_runtime_bundle_sha が PINNED を宣言しているが、"
            "inputs/backbone_runtime_bundle.json の実バイト sha256 と一致しない"
        )
    else:
        assert field["status"] == "PENDING"
        assert field["value"] is None


def test_revision02_render_code_commit_value_and_confirmation_metadata_present() -> None:
    """降格後も値自体（推論結果）と根拠・確定条件は保持されていること。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    rcc = bundle["render_code_commit"]
    assert rcc["commit_full"] == "e2307b1080b00f3999702ce9017cfd75c7f862fe"
    assert rcc["commit_short"] == "e2307b1"
    assert rcc["status"] == "INFERRED_UNCONFIRMED"
    assert rcc["confirmation_required"]
    assert rcc["inference_basis"]
    # RUN6 export 記録自体には commit が明記されていない事実が明文化されていること。
    assert "s5_record" in rcc["note"]
    assert "does not" in rcc["note"].lower() or "does NOT" in rcc["note"]


def test_revision02_backbone_bundle_acoustic_onnx_matches_ruling() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert (
        bundle["acoustic_onnx_sha256"]["value"]
        == "aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706"
    )


def test_revision02_backbone_bundle_checkpoint_matches_contract() -> None:
    """backbone_runtime_bundle.json と RUN9_CONTRACT.yaml の
    backbone_checkpoint_sha が同一値を pin していること（二重管理の不整合
    が無いことの確認）。checkpoint_sha256 自体は render_code_commit の
    降格とは独立に、両ファイルとも引き続き PINNED 相当の値を持つ。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    contract_raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert bundle["checkpoint_sha256"]["value"] == contract_raw["backbone_checkpoint_sha"]["value"]


def test_revision02_backbone_bundle_run7_not_used_records_teacher_swap_reason() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "教師交代" in bundle["run7_not_used"]["reason"] or "teacher swap" in bundle["run7_not_used"]["reason"].lower()


# --- item 4: rights_manifest が PENDING_USER_ATTESTATION の間、domain user
#     anchor は未 pin のまま（gate BLOCKED 継続） -----------------------------


def test_revision02_rights_manifest_is_pending_user_attestation() -> None:
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert rights["rights_class"] == "PENDING_USER_ATTESTATION"
    assert rights["consent_status"] == "PENDING_USER_ATTESTATION"
    assert rights["attestation"]["attested"] is False
    assert rights["usage_grants"]["raw_audio_publication"] == "not_granted"
    assert rights["usage_grants"]["model_general_distribution"] == "not_granted"


def _load_rights_manifest_and_ledger() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """rights_manifest / donor_ledger を、`verify_rights_manifest_against_ledger()`
    が規定する重複キー拒否読込経路（`run9_schema.load_rights_manifest_json()`
    / `load_user_donor_ledger_json()`）経由で読み込む — 生の `json.loads()`
    は使わない（Codex bot レビュー PR #316 第10巡指摘採用, c34bdff: 生
    `json.loads()` は同一 entry 内の重複キーを last-key-wins で黙って
    解決してしまうため、rights 検証テスト群全体をこの2関数経由へ統一する）。
    """
    rights = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    ledger = m.load_user_donor_ledger_json(
        (_FOUNDRY_DIR / "recording_kit" / "user_donor_ledger.json").read_text(encoding="utf-8")
    )
    return rights, ledger


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第10巡対応（本 PR 最終レビュー対応巡）—
# rights manifest / ledger 読込への重複キー拒否の適用
# ---------------------------------------------------------------------------


def test_revision02_load_rights_manifest_json_rejects_duplicate_entry_key() -> None:
    """負例: rights_manifest の1エントリ内で同一キー（`sha256`）が2回
    異なる値で宣言された生 JSON テキストが、`load_rights_manifest_json()`
    の読込段で拒否されること（生の `json.loads()` なら
    last-key-wins で後勝ちの値だけが黙って通り、手編集で「たまたま期待値
    に潰れた」曖昧な入力がそのまま検証器へ届いてしまっていた —
    Codex bot レビュー PR #316 第10巡指摘採用）。"""
    duplicate_key_text = """
    {
      "schema": "run9-user-donor-rights/1.0",
      "entries": [
        {
          "card_id": "UC-001",
          "source_sha256": "%s",
          "sha256": "%s",
          "duration_sec": 1.0,
          "sha256": "%s"
        }
      ]
    }
    """ % ("a" * 64, "b" * 64, "c" * 64)
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.load_rights_manifest_json(duplicate_key_text)


def test_revision02_load_user_donor_ledger_json_rejects_duplicate_entry_key() -> None:
    """負例: donor_ledger 側でも同様に、1エントリ内の重複キー
    （`card_id`、値相違）を持つ生 JSON テキストが読込段で拒否されること。"""
    duplicate_key_text = """
    {
      "schema": "user-donor-ledger/0.1",
      "entries": [
        {
          "card_id": "UC-001",
          "source_sha256": "%s",
          "sha256": "%s",
          "duration_sec": 1.0,
          "card_id": "UC-002"
        }
      ]
    }
    """ % ("a" * 64, "b" * 64)
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.load_user_donor_ledger_json(duplicate_key_text)


def test_revision02_load_rights_manifest_json_and_ledger_accept_well_formed_text() -> None:
    """対照実験: 重複キーの無い実ファイルは引き続き読み込める（正常系まで
    壊していないことの確認）。"""
    rights = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    ledger = m.load_user_donor_ledger_json(
        (_FOUNDRY_DIR / "recording_kit" / "user_donor_ledger.json").read_text(encoding="utf-8")
    )
    assert isinstance(rights, dict) and isinstance(ledger, dict)
    m.verify_rights_manifest_against_ledger(rights, ledger)  # 例外を投げないことの確認


def test_revision02_load_rights_manifest_json_rejects_non_object_top_level() -> None:
    """負例: トップレベルがオブジェクトでない JSON は拒否されること。"""
    with pytest.raises(m.Run9ValidationError, match="must be an object"):
        m.load_rights_manifest_json("[]")


def test_revision02_verify_rights_manifest_docstring_specifies_strict_loader_input() -> None:
    """`verify_rights_manifest_against_ledger()` の docstring が、入力は
    `load_rights_manifest_json()`/`load_user_donor_ledger_json()` 経由で
    読み込むことを規定していることの直接確認。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "load_rights_manifest_json" in source
    assert "load_user_donor_ledger_json" in source


def test_revision02_rights_manifest_entries_match_donor_ledger() -> None:
    """rights_manifest.json の17件が user_donor_ledger.json の実測値と
    過不足なく一致すること（card_id/source_sha256/sha256/duration_sec）。
    実際の検査ロジックは `run9_schema.verify_rights_manifest_against_ledger()`
    （loader 側ヘルパ、Codex bot レビュー PR #316 第3巡指摘 0a4d0cf 採用）を
    呼ぶだけにする — attest 後の実運用でも同じ検査が効くようにするため。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    assert len(rights["entries"]) == len(ledger["entries"]) == 17
    m.verify_rights_manifest_against_ledger(rights, ledger)  # 例外を投げないことの確認


def test_revision02_rights_manifest_card_id_set_matches_ledger_exactly() -> None:
    """card_id 集合そのものが ledger と完全一致すること（過不足双方の検出）
    を直接確認する。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    rights_ids = {e["card_id"] for e in rights["entries"]}
    ledger_ids = {e["card_id"] for e in ledger["entries"]}
    assert rights_ids == ledger_ids
    assert len(rights_ids) == len(rights["entries"]), "rights_manifest 内に重複 card_id がある"


def test_revision02_verify_rights_manifest_rejects_duplicate_card_id() -> None:
    """負例: rights_manifest 側で card_id を重複させた（= 1本欠落 + 1本二重）
    合成 fixture が拒否されること。UC-017 を UC-016 の複製に差し替える —
    件数は17件のまま・両方とも ledger 側に実在する card_id のため、件数
    一致 + ledger からの引き当てだけの旧検査は素通りしていた欠陥の再現。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    uc016 = next(e for e in tampered["entries"] if e["card_id"] == "UC-016")
    for entry in tampered["entries"]:
        if entry["card_id"] == "UC-017":
            entry["card_id"] = "UC-016"
            entry["source_sha256"] = uc016["source_sha256"]
            entry["sha256"] = uc016["sha256"]
            entry["duration_sec"] = uc016["duration_sec"]
    assert len(tampered["entries"]) == 17  # 件数は変わらない（旧検査が見逃す条件を再現）
    with pytest.raises(m.Run9ValidationError, match="duplicate card_id"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_missing_card_id() -> None:
    """負例: rights_manifest から1件（UC-017）を欠落させた合成 fixture が
    拒否されること。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    tampered["entries"] = [e for e in tampered["entries"] if e["card_id"] != "UC-017"]
    with pytest.raises(m.Run9ValidationError, match="does not exactly match"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_extra_card_id() -> None:
    """負例: ledger に存在しない card_id を rights_manifest 側に追加した
    合成 fixture が拒否されること。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    forged_entry = dict(tampered["entries"][0])
    forged_entry["card_id"] = "UC-999"
    tampered["entries"].append(forged_entry)
    with pytest.raises(m.Run9ValidationError, match="does not exactly match"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_value_mismatch() -> None:
    """負例: card_id 集合は正しく、整形式（64hex）でもあるが、値そのものが
    ledger と食い違う fixture が拒否されること。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    tampered["entries"][0]["sha256"] = "f" * 64  # 整形式は正しいが ledger の実値とは不一致
    with pytest.raises(m.Run9ValidationError, match="does not match"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第4巡対応 — 両側欠落穴（None == None すり抜け）
# の閉塞
# ---------------------------------------------------------------------------


def test_revision02_verify_rights_manifest_rejects_both_sides_missing_field() -> None:
    """負例: rights_manifest 側と donor_ledger 側の両方から同じ必須
    フィールド（source_sha256）が欠落した合成ペアが拒否されること。
    旧実装は `entry.get(field)` 同士の等値比較のみのため、両側欠落だと
    `None == None` で素通りしていた（Codex bot レビュー PR #316 第4巡
    指摘, 4b1c872, 採用）。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_rights = copy.deepcopy(rights)
    tampered_ledger = copy.deepcopy(ledger)
    for entry in tampered_rights["entries"]:
        if entry["card_id"] == "UC-001":
            del entry["source_sha256"]
    for entry in tampered_ledger["entries"]:
        if entry["card_id"] == "UC-001":
            del entry["source_sha256"]
    with pytest.raises(m.Run9ValidationError, match="missing required field"):
        m.verify_rights_manifest_against_ledger(tampered_rights, tampered_ledger)


def test_revision02_verify_rights_manifest_rejects_one_side_missing_field() -> None:
    """負例: rights_manifest 側のみ必須フィールド（duration_sec）が欠落した
    場合も拒否されること（donor_ledger 側は無傷）。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_rights = copy.deepcopy(rights)
    for entry in tampered_rights["entries"]:
        if entry["card_id"] == "UC-001":
            del entry["duration_sec"]
    with pytest.raises(m.Run9ValidationError, match="missing required field"):
        m.verify_rights_manifest_against_ledger(tampered_rights, ledger)


def test_revision02_verify_rights_manifest_rejects_non_64hex_sha() -> None:
    """負例: sha256 フィールドが64hex形式でない値（短い/大文字/非hex文字）
    はいずれも拒否されること。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    for bad_value in ("not-hex", "A" * 64, "f" * 63, ""):
        tampered = copy.deepcopy(rights)
        for entry in tampered["entries"]:
            if entry["card_id"] == "UC-001":
                entry["source_sha256"] = bad_value
        with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
            m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_non_positive_or_bool_duration() -> None:
    """負例: duration_sec が bool・0・負値・非有限（NaN/inf）のいずれも
    拒否されること。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    for bad_value in (True, False, 0, -1.5, float("nan"), float("inf")):
        tampered = copy.deepcopy(rights)
        for entry in tampered["entries"]:
            if entry["card_id"] == "UC-001":
                entry["duration_sec"] = bad_value
        with pytest.raises(m.Run9ValidationError):
            m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_require_rights_ledger_helpers_accept_well_formed_values() -> None:
    """対照実験: 正しい形の値はヘルパ単体でも通過する（正常系まで壊して
    いないことの確認）。"""
    assert (
        m._require_rights_ledger_sha256_hex("a" * 64, side="rights_manifest", card_id="UC-001", field="sha256")
        == "a" * 64
    )
    assert m._require_rights_ledger_positive_duration(
        12.5, side="donor_ledger", card_id="UC-001"
    ) == 12.5


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第5巡対応 — rights 検証器ファミリー終端
# (Fix A: schema 版の厳密検証 / Fix B: ledger 側の重複 card_id 拒否)
# ---------------------------------------------------------------------------


def test_revision02_verify_rights_manifest_rejects_wrong_rights_schema() -> None:
    """負例（Fix A）: rights_manifest.schema が別値だと拒否される。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    tampered["schema"] = "run9-user-donor-rights/0.9"
    with pytest.raises(m.Run9ValidationError, match="rights_manifest.schema"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_missing_rights_schema() -> None:
    """負例（Fix A）: rights_manifest.schema が欠落していると拒否される。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered = copy.deepcopy(rights)
    del tampered["schema"]
    with pytest.raises(m.Run9ValidationError, match="rights_manifest.schema"):
        m.verify_rights_manifest_against_ledger(tampered, ledger)


def test_revision02_verify_rights_manifest_rejects_wrong_ledger_schema() -> None:
    """負例（Fix A）: donor_ledger.schema が別値だと拒否される。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_ledger = copy.deepcopy(ledger)
    tampered_ledger["schema"] = "user-donor-ledger/0.2"
    with pytest.raises(m.Run9ValidationError, match="donor_ledger.schema"):
        m.verify_rights_manifest_against_ledger(rights, tampered_ledger)


def test_revision02_verify_rights_manifest_rejects_missing_ledger_schema() -> None:
    """負例（Fix A）: donor_ledger.schema が欠落していると拒否される。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_ledger = copy.deepcopy(ledger)
    del tampered_ledger["schema"]
    with pytest.raises(m.Run9ValidationError, match="donor_ledger.schema"):
        m.verify_rights_manifest_against_ledger(rights, tampered_ledger)


def test_revision02_verify_rights_manifest_rejects_duplicate_ledger_card_id() -> None:
    """負例（Fix B）: donor_ledger 側で同一 card_id が2回（hash 相違）
    出現する合成 ledger が拒否されること — 第3巡は rights 側のみ重複拒否
    しており、ledger 側は last-entry-wins で黙って解決していた非対称の
    解消。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_ledger = copy.deepcopy(ledger)
    duplicate_entry = copy.deepcopy(tampered_ledger["entries"][0])
    duplicate_entry["source_sha256"] = "f" * 64
    duplicate_entry["sha256"] = "e" * 64
    tampered_ledger["entries"].append(duplicate_entry)
    with pytest.raises(m.Run9ValidationError, match="duplicate card_id"):
        m.verify_rights_manifest_against_ledger(rights, tampered_ledger)


def test_revision02_verify_rights_manifest_correct_schemas_and_ledger_still_pass() -> None:
    """対照実験: schema・ledger の重複無しの正常系は引き続き通る（Fix A/B
    が正常系まで壊していないことの確認）。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    assert rights["schema"] == "run9-user-donor-rights/1.0"
    assert ledger["schema"] == "user-donor-ledger/0.1"
    m.verify_rights_manifest_against_ledger(rights, ledger)  # 例外を投げないことの確認


def test_revision02_verify_rights_manifest_docstring_declares_family_termination() -> None:
    """rights 検証器ファミリーの終端宣言（PR #316 第3〜6巡・期待集合の
    凍結を含め完結）がソース中に明文化されていることの直接確認。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "全数掃討・" in source and "終端" in source
    assert "境界宣言" in source
    assert "USER_DONOR_CARD_IDS" in source


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第6巡対応 — RUN9 期待 donor 集合の凍結
# ---------------------------------------------------------------------------


def test_revision02_user_donor_card_ids_constant_is_uc001_to_uc017() -> None:
    """USER_DONOR_CARD_IDS は User 裁定4の逐語「UC-001〜017」の機械化 —
    17件・重複無し・UC-001〜UC-017 の完全形。"""
    assert m.USER_DONOR_CARD_IDS == tuple(f"UC-{i:03d}" for i in range(1, 18))
    assert len(m.USER_DONOR_CARD_IDS) == len(set(m.USER_DONOR_CARD_IDS)) == 17


def test_revision02_verify_rights_manifest_rejects_both_sides_swapped_card_id() -> None:
    """負例: rights_manifest と donor_ledger の**両側同時**に UC-017 を
    UC-999 へ差し替えた合成ペアが拒否されること。item 2（相互一致）だけ
    では両側が同じ ID へ揃って差し替わる攻撃を検出できず、外部の凍結
    参照点 USER_DONOR_CARD_IDS との突き合わせ（item 6）が必要な理由の
    再現（Codex bot レビュー PR #316 第6巡指摘, be8f448, 採用）。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_rights = copy.deepcopy(rights)
    tampered_ledger = copy.deepcopy(ledger)
    for entry in tampered_rights["entries"]:
        if entry["card_id"] == "UC-017":
            entry["card_id"] = "UC-999"
    for entry in tampered_ledger["entries"]:
        if entry["card_id"] == "UC-017":
            entry["card_id"] = "UC-999"
    # 前提確認: 相互一致（item 2 相当）はこの改変では壊れていない。
    assert {e["card_id"] for e in tampered_rights["entries"]} == {
        e["card_id"] for e in tampered_ledger["entries"]
    }
    with pytest.raises(m.Run9ValidationError, match="USER_DONOR_CARD_IDS"):
        m.verify_rights_manifest_against_ledger(tampered_rights, tampered_ledger)


def test_revision02_verify_rights_manifest_rejects_rights_only_swapped_card_id() -> None:
    """負例（対照）: rights 側のみ UC-017→UC-999 に差し替えた場合も拒否
    されること（item 2 の相互不一致経由でも、item 6 の凍結集合経由でも
    どちらでも検出できる状態であることの確認）。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    tampered_rights = copy.deepcopy(rights)
    for entry in tampered_rights["entries"]:
        if entry["card_id"] == "UC-017":
            entry["card_id"] = "UC-999"
    with pytest.raises(m.Run9ValidationError):
        m.verify_rights_manifest_against_ledger(tampered_rights, ledger)


def test_revision02_verify_rights_manifest_current_files_match_frozen_donor_set() -> None:
    """対照実験: 現行 rights_manifest.json / user_donor_ledger.json は
    USER_DONOR_CARD_IDS と完全一致し、正常系まで壊していないこと。"""
    rights, ledger = _load_rights_manifest_and_ledger()
    expected = set(m.USER_DONOR_CARD_IDS)
    assert {e["card_id"] for e in rights["entries"]} == expected
    assert {e["card_id"] for e in ledger["entries"]} == expected
    m.verify_rights_manifest_against_ledger(rights, ledger)  # 例外を投げないことの確認


def test_revision02_domain_user_anchor_still_unpinned_while_rights_pending() -> None:
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.anchor_hashes["user"] == "<PIN_BEFORE_RUN>"
    assert domain.is_pinned() is False


def test_revision02_gate_remains_blocked_after_af0_ritsu_backbone_pins(
    contract: m.Run9RunContract,
) -> None:
    """af0/ritsu anchor と backbone (checkpoint + runtime bundle) が新たに
    PINNED になっても、user anchor / lesson / VG-L0 ハーネス関連欄が
    PENDING のままである限り gate_state() は "BLOCKED" のまま
    （部分的な pin 進展だけでは READY へ到達しないことの機械証明）。"""
    assert m.gate_state(contract) == "BLOCKED"


def test_revision02_build_founder_still_rejects_current_domain_draft() -> None:
    """user anchor 未 pin のため、現行 domain draft からは
    build_founder() が依然として拒否されること（Phase 0.2 でも段階3→4の
    機械強制が有効なままであることの確認）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(domain, "R9F-01")


# --- README 更新の整合確認 --------------------------------------------------


def test_revision02_readme_mentions_revision_0_2() -> None:
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "0.2" in readme
    assert "2026-08-24" in readme


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第2巡対応 — runtime bundle に RUN6 render フロー
# の全消費物（canon model assets）を追加
# ---------------------------------------------------------------------------


def test_revision02_bundle_has_canon_model_assets_section() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "canon_model_assets" in bundle
    assert "assets" in bundle["canon_model_assets"]
    assert set(bundle["canon_model_assets"]["assets"].keys()) == {
        "linguistic_onnx",
        "variance_duration_onnx",
        "variance_pitch_onnx",
        "phonemes_txt",
    }


def test_revision02_bundle_canon_model_assets_entries_have_64hex_and_source() -> None:
    """canon_model_assets.assets / acoustic_export_companions の各リーフ
    エントリは、value が64hex sha256 なら source を持ち、value が確定して
    いない（PENDING 相当）なら reason を持つこと（Codex bot レビュー
    PR #316 第2巡指摘）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    checked = 0
    for section_name in ("assets", "acoustic_export_companions"):
        section = bundle["canon_model_assets"][section_name]
        for entry_name, entry in section.items():
            if not isinstance(entry, dict) or "value" not in entry:
                continue  # "note" のような非エントリ・メタキーはスキップ
            checked += 1
            value = entry["value"]
            if value is None or (isinstance(value, str) and value.strip() in ("", "<PENDING>")):
                assert "reason" in entry, (
                    f"canon_model_assets.{section_name}.{entry_name} は未確定値だが reason が無い"
                )
            else:
                assert isinstance(value, str) and m._SHA256_HEX_RE.match(value), (
                    f"canon_model_assets.{section_name}.{entry_name}.value は64hex sha256 で"
                    f"なければならない: {value!r}"
                )
                assert "source" in entry, (
                    f"canon_model_assets.{section_name}.{entry_name} は確定値だが source が無い"
                )
    assert checked >= 7, "canon_model_assets 配下のエントリ検査が発火していない（空洞化防止）"


def test_revision02_bundle_canon_model_assets_values_match_probe_records() -> None:
    """canon_model_assets の各値が、一次ソースの probe result JSON の
    実測値と一致すること（転記誤りの検出）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    probe = json.loads(
        (_RUN_DIR.parent / "records" / "vgl0_control_axis_probe_result_n6.json").read_text(
            encoding="utf-8"
        )
    )
    pins = probe["pins"]
    assets = bundle["canon_model_assets"]["assets"]
    assert assets["linguistic_onnx"]["value"] == pins["canon_linguistic_onnx"]["sha256"]
    assert assets["variance_duration_onnx"]["value"] == pins["canon_dur_onnx"]["sha256"]
    assert assets["variance_pitch_onnx"]["value"] == pins["canon_pitch_onnx"]["sha256"]
    assert assets["phonemes_txt"]["value"] == pins["canon_phonemes"]["sha256"]

    companions = bundle["canon_model_assets"]["acoustic_export_companions"]
    assert companions["dsconfig_yaml"]["value"] == pins["acoustic_dsconfig"]["sha256"]
    assert companions["acoustic_phonemes_json"]["value"] == pins["acoustic_phonemes_json"]["sha256"]
    assert companions["speaker_embed"]["value"] == pins["speaker_embed"]["sha256"]

    # acoustic_onnx / vocoder_onnx の両方が、bundle 側の既存 top-level pin
    # とも probe record 側とも一致すること（run6 backbone の同一性の
    # 追加の交差確認）。
    assert bundle["acoustic_onnx_sha256"]["value"] == pins["acoustic_onnx"]["sha256"]
    assert bundle["vocoder"]["runtime_onnx_sha256"]["value"] == pins["vocoder_onnx"]["sha256"]


def test_revision02_bundle_canon_model_assets_cross_checked_across_4_probe_records() -> None:
    """canon_model_assets の各値が、独立した4件の probe result（n6 / 無印 /
    n10 / render_reproducibility）すべてで同一であることを確認する
    （n6 以外の3件は補助的な相互一致確認）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assets = bundle["canon_model_assets"]["assets"]
    records_dir = _RUN_DIR.parent / "records"
    for filename in (
        "vgl0_control_axis_probe_result.json",
        "vgl0_control_axis_probe_result_n10.json",
        "vgl0_render_reproducibility_result.json",
    ):
        probe = json.loads((records_dir / filename).read_text(encoding="utf-8"))
        pins = probe["pins"]
        assert assets["linguistic_onnx"]["value"] == pins["canon_linguistic_onnx"]["sha256"], filename
        assert assets["variance_duration_onnx"]["value"] == pins["canon_dur_onnx"]["sha256"], filename
        assert assets["variance_pitch_onnx"]["value"] == pins["canon_pitch_onnx"]["sha256"], filename


def test_revision02_bundle_canon_model_source_distribution_is_distinct_from_ritsu_anchor() -> None:
    """canon model distribution（NamineRitsu_DiffSinger.zip）の sha256 が、
    RUN9 identity anchor として pin されている波音リツ配布 zip（別ファイル）
    の sha256 と異なることを直接確認する（両者を混同していないことの
    構造的検査）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    canon_zip_sha = bundle["canon_model_assets"]["source_distribution"]["sha256"]
    ritsu_anchor_sha = domain_raw["anchor_hashes"]["ritsu"]
    assert canon_zip_sha != ritsu_anchor_sha
    assert canon_zip_sha == "5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530"
    assert ritsu_anchor_sha == "88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76"


def test_revision02_bundle_completeness_note_explains_canon_assets_are_required() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "completeness_note" in bundle
    note = bundle["completeness_note"]
    assert "canon" in note.lower()
    assert "acoustic" in note.lower()


def test_revision02_backbone_runtime_bundle_sha_still_pending_after_canon_assets_added(
    contract_raw: Dict[str, Any],
) -> None:
    """canon_model_assets 追加は render_code_commit の確定状態を変えない
    ため、`backbone_runtime_bundle_sha` は引き続き PENDING のまま
    （変更なし — この巡の追加が既存の降格判断へ副作用しないことの確認）。"""
    field = contract_raw["backbone_runtime_bundle_sha"]
    assert field["status"] == "PENDING"
    assert field["value"] is None


# ---------------------------------------------------------------------------
# User 裁定 2026-08-24（PoR メモ編入、design_revision 0.2 -> 0.3）対応
# ---------------------------------------------------------------------------


def test_por_revision_design_revision_doc_path_exists() -> None:
    assert REVISION_DOC_PATH.exists()
    assert REVISION_DOC_PATH.name == "DESIGN_RUN9_REVISION_0.3.md"


def test_por_revision_por_adjudication_path_exists() -> None:
    assert POR_ADJUDICATION_PATH.exists()
    assert POR_ADJUDICATION_PATH.name == "POR_CONCEPT_ADJUDICATION_20260824.txt"


# --- 改訂A: interventions 構造 / BRANCH_REVISIONS / BIRTH_EDGE -------------


def test_por_revision_intervention_edges_frozen_values() -> None:
    """PoR §3.2/§3.3: 稽古=PRACTICE_FROM_AUDIO, 教育=TRANSFER_TECHNIQUE の
    順で凍結される（PoR §16 最小実験図の左→右の記載順とも整合）。"""
    assert m.INTERVENTION_EDGES == ("PRACTICE_FROM_AUDIO", "TRANSFER_TECHNIQUE")


def test_por_revision_branch_revisions_frozen_mapping() -> None:
    """PoR §4 比較構造 + User 外部レビュー PR #317 P1-3 採用（C0/C1 分離）:
    CONTROL は条件別2値のネスト mapping
    （NO_LEARNING_REPLAY->replay, ZERO_CONTROLPROFILE_SHAM->r_sham）、
    PRACTICE_FROM_AUDIO->r_practice, TRANSFER_TECHNIQUE->r_taught の対応が
    凍結されている。"""
    assert dict(m.BRANCH_REVISIONS["CONTROL"]) == {
        "NO_LEARNING_REPLAY": "replay",
        "ZERO_CONTROLPROFILE_SHAM": "r_sham",
    }
    assert m.BRANCH_REVISIONS["PRACTICE_FROM_AUDIO"] == "r_practice"
    assert m.BRANCH_REVISIONS["TRANSFER_TECHNIQUE"] == "r_taught"
    assert set(m.BRANCH_REVISIONS.keys()) == {"CONTROL", "PRACTICE_FROM_AUDIO", "TRANSFER_TECHNIQUE"}


def test_por_revision_branch_revisions_is_immutable_mapping() -> None:
    """BRANCH_REVISIONS（および CONTROL の入れ子 mapping）は他の凍結 dict
    （Run9IdentityDomain の anchor_hashes 等）と同様に
    types.MappingProxyType で直接改変を防ぐ。"""
    import types as _types

    assert isinstance(m.BRANCH_REVISIONS, _types.MappingProxyType)
    assert isinstance(m.BRANCH_REVISIONS["CONTROL"], _types.MappingProxyType)
    with pytest.raises(TypeError):
        m.BRANCH_REVISIONS["CONTROL"] = "tampered"  # type: ignore[index]
    with pytest.raises(TypeError):
        m.BRANCH_REVISIONS["CONTROL"]["NO_LEARNING_REPLAY"] = "tampered"  # type: ignore[index]


def test_por_revision_birth_edge_does_not_change_operator_id() -> None:
    """PoR §3.1 / DESIGN_RUN9_REVISION_0.3.md 改訂A: INHERIT_TRAIT の導入は
    TRI_CROSSOVER operator の計算規約（genome_id 決定論）を変更しない —
    OPERATOR_ID 定数がそのまま TRI_CROSSOVER/1.0 であることを確認する。"""
    assert m.BIRTH_EDGE == "INHERIT_TRAIT"
    assert m.OPERATOR_ID == "TRI_CROSSOVER/1.0"


# --- 改訂D: 結果分類語彙（PoR §13 逐語） -----------------------------------


def test_por_revision_birth_outcomes_verbatim() -> None:
    assert m.BIRTH_OUTCOMES == ("ESTABLISHED", "NOT_ESTABLISHED")


def test_por_revision_practice_outcomes_verbatim() -> None:
    assert m.PRACTICE_OUTCOMES == ("GAIN_ESTABLISHED", "NO_GAIN", "UNOBSERVABLE")


def test_por_revision_education_outcomes_verbatim() -> None:
    assert m.EDUCATION_OUTCOMES == ("TRANSFER_ESTABLISHED", "NO_TRANSFER", "UNOBSERVABLE")


def test_por_revision_separation_outcomes_verbatim() -> None:
    assert m.SEPARATION_OUTCOMES == (
        "MACHINE_EVIDENCE_SUPPORTED", "MIXED", "NOT_ESTABLISHED",
    )


def test_por_revision_founder_response_outcomes_verbatim() -> None:
    assert m.FOUNDER_RESPONSE_OUTCOMES == (
        "DIFFERENTIAL_RESPONSE", "COMMON_RESPONSE", "UNDETERMINED",
    )


def test_por_revision_identity_outcomes_verbatim() -> None:
    assert m.IDENTITY_OUTCOMES == (
        "STABLE_BY_MACHINE_METRIC", "SHIFTED", "UNCALIBRATED",
    )


def test_por_revision_outcome_vocabularies_have_no_internal_duplicates() -> None:
    """6分類それぞれの内部に重複値が無いこと（各分類自身の定義ミスの検出）。
    分類**間**での語彙の再利用（例: `"NOT_ESTABLISHED"` は BIRTH と
    SEPARATION の両方で、`"UNOBSERVABLE"` は PRACTICE と EDUCATION の
    両方で使われる）は PoR §13 の逐語どおりであり、意図的な共有 — これは
    禁止しない（「一つの PASS だけで全現象を代表させない」という PoR の
    趣旨は、6つの分類を**独立に判定する**ことであり、各分類の語彙が互いに
    素であることまでは要求していない）。"""
    all_vocabs = [
        m.BIRTH_OUTCOMES, m.PRACTICE_OUTCOMES, m.EDUCATION_OUTCOMES,
        m.SEPARATION_OUTCOMES, m.FOUNDER_RESPONSE_OUTCOMES, m.IDENTITY_OUTCOMES,
    ]
    for vocab in all_vocabs:
        assert len(vocab) == len(set(vocab)), f"duplicate value within {vocab!r}"


def test_por_revision_outcome_vocabulary_sizes_match_por_13() -> None:
    """PoR §13 逐語の各分類の要素数（BIRTH=2, 他5分類は各3）と一致する。"""
    assert len(m.BIRTH_OUTCOMES) == 2
    for vocab in (
        m.PRACTICE_OUTCOMES, m.EDUCATION_OUTCOMES, m.SEPARATION_OUTCOMES,
        m.FOUNDER_RESPONSE_OUTCOMES, m.IDENTITY_OUTCOMES,
    ):
        assert len(vocab) == 3


def test_por_revision_v01_transfer_status_superseded_note_present() -> None:
    """v0.1 §20 の transfer_status 語彙が rev 0.3 で superseded と明記
    されていること（DESIGN_RUN9_REVISION_0.3.md 改訂D）。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "transfer_status" in doc
    assert "superseded" in doc


# --- 改訂E: 失敗分類語彙（PoR §9） ------------------------------------------


def test_por_revision_failure_classes_verbatim() -> None:
    assert m.FAILURE_CLASSES == (
        "IMPLEMENTATION_FAILURE", "SCIENTIFIC_NULL", "DESIGN_FAILURE",
    )


# --- 改訂C: 情報境界の凍結定数（PoR §3.2/§3.3/§11） -------------------------


def test_por_revision_practice_forbidden_inputs_includes_spk_embedding_and_correct_parameter() -> (
    None
):
    """PRACTICE 禁止に spk embedding と正解 Technique parameter が含まれる
    こと（コーディネータ指示の設計必須項目）。PR #317 Codex bot レビュー
    第2巡 Fix 4 採用: PoR §3.2 冒頭「教師の正解パラメータやTechnique
    labelは与えず」の後半（Technique label）が第1巡実装時に転記漏れして
    いたため `teacher_technique_label` を追加し5要素へ是正。"""
    assert "pjs_speaker_embedding" in m.PRACTICE_FORBIDDEN_INPUTS
    assert "correct_technique_parameter" in m.PRACTICE_FORBIDDEN_INPUTS
    assert "teacher_technique_label" in m.PRACTICE_FORBIDDEN_INPUTS
    assert "pjs_identity_coordinate" in m.PRACTICE_FORBIDDEN_INPUTS
    assert "teacher_internal_parameter_dump" in m.PRACTICE_FORBIDDEN_INPUTS
    assert len(m.PRACTICE_FORBIDDEN_INPUTS) == 5


def test_por_revision_practice_allowed_data_inputs_and_operations_cover_por_3_2() -> None:
    """PoR §3.2 は許可を5項目列挙する。旧 `PRACTICE_ALLOWED_INPUTS`
    （User 外部レビュー PR #317 P2-1 採用で3分割・廃止）は、data/operation
    混在のまま5要素（`pjs_audio_direct_listen` + 4つの動作）を保持して
    いた。3分割後は `PRACTICE_ALLOWED_DATA_INPUTS`（データ）3要素と
    `PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS`（動作）5要素へ分かれる —
    「自律的模倣対象選択」（元 Fix 3 の転記漏れ是正対象）は動作側
    `imitation_target_selection` として保持されていることを確認する。"""
    assert "pjs_training_audio" in m.PRACTICE_ALLOWED_DATA_INPUTS
    assert len(m.PRACTICE_ALLOWED_DATA_INPUTS) == 3
    assert "feature_extraction" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    assert "imitation_target_selection" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    assert "self_teacher_difference_estimation" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    assert "candidate_generation" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    assert "allowed_range_search" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    assert len(m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS) == 5
    assert not hasattr(m, "PRACTICE_ALLOWED_INPUTS")


def test_por_revision_education_forbidden_inputs_includes_spk_embedding_and_raw_audio() -> None:
    """EDUCATION 禁止に spk embedding と raw audio 直接参照が含まれること
    （コーディネータ指示の設計必須項目 — PoR §11 の非対称性の核心）。"""
    assert "pjs_speaker_embedding" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "learner_pjs_raw_audio_direct_reference" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "pjs_identity_coordinate" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "pjs_voice_quality_latent" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "formant_inheritance_target" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "spectral_envelope_identity_replication" in m.EDUCATION_FORBIDDEN_INPUTS
    assert "founder_identity_replacement_parameter" in m.EDUCATION_FORBIDDEN_INPUTS


def test_por_revision_education_allowed_channels_covers_por_3_3_candidates() -> None:
    """EDUCATION 許可に timing/pitch trajectory 等が含まれること
    （コーディネータ指示の設計必須項目 — PoR §3.3 の許可候補列挙）。"""
    expected = {
        "timing", "phoneme_note_duration_relation", "pitch_trajectory",
        "dynamics_energy_trajectory", "onset_release_pattern", "vibrato_pattern",
        "phrasing", "phrase_end_control", "breath_placement",
    }
    assert expected == set(m.EDUCATION_ALLOWED_CHANNELS)


def test_por_revision_practice_and_education_forbidden_sets_do_not_overlap_allowed() -> None:
    """禁止 id が同じ経路の許可/必須 id 集合と重複していないこと（矛盾する
    語彙定義の検出）。PRACTICE 側は3分割後の3語彙全て相互に、EDUCATION
    側は従来どおり検査する。"""
    assert not (set(m.PRACTICE_FORBIDDEN_INPUTS) & set(m.PRACTICE_ALLOWED_DATA_INPUTS))
    assert not (
        set(m.PRACTICE_FORBIDDEN_INPUTS) & set(m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS)
    )
    assert not (
        set(m.PRACTICE_ALLOWED_DATA_INPUTS) & set(m.PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE)
    )
    assert not (
        set(m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS)
        & set(m.PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE)
    )
    assert not (set(m.EDUCATION_FORBIDDEN_INPUTS) & set(m.EDUCATION_ALLOWED_CHANNELS))


def test_por_revision_all_info_boundary_constants_are_nonempty_str_tuples() -> None:
    for vocab in (
        m.PRACTICE_FORBIDDEN_INPUTS, m.PRACTICE_ALLOWED_DATA_INPUTS,
        m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS, m.PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE,
        m.EDUCATION_ALLOWED_CHANNELS, m.EDUCATION_FORBIDDEN_INPUTS,
    ):
        assert isinstance(vocab, tuple) and len(vocab) > 0
        for item in vocab:
            assert isinstance(item, str) and item


# --- RUN9_CONTRACT.yaml の interventions 構造との整合 -----------------------


def test_por_revision_contract_interventions_edges_match_constant(
    contract_raw: Dict[str, Any],
) -> None:
    assert tuple(contract_raw["interventions"]["edges"]) == m.INTERVENTION_EDGES


def test_por_revision_contract_interventions_control_branch_matches_constant(
    contract_raw: Dict[str, Any],
) -> None:
    assert contract_raw["interventions"]["control_branch"] == m.CONTROL_BRANCH


def test_por_revision_contract_no_longer_has_single_intervention_key(
    contract_raw: Dict[str, Any],
) -> None:
    assert "single_intervention" not in contract_raw
    assert "interventions" in contract_raw


# --- design_revision 0.3 loader 検証: 既存 genome/domain/rights 系は無変更 --


def test_por_revision_genome_domain_rights_helpers_still_importable() -> None:
    """genome/domain/rights 系 API は rev 0.3 でシグネチャ変更していない
    ことの直接確認（TRI_CROSSOVER/genome_id 決定論を壊さないという
    コーディネータ指示の遵守）。"""
    assert hasattr(m, "build_run9_identity_domain")
    assert hasattr(m, "build_founder")
    assert hasattr(m, "founder_genome_from_dict")
    assert hasattr(m, "verify_rights_manifest_against_ledger")
    assert m.OPERATOR_ID == "TRI_CROSSOVER/1.0"


def test_por_revision_pinned_domain_and_founder_still_work_under_0_3(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """pinned_domain フィクスチャ経由の build_founder が rev 0.3 でも
    従来どおり動作し、genome_id が決定論的であること（同じ入力なら同じ
    genome_id — この巡の変更が genome 計算に一切触れていないことの実証）。"""
    genome_a = m.build_founder(pinned_domain, "R9F-01")
    genome_b = m.build_founder(pinned_domain, "R9F-01")
    assert genome_a.genome_id == genome_b.genome_id
    assert genome_a.to_dict() == genome_b.to_dict()


def test_por_revision_full_contract_gate_state_still_blocked(
    contract: m.Run9RunContract,
) -> None:
    """rev 0.3 編入後も現行 RUN9_CONTRACT.yaml は正直に BLOCKED のまま
    （por_adjudication_sha256/design_revision_doc_sha256 が新たに PINNED
    化されても、他の多くの pre-run 欄が依然 PENDING のため READY へは
    到達しない）。"""
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第2巡対応 — Fix 5: R9-G5 supersession 登録
# ---------------------------------------------------------------------------


def test_fix5_r9_g5_supersession_row_present_in_contradiction_table() -> None:
    """DESIGN_RUN9_REVISION_0.3.md の矛盾解決表に v0.1 §19 R9-G5
    （BIRTH_IDENTITY_SEPARATION）の読み替え行が追加されていること。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "R9-G5" in doc
    assert "BIRTH_IDENTITY_SEPARATION" in doc
    assert "機械計測の出生分離ゲートとして存続" in doc
    assert "blind human audit への fallback routing は除去" in doc


def test_fix5_design_revision_doc_sha256_pin_matches_recomputed_file(
    contract_raw: Dict[str, Any],
) -> None:
    """Fix 5 の文書編集（R9-G5 行追加）後、design_revision_doc_sha256 pin
    が実ファイルの再計算 sha256 と一致していること（マージ前限定編集後の
    再 pin 漏れが無いことの直接確認 — 値そのものは
    test_revision02_doc_sha256_pin_matches_actual_file が汎用的に検証
    しているが、本テストは Fix 5 の変更に紐づけて明示する）。"""
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(REVISION_DOC_PATH)
    assert field["value"] == m.compute_file_sha256(REVISION_DOC_PATH)


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第2巡対応 — Fix 6: practice_audio_split_manifest_sha 新設
# ---------------------------------------------------------------------------


def test_fix6_practice_split_sha_is_a_pin_field() -> None:
    assert "practice_audio_split_manifest_sha" in m.CONTRACT_PIN_FIELDS
    # post-run/optional のどちらにも属さない（pre-run 必須欄）ことを確認。
    assert "practice_audio_split_manifest_sha" not in m.CONTRACT_POST_RUN_PIN_FIELDS
    assert "practice_audio_split_manifest_sha" not in m.CONTRACT_OPTIONAL_PIN_FIELDS


def test_fix6_current_contract_practice_split_sha_is_pending(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["practice_audio_split_manifest_sha"]
    assert field["status"] == "PENDING"
    assert field["value"] is None


def test_fix6_current_contract_still_blocked(contract: m.Run9RunContract) -> None:
    """practice_audio_split_manifest_sha 新設後も現行 RUN9_CONTRACT.yaml は正直に
    BLOCKED のまま。"""
    assert m.gate_state(contract) == "BLOCKED"


def test_fix6_practice_split_sha_gets_64hex_format_enforced(
    contract_raw: Dict[str, Any],
) -> None:
    """欄名が `_sha` で終わるため `_validate_pin_field_value_shape` の
    汎用64hexブランチが自動適用される（特別扱いの分岐を追加していない
    ことの確認 — design_revision_doc_sha256/backbone_runtime_bundle_sha/
    por_adjudication_sha256 と同型のテスト）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["practice_audio_split_manifest_sha"] = {
        "value": "not-a-valid-hex-value", "status": "PINNED", "source": "x",
    }
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.load_run9_contract(tampered)


def test_fix6_practice_split_sha_missing_pin_blocks_ready_even_if_others_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """負例: practice_audio_split_manifest_sha 以外の pre-run 欄を全て PINNED にしても、
    practice_audio_split_manifest_sha 自身が PENDING のままなら gate_state() は BLOCKED
    のまま（education_technique_lesson_manifest_sha の PRACTICE 版対概念として、他欄の充足だけでは
    READY へ迂回できないことの確認）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    assert fully_pinned["practice_audio_split_manifest_sha"]["status"] == "PINNED"
    contract_ready = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract_ready) == "READY"

    regressed = copy.deepcopy(fully_pinned)
    regressed["practice_audio_split_manifest_sha"] = {
        "value": None, "status": "PENDING", "reason": "regressed",
    }
    contract_blocked = m.load_run9_contract(regressed)
    assert m.gate_state(contract_blocked) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第3巡対応 — Fix 7: rev 0.3 文書の PRACTICE
# 禁止列挙へ Technique label を同期 / Fix 8: 陳腐化した繰延記述の掃討
# ---------------------------------------------------------------------------


def test_fix7_rev03_doc_practice_forbidden_prose_mentions_technique_label() -> None:
    """DESIGN_RUN9_REVISION_0.3.md 改訂C の PRACTICE 禁止入力の散文列挙に
    「教師付与の Technique label」が存在すること — 第2巡 Fix 4 で
    `PRACTICE_FORBIDDEN_INPUTS` へ追加した `teacher_technique_label` と
    文書側が同期していなかった漏れ（第3巡 Fix 7）の是正確認。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "教師付与の Technique label" in doc
    assert "teacher_technique_label" in doc


def test_fix7_rev03_doc_practice_forbidden_prose_item_count_matches_tuple() -> None:
    """改訂C の PRACTICE 禁止列挙（散文の箇条書き）のトップレベル項目数が、
    `PRACTICE_FORBIDDEN_INPUTS`（5要素）と一致すること。禁止列挙の節
    （'**禁止（データ入力として渡してはいけないもの）**（PoR §3.2）:' から
    次の '**3分割の意味論**' 見出しまで — P2-1 の3分割導入で PRACTICE
    禁止列挙の直後が '**許可**' から '**3分割の意味論**' へ変わった）に
    含まれる、行頭が `- `（インデントなし = 継続行ではなくトップレベル
    箇条書き）の行数を数える。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    marker = "**禁止（データ入力として渡してはいけないもの）**（PoR §3.2）:"
    next_heading = "**3分割の意味論**"
    start = doc.index(marker) + len(marker)
    section = doc[start:doc.index(next_heading, start)]
    bullet_lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == len(m.PRACTICE_FORBIDDEN_INPUTS) == 5, (
        f"rev 0.3 文書の PRACTICE 禁止列挙が{len(bullet_lines)}項目だが "
        f"PRACTICE_FORBIDDEN_INPUTS は{len(m.PRACTICE_FORBIDDEN_INPUTS)}要素 — "
        "文書とタプルの同期が崩れている可能性がある"
    )


def test_fix7_design_revision_doc_sha256_pin_matches_recomputed_file_again(
    contract_raw: Dict[str, Any],
) -> None:
    """Fix 7 の文書編集（Technique label 同期）後、design_revision_doc_sha256
    pin が実ファイルの再計算 sha256 と一致していること。"""
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(REVISION_DOC_PATH)
    assert field["value"] == m.compute_file_sha256(REVISION_DOC_PATH)


def test_fix8_no_stale_deferred_practice_pin_field_language_remains() -> None:
    """PR #317 Codex bot レビュー第3巡 Fix 8: 「PRACTICE 側 pin 欄は後日
    命名/新設予定」型の陳腐化した記述が RUN9_CONTRACT.yaml / README.md に
    残っていないこと（`practice_audio_split_manifest_sha` が第2巡 Fix 6 で既に実在する
    ため、二重管理による競合欄導入・偽 BLOCKED の芽を除去する）。"""
    stale_phrases = ("新設予定", "新設する想定", "欄名は VG-L0 ハーネス実装時に確定")
    for path in (CONTRACT_PATH, _RUN_DIR / "README.md"):
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, (
                f"{path.name} に陳腐化した繰延記述 {phrase!r} が残っている"
            )


def test_fix8_readme_blocker_4_references_existing_practice_split_sha_field() -> None:
    """README.md のブロッカー(4)が、実在する `practice_audio_split_manifest_sha` 欄を
    現在形で参照していること（「別欄として新設予定」という将来形のまま
    ではないこと）。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "practice_audio_split_manifest_sha" in readme
    assert "新設済み" in readme or "既に" in readme


def test_fix8_contract_lesson_sha_comment_references_practice_split_sha() -> None:
    """RUN9_CONTRACT.yaml の education_technique_lesson_manifest_sha 直前コメントが、`practice_audio_split_manifest_sha`
    を実在欄として参照していること（分割「する想定」のまま陳腐化して
    いないこと）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "practice_audio_split_manifest_sha" in contract_text
    lesson_comment_start = contract_text.index("# --- lesson / learning")
    lesson_field_start = contract_text.index("education_technique_lesson_manifest_sha:")
    comment_block = contract_text[lesson_comment_start:lesson_field_start]
    assert "practice_audio_split_manifest_sha" in comment_block


# ---------------------------------------------------------------------------
# User 外部レビュー対応（2026-08-24, PR #317 head 71eeccad, CHANGES_REQUESTED
# → 全項目採用）— P1-1〜P1-4 / P2-1〜P2-5
# ---------------------------------------------------------------------------

BRANCH_WRITE_POLICY_PATH = _RUN_DIR / "inputs" / "branch_write_policy.json"


# --- P1-1: 書き込み境界（5必須テスト） --------------------------------------


def test_p1_1_control_writable_set_is_empty() -> None:
    assert m.BRANCH_WRITABLE_PARTITIONS["CONTROL"] == ()


def test_p1_1_practice_writable_trait_and_technique_only() -> None:
    assert m.BRANCH_WRITABLE_PARTITIONS["PRACTICE_FROM_AUDIO"] == (
        "TRAIT_CONTROL", "TECHNIQUE_CONTROL",
    )


def test_p1_1_education_writable_technique_only() -> None:
    assert m.BRANCH_WRITABLE_PARTITIONS["TRANSFER_TECHNIQUE"] == ("TECHNIQUE_CONTROL",)


def test_p1_1_no_branch_can_write_identity_or_immutable_artifacts() -> None:
    """いずれの枝も Identity/Genome/Backbone 等へ書込不可。
    IDENTITY_STATE は `IMMUTABLE_STATE_PARTITIONS` によりどの枝の writable
    集合にも現れない。Genome/Backbone 等は state partition ではなく
    `BRANCH_IMMUTABLE_ARTIFACTS`（ControlProfile の外側にある永続
    artifact）として別枠で凍結されている。"""
    for branch, writable in m.BRANCH_WRITABLE_PARTITIONS.items():
        for partition in m.IMMUTABLE_STATE_PARTITIONS:
            assert partition not in writable, f"{branch} may not write {partition}"
        with pytest.raises(m.Run9ValidationError):
            m.validate_branch_write(branch, "IDENTITY_STATE")
    # BRANCH_IMMUTABLE_ARTIFACTS はどの branch_writable_partitions 値にも
    # 含まれない（partition と artifact が別の名前空間であることの直接
    # 確認 — 万一同名の partition が誤って追加された場合に検出する）。
    all_writable_values = {p for writable in m.BRANCH_WRITABLE_PARTITIONS.values() for p in writable}
    assert not (set(m.BRANCH_IMMUTABLE_ARTIFACTS) & all_writable_values)


def test_p1_1_education_writing_trait_control_rejected() -> None:
    """修正指示5: EDUCATION 枝が TRAIT_CONTROL または IDENTITY_STATE へ
    書き込もうとした場合、fail-closed で拒否する契約。"""
    with pytest.raises(m.Run9ValidationError):
        m.validate_branch_write("TRANSFER_TECHNIQUE", "TRAIT_CONTROL")
    with pytest.raises(m.Run9ValidationError):
        m.validate_branch_write("TRANSFER_TECHNIQUE", "IDENTITY_STATE")
    # 対照: TECHNIQUE_CONTROL への書込は許可される。
    m.validate_branch_write("TRANSFER_TECHNIQUE", "TECHNIQUE_CONTROL")


def test_p1_1_control_writing_anything_rejected() -> None:
    for partition in m.STATE_PARTITIONS:
        with pytest.raises(m.Run9ValidationError):
            m.validate_branch_write("CONTROL", partition)


def test_p1_1_practice_writing_identity_rejected() -> None:
    with pytest.raises(m.Run9ValidationError):
        m.validate_branch_write("PRACTICE_FROM_AUDIO", "IDENTITY_STATE")
    # 対照: TRAIT_CONTROL/TECHNIQUE_CONTROL への書込は許可される。
    m.validate_branch_write("PRACTICE_FROM_AUDIO", "TRAIT_CONTROL")
    m.validate_branch_write("PRACTICE_FROM_AUDIO", "TECHNIQUE_CONTROL")


def test_p1_1_branch_write_policy_manifest_matches_constants() -> None:
    text = BRANCH_WRITE_POLICY_PATH.read_text(encoding="utf-8")
    data = m.load_branch_write_policy_json(text)
    m.validate_branch_write_policy_manifest(data)  # 例外を投げないことの確認


@pytest.mark.parametrize(
    ("tamper_path", "tamper_value"),
    [
        (("state_partitions",), ["IDENTITY_STATE", "TRAIT_CONTROL"]),
        (("immutable_state_partitions",), []),
        (("branch_writable_partitions", "CONTROL"), ["TECHNIQUE_CONTROL"]),
        (("branch_writable_partitions", "TRANSFER_TECHNIQUE"), ["TRAIT_CONTROL", "TECHNIQUE_CONTROL"]),
        (("immutable_artifacts",), ["shared_backbone"]),
    ],
)
def test_p1_1_policy_tampering_rejected(tamper_path: tuple, tamper_value: Any) -> None:
    """必須テスト「policy 改変で contract load または pre-run Gate が失敗
    する」: manifest の各セクションを個別に改変すると
    `validate_branch_write_policy_manifest()` が拒否する。"""
    text = BRANCH_WRITE_POLICY_PATH.read_text(encoding="utf-8")
    data = m.load_branch_write_policy_json(text)
    tampered = copy.deepcopy(data)
    node = tampered
    for key in tamper_path[:-1]:
        node = node[key]
    node[tamper_path[-1]] = tamper_value
    with pytest.raises(m.Run9ValidationError):
        m.validate_branch_write_policy_manifest(tampered)


def test_p1_1_branch_write_policy_sha_pinned_and_matches_actual_file(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["branch_write_policy_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(BRANCH_WRITE_POLICY_PATH)
    assert field["value"] == m.compute_file_sha256(BRANCH_WRITE_POLICY_PATH)


def test_p1_1_trait_change_definition_documented() -> None:
    """修正指示6: PRACTICE で許す Trait 変化は speaker embedding や Genome
    変更ではなく「明示的に許可された発声制御領域の後天的変化」であること
    の文書化。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "明示的に許可された発声制御領域の後天的変化" in doc


# --- P1-2: split pin の明示化（5必須テスト） --------------------------------


def test_p1_2_old_field_names_no_longer_exist_in_contract_pin_fields() -> None:
    assert "lesson_sha" not in m.CONTRACT_PIN_FIELDS
    assert "practice_split_sha" not in m.CONTRACT_PIN_FIELDS
    assert "education_technique_lesson_manifest_sha" in m.CONTRACT_PIN_FIELDS
    assert "practice_audio_split_manifest_sha" in m.CONTRACT_PIN_FIELDS


def test_p1_2_practice_manifest_field_missing_from_contract_rejected(
    contract_raw: Dict[str, Any],
) -> None:
    """必須テスト「practice split 欄欠落で contract 拒否」。"""
    tampered = copy.deepcopy(contract_raw)
    del tampered["practice_audio_split_manifest_sha"]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_p1_2_practice_manifest_field_pending_blocks_gate(contract_raw: Dict[str, Any]) -> None:
    """必須テスト「practice split が PENDING なら Gate は BLOCKED」。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    assert fully_pinned["practice_audio_split_manifest_sha"]["status"] == "PINNED"
    contract_ready = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract_ready) == "READY"

    regressed = copy.deepcopy(fully_pinned)
    regressed["practice_audio_split_manifest_sha"] = {
        "value": None, "status": "PENDING", "reason": "regressed",
    }
    contract_blocked = m.load_run9_contract(regressed)
    assert m.gate_state(contract_blocked) == "BLOCKED"


def _valid_practice_manifest() -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST,
        "pjs_source_archive_sha256": "a" * 64,
        "expanded_corpus_identity_sha256": "b" * 64,
        "training_split_sha256": "c" * 64,
        "validation_split_sha256": "d" * 64,
        "sealed_holdout_sha256": "e" * 64,
        "row_order_sha256": "f" * 64,
        "sample_inventory": ["s1", "s2"],
        "rights_source_class": "internal",
        "is_raw_audio": True,
        "excludes_correct_technique_parameters": True,
        "identical_bytes_and_order_across_founders": True,
        "row_ids": {
            "training": ["r1", "r2"],
            "validation": ["r3"],
            "sealed_holdout": ["r4"],
        },
    }


def _valid_education_manifest() -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_EDUCATION_TECHNIQUE_LESSON_MANIFEST,
        "training_technique_lesson_sha256": "a" * 64,
        "validation_technique_lesson_sha256": "b" * 64,
        "sealed_holdout_technique_release_policy": m.EDUCATION_SEALED_HOLDOUT_RELEASE_POLICIES[0],
        "excludes_identity_and_trait_donor_info": True,
        "identical_lesson_bytes_across_founders": True,
    }


def test_p1_2_practice_manifest_missing_required_key_rejected() -> None:
    for key in m.PRACTICE_MANIFEST_REQUIRED_KEYS:
        manifest = _valid_practice_manifest()
        del manifest[key]
        with pytest.raises(m.Run9ValidationError, match="missing required key"):
            m.validate_practice_split_manifest(manifest)


def test_p1_2_education_manifest_missing_required_key_rejected() -> None:
    for key in m.EDUCATION_MANIFEST_REQUIRED_KEYS:
        manifest = _valid_education_manifest()
        del manifest[key]
        with pytest.raises(m.Run9ValidationError, match="missing required key"):
            m.validate_education_lesson_manifest(manifest)


def test_p1_2_practice_manifest_holdout_overlaps_training_rejected() -> None:
    """必須テスト「holdout が training 集合へ混入した manifest を拒否」。"""
    manifest = _valid_practice_manifest()
    manifest["row_ids"]["sealed_holdout"] = ["r1"]  # r1 は training にも存在
    with pytest.raises(m.Run9ValidationError, match="overlaps training"):
        m.validate_practice_split_manifest(manifest)


def test_p1_2_practice_manifest_per_founder_structure_rejected() -> None:
    """必須テスト「Founder ごとに異なる practice split を与える構造を
    拒否」。"""
    manifest = _valid_practice_manifest()
    manifest["row_ids"]["R9F-01"] = {"training": ["x1"]}
    with pytest.raises(m.Run9ValidationError, match="must not branch by founder_id"):
        m.validate_practice_split_manifest(manifest)


def test_p1_2_education_manifest_per_founder_structure_rejected() -> None:
    manifest = _valid_education_manifest()
    manifest["R9F-02"] = {"training_technique_lesson_sha256": "c" * 64}
    with pytest.raises(m.Run9ValidationError, match="must not branch by founder_id"):
        m.validate_education_lesson_manifest(manifest)


def test_p1_2_manifest_kind_swap_rejected() -> None:
    """必須テスト「practice/education の manifest hash を入れ替えた場合に
    拒否」— schema 自己宣言による種別取り違え検出（双方向）。"""
    with pytest.raises(m.Run9ValidationError, match="practice split manifest schema"):
        m.validate_practice_split_manifest(_valid_education_manifest())
    with pytest.raises(m.Run9ValidationError, match="education lesson manifest schema"):
        m.validate_education_lesson_manifest(_valid_practice_manifest())


def test_p1_2_valid_manifests_pass_validators() -> None:
    """対照実験: 正しく構成された manifest は validator を通過する。"""
    m.validate_practice_split_manifest(_valid_practice_manifest())
    m.validate_education_lesson_manifest(_valid_education_manifest())


def test_p1_2_manifest_required_true_keys_reject_false() -> None:
    for key in ("is_raw_audio", "excludes_correct_technique_parameters",
                "identical_bytes_and_order_across_founders"):
        manifest = _valid_practice_manifest()
        manifest[key] = False
        with pytest.raises(m.Run9ValidationError, match="must be exactly True"):
            m.validate_practice_split_manifest(manifest)
    for key in ("excludes_identity_and_trait_donor_info", "identical_lesson_bytes_across_founders"):
        manifest = _valid_education_manifest()
        manifest[key] = False
        with pytest.raises(m.Run9ValidationError, match="must be exactly True"):
            m.validate_education_lesson_manifest(manifest)


# --- P1-3: C0/C1 分離（5必須テスト） ----------------------------------------


def test_p1_3_c0_condition_maps_to_replay_revision_no_controlprofile_change() -> None:
    """C0 には ControlProfile 変更が存在しない（C0 の revision 名
    "replay" 自体が「学習 step を実行しない re-render」であることを表し、
    r0 からの ControlProfile 差分を持たない — CONTROL 全体の writable
    集合が空であることと対応する）。"""
    assert m.BRANCH_REVISIONS["CONTROL"]["NO_LEARNING_REPLAY"] == "replay"
    assert m.BRANCH_WRITABLE_PARTITIONS["CONTROL"] == ()


def test_p1_3_c1_condition_is_neutral_profile_no_learning_step() -> None:
    """C1 は中立 profile のみで学習 step なし（revision 名 "r_sham" が
    Sham Transition であることを表す。CONTROL 全体として学習 step を
    実行しない = writable 集合が空である点は C0/C1 共通）。"""
    assert m.BRANCH_REVISIONS["CONTROL"]["ZERO_CONTROLPROFILE_SHAM"] == "r_sham"
    assert m.BRANCH_WRITABLE_PARTITIONS["CONTROL"] == ()


def test_p1_3_gain_baseline_noise_from_c0_documented() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "Practice/Education gain の\n基準ノイズは C0 由来" in doc or "基準ノイズは C0 由来" in doc


def test_p1_3_profile_side_effect_recorded_as_c1_minus_c0_documented() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "C1−C0" in doc or "C1-C0" in doc


def test_p1_3_control_conditions_satisfied_requires_both() -> None:
    """必須テスト「C0/C1 の片方が欠けた attempt は評価 READY にならない」。"""
    assert m.control_conditions_satisfied(set(m.CONTROL_CONDITIONS)) is True
    assert m.control_conditions_satisfied({"NO_LEARNING_REPLAY"}) is False
    assert m.control_conditions_satisfied({"ZERO_CONTROLPROFILE_SHAM"}) is False
    assert m.control_conditions_satisfied(set()) is False


def test_p1_3_control_conditions_satisfied_rejects_non_container() -> None:
    with pytest.raises(m.Run9ValidationError):
        m.control_conditions_satisfied("NO_LEARNING_REPLAY")  # 文字列は不可（文字ごとに反復される事故の防止）


def test_p1_3_control_conditions_frozen_values() -> None:
    assert m.CONTROL_CONDITIONS == ("NO_LEARNING_REPLAY", "ZERO_CONTROLPROFILE_SHAM")


# --- P1-4: 結果と昇格の分離（5必須テスト） ----------------------------------


def test_p1_4_no_gain_is_archive_eligible() -> None:
    """必須テスト「NO_GAIN でも attempt evidence が保存対象」。
    archive_status の唯一の値が全 terminal outcome に無条件適用される
    ことを、語彙上「PRACTICE 結果による分岐が存在しない」ことで確認する
    （archive_status は scientific_outcomes から独立した単一値語彙）。"""
    assert "NO_GAIN" in m.PRACTICE_OUTCOMES
    assert m.ARCHIVE_STATUSES == ("IMMUTABLE_ARCHIVED",)
    # NO_GAIN という結果を宣言しても archive_status の選択肢は変わらない
    # （分岐が存在しないこと自体が「無条件保存」の語彙的裏付け）。


def test_p1_4_design_failure_is_archive_eligible_evidence_not_deletable() -> None:
    """必須テスト「DESIGN_FAILURE でも証拠削除不可」。archive_status は
    単一値のみで「削除」に相当する値が存在しない。"""
    assert "DESIGN_FAILURE" in m.FAILURE_CLASSES
    assert len(m.ARCHIVE_STATUSES) == 1
    forbidden_tokens = ("DELETE", "DELETED", "PURGE", "DISCARD")
    for value in m.ARCHIVE_STATUSES:
        assert not any(tok in value for tok in forbidden_tokens)


def test_p1_4_no_function_derives_single_pass_from_six_outcomes() -> None:
    """必須テスト「6分類から単一 TotalScore/PASS を自動生成しない」。
    モジュール内にそのような変換関数が存在しないことを、禁止する関数名の
    非存在で確認する（新設のたびにこの禁止リストへ追加していく想定）。"""
    forbidden_function_names = (
        "overall_verdict", "combined_outcome", "aggregate_outcome",
        "compute_total_score", "compute_pass", "derive_pass",
        "scientific_outcomes_to_pass", "outcomes_to_verdict",
    )
    for name in forbidden_function_names:
        assert not hasattr(m, name), f"{name} must not exist (single-PASS aggregation is forbidden)"


def test_p1_4_promotion_statuses_never_a_promoted_value() -> None:
    """必須テスト「RUN9 結果だけでは promotion_status が昇格値にならない」。
    PROMOTION_STATUSES は単一値のみであり、その値自体が非昇格を明示する
    （"ARCHIVE_ONLY" プレフィックスと "PENDING_USER_RULING" サフィックス）。
    昇格を意味する語彙（CANONICAL/PARENT_POOL 等）は含まれない。"""
    assert m.PROMOTION_STATUSES == ("ARCHIVE_ONLY_PENDING_USER_RULING",)
    forbidden_tokens = ("CANONICAL", "PARENT_POOL", "APPROVED", "PROMOTED")
    for value in m.PROMOTION_STATUSES:
        assert not any(tok in value for tok in forbidden_tokens)


def test_p1_4_parent_pool_registration_requires_separate_user_ruling_documented() -> None:
    """必須テスト「Parent Pool 登録には別 User ruling pin が必要」の文書化
    確認（機械強制は本 PR の範囲外 — PROMOTION_STATUSES の拡張自体が新しい
    design_revision を要する設計になっていることの語彙的裏付けは上記
    test_p1_4_promotion_statuses_never_a_promoted_value が担う）。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "別の User ruling pin" in doc or "別の\nUser 裁定" in doc or "新しい design_revision（= 別の" in doc


def test_p1_4_run_statuses_do_not_encode_scientific_verdict() -> None:
    """run_status の値は実行完了状態のみを表し、PASS/FAIL 語彙を含まない。"""
    forbidden_tokens = ("PASS", "FAIL_", "SUCCESS")
    for value in m.RUN_STATUSES:
        for tok in forbidden_tokens:
            assert tok not in value, f"{value!r} unexpectedly contains scientific-verdict token {tok!r}"
    assert m.RUN_STATUSES == ("COMPLETE", "BLOCKED", "IMPLEMENTATION_FAILED", "DESIGN_FAILED")


def test_p1_4_four_vocabularies_are_pairwise_disjoint() -> None:
    """scientific_outcomes（6分類統合集合）・run_status・archive_status・
    promotion_status の4語彙が互いに素であること（値の再利用による
    意味論の混同を防ぐ）。"""
    scientific_outcomes_union = (
        set(m.BIRTH_OUTCOMES) | set(m.PRACTICE_OUTCOMES) | set(m.EDUCATION_OUTCOMES)
        | set(m.SEPARATION_OUTCOMES) | set(m.FOUNDER_RESPONSE_OUTCOMES) | set(m.IDENTITY_OUTCOMES)
    )
    groups = [
        ("scientific_outcomes", scientific_outcomes_union),
        ("run_status", set(m.RUN_STATUSES)),
        ("archive_status", set(m.ARCHIVE_STATUSES)),
        ("promotion_status", set(m.PROMOTION_STATUSES)),
    ]
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            name_a, set_a = groups[i]
            name_b, set_b = groups[j]
            overlap = set_a & set_b
            assert not overlap, f"{name_a} and {name_b} share value(s): {overlap}"


# --- P2-4: held-out gain 必須欄 ----------------------------------------------


def test_p2_4_required_gain_fields_frozen() -> None:
    assert m.REQUIRED_GAIN_FIELDS == (
        "practice_train_gain", "practice_heldout_gain",
        "education_train_gain", "education_heldout_gain",
    )


def test_p2_4_optional_generalization_fields_frozen() -> None:
    assert m.OPTIONAL_GENERALIZATION_FIELDS == (
        "broad_generalization_gain", "cross_song_generalization", "cross_register_generalization",
    )


def test_p2_4_required_and_optional_gain_fields_disjoint() -> None:
    assert not (set(m.REQUIRED_GAIN_FIELDS) & set(m.OPTIONAL_GENERALIZATION_FIELDS))


def test_p2_4_heldout_gain_documented_as_mandatory_not_best_effort() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "実装可能なら」ではなく" in doc


# --- P2-2: human_audit_mode ---------------------------------------------------


def test_p2_2_human_audit_modes_frozen() -> None:
    assert m.HUMAN_AUDIT_MODES == ("DISABLED", "ADVISORY_PREDECLARED")
    assert m.DEFAULT_HUMAN_AUDIT_MODE == "DISABLED"


def test_p2_2_current_contract_human_audit_mode_is_disabled(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["human_audit_mode"] == "DISABLED"


def test_p2_2_invalid_human_audit_mode_rejected(contract_raw: Dict[str, Any]) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["human_audit_mode"] = "SOMETHING_ELSE"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_p2_2_missing_human_audit_mode_rejected(contract_raw: Dict[str, Any]) -> None:
    tampered = copy.deepcopy(contract_raw)
    del tampered["human_audit_mode"]
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_p2_2_disabled_mode_leaves_protocol_sha_optional(contract_raw: Dict[str, Any]) -> None:
    """DISABLED（既定）のときは human_evaluation_protocol_sha が PENDING
    のままでも READY を妨げない（従来どおりの optional 挙動）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    fully_pinned["human_audit_mode"] = "DISABLED"
    fully_pinned["human_evaluation_protocol_sha"] = {
        "value": None, "status": "PENDING", "reason": "not planned",
    }
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "READY"


def test_p2_2_advisory_predeclared_requires_protocol_sha_pinned_for_ready(
    contract_raw: Dict[str, Any],
) -> None:
    """ADVISORY_PREDECLARED のとき human_evaluation_protocol_sha が
    PINNED でなければ gate READY 不可。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    fully_pinned["human_audit_mode"] = "ADVISORY_PREDECLARED"
    fully_pinned["human_evaluation_protocol_sha"] = {
        "value": None, "status": "PENDING", "reason": "advisory audit planned but not ready",
    }
    contract_blocked = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract_blocked) == "BLOCKED"

    ready_variant = copy.deepcopy(fully_pinned)
    ready_variant["human_evaluation_protocol_sha"] = {
        "value": "a" * 64, "status": "PINNED", "source": "synthetic-fixture",
    }
    contract_ready = m.load_run9_contract(ready_variant)
    assert m.gate_state(contract_ready) == "READY"


def test_p2_2_holdout_and_null_shift_rescue_discipline_documented() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "holdout 開封後の" in doc
    assert "human_audit_mode` 変更は禁止" in doc
    assert "人間監査を" in doc
    assert "の救済に使わない" in doc
    assert "SCIENTIFIC_NULL" in doc and "Identity SHIFTED" in doc


# --- P2-3: 機械的校正の定義 ----------------------------------------------------


def test_p2_3_calibration_definition_section_present() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "## 改訂 G — 機械的校正の定義" in doc
    assert "人間知覚との一致証明ではない" in doc


def test_p2_3_calibration_result_rules_present() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "UNCALIBRATED" in doc
    assert "holdout 開封前に freeze" in doc


# --- P2-5: Non-Claim / Rights Boundary ---------------------------------------


def test_p2_5_non_claim_rights_boundary_section_present() -> None:
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "## 改訂 H — Non-Claim / Rights Boundary" in doc


def test_p2_5_non_claim_five_items_present() -> None:
    """本文は Markdown の折り返しで改行+インデント空白を含むため、
    照合前に空白（改行含む）を単一スペースへ正規化してから部分文字列
    一致を見る。"""
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())
    required_phrases = [
        "法的・契約上の 許諾が自動成立するわけではない",
        "元音声の利用権確認",
        "入力音声の許諾範囲に拘束される",
        "AQUEST 由来素材は明示許諾が得られるまで",
        "許諾の代替ではない",
    ]
    for phrase in required_phrases:
        normalized_phrase = " ".join(phrase.split())
        assert normalized_phrase in normalized, f"Non-Claim item missing from doc: {phrase!r}"


# --- 不変制約の回帰確認 -------------------------------------------------------


def test_invariant_por_and_v01_and_rev02_byte_pins_unchanged(contract_raw: Dict[str, Any]) -> None:
    """不変制約: POR txt / v0.1 / rev 0.2 / 既存 AF0・Ritsu・rights・
    backbone pin 値は無変更。"""
    assert contract_raw["por_adjudication_sha256"]["value"] == (
        "56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007"
    )
    assert contract_raw["design_doc_sha256"]["value"] == (
        "b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e"
    )
    assert _sha256_file(REVISION_0_2_DOC_PATH) == (
        "406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb"
    )
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    assert domain_raw["anchor_hashes"]["af0"] == (
        "183bf32561589ddad69daa0faf5838c3e9601d17b24b62ee32aa629123a87f1e"
    )
    assert domain_raw["anchor_hashes"]["ritsu"] == (
        "88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76"
    )
    assert contract_raw["backbone_checkpoint_sha"]["value"] == (
        "6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a"
    )


def test_invariant_tri_crossover_and_genome_id_unchanged(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """不変制約: TRI_CROSSOVER/1.0 および genome_id 計算は無変更 —
    既知の pinned_domain 入力から従来と同じ genome_id が出ることを確認
    する（回帰検出のための固定値照合ではなく、決定論の実証）。"""
    assert m.OPERATOR_ID == "TRI_CROSSOVER/1.0"
    genome_a = m.build_founder(pinned_domain, "R9F-01")
    genome_b = m.build_founder(pinned_domain, "R9F-02")
    assert genome_a.genome_id != genome_b.genome_id
    # 決定論: 同じ入力から再度呼び出しても同じ genome_id。
    assert m.build_founder(pinned_domain, "R9F-01").genome_id == genome_a.genome_id


def test_invariant_design_revision_doc_sha256_updated_and_matches_file(
    contract_raw: Dict[str, Any],
) -> None:
    """rev 0.3 編集後は design_revision_doc_sha256 を再計算して contract
    反映済みであること（本巡の全編集を含む最終状態との一致）。"""
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(REVISION_DOC_PATH)


def test_invariant_existing_codex_fixes_not_regressed(contract_raw: Dict[str, Any]) -> None:
    """既存 Codex bot レビュー3件（PR #317 第1〜3巡）の修正が退行していない
    ことの直接確認。"""
    # 第1巡 Fix 1: human_evaluation_protocol_sha は optional 分類のまま。
    assert "human_evaluation_protocol_sha" in m.CONTRACT_OPTIONAL_PIN_FIELDS
    # 第1巡 Fix 2: 旧 revision 拒否メッセージは DESIGN_REVISION から動的生成。
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError, match=m.DESIGN_REVISION):
        m.load_run9_contract(tampered)
    # 第1巡 Fix 3: autonomous_imitation_target_selection は
    # PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS 側に保持されている
    # （P2-1 の3分割後も転記漏れが再発していないこと）。
    assert "imitation_target_selection" in m.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS
    # 第2巡 Fix 4: teacher_technique_label は引き続き禁止語彙。
    assert "teacher_technique_label" in m.PRACTICE_FORBIDDEN_INPUTS
    # 第2巡 Fix 6 → P1-2 改名: practice_audio_split_manifest_sha が
    # pre-run 必須欄として存在。
    assert "practice_audio_split_manifest_sha" in m.CONTRACT_PIN_FIELDS
    # 第3巡 Fix 7/8: rev 0.3 文書の PRACTICE 禁止列挙に Technique label が
    # 存在し、陳腐化した繰延記述が残っていない。
    doc = REVISION_DOC_PATH.read_text(encoding="utf-8")
    assert "教師付与の Technique label" in doc
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    for stale_phrase in ("新設予定", "新設する想定"):
        assert stale_phrase not in contract_text


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第4巡対応 — Fix A（P1: manifest validator の値
# 整形式強制） / Fix B（P2: split 内重複 row ID の拒否）
# ---------------------------------------------------------------------------


def test_fix4a_positive_control_manifests_still_pass() -> None:
    """正常系対照: 整形式を満たす manifest は引き続き通過する。"""
    m.validate_practice_split_manifest(_valid_practice_manifest())
    m.validate_education_lesson_manifest(_valid_education_manifest())


@pytest.mark.parametrize("key", sorted(m._PRACTICE_MANIFEST_SHA256_KEYS))
def test_fix4a_practice_manifest_null_hash_rejected(key: str) -> None:
    manifest = _valid_practice_manifest()
    manifest[key] = None
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_practice_split_manifest(manifest)


@pytest.mark.parametrize("key", sorted(m._PRACTICE_MANIFEST_SHA256_KEYS))
def test_fix4a_practice_manifest_non_64hex_rejected(key: str) -> None:
    manifest = _valid_practice_manifest()
    manifest[key] = "not-a-valid-hex-value"
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_practice_split_manifest(manifest)
    # 大文字hex（形式は近いが規約外）も拒否される。
    manifest2 = _valid_practice_manifest()
    manifest2[key] = "A" * 64
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_practice_split_manifest(manifest2)
    # 63桁（1桁不足）も拒否される。
    manifest3 = _valid_practice_manifest()
    manifest3[key] = "a" * 63
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_practice_split_manifest(manifest3)


@pytest.mark.parametrize("key", sorted(m._EDUCATION_MANIFEST_SHA256_KEYS))
def test_fix4a_education_manifest_null_and_non_64hex_hash_rejected(key: str) -> None:
    manifest_null = _valid_education_manifest()
    manifest_null[key] = None
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_education_lesson_manifest(manifest_null)

    manifest_bad = _valid_education_manifest()
    manifest_bad[key] = "z" * 64
    with pytest.raises(m.Run9ValidationError, match="64 lowercase hex"):
        m.validate_education_lesson_manifest(manifest_bad)


def test_fix4a_practice_manifest_empty_sample_inventory_rejected() -> None:
    manifest = _valid_practice_manifest()
    manifest["sample_inventory"] = []
    with pytest.raises(m.Run9ValidationError, match="sample_inventory must be a non-empty list"):
        m.validate_practice_split_manifest(manifest)


def test_fix4a_practice_manifest_sample_inventory_with_blank_entry_rejected() -> None:
    manifest = _valid_practice_manifest()
    manifest["sample_inventory"] = ["s1", "   "]
    with pytest.raises(m.Run9ValidationError, match="sample_inventory"):
        m.validate_practice_split_manifest(manifest)


def test_fix4a_practice_manifest_empty_rights_source_class_rejected() -> None:
    manifest = _valid_practice_manifest()
    manifest["rights_source_class"] = ""
    with pytest.raises(m.Run9ValidationError, match="rights_source_class must be a non-empty string"):
        m.validate_practice_split_manifest(manifest)
    manifest2 = _valid_practice_manifest()
    manifest2["rights_source_class"] = "   "
    with pytest.raises(m.Run9ValidationError, match="rights_source_class must be a non-empty string"):
        m.validate_practice_split_manifest(manifest2)


@pytest.mark.parametrize("split_name", ["training", "validation", "sealed_holdout"])
def test_fix4a_practice_manifest_single_empty_split_rejected(split_name: str) -> None:
    manifest = _valid_practice_manifest()
    manifest["row_ids"][split_name] = []
    with pytest.raises(m.Run9ValidationError, match=f"row_ids.{split_name} must be a non-empty list"):
        m.validate_practice_split_manifest(manifest)


def test_fix4a_practice_manifest_all_three_splits_empty_rejected() -> None:
    """必須負例テスト「3 split 全空」: 使用可能な素材ゼロの manifest が
    disjoint 検査（空集合同士は自明に素）だけでは検出できず素通り
    していた欠陥の是正確認。"""
    manifest = _valid_practice_manifest()
    manifest["row_ids"] = {"training": [], "validation": [], "sealed_holdout": []}
    with pytest.raises(m.Run9ValidationError, match="must be a non-empty list"):
        m.validate_practice_split_manifest(manifest)


def test_fix4a_education_manifest_invalid_release_policy_rejected() -> None:
    manifest = _valid_education_manifest()
    manifest["sealed_holdout_technique_release_policy"] = "RELEASE_IMMEDIATELY"
    with pytest.raises(
        m.Run9ValidationError, match="sealed_holdout_technique_release_policy must be one of"
    ):
        m.validate_education_lesson_manifest(manifest)


def test_fix4a_education_manifest_release_policy_wrong_case_rejected() -> None:
    """閉じた語彙は大文字小文字も含めて厳密一致（旧実装のテストフィクスチャ
    が使っていた小文字 `"release_after_training_complete"` は本巡で凍結
    した正典値 `RELEASE_AFTER_TRAINING_COMPLETE` とは別値として拒否される
    ことの確認 — 値の同一性判定に曖昧さを残さない）。"""
    manifest = _valid_education_manifest()
    manifest["sealed_holdout_technique_release_policy"] = "release_after_training_complete"
    with pytest.raises(
        m.Run9ValidationError, match="sealed_holdout_technique_release_policy must be one of"
    ):
        m.validate_education_lesson_manifest(manifest)


def test_fix4a_release_policy_vocabulary_frozen() -> None:
    assert m.EDUCATION_SEALED_HOLDOUT_RELEASE_POLICIES == ("RELEASE_AFTER_TRAINING_COMPLETE",)


# --- Fix B: split 内重複 row ID の拒否 --------------------------------------


@pytest.mark.parametrize("split_name", ["training", "validation", "sealed_holdout"])
def test_fix4b_duplicate_row_id_within_single_split_rejected(split_name: str) -> None:
    manifest = _valid_practice_manifest()
    manifest["row_ids"][split_name] = manifest["row_ids"][split_name] + manifest["row_ids"][split_name][:1]
    with pytest.raises(m.Run9ValidationError, match="contains duplicate value"):
        m.validate_practice_split_manifest(manifest)


def test_fix4b_duplicate_row_id_checked_before_overlap_when_both_present() -> None:
    """重複行 ID と split 間 overlap が同時に存在する manifest でも、
    重複検査（Fix B）が overlap 検査より先に走り、より根本的な欠陥
    （同一 split 内の重複）を先に報告する。"""
    manifest = _valid_practice_manifest()
    manifest["row_ids"]["training"] = ["r1", "r1"]
    manifest["row_ids"]["sealed_holdout"] = ["r1"]  # training と overlap もしている
    with pytest.raises(m.Run9ValidationError, match="contains duplicate value"):
        m.validate_practice_split_manifest(manifest)


def test_fix4b_three_duplicate_occurrences_reported_once() -> None:
    manifest = _valid_practice_manifest()
    manifest["row_ids"]["training"] = ["r1", "r1", "r1", "r2"]
    with pytest.raises(m.Run9ValidationError, match=r"\['r1'\]"):
        m.validate_practice_split_manifest(manifest)


def test_fix4b_no_duplicates_positive_control() -> None:
    """対照実験: 重複の無い manifest は Fix B の新設検査を素通りする。"""
    manifest = _valid_practice_manifest()
    m.validate_practice_split_manifest(manifest)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第5巡対応 — Fix A（P2: founder 別構造検出の値
# 走査） / Fix B（P2: sample_inventory の重複 ID 拒否）
# ---------------------------------------------------------------------------


def test_fix5a_founder_id_key_with_value_record_rejected() -> None:
    """負例: `{"founder_id": "R9F-01", ...}` のような**値フィールドでの
    founder 分岐**（第4巡実装ではキー走査のみだったため素通りしていた）。
    manifest 内の任意の深さに `founder_id` というキー自体が現れたら拒否
    する。"""
    manifest = _valid_practice_manifest()
    manifest["provenance_records"] = [
        {"founder_id": "R9F-01", "note": "collected for R9F-01"},
    ]
    with pytest.raises(m.Run9ValidationError, match="must not contain a 'founder_id' key"):
        m.validate_practice_split_manifest(manifest)


def test_fix5a_nested_founder_key_rejected() -> None:
    """負例: ネスト構造の奥深くに `R9F-01`/`R9F-02` が**キー**として現れた
    場合の拒否（row_ids 直下だけでなく任意の深さで検出されること）。"""
    manifest = _valid_practice_manifest()
    manifest["extra"] = {"nested": {"R9F-02": {"detail": "per-founder override"}}}
    with pytest.raises(m.Run9ValidationError, match="must not branch by founder_id"):
        m.validate_practice_split_manifest(manifest)


def test_fix5a_nested_founder_exact_match_value_rejected() -> None:
    """負例: ネスト構造の奥深くに `R9F-01`/`R9F-02` が**値**として（dict
    の value としても list の要素としても）現れた場合の拒否。"""
    manifest_dict_value = _valid_practice_manifest()
    manifest_dict_value["extra"] = {"nested": {"target_founder": "R9F-02"}}
    with pytest.raises(m.Run9ValidationError, match="must not contain a founder ID as a value"):
        m.validate_practice_split_manifest(manifest_dict_value)

    manifest_list_element = _valid_practice_manifest()
    manifest_list_element["extra"] = {"applies_to": ["R9F-01"]}
    with pytest.raises(m.Run9ValidationError, match="must not contain a founder ID as a list element"):
        m.validate_practice_split_manifest(manifest_list_element)


def test_fix5a_education_manifest_founder_value_record_rejected() -> None:
    """education 側でも同じ拡張検出が効くことの確認。"""
    manifest = _valid_education_manifest()
    manifest["provenance"] = {"founder_id": "R9F-02"}
    with pytest.raises(m.Run9ValidationError, match="must not contain a 'founder_id' key"):
        m.validate_education_lesson_manifest(manifest)


def test_fix5a_substring_containing_founder_id_not_falsely_rejected() -> None:
    """対照実験（誤爆防止）: sample id 等、founder ID を**部分文字列として
    含むが完全一致ではない**正当な文字列は拒否されない
    （完全一致のみを対象とする設計の確認）。"""
    manifest = _valid_practice_manifest()
    manifest["sample_inventory"] = ["clip-R9F-01-take3", "s2"]
    m.validate_practice_split_manifest(manifest)  # 例外を投げないことの確認

    manifest_key = _valid_practice_manifest()
    manifest_key["extra"] = {"R9F-01_summary": "aggregate note, not a per-founder branch"}
    m.validate_practice_split_manifest(manifest_key)  # 例外を投げないことの確認


def test_fix5a_founder_id_key_prefix_not_falsely_rejected() -> None:
    """対照実験（誤爆防止）: `founder_id` という完全一致キー名でなければ
    （例: `founder_identity_note`）拒否されない — 接頭辞一致ではなく
    キー名の完全一致のみを対象とする設計の確認。"""
    manifest = _valid_practice_manifest()
    manifest["founder_identity_note"] = "shared note, not a per-founder branch"
    m.validate_practice_split_manifest(manifest)  # 例外を投げないことの確認


# --- Fix B: sample_inventory の重複 ID 拒否 ----------------------------------


def test_fix5b_duplicate_sample_inventory_id_rejected() -> None:
    manifest = _valid_practice_manifest()
    manifest["sample_inventory"] = ["s1", "s2", "s1"]
    with pytest.raises(m.Run9ValidationError, match="sample_inventory contains duplicate value"):
        m.validate_practice_split_manifest(manifest)


def test_fix5b_no_duplicate_sample_inventory_positive_control() -> None:
    """対照実験: 重複の無い sample_inventory は通過する。"""
    manifest = _valid_practice_manifest()
    manifest["sample_inventory"] = ["s1", "s2", "s3"]
    m.validate_practice_split_manifest(manifest)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #317 Codex bot レビュー第6巡対応 — Fix A（P1・部分採用: manifest 照合
# のテスト層事前配線） / Fix B（P2・採用: 「両枝同一recipe」記述の是正）
# ---------------------------------------------------------------------------


def test_fix6a_practice_manifest_sha_matches_actual_file_and_validates_once_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """`practice_audio_split_manifest_sha` の実ファイル照合を、
    `backbone_runtime_bundle_sha` の事前配線パターン（PR #316 第9巡,
    e490985, 部分採用）と同型でテスト層へ配線する（Codex bot レビュー
    PR #317 第6巡 Fix A, 部分採用）。

    **層分離の境界宣言（変更しない）**: 実体照合はテスト層にのみ配線し、
    `load_run9_contract()`/`gate_state()`（loader/runtime 層）へは配線
    しない — PR #315 第4巡の境界宣言どおり、contract loader は事前登録
    契約の構造述語（型・整形式・状態の整合）を検査する層であり、pin 値
    と実体ファイルの突合は R9-G1（INPUT_FREEZE_AND_RIGHTS）検証ツーリング
    の職務として分離する。

    現状 status は PENDING のためこのテストは「PENDING であること」だけ
    を確認するが、将来本欄が PINNED へ昇格した瞬間、この同じテストが
    (a) `compute_file_sha256(PRACTICE_MANIFEST_PATH)` との一致、
    (b) `PRACTICE_MANIFEST_PATH` の内容が `validate_practice_split_
    manifest()` を通過すること、の両方を自動的に強制するようになる
    （テストコードの変更を要さない = 事前配線）。
    """
    field = contract_raw["practice_audio_split_manifest_sha"]
    if field["status"] == "PINNED":
        assert field["value"] == m.compute_file_sha256(m.PRACTICE_MANIFEST_PATH), (
            "practice_audio_split_manifest_sha が PINNED を宣言しているが、"
            f"{m.PRACTICE_MANIFEST_PATH} の実バイト sha256 と一致しない"
        )
        manifest_data = m._loads_strict_json(
            m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        m.validate_practice_split_manifest(manifest_data)  # 例外を投げないことの確認
    else:
        assert field["status"] == "PENDING"
        assert field["value"] is None


def test_fix6a_education_manifest_sha_matches_actual_file_and_validates_once_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """`education_technique_lesson_manifest_sha` 版の同型テスト
    （上記 `test_fix6a_practice_manifest_sha_matches_actual_file_and_
    validates_once_pinned` と対）。"""
    field = contract_raw["education_technique_lesson_manifest_sha"]
    if field["status"] == "PINNED":
        assert field["value"] == m.compute_file_sha256(m.EDUCATION_MANIFEST_PATH), (
            "education_technique_lesson_manifest_sha が PINNED を宣言しているが、"
            f"{m.EDUCATION_MANIFEST_PATH} の実バイト sha256 と一致しない"
        )
        manifest_data = m._loads_strict_json(
            m.EDUCATION_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        m.validate_education_lesson_manifest(manifest_data)  # 例外を投げないことの確認
    else:
        assert field["status"] == "PENDING"
        assert field["value"] is None


def test_fix6a_manifest_path_constants_point_to_conventional_inputs_location() -> None:
    assert m.PRACTICE_MANIFEST_PATH.name == "practice_audio_split_manifest.json"
    assert m.EDUCATION_MANIFEST_PATH.name == "education_technique_lesson_manifest.json"
    assert m.PRACTICE_MANIFEST_PATH.parent == _RUN_DIR / "inputs"
    assert m.EDUCATION_MANIFEST_PATH.parent == _RUN_DIR / "inputs"


def test_fix6a_prewired_check_actually_fails_for_mismatched_or_invalid_manifest_simulation(
    tmp_path: Path,
) -> None:
    """事前配線が偽陽性（常に pass する壊れた検査）でないことのシミュレー
    ション確認: 実ファイルがまだ存在しない現状では上記2テストは
    「PENDING であること」の分岐しか通らないため、事前配線ロジックが
    「PINNED + 不正 manifest」で実際に失敗することを、一時ファイルを
    使った直接シミュレーションで別途実証する（本番の pin フィールドは
    一切変更しない）。

    (a) sha256 不一致: 宣言された pin 値が実ファイルの実測 sha256 と
        異なれば assert が失敗する。
    (b) manifest 検証失敗: schema 不一致（取り違え・破損の代表例）の
        manifest は `validate_practice_split_manifest()` が
        `Run9ValidationError` で拒否する — PINNED を騙っても検証は
        迂回できない。
    """
    manifest_path = tmp_path / "practice_audio_split_manifest.json"
    manifest_path.write_text(json.dumps(_valid_practice_manifest()), encoding="utf-8")
    actual_hash = m.compute_file_sha256(manifest_path)

    # (a) 宣言 pin 値が実ファイルと不一致なら AssertionError（= 事前配線の
    # 主張どおり、照合が実際に機能して失敗すること）。
    declared_wrong_value = "0" * 64
    assert declared_wrong_value != actual_hash
    with pytest.raises(AssertionError):
        assert declared_wrong_value == m.compute_file_sha256(manifest_path)

    # (b) manifest 自体が不正（schema 取り違え）なら validator が拒否する。
    invalid_manifest_path = tmp_path / "invalid_practice_manifest.json"
    invalid_manifest_path.write_text(json.dumps({"schema": "wrong-schema/9.9"}), encoding="utf-8")
    invalid_data = m._loads_strict_json(invalid_manifest_path.read_text(encoding="utf-8"))
    with pytest.raises(m.Run9ValidationError, match="practice split manifest schema"):
        m.validate_practice_split_manifest(invalid_data)


# --- Fix B: 「両枝同一recipe」記述の是正 -------------------------------------


def test_fix6b_no_stale_same_recipe_language_in_contract_or_docs() -> None:
    """PoR §8 の等価性規定と矛盾する「両枝同一recipe」型の記述が
    RUN9_CONTRACT.yaml / rev 0.3 文書 / README.md に残っていないことの
    全数掃討回帰検査（Codex bot レビュー第6巡 Fix B 採用）。等条件・等
    予算が要求されるのは各枝『内』の二体間のみであり、PRACTICE と
    EDUCATION 自体を「同一recipe」で括ってはならない。"""
    stale_phrases = ("同一recipe", "同一 recipe", "same recipe", "同一レシピ")
    for path in (CONTRACT_PATH, REVISION_DOC_PATH, _RUN_DIR / "README.md"):
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, f"{path.name} に陳腐化した記述 {phrase!r} が残っている"


def test_fix6b_contract_interventions_description_states_per_branch_recipe() -> None:
    """`interventions.description` が枝別 recipe（非対称性が実験変数）を
    正しく記述していることの直接確認。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "枝別recipe" in contract_text
    assert "非対称" in contract_text


def test_fix6b_learning_recipe_sha_reason_states_per_branch_bundled_manifest() -> None:
    """`learning_recipe_sha.reason` が「枝別 recipe を束ねた単一 manifest」
    という正しい意味論（欄の分割はしない・manifest 内で枝別に持つ）を
    記述していることの確認。"""
    field_reason = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))["learning_recipe_sha"]["reason"]
    assert "枝別" in field_reason
    assert "非対称" in field_reason
    assert "PoR §8" in field_reason


def test_fix6b_design_revision_doc_unchanged_this_round() -> None:
    """本巡（第6巡）は RUN9_CONTRACT.yaml のみを編集し
    DESIGN_RUN9_REVISION_0.3.md は無改変だったため、design_revision_doc_sha256
    は前巡（User 外部レビュー P1-1〜P2-5 実装時に確定した値）のまま
    据え置きであることを確認する（値そのものの回帰チェックは
    `test_revision02_doc_sha256_pin_matches_actual_file` が汎用的に担う
    ため、本テストは「本巡で変化していない」ことに焦点を当てる）。"""
    contract_raw_local = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    field = contract_raw_local["design_revision_doc_sha256"]
    assert field["value"] == _sha256_file(REVISION_DOC_PATH)
