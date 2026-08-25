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
# 現行 design_revision (0.4) の差分メモ。design_revision_doc_sha256 が
# pin する対象。
REVISION_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.4.md"
# rev 0.2/0.3 文書は無改変のまま存続する（design_revision 系譜の各1件）。
REVISION_0_2_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.2.md"
REVISION_0_3_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.3.md"
POR_ADJUDICATION_PATH = _RUN_DIR / "POR_CONCEPT_ADJUDICATION_20260824.txt"
DERIVED_DESIGN_CHANGES_PATH = _RUN_DIR / "DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt"
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
    """rev 0.4 では `run9_schema.DESIGN_REVISION` 自体が "0.4" を凍結する
    （テスト名は歴史的に revision02_ prefix のまま — Fix 15 の
    founder_genome_shas 改名前例と同様、rename ではなく assertion のみ
    更新する）。"""
    assert m.DESIGN_REVISION == "0.4"


def test_revision02_current_contract_declares_0_2(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["design_revision"] == "0.4"
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
    """design_revision 0.2 → 0.3 → 0.4: 旧 "0.2" を宣言する contract も
    引き続き意図どおり拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_revision04_old_0_3_contract_rejected(contract_raw: Dict[str, Any]) -> None:
    """rev 0.4（DESIGN_RUN9_REVISION_0.4.md、外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモ
    の採用）: 旧 "0.3" を宣言する contract も意図どおり拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.3"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_revision03_old_0_2_contract_rejection_message_names_current_revision(
    contract_raw: Dict[str, Any],
) -> None:
    """PR #317 Codex bot レビュー第1巡 Fix 2 採用: 拒否メッセージが固定
    ファイル名（例: "DESIGN_RUN9_REVISION_0.2.md"）をハードコードして
    いると、design_revision を上げるたびにメッセージ内のファイル名だけが
    陳腐化する（実際に 0.2 -> 0.3、0.3 -> 0.4 進行時に発生した不備）。
    メッセージが `DESIGN_REVISION` 定数（現在は "0.4"）から動的に導出
    されていることを、"0.2" 拒否時のメッセージに現行の "0.4" が含まれる
    ことで確認する — メッセージが旧値のまま固定化されていれば失敗する。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError, match="0.4"):
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


def test_revision02_backbone_runtime_bundle_sha_pinned_via_run9_render_code_commit(
    contract_raw: Dict[str, Any],
) -> None:
    """rev 0.4（DESIGN_RUN9_REVISION_0.4.md「User裁定a/bの記録」の b）→
    2026-08-25 同日中の追加 User 裁定①による是正後: `backbone_runtime_bundle_sha`
    が PINNED である根拠は独立の前方宣言欄 `run9_render_code_commit`
    （status: `DECLARED_FOR_RUN9`）の確定であり、`render_code_commit`
    （RUN6 の歴史的 export provenance）は `INFERRED_UNCONFIRMED` のまま
    でよい——歴史的事実は遡って attest しない方針のため両者は独立
    （`backbone_checkpoint_sha` は元々直接記録4件一致のため PINNED の
    まま——対象は別欄）。テスト名は歴史的に revision02_ prefix のまま
    （rename ではなく assertion のみ更新する既存の repo 慣習）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle["historical_export_provenance"]["render_code_commit"]["status"] == "INFERRED_UNCONFIRMED"
    assert bundle["run9_runtime_inputs"]["run9_render_code_commit"]["status"] == "DECLARED_FOR_RUN9"
    assert (
        bundle["run9_runtime_inputs"]["run9_render_code_commit"]["declaration"]["declared_by"]
        == "User"
    )
    assert (
        bundle["run9_runtime_inputs"]["run9_render_code_commit"]["declaration"]["declared_at"]
        == "2026-08-25"
    )

    field = contract_raw["backbone_runtime_bundle_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(BACKBONE_BUNDLE_PATH)


def test_revision02_render_code_commit_status_and_bundle_sha_status_are_consistent(
    contract_raw: Dict[str, Any],
) -> None:
    """負例的整合検査（2026-08-25 User 追加裁定①で意味論を更新）: contract
    の `backbone_runtime_bundle_sha.status` が `PINNED` であるとき、その
    根拠であるべき bundle 内 `run9_render_code_commit.status` が
    `DECLARED_FOR_RUN9` になっていること（両者の食い違いを機械的に検出
    する）。旧版は `render_code_commit`（歴史的推定）の status を根拠と
    見なしていたが、追加裁定①により根拠は独立の前方宣言欄へ移った——
    `render_code_commit` が `INFERRED_UNCONFIRMED` のままであることは
    もはや不整合ではない。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    bundle_field_status = contract_raw["backbone_runtime_bundle_sha"]["status"]
    if bundle_field_status == "PINNED":
        assert bundle["run9_runtime_inputs"]["run9_render_code_commit"]["status"] == "DECLARED_FOR_RUN9", (
            "backbone_runtime_bundle_sha は PINNED だが、bundle 内 "
            "run9_render_code_commit は DECLARED_FOR_RUN9 になっていない — "
            "PINNED 判定の根拠が bundle 内で裏付けられていない"
        )


def test_revision02_backbone_runtime_bundle_sha_matches_actual_file_once_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """`backbone_runtime_bundle_sha` の実ファイル照合を、design_doc_sha256
    / design_revision_doc_sha256 の既存テスト（`test_revision02_doc_sha256_pin_matches_actual_file`
    / `test_revision02_compute_file_sha256_matches_design_doc_sha256_pin`）
    と同型で事前配線する（Codex bot レビュー PR #316 第9巡指摘, e490985,
    部分採用）。現状 status は PENDING のため value は None のままである
    ことだけを確認するが、将来 `run9_render_code_commit`（前方宣言欄）が
    確定して本欄が PINNED へ昇格した瞬間、この同じテストが
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
    """2026-08-25 User 追加裁定①による差し戻し後も、値自体・根拠
    （inference_basis、無改変で保持）・history（昇格→差し戻し両イベント
    の記録）がいずれも保持されていること。attestation はもはや本欄には
    無い（実体的な意味は run9_render_code_commit.declaration へ移った —
    別テスト test_rev04_run9_render_code_commit_declared_for_run9_with_ruling_reference
    参照）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    rcc = bundle["historical_export_provenance"]["render_code_commit"]
    assert rcc["commit_full"] == "e2307b1080b00f3999702ce9017cfd75c7f862fe"
    assert rcc["commit_short"] == "e2307b1"
    assert rcc["status"] == "INFERRED_UNCONFIRMED"
    assert rcc["confirmation_required"]
    assert rcc["inference_basis"]
    assert "attestation" not in rcc
    history = rcc["history"]
    # PR #319 第2巡指摘（P2, 採用）の構造分離イベントが append-only で
    # 追加され、2026-08-25 の昇格→差し戻しの2件に続く3件目となった。
    assert len(history) == 3
    assert all(h["date"] == "2026-08-25" for h in history)
    assert any("PR #319" in h["event"] for h in history)
    # RUN6 export 記録自体には commit が明記されていない事実が明文化されていること
    # （note は無改変ではないが、この根拠自体は消えていないことを確認する）。
    assert "s5_record" in rcc["note"]
    assert "does not" in rcc["note"].lower() or "does NOT" in rcc["note"]


def test_revision02_backbone_bundle_acoustic_onnx_matches_ruling() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert (
        bundle["run9_runtime_inputs"]["acoustic_onnx_sha256"]["value"]
        == "aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706"
    )


def test_revision02_backbone_bundle_checkpoint_matches_contract() -> None:
    """backbone_runtime_bundle.json と RUN9_CONTRACT.yaml の
    backbone_checkpoint_sha が同一値を pin していること（二重管理の不整合
    が無いことの確認）。checkpoint_sha256 自体は render_code_commit の
    降格とは独立に、両ファイルとも引き続き PINNED 相当の値を持つ。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    contract_raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert (
        bundle["run9_runtime_inputs"]["checkpoint_sha256"]["value"]
        == contract_raw["backbone_checkpoint_sha"]["value"]
    )


def test_revision02_backbone_bundle_run7_not_used_records_teacher_swap_reason() -> None:
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "教師交代" in bundle["run7_not_used"]["reason"] or "teacher swap" in bundle["run7_not_used"]["reason"].lower()


# --- item 4: rights_manifest が PENDING_USER_ATTESTATION の間、domain user
#     anchor は未 pin のまま（gate BLOCKED 継続） -----------------------------


def test_revision02_rights_manifest_is_pending_user_attestation() -> None:
    """rev 0.4（4層再編）後: voice_identity_rights 層の内容・attest 対象は
    無改変のまま（DESIGN_RUN9_REVISION_0.4.md 変更1・2「attest対象の更新」
    — User裁定「aとbを承認」のa = 新4層構造に対する attest は次段で確定）。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    layer = rights["voice_identity_rights"]
    assert layer["rights_class"] == "PENDING_USER_ATTESTATION"
    assert layer["consent_status"] == "PENDING_USER_ATTESTATION"
    assert layer["attestation"]["attested"] is False
    assert layer["usage_grants"]["raw_audio_publication"] == "not_granted"
    assert layer["usage_grants"]["model_general_distribution"] == "not_granted"


def _load_rights_manifest_and_ledger() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """rights_manifest / donor_ledger を、`verify_rights_manifest_against_ledger()`
    が規定する重複キー拒否読込経路（`run9_schema.load_rights_manifest_json()`
    / `load_user_donor_ledger_json()`）経由で読み込む — 生の `json.loads()`
    は使わない（Codex bot レビュー PR #316 第10巡指摘採用, c34bdff: 生
    `json.loads()` は同一 entry 内の重複キーを last-key-wins で黙って
    解決してしまうため、rights 検証テスト群全体をこの2関数経由へ統一する）。

    rev 0.4（DESIGN_RUN9_REVISION_0.4.md 変更1・2）: 実ファイルは4層構造
    （schema `run9-rights-manifest/2.0`）へ再編済みのため、
    `run9_schema.extract_voice_identity_rights_layer()` で
    voice_identity_rights 層を旧 schema `run9-user-donor-rights/1.0`
    相当のフラット構造へ変換してから返す——本ヘルパを消費する既存テスト群
    （card_id 完全一致・値照合・改ざん拒否等）はこの変換により無改訂で
    そのまま通る。
    """
    raw = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    rights = m.extract_voice_identity_rights_layer(raw)
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
    壊していないことの確認）。rev 0.4 の4層構造は
    `extract_voice_identity_rights_layer()` でフラット化してから渡す。"""
    raw = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    rights = m.extract_voice_identity_rights_layer(raw)
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
# PR #319 Codex bot レビュー第2巡対応 — Fix 4（P2）: README の stale blocker
# 文の更新（performer/composer 記録済みなのに未記録と言っていた記述の是正）
# ---------------------------------------------------------------------------


def test_fix319_4_readme_blocker_no_longer_claims_singer_composer_unrecorded() -> None:
    """旧 blocker 文「歌唱者個人・作曲者/作詞者の特定が repo 内に記録なし」
    は、performer/composer が同 revision（2026-08-25 User 追加裁定②）で
    既に Junya Koguchi と記録済みであるにもかかわらず未記録と主張していた
    ——「残存」節（現在ブロッカーとして提示している箇所）からこの stale な
    文言を除去したことを確認する（Codex bot レビュー PR #319 第2巡指摘,
    P2, 採用）。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    blocker_section = readme.split("**残存**:", 1)[1]
    assert "歌唱者個人・作曲者/作詞者の特定が repo 内" not in blocker_section
    assert "に記録なし" not in blocker_section.split("2. **VG-L0", 1)[0]


def test_fix319_4_readme_blocker_1_lists_confirmed_performer_composer_owner() -> None:
    """残存ブロッカー(1) が、確定済み（performer/composer/owner = Junya
    Koguchi 出典付き、recording license = CC BY-SA 4.0）を明記している
    こと。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    blocker_1 = readme.split("**残存**:", 1)[1].split("2. **VG-L0", 1)[0]
    assert "確定済み" in blocker_1
    assert "Junya Koguchi" in blocker_1
    assert "CC BY-SA 4.0" in blocker_1


def test_fix319_4_readme_blocker_1_lists_remaining_unresolved_items() -> None:
    """残存ブロッカー(1) が、真に未解決の項目（lyricist=UNRESOLVED_EXTERNAL・
    SA義務の解釈・User attest 待ちの usage grants・Fix 5/6 の rights_class/
    consent_status 仕分け）のみを未解決として挙げていること。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    blocker_1 = readme.split("**残存**:", 1)[1].split("2. **VG-L0", 1)[0]
    assert "lyricist" in blocker_1
    assert "UNRESOLVED_EXTERNAL" in blocker_1
    assert "share_alike_applies_to_synthesis_output" in blocker_1 or "share-alike" in blocker_1
    assert "PENDING_USER_ATTESTATION" in blocker_1
    assert "not_granted" in blocker_1


def test_fix319_4_readme_no_stale_singer_composer_unrecorded_claim_anywhere() -> None:
    """README 全体を grep 掃討: 「特定が...記録なし」という stale な確定
    文言が、履歴的記述（明示的過去形・日付付きセクション）以外に残って
    いないこと（同型の stale 文の全数確認）。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "の特定が repo 内に記録なし" not in readme
    assert "の特定が repo 内\n" not in readme


# ---------------------------------------------------------------------------
# PR #316 Codex bot レビュー第2巡対応 — runtime bundle に RUN6 render フロー
# の全消費物（canon model assets）を追加
# ---------------------------------------------------------------------------


def test_revision02_bundle_has_canon_model_assets_section() -> None:
    """PR #319 第2巡指摘（P2, 採用）: canon_model_assets は
    run9_runtime_inputs 節配下へ移動した（RUN9 が実際に消費する入力の
    直接証拠であり、歴史的推定を含まないため）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    runtime_inputs = bundle["run9_runtime_inputs"]
    assert "canon_model_assets" in runtime_inputs
    assert "assets" in runtime_inputs["canon_model_assets"]
    assert set(runtime_inputs["canon_model_assets"]["assets"].keys()) == {
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
    canon_model_assets = bundle["run9_runtime_inputs"]["canon_model_assets"]
    checked = 0
    for section_name in ("assets", "acoustic_export_companions"):
        section = canon_model_assets[section_name]
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
    runtime_inputs = bundle["run9_runtime_inputs"]
    probe = json.loads(
        (_RUN_DIR.parent / "records" / "vgl0_control_axis_probe_result_n6.json").read_text(
            encoding="utf-8"
        )
    )
    pins = probe["pins"]
    assets = runtime_inputs["canon_model_assets"]["assets"]
    assert assets["linguistic_onnx"]["value"] == pins["canon_linguistic_onnx"]["sha256"]
    assert assets["variance_duration_onnx"]["value"] == pins["canon_dur_onnx"]["sha256"]
    assert assets["variance_pitch_onnx"]["value"] == pins["canon_pitch_onnx"]["sha256"]
    assert assets["phonemes_txt"]["value"] == pins["canon_phonemes"]["sha256"]

    companions = runtime_inputs["canon_model_assets"]["acoustic_export_companions"]
    assert companions["dsconfig_yaml"]["value"] == pins["acoustic_dsconfig"]["sha256"]
    assert companions["acoustic_phonemes_json"]["value"] == pins["acoustic_phonemes_json"]["sha256"]
    assert companions["speaker_embed"]["value"] == pins["speaker_embed"]["sha256"]

    # acoustic_onnx / vocoder_onnx の両方が、bundle 側の既存
    # run9_runtime_inputs 直下の pin とも probe record 側とも一致すること
    # （run6 backbone の同一性の追加の交差確認）。
    assert runtime_inputs["acoustic_onnx_sha256"]["value"] == pins["acoustic_onnx"]["sha256"]
    assert runtime_inputs["vocoder"]["runtime_onnx_sha256"]["value"] == pins["vocoder_onnx"]["sha256"]


def test_revision02_bundle_canon_model_assets_cross_checked_across_4_probe_records() -> None:
    """canon_model_assets の各値が、独立した4件の probe result（n6 / 無印 /
    n10 / render_reproducibility）すべてで同一であることを確認する
    （n6 以外の3件は補助的な相互一致確認）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assets = bundle["run9_runtime_inputs"]["canon_model_assets"]["assets"]
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
    canon_zip_sha = bundle["run9_runtime_inputs"]["canon_model_assets"]["source_distribution"]["sha256"]
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
    """canon_model_assets 追加自体は backbone_runtime_bundle_sha の PINNED
    化手段を変えない——PINNED 化するのは 2026-08-25 の User 承認 b +
    裁定①によって前方宣言欄 `run9_render_code_commit`（status:
    `DECLARED_FOR_RUN9`）が確定したことによってであり（歴史的
    `render_code_commit` は `INFERRED_UNCONFIRMED` のまま・両欄は独立）、
    canon_model_assets 追加（別巡の独立した拡張）が副作用として昇格を
    引き起こしたのではないことを確認する（`backbone_runtime_bundle_sha`
    は rev 0.4 時点では PINNED — 昇格の原因が正しく
    run9_render_code_commit.status の変化であることの確認であり、
    PENDING 固定の確認ではない）。"""
    field = contract_raw["backbone_runtime_bundle_sha"]
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "canon_model_assets" in bundle["run9_runtime_inputs"]  # 対象拡張が引き続き存在すること
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(BACKBONE_BUNDLE_PATH)


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第2巡対応 — Fix 3（P2）: bundle pin の証明範囲の
# 構造分離（run9_runtime_inputs / historical_export_provenance）
# ---------------------------------------------------------------------------


def test_fix319_2_bundle_has_two_top_level_proof_scope_sections() -> None:
    """bundle が run9_runtime_inputs / historical_export_provenance の
    2節へ再編されていること（Codex bot レビュー PR #319 第2巡指摘, P2,
    採用: bundle pin の証明範囲の構造分離）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert "run9_runtime_inputs" in bundle
    assert "historical_export_provenance" in bundle
    # 旧 top-level 直下キーは新節の配下へ移動済みで、bundle 直下には
    # もう存在しない（構造分離が実体を伴うことの確認 — 節を追加しただけの
    # 見せかけの分離ではない）。
    for moved_key in (
        "checkpoint_sha256",
        "acoustic_onnx_sha256",
        "config_sha256",
        "speaker_map_sha256",
        "phoneme_map_sha256",
        "language_map_sha256",
        "vocoder",
        "run9_render_code_commit",
        "canon_model_assets",
        "render_code_commit",
    ):
        assert moved_key not in bundle, f"{moved_key} は旧位置(bundle直下)にまだ残っている"


def test_fix319_2_run9_runtime_inputs_contains_only_direct_evidence_fields() -> None:
    """run9_runtime_inputs 節は「RUN9 が実際に消費する入力の直接証拠」
    （checkpoint/ONNX sha256・export companions の hash 群・
    run9_render_code_commit 前方宣言）のみを収載し、歴史的推定
    （render_code_commit）を含まないこと。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    runtime_inputs = bundle["run9_runtime_inputs"]
    for expected_key in (
        "checkpoint_sha256",
        "acoustic_onnx_sha256",
        "config_sha256",
        "speaker_map_sha256",
        "phoneme_map_sha256",
        "language_map_sha256",
        "vocoder",
        "run9_render_code_commit",
        "canon_model_assets",
    ):
        assert expected_key in runtime_inputs
    assert "render_code_commit" not in runtime_inputs, (
        "run9_runtime_inputs は直接証拠のみを収載する節であり、歴史的推定である "
        "render_code_commit（historical_export_provenance 節に隔離済み）を含んではならない"
    )
    assert runtime_inputs["run9_render_code_commit"]["status"] == "DECLARED_FOR_RUN9"


def test_fix319_2_historical_export_provenance_contains_only_render_code_commit() -> None:
    """historical_export_provenance 節は RUN6 期の export commit 推定
    （render_code_commit）のみを収載し、status は INFERRED_UNCONFIRMED の
    まま変化していないこと（構造移動のみで内容は無改変）。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    historical = bundle["historical_export_provenance"]
    assert set(historical.keys()) == {"claim_scope_note", "render_code_commit"}
    assert historical["render_code_commit"]["status"] == "INFERRED_UNCONFIRMED"
    assert "RUN9" in historical["claim_scope_note"]
    assert "根拠には使わない" in historical["claim_scope_note"]


def test_fix319_2_bundle_claim_scope_field_documents_pinned_meaning() -> None:
    """bundle 冒頭の claim_scope 節が、backbone_runtime_bundle_sha の
    PINNED が主張する範囲（本文書のバイト同一性 + run9_runtime_inputs の
    確定のみ）と、historical_export_provenance が証明範囲外であることを
    明記していること。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    claim_scope = bundle["claim_scope"]
    assert "run9_runtime_inputs" in claim_scope["statement"]
    assert "historical_export_provenance" in claim_scope["statement"]
    assert "対象外" in claim_scope["statement"] or "含まれない" in claim_scope["statement"]
    assert "why_run9_reproducibility_is_unaffected" in claim_scope
    assert "run9_render_code_commit" in claim_scope["why_run9_reproducibility_is_unaffected"]


def test_fix319_2_run9_contract_yaml_pin_comment_documents_claim_scope() -> None:
    """RUN9_CONTRACT.yaml の backbone_runtime_bundle_sha pin 注記（コメント）
    にも claim scope の要点（バイト同一性 + run9_runtime_inputs 確定のみを
    主張し、historical_export_provenance の真理値は主張しない）が記載されて
    いること（bundle json 側と RUN9_CONTRACT.yaml 側の両方に記載する指示の
    確認）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "claim scope" in contract_text.lower()
    assert "run9_runtime_inputs" in contract_text
    assert "historical_export_provenance" in contract_text
    assert "証明範囲外" in contract_text


def test_fix319_2_backbone_runtime_bundle_sha_history_notes_prior_value() -> None:
    """repin 時、旧値（69ea578b...）が履歴として append-only に保持されて
    いること（コミット規約: sha 再計算 → repin、旧値は履歴保持）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "69ea578bb702f0dd0ca16c1a20b34d4f78c81495b1318a5d0503050c84d37a53" in contract_text


# ---------------------------------------------------------------------------
# User 裁定 2026-08-24（PoR メモ編入、design_revision 0.2 -> 0.3）対応
# ---------------------------------------------------------------------------


def test_por_revision_design_revision_doc_path_exists() -> None:
    """テスト名は歴史的に por_revision_ prefix のまま（PoR メモ編入時の
    命名）——rev 0.4 現在は最新差分メモへ追随して assertion のみ更新する。"""
    assert REVISION_DOC_PATH.exists()
    assert REVISION_DOC_PATH.name == "DESIGN_RUN9_REVISION_0.4.md"
    assert REVISION_0_3_DOC_PATH.exists()


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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
    assert "Practice/Education gain の\n基準ノイズは C0 由来" in doc or "基準ノイズは C0 由来" in doc


def test_p1_3_profile_side_effect_recorded_as_c1_minus_c0_documented() -> None:
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
    assert "holdout 開封後の" in doc
    assert "human_audit_mode` 変更は禁止" in doc
    assert "人間監査を" in doc
    assert "の救済に使わない" in doc
    assert "SCIENTIFIC_NULL" in doc and "Identity SHIFTED" in doc


# --- P2-3: 機械的校正の定義 ----------------------------------------------------


def test_p2_3_calibration_definition_section_present() -> None:
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
    assert "## 改訂 G — 機械的校正の定義" in doc
    assert "人間知覚との一致証明ではない" in doc


def test_p2_3_calibration_result_rules_present() -> None:
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
    assert "UNCALIBRATED" in doc
    assert "holdout 開封前に freeze" in doc


# --- P2-5: Non-Claim / Rights Boundary ---------------------------------------


def test_p2_5_non_claim_rights_boundary_section_present() -> None:
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
    assert "## 改訂 H — Non-Claim / Rights Boundary" in doc


def test_p2_5_non_claim_five_items_present() -> None:
    """本文は Markdown の折り返しで改行+インデント空白を含むため、
    照合前に空白（改行含む）を単一スペースへ正規化してから部分文字列
    一致を見る。"""
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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
    doc = REVISION_0_3_DOC_PATH.read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# RUN9 Phase 3 対応 — item 1: identity metric space の定義と pin
# ---------------------------------------------------------------------------

IDENTITY_METRIC_SPACE_PATH = _RUN_DIR / "inputs" / "identity_metric_space.json"


def test_phase3_identity_metric_space_file_exists_and_is_valid_json() -> None:
    """Codex bot レビュー PR #318 第6巡 Fix 19 で validator 呼び出しへ強化
    （削除ではなく置換）: 旧実装はトップレベルの `schema`/`metric_version`
    2ラベルしか検証しておらず、`extraction_procedure` の削除や
    `voiced_mask` の省略・ネスト型変更が素通りしていた。
    `validate_identity_metric_space_manifest()` を通すことで、閉じた
    形状（トップレベル必須キー閉集合・`extraction_procedure`/
    `calibration` の必須ネストキーと型）を機械強制する。"""
    assert IDENTITY_METRIC_SPACE_PATH.exists()
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert data["schema"] == "run9-identity-metric-space/1.2"
    assert data["metric_version"] == "run9-identity-metric/0.5"
    m.validate_identity_metric_space_manifest(data)  # raises on any shape defect


def test_phase3_domain_metric_space_sha_matches_canonical_form_of_identity_metric_space() -> None:
    """`domains/identity_domain_run9_v1.json` の `metric_space_sha` が、
    `inputs/identity_metric_space.json` の正規形 sha256（af0_anchor_manifest
    と同一規約）と一致することを実測で確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    recomputed = _sha256_canonical_json(metric_space_obj)
    assert domain_raw["metric_space_sha"] == recomputed
    assert m._SHA256_HEX_RE.match(domain_raw["metric_space_sha"])
    assert domain_raw["metric_space_sha"] != "<PIN_BEFORE_RUN>"


def test_phase3_identity_metric_space_excludes_f0() -> None:
    """f0 が identity metric から明示除外されていることの内容検証
    （PoR §2 の層分離整合 — pitch は Trait/Technique 層の観測軸）。"""
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    f0_exclusion = data["identity_feature"]["f0_exclusion"]
    assert f0_exclusion["excluded"] is True
    # distance 節に f0 が混入していないこと（ensure_ascii=False で
    # ダンプする — True だと日本語の unicode エスケープ列に偶然 "f0" と
    # いう16進数の並びが現れ得るため、素の文字列として照合する）。
    assert "f0" not in json.dumps(data["distance"], ensure_ascii=False).lower()
    # identity_feature の vector_source は sp（スペクトル包絡）由来であり、
    # f0 という語を独立トークンとして含まない。
    vector_source = data["identity_feature"]["vector_source"].lower()
    assert "f0" not in vector_source


def test_phase3_identity_metric_space_aperiodicity_is_advisory_only() -> None:
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert data["identity_feature"]["aperiodicity"]["status"] == "advisory"


def test_phase3_identity_metric_space_distance_is_euclidean_symmetric_deterministic() -> None:
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert data["distance"]["method"] == "Euclidean distance"
    assert "symmetric" in data["distance"]["properties"]
    assert "deterministic" in data["distance"]["properties"]


def test_phase3_identity_metric_space_calibration_references_c0_c1_and_holdout_freeze() -> None:
    """Codex bot レビュー PR #318 第6巡 Fix 18 で `calibration_procedure`
    （散文）は機械可読な `calibration` 節へ置換された。旧テストが確認して
    いた内容（C0/C1・95th percentile・R9-G5・holdout freeze・positive/
    negative reference への言及）を新構造で同値に確認する。"""
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    calibration = data["calibration"]
    assert "calibration_procedure" not in data
    dumped = json.dumps(calibration, ensure_ascii=False)
    assert "C0" in dumped and "C1" in dumped
    assert "P95" in calibration["freeze_threshold"]["formula"]
    assert "R9-G5" in calibration["source_references"]["r9_g5"]
    assert "holdout 開封前に freeze" in calibration["source_references"]["holdout_freeze"]
    assert "positive_reference" in calibration["validity_gates"]["positive_reference_gate"]["id"]
    assert "negative_reference" in calibration["validity_gates"]["negative_reference_gate"]["id"]


def test_phase3_identity_metric_space_feasibility_note_references_design_failure() -> None:
    data = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    note = data["feasibility_note"]
    assert "DESIGN FAILURE" in note or "DESIGN_FAILURE" in note
    assert "UNOBSERVABLE" in note
    assert "事後" in note  # 事後調整で救済しないことの明記


def test_phase3_domain_is_pinned_still_false_after_metric_space_pin() -> None:
    """metric_space_sha が pin されても、user anchor が残るため
    `is_pinned()` は依然 False（意図どおり）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    domain = m.run9_identity_domain_from_dict({
        **domain_raw,
        "anchor_hashes": {
            "af0": domain_raw["anchor_hashes"]["af0"],
            "ritsu": domain_raw["anchor_hashes"]["ritsu"],
            "user": "c" * 64,  # user はまだ pin されないため合成値で domain 構築だけ確認
        },
    })
    # user anchor はまだ本物ではないため is_pinned() は本テストの主張対象
    # ではない（別途 anchor_hashes.user 自体が "<PIN_BEFORE_RUN>" のままで
    # あることを直接確認する）。
    assert domain_raw["anchor_hashes"]["user"] == "<PIN_BEFORE_RUN>"
    assert domain.metric_space_sha == domain_raw["metric_space_sha"]


def test_phase3_readme_documents_metric_space_fable_pin_with_veto() -> None:
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "metric space" in readme.lower() or "metric_space" in readme
    assert "Fable" in readme
    assert "veto" in readme.lower()


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第6巡 Fix 19: identity metric space manifest
# validator の閉じた形状検証（正例 + 負例）。旧テストはトップレベル
# ラベル2個しか見ておらず、`extraction_procedure` 削除・`voiced_mask`
# 省略・ネスト型変更・calibration ゲート欠落のいずれも素通りしていた。
# ---------------------------------------------------------------------------


def _valid_metric_space_doc() -> Dict[str, Any]:
    return json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))


def test_fix19_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: 現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix19_validator_rejects_extraction_procedure_deletion() -> None:
    doc = _valid_metric_space_doc()
    del doc["extraction_procedure"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_voiced_mask_omission() -> None:
    doc = _valid_metric_space_doc()
    del doc["extraction_procedure"]["voiced_mask"]
    with pytest.raises(m.Run9ValidationError, match="voiced_mask"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_voiced_mask_definition_missing() -> None:
    doc = _valid_metric_space_doc()
    del doc["extraction_procedure"]["voiced_mask"]["definition"]
    with pytest.raises(m.Run9ValidationError, match="voiced_mask missing required key.*definition"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_frame_period_ms_nested_type_change() -> None:
    """`frame_period_ms` が数値でなく文字列化された repin を拒否する
    （ネスト型変更が素通りする穴のリグレッションガード）。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["frame_period_ms"] = "5.0"
    with pytest.raises(m.Run9ValidationError, match="frame_period_ms"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_sample_rate_value_hz_as_float() -> None:
    """`_is_strict_int()` 系の型厳密化を sample_rate.value_hz にも適用する
    （6.0/True のような非正準値が genome_id 決定論を壊す穴と同型）。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate"]["value_hz"] = 44100.0
    with pytest.raises(m.Run9ValidationError, match="value_hz"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_calibration_c1_gate_deletion() -> None:
    """calibration ゲート欠落（C1 を無視する実装が同じ pin に適合してし
    まっていた Fix 18 指摘の再発防止）を拒否する。"""
    doc = _valid_metric_space_doc()
    del doc["calibration"]["validity_gates"]["c1_gate"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_calibration_worked_example_without_synthetic_disclaimer() -> None:
    """worked_example の disclaimer が synthetic/実測ではない旨を含まない
    場合を拒否する（実測偽装の禁止 — 本 repo の規律）。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["worked_example"]["disclaimer"] = "この例は正しい"
    with pytest.raises(m.Run9ValidationError, match="synthetic"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_unknown_top_level_key() -> None:
    doc = _valid_metric_space_doc()
    doc["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix19_validator_rejects_schema_version_mismatch() -> None:
    doc = _valid_metric_space_doc()
    doc["schema"] = "run9-identity-metric-space/1.0"
    with pytest.raises(m.Run9ValidationError, match="schema"):
        m.validate_identity_metric_space_manifest(doc)


# ---------------------------------------------------------------------------
# RUN9 Phase 3 対応 — item 3: learning recipe manifest
# ---------------------------------------------------------------------------


def _valid_learning_recipe_arm() -> Dict[str, Any]:
    """Codex bot レビュー PR #318 第2巡 Fix 7 採用: draft/runnable の
    二段 schema は作らず単一の厳密 schema とするため、stopping_rule/
    trial_count/render_budget はもはや None を許容しない（実行可能な
    プレースホルダ値へ更新 — 具体的な語彙・数値そのものは VG-L0 ハーネス
    実装時の build 対象のまま、ここでは「型として実行可能」な最小例）。

    第5巡 Fix 17 採用: rev 0.3 改訂E「公平性（PoR §8）」節が定める枝内
    二体等条件のうち、Fix 7/15 で未カバーだった残り5項目
    （search_space/candidate_generation/evaluator/compute_budget/
    data_binding）を追加。値そのものの語彙・形式は VG-L0 ハーネス実装時の
    build 対象のまま、ここでは「非空文字列」の最小例。"""
    return {
        "equal_budget_within_arm": True,
        "stopping_rule": "fixed_trial_count",
        "trial_count": 100,
        "render_budget": 100,
        "search_space": "placeholder_search_space_v0",
        "candidate_generation": "placeholder_candidate_generation_v0",
        "evaluator": "placeholder_evaluator_v0",
        "compute_budget": "placeholder_compute_budget_v0",
        "data_binding": "placeholder_data_binding_v0",
    }


def _valid_learning_recipe_manifest() -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_LEARNING_RECIPE_MANIFEST,
        "seed": m.LEARNING_SEED,
        "practice_recipe": _valid_learning_recipe_arm(),
        "education_recipe": _valid_learning_recipe_arm(),
    }


def test_phase3_learning_recipe_manifest_valid_passes() -> None:
    m.validate_learning_recipe_manifest(_valid_learning_recipe_manifest())


def test_phase3_learning_recipe_manifest_wrong_seed_rejected() -> None:
    manifest = _valid_learning_recipe_manifest()
    manifest["seed"] = 1
    with pytest.raises(m.Run9ValidationError, match="seed must be the exact int"):
        m.validate_learning_recipe_manifest(manifest)


def test_phase3_learning_recipe_manifest_seed_bool_rejected() -> None:
    """`seed` は bool を拒否する（`True == 1` だが 909002 とは一致しない
    ため実害は小さいが、`_is_strict_int` の家風どおり bool を明示的に
    排除することの確認）。"""
    manifest = _valid_learning_recipe_manifest()
    manifest["seed"] = True
    with pytest.raises(m.Run9ValidationError):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_phase3_learning_recipe_manifest_equal_budget_false_rejected(arm_name: str) -> None:
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["equal_budget_within_arm"] = False
    with pytest.raises(m.Run9ValidationError, match="equal_budget_within_arm must be exactly True"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_phase3_learning_recipe_manifest_arm_missing_rejected(arm_name: str) -> None:
    manifest = _valid_learning_recipe_manifest()
    del manifest[arm_name]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("bad_value", [None, "", "   ", 123, 1.5, True])
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix7_stopping_rule_non_empty_str_required(arm_name: str, bad_value: Any) -> None:
    """必須テスト（Codex bot レビュー PR #318 第2巡 Fix 7, 負例1/3）:
    `stopping_rule` は非空文字列以外（None/空文字/空白のみ/数値/bool）を
    すべて拒否する — draft 段階の None プレースホルダはもはや通らない。"""
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["stopping_rule"] = bad_value
    with pytest.raises(m.Run9ValidationError, match="stopping_rule must be a non-empty string"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize(
    "bad_value", [None, 0, -1, -0.5, "10", True, float("nan"), float("inf"), 1.5, 2.0],
)
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix7_trial_count_positive_finite_number_required(arm_name: str, bad_value: Any) -> None:
    """必須テスト（Fix 7, 負例2/3。Fix 15 で 1.5/2.0 の float 全般拒否へ
    厳密化）: `trial_count` は None/0/負値/文字列/bool/NaN/inf に加え、
    `1.5`（分数）・`2.0`（整数値だが型が float）もすべて拒否し、厳密な
    正の int のみを許可する — READY 昇格時点で実行不能な予算（0件・負の
    試行回数・分数試行等）が凍結される事故を防ぐ。"""
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["trial_count"] = bad_value
    with pytest.raises(m.Run9ValidationError, match="trial_count must be"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("bad_value", [None, 0, -1, -0.5, "10", True, float("nan"), float("inf")])
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix7_render_budget_positive_finite_number_required(arm_name: str, bad_value: Any) -> None:
    """必須テスト（Fix 7, 負例3/3）: `render_budget` も trial_count と
    同じ実行可能性要件（正の有限数値）を課される。"""
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["render_budget"] = bad_value
    with pytest.raises(m.Run9ValidationError, match="render_budget must be"):
        m.validate_learning_recipe_manifest(manifest)


def test_fix7_valid_arm_with_float_render_budget_accepted() -> None:
    """正例回帰: render_budget は連続予算でありうるため、正の有限 float
    も引き続き受理される（bool のみを明示的に除外し、それ以外の数値型は
    許容する設計であることの確認）。trial_count の float 受理は Fix 15 で
    廃止された — 旧テスト名が主張していた `trial_count = 100.0` の受理は
    もはや成立しない（下記 Fix 15 セクションの負例で 100.0 相当の
    `2.0` が拒否されることを確認する）。"""
    manifest = _valid_learning_recipe_manifest()
    manifest["practice_recipe"]["render_budget"] = 50.5
    m.validate_learning_recipe_manifest(manifest)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# RUN9 Phase 3 対応 — Codex bot レビュー PR #318 第5巡 Fix 17: rev 0.3
# 改訂E「公平性（PoR §8）」節が定める枝内二体等条件のうち、Fix 7/15 で
# 未カバーだった残り5項目（search_space/candidate_generation/evaluator/
# compute_budget/data_binding）を機械検証可能フィールドとして追加する。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["search_space", "candidate_generation", "evaluator", "compute_budget", "data_binding"],
)
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix17_equal_condition_field_missing_rejected(arm_name: str, field_name: str) -> None:
    """必須テスト（Fix 17, 負例1/3）: 新設5キーはいずれも欠落すると
    `missing required key` で拒否される（`_LEARNING_RECIPE_ARM_KEYS` の
    必須集合に組み込まれていることの確認）。"""
    manifest = _valid_learning_recipe_manifest()
    del manifest[arm_name][field_name]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("bad_value", [None, "", "   ", 123, 1.5, True, [], {}])
@pytest.mark.parametrize(
    "field_name",
    ["search_space", "candidate_generation", "evaluator", "compute_budget", "data_binding"],
)
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix17_equal_condition_field_non_empty_str_required(
    arm_name: str, field_name: str, bad_value: Any
) -> None:
    """必須テスト（Fix 17, 負例2/3）: 新設5キーはいずれも非空文字列以外
    （None/空文字/空白のみ/数値/bool/list/dict）を拒否する — Fix 7 の
    stopping_rule と同じ機械検証水準を適用する。"""
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name][field_name] = bad_value
    with pytest.raises(m.Run9ValidationError, match=f"{field_name} must be a non-empty string"):
        m.validate_learning_recipe_manifest(manifest)


def test_fix17_equal_condition_fields_shared_object_enforces_equality_by_construction() -> None:
    """必須テスト（Fix 17, 負例3/3 の代替 — 「founder 間不一致」テスト）:
    `practice_recipe`/`education_recipe` は各枝ごとに単一の object として
    定義され、R9F-01/R9F-02 の双方へその1つの object がそのまま共通適用
    される（`run9_controlprofile.derive_profile()` は `branch` 単位で
    `updates` を受け取り、founder 別の recipe 値を表現するフィールドは
    schema 上そもそも存在しない）。したがって「founder 間で新フィールドが
    不一致」を表現する入力自体が構造的に組み立てられず、既存の founder
    間一致比較器（`run9_schema.load_run9_contract()` の
    `founder_genome_shas` 一致検証等）を新フィールドへ配線する対象も
    存在しない — 本テストはその構造的保証（等条件は比較ではなく共有に
    よって保証される）そのものを固定する: manifest の practice_recipe と
    education_recipe が独立した dict オブジェクトであっても、それぞれの
    中身が単一の値である以上、二体 Founder 間の不一致という状態を
    manifest 上に作ることはできない。"""
    manifest = _valid_learning_recipe_manifest()
    # practice_recipe を書き換えても education_recipe とは独立に検証が通る
    # （枝間の値は比較対象外 — PoR §8「PRACTICE と EDUCATION は情報量が
    # 本質的に異なるため、両者を『同じ入力』とはしない」の確認）。
    manifest["practice_recipe"]["search_space"] = "practice_only_space"
    manifest["education_recipe"]["search_space"] = "education_only_space"
    m.validate_learning_recipe_manifest(manifest)  # 例外を投げないことの確認（枝間は非比較）


def test_fix17_equal_condition_fields_all_present_accepted() -> None:
    """正例（Fix 17）: 新設5キーを全て充足した manifest は引き続き
    受理される（`_valid_learning_recipe_arm()` fixture 更新後の回帰
    確認）。"""
    manifest = _valid_learning_recipe_manifest()
    m.validate_learning_recipe_manifest(manifest)  # 例外を投げないことの確認
    for arm_name in ("practice_recipe", "education_recipe"):
        for field_name in (
            "search_space", "candidate_generation", "evaluator", "compute_budget", "data_binding",
        ):
            assert field_name in manifest[arm_name]
            assert isinstance(manifest[arm_name][field_name], str) and manifest[arm_name][field_name]


# ---------------------------------------------------------------------------
# RUN9 Phase 3 対応 — Codex bot レビュー PR #318 第4巡 Fix 15: trial_count
# の整数厳密化。共有の `_require_positive_finite_number()` は
# `trial_count: 1.5` のような分数試行を通してしまい、PINNED recipe
# チェックも満たしてしまっていた（分数試行は実行不能であり、PoR §8 の
# equal_budget_within_arm — 枝内の二体 Founder 間の等予算契約 — を
# 掘り崩す）。`_require_positive_int()` を trial_count 専用に配線する。
# render_budget は連続予算でありうるため対象外のまま
# `_require_positive_finite_number()` を維持する。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [1.5, 2.0, True, "3"])
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_fix15_trial_count_rejects_non_strict_int_values(arm_name: str, bad_value: Any) -> None:
    """必須テスト（Fix 15, 負例）: trial_count は bool を除く厳密 int の
    みを許可し、`1.5`（分数）・`2.0`（整数値に見える float）・`True`
    （bool）・`"3"`（文字列表現）のいずれも型として拒否する。render_budget
    はこの厳密化の対象外であり、これらの値をそのまま許容し続ける
    （trial_count と render_budget の意味論が異なることの確認）。"""
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["trial_count"] = bad_value
    with pytest.raises(m.Run9ValidationError, match="trial_count must be an exact int"):
        m.validate_learning_recipe_manifest(manifest)


def test_fix15_trial_count_accepts_positive_int() -> None:
    """正例（Fix 15）: 厳密な正の int である trial_count は引き続き
    受理される（既存 fixture `_valid_learning_recipe_arm()` も
    `trial_count: 100`（int）を使用しており、Fix 15 適用後も無変更で
    通ることを確認する）。"""
    manifest = _valid_learning_recipe_manifest()
    manifest["practice_recipe"]["trial_count"] = 100
    m.validate_learning_recipe_manifest(manifest)  # 例外を投げないことの確認


def test_phase3_learning_recipe_manifest_no_control_recipe_section() -> None:
    """CONTROL は学習 step を実行しないため recipe を持たない（PoR §4）—
    manifest の許容トップレベルキーに control_recipe 相当が存在しない
    ことの確認。"""
    assert "control_recipe" not in m._LEARNING_RECIPE_TOP_LEVEL_KEYS
    assert m._LEARNING_RECIPE_TOP_LEVEL_KEYS == {"schema", "seed", "practice_recipe", "education_recipe"}


def test_phase3_learning_recipe_manifest_sha_still_pending(contract_raw: Dict[str, Any]) -> None:
    field = contract_raw["learning_recipe_sha"]
    assert field["status"] == "PENDING"
    assert field["value"] is None


def test_phase3_learning_recipe_manifest_pin_prewired_once_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """`learning_recipe_sha` の実ファイル照合を、practice/education
    manifest と同型でテスト層へ事前配線する（PENDING の間は待機。
    PINNED へ昇格した瞬間、この同じテストが (a) 実ファイル sha256 一致、
    (b) `validate_learning_recipe_manifest()` の通過、を自動的に強制
    するようになる）。"""
    field = contract_raw["learning_recipe_sha"]
    if field["status"] == "PINNED":
        assert field["value"] == m.compute_file_sha256(m.LEARNING_RECIPE_MANIFEST_PATH), (
            "learning_recipe_sha が PINNED を宣言しているが、"
            f"{m.LEARNING_RECIPE_MANIFEST_PATH} の実バイト sha256 と一致しない"
        )
        manifest_data = m._loads_strict_json(
            m.LEARNING_RECIPE_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        m.validate_learning_recipe_manifest(manifest_data)  # 例外を投げないことの確認
    else:
        assert field["status"] == "PENDING"
        assert field["value"] is None


def test_phase3_learning_recipe_manifest_path_constant_conventional_location() -> None:
    assert m.LEARNING_RECIPE_MANIFEST_PATH.name == "learning_recipe_manifest.json"
    assert m.LEARNING_RECIPE_MANIFEST_PATH.parent == _RUN_DIR / "inputs"


def test_phase3_learning_recipe_manifest_prewired_check_fails_for_invalid_manifest_simulation(
    tmp_path: Path,
) -> None:
    """事前配線が偽陽性でないことのシミュレーション確認: 不正 manifest
    （seed 不一致）を一時ファイルへ書き、validator が実際に拒否すること
    を確認する。"""
    bad_manifest = _valid_learning_recipe_manifest()
    bad_manifest["seed"] = 1
    bad_path = tmp_path / "learning_recipe_manifest.json"
    bad_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
    data = m._loads_strict_json(bad_path.read_text(encoding="utf-8"))
    with pytest.raises(m.Run9ValidationError):
        m.validate_learning_recipe_manifest(data)


# ---------------------------------------------------------------------------
# RUN9 Phase 3 対応 — 既存不変制約の回帰確認
# ---------------------------------------------------------------------------


def test_phase3_existing_pin_values_unchanged() -> None:
    """既存 pin 値・設計書3本・PoR は無変更（byte-pin テスト維持）の
    直接確認 — Phase 3 は metric_space_sha 以外の pin を一切変更しない。"""
    assert _sha256_file(DESIGN_DOC_PATH) == (
        "b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e"
    )
    assert _sha256_file(REVISION_0_2_DOC_PATH) == (
        "406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb"
    )
    assert _sha256_file(POR_ADJUDICATION_PATH) == (
        "56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007"
    )


def test_phase3_domain_af0_and_ritsu_anchors_unchanged() -> None:
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    assert domain_raw["anchor_hashes"]["af0"] == (
        "183bf32561589ddad69daa0faf5838c3e9601d17b24b62ee32aa629123a87f1e"
    )
    assert domain_raw["anchor_hashes"]["ritsu"] == (
        "88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76"
    )


def test_phase3_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第7巡 Fix 20（P1）: C0/C1 校正標本の
# per-founder テイク数を RUN9_CONTRACT.yaml の `interventions` 配下へ
# 契約 pin する。旧 identity_metric_space.json は「テイク数は本ファイルの
# interventions 規定に従う」と書きながら実体の無い欄への委譲だった欠陥を
# 是正する。正例/負例は他の pin 欄（Fix 15 の trial_count 等）と同型の
# fail-closed 流儀で検証する。
# ---------------------------------------------------------------------------


def test_fix20_intervention_take_count_fields_constant() -> None:
    assert m.INTERVENTION_TAKE_COUNT_FIELDS == (
        "c0_replay_takes_per_founder",
        "c1_sham_takes_per_founder",
    )


def test_fix20_current_contract_take_count_fields_are_pinned_twenty(
    contract_raw: Dict[str, Any],
) -> None:
    for name in m.INTERVENTION_TAKE_COUNT_FIELDS:
        field = contract_raw["interventions"][name]
        assert field["status"] == "PINNED"
        assert field["value"] == 20


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix20_take_count_field_missing_rejected(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """欠落: `interventions` の必須キー閉集合検査（allowed/missing）で
    fail-closed 拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    del tampered["interventions"][field_name]
    with pytest.raises(m.Run9ValidationError, match="interventions missing key"):
        m.load_run9_contract(tampered)


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
@pytest.mark.parametrize("bad_value", [0, -1, -20, 1.5, 20.0, True, False])
def test_fix20_take_count_field_pinned_bad_value_rejected(
    contract_raw: Dict[str, Any], field_name: str, bad_value: Any
) -> None:
    """負例（0・負値・float・bool）: `_require_positive_int()` は
    `_is_strict_int()`（bool 除外）+ 正値要求のため、いずれも拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"][field_name] = {"value": bad_value, "status": "PINNED"}
    with pytest.raises(m.Run9ValidationError, match=f"interventions.{field_name}.value"):
        m.load_run9_contract(tampered)


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix20_take_count_field_pinned_null_value_rejected(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """欠落級の負例: PINNED を名乗りながら value が null な欄は、他の pin
    欄と同型の `_validate_pin_field()` null チェックで拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"][field_name] = {"value": None, "status": "PINNED"}
    with pytest.raises(m.Run9ValidationError, match="status is PINNED but value is null"):
        m.load_run9_contract(tampered)


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix20_take_count_field_pinned_positive_int_accepted(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """正例: 正の厳密 int（bool でない）は PINNED として受理される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"][field_name] = {"value": 42, "status": "PINNED"}
    m.load_run9_contract(tampered)  # raises on failure


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix20_take_count_field_pending_with_null_value_accepted(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """正例: PENDING/BLOCKED は他の pin 欄と同様、value=null のままでも
    正直な未 pin 表現として許容される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["interventions"][field_name] = {
        "value": None,
        "status": "PENDING",
        "reason": "not yet decided",
    }
    m.load_run9_contract(tampered)  # raises on failure


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第7巡 Fix 20（P1, つづき）: identity_metric_
# space.json validator 側 — pooling 禁止文言・per-founder フィールド名
# 参照の欠落を fail-closed で拒否する。
# ---------------------------------------------------------------------------


def test_fix20_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix20_validator_rejects_d_c0_population_missing_pooling_prohibition() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["freeze_threshold"]["d_c0_population"] = (
        "founder F 自身の C0 テイクと reference_render(F) の距離標本。テイク数は "
        "RUN9_CONTRACT.yaml の interventions.c0_replay_takes_per_founder を参照する。"
    )
    with pytest.raises(m.Run9ValidationError, match="founder-pooling prohibition"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix20_validator_rejects_d_c0_population_missing_field_ref() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["freeze_threshold"]["d_c0_population"] = (
        "founder F 自身の C0 テイクと reference_render(F) の距離標本のみ（founder 横断の "
        "pooling は不採用）。テイク数は RUN9_CONTRACT.yaml の interventions 規定に従う。"
    )
    with pytest.raises(m.Run9ValidationError, match="reference RUN9_CONTRACT.yaml"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix20_validator_rejects_d_c1_population_missing_field_ref() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"] = (
        "founder F 自身の C1 テイクと reference_render(F) の距離標本のみ（pooling 禁止）。"
        "テイク数は RUN9_CONTRACT.yaml の interventions 規定に従う。"
    )
    with pytest.raises(m.Run9ValidationError, match="reference RUN9_CONTRACT.yaml"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix20_validator_rejects_reference_render_definition_missing_per_founder_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["distance_unit"]["reference_render_definition"] = (
        "reference_example 節が固定する first birth-probe measurement の実測値。捏造禁止。"
    )
    with pytest.raises(m.Run9ValidationError, match="fixed per founder"):
        m.validate_identity_metric_space_manifest(doc)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第7巡 Fix 21（P2）:
# `validate_identity_metric_space_manifest()` を全トップレベル実行関連
# フィールド（`feature_extractor`/`identity_feature`/`distance`/
# `reference_example`/`feasibility_note`）へ拡張する。旧実装はこれらを
# トップレベルキー集合にのみ含め、null 化・ネストキー欠落・追加がいずれも
# 素通りしていた。
# ---------------------------------------------------------------------------


def test_fix21_validator_rejects_feature_extractor_null() -> None:
    doc = _valid_metric_space_doc()
    doc["feature_extractor"] = None
    with pytest.raises(m.Run9ValidationError, match="feature_extractor must be an object"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_feature_extractor_missing_key() -> None:
    doc = _valid_metric_space_doc()
    del doc["feature_extractor"]["reference_implementation"]
    with pytest.raises(m.Run9ValidationError, match="feature_extractor missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_feature_extractor_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["feature_extractor"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(m.Run9ValidationError, match="feature_extractor has unknown key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_feature_extractor_version_source_missing_key() -> None:
    doc = _valid_metric_space_doc()
    del doc["feature_extractor"]["version_source"]["note"]
    with pytest.raises(
        m.Run9ValidationError, match="feature_extractor.version_source missing required key"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_identity_feature_null() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"] = None
    with pytest.raises(m.Run9ValidationError, match="identity_feature must be an object"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_identity_feature_f0_exclusion_excluded_non_bool() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["f0_exclusion"]["excluded"] = "true"
    with pytest.raises(m.Run9ValidationError, match="f0_exclusion.excluded must be a bool"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_distance_null() -> None:
    doc = _valid_metric_space_doc()
    doc["distance"] = None
    with pytest.raises(m.Run9ValidationError, match="distance must be an object"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_distance_properties_not_a_list() -> None:
    doc = _valid_metric_space_doc()
    doc["distance"]["properties"] = "symmetric,deterministic"
    with pytest.raises(m.Run9ValidationError, match="distance.properties must be a non-empty list"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_reference_example_null() -> None:
    """指摘の中心事例: `feature_extractor`/`reference_example` 等は存在
    チェックのみで null 置換が通っていた欠陥の直接リグレッションガード。"""
    doc = _valid_metric_space_doc()
    doc["reference_example"] = None
    with pytest.raises(m.Run9ValidationError, match="reference_example must be an object"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_reference_example_missing_nested_key() -> None:
    doc = _valid_metric_space_doc()
    del doc["reference_example"]["procedure"]
    with pytest.raises(m.Run9ValidationError, match="reference_example missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_reference_example_unknown_nested_key() -> None:
    doc = _valid_metric_space_doc()
    doc["reference_example"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(m.Run9ValidationError, match="reference_example has unknown key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_reference_example_value_present_while_pending() -> None:
    """捏造禁止の裏側: 手続きのみの status のまま value を非 null にする
    （実測前の値のでっち上げ）ことも拒否する。第12巡 Fix 29 で
    reference_example.value は「常に null が正」の恒久ルールへ書き換わった
    （旧 PENDING_BIRTH_PROBE の非対称ルールを反転 — 詳細は Fix 29 の
    テスト群を参照）ため、本テストのエラー文言もそれに追随する。"""
    doc = _valid_metric_space_doc()
    doc["reference_example"]["value"] = {"fabricated": True}
    with pytest.raises(m.Run9ValidationError, match="value must remain permanently null"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_reference_example_status_not_procedure_only() -> None:
    """第12巡 Fix 29 による置換: 旧テストは「status が PENDING_BIRTH_PROBE
    を過ぎたら value は null であってはならない」という非対称ルールの片側を
    確認していたが、Fix 29 でこの非対称ルールは反転し（value は常に null が
    正）、status 自体も凍結した唯一の値と厳密一致するよう強化された。
    status を別の値（例: 旧 "MEASURED"）へ変えると、value の状態に関わらず
    status 不一致として拒否されることを確認する。"""
    doc = _valid_metric_space_doc()
    doc["reference_example"]["status"] = "MEASURED"
    with pytest.raises(m.Run9ValidationError, match="status must be exactly"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix21_validator_rejects_feasibility_note_empty() -> None:
    doc = _valid_metric_space_doc()
    doc["feasibility_note"] = "   "
    with pytest.raises(m.Run9ValidationError, match="feasibility_note must be a non-empty string"):
        m.validate_identity_metric_space_manifest(doc)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第8巡 Fix 22（P1）: gate_state() の READY 判定へ
# calibration take pin（`interventions.{c0_replay,c1_sham}_takes_per_founder`、
# Fix 20 新設）を組み込む。旧実装は `pre_run_fields` を `CONTRACT_PIN_FIELDS`
# （トップレベル欄のみ）からしか導出しておらず、両ネスト欄が PENDING でも
# 他のトップレベル欄が全 PINNED なら READY を返してしまっていた ——
# C0/C1 校正母集団サイズが未凍結のまま学習が開始でき、事前登録 P95 閾値が
# 無効化される穴だった。
# ---------------------------------------------------------------------------


def test_fix22_fully_pinned_synthetic_contract_with_take_counts_pinned_is_ready(
    contract_raw: Dict[str, Any],
) -> None:
    """回帰①: 現行 RUN9_CONTRACT.yaml の interventions 配下は既に両欄
    PINNED=20（Fix 20）。他のトップレベル pre-run 欄も合成 PINNED にした
    fully-pinned fixture は Fix 22 適用後も従来どおり READY を返す
    （新チェックが既存の正常系まで巻き込んで壊していないことの確認）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    for name in m.INTERVENTION_TAKE_COUNT_FIELDS:
        assert fully_pinned["interventions"][name]["status"] == "PINNED"
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "READY"


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix22_pending_take_count_field_blocks_gate(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """負例②: `interventions.{c0_replay,c1_sham}_takes_per_founder` の
    どちらか一方だけを PENDING 化すると、他の全欄が PINNED でも
    gate_state() は READY にならず BLOCKED を返す（校正母集団サイズが
    未凍結のまま学習開始できてしまう穴の直接リグレッションガード）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    fully_pinned["interventions"][field_name] = {
        "value": None,
        "status": "PENDING",
        "reason": "regressed for fix22 test",
    }
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "BLOCKED"


def test_fix22_both_take_count_fields_pending_blocks_gate(
    contract_raw: Dict[str, Any],
) -> None:
    """負例②補足: 両欄同時 PENDING でも同様に BLOCKED。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    for name in m.INTERVENTION_TAKE_COUNT_FIELDS:
        fully_pinned["interventions"][name] = {
            "value": None,
            "status": "PENDING",
            "reason": "regressed for fix22 test",
        }
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "BLOCKED"


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix22_take_count_field_missing_status_key_fails_closed_via_gate(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """負例③: gate_state() の再検証時（Fix 4 と同じ「load 後に
    contract.raw を直接改変する」パターン）に status キーが欠落した入れ子
    pin を混入させても、READY を騙れず Run9ValidationError で
    fail-closed する。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    contract = m.load_run9_contract(fully_pinned)
    del contract.raw["interventions"][field_name]["status"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.gate_state(contract)


@pytest.mark.parametrize("field_name", list(m.INTERVENTION_TAKE_COUNT_FIELDS))
def test_fix22_take_count_field_invalid_status_fails_closed_via_gate(
    contract_raw: Dict[str, Any], field_name: str
) -> None:
    """負例③補足: status に語彙外の値（"READY" という偽装値）を直接
    混入させても、gate_state() の再検証で Run9ValidationError が飛ぶ
    （PINNED を騙る経路が status 語彙検査でも閉じていることの確認）。"""
    fully_pinned = _fully_pinned_synthetic_contract(contract_raw)
    contract = m.load_run9_contract(fully_pinned)
    contract.raw["interventions"][field_name]["status"] = "READY"
    with pytest.raises(m.Run9ValidationError, match="status must be one of"):
        m.gate_state(contract)


def test_fix22_intervention_take_count_field_accessor() -> None:
    """`Run9RunContract.intervention_take_count_field()` が
    `pin_field()`/`founder_genome_sha()` と同じアクセサ規約で
    `interventions` 配下の入れ子 pin 欄を返すことの直接確認。"""
    contract = m.load_run9_contract_from_yaml_path(CONTRACT_PATH)
    for name in m.INTERVENTION_TAKE_COUNT_FIELDS:
        assert (
            contract.intervention_take_count_field(name)
            == contract.raw["interventions"][name]
        )


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第9巡 Fix 23（P1）: reference テイクの C0
# 母集団からの除外を凍結する。reference_render(F) が C0/C1 replay テイクの
# 一員なら D_C0(F)/D_C1(F) に自己比較ゼロ距離が保証混入し、P95 閾値と
# STABLE/SHIFTED 判定が変わり得た未確定点を、d_c0_population/
# d_c1_population/reference_render_definition の文言凍結 + validator
# チェックとして機械強制する。
# ---------------------------------------------------------------------------


def test_fix23_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix23_d_c0_population_states_self_comparison_prohibition() -> None:
    data = _valid_metric_space_doc()
    d_c0_population = data["calibration"]["freeze_threshold"]["d_c0_population"]
    assert "自己比較" in d_c0_population
    assert "C0 母集団に属さない" in d_c0_population


def test_fix23_d_c1_population_states_self_comparison_prohibition() -> None:
    data = _valid_metric_space_doc()
    d_c1_population = data["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"]
    assert "自己比較" in d_c1_population
    assert "C1 母集団にも属さない" in d_c1_population


def test_fix23_reference_render_definition_states_not_a_take_member() -> None:
    data = _valid_metric_space_doc()
    reference_render_definition = data["calibration"]["distance_unit"]["reference_render_definition"]
    assert "C0/C1 テイクの一員ではなく" in reference_render_definition
    assert "独立レンダー" in reference_render_definition


def test_fix23_worked_example_disclaimer_states_samples_are_independent_of_reference() -> None:
    data = _valid_metric_space_doc()
    disclaimer = data["calibration"]["worked_example"]["disclaimer"]
    assert "reference_render(F) と独立なテイク" in disclaimer
    assert "自己比較" in disclaimer


def test_fix23_validator_rejects_d_c0_population_missing_self_comparison_prohibition() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["freeze_threshold"]["d_c0_population"] = (
        "D_C0(F) = founder F 自身の C0 テイクと reference_render(F) との距離標本のみで構成する。"
        "founder 横断の pooling は不採用。テイク数は RUN9_CONTRACT.yaml の "
        "interventions.c0_replay_takes_per_founder を参照する。"
    )
    with pytest.raises(m.Run9ValidationError, match="self-comparison contamination prohibition"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix23_validator_rejects_d_c1_population_missing_self_comparison_prohibition() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"] = (
        "D_C1(F) = founder F 自身の C1 テイクと reference_render(F) との距離標本のみで構成する"
        "（pooling 不採用）。テイク数は RUN9_CONTRACT.yaml の "
        "interventions.c1_sham_takes_per_founder を参照する。"
    )
    with pytest.raises(m.Run9ValidationError, match="self-comparison contamination prohibition"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix23_validator_rejects_reference_render_definition_missing_not_a_take_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["distance_unit"]["reference_render_definition"] = (
        "当該 founder F 自身の first birth-probe measurement に固定する（founder ごとに1つ）。"
        "実測値は reference_example 節が固定する手続きに従う。捏造禁止。"
    )
    with pytest.raises(m.Run9ValidationError, match="not a member of the C0/C1 takes"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix23_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 23 の文言追記後、`metric_space_sha` は
    `identity_metric_space.json` の正規形 sha256 を再計算した値と一致する
    （= repin 済みであり、追記前の pin 値のまま素通りしていない）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第9巡 Fix 24（P2）: `_validate_nested_str_keys()`
# を「必須キー存在」から「キー集合完全一致（未知キー拒否）」へ強化する。
# `f0_estimation.algorithm_override: "dio"` のような契約に無いキーの追加が
# repin だけで素通りしていた穴の是正 — 本関数を呼ぶ全ネスト object へ
# 一括適用されることを個別に確認する。
# ---------------------------------------------------------------------------


def test_fix24_validator_accepts_current_identity_metric_space_file() -> None:
    """正例（回帰確認）: 現行ファイルは追加のキーを一切含まないため、
    キー集合完全一致チェックを追加してもそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix24_validator_rejects_f0_estimation_algorithm_override() -> None:
    """指摘の直接事例: `f0_estimation.algorithm_override: "dio"` のような
    矛盾キーの追加を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["f0_estimation"]["algorithm_override"] = "dio"
    with pytest.raises(
        m.Run9ValidationError,
        match=r"f0_estimation has unknown key.*algorithm_override",
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_spectral_envelope_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["spectral_envelope"]["q1"] = 0.5
    with pytest.raises(m.Run9ValidationError, match=r"spectral_envelope has unknown key.*q1"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_voiced_mask_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["voiced_mask"]["threshold"] = "0.0"
    with pytest.raises(m.Run9ValidationError, match=r"voiced_mask has unknown key.*threshold"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_distance_unit_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["distance_unit"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"distance_unit has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_freeze_threshold_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["freeze_threshold"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"freeze_threshold has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_decision_rule_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["decision_rule"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"decision_rule has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_rejects_source_references_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["source_references"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"source_references has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_still_rejects_sample_rate_unknown_key_after_allowed_keys_refactor() -> None:
    """回帰ガード: `sample_rate`/`log_transform` は `value_hz`/`floor_value`
    という非 str 型フィールドを含む部分集合を `_validate_nested_str_keys()`
    へ渡すため、`allowed_keys` 経由の閉集合検証が壊れていないかを個別に
    確認する（壊れていると `value_hz` 自体を未知キーと誤検知していた —
    実装中に一度この regression を踏んだ)。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"sample_rate has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix24_validator_still_rejects_log_transform_unknown_key_after_allowed_keys_refactor() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["log_transform"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"log_transform has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第10巡 Fix 25（P1）: identity feature のゲイン
# 不変化を凍結する。WORLD の sp はパワー領域でレンダーゲインに比例スケール
# するため、raw log 包絡は全 bin に約定数のオフセットが乗り、稽古/教育が
# dynamics/全体ゲインだけ変えても Euclidean 距離が閾値超過し得た
# （Technique の dynamics 軸と Identity の混同）。identity_feature に
# level_normalization 節（集約後ベクトルへの1回のスカラー平均減算）を新設
# し、validator が gain invariance の理由 + 凍結した式を機械強制する。
# ---------------------------------------------------------------------------


def test_fix25_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix25_metric_version_bumped_to_0_3() -> None:
    """feature 定義の意味変更（level 正規化の導入）に伴い metric_version が
    0.2 から 0.3 へ bump されたことの歴史的事実を確認する（現行値は第13巡
    Fix 32 の校正規則変更でさらに 0.4 へ進んでいるため、ここでは 0.2 への
    逆行がないことのみを confirm し、現行の正確な値は
    `test_fix32_metric_version_bumped_to_0_4` が担う）。"""
    data = _valid_metric_space_doc()
    assert data["metric_version"] != "run9-identity-metric/0.2"


def test_fix25_identity_feature_definition_states_gain_invariance() -> None:
    data = _valid_metric_space_doc()
    definition = data["identity_feature"]["definition"]
    assert "mean(v(x))" in definition
    assert "レンダーゲイン" in definition or "ゲイン" in definition


def test_fix25_level_normalization_formula_is_scalar_mean_subtraction() -> None:
    data = _valid_metric_space_doc()
    level_normalization = data["identity_feature"]["level_normalization"]
    assert level_normalization["formula"] == "feature(x) = v(x) - mean(v(x))・1"


def test_fix25_level_normalization_rationale_explains_gain_invariance_mechanism() -> None:
    data = _valid_metric_space_doc()
    rationale = data["identity_feature"]["level_normalization"]["rationale"]
    assert "ゲイン" in rationale
    assert "log" in rationale.lower()


def test_fix25_level_normalization_method_justifies_aggregate_once_choice() -> None:
    """per-frame 正規化ではなく集約後1回の減算を選んだ理由が明記されている
    ことを確認する（凍結裁定の一部）。"""
    data = _valid_metric_space_doc()
    method = data["identity_feature"]["level_normalization"]["method"]
    assert "per-frame" in method
    assert "集約後" in method


def test_fix25_worked_example_disclaimer_notes_normalized_distances() -> None:
    data = _valid_metric_space_doc()
    disclaimer = data["calibration"]["worked_example"]["disclaimer"]
    assert "level_normalization" in disclaimer
    assert "gain-invariant" in disclaimer


def test_fix25_validator_rejects_identity_feature_missing_level_normalization() -> None:
    doc = _valid_metric_space_doc()
    del doc["identity_feature"]["level_normalization"]
    with pytest.raises(m.Run9ValidationError, match="identity_feature missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix25_validator_rejects_level_normalization_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["level_normalization"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"level_normalization has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix25_validator_rejects_rationale_missing_gain_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["level_normalization"]["rationale"] = (
        "log スペクトル包絡の平均を差し引くことで一定のオフセットを除去する。"
    )
    with pytest.raises(m.Run9ValidationError, match="render-gain invariance"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix25_validator_rejects_formula_replaced_by_per_frame_normalization() -> None:
    """凍結した式（集約後1回のスカラー平均減算）以外への無断置換
    （例: per-frame 正規化への repin）を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["level_normalization"]["formula"] = (
        "feature(x, t) = v(x, t) - mean_bin(v(x, t))・1  # per-frame"
    )
    with pytest.raises(m.Run9ValidationError, match="frozen scalar-mean-subtraction"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix25_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 25/Fix 27 の文言改訂後、`metric_space_sha` は
    `identity_metric_space.json` の正規形 sha256 を再計算した値と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第10巡 Fix 27（P1）: positive/negative reference
# の生成・選定手続きを凍結する。「同一 founder の再レンダー」では枝・
# revision・制御条件・テイク・生成タイミングが未指定で、neutral C0 レンダー
# を使う評価者と学習後レンダーを使う評価者で gate が反転し得た。
# ---------------------------------------------------------------------------


def test_fix27_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix27_positive_reference_definition_is_dedicated_independent_take() -> None:
    data = _valid_metric_space_doc()
    positive_reference_definition = data["calibration"]["validity_gates"]["positive_reference_gate"][
        "positive_reference_definition"
    ]
    assert "専用" in positive_reference_definition
    assert "reference_render(F) 自身ではなく" in positive_reference_definition
    assert "いずれでもない" in positive_reference_definition


def test_fix27_positive_reference_definition_pins_birth_probe_timing_and_forbids_post_learning() -> None:
    data = _valid_metric_space_doc()
    positive_reference_definition = data["calibration"]["validity_gates"]["positive_reference_gate"][
        "positive_reference_definition"
    ]
    assert "birth probe" in positive_reference_definition
    assert "学習後レンダーの使用は明示禁止" in positive_reference_definition


def test_fix27_negative_reference_definition_pins_birth_probe_timing() -> None:
    data = _valid_metric_space_doc()
    negative_reference_definition = data["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ]
    assert "birth probe" in negative_reference_definition


def test_fix27_validator_rejects_positive_reference_definition_reproduction_case() -> None:
    """指摘の再現例そのもの: 旧文言「同一 founder F 自身の再レンダー。」は
    5マーカーのいずれも含まない。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["positive_reference_gate"][
        "positive_reference_definition"
    ] = "同一 founder F 自身の再レンダー。"
    with pytest.raises(m.Run9ValidationError, match="positive_reference_definition"):
        m.validate_identity_metric_space_manifest(doc)


_POSITIVE_REFERENCE_MARKER_PHRASES: Dict[str, str] = {
    "専用": "専用の追加レンダー",
    "reference_render(F) 自身ではなく": "reference_render(F) 自身ではなく",
    "いずれでもない": "C0 母集団のいずれでもない",
    "birth probe": "birth probe 時に生成する",
    "学習後レンダーの使用は明示禁止": "学習後レンダーの使用は明示禁止",
}


@pytest.mark.parametrize("missing_marker", list(_POSITIVE_REFERENCE_MARKER_PHRASES))
def test_fix27_validator_rejects_positive_reference_definition_missing_one_marker(
    missing_marker: str,
) -> None:
    """5マーカーのうち1つだけを個別に欠落させ、他4件は残した文言でも拒否
    されることを確認する（各マーカーが独立に enforce されていることの
    確認 — 全マーカー一括欠落だけでは他マーカーのチェックがショート
    サーキットで通過していないかを見分けられない）。"""
    doc = _valid_metric_space_doc()
    included = [
        phrase
        for marker, phrase in _POSITIVE_REFERENCE_MARKER_PHRASES.items()
        if marker != missing_marker
    ]
    doc["calibration"]["validity_gates"]["positive_reference_gate"]["positive_reference_definition"] = (
        "positive_reference(F) = " + "。".join(included) + "。"
    )
    with pytest.raises(m.Run9ValidationError, match="positive_reference_definition"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix27_validator_rejects_negative_reference_definition_missing_timing() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ] = (
        "他方 founder のレンダー（PJS は teacher であり RUN9 Identity anchor 空間から構造的に"
        "排除済み — negative reference としてのみ利用する）。"
    )
    with pytest.raises(m.Run9ValidationError, match="generation timing"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix27_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認（Fix 25 と共通の repin — 同ラウンドで両方改訂）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第11巡 Fix 28（P1）: 校正距離を
# identity_feature.level_normalization が定義する正規化 feature 基準へ統一
# する。旧 calibration.distance_unit.formula は feature 定義がゲイン不変
# （Fix 25）になった後も raw な mean_voiced_log_sp ベクトルへ直接 Euclidean
# を適用しており、ハーネスが pin どおりに計算すると dynamics のみのゲイン
# 変化が再び STABLE/SHIFTED を反転させ得た（metric_version 0.3 のゲイン
# 不変の主張と矛盾）。
# ---------------------------------------------------------------------------


def test_fix28_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix28_distance_unit_formula_uses_normalized_feature_call() -> None:
    data = _valid_metric_space_doc()
    formula = data["calibration"]["distance_unit"]["formula"]
    assert "feature(reference_render(F))" in formula
    assert "feature(take_i(F))" in formula
    assert "mean_voiced_log_sp" not in formula


def test_fix28_distance_unit_formula_references_level_normalization_definition() -> None:
    """定義の重複記載ではなく参照で束縛されていることを確認する。"""
    data = _valid_metric_space_doc()
    formula = data["calibration"]["distance_unit"]["formula"]
    assert "level_normalization" in formula


def test_fix28_calibration_section_has_no_remaining_raw_vector_reference() -> None:
    """calibration 節全体を通じて raw な mean_voiced_log_sp への直接参照が
    残っていないことを確認する（指摘②: 他箇所への残存確認）。"""
    data = _valid_metric_space_doc()
    calibration_text = json.dumps(data["calibration"], ensure_ascii=False)
    assert "mean_voiced_log_sp" not in calibration_text


def test_fix28_worked_example_disclaimer_already_notes_normalized_distances() -> None:
    """worked_example は Fix 25 時点で既に『距離はすべて正規化 feature 間の
    Euclidean』相当の注記を保持しているため、Fix 28 では追加改訂不要
    （指摘②の worked_example 免除条件を満たすことの確認）。"""
    data = _valid_metric_space_doc()
    disclaimer = data["calibration"]["worked_example"]["disclaimer"]
    assert "level_normalization" in disclaimer
    assert "raw ベクトル距離ではない" in disclaimer


def test_fix28_validator_rejects_formula_regression_to_raw_vector_distance() -> None:
    """指摘の再現例そのもの: raw mean_voiced_log_sp ベクトルへの直接
    Euclidean への逆行を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["distance_unit"]["formula"] = (
        "d_i(F) = euclidean(mean_voiced_log_sp(reference_render(F)), "
        "mean_voiced_log_sp(take_i(F)))"
    )
    with pytest.raises(m.Run9ValidationError, match="normalized feature"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix28_validator_rejects_formula_missing_level_normalization_reference() -> None:
    """feature(...) 呼び出し形式であっても、level_normalization への参照が
    欠落した文言（定義を暗黙のうちに別のものへすり替え得る）は拒否する。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["distance_unit"]["formula"] = (
        "d_i(F) = euclidean(feature(reference_render(F)), feature(take_i(F)))"
    )
    with pytest.raises(m.Run9ValidationError, match="level_normalization"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix28_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 28 の formula 改訂後、`metric_space_sha` は
    `identity_metric_space.json` の正規形 sha256 を再計算した値と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix28_domain_metric_space_sha_differs_from_pre_fix28_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 28 の formula 改訂により
    metric_space_sha が Fix 27 時点の旧値から更新されていることを確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_fix28_sha = "196de131cbe50ed71e93254ae89175be7c92f878e720dfc3a28b916b2ff2ef62"
    assert domain_raw["metric_space_sha"] != old_pre_fix28_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第12巡 Fix 29（P1）: 実測 reference の循環
# provenance 解消。reference_example は procedure-only の恒久 pin へ改訂し、
# 実測値の記録先を出生後アーティファクト（RUN9_CONTRACT.yaml の post-run
# pin `artifact_manifest_sha` 配下）へ切り出す。旧 status
# PENDING_BIRTH_PROBE の非対称ルール（PENDING 中のみ value null 許容）は
# 反転し、新 status では value は常に null が正・null 以外は拒否する。
# ---------------------------------------------------------------------------


def test_fix29_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix29_reference_example_status_is_procedure_only_literal() -> None:
    data = _valid_metric_space_doc()
    assert (
        data["reference_example"]["status"]
        == "PROCEDURE_ONLY_VALUE_RECORDED_IN_POST_BIRTH_ARTIFACT"
    )
    assert data["reference_example"]["status"] != "PENDING_BIRTH_PROBE"


def test_fix29_reference_example_value_is_permanently_null() -> None:
    data = _valid_metric_space_doc()
    assert data["reference_example"]["value"] is None


def test_fix29_reference_example_procedure_states_circular_provenance_mechanism() -> None:
    """循環 provenance の機序（metric_space_sha → content_digest() →
    genome_id）が procedure 文言に明記されていることを確認する。"""
    data = _valid_metric_space_doc()
    procedure = data["reference_example"]["procedure"]
    assert "metric_space_sha" in procedure
    assert "content_digest" in procedure
    assert "genome_id" in procedure
    assert "循環" in procedure


def test_fix29_reference_example_procedure_states_post_birth_artifact_destination() -> None:
    """実測値の記録先が出生後アーティファクト（RUN9_CONTRACT.yaml の
    post-run pin artifact_manifest_sha 配下）であることが明記されている
    ことを確認する。"""
    data = _valid_metric_space_doc()
    procedure = data["reference_example"]["procedure"]
    assert "artifact_manifest_sha" in procedure
    assert "post-birth artifact" in procedure or "出生後アーティファクト" in procedure


def test_fix29_reference_example_procedure_states_never_written_back() -> None:
    data = _valid_metric_space_doc()
    procedure = data["reference_example"]["procedure"]
    assert "書き込まない" in procedure or "一切書き込まない" in procedure


def test_fix29_distance_unit_reference_render_definition_no_longer_promises_write_back() -> None:
    """calibration.distance_unit.reference_render_definition も、実測値を
    この manifest へ記録する旨の旧文言（書き戻しを前提とした表現）を残して
    いないことを確認する。"""
    data = _valid_metric_space_doc()
    reference_render_definition = data["calibration"]["distance_unit"][
        "reference_render_definition"
    ]
    assert "procedure-only" in reference_render_definition or (
        "書き込まない" in reference_render_definition
    )


def test_fix29_validator_rejects_reference_example_status_old_pending_literal() -> None:
    """指摘の直接再現例: 旧 status PENDING_BIRTH_PROBE への逆行を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["reference_example"]["status"] = "PENDING_BIRTH_PROBE"
    with pytest.raises(m.Run9ValidationError, match="status must be exactly"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix29_validator_rejects_reference_example_value_non_null() -> None:
    doc = _valid_metric_space_doc()
    doc["reference_example"]["value"] = {"mean_bin_0": 0.021}
    with pytest.raises(m.Run9ValidationError, match="value must remain permanently null"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix29_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 29/30/31 の文言改訂後、`metric_space_sha` は
    `identity_metric_space.json` の正規形 sha256 を再計算した値と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第12巡 Fix 30（P1）: negative reference の
# 単一ソース化。negative_reference_definition は「他方 founder の
# reference_render のみ」と定めつつ、同節内で PJS を negative reference と
# して使う旨の矛盾節が残存していた（PJS は構造的に Identity anchor 空間から
# 排除済みのはずなのに、直後で negative reference としての利用を肯定する
# 自己矛盾）。旧 validator は birth probe マーカーしか見ておらず、この
# 内容矛盾を素通りさせていた。
# ---------------------------------------------------------------------------


def test_fix30_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix30_negative_reference_definition_states_pjs_non_use() -> None:
    data = _valid_metric_space_doc()
    negative_reference_definition = data["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ]
    assert "PJS" in negative_reference_definition
    assert "negative reference としても使用しない" in negative_reference_definition


def test_fix30_negative_reference_definition_no_longer_contains_contradictory_phrase() -> None:
    data = _valid_metric_space_doc()
    negative_reference_definition = data["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ]
    assert "negative reference としてのみ利用する" not in negative_reference_definition


def test_fix30_negative_reference_definition_single_sources_from_other_founder_reference_render() -> None:
    data = _valid_metric_space_doc()
    negative_reference_definition = data["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ]
    assert "他方 founder" in negative_reference_definition
    assert "reference_render" in negative_reference_definition


def test_fix30_validator_rejects_negative_reference_definition_reintroducing_contradictory_phrase() -> (
    None
):
    """指摘の直接再現例: 「構造的に排除済み」と述べつつ直後で「negative
    reference としてのみ利用する」と矛盾させる旧文言への逆行を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ] = (
        "negative_reference(F) = 他方 founder F' の reference_render(F')（birth probe 時に生成）。"
        "PJS は teacher であり RUN9 Identity anchor 空間から構造的に排除済み — negative reference "
        "としても使用しない。ただし旧文言は negative reference としてのみ利用する、とも読めた。"
    )
    with pytest.raises(m.Run9ValidationError, match="old contradictory phrase"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix30_validator_rejects_negative_reference_definition_pjs_mention_without_non_use_marker() -> (
    None
):
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ] = (
        "negative_reference(F) = 他方 founder F' の reference_render(F')（birth probe 時に生成）。"
        "PJS is the teacher and is excluded from the anchor space."
    )
    with pytest.raises(m.Run9ValidationError, match="mentions PJS but does not state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix30_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認（Fix 29/31 と共通の repin — 同ラウンドで3件改訂）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第12巡 Fix 31（P1）: identity_feature の
# 定義域（scope）を評価レンダー全体へ拡張する。旧 scope は neutral P0/C0
# レンダー限定だったが、calibration.decision_rule は post-practice/
# post-education レンダーの d(r) を要求しており、厳密実装は feature を
# 計算できず、寛容実装は pinned scope を無視するしかない契約矛盾を抱えて
# いた。「feature の計算可能域」と「校正・参照に使える母集団（neutral 限定）」
# を区別して明文化する。
# ---------------------------------------------------------------------------


def test_fix31_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix31_identity_feature_scope_states_evaluated_renders_extension() -> None:
    data = _valid_metric_space_doc()
    scope = data["identity_feature"]["scope"]
    assert "全ての identity 評価対象レンダー" in scope
    assert "r0" in scope
    assert "r_practice" in scope
    assert "r_taught" in scope


def test_fix31_identity_feature_scope_distinguishes_computable_domain_from_neutral_population() -> None:
    data = _valid_metric_space_doc()
    scope = data["identity_feature"]["scope"]
    assert "計算可能域" in scope
    assert "neutral" in scope
    assert "校正母集団" in scope or "reference" in scope


def test_fix31_identity_feature_definition_no_longer_restricted_to_p0_probe() -> None:
    """指摘の直接事例: definition の冒頭が「P0 中立 identity probe の」に
    固定されたままだと、C0/C1/r_practice/r_taught レンダーへの feature(x)
    適用（calibration.distance_unit.formula が要求する）と矛盾する。"""
    data = _valid_metric_space_doc()
    definition = data["identity_feature"]["definition"]
    assert "P0 中立 identity probe の voiced フレームにおける" not in definition
    assert "mean(v(x))" in definition  # Fix 25 のゲイン不変式は維持される


def test_fix31_validator_rejects_scope_missing_evaluated_renders_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["scope"] = (
        "identity_feature の計算可能域は neutral な r0 校正母集団に限定する。"
    )
    with pytest.raises(m.Run9ValidationError, match="identity_feature.scope must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix31_validator_rejects_scope_missing_neutral_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["scope"] = (
        "identity_feature の計算可能域は全ての identity 評価対象レンダー（r0/r_practice/"
        "r_taught）へ拡張する。校正母集団は限定的なレンダーのみに絞る。"
    )
    with pytest.raises(m.Run9ValidationError, match="identity_feature.scope must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix31_validator_rejects_scope_missing_distinction_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["scope"] = (
        "全ての identity 評価対象レンダー（r0/r_practice/r_taught）の voiced フレームを対象と"
        "する。校正母集団は neutral なレンダーのみに絞る。"
    )
    with pytest.raises(m.Run9ValidationError, match="identity_feature.scope must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix31_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認（Fix 29/30 と共通の repin — 同ラウンドで3件改訂）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix31_domain_metric_space_sha_differs_from_pre_fix29_round_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 29/30/31（第12巡）の3件改訂に
    より metric_space_sha が Fix 28 時点（第11巡）の旧値から更新されている
    ことを確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round12_sha = "b135ca1434c89fc981e994b52794c50675b44a074cdb7e14b68d4de148be93df"
    assert domain_raw["metric_space_sha"] != old_pre_round12_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第13巡 Fix 32（P1）: C1 ゲートの統計的欠陥の
# 是正。C1 のアダプター効果が完全にゼロのとき D_C0(F)/D_C1(F) は同一
# replay-noise 分布からの独立標本であり、経験 P95 同士（尾側 vs 尾側）は
# 交換可能なため、旧ゲート `P95(D_C1(F)) <= theta_cal(F)` はゼロ効果下でも
# 約1/2の確率で偽って不成立となり founder を不当に INVALID 化していた。
# ゲート条件を分布中心（P50）vs 尾側（theta_cal(F)）の比較へ改訂する。
# ---------------------------------------------------------------------------


def test_fix32_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix32_c1_gate_condition_is_median_vs_tail_comparison() -> None:
    data = _valid_metric_space_doc()
    condition = data["calibration"]["validity_gates"]["c1_gate"]["condition"]
    assert condition == "P50(D_C1(F)) <= theta_cal(F)"
    assert "P95(D_C1(F))" not in condition


def test_fix32_worked_example_uses_d_c1_p50_key_not_d_c1_p95() -> None:
    data = _valid_metric_space_doc()
    worked_example = data["calibration"]["worked_example"]
    assert "d_c1_p50" in worked_example
    assert "d_c1_p95" not in worked_example
    assert isinstance(worked_example["d_c1_p50"], (int, float))


def test_fix32_metric_version_bumped_to_0_4() -> None:
    """C1 ゲートの校正規則変更（式は不変だが判定意味論が変わる）に伴い
    metric_version が run9-identity-metric/0.3 から 0.4 へ bump された歴史的
    事実を確認する（現行値は第15巡 Fix 35 の入力正規化導入でさらに 0.5 へ
    進んでいるため、ここでは 0.3 への逆行がないことのみを confirm し、現行の
    正確な値は `test_fix35_metric_version_bumped_to_0_5` が担う）。"""
    data = _valid_metric_space_doc()
    assert data["metric_version"] != "run9-identity-metric/0.3"


def test_fix32_d_c1_population_binds_p50_to_percentile_method_by_reference() -> None:
    data = _valid_metric_space_doc()
    d_c1_population = data["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"]
    assert "percentile_method" in d_c1_population


def test_fix32_d_c1_population_states_zero_effect_exchangeability_rationale() -> None:
    data = _valid_metric_space_doc()
    d_c1_population = data["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"]
    assert "交換可能" in d_c1_population


def test_fix32_validator_rejects_c1_gate_condition_regression_to_old_p95() -> None:
    """指摘の直接再現例: 新条件マーカーは満たしつつ、旧尾側 vs 尾側条件
    （統計的欠陥のあった式）の文言が併存する repin を拒否する。"""
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["condition"] = (
        "P50(D_C1(F)) <= theta_cal(F)（旧 P95(D_C1(F)) <= theta_cal(F) から改訂）"
    )
    with pytest.raises(m.Run9ValidationError, match="must not regress to the old tail-vs-tail"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix32_validator_rejects_c1_gate_condition_missing_p50_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["condition"] = "D_C1(F) <= theta_cal(F)"
    with pytest.raises(m.Run9ValidationError, match="must be the median-vs-tail comparison"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix32_validator_rejects_d_c1_population_missing_percentile_method_reference() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"] = (
        "D_C1(F) = founder F 自身の C1 枝テイクと founder F 自身の reference_render(F) との距離"
        "標本のみで構成する（pooling は不採用、自己比較は禁止する）。P50 は交換可能な旧 P95 の"
        "欠陥を是正する。テイク数は RUN9_CONTRACT.yaml の interventions.c1_sham_takes_per_founder "
        "を参照する。"
    )
    with pytest.raises(m.Run9ValidationError, match="must bind P50's quantile method"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix32_validator_rejects_d_c1_population_missing_exchangeability_rationale() -> None:
    doc = _valid_metric_space_doc()
    doc["calibration"]["validity_gates"]["c1_gate"]["d_c1_population"] = (
        "D_C1(F) = founder F 自身の C1 枝テイクと founder F 自身の reference_render(F) との距離"
        "標本のみで構成する（pooling は不採用、自己比較は禁止する）。P50 は "
        "calibration.freeze_threshold.percentile_method が定める線形補間分位で算出する。テイク数は "
        "RUN9_CONTRACT.yaml の interventions.c1_sham_takes_per_founder を参照する。"
    )
    with pytest.raises(m.Run9ValidationError, match="must state the zero-effect"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix32_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認（Fix 33 と共通の repin — 同ラウンドで2件改訂）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第13巡 Fix 33（P1）: PJS confuser（C3）評価
# 経路の復元。DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §14
# C3「PJS Confuser」は PJS を Identity confuser としてのみ評価へ入れ、
# Founder が PJS へ接近していないことを確認することを要求していたが、第12巡
# Fix 30 の PJS 全面不使用宣言 + founder revision 限定 scope により、この
# 評価経路が消えていた。新設 `confuser_control` 節（role/metric/
# pjs_reference_definition/evaluation）としてこの評価経路を復元する。
# ---------------------------------------------------------------------------


def test_fix33_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix33_schema_bumped_to_1_2() -> None:
    """confuser_control キー追加に伴い schema が run9-identity-metric-space/1.2
    へ minor bump されていることを確認する。"""
    data = _valid_metric_space_doc()
    assert data["schema"] == "run9-identity-metric-space/1.2"
    assert m.SCHEMA_IDENTITY_METRIC_SPACE == "run9-identity-metric-space/1.2"


def test_fix33_confuser_control_section_present_with_required_keys() -> None:
    data = _valid_metric_space_doc()
    assert "confuser_control" in data
    confuser = data["confuser_control"]
    assert set(confuser.keys()) == {"role", "metric", "pjs_reference_definition", "evaluation"}


def test_fix33_confuser_control_role_states_non_use_and_confuser_only_distinction() -> None:
    data = _valid_metric_space_doc()
    role = data["confuser_control"]["role"]
    assert "negative reference としては使用しない" in role
    assert "confuser control としてのみ使用する" in role
    assert "PJS" in role


def test_fix33_confuser_control_metric_binds_to_level_normalization_feature() -> None:
    """独自の距離式を新設せず identity_feature.level_normalization の
    feature(x) を参照していることを確認する（distance_unit の Fix 28 と同型
    の規律）。"""
    data = _valid_metric_space_doc()
    metric = data["confuser_control"]["metric"]
    assert "feature(" in metric
    assert "level_normalization" in metric
    assert "euclidean" in metric.lower()


def test_fix33_confuser_control_pjs_reference_definition_is_procedure_only() -> None:
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "事前登録手続き" in pjs_reference_definition
    assert "artifact_manifest_sha" in pjs_reference_definition


def test_fix33_confuser_control_evaluation_states_no_aggregate_score_and_calibration_independence() -> (
    None
):
    data = _valid_metric_space_doc()
    evaluation = data["confuser_control"]["evaluation"]
    assert "PASS/FAIL" in evaluation
    assert "calibration_status" in evaluation
    assert "独立" in evaluation


def test_fix33_identity_feature_scope_extended_to_confuser_control_pjs_reference() -> None:
    data = _valid_metric_space_doc()
    scope = data["identity_feature"]["scope"]
    assert "confuser_control" in scope
    assert "pjs_reference" in scope


def test_fix33_negative_reference_definition_cross_references_confuser_control() -> None:
    """Fix 30 の non-use 宣言（校正ゲート専用スコープ）と Fix 33 の
    confuser_control（別スコープ）が矛盾しないよう、negative_reference_
    definition 側にも相互参照が追記されていることを確認する。"""
    data = _valid_metric_space_doc()
    negative_reference_definition = data["calibration"]["validity_gates"]["negative_reference_gate"][
        "negative_reference_definition"
    ]
    assert "confuser_control" in negative_reference_definition
    # Fix 30 のマーカーは引き続き健在（Fix 33 が上書きしていないこと）。
    assert "negative reference としても使用しない" in negative_reference_definition
    assert "negative reference としてのみ利用する" not in negative_reference_definition


def test_fix33_validator_rejects_confuser_control_deletion() -> None:
    doc = _valid_metric_space_doc()
    del doc["confuser_control"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_confuser_control_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(m.Run9ValidationError, match="confuser_control has unknown key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_confuser_control_missing_key() -> None:
    doc = _valid_metric_space_doc()
    del doc["confuser_control"]["evaluation"]
    with pytest.raises(m.Run9ValidationError, match="confuser_control missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_role_missing_non_use_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["role"] = (
        "PJS は confuser control としてのみ使用する（本節の距離計算にのみ登場する）。"
    )
    with pytest.raises(m.Run9ValidationError, match="confuser_control.role must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_role_missing_confuser_only_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["role"] = (
        "PJS は negative reference としては使用しない（校正ゲートには登場しない）。"
    )
    with pytest.raises(m.Run9ValidationError, match="confuser_control.role must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_metric_missing_feature_call_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["metric"] = (
        "d_pjs(r) = euclidean(mean_voiced_log_sp(r), mean_voiced_log_sp(pjs_reference))。"
    )
    with pytest.raises(m.Run9ValidationError, match="confuser_control.metric must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_metric_missing_level_normalization_reference() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["metric"] = "d_pjs(r) = euclidean(feature(r), feature(pjs_reference))。"
    with pytest.raises(m.Run9ValidationError, match="confuser_control.metric must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_evaluation_missing_no_aggregate_score_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["evaluation"] = (
        "per-founder で d_pjs(r_learned) と d_pjs(r0) を比較し、系統的減少を evidence として報告"
        "する。本評価は calibration_status(F) から独立である。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="must state that no aggregate score"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_evaluation_missing_calibration_independence_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["evaluation"] = (
        "per-founder で d_pjs(r_learned) と d_pjs(r0) を比較し、系統的減少を evidence として報告"
        "する。総合スコア化・PASS/FAIL 化はしない。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="must state its independence from calibration_status"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_validator_rejects_identity_feature_scope_missing_confuser_control_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["identity_feature"]["scope"] = (
        "identity_feature の計算可能域は全ての identity 評価対象レンダー（r0/r_practice/"
        "r_taught）へ拡張する。校正母集団・reference は neutral な r0 限定のまま。"
    )
    with pytest.raises(m.Run9ValidationError, match="identity_feature.scope must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix33_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認（Fix 32 と共通の repin — 同ラウンドで2件改訂）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix33_domain_metric_space_sha_differs_from_pre_round13_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 32/33（第13巡）の2件改訂により
    metric_space_sha が Fix 31 時点（第12巡）の旧値から更新されていることを
    確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round13_sha = "3c79a742627530b80a43cd6d70e601cd91cc7013c588780bc3f6a0bee8dc0fb3"
    assert domain_raw["metric_space_sha"] != old_pre_round13_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第14巡 Fix 34（P1）: pjs_reference の学習前
# 決定論的凍結。旧 pjs_reference_definition は「事前登録手続きで単一の
# 参照レンダー/特徴を選ぶ」としか言っておらず、テイク index・digest・生成
# 条件・決定論的集約規則を指定していなかった。選定値は post-run の
# artifact_manifest_sha 配下にしか記録されないため、評価者が学習後レンダー
# を観察したあとで有利な PJS テイクを選定でき、d_pjs(r_learned) の減少
# 有無 = no-leakage evidence を汚染し得た。単一テイク選択を全廃し、
# 決定論的コーパス全体集約（辞書順列挙 → 同一抽出手続き適用 → 機械的
# voiced_mask 除外 → 要素ごとの算術平均）へ置換した。
# ---------------------------------------------------------------------------


def test_fix34_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix34_pjs_reference_definition_states_deterministic_corpus_aggregation_markers() -> None:
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "辞書順" in pjs_reference_definition
    assert "算術平均" in pjs_reference_definition
    assert "expanded_corpus_identity_sha256" in pjs_reference_definition
    assert "voiced_mask" in pjs_reference_definition
    assert "事後選択" in pjs_reference_definition
    # procedure-only の恒久 pin であること（Fix 29 と同型の一方向 provenance）
    # を示す既存マーカーは Fix 34 改訂後も健在。
    assert "事前登録手続き" in pjs_reference_definition
    assert "artifact_manifest_sha" in pjs_reference_definition


def test_fix34_pjs_reference_definition_does_not_regress_to_single_take_selection() -> None:
    """現ファイルが旧「単一の参照レンダー」選択方式の文言を含まないこと
    （言い換えでの再導入がないこと）を確認する。"""
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "単一の参照レンダー" not in pjs_reference_definition


def test_fix34_validator_rejects_single_take_selection_regression() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference = PJS 教材コーパスから事前登録手続きで固定する単一の参照レンダー/特徴。"
        "実測値は出生後アーティファクト側（artifact_manifest_sha 配下）に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="must not regress to single-take PJS reference selection"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_validator_rejects_pjs_reference_definition_missing_lexicographic_enumeration_marker() -> (
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は expanded_corpus_identity_sha256 が pin する PJS expanded corpus 内の"
        "全ファイルへ extraction_procedure と identity_feature（voiced_mask 除外込み）を適用し、"
        "feature ベクトルを要素ごとの算術平均で集約する。事後選択は構造的に不可能。事前登録手続き"
        "として artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_validator_rejects_pjs_reference_definition_missing_arithmetic_mean_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は expanded_corpus_identity_sha256 が pin する PJS expanded corpus 内の"
        "全ファイルを相対パスの辞書順で列挙し、extraction_procedure と identity_feature"
        "（voiced_mask 除外込み）を適用したうえで集約する。事後選択は構造的に不可能。事前登録"
        "手続きとして artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_validator_rejects_pjs_reference_definition_missing_corpus_pin_field_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は PJS expanded corpus 内の全ファイルを相対パスの辞書順で列挙し、"
        "extraction_procedure と identity_feature（voiced_mask 除外込み）を適用し、feature "
        "ベクトルを要素ごとの算術平均で集約する。事後選択は構造的に不可能。事前登録手続きとして "
        "artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_validator_rejects_pjs_reference_definition_missing_voiced_mask_exclusion_marker() -> (
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は expanded_corpus_identity_sha256 が pin する PJS expanded corpus 内の"
        "全ファイルを相対パスの辞書順で列挙し、extraction_procedure と identity_feature を"
        "適用したうえで feature ベクトルを要素ごとの算術平均で集約する。事後選択は構造的に"
        "不可能。事前登録手続きとして artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_validator_rejects_pjs_reference_definition_missing_post_hoc_selection_impossible_marker() -> (  # noqa: E501
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は expanded_corpus_identity_sha256 が pin する PJS expanded corpus 内の"
        "全ファイルを相対パスの辞書順で列挙し、extraction_procedure と identity_feature"
        "（voiced_mask 除外込み）を適用し、feature ベクトルを要素ごとの算術平均で集約する。"
        "事前登録手続きとして artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix34_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 34 の pjs_reference_definition 改訂後、
    `metric_space_sha` は正規形 sha256 と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix34_domain_metric_space_sha_differs_from_pre_round14_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 34（第14巡）の改訂により
    metric_space_sha が Fix 32/33 時点（第13巡）の旧値から更新されている
    ことを確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round14_sha = "747113488043b944587eb7de1cf7f175193178e9ba6fbe034c90646b54a1e7d1"
    assert domain_raw["metric_space_sha"] != old_pre_round14_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第15巡 Fix 35（P1）: PJS コーパスの metric
# sample rate への決定論的正規化。corpus_inventory_pjs.json によれば PJS の
# 203 WAV は全て 48000 Hz である一方、旧 extraction_procedure.sample_rate は
# 44100 Hz を pin するのみで再サンプル手続きが存在せず、WORLD をネイティブ
# 適用すればスペクトル bin の対応周波数が食い違い、未凍結の再サンプルなら
# 実装者間で結果が再現不能になる（いずれの経路でも confuser_control の
# d_pjs(r) が壊れる）。着手前調査で引用一次ソース donor_bank.py:190-196
# analyze_donor_world() が内部リサンプルを行わないことを確認したうえで、
# extraction_procedure.sample_rate_normalization（scipy.signal.resample_poly
# による 44100/48000 の既約有理比変換）を新規に決定論的 pin した。
# ---------------------------------------------------------------------------


def test_fix35_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix35_metric_version_bumped_to_0_5() -> None:
    """入力正規化ステップの導入（feature 値を変え得る抽出手続きの意味追加）
    に伴い metric_version が run9-identity-metric/0.5 へ bump されている
    ことを確認する（Fix 25/28/32 と同型の「抽出手続き変更 = metric の意味
    変更」判断）。"""
    data = _valid_metric_space_doc()
    assert data["metric_version"] == "run9-identity-metric/0.5"


def test_fix35_schema_unchanged_at_1_2() -> None:
    """sample_rate_normalization の追加は extraction_procedure 配下に収まる
    キー追加のため schema は run9-identity-metric-space/1.2 のまま据え置き
    であることを確認する。"""
    data = _valid_metric_space_doc()
    assert data["schema"] == "run9-identity-metric-space/1.2"


def test_fix35_extraction_procedure_sample_rate_normalization_present_with_required_keys() -> None:
    data = _valid_metric_space_doc()
    sample_rate_normalization = data["extraction_procedure"]["sample_rate_normalization"]
    assert set(sample_rate_normalization.keys()) == {
        "role", "investigation_finding", "rule", "applies_to", "procedure_only",
    }


def test_fix35_sample_rate_normalization_rule_states_deterministic_ratio() -> None:
    """48kHz 導出例マーカー（Fix 36 で「固定比の rule」から「一般導出式の
    適用例」へ意味づけを更新済み — 具体文字列自体は不変）。"""
    data = _valid_metric_space_doc()
    rule = data["extraction_procedure"]["sample_rate_normalization"]["rule"]
    assert "resample_poly(x, up=147, down=160)" in rule
    assert "147/160" in rule


def test_fix36_sample_rate_normalization_rule_states_general_derivation_formula() -> None:
    """Fix 36: rule が固定比ではなく native rate ごとの一般導出式
    （g = gcd(44100, native_sr) → up=44100//g, down=native_sr//g）を
    述べていることの直接確認。"""
    data = _valid_metric_space_doc()
    rule = data["extraction_procedure"]["sample_rate_normalization"]["rule"]
    assert "gcd(44100, native_sr)" in rule
    assert "up=44100//g" in rule
    assert "down=native_sr//g" in rule


def test_fix35_sample_rate_normalization_applies_to_states_general_rule_not_pjs_specific() -> None:
    data = _valid_metric_space_doc()
    applies_to = data["extraction_procedure"]["sample_rate_normalization"]["applies_to"]
    assert "あらゆる入力" in applies_to
    assert "PJS corpus に限定しない" in applies_to


def test_fix35_sample_rate_normalization_investigation_finding_states_no_fixed_sr_load() -> None:
    """着手前調査の実測結果（参照実装に固定 sr ロードが無い）が記録されて
    いることの直接確認。"""
    data = _valid_metric_space_doc()
    finding = data["extraction_procedure"]["sample_rate_normalization"]["investigation_finding"]
    assert "donor_bank.py" in finding
    assert "analyze_donor_world" in finding


def test_fix35_pjs_reference_definition_cross_references_sample_rate_normalization() -> None:
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "sample_rate_normalization" in pjs_reference_definition


def test_fix35_validator_rejects_sample_rate_normalization_deletion() -> None:
    """旧「変換規則なし」状態への逆行拒否の直接確認。"""
    doc = _valid_metric_space_doc()
    del doc["extraction_procedure"]["sample_rate_normalization"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_sample_rate_normalization_unknown_key() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["extra_unexpected_field"] = "sneaked in"
    with pytest.raises(
        m.Run9ValidationError, match=r"sample_rate_normalization has unknown key.*extra_unexpected_field"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_sample_rate_normalization_missing_key() -> None:
    doc = _valid_metric_space_doc()
    del doc["extraction_procedure"]["sample_rate_normalization"]["procedure_only"]
    with pytest.raises(
        m.Run9ValidationError, match=r"sample_rate_normalization missing required key.*procedure_only"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_rule_missing_ratio_call_marker() -> None:
    """一般導出式は述べているが 48kHz 導出例の呼び出し形が欠落する repin を
    拒否する（Fix 36: 導出例チェックは一般式チェックとは独立の第2段
    チェックであることの確認）。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["rule"] = (
        "native sr が 44100 Hz と異なる入力は、g = gcd(44100, native_sr) として "
        "scipy.signal.resample_poly(x, up=44100//g, down=native_sr//g) を適用し 44100 Hz へ"
        "決定論的に変換する。導出例: native 48000 Hz は既約有理比 147/160 となる。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="must state the worked 48000 Hz derivation example"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_rule_missing_ratio_fraction_marker() -> None:
    """有理比呼び出しの up/down 数値は書かれているが 147/160 表記が欠落する
    repin を拒否する（部分一致だけでは決定論性の主張として不十分）。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["rule"] = (
        "native sr が 44100 Hz と異なる入力は、g = gcd(44100, native_sr) として "
        "scipy.signal.resample_poly(x, up=44100//g, down=native_sr//g) を適用し 44100 Hz へ"
        "決定論的に変換する。導出例: native 48000 Hz は scipy.signal.resample_poly(x, up=147, "
        "down=160) となる。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="must state the worked 48000 Hz derivation example"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix36_validator_rejects_general_formula_missing_gcd_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["rule"] = (
        "native sr が 44100 Hz と異なる入力は scipy.signal.resample_poly(x, up=44100//g, "
        "down=native_sr//g)（g は既約化に使う共通因数）を適用し 44100 Hz へ決定論的に変換する。"
        "導出例: native 48000 Hz は既約有理比 147/160、すなわち "
        "scipy.signal.resample_poly(x, up=147, down=160) となる。"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="must state the general per-native-rate ratio derivation formula",
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix36_validator_rejects_general_formula_missing_up_down_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["rule"] = (
        "native sr が 44100 Hz と異なる入力は g = gcd(44100, native_sr) から導かれる既約有理比で"
        "scipy.signal.resample_poly を適用し 44100 Hz へ決定論的に変換する。導出例: native "
        "48000 Hz は既約有理比 147/160、すなわち scipy.signal.resample_poly(x, up=147, "
        "down=160) となる。"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="must state the general per-native-rate ratio derivation formula",
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix36_validator_rejects_regression_to_pre_fix36_fixed_ratio_only_rule() -> None:
    """負例（Fix 36 の核心要求）: 一般導出式を欠き固定 147/160 比のみを pin
    していた第15巡 Fix 35 時点の rule 文言そのものへの逆行を拒否する —
    47/160 という導出例の値自体は残っていても、native rate ごとの導出式
    （gcd(44100, native_sr) / up=44100//g / down=native_sr//g）が無ければ、
    このルールは『あらゆる native sr ≠ 44100 Hz の入力に適用する一般規則』
    という applies_to の主張と矛盾したまま repin されてしまう。"""
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["rule"] = (
        "native sr が 44100 Hz と異なる入力は、extraction_procedure（f0_estimation 以降）を"
        "適用する前の入力正規化ステップとして、soundfile 直読みで得た native サンプル列 x に "
        "scipy.signal.resample_poly(x, up=147, down=160) を適用し 44100 Hz へ決定論的に変換する"
        "（44100 Hz / 48000 Hz の既約有理比 = gcd(44100,48000)=300 → 147/160。"
        "load_donor_24k_bytes() の up/down 明示指定パターンと同型 — RUN9 側で独自に再実装しない）。"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="must state the general per-native-rate ratio derivation formula",
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_applies_to_missing_general_rule_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["applies_to"] = (
        "PJS corpus に限定しない一般規則。native 44.1kHz の入力は変換不要。"
    )
    with pytest.raises(m.Run9ValidationError, match="sample_rate_normalization.applies_to must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_applies_to_missing_not_pjs_specific_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["extraction_procedure"]["sample_rate_normalization"]["applies_to"] = (
        "native sr ≠ 44100 Hz のあらゆる入力に適用する一般規則。native 44.1kHz の入力は変換不要。"
    )
    with pytest.raises(m.Run9ValidationError, match="sample_rate_normalization.applies_to must state"):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_validator_rejects_pjs_reference_definition_missing_sample_rate_normalization_marker() -> (
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference は expanded_corpus_identity_sha256 が pin する PJS expanded corpus 内の"
        "全ファイルを相対パスの辞書順で列挙し、extraction_procedure と identity_feature"
        "（voiced_mask 除外込み）を適用し、feature ベクトルを要素ごとの算術平均で集約する。"
        "事後選択は構造的に不可能。事前登録手続きとして artifact_manifest_sha 配下に記録する。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="pjs_reference_definition must cross-reference"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix35_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 35 の sample_rate_normalization 追加後、
    `metric_space_sha` は正規形 sha256 と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix35_domain_metric_space_sha_differs_from_pre_round15_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 35（第15巡）の改訂により
    metric_space_sha が Fix 34 時点（第14巡）の旧値から更新されていることを
    確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round15_sha = "7eef67beab1028f205c292a89f84aeab5d042862152284362a0a189d69722283"
    assert domain_raw["metric_space_sha"] != old_pre_round15_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第16巡 Fix 36（P1）: リサンプル比の native rate
# からの一般導出。第15巡 Fix 35 の rule は固定 147/160 比を pin していたが、
# 直後の applies_to が宣言する「native sr ≠ 44100 Hz のあらゆる入力に適用
# する一般規則」と矛盾していた（例: native 24000 Hz の入力に 147/160 を
# 適用すると 22050 Hz へ変換され、WORLD には 44100 Hz として扱われて時間軸・
# 周波数軸と identity 距離が壊れる）。rule を g = gcd(44100, native_sr) から
# up/down を native rate ごとに機械的に導出する一般導出式へ改訂し、147/160
# は native 48000 Hz（PJS の全203 WAV）に対する導出例として位置づけ直した。
# ---------------------------------------------------------------------------


def test_fix36_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix36_metric_version_still_0_5() -> None:
    """内部矛盾（一般規則の宣言と固定比のみの rule）の是正であり feature 値
    の意味を変えないため、metric_version は Fix 35 時点の run9-identity-
    metric/0.5 のまま据え置きであることを確認する。"""
    data = _valid_metric_space_doc()
    assert data["metric_version"] == "run9-identity-metric/0.5"


def test_fix36_schema_still_1_2() -> None:
    """rule 文言の改訂のみで extraction_procedure 配下のキー集合は不変の
    ため schema は run9-identity-metric-space/1.2 のまま据え置き。"""
    data = _valid_metric_space_doc()
    assert data["schema"] == "run9-identity-metric-space/1.2"


def test_fix36_applies_to_still_general_and_not_pjs_specific() -> None:
    """applies_to（Fix 35 で凍結済みの一般規則宣言）は Fix 36 で無改訂の
    まま、rule 側がその宣言に整合する内容へ追いついたことを確認する。"""
    data = _valid_metric_space_doc()
    applies_to = data["extraction_procedure"]["sample_rate_normalization"]["applies_to"]
    assert "あらゆる入力" in applies_to
    assert "PJS corpus に限定しない" in applies_to


def test_fix36_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 36 の rule 一般導出式化後、`metric_space_sha`
    は正規形 sha256 と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix36_domain_metric_space_sha_differs_from_pre_round16_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 36（第16巡）の改訂により
    metric_space_sha が Fix 35 時点（第15巡）の旧値から更新されていることを
    確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round16_sha = "7ff07e874a95bbd6224161566068fdf57526218a7f6a6d193b66e6ebedc7b115"
    assert domain_raw["metric_space_sha"] != old_pre_round16_sha


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #318 第17巡 Fix 37（P1）: PJS reference 集約対象を
# pin 被覆ファイルへ限定。着手前調査（donor_bank_lab.py corpus_identity_
# hash() line 192-227 付近）の実測により、①が引用する corpus_sha256 は
# 各 .lab とその対応 _song.wav のみから決定論的集約された値であり、speech
# 100 WAV・background 3 WAV は被覆外と判明した。旧②列挙規則（コーパス内の
# 音声ファイル全件 = 全203 WAV を対象化）はこの被覆外ファイルを pjs_
# reference の集約対象へ混入させており、未 pin ファイルは corpus_identity_
# hash() を変えずに pjs_reference・no-leakage evidence を汚染し得る欠陥
# だった（identity_metric_space.json 旧143行付近）。集約対象を pin 被覆
# ファイル集合（`_song.wav`）へ限定したことを機械強制する。
# ---------------------------------------------------------------------------


def test_fix37_validator_accepts_current_identity_metric_space_file() -> None:
    """正例: repin 済みの現ファイルが validator をそのまま通る。"""
    m.validate_identity_metric_space_manifest(_valid_metric_space_doc())


def test_fix37_pjs_reference_definition_states_song_wav_scope_marker() -> None:
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "_song.wav" in pjs_reference_definition


def test_fix37_pjs_reference_definition_references_corpus_identity_hash() -> None:
    """被覆定義の一次ソース（donor_bank_lab.py corpus_identity_hash()）が
    フィールド参照で明記されていることの直接確認。"""
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "corpus_identity_hash" in pjs_reference_definition
    assert "donor_bank_lab.py" in pjs_reference_definition


def test_fix37_pjs_reference_definition_states_speech_background_exclusion() -> None:
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "混入禁止" in pjs_reference_definition
    assert "speech" in pjs_reference_definition
    assert "background" in pjs_reference_definition


def test_fix37_pjs_reference_definition_does_not_regress_to_full_corpus_enumeration() -> None:
    """負例（Fix 37 の核心要求）: 旧②「束縛したコーパス内の音声ファイルを
    相対パスの辞書順で全件列挙する」（pin 被覆に関係なく全203 WAV を対象化
    する規則）文言そのものへの逆行を拒否する。"""
    data = _valid_metric_space_doc()
    pjs_reference_definition = data["confuser_control"]["pjs_reference_definition"]
    assert "束縛したコーパス内の音声ファイルを相対パスの辞書順" not in pjs_reference_definition


def test_fix37_validator_rejects_regression_to_pre_fix37_full_corpus_enumeration_rule() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference = 単一テイク選択を全廃し、決定論的コーパス集約で学習前に凍結する。①入力"
        "束縛 — expanded_corpus_identity_sha256 が pin する PJS expanded corpus に束縛する。②"
        "列挙規則 — 束縛したコーパス内の音声ファイルを相対パスの辞書順（bytewise lexicographic）"
        "で全件列挙する（乱数・手動選定・任意順は不採用）。③特徴計算 — extraction_procedure."
        "sample_rate_normalization を適用してから extraction_procedure と identity_feature を"
        "適用する。④除外規則 — voiced_mask に従い除外する。⑤集約 — 要素ごとの算術平均で集約する。"
        "⑥記録 — artifact_manifest_sha 配下へ記録する。事後選択は構造的に不可能。"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="must not regress to enumerating every audio file in the bound corpus",
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix37_validator_rejects_pjs_reference_definition_missing_song_wav_marker() -> None:
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference = 単一テイク選択を全廃し、決定論的コーパス集約で学習前に凍結する。①入力"
        "束縛 — expanded_corpus_identity_sha256 が pin する PJS expanded corpus に束縛する。②"
        "列挙規則 — corpus_identity_hash() が被覆するファイルのみを相対パスの辞書順で列挙する。"
        "speech/background は混入禁止。③特徴計算 — sample_rate_normalization を適用してから"
        "extraction_procedure と identity_feature を適用する。④除外規則 — voiced_mask に従い"
        "除外する。⑤集約 — 要素ごとの算術平均で集約する。⑥記録 — artifact_manifest_sha 配下へ"
        "記録する。事後選択は構造的に不可能。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix37_validator_rejects_pjs_reference_definition_missing_corpus_identity_hash_marker() -> (
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference = 単一テイク選択を全廃し、決定論的コーパス集約で学習前に凍結する。①入力"
        "束縛 — expanded_corpus_identity_sha256 が pin する PJS expanded corpus に束縛する。②"
        "列挙規則 — 既 pin が被覆する `_song.wav` ファイルのみを相対パスの辞書順で列挙する。"
        "speech/background は混入禁止。③特徴計算 — sample_rate_normalization を適用してから"
        "extraction_procedure と identity_feature を適用する。④除外規則 — voiced_mask に従い"
        "除外する。⑤集約 — 要素ごとの算術平均で集約する。⑥記録 — artifact_manifest_sha 配下へ"
        "記録する。事後選択は構造的に不可能。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix37_validator_rejects_pjs_reference_definition_missing_speech_background_exclusion_marker() -> (  # noqa: E501
    None
):
    doc = _valid_metric_space_doc()
    doc["confuser_control"]["pjs_reference_definition"] = (
        "pjs_reference = 単一テイク選択を全廃し、決定論的コーパス集約で学習前に凍結する。①入力"
        "束縛 — expanded_corpus_identity_sha256 が pin する PJS expanded corpus に束縛する。②"
        "列挙規則 — corpus_identity_hash() が被覆する `_song.wav` ファイルのみを相対パスの辞書順"
        "で列挙する。③特徴計算 — sample_rate_normalization を適用してから extraction_procedure"
        "と identity_feature を適用する。④除外規則 — voiced_mask に従い除外する。⑤集約 — 要素"
        "ごとの算術平均で集約する。⑥記録 — artifact_manifest_sha 配下へ記録する。事後選択は構造"
        "的に不可能。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="confuser_control.pjs_reference_definition must state"
    ):
        m.validate_identity_metric_space_manifest(doc)


def test_fix37_metric_version_still_0_5() -> None:
    """入力集合の是正（pin 整合化）であり feature 式・校正規則は不変のため
    metric_version は Fix 35 時点の run9-identity-metric/0.5 のまま据え置き。"""
    data = _valid_metric_space_doc()
    assert data["metric_version"] == "run9-identity-metric/0.5"


def test_fix37_schema_still_1_2() -> None:
    """キー集合不変（definition 文言の限定のみ）のため schema は
    run9-identity-metric-space/1.2 のまま据え置き。"""
    data = _valid_metric_space_doc()
    assert data["schema"] == "run9-identity-metric-space/1.2"


def test_fix37_domain_metric_space_sha_matches_recomputed_canonical_form() -> None:
    """repin の直接確認: Fix 37 の集約対象限定後、`metric_space_sha` は
    正規形 sha256 と一致する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_fix37_domain_metric_space_sha_differs_from_pre_round17_value() -> None:
    """repin の直接確認（旧値との差分）: Fix 37（第17巡）の改訂により
    metric_space_sha が Fix 36 時点（第16巡）の旧値から更新されていることを
    確認する。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    old_pre_round17_sha = "00264b5641e1b3b3112a9ef06912e2f96a2c449d25ae78adba36fab6613020e9"
    assert domain_raw["metric_space_sha"] != old_pre_round17_sha


# ---------------------------------------------------------------------------
# rev 0.4（DESIGN_RUN9_REVISION_0.4.md、外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモ
# `DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt` の採用 + 2026-08-25 User 追加裁定
# 「確認メモ / RUN9 用語整理」）対応テスト。
# ---------------------------------------------------------------------------

REVISION_0_4_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.4.md"


def test_rev04_external_review_byte_pin() -> None:
    """派生設計変更メモ（DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt）の byte-pin
    テスト——既存 POR pin テスト（`test_revision03_por_adjudication_sha256_pin_matches_actual_file`
    等）と同型: sha256 一致 + 無改変であることの確認。"""
    assert DERIVED_DESIGN_CHANGES_PATH.exists()
    assert _sha256_file(DERIVED_DESIGN_CHANGES_PATH) == (
        "a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091"
    )


def test_rev04_external_review_declares_design_revision_0_2() -> None:
    """派生設計変更メモは自称 'design_revision 0.2' — 番号注記の前提事実の
    直接確認（本文書は書き換えない）。"""
    text = DERIVED_DESIGN_CHANGES_PATH.read_text(encoding="utf-8")
    assert "design_revision 0.2" in text


def test_rev04_doc_exists_and_declares_lineage() -> None:
    """rev 0.4 文書の存在 + 系譜（0.3 → 0.4）の宣言確認。"""
    assert REVISION_0_4_DOC_PATH.exists()
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "0.3 → 0.4" in doc
    assert "a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091" in doc


def test_rev04_doc_sha256_pin_matches_actual_file(contract_raw: Dict[str, Any]) -> None:
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == _sha256_file(REVISION_0_4_DOC_PATH)
    assert field["value"] == m.compute_file_sha256(REVISION_0_4_DOC_PATH)


def test_rev04_doc_records_case_a_and_central_problem_redefinition() -> None:
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "CASE A" in doc
    assert "Identity 非依存の Performance Residual のみを抽出し" in doc


def test_rev04_doc_records_user_ruling_a_and_b() -> None:
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "aとbを承認" in doc
    assert "USER_ATTESTED" in doc


def test_rev04_doc_records_user_terminology_memo_verbatim() -> None:
    """2026-08-25 User 追加裁定「確認メモ / RUN9 用語整理」が rev 0.4 doc
    へ逐語収載されていることの確認（指示1〜6の要旨語を機械的に検査）。"""
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "確認メモ / RUN9 用語整理" in doc
    assert "teacher 語の全面置換はしない" in doc
    assert "Voice 所有者" in doc
    for i in range(1, 7):
        assert f"{i}. " in doc or f"{i}." in doc  # 指示1〜6 の番号付き列挙


def test_rev04_doc_common_performance_lesson_adopted_with_legacy_note() -> None:
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "Common Performance Lesson" in doc
    assert "旧称" in doc
    assert "Common Teacher Transfer" in doc  # 旧名注記として言及される


def test_rev04_frozen_docs_unchanged_after_rev04() -> None:
    """凍結文書（v0.1 / rev 0.2 / rev 0.3 / POR txt / 派生設計変更メモ txt）の
    無改変を sha256 で確認する（git diff とは独立の直接検証）。"""
    assert _sha256_file(DESIGN_DOC_PATH) == (
        "b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e"
    )
    assert _sha256_file(REVISION_0_2_DOC_PATH) == (
        "406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb"
    )
    assert _sha256_file(REVISION_0_3_DOC_PATH) == (
        "b4f05cfbccb484a16a39b736086e989e1c953f295bda66970d491e4db5b94b04"
    )
    assert _sha256_file(POR_ADJUDICATION_PATH) == (
        "56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007"
    )
    assert _sha256_file(DERIVED_DESIGN_CHANGES_PATH) == (
        "a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091"
    )


# --- R9-G1 拡張（変更5） ------------------------------------------------------


def test_rev04_r9_g1_semantic_name_and_legacy_name() -> None:
    assert m.R9_G1_ID == "R9-G1"
    assert m.R9_G1_LEGACY_NAME == "INPUT_FREEZE_AND_RIGHTS"
    assert m.R9_G1_SEMANTIC_NAME == "RIGHTS_AND_PROVENANCE_GATE"


def test_rev04_r9_g1_pass_conditions_frozen_8_items() -> None:
    expected = (
        "VOICE_SOURCE_IDENTIFIED",
        "VOICE_USAGE_TERMS_CONFIRMED",
        "PERFORMANCE_AUTHOR_IDENTIFIED",
        "PERFORMANCE_USAGE_TERMS_CONFIRMED",
        "COMPOSITION_RIGHTS_CONFIRMED",
        "RECORDING_MASTER_RIGHTS_CONFIRMED",
        "TEACHER_SOURCE_VS_VOICE_IDENTITY_SOURCE_DISTINGUISHED",
        "NO_UNKNOWN_RIGHTS_HOLDER",
    )
    assert m.R9_G1_PASS_CONDITIONS == expected
    assert len(m.R9_G1_PASS_CONDITIONS) == 8


def test_rev04_gate_fail_rights_provenance_unresolved_vocab() -> None:
    assert m.GATE_FAIL_RIGHTS_PROVENANCE_UNRESOLVED == "RIGHTS_PROVENANCE_UNRESOLVED"
    # 独立した2層の語彙であることの確認 — FAILURE_CLASSES を置換しない。
    assert m.GATE_FAIL_RIGHTS_PROVENANCE_UNRESOLVED not in m.FAILURE_CLASSES


def test_rev04_r9_g1_pass_conditions_declared_structural_predicate() -> None:
    assert m.r9_g1_pass_conditions_declared(set(m.R9_G1_PASS_CONDITIONS)) is True
    assert m.r9_g1_pass_conditions_declared(set(m.R9_G1_PASS_CONDITIONS[:-1])) is False
    assert m.r9_g1_pass_conditions_declared(list(m.R9_G1_PASS_CONDITIONS)) is True
    with pytest.raises(m.Run9ValidationError):
        m.r9_g1_pass_conditions_declared("not-a-collection")


# --- Performance Residual / Identity 除外語彙（変更3・6） -----------------------


def test_rev04_performance_trait_vocab_frozen_9_items() -> None:
    expected = (
        "relative_F0", "duration_ratio", "onset_offset", "energy_envelope",
        "vibrato", "phrase_dynamics", "attack_behavior", "release_behavior",
        "articulation_timing",
    )
    assert m.PERFORMANCE_RESIDUAL_VOCAB == expected


def test_rev04_identity_excluded_trait_vocab_frozen_7_items() -> None:
    expected = (
        "speaker_embedding", "timbre_identity", "formant_identity",
        "spectral_identity", "voice_genome",
        "source_specific_identity_representation", "identity_vector",
    )
    assert m.IDENTITY_EXCLUDED_TRAIT_VOCAB == expected


def test_rev04_lesson_record_trait_alias_resolution() -> None:
    assert m.resolve_lesson_record_trait_alias("relative_F0") == "relative_F0"
    assert m.resolve_lesson_record_trait_alias("duration") == "duration_ratio"
    assert m.resolve_lesson_record_trait_alias("timing") == "onset_offset"
    assert m.resolve_lesson_record_trait_alias("dynamics") == "energy_envelope"
    assert m.resolve_lesson_record_trait_alias("articulation") == "articulation_timing"
    with pytest.raises(m.Run9ValidationError):
        m.resolve_lesson_record_trait_alias("unknown_trait_xyz")


def _valid_lesson_record() -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_LESSON_RECORD,
        "lesson_id": "LS-R9-PJS-001",
        "performance_source": "PJS",
        "voice_source": "PJS_corpus_ver1.1",
        "performance_author": "<UNRESOLVED_EXTERNAL>",
        "composition_source": "<UNRESOLVED_EXTERNAL>",
        "recording_source": "PJS_corpus_ver1.1",
        "extracted_traits": ["relative_F0", "duration", "timing", "dynamics", "articulation"],
        "explicitly_excluded_identity_traits": list(m.IDENTITY_EXCLUDED_TRAIT_VOCAB),
        "rights_manifest": "inputs/rights_manifest.json",
        "provenance_manifest": "inputs/rights_manifest.json#performance_rights.provenance",
    }


def test_rev04_lesson_record_valid_example_passes() -> None:
    m.validate_lesson_record(_valid_lesson_record())  # 例外を投げないことの確認


def test_rev04_lesson_record_rejects_unknown_trait() -> None:
    record = _valid_lesson_record()
    record["extracted_traits"] = ["not_a_real_trait"]
    with pytest.raises(m.Run9ValidationError, match="unknown Performance Residual"):
        m.validate_lesson_record(record)


def test_rev04_lesson_record_rejects_incomplete_identity_exclusion() -> None:
    record = _valid_lesson_record()
    record["explicitly_excluded_identity_traits"] = ["speaker_embedding"]
    with pytest.raises(m.Run9ValidationError, match="fully contain"):
        m.validate_lesson_record(record)


def test_rev04_lesson_record_rejects_missing_key() -> None:
    record = _valid_lesson_record()
    del record["lesson_id"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_lesson_record(record)


def test_rev04_lesson_record_rejects_unknown_key() -> None:
    record = _valid_lesson_record()
    record["unexpected_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_lesson_record(record)


def test_rev04_lesson_record_rejects_wrong_schema() -> None:
    record = _valid_lesson_record()
    record["schema"] = "run9-lesson-record/9.9"
    with pytest.raises(m.Run9ValidationError, match="schema"):
        m.validate_lesson_record(record)


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第7巡対応 — Fix 15（P2）: LessonRecord
# provenance の語彙予約適用。performance_source/voice_source/
# performance_author/composition_source/recording_source（+
# rights_manifest/provenance_manifest 参照欄）は全て外部第三者（PJS 側）の
# 事実を記述する欄であり、User 帰属専用 `<PENDING_USER_ATTESTATION>` を
# 拒否し `<UNRESOLVED_EXTERNAL>` のみ未解決値として許容する。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "performance_source",
        "voice_source",
        "performance_author",
        "composition_source",
        "recording_source",
    ],
)
def test_fix319_15_rejects_user_attestation_sentinel_in_provenance_field(field: str) -> None:
    """負例（parametrize）: provenance 系5フィールドいずれも User 帰属専用
    sentinel `<PENDING_USER_ATTESTATION>` を拒否し、`<UNRESOLVED_EXTERNAL>`
    への誘導メッセージを含むこと（Codex bot レビュー PR #319 第7巡指摘,
    Fix 15, P2, 採用）。"""
    record = _valid_lesson_record()
    record[field] = "<PENDING_USER_ATTESTATION>"
    with pytest.raises(m.Run9ValidationError, match="UNRESOLVED_EXTERNAL"):
        m.validate_lesson_record(record)


@pytest.mark.parametrize("field", ["rights_manifest", "provenance_manifest"])
def test_fix319_15_rejects_user_attestation_sentinel_in_reference_field(field: str) -> None:
    """負例: rights_manifest/provenance_manifest 参照欄で User 帰属専用
    sentinel の混入を拒否する（Fix 15 導入時点の挙動 — Fix 18 でこの2欄は
    さらに `<UNRESOLVED_EXTERNAL>` も拒否するよう強化されたが、
    `<PENDING_USER_ATTESTATION>` の拒否自体は変わらず回帰対象として残す。
    下記 `test_fix319_18_*` が Fix 18 で追加された両 sentinel 拒否を
    網羅する）。"""
    record = _valid_lesson_record()
    record[field] = "<PENDING_USER_ATTESTATION>"
    with pytest.raises(m.Run9ValidationError, match="UNRESOLVED_EXTERNAL"):
        m.validate_lesson_record(record)


def test_fix319_15_accepts_unresolved_external_in_provenance_field() -> None:
    """正例: 未解決の外部第三者事実は `<UNRESOLVED_EXTERNAL>` で表現でき、
    受理されること（`_valid_lesson_record()` fixture の
    performance_author/composition_source が実例)。"""
    m.validate_lesson_record(_valid_lesson_record())  # 例外を投げないことの確認


def test_fix319_15_valid_fixture_no_longer_uses_stale_pending_user_attestation_value() -> None:
    """`_valid_lesson_record()` 正例 fixture が stale な
    `<PENDING_USER_ATTESTATION>` 値を含まないことの直接確認——Fix 15 の
    語彙予約適用に伴う必須追随（放置すると正例が赤化する）。"""
    record = _valid_lesson_record()
    for field in (
        "performance_source", "voice_source", "performance_author",
        "composition_source", "recording_source",
        "rights_manifest", "provenance_manifest",
    ):
        assert record[field] != "<PENDING_USER_ATTESTATION>"


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第9巡対応 — Fix 18（P2）: LessonRecord の
# rights_manifest/provenance_manifest（参照/pin欄）で両 sentinel を拒否
# する。第7巡 Fix 15 は `<PENDING_USER_ATTESTATION>` のみを拒否し
# `<UNRESOLVED_EXTERNAL>` を代替として推奨したため、両欄が
# `<UNRESOLVED_EXTERNAL>` の record（使用可能な参照/pin を一切持たない）
# が構造的 valid のまま validate_lesson_record() を通過してしまっていた。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["rights_manifest", "provenance_manifest"])
@pytest.mark.parametrize(
    "sentinel", ["<PENDING_USER_ATTESTATION>", "<UNRESOLVED_EXTERNAL>"]
)
def test_fix319_18_rejects_both_sentinels_in_reference_field(field: str, sentinel: str) -> None:
    """負例（2欄 × 2 sentinel の parametrize、計4ケース）: rights_manifest/
    provenance_manifest はいずれの sentinel も参照/pin として使用不可の
    ため拒否されること——特に `<UNRESOLVED_EXTERNAL>` は provenance 系
    外部事実欄では許容される値だが、本2欄（参照/pin欄）では許容しない
    ことがこの負例の核心（Fix 15 との差分）。"""
    record = _valid_lesson_record()
    record[field] = sentinel
    with pytest.raises(m.Run9ValidationError, match="genuine reference/pin"):
        m.validate_lesson_record(record)


def test_fix319_18_both_reference_fields_unresolved_external_rejected() -> None:
    """負例（指摘本文が名指しするケース）: rights_manifest と
    provenance_manifest の両方が `<UNRESOLVED_EXTERNAL>` の record は、
    使用可能な参照/pin を一切持たない構造的 valid record になってはならず
    拒否されること。"""
    record = _valid_lesson_record()
    record["rights_manifest"] = "<UNRESOLVED_EXTERNAL>"
    record["provenance_manifest"] = "<UNRESOLVED_EXTERNAL>"
    with pytest.raises(m.Run9ValidationError, match="genuine reference/pin"):
        m.validate_lesson_record(record)


def test_fix319_18_valid_fixture_still_validates() -> None:
    """正例（回帰）: `_valid_lesson_record()` の rights_manifest/
    provenance_manifest（実在ファイルへの相対パス参照）が Fix 18 追加後も
    validator を通ることの end-to-end 確認。"""
    m.validate_lesson_record(_valid_lesson_record())  # 例外を投げないことの確認


# --- rights_manifest 4層構造の validator（変更1・2） -------------------------


def test_rev04_rights_manifest_four_layer_valid_file_passes() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_rev04_rights_manifest_principles_exact_3_statements() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert tuple(data["principles"]["statements"]) == (
        "Teacher ≠ Voice Identity Owner",
        "Teacher ≠ Performance Author",
        "Voice Source ≠ Performance Source",
    )


def test_rev04_rights_manifest_auto_interpretation_prohibited_present() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "自動的に解釈" in data["auto_interpretation_prohibited"]


def test_rev04_rights_manifest_rejects_missing_layer() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["performance_rights"]
    with pytest.raises(m.Run9ValidationError, match="missing required top-level key"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_unknown_top_level_key() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["unexpected_top_level"] = {}
    with pytest.raises(m.Run9ValidationError, match="unknown top-level key"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_missing_provenance_in_performance_layer() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["performance_rights"]["provenance"]
    with pytest.raises(m.Run9ValidationError, match="provenance"):
        m.validate_rights_manifest_four_layer(data)


# --- ネストブロック形状の閉集合強制（Codex bot レビュー PR #319 第1巡指摘2、P2）-


def test_rev04_rights_manifest_rejects_empty_provenance_dict() -> None:
    """`provenance: {}` は旧 validator を素通りしていた——ブロック欠落として拒否する。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["provenance"] = {}
    with pytest.raises(m.Run9ValidationError, match="missing required block"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_missing_synthesis_block() -> None:
    """DESIGN_RUN9_REVISION_0.4.md が規定する synthesis ブロックが欠落したまま
    valid-file テストが green だった実際の欠落（本 PR の起点）を再現する負例。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["provenance"]["synthesis"]
    with pytest.raises(m.Run9ValidationError, match="missing required block"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_unknown_block_in_provenance() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["provenance"]["unexpected_block"] = {"x": "y"}
    with pytest.raises(m.Run9ValidationError, match="unknown block"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_missing_key_inside_block() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["provenance"]["voice_source"]["source_id"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_unknown_key_inside_block() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["provenance"]["voice_source"]["unexpected_key"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_not_applicable_without_note() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["provenance"]["synthesis"]["note"]
    with pytest.raises(m.Run9ValidationError, match="not_applicable.*note"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_empty_string_value_in_block() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["performance_rights"]["provenance"]["performance_author"]["performer"] = "  "
    with pytest.raises(m.Run9ValidationError, match="non-empty string"):
        m.validate_rights_manifest_four_layer(data)


# --- 2026-08-25 User 追加裁定②: performer/composer 充填 + placeholder 語彙分離 -


def test_rev04_rights_manifest_performer_and_composer_filled_with_source() -> None:
    """performer/composer は外部資料出典付きで Junya Koguchi が充填されて
    いること（旧 `<PENDING_USER_ATTESTATION>` は誤用だった — 追加裁定②）。
    recording-master owner は裁定②の確定範囲外（論文著者性は録音物権利保有の
    証拠でない — PR #319 第 4 巡指摘採用）のため <UNRESOLVED_EXTERNAL> を維持。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert data["performance_rights"]["provenance"]["performance_author"]["performer"] == (
        "Junya Koguchi"
    )
    assert data["composition_rights"]["provenance"]["composition"]["composer"] == (
        "Junya Koguchi"
    )
    assert data["recording_master_rights"]["provenance"]["voice_source"]["owner"] == (
        "<UNRESOLVED_EXTERNAL>"
    )


def test_rev04_rights_manifest_lyricist_uses_unresolved_external_not_pending_user_attestation() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    lyricist = data["composition_rights"]["provenance"]["composition"]["lyricist"]
    assert lyricist == "<UNRESOLVED_EXTERNAL>"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_rev04_rights_manifest_rejects_pending_user_attestation_in_external_field() -> None:
    """外部の第三者事実欄に `<PENDING_USER_ATTESTATION>`（User 帰属欄専用）を
    使うのは誤用——`<UNRESOLVED_EXTERNAL>` を使うべき旨のエラーで拒否する
    （旧 performer/composer/lyricist がこの誤用の実例だった）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["composition_rights"]["provenance"]["composition"]["lyricist"] = (
        "<PENDING_USER_ATTESTATION>"
    )
    with pytest.raises(m.Run9ValidationError, match="UNRESOLVED_EXTERNAL"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_recording_master_rights_has_interpretations_section() -> None:
    """CC BY-SA 4.0 の share-alike 義務が合成出力へ及ぶかは事実でなく解釈
    であり、`interpretations` 節で license（事実）から分離されていること
    （2026-08-25 User 追加裁定②）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    interp = data["recording_master_rights"]["interpretations"][
        "share_alike_applies_to_synthesis_output"
    ]
    assert interp["status"] == "UNSETTLED_LEGAL_INTERPRETATION"
    assert interp["question"]
    assert interp["note"]


def test_rev04_rights_manifest_rejects_missing_interpretations_section() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["interpretations"]
    with pytest.raises(m.Run9ValidationError, match="interpretations"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_interpretations_entry_missing_status() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["interpretations"][
        "share_alike_applies_to_synthesis_output"
    ]["status"]
    with pytest.raises(m.Run9ValidationError, match="status"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_rejects_wrong_performance_source_id() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["performance_rights"]["performance_source"]["id"] = "NOT_PJS"
    with pytest.raises(m.Run9ValidationError, match="performance_source.id"):
        m.validate_rights_manifest_four_layer(data)


def test_rev04_rights_manifest_voice_identity_layer_extraction_matches_ledger() -> None:
    """4層再編後も voice_identity_rights 層の実体（17件・attest 状態）は
    無改変であることを既存 verify 関数経由で再確認する。"""
    raw = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    flat = m.extract_voice_identity_rights_layer(raw)
    ledger = m.load_user_donor_ledger_json(
        (_FOUNDRY_DIR / "recording_kit" / "user_donor_ledger.json").read_text(encoding="utf-8")
    )
    m.verify_rights_manifest_against_ledger(flat, ledger)  # 例外を投げないことの確認
    assert len(flat["entries"]) == 17


def test_rev04_rights_manifest_extract_rejects_wrong_top_schema() -> None:
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["schema"] = "some-other-schema/1.0"
    with pytest.raises(m.Run9ValidationError, match="schema"):
        m.extract_voice_identity_rights_layer(data)


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第2巡対応 — Fix 5（P2）: 層の permission
# フィールドの必須化（層別閉集合キー + rights_class/consent_status 語彙）
# ---------------------------------------------------------------------------


def test_fix319_5_rejects_recording_master_license_deleted() -> None:
    """recording_master_rights.license を削除しても旧 validator は非空
    role と provenance ブロックしか見ておらず受理していた——Fix 5 で
    層別必須キー閉集合が拒否するようになったことの確認（負例1）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["license"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_5_rejects_performance_rights_class_deleted() -> None:
    """performance_rights.rights_class の削除を拒否する（負例2）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["performance_rights"]["rights_class"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize(
    "layer_name",
    ["voice_identity_rights", "performance_rights", "composition_rights", "recording_master_rights"],
)
def test_fix319_5_rejects_consent_status_deleted_in_every_layer(layer_name: str) -> None:
    """4層すべてで consent_status 削除を拒否する（負例3。指摘が名指しした
    「各層の consent_status」を1層に留めず全数掃討する）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data[layer_name]["consent_status"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize(
    "layer_name",
    ["voice_identity_rights", "performance_rights", "composition_rights", "recording_master_rights"],
)
def test_fix319_5_rejects_unknown_key_added_to_every_layer(layer_name: str) -> None:
    """4層すべてで未知キー追加を拒否する（負例4）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data[layer_name]["unexpected_layer_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_5_rejects_voice_identity_rights_class_deleted() -> None:
    """User 帰属層（voice_identity_rights）も同じ閉集合強制の対象である
    ことの確認（layer 固有ではなく全層一律であることの直接確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["voice_identity_rights"]["rights_class"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_5_rejects_bare_pending_user_attestation_in_external_layer() -> None:
    """performance_rights は外部第三者（PJS）に関する層——rights_class に
    裸トークン `PENDING_USER_ATTESTATION`（User 帰属専用）を使うのは誤用
    として拒否し、`UNRESOLVED_EXTERNAL` を使うよう案内する（provenance
    ブロックの誤用拒否ロジックを層レベルへ拡張したことの確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["performance_rights"]["rights_class"] = "PENDING_USER_ATTESTATION"
    with pytest.raises(m.Run9ValidationError, match="UNRESOLVED_EXTERNAL"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_5_rejects_bare_unresolved_external_in_user_layer() -> None:
    """voice_identity_rights は User 帰属層——consent_status に裸トークン
    `UNRESOLVED_EXTERNAL`（外部第三者専用）を使うのは誤用として拒否する
    （逆方向の誤用拒否）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["consent_status"] = "UNRESOLVED_EXTERNAL"
    with pytest.raises(m.Run9ValidationError, match="PENDING_USER_ATTESTATION"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_5_recording_master_rights_free_text_status_still_accepted() -> None:
    """recording_master_rights.rights_class/consent_status は裸の予約
    トークンではなく自由記述の具体値（機械検証済みライセンス事実の要約）
    であり、値語彙検証は誤用としてこれを拒否しない（有効ファイルが
    green のままであることの直接確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert data["recording_master_rights"]["consent_status"] == (
        "LICENSE_CONFIRMED_USAGE_SCOPE_PENDING_TOOLING_REVIEW"
    )
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第2巡対応 — Fix 6（P2）: PJS rights の外部
# 未解決語彙への張り替え（UNRESOLVED_EXTERNAL）
# ---------------------------------------------------------------------------


def test_fix319_6_performance_and_composition_rights_use_unresolved_external() -> None:
    """performance_rights/composition_rights の rights_class/consent_status
    は `PENDING_USER_ATTESTATION`（User 帰属専用）ではなく
    `UNRESOLVED_EXTERNAL`（外部第三者専用）へ張り替え済み——PJS の演者/
    作曲者に関する権利は User が attest できる対象ではない（Codex bot
    レビュー PR #319 第2巡指摘, P2, 採用）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    for layer_name in ("performance_rights", "composition_rights"):
        assert data[layer_name]["rights_class"] == "UNRESOLVED_EXTERNAL"
        assert data[layer_name]["consent_status"] == "UNRESOLVED_EXTERNAL"


def test_fix319_6_voice_identity_rights_still_pending_user_attestation() -> None:
    """User 帰属欄（User donor の同意・usage grants 等）は張り替え対象外
    ——引き続き `PENDING_USER_ATTESTATION` のまま維持する（voice_identity_
    rights は User donor 自身の声の権利であり、Fix 6 の対象は PJS 側の
    3層のみ）。usage_grants.run9_identity_anchor は Fix 19（第9巡, P2,
    採用）で値語彙が {not_granted, granted} の閉集合へ凍結されたのに伴い、
    旧値 `pending`（閉集合外の第3値）から `not_granted` へ改めた——
    「まだ承認されていない」という意味論自体は変わらない。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert data["voice_identity_rights"]["rights_class"] == "PENDING_USER_ATTESTATION"
    assert data["voice_identity_rights"]["consent_status"] == "PENDING_USER_ATTESTATION"
    assert data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] == "not_granted"


def test_fix319_6_rights_manifest_still_validates_after_vocab_swap() -> None:
    """張り替え後も rights_manifest.json 全体が validator を通ることの
    直接確認（Fix 5 の必須キー閉集合・語彙検証拡張と Fix 6 の張り替えが
    整合していることの end-to-end 確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_6_recording_master_rights_not_swapped_no_bare_pending_token() -> None:
    """recording_master_rights は今回の張り替え対象に含めない
    （誤用パターン自体が元から存在しないため）——rights_class/
    consent_status に裸トークン `PENDING_USER_ATTESTATION` が残っていない
    ことの確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert data["recording_master_rights"]["rights_class"] != "PENDING_USER_ATTESTATION"
    assert data["recording_master_rights"]["consent_status"] != "PENDING_USER_ATTESTATION"


def test_fix319_6_history_records_vocab_reassignment_rationale() -> None:
    """仕分けの根拠（どの欄がどちらの主体に帰属するか）が manifest 注記
    （history）に明記されていること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    history = data["history"]
    swap_events = [h for h in history if "Fix 6" in h["event"]]
    assert len(swap_events) == 1
    event_text = swap_events[0]["event"]
    assert "UNRESOLVED_EXTERNAL" in event_text
    assert "voice_identity_rights" in event_text
    assert "User" in event_text


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第3巡対応 — Fix 8（P2）: license ネスト payload
# の形状検証（+ 同型欠陥だった usage_grants/interpretations エントリ/
# corpus_pins への同流儀の拡張）
# ---------------------------------------------------------------------------


def test_fix319_8_license_empty_dict_rejected() -> None:
    """recording_master_rights.license を `{}` へ置換しても、Fix 5 の層別
    必須キー閉集合はキーの**存在**しか見ていないため旧実装は受理していた
    ——Fix 8 のネスト形状検証（value/scope/derivative_obligation/source の
    4キー閉集合）が missing key として拒否することの確認（負例1）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["license"] = {}
    with pytest.raises(m.Run9ValidationError, match="license missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_license_scalar_replacement_rejected() -> None:
    """license をスカラー文字列へ置換した場合の拒否（負例2）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["license"] = "CC BY-SA 4.0"
    with pytest.raises(m.Run9ValidationError, match="license must be an object"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_license_value_field_deleted_rejected() -> None:
    """license.value（ライセンス種別そのもの）だけを削除した場合の拒否
    （負例3 — CC BY-SA 4.0 という値自体が消えるケースを名指しで検査）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["license"]["value"]
    with pytest.raises(m.Run9ValidationError, match=r"license missing required key.*value"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_license_unknown_key_rejected() -> None:
    """license に未知キーが混入した場合の拒否（負例4）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["license"]["unexpected_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="license has unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_license_blank_string_value_rejected() -> None:
    """license の値が空白のみの文字列の場合の拒否（非空文字列強制の確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["license"]["source"] = "   "
    with pytest.raises(m.Run9ValidationError, match=r"license\.source"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_usage_grants_empty_dict_rejected() -> None:
    """voice_identity_rights.usage_grants を `{}` へ置換した場合の拒否
    （負例1 — rev 0.2 改訂4「raw_audio_publication/model_general_
    distribution は run9_identity_anchor と別承認」の意味論を担う3キーが
    消えても旧実装は受理していた）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"] = {}
    with pytest.raises(m.Run9ValidationError, match="usage_grants missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_usage_grants_scalar_replacement_rejected() -> None:
    """usage_grants をスカラーへ置換した場合の拒否（負例2）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"] = "not_granted"
    with pytest.raises(m.Run9ValidationError, match="usage_grants must be an object"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_usage_grants_key_deleted_rejected() -> None:
    """usage_grants.raw_audio_publication の削除拒否（負例3）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"]
    with pytest.raises(m.Run9ValidationError, match="usage_grants missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_usage_grants_unknown_key_rejected() -> None:
    """usage_grants への未知キー混入拒否（負例4）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"]["unexpected_grant"] = "granted"
    with pytest.raises(m.Run9ValidationError, match="usage_grants has unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_interpretations_entry_unknown_key_rejected() -> None:
    """recording_master_rights.interpretations の1エントリに未知キーが
    混入した場合の拒否（負例1 — 旧実装は status/question/note の3キーが
    非空文字列であることのみを見ており、実データが持つ `source` を含む
    閉集合としては強制していなかった）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["interpretations"][
        "share_alike_applies_to_synthesis_output"
    ]["unexpected_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_interpretations_entry_source_deleted_rejected() -> None:
    """interpretations エントリの source 削除拒否（負例2 — source は実
    データに存在するが旧実装では必須キーとして強制されていなかった）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["interpretations"][
        "share_alike_applies_to_synthesis_output"
    ]["source"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_interpretations_entry_scalar_replacement_rejected() -> None:
    """interpretations の1エントリをスカラーへ置換した場合の拒否（負例3）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["interpretations"][
        "share_alike_applies_to_synthesis_output"
    ] = "UNSETTLED_LEGAL_INTERPRETATION"
    with pytest.raises(m.Run9ValidationError, match="must be an object"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_corpus_pins_scalar_replacement_rejected() -> None:
    """recording_master_rights.corpus_pins.source_archive_sha256（実 sha256
    pin 値）をスカラーへ置換した場合の拒否（負例1 — source archive pin /
    expanded corpus pin の2値は互いに代替ではない別対象、rev 0.2 改訂3）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"]["source_archive_sha256"] = (
        "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca"
    )
    with pytest.raises(m.Run9ValidationError, match="must be an object"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_corpus_pins_note_deleted_rejected() -> None:
    """corpus_pins.note 削除の拒否（負例2）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["corpus_pins"]["note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_corpus_pins_empty_dict_rejected() -> None:
    """corpus_pins を `{}` へ置換した場合の拒否（負例3）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"] = {}
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_corpus_pins_sub_block_unknown_key_rejected() -> None:
    """corpus_pins のサブブロック（source_archive_sha256）への未知キー
    混入拒否（負例4）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"]["source_archive_sha256"]["extra"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_8_valid_manifest_still_validates_after_shape_checks_added() -> None:
    """実 manifest（license/usage_grants/interpretations/corpus_pins 全て
    実キーのみを持つ）が Fix 8 追加後も validator を通ることの end-to-end
    確認（有効ファイルへの過剰一般化による誤検知が無いことの確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# --- performance_source ブロック（RUN9_CONTRACT.yaml 新設欄） ----------------


def test_rev04_performance_source_block_present_and_valid(contract_raw: Dict[str, Any]) -> None:
    block = contract_raw["performance_source"]
    assert block["id"] == "PJS"
    assert block["role"] == "EXTERNAL_PERFORMANCE_SOURCE"
    m.validate_performance_source_block(block)  # 例外を投げないことの確認


def test_rev04_performance_source_block_rejects_missing_teacher_note() -> None:
    block = {
        "id": "PJS",
        "role": "EXTERNAL_PERFORMANCE_SOURCE",
        "rights_manifest_ref": "inputs/rights_manifest.json#performance_rights",
        "teacher_terminology_note": "no owner claim here",
    }
    with pytest.raises(m.Run9ValidationError, match="does not mean the Voice owner"):
        m.validate_performance_source_block(block)


def test_rev04_performance_source_block_rejects_missing_separation_markers() -> None:
    block = {
        "id": "PJS",
        "role": "EXTERNAL_PERFORMANCE_SOURCE",
        "rights_manifest_ref": "inputs/rights_manifest.json#performance_rights",
        "teacher_terminology_note": "Teacher は Voice 所有者を意味しない。",
    }
    with pytest.raises(m.Run9ValidationError, match="Voice Source"):
        m.validate_performance_source_block(block)


def test_rev04_contract_declares_performance_source_top_level_key() -> None:
    assert "performance_source" in m._CONTRACT_TOP_LEVEL_KEYS


# --- b裁定 + 追加①是正: render_code_commit(歴史)=INFERRED_UNCONFIRMED /
# run9_render_code_commit(前方宣言)=DECLARED_FOR_RUN9 + bundle sha PINNED --


def test_rev04_render_code_commit_reverted_to_inferred_unconfirmed_historically() -> None:
    """2026-08-25 User 追加裁定①: render_code_commit（RUN6 の歴史的 export
    provenance）を USER_ATTESTED へ昇格したのは過大だった——歴史的事実は
    遡って attest しない方針により INFERRED_UNCONFIRMED へ差し戻した。
    history 配列に昇格・差し戻し両イベントが append-only で記録される。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    rcc = bundle["historical_export_provenance"]["render_code_commit"]
    assert rcc["status"] == "INFERRED_UNCONFIRMED"
    assert "attestation" not in rcc
    history = rcc["history"]
    assert any("USER_ATTESTED" in h["event"] for h in history)
    assert any(
        "INFERRED_UNCONFIRMED" in h["event"] and "差し戻" in h["event"] for h in history
    )


def test_rev04_run9_render_code_commit_declared_for_run9_with_ruling_reference() -> None:
    """b裁定の実体的な意味（RUN9 が今後使用する commit の確定）は独立の
    新設欄 run9_render_code_commit（status: DECLARED_FOR_RUN9）へ移した
    （2026-08-25 User 追加裁定①）。PR #319 第2巡指摘（P2, 採用）により
    さらに run9_runtime_inputs 節配下へ構造移動した。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    rrc = bundle["run9_runtime_inputs"]["run9_render_code_commit"]
    assert rrc["status"] == "DECLARED_FOR_RUN9"
    assert rrc["commit_full"] == "e2307b1080b00f3999702ce9017cfd75c7f862fe"
    assert rrc["declaration"]["declared_by"] == "User"
    assert rrc["declaration"]["declared_at"] == "2026-08-25"
    assert "aとbを承認" in rrc["declaration"]["statement"]


def test_rev04_render_code_commit_and_run9_render_code_commit_are_independent() -> None:
    """歴史的推定 (render_code_commit) と前方宣言 (run9_render_code_commit)
    は独立の欄——片方が INFERRED_UNCONFIRMED のまま、もう片方が確定済み
    (DECLARED_FOR_RUN9) であることは矛盾ではない。値（commit）は同じだが
    意味論は独立（2026-08-25 User 追加裁定①）。PR #319 以降は別の節
    （historical_export_provenance / run9_runtime_inputs）に構造分離されて
    いる。"""
    bundle = json.loads(BACKBONE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle["historical_export_provenance"]["render_code_commit"]["status"] == "INFERRED_UNCONFIRMED"
    assert bundle["run9_runtime_inputs"]["run9_render_code_commit"]["status"] == "DECLARED_FOR_RUN9"
    assert (
        bundle["historical_export_provenance"]["render_code_commit"]["commit_full"]
        == bundle["run9_runtime_inputs"]["run9_render_code_commit"]["commit_full"]
    )


def test_rev04_backbone_runtime_bundle_sha_pinned_matches_real_file(
    contract_raw: Dict[str, Any],
) -> None:
    """backbone_runtime_bundle_sha の PINNED 判定は run9_render_code_commit
    の確定を根拠とする（render_code_commit が INFERRED_UNCONFIRMED のまま
    であることは妨げない——2026-08-25 User 追加裁定①）。PR #319 第2巡指摘
    （P2, 採用）の構造分離により値を再計算した（旧値は
    test_fix319_2_backbone_runtime_bundle_sha_history_notes_prior_value
    が別途 append-only 保持を確認する）。"""
    field = contract_raw["backbone_runtime_bundle_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == (
        "83f67a309a8e918ff7758f0793d68bb885721d84f3a144916927bf00b67952a6"
    )
    assert field["value"] == m.compute_file_sha256(BACKBONE_BUNDLE_PATH)


def test_rev04_gate_state_still_blocked_after_bundle_promotion(contract: m.Run9RunContract) -> None:
    """backbone_runtime_bundle_sha の昇格だけでは他の PENDING 欄
    （dataset/config/learning_recipe 等）が残るため gate_state() は
    引き続き BLOCKED——正直な状態表現であることの確認（実装バグではない）。"""
    assert m.gate_state(contract) == "BLOCKED"


# --- metric_space_sha repin 整合 + terminology 非所有注記（変更1・4/§7裁定） -


def test_rev04_metric_space_sha_repinned_and_matches_domain() -> None:
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    assert domain_raw["metric_space_sha"] == (
        "de3a459bdea761850d465caa60a91a16d7a9a39b65652dd409f6e45a20ee1bb4"
    )
    assert domain_raw["metric_space_sha"] == _sha256_canonical_json(metric_space_obj)


def test_rev04_metric_space_manifest_still_validates_after_terminology_note() -> None:
    metric_space_obj = json.loads(IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8"))
    m.validate_identity_metric_space_manifest(metric_space_obj)  # 例外を投げないことの確認


def test_rev04_teacher_terminology_note_present_where_teacher_word_appears() -> None:
    """2026-08-25 User 追加裁定「確認メモ / RUN9 用語整理」指示5: 「teacher
    語の再出現拒否」チェックは実装しない代わりに、teacher という語が
    出現する identity_metric_space.json が非所有注記も併せ持つことを
    軽量に確認する（専用の run9_schema.py validator 関数としては実装
    しない——検証自体を見送る選択肢の部分的採用）。"""
    text = IDENTITY_METRIC_SPACE_PATH.read_text(encoding="utf-8")
    assert "teacher" in text
    assert "Voice 所有者" in text
    assert "Voice Source ≠ Performance Source ≠ Performance Author" in text


def test_rev04_common_teacher_transfer_literal_occurrences_are_old_name_references() -> None:
    """「Common Teacher Transfer」の literal な出現は、frozen 文書
    （v0.1 §14 見出しラベル・派生設計変更メモ、無改変）か、可変 artifact
    側では「旧名」として参照される場合に限る（DESIGN_RUN9_REVISION_0.4.md
    「変更4」の旧名注記付き参照規約 — active な呼称としての置換ではなく、
    旧称の由来注記としてのみ言及する）。README.md で言及する場合は
    「旧」または「Common Performance Lesson」という新称が同じ行内に
    現れていることを確認する。"""
    assert "Common Teacher Transfer" in DESIGN_DOC_PATH.read_text(encoding="utf-8")
    assert "Common Teacher Transfer" in DERIVED_DESIGN_CHANGES_PATH.read_text(encoding="utf-8")
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    for line in readme_text.splitlines():
        if "Common Teacher Transfer" in line:
            assert "旧" in line or "Common Performance Lesson" in line, (
                f"README references the old name without old-name framing: {line!r}"
            )


def test_rev04_teacher_reference_field_does_not_reappear_in_contract(
    contract_raw: Dict[str, Any],
) -> None:
    """terminology 逆行拒否（可能な範囲で）: `teacher_reference` という
    旧 v0.1 §11 の欄名が RUN9_CONTRACT.yaml の実際のトップレベルキーとして
    再出現していないことの確認——rev 0.3 で `interventions` 構造へ移行
    済みであり、rev 0.4 の `performance_source` 新設もこの欄を再導入
    しない。コメント中の説明的な言及（「teacher_reference 相当の欄は
    存在しない」等）まで禁止すると、その説明自体が書けなくなるため、
    パース済みキー集合を見る（生テキストの文字列検索ではない）。"""
    assert "teacher_reference" not in contract_raw.keys()
    assert "teacher_reference" not in contract_raw.get("interventions", {}).keys()


# --- design_revision 系譜表の repo artifact 写像確認 -------------------------


def test_rev04_doc_mapping_table_covers_all_8_changes() -> None:
    doc = REVISION_0_4_DOC_PATH.read_text(encoding="utf-8")
    assert "変更1" in doc and "変更2" in doc and "変更3" in doc and "変更4" in doc
    assert "変更5" in doc and "変更6" in doc and "変更7" in doc and "変更8" in doc


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第6巡対応 — Fix 12（P2）: not_applicable の
# フィールド別 allowlist 化（performance_author.performance_editor /
# synthesis.engine / synthesis.voicebank の3欄のみ許可）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layer_name", "block_name", "field_name"),
    [
        ("recording_master_rights", "voice_source", "owner"),
        ("performance_rights", "performance_author", "performer"),
        ("composition_rights", "composition", "composer"),
        ("composition_rights", "composition", "lyricist"),
    ],
)
def test_fix319_12_not_applicable_rejected_outside_allowlist(
    layer_name: str, block_name: str, field_name: str
) -> None:
    """owner/performer/composer/lyricist は allowlist 外——`not_applicable`
    へ書き換えると、必須権利保有者欄が未解決のまま消去され、将来の
    R9-G1 tooling に NO_UNKNOWN_RIGHTS_HOLDER を偽成立させ得る（負例
    ファミリー、Codex bot レビュー PR #319 第6巡指摘, Fix 12, P2, 採用）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data[layer_name]["provenance"][block_name][field_name] = "not_applicable"
    with pytest.raises(m.Run9ValidationError, match="does not permit 'not_applicable'"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_12_not_applicable_rejection_message_names_replacement_tokens() -> None:
    """拒否メッセージが未解決値の代替語彙（`<UNRESOLVED_EXTERNAL>` /
    `<PENDING_USER_ATTESTATION>`）を案内することの確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["provenance"]["voice_source"]["owner"] = "not_applicable"
    with pytest.raises(
        m.Run9ValidationError,
        match=r"<UNRESOLVED_EXTERNAL>.*<PENDING_USER_ATTESTATION>",
    ):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize(
    ("block_name", "field_name"),
    [
        ("performance_author", "performance_editor"),
        ("synthesis", "engine"),
        ("synthesis", "voicebank"),
    ],
)
def test_fix319_12_not_applicable_still_accepted_in_allowlisted_fields(
    block_name: str, field_name: str
) -> None:
    """allowlist 3欄（DESIGN_RUN9_REVISION_0.4.md 「provenance の実値充填」表
    と一致）は実 manifest で `not_applicable` + 理由 note のまま従来どおり
    受理されることの正例確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    layer_name = "performance_rights" if block_name == "performance_author" else "recording_master_rights"
    assert data[layer_name]["provenance"][block_name][field_name] == "not_applicable"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第6巡対応 — Fix 13（P2）: 契約 blocker 文の
# schema 名更新（旧 run9-user-donor-rights/1.0 のまま記述していた誤導経路
# の是正）
# ---------------------------------------------------------------------------


def test_fix319_13_contract_blocker_names_current_four_layer_schema() -> None:
    """RUN9_CONTRACT.yaml の dataset_manifest_sha blocker 文が、現 schema
    `run9-rights-manifest/2.0` と、legacy verifier への
    `extract_voice_identity_rights_layer()` 抽出フローを名指しで記述して
    いることの確認（Codex bot レビュー PR #319 第6巡指摘, Fix 13, P2,
    採用）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    reason = yaml.safe_load(contract_text)["dataset_manifest_sha"]["reason"]
    assert m.SCHEMA_RIGHTS_MANIFEST_FOUR_LAYER in reason
    assert "extract_voice_identity_rights_layer" in reason
    assert "PENDING_USER_ATTESTATION" in reason


def test_fix319_13_contract_blocker_does_not_name_legacy_schema_as_top_level() -> None:
    """blocker 文が旧 schema 名 `run9-user-donor-rights/1.0` を rights
    manifest の**トップレベル** schema として名指ししていないことの確認
    ——旧値への言及自体は「相当の内容」という nested 層への参照として
    残る（実装者が legacy verifier へ直接渡す誤導経路を閉じる）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    reason = yaml.safe_load(contract_text)["dataset_manifest_sha"]["reason"]
    assert "schema run9-user-donor-rights/1.0" not in reason
    assert "run9-user-donor-rights/1.0 相当の内容" in reason


def test_fix319_13_repo_wide_grep_finds_no_stale_present_tense_legacy_schema_top_level_claim() -> None:
    """repo 全体で、rights_manifest.json のトップレベル schema を旧
    `run9-user-donor-rights/1.0` と現在形で名指しする残存が無いことを
    確認する（履歴文脈・legacy verifier のフラット化後入力としての言及
    ・test 内の定数一致検査は対象外）。domains/identity_domain_run9_v1.json
    の pending pin note も rev 0.4 の4層 schema 名へ更新済みであることを
    直接確認する（Fix 13 と同型の誤導経路の同時是正）。"""
    domain_raw = (_RUN_DIR / "domains" / "identity_domain_run9_v1.json").read_text(
        encoding="utf-8"
    )
    assert "run9-rights-manifest/2.0" in domain_raw
    assert "schema run9-user-donor-rights/1.0）が起草済み" not in domain_raw


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第7巡対応 — Fix 14（P2）: 層抽出前の4層全体
# 検証の必須化。旧 extract_voice_identity_rights_layer() はトップレベル
# schema と voice_identity_rights しか見ておらず、他3層を削除した
# manifest でも抽出が成功し verify_rights_manifest_against_ledger() を
# 通過できていた——抽出は validate_rights_manifest_four_layer() を必須
# 内包する fail-closed 経路へ強化した。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "layer_name",
    ["performance_rights", "composition_rights", "recording_master_rights"],
)
def test_fix319_14_extract_rejects_manifest_missing_other_required_layer(
    layer_name: str,
) -> None:
    """負例（3パターン）: voice_identity_rights 自体は無傷でも、他の必須層
    （performance_rights/composition_rights/recording_master_rights）の
    いずれかが manifest から削除されていれば
    `extract_voice_identity_rights_layer()` が拒否すること——旧経路では
    donor rights 層だけ見て抽出に成功し、legacy verifier が他3層の欠落に
    気づかないまま通過していた（Codex bot レビュー PR #319 第7巡指摘,
    Fix 14, P2, 採用）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data[layer_name]
    with pytest.raises(m.Run9ValidationError, match="missing required top-level key"):
        m.extract_voice_identity_rights_layer(data)


def test_fix319_14_extract_still_succeeds_and_passes_legacy_verifier_on_valid_manifest() -> None:
    """正例（回帰）: 現行 rights_manifest.json（4層すべて valid）からの
    抽出は引き続き成功し、`verify_rights_manifest_against_ledger()` も
    従来どおり通過すること——Fix 14 の必須検証追加が正常系を壊していない
    ことの確認。"""
    raw = m.load_rights_manifest_json(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    flat = m.extract_voice_identity_rights_layer(raw)
    ledger = m.load_user_donor_ledger_json(
        (_FOUNDRY_DIR / "recording_kit" / "user_donor_ledger.json").read_text(encoding="utf-8")
    )
    m.verify_rights_manifest_against_ledger(flat, ledger)  # 例外を投げないことの確認
    assert len(flat["entries"]) == 17


def test_fix319_14_extract_calls_full_four_layer_validation_not_only_layer_shape() -> None:
    """他3層の構造は保ったまま `recording_master_rights.interpretations`
    のような4層検証固有のチェック対象を壊した manifest でも抽出が拒否
    されること——検証がトップレベルキー存在チェックのみに留まらず
    `validate_rights_manifest_four_layer()` を丸ごと呼んでいることの
    直接確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    del data["recording_master_rights"]["interpretations"]
    with pytest.raises(m.Run9ValidationError, match="interpretations"):
        m.extract_voice_identity_rights_layer(data)


def test_fix319_14_extract_docstring_and_contract_document_the_four_layer_gate() -> None:
    """docstring と RUN9_CONTRACT.yaml の抽出フロー記述の双方に、抽出が
    4層全体検証を内包する旨が追記されていることの確認。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    extract_start = source.index("def extract_voice_identity_rights_layer(")
    extract_end = source.index("def validate_rights_manifest_four_layer(")
    extract_body = source[extract_start:extract_end]
    assert "validate_rights_manifest_four_layer" in extract_body
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    reason = yaml.safe_load(contract_text)["dataset_manifest_sha"]["reason"]
    assert "validate_rights_manifest_four_layer" in reason


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第8巡対応 — Fix 16（P2）: voice_identity_rights.
# attestation の形状 + pending/attested 二形態の整合検証。旧
# validate_rights_manifest_four_layer() は attestation キーの**存在**しか
# 見ておらず、`{}`/スカラー/signer・timestamp・statement 欠落の
# `{"attested": true}` へ置換しても受理していた——User rights 遷移を
# 裏付ける証拠が構造的に valid のまま消えていた。
# ---------------------------------------------------------------------------


def test_fix319_16_attestation_empty_dict_rejected() -> None:
    """attestation を `{}` へ置換した場合の拒否（負例1）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {}
    with pytest.raises(m.Run9ValidationError, match="attestation missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attestation_scalar_replacement_rejected() -> None:
    """attestation をスカラーへ置換した場合の拒否（負例2）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = "not_attested"
    with pytest.raises(m.Run9ValidationError, match="attestation must be an object"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attestation_attested_true_missing_signer_rejected() -> None:
    """`{"attested": true}` へ置換（signer/timestamp/statement 欠落）した
    場合の拒否（負例3 — 指摘本文が名指しするケース）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {"attested": True}
    with pytest.raises(m.Run9ValidationError, match="attestation missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attestation_unknown_key_rejected() -> None:
    """attestation への未知キー混入拒否（負例4）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"]["unexpected_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="attestation has unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attestation_non_bool_attested_rejected() -> None:
    """attestation.attested が bool でない場合の拒否（負例5）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"]["attested"] = "false"
    with pytest.raises(m.Run9ValidationError, match="attestation.attested must be a bool"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_pending_form_with_nonnull_signer_rejected() -> None:
    """pending 形態（attested=false）なのに attested_by が非 null な場合の
    拒否（負例6 — pending/attested 二形態の混在を許さない）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"]["attested_by"] = "someone"
    with pytest.raises(m.Run9ValidationError, match=r"attested_by must be null"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attested_form_bad_timestamp_rejected() -> None:
    """attested 形態で attested_at が UTC ISO 8601 でない場合の拒否
    （負例7）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25",
        "statement": "I attest this recording as my own voice.",
    }
    with pytest.raises(m.Run9ValidationError, match="attested_at must be a UTC ISO 8601 timestamp"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attested_form_empty_statement_rejected() -> None:
    """attested 形態で statement が空文字列の場合の拒否（負例8）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "   ",
    }
    with pytest.raises(m.Run9ValidationError, match="statement must be a non-empty string"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_attested_form_while_status_still_pending_rejected() -> None:
    """attestation が attested 形態に埋まっているのに、層の rights_class/
    consent_status が依然 `PENDING_USER_ATTESTATION` のままの場合の拒否
    （負例9 — 二形態の整合違反、順方向）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_pending_form_while_status_no_longer_pending_rejected() -> None:
    """層の rights_class/consent_status が `PENDING_USER_ATTESTATION` から
    離れているのに、attestation が pending 形態のまま放置されている場合の
    拒否（負例10 — 二形態の整合違反、逆方向）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_valid_pending_fixture_still_validates() -> None:
    """正例（回帰）: 現行 rights_manifest.json の pending 形態
    （attested=false + signer/timestamp/statement すべて null +
    rights_class/consent_status = PENDING_USER_ATTESTATION）が Fix 16 追加後も
    validator を通ることの end-to-end 確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_16_valid_attested_form_accepted() -> None:
    """正例: pending 形態から attested 形態へ整合的に遷移した manifest
    （attestation 充足 + rights_class/consent_status を PENDING から離す）
    は受理されること——二形態の整合検証が誤検知しないことの確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第9巡対応 — Fix 19（P2）: usage_grants の値語彙を
# {not_granted, granted} の閉集合へ凍結し、granted への遷移に
# ①attestation の attested 形態 ②grant ごとの独立した承認記録
# （<grant>_approval: approved_at + approval_statement）を fail-closed で
# 要求する。旧 Fix 8（非空文字列のみの形状検証）は attestation.attested=
# false のまま raw_audio_publication/model_general_distribution を
# "granted" へ手編集しても受理してしまっていた——rev 0.2 改訂4
# （DESIGN_RUN9_REVISION_0.2.md 194-199行、「別承認まで独立に not_granted
# 維持」）への違反を構造的に防げていなかった。
# ---------------------------------------------------------------------------


_USAGE_GRANT_KEYS = (
    "run9_identity_anchor", "raw_audio_publication", "model_general_distribution",
)


def _attested_voice_identity_rights_layer(data: Dict[str, Any]) -> Dict[str, Any]:
    """`voice_identity_rights` 層を attested 形態（attestation.attested=true
    + rights_class/consent_status を PENDING から離す）へ書き換えたコピーを
    返す——Fix 19 の granted 前提条件①（attested 形態必須）を満たした
    状態から出発するテスト群の共通セットアップ。"""
    data = copy.deepcopy(data)
    layer = data["voice_identity_rights"]
    layer["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    layer["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    layer["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    return data


@pytest.mark.parametrize("grant_key", _USAGE_GRANT_KEYS)
def test_fix319_19_out_of_vocab_grant_value_rejected(grant_key: str) -> None:
    """負例（3キー parametrize）: usage_grants の値が {not_granted, granted}
    の閉集合外（例 'pending' — Fix 19 導入前の run9_identity_anchor の旧値）
    の場合に拒否されること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"][grant_key] = "pending"
    with pytest.raises(m.Run9ValidationError, match=r"usage_grants\." + grant_key + r" must be one of"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize("grant_key", _USAGE_GRANT_KEYS)
def test_fix319_19_granted_while_attestation_still_pending_rejected(grant_key: str) -> None:
    """負例（3キー parametrize）: attestation が pending 形態（attested=
    false）のまま grant を 'granted' へ書き換えても拒否されること——
    手編集での「承認証拠なしの公開/配布許可」成立を防ぐ核心の負例。
    run9_identity_anchor も他の2キーと同じ前提条件（attested 形態必須）を
    適用することの確認を兼ねる。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"][grant_key] = "granted"
    with pytest.raises(m.Run9ValidationError, match="attestation is not in attested form"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize("grant_key", _USAGE_GRANT_KEYS)
def test_fix319_19_granted_without_approval_record_rejected(grant_key: str) -> None:
    """負例（3キー parametrize）: attestation は attested 形態に整えても、
    grant 別の承認記録（`<grant>_approval`）が無ければ granted 遷移は拒否
    されること。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"][grant_key] = "granted"
    with pytest.raises(m.Run9ValidationError, match="missing its separate approval record"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_approval_record_missing_key_rejected() -> None:
    """負例: 承認記録が approval_statement を欠いた場合の拒否
    （`{"approved_at": ...}` のみ）。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
    }
    with pytest.raises(m.Run9ValidationError, match="approval missing required key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_approval_record_bad_timestamp_rejected() -> None:
    """負例: 承認記録の approved_at が UTC ISO 8601 でない場合の拒否。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25",
        "approval_statement": "Publication approved by User.",
    }
    with pytest.raises(m.Run9ValidationError, match="approved_at must be a UTC ISO 8601 timestamp"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_approval_record_blank_statement_rejected() -> None:
    """負例: 承認記録の approval_statement が空白のみの文字列の場合の拒否。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "   ",
    }
    with pytest.raises(m.Run9ValidationError, match="approval_statement must be a non-empty string"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_approval_record_present_while_not_granted_rejected() -> None:
    """負例: grant が not_granted のまま承認記録キーだけが残置されている
    場合の拒否（取り消し後の残置レコードのような矛盾状態を許さない）。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Publication approved by User.",
    }
    with pytest.raises(m.Run9ValidationError, match="must not be present while"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_approval_record_unknown_key_rejected() -> None:
    """負例: 承認記録に未知キーが混入した場合の拒否。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Publication approved by User.",
        "unexpected_field": "x",
    }
    with pytest.raises(m.Run9ValidationError, match="approval has unknown key"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_19_valid_not_granted_fixture_still_validates() -> None:
    """正例（回帰）: 現行 rights_manifest.json（3キーとも not_granted）が
    Fix 19 追加後も validator を通ることの end-to-end 確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


@pytest.mark.parametrize("grant_key", _USAGE_GRANT_KEYS)
def test_fix319_19_granted_with_full_preconditions_accepted(grant_key: str) -> None:
    """正例（3キー parametrize、run9_identity_anchor を含む）: attestation
    が attested 形態 + grant 別の承認記録が整っていれば granted 遷移は
    受理されること——run9_identity_anchor も他の2キーと同じ構造で
    granted 化できることの確認（境界宣言: 既存の attest 受理検証器要件
    run9_identity_anchor=='granted' と矛盾しない構造）。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"][grant_key] = "granted"
    data["voice_identity_rights"]["usage_grants"][f"{grant_key}_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": f"{grant_key} approved by User.",
    }
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_19_grant_approval_is_independent_per_key() -> None:
    """正例: raw_audio_publication を granted 化しても
    model_general_distribution は not_granted のまま独立に維持できること
    ——rev 0.2 改訂4「別承認」の意味論が承認記録レベルでも保たれていること
    の確認（一方の承認記録が他方の grant を暗黙に granted 化しない）。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Raw audio publication approved by User.",
    }
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認
    assert data["voice_identity_rights"]["usage_grants"]["model_general_distribution"] == "not_granted"
    assert "model_general_distribution_approval" not in data["voice_identity_rights"]["usage_grants"]


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第10巡対応 — Fix 21（P2）: pending/attested
# 二形態判定の両 status 要求化。旧 Fix 16 の判定は
# `rights_class == PENDING or consent_status == PENDING` という `or` 判定
# で pending 形態を認定しており、`consent_status` だけを
# `USER_ATTESTED_OWN_VOICE` 等へ書き換え `rights_class` を
# `PENDING_USER_ATTESTATION` のまま・attestation も pending 形態
# （attested=false）のままにしても「どちらかが pending」を満たすため通過
# してしまっていた——attestation なしに正典 permission フィールドの一部が
# 完了を主張できる抜け道。pending 形態は rights_class/consent_status の
# **両方**が `PENDING_USER_ATTESTATION` であることを要求し、attested
# 形態は**どちらも** PENDING でないことを要求する（片方のみの中間状態は
# 双方向とも form mismatch として拒否）。
# ---------------------------------------------------------------------------


def test_fix319_21_pending_form_with_only_consent_status_confirmed_rejected() -> None:
    """負例1（方向A）: consent_status のみを PENDING から確定値へ書き換え、
    rights_class は PENDING_USER_ATTESTATION・attestation は pending 形態
    （attested=false）のままの場合の拒否——Fix 21 導入前の `or` 判定では
    rights_class が pending のままのため通過してしまっていた抜け道。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_21_pending_form_with_only_rights_class_confirmed_rejected() -> None:
    """負例2（方向B、負例1 の対称ケース）: rights_class のみを PENDING
    から確定値へ書き換え、consent_status は PENDING_USER_ATTESTATION・
    attestation は pending 形態（attested=false）のままの場合の拒否。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_21_valid_both_pending_fixture_still_validates() -> None:
    """正例（回帰）: 現行 rights_manifest.json（rights_class/consent_status
    ともに PENDING_USER_ATTESTATION・attested=false）が Fix 21 適用後も
    validator を通ることの end-to-end 確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_21_valid_both_confirmed_attested_form_accepted() -> None:
    """正例: rights_class/consent_status を両方 PENDING から離し、
    attestation も attested 形態へ整合的に遷移した manifest は受理される
    こと（Fix 16 の正例と同型 — Fix 21 が両方向とも過剰一般化していない
    ことの確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第10巡対応 — Fix 22（P2）: attestation の
# timestamp 実在日時検証。`_RIGHTS_MANIFEST_UTC_TIMESTAMP_RE` は桁配置しか
# 見ておらず、`2026-99-99T99:99:99Z` のような実在しない日時（暦として
# 成立しない月/日/時/分/秒）を通してしまっていた。正規形チェックの後段で
# `datetime.fromisoformat`（Python 3.11+ は `Z` サフィックスをそのまま
# 受理する）による実在日時としてのパース可能性を追加検証する
# （`_is_real_utc_timestamp()`、`attested_at`/`approved_at` 両方で共有）。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "2026-99-99T99:99:99Z",  # 指摘本文が名指しする値そのもの
        "2026-13-01T00:00:00Z",  # 月 13
        "2026-02-30T00:00:00Z",  # 2 月 30 日（実在しない日）
        "2026-08-25T25:00:00Z",  # 時 25
        "2026-08-25T00:60:00Z",  # 分 60
    ],
)
def test_fix319_22_attested_at_impossible_datetime_rejected(bad_timestamp: str) -> None:
    """負例（5値 parametrize）: attested_at が正規形（桁配置）には一致する
    が暦として実在しない日時の場合、attested 形態として拒否されること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": bad_timestamp,
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(
        m.Run9ValidationError, match="attested_at must be a UTC ISO 8601 timestamp denoting a real"
    ):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_22_approved_at_impossible_datetime_rejected() -> None:
    """負例: usage_grants の承認記録（approved_at）が同じ実在しない日時
    （月 99 / 日 99 / 時 99 / 分 99 / 秒 99）の場合の拒否——`attested_at`
    と共有する `_is_real_utc_timestamp()` が承認記録側にも配線されている
    ことの確認。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-99-99T99:99:99Z",
        "approval_statement": "Publication approved by User.",
    }
    with pytest.raises(
        m.Run9ValidationError, match="approved_at must be a UTC ISO 8601 timestamp denoting a real"
    ):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_22_valid_real_attested_at_accepted() -> None:
    """正例（回帰）: 実在日時の attested_at（既存 Fix 16 正例と同じ値）は
    Fix 22 適用後も受理されること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_22_valid_real_approved_at_accepted() -> None:
    """正例（回帰）: 実在日時の approved_at（既存 Fix 19 正例と同じ値）は
    Fix 22 適用後も受理されること。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Publication approved by User.",
    }
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第10巡対応 — Fix 23（P2）: corpus_pins 値の
# 64hex 強制。Fix 8 は corpus_pins の各 `value` を非空文字列としてしか
# 検証しておらず、`"x"` のような使用不能な値でも構造的に valid のまま通過
# していた——実際に利用可能な sha256 pin を失っても検出できなかった。
# 既存の 64hex 検証ヘルパーと同じ正規表現（`_SHA256_HEX_RE`）を再利用し、
# `corpus_pins` の両 sha256 サブブロックの `value` に lowercase 64-hex を
# 追加強制する。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sub_key", ["source_archive_sha256", "expanded_corpus_identity_sha256"])
def test_fix319_23_corpus_pin_value_too_short_rejected(sub_key: str) -> None:
    """負例1（2キー parametrize）: corpus_pins の value を短い hex 文字列
    （指摘本文が名指しする `"x"` 相当）へ書き換えた場合の拒否。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"][sub_key]["value"] = "x"
    with pytest.raises(m.Run9ValidationError, match=r"\.value must be exactly 64 lowercase hex"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize("sub_key", ["source_archive_sha256", "expanded_corpus_identity_sha256"])
def test_fix319_23_corpus_pin_value_uppercase_rejected(sub_key: str) -> None:
    """負例2（2キー parametrize）: 64桁ではあるが大文字混じりの hex 文字列
    は拒否されること（lowercase 強制）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"][sub_key]["value"] = "A" * 64
    with pytest.raises(m.Run9ValidationError, match=r"\.value must be exactly 64 lowercase hex"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize("sub_key", ["source_archive_sha256", "expanded_corpus_identity_sha256"])
def test_fix319_23_corpus_pin_value_non_hex_rejected(sub_key: str) -> None:
    """負例3（2キー parametrize）: 64桁だが16進以外の文字を含む文字列は
    拒否されること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["recording_master_rights"]["corpus_pins"][sub_key]["value"] = "g" * 64
    with pytest.raises(m.Run9ValidationError, match=r"\.value must be exactly 64 lowercase hex"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_23_valid_real_corpus_pin_values_accepted() -> None:
    """正例（回帰）: 現行 rights_manifest.json の corpus_pins 実値
    （source_archive_sha256/expanded_corpus_identity_sha256 いずれも実在の
    lowercase 64-hex）が Fix 23 適用後も validator を通ることの
    end-to-end 確認。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認
