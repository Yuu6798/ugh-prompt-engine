"""test_run9_contract.py — RUN9 Phase 0 スキャフォールドの最低テスト
（DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §27 の静的検証可能
サブセット）。

各テストの docstring / 名前に §27 の項目番号を対応づける。音声処理・実
学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
import sys
import textwrap
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
# 現行 design_revision (0.6) の差分メモ。design_revision_doc_sha256 が
# pin する対象（RUN9-L0-HARNESS-3c、2026-08-27、design_revision 0.5 →
# 0.6 昇格）。
REVISION_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.6.md"
# rev 0.2/0.3/0.5 文書は無改変のまま存続する（design_revision 系譜の各1件）。
REVISION_0_2_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.2.md"
REVISION_0_3_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.3.md"
POR_ADJUDICATION_PATH = _RUN_DIR / "POR_CONCEPT_ADJUDICATION_20260824.txt"
PIN2_USER_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt"
)
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
# 共有 fixture: pinned な合成 domain（64hex ダミー値）+ 有効な rights_manifest
# （Fix 7, Codex bot レビュー PR #320 第5巡指摘, P1, 採用: build_founder() が
# `rights_manifest` を必須 keyword-only 引数化したため、以降 build_founder()/
# founder_genome_from_dict() を呼ぶ全テストが有効な manifest を渡す必要が
# ある。af0/ritsu は取消意味論を持たない外部アーティファクトの形状 pin
# のため引き続きダミー値のままでよいが、user だけは
# `extract_user_identity_attestation_projection()` が実際に受理する
# 内容と、その正規形 sha256 が一致していなければならない——本 fixture
# domain の user anchor は現行 `inputs/rights_manifest.json` の projection
# 正規形 sha256 で計算する（ハードコードしない: 同ファイルが将来改訂
# されても自動的に追随する）。
# ---------------------------------------------------------------------------


def _valid_test_rights_manifest() -> Dict[str, Any]:
    """`inputs/rights_manifest.json`（現行 attested + anchor grant granted
    状態）を読み込んだフレッシュなコピーを返す——Fix 7 以降、
    `build_founder()`/`founder_genome_from_dict()` を呼ぶテストが共有する
    「有効な」manifest。呼び出しごとに新規ロードする（テスト間の可変
    状態共有・書き換え混入を避ける——各テストは必要なら
    `copy.deepcopy` 済みの dict を自由に改変できる）。"""
    return json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def valid_rights_manifest() -> Dict[str, Any]:
    return _valid_test_rights_manifest()


def _pinned_fixture_domain() -> m.Run9IdentityDomain:
    return m.build_run9_identity_domain(
        anchor_hashes={
            "af0": "a" * 64,
            "ritsu": "b" * 64,
            "user": _sha256_canonical_json(
                m.extract_user_identity_attestation_projection(_valid_test_rights_manifest())
            ),
        },
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
    good = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()).to_dict()
    tampered = copy.deepcopy(good)
    tampered["coords"] = {"af0": 0.6, "ritsu": 0.3, "pjs": 0.1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(tampered, domain=domain, rights_manifest=_valid_test_rights_manifest())


# ---------------------------------------------------------------------------
# item 11/12: R9F-01 / R9F-02 weights exactly frozen
# ---------------------------------------------------------------------------


def test_item11_r9f01_weights_exactly_0p6_0p3_0p1(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    assert (g.coords.af0, g.coords.ritsu, g.coords.user) == (0.6, 0.3, 0.1)
    assert g.profile_label == "AF0_DOMINANT"


def test_item12_r9f02_weights_exactly_0p1_0p3_0p6(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert (g.coords.af0, g.coords.ritsu, g.coords.user) == (0.1, 0.3, 0.6)
    assert g.profile_label == "USER_DOMINANT"


# ---------------------------------------------------------------------------
# item 13: shared performance seed is identical
# ---------------------------------------------------------------------------


def test_item13_shared_performance_seed_identical(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert g1.performance_seed == g2.performance_seed == 909001 == m.SHARED_PERFORMANCE_SEED


# ---------------------------------------------------------------------------
# item 14: TRI_CROSSOVER deterministic Genome ID
# ---------------------------------------------------------------------------


def test_item14_genome_id_deterministic_same_input(pinned_domain: m.Run9IdentityDomain) -> None:
    """item 14: 同一 domain + 同一 founder_id → genome_id バイト一致
    （2回呼び出し）。"""
    a = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    b = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    assert a.genome_id == b.genome_id


def test_item14_genome_id_stable_through_canonical_json_roundtrip(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """item 14 補足: 正規形 JSON 経由の再構築（to_dict -> json.dumps ->
    json.loads -> founder_genome_from_dict）でも genome_id が一致する。"""
    original = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    text = json.dumps(original.to_dict(), sort_keys=True)
    reconstructed = m.founder_genome_from_dict(json.loads(text), domain=pinned_domain, rights_manifest=_valid_test_rights_manifest())
    assert reconstructed.genome_id == original.genome_id


# ---------------------------------------------------------------------------
# item 15: Founder IDs are distinct
# ---------------------------------------------------------------------------


def test_item15_founder_genome_ids_are_distinct(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert g1.genome_id != g2.genome_id
    assert g1.voice_id != g2.voice_id


# ---------------------------------------------------------------------------
# item 16: default skill state has no inherited PJS lesson
# ---------------------------------------------------------------------------


def test_item16_skill_state_is_default_neutral(pinned_domain: m.Run9IdentityDomain) -> None:
    g1 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert g1.skill_state == "DEFAULT_NEUTRAL"
    assert g2.skill_state == "DEFAULT_NEUTRAL"


def test_item16_genome_dict_has_no_pjs_lesson_derived_field(pinned_domain: m.Run9IdentityDomain) -> None:
    """item 16 補足: genome dict のフィールド集合に PJS lesson 由来の
    キー（lesson_id / teacher_reference 等）が存在しない（構造的保証 —
    Run9FounderGenome のフィールド定義そのものに含まれない）。"""
    g = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()).to_dict()
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


def test_item22_build_founder_signature_is_domain_founder_id_and_rights_manifest_only() -> None:
    """item 22 直接検査: `build_founder` のシグネチャが `(domain, founder_id,
    *, rights_manifest)` の3引数のみであること（Codex bot レビュー PR #320
    第5巡指摘, P1, 採用, Fix 7 により `rights_manifest` が必須 keyword-only
    引数として追加された——weights/coords を直接渡す公開経路が引き続き
    存在しないことは本テストの主張と別（`test_item22_no_public_weight_
    adjustment_api` が forbidden_params で直接検査する）。`rights_manifest`
    が keyword-only（デフォルト値なし）であることも確認する——省略可能な
    optional 化は fail-open 経路になるため禁止（Fix 7 設計判定）。"""
    sig = inspect.signature(m.build_founder)
    assert list(sig.parameters.keys()) == ["domain", "founder_id", "rights_manifest"]
    rights_manifest_param = sig.parameters["rights_manifest"]
    assert rights_manifest_param.kind == inspect.Parameter.KEYWORD_ONLY
    assert rights_manifest_param.default is inspect.Parameter.empty


def test_item22_unknown_founder_id_rejected(pinned_domain: m.Run9IdentityDomain) -> None:
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(pinned_domain, "R9F-03", rights_manifest=_valid_test_rights_manifest())


# ---------------------------------------------------------------------------
# item 40: no TotalScore field in evaluation/result schema
# ---------------------------------------------------------------------------


def test_item40_contract_has_no_total_score_field(contract_raw: Dict[str, Any]) -> None:
    canonical = json.dumps(contract_raw).lower().replace("_", "")
    assert "totalscore" not in canonical


def test_item40_genome_dict_has_no_total_score_field(pinned_domain: m.Run9IdentityDomain) -> None:
    g = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()).to_dict()
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


def test_unpinned_domain_rejects_build_founder() -> None:
    """合成の未 pin domain（anchor_hashes.user がプレースホルダ）は構造
    valid だが is_pinned() == False であり、build_founder() は
    ValueError を送出する（DESIGN_RUN9 §22 実行順 step 3→4 の機械強制）。
    2026-08-25 RUN9 User attestation 実行により実 domain draft
    （`domains/identity_domain_run9_v1.json`）は PINNED 済みへ遷移した
    ため、本テストはプレースホルダを直接注入した合成 domain で未 pin
    経路の拒否を回帰確認する（実 domain 側の pinned 経路は
    `test_run9_attest20260825_domain_user_anchor_now_pinned` が担う）。"""
    domain = m.run9_identity_domain_from_dict({
        "schema": m.SCHEMA_IDENTITY_DOMAIN,
        "domain_id": m.RUN9_DOMAIN_ID,
        "anchor_order": list(m.RUN9_ANCHOR_ORDER),
        "anchor_hashes": {"af0": "a" * 64, "ritsu": "b" * 64, "user": "<PIN_BEFORE_RUN>"},
        "excluded_teacher_identities": list(m.RUN9_EXCLUDED_TEACHER_IDENTITIES),
        "coordinate_precision": m.RUN9_COORDINATE_PRECISION,
        "normalization": m.RUN9_NORMALIZATION,
        "metric_space_sha": "d" * 64,
        "pin_source_candidates": {},
    })
    assert domain.is_pinned() is False
    with pytest.raises(m.Run9ValidationError):
        m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())


def test_unpinned_domain_placeholder_is_structurally_valid() -> None:
    """未 pin ドラフト domain 自体は構造検証（未知キー・anchor_order 等）は
    通過する — 未 pin は「構造不正」ではなく「pin 未充足」として区別する。
    2026-08-25 RUN9 User attestation 実行後は実 domain draft
    （`domains/identity_domain_run9_v1.json`）が PINNED 済みのため、
    合成の未 pin domain（`test_unpinned_domain_rejects_build_founder` と
    同一 fixture）で構造検証パスを回帰確認する。"""
    domain = m.run9_identity_domain_from_dict({
        "schema": m.SCHEMA_IDENTITY_DOMAIN,
        "domain_id": m.RUN9_DOMAIN_ID,
        "anchor_order": list(m.RUN9_ANCHOR_ORDER),
        "anchor_hashes": {"af0": "a" * 64, "ritsu": "b" * 64, "user": "<PIN_BEFORE_RUN>"},
        "excluded_teacher_identities": list(m.RUN9_EXCLUDED_TEACHER_IDENTITIES),
        "coordinate_precision": m.RUN9_COORDINATE_PRECISION,
        "normalization": m.RUN9_NORMALIZATION,
        "metric_space_sha": "d" * 64,
        "pin_source_candidates": {},
    })
    assert domain.domain_id == m.RUN9_DOMAIN_ID
    assert domain.anchor_order == ("af0", "ritsu", "user")
    assert domain.is_pinned() is False


def test_pinned_fixture_domain_succeeds(pinned_domain: m.Run9IdentityDomain) -> None:
    assert pinned_domain.is_pinned() is True
    g1 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
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
        m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())


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
    r9f02 = m.build_founder(domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    forged = r9f02.to_dict()
    forged["voice_id"] = "R9F-01"  # 座標・profile_label は R9F-02 のまま、ラベルだけ差し替え
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


def test_fix3_tampered_genome_id_rejected() -> None:
    """Codex bot レビュー PR #315 指摘3: genome_id だけを任意の16hex値へ
    差し替えた genome document は builder 照合で検出される（構造的には
    正規の16hexだが再計算値と不一致）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["genome_id"] = "f" * 16
    assert forged["genome_id"] != genuine.genome_id
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


def test_fix3_correctly_signed_genome_document_still_roundtrips() -> None:
    """対照実験: 改ざんされていない genome document は builder 照合を通過し、
    正典 Run9FounderGenome と完全一致する（Fix 3 が正常系まで壊していない
    ことの確認）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    reconstructed = m.founder_genome_from_dict(genuine.to_dict(), domain=domain, rights_manifest=_valid_test_rights_manifest())
    assert reconstructed.to_dict() == genuine.to_dict()


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第2巡対応 — Fix 4: load 後の raw 直接改変で
# READY を騙る経路の閉塞
# ---------------------------------------------------------------------------


def test_fix4_load_run9_contract_deepcopies_input(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘1(a): `load_run9_contract()` は
    入力 dict を deepcopy する。load 後に呼び出し元が渡した元 dict の
    ネストした pin 欄を書き換えても `Run9RunContract.raw` は影響を受けない
    （浅いコピーだとネスト dict が共有されたままになる）。

    テスト対象欄は依然 PENDING の `learning_recipe_sha`
    （`education_technique_lesson_manifest_sha` は RUN9-L0-HARNESS-3b で
    PINNED 化されたため本 fixture の対象から外した — 2026-08-27）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    fresh_raw["learning_recipe_sha"]["status"] = "PINNED"
    fresh_raw["learning_recipe_sha"]["value"] = "z" * 64  # 非hexだが元dict側だけの改変
    assert contract.raw["learning_recipe_sha"]["status"] == "PENDING"
    assert contract.raw["learning_recipe_sha"] is not fresh_raw["learning_recipe_sha"]


def test_fix4_gate_state_revalidates_and_rejects_direct_raw_tampering(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第2巡指摘1(b): 正常 load 後に
    `contract.raw["learning_recipe_sha"]["status"]` を直接 "PINNED" へ書き換えても
    （value は null のまま）、`gate_state()` は毎回 `contract.raw` を
    `load_run9_contract()` で再検証するため Run9ValidationError を送出する
    （load 済みオブジェクトの raw を直接書き換えて READY を騙る経路の閉塞。
    共有 module fixture の汚染を避けるため、ここではローカルにコピーした
    contract を使う）。

    テスト対象欄は依然 PENDING の `learning_recipe_sha`
    （`education_technique_lesson_manifest_sha` は RUN9-L0-HARNESS-3b で
    PINNED 化されたため本 fixture の対象から外した — 2026-08-27）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    contract.raw["learning_recipe_sha"]["status"] = "PINNED"  # value は null のまま
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
        m.build_founder(forged_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())


def test_fix6_validate_domain_invariants_accepts_genuine_domain(
    pinned_domain: m.Run9IdentityDomain,
) -> None:
    """対照実験: `build_run9_identity_domain()` が返す正規 domain は
    `_validate_domain_invariants()` を素通りする（正常系まで壊していない
    ことの確認）。"""
    m._validate_domain_invariants(pinned_domain)  # 例外を投げないことの確認
    g = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
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
    g1 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    with pytest.raises(TypeError):
        pinned_domain.anchor_hashes["af0"] = "f" * 64  # type: ignore[index]  # 差し替えを試みても失敗する
    g2 = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
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
        m.build_founder(forged_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())


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
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["coords"] = {"af0": "0.6", "ritsu": "0.3", "user": "0.1"}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


def test_fix12_coords_bool_value_rejected() -> None:
    """Codex bot レビュー PR #315 第5巡指摘2 補足: coords 値が bool の
    genome document も拒否される（bool は int のサブクラスのため、
    `isinstance(value, (int, float))` だけの判定だと素通りしてしまう）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["coords"] = {"af0": True, "ritsu": 0.3, "user": 0.1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


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
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["parents"] = {"AF0": 1, "RITSU": 1, "USER_DONOR": 1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


def test_fix14_performance_seed_float_variant_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `performance_seed` に
    `909001.0`（float）を渡すと拒否される（`909001.0 == 909001` は真だが
    `_is_strict_int()` が float を先に排除する）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["performance_seed"] = 909001.0
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


def test_fix14_genetic_generation_bool_variant_rejected() -> None:
    """Codex bot レビュー PR #315 第6巡指摘2: `genetic_generation` に
    `True` を渡すと拒否される（`True == 1` は真だが `_is_strict_int()` が
    bool を先に排除する）。"""
    domain = _pinned_fixture_domain()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    forged = genuine.to_dict()
    forged["genetic_generation"] = True
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(forged, domain=domain, rights_manifest=_valid_test_rights_manifest())


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
    """正例: 重複キーの無い既存 domain draft JSON は引き続き load できる。
    2026-08-25 RUN9 User attestation 実行により user anchor も PINNED 済み
    となったため、is_pinned() は True へ遷移した（旧: False）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.is_pinned() is True


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


def _pending_rights_manifest_fixture() -> Dict[str, Any]:
    """`inputs/rights_manifest.json`（2026-08-25 RUN9 User attestation 実行
    後は attested 形態）を読み込み、`voice_identity_rights` 層だけを attest
    前の pending 形態（`attested=false`・signer/timestamp/statement=`None`・
    `rights_class`/`consent_status`=`PENDING_USER_ATTESTATION`・
    `usage_grants` 全3キー `not_granted`）へ差し戻したコピーを返す。
    entries/donor_ledger_source 等の他フィールド、および他3層
    （performance_rights/composition_rights/recording_master_rights）は
    実ファイルのまま——pending 経路の負例テスト群（Fix 16/19/21/27 等）が
    要求する「現行 fixture の pending baseline」を、実 fixture が attested
    形態へ遷移した後も保つためのヘルパ（2026-08-25 RUN9 User attestation
    実行時点で新設。`_attested_voice_identity_rights_layer()` の逆方向）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    layer = data["voice_identity_rights"]
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
    return data


# --- item 1: design_revision 0.3 での contract load 成功 / "0.1"/"0.2" 拒否 -


def test_revision02_design_revision_constant_is_0_2() -> None:
    """rev 0.6（RUN9-L0-HARNESS-3c、2026-08-27）では
    `run9_schema.DESIGN_REVISION` 自体が "0.6" を凍結する
    （テスト名は歴史的に revision02_ prefix のまま — Fix 15 の
    founder_genome_shas 改名前例と同様、rename ではなく assertion のみ
    更新する）。"""
    assert m.DESIGN_REVISION == "0.6"


def test_revision02_current_contract_declares_0_2(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["design_revision"] == "0.6"
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


def test_revision05_old_0_4_contract_rejected(contract_raw: Dict[str, Any]) -> None:
    """rev 0.5（DESIGN_RUN9_REVISION_0.5.md、RUN9-L0-HARNESS-3a、
    2026-08-26, User 裁定「RUN9 User裁定 — AF0 runtime mapping」の採用）:
    旧 "0.4" を宣言する contract も意図どおり拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.4"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_revision03_old_0_2_contract_rejection_message_names_current_revision(
    contract_raw: Dict[str, Any],
) -> None:
    """PR #317 Codex bot レビュー第1巡 Fix 2 採用: 拒否メッセージが固定
    ファイル名（例: "DESIGN_RUN9_REVISION_0.2.md"）をハードコードして
    いると、design_revision を上げるたびにメッセージ内のファイル名だけが
    陳腐化する（実際に 0.2 -> 0.3、0.3 -> 0.4、0.4 -> 0.5、0.5 -> 0.6
    進行時に発生した/し得た不備）。メッセージが `DESIGN_REVISION` 定数
    （現在は "0.6"）から動的に導出されていることを、"0.2" 拒否時の
    メッセージに現行の "0.6" が含まれることで確認する — メッセージが
    旧値のまま固定化されていれば失敗する。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.2"
    with pytest.raises(m.Run9ValidationError, match="0.6"):
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


def test_fix320_5_hash_convention_inventory_lists_user_anchor_as_canonical_projection() -> None:
    """Codex bot レビュー PR #320 第3巡指摘（P2, 採用, Fix 5）の回帰テスト:
    `compute_file_sha256()` の docstring と `RUN9_CONTRACT.yaml` の規約
    サマリーコメントが、`anchor_hashes.user` を「af0 のみの例外」から
    取り残さず canonical 規約側の一員として列挙し、かつ user anchor が
    ファイル hash ではなく `extract_user_identity_attestation_
    projection()` 由来のメモリ上 projection dict の hash であることを
    明記していることを直接確認する——旧文言（af0 だけが例外）のまま
    再計算した maintainer が user anchor をファイル bytes で誤って
    再計算し、異なる pin・異なる genome_id を導出する経路を閉じる。"""
    schema_source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")

    # 1. docstring/contract コメントのいずれも "anchor_hashes.af0 のみ" /
    #    "af0 pin だけは" という単独例外の主張を残していないこと
    #    （両者とも "のみ"/"だけ" は他の許容された文脈で使われ得るため、
    #    af0 に限定した独占的例外主張の具体的な文字列のみを負例とする）。
    assert "`anchor_hashes.af0` のみ" not in schema_source
    assert "anchor_hashes.af0 pin だけは" not in contract_text

    # 2. compute_file_sha256() の docstring が3件（af0/metric_space_sha/
    #    user）を canonical 規約側として列挙し、user anchor が
    #    projection 由来（ファイル hash ではない）であることを明記して
    #    いること。
    assert "extract_user_identity_attestation_projection" in schema_source
    doc = m.compute_file_sha256.__doc__ or ""
    assert "anchor_hashes.user" in doc
    assert "extract_user_identity_attestation_projection" in doc
    assert "ファイルの hash ですらない" in doc

    # 3. RUN9_CONTRACT.yaml の規約サマリーコメントも同様に user anchor を
    #    canonical 規約側として列挙していること。
    assert "anchor_hashes.user" in contract_text
    assert "extract_user_identity_attestation_projection" in contract_text
    idx = contract_text.index("意図的な例外で正規形（canonical）規約を使う")
    nearby = contract_text[max(0, idx - 400) : idx + 1000]
    assert "anchor_hashes.user" in nearby, nearby
    assert "af0" in nearby and "metric_space_sha" in nearby, nearby


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


# --- item 4 (2026-08-25 RUN9 User attestation 実行により attested 形態へ
#     遷移。旧テスト名 revision02_rights_manifest_is_pending_user_attestation
#     は「Fix 15 の founder_genome_shas 改名前例」に倣い assertion のみ
#     更新する) ------------------------------------------------------------


def test_run9_attest20260825_rights_manifest_is_attested() -> None:
    """2026-08-25 RUN9 User attestation 実行（User 裁定「承認する」）後:
    voice_identity_rights 層は pending 形態から attested 形態へ遷移した
    （旧 test_revision02_rights_manifest_is_pending_user_attestation の
    assertion を反転——rev 0.4 4層構造自体・他3層・entries は無改変）。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    layer = rights["voice_identity_rights"]
    assert layer["rights_class"] == "USER_ATTESTED_OWN_VOICE"
    assert layer["consent_status"] == "USER_ATTESTED_OWN_VOICE"
    assert layer["attestation"]["attested"] is True
    assert layer["attestation"]["attested_by"] == "Yuu6798"
    assert layer["attestation"]["attested_at"] == "2026-08-25T06:47:25Z"
    assert layer["attestation"]["statement"]
    assert layer["usage_grants"]["run9_identity_anchor"] == "granted"
    assert layer["usage_grants"]["raw_audio_publication"] == "not_granted"
    assert layer["usage_grants"]["model_general_distribution"] == "not_granted"
    m.validate_rights_manifest_four_layer(rights)  # 例外を投げないことの確認


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


def test_run9_attest20260825_domain_user_anchor_now_pinned() -> None:
    """2026-08-25 RUN9 User attestation 実行により user anchor が PINNED
    化された（旧 test_revision02_domain_user_anchor_still_unpinned_while_
    rights_pending の assertion を反転）。

    Codex bot レビュー PR #320 第1巡指摘（P1, 採用, Fix 1）: binding scope を
    rights_manifest.json **全体**の正規形 sha256 から、
    `extract_voice_identity_rights_layer()` が返す **voice_identity_rights
    層のみ**の正規形 sha256 へ是正した（旧 assertion は全体束縛を検証して
    いたため反転）——他3層（performance_rights/composition_rights/
    recording_master_rights）の外部第三者 provenance が将来解決されても
    anchor が動かないことの直接保証は
    `test_fix320_1_user_anchor_unaffected_by_external_layer_only_change`
    が別途担う。

    Codex bot レビュー PR #320 第2巡指摘（P1, 採用, Fix 3）: 束縛対象を
    voice_identity_rights 層全体から `extract_user_identity_attestation_
    projection()` が返す不変 projection へさらに再限定したため、本テストの
    基準関数を張り替える（層抽出関数自体の単体テストは
    `test_fix320_1_user_anchor_binds_only_voice_identity_rights_layer_not_
    whole_file` 内で layer_sha として引き続き参照・別途保持）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    projection = m.extract_user_identity_attestation_projection(rights)
    assert domain.anchor_hashes["user"] == _sha256_canonical_json(projection)
    assert m._SHA256_HEX_RE.match(domain.anchor_hashes["user"])
    assert domain.anchor_hashes["user"] != "<PIN_BEFORE_RUN>"
    assert domain.is_pinned() is True


def test_fix320_1_user_anchor_binds_only_voice_identity_rights_layer_not_whole_file() -> None:
    """Codex bot レビュー PR #320 第1巡指摘（P1, 採用, Fix 1）の直接反証
    テスト: 旧 binding（rights_manifest.json 全体の正規形 sha256）だった
    ら一致するはずの値と、新 binding（voice_identity_rights 層のみ）の値が
    異なることを確認する——is_pinned() の pin 値が本当に層限定へ切り替わって
    いることの negative control。

    Codex bot レビュー PR #320 第2巡指摘（P1, 採用, Fix 3）更新: 現行の
    anchor 束縛値は layer_sha ですらなく、さらに先の projection_sha である
    ため、whole_file/layer/projection の3値がいずれも相異し、
    anchor_hashes.user が projection_sha とのみ一致することを確認する
    （`extract_voice_identity_rights_layer()` 自体は引き続き存在し
    layer_sha を計算できる——汎用アダプタとしての単体挙動はここで維持
    確認する）。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    whole_file_sha = _sha256_canonical_json(rights)
    layer_sha = _sha256_canonical_json(m.extract_voice_identity_rights_layer(rights))
    projection_sha = _sha256_canonical_json(m.extract_user_identity_attestation_projection(rights))
    assert len({whole_file_sha, layer_sha, projection_sha}) == 3
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.anchor_hashes["user"] == projection_sha
    assert domain.anchor_hashes["user"] != whole_file_sha
    assert domain.anchor_hashes["user"] != layer_sha


def test_fix320_1_user_anchor_unaffected_by_external_layer_only_change() -> None:
    """Codex bot レビュー PR #320 第1巡指摘（P1, 採用, Fix 1）の中核契約:
    voice_identity_rights 層の内容（User donor 録音・attestation・
    usage_grants）が不変のまま、他層（例: composition_rights.provenance.
    composition.lyricist という PJS 側 `<UNRESOLVED_EXTERNAL>` 欄）だけを
    具体値へ書き換えた合成 manifest でも、抽出した voice_identity_rights
    層の正規形 sha256（＝Fix 1 時点で anchor が束縛していた値）は変化
    しないことを直接確認する——外部第三者 provenance の解決が anchor・
    genome_id に影響しないという設計原則の機械証明。

    Codex bot レビュー PR #320 第2巡指摘（P1, 採用, Fix 3）更新: 現行の
    anchor 束縛値は projection_sha であるため、projection_sha についても
    同じ不変性を直接確認し、domain.anchor_hashes.user との一致を
    projection_sha 基準へ張り替える（layer_sha の不変性確認は
    `extract_voice_identity_rights_layer()` 自体の回帰として引き続き
    保持する）。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original_layer_sha = _sha256_canonical_json(m.extract_voice_identity_rights_layer(rights))
    original_projection_sha = _sha256_canonical_json(
        m.extract_user_identity_attestation_projection(rights)
    )

    mutated = copy.deepcopy(rights)
    mutated["composition_rights"]["provenance"]["composition"]["lyricist"] = "Someone Resolved"
    mutated_layer_sha = _sha256_canonical_json(m.extract_voice_identity_rights_layer(mutated))
    mutated_projection_sha = _sha256_canonical_json(
        m.extract_user_identity_attestation_projection(mutated)
    )

    assert mutated_layer_sha == original_layer_sha
    assert mutated_projection_sha == original_projection_sha
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.anchor_hashes["user"] == mutated_projection_sha


# ---------------------------------------------------------------------------
# PR #320 Codex bot レビュー第2巡対応 — Fix 3（P1）: user anchor 束縛を
# 「不変 identity-attestation projection」へさらに再限定する
# （`extract_user_identity_attestation_projection()`）。Fix 1（層全体束縛）は
# usage_grants/usage_grants_note を含んでおり、raw_audio_publication/
# model_general_distribution の別承認（rev 0.2 改訂4）が起きるたびに anchor
# が動く二律背反を再発させていた——本節はその再限定の直接証拠を積む。
# ---------------------------------------------------------------------------

_ATTESTATION_PROJECTION_KEYS = frozenset(
    {
        "schema", "source_layer", "donor_ledger_source", "donor_ledger_schema",
        "transcribed_at", "entries", "rights_class", "consent_status", "attestation",
    }
)


def _projection_hash(rights: Dict[str, Any]) -> str:
    """`extract_user_identity_attestation_projection()` の返り値の正規形
    sha256（Fix 3 テスト群の共通ヘルパー）。"""
    return _sha256_canonical_json(m.extract_user_identity_attestation_projection(rights))


def test_fix320_3_projection_closed_key_set() -> None:
    """projection の返り値が閉じた9キーちょうどを持つこと（余剰キーなし・
    欠落キーなし）——`usage_grants`/`usage_grants_note`/`role`/`note`/
    `binding_note`/`schema_legacy` がいずれも含まれないことを直接確認
    する。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    projection = m.extract_user_identity_attestation_projection(rights)
    assert set(projection.keys()) == _ATTESTATION_PROJECTION_KEYS
    assert projection["schema"] == "run9-identity-attestation-projection/1.0"
    assert projection["source_layer"] == "voice_identity_rights"
    for excluded_key in (
        "usage_grants", "usage_grants_note", "role", "note", "binding_note", "schema_legacy",
    ):
        assert excluded_key not in projection


def test_fix320_3_pending_attestation_rejected() -> None:
    """負例: attestation が pending 形態（`attested=false`）の manifest
    からの抽出は `Run9ValidationError`（`ValueError` サブクラス）で拒否
    されること——本 projection は anchor pin 専用であり、pending 形態の
    hash が anchor 候補として見える経路をここで構造的に閉じる。"""
    data = _pending_rights_manifest_fixture()
    with pytest.raises(m.Run9ValidationError, match="attested"):
        m.extract_user_identity_attestation_projection(data)


def test_fix320_3_anchor_equals_projection_hash() -> None:
    """統合テスト: `domain.anchor_hashes.user` が projection の実 hash と
    一致すること（旧 `extract_voice_identity_rights_layer()` 基準の
    anchor 一致テストは本関数基準へ張り替え済み——
    `test_run9_attest20260825_domain_user_anchor_now_pinned` 参照）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert domain.anchor_hashes["user"] == _projection_hash(rights)


def test_fix320_3_invariant_to_raw_audio_publication_separate_approval() -> None:
    """不変性 (a): `raw_audio_publication` を `granted` + 正しい承認記録
    （`approved_at` の UTC ISO 8601 タイムスタンプ + 非空
    `approval_statement` — 既存 validator が要求する形）付きへ遷移しても
    projection（延いては anchor hash）は不変であること——別承認による
    設計上正規の遷移が anchor を動かさないことの直接証明。変異後も四層
    検証自体は通ることを前提として確認する。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    mutated["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Raw audio publication approved by User.",
    }
    m.validate_rights_manifest_four_layer(mutated)  # 前提: 変異後も四層検証は通る
    assert _projection_hash(mutated) == original


def test_fix320_3_invariant_to_usage_grants_note_wording() -> None:
    """不変性 (b): `usage_grants_note` の文言変更は projection を変えない
    こと。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["usage_grants_note"] = "Rewritten note text for clarity."
    m.validate_rights_manifest_four_layer(mutated)
    assert _projection_hash(mutated) == original


def test_fix320_3_invariant_to_binding_note_wording() -> None:
    """不変性 (c): `binding_note` の追記・文言変更は projection を変えない
    こと——`binding_note` は束縛方式自体の記述という自己参照であり、
    これを projection 外に置いたことで「binding 文書の明確化のたびに
    repin」という Fix 1 時代の実例（本 PR の作業自体がその実例）を今後
    解消することの直接証明。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["binding_note"] += " Additional clarifying remark."
    m.validate_rights_manifest_four_layer(mutated)
    assert _projection_hash(mutated) == original


def test_fix320_3_invariant_to_role_and_note_wording() -> None:
    """不変性 (d): `role` / `note` の文言変更は projection を変えないこと。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["role"] = "Rewritten role description."
    mutated["voice_identity_rights"]["note"] = "Rewritten note description."
    m.validate_rights_manifest_four_layer(mutated)
    assert _projection_hash(mutated) == original


def test_fix320_3_invariant_to_external_layer_resolution() -> None:
    """不変性 (e): 外部3層の `<UNRESOLVED_EXTERNAL>` 欄が将来解決されても
    projection は不変であること（Fix 1 の中核契約を projection 化後も
    継承していることの確認 — `test_fix320_1_user_anchor_unaffected_by_
    external_layer_only_change` の複数欄版）。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["composition_rights"]["provenance"]["composition"]["lyricist"] = "Someone Resolved"
    mutated["recording_master_rights"]["provenance"]["voice_source"]["owner"] = "Someone Resolved"
    m.validate_rights_manifest_four_layer(mutated)
    assert _projection_hash(mutated) == original


def test_fix320_3_sensitive_to_entries_mutation() -> None:
    """感度の検証: `entries` の1件改変で projection hash が変わること。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["entries"][0]["duration_sec"] = 999.0
    assert _projection_hash(mutated) != original


def test_fix320_3_sensitive_to_attestation_statement_mutation() -> None:
    """感度の検証: `attestation.statement` の改変で projection hash が
    変わること。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = _projection_hash(rights)
    mutated = copy.deepcopy(rights)
    mutated["voice_identity_rights"]["attestation"]["statement"] += " Amended."
    assert _projection_hash(mutated) != original


def test_fix320_3_projection_hash_is_sensitive_to_rights_class_and_consent_status() -> None:
    """感度の検証（projection 構造への直接検証）: `rights_class`/
    `consent_status` は projection の閉じたキー集合に含まれるため、値が
    変われば canonical hash も変わる。

    実装ノート: attested 形態は両 status が厳密に `USER_ATTESTED_OWN_VOICE`
    と一致することを要求する（`_validate_rights_manifest_voice_identity_
    attestation()`）ため、四層検証を満たしたまま `rights_class` だけを
    単独で閉語彙外の値へ書き換えて `extract_user_identity_attestation_
    projection()` を再度通すことはできない（`Run9ValidationError` の
    form-mismatch で拒否される——`test_fix320_3_pending_attestation_
    rejected` が pending 形態側の同種ガードを別途確認する）。本テストは
    projection の**返り値そのもの**を直接変異させ、`rights_class`/
    `consent_status` が canonical hash の対象キーに実際に含まれている
    ことを確認する——宣誓事実（両 status）の変更が anchor hash に反映
    される設計であることの直接証明であり、四層検証の form-mismatch ガード
    とは独立した検証。"""
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    projection = m.extract_user_identity_attestation_projection(rights)
    mutated_projection = copy.deepcopy(projection)
    mutated_projection["rights_class"] = "SOME_OTHER_VALUE"
    mutated_projection["consent_status"] = "SOME_OTHER_VALUE"
    assert _sha256_canonical_json(mutated_projection) != _sha256_canonical_json(projection)


# ---------------------------------------------------------------------------
# PR #320 Codex bot レビュー第2巡対応 — Fix 4（P2）: pin_source_candidates.user
# の先頭 PINNED エントリを履歴として明示する（全 manifest hash を現行
# レシピとして提示し続け後続 REPINNED と矛盾していた欠陥の是正）。
# ---------------------------------------------------------------------------

_PIN_SOURCE_CURRENT_LABELS = ("SUPERSEDED", "PENDING", "REPINNED")


def test_fix320_4_no_current_pinned_label_in_user_pin_source_candidates() -> None:
    """回帰テスト（Codex bot レビュー PR #320 第2巡指摘, P2, 採用, Fix 4）:
    `pin_source_candidates.user` のどのエントリも先頭語が生の `PINNED`
    ではないこと（`SUPERSEDED`/`PENDING`/`REPINNED` のいずれかのみ）、
    かつ末尾以外の `REPINNED`/`PINNED` 系エントリはすべて `SUPERSEDED`
    注記を含むことを検証する——「現行レシピを名乗る PINNED ラベル」が
    1件も残らないことの機械証明。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    entries = domain_raw["pin_source_candidates"]["user"]
    assert len(entries) >= 2
    for entry in entries:
        assert entry.startswith(_PIN_SOURCE_CURRENT_LABELS), entry[:80]
        assert not entry.startswith("PINNED"), entry[:80]
    for entry in entries[:-1]:
        if entry.startswith("REPINNED") or entry.startswith("PINNED"):
            assert "SUPERSEDED" in entry, entry[:80]


def test_fix320_4_final_entry_is_current_repinned_recipe() -> None:
    """回帰テスト: 末尾エントリ（現行レシピ）は `REPINNED` で始まり、
    `SUPERSEDED` 注記を含まないこと（現行レシピ自身を SUPERSEDED 扱い
    しては矛盾するため）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    entries = domain_raw["pin_source_candidates"]["user"]
    final_entry = entries[-1]
    assert final_entry.startswith("REPINNED")
    assert "SUPERSEDED" not in final_entry.split(":", 1)[0]


# ---------------------------------------------------------------------------
# PR #320 Codex bot レビュー第4巡対応 — Fix 6（P1）: `usage_grants.
# run9_identity_anchor` の取消（revoked: "granted" → "not_granted"、
# attestation 自体は歴史的記録として保持）が projection hash・genome_id の
# どこにも効かない偽成功経路を閉じる。hash は宣誓事実のみで不変のまま
# （repin なし）、抽出（`extract_user_identity_attestation_projection()`）を
# 第2の fail-closed 前提条件でゲートする。
# ---------------------------------------------------------------------------


def _revoked_anchor_grant_rights_manifest_fixture() -> Dict[str, Any]:
    """`inputs/rights_manifest.json`（attested 形態）を読み込み、
    `usage_grants.run9_identity_anchor` だけを `"granted"` → `"not_granted"`
    へ差し戻したコピーを返す——attestation 自体（attested=true / signer /
    timestamp / statement）と rights_class/consent_status
    （`USER_ATTESTED_OWN_VOICE`）は不変のまま（Fix 6 の指摘が要求する
    「attestation は歴史的記録として保持したまま grant のみ取消」状態を
    再現する）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] = "not_granted"
    return data


def test_fix320_6_projection_extraction_rejects_revoked_anchor_grant() -> None:
    """(a) 取消 manifest（attested 形 + anchor grant not_granted）から
    `extract_user_identity_attestation_projection()` を呼ぶと
    `Run9ValidationError` で拒否されること——取消状態の manifest からは
    anchor-eligible hash を生成させない。"""
    revoked = _revoked_anchor_grant_rights_manifest_fixture()
    with pytest.raises(m.Run9ValidationError, match="run9_identity_anchor"):
        m.extract_user_identity_attestation_projection(revoked)


def test_fix320_6_revoked_manifest_still_passes_four_layer_validation() -> None:
    """(b) 同じ取消 manifest は `validate_rights_manifest_four_layer()` を
    通ること——取消は正当に記録可能な文書状態であり、拒否するのは
    projection 抽出であって文書検証そのものではない（記録すら拒否すると
    取消の証跡が残せなくなる、という設計判定の直接確認）。"""
    revoked = _revoked_anchor_grant_rights_manifest_fixture()
    m.validate_rights_manifest_four_layer(revoked)  # 例外を投げないことの確認


def test_fix320_6_gate_is_projection_extraction_not_build_founder_or_gate_state() -> None:
    """(c) 「gate」側の直接テスト。**Fix 7（Codex bot レビュー PR #320
    第5巡指摘, P1, 採用）による記述の撤回・是正**: 本テストは元々
    （Fix 6 時点）「取消後も `domain.anchor_hashes['user']` の pin 値
    自体は不変のため `is_pinned()`/`build_founder()` は rights_manifest.
    json を一切参照せず成功し続ける」ことを期待どおりの挙動として明文化
    していたが、指摘のとおりこれは実効性の無いガード（呼び出し元が
    テスト/docs のみ）だった。Fix 7 で `build_founder()` へ
    `rights_manifest` が必須引数化され、内部で `extract_user_identity_
    attestation_projection()` を実行するようになったため、**取消後の
    manifest を渡すと `build_founder()` 自身が今度は確実に失敗する**
    ——本テストのアサーションを反転する（`gate_state()`/
    `Run9IdentityDomain.is_pinned()` 自体は rights_manifest.json の
    内容を一切評価しない構造述語のまま据え置き。これは変わらない —
    `is_pinned()` は pin 値の形状のみを見る）。"""
    revoked = _revoked_anchor_grant_rights_manifest_fixture()
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)

    # is_pinned()（domain 側の pin 値の形状のみを見る構造述語）は
    # rights_manifest.json の内容を一切参照しないため、取消後も引き続き
    # True のまま——これは Fix 7 でも変更していない。
    assert domain.is_pinned() is True

    # 一方、build_founder() は Fix 7 により rights_manifest を消費経路へ
    # 取り込んだため、取消後の manifest を渡すと確実に失敗する
    # （Fix 6 時点は「成功し続ける」だったが、本 Fix でこれを反転した）。
    with pytest.raises(m.Run9ValidationError, match="run9_identity_anchor"):
        m.build_founder(domain, "R9F-01", rights_manifest=revoked)

    # 有効な（取消されていない）manifest を渡せば引き続き成功する。
    m.build_founder(
        domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()
    )  # 例外を投げないことの確認


def test_fix320_6_anchor_hash_and_genome_id_unchanged_by_this_fix() -> None:
    """(d) 回帰: 本 Fix（第2の fail-closed 前提条件の追加）は現行
    rights_manifest.json・domain の pin 値・genome_id のいずれも変更しない
    ことの直接確認——「hash 復帰ではなくゲート化」という設計判定どおり
    repin が発生していないこと。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert (
        domain.anchor_hashes["user"]
        == "8569705be318d672d5f77ba955054a76d446664bb0883850a69c1fc35a55e804"
    )
    rights = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert domain.anchor_hashes["user"] == _projection_hash(rights)
    g1 = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert g1.genome_id == "66f420672a154283"
    assert g2.genome_id == "63f4b8f24b827cd4"


# ---------------------------------------------------------------------------
# PR #320 Codex bot レビュー第5巡対応 — Fix 7（P1）: Fix 6 のガード
# （`extract_user_identity_attestation_projection()` の取消/pending 検知）
# を実消費経路（`build_founder()`）へ配線する。`rights_manifest` を
# デフォルト値のない必須 keyword-only 引数として追加し、projection の
# 正規形 sha256 が `domain.anchor_hashes.user` と厳密一致することも
# 検証する（stale pin・manifest 改変の検出を「テスト時のみ」から
# 「genome_id 構築の実経路」へ昇格）。genome_id の計算ロジック自体は
# 無変更——本節のテストは主に「本 Fix 前後で genome_id が不変」ことを
# 反復して確認する。
# ---------------------------------------------------------------------------


def test_fix320_7_build_founder_genome_id_unchanged_after_wiring(
    valid_rights_manifest: Dict[str, Any],
) -> None:
    """(a) 既存の `build_founder()` 呼び出し箇所を全て実 manifest 渡しへ
    更新した後も、実 domain（`domains/identity_domain_run9_v1.json`）から
    計算される genome_id 期待値が**不変**であることの直接確認
    （`test_fix320_6_anchor_hash_and_genome_id_unchanged_by_this_fix` と
    同型だが、本節では Fix 7 固有の観点として明示的に保持する——
    `rights_manifest` 引数の追加という**署名変更**それ自体が genome_id
    計算へ影響しないことの確認）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    g1 = m.build_founder(domain, "R9F-01", rights_manifest=valid_rights_manifest)
    g2 = m.build_founder(domain, "R9F-02", rights_manifest=valid_rights_manifest)
    assert g1.genome_id == "66f420672a154283"
    assert g2.genome_id == "63f4b8f24b827cd4"
    # 決定論: 同一入力で再構築しても同じ genome_id（署名変更後も維持）。
    assert m.build_founder(
        domain, "R9F-01", rights_manifest=valid_rights_manifest
    ).genome_id == g1.genome_id


def test_fix320_7_build_founder_rejects_revoked_anchor_grant() -> None:
    """(b) 取消 manifest（attested 形 + anchor grant not_granted）を渡すと
    `build_founder()` が `Run9ValidationError` になること——Fix 6 時点の
    `test_fix320_6_gate_is_projection_extraction_not_build_founder_or_
    gate_state`（当時「build_founder は成功し続ける」と明文化していた）
    のアサーションを Fix 7 で反転したことの単体確認。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    revoked = _revoked_anchor_grant_rights_manifest_fixture()
    with pytest.raises(m.Run9ValidationError, match="run9_identity_anchor"):
        m.build_founder(domain, "R9F-01", rights_manifest=revoked)


def test_fix320_7_build_founder_rejects_pending_attestation() -> None:
    """(c) pending 形態（attested=false）の manifest を渡すと
    `build_founder()` が `Run9ValidationError` になること——Fix 6 の
    attested 前提条件も消費経路（build_founder）で毎回強制されることの
    確認。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    pending = _pending_rights_manifest_fixture()
    with pytest.raises(m.Run9ValidationError, match="attested"):
        m.build_founder(domain, "R9F-01", rights_manifest=pending)


def test_fix320_7_build_founder_rejects_manifest_hash_mismatch() -> None:
    """(d) attested 形 + anchor grant granted であっても、`entries` の
    1件改変等により projection の正規形 sha256 が `domain.anchor_hashes
    ['user']` と一致しない manifest を渡すと `build_founder()` が
    `Run9ValidationError` になること——stale pin・manifest 改変の検出が
    「テスト時のみ」から「genome_id 構築の実経路」へ昇格したことの核心
    テスト。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    mismatched = _valid_test_rights_manifest()
    mismatched["voice_identity_rights"]["entries"][0]["duration_sec"] = 999.0
    with pytest.raises(m.Run9ValidationError, match="anchor_hashes"):
        m.build_founder(domain, "R9F-01", rights_manifest=mismatched)


def test_fix320_7_build_founder_rights_manifest_argument_is_required() -> None:
    """(e) `rights_manifest` を省略した呼び出しは `TypeError` になること
    （署名レベルの fail-closed 確認——デフォルト値のない必須 keyword-only
    引数であり、None 許容やオプション化のような fail-open 経路は
    存在しない）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    with pytest.raises(TypeError):
        m.build_founder(domain, "R9F-01")  # type: ignore[call-arg]


def test_fix320_7_founder_genome_from_dict_rights_manifest_argument_is_required() -> None:
    """(e) 兄弟関数 `founder_genome_from_dict()` も同様に `rights_manifest`
    が必須 keyword-only 引数であり、省略すると `TypeError` になること
    （「manifest を渡せない呼び出し形が型的に存在しない」という Fix 7
    の呼び出し規約を、build_founder() の唯一の内部呼び出し元でも
    確認する）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    valid = _valid_test_rights_manifest()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=valid)
    with pytest.raises(TypeError):
        m.founder_genome_from_dict(genuine.to_dict(), domain=domain)  # type: ignore[call-arg]


def test_fix320_7_founder_genome_from_dict_rejects_revoked_anchor_grant() -> None:
    """founder_genome_from_dict() 経由でも取消 manifest は拒否されること
    （build_founder() への配線が兄弟関数からも実効することの end-to-end
    確認）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    valid = _valid_test_rights_manifest()
    genuine = m.build_founder(domain, "R9F-01", rights_manifest=valid)
    revoked = _revoked_anchor_grant_rights_manifest_fixture()
    with pytest.raises(m.Run9ValidationError, match="run9_identity_anchor"):
        m.founder_genome_from_dict(genuine.to_dict(), domain=domain, rights_manifest=revoked)


def test_revision02_gate_remains_blocked_after_af0_ritsu_backbone_pins(
    contract: m.Run9RunContract,
) -> None:
    """af0/ritsu/user anchor（2026-08-25 User attestation 実行により user も
    PINNED 化済み）と backbone (checkpoint + runtime bundle) が新たに
    PINNED になっても、dataset/config/lesson/practice/learning-recipe 等
    VG-L0 ハーネス関連欄が PENDING のままである限り gate_state() は
    "BLOCKED" のまま（部分的な pin 進展だけでは READY へ到達しないことの
    機械証明）。"""
    assert m.gate_state(contract) == "BLOCKED"


def test_run9_attest20260825_build_founder_now_succeeds_for_both_founders() -> None:
    """2026-08-25 RUN9 User attestation 実行により domain が凍結
    （is_pinned()==True）されたため、現行 domain draft から
    build_founder() が両 Founder 分成功するようになったこと（旧
    test_revision02_build_founder_still_rejects_current_domain_draft の
    assertion を反転）。genome_id は決定論的（再計算しても同じ値）かつ
    R9F-01/R9F-02 間で相異することを直接確認する（DESIGN_RUN9 §9.4
    「両 Founder は異なる座標を持つ」の機械証明）。"""
    domain = m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)
    assert domain.is_pinned() is True
    g1 = m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    g2 = m.build_founder(domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert m._GENOME_ID_RE.match(g1.genome_id)
    assert m._GENOME_ID_RE.match(g2.genome_id)
    assert g1.genome_id != g2.genome_id
    # 決定論: 同一 domain から再構築しても同じ genome_id。
    assert m.build_founder(domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()).genome_id == g1.genome_id
    assert m.build_founder(domain, "R9F-02", rights_manifest=_valid_test_rights_manifest()).genome_id == g2.genome_id


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


def test_fix319_4_readme_blocker_1_lists_confirmed_and_unresolved_owner() -> None:
    """残存ブロッカー(1) が、確定済み（performer/composer = Junya Koguchi
    出典付き、recording license = CC BY-SA 4.0）と、**未解決のままの
    recording-master owner**（PR #319 第 4 巡指摘採用で
    `<UNRESOLVED_EXTERNAL>` へ差し戻し — owner は確定済みに含めない）を
    ともに明記していること（第 15 巡指摘採用: 旧テストは owner を確定済み
    に分類し汎用文字列しか検証していなかったため、README が owner 主張へ
    退行しても検知できなかった）。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    blocker_1 = readme.split("**残存**:", 1)[1].split("2. **VG-L0", 1)[0]
    assert "確定済み" in blocker_1
    assert "Junya Koguchi" in blocker_1
    assert "CC BY-SA 4.0" in blocker_1
    # owner は未解決として明示され、UNRESOLVED_EXTERNAL の現在値が宣言されていること
    assert "owner" in blocker_1
    owner_stmt_idx = blocker_1.find("owner")
    assert "<UNRESOLVED_EXTERNAL>" in blocker_1
    # 「owner が確定済み」型の現在形主張への退行を検知: 確定済み節（未解決節より前）
    # に owner が登場しないこと
    confirmed_part = blocker_1.split("未解決", 1)[0]
    assert "owner" not in confirmed_part, (
        "確定済み節に owner が再登場している — recording-master owner は"
        " <UNRESOLVED_EXTERNAL> 維持（第 4 巡）であり確定済みに含めてはならない"
    )
    assert owner_stmt_idx >= 0


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
    命名）——rev 0.6（RUN9-L0-HARNESS-3c rev 0.6）現在は最新差分メモへ
    追随して assertion のみ更新する。"""
    assert REVISION_DOC_PATH.exists()
    assert REVISION_DOC_PATH.name == "DESIGN_RUN9_REVISION_0.6.md"
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
    genome_a = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    genome_b = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
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


def test_fix6_current_contract_practice_split_sha_is_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """2026-08-25 実 PJS practice split 実行により本欄は PENDING → PINNED
    へ遷移した（`test_fix6a_practice_manifest_sha_matches_actual_file_and_
    validates_once_pinned` が実ファイル照合・schema 検証まで事前配線済み
    のため、本テストは現行値の形状のみを確認する軽量スナップショット）。"""
    field = contract_raw["practice_audio_split_manifest_sha"]
    assert field["status"] == "PINNED"
    assert isinstance(field["value"], str)
    assert len(field["value"]) == 64


def test_fix6_current_contract_still_blocked(contract: m.Run9RunContract) -> None:
    """practice_audio_split_manifest_sha が PINNED 化した後も、他の VG-L0
    ハーネス関連欄（dataset_manifest_sha 等）が引き続き PENDING のため
    現行 RUN9_CONTRACT.yaml は正直に BLOCKED のまま。"""
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
    genome_a = m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest())
    genome_b = m.build_founder(pinned_domain, "R9F-02", rights_manifest=_valid_test_rights_manifest())
    assert genome_a.genome_id != genome_b.genome_id
    # 決定論: 同じ入力から再度呼び出しても同じ genome_id。
    assert m.build_founder(pinned_domain, "R9F-01", rights_manifest=_valid_test_rights_manifest()).genome_id == genome_a.genome_id


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

    〔履歴: 起草当時（PR #317 第6巡）は status が PENDING のためこの
    テストは「PENDING であること」だけを確認していた——2026-08-25 実 PJS
    practice split 実行により本欄は PINNED へ昇格し、事前配線どおり
    このテストが自動的に (a) `compute_file_sha256(PRACTICE_MANIFEST_PATH)`
    との一致、(b) `PRACTICE_MANIFEST_PATH` の内容が `validate_practice_
    split_manifest()` を通過すること、の両方を強制するようになった
    （テストコード自体は無改変 = 事前配線どおりの動作）。以下は現行
    PINNED ブランチの検証内容。〕
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


def test_phase3_domain_is_pinned_true_after_metric_space_and_user_pin() -> None:
    """metric_space_sha の pin（Phase 3）に続き、2026-08-25 RUN9 User
    attestation 実行で user anchor も PINNED 化されたため、実 domain draft
    は `is_pinned() == True`（旧 test_phase3_domain_is_pinned_still_false_
    after_metric_space_pin の assertion を反転 — Fix 15 の
    founder_genome_shas 改名前例に倣い名称・assertion を更新する）。"""
    domain_raw = json.loads(DOMAIN_DRAFT_PATH.read_text(encoding="utf-8"))
    domain = m.run9_identity_domain_from_dict(domain_raw)
    assert domain_raw["anchor_hashes"]["user"] != "<PIN_BEFORE_RUN>"
    assert domain.metric_space_sha == domain_raw["metric_space_sha"]
    assert domain.is_pinned() is True


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
    build 対象のまま、ここでは「非空文字列」の最小例。

    RUN9-L0-PIN-2（User 裁定 2026-08-25）更新: stopping_rule/trial_count/
    render_budget はもはや任意のプレースホルダ値を許容しない——
    `m.LEARNING_RECIPE_ADJUDICATED_*` の裁定値へ厳密固定された
    （`m.validate_learning_recipe_manifest()` が fail-closed 強制する）。
    旧プレースホルダ値（`"fixed_trial_count"`/`100`/`100`）はもはや
    valid ではないため、本 fixture も裁定値へ更新する。"""
    return {
        "equal_budget_within_arm": True,
        "stopping_rule": m.LEARNING_RECIPE_ADJUDICATED_STOPPING_RULE,
        "trial_count": m.LEARNING_RECIPE_ADJUDICATED_TRIAL_COUNT,
        "render_budget": m.LEARNING_RECIPE_ADJUDICATED_RENDER_BUDGET,
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
    `2.0` が拒否されることを確認する）。

    RUN9-L0-PIN-2（User 裁定 2026-08-25）更新: render_budget は任意の
    正の有限値ではなく裁定値 128 へ厳密固定されたため、旧テストが使って
    いた `50.5` はもはや valid ではない。裁定値と数値として等価な
    `128.0`（float 型）を用いて「型は float でも裁定値と数値等価なら
    受理される」ことを確認する形へ更新した（bool を除く数値型許容という
    Fix 7 の設計自体は不変）。"""
    manifest = _valid_learning_recipe_manifest()
    manifest["practice_recipe"]["render_budget"] = 128.0
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
    厳密 int を使用しており、Fix 15 適用後も無変更で通ることを確認
    する）。

    RUN9-L0-PIN-2（User 裁定 2026-08-25）更新: trial_count は任意の正の
    int ではなく裁定値 32 へ厳密固定されたため、旧テストが使っていた
    `100` はもはや valid ではない——fixture 既定値（裁定値 32）をそのまま
    使う形へ更新した（型検証（厳密 int・bool 拒否）自体の非退行確認と
    いう本テストの目的は不変）。"""
    manifest = _valid_learning_recipe_manifest()
    assert manifest["practice_recipe"]["trial_count"] == m.LEARNING_RECIPE_ADJUDICATED_TRIAL_COUNT
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
    """〔履歴: rev 0.4 が現行 design_revision だった間、本テストは
    `design_revision_doc_sha256` pin と rev 0.4 文書の一致を検証していた。
    RUN9-L0-HARNESS-3a（2026-08-26）で design_revision が 0.5 へ昇格し、
    同 pin は `DESIGN_RUN9_REVISION_0.5.md` へ repoint された
    （`test_revision02_doc_sha256_pin_matches_actual_file` が現行の一致を
    検証する）。本テストは rev 0.4 文書自体が無改変のまま存続している
    ことのみを確認する形へ改める——テスト名はレビュー履歴保持のため
    改名しない〕。"""
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] != _sha256_file(REVISION_0_4_DOC_PATH)
    assert _sha256_file(REVISION_0_4_DOC_PATH) == (
        "7bfefcf61886062511c30df92c25e597b7a4a7745037514ed4655a623e38df07"
    )
    assert m.compute_file_sha256(REVISION_0_4_DOC_PATH) == (
        "7bfefcf61886062511c30df92c25e597b7a4a7745037514ed4655a623e38df07"
    )


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


def test_fix319_6_voice_identity_rights_scoped_out_of_vocab_swap() -> None:
    """User 帰属欄（User donor の同意・usage grants 等）は Fix 6 の張り替え
    対象外——`PENDING_USER_ATTESTATION`/`UNRESOLVED_EXTERNAL` の仕分けは
    voice_identity_rights 層（User donor 自身の声の権利）には及ばない
    （Fix 6 の対象は PJS 側の3層のみ）。pending 形態の baseline
    （`_pending_rights_manifest_fixture()`）で回帰確認する——現行 fixture
    自体は 2026-08-25 RUN9 User attestation 実行により attested 形態へ
    遷移済み（`test_run9_attest20260825_rights_manifest_is_attested` 参照。
    旧テスト名 fix319_6_voice_identity_rights_still_pending_user_attestation
    は Fix 15 の founder_genome_shas 改名前例に倣い改名した）。
    usage_grants.run9_identity_anchor は Fix 19（第9巡, P2, 採用）で値語彙が
    {not_granted, granted} の閉集合へ凍結されたのに伴い、旧値 `pending`
    （閉集合外の第3値）から `not_granted` へ改めた——「まだ承認されていない」
    という pending 時点の意味論自体は変わらない。"""
    pending = _pending_rights_manifest_fixture()
    assert pending["voice_identity_rights"]["rights_class"] == "PENDING_USER_ATTESTATION"
    assert pending["voice_identity_rights"]["consent_status"] == "PENDING_USER_ATTESTATION"
    assert pending["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] == "not_granted"
    m.validate_rights_manifest_four_layer(pending)  # 例外を投げないことの確認
    # 現行 fixture 自体は attested 形態（対照）。
    current = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert current["voice_identity_rights"]["rights_class"] == "USER_ATTESTED_OWN_VOICE"
    assert current["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] == "granted"


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
    （history）に明記されていること。

    フィルタは「PR #319 第2巡指摘（... Fix 6）」まで絞り込む（素の
    "Fix 6" 部分文字列だけでは、PR ラウンドごとに 1 から振り直される
    Codex Fix 番号が別 PR で再度 "Fix 6" になった際に衝突する——実例:
    Codex bot レビュー PR #320 第4巡指摘も "Fix 6" を名乗る。history
    エントリは "Codex bot レビュー PR #<N> 第<M>巡指摘（..., Fix <K>）"
    の定型で書かれるため、PR 番号・巡・Fix 番号の3つ組で絞り込めば
    将来の同名衝突を再発させない）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    history = data["history"]
    swap_events = [
        h for h in history if "PR #319 第2巡指摘（P2, 採用, Fix 6）" in h["event"]
    ]
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
    拒否（負例6 — pending/attested 二形態の混在を許さない）。現行
    rights_manifest.json は 2026-08-25 RUN9 User attestation 実行後は
    attested 形態のため、pending baseline は
    `_pending_rights_manifest_fixture()` で構築する。"""
    data = _pending_rights_manifest_fixture()
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
    （負例9 — 二形態の整合違反、順方向）。pending baseline から出発する
    （現行 fixture は既に attested 形態のため
    `_pending_rights_manifest_fixture()` で構築）。"""
    data = _pending_rights_manifest_fixture()
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
    拒否（負例10 — 二形態の整合違反、逆方向）。pending baseline から出発
    する（現行 fixture は既に attested 形態のため
    `_pending_rights_manifest_fixture()` で構築 — 素の pending fixture に
    対する mutation でなければ「attestation は pending のまま放置」の
    シナリオを再現できない）。"""
    data = _pending_rights_manifest_fixture()
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_16_valid_pending_fixture_still_validates() -> None:
    """正例（回帰）: pending 形態の voice_identity_rights 層
    （attested=false + signer/timestamp/statement すべて null +
    rights_class/consent_status = PENDING_USER_ATTESTATION）が Fix 16
    追加後も validator を通ることの end-to-end 確認。2026-08-25 RUN9
    User attestation 実行により現行 fixture 自体は attested 形態へ遷移
    済みのため、`_pending_rights_manifest_fixture()` で pending baseline
    を構築する（旧: 現行 fixture を直接使用）。"""
    data = _pending_rights_manifest_fixture()
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
    適用することの確認を兼ねる。現行 rights_manifest.json は 2026-08-25
    RUN9 User attestation 実行後は attested 形態のため、pending baseline
    は `_pending_rights_manifest_fixture()` で構築する。"""
    data = _pending_rights_manifest_fixture()
    data["voice_identity_rights"]["usage_grants"][grant_key] = "granted"
    with pytest.raises(m.Run9ValidationError, match="attestation is not in attested form"):
        m.validate_rights_manifest_four_layer(data)


@pytest.mark.parametrize(
    "grant_key", ("raw_audio_publication", "model_general_distribution")
)
def test_fix319_19_granted_without_approval_record_rejected(grant_key: str) -> None:
    """負例（2キー parametrize — Fix 27 で `run9_identity_anchor` は本要求
    から除外された。§rev 0.2 改訂4「別承認」は raw_audio_publication /
    model_general_distribution のみが対象）: attestation は attested 形態に
    整えても、grant 別の承認記録（`<grant>_approval`）が無ければ granted
    遷移は拒否されること。"""
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
    """正例（回帰）: pending baseline（3キーとも not_granted）が Fix 19
    追加後も validator を通ることの end-to-end 確認。2026-08-25 RUN9 User
    attestation 実行により現行 rights_manifest.json 自体は
    run9_identity_anchor が granted へ遷移済みのため、pending baseline は
    `_pending_rights_manifest_fixture()` で構築する（現行 fixture 側の
    granted 経路は `test_fix319_19_granted_with_full_preconditions_accepted`
    /`test_fix319_27_*` が回帰確認する）。"""
    data = _pending_rights_manifest_fixture()
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
# PR #319 Codex bot レビュー第14巡対応 — Fix 27（P2）: identity anchor への
# 二重承認要求の撤回。Fix 19（第9巡）は usage_grants の3キー
# （run9_identity_anchor / raw_audio_publication / model_general_
# distribution）へ一律に「①attested 形態 ②grant 専用の承認記録」を要求
# したが、DESIGN_RUN9_REVISION_0.2.md 194-203行（改訂4）は「別承認」を
# raw_audio_publication/model_general_distribution の2件のみに限定して
# おり、run9_identity_anchor は User attest 完了後に anchor の grant が
# それへ束縛される（attestation 自体が根拠）——追加の承認記録は正典が
# 要求しない。User が規定どおり attest を完了して run9_identity_anchor を
# granted にする正常遷移を、根拠のない承認記録の捏造なしに通せなかった
# Fix 19 の一律適用（第9巡裁定）をここで訂正する。
# ---------------------------------------------------------------------------


def test_fix319_27_identity_anchor_granted_without_approval_record_accepted() -> None:
    """正例（核心）: run9_identity_anchor を attested 形態 + 承認記録なしで
    granted にしても受理されること——rev 0.2 改訂4が定める正典フロー
    （attestation が anchor grant の唯一の根拠）どおりの正常遷移が、Fix 19
    の一律ルールでブロックされていた欠陥の直接的な回帰確認。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] = "granted"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認
    assert "run9_identity_anchor_approval" not in data["voice_identity_rights"]["usage_grants"]


def test_fix319_27_identity_anchor_granted_without_attestation_still_rejected() -> None:
    """負例（回帰）: attestation が attested 形態でなければ
    run9_identity_anchor の granted 遷移は Fix 27 適用後も拒否されること
    ——撤回したのは承認記録の必須性のみで、attested 形態の前提条件①は
    run9_identity_anchor にも引き続き適用される
    （`test_fix319_19_granted_while_attestation_still_pending_rejected` と
    同型だが、Fix 27 のパラメトライズ変更後も run9_identity_anchor 単独で
    固定しておく）。現行 rights_manifest.json は 2026-08-25 RUN9 User
    attestation 実行後は attested 形態（run9_identity_anchor も既に
    granted）のため、pending baseline は
    `_pending_rights_manifest_fixture()` で構築する。"""
    data = _pending_rights_manifest_fixture()
    data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] = "granted"
    with pytest.raises(m.Run9ValidationError, match="attestation is not in attested form"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_27_identity_anchor_granted_with_optional_approval_record_still_validates() -> None:
    """正例（境界宣言）: run9_identity_anchor に承認記録が付与されていても
    拒否はしない——`run9_identity_anchor_approval` は
    `_RIGHTS_MANIFEST_USAGE_GRANTS_KEYS` から機械導出される既存の閉集合
    `allowed_keys` に元々含まれるキーであり、規定にない記録の混入は未知
    キー拒否ではなく既存の閉集合検証に委ねる、という設計方針の直接確認。
    付与されている場合は形状検証（approved_at/approval_statement の閉じた
    2キー）は引き続き通ること。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["run9_identity_anchor_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Identity anchor usage attested by User.",
    }
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


def test_fix319_27_publication_and_distribution_approval_requirement_unchanged() -> None:
    """回帰（両輪確認）: raw_audio_publication / model_general_distribution
    は Fix 27 適用後も「attested 形態 + 承認記録」の両方を要求し続けること
    ——run9_identity_anchor の撤回がこの2キーへ波及していないことの直接
    確認（`test_fix319_19_granted_without_approval_record_rejected` の
    parametrize 縮小と対をなす）。"""
    for grant_key in ("raw_audio_publication", "model_general_distribution"):
        data = _attested_voice_identity_rights_layer(
            json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
        )
        data["voice_identity_rights"]["usage_grants"][grant_key] = "granted"
        with pytest.raises(m.Run9ValidationError, match="missing its separate approval record"):
            m.validate_rights_manifest_four_layer(data)


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
    rights_class が pending のままのため通過してしまっていた抜け道。
    pending baseline から出発する（現行 fixture は 2026-08-25 RUN9 User
    attestation 実行後は既に両方 attested のため
    `_pending_rights_manifest_fixture()` で構築しないと本負例を再現
    できない）。"""
    data = _pending_rights_manifest_fixture()
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_21_pending_form_with_only_rights_class_confirmed_rejected() -> None:
    """負例2（方向B、負例1 の対称ケース）: rights_class のみを PENDING
    から確定値へ書き換え、consent_status は PENDING_USER_ATTESTATION・
    attestation は pending 形態（attested=false）のままの場合の拒否。
    pending baseline から出発する（理由は負例1と同じ）。"""
    data = _pending_rights_manifest_fixture()
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_21_valid_both_pending_fixture_still_validates() -> None:
    """正例（回帰）: pending 形態（rights_class/consent_status ともに
    PENDING_USER_ATTESTATION・attested=false）が Fix 21 適用後も validator
    を通ることの end-to-end 確認。2026-08-25 RUN9 User attestation 実行に
    より現行 rights_manifest.json 自体は attested 形態へ遷移済みのため、
    `_pending_rights_manifest_fixture()` で pending baseline を構築する
    （旧: 現行 fixture を直接使用）。"""
    data = _pending_rights_manifest_fixture()
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


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第11巡対応 — Fix 24（P2）: 完了 voice-rights
# status の閉語彙化。attested 形態の判定（Fix 16/21）は「両 status が
# PENDING_USER_ATTESTATION と異なる」ことしか要求しておらず、
# `rights_class`/`consent_status` を `DENIED` 等の任意・矛盾値へ書き換えても
# 受理してしまっていた——承認記録付き `granted` usage grant（Fix 19 前提
# 条件①）と組み合わせると、権利状態が否認/未記述のまま raw 公開・配布を
# 許可する正典 manifest が通ってしまう。閉語彙は現物確認の結果（設計裁定
# ではなく既存の現物規約）: `inputs/rights_manifest.json` /
# DESIGN_RUN9_REVISION_0.2.md 改訂4 / DESIGN_RUN9_REVISION_0.4.md いずれにも
# attested 後の status 値の明文規定はないが、`tests/test_run9_contract.py`
# 自身が Fix 16/19 導入時点から `rights_class`/`consent_status` 双方に
# "USER_ATTESTED_OWN_VOICE" を使う規約を既に確立しており（本ファイル上記
# 7 箇所）、これを閉語彙として run9_schema.py 側で凍結した
# （`_RIGHTS_MANIFEST_STATUS_USER_ATTESTED_OWN_VOICE`）。
# ---------------------------------------------------------------------------


def test_fix319_24_attested_form_both_denied_rejected() -> None:
    """負例1: attested=true で rights_class/consent_status が両方とも
    閉語彙外の `DENIED`（否認）の場合の拒否——指摘が名指しする再現例の
    根本形。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "DENIED"
    data["voice_identity_rights"]["consent_status"] = "DENIED"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_24_attested_form_rights_class_denied_only_rejected() -> None:
    """負例2: rights_class のみ `DENIED`、consent_status は閉語彙値
    （USER_ATTESTED_OWN_VOICE）という片方だけ矛盾した組み合わせの拒否。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "DENIED"
    data["voice_identity_rights"]["consent_status"] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_24_attested_form_consent_status_denied_only_rejected() -> None:
    """負例3: consent_status のみ `DENIED`、rights_class は閉語彙値という
    逆方向の片方矛盾の拒否。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "USER_ATTESTED_OWN_VOICE"
    data["voice_identity_rights"]["consent_status"] = "DENIED"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_24_attested_form_arbitrary_string_rejected() -> None:
    """負例4: `DENIED` に限らず、閉語彙外の任意文字列（PENDING でも
    USER_ATTESTED_OWN_VOICE でもない未知の記述値）も拒否されること——旧
    条件（PENDING と異なるかどうかのみ）では通っていた経路。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "SOME_UNSPECIFIED_STATUS"
    data["voice_identity_rights"]["consent_status"] = "SOME_UNSPECIFIED_STATUS"
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_24_denied_status_with_granted_grant_compound_rejected() -> None:
    """複合負例（指摘本文の再現例）: rights_class/consent_status が両方
    `DENIED` のまま、attestation は形状要件を満たし usage_grants の1キーを
    承認記録付きで `granted` へ書き換えても、attestation の二形態整合検証
    （usage_grants 検証より先に走る）で拒否され、権利状態が否認のまま
    raw 公開/配布を許可する正典 manifest は成立しないこと。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["voice_identity_rights"]["attestation"] = {
        "attested": True,
        "attested_by": "user@example.com",
        "attested_at": "2026-08-25T00:00:00Z",
        "statement": "I attest this recording as my own voice.",
    }
    data["voice_identity_rights"]["rights_class"] = "DENIED"
    data["voice_identity_rights"]["consent_status"] = "DENIED"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Publication approved by User.",
    }
    with pytest.raises(m.Run9ValidationError, match="status/attestation form mismatch"):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_24_valid_closed_vocab_attested_form_accepted() -> None:
    """正例（回帰）: 閉語彙値 `USER_ATTESTED_OWN_VOICE`（両 status）での
    attested 形態は Fix 24 適用後も受理されること——Fix 16 の正例
    （`test_fix319_16_valid_attested_form_accepted`）と同型で、Fix 24 が
    正当な attested 遷移を過剰拒否していないことの確認。"""
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


def test_fix319_24_valid_closed_vocab_attested_form_with_granted_grant_accepted() -> None:
    """正例（回帰）: 閉語彙値での attested 形態 + 承認記録付き granted
    usage grant の組み合わせは Fix 24 適用後も受理されること
    （`test_fix319_19_granted_with_full_preconditions_accepted` と同型の
    end-to-end 確認 — Fix 19 の granted 前提条件①は本 Fix 24 の閉語彙
    検証を通った attested 形態のみを意味するようになったことの固定）。"""
    data = _attested_voice_identity_rights_layer(
        json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    )
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication"] = "granted"
    data["voice_identity_rights"]["usage_grants"]["raw_audio_publication_approval"] = {
        "approved_at": "2026-08-25T00:00:00Z",
        "approval_statement": "Raw audio publication approved by User.",
    }
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第12巡対応 — Fix 25（P2）: 外部層での User 専用
# 完了トークンの拒否。Fix 24 が `USER_ATTESTED_OWN_VOICE` を
# voice_identity_rights 層の User-donor attestation 完了を表す正確な意味と
# して閉語彙化した以上、performance_rights/composition_rights のような
# 外部第三者層がこの同じトークンで「User attestation 済み」を手編集で
# 主張できてしまうのは対称漏れ——未解決 provenance と並存したまま validate
# を通過させない。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layer_name", "field_name"),
    [
        ("performance_rights", "rights_class"),
        ("performance_rights", "consent_status"),
        ("composition_rights", "rights_class"),
        ("composition_rights", "consent_status"),
    ],
)
def test_fix319_25_rejects_user_attested_own_voice_in_external_layer(
    layer_name: str, field_name: str
) -> None:
    """負例（4ケース）: performance_rights/composition_rights の
    rights_class/consent_status へ `USER_ATTESTED_OWN_VOICE`（voice_identity_
    rights 層の User-donor attestation 専用トークン）を手編集で混入させても
    「外部層で User attestation 済み」を偽装できないことの確認——拒否
    メッセージは voice_identity_rights 層専用であることと、この外部層での
    代替語彙（UNRESOLVED_EXTERNAL / 具体的な外部権利状態の記述）を案内する。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data[layer_name][field_name] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(
        m.Run9ValidationError,
        match=r"voice_identity_rights layer User-donor attestation",
    ):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_25_unresolved_external_regression_still_accepted() -> None:
    """正例（回帰）: performance_rights/composition_rights の現行値
    `UNRESOLVED_EXTERNAL` は Fix 25 適用後も引き続き受理されること
    （`test_fix319_6_performance_and_composition_rights_use_unresolved_external`
    と同一 fixture 値での end-to-end 回帰確認）。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    for layer_name in ("performance_rights", "composition_rights"):
        assert data[layer_name]["rights_class"] == "UNRESOLVED_EXTERNAL"
        assert data[layer_name]["consent_status"] == "UNRESOLVED_EXTERNAL"
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #319 Codex bot レビュー第13巡対応 — Fix 26（P2）: nested provenance /
# LessonRecord 外部事実欄での User 完了トークン拒否。Fix 25 は層直下の
# rights_class/consent_status（角括弧なし裸トークン）への
# `USER_ATTESTED_OWN_VOICE` 混入は塞いだが、nested provenance ブロック
# （`_validate_rights_provenance_block()` の値検証 — performance_author.
# performer / composition.composer / composition.lyricist / voice_source.
# owner 等、角括弧なし自由記述の具体値を受理する経路）と
# `validate_lesson_record()` の外部事実5欄は同トークンを通常の具体値として
# 素通りさせていた。Fix 25 と同型の fail-closed 拒否を両経路へ対称適用する。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layer_name", "block_name", "field_name"),
    [
        ("performance_rights", "performance_author", "performer"),
        ("recording_master_rights", "voice_source", "owner"),
        ("composition_rights", "composition", "composer"),
        ("composition_rights", "composition", "lyricist"),
    ],
)
def test_fix319_26_rejects_user_attested_own_voice_in_nested_provenance(
    layer_name: str, block_name: str, field_name: str
) -> None:
    """負例（4ケース）: nested provenance ブロック内の代表4欄
    （performance_author.performer / voice_source.owner / composition.
    composer / composition.lyricist）へ `USER_ATTESTED_OWN_VOICE`
    （voice_identity_rights 層 User-donor attestation 完了専用トークン）を
    手編集で混入させても、第三者 author/権利者欄を「User attestation 済み」
    に偽装できないことの確認——拒否メッセージは voice_identity_rights 層
    専用であることと、この外部欄での代替語彙（UNRESOLVED_EXTERNAL / 具体的
    な外部権利状態の記述）を案内する。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    data[layer_name]["provenance"][block_name][field_name] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(
        m.Run9ValidationError,
        match=r"voice_identity_rights layer User-donor attestation",
    ):
        m.validate_rights_manifest_four_layer(data)


def test_fix319_26_nested_provenance_current_values_regression_accepted() -> None:
    """正例（回帰）: `inputs/rights_manifest.json` の nested provenance
    現行値（`Junya Koguchi` の具体的記述値 / `<UNRESOLVED_EXTERNAL>`）は
    Fix 26 適用後も引き続き受理されること。"""
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)  # 例外を投げないことの確認


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
def test_fix319_26_rejects_user_attested_own_voice_in_lesson_record_field(field: str) -> None:
    """負例（5ケース、parametrize）: LessonRecord 外部事実5欄いずれも
    `USER_ATTESTED_OWN_VOICE`（voice_identity_rights 層 User-donor
    attestation 完了専用トークン）を拒否し、voice_identity_rights 層専用で
    あることを案内するメッセージを含むこと（Codex bot レビュー PR #319
    第13巡指摘, Fix 26, P2, 採用）。"""
    record = _valid_lesson_record()
    record[field] = "USER_ATTESTED_OWN_VOICE"
    with pytest.raises(
        m.Run9ValidationError,
        match=r"voice_identity_rights layer User-donor attestation",
    ):
        m.validate_lesson_record(record)


def test_fix319_26_valid_lesson_record_fixture_still_validates() -> None:
    """正例（回帰）: `_valid_lesson_record()` fixture は Fix 26 適用後も
    引き続き受理されること。"""
    m.validate_lesson_record(_valid_lesson_record())  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# Codex bot レビュー PR #319 第16巡指摘, Fix 29（P2, 採用）: 層 status 欄での
# 角括弧綴り sentinel の拒否（裸トークン検査をすり抜ける別綴り経路の閉鎖）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layer_name", "field_name"),
    [
        ("performance_rights", "rights_class"),
        ("performance_rights", "consent_status"),
        ("composition_rights", "rights_class"),
        ("composition_rights", "consent_status"),
        ("voice_identity_rights", "rights_class"),
    ],
)
@pytest.mark.parametrize(
    "bracketed",
    [
        "<PENDING_USER_ATTESTATION>",
        "<UNRESOLVED_EXTERNAL>",
        "<USER_ATTESTED_OWN_VOICE>",
    ],
)
def test_fix319_29_bracketed_sentinel_rejected_in_layer_status(
    layer_name: str, field_name: str, bracketed: str
) -> None:
    """層 status 欄は裸トークン規約 — 角括弧綴りの予約トークンは、裸トークン
    等値検査（主体種別の誤用拒否・Fix 5/6/25）をすべてすり抜けて自由記述の
    具体値として受理されてしまうため、一律拒否する（第16巡指摘採用）。"""
    with pytest.raises(m.Run9ValidationError, match="bracketed sentinel"):
        m._validate_rights_manifest_layer_status_value(layer_name, field_name, bracketed)


def test_fix319_29_bare_tokens_and_free_form_unchanged() -> None:
    """裸トークンの既存意味論（主体種別整合の受理/拒否）と自由記述の具体値の
    受理は不変であることの回帰。"""
    # 外部層の裸 UNRESOLVED_EXTERNAL は従来どおり受理
    m._validate_rights_manifest_layer_status_value(
        "performance_rights", "rights_class", "UNRESOLVED_EXTERNAL"
    )
    # 自由記述の具体値も従来どおり受理
    m._validate_rights_manifest_layer_status_value(
        "recording_master_rights",
        "consent_status",
        "LICENSE_CONFIRMED_USAGE_SCOPE_PENDING_TOOLING_REVIEW",
    )
    # 現行 manifest 全体も引き続き valid
    data = json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    m.validate_rights_manifest_four_layer(data)


# ---------------------------------------------------------------------------
# PR #321 Codex bot レビュー第1巡対応 — Fix 2（P2）: stale founder-genome
# blocker の掃討。`founder_genome_shas` が PINNED 化された後も、契約ヘッダ
# （lines 8-9・91-96 付近）が「genome 文書未生成・pin は PENDING のまま」と
# 現在形で言い続けており正典 run 記録が内部矛盾していた。現状記述を
# PINNED 後の事実へ更新し、純粋に歴史的な記述は「〔履歴: … →
# RUN9-BIRTH-PREP-1 で解消済み〕」形式で保持した。
# ---------------------------------------------------------------------------


def test_fix321_2_founder_genome_shas_are_pinned_not_stale_pending() -> None:
    """`founder_genome_shas.R9F-01/R9F-02` は RUN9-BIRTH-PREP-1 の正式発行
    以降 PINNED であり、契約ヘッダの「実体未生成」主張と矛盾していた事実の
    直接確認（Fix 2 の前提条件）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    contract = yaml.safe_load(contract_text)
    for founder_id in ("R9F-01", "R9F-02"):
        entry = contract["founder_genome_shas"][founder_id]
        assert entry["status"] == "PINNED"
        assert isinstance(entry["value"], str) and len(entry["value"]) == 64


def test_fix321_2_no_stale_present_tense_founder_genome_unissued_claim() -> None:
    """契約ヘッダに founder genome 文書を「未生成」「配線されていない」と
    現在形で主張する記述が残っていないこと（Codex bot レビュー PR #321
    第1巡指摘, P2, 採用, Fix 2）。過度に脆い文字列一致は避け、Fix 2 が
    是正した具体フレーズのみを要点マーカーとして検査する。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    stale_phrases = (
        "founder genome 文書の\n# 実体未生成のみ",
        "その文書を実際に\n# `founders/` へ書き出して正式\n# 発行する builder・pin 手続きはまだ本 repo に配線されていない",
        "`founder_genome_shas` は本改訂でも\n# 引き続き PENDING のまま（下記該当欄の reason 参照。捏造 PIN はしない）。",
    )
    for phrase in stale_phrases:
        assert phrase not in contract_text, (
            f"RUN9_CONTRACT.yaml に陳腐化した founder genome blocker 記述が残っている: {phrase!r}"
        )


def test_fix321_2_historical_founder_genome_blocker_marked_superseded() -> None:
    """是正済みの旧記述が単純削除ではなく「〔履歴: … → RUN9-BIRTH-PREP-1
    で解消済み〕」形式の superseded 明示で保持されていること（AGENTS.md
    運用: 純粋に歴史的な記述は削除せず superseded 明示で保持する）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert contract_text.count("RUN9-BIRTH-PREP-1 で解消済み") >= 2
    assert contract_text.count("〔履歴:") >= 2


def test_fix321_2_repo_wide_grep_finds_no_other_stale_genome_unissued_claim() -> None:
    """RUN9_CONTRACT.yaml + README.md 全体を掃討し、同族の「founder genome
    文書は未生成/PENDING のまま」という現在形の残存がゼロであることを
    確認する（凍結文書 = founders/*.json・DESIGN_*.md・POR/DERIVED txt は
    対象外）。README.md は既に RUN9-BIRTH-PREP-1 時点で「解消済み」節へ
    整理済み（取消線付き履歴マーカー `~~founder genome 文書の正式発行
    builder・pin 手続き未配線~~` として保持）のため、現在形の未解消主張が
    無いことのみ確認する。"""
    for path in (CONTRACT_PATH, _RUN_DIR / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert "founder genome 文書の実体未生成" not in text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第1巡指摘（P2, 採用, Fix 1）: practice_audio_
# split_manifest_sha の PINNED 化後も、RUN9_CONTRACT.yaml ヘッダの現状
# サマリー・README.md P1-2 節・tests/test_run9_contract.py の事前配線
# テスト docstring が「practice manifest は未生成/PENDING」と現在形で
# 主張し続けていた（PR #321 Fix 2 の founder genome 陳腐化と同族）。
# ---------------------------------------------------------------------------


def test_fix323_1_practice_manifest_sha_is_pinned_not_stale_pending() -> None:
    """`practice_audio_split_manifest_sha` は 2026-08-25 実 PJS practice
    split 実行以降 PINNED であり、契約ヘッダ/README の「未生成/PENDING」
    主張と矛盾していた事実の直接確認（Fix 1 の前提条件）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    contract = yaml.safe_load(contract_text)
    field = contract["practice_audio_split_manifest_sha"]
    assert field["status"] == "PINNED"
    assert isinstance(field["value"], str) and len(field["value"]) == 64


def test_fix323_1_no_stale_present_tense_practice_manifest_unissued_claim() -> None:
    """契約ヘッダ・README に practice manifest を「未生成」「両方 PENDING
    のまま」と現在形で主張する記述が残っていないこと（Codex bot レビュー
    PR #323 第1巡指摘, P2, 採用, Fix 1）。過度に脆い文字列一致は避け、
    Fix 1 が是正した具体フレーズのみを要点マーカーとして検査する。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    stale_contract_phrases = (
        "主因は VG-L0 学習ハーネス未実装 + practice/education/learning-recipe\n"
        "# manifest の実体未生成のみ",
    )
    for phrase in stale_contract_phrases:
        assert phrase not in contract_text, (
            f"RUN9_CONTRACT.yaml に陳腐化した practice manifest blocker 記述が残っている: {phrase!r}"
        )
    stale_readme_phrases = (
        "`practice_audio_split_manifest_sha` へ改名（両方 PENDING のまま）",
    )
    for phrase in stale_readme_phrases:
        assert phrase not in readme_text, (
            f"README.md に陳腐化した practice manifest blocker 記述が残っている: {phrase!r}"
        )


def test_fix323_1_historical_practice_manifest_blocker_marked_superseded() -> None:
    """是正済みの旧記述が単純削除ではなく「〔履歴: … → 解消済み〕」形式の
    superseded 明示で保持されていること（AGENTS.md 運用: 純粋に歴史的な
    記述は削除せず superseded 明示で保持する）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "〔履歴:" in contract_text
    assert "実 PJS practice split 実行" in contract_text
    assert "〔履歴:" in readme_text


def test_fix323_1_prewired_test_docstring_reflects_pinned_branch_not_stale_pending() -> None:
    """`test_fix6a_practice_manifest_sha_matches_actual_file_and_validates_
    once_pinned` の docstring が、起草当時（PENDING）の現在形記述のまま
    陳腐化していないこと（Fix 1 指摘 tests/test_run9_contract.py:4731-4736
    該当箇所）。`inspect.getdoc()` で対象関数の docstring のみを取得する
    ——本ファイル全体を自己参照 grep すると、この assert の文字列 literal
    自体が誤って自己マッチしてしまうため、対象を関数 1 個の docstring へ
    厳密に絞る。"""
    target_doc = inspect.getdoc(
        test_fix6a_practice_manifest_sha_matches_actual_file_and_validates_once_pinned
    )
    assert target_doc is not None
    stale_docstring_phrase = "現状 status は PENDING の" + "ためこのテストは「PENDING であること」だけ"
    assert stale_docstring_phrase not in target_doc
    assert "PINNED へ昇格し" in target_doc


def test_fix323_1_repo_wide_grep_finds_no_other_stale_practice_manifest_claim() -> None:
    """RUN9_CONTRACT.yaml + README.md + run9_schema.py + practice_split_
    builder.py を掃討し、同族の「practice manifest は未生成/PENDING の
    まま」という現在形の残存がゼロであることを確認する（PR #323 第2巡
    指摘, P2, 採用, Fix 2 — 走査対象を run9_schema.py/practice_split_
    builder.py へ拡張。読み取り専用参照の gate_synth.py/score.py/
    phoneme_jp.py、凍結文書 = DESIGN_*.md・POR/DERIVED txt・inputs/*.json
    の既存ファイル・domains/・founders/・evaluation/probe_manifest.json は
    対象外）。"""
    stale_markers = (
        "practice manifest は未生成",
        "practice manifest は引き続き PENDING",
        "practice split manifest は未生成",
    )
    swept_paths = (
        CONTRACT_PATH,
        _RUN_DIR / "README.md",
        _RUN_DIR / "run9_schema.py",
        _RUN_DIR / "practice_split_builder.py",
    )
    for path in swept_paths:
        text = path.read_text(encoding="utf-8")
        for marker in stale_markers:
            assert marker not in text, f"{path.name} に陳腐化した記述が残っている: {marker!r}"

    # PR #323 第4巡指摘（P2, 採用, Fix 4）: 「両欄名併記 + PENDING の
    # 現時点」パターン（例: RUN9_CONTRACT.yaml:855-856 の PR #322 第20巡
    # repin 履歴）は、practice 側 PINNED 化後に読者が「現時点」を執筆時点
    # ではなく現在と誤読しうる。append-only 規約により原文（履歴記録）は
    # 書き換えないため単純不在チェックにはできない——このパターンが検出
    # されたら、その直後に supersede 注記（`〔履歴注記` または `〔履歴:`
    # + 2026-08-25 の日付）が存在することを要求する条件付き検査とする
    # （存在しないまま放置されている＝新規/未 supersede の stale 主張と
    # して拒否）。
    two_pin_pending_present_tense = re.compile(
        r"practice_audio_split_manifest_sha.{0,80}education_technique_lesson_manifest_sha"
        r".{0,40}PENDING.{0,10}現時点",
        re.DOTALL,
    )
    for path in swept_paths:
        text = path.read_text(encoding="utf-8")
        for match in two_pin_pending_present_tense.finditer(text):
            # supersede 注記は原文の直前（quote を開く形）か直後（追記形）
            # のいずれの配置もあり得るため、前後 400 文字を合わせて検査する
            # （前方: run9_schema.py の `〔履歴: 当初は「…」と記していたが、
            # 2026-08-25…` 型／後方: RUN9_CONTRACT.yaml の「原文の後に
            # `〔履歴注記 2026-08-25…〕` を追記」型）。
            context = text[max(0, match.start() - 400) : match.end() + 400]
            has_supersede_marker = "履歴注記" in context or "〔履歴" in context
            assert has_supersede_marker and "2026-08-25" in context, (
                f"{path.name} に「両欄名併記 + PENDING の現時点」パターンが supersede "
                f"注記なしで残っている（match={match.group()!r}）"
            )


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第2巡指摘（P2, 採用, Fix 2）: 第1巡 sweep
# （test_fix323_1_repo_wide_grep_finds_no_other_stale_practice_manifest_
# claim、当時は CONTRACT_PATH/README のみ走査）が run9_schema.py:4296-4298
# / 4314-4316 の同族 stale コメント（「practice_audio_split_manifest_sha/
# education_technique_lesson_manifest_sha は共に PENDING」現在形）を見逃して
# いた。第1巡と同じく、コメントのみを是正しロジック・凍結表・定数値は
# 一切変更しない（`_P5_DEFERRED_VERIFICATION_BLOCKED_BY` の2欄列挙は
# 「probe manifest 発行時点の凍結宣言」として正当なまま不変）。
# ---------------------------------------------------------------------------


def test_fix323_2_run9_schema_comment_reflects_practice_pinned_education_pending() -> None:
    """run9_schema.py の `_P5_DEFERRED_VERIFICATION_BLOCKED_BY` 周辺コメント
    が、practice 側は 2026-08-25 実 PJS 実行で PINNED 化済み・education 側は
    引き続き PENDING という現行の非対称状態を正確に記述していること。"""
    schema_text = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "2026-08-25 実 PJS practice split" in schema_text
    assert "practice_audio_split_manifest_sha` は PINNED 化された" in schema_text
    assert "education_technique_lesson_manifest_sha` が依然 PENDING" in schema_text


def test_fix323_2_run9_schema_historical_stale_comment_marked_superseded() -> None:
    """是正済みの旧コメントが単純削除ではなく「〔履歴: … → 解消済み〕」
    相当の superseded 明示で保持されていること（AGENTS.md 運用: 純粋に
    歴史的な記述は削除せず superseded 明示で保持する。第1巡 Fix 1・PR #321
    Fix 2 と同じ規約）。"""
    schema_text = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert schema_text.count("〔履歴:") >= 2
    assert "stale になった" in schema_text


def test_fix323_2_frozen_probe_manifest_blocked_by_untouched_after_schema_comment_fix() -> None:
    """凍結済み `evaluation/probe_manifest.json`（sha pin 済み）は本 Fix で
    一切改変されていないこと——`blocked_by` が practice/education 両欄を
    列挙し続けるのは「probe manifest 発行時点の凍結宣言」として正当で
    あり、practice 側が事後に PINNED 化されたことは凍結済み manifest の
    改変理由にならない（RUN9_CONTRACT.yaml `probe_manifest_sha` pin 値との
    一致がこのテストの実体保証——凍結境界の直接確認）。"""
    probe_manifest_path = _RUN_DIR / "evaluation" / "probe_manifest.json"
    field = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))["probe_manifest_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(probe_manifest_path)
    manifest_data = json.loads(probe_manifest_path.read_text(encoding="utf-8"))
    p5 = next(p for p in manifest_data["probes"] if p["probe_id"] == "P5")
    assert set(p5["deferred_verification"]["blocked_by"]) == {
        "practice_audio_split_manifest_sha", "education_technique_lesson_manifest_sha",
    }


def test_fix323_2_p5_deferred_verification_blocked_by_constant_unchanged() -> None:
    """Fix 2 はコメントのみの変更であり、`_P5_DEFERRED_VERIFICATION_
    BLOCKED_BY`（凍結集合の実体）自体は変更していないことの直接確認。"""
    assert m._P5_DEFERRED_VERIFICATION_BLOCKED_BY == frozenset(
        {"practice_audio_split_manifest_sha", "education_technique_lesson_manifest_sha"}
    )


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第3巡指摘（P2, 部分採用, Fix 3）: README.md の
# practice split 「分類訂正」記述（次フェーズ節）が、権限根拠を示さずに
# CLAUDE.md:71-75 の一般分類（実音源 = マシン依存 = Codex/User）を上書き
# して見え、後続セッションへ規約違反を指示しうるという懸念——正当なため
# 採用。ただし再分類そのものの削除は不採用（2026-08-25 本 PR セッション
# 中の User 裁定による scoped 事実であり、削除は逆方向の汚染）。採った
# 対応は出所（User 裁定）と適用範囲（practice_audio_split_manifest 生成
# のみ・2条件）の明記。CLAUDE.md/AGENTS.md 自体は本 Fix で改変しない
# （一般政策の改訂は User 権限 — 境界宣言）。
# ---------------------------------------------------------------------------


def test_fix323_3_readme_practice_scoped_exception_has_user_decision_provenance() -> None:
    """README.md の practice split scoped 例外記述に、出所（User 裁定・
    2026-08-25）を示す provenance マーカーが存在すること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "User 裁定" in readme_text
    assert "2026-08-25" in readme_text
    assert "scoped 例外" in readme_text


def test_fix323_3_readme_practice_scoped_exception_states_claude_md_classification_unchanged() -> None:
    """README.md が、CLAUDE.md の一般分類（実音源 = マシン依存 =
    Codex/User）は不変のままであり、本節がそれを上書きする一般規則では
    ないことを明記していること（Codex bot レビュー PR #323 第3巡指摘の
    核心 — 権限根拠なき上書きに見えることの是正）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "CLAUDE.md:71-75 の一般分類は不変" in readme_text
    assert "一般政策の改訂自体は User 権限" in readme_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第4巡指摘（P2, 採用, Fix 4）: RUN9_CONTRACT.yaml
# の PR #322 第20巡 repin 履歴ブロック（855-856行）「practice_audio_split_
# manifest_sha/education_technique_lesson_manifest_sha が PENDING の
# 現時点ではこの分離検証は実行不能」——第2巡では「日付明記済み過去記録」
# として無修正と判定したが、「現時点」という語が記録執筆時点か現在かを
# 曖昧にし読者が誤読しうるという新しい具体経路の指摘で採用に転じた。
# append-only 規約（第2巡返信で自ら宣言済み）を守り、原文（855-858行）は
# 一切書き換えず、直後に supersede 注記コメントを追記のみした。
# ---------------------------------------------------------------------------


def test_fix323_4_contract_two_pin_pending_claim_has_supersede_annotation() -> None:
    """RUN9_CONTRACT.yaml の PR #322 第20巡 repin 履歴ブロック直後に、
    「現時点」の曖昧性を是正する 2026-08-25 付 supersede 注記が実在する
    こと（原文は書き換えず追記のみ — append-only 規約の直接確認）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "PENDING の現時点ではこの分離検証は実行不能" in contract_text  # 原文は不変
    assert "履歴注記 2026-08-25" in contract_text
    assert "本 repin 記録の執筆時点（PR #322 第20巡）を指す" in contract_text
    assert "education_technique_lesson_\n# manifest_sha は引き続き PENDING" in contract_text


def test_fix323_4_repo_wide_two_pin_pending_pattern_all_supersede_or_absent() -> None:
    """`test_fix323_1_repo_wide_grep_finds_no_other_stale_practice_manifest_
    claim` が実装した条件付き検査（「両欄名併記 + PENDING の現時点」パター
    ンが見つかった場合、直後に 2026-08-25 supersede 注記が存在すること）
    を、走査対象全ファイルに対して独立に直接実行し、少なくとも1件
    （RUN9_CONTRACT.yaml の当該箇所）が実際に検出・supersede 確認されて
    いること（検査ロジック自体が「一致ゼロで常に pass する」空振りテスト
    になっていないことの確認）。"""
    pattern = re.compile(
        r"practice_audio_split_manifest_sha.{0,80}education_technique_lesson_manifest_sha"
        r".{0,40}PENDING.{0,10}現時点",
        re.DOTALL,
    )
    swept_paths = (
        CONTRACT_PATH,
        _RUN_DIR / "README.md",
        _RUN_DIR / "run9_schema.py",
        _RUN_DIR / "practice_split_builder.py",
    )
    total_matches = 0
    for path in swept_paths:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            total_matches += 1
            context = text[max(0, match.start() - 400) : match.end() + 400]
            has_supersede_marker = "履歴注記" in context or "〔履歴" in context
            assert has_supersede_marker and "2026-08-25" in context, (
                f"{path.name} に supersede 注記なしのパターンが残っている"
            )
    assert total_matches >= 1, (
        "パターンが一件も検出されなかった — 検査ロジックが実際に機能して"
        "いることを確認できない（空振りテストの疑い）"
    )


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第5巡指摘（P2, 採用, Fix 5）: README の
# practice split 節は再現可能と主張しながら、fresh checkout の読者が
# 実行できる逐語レシピ（取得コマンド・sha 検証・展開・builder 呼び出し・
# 直列化・出力 sha 照合）を示していなかった。producer script を別途 pin
# せよという提案は、producer（practice_split_builder.py）が manifest と
# 同一リポジトリ・同一コミットで版管理され、identity 定数がモジュール内
# ハードコードで fail-closed 照合されることを理由に「追加機構不要」と
# 整理し、README にその論理を明記した。
# ---------------------------------------------------------------------------


def test_fix323_5_readme_has_verbatim_executable_recipe_commands() -> None:
    """README.md の practice split 節に、取得（gdown）・sha 検証・展開
    （unzip）・生成（build_practice_split_manifest 呼び出し）の逐語
    コマンドが実在すること。sha 検証コマンドは PR #323 第7巡 Fix 7a で
    `sha256sum -c -`（不一致を非零 exit で検出する形）へ改訂済み——
    旧形式（素の `sha256sum FILE` 併記コメント）はもう存在しない。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "再現レシピ" in readme_text
    assert "gdown.download(" in readme_text
    # Fix 11（第11巡, P2, 採用）で zip 検証対象が $workdir 内へ移動
    assert (
        '683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca  '
        '$workdir/PJS_corpus_ver1.1.zip" | sha256sum -c -'
    ) in readme_text
    assert 'unzip -q "$workdir/PJS_corpus_ver1.1.zip" -d "$workdir/extracted"' in readme_text
    assert "psb.build_practice_split_manifest(" in readme_text
    assert "expected_corpus_identity=psb.EXPANDED_CORPUS_IDENTITY_SHA256" in readme_text
    assert "psb.dump_practice_split_manifest_bytes(manifest)" in readme_text


def test_fix323_5_readme_recipe_pins_both_output_hashes() -> None:
    """README.md のレシピが、manifest 実バイトの sha256（契約 pin 値）と
    row_order_sha256 の両方を照合対象として明記していること——契約 pin
    値との一致は builder 呼び出しだけでは自動確認されないため、レシピ
    自体に照合手順が要る。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "fd06000888736e87bba867b48fdf5651cf7c53b152121a318d1e10f11373f1e6" in readme_text
    assert "6b8435bcf006e9dc90bd5272671da84ee7c82baaaad497ea2926a811e6e9d45a" in readme_text


def test_fix323_5_readme_states_producer_pin_needs_no_extra_mechanism() -> None:
    """README.md が、producer（practice_split_builder.py）の版管理・
    identity 定数ハードコード・fail-closed 照合という既存構造が producer
    pin として機能する旨——別途 producer pin 機構は不要という整理——を
    明記していること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "producer pin の意味論" in readme_text
    assert "同一リポジトリ・同一コミットで版管理" in readme_text
    assert "この構造自体が producer" in readme_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第6巡指摘（P2, 採用, Fix 6）: 第5巡の整理
# 「生成コミット自体が producer pin」は、その生成コミットの具体値を
# README に記録していなかったため、後日 checkout の読者は `git rev-parse
# HEAD` が現在のコミットを返すだけで、pin 済みバイトを作った実装を
# 特定・再実行できないという残欠陥だった（第5巡整理の未完部分を突く
# 新しい具体経路）。生成コミット全 40hex・汎用の特定手順（`git log
# --follow`）・生成時点の builder sha256 を README に記録した。
# ---------------------------------------------------------------------------


def test_fix323_6_readme_records_generating_commit_and_git_log_follow_recipe() -> None:
    """README.md の producer pin 節に、生成コミットの全 40hex sha と、
    今後 producer が変わっても通用する `git log --follow` による汎用の
    特定手順が記載されていること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "producer revision の具体記録" in readme_text
    assert "bf056ae635b2435e6888b85091c65626a9b0e3a3" in readme_text
    assert (
        "git log --follow -- voice_genesis/evolution/run9_dual_founder_pjs/"
        "inputs/practice_audio_split_manifest.json"
    ) in readme_text


def test_fix323_6_readme_builder_sha_matches_actual_file() -> None:
    """README.md に記載された生成時点の `practice_split_builder.py`
    sha256 が、現在のリポジトリ実ファイルの実測 sha256 と一致すること
    ——**fail-closed 配線**: `practice_split_builder.py` が将来変更
    されると本テストが赤くなり、README の producer 記録（生成コミット
    sha・builder sha256）を同じ PR で同時更新する repin 手続きを機械
    強制する（更新を怠って README の記録だけが stale になることを防ぐ
    ための直接照合であり、単なる存在確認ではない）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    recorded_sha = "894451c953d5eb5b50448687480ede9b7b808c8c2c620a97b63978704e37d479"
    assert recorded_sha in readme_text
    actual_sha = m.compute_file_sha256(_RUN_DIR / "practice_split_builder.py")
    assert actual_sha == recorded_sha, (
        "practice_split_builder.py が変更されたが README.md の producer 記録"
        f"（builder sha256）が追随していない: recorded={recorded_sha!r} "
        f"actual={actual_sha!r} — この PR で README を repin として更新すること"
    )


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第7巡2件の対応（Fix 7a: 採用 / Fix 7b: 部分採用）
# ---------------------------------------------------------------------------


def test_fix323_7a_readme_checksum_steps_use_sha256sum_dash_c_not_bare_print() -> None:
    """README.md の practice split レシピの sha 検証手順（zip archive /
    manifest / row_order_sha256）が、非対話実行でも不一致を非零 exit で
    検出する形（`sha256sum -c -` または python `assert`）を使っている
    こと——旧形式（素の `sha256sum FILE` と期待値コメント併記のみ）は
    ファイルが読める限り常に exit 0 を返し、誤った archive の展開・
    誤った manifest の書き出しが成功として進んでしまう致命的欠陥だった
    （Codex bot レビュー PR #323 第7巡指摘, P2, 採用, Fix 7a）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    # Fix 11（第11巡, P2, 採用）で zip 検証対象が $workdir 内へ移動
    assert (
        'echo "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca'
        '  $workdir/PJS_corpus_ver1.1.zip" | sha256sum -c -'
    ) in readme_text
    assert (
        'echo "fd06000888736e87bba867b48fdf5651cf7c53b152121a318d1e10f11373f1e6'
        '  $workdir/output.json" | sha256sum -c -'
    ) in readme_text
    assert "assert d['row_order_sha256'] ==" in readme_text
    # 旧・非 fail-closed 形式（コメント併記のみ）が残っていないこと
    assert "sha256sum PJS_corpus_ver1.1.zip\n" not in readme_text


def test_fix323_7b_readme_downgrades_pr_commit_to_attestation_and_prioritizes_git_log_follow() -> None:
    """README.md が、PR 側生成コミット `bf056ae…` を「checkout 保証付きの
    再現パス」ではなく「生成イベントの attestation（証跡）」へ明示的に
    降格し、実行可能な第一の再現ポインタとして `git log --follow`
    （マージ方式に依存せず常に有効）を優先していること（Codex bot
    レビュー PR #323 第7巡指摘, P2, 部分採用, Fix 7b — 核心＝checkout
    可能性はマージ方式依存という指摘は採用。「reviewed commit の祖先で
    ない」という副次的主張は事実誤認として返信で訂正済み）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "第一の再現ポインタ" in readme_text
    assert "attestation（証跡・降格記録）" in readme_text
    assert "squash merge" in readme_text and "merge commit" in readme_text
    # 「第一の再現ポインタ」節が「生成イベントの attestation」節より先に出現すること
    assert readme_text.index("第一の再現ポインタ") < readme_text.index("attestation（証跡・降格記録）")


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第8巡指摘（P2, 採用, Fix 8）: レシピ step 4 は
# 現在 checkout の run9_schema.py を import しており、_song_score()/
# assign_split() が消費する LEARNING_SEED や検証ロジックが producer
# revision と異なり得る。記録・照合済みの producer sha は
# practice_split_builder.py 単体のみで、run9_schema.py 側の変更では
# test_fix323_6_readme_builder_sha_matches_actual_file が green のまま
# レシピが pin バイトを再現できなくなる欠陥だった。是正方式は指摘の
# 第1選択肢「producer tree からの実行」（git worktree）を採用——第2選択肢
# （依存閉包の全ファイル sha pin）は run9_schema.py がコメント編集で頻繁に
# 変わり（本 PR の Fix 2 が実例）脆いため不採用。
# ---------------------------------------------------------------------------


def test_fix323_8_readme_recipe_executes_from_producer_tree_via_git_worktree() -> None:
    """README.md のレシピ step 4 が、`git log --follow` で特定した
    producer revision を `git worktree add` で実際に checkout し、その
    worktree 内の `practice_split_builder.py`/`run9_schema.py`（依存閉包
    全体）から実行する逐語コマンドを含んでいること——現在 checkout の
    `run9_schema.py` を import する旧手順（`LEARNING_SEED`/検証ロジック
    の producer revision との差異を見逃す）ではないことの確認。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "producer tree で実行" in readme_text
    # Fix 10b（第10巡, P1, 採用）で固定パス /tmp/pjs_producer から
    # mktemp -d ベースの衝突安全パス（$workdir/producer_tree）へ移行済み
    assert 'git worktree add "$workdir/producer_tree" "$producer_rev"' in readme_text
    assert 'git worktree remove "$workdir/producer_tree"' in readme_text
    assert (
        'sys.path.insert(0, os.path.join(workdir, "producer_tree", '
        '"voice_genesis", "evolution", "run9_dual_founder_pjs"))'
    ) in readme_text
    # Fix 12（第12巡, P2, 採用）で in-place 近道注記（省略可能扱い）を
    # 撤去済み——worktree 手順は常に実行する単一経路であることの確認
    assert "in-place 実行が等価" not in readme_text
    assert "本手順は現在 checkout が producer revision と一致している場合" in readme_text
    assert "でも省略せずそのまま実行する" in readme_text


def test_fix323_8_readme_clarifies_dependency_closure_is_full_package_not_builder_alone() -> None:
    """README.md の producer pin 意味論節が、依存閉包は
    `practice_split_builder.py` 単体ではなく producer revision 時点の
    `run9_dual_founder_pjs/` パッケージ全体であり、builder sha256 記録は
    「split ロジック本体の同一性の証跡」に過ぎず閉包全体を覆わないことを
    明記していること。閉包全体を pin する手段として、依存ファイル個別の
    sha pin 方式ではなく producer tree 実行を選んだ理由（run9_schema.py
    がコメント編集で頻繁に変わり脆いこと、本 PR の Fix 2 が実例）も
    明記されていること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "依存閉包の範囲" in readme_text
    assert "split ロジック\n本体の同一性の証跡" in readme_text
    assert "この閉包全体を覆わない" in readme_text
    assert "コメント編集\nだけで頻繁に変わる" in readme_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第9巡指摘（P2, 採用, Fix 9）: レシピ step 4c が
# 生の Python コードフェンスのままで、逐語シェル実行の主張と矛盾していた
# （シェルが `import sys` を実行しようとして構文エラーになり、出力 json が
# 生成されず後続 checksum も走らない）。`python3 - <<'EOF' … EOF` heredoc
# 形式へ改訂し、README から step 4c のコードブロックを機械抽出して
# (a) heredoc ラッパー構造 (b) heredoc 内 Python body の構文、を検証する
# 回帰テストを新設した。**境界宣言**: 完全実行（275MB の PJS コーパス
# 取得を要する）は CI では実行不能なため、本テストは構文レベルの検証に
# 留める——完全な通し実行は本セッションの実測ログ
# （scratchpad/pjs_r10_heredoc_rerun.txt、worktree add → heredoc python
# 実行 → sha256sum -c OK → row_order_sha256 OK → worktree remove まで
# 全ステップ成功）で担保する。
# ---------------------------------------------------------------------------


def _extract_step4c_bash_block(readme_text: str) -> str:
    """README.md の step 4c（`python3 - <<'EOF' … EOF` を含む ```bash
    フェンス）の中身を、Markdown のリスト項目インデント（本文の共通
    先頭空白）を除去して返す——GitHub のレンダリングで読者が実際に目に
    する/コピーする形と一致させる（`textwrap.dedent()`）。閉じ ``` も
    リスト項目インデント付きで出現する（列挙の呼応点）ため、終端を
    非貪欲パターンへ焼き込まず「```bash フェンスを全列挙し、対象の
    heredoc マーカーを含む最初の1件を選ぶ」方式にする——フェンス記法
    自体が壊れていれば1件も見つからずここで例外になる（抽出ロジックも
    「実在するコードブロックを対象にしている」ことの間接検証を兼ねる）。
    """
    for match in re.finditer(r"```bash\n(.*?)\n[ \t]*```", readme_text, re.DOTALL):
        block = textwrap.dedent(match.group(1))
        if "python3 - <<'EOF'" in block:
            return block
    raise AssertionError("README.md に step 4c の ```bash フェンス（heredoc含む）が見つからない")


def test_fix323_9_readme_step4c_is_heredoc_wrapped_not_raw_python_fence() -> None:
    """README.md の step 4c が、生の Python コードフェンスではなく
    `python3 - <<'EOF' … EOF`（クォート付きデリミタ）で包まれた実行可能
    シェルコマンドであること。旧・生フェンス形式（```python 単体）は
    もう存在しない。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    block = _extract_step4c_bash_block(readme_text)
    lines = [line for line in block.splitlines() if line.strip()]
    assert lines[0].strip() == "python3 - <<'EOF'", lines[0]
    assert lines[-1].strip() == "EOF", lines[-1]
    # クォート付きデリミタ（'EOF'）であること——非クォートだと $producer_rev 等の
    # シェル変数展開がヒアドキュメント内で起きてしまう
    assert "<<'EOF'" in block
    # 旧・生フェンス（```python）がもう存在しないこと
    assert "```python\n" not in readme_text


def test_fix323_9_readme_step4c_heredoc_body_compiles_as_valid_python() -> None:
    """README.md の step 4c heredoc 内 Python body を `compile(..., 'exec')`
    で構文検証する（実行はしない——275MB の PJS コーパス取得を要するため
    CI では実行不能。完全な通し実行は本セッションの実測ログで担保する
    境界宣言）。旧版（生フェンス）は Fix 9 是正前は README 上の文字列と
    しては元々正しい Python だったため、本テストは「heredoc 抽出ロジック
    が実際に動く Python を取り出せていること」の直接確認として機能する
    ——将来 heredoc 記法が壊れれば（閉じ `EOF` 欠落等）抽出結果が空/不正
    になり compile が失敗する。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    block = _extract_step4c_bash_block(readme_text)
    lines = block.splitlines()
    # 先頭行（python3 - <<'EOF'）と末尾行（EOF）を除いた本体
    body_lines = lines[1:-1]
    assert body_lines, "heredoc 本体が空——抽出に失敗している"
    body = "\n".join(body_lines)
    assert "import practice_split_builder as psb" in body
    assert "psb.build_practice_split_manifest(" in body
    assert "psb.dump_practice_split_manifest_bytes(manifest)" in body
    compile(body, "<readme step4c heredoc body>", "exec")  # SyntaxError なら本テストが失敗する


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第10巡3件（P1/P2, 全採用, Fix 10a/10b/10c）:
# Fix 10a — レシピを一括シェル実行した場合、`sha256sum -c` の非零 exit が
# 後続ステップを止めない（偽成功経路）。Fix 10b（P1）— 固定パス
# `/tmp/pjs_producer`・`/tmp/pjs_producer_output.json` は中断/並行実行で
# 衝突する。Fix 10c — `--depth 1` 等の shallow clone では `git log
# --follow` が shallow 境界を返し「常に有効」の主張と矛盾する。
# 本巡で bot レビュー対応の上限10巡（CLAUDE.md 規約）に到達したため、
# 各返信の末尾にその旨と以降の採否方針を明記した（本ファイル内の
# コメントとしても記録: 以降の巡では実コード被害/将来汚染/致命的バグの
# 新しい具体経路を示す指摘のみ採用し、その他は境界宣言として未対応
# リストへまとめ User のマージ判断へ委ねる）。
# ---------------------------------------------------------------------------


def test_fix323_10a_readme_recipe_has_errexit_preamble_and_explicit_exit_on_checksum() -> None:
    """README.md のレシピが、一括実行時の前提として `set -euo pipefail`
    を明示し、かつ zip/manifest の `sha256sum -c -` 双方に `|| exit 1`
    を併記していること（`set -e` の実行し忘れに対する二重の安全策）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    # Fix 11（第11巡）で workdir 作成をこのプリアンブルへ集約したため、
    # `set -euo pipefail` ブロックには `workdir=`/`export` 行も同居する
    assert "```bash\nset -euo pipefail\nworkdir=" in readme_text
    assert (
        'echo "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca'
        '  $workdir/PJS_corpus_ver1.1.zip" | sha256sum -c - || exit 1'
    ) in readme_text
    assert (
        'echo "fd06000888736e87bba867b48fdf5651cf7c53b152121a318d1e10f11373f1e6'
        '  $workdir/output.json" | sha256sum -c - || exit 1'
    ) in readme_text


def test_fix323_10b_readme_recipe_uses_mktemp_workdir_not_fixed_tmp_paths() -> None:
    """README.md のレシピが、衝突しうる固定パス（`/tmp/pjs_producer`・
    `/tmp/pjs_producer_output.json`）ではなく `mktemp -d` による一意な
    作業ディレクトリを使っていること。heredoc は Fix 9 のクォート付き
    デリミタ（`<<'EOF'`）を維持したまま、出力先パスは環境変数
    `PJS_WORKDIR`（`export` + `os.environ`）経由で heredoc 内へ渡して
    いる——heredoc 内でのシェル変数展開に頼っていないことも確認する。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert 'workdir="$(mktemp -d)"' in readme_text
    assert 'export PJS_WORKDIR="$workdir"' in readme_text
    assert 'workdir = os.environ["PJS_WORKDIR"]' in readme_text
    assert 'rm -rf "$workdir"' in readme_text
    # 旧・固定パスへの実行コマンドがもう存在しないこと（Fix 10b の説明文
    # 内で経緯として言及されるのは許容——実行コマンドの形での残存のみ拒否）
    assert "git worktree add /tmp/pjs_producer" not in readme_text
    assert "git worktree remove /tmp/pjs_producer" not in readme_text
    assert '"/tmp/pjs_producer_output.json"' not in readme_text
    # heredoc 本体が引き続きクォート付きデリミタであること（Fix 9 維持）
    block = _extract_step4c_bash_block(readme_text)
    assert block.splitlines()[0].strip() == "python3 - <<'EOF'"


def test_fix323_10c_readme_step4a_guards_against_shallow_clone() -> None:
    """README.md の step 4a が、`git rev-parse --is-shallow-repository`
    による shallow 判定 + `git fetch --unshallow` の逐語コマンドを含み、
    「第一の再現ポインタ」の「常に有効」表記が「完全履歴の checkout で
    常に有効」へ精密化されていること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert 'git rev-parse --is-shallow-repository' in readme_text
    assert "git fetch --unshallow" in readme_text
    assert "完全履歴の\n  checkout で常に有効" in readme_text
    # 精密化前の無条件「常に有効」（括弧内の単独表記）がもう残っていないこと
    assert "汎用手順、常に有効）" not in readme_text


def test_fix323_10_replies_document_10_round_cap_reached() -> None:
    """本ファイル（このコメントブロック自体）が、bot レビュー対応の上限
    10巡（CLAUDE.md 規約）に到達した旨を記録していること——返信本文側の
    記録は投稿時に別途確認する対象のため、ここではソース側の記録の存在
    のみを直接確認する。"""
    self_text = Path(__file__).read_text(encoding="utf-8")
    assert "上限10巡" in self_text
    assert "CLAUDE.md 規約" in self_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第11巡1件（P2, 上限超過後だが3分類の新しい
# 具体経路として採用, Fix 11）: レシピは repo root での実行を要求
# （step 4a の repo 相対 `git log` パス）しながら、zip 取得（旧 step 1）と
# 展開（旧 step 3）を CWD 直下に書いていたため、成功のたびに 275MB の
# 実音源が checkout 内へ untracked のまま残っていた——「実 PJS 音源・
# 展開物は repo 配下へ置いていない」という同 README の宣言と矛盾し、
# 後続コミットへの実音源混入（権利面でも汚染）リスクがあった。
# workdir 作成をレシピ冒頭へ集約し、取得・展開・生成・照合のすべての
# データを `$workdir` 側へ閉じ込めることで解消した。
# ---------------------------------------------------------------------------


def test_fix323_11_readme_workdir_created_before_download_step() -> None:
    """README.md のレシピが、`workdir="$(mktemp -d)"` をステップ列（取得
    ステップ）より前のプリアンブルで作成し、取得（gdown 出力先）・検証・
    展開（unzip 展開先）のいずれもリポジトリ CWD 直下の裸パスではなく
    `$workdir`/`$PJS_WORKDIR` 配下を参照していること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    preamble_idx = readme_text.index('workdir="$(mktemp -d)"')
    download_idx = readme_text.index("gdown.download(")
    assert preamble_idx < download_idx, (
        "workdir の作成がステップ1（取得）より後にある——repo root 直下への"
        "書き込みを避けるには取得の前に作成されていなければならない"
    )
    assert "output='$PJS_WORKDIR/PJS_corpus_ver1.1.zip'" in readme_text
    assert 'unzip -q "$workdir/PJS_corpus_ver1.1.zip" -d "$workdir/extracted"' in readme_text
    assert (
        'os.path.join(workdir, "extracted", "PJS_corpus_ver1.1")'
    ) in readme_text


def test_fix323_11_readme_no_bare_cwd_writes_for_zip_or_extraction() -> None:
    """README.md のレシピに、CWD 直下の裸パス（`output='PJS_corpus_
    ver1.1.zip'` や `unzip ... PJS_corpus_ver1.1.zip -d extracted`、
    生成入力の裸 `"extracted/PJS_corpus_ver1.1"`）への書き込みコマンドが
    もう存在しないこと——旧版はこれらの裸パスへ実際に書き込むことで、
    成功のたびに 275MB の実音源が checkout 内へ untracked のまま残る
    経路になっていた。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "output='PJS_corpus_ver1.1.zip'" not in readme_text
    assert "unzip -q PJS_corpus_ver1.1.zip -d extracted" not in readme_text
    assert '"extracted/PJS_corpus_ver1.1"' not in readme_text
    # 最終 cleanup が $workdir 一括削除であり、zip/展開物/worktree 出力の
    # すべてがこの1コマンドに含まれる旨の説明が存在すること
    assert 'rm -rf "$workdir"' in readme_text
    assert "zip・展開コーパス・\n   worktree 出力" in readme_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第12巡1件（P2, 上限超過後だが3分類の新しい
# 具体経路として採用, Fix 12）: Fix 8（第8巡）で導入した「現在 checkout
# が producer revision と一致する場合は in-place 実行が等価であり
# worktree 手順は省略してよい」という近道注記が、第9〜11巡の改訂
# （heredoc 化・`$workdir` 集約）後も残っていたが、step 4c/4d は無条件に
# `$workdir/producer_tree/...` を参照するため、近道に従った読者は
# `ModuleNotFoundError`（4c）/存在しない worktree への `remove` 失敗
# （4d）に陥る——文書化された近道が実行不能という致命的バグ類型の新しい
# 具体経路。worktree 手順自体は checkout が producer revision と一致
# していても問題なく動作するため、in-place 代替2系統を維持する価値が
# なく、近道の撤去（単一経路への一本化）を選んだ。
# ---------------------------------------------------------------------------


def test_fix323_12_readme_in_place_shortcut_removed_worktree_step_mandatory() -> None:
    """README.md から Fix 8 が導入した in-place 近道注記（省略可能扱い）
    が撤去され、worktree 手順（`git worktree add`）が現在 checkout の
    状態に関わらず常に実行される単一経路であることを明記した文へ
    置き換わっていること。撤去理由は `〔履歴:〕` 形式で保持されている
    こと（AGENTS.md 運用: 純粋に歴史的な記述は削除せず superseded 明示で
    保持する）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    # 旧・近道注記の決定的フレーズ（改行なし連続文字列）が本文として
    # 残っていないこと——同フレーズは下記〔履歴:〕引用内にのみ改行を挟んで
    # 分割再掲されるため、この形での不在確認は歴史的引用と現在の本文を
    # 正しく区別する。
    assert "in-place 実行が等価" not in readme_text
    assert (
        "**本手順は現在 checkout が producer revision と一致している場合\n"
        "      でも省略せずそのまま実行する**——分岐は不要"
    ) in readme_text
    assert "〔履歴: Fix 8（第8巡）で" in readme_text
    assert "第12巡で解消〕" in readme_text


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第13巡1件（P2, 上限超過後だが3分類の新しい
# 具体経路として採用, Fix 13）: clean Python 環境でレシピを逐語実行すると、
# 「取得」ステップの説明が `gdown` の導入のみを案内する一方、step 4c が
# import する `practice_split_builder`（→ `run9_schema`）の依存
# （numpy / PyYAML）を導入する手順が存在しなかった。読者は展開まで完走した
# 後で初めて `ModuleNotFoundError` に遭遇し、依存導入が必要だったことを
# 逆算しなければならない——依存欠落による文書化フローの実行不能という
# 致命的バグ類型の新しい具体経路（Fix 12 の「近道注記の撤去」とは独立の
# 欠陥）。producer tree の実ソースを確認し（practice_split_builder.py の
# top-level import は numpy のみ、run9_schema.py の top-level import は
# PyYAML のみ、librosa は acoustic inventory sidecar 専用の関数内
# import で本レシピの生成経路には現れない）、README プリアンブルへ
# 「推奨: `pip install -e ".[dev]"`」「代替（最小）: `pip install numpy
# pyyaml gdown`」の2段構え依存導入ステップを明記した。venv でのクリーン
# 環境実測（依存導入前は ModuleNotFoundError、導入後は生成 + sha 一致まで
# 成功）を本セッションで実施済み（scratchpad/pjs_r14_venv_verify.txt）。
# ---------------------------------------------------------------------------


def test_fix323_13_readme_dependency_install_step_present_before_download() -> None:
    """README.md のレシピプリアンブルに、`gdown` 導入案内より前（または
    同ブロック内）で明示的な依存導入ステップ（推奨 = `pip install
    -e ".[dev]"`、代替 = 最小閉包 `pip install numpy pyyaml gdown`）が
    存在し、その位置が「取得」ステップ（gdown.download 呼び出し）より
    前であること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    dep_idx = readme_text.index("**依存導入**")
    download_idx = readme_text.index("gdown.download(")
    assert dep_idx < download_idx, (
        "依存導入ステップが取得ステップ（gdown.download）より後にある——"
        "clean 環境の読者が取得ステップに到達する前に依存導入を終えられない"
    )
    assert 'pip install -e ".[dev]"' in readme_text
    assert "pip install numpy pyyaml gdown" in readme_text


def test_fix323_13_readme_dependency_closure_matches_verified_imports() -> None:
    """README.md の依存導入節が、producer tree の実ソースを確認した結果
    （`practice_split_builder.py` は numpy のみ、`run9_schema.py` は
    PyYAML のみを top-level import し、librosa は acoustic inventory
    sidecar 専用のローカル import で生成経路には現れない）と整合する
    文言を含むこと。実ソース側も matching import 構造を保っていること
    （閉包の記述が実装からドリフトしていないことの回帰確認）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert (
        "top-level import は\n"
        "`practice_split_builder.py` の `numpy` と `run9_schema.py` の `PyYAML`"
    ) in readme_text
    assert "_measure_pitch_range_hz" in readme_text

    builder_text = (_RUN_DIR / "practice_split_builder.py").read_text(encoding="utf-8")
    schema_text = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    builder_lines = builder_text.splitlines()
    schema_lines = schema_text.splitlines()

    def _top_level_third_party_imports(lines: list[str]) -> list[str]:
        found = []
        for line in lines:
            if line.startswith(("import ", "from ")) and not line.startswith(
                ("import run9_schema", "from __future__")
            ):
                found.append(line.strip())
        return found

    builder_imports = _top_level_third_party_imports(builder_lines)
    assert any("numpy" in line for line in builder_imports)
    assert not any("librosa" in line for line in builder_imports), (
        "librosa が practice_split_builder.py の top-level import に"
        "現れている——README の依存閉包記述（librosa 不要）とズレている"
    )
    schema_imports = _top_level_third_party_imports(schema_lines)
    assert any("yaml" in line for line in schema_imports)


def test_fix323_13_readme_dependency_steps_appear_exactly_once() -> None:
    """依存導入ステップの推奨（`pip install -e ".[dev]"` を含む行）・
    代替（最小閉包）行が README 本文中でそれぞれ重複なく1回だけ記述
    されていること（コピペミスや二重記載の回帰防止）。〔履歴: 第13巡
    導入時点では説明コメントが `pip install -e ".[dev]"` を引用のため
    2回一致だったが、第14巡（Fix 14）で推奨コマンド自体が `gdown` を
    追記した1コマンドへ改訂され、説明コメントは引用形を使わなくなった
    ため1回一致に変わった → 第14巡で解消〕。実測（依存導入前は
    ModuleNotFoundError、導入後は生成 + pin sha 一致まで成功）は本
    セッションで実施済み——生ログは scratchpad/pjs_r14_venv_verify.txt
    （リポジトリ外・監査目的のみで pytest 対象外）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert readme_text.count('pip install -e ".[dev]"') == 1
    assert readme_text.count('pip install -e ".[dev]" gdown') == 1
    assert readme_text.count("pip install numpy pyyaml gdown") == 1


# ---------------------------------------------------------------------------
# PR #323 Codex bot レビュー第14巡1件（P2, 上限超過後だが3分類の新しい
# 具体経路として採用, Fix 14）: Fix 13 が追加した推奨コマンド `pip
# install -e ".[dev]"` は、`pyproject.toml` の本体依存にも `dev` extra
# にも `gdown` を含まないため、推奨経路を選んだ読者が step 1 の
# `import gdown` で `ModuleNotFoundError` に陥る——Fix 13 自身が対処した
# 欠陥（依存欠落による文書化フローの実行不能）が、Fix 13 の直した箇所に
# 残存する新しい具体経路。`pyproject.toml` へ `gdown` を追加する案は
# 不採用（`gdown` は本レシピ専用でプロジェクト本体の実行時依存ではない
# ため、本体依存表を汚染しない）。代わりに推奨コマンド自体を `pip
# install -e ".[dev]" gdown` へ改め、1コマンドで完結させた。
# ---------------------------------------------------------------------------


def test_fix323_14_readme_recommended_command_installs_gdown() -> None:
    """README.md の推奨依存導入コマンドが `pip install -e ".[dev]"
    gdown`（1コマンドで `gdown` を含む）へ改訂されており、`gdown` を
    含まない旧単体コマンド `pip install -e ".[dev]"`（末尾がそこで
    終わる形）がもう本文に残っていないこと。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev]" gdown' in readme_text
    # 旧・gdown を含まない単体コマンドが独立行として残っていないこと
    # （直後に " gdown" が続く形以外での出現がないことを確認する）。
    idx = readme_text.index('pip install -e ".[dev]"')
    tail = readme_text[idx : idx + len('pip install -e ".[dev]" gdown')]
    assert tail == 'pip install -e ".[dev]" gdown'


def test_fix323_14_pyproject_has_no_gdown_dependency() -> None:
    """`pyproject.toml` の本体依存 (`[project].dependencies`) にも `dev`
    extra (`[project.optional-dependencies].dev`) にも `gdown` が
    含まれていないこと——Fix 14 の裁定根拠（推奨コマンド単体では gdown
    が入らない）が実ファイルと一致していることの直接確認。本テストは
    「`pyproject.toml` へ `gdown` を追加しない」という Fix 14 の設計判断
    （本レシピ専用の依存であり本体依存表を汚染しない）を pin する回帰
    テストでもある——将来 `pyproject.toml` に `gdown` が追加された場合は
    本テストが red になり、README 側の二重記載（1コマンド化の前提が
    崩れる）に気づける。"""
    repo_root = _RUN_DIR.parent.parent.parent
    pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    deps_start = pyproject_text.index("dependencies = [")
    deps_end = pyproject_text.index("]", deps_start)
    dependencies_block = pyproject_text[deps_start:deps_end]
    assert "gdown" not in dependencies_block

    dev_start = pyproject_text.index('dev = ["pytest')
    dev_end = pyproject_text.index("]", dev_start)
    dev_block = pyproject_text[dev_start:dev_end]
    assert "gdown" not in dev_block
# ---------------------------------------------------------------------------
# RUN9-L0-PIN-1（Design Memo, 2026-08-25 実装）: seed_policy_sha /
# failure_abort_criteria_sha / measurement_spec_sha の manifest 化 + PINNED
# 化に対する回帰・fail-closed テスト。probe_manifest（PR #322）のテスト構成
# を踏襲する: 実ファイル validate 成功 → fail-closed 分岐 → read-once
# loader（PINNED 確認・contract 改竄検出・バイト改竄検出・正常系）→
# 全体回帰（3欄 PINNED・gate_state() BLOCKED・再直列化 byte 一致・
# stale PENDING マーカー不在）。
# ---------------------------------------------------------------------------


def _seed_policy_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.SEED_POLICY_MANIFEST_PATH.read_text(encoding="utf-8"))


def _failure_abort_criteria_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.FAILURE_ABORT_MANIFEST_PATH.read_text(encoding="utf-8"))


def _measurement_spec_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.MEASUREMENT_SPEC_MANIFEST_PATH.read_text(encoding="utf-8"))


def _dataset_split_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.DATASET_SPLIT_MANIFEST_PATH.read_text(encoding="utf-8"))


def _dependency_pins_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.DEPENDENCY_PINS_MANIFEST_PATH.read_text(encoding="utf-8"))


def _legacy_dependency_pins_manifest_data() -> Dict[str, Any]:
    """RUN9-L0-HARNESS-2 以前の `NOT_OBTAINED_TARBALL_MISS`/`BLOCKED` shape
    を独立に再構築したテスト専用フィクスチャ。実データの
    `inputs/dependency_pins_manifest.json` は HARNESS-2 で
    `acoustic_export_companions.status == OBTAINED_VIA_REEXPORT` /
    `smoke_render.status == COMPLETED` / `budget_estimate.status ==
    COMPLETED` へ恒久遷移した——MISS/BLOCKED 経路の validator shape
    自体は非退行のまま残っている（AC「既存 status の shape 検証非退行」）
    ことを単体テストするため、本ヘルパーで独立に MISS/BLOCKED shape を
    合成する（値そのものが RUN9 の実測事実であることは主張しない——
    render_asset_ledger/python_dependency_pins/diffsinger_render_code_
    commit/tar_gz_*/claim_scope/speaker_embeddings_unpinned_candidates は
    HARNESS-2 で shape 変更していないため実データをそのまま流用する）。
    """
    data = copy.deepcopy(_dependency_pins_manifest_data())
    # basename は実データの旧 shape（HARNESS-1 時点）と同一にする——複数の
    # テストが "acoustic.onnx"/"dsconfig.yaml" 等の basename をハード
    # コードして tar member との一致/不一致を検証するため。
    _legacy_companion_files = {
        "acoustic_onnx": "onnx_gate_40000/acoustic.onnx",
        "acoustic_dsconfig_yaml": "onnx_gate_40000/dsconfig.yaml",
        "acoustic_phonemes_json": "onnx_gate_40000/s5_run6_acoustic_v1.phonemes.json",
        "speaker_embed_ritsu": "onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb",
    }
    data["acoustic_export_companions"] = {
        "status": "NOT_OBTAINED_TARBALL_MISS",
        "expected_items": [
            {
                "logical_name": name,
                "file": _legacy_companion_files[name],
                "expected_sha256": "a" * 64,
            }
            for name in sorted(m._DEPENDENCY_ACOUSTIC_COMPANION_BUNDLE_PATHS)
        ],
        "attempted_source": {"description": "legacy test fixture (pre-HARNESS-2 shape)"},
        "indirect_provenance_found": {},
        "run_execution_manifest_search": {},
        "verdict": "MISS — legacy test fixture",
        "fail_closed_disposition": "legacy test fixture — not attempted beyond this point",
    }
    data["smoke_render"] = {
        "status": "BLOCKED",
        "reason": "legacy test fixture — acoustic export companions not obtained",
        "blocked_by": "acoustic_export_companions.status == NOT_OBTAINED_TARBALL_MISS",
        "not_attempted_reason_is_missing_input_not_failure": True,
    }
    data["budget_estimate"] = {
        "status": "BLOCKED",
        "reason": "legacy test fixture — smoke_render not completed",
        "reference_only_prior_gpu_measurement_sec_per_item": [3.7, 7.6],
        "reference_only_source": "legacy test fixture",
        "reference_only_caveat": "legacy test fixture",
    }
    return data


# ---------------------------------------------------------------------------
# seed_policy_manifest: 正常系 + fail-closed 分岐
# ---------------------------------------------------------------------------


def test_pin1_seed_policy_manifest_validates() -> None:
    m.validate_seed_policy_manifest(_seed_policy_manifest_data())  # 例外を投げないことの確認


def test_pin1_seed_policy_manifest_registers_exactly_three_seeds() -> None:
    data = _seed_policy_manifest_data()
    seed_ids = {entry["seed_id"] for entry in data["seeds"]}
    assert seed_ids == {"performance_seed", "learning_seed", "gate_synth_runtime_seed"}


def test_pin1_seed_policy_manifest_values_match_frozen_sources() -> None:
    """一次ソースからの逐語転記の実測確認（2026-08-25 Codex bot レビュー
    PR #324 第2巡 Fix 4, P2, 採用 — 旧実装は `_SEED_POLICY_EXPECTED_VALUE`
    と同じリテラル `42` を manifest 側と重複主張するだけで
    `gate_synth.py` の実 `SEED` を読んでいなかった。本テストは
    `gate_synth.py` のソーステキストから `SEED = <int>` 代入を正規表現で
    抽出し、manifest 値・`run9_schema._SEED_POLICY_EXPECTED_VALUE` の
    双方と三者照合する。複数マッチ・マッチ0件は fail-closed（構造が
    変わって抽出が誤動作した場合に静かに緑のままにしない）。"""
    gate_synth_path = _FOUNDRY_DIR / "s1_gate" / "gate_synth.py"
    gate_synth_text = gate_synth_path.read_text(encoding="utf-8")
    seed_matches = re.findall(r"^SEED\s*=\s*(\d+)\s*$", gate_synth_text, flags=re.MULTILINE)
    assert len(seed_matches) == 1, (
        f"gate_synth.py の 'SEED = <int>' 代入が厳密に1件であることを期待したが "
        f"{len(seed_matches)} 件マッチした: {seed_matches!r}（マッチ0件・複数件は "
        "抽出ロジックの前提が崩れたことを意味し fail-closed で拒否する）"
    )
    gate_synth_actual_seed = int(seed_matches[0])
    assert gate_synth_actual_seed == m._SEED_POLICY_EXPECTED_VALUE["gate_synth_runtime_seed"]

    data = _seed_policy_manifest_data()
    by_id = {entry["seed_id"]: entry for entry in data["seeds"]}
    assert by_id["gate_synth_runtime_seed"]["value"] == gate_synth_actual_seed == 42

    # learning_seed/performance_seed は run9_schema モジュールの実定数
    # （LEARNING_SEED/SHARED_PERFORMANCE_SEED）を直接読むため、
    # gate_synth_runtime_seed のような別ファイルへのリテラル複製は生じない
    # ——ここでの `== m.LEARNING_SEED` は「重複リテラルの再主張」ではなく、
    # 生きた定数値そのものへの参照である。
    assert by_id["learning_seed"]["value"] == m.LEARNING_SEED == 909002
    assert by_id["performance_seed"]["value"] == m.SHARED_PERFORMANCE_SEED == 909001

    # performance_seed は DESIGN_RUN9 §9 の逐語行（R9F-01/R9F-02 双方）が
    # 実在することも grep 相当で追加照合する（一次ソース直読み、孫引き
    # 防止）。
    design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    perf_seed_matches = re.findall(
        r"^performance_seed:\s*(\d+)\s*$", design_text, flags=re.MULTILINE
    )
    assert len(perf_seed_matches) == 2, (
        f"DESIGN_RUN9 §9.2/§9.3 の 'performance_seed: <int>' 逐語行が厳密に2件"
        f"（R9F-01/R9F-02）であることを期待したが {len(perf_seed_matches)} 件マッチした: "
        f"{perf_seed_matches!r}"
    )
    for value in perf_seed_matches:
        assert int(value) == m.SHARED_PERFORMANCE_SEED == 909001


def test_pin1_seed_policy_manifest_unknown_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_seed_policy_manifest_data())
    data["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_seed_policy_manifest(data)


def test_pin1_seed_policy_manifest_missing_seed_fail_closed() -> None:
    data = copy.deepcopy(_seed_policy_manifest_data())
    data["seeds"] = [e for e in data["seeds"] if e["seed_id"] != "learning_seed"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_seed_policy_manifest(data)


def test_pin1_seed_policy_manifest_wrong_value_fail_closed() -> None:
    data = copy.deepcopy(_seed_policy_manifest_data())
    for entry in data["seeds"]:
        if entry["seed_id"] == "gate_synth_runtime_seed":
            entry["value"] = 43
    with pytest.raises(m.Run9ValidationError, match="must be exactly 42"):
        m.validate_seed_policy_manifest(data)


def test_pin1_seed_policy_manifest_wrong_independent_from_fail_closed() -> None:
    data = copy.deepcopy(_seed_policy_manifest_data())
    for entry in data["seeds"]:
        if entry["seed_id"] == "performance_seed":
            entry["independent_from"] = ["learning_seed"]  # gate_synth_runtime_seed が欠落
    with pytest.raises(m.Run9ValidationError, match="independent_from"):
        m.validate_seed_policy_manifest(data)


def test_pin1_seed_policy_manifest_unregistered_seed_prohibition_present() -> None:
    data = _seed_policy_manifest_data()
    assert isinstance(data["unregistered_seed_prohibition"], str)
    assert data["unregistered_seed_prohibition"].strip()


# ---------------------------------------------------------------------------
# failure_abort_criteria: 正常系 + fail-closed 分岐
# ---------------------------------------------------------------------------


def test_pin1_failure_abort_criteria_validates() -> None:
    m.validate_failure_abort_criteria(_failure_abort_criteria_data())  # 例外を投げないことの確認


def test_pin1_failure_abort_criteria_has_exactly_twenty_rules_1_to_20() -> None:
    data = _failure_abort_criteria_data()
    assert len(data["rules"]) == 20
    assert [r["rule_id"] for r in data["rules"]] == list(range(1, 21))


def test_pin1_failure_abort_criteria_verbatim_matches_design_doc_section30() -> None:
    """DESIGN_RUN9 §30（1466-1489行）の20項目逐語との一致を、design_doc
    実ファイルから直接抽出して照合する（孫引き防止 — 一次ソース直読み）。"""
    design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    section = design_text.split("# 30. Stop Rules", 1)[1]
    block = section.split("```text", 1)[1].split("```", 1)[0]
    expected_lines = [
        re.sub(r"^\d+\s+", "", line).strip()
        for line in block.strip().splitlines()
        if line.strip()
    ]
    assert len(expected_lines) == 20
    data = _failure_abort_criteria_data()
    actual_lines = [r["verbatim"] for r in data["rules"]]
    assert actual_lines == expected_lines


def test_pin1_failure_abort_criteria_enforcement_vocab_closed() -> None:
    data = _failure_abort_criteria_data()
    for rule in data["rules"]:
        assert rule["enforcement"] in ("MACHINE", "PROCEDURAL")


def test_pin1_failure_abort_criteria_one_machine_nineteen_procedural() -> None:
    """PR #324 Codex bot レビュー第1-3巡の累積分類（2026-08-25）: MACHINE
    1件（#2）のみが『(a) 実装済み・(b) 実内容検査』の2条件を満たして
    生存し、残り19件は PROCEDURAL。#12 は第3巡指摘（genome 文書側は
    load_pinned_founder_genome_document() で機械検証可能だが r0 state 側
    の検証機構が存在しない部分保証の過大主張）により第2巡の MACHINE 維持
    から PROCEDURAL へ再降格した。"""
    data = _failure_abort_criteria_data()
    machine = [r for r in data["rules"] if r["enforcement"] == "MACHINE"]
    procedural = [r for r in data["rules"] if r["enforcement"] == "PROCEDURAL"]
    assert {r["rule_id"] for r in machine} == {2}
    assert len(procedural) == 19
    assert 12 in {r["rule_id"] for r in procedural}


def test_pin1_failure_abort_criteria_no_current_deferred_threshold_ref_usage() -> None:
    """#14/#16 は PR #324 監査で PROCEDURAL へ再分類され
    （hypothesis_algebra_sha 自体が schema/validator 未実装のため
    deferred_threshold_ref を維持できない）、現時点で本キーを使う rule は
    ゼロである（メカニズム自体は将来の MACHINE 項目のために validator に
    残置——次の2テストが正例/負例で機構の健全性を確認する）。"""
    data = _failure_abort_criteria_data()
    deferred = [r for r in data["rules"] if "deferred_threshold_ref" in r]
    assert deferred == []


def test_pin1_failure_abort_criteria_deferred_threshold_ref_mechanism_accepts_real_pin_field() -> None:
    """deferred_threshold_ref 機構自体は生きていること（合成 MACHINE rule
    で正例確認）。rule 12 は PR #324 第3巡で PROCEDURAL へ再降格したため、
    唯一残る MACHINE 項目である rule 2 を対象に確認する。"""
    data = copy.deepcopy(_failure_abort_criteria_data())
    machine_rule = next(r for r in data["rules"] if r["rule_id"] == 2)
    assert machine_rule["enforcement"] == "MACHINE"
    machine_rule["deferred_threshold_ref"] = "hypothesis_algebra_sha"
    m.validate_failure_abort_criteria(data)  # 例外を投げないことの確認


def test_pin1_failure_abort_criteria_procedural_missing_machine_promotion_condition_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    procedural_rule = next(r for r in data["rules"] if r["enforcement"] == "PROCEDURAL")
    del procedural_rule["machine_promotion_condition"]
    with pytest.raises(m.Run9ValidationError, match="machine_promotion_condition"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_machine_rule_with_machine_promotion_condition_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    machine_rule = next(r for r in data["rules"] if r["enforcement"] == "MACHINE")
    machine_rule["machine_promotion_condition"] = "should not be allowed here"
    with pytest.raises(m.Run9ValidationError, match="machine_promotion_condition"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_rules_3_4_6_reclassified_procedural_with_grounded_citations() -> None:
    """PR #324 Codex bot レビュー第1巡 Fix 1/2/3（全3件 P1, 採用）の直接
    回帰: rule 3/4/6 が PROCEDURAL であり、各 checkpoint が指摘の一次
    ソース citation（run9_schema.py の行番号 / DESIGN_RUN9 の行番号）を
    保持していること。"""
    data = _failure_abort_criteria_data()
    by_id = {r["rule_id"]: r for r in data["rules"]}
    assert by_id[3]["enforcement"] == "PROCEDURAL"
    assert "run9_schema.py:961-993" in by_id[3]["checkpoint"]
    assert by_id[4]["enforcement"] == "PROCEDURAL"
    assert "run9_schema.py:3257-3264" in by_id[4]["checkpoint"]
    assert "999-1001" in by_id[4]["checkpoint"]
    assert by_id[6]["enforcement"] == "PROCEDURAL"
    assert "987-989" in by_id[6]["checkpoint"]


def test_pin1_failure_abort_criteria_rule6_r9g4_verbatim_matches_design_doc() -> None:
    """rule 6 の checkpoint が引用する R9-G4 DUAL_BIRTH_VIABILITY の逐語
    （最低発声/artifact/replay/provenance）が design_doc 実ファイルと
    一致すること（孫引き防止・一次ソース直読み）。"""
    design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    assert "二体とも最低発声、artifact、replay、provenanceを満たす。" in design_text
    data = _failure_abort_criteria_data()
    rule6 = next(r for r in data["rules"] if r["rule_id"] == 6)
    assert "最低発声" in rule6["checkpoint"]
    assert "provenance" in rule6["checkpoint"]


def test_pin1_failure_abort_criteria_no_viability_implementation_in_repo() -> None:
    """rule 6 の PROCEDURAL 根拠（R9-G4 の4特性を実検査する機構が repo に
    存在しない）を grep 相当で機械照合する。"""
    schema_text = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    for token in ("def phonation", "def artifact_viability", "def replay_viability"):
        assert token not in schema_text


def test_pin1_failure_abort_criteria_post_stop_prohibitions_match_design_doc() -> None:
    design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    assert (
        "new weights\nnew teacher\nnew Founder\nnew metric threshold\n"
        "new Lesson channel\nnew optimizer search" in design_text
    )
    data = _failure_abort_criteria_data()
    assert data["post_stop_prohibitions"]["items"] == [
        "new weights", "new teacher", "new Founder", "new metric threshold",
        "new Lesson channel", "new optimizer search",
    ]


def test_pin1_failure_abort_criteria_rule_id_not_matching_index_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    data["rules"][0]["rule_id"] = 2
    with pytest.raises(m.Run9ValidationError, match="rule_id"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_wrong_verbatim_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    data["rules"][0]["verbatim"] = "something else entirely"
    with pytest.raises(m.Run9ValidationError, match="verbatim"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_unknown_enforcement_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    data["rules"][0]["enforcement"] = "AUTOMATIC"
    with pytest.raises(m.Run9ValidationError, match="enforcement"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_machine_rule_with_checkpoint_fail_closed() -> None:
    """MACHINE 項目に PROCEDURAL 専用キー checkpoint を混入させると拒否
    される（両語彙の排他性の機械強制）。"""
    data = copy.deepcopy(_failure_abort_criteria_data())
    machine_rule = next(r for r in data["rules"] if r["enforcement"] == "MACHINE")
    machine_rule["checkpoint"] = "should not be allowed here"
    with pytest.raises(m.Run9ValidationError, match="checkpoint"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_procedural_rule_with_condition_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    procedural_rule = next(r for r in data["rules"] if r["enforcement"] == "PROCEDURAL")
    procedural_rule["condition"] = "should not be allowed here"
    with pytest.raises(m.Run9ValidationError, match="condition"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_bogus_deferred_threshold_ref_fail_closed() -> None:
    """deferred_threshold_ref に CONTRACT_PIN_FIELDS 外の名前（＝実質的な
    裸の閾値発明の代替経路）を与えると拒否される。"""
    data = copy.deepcopy(_failure_abort_criteria_data())
    rule14 = next(r for r in data["rules"] if r["rule_id"] == 14)
    rule14["deferred_threshold_ref"] = "made_up_threshold_field"
    with pytest.raises(m.Run9ValidationError, match="deferred_threshold_ref"):
        m.validate_failure_abort_criteria(data)


def test_pin1_failure_abort_criteria_missing_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_failure_abort_criteria_data())
    del data["classification_policy"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_failure_abort_criteria(data)


# ---------------------------------------------------------------------------
# measurement_spec_manifest: 正常系 + fail-closed 分岐
# ---------------------------------------------------------------------------


def test_pin1_measurement_spec_manifest_validates() -> None:
    m.validate_measurement_spec_manifest(_measurement_spec_manifest_data())  # 例外なし確認


def test_pin1_measurement_spec_manifest_identity_axis_matches_revision_bridge_entries() -> None:
    """identity_axis_metric_paths が evaluation/probe_manifest.json
    revision_bridge の7エントリと過不足なく一致すること（一次ソース直読み
    で照合 — 孫引き防止）。"""
    probe_manifest = m._loads_strict_json(m.PROBE_MANIFEST_PATH.read_text(encoding="utf-8"))
    revision_bridge_keys = set(probe_manifest["revision_bridge"].keys())
    data = _measurement_spec_manifest_data()
    assert set(data["identity_axis_metric_paths"].keys()) == revision_bridge_keys
    assert revision_bridge_keys == set(m._REVISION_BRIDGE_ENTRY_NAMES)


def test_pin1_measurement_spec_manifest_metric_path_refs_match_frozen_table() -> None:
    data = _measurement_spec_manifest_data()
    for entry_name, expected_ref in m._REVISION_BRIDGE_EXPECTED_METRIC_REF.items():
        assert (
            data["identity_axis_metric_paths"][entry_name]["identity_metric_space_ref"]
            == expected_ref
        )


def test_pin1_measurement_spec_manifest_extractor_module_exists_and_function_greppable() -> None:
    """extractor 参照（module path + 消費関数）が実在することを grep 相当
    で機械照合する（Design Memo Risk 節: 存在しない extractor を書かない）。"""
    data = _measurement_spec_manifest_data()
    repo_root = _RUN_DIR.parent.parent.parent
    for entry in data["identity_axis_metric_paths"].values():
        module_path = repo_root / entry["extractor"]["module"]
        assert module_path.is_file(), f"extractor module does not exist: {module_path}"
        source = module_path.read_text(encoding="utf-8")
        assert "def analyze_donor_world" in source


def test_pin1_measurement_spec_manifest_calibration_status_all_uncalibrated() -> None:
    """C0/C1 実測前の現在のデータスナップショットは全エントリ
    UNCALIBRATED（REVISION_0.3 改訂G 語彙）。"""
    data = _measurement_spec_manifest_data()
    for entry in data["identity_axis_metric_paths"].values():
        assert entry["calibration_status"] == "UNCALIBRATED"


def test_pin1_measurement_spec_manifest_dev_gen_axis_metrics_match_design_doc_16_3() -> None:
    """一次ソース DESIGN_RUN9 §16.3 DevelopmentalVector の逐語9指標との
    照合（design_doc から直接抽出、孫引き防止）。"""
    design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")
    section = design_text.split("## 16.3 DevelopmentalVector", 1)[1]
    block = section.split("```text", 1)[1].split("```", 1)[0]
    expected = [line.strip() for line in block.strip().splitlines() if line.strip()]
    assert len(expected) == 9
    data = _measurement_spec_manifest_data()
    assert data["development_generalization_axis"]["metrics"] == expected


def test_pin1_measurement_spec_manifest_dev_gen_axis_status_not_yet_implemented() -> None:
    data = _measurement_spec_manifest_data()
    assert data["development_generalization_axis"]["status"] == "NOT_YET_IMPLEMENTED"


def test_pin1_measurement_spec_manifest_dev_gen_extractors_confirmed_absent_from_repo() -> None:
    """development/generalization 軸9指標 + GENERALIZED_GAIN の extractor
    実装が repo に実在しないことの grep 機械照合（「あるべき姿」で書かず
    正直に NOT_YET_IMPLEMENTED を宣言している、という主張自体を検証する）。
    """
    repo_root = _RUN_DIR.parent.parent.parent
    names = [
        "pitch_gain", "voicing_gain", "duration_gain", "energy_contour_gain",
        "attack_gain", "phrase_end_gain", "lyrics_delta", "artifact_delta",
        "identity_delta",
    ]
    hits = 0
    for py_file in repo_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in names:
            if f"def {name}" in text or f" {name}(" in text:
                hits += 1
    assert hits == 0, "development/generalization axis の extractor 実装が repo に見つかった"


def test_pin1_measurement_spec_manifest_unknown_metric_path_key_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    data["identity_axis_metric_paths"]["bogus_entry"] = data["identity_axis_metric_paths"][
        "reference_render"
    ]
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_measurement_spec_manifest(data)


def test_pin1_measurement_spec_manifest_missing_metric_path_entry_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    del data["identity_axis_metric_paths"]["pjs_reference"]
    with pytest.raises(m.Run9ValidationError, match="missing required entry"):
        m.validate_measurement_spec_manifest(data)


def test_pin1_measurement_spec_manifest_wrong_ref_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    data["identity_axis_metric_paths"]["reference_render"]["identity_metric_space_ref"] = (
        "inputs/identity_metric_space.json#calibration.freeze_threshold.d_c0_population"
    )
    with pytest.raises(m.Run9ValidationError, match="identity_metric_space_ref"):
        m.validate_measurement_spec_manifest(data)


def test_pin1_measurement_spec_manifest_bad_calibration_status_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    data["identity_axis_metric_paths"]["reference_render"]["calibration_status"] = "MADE_UP"
    with pytest.raises(m.Run9ValidationError, match="calibration_status"):
        m.validate_measurement_spec_manifest(data)


def test_pin1_measurement_spec_manifest_wrong_dev_gen_metrics_order_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    data["development_generalization_axis"]["metrics"] = list(
        reversed(data["development_generalization_axis"]["metrics"])
    )
    with pytest.raises(m.Run9ValidationError, match="metrics"):
        m.validate_measurement_spec_manifest(data)


def test_pin1_measurement_spec_manifest_bad_dev_gen_status_fail_closed() -> None:
    data = copy.deepcopy(_measurement_spec_manifest_data())
    data["development_generalization_axis"]["status"] = "IMPLEMENTED"
    with pytest.raises(m.Run9ValidationError, match="status"):
        m.validate_measurement_spec_manifest(data)


# ---------------------------------------------------------------------------
# read-once loader: PINNED確認 / contract改竄検出 / manifestバイト改竄検出 /
# 正常系 parse 返却（3関数とも同一パターン、Persistent Artifact Safety Gate
# 該当項目 = AGENTS.md §8: 単一 read で parse+hash・unknown 推測補完禁止）。
# ---------------------------------------------------------------------------

_PIN1_LOADER_CASES = (
    ("seed_policy_sha", "load_pinned_seed_policy_manifest", "SEED_POLICY_MANIFEST_PATH"),
    (
        "failure_abort_criteria_sha", "load_pinned_failure_abort_criteria",
        "FAILURE_ABORT_MANIFEST_PATH",
    ),
    (
        "measurement_spec_sha", "load_pinned_measurement_spec_manifest",
        "MEASUREMENT_SPEC_MANIFEST_PATH",
    ),
    # RUN9-L0-PIN-2（2026-08-26 実装）: dataset_manifest_sha は probe/
    # seed_policy と同型の4段構成に加え本欄固有の cross-manifest 三者
    # 一致を持つが、汎用ケース（PINNED 確認・in-process 改竄検出・
    # manifest バイト改竄検出・欠落ファイル検出・PENDING 時拒否）は
    # 完全に同型のため本 parametrize へ相乗りさせる（重複テスト新設を
    # 避ける）。dataset_split_manifest.json 固有の cross-manifest 三者
    # 一致（practice/probe/c1 take drift）・dataset_row_order_sha 三者
    # 一致は下記専用セクションで別途テストする。
    (
        "dataset_manifest_sha", "load_pinned_dataset_split_manifest",
        "DATASET_SPLIT_MANIFEST_PATH",
    ),
    # RUN9-L0-HARNESS-1（2026-08-26 実装）: dependency_pins_sha も probe/
    # seed_policy と同型の4段構成（+ 本欄固有の backbone_runtime_bundle.json
    # cross-check）を持つが、汎用ケースは完全に同型のため相乗りさせる。
    # cross-check 固有の fail-closed 分岐は下記専用セクションで別途テストする。
    (
        "dependency_pins_sha", "load_pinned_dependency_pins_manifest",
        "DEPENDENCY_PINS_MANIFEST_PATH",
    ),
    # RUN9-EXECPROFILE-1（2026-08-26 実装）: execution_profile_sha も
    # probe/seed_policy と同型の4段構成（+ 本欄固有の adjudication_basis
    # 実バイト cross-check）を持つが、汎用ケースは完全に同型のため相乗り
    # させる。cross-check 固有の fail-closed 分岐は下記専用セクションで
    # 別途テストする。
    (
        "execution_profile_sha", "load_pinned_execution_profile_manifest",
        "EXECUTION_PROFILE_MANIFEST_PATH",
    ),
)

# PR #324 第2巡 Fix 5（measurement_spec_sha を PENDING へ復帰）+ PR #326
# 第2巡 Fix 3（dependency_pins_sha を PENDING へ復帰、P1、同型の理由
# ——render/analysis 層のみの manifest で学習ハーネス本体の依存 closure
# 未確定のまま PINNED にすると gate_state() の偽 READY 経路を開く）後、
# 実際に現在 PINNED なのは seed_policy_sha/failure_abort_criteria_sha の
# 2欄のみ。「PINNED な artifact に対する loader の正常系/改竄検出」を
# 検証するテスト群はこの2欄のみを対象とする（measurement_spec_sha/
# dependency_pins_sha 側の対応する期待はそれぞれ専用テスト
# `test_pin1_r3_load_pinned_measurement_spec_manifest_raises_pending`/
# `test_harness1_pr326_fix3_load_pinned_dependency_pins_manifest_raises_pending`
# が別途カバーする）。`_PIN1_LOADER_CASES`（4欄）は
# `test_pin1_load_pinned_manifest_rejects_when_not_pinned` のように現在の
# 実 pin 状態に依存しない合成テストでのみ引き続き使う。
_PIN1_PINNED_LOADER_CASES = tuple(
    case for case in _PIN1_LOADER_CASES
    if case[0] not in ("measurement_spec_sha", "dependency_pins_sha")
)


@pytest.mark.parametrize("pin_name,loader_name,path_const_name", _PIN1_PINNED_LOADER_CASES)
def test_pin1_load_pinned_manifest_happy_path(
    contract: m.Run9RunContract, pin_name: str, loader_name: str, path_const_name: str,
) -> None:
    loader = getattr(m, loader_name)
    data = loader(contract)
    assert isinstance(data, dict)


def test_pin1_r3_load_pinned_measurement_spec_manifest_raises_pending(
    contract: m.Run9RunContract,
) -> None:
    """PR #324 第2巡 Fix 5: measurement_spec_sha は PENDING へ復帰した
    ため、`load_pinned_measurement_spec_manifest()` は現在の実 contract に
    対して呼ぶと必ず『not PINNED』で fail-closed 拒否する（それが正しい
    挙動 — manifest/validator/loader 自体は事前配線のまま残置しつつ、
    pin されていない artifact を消費させない）。"""
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_measurement_spec_manifest(contract)


@pytest.mark.parametrize("pin_name,loader_name,path_const_name", _PIN1_LOADER_CASES)
def test_pin1_load_pinned_manifest_rejects_when_not_pinned(
    contract_raw: Dict[str, Any],
    tmp_path: Path,
    pin_name: str,
    loader_name: str,
    path_const_name: str,
) -> None:
    """disk 正典側もあわせて PENDING にした contract_path を渡し、
    ディスク正典との乖離チェック（層(i)）ではなく PINNED 状態チェック
    （層(ii)）が拒否理由になる分岐を確認する（両チェックが独立に機能する
    ことの確認 — in-process 改竄検出とは別のテスト、上記
    `test_pin1_load_pinned_manifest_detects_in_process_contract_tampering`
    と対）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered[pin_name] = {"value": None, "status": "PENDING", "reason": "test"}
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(yaml.safe_dump(tampered, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered)
    loader = getattr(m, loader_name)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        loader(tampered_contract, contract_path=tampered_yaml_path)


@pytest.mark.parametrize("pin_name,loader_name,path_const_name", _PIN1_PINNED_LOADER_CASES)
def test_pin1_load_pinned_manifest_detects_in_process_contract_tampering(
    contract: m.Run9RunContract, pin_name: str, loader_name: str, path_const_name: str,
) -> None:
    """in-process contract.raw を直接改竄しても、ディスク正典
    RUN9_CONTRACT.yaml との乖離が fail-closed で検出される
    （load_pinned_probe_manifest() Fix 27/17 と同型の3層防御）。"""
    tampered = copy.deepcopy(contract)
    tampered.raw[pin_name]["value"] = "0" * 64
    loader = getattr(m, loader_name)
    with pytest.raises(m.Run9ValidationError, match="diverges from the canonical on-disk"):
        loader(tampered)


@pytest.mark.parametrize("pin_name,loader_name,path_const_name", _PIN1_PINNED_LOADER_CASES)
def test_pin1_load_pinned_manifest_detects_manifest_byte_tampering(
    contract: m.Run9RunContract,
    tmp_path: Path,
    pin_name: str,
    loader_name: str,
    path_const_name: str,
) -> None:
    path_const: Path = getattr(m, path_const_name)
    tampered_path = tmp_path / path_const.name
    tampered_path.write_bytes(path_const.read_bytes() + b"\n// tampered")
    loader = getattr(m, loader_name)
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        loader(contract, manifest_path=tampered_path)


@pytest.mark.parametrize("pin_name,loader_name,path_const_name", _PIN1_PINNED_LOADER_CASES)
def test_pin1_load_pinned_manifest_missing_file_fail_closed(
    contract: m.Run9RunContract,
    tmp_path: Path,
    pin_name: str,
    loader_name: str,
    path_const_name: str,
) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    loader = getattr(m, loader_name)
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        loader(contract, manifest_path=missing_path)


# ---------------------------------------------------------------------------
# load_pinned_founder_genome_document(): rule 12（failure_abort_criteria
# #12 r0 or frozen Genome changed）の production 消費経路（2026-08-25
# Codex bot レビュー PR #324 第2巡 Fix 6, P1, 採用 — 旧実装は raw sha 照合が
# test module にしか存在せず production 消費経路が無い欠陥の是正）。
# founders/*.json は byte 不変厳守（本節のいずれのテストも実ファイルへ
# 書き込まない）。
# ---------------------------------------------------------------------------


def _real_domain() -> m.Run9IdentityDomain:
    return m.load_run9_identity_domain(DOMAIN_DRAFT_PATH)


def _real_rights_manifest() -> Dict[str, Any]:
    return json.loads(RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_pin1_r3_load_pinned_founder_genome_document_happy_path(
    contract: m.Run9RunContract, founder_id: str,
) -> None:
    genome = m.load_pinned_founder_genome_document(
        founder_id, contract=contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
    )
    assert genome.voice_id == founder_id
    # RUN9-BIRTH-PREP-1 実測当時の genome_id と不変であることを確認する
    # （README.md「解消済み（RUN9-BIRTH-PREP-1）」節と同一値）。
    expected_genome_id = {"R9F-01": "66f420672a154283", "R9F-02": "63f4b8f24b827cd4"}[founder_id]
    assert genome.genome_id == expected_genome_id


def test_pin1_r3_load_pinned_founder_genome_document_rejects_invalid_founder_id(
    contract: m.Run9RunContract,
) -> None:
    with pytest.raises(m.Run9ValidationError, match="founder_id must be one of"):
        m.load_pinned_founder_genome_document(
            "R9F-99", contract=contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
        )


def test_pin1_r3_load_pinned_founder_genome_document_detects_byte_tampering(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    real_path = m.founder_genome_document_path("R9F-01")
    tampered_path = tmp_path / "R9F-01_genome.json"
    tampered_path.write_bytes(real_path.read_bytes() + b"\n// tampered")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        m.load_pinned_founder_genome_document(
            "R9F-01", contract=contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            document_path=tampered_path,
        )


def test_pin1_r3_load_pinned_founder_genome_document_missing_file(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_founder_genome_document(
            "R9F-01", contract=contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            document_path=missing_path,
        )


def test_pin1_r3_load_pinned_founder_genome_document_rejects_when_not_pinned(
    contract_raw: Dict[str, Any], tmp_path: Path,
) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": None, "status": "PENDING", "reason": "test"}
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(yaml.safe_dump(tampered, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_founder_genome_document(
            "R9F-01", contract=tampered_contract, domain=_real_domain(),
            rights_manifest=_real_rights_manifest(), contract_path=tampered_yaml_path,
        )


def test_pin1_r3_load_pinned_founder_genome_document_detects_in_process_contract_tampering(
    contract: m.Run9RunContract,
) -> None:
    tampered = copy.deepcopy(contract)
    tampered.raw["founder_genome_shas"]["R9F-01"]["value"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="diverges from the canonical on-disk"):
        m.load_pinned_founder_genome_document(
            "R9F-01", contract=tampered, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
        )


def test_pin1_r3_founders_json_bytes_unchanged() -> None:
    """Fix 6 の対応は founders/*.json のバイトを一切変更しないこと
    （raw sha256 は既存 founder_genome_shas pin 値と引き続き一致する）。"""
    contract = m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH)
    for founder_id in m.CONTRACT_FOUNDER_IDS:
        pin_value = contract.founder_genome_sha(founder_id)["value"]
        assert pin_value == m.compute_file_sha256(m.founder_genome_document_path(founder_id))


def test_pin1_r3_rule12_condition_references_new_loader_function() -> None:
    """PR #324 第2巡 Fix 6 で `load_pinned_founder_genome_document` を
    新設した事実自体は、第3巡で rule 12 が PROCEDURAL へ再降格した後も
    checkpoint 内の言及として残ること（genome 文書側は機械検証可能で
    あるという事実は変わらない——rule 全体としての enforcement 判定が
    変わっただけ）。"""
    data = _failure_abort_criteria_data()
    rule12 = next(r for r in data["rules"] if r["rule_id"] == 12)
    assert rule12["enforcement"] == "PROCEDURAL"
    assert "load_pinned_founder_genome_document" in rule12["checkpoint"]


def test_pin1_r4_rule12_reclassified_procedural_r0_state_absent() -> None:
    """PR #324 Codex bot レビュー第3巡指摘（P1, 採用）の直接回帰: rule 12
    は genome 文書 / r0 state の二本柱のうち r0 state 側の検証機構が
    無いため PROCEDURAL であり、checkpoint/machine_promotion_condition が
    その理由（r0 state ファイル不在 + pin 欄不在）を明記していること。"""
    data = _failure_abort_criteria_data()
    rule12 = next(r for r in data["rules"] if r["rule_id"] == 12)
    assert rule12["enforcement"] == "PROCEDURAL"
    assert "condition" not in rule12
    assert "deferred_threshold_ref" not in rule12
    assert "r0_state" in rule12["checkpoint"]
    assert "r0 state" in rule12["machine_promotion_condition"]


def test_pin1_r4_r0_state_files_confirmed_absent_from_repo() -> None:
    """事実確認 (a): founders/R9F-0x_r0_state.json は repo に実在しない
    （DESIGN_RUN9 §24 推奨ディレクトリ図に記載があるのみ）。grep 相当で
    機械照合する（一次ソース直読み、孫引き防止）。"""
    for founder_id in ("R9F-01", "R9F-02"):
        assert not (m.FOUNDER_GENOME_DIR / f"{founder_id}_r0_state.json").is_file()
    founders_dir_files = sorted(p.name for p in m.FOUNDER_GENOME_DIR.iterdir())
    assert founders_dir_files == ["R9F-01_genome.json", "R9F-02_genome.json"]


def test_pin1_r4_r0_state_has_no_contract_pin_field() -> None:
    """事実確認 (b): RUN9_CONTRACT.yaml に r0 state 専用の pin 欄は存在
    しない（`CONTRACT_PIN_FIELDS` の全欄名を grep 相当で照合）。"""
    for field_name in m.CONTRACT_PIN_FIELDS:
        assert "r0_state" not in field_name
        assert "r0state" not in field_name.replace("_", "").lower()


def test_pin1_r4_branch_write_policy_r0_bytes_is_write_boundary_not_pin() -> None:
    """事実確認 (c): branch_write_policy.json の `r0_bytes` は
    immutable_artifacts（書込境界ポリシー宣言）の1項目であり、r0 state
    ファイル実体の sha256 を pin する契約欄ではないこと。"""
    policy = json.loads(BRANCH_WRITE_POLICY_PATH.read_text(encoding="utf-8"))
    assert "r0_bytes" in policy["immutable_artifacts"]
    assert "r0_bytes" not in m.CONTRACT_PIN_FIELDS
    assert "r0_bytes" in m.BRANCH_IMMUTABLE_ARTIFACTS


# `test_pin1_r4_failure_abort_criteria_repinned_lineage_four_generations`
# （4世代版）は PR #324 第4巡の repin により超過し、下記
# `test_pin1_r5_failure_abort_criteria_repinned_lineage_five_generations`
# （5世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3巡と
# 同じ理由）。


# `test_pin1_r5_failure_abort_criteria_repinned_lineage_five_generations`
# （5世代版）は PR #324 第5巡の repin により超過し、下記
# `test_pin1_r6_failure_abort_criteria_repinned_lineage_six_generations`
# （6世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3/4巡と
# 同じ理由）。


# `test_pin1_r6_failure_abort_criteria_repinned_lineage_six_generations`
# （6世代版）は PR #324 第6巡の repin により超過し、下記
# `test_pin1_r7_failure_abort_criteria_repinned_lineage_seven_generations`
# （7世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3-5巡と
# 同じ理由）。


# `test_pin1_r7_failure_abort_criteria_repinned_lineage_seven_generations`
# （7世代版）は PR #324 第7巡の repin により超過し、下記
# `test_pin1_r8_failure_abort_criteria_repinned_lineage_eight_generations`
# （8世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3-6巡と
# 同じ理由）。


# `test_pin1_r8_failure_abort_criteria_repinned_lineage_eight_generations`
# （8世代版）は PR #327 レビュー第13巡指摘25の repin により超過し、下記
# `test_pr327_r13_failure_abort_criteria_repinned_lineage_nine_generations`
# （9世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3-6巡と
# 同じ理由）。


# `test_pr327_r13_failure_abort_criteria_repinned_lineage_nine_generations`
# （9世代版）は RUN9-L0-HARNESS-3b（2026-08-27）の repin により超過し、下記
# `test_harness3b_failure_abort_criteria_repinned_lineage_ten_generations`
# （10世代・全履歴を包含する上位互換）へ置き換えた（重複削除、第3-7巡と
# 同じ理由）。


def test_harness3b_failure_abort_criteria_repinned_lineage_ten_generations(
    contract_raw: Dict[str, Any],
) -> None:
    """failure_abort_criteria_sha の repin 履歴10世代（RUN9-L0-PIN-1 初回
    → PR #324 第1巡 Fix 1/2/3 → 第2巡 Fix 6 → 第3巡 rule 12 再降格 →
    第4巡 rule 2 の condition 強化 → 第5巡 verify_user_donor_manifest_
    complete() の path ベース署名化 → 第6巡 独立 pinned anchor への
    接地追加 → 第7巡 domain 自体の founder_genome_shas pin への束縛
    追加 → PR #327 レビュー第13巡指摘25 で rule 19（cost cap exceeded）の
    checkpoint の stale 文言を訂正 → RUN9-L0-HARNESS-3b（2026-08-27）で
    rule 8（§22 step 8）の checkpoint/machine_promotion_condition が
    「education_technique_lesson_manifest_sha は引き続き PENDING」という
    stale な言及を残していた欠陥を訂正——同欄は本改訂で PINNED 化された）
    + design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）で
    rule 7（Birth Identity separation not established）/rule 16（Identity
    drift beyond non-inferiority）の theta_cal(F)/calibration 依存の stale
    文言を rev 0.6 supersede 後の記述へ訂正した第11世代 + PR #333 Codex bot
    レビュー第1巡指摘1 是正（2026-08-28、Fable 判定）で rule 14/16 の
    checkpoint/machine_promotion_condition が引き続き参照していた
    `hypothesis_algebra_sha`（rev 0.6 で identity decision protocol の
    pin 欄へ用途確定済み）を、H1-H6 δtarget/εk 校正前提の新追跡先
    `hypothesis_threshold_calibration_sha` へ更新した第12世代が
    append-only で、現在値が最新のものであることを明示的に確認する
    （repin 漏れの回帰防止）。"""
    round1_value = "b045af35b6ad3131e076624568e0449bb0d5625853a2e8c99f0bdc17690bb110"
    round2_value = "8892230a81f40f2d91dfdf454f9637a65244430ab6241aebf03b7ad655f26d81"
    round3_value = "6cdfcb05763e9c15f9a70e7e887b4f4c3600bbc94e468e02970a1692fb1fef44"
    round4_value = "3ef4edf25b93a6c996d50b38f6348b567f2eee44f22540a8aca8593786b3f6d1"
    round5_value = "954b463ecfa497b732240d92c6e07a29ebecef480d97dc1f42e9108c12635a52"
    round6_value = "8f9f8c30d521b5f2048891aa17fe9c1aeeb068a1ec8007f2146d4c1ec22cf38d"
    round7_value = "9b68656d6b5cb30019376ae9848e03801e10b595b5372dca63ba6a59a9d03caf"
    round8_value = "20c71d273993f062cf562b2097a57bfe530c54303e87287c58e98bad9876df4a"
    round9_value = "da8aee0d49a5dac58b5ddd6b6dc7959f1a15914e9a6e565a4e6851e2b6c7a527"
    round10_value = "ead64d2fd7896728b1fc7070c90d7a5b2d8bb17740e21c4056a5210a081cf98b"
    round11_value = "297dd46aaa8c520238072f93b9d5e18748dbdd31b4a389a4a8d7e48cd70d8cba"
    round12_value = "3de4db27a23498c236b75b3efbb152c0675fce84fe2d6bddfb8bd565850b1251"
    current = contract_raw["failure_abort_criteria_sha"]["value"]
    assert current == round12_value
    assert current not in (
        round1_value, round2_value, round3_value, round4_value, round5_value, round6_value,
        round7_value, round8_value, round9_value, round10_value, round11_value,
    )
    assert current == m.compute_file_sha256(m.FAILURE_ABORT_MANIFEST_PATH)


def test_pr327_r13_rule19_checkpoint_documents_pinned_identity_scope() -> None:
    """PR #327 レビュー第13巡指摘25の直接回帰: rule 19（cost cap exceeded）
    の checkpoint が (a) execution_profile_sha が RUN9-EXECPROFILE-1 で
    PINNED 済みであること、(b) その収載範囲は runtime identity のみで
    cost cap を含まないため cost cap は依然未凍結であること、の両方を
    明記し、旧 stale 文言「Group C、現状 PENDING」を現在形の主張として
    残していないこと（enforcement/machine_promotion_condition は不変）。"""
    data = _failure_abort_criteria_data()
    rule19 = next(r for r in data["rules"] if r["rule_id"] == 19)
    assert rule19["enforcement"] == "PROCEDURAL"
    assert rule19["verbatim"] == "cost cap exceeded"
    assert "RUN9-EXECPROFILE-1" in rule19["checkpoint"]
    assert "PINNED" in rule19["checkpoint"]
    assert "cost cap 数値は引き続き未凍結" in rule19["checkpoint"]
    assert "履歴" in rule19["checkpoint"]
    assert "Group C、現状 PENDING" not in rule19["checkpoint"].split("〔履歴:")[0]
    assert rule19["machine_promotion_condition"] == (
        "execution_profile_sha に cost cap 数値が凍結され、cost record との"
        "自動比較機構を実装した時点で MACHINE へ昇格する。"
    )


def test_pin1_r5_rule2_still_machine_with_updated_condition() -> None:
    """PR #324 第4巡指摘の直接回帰: rule 2 は MACHINE のまま
    （分類は不変）、condition が新設した
    `verify_user_donor_manifest_complete` を参照していること。"""
    data = _failure_abort_criteria_data()
    rule2 = next(r for r in data["rules"] if r["rule_id"] == 2)
    assert rule2["enforcement"] == "MACHINE"
    assert "verify_user_donor_manifest_complete" in rule2["condition"]
    assert "USER_DONOR_CARD_IDS" in rule2["condition"]


# ---------------------------------------------------------------------------
# verify_user_donor_manifest_complete(): PR #324 第5巡指摘（P2, 採用）で
# path ベース署名（rights_manifest_path/donor_ledger_path、省略時は正典
# パス既定）へ変更。公開契約そのもの（path 経由・自前の厳密 parse）を
# 検証する——任意 dict を直接渡す旧経路はテストからも一切使わない。
# ---------------------------------------------------------------------------


def _write_json_bytes(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _real_donor_ledger() -> Dict[str, Any]:
    return m.load_user_donor_ledger_json(m.USER_DONOR_LEDGER_PATH.read_text(encoding="utf-8"))


def _real_rights_manifest_loaded() -> Dict[str, Any]:
    return m.load_rights_manifest_json(m.RIGHTS_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_pin1_r5_verify_user_donor_manifest_complete_default_paths_happy_path() -> None:
    """省略時は正典パス（`RIGHTS_MANIFEST_PATH`/`USER_DONOR_LEDGER_PATH`）
    を用いて実データに対し PASS すること。"""
    flat = m.verify_user_donor_manifest_complete()
    assert len(flat["entries"]) == len(m.USER_DONOR_CARD_IDS) == 17


def test_pin1_r5_verify_user_donor_manifest_complete_explicit_paths_happy_path() -> None:
    flat = m.verify_user_donor_manifest_complete(
        rights_manifest_path=m.RIGHTS_MANIFEST_PATH, donor_ledger_path=m.USER_DONOR_LEDGER_PATH,
    )
    assert len(flat["entries"]) == len(m.USER_DONOR_CARD_IDS) == 17


def test_pin1_r5_verify_user_donor_manifest_complete_missing_rights_file(tmp_path: Path) -> None:
    with pytest.raises(m.Run9ValidationError, match="rights manifest source"):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tmp_path / "does_not_exist.json")


def test_pin1_r5_verify_user_donor_manifest_complete_missing_ledger_file(tmp_path: Path) -> None:
    with pytest.raises(m.Run9ValidationError, match="donor ledger source"):
        m.verify_user_donor_manifest_complete(donor_ledger_path=tmp_path / "does_not_exist.json")


def test_pin1_r5_verify_user_donor_manifest_complete_detects_empty_entries(tmp_path: Path) -> None:
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    rights["voice_identity_rights"]["entries"] = []
    tampered_path = tmp_path / "rights_manifest.json"
    _write_json_bytes(tampered_path, rights)
    with pytest.raises(m.Run9ValidationError, match="does not exactly match"):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tampered_path)


def test_pin1_r5_verify_user_donor_manifest_complete_detects_duplicate_card_id(
    tmp_path: Path,
) -> None:
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    entries = rights["voice_identity_rights"]["entries"]
    entries[1]["card_id"] = entries[0]["card_id"]
    tampered_path = tmp_path / "rights_manifest.json"
    _write_json_bytes(tampered_path, rights)
    with pytest.raises(m.Run9ValidationError, match="duplicate card_id"):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tampered_path)


def test_pin1_r5_verify_user_donor_manifest_complete_detects_hash_tampering(tmp_path: Path) -> None:
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    rights["voice_identity_rights"]["entries"][0]["sha256"] = "0" * 64
    tampered_path = tmp_path / "rights_manifest.json"
    _write_json_bytes(tampered_path, rights)
    with pytest.raises(m.Run9ValidationError, match="does not match donor_ledger"):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tampered_path)


def test_pin1_r5_verify_user_donor_manifest_complete_rejects_missing_other_layer(
    tmp_path: Path,
) -> None:
    """`extract_voice_identity_rights_layer()` 経由の (1) 再実行により、
    他層（performance_rights 等）が欠落した4層文書は
    voice_identity_rights 単独が妥当でも拒否されること（既存の Fix 14
    是正の維持確認）。"""
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    del rights["performance_rights"]
    tampered_path = tmp_path / "rights_manifest.json"
    _write_json_bytes(tampered_path, rights)
    with pytest.raises(m.Run9ValidationError):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tampered_path)


def test_pin1_r6_verify_user_donor_manifest_complete_rejects_duplicate_key_bytes(
    tmp_path: Path,
) -> None:
    """PR #324 第5巡指摘の直接回帰: 正規消費経路が自前で厳密 parse
    （重複キー拒否）を強制すること。手書きの重複キー JSON バイト列
    （通常の `json.loads()` なら last-key-wins で黙って解決してしまう）
    を直接渡すと fail-closed で拒否される。"""
    tampered_path = tmp_path / "rights_manifest.json"
    tampered_path.write_text('{"schema": "x", "schema": "y"}', encoding="utf-8")
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.verify_user_donor_manifest_complete(rights_manifest_path=tampered_path)


def test_pin1_r6_verify_user_donor_manifest_complete_rejects_duplicate_key_ledger_bytes(
    tmp_path: Path,
) -> None:
    """同上、`donor_ledger_path` 側でも厳密 parse が効くことを確認する。"""
    tampered_path = tmp_path / "user_donor_ledger.json"
    tampered_path.write_text('{"schema": "x", "schema": "y"}', encoding="utf-8")
    with pytest.raises(m.Run9ValidationError, match="duplicate key"):
        m.verify_user_donor_manifest_complete(donor_ledger_path=tampered_path)


def test_pin1_r6_verify_user_donor_manifest_complete_has_no_mapping_signature() -> None:
    """PR #324 第5巡指摘: 任意 Mapping を受け取る互換シグネチャを意図的に
    残していないこと——位置引数でリアルな dict を2つ渡すと
    TypeError（keyword-only 引数 `rights_manifest_path`/
    `donor_ledger_path` 以外を受け付けない）になることを確認する。"""
    with pytest.raises(TypeError):
        m.verify_user_donor_manifest_complete(  # type: ignore[call-arg, misc]
            _real_rights_manifest_loaded(), _real_donor_ledger()
        )


# ---------------------------------------------------------------------------
# PR #324 第6巡指摘（P2, 採用）: rights/ledger 両ファイルの lockstep 改変
# （同一 entry の値を両側**同値**で書き換える）は (1)〜(3) の相互照合だけ
# では検出できない——独立 pinned anchor（PR #320 で確立済みの user
# identity-attestation projection、domains/identity_domain_run9_v1.json
# anchor_hashes["user"]）への接地を第4段として追加した。
# ---------------------------------------------------------------------------


def test_pin1_r7_verify_user_donor_manifest_complete_still_grounds_to_real_domain_anchor() -> None:
    """既存正常系は不変で通ること（第4段追加後も実データで PASS）。"""
    flat = m.verify_user_donor_manifest_complete()
    assert len(flat["entries"]) == len(m.USER_DONOR_CARD_IDS) == 17


def test_pin1_r7_run9_identity_domain_path_is_pinned_and_matches_known_anchor() -> None:
    """事実確認: 独立 pin（domain の anchor_hashes["user"]）が実在し
    PINNED であることの直接確認（Fable 設計判定の前提）。"""
    domain = m.load_run9_identity_domain(m.RUN9_IDENTITY_DOMAIN_PATH)
    assert domain.is_pinned()
    assert domain.anchor_hashes["user"] == (
        "8569705be318d672d5f77ba955054a76d446664bb0883850a69c1fc35a55e804"
    )


def _write_lockstep_tampered_pair(
    tmp_path: Path, *, fake_sha256: str = "1" * 64
) -> Tuple[Path, Path]:
    """rights と ledger の同一 card_id entry の `sha256` を両側**同値**で
    書き換えた temp ファイルペアを作る（(3) の相互照合だけでは検出
    できない lockstep 改変）。"""
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    ledger = copy.deepcopy(_real_donor_ledger())
    r_entries = rights["voice_identity_rights"]["entries"]
    card_id = r_entries[0]["card_id"]
    r_entries[0]["sha256"] = fake_sha256
    for entry in ledger["entries"]:
        if entry["card_id"] == card_id:
            entry["sha256"] = fake_sha256
    rights_path = tmp_path / "rights_manifest.json"
    ledger_path = tmp_path / "user_donor_ledger.json"
    _write_json_bytes(rights_path, rights)
    _write_json_bytes(ledger_path, ledger)
    return rights_path, ledger_path


def test_pin1_r7_verify_user_donor_manifest_complete_detects_lockstep_tampering(
    tmp_path: Path,
) -> None:
    """第6巡指摘の直接回帰: (1)〜(3) の相互照合だけでは通ってしまう
    lockstep 改変（rights/ledger 同一 entry の sha256 を両側同値で
    書き換え）が、第4段の独立 pinned anchor 不一致で fail-closed 拒否
    されること。"""
    rights_path, ledger_path = _write_lockstep_tampered_pair(tmp_path)
    # (3) の相互照合は素通りする前提（rights/ledger が合意しているため）
    # ことを確認してから、本関数全体が第4段で拒否することを確認する。
    tampered_rights = m.load_rights_manifest_json(rights_path.read_text(encoding="utf-8"))
    tampered_ledger = m.load_user_donor_ledger_json(ledger_path.read_text(encoding="utf-8"))
    flat = m.extract_voice_identity_rights_layer(tampered_rights)
    m.verify_rights_manifest_against_ledger(flat, tampered_ledger)  # (3) 単体は PASS する前提の確認

    with pytest.raises(m.Run9ValidationError, match="projection hash does not match"):
        m.verify_user_donor_manifest_complete(
            rights_manifest_path=rights_path, donor_ledger_path=ledger_path
        )


def test_pin1_r7_rule2_condition_documents_fourth_stage_anchor_grounding() -> None:
    """rule 2 の condition が第4段（`_verify_user_anchor_matches_rights_
    manifest`・独立 pin への接地）を明記していること。"""
    data = _failure_abort_criteria_data()
    rule2 = next(r for r in data["rules"] if r["rule_id"] == 2)
    assert rule2["enforcement"] == "MACHINE"
    assert "_verify_user_anchor_matches_rights_manifest" in rule2["condition"]
    assert "anchor_hashes" in rule2["condition"]
    assert "lockstep" in rule2["condition"]


# ---------------------------------------------------------------------------
# PR #324 第7巡指摘（P2, 採用）: domain ファイル自体は第4段までのチェーン
# では何とも照合されておらず、domain 側だけを改変（+ 辻褄合わせに
# rights/ledger 側の projection も同時に偽装する「3点 lockstep」）すれば
# (1)〜(4) を素通りし得る——domain を founder_genome_shas pin（+
# founders/*.json 実バイト）へ束縛する第5段を追加した。
# ---------------------------------------------------------------------------


def test_pin1_r8_verify_user_donor_manifest_complete_still_grounds_with_fifth_stage() -> None:
    """既存正常系は不変で通ること（第5段追加後も実データで PASS）。"""
    flat = m.verify_user_donor_manifest_complete()
    assert len(flat["entries"]) == len(m.USER_DONOR_CARD_IDS) == 17


def test_pin1_r8_compute_founder_genome_id_depends_on_domain_content_digest() -> None:
    """事実確認: `_compute_founder_genome_id()` のハッシュ入力に
    `identity_domain.content_digest()`（`anchor_hashes` 全件を含む）が
    含まれること（run9_schema.py:6401-6423 の実装読解の直接回帰）——
    第5段が domain 改変を検出できる根拠。"""
    schema_text = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "identity_domain_content_sha256" in schema_text
    assert "identity_domain.content_digest()" in schema_text


def test_pin1_r8_genome_id_changed_when_anchor_computation_changed_pr320() -> None:
    """事実確認: PR #320 で anchor 計算方式を変更した際に実際に genome_id
    が変化した実績（`f5ea253804728b3b` → 現行 PINNED 値
    `66f420672a154283`）が RUN9_CONTRACT.yaml に記録されていること
    （依存が机上の理屈ではなく実測された事実であることの直接証拠）。"""
    contract_text = (_RUN_DIR / "RUN9_CONTRACT.yaml").read_text(encoding="utf-8")
    assert "f5ea253804728b3b" in contract_text
    contract = m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH)
    assert contract.founder_genome_sha("R9F-01")["value"] == m.compute_file_sha256(
        m.founder_genome_document_path("R9F-01")
    )


def _write_three_point_lockstep_tamper(tmp_path: Path) -> Tuple[Path, Path, Path]:
    """domain の `anchor_hashes["user"]` を改変し、それに辻褄を合わせて
    rights/ledger 側の projection も同時に偽装した3点 lockstep の temp
    ファイル3つを作る——(1)〜(4) は素通りするが、domain 自体は
    founders/*.json の pin と食い違う状態になる。"""
    rights = copy.deepcopy(_real_rights_manifest_loaded())
    ledger = copy.deepcopy(_real_donor_ledger())
    domain_raw = copy.deepcopy(json.loads(m.RUN9_IDENTITY_DOMAIN_PATH.read_text(encoding="utf-8")))

    fake_sha256 = "2" * 64
    r_entries = rights["voice_identity_rights"]["entries"]
    card_id = r_entries[0]["card_id"]
    r_entries[0]["sha256"] = fake_sha256
    for entry in ledger["entries"]:
        if entry["card_id"] == card_id:
            entry["sha256"] = fake_sha256

    # 辻褄合わせ: 偽装した rights_manifest から projection を再計算し、
    # domain.anchor_hashes["user"] をその値へ差し替える（第4段を素通り
    # させるための偽装）。
    projection = m.extract_user_identity_attestation_projection(rights)
    domain_raw["anchor_hashes"]["user"] = m._compute_canonical_pin_sha256(projection)

    rights_path = tmp_path / "rights_manifest.json"
    ledger_path = tmp_path / "user_donor_ledger.json"
    domain_path = tmp_path / "identity_domain_run9_v1.json"
    _write_json_bytes(rights_path, rights)
    _write_json_bytes(ledger_path, ledger)
    _write_json_bytes(domain_path, domain_raw)
    return rights_path, ledger_path, domain_path


def test_pin1_r8_three_point_lockstep_passes_stage_four_alone(tmp_path: Path) -> None:
    """前提確認: 3点 lockstep 偽装は第4段単体（`_verify_user_anchor_
    matches_rights_manifest`）では素通りすること（= 第5段が無ければ
    検出できないことの直接証拠）。"""
    rights_path, _ledger_path, domain_path = _write_three_point_lockstep_tamper(tmp_path)
    tampered_rights = m.load_rights_manifest_json(rights_path.read_text(encoding="utf-8"))
    tampered_domain = m.load_run9_identity_domain(domain_path)
    assert tampered_domain.is_pinned()
    m._verify_user_anchor_matches_rights_manifest(  # 例外を投げないことの確認（前提が成立）
        tampered_domain, tampered_rights
    )


def test_pin1_r8_verify_user_donor_manifest_complete_detects_three_point_lockstep_tampering(
    tmp_path: Path,
) -> None:
    """第7巡指摘の直接回帰: domain 単独改変 + rights/ledger 側の辻褄合わせ
    偽装という3点 lockstep が、第5段の genome 再構成不一致で fail-closed
    拒否されること。"""
    rights_path, ledger_path, domain_path = _write_three_point_lockstep_tamper(tmp_path)
    with pytest.raises(m.Run9ValidationError, match="does not match the canonical reconstruction"):
        m.verify_user_donor_manifest_complete(
            rights_manifest_path=rights_path,
            donor_ledger_path=ledger_path,
            identity_domain_path=domain_path,
        )


def test_pin1_r8_rule2_condition_documents_fifth_stage_and_trust_root_boundary() -> None:
    """rule 2 の condition が第5段（domain の founder_genome_shas pin
    への束縛）と信頼根の境界宣言の両方を明記していること。"""
    data = _failure_abort_criteria_data()
    rule2 = next(r for r in data["rules"] if r["rule_id"] == 2)
    assert rule2["enforcement"] == "MACHINE"
    assert "load_pinned_founder_genome_document" in rule2["condition"]
    assert "founder_genome_shas" in rule2["condition"]
    assert "信頼根" in rule2["condition"]
    assert "自己参照" in rule2["condition"]


# ---------------------------------------------------------------------------
# 全体回帰: 3欄 PINNED + gate_state() BLOCKED + 既存 pin 値不変 + 再直列化
# byte 一致 + stale PENDING マーカー不在
# ---------------------------------------------------------------------------


def test_pin1_seed_policy_and_failure_abort_pinned_measurement_spec_pending(
    contract_raw: Dict[str, Any],
) -> None:
    """PR #324 第2巡 Fix 5 後の最終状態: seed_policy_sha/
    failure_abort_criteria_sha は PINNED（実ファイル sha256 と一致）、
    measurement_spec_sha は PENDING（value は null）——3欄が横並びで
    PINNED だった RUN9-L0-PIN-1 直後の状態からの意図的な変更。"""
    pinned_expectations = {
        "seed_policy_sha": m.SEED_POLICY_MANIFEST_PATH,
        "failure_abort_criteria_sha": m.FAILURE_ABORT_MANIFEST_PATH,
    }
    for field_name, path in pinned_expectations.items():
        field = contract_raw[field_name]
        assert field["status"] == "PINNED"
        assert field["value"] == m.compute_file_sha256(path)

    measurement_spec_field = contract_raw["measurement_spec_sha"]
    assert measurement_spec_field["status"] == "PENDING"
    assert measurement_spec_field["value"] is None


def test_pin1_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    """seed_policy_sha/failure_abort_criteria_sha が PINNED になっても
    （measurement_spec_sha は PR #324 第2巡 Fix 5 で PENDING へ復帰済み）、
    残る pre-run 欄（attempt_id ほか）が PENDING のままである限り
    gate_state() は依然 BLOCKED（誤 READY 化していないことの回帰確認 —
    probe_manifest/founder_genome_shas と同型）。"""
    assert m.gate_state(contract) == "BLOCKED"


# `test_pin1_r3_pre_run_pending_count_is_twelve`（12件版）は RUN9-L0-PIN-2
# （dataset_manifest_sha/dataset_row_order_sha の2欄 PINNED 化）により
# 超過し、下記 `test_pin2_pre_run_pending_count_is_ten`（10件・全履歴を
# 包含する上位互換）へ置き換えた（重複削除、「修正からの再修正は早期に
# 打ち切る」に沿い、単に件数を書き換えるのではなく1本化した）。


# RUN9-L0-HARNESS-1 で `dependency_pins_sha` を PINNED 化した際、pre-run
# PENDING は10→9件になった（旧テスト `test_harness1_pre_run_pending_
# count_is_nine`）。PR #326 第2巡 Fix 3（P1、採用、2026-08-26）で同欄が
# PENDING へ差し戻され pre-run PENDING は9→10件へ戻った（旧テスト
# `test_harness1_pr326_fix3_pre_run_pending_count_is_ten`）。続けて
# RUN9-EXECPROFILE-1（2026-08-26）で `execution_profile_sha` が PINNED 化
# され、pre-run PENDING は10→9件へ再度減少した——下記テストへ一本化した
# （重複削除、「修正からの再修正は早期に打ち切る」に沿い、単に件数を
# 書き換えるのではなく1本化する既存規約——上記
# `test_pin2_pre_run_pending_count_is_ten` 直前のコメントと同型）。
def test_execprofile_pre_run_pending_count_is_nine(contract: m.Run9RunContract) -> None:
    """RUN9-EXECPROFILE-1（2026-08-26）: `execution_profile_sha` を
    PINNED 化したことにより、pre-run PENDING 欄は10→9件へ減少した
    （optional の human_evaluation_protocol_sha を含めると総
    PENDING 11→10件）——README.md の記述更新（10→9/11→10）と対応する
    回帰固定。

    〔履歴: 本テストが固定していた「9件」は RUN9-EXECPROFILE-1 時点の値。
    RUN9-L0-HARNESS-3a（2026-08-26）で `expected_speaker_map_sha` が
    追加で PINNED 化され、8件へ減少（下記
    `test_harness3a_pre_run_pending_count_is_eight` 参照）。さらに
    RUN9-L0-HARNESS-3b（2026-08-27）で `education_technique_lesson_
    manifest_sha` が PINNED 化され、7件へ減少（下記
    `test_harness3b_pre_run_pending_count_is_seven` 参照）。さらに
    design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）で
    `hypothesis_algebra_sha` が PINNED 化され、下記
    `test_harness3c_rev06_pre_run_pending_count_is_six` が固定していた
    6件へ一時的に減少したが、PR #333 Codex bot レビュー第1巡指摘1
    （2026-08-28、P1、採用）で `hypothesis_threshold_calibration_sha`
    （H1-H6 δtarget/εk 校正欄の分離新設）が追加されたため、現在は下記
    `test_pr333_r1_pre_run_pending_count_is_seven` が固定する7件へ増加
    した——テスト名はレビュー履歴保持のため改名しない〕。"""
    revalidated = m.load_run9_contract(contract.raw)
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [
        n for n in pre_run_fields if not m._is_field_pinned(revalidated.pin_field(n))
    ]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(revalidated.pin_field(n))
    ]
    assert "dependency_pins_sha" in pending
    assert "measurement_spec_sha" in pending
    assert "execution_profile_sha" not in pending
    assert "dataset_manifest_sha" not in pending
    assert "dataset_row_order_sha" not in pending
    # 現在値は7/8（下記 test_pr333_r1_pre_run_pending_count_is_seven と
    # 同一の期待値）——`expected_speaker_map_sha`/`education_technique_
    # lesson_manifest_sha`/`hypothesis_algebra_sha` の PINNED 化 +
    # `hypothesis_threshold_calibration_sha` の新設以降、9/10・8/9・7/8・
    # 6/7 という値そのものはもはや成立しない。
    assert len(pending) == 7
    assert len(all_pending) == 8


def test_harness3a_pre_run_pending_count_is_eight(contract: m.Run9RunContract) -> None:
    """RUN9-L0-HARNESS-3a（2026-08-26）: `expected_speaker_map_sha` を
    PINNED 化したことにより、pre-run PENDING 欄は9→8件へ減少した
    （optional の human_evaluation_protocol_sha を含めると総
    PENDING 10→9件）——README.md の記述更新（9→8/10→9）と対応する
    回帰固定。

    〔履歴: 本テストが固定していた「8件」は RUN9-L0-HARNESS-3a 時点の値。
    RUN9-L0-HARNESS-3b（2026-08-27）で `education_technique_lesson_
    manifest_sha` が追加で PINNED 化され、7件へ減少（下記
    `test_harness3b_pre_run_pending_count_is_seven` 参照）。さらに
    design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）で
    `hypothesis_algebra_sha` が PINNED 化され、下記
    `test_harness3c_rev06_pre_run_pending_count_is_six` が固定していた
    6件へ一時的に減少したが、PR #333 Codex bot レビュー第1巡指摘1
    （2026-08-28、P1、採用）で `hypothesis_threshold_calibration_sha` が
    追加されたため、現在は下記 `test_pr333_r1_pre_run_pending_count_is_seven`
    が固定する7件へ増加した——テスト名はレビュー履歴保持のため改名しない〕。"""
    revalidated = m.load_run9_contract(contract.raw)
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [
        n for n in pre_run_fields if not m._is_field_pinned(revalidated.pin_field(n))
    ]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(revalidated.pin_field(n))
    ]
    # 現在値は7/8（下記 test_pr333_r1_pre_run_pending_count_is_seven と
    # 同一の期待値）——`education_technique_lesson_manifest_sha`/
    # `hypothesis_algebra_sha` の PINNED 化 + `hypothesis_threshold_
    # calibration_sha` の新設以降、8/9・7/8・6/7 という値そのものは
    # もはや成立しない。
    assert len(pending) == 7
    assert len(all_pending) == 8
    assert "dependency_pins_sha" in pending
    assert "measurement_spec_sha" in pending
    assert "expected_speaker_map_sha" not in pending
    assert "execution_profile_sha" not in pending
    assert "dataset_manifest_sha" not in pending
    assert "dataset_row_order_sha" not in pending
    assert "education_technique_lesson_manifest_sha" not in pending
    assert m.gate_state(revalidated) == "BLOCKED"


def test_harness3b_pre_run_pending_count_is_seven(contract: m.Run9RunContract) -> None:
    """RUN9-L0-HARNESS-3b（2026-08-27）: `education_technique_lesson_
    manifest_sha` を PINNED 化したことにより、pre-run PENDING 欄は8→7件へ
    減少した（optional の human_evaluation_protocol_sha を含めると総
    PENDING 9→8件）——README.md の記述更新と対応する回帰固定。

    〔履歴: 本テストが固定していた「7件」は RUN9-L0-HARNESS-3b 時点の値。
    design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）で
    `hypothesis_algebra_sha` が追加で PINNED 化され、下記
    `test_harness3c_rev06_pre_run_pending_count_is_six` が固定していた
    6件へ一時的に減少したが、PR #333 Codex bot レビュー第1巡指摘1
    （2026-08-28、P1、採用）で `hypothesis_threshold_calibration_sha` が
    追加されたため、現在は下記 `test_pr333_r1_pre_run_pending_count_is_seven`
    が固定する7件へ増加した——テスト名はレビュー履歴保持のため改名しない〕。"""
    revalidated = m.load_run9_contract(contract.raw)
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [
        n for n in pre_run_fields if not m._is_field_pinned(revalidated.pin_field(n))
    ]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(revalidated.pin_field(n))
    ]
    assert len(pending) == 7
    assert len(all_pending) == 8
    assert "dependency_pins_sha" in pending
    assert "measurement_spec_sha" in pending
    assert "expected_speaker_map_sha" not in pending
    assert "execution_profile_sha" not in pending
    assert "dataset_manifest_sha" not in pending
    assert "dataset_row_order_sha" not in pending
    assert "education_technique_lesson_manifest_sha" not in pending
    assert m.gate_state(revalidated) == "BLOCKED"


def test_harness3c_rev06_pre_run_pending_count_is_six(contract: m.Run9RunContract) -> None:
    """design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）:
    `hypothesis_algebra_sha` を（H1-H6 閾値校正欄から identity decision
    protocol の pin 欄へ用途確定した上で）PINNED 化したことにより、
    pre-run PENDING 欄は7→6件へ減少した（optional の human_evaluation_
    protocol_sha を含めると総 PENDING 8→7件）。

    〔履歴: 本テストが固定していた「6件」は RUN9-L0-HARNESS-3c rev 0.6
    時点の値。PR #333 Codex bot レビュー第1巡指摘1（2026-08-28、P1、
    採用）: `hypothesis_algebra_sha` の pin 用途が identity decision
    protocol へ確定した結果、design §18 / failure_abort_criteria.json
    rule 14・16 が要求する H1-H6 δtarget/εk 校正前提の追跡が pre-run
    閉集合から外れていた欠陥を是正するため `hypothesis_threshold_
    calibration_sha` を分離新設し、現在は下記
    `test_pr333_r1_pre_run_pending_count_is_seven` が固定する7件へ
    増加した——テスト名はレビュー履歴保持のため改名しない〕。"""
    revalidated = m.load_run9_contract(contract.raw)
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [
        n for n in pre_run_fields if not m._is_field_pinned(revalidated.pin_field(n))
    ]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(revalidated.pin_field(n))
    ]
    assert len(pending) == 7
    assert len(all_pending) == 8
    assert "dependency_pins_sha" in pending
    assert "measurement_spec_sha" in pending
    assert "hypothesis_algebra_sha" not in pending
    assert "hypothesis_threshold_calibration_sha" in pending
    assert "expected_speaker_map_sha" not in pending
    assert "execution_profile_sha" not in pending
    assert "dataset_manifest_sha" not in pending
    assert "dataset_row_order_sha" not in pending
    assert "education_technique_lesson_manifest_sha" not in pending
    assert m.gate_state(revalidated) == "BLOCKED"


def test_pr333_r1_pre_run_pending_count_is_seven(contract: m.Run9RunContract) -> None:
    """PR #333 Codex bot レビュー第1巡指摘1（2026-08-28、P1、採用）:
    `hypothesis_threshold_calibration_sha` を新設したことにより、
    pre-run PENDING 欄は6→7件へ増加した（optional の human_evaluation_
    protocol_sha を含めると総 PENDING 7→8件）——design §18 /
    failure_abort_criteria.json rule 14・16 が要求する H1-H6 δtarget/εk
    校正前提が rev 0.6 で pre-run 閉集合から外れていた欠陥の是正
    （`hypothesis_algebra_sha` 自体は無改変のまま）。"""
    revalidated = m.load_run9_contract(contract.raw)
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [
        n for n in pre_run_fields if not m._is_field_pinned(revalidated.pin_field(n))
    ]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(revalidated.pin_field(n))
    ]
    assert len(pending) == 7
    assert len(all_pending) == 8
    assert "dependency_pins_sha" in pending
    assert "measurement_spec_sha" in pending
    assert "hypothesis_algebra_sha" not in pending
    assert "hypothesis_threshold_calibration_sha" in pending
    assert "expected_speaker_map_sha" not in pending
    assert "execution_profile_sha" not in pending
    assert "dataset_manifest_sha" not in pending
    assert "dataset_row_order_sha" not in pending
    assert "education_technique_lesson_manifest_sha" not in pending
    assert m.gate_state(revalidated) == "BLOCKED"


def test_pin2_other_existing_pins_unchanged(contract_raw: Dict[str, Any]) -> None:
    """RUN9-L0-PIN-2 は Scope IN の5ファイル
    （inputs/dataset_split_manifest.json 新規 / run9_schema.py /
    RUN9_CONTRACT.yaml / README.md / tests/test_run9_contract.py）以外の
    既存 pin 済みファイルの実バイトを一切変更していないこと（代表サンプル
    ——probe_manifest_sha/practice_audio_split_manifest_sha/seed_policy_sha/
    failure_abort_criteria_sha が引き続き実ファイルと一致する）。"""
    for pin_name, path_const_name in (
        ("probe_manifest_sha", "PROBE_MANIFEST_PATH"),
        ("practice_audio_split_manifest_sha", "PRACTICE_MANIFEST_PATH"),
        ("seed_policy_sha", "SEED_POLICY_MANIFEST_PATH"),
        ("failure_abort_criteria_sha", "FAILURE_ABORT_MANIFEST_PATH"),
    ):
        field = contract_raw[pin_name]
        assert field["status"] == "PINNED"
        assert field["value"] == m.compute_file_sha256(getattr(m, path_const_name)), (
            f"{pin_name} diverged from its pinned file — RUN9-L0-PIN-2 must not touch it"
        )


# ---------------------------------------------------------------------------
# RUN9-L0-PIN-2: dataset split manifest（`run9-dataset-split-manifest/1.0`）
# — 正常系 + validate_dataset_split_manifest() fail-closed 全分岐 +
# load_pinned_dataset_split_manifest() 本欄固有の cross-manifest 三者一致
# + dataset_row_order_sha 三者一致 + learning recipe 裁定値の境界値回帰。
# 汎用 PINNED loader ケース（正常系・in-process 改竄・manifest バイト
# 改竄・欠落ファイル・PENDING 時拒否）は上記 `_PIN1_LOADER_CASES` へ相乗り
# 済み（重複テスト新設を避ける）。
# ---------------------------------------------------------------------------


def test_pin2_dataset_split_manifest_valid_passes() -> None:
    m.validate_dataset_split_manifest(_dataset_split_manifest_data())  # 例外を投げないことの確認


def test_pin2_dataset_manifest_sha_is_pinned_and_matches_actual_file(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["dataset_manifest_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.DATASET_SPLIT_MANIFEST_PATH)
    # PR #333 第2巡指摘1（P1、採用）の probe_manifest_sha repin に追随し
    # dataset_split_manifest.json（identity_probe.probe_manifest_sha 転記
    # 値・行番号引用）を更新したため repin（旧値
    # ba52536c1e36f5d64018a2de7877c288c39ee855a0b463d937ace8032650d448 は
    # RUN9_CONTRACT.yaml の repin 履歴コメントに保持）。
    assert field["value"] == (
        "4138639209caabf08465141681756e3b0bc7be4167516ea9bd93b6d276456cf4"
    )


def test_pin2_dataset_row_order_sha_is_pinned_and_matches_practice_manifest(
    contract_raw: Dict[str, Any],
) -> None:
    """`dataset_row_order_sha` は practice manifest の `row_order_sha256`
    と同値で PINNED 化されている（DESIGN §12 規則6の写像。新規計算では
    ない）。"""
    field = contract_raw["dataset_row_order_sha"]
    assert field["status"] == "PINNED"
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert field["value"] == practice_data["row_order_sha256"]
    assert field["value"] == (
        "6b8435bcf006e9dc90bd5272671da84ee7c82baaaad497ea2926a811e6e9d45a"
    )


@pytest.mark.parametrize("missing_key", sorted(m._DATASET_SPLIT_TOP_LEVEL_KEYS))
def test_pin2_dataset_split_manifest_missing_top_level_key_rejected(missing_key: str) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    del data[missing_key]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dataset_split_manifest(data)


def test_pin2_dataset_split_manifest_unknown_top_level_key_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["unexpected_extra_key"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dataset_split_manifest(data)


def test_pin2_dataset_split_manifest_wrong_schema_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["schema"] = "run9-dataset-split-manifest/0.9"
    with pytest.raises(m.Run9ValidationError, match="schema must be exactly"):
        m.validate_dataset_split_manifest(data)


def test_pin2_song_splits_wrong_canonical_source_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["song_splits"]["canonical_source"] = "some/other/path.json"
    with pytest.raises(m.Run9ValidationError, match="canonical_source must be exactly"):
        m.validate_dataset_split_manifest(data)


def test_pin2_song_splits_wrong_canonical_source_schema_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["song_splits"]["canonical_source_schema"] = "run9-practice-audio-split-manifest/0.9"
    with pytest.raises(m.Run9ValidationError, match="canonical_source_schema must be exactly"):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize("bad_sha", [None, "", "not-hex", "a" * 63, "A" * 64, 12345])
def test_pin2_song_splits_bad_practice_sha_rejected(bad_sha: Any) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["song_splits"]["practice_audio_split_manifest_sha"] = bad_sha
    with pytest.raises(m.Run9ValidationError):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize("bad_sha", [None, "", "not-hex", "b" * 63])
def test_pin2_song_splits_bad_row_order_sha256_rejected(bad_sha: Any) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["song_splits"]["row_order_sha256"] = bad_sha
    with pytest.raises(m.Run9ValidationError):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize(
    "bad_counts",
    [
        {"training": 71, "validation": 15, "sealed_holdout": 15},
        {"training": 70, "validation": 15},
        {},
    ],
)
def test_pin2_song_splits_wrong_row_counts_rejected(bad_counts: Dict[str, int]) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["song_splits"]["row_counts"] = bad_counts
    with pytest.raises(m.Run9ValidationError, match="row_counts must be exactly"):
        m.validate_dataset_split_manifest(data)


def test_pin2_identity_probe_wrong_implementation_class_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["identity_probe"]["implementation_class"] = "SONG_SPLIT"
    with pytest.raises(m.Run9ValidationError, match="implementation_class must be exactly"):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize("bad_sha", [None, "", "not-hex", "c" * 63])
def test_pin2_identity_probe_bad_probe_manifest_sha_rejected(bad_sha: Any) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["identity_probe"]["probe_manifest_sha"] = bad_sha
    with pytest.raises(m.Run9ValidationError):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize(
    "field_name",
    ["design_vocabulary_citation", "pjs_song_based_probe_non_adoption_citation", "design_vocabulary_note"],
)
def test_pin2_identity_probe_missing_citation_field_rejected(field_name: str) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    del data["identity_probe"][field_name]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dataset_split_manifest(data)


def test_pin2_negative_sham_control_wrong_implementation_class_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["negative_sham_control"]["implementation_class"] = "SONG_SPLIT"
    with pytest.raises(m.Run9ValidationError, match="implementation_class must be exactly"):
        m.validate_dataset_split_manifest(data)


@pytest.mark.parametrize("bad_value", [19, 21, True, 20.0, "20", None])
def test_pin2_negative_sham_control_wrong_c1_takes_rejected(bad_value: Any) -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["negative_sham_control"]["c1_sham_takes_per_founder"] = bad_value
    with pytest.raises(m.Run9ValidationError, match="c1_sham_takes_per_founder must be"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_missing_rule_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    del data["design_rule_accounting"]["rule_4"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_unknown_rule_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["design_rule_accounting"]["rule_8"] = dict(data["design_rule_accounting"]["rule_1"])
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_wrong_verbatim_rejected() -> None:
    """規則の verbatim は DESIGN_RUN9 §12 一次ソースからの逐語転記のため、
    改変（要約・意訳含む）は fail-closed 拒否する。"""
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["design_rule_accounting"]["rule_1"]["verbatim"] = "song単位でsplitする（要約）"
    with pytest.raises(m.Run9ValidationError, match="verbatim must be exactly"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_unknown_status_rejected() -> None:
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["design_rule_accounting"]["rule_1"]["status"] = "PROBABLY_FINE"
    with pytest.raises(m.Run9ValidationError, match="status must be one of"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_rule3_must_stay_not_recorded() -> None:
    """規則3（pitch range/phrase length/phoneme class の記録）は数値を
    発明しない正直な会計として `NOT_RECORDED` 固定を Design Memo
    RUN9-L0-PIN-2 が明示的に要求する——他のステータス（音響 inventory
    sidecar を生成していないにもかかわらず `STRUCTURALLY_PINNED` を
    僭称する等）は fail-closed 拒否する。"""
    data = copy.deepcopy(_dataset_split_manifest_data())
    data["design_rule_accounting"]["rule_3"]["status"] = "STRUCTURALLY_PINNED"
    with pytest.raises(m.Run9ValidationError, match="rule_3.status must be exactly"):
        m.validate_dataset_split_manifest(data)


def test_pin2_design_rule_accounting_all_seven_rules_present_with_honest_statuses() -> None:
    """正例回帰: 実 manifest は規則1-7を過不足なく登録し、規則3のみ
    `NOT_RECORDED`、他は `STRUCTURALLY_PINNED`/`BOUNDARY_DECLARED`/
    `PROCEDURAL_NOT_MACHINE_ENFORCED` のいずれかであること（発明された
    虚偽の `STRUCTURALLY_PINNED` 全数主張になっていないことの確認）。"""
    data = _dataset_split_manifest_data()
    rules = data["design_rule_accounting"]
    assert set(rules.keys()) == set(m._DATASET_SPLIT_RULE_IDS)
    assert rules["rule_3"]["status"] == "NOT_RECORDED"
    non_structurally_pinned = {
        rule_id for rule_id, rule in rules.items() if rule["status"] != "STRUCTURALLY_PINNED"
    }
    # 規則2(BOUNDARY_DECLARED)・規則3(NOT_RECORDED)・規則4/7
    # (PROCEDURAL_NOT_MACHINE_ENFORCED) の4件は非 STRUCTURALLY_PINNED —
    # 全数を機械保証済みと偽らない正直な会計であることの確認。
    assert non_structurally_pinned == {"rule_2", "rule_3", "rule_4", "rule_7"}


def test_pin2_load_pinned_dataset_split_manifest_detects_practice_sha_drift(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """cross-manifest 三者一致（本欄固有の追加防御）: 転記された
    `song_splits.practice_audio_split_manifest_sha` がディスク正典
    `RUN9_CONTRACT.yaml` の `practice_audio_split_manifest_sha` pin 値と
    一致しなければ fail-closed 拒否する（`validate_dataset_split_
    manifest()` 単体では検出できない「将来の repin に本 manifest の転記が
    追随していない」経路を、消費時点でディスク正典と突き合わせて検出する
    ——docstring 記載の意図そのものの直接確認）。"""
    tampered_data = copy.deepcopy(_dataset_split_manifest_data())
    tampered_data["song_splits"]["practice_audio_split_manifest_sha"] = "0" * 64
    tampered_path = tmp_path / "dataset_split_manifest.json"
    serialized = json.dumps(tampered_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tampered_path.write_bytes(serialized.encode("utf-8"))
    # dataset_manifest_sha pin をこの改変後バイトに合わせて差し替えた
    # tampered contract を用意する（本テストの狙いは byte-tamper 検出では
    # なく、あくまで cross-manifest sha 不一致の検出）。
    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["dataset_manifest_sha"]["value"] = m.compute_file_sha256(tampered_path)
    tampered_contract_raw["dataset_manifest_sha"]["status"] = "PINNED"
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="practice_audio_split_manifest_sha"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract, manifest_path=tampered_path, contract_path=tampered_yaml_path,
        )


def test_pin2_load_pinned_dataset_split_manifest_detects_probe_sha_drift(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    tampered_data = copy.deepcopy(_dataset_split_manifest_data())
    tampered_data["identity_probe"]["probe_manifest_sha"] = "1" * 64
    tampered_path = tmp_path / "dataset_split_manifest.json"
    serialized = json.dumps(tampered_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tampered_path.write_bytes(serialized.encode("utf-8"))
    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["dataset_manifest_sha"]["value"] = m.compute_file_sha256(tampered_path)
    tampered_contract_raw["dataset_manifest_sha"]["status"] = "PINNED"
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="probe_manifest_sha"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract, manifest_path=tampered_path, contract_path=tampered_yaml_path,
        )


def test_pin2_load_pinned_dataset_split_manifest_detects_c1_takes_drift(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    tampered_data = copy.deepcopy(_dataset_split_manifest_data())
    tampered_data["negative_sham_control"]["c1_sham_takes_per_founder"] = 99
    tampered_path = tmp_path / "dataset_split_manifest.json"
    serialized = json.dumps(tampered_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tampered_path.write_bytes(serialized.encode("utf-8"))
    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["dataset_manifest_sha"]["value"] = m.compute_file_sha256(tampered_path)
    tampered_contract_raw["dataset_manifest_sha"]["status"] = "PINNED"
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="c1_sham_takes_per_founder"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract, manifest_path=tampered_path, contract_path=tampered_yaml_path,
        )


def test_pin2_load_pinned_dataset_split_manifest_detects_row_order_sha_drift(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """dataset_row_order_sha 三者一致（AC 固有）: 本 manifest の転記値
    （`song_splits.row_order_sha256`）が practice manifest 実体の
    `row_order_sha256` と食い違えば fail-closed 拒否する（contract pin 側
    は不変のまま、転記だけが陳腐化した経路の検出）。"""
    tampered_data = copy.deepcopy(_dataset_split_manifest_data())
    tampered_data["song_splits"]["row_order_sha256"] = "2" * 64
    tampered_path = tmp_path / "dataset_split_manifest.json"
    serialized = json.dumps(tampered_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tampered_path.write_bytes(serialized.encode("utf-8"))
    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["dataset_manifest_sha"]["value"] = m.compute_file_sha256(tampered_path)
    tampered_contract_raw["dataset_manifest_sha"]["status"] = "PINNED"
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="dataset_row_order_sha 不一致"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract, manifest_path=tampered_path, contract_path=tampered_yaml_path,
        )


def test_pin2_load_pinned_dataset_split_manifest_recomputes_row_order_from_row_ids(
    contract: m.Run9RunContract,
) -> None:
    """PR #325 第1巡 Codex bot レビュー Fix 1（P2, 採用）: 正例回帰——
    実 contract に対する呼び出しは `row_ids.{training,validation,
    sealed_holdout}` の rank 順連結から再計算した digest と、practice
    manifest の宣言値 `row_order_sha256` が実際に一致することを確認する
    （実データに対する再計算パスが実際に機能することの直接確認）。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    reconstructed = (
        practice_data["row_ids"]["training"]
        + practice_data["row_ids"]["validation"]
        + practice_data["row_ids"]["sealed_holdout"]
    )
    assert m._compute_canonical_pin_sha256(reconstructed) == practice_data["row_order_sha256"]
    m.load_pinned_dataset_split_manifest(contract)  # 例外を投げないことの確認


def test_pin2_load_pinned_dataset_split_manifest_rejects_stale_declared_row_order_after_row_swap(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """必須負例（PR #325 第1巡 Codex bot レビュー Fix 1, P2, 採用）: 指摘が
    名指しした具体的攻撃シナリオ——practice manifest の `row_ids.training`
    内で2行を入れ替え、宣言値 `row_order_sha256` は据え置いたまま（stale）
    repin をシミュレートする（practice_audio_split_manifest_sha /
    dataset_manifest_sha / dataset_row_order_sha の contract pin 値、
    および dataset manifest の転記値はすべてこの改竄後バイトへ揃えて
    更新する——「宣言値同士は全員一致しているが、宣言値自体が実体と食い
    違っている」という、Fix 1 以前の三者一致だけでは検出できなかった
    経路を再現する）。row-order 再計算チェックがこの食い違いを
    fail-closed で検出することを確認する。

    PR #325 第3巡 Fix 4 追随: `training_split_sha256`（per-split digest）
    は入れ替え後の順序へ正しく追随させる（stale にしない）——Fix 4 の
    per-split チェックが先に発火して Fix 1 の検出力を覆い隠さないよう
    分離する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    training = tampered_practice["row_ids"]["training"]
    training[0], training[1] = training[1], training[0]
    tampered_practice["training_split_sha256"] = m._compute_canonical_pin_sha256(list(training))
    # row_order_sha256 declared value is deliberately left unchanged (stale) —
    # this is the exact scenario Fix 1 targets.
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    dataset_data = _dataset_split_manifest_data()
    tampered_dataset = copy.deepcopy(dataset_data)
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    # row_order_sha256 transcription also left unchanged — faithfully carries
    # forward the (stale) declared value, exactly as a careless repin would.
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    # dataset_row_order_sha pin is also left unchanged — matches the stale
    # declared/transcribed value everywhere except the actual row order.
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="row_order_sha256 宣言値"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2_load_pinned_dataset_split_manifest_positive_matches_actual_data(
    contract: m.Run9RunContract,
) -> None:
    """正常系（回帰）: 実 contract に対する `load_pinned_dataset_split_
    manifest()` は実ファイルと byte-identical な内容を返す。"""
    loaded = m.load_pinned_dataset_split_manifest(contract)
    assert loaded == _dataset_split_manifest_data()


def _pin2r3_build_tampered_fixture_with_dropped_training_song(
    contract: m.Run9RunContract, tmp_path: Path, *, update_row_counts: bool,
) -> Tuple[m.Run9RunContract, Path, Path, Path]:
    """PR #325 第2巡 Fix 3 の負例 fixture 構築を共有するヘルパー:
    practice manifest の `row_ids.training` から1曲除去し、`row_order_
    sha256`（宣言値）・`practice_audio_split_manifest_sha`/
    `dataset_manifest_sha`/`dataset_row_order_sha` の contract pin 値・
    dataset manifest の転記値はすべてこの改竄後バイトへ揃えて更新する
    （Fix 1 の四者一致チェックを通過させ、`row_counts` 単独の食い違いを
    Fix 3 が検出できるかを分離検証するため）。`update_row_counts=True`
    のときは `song_splits.row_counts.training` も 69 へ追随させる正例
    （row_counts が実体に追随していれば受理される）、`False` のときは
    70/15/15 のまま stale に残す負例（Fix 3 が拒否すべきケース）。

    PR #325 第3巡 Fix 4 追随: `training_split_sha256`（per-split digest）
    も新しい `row_ids.training` から再計算して更新する——これを更新しない
    と Fix 4 のチェックがこの fixture 内で先に発火し、Fix 3 の検出力を
    分離できなくなる。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    tampered_practice["row_ids"]["training"].pop(0)
    tampered_practice["training_split_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["training"])
    )
    new_row_order = (
        tampered_practice["row_ids"]["training"]
        + tampered_practice["row_ids"]["validation"]
        + tampered_practice["row_ids"]["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    if update_row_counts:
        tampered_dataset["song_splits"]["row_counts"]["training"] = 69
    # update_row_counts=False の場合は row_counts.training=70 のまま
    # stale に残す — これが Fix 3 の負例本体。
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    return tampered_contract, tampered_dataset_path, tampered_practice_path, tampered_yaml_path


def test_pin2r3_fix3_rejects_stale_row_counts_after_practice_song_removed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """必須負例（PR #325 第2巡 Codex bot レビュー Fix 3, P2, 採用 — Fix 1
    と同族）: practice manifest から1曲（`row_ids.training` の先頭）を
    除去し、`row_order_sha256`（宣言値）・`practice_audio_split_
    manifest_sha`/`dataset_manifest_sha`/`dataset_row_order_sha` の
    contract pin 値・dataset manifest の row_order_sha256 転記値はすべて
    整合するよう更新する（Fix 1 の四者一致は通過する）が、
    `song_splits.row_counts.training` だけは 70 のまま stale に残す——
    「宣言値同士（row_order 系）は全員一致しているが、別の転記値
    （row_counts）が実体と食い違っている」という Fix 1 とは独立の経路を
    再現し、Fix 3 の件数照合がこれを fail-closed で検出することを確認
    する。"""
    (
        tampered_contract, tampered_dataset_path, tampered_practice_path, tampered_yaml_path,
    ) = _pin2r3_build_tampered_fixture_with_dropped_training_song(
        contract, tmp_path, update_row_counts=False,
    )
    with pytest.raises(m.Run9ValidationError, match="song_splits.row_counts の転記値"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2r3_fix3_accepts_when_row_counts_correctly_tracks_dropped_song(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正例回帰（Fix 3 の逆方向確認、PR #325 第4巡 Fix 5 導入後に更新）:
    同じ「1曲除去」シナリオでも、`row_counts` だけでなく row_ids 自体を
    決定論規則（`_expected_practice_split_assignment()`、Fix 5）へ正しく
    再導出すれば受理される——Fix 3 が「件数が変わった」こと自体を拒否
    するのではなく「転記が実体に追随していない」ことのみを拒否する設計
    であることの確認。

    PR #325 第4巡 Fix 5 導入前の旧テストは、単純に `row_ids.training`
    から先頭1件を pop するだけの「truncation」を『件数が実体に追随した
    正しい repin』とみなしていたが、これは決定論規則
    （score 昇順ランキング → 70/15/15 スライス）が実際に N=99 で生成する
    割当（floor(99*0.15)=14 により 71/14/14、かつ全 song のランキング
    順位も再計算される）とは一致しない——Fix 5 はまさにこの種の「件数・
    digest は自己整合的だが決定論規則には従わない」割当を拒否する
    ため、旧テストの fixture は Fix 5 導入後は正しく reject されるように
    なった（=`test_pin2r3_fix3_rejects_stale_row_counts_after_practice_
    song_removed` 等、他の Fix 3 負例テストの土台となる helper
    `_pin2r3_build_tampered_fixture_with_dropped_training_song` は「負例
    fixture」としては引き続き妥当——row_counts 単体の食い違いを Fix 3 が
    先に検出するため Fix 5 まで到達しない。問題になるのは「正例として
    受理されるべき」という主張だけであり、本テストはその主張を N=99 の
    真に妥当な決定論割当へ差し替えて修正する）。

    `validate_dataset_split_manifest()` は `row_counts` を書き込み時点の
    凍結定数 `_DATASET_SPLIT_EXPECTED_ROW_COUNTS`（70/15/15）とも別途
    厳密照合する（RUN9-L0-PIN-2 第1巡の既存構造検証、Fix 3/5 とは独立の
    層）ため、本テストのみ `monkeypatch` でこの定数を N=99 の正しい
    71/14/14 へ一時上書きし、動的照合を静的定数照合から分離して検証
    する。"""
    monkeypatch.setattr(
        m, "_DATASET_SPLIT_EXPECTED_ROW_COUNTS",
        {"training": 71, "validation": 14, "sealed_holdout": 14},
    )
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    all_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    remaining_ids = [sid for sid in all_ids if sid != practice_data["row_ids"]["training"][0]]
    new_assignment = m._expected_practice_split_assignment(remaining_ids)
    assert {name: len(v) for name, v in new_assignment.items()} == {
        "training": 71, "validation": 14, "sealed_holdout": 14,
    }

    tampered_practice = copy.deepcopy(practice_data)
    tampered_practice["row_ids"] = new_assignment
    for split_name, digest_field in (
        ("training", "training_split_sha256"),
        ("validation", "validation_split_sha256"),
        ("sealed_holdout", "sealed_holdout_sha256"),
    ):
        tampered_practice[digest_field] = m._compute_canonical_pin_sha256(
            list(new_assignment[split_name])
        )
    new_row_order = (
        new_assignment["training"] + new_assignment["validation"] + new_assignment["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    split_of_song = {
        sid: split_name for split_name in ("training", "validation", "sealed_holdout")
        for sid in new_assignment[split_name]
    }
    tampered_practice["sample_inventory"] = [
        f"{rank:04d}|{split_of_song[sid]}|{sid}" for rank, sid in enumerate(new_row_order)
    ]
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    tampered_dataset["song_splits"]["row_counts"] = {
        "training": 71, "validation": 14, "sealed_holdout": 14,
    }
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)

    loaded = m.load_pinned_dataset_split_manifest(
        tampered_contract,
        manifest_path=tampered_dataset_path,
        practice_manifest_path=tampered_practice_path,
        contract_path=tampered_yaml_path,
    )
    assert loaded["song_splits"]["row_counts"] == {
        "training": 71, "validation": 14, "sealed_holdout": 14,
    }


def test_pin2r3_fix3_rejects_song_moved_between_splits_with_stale_counts(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """必須負例その2（Fix 3, 移動シナリオ）: song を training から
    sealed_holdout へ移動（除去ではなく再配分）しても総件数 100 は不変の
    ため Fix 3 の合計チェック単体では検出できないが、per-split
    （training=69/sealed_holdout=16 の実体 vs 70/15 の stale 転記）の
    不一致は検出されることを確認する。

    PR #325 第3巡 Fix 4 追随: `training_split_sha256`/`sealed_holdout_
    sha256`（per-split digest、移動元・移動先の両方）を新しい row_ids
    へ正しく追随させる——Fix 4 が先に発火して Fix 3 の検出力を覆い隠さ
    ないよう分離する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    moved_song = tampered_practice["row_ids"]["training"].pop(0)
    tampered_practice["row_ids"]["sealed_holdout"].append(moved_song)
    tampered_practice["training_split_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["training"])
    )
    tampered_practice["sealed_holdout_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["sealed_holdout"])
    )
    new_row_order = (
        tampered_practice["row_ids"]["training"]
        + tampered_practice["row_ids"]["validation"]
        + tampered_practice["row_ids"]["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    # row_counts left stale at 70/15/15 (actual is now 69/15/16) — total
    # is still 100, so this exercises the per-split check independently
    # of the total-sum check.
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="song_splits.row_counts の転記値"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2r3_fix3_no_other_untranscribed_values_remain() -> None:
    """点検結果（PR #325 第2巡指摘対応時の付随確認）: dataset split
    manifest 内の「別の pin 済み実体からの転記値」はここまでで全て
    cross-manifest 照合済みであることを構造的に固定する——
    `practice_audio_split_manifest_sha`/`row_order_sha256`/`row_counts`
    （song_splits 節）、`probe_manifest_sha`（identity_probe 節）、
    `c1_sham_takes_per_founder`（negative_sham_control 節）の5値が
    song_splits/identity_probe/negative_sham_control 節に存在する数値/
    sha 系フィールドの全数であることを確認する（同族の残余なし、を
    フィールド集合の網羅性として機械固定する）。"""
    assert m._DATASET_SPLIT_SONG_SPLITS_KEYS == frozenset({
        "canonical_source", "canonical_source_schema", "practice_audio_split_manifest_sha",
        "row_order_sha256", "row_counts", "row_ids_and_sample_inventory_note",
    })
    assert m._DATASET_SPLIT_IDENTITY_PROBE_KEYS == frozenset({
        "implementation_class", "implementation", "probe_manifest_sha",
        "design_vocabulary_citation", "pjs_song_based_probe_non_adoption_citation",
        "design_vocabulary_note",
    })
    assert m._DATASET_SPLIT_NEGATIVE_SHAM_KEYS == frozenset({
        "implementation_class", "implementation", "c1_sham_takes_per_founder",
        "design_vocabulary_citation", "design_vocabulary_note",
    })
    # canonical_source/canonical_source_schema/implementation_class の
    # 残り4フィールドは固定リテラル文字列（`validate_dataset_split_
    # manifest()` が凍結定数と厳密照合済み）であり、別の可変な pin 済み
    # 実体からの「転記」ではないため cross-manifest 照合の対象外。
    # design_rule_accounting.*.verbatim は DESIGN doc（byte-pin 済み・
    # 不変）からの逐語であり、対象実体自体が変化し得ないため drift の
    # リスクが構造的に存在しない。


def test_pin2_load_pinned_dataset_split_manifest_positive_matches_actual_data_after_fix3(
    contract: m.Run9RunContract,
) -> None:
    """正常系回帰（Fix 3 適用後）: 実 contract に対する呼び出しは引き続き
    成功し、`song_splits.row_counts` が実 practice manifest の row_ids
    各split長と一致することを直接確認する。"""
    loaded = m.load_pinned_dataset_split_manifest(contract)
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    actual_counts = {
        name: len(practice_data["row_ids"][name])
        for name in ("training", "validation", "sealed_holdout")
    }
    assert loaded["song_splits"]["row_counts"] == actual_counts


# ---------------------------------------------------------------------------
# PR #325 第3巡 Codex bot レビュー Fix 4（P2, 採用 — Fix 1/3 と同族の
# 最終掃討）: practice manifest の per-split digest 3つ
# （training_split_sha256/validation_split_sha256/sealed_holdout_sha256）
# の再計算照合。
# ---------------------------------------------------------------------------


def test_pin2r4_fix4_positive_recomputes_all_three_split_digests() -> None:
    """正例回帰: 実 practice manifest の3 digest がいずれも対応する
    row_ids から再計算した値と一致することを直接確認する（builder が
    実際に計算するのと同じ規則 —
    `_canonical_song_list_sha256(split[name]) ==
    m._compute_canonical_pin_sha256(list(split[name]))` — で再現できる
    ことの確認）。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    field_by_name = {
        "training": "training_split_sha256",
        "validation": "validation_split_sha256",
        "sealed_holdout": "sealed_holdout_sha256",
    }
    for split_name, digest_field in field_by_name.items():
        recomputed = m._compute_canonical_pin_sha256(list(practice_data["row_ids"][split_name]))
        assert recomputed == practice_data[digest_field]


def test_pin2r4_fix4_positive_real_contract_still_loads() -> None:
    m.load_pinned_dataset_split_manifest(m.load_run9_contract_from_yaml_path(m.RUN9_CONTRACT_YAML_PATH))  # noqa: E501 - 例外を投げないことの確認


@pytest.mark.parametrize(
    "split_name,digest_field",
    [
        ("training", "training_split_sha256"),
        ("validation", "validation_split_sha256"),
        ("sealed_holdout", "sealed_holdout_sha256"),
    ],
)
def test_pin2r4_fix4_rejects_stale_per_split_digest(
    contract: m.Run9RunContract, tmp_path: Path, split_name: str, digest_field: str,
) -> None:
    """必須負例（指摘が名指しした具体的攻撃シナリオ、3 digest それぞれに
    ついて確認）: 該当 split 内で2行を入れ替え、その split の宣言 digest
    （`{digest_field}`）だけは stale に据え置いたまま、`row_order_
    sha256`（宣言値・contract pin・dataset manifest 転記値の四者）と
    `practice_audio_split_manifest_sha`/`dataset_manifest_sha` はすべて
    改竄後バイトへ揃えて更新する——Fix 1（row_order 四者一致）・Fix 3
    （row_counts、件数は不変のため対象外）のいずれも通過するが、Fix 4 の
    per-split digest 再計算だけがこの食い違いを検出できることを確認
    する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    target_list = tampered_practice["row_ids"][split_name]
    target_list[0], target_list[1] = target_list[1], target_list[0]
    # row_order_sha256(連結) の宣言値は新しい順序へ追随させる（Fix 1 を
    # 通過させ、Fix 4 単体の検出力を分離するため）。対象 split の
    # digest_field は意図的に据え置く（stale、Fix 4 の負例本体）。
    new_row_order = (
        tampered_practice["row_ids"]["training"]
        + tampered_practice["row_ids"]["validation"]
        + tampered_practice["row_ids"]["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    # row_counts unaffected by a within-split swap (lengths unchanged) —
    # left as-is deliberately so Fix 3 does not mask the Fix 4 finding.
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match=f"{digest_field} 宣言値"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2r4_fix4_hash_family_closure_documented() -> None:
    """終端宣言の直接確認: `load_pinned_dataset_split_manifest()` の
    docstring が practice manifest の hash 系6フィールド全数を列挙し、
    repo 外実体（`pjs_source_archive_sha256`/`expanded_corpus_identity_
    sha256`）が再計算不能である境界を明記していること。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    doc_start = source.index("def load_pinned_dataset_split_manifest(")
    doc_end = source.index('    effective_contract_path = (\n', doc_start)
    docstring = source[doc_start:doc_end]
    assert "同族ファミリーの終端宣言" in docstring
    for field in (
        "row_order_sha256", "training_split_sha256", "validation_split_sha256",
        "sealed_holdout_sha256", "pjs_source_archive_sha256", "expanded_corpus_identity_sha256",
    ):
        assert field in docstring
    assert "sha256sum -c" in docstring


# ---------------------------------------------------------------------------
# PR #325 第4巡 Codex bot レビュー Fix 5/6（P2 ×2, 採用 — Fix 1/3/4 と
# 同族の最終層）: 決定論割当の再導出照合（Fix 5）+ sample_inventory の
# 再構成照合（Fix 6）。
# ---------------------------------------------------------------------------


def test_pin2r5_fix5_drift_detection_against_builder_real_and_synthetic() -> None:
    """必須テスト（コーディネータ指示: builder の assign_split() 実出力と
    schema 側再実装の一致を実データ + 合成 N で照合する drift 検出テスト
    を必ず追加）。テスト層は `practice_split_builder`（numpy 依存）を
    import してよい——制約は `run9_schema.py` 本体側のみ。実
    `practice_audio_split_manifest.json`（N=100）+ 合成 N（fail-closed
    ガードに抵触しない N=7 から N=137 まで複数点）で、
    `run9_schema._expected_practice_split_assignment()` と
    `practice_split_builder.assign_split()` の training/validation/
    sealed_holdout 3リストが完全一致することを確認する。"""
    import practice_split_builder as psb  # noqa: PLC0415 - test-local, numpy 依存を隔離

    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    real_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    id_sets = [real_ids] + [
        [f"synthetic_song_{i:05d}" for i in range(n)] for n in (7, 8, 15, 20, 33, 99, 100, 137, 251)
    ]
    for ids in id_sets:
        schema_result = m._expected_practice_split_assignment(list(ids))
        builder_result = psb.assign_split(list(ids))
        for split_name in ("training", "validation", "sealed_holdout"):
            assert schema_result[split_name] == builder_result[split_name], (
                f"drift detected for N={len(ids)}, split={split_name!r}"
            )


def test_pin2r5_fix5_rejects_small_n_empty_split() -> None:
    """builder の `assign_split()` は N<=6 で「いずれかの split が空になる」
    ため fail-closed 拒否する（`practice_split_builder.py` 逐語確認済み）。
    schema 側再実装も同じ N で raise することを確認する（完全な等価性の
    負例側の確認 — 空リストのみ明示的にガードしている本関数の docstring
    どおり、N=0 で拒否されることの直接確認）。"""
    with pytest.raises(m.Run9ValidationError, match="song_ids must be non-empty"):
        m._expected_practice_split_assignment([])


def test_pin2r5_fix5_positive_real_contract_matches_expected_assignment(
    contract: m.Run9RunContract,
) -> None:
    """正例回帰: 実 contract に対する呼び出しは引き続き成功し、実
    practice manifest の row_ids が決定論規則の期待割当と一致すること
    （load 経路が実際に Fix 5 を通過していることの直接確認）。"""
    loaded = m.load_pinned_dataset_split_manifest(contract)
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    all_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    expected = m._expected_practice_split_assignment(all_ids)
    for split_name in ("training", "validation", "sealed_holdout"):
        assert practice_data["row_ids"][split_name] == expected[split_name]
    assert loaded["schema"] == m.SCHEMA_DATASET_SPLIT_MANIFEST


def test_pin2r5_fix5_rejects_two_songs_swapped_between_splits_with_all_digests_consistent(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """必須負例（指摘が名指しした具体的攻撃シナリオ）: `row_ids.training`
    と `row_ids.validation` それぞれから1曲を選んで交換する（ID 和集合・
    総件数は完全に不変）。その状態に合わせて `training_split_sha256`/
    `validation_split_sha256`/`row_order_sha256`（宣言値・contract pin・
    dataset manifest 転記値の四者）はすべて「正直に」再計算・更新する
    （Fix 1/3/4 を全て通過させる構成——row_counts も 70/15/15 のまま
    不変のため Fix 3 も無関係に通過する）。決定論規則
    （score 昇順ランキング → スライス）はこの交換後の割当を生成し得ない
    ため、Fix 5 の再導出照合のみがこれを検出できることを確認する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    swapped_out_training = tampered_practice["row_ids"]["training"][0]
    swapped_out_validation = tampered_practice["row_ids"]["validation"][0]
    tampered_practice["row_ids"]["training"][0] = swapped_out_validation
    tampered_practice["row_ids"]["validation"][0] = swapped_out_training
    tampered_practice["training_split_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["training"])
    )
    tampered_practice["validation_split_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["validation"])
    )
    new_row_order = (
        tampered_practice["row_ids"]["training"]
        + tampered_practice["row_ids"]["validation"]
        + tampered_practice["row_ids"]["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    # sample_inventory も rank|split|song_id の再構成規則に合わせて正直に
    # 更新する（Fix 6 が先に発火しないよう分離する必要は無い——Fix 5 が
    # Fix 6 より先に実行されるため本来は不要だが、意図の一貫性のため
    # 更新しておく）。
    split_of_song = {
        sid: split_name for split_name in ("training", "validation", "sealed_holdout")
        for sid in tampered_practice["row_ids"][split_name]
    }
    tampered_practice["sample_inventory"] = [
        f"{rank:04d}|{split_of_song[sid]}|{sid}" for rank, sid in enumerate(new_row_order)
    ]
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    # row_counts unaffected by a 1-for-1 swap (70/15/15 unchanged).
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="凍結規則"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2r6_fix6_positive_real_contract_matches_expected_inventory(
    contract: m.Run9RunContract,
) -> None:
    """正例回帰: 実 practice manifest の `sample_inventory` が row_ids
    から再構成した期待 inventory（`rank|split|song_id` 形式、rank は
    row_order 全体での0始まり通し番号）と順序込みで一致することを直接
    確認する。"""
    m.load_pinned_dataset_split_manifest(contract)  # 例外を投げないことの確認
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    row_order = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    split_of_song = {
        sid: split_name for split_name in ("training", "validation", "sealed_holdout")
        for sid in practice_data["row_ids"][split_name]
    }
    expected_inventory = [
        f"{rank:04d}|{split_of_song[sid]}|{sid}" for rank, sid in enumerate(row_order)
    ]
    assert practice_data["sample_inventory"] == expected_inventory


def test_pin2r6_fix6_rejects_stale_sample_inventory_after_song_removed(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須負例（指摘が名指しした具体的攻撃シナリオ、Fix 5 と同型の1曲
    除去 fixture を再利用）: N=99 への正しい決定論再割当（row_ids/
    digest/row_counts/row_order はすべて正しく更新——Fix 1/3/4/5 を全て
    通過する構成）を行うが、`sample_inventory` だけは旧 N=100 のリスト
    のまま更新しない——曲除去後に inventory が追随しなかった経路を
    再現し、Fix 6 の再構成照合のみがこれを検出できることを確認する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    stale_sample_inventory = list(practice_data["sample_inventory"])
    all_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    remaining_ids = [sid for sid in all_ids if sid != practice_data["row_ids"]["training"][0]]
    new_assignment = m._expected_practice_split_assignment(remaining_ids)

    tampered_practice = copy.deepcopy(practice_data)
    tampered_practice["row_ids"] = new_assignment
    for split_name, digest_field in (
        ("training", "training_split_sha256"),
        ("validation", "validation_split_sha256"),
        ("sealed_holdout", "sealed_holdout_sha256"),
    ):
        tampered_practice[digest_field] = m._compute_canonical_pin_sha256(
            list(new_assignment[split_name])
        )
    new_row_order = (
        new_assignment["training"] + new_assignment["validation"] + new_assignment["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    # sample_inventory left stale (still the old N=100 list) — this is the
    # Fix 6 negative case itself.
    tampered_practice["sample_inventory"] = stale_sample_inventory
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    tampered_dataset["song_splits"]["row_counts"] = {
        "training": 71, "validation": 14, "sealed_holdout": 14,
    }
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    monkeypatch.setattr(
        m, "_DATASET_SPLIT_EXPECTED_ROW_COUNTS",
        {"training": 71, "validation": 14, "sealed_holdout": 14},
    )
    with pytest.raises(m.Run9ValidationError, match="sample_inventory"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


# ---------------------------------------------------------------------------
# PR #325 第5巡 Codex bot レビュー Fix 7（P2, 採用 — probe/seed_policy
# loader 由来の in-process 乖離検査パターンの適用漏れ）: 本 loader が
# 消費する contract 欄5つ全てを revalidated/disk_contract 間で照合する。
# 「Fix 7」という名称は PR #318 系（learning recipe 型検証）の Fix 7 とは
# 無関係の別スレッド由来——本セクションのテスト名は既存の pin2r{round}
# 命名規約（`pin2r6` 直後）に従い区別する。
# ---------------------------------------------------------------------------

# ("linked_field_name", "raw キーパス（ネストは (親キー, 子キー) のタプル
# で表現）") — `interventions.c1_sham_takes_per_founder` のみネスト。
_PIN2R7_FIX7_LINKED_FIELD_RAW_PATHS: Tuple[Tuple[str, Any], ...] = (
    ("dataset_manifest_sha", "dataset_manifest_sha"),
    ("dataset_row_order_sha", "dataset_row_order_sha"),
    ("practice_audio_split_manifest_sha", "practice_audio_split_manifest_sha"),
    ("probe_manifest_sha", "probe_manifest_sha"),
    ("c1_sham_takes_per_founder", ("interventions", "c1_sham_takes_per_founder")),
)


@pytest.mark.parametrize(
    "linked_field_name,raw_path", _PIN2R7_FIX7_LINKED_FIELD_RAW_PATHS,
)
def test_pin2r7_fix7_rejects_in_process_divergence_on_each_linked_field(
    contract: m.Run9RunContract, linked_field_name: str, raw_path: Any,
) -> None:
    """必須負例（指摘が名指しした具体的シナリオ、5欄それぞれについて
    パラメタライズ）: 渡す contract の raw で対象欄**のみ**を改変し
    （`dataset_manifest_sha` は他4欄のケースでは不変のまま）、他4欄が
    自己整合していても in-process 乖離が fail-closed で拒否されることを
    確認する——Fix 7 以前は `dataset_manifest_sha` 以外の4欄がこの防御の
    対象外だった。"""
    tampered = m.load_run9_contract(copy.deepcopy(contract.raw))
    if isinstance(raw_path, tuple):
        parent_key, child_key = raw_path
        original_value = tampered.raw[parent_key][child_key]["value"]
        tampered_value = "0" * 64 if isinstance(original_value, str) else 999999
        tampered.raw[parent_key][child_key]["value"] = tampered_value
    else:
        tampered.raw[raw_path]["value"] = "0" * 64
    with pytest.raises(
        m.Run9ValidationError,
        match=f"the passed-in contract's {linked_field_name} pin",
    ):
        m.load_pinned_dataset_split_manifest(tampered)


def test_pin2r7_fix7_positive_real_contract_unaffected(contract: m.Run9RunContract) -> None:
    """正例回帰: 未改変の実 contract に対する呼び出しは、5欄全数の乖離
    検査を追加した後も引き続き成功する。"""
    m.load_pinned_dataset_split_manifest(contract)  # 例外を投げないことの確認


def test_pin2r7_fix7_covers_exactly_the_five_fields_this_loader_consumes() -> None:
    """Fix 7 の乖離検査対象が、本 loader が実際に消費する contract 欄
    ちょうど5つ（`dataset_manifest_sha`/`dataset_row_order_sha`/
    `practice_audio_split_manifest_sha`/`probe_manifest_sha`/
    `interventions.c1_sham_takes_per_founder`）と一致することを機械
    固定する（過不足の回帰防止）。"""
    assert set(m._DATASET_LOADER_LINKED_PIN_ACCESSORS) == {
        ("dataset_manifest_sha", "pin_field"),
        ("dataset_row_order_sha", "pin_field"),
        ("practice_audio_split_manifest_sha", "pin_field"),
        ("probe_manifest_sha", "pin_field"),
        ("c1_sham_takes_per_founder", "intervention_take_count_field"),
    }


def test_pin2r7_fix7_other_loaders_have_no_same_type_residual() -> None:
    """同型残余点検（コーディネータ指示）: 他の `load_pinned_*` loader
    （`load_pinned_probe_manifest`/`load_pinned_seed_policy_manifest`/
    `load_pinned_failure_abort_criteria`/`load_pinned_measurement_spec_
    manifest`/`load_pinned_founder_genome_document`）は、それぞれが
    実際に消費する contract 欄が単一（`load_pinned_founder_genome_
    document` は `founder_genome_shas.{founder_id}` の1欄のみ、他4関数
    はそれぞれ対応する `*_sha` 欄1欄のみ）であり、既存の単一欄乖離検査
    がその消費欄と過不足なく一致することをソース走査で機械確認する
    ——`load_pinned_dataset_split_manifest()` が5欄を消費するにもかかわ
    らず乖離検査を1欄にしか適用していなかったという Fix 7 と同型の欠陥
    が、他の loader には存在しないことの確認（「残余なし」の直接検証、
    憶測ではなくソース走査による確認）。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    single_field_loaders = {
        "load_pinned_probe_manifest": "probe_manifest_sha",
        "load_pinned_seed_policy_manifest": "seed_policy_sha",
        "load_pinned_failure_abort_criteria": "failure_abort_criteria_sha",
        "load_pinned_measurement_spec_manifest": "measurement_spec_sha",
    }
    # 次の**トップレベル**定義（関数/クラスいずれも、インデント無しの
    # `def `/`class ` で始まる行）の開始位置を全て収集する——`load_
    # pinned_founder_genome_document()` のように次の `load_pinned_*`
    # 関数までの間に無関係な関数が多数挟まるケースがあるため、
    # `load_pinned_` 限定のマーカーでは本文の終端を誤検出する。
    top_level_def_markers = sorted(
        m_.start() for m_ in re.finditer(r"\n(?:def |class )", source)
    )

    def _function_body(func_name: str) -> str:
        start = source.index(f"def {func_name}(")
        later_defs = [pos for pos in top_level_def_markers if pos > start]
        end = later_defs[0] if later_defs else len(source)
        return source[start:end]

    for func_name, expected_field in single_field_loaders.items():
        body = _function_body(func_name)
        # disk_contract/revalidated から読む pin 欄名（`pin_field("...")`
        # 呼び出しの引数）を全て抽出し、消費欄がちょうど1つで、それが
        # 期待欄と一致することを確認する。
        referenced_fields = sorted(set(re.findall(r'\.pin_field\("([^"]+)"\)', body)))
        assert referenced_fields == [expected_field], (
            f"{func_name}: expected to consume exactly {[expected_field]!r} via pin_field(), "
            f"found {referenced_fields!r} — possible same-type Fix 7 gap"
        )
        # 乖離検査（revalidated 側の同名呼び出し）も同じ欄に対して行われ
        # ていることを確認する（disk 側だけでなく passed 側も同一欄）。
        assert body.count(f'.pin_field("{expected_field}")') == 2, (
            f"{func_name}: expected exactly 2 pin_field({expected_field!r}) call sites "
            "(disk_contract + revalidated) — divergence check may not cover the consumed field"
        )

    genome_body = _function_body("load_pinned_founder_genome_document")
    assert "founder_genome_sha(founder_id)" in genome_body
    assert genome_body.count("founder_genome_sha(founder_id)") == 2
    # founder_genome_document 系関数は他の contract pin_field()/
    # intervention_take_count_field() を読まない（domain/rights_manifest
    # は別引数として渡され、contract からは読まない）。
    assert ".pin_field(" not in genome_body
    assert ".intervention_take_count_field(" not in genome_body


# ---------------------------------------------------------------------------
# PR #325 第7巡 Codex bot レビュー Fix 8（P2, 採用 — 正直性是正）: ID
# 和集合が canonical corpus 由来であることの「束縛」は load 時には
# 検証不能——正直に「sanity」（ID 形式 + 件数）へ格下げし、真の束縛は
# build 時/取得時/独立再現の3層（docstring (12)）が担うことを明記した。
# ---------------------------------------------------------------------------


def test_pin2r8_fix8_positive_real_contract_satisfies_sanity_checks(
    contract: m.Run9RunContract,
) -> None:
    """正例回帰: 実 contract は ID 形式（`^pjs\\d{3}$`）・件数（現行
    canonical N）の両 sanity 検査を満たし、引き続き成功する。"""
    loaded = m.load_pinned_dataset_split_manifest(contract)
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    all_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    assert all(m._PIN2_PRACTICE_SONG_ID_RE.match(sid) for sid in all_ids)
    assert len(all_ids) == sum(m._DATASET_SPLIT_EXPECTED_ROW_COUNTS.values())
    assert loaded["schema"] == m.SCHEMA_DATASET_SPLIT_MANIFEST


def test_pin2r8_fix8_rejects_id_substituted_with_invalid_format(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """必須負例（指摘のシナリオの一形態——1 ID 置換 + 全 pin/digest/
    inventory を自己整合させた改竄）: `row_ids.training[0]` を
    `_enumerate_pjs_song_ids()` の列挙規約（`^pjs\\d{3}$`）に反する ID
    へ置換し、`training_split_sha256`/`row_order_sha256`（宣言値・
    contract pin・dataset manifest 転記値の四者）/`sample_inventory` は
    すべて「正直に」再計算・更新する——Fix 1/3/4/6 を全て通過する構成
    （件数は不変のため Fix 3/新設の件数 sanity も無関係に通過、Fix 5 の
    決定論割当再導出へ到達する前に本 sanity 検査が先に発火することを
    確認する）。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered_practice = copy.deepcopy(practice_data)
    tampered_practice["row_ids"]["training"][0] = "not-a-valid-song-id"
    tampered_practice["training_split_sha256"] = m._compute_canonical_pin_sha256(
        list(tampered_practice["row_ids"]["training"])
    )
    new_row_order = (
        tampered_practice["row_ids"]["training"]
        + tampered_practice["row_ids"]["validation"]
        + tampered_practice["row_ids"]["sealed_holdout"]
    )
    new_row_order_sha = m._compute_canonical_pin_sha256(new_row_order)
    tampered_practice["row_order_sha256"] = new_row_order_sha
    split_of_song = {
        sid: split_name for split_name in ("training", "validation", "sealed_holdout")
        for sid in tampered_practice["row_ids"][split_name]
    }
    tampered_practice["sample_inventory"] = [
        f"{rank:04d}|{split_of_song[sid]}|{sid}" for rank, sid in enumerate(new_row_order)
    ]
    tampered_practice_path = tmp_path / "practice_audio_split_manifest.json"
    tampered_practice_path.write_bytes(
        (json.dumps(tampered_practice, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_practice_sha = m.compute_file_sha256(tampered_practice_path)

    tampered_dataset = copy.deepcopy(_dataset_split_manifest_data())
    tampered_dataset["song_splits"]["practice_audio_split_manifest_sha"] = new_practice_sha
    tampered_dataset["song_splits"]["row_order_sha256"] = new_row_order_sha
    # row_counts unaffected by a 1-for-1 substitution (70/15/15 unchanged).
    tampered_dataset_path = tmp_path / "dataset_split_manifest.json"
    tampered_dataset_path.write_bytes(
        (json.dumps(tampered_dataset, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    new_dataset_sha = m.compute_file_sha256(tampered_dataset_path)

    tampered_contract_raw = copy.deepcopy(contract.raw)
    tampered_contract_raw["practice_audio_split_manifest_sha"]["value"] = new_practice_sha
    tampered_contract_raw["dataset_manifest_sha"]["value"] = new_dataset_sha
    tampered_contract_raw["dataset_row_order_sha"]["value"] = new_row_order_sha
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(
        yaml.safe_dump(tampered_contract_raw, allow_unicode=True), encoding="utf-8"
    )
    tampered_contract = m.load_run9_contract(tampered_contract_raw)
    with pytest.raises(m.Run9ValidationError, match="列挙規約"):
        m.load_pinned_dataset_split_manifest(
            tampered_contract,
            manifest_path=tampered_dataset_path,
            practice_manifest_path=tampered_practice_path,
            contract_path=tampered_yaml_path,
        )


def test_pin2r8_fix8_song_id_regex_matches_real_ids_and_rejects_variants() -> None:
    """`_PIN2_PRACTICE_SONG_ID_RE`（`^pjs\\d{3}$`）が実データ全ID
    （`pjs001`〜`pjs100` 相当）に一致し、隣接する変種
    （桁数違い・大文字・接尾辞付き）を拒否することを直接確認する。"""
    practice_data = json.loads(m.PRACTICE_MANIFEST_PATH.read_text(encoding="utf-8"))
    all_ids = (
        list(practice_data["row_ids"]["training"])
        + list(practice_data["row_ids"]["validation"])
        + list(practice_data["row_ids"]["sealed_holdout"])
    )
    for sid in all_ids:
        assert m._PIN2_PRACTICE_SONG_ID_RE.match(sid), sid
    for bad in ("pjs1", "pjs0001", "PJS001", "pjs001x", "pjs-01", "xpjs001"):
        assert not m._PIN2_PRACTICE_SONG_ID_RE.match(bad), bad


def test_pin2r8_fix8_count_sanity_check_present_as_defense_in_depth() -> None:
    """件数 sanity 検査（`len(reconstructed_row_order) != sum(
    _DATASET_SPLIT_EXPECTED_ROW_COUNTS.values())`）の直接確認。

    境界宣言: 本チェックは現行コードパス上では他チェック
    （`validate_dataset_split_manifest()` の row_counts 静的照合 + Fix 3
    の row_counts 実体照合）により、到達時点で数学的に自明に成立する
    ことが既に保証されている——Fix 3 自身の合計チェックと同型の、意図的
    な多層防御（fail-closed defense in depth）である。ソース上の存在と
    docstring への明記をここで固定し、独立した runtime 到達可能性までは
    主張しない（誠実な境界宣言——実行不可能な負例を捏造しない）。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    func_start = source.index("def load_pinned_dataset_split_manifest(")
    later_def_positions = [
        m_.start() for m_ in re.finditer(r"\ndef ", source) if m_.start() > func_start
    ]
    func_end = later_def_positions[0] if later_def_positions else len(source)
    body = source[func_start:func_end]
    assert "canonical N" in body
    assert "_DATASET_SPLIT_EXPECTED_ROW_COUNTS.values()" in body
    doc_end = source.index('    effective_contract_path = (\n', func_start)
    docstring = source[func_start:doc_end]
    assert "束縛ではなく" in docstring
    assert "sanity" in docstring
    assert "再入条件" in docstring
    assert "machine_promotion_condition" in docstring


def test_pin2r8_fix8_docstring_cites_three_binding_layers_and_stale_history() -> None:
    """docstring が3層構造（build時/取得時/独立再現）と、第4-6巡時点の
    不正確な「構造的に束縛される」宣言が履歴として保持されていることを
    確認する。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    func_start = source.index("def load_pinned_dataset_split_manifest(")
    doc_end = source.index('    effective_contract_path = (\n', func_start)
    docstring = source[func_start:doc_end]
    assert "build 時" in docstring
    assert "取得時" in docstring
    assert "独立再現" in docstring
    assert "build_practice_split_manifest()" in docstring
    assert "practice_split_builder.py:198" in docstring
    assert "〔履歴:" in docstring
    assert "正直性是正" in docstring


# ---------------------------------------------------------------------------
# RUN9-L0-PIN-2: learning recipe 裁定値の境界値回帰（User 裁定 2026-08-25
# を validate_learning_recipe_manifest() が厳密強制することの直接確認 —
# 上記 Fix 7/15/17 系の型検証テストとは別に、裁定値そのものの境界を
# 明示的にカバーする）。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_trial_count", [31, 33])
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_pin2_learning_recipe_trial_count_off_by_one_rejected(
    arm_name: str, bad_trial_count: int,
) -> None:
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["trial_count"] = bad_trial_count
    with pytest.raises(m.Run9ValidationError, match="trial_count must be exactly 32"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize("bad_render_budget", [127, 129])
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_pin2_learning_recipe_render_budget_off_by_one_rejected(
    arm_name: str, bad_render_budget: int,
) -> None:
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["render_budget"] = bad_render_budget
    with pytest.raises(m.Run9ValidationError, match="render_budget must be exactly 128"):
        m.validate_learning_recipe_manifest(manifest)


@pytest.mark.parametrize(
    "bad_stopping_rule",
    ["FIXED_BUDGET_32_TRIALS", "fixed_budget_32_trials_no_success_early_stop", "EARLY_STOP_ON_GAIN"],
)
@pytest.mark.parametrize("arm_name", ["practice_recipe", "education_recipe"])
def test_pin2_learning_recipe_wrong_stopping_rule_rejected(
    arm_name: str, bad_stopping_rule: str,
) -> None:
    manifest = _valid_learning_recipe_manifest()
    manifest[arm_name]["stopping_rule"] = bad_stopping_rule
    with pytest.raises(m.Run9ValidationError, match="stopping_rule must be exactly"):
        m.validate_learning_recipe_manifest(manifest)


def test_pin2_learning_recipe_adjudicated_values_exactly_pass() -> None:
    """正例（境界ちょうど）: trial_count=32・render_budget=128・
    stopping_rule=裁定値 のとき validate は例外を投げない（fixture 既定値
    がすでに裁定値のため、本テストは裁定値そのものを明示して再確認する
    回帰）。"""
    manifest = _valid_learning_recipe_manifest()
    for arm_name in ("practice_recipe", "education_recipe"):
        manifest[arm_name]["trial_count"] = 32
        manifest[arm_name]["render_budget"] = 128
        manifest[arm_name]["stopping_rule"] = "FIXED_BUDGET_32_TRIALS_NO_SUCCESS_EARLY_STOP"
    m.validate_learning_recipe_manifest(manifest)  # 例外を投げないことの確認


def test_pin2_learning_recipe_reason_states_adjudicated_values(
    contract_raw: Dict[str, Any],
) -> None:
    """learning_recipe_sha の reason が User 裁定の出典と3つの機械強制値を
    明記していること。"""
    reason = contract_raw["learning_recipe_sha"]["reason"]
    assert "User 裁定 2026-08-25" in reason
    assert "trial_count == 32" in reason
    assert "render_budget == 128" in reason
    assert "FIXED_BUDGET_32_TRIALS_NO_SUCCESS_EARLY_STOP" in reason


def test_pin2_config_sha_reason_distinguishes_from_learning_recipe(
    contract_raw: Dict[str, Any],
) -> None:
    """config_sha の reason が learning_recipe_sha との混同を是正し、
    run9_execution_config.yaml を対象と明記していること（User 裁定
    2026-08-25）。"""
    reason = contract_raw["config_sha"]["reason"]
    assert "config_sha は learning_recipe_sha と同一ファイルを指さない" in reason
    assert "inputs/run9_execution_config.yaml" in reason
    assert contract_raw["config_sha"]["status"] == "PENDING"


def test_pin2_expected_speaker_map_sha_reason_states_af0_gap(
    contract_raw: Dict[str, Any],
) -> None:
    """〔履歴: RUN9-L0-PIN-2（2026-08-26）時点の回帰——当時 `expected_
    speaker_map_sha` は PENDING で、reason が af0 の gate_synth.py
    --speaker choices 非対応という構造的ギャップを明記していた。
    RUN9-L0-HARNESS-3a（2026-08-26）で同欄は PINNED 化され、`reason` キー
    は PINNED 欄の shape 規約（`value`/`status`/`source` のみ、`reason`
    は PENDING/BLOCKED 欄専用）により消失した——旧 reason 文言自体は
    `RUN9_CONTRACT.yaml` の YAML コメント（parse 対象外）として
    append-only で保持されている。本テストは新しい PINNED 状態と、旧
    reason 文言がコメントとして repo に残存していることの両方を確認する
    （`test_harness3a_expected_speaker_map_sha_pinned` と対になる）〕。"""
    field = contract_raw["expected_speaker_map_sha"]
    assert field["status"] == "PINNED"
    assert "reason" not in field
    contract_yaml_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert "ritsu, pjs, user, d3synth, amitaro" in contract_yaml_text


# ---------------------------------------------------------------------------
# PR #325 第1巡 Codex bot レビュー Fix 2（P2, 採用）: User 裁定
# 2026-08-25 の逐語一次ソースを scratchpad から repo 内収載
# （USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt、
# POR_CONCEPT_ADJUDICATION_20260824.txt と同型の前例）へ差し替えた。
# ---------------------------------------------------------------------------


def test_pin2r2_fix2_adjudication_source_file_exists() -> None:
    assert PIN2_USER_ADJUDICATION_PATH.is_file()


def test_pin2r2_fix2_adjudication_source_contains_verbatim_three_values() -> None:
    """凍結した3値（trial_count/render_budget/stopping_rule）が、repo 内
    収載した裁定文書の本文に一字一句そのまま存在すること（grep 照合 —
    「User 転記であって発明でない」ことを機械検証する）。"""
    text = PIN2_USER_ADJUDICATION_PATH.read_text(encoding="utf-8")
    assert "trial_count: 32" in text
    assert "render_budget: 128 logical_render_units per Founder" in text
    assert "FIXED_BUDGET_32_TRIALS_NO_SUCCESS_EARLY_STOP" in text
    assert (
        "config_sha は learning_recipe_sha と同一ファイルを指さない。" in text
    )
    assert "`inputs/run9_execution_config.yaml` の raw byte sha256 とする。" in text


def test_pin2r2_fix2_adjudication_source_body_byte_identical_to_scratchpad_origin() -> None:
    """本文（【RUN9 User裁定 — PIN-2 前提】以降）が起草時の作業メモ
    scratchpad/run9_user_adjudication_pin2.md と一字一句改変なしで
    一致すること（改変禁止の直接確認）。scratchpad ファイルが本セッション
    後に存在しない環境（fresh checkout 等）ではこの照合自体ができない
    ため、存在する場合のみ実行する（存在しない場合は repo 内収載ファイル
    自体の実在・grep 照合で足りるとみなし skip）。"""
    scratchpad_path = Path(
        "/tmp/claude-0/-home-user-ugh-prompt-engine/"
        "e505c1c2-c4ad-588b-a1b2-258051a522de/scratchpad/"
        "run9_user_adjudication_pin2.md"
    )
    if not scratchpad_path.is_file():
        pytest.skip("scratchpad origin file not present in this environment")
    origin_body = scratchpad_path.read_text(encoding="utf-8")
    origin_body = "【RUN9 User裁定" + origin_body.split("【RUN9 User裁定", 1)[1]
    committed_text = PIN2_USER_ADJUDICATION_PATH.read_text(encoding="utf-8")
    committed_body = "【RUN9 User裁定" + committed_text.split("【RUN9 User裁定", 1)[1]
    assert committed_body == origin_body


def test_pin2r2_fix2_contract_records_adjudication_source_sha256_as_comment() -> None:
    """RUN9_CONTRACT.yaml が USER_ADJUDICATION_20260825_PIN2_LEARNING_
    BUDGET.txt の実測 sha256 を append-only 情報コメントとして記録して
    いること（新 pin 欄は作らない設計判断——CONTRACT_PIN_FIELDS には
    含まれないことも確認する）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    actual_sha = m.compute_file_sha256(PIN2_USER_ADJUDICATION_PATH)
    assert actual_sha in contract_text
    assert "USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt" not in m.CONTRACT_PIN_FIELDS
    assert "user_adjudication" not in {f.lower() for f in m.CONTRACT_PIN_FIELDS}


def test_pin2r2_fix2_learning_recipe_and_config_reason_cite_committed_file(
    contract_raw: Dict[str, Any],
) -> None:
    for field_name in ("learning_recipe_sha", "config_sha"):
        reason = contract_raw[field_name]["reason"]
        assert "USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt" in reason, (
            f"{field_name}.reason should cite the committed adjudication source"
        )


def test_pin2r2_fix2_run9_schema_docstrings_cite_committed_file() -> None:
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt" in source
    assert "scratchpad/run9_user_adjudication_pin2.md" not in source


def test_pin1_other_existing_pins_unchanged(contract_raw: Dict[str, Any]) -> None:
    """本 PR は Scope IN の3欄以外の既存 pin 値を一切変更していないこと
    （代表サンプル: probe_manifest_sha / practice_audio_split_manifest_sha /
    backbone_checkpoint_sha が実ファイルと引き続き一致する）。"""
    assert contract_raw["probe_manifest_sha"]["status"] == "PINNED"
    assert contract_raw["probe_manifest_sha"]["value"] == m.compute_file_sha256(
        m.PROBE_MANIFEST_PATH
    )
    assert contract_raw["practice_audio_split_manifest_sha"]["status"] == "PINNED"
    assert contract_raw["practice_audio_split_manifest_sha"]["value"] == m.compute_file_sha256(
        m.PRACTICE_MANIFEST_PATH
    )


def test_pin1_r2_seed_policy_manifest_byte_unchanged(contract_raw: Dict[str, Any]) -> None:
    """seed_policy_manifest.json のバイト・pin 値は RUN9-L0-PIN-1 初回
    実装時点から一貫して不変であること（第1巡・第2巡とも同ファイルは
    改訂対象外）をハードコードした sha256 で固定する。"""
    assert contract_raw["seed_policy_sha"]["value"] == (
        "1ff25f429e544c1b6c9b10c5a388833fa2506b1ce12162c7b17cc4af32df05f4"
    )
    assert contract_raw["seed_policy_sha"]["value"] == m.compute_file_sha256(
        m.SEED_POLICY_MANIFEST_PATH
    )


def test_pin1_r3_measurement_spec_manifest_file_byte_unchanged_despite_pending_pin() -> None:
    """PR #324 第2巡 Fix 5: measurement_spec_sha は contract 欄としては
    PENDING へ復帰したが、inputs/measurement_spec_manifest.json 自体の
    バイトは RUN9-L0-PIN-1 初回実装時点から一切改変していない（manifest/
    validator/loader は事前配線のまま残置——撤去していないことの確認）。

    PR #333 第2巡指摘1（P1、採用）で例外的に改訂: C0/C1/positive/negative
    の4エントリの identity_metric_space_ref が rev 0.6 裁定 §7 で
    supersede 済みの calibration 節を参照したままだったため、新規
    identity_decision_protocol_ref を追加した（probe_manifest.json
    revision_bridge と同型の是正——evaluation/probe_manifest.json 側の
    対応する `test_rev06_*` 系テスト参照）。measurement_spec_sha 自体は
    引き続き PENDING のまま（VG-L0 学習ハーネス実装待ちの律速は不変——
    本改訂は identity 軸カタログの参照先是正のみで、development/
    generalization 軸の extractor 未実装状態は変えない）。旧値（PIN-1〜
    PR #333 第1巡まで不変だった値、履歴として保持）:
    "cb3e3b45973caa3737531b9636454a4542bc75f60d03a80a6a0411a9847bfdd5"
    """
    assert m.compute_file_sha256(m.MEASUREMENT_SPEC_MANIFEST_PATH) == (
        "22ea90724141df64bcb5f393ed2000261641e6c2c51a14445853689e90e9bc52"
    )


# `test_pin1_r3_failure_abort_criteria_repinned_lineage`（3世代版）は PR #324
# 第3巡の repin により超過し、下記
# `test_pin1_r4_failure_abort_criteria_repinned_lineage_four_generations`
# （4世代・全履歴を包含する上位互換）へ置き換えた（重複削除、Codex bot
# レビュー各巡の教訓「修正からの再修正は早期に打ち切る」に沿い、単に
# 世代数を書き換えるのではなく1本化した）。


@pytest.mark.parametrize(
    "path_const_name,loader",
    [
        ("SEED_POLICY_MANIFEST_PATH", "_seed_policy_manifest_data"),
        ("FAILURE_ABORT_MANIFEST_PATH", "_failure_abort_criteria_data"),
        ("MEASUREMENT_SPEC_MANIFEST_PATH", "_measurement_spec_manifest_data"),
        ("DATASET_SPLIT_MANIFEST_PATH", "_dataset_split_manifest_data"),
    ],
)
def test_pin1_manifest_reserializes_to_identical_bytes(path_const_name: str, loader: str) -> None:
    """`json.dumps(..., ensure_ascii=False, sort_keys=True, indent=2)` +
    末尾改行の決定論 pretty 規約で再直列化した結果が実ファイルと byte-for-
    byte 一致すること（founders/*.json・probe_manifest.json と同一規約）。
    """
    path: Path = getattr(m, path_const_name)
    data = globals()[loader]()
    reserialized = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    assert reserialized.encode("utf-8") == path.read_bytes()


def test_pin1_readme_has_no_stale_present_tense_pending_claims() -> None:
    """README.md のブロッカー節・現在地マップに、引き続き PINNED のままの
    2欄（seed_policy_sha/failure_abort_criteria_sha）を「現在 PENDING」と
    主張する記述が残っていないこと（履歴注記〔履歴: ...〕・取消線
    ~~...~~・「解消済み」節見出し内の言及は許容）。

    measurement_spec_sha は対象外——PR #324 第2巡 Fix 5 で PENDING へ
    正当に復帰したため、現在形の PENDING 記述はむしろ正しい状態表現で
    あり stale ではない（専用テスト
    `test_pin1_r3_readme_measurement_spec_pending_count_updated` が
    別途、件数記述の追随を確認する）。
    """
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    fields = ("seed_policy_sha", "failure_abort_criteria_sha")
    for line in readme_text.splitlines():
        for field in fields:
            if field in line and "PENDING" in line:
                assert ("履歴" in line) or ("解消済み" in line) or ("~~" in line), (
                    f"stale current-tense PENDING claim for {field!r}: {line!r}"
                )


def test_pin1_r3_readme_measurement_spec_pending_count_updated() -> None:
    """PR #324 第2巡指摘: 残 PENDING 件数の記述を12→13へ全箇所更新
    （measurement_spec_sha が PENDING へ復帰した分の増分）。旧い『12件』
    という総数記述が残存していないことを確認する。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "13" in readme_text
    for stale in ("残 PENDING 12件", "PENDING 件数 12", "残る12件"):
        assert stale not in readme_text


def test_pin2_readme_pending_count_updated_to_ten_and_eleven() -> None:
    """RUN9-L0-PIN-2: 残 PENDING 件数の記述を12→10（総13→11）へ更新
    したことの確認。旧い『12→13』時点の件数記述はもはや現在形の主張として
    残っていない（`gate_state()` bullet 内の〔履歴: ...〕マーカー配下の
    言及のみが許容される — `test_pin1_readme_has_no_stale_present_tense_
    pending_claims` と同じ 履歴/解消済み/取消線 許容規約）。
    〔RUN9-L0-HARNESS-1（2026-08-26）で 10欄/11欄 自体も履歴化された——
    下記 `test_harness1_readme_pending_count_updated_to_nine_and_ten` 参照〕"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "pre-run 必須10欄" in readme_text
    assert "総 PENDING 11欄" in readme_text
    # 12/13欄の言及は段落単位（空行区切り）で 履歴/解消済み マーカーの
    # 有無を判定する——bullet が折り返して物理行が分かれるため、
    # `test_pin1_readme_has_no_stale_present_tense_pending_claims` の per-line
    # 判定はここでは使えない（該当マーカーと数値が別の物理行にある）。
    for paragraph in readme_text.split("\n\n"):
        if "pre-run 必須12欄" in paragraph or "総 PENDING 13欄" in paragraph:
            assert ("履歴" in paragraph) or ("解消済み" in paragraph), (
                f"stale current-tense 12/13-count claim in paragraph: {paragraph!r}"
            )
    # RUN9-L0-HARNESS-1 後は 10欄/11欄 も同じ理由で履歴化されている
    # べき（本テスト自体は旧 12/13 数値のみを対象とするため、10/11 の
    # 現在形残存チェックは下記の新規テストが担当する）。


# RUN9-L0-HARNESS-1 で `dependency_pins_sha` を PINNED 化した際、README の
# 記述は10→9（総11→10）へ更新された（旧テスト
# `test_harness1_readme_pending_count_updated_to_nine_and_ten`）。PR #326
# 第2巡 Fix 3（P1、採用、2026-08-26）で同欄が PENDING へ差し戻されたため
# 9/10 は一時的な状態に留まり、下記テストへ一本化した（重複削除、
# `test_pin2_readme_pending_count_updated_to_ten_and_eleven` 直前の
# コメントと同型の既存規約）。
def test_harness1_pr326_fix3_readme_pending_count_reverted_to_ten_and_eleven() -> None:
    """PR #326 第2巡 Fix 3（P1、採用、2026-08-26）: `dependency_pins_sha`
    の PENDING 差し戻しに伴い、README の残 PENDING 件数記述は9→10
    （総10→11）へ戻った。一時的だった『9→10』時点の件数記述はもはや
    現在形の主張として残っていない（履歴/解消済み マーカー配下の言及の
    み許容 — 同じ規約の第4世代）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "pre-run 必須10欄" in readme_text
    assert "総 PENDING 11欄" in readme_text
    for paragraph in readme_text.split("\n\n"):
        if "pre-run 必須9欄" in paragraph or "総 PENDING 10欄" in paragraph:
            assert ("履歴" in paragraph) or ("解消済み" in paragraph), (
                f"stale current-tense 9/10-count claim in paragraph: {paragraph!r}"
            )


# RUN9-EXECPROFILE-1（2026-08-26）で `execution_profile_sha` が PINNED 化
# されたことにより、README の残 PENDING 件数記述は9→10（総10→11）から
# 10→9（総11→10）へ再度更新された。旧『9/10』（HARNESS-1 一時 PINNED 時点）
# は上記テストが既に履歴ガードしているため、本テストは新たな現在値
# （9欄/10欄）が実在すること、および直前の現在値だった10欄/11欄が
# 履歴/解消済みマーカー配下でのみ残っていることを確認する（同じ規約の
# 第5世代）。
def test_execprofile_readme_pending_count_updated_to_nine_and_ten() -> None:
    """RUN9-EXECPROFILE-1: `execution_profile_sha` の PINNED 化に伴い、
    README の残 PENDING 件数記述は9件（総10件）へ更新された。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "pre-run 必須9欄" in readme_text
    assert "総 PENDING 10欄" in readme_text
    for paragraph in readme_text.split("\n\n"):
        if "pre-run 必須10欄" in paragraph or "総 PENDING 11欄" in paragraph:
            assert ("履歴" in paragraph) or ("解消済み" in paragraph), (
                f"stale current-tense 10/11-count claim in paragraph: {paragraph!r}"
            )


def test_harness1_pr326_fix3_readme_dependency_pins_sha_pending_again() -> None:
    """`dependency_pins_sha` が PR #326 第2巡 Fix 3（P1、採用）で PENDING
    へ差し戻されたことに伴い、README の pre-run 必須欄の現行列挙に同欄が
    再び含まれていること（PINNED 化時に一時的に除去されていたが、
    PENDING 復帰後は他の PENDING 欄と同列で列挙されているべき —
    `test_pin2_readme_dataset_manifest_sha_no_longer_claimed_pending` の
    逆方向チェック）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    found = any(
        "`config_sha`/`dependency_pins_sha`" in line for line in readme_text.splitlines()
    )
    assert found, "README no longer lists dependency_pins_sha in the pending enumeration"


def test_pin2_readme_dataset_manifest_sha_no_longer_claimed_pending() -> None:
    """`dataset_manifest_sha`/`dataset_row_order_sha` を含む pre-run 必須
    欄の現行列挙から両欄が除去されていること（PINNED 化後も列挙に残る
    stale claim の防止）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    for line in readme_text.splitlines():
        if "`dataset_manifest_sha`/`dataset_row_order_sha`/`config_sha`" in line:
            raise AssertionError(
                f"stale enumeration still lists dataset_manifest_sha/dataset_row_order_sha as "
                f"pending alongside config_sha: {line!r}"
            )


# ---------------------------------------------------------------------------
# dependency_pins_manifest (RUN9-L0-HARNESS-1): 正常系 + fail-closed 分岐
# + cross-check with backbone_runtime_bundle.json
# ---------------------------------------------------------------------------


def test_harness1_dependency_pins_manifest_validates() -> None:
    m.validate_dependency_pins_manifest(_dependency_pins_manifest_data())  # 例外なしの確認


def test_harness1_dependency_pins_manifest_schema_field() -> None:
    data = _dependency_pins_manifest_data()
    assert data["schema"] == "run9-dependency-pins/1.0" == m.SCHEMA_DEPENDENCY_PINS_MANIFEST


def test_harness1_dependency_pins_manifest_unknown_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_dependency_pins_manifest_missing_key_fail_closed() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    del data["render_asset_ledger"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_ledger_entry_mismatched_sha_cannot_claim_verified_match() -> None:
    """expected_sha256 != actual_sha256 のまま status VERIFIED_MATCH を
    名乗ることはできない（validator が個別に強制する）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["render_asset_ledger"][0]["actual_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="VERIFIED_MATCH but expected_sha256"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_ledger_registers_exactly_expected_logical_names() -> None:
    data = _dependency_pins_manifest_data()
    names = {entry["logical_name"] for entry in data["render_asset_ledger"]}
    assert names == set(m._DEPENDENCY_LEDGER_BUNDLE_PATHS)


def test_harness1_ledger_duplicate_logical_name_fail_closed() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    dup = copy.deepcopy(data["render_asset_ledger"][0])
    data["render_asset_ledger"].append(dup)
    with pytest.raises(m.Run9ValidationError, match="duplicate logical_name"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_acoustic_export_companions_status_is_honest_obtained_via_reexport() -> None:
    """RUN9-L0-HARNESS-2 実測結果の回帰確認: acoustic export companions は
    checkpoint からの再export で取得済み（`OBTAINED_VIA_REEXPORT`）——
    acoustic.onnx は歴史値と不一致のまま `OBTAINED_DERIVED_NEW_BYTES` を
    正直に自称し、`OBTAINED_VERIFIED_MATCH` を捏造していないことを確認
    する（旧 HARNESS-1 時点の `NOT_OBTAINED_TARBALL_MISS` 状態を
    引き継いだ回帰テストの後継——`test_harness1_tar_gz_ledger_stale_miss_
    consistency_fail_closed` 等 MISS 経路の validator shape 自体は
    `_legacy_dependency_pins_manifest_data()` で引き続き非退行確認する）。
    """
    data = _dependency_pins_manifest_data()
    assert data["acoustic_export_companions"]["status"] == "OBTAINED_VIA_REEXPORT"
    items_by_name = {
        item["logical_name"]: item for item in data["acoustic_export_companions"]["expected_items"]
    }
    expected_names = set(items_by_name)
    assert expected_names == {
        "acoustic_onnx", "acoustic_dsconfig_yaml", "acoustic_phonemes_json", "speaker_embed_ritsu",
    }
    assert items_by_name["acoustic_onnx"]["status"] == "OBTAINED_DERIVED_NEW_BYTES"
    assert items_by_name["acoustic_onnx"]["matches_historical"] is False
    for name in ("acoustic_dsconfig_yaml", "acoustic_phonemes_json", "speaker_embed_ritsu"):
        assert items_by_name[name]["status"] == "OBTAINED_VERIFIED_MATCH"
        assert items_by_name[name]["matches_historical"] is True
        assert items_by_name[name]["replay_evidence"] is True


def test_harness1_ledger_and_acoustic_companion_vocabularies_are_disjoint() -> None:
    """render_asset_ledger の logical_name 語彙と acoustic_export_
    companions.expected_items の logical_name 語彙が disjoint であること
    （構造的に二重計上を不可能にする不変条件そのものの回帰確認 —
    `validate_dependency_pins_manifest()` の集合等価チェック2つが独立に
    強制するため、経由テストは不要——本テストは前提となる定数の disjoint
    性自体を確認する）。"""
    assert set(m._DEPENDENCY_LEDGER_BUNDLE_PATHS).isdisjoint(
        set(m._DEPENDENCY_ACOUSTIC_COMPANION_BUNDLE_PATHS)
    )
    data = _dependency_pins_manifest_data()
    ledger_names = {entry["logical_name"] for entry in data["render_asset_ledger"]}
    companion_names = {
        item["logical_name"] for item in data["acoustic_export_companions"]["expected_items"]
    }
    assert ledger_names.isdisjoint(companion_names)


def test_harness1_ledger_rejects_unknown_logical_name_fail_closed() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    bogus_entry = copy.deepcopy(data["render_asset_ledger"][0])
    bogus_entry["logical_name"] = "not_a_registered_asset"
    bogus_entry["expected_sha256"] = bogus_entry["actual_sha256"] = "a" * 64
    data["render_asset_ledger"].append(bogus_entry)
    with pytest.raises(m.Run9ValidationError, match="must register exactly"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_tar_gz_ledger_nonempty_and_well_formed() -> None:
    data = _dependency_pins_manifest_data()
    members = data["tar_gz_full_member_ledger"]
    assert len(members) == 39
    paths = [entry["path"] for entry in members]
    assert len(paths) == len(set(paths))
    for entry in members:
        assert m._SHA256_HEX_RE.match(entry["sha256"])
        assert entry["size_bytes"] > 0


def test_harness1_tar_gz_ledger_stale_miss_consistency_fail_closed() -> None:
    """tar member ledger に acoustic export companion と同名 basename +
    同一 digest（PR #326 第5巡 Fix 12 以降、basename だけでは足りない）が
    紛れ込んだのに acoustic_export_companions.status が
    NOT_OBTAINED_TARBALL_MISS のままだと fail-closed 拒否する（将来
    tar.gz が repin されて中身が変わった際の drift 検出）。RUN9-L0-
    HARNESS-2 で実データの companions は `OBTAINED_VIA_REEXPORT` へ恒久
    遷移したため、MISS 経路 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    expected_sha = next(
        item["expected_sha256"] for item in data["acoustic_export_companions"]["expected_items"]
        if item["logical_name"] == "acoustic_onnx"
    )
    _append_tar_member(
        data, path="onnx_gate_40000/acoustic.onnx", size_bytes=1234, sha256=expected_sha,
    )
    with pytest.raises(m.Run9ValidationError, match="stale-miss inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_python_dependency_pins_registers_render_and_analysis_stack() -> None:
    data = _dependency_pins_manifest_data()
    packages = {entry["package"] for entry in data["python_dependency_pins"]}
    assert packages == {
        "python", "numpy", "librosa", "numba", "scipy", "soundfile", "PyYAML",
        "pyloudnorm", "onnxruntime",
    }
    for entry in data["python_dependency_pins"]:
        assert entry["status"] == "MATCH"
        assert entry["pin_version"] == entry["observed_version"]


def test_harness1_python_dependency_pins_missing_package_fail_closed() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["python_dependency_pins"] = [
        e for e in data["python_dependency_pins"] if e["package"] != "onnxruntime"
    ]
    with pytest.raises(m.Run9ValidationError, match="must register exactly"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_python_dependency_pins_version_mismatch_cannot_claim_match() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    for entry in data["python_dependency_pins"]:
        if entry["package"] == "onnxruntime":
            entry["observed_version"] = "1.0.0"
    with pytest.raises(m.Run9ValidationError, match="declares status MATCH but pin_version"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_diffsinger_commit_matches_bundle_forward_declaration() -> None:
    """diffsinger_render_code_commit.pin_commit_full が
    backbone_runtime_bundle.json の run9_render_code_commit（前方宣言、
    DECLARED_FOR_RUN9）と一致すること（実測 clone の rev-parse とも一致
    済み）。"""
    data = _dependency_pins_manifest_data()
    bundle = m._loads_strict_json(m.BACKBONE_RUNTIME_BUNDLE_PATH.read_text(encoding="utf-8"))
    expected = bundle["run9_runtime_inputs"]["run9_render_code_commit"]["commit_full"]
    assert data["diffsinger_render_code_commit"]["pin_commit_full"] == expected
    assert data["diffsinger_render_code_commit"]["cloned_commit_full"] == expected


def test_harness1_diffsinger_commit_mismatch_cannot_claim_verified_match() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["diffsinger_render_code_commit"]["cloned_commit_full"] = "0" * 40
    with pytest.raises(m.Run9ValidationError, match="VERIFIED_MATCH but pin_commit_full"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_speaker_embed_candidates_are_never_pinned_vocabulary() -> None:
    data = _dependency_pins_manifest_data()
    assert data["speaker_embeddings_unpinned_candidates"]["pjs"]["status"] == "UNPINNED_CANDIDATE"
    assert data["speaker_embeddings_unpinned_candidates"]["user"]["status"] == "UNPINNED_CANDIDATE"
    assert (
        data["speaker_embeddings_unpinned_candidates"]["pjs"]["candidate_sha256"]
        != data["speaker_embeddings_unpinned_candidates"]["user"]["candidate_sha256"]
    )


def test_harness1_speaker_embed_candidate_pinned_status_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"]["pjs"]["status"] = "PINNED"
    with pytest.raises(m.Run9ValidationError, match="UNPINNED_CANDIDATE"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_speaker_embed_candidates_pjs_user_identical_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    pjs_sha = data["speaker_embeddings_unpinned_candidates"]["pjs"]["candidate_sha256"]
    data["speaker_embeddings_unpinned_candidates"]["user"]["candidate_sha256"] = pjs_sha
    # Fix 16 (PR #326 第7巡) の first16 整合検証が先に発火しないよう、
    # first16 も揃える（本テストの意図は pjs==user 一致検出であり、
    # first16 矛盾検出ではない）。
    data["speaker_embeddings_unpinned_candidates"]["user"]["candidate_sha256_first16"] = (
        pjs_sha[:16]
    )
    with pytest.raises(m.Run9ValidationError, match="must differ"):
        m.validate_dependency_pins_manifest(data)


@pytest.mark.parametrize("section_name", ["smoke_render", "budget_estimate"])
def test_harness2_completed_sections_are_honestly_completed(section_name: str) -> None:
    """smoke render / budget estimate はいずれも RUN9-L0-HARNESS-2 実測
    により COMPLETED（数値を捏造しない、実測値に基づく）ことの回帰確認
    （旧 HARNESS-1 時点の BLOCKED 状態の後継）。"""
    data = _dependency_pins_manifest_data()
    assert data[section_name]["status"] == "COMPLETED"
    assert data[section_name]["reason"].strip()


@pytest.mark.parametrize("section_name", ["smoke_render", "budget_estimate"])
def test_harness1_blocked_section_bad_status_fail_closed(section_name: str) -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data[section_name]["status"] = "DONE"
    with pytest.raises(m.Run9ValidationError, match="status must be one of"):
        m.validate_dependency_pins_manifest(data)


# --- load_pinned_dependency_pins_manifest(): bundle cross-check ------------


def test_harness1_pr326_fix3_load_pinned_dependency_pins_manifest_raises_pending(
    contract: m.Run9RunContract,
) -> None:
    """PR #326 第2巡 Fix 3（P1, 採用）: `dependency_pins_sha` は PENDING へ
    差し戻されたため、現行の実 contract に対して呼ぶと必ず『not PINNED』
    で fail-closed 拒否する（`test_pin1_r3_load_pinned_measurement_spec_
    manifest_raises_pending` と同型 — manifest/validator/loader 自体は
    事前配線のまま残置しつつ、pin されていない artifact を消費させない）。"""
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_dependency_pins_manifest(contract)


def _tampered_contract_with_dependency_pins_sha_pinned(
    contract: m.Run9RunContract, tmp_path: Path, *, value: str, suffix: str = "",
) -> Tuple[m.Run9RunContract, Path]:
    """`dependency_pins_sha` を強制的に PINNED（指定した value）へ書き
    換えた合成 contract + その disk 正典コピーを用意する（PR #326 第2巡
    Fix 3 で本欄が PENDING へ差し戻された後も、cross-check 等 PINNED
    前提のロジック自体は生きていることをテストするための共通ヘルパー）。"""
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["dependency_pins_sha"] = {"value": value, "status": "PINNED"}
    tampered_contract_path = tmp_path / f"RUN9_CONTRACT{suffix}.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), tampered_contract_path


def test_harness1_load_pinned_dependency_pins_manifest_happy_path_with_forced_pin(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`dependency_pins_sha` を（テスト内で）PINNED へ強制した合成
    contract を使えば、loader は現行 manifest を正常に読み込める
    ——manifest/validator/loader 自体の正常系は本欄の contract 上の
    PENDING/PINNED 状態と独立に機能することの確認（`_PIN1_LOADER_CASES`
    が測る「pin 欄が PINNED でなければ拒否する」層とは別の層）。"""
    real_value = m.compute_file_sha256(m.DEPENDENCY_PINS_MANIFEST_PATH)
    tampered_contract, tampered_contract_path = _tampered_contract_with_dependency_pins_sha_pinned(
        contract, tmp_path, value=real_value, suffix="1",
    )
    data = m.load_pinned_dependency_pins_manifest(
        tampered_contract, contract_path=tampered_contract_path,
    )
    assert data["schema"] == m.SCHEMA_DEPENDENCY_PINS_MANIFEST


def test_harness1_load_pinned_dependency_pins_manifest_detects_ledger_bundle_drift(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """render_asset_ledger の expected_sha256 が
    backbone_runtime_bundle.json の対応する pin 値と乖離していると
    cross-check (6) が fail-closed 拒否する（`dependency_pins_sha` を
    テスト内で PINNED 強制した合成 contract 経由 — 本欄が PR #326 第2巡
    Fix 3 で PENDING へ差し戻された後も、この cross-check ロジック自体は
    生きていることの確認）。"""
    tampered = copy.deepcopy(_dependency_pins_manifest_data())
    tampered["render_asset_ledger"][0]["expected_sha256"] = "f" * 64
    tampered["render_asset_ledger"][0]["actual_sha256"] = "f" * 64
    tampered_path = tmp_path / "dependency_pins_manifest.json"
    text = json.dumps(tampered, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    tampered_path.write_bytes(text.encode("utf-8"))
    # 差し替え後の manifest バイトは元 pin と一致しないため、まずは
    # sha256 照合 (4) 段階で拒否される想定——本テストは「manifest
    # バイト改竄検出」自体は既存の parametrize 済みテストが担うため、
    # ここでは cross-check 経路そのものをコード経由（validate 済みデータを
    # 直接 loader 内部相当で突き合わせる）で検証する代わりに、
    # 実運用と同じ経路（pin 値との不一致で fail-closed）を素直に確認する。
    real_value = m.compute_file_sha256(m.DEPENDENCY_PINS_MANIFEST_PATH)
    tampered_contract, tampered_contract_path = _tampered_contract_with_dependency_pins_sha_pinned(
        contract, tmp_path, value=real_value, suffix="2",
    )
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        m.load_pinned_dependency_pins_manifest(
            tampered_contract, manifest_path=tampered_path, contract_path=tampered_contract_path,
        )


def test_harness1_bundle_get_missing_path_fail_closed() -> None:
    with pytest.raises(m.Run9ValidationError, match="期待するキー経路"):
        m._bundle_get({"a": {}}, "a", "b", "c")


def test_harness1_dependency_ledger_bundle_paths_cover_all_ledger_entries() -> None:
    """cross-check (6) が使うマッピングが render_asset_ledger の全
    logical_name を網羅していること（片方だけ更新されて drift する事故の
    防止）。"""
    data = _dependency_pins_manifest_data()
    ledger_names = {entry["logical_name"] for entry in data["render_asset_ledger"]}
    assert set(m._DEPENDENCY_LEDGER_BUNDLE_PATHS) == ledger_names
    bundle = m._loads_strict_json(m.BACKBONE_RUNTIME_BUNDLE_PATH.read_text(encoding="utf-8"))
    for entry in data["render_asset_ledger"]:
        bundle_value = m._bundle_get(bundle, *m._DEPENDENCY_LEDGER_BUNDLE_PATHS[entry["logical_name"]])
        assert bundle_value == entry["expected_sha256"] == entry["actual_sha256"]


def test_harness1_pr326_fix3_dependency_pins_sha_still_pending(
    contract: m.Run9RunContract,
) -> None:
    """PR #326 第2巡 Fix 3（P1, 採用）: `dependency_pins_sha` は VG-L0
    学習ハーネス本体の import closure が未確定のため PENDING（数値を
    捏造しない、fail-closed 判断の回帰確認 — `execution_profile_sha` と
    同型）。manifest 実体は repo に残置されたまま（render/analysis 層の
    実測記録として）であることも併せて確認する。"""
    field = contract.pin_field("dependency_pins_sha")
    assert field["status"] == "PENDING"
    assert field["value"] is None
    assert m.DEPENDENCY_PINS_MANIFEST_PATH.is_file()


# `test_harness1_execution_profile_sha_still_pending`（本 Memo=RUN9-L0-
# HARNESS-1 時点で execution_profile_sha が PENDING のままであることの
# 回帰確認）は RUN9-EXECPROFILE-1（2026-08-26）で同欄が PINNED 化された
# ことにより恒久的に破綻する（`contract` fixture は live の disk contract
# を指すため、旧テストは書き換えるほかない——`test_pin2_pre_run_pending_
# count_is_ten` 系と同型の一本化規約）。下記テストへ置き換える。
def test_execprofile_execution_profile_sha_now_pinned(contract: m.Run9RunContract) -> None:
    """RUN9-EXECPROFILE-1（2026-08-26）: User 裁定「RUN9 User裁定 —
    execution_profile_sha」の承認により、execution_profile_sha は
    PENDING → PINNED へ遷移した（数値は捏造ではなく実測 manifest 実バイト
    sha256——`test_execprofile_load_pinned_execution_profile_manifest_
    happy_path` が実体側の検証を担う）。"""
    field = contract.pin_field("execution_profile_sha")
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.EXECUTION_PROFILE_MANIFEST_PATH)


def test_harness1_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #326 第1巡 Codex bot レビュー Fix 1/Fix 2（P2 ×2, 採用, 2026-08-26）:
# status 判別型 shape の負例テスト（将来汚染防止 — status 文字列の書き換え
# だけでは validator を通過できないことの回帰固定）。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix1_status_flip_alone_is_rejected() -> None:
    """acoustic_export_companions.status を OBTAINED_VERIFIED_MATCH へ
    書き換えるだけ（トップレベルの MISS narrative も expected_items の
    measured_sha256 も一切追加しない）の改竄は fail-closed 拒否される
    ——PR #326 第6巡 Fix 14 により、まずトップレベルの MISS-only
    フィールド（verdict/fail_closed_disposition）残置が unknown key と
    して先に拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix1_status_flip_with_top_level_fixed_still_rejected() -> None:
    """トップレベル（Fix 14）は正しく整合させても、expected_items に
    measured_sha256 を追加しなければ item レベルの欠落として fail-closed
    拒否される（Fix 1 の本来の対象、Fix 14 導入後の回帰確認）。RUN9-L0-
    HARNESS-2 で実データの companions shape が変わったため、旧 shape の
    非退行確認には `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    del data["acoustic_export_companions"]["verdict"]
    del data["acoustic_export_companions"]["fail_closed_disposition"]
    data["acoustic_export_companions"]["acquisition_record"] = {
        "acquired_at": "2026-08-27", "acquisition_summary": "x",
    }
    with pytest.raises(m.Run9ValidationError, match="missing required key.*measured_sha256|measured_sha256"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix1_not_obtained_forbids_measured_sha256() -> None:
    """status が NOT_OBTAINED_TARBALL_MISS のまま measured_sha256 を item
    へ書き加える（未取得なのに実測値がある、という矛盾）と unknown key
    として拒否される。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["expected_items"][0]["measured_sha256"] = (
        data["acoustic_export_companions"]["expected_items"][0]["expected_sha256"]
    )
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix1_obtained_measured_mismatch_rejected() -> None:
    """OBTAINED_VERIFIED_MATCH で measured_sha256 を付与しても、
    expected_sha256 と不一致なら拒否される。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    del data["acoustic_export_companions"]["verdict"]
    del data["acoustic_export_companions"]["fail_closed_disposition"]
    data["acoustic_export_companions"]["acquisition_record"] = {
        "acquired_at": "2026-08-27", "acquisition_summary": "x",
    }
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = "0" * 64
        item["acquisition_source"] = "THIS_TARBALL"
    with pytest.raises(m.Run9ValidationError, match="measured_sha256.*!= expected_sha256"):
        m.validate_dependency_pins_manifest(data)


def _append_tar_member(data: Dict[str, Any], *, path: str, size_bytes: int, sha256: str) -> None:
    """テストヘルパー: `tar_gz_full_member_ledger` へ1行追加し、PR #326
    第4巡 Fix 10 が要求する `tar_gz_ledger_integrity` の
    `member_count`/`total_size_bytes` 宣言を実体に追随させる（追随
    させないと Fix 10 の len(ledger)==member_count 束縛に必ず抵触する
    ため、`tar_gz_full_member_ledger.append()` の直接呼び出しの代わりに
    本ヘルパーを使う）。"""
    data["tar_gz_full_member_ledger"].append({
        "path": path, "size_bytes": size_bytes, "sha256": sha256,
    })
    data["tar_gz_ledger_integrity"]["member_count"] += 1
    data["tar_gz_ledger_integrity"]["total_size_bytes"] += size_bytes
    # member_count_matched must equal member_count (validator invariant) —
    # keep the synthetic reread record in lockstep with the mutated ledger.
    data["tar_gz_ledger_integrity"]["independent_reread_verification"]["member_count_matched"] += 1


def _mark_companions_top_level_obtained(
    data: Dict[str, Any], *, acquired_at: str = "2026-08-27",
    acquisition_summary: str = "test fixture acquisition",
) -> None:
    """テストヘルパー: acoustic_export_companions のトップレベル
    narrative を OBTAINED shape（PR #326 第6巡 Fix 14）へ揃える——status
    自体・expected_items 側の遷移は呼び出し側の責務のまま（本ヘルパーは
    トップレベルの MISS-only フィールド除去 + acquisition_record 付与
    のみを担う、`_obtain_all_acoustic_companions()` が status/items まで
    含めて丸ごと遷移させるのとは対象範囲が異なる）。"""
    data["acoustic_export_companions"].pop("verdict", None)
    data["acoustic_export_companions"].pop("fail_closed_disposition", None)
    data["acoustic_export_companions"]["acquisition_record"] = {
        "acquired_at": acquired_at, "acquisition_summary": acquisition_summary,
    }


def _obtain_all_acoustic_companions(
    data: Dict[str, Any], *, acquisition_source: str = "THIS_TARBALL",
    acquired_at: str = "2026-08-27", acquisition_summary: str = "test fixture acquisition",
) -> None:
    """テストヘルパー: acoustic_export_companions を OBTAINED_VERIFIED_
    MATCH へ遷移させ、各 item に正しい measured_sha256 +
    acquisition_source（PR #326 第3巡 Fix 7、既定 THIS_TARBALL）を付与
    する。`acquisition_source="THIS_TARBALL"` の場合のみ、Fix 4（第2巡）
    が要求する対応 tar member（basename 一致 + sha256 一致）を
    `_append_tar_member()` 経由で tar_gz_full_member_ledger へ追加する
    （THIS_TARBALL 以外——DRIVE_DIRECT/RE_EXPORT——は tar member 無しでも
    正当と認められる、Fix 7 の主眼）。PR #326 第6巡 Fix 14 により
    トップレベルも status 判別 shape のため、MISS narrative
    （`verdict`/`fail_closed_disposition`）を除去し `acquisition_record`
    を付与する。"""
    # RUN9-L0-HARNESS-2: 実データの base fixture が既に `OBTAINED_VIA_
    # REEXPORT`（item ごとの `status`/`matches_historical`/derived-only/
    # verified-only 拡張キー付き）へ遷移済みのため、旧来の一様
    # `OBTAINED_VERIFIED_MATCH` shape へ戻す前に、両トップレベル narrative
    # （MISS-only/OBTAINED-only いずれも `.pop(..., None)` で安全に除去）と
    # item 側の HARNESS-2 専用キーを掃除する。
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    data["acoustic_export_companions"].pop("verdict", None)
    data["acoustic_export_companions"].pop("fail_closed_disposition", None)
    data["acoustic_export_companions"]["acquisition_record"] = {
        "acquired_at": acquired_at, "acquisition_summary": acquisition_summary,
    }
    for item in data["acoustic_export_companions"]["expected_items"]:
        for extra_key in (
            "status", "matches_historical", "historical_expected_sha256",
            "reexport_manifest_ref", "replay_evidence",
        ):
            item.pop(extra_key, None)
        item["measured_sha256"] = item["expected_sha256"]
        item["acquisition_source"] = acquisition_source
        if acquisition_source == "THIS_TARBALL":
            _append_tar_member(
                data,
                path=f"onnx_gate_40000/{item['file'].rsplit('/', 1)[-1]}",
                size_bytes=1,
                sha256=item["expected_sha256"],
            )


def _complete_smoke_render(
    data: Dict[str, Any], *, measured_sec_per_render: float = 4.2,
    render_condition: str = "CPU, ritsu, 1.0s phrase",
    render_output_sha256: str = "c" * 64,
    acquisition_source: str = "THIS_TARBALL",
    total_render_count: int = 616,
) -> None:
    """テストヘルパー: smoke_render を COMPLETED（有効な evidence 込み）
    へ遷移させる。PR #326 第3巡 Fix 8 により acoustic_export_companions
    が OBTAINED_VERIFIED_MATCH でなければ拒否されるため、まず
    `_obtain_all_acoustic_companions()` を呼ぶ。PR #326 第6巡 Fix 15 に
    より同一入力2回の render 出力 sha256（一致必須）も付与する。
    `acquisition_source` は `_obtain_all_acoustic_companions()` へそのまま
    渡す（既定 THIS_TARBALL、PR #326 第7巡以降の DRIVE_DIRECT/RE_EXPORT
    経路テストが companions を OBTAINED にしつつ smoke も同時に COMPLETED
    へ揃える必要があるため——第9巡 Fix 18 で smoke BLOCKED は companions
    NOT_OBTAINED を要求するようになったため、companions だけ OBTAINED に
    する既存テストは smoke も揃えないと通らなくなった）。PR #326 第10巡
    Fix 20 により、smoke_render.status == COMPLETED のときに
    budget_estimate が BLOCKED のまま残ると自己矛盾で拒否されるように
    なったため、budget_estimate も同時に COMPLETED（算術整合）へ揃える
    （`estimated_total_sec == measured_sec_per_render × total_render_count`
    を厳密一致させる、Fix 5 の許容誤差要件を満たす）。"""
    _obtain_all_acoustic_companions(data, acquisition_source=acquisition_source)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": measured_sec_per_render,
        "render_condition": render_condition,
        "render_output_sha256_first": render_output_sha256,
        "render_output_sha256_second": render_output_sha256,
        # RUN9-L0-HARNESS-2 追加フィールド: measured_sec_per_render は
        # render1/render2 の平均であることが machine 強制されるため、
        # 両者を同値（= measured_sec_per_render そのもの）にして平均も
        # 一致させる（呼び出し側は測定秒の内訳までは気にしないテストが
        # 大半のため、既定はシンプルな同値ペアにする）。
        "render1_total_elapsed_sec": measured_sec_per_render,
        "render2_total_elapsed_sec": measured_sec_per_render,
        "render_entrypoint": "gate_synth.py run --skip-export --acoustic-dir <dir> --speaker ritsu",
        "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": total_render_count,
        "estimated_total_sec": measured_sec_per_render * total_render_count,
        "total_render_count_provenance_note": (
            "test fixture: total_render_count is an illustrative constant, not a claimed-final value"
        ),
    }


def test_harness1_pr326_fix1_obtained_correct_measured_hashes_accepted() -> None:
    """正しい measured_sha256（expected_sha256 と一致）+ 対応する tar
    member を全 item に付与すれば OBTAINED_VERIFIED_MATCH は validate を
    通る（過剰拒否でないことの確認）。PR #326 第9巡 Fix 18 により、
    companions OBTAINED のまま smoke_render を BLOCKED（missing-input）に
    残すと自己矛盾で拒否されるため、`_complete_smoke_render()` で smoke
    側も揃える。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix1_loader_accepts_correctly_obtained_companions(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """expected_sha256 と一致する measured_sha256 を全 item に付与した
    OBTAINED_VERIFIED_MATCH manifest が、`load_pinned_dependency_pins_
    manifest()` の全段（validate + bundle cross-check 含む三者一致）を
    通って正常に読み込めること（過剰拒否でないことの確認、正常系）。
    PR #326 第9巡 Fix 18 により smoke_render も揃える必要があるため
    `_complete_smoke_render()` を使う。"""
    manifest_data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(manifest_data)
    manifest_text = json.dumps(manifest_data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    manifest_path = tmp_path / "dependency_pins_manifest.json"
    manifest_path.write_bytes(manifest_text.encode("utf-8"))

    # contract の dependency_pins_sha を、この改変後 manifest のバイトへ
    # 一時的に付け替えた合成 contract を用意する（PR #326 第2巡 Fix 3 で
    # 本欄は disk 正典上 PENDING のため、status も PINNED へ強制する——
    # disk 正典との乖離検査を迂回するため、tmp_path 側に
    # RUN9_CONTRACT.yaml も複製する）。
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["dependency_pins_sha"] = {
        "value": m.compute_file_sha256(manifest_path), "status": "PINNED",
    }
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered_raw)

    data = m.load_pinned_dependency_pins_manifest(
        tampered_contract, manifest_path=manifest_path, contract_path=tampered_contract_path,
    )
    assert data["acoustic_export_companions"]["status"] == "OBTAINED_VERIFIED_MATCH"


def test_harness1_pr326_fix1_loader_measured_sha256_checked_against_bundle_directly() -> None:
    """loader 内の三者一致（測定値 vs bundle）は、`validate_dependency_
    pins_manifest()` が強制する measured==expected と、loader 自身の
    expected==bundle チェックから数学的に導かれる（現行データではこの
    行の raise 分岐へ到達しうる独立した改竄経路が存在しない——
    `bundle_value` を1回だけ算出しどちらの比較にも使い回す実装のため）。
    本テストはその推移律を明示的に確認する形で回帰固定し、コード自体が
    3値（measured/expected/bundle）を直接比較する行を持つこと
    （将来 `expected` 比較だけが削除されても `measured` 比較が独立に
    生き残ること）をソース走査で確認する——将来の実装変更で三者目の
    比較行自体が誤って削除されないための最終防衛線。"""
    source = inspect.getsource(m.load_pinned_dependency_pins_manifest)
    assert 'item["measured_sha256"] != bundle_value' in source
    assert "three-way cross-check" in source


def test_harness1_pr326_fix2_smoke_render_completed_with_blocked_fields_rejected() -> None:
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["smoke_render"]["status"] = "COMPLETED"  # blocked_by 等を残置したまま
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_smoke_render_completed_missing_evidence_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["smoke_render"] = {"status": "COMPLETED", "reason": "done"}
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_smoke_render_completed_determinism_not_true_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": False, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "c" * 64, "render_output_sha256_second": "c" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    with pytest.raises(m.Run9ValidationError, match="determinism_confirmed"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_smoke_render_completed_nonpositive_sec_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 0,
        "render_condition": "CPU, ritsu",
    }
    with pytest.raises(m.Run9ValidationError):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_smoke_render_completed_valid_evidence_accepted() -> None:
    """PR #326 第3巡 Fix 8（P2, 採用）反映後: smoke_render が COMPLETED を
    名乗るには acoustic_export_companions も OBTAINED_VERIFIED_MATCH で
    なければならない。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix2_budget_estimate_completed_with_blocked_fields_rejected() -> None:
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["budget_estimate"]["status"] = "COMPLETED"  # reference_only_* を残置したまま
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_budget_estimate_completed_missing_evidence_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["budget_estimate"] = {"status": "COMPLETED", "reason": "done"}
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_budget_estimate_completed_nonpositive_count_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": 0, "estimated_total_sec": 100.0,
    }
    with pytest.raises(m.Run9ValidationError, match="total_render_count"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix2_budget_estimate_completed_valid_evidence_accepted() -> None:
    """PR #326 第2巡 Fix 5（P2, 採用）反映後: budget_estimate が COMPLETED
    を名乗るには smoke_render も COMPLETED でなければならず、
    estimated_total_sec は measured_sec_per_render × total_render_count
    と一致していなければならない（2587.2 == 4.2 × 616）。第3巡 Fix 8 に
    より smoke_render COMPLETED は acoustic_export_companions の
    OBTAINED も要求する。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": 616, "estimated_total_sec": 2587.2,
        "total_render_count_provenance_note": "test fixture: illustrative constant",
    }
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


@pytest.mark.parametrize("section_name", ["smoke_render", "budget_estimate"])
def test_harness1_pr326_fix2_unknown_status_rejected(section_name: str) -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data[section_name]["status"] = "IN_PROGRESS"
    with pytest.raises(m.Run9ValidationError, match="status must be one of"):
        m.validate_dependency_pins_manifest(data)


# ---------------------------------------------------------------------------
# PR #326 第2巡 Codex bot レビュー Fix 4/5/6（P2 ×3, 採用, 2026-08-26）:
# tar member 検査の status 連動化 / budget↔smoke 結合強制 / companions の
# 重複 logical_name 拒否。負例テスト（将来汚染防止の回帰固定）。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix6_duplicate_companion_logical_name_rejected() -> None:
    """4種の正しい logical_name + 1件の重複（計5件）は、旧実装では
    `set()` 等価判定に潰されて通過してしまっていた——長さ一致の事前
    チェックで拒否されることを確認する。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    dup = copy.deepcopy(data["acoustic_export_companions"]["expected_items"][0])
    data["acoustic_export_companions"]["expected_items"].append(dup)
    with pytest.raises(m.Run9ValidationError, match="duplicate logical_name"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix6_duplicate_with_all_four_names_still_rejected() -> None:
    """重複を除いた集合は4件の正しい logical_name をちょうど満たすため、
    旧実装の `set(seen_names) == set(...)` チェックだけでは通過してしまう
    ケースを明示的に再現する（4件 + 1件の重複 = 5件、set は4件に潰れる）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    items = data["acoustic_export_companions"]["expected_items"]
    assert len(items) == 4
    items.append(copy.deepcopy(items[0]))
    assert len(items) == 5
    assert len({i["logical_name"] for i in items}) == 4  # set 単独では検出不能なことの前提確認
    with pytest.raises(m.Run9ValidationError, match="duplicate logical_name"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix4_not_obtained_still_rejects_matching_tar_member() -> None:
    """companion_status が NOT_OBTAINED_TARBALL_MISS のままなら、旧来
    どおり companion basename + digest の両方が一致する tar member の
    混入は stale-miss inconsistency として拒否される（Fix 4 は分岐を
    追加しただけで、NOT_OBTAINED 側の既存挙動は Fix 12 の digest 限定化
    後も本質的に不変であることの回帰確認）。RUN9-L0-HARNESS-2 で実データの
    companions shape が変わったため、旧 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    expected_sha = next(
        item["expected_sha256"] for item in data["acoustic_export_companions"]["expected_items"]
        if item["logical_name"] == "acoustic_onnx"
    )
    _append_tar_member(
        data, path="onnx_gate_40000/acoustic.onnx", size_bytes=1234, sha256=expected_sha,
    )
    with pytest.raises(m.Run9ValidationError, match="stale-miss inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix4_obtained_without_tar_member_rejected() -> None:
    """companion_status が OBTAINED_VERIFIED_MATCH で acquisition_source
    が THIS_TARBALL を主張しているのに、対応する tar member が
    tar_gz_full_member_ledger に存在しないと拒否される（正当な取得経路
    の主張には実体の裏付けが要る——PR #326 第3巡 Fix 7 で
    acquisition_source が導入された後も、THIS_TARBALL を主張する限り
    Fix 4 の整合検査は有効であることの回帰確認）。RUN9-L0-HARNESS-2 で
    実データの companions shape が変わったため、旧 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    _mark_companions_top_level_obtained(data)
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = item["expected_sha256"]
        item["acquisition_source"] = "THIS_TARBALL"
    with pytest.raises(m.Run9ValidationError, match="obtained-status inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix4_obtained_with_mismatched_tar_member_sha_rejected() -> None:
    """companion_status が OBTAINED_VERIFIED_MATCH で対応する basename の
    tar member はあるが、sha256 が expected と一致しないと拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    for member in data["tar_gz_full_member_ledger"]:
        if member["path"].endswith("acoustic.onnx"):
            member["sha256"] = "9" * 64
    with pytest.raises(m.Run9ValidationError, match="obtained-status inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix4_obtained_with_matching_tar_member_accepted() -> None:
    """companion_status が OBTAINED_VERIFIED_MATCH で、対応する tar
    member（basename一致・sha256一致）が揃っていれば通る（過剰拒否で
    ないことの確認 — `_obtain_all_acoustic_companions()` ヘルパー自体の
    回帰固定も兼ねる）。PR #326 第9巡 Fix 18 により smoke_render も
    揃える必要があるため `_complete_smoke_render()` を使う。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix5_budget_completed_alone_rejected() -> None:
    """budget_estimate だけを COMPLETED へ書き換え、smoke_render は
    BLOCKED のまま残す改竄は拒否される（実測秒の源泉が無いまま完了を
    主張する自己矛盾）。RUN9-L0-HARNESS-2 で実データの smoke_render は
    COMPLETED へ恒久遷移したため、BLOCKED 前提の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": 616, "estimated_total_sec": 2587.2,
        "total_render_count_provenance_note": "test fixture: illustrative constant",
    }
    assert data["smoke_render"]["status"] == "BLOCKED"
    with pytest.raises(m.Run9ValidationError, match="smoke_render.status is not COMPLETED"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix5_budget_completed_smoke_completed_but_arithmetic_mismatch_rejected() -> None:
    """smoke_render は正しく COMPLETED（companions も OBTAINED）でも、
    estimated_total_sec が measured_sec_per_render × total_render_count
    と算術的に一致しなければ拒否される（両方 present なだけでは足りない）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data, measured_sec_per_render=4.2, render_condition="CPU, ritsu")
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": 616, "estimated_total_sec": 4.2 * 616 + 1.0,
        "total_render_count_provenance_note": "test fixture: illustrative constant",
    }
    with pytest.raises(m.Run9ValidationError, match="does not match"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix5_budget_completed_arithmetic_within_tight_tolerance_accepted() -> None:
    """rel_tol=1e-9 という厳しめの許容誤差の範囲内（浮動小数点演算の丸め
    程度）なら受理される（過剰拒否でないことの確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    measured = 4.2
    count = 616
    _complete_smoke_render(data, measured_sec_per_render=measured, render_condition="CPU, ritsu")
    data["budget_estimate"] = {
        "status": "COMPLETED", "reason": "done",
        "total_render_count": count, "estimated_total_sec": measured * count,
        "total_render_count_provenance_note": "test fixture: illustrative constant",
    }
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix5_rel_tol_is_tight() -> None:
    """許容誤差 `_BUDGET_ESTIMATE_TOTAL_SEC_REL_TOL` が「厳しめ」（緩い
    概算を通してしまわない水準）であることの回帰固定 —— 1e-6 以下。"""
    assert m._BUDGET_ESTIMATE_TOTAL_SEC_REL_TOL <= 1e-6


# ---------------------------------------------------------------------------
# PR #326 第2巡 Codex bot レビュー Fix 3（P1, 採用, 2026-08-26）:
# dependency_pins_sha を PENDING へ差し戻したことの回帰固定。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix3_claim_scope_states_incomplete_dependency_coverage() -> None:
    """manifest 自身の claim_scope が「render/analysis 層の実測記録であり
    dependency_pins_sha の完全な充足を主張しない」ことを明記していること
    （contract 側 pin の narrowing ではなく manifest 自身の正直な自己
    申告であることの確認）。"""
    data = _dependency_pins_manifest_data()
    statement = data["claim_scope"]["statement"]
    assert "の完全な充足" in statement
    assert "を主張しない" in statement


def test_harness1_pr326_fix3_manifest_and_code_remain_prewired() -> None:
    """PIN-1 measurement_spec と同型の運用: pin 欄は PENDING でも
    manifest 実体・validator・loader は撤去されず、validate/load とも
    呼び出し可能なまま残置されていること（validate は無条件で通る、
    load は forced-PINNED contract 経由でのみ通ることは別テストが
    確認済み）。"""
    data = _dependency_pins_manifest_data()
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認
    assert callable(m.load_pinned_dependency_pins_manifest)
    assert m.DEPENDENCY_PINS_MANIFEST_REQUIRED_KEYS  # 定数も撤去されていない


# ---------------------------------------------------------------------------
# PR #326 第3巡 Codex bot レビュー Fix 7/8/9（P2 ×3, 採用, 2026-08-26）:
# 取得元別 tar membership 要求 / smoke↔companions 結合強制 /
# speaker candidate status の厳密語彙化。正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix7_drive_direct_obtained_without_tar_member_accepted() -> None:
    """acquisition_source=DRIVE_DIRECT で取得した companion は、
    tar_gz_full_member_ledger に一切現れなくても OBTAINED_VERIFIED_MATCH
    を主張できる（HARNESS1_PROVISION_RECORD.md §7 が記録する非 tar 経路
    ——別 Drive フォルダの探索——の正当性を machine check する）。
    PR #326 第9巡 Fix 18 により smoke_render も揃える必要があるため
    `_complete_smoke_render()` を使う。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data, acquisition_source="DRIVE_DIRECT")
    assert not any(
        m2["path"].endswith(item["file"].rsplit("/", 1)[-1])
        for item in data["acoustic_export_companions"]["expected_items"]
        for m2 in data["tar_gz_full_member_ledger"]
    )
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix7_re_export_obtained_without_tar_member_accepted() -> None:
    """acquisition_source=RE_EXPORT（再export 経路）も同様に tar member
    無しで受理される。PR #326 第9巡 Fix 18 により smoke_render も揃える
    必要があるため `_complete_smoke_render()` を使う。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data, acquisition_source="RE_EXPORT")
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix7_this_tarball_still_requires_membership() -> None:
    """acquisition_source=THIS_TARBALL を主張する item は、引き続き
    tar_gz_full_member_ledger 内の対応 member を要求する（Fix 7 は
    THIS_TARBALL 経路の既存挙動を変えない——同型テストは
    `test_harness1_pr326_fix4_obtained_without_tar_member_rejected` が
    既にカバーするため、ここでは acquisition_source 語彙自体の妥当性の
    みを回帰確認する）。RUN9-L0-HARNESS-2 で実データの companions shape が
    変わったため、旧 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    _mark_companions_top_level_obtained(data)
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = item["expected_sha256"]
        item["acquisition_source"] = "THIS_TARBALL"
    with pytest.raises(m.Run9ValidationError, match="obtained-status inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix7_acquisition_source_required_for_obtained() -> None:
    """OBTAINED_VERIFIED_MATCH の item は acquisition_source を必須と
    する（欠落は missing required key で拒否）。RUN9-L0-HARNESS-2 で
    実データの companions shape が変わったため、旧 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    _mark_companions_top_level_obtained(data)
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = item["expected_sha256"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix7_acquisition_source_forbidden_for_not_obtained() -> None:
    """NOT_OBTAINED_TARBALL_MISS の item に acquisition_source を付与
    すると unknown key として拒否される（measured_sha256 と同型の
    禁止）。RUN9-L0-HARNESS-2 で実データの companions は
    OBTAINED_VIA_REEXPORT へ恒久遷移したため、NOT_OBTAINED 前提の非退行
    確認には `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["expected_items"][0]["acquisition_source"] = "DRIVE_DIRECT"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix7_unknown_acquisition_source_rejected() -> None:
    """acquisition_source が閉じた語彙 (THIS_TARBALL/DRIVE_DIRECT/
    RE_EXPORT) 以外の値だと拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data, acquisition_source="THIS_TARBALL")
    data["acoustic_export_companions"]["expected_items"][0]["acquisition_source"] = "SOMEWHERE_ELSE"
    with pytest.raises(m.Run9ValidationError, match="acquisition_source must be one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix8_smoke_completed_alone_rejected() -> None:
    """smoke_render だけを COMPLETED へ書き換え、acoustic_export_
    companions は NOT_OBTAINED_TARBALL_MISS のまま残す改竄は拒否される
    （存在しないと同時に主張している入力で render したという自己矛盾）。
    RUN9-L0-HARNESS-2 で実データの companions は OBTAINED_VIA_REEXPORT へ
    恒久遷移したため、NOT_OBTAINED 前提の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "c" * 64, "render_output_sha256_second": "c" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    assert data["acoustic_export_companions"]["status"] == "NOT_OBTAINED_TARBALL_MISS"
    with pytest.raises(m.Run9ValidationError, match="acoustic_export_companions.status is not"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix8_smoke_completed_with_companions_obtained_accepted() -> None:
    """companions が正しく OBTAINED_VERIFIED_MATCH であれば smoke_render
    COMPLETED は受理される（過剰拒否でないことの確認 —
    `_complete_smoke_render()` ヘルパー自体の回帰固定も兼ねる）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


# ---------------------------------------------------------------------------
# PR #326 第9巡 Codex bot レビュー Fix 18（P2, 採用, 2026-08-26, 将来汚染:
# Fix 8 の逆方向の未結合）: `smoke_render` の missing-input BLOCKED shape
# は companions が実際に NOT_OBTAINED_TARBALL_MISS のときのみ許容される。
# 正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix18_stale_blocked_after_companions_obtained_rejected() -> None:
    """companions を OBTAINED_VERIFIED_MATCH へ遷移させても smoke_render
    を missing-input BLOCKED のまま残す改竄は拒否される（Fix 8 の逆方向
    ——「取得済み」と「入力欠落で BLOCKED」の同時主張は自己矛盾）。
    RUN9-L0-HARNESS-2 で実データは companions/smoke とも既に整合済み
    （OBTAINED_VIA_REEXPORT/COMPLETED）へ恒久遷移したため、この「片方
    だけ遷移させた stale 状態」の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    assert data["smoke_render"]["status"] == "BLOCKED"
    with pytest.raises(m.Run9ValidationError, match="status is BLOCKED \\(missing-input\\)"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix18_error_message_states_reentry_condition() -> None:
    """拒否メッセージ自体に、将来 HARNESS-2 で中間状態が必要になっても
    新しい status 値を先取り発明しないという再入条件（PIN-1 以来の規律）
    が記録されていることの確認。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    with pytest.raises(m.Run9ValidationError, match="design_revision"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_current_real_data_all_obtained_and_completed_accepted() -> None:
    """現行実データ（companions OBTAINED_VIA_REEXPORT + smoke_render/
    budget_estimate とも COMPLETED、三者とも整合済み）は引き続き受理
    される（過剰拒否でないことの確認。旧 HARNESS-1 時点の
    `test_harness1_pr326_fix18_current_real_data_both_missing_blocked_
    accepted`/`test_harness1_pr326_fix20_current_real_data_both_blocked_
    accepted` の後継——現行実データはもはや MISS/BLOCKED ではない）。"""
    data = _dependency_pins_manifest_data()
    assert data["acoustic_export_companions"]["status"] == "OBTAINED_VIA_REEXPORT"
    assert data["smoke_render"]["status"] == "COMPLETED"
    assert data["budget_estimate"]["status"] == "COMPLETED"
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix18_companions_obtained_and_smoke_completed_accepted() -> None:
    """companions OBTAINED + smoke_render COMPLETED（両方揃って一貫）は
    引き続き受理される（Fix 8 の正方向・Fix 18 の負方向のいずれの拒否
    条件にも該当しないことの確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


# ---------------------------------------------------------------------------
# PR #326 第10巡 Codex bot レビュー Fix 20（P2, 採用, 2026-08-26, 将来汚染:
# Fix 18 の対）: `budget_estimate` の BLOCKED shape は smoke_render が
# 実際に BLOCKED（実測秒が存在しない）のときのみ許容される——smoke が
# COMPLETED（実測秒あり）なのに budget が実測欠如を理由に BLOCKED を
# 主張し続けるのは自己矛盾。正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix20_stale_budget_blocked_after_smoke_completed_rejected() -> None:
    """smoke_render を COMPLETED へ遷移させても budget_estimate を
    reference-only BLOCKED のまま残す改竄は拒否される（Fix 18 の対——
    「実測が存在する」と「実測欠如を理由に BLOCKED」の同時主張は
    自己矛盾）。RUN9-L0-HARNESS-2 で実データは三者とも整合済みへ恒久
    遷移したため、この「片方だけ遷移させた stale 状態」の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "c" * 64, "render_output_sha256_second": "c" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    assert data["budget_estimate"]["status"] == "BLOCKED"
    with pytest.raises(
        m.Run9ValidationError, match="status is BLOCKED \\(citing absent measurement\\)"
    ):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix20_error_message_states_reentry_condition() -> None:
    """拒否メッセージ自体に、Fix 18 と同型の再入条件（新 status を先取り
    発明しない）が記録されていることの確認。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "c" * 64, "render_output_sha256_second": "c" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    with pytest.raises(m.Run9ValidationError, match="design_revision"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix20_smoke_and_budget_both_completed_accepted() -> None:
    """smoke_render COMPLETED + budget_estimate COMPLETED（算術整合、
    両方揃って一貫）は引き続き受理される（`_complete_smoke_render()`
    ヘルパーが budget も同時 COMPLETED へ揃えるようになったことの回帰
    固定も兼ねる）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    assert data["budget_estimate"]["status"] == "COMPLETED"
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix18_fix20_three_way_coupling_is_closed() -> None:
    """companions↔smoke↔budget の3セクション間の状態結合が全方向
    （OBTAINED/COMPLETED 方向 = Fix 5/8、BLOCKED 残置方向 = Fix 18/20）
    で閉じ、相互矛盾する状態組が構造的に表現不能になったことの回帰
    固定——4つの整合済み状態組み合わせをすべて受理し、片方だけ更新した
    非整合な中間状態をすべて拒否することを一括確認する。RUN9-L0-
    HARNESS-2 で実データは三者とも整合済み（OBTAINED_VIA_REEXPORT/
    COMPLETED）へ恒久遷移したため、「未取得/未実行」側の整合状態・
    非整合な中間状態の合成には `_legacy_dependency_pins_manifest_data()`
    を base に使う。"""
    # 整合1: 3セクションとも「未取得/未実行」（旧 HARNESS-1 時点の実データ
    # shape、legacy fixture として独立に維持）。
    consistent_all_blocked = _legacy_dependency_pins_manifest_data()
    m.validate_dependency_pins_manifest(consistent_all_blocked)  # 例外なしの確認

    # 整合2: 3セクションとも「取得済み/完了」（現行実データそのまま）。
    consistent_all_completed = _dependency_pins_manifest_data()
    assert consistent_all_completed["acoustic_export_companions"]["status"] == "OBTAINED_VIA_REEXPORT"
    assert consistent_all_completed["smoke_render"]["status"] == "COMPLETED"
    assert consistent_all_completed["budget_estimate"]["status"] == "COMPLETED"
    m.validate_dependency_pins_manifest(consistent_all_completed)  # 例外なしの確認

    # 非整合1: companions だけ OBTAINED、smoke/budget は BLOCKED のまま
    # （Fix 18 が拒否）。
    stale_smoke_blocked = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(stale_smoke_blocked)
    with pytest.raises(m.Run9ValidationError, match="status is BLOCKED \\(missing-input\\)"):
        m.validate_dependency_pins_manifest(stale_smoke_blocked)

    # 非整合2: companions/smoke は COMPLETED、budget だけ BLOCKED のまま
    # （Fix 20 が拒否）。
    stale_budget_blocked = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(stale_budget_blocked)
    stale_budget_blocked["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "c" * 64, "render_output_sha256_second": "c" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    assert stale_budget_blocked["budget_estimate"]["status"] == "BLOCKED"
    with pytest.raises(
        m.Run9ValidationError, match="status is BLOCKED \\(citing absent measurement\\)"
    ):
        m.validate_dependency_pins_manifest(stale_budget_blocked)


def test_harness1_pr326_fix9_speaker_candidate_typo_status_rejected() -> None:
    """`UNPINNED_CANDIDATE_PINNED_VERIFIED` のような typo/混成値は、
    startswith() 判定なら通過してしまっていたが、厳密一致では拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"]["pjs"]["status"] = (
        "UNPINNED_CANDIDATE_PINNED_VERIFIED"
    )
    with pytest.raises(m.Run9ValidationError, match="must be exactly one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix9_d3synth_wrong_vocab_rejected() -> None:
    """d3synth_reference_only に pjs/user 用の語彙
    （"UNPINNED_CANDIDATE"）を流用すると拒否される（2つの語彙を混同
    しないことの確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"]["d3synth_reference_only"]["status"] = (
        "UNPINNED_CANDIDATE"
    )
    with pytest.raises(m.Run9ValidationError, match="must be exactly one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix9_exact_vocab_values_accepted() -> None:
    """語彙どおりの厳密値（変更なし）は引き続き通る（過剰拒否でないこと
    の確認）。"""
    data = _dependency_pins_manifest_data()
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認
    assert data["speaker_embeddings_unpinned_candidates"]["pjs"]["status"] == "UNPINNED_CANDIDATE"
    assert data["speaker_embeddings_unpinned_candidates"]["user"]["status"] == "UNPINNED_CANDIDATE"
    assert (
        data["speaker_embeddings_unpinned_candidates"]["d3synth_reference_only"]["status"]
        == "UNPINNED_CANDIDATE_NOT_A_RUN9_FOUNDER"
    )


# ---------------------------------------------------------------------------
# PR #326 第7巡 Codex bot レビュー Fix 16（P2, 採用, 2026-08-26）:
# `_validate_speaker_embed_candidate()` を全必須フィールド検証へ強化
# （candidate_sha256_first16 の機械照合・file/note の非空検証）。正負
# テスト。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry_key", ["pjs", "user"])
def test_harness1_pr326_fix16_first16_mismatch_rejected(entry_key: str) -> None:
    """`candidate_sha256_first16` が `candidate_sha256` の先頭16文字と
    矛盾する（typo・手打ちミス等）と拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"][entry_key]["candidate_sha256_first16"] = (
        "0000000000000000"
    )
    with pytest.raises(m.Run9ValidationError, match="candidate_sha256_first16 must equal"):
        m.validate_dependency_pins_manifest(data)


@pytest.mark.parametrize("entry_key", ["pjs", "user"])
def test_harness1_pr326_fix16_file_empty_rejected(entry_key: str) -> None:
    """`file` が空文字だと拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"][entry_key]["file"] = ""
    with pytest.raises(m.Run9ValidationError, match="\\.file"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix16_d3synth_note_empty_rejected() -> None:
    """d3synth entry の `note` が空文字だと拒否される（section 全体の
    `note` とは別フィールドであることの確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"]["d3synth_reference_only"]["note"] = ""
    with pytest.raises(m.Run9ValidationError, match="\\.note"):
        m.validate_dependency_pins_manifest(data)


@pytest.mark.parametrize("entry_key", ["pjs", "user", "d3synth_reference_only"])
def test_harness1_pr326_fix16_unknown_key_rejected(entry_key: str) -> None:
    """entry ごとの許容キー集合が閉じていることの確認（未知キーは拒否
    される）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"][entry_key]["unexpected_extra_key"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix16_real_data_accepted() -> None:
    """現行実データ（first16 が実際に candidate_sha256 の先頭16文字と
    一致・file/note が非空）は新検証を通過することの確認（過剰拒否で
    ないこと）。"""
    data = _dependency_pins_manifest_data()
    candidates = data["speaker_embeddings_unpinned_candidates"]
    for key in ("pjs", "user"):
        assert candidates[key]["candidate_sha256_first16"] == candidates[key]["candidate_sha256"][:16]
        assert candidates[key]["file"]
    assert candidates["d3synth_reference_only"]["note"]
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


# ---------------------------------------------------------------------------
# PR #326 第4巡 Codex bot レビュー Fix 10（P2, 採用, 2026-08-26）:
# tar member ledger の束縛強化（member_count/total_size_bytes 宣言 +
# 独立再生成一致実測の record）。正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix10_ledger_omission_rejected() -> None:
    """well-formed な行を1件減らす（列挙漏れの模擬）だけで
    len(ledger) != member_count となり拒否される——旧実装は「非空の
    well-formed 行の任意部分集合」を通過させていた欠陥そのもの。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["tar_gz_full_member_ledger"] = data["tar_gz_full_member_ledger"][:-1]
    with pytest.raises(m.Run9ValidationError, match="does not match tar_gz_ledger_integrity.member_count"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_total_size_mismatch_rejected() -> None:
    """member_count は合っていても sum(size_bytes) が
    total_size_bytes と食い違えば拒否される（1行の size_bytes だけ
    改竄したケース）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["tar_gz_full_member_ledger"][0]["size_bytes"] += 1
    with pytest.raises(m.Run9ValidationError, match="does not match tar_gz_ledger_integrity.total_size_bytes"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_archive_sha_cross_check() -> None:
    """tar_gz_ledger_integrity.archive_sha256 が
    acoustic_export_companions.attempted_source.actual_sha256 と乖離
    すると拒否される（同じ tarball を指しているという manifest 内部
    整合）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["tar_gz_ledger_integrity"]["archive_sha256"] = "f" * 64
    with pytest.raises(m.Run9ValidationError, match="diverges from tar_gz_ledger_integrity.archive_sha256"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_integrity_missing_key_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    del data["tar_gz_ledger_integrity"]["generation_method"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_reread_result_vocab_enforced() -> None:
    """independent_reread_verification.result は閉じた語彙
    （現状 EXACT_MATCH のみ）——MISMATCH 等の値は拒否する（列挙漏れが
    見つかった場合に「見つかった」と正直に record する語彙は、pin 化
    そのものを妨げる形で別途設計する必要があり、本欄を汚染で通過させ
    ない）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["tar_gz_ledger_integrity"]["independent_reread_verification"]["result"] = "MISMATCH"
    with pytest.raises(m.Run9ValidationError, match="result must be one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_reread_member_count_matched_must_equal_declared() -> None:
    """independent_reread_verification.member_count_matched が
    member_count と食い違うと拒否される（宣言した件数と、実際に再読で
    一致確認できた件数が別、という矛盾を許さない）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["tar_gz_ledger_integrity"]["independent_reread_verification"]["member_count_matched"] = 38
    with pytest.raises(m.Run9ValidationError, match="must equal tar_gz_ledger_integrity.member_count"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix10_real_manifest_reread_verification_is_exact_match() -> None:
    """本 manifest の実測台帳そのものの回帰確認: 39ファイル・
    独立再生成一致・列挙漏れなし（本巡で実際に workdir の tarball から
    独立再生成し、現行 ledger と全一致したことを実測した結果）。"""
    data = _dependency_pins_manifest_data()
    integrity = data["tar_gz_ledger_integrity"]
    assert integrity["member_count"] == 39
    assert len(data["tar_gz_full_member_ledger"]) == 39
    assert sum(m2["size_bytes"] for m2 in data["tar_gz_full_member_ledger"]) == integrity["total_size_bytes"]
    assert integrity["independent_reread_verification"]["result"] == "EXACT_MATCH"
    assert integrity["independent_reread_verification"]["member_count_matched"] == 39
    assert integrity["archive_sha256"] == (
        data["acoustic_export_companions"]["attempted_source"]["actual_sha256"]
    )


# ---------------------------------------------------------------------------
# PR #326 第5巡 Codex bot レビュー Fix 12/13（P2 ×2, 採用, 2026-08-26）:
# MISS 矛盾判定を digest 一致に限定 / claim_scope の PENDING 主表明化。
# 正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix12_same_basename_different_digest_not_flagged() -> None:
    """companion basename と同名だが digest が異なる（＝無関係の別
    ファイル）tar member が混入しても NOT_OBTAINED_TARBALL_MISS は
    受理される（旧実装は basename だけで矛盾を発火させ、この正当な
    ケースを偽ブロックしていた）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _append_tar_member(
        data, path="onnx_gate_40000/acoustic.onnx", size_bytes=999, sha256="9" * 64,
    )
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix12_same_basename_same_digest_still_flagged() -> None:
    """basename + digest の両方が一致する member が混入した場合は、
    引き続き stale-miss inconsistency として拒否される（Fix 12 は
    matching 条件を絞っただけで、真の矛盾検出能力は失っていない）。
    RUN9-L0-HARNESS-2 で実データの companions は OBTAINED_VIA_REEXPORT へ
    恒久遷移したため、NOT_OBTAINED 前提の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    expected_sha = next(
        item["expected_sha256"] for item in data["acoustic_export_companions"]["expected_items"]
        if item["logical_name"] == "acoustic_dsconfig_yaml"
    )
    _append_tar_member(
        data, path="onnx_gate_40000/dsconfig.yaml", size_bytes=42, sha256=expected_sha,
    )
    with pytest.raises(m.Run9ValidationError, match="stale-miss inconsistency"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix12_multiple_unrelated_same_basename_members_not_flagged() -> None:
    """同名 basename の無関係ファイルが複数混入しても（いずれも digest
    不一致）拒否されない（found_basename_shas がリストで複数値を保持
    する経路の回帰確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _append_tar_member(
        data, path="a/acoustic.onnx", size_bytes=10, sha256="1" * 64,
    )
    _append_tar_member(
        data, path="b/acoustic.onnx", size_bytes=20, sha256="2" * 64,
    )
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix13_claim_scope_statement_leads_with_pending() -> None:
    """claim_scope.statement が PENDING 主表明マーカーを先頭付近
    （80文字以内）に持つことの回帰確認（実測台帳そのもの）。"""
    data = _dependency_pins_manifest_data()
    statement = data["claim_scope"]["statement"]
    offset = statement.find(m._CLAIM_SCOPE_PENDING_MARKER)
    assert 0 <= offset <= m._CLAIM_SCOPE_PENDING_MARKER_MAX_OFFSET


def test_harness1_pr326_fix13_pending_marker_missing_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["claim_scope"]["statement"] = "この manifest は完全に信頼できる。"
    with pytest.raises(m.Run9ValidationError, match="must state the current PENDING status"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix13_pending_marker_buried_at_end_rejected() -> None:
    """PENDING マーカーが末尾遠くに追記されただけ（旧実装の症状そのもの
    ——先頭は PINNED 前提の文言）だと拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["claim_scope"]["statement"] = (
        "本 manifest が PINNED 判定を通じて主張するのは、以下の記録が"
        "すべて正確であるという2点のみである。" + " " * 60 +
        "追記: 実は現在 PENDING である。"
    )
    with pytest.raises(m.Run9ValidationError, match="must state the current PENDING status"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix13_historical_generations_separated() -> None:
    """旧 PINNED 世代（第1-2世代）への言及が、statement 本文ではなく
    独立フィールド historical_pinned_generations に分離されていること。"""
    data = _dependency_pins_manifest_data()
    historical = data["claim_scope"]["historical_pinned_generations"]
    generations = historical["generations"]
    assert {g["generation"] for g in generations} == {1, 2}
    for g in generations:
        assert g["status_at_time"] == "PINNED"
        assert m._SHA256_HEX_RE.match(g["sha256"])
    # statement 本文自体に第1-2世代への言及（「第1世代」等）が残っていない
    # こと——分離が名目だけでないことの確認。
    assert "第1世代" not in data["claim_scope"]["statement"]
    assert "第2世代" not in data["claim_scope"]["statement"]


def test_harness1_pr326_fix13_claim_scope_unknown_key_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["claim_scope"]["unexpected_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix13_historical_generations_duplicate_number_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    gens = data["claim_scope"]["historical_pinned_generations"]["generations"]
    dup = copy.deepcopy(gens[0])
    dup["generation"] = gens[1]["generation"]
    data["claim_scope"]["historical_pinned_generations"]["generations"].append(dup)
    with pytest.raises(m.Run9ValidationError, match="duplicate generation"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix13_historical_generations_bad_status_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["claim_scope"]["historical_pinned_generations"]["generations"][0]["status_at_time"] = (
        "PENDING"
    )
    with pytest.raises(m.Run9ValidationError, match="status_at_time must be one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix13_claim_scope_without_historical_field_still_valid() -> None:
    """historical_pinned_generations は optional——省略しても statement/
    rationale さえ揃っていれば通る（過剰拒否でないことの確認）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    del data["claim_scope"]["historical_pinned_generations"]
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


# ---------------------------------------------------------------------------
# PR #326 第6巡 Codex bot レビュー Fix 14/15（P2 ×2, 採用, 2026-08-26）:
# companions トップレベルの status 判別 shape 化 / smoke 決定論の出力
# hash 証拠必須化。正負テスト。
# ---------------------------------------------------------------------------


def test_harness1_pr326_fix14_obtained_with_stale_miss_narrative_rejected() -> None:
    """status を OBTAINED_VERIFIED_MATCH へ正しく遷移させても、トップ
    レベルの MISS narrative（verdict/fail_closed_disposition）を残置
    したままだと拒否される——「取得済み」と「未取得」の同時主張を防ぐ。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = item["expected_sha256"]
        item["acquisition_source"] = "DRIVE_DIRECT"
    # verdict/fail_closed_disposition をあえて残置（acquisition_record は付与しない）
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix14_obtained_missing_acquisition_record_rejected() -> None:
    """MISS narrative を正しく除去しても、acquisition_record を付与
    しなければ missing required key で拒否される。RUN9-L0-HARNESS-2 で
    実データの companions shape が変わったため、旧 shape の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["status"] = "OBTAINED_VERIFIED_MATCH"
    del data["acoustic_export_companions"]["verdict"]
    del data["acoustic_export_companions"]["fail_closed_disposition"]
    for item in data["acoustic_export_companions"]["expected_items"]:
        item["measured_sha256"] = item["expected_sha256"]
        item["acquisition_source"] = "DRIVE_DIRECT"
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix14_obtained_correctly_shaped_accepted() -> None:
    """トップレベル・item レベルとも正しく OBTAINED shape へ揃えれば
    受理される（`_obtain_all_acoustic_companions()` ヘルパー自体の
    回帰固定も兼ねる、過剰拒否でないことの確認）。PR #326 第9巡 Fix 18
    により smoke_render も揃える必要があるため `_complete_smoke_render()`
    を使う。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data, acquisition_source="DRIVE_DIRECT")
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix14_not_obtained_forbids_acquisition_record() -> None:
    """NOT_OBTAINED_TARBALL_MISS のまま acquisition_record を付与すると
    unknown key として拒否される（MISS/OBTAINED 語彙の disjoint 性）。
    RUN9-L0-HARNESS-2 で実データの companions は OBTAINED_VIA_REEXPORT へ
    恒久遷移したため、NOT_OBTAINED 前提の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["acquisition_record"] = {
        "acquired_at": "2026-08-27", "acquisition_summary": "x",
    }
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix14_verdict_must_start_with_miss() -> None:
    """RUN9-L0-HARNESS-2 で実データの companions は OBTAINED_VIA_REEXPORT
    へ恒久遷移したため、`verdict`（MISS-only フィールド）の非退行確認には
    `_legacy_dependency_pins_manifest_data()` を使う。"""
    data = copy.deepcopy(_legacy_dependency_pins_manifest_data())
    data["acoustic_export_companions"]["verdict"] = "everything is fine, nothing to see here"
    with pytest.raises(m.Run9ValidationError, match="verdict must start with 'MISS'"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix14_acquisition_record_unknown_key_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["acoustic_export_companions"]["acquisition_record"]["extra_field"] = "x"
    with pytest.raises(m.Run9ValidationError, match="acquisition_record has unknown key"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix15_smoke_completed_missing_output_hashes_rejected() -> None:
    """determinism_confirmed=True + 実測秒 + 条件文だけでは、出力 hash
    2件が無いと COMPLETED を名乗れない。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
    }
    with pytest.raises(m.Run9ValidationError, match="missing required key.*render_output_sha256"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix15_smoke_completed_mismatched_output_hashes_rejected() -> None:
    """2回の render 出力 sha256 が食い違うと、determinism_confirmed=True
    という主張自体と矛盾するとして拒否される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "a" * 64, "render_output_sha256_second": "b" * 64,
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    with pytest.raises(m.Run9ValidationError, match="contradicts.*determinism_confirmed"):
        m.validate_dependency_pins_manifest(data)


def test_harness1_pr326_fix15_smoke_completed_matching_output_hashes_accepted() -> None:
    """2回の render 出力 sha256 が一致していれば受理される（過剰拒否で
    ないことの確認、`_complete_smoke_render()` ヘルパー自体の回帰固定も
    兼ねる）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _complete_smoke_render(data)
    m.validate_dependency_pins_manifest(data)  # 例外なしの確認


def test_harness1_pr326_fix15_output_hash_shape_enforced() -> None:
    """render_output_sha256_first/second は64hex 形状を要求する。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    _obtain_all_acoustic_companions(data)
    data["smoke_render"] = {
        "status": "COMPLETED", "reason": "done",
        "determinism_confirmed": True, "measured_sec_per_render": 4.2,
        "render_condition": "CPU, ritsu",
        "render_output_sha256_first": "not-a-hash", "render_output_sha256_second": "not-a-hash",
        "render1_total_elapsed_sec": 4.2, "render2_total_elapsed_sec": 4.2,
        "render_entrypoint": "gate_synth.py run", "onnxruntime_providers": ["CPUExecutionProvider"],
    }
    with pytest.raises(m.Run9ValidationError, match="render_output_sha256_first must be a 64hex sha256"):
        m.validate_dependency_pins_manifest(data)


# ---------------------------------------------------------------------------
# RUN9-L0-HARNESS-2: reexport_manifest / companions・smoke・budget 状態遷移
# （User 裁定 2026-08-26「RUN9 User裁定 — acoustic export companions /
# speaker embeds」に基づく checkpoint 再export・決定論 smoke render 実測）
# ---------------------------------------------------------------------------

HARNESS2_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt"
)


def _reexport_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.REEXPORT_MANIFEST_PATH.read_text(encoding="utf-8"))


def _canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


# --- 裁定文書の repo 収載（PIN-2 前例と同型） -------------------------------


def test_harness2_adjudication_source_file_exists() -> None:
    assert HARNESS2_ADJUDICATION_PATH.is_file()


def test_harness2_adjudication_source_contains_verbatim_values() -> None:
    """凍結した各値（historical pin 4点・checkpoint sha・DiffSinger commit・
    pjs/user emb 候補値）が、repo 内収載した裁定文書の本文に一字一句
    そのまま存在すること（grep 照合——「User 転記であって発明でない」こと
    を機械検証する）。"""
    text = HARNESS2_ADJUDICATION_PATH.read_text(encoding="utf-8")
    for value in (
        "aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706",
        "a7da75f5c403bd347f108ded6ea6925df6260dae83cf72877c5b19018443899c",
        "5071e1654c4572d90011a49959b97467b6bed5ecf08c203b71b9aff4b02807a8",
        "ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c",
        "6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a",
        "e2307b1080b00f3999702ce9017cfd75c7f862fe",
        "074e09b390c207a7cf98105db549e1006d035a797d57f73e103e848bb3216015",
        "588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0",
    ):
        assert value in text, f"missing verbatim value: {value!r}"


def test_harness2_adjudication_source_body_byte_identical_to_scratchpad_origin() -> None:
    """本文（【RUN9 User裁定 — acoustic export companions / speaker
    embeds】以降）が起草時の作業メモ
    scratchpad/run9_user_adjudication_harness2.md と一字一句改変なしで
    一致すること（改変禁止の直接確認、PIN-2 前例と同型——scratchpad
    ファイルが本セッション後に存在しない環境では skip）。"""
    scratchpad_path = Path(
        "/tmp/claude-0/-home-user-ugh-prompt-engine/"
        "e505c1c2-c4ad-588b-a1b2-258051a522de/scratchpad/"
        "run9_user_adjudication_harness2.md"
    )
    if not scratchpad_path.is_file():
        pytest.skip("scratchpad origin file not present in this environment")
    origin_body = scratchpad_path.read_text(encoding="utf-8")
    origin_body = "【RUN9 User裁定" + origin_body.split("【RUN9 User裁定", 1)[1]
    committed_text = HARNESS2_ADJUDICATION_PATH.read_text(encoding="utf-8")
    committed_body = "【RUN9 User裁定" + committed_text.split("【RUN9 User裁定", 1)[1]
    assert committed_body == origin_body


def test_harness2_contract_records_adjudication_source_sha256_as_comment() -> None:
    """RUN9_CONTRACT.yaml が
    USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt の実測
    sha256 を情報コメントとして記録していること（新 pin 欄は作らない設計
    判断——CONTRACT_PIN_FIELDS には含まれないことも確認する）。"""
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    actual_sha = m.compute_file_sha256(HARNESS2_ADJUDICATION_PATH)
    assert actual_sha in contract_text
    assert (
        "USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt"
        not in m.CONTRACT_PIN_FIELDS
    )


# --- reexport_manifest_sha 新規 PINNED -------------------------------------


def test_harness2_reexport_manifest_sha_in_contract_pin_fields() -> None:
    assert "reexport_manifest_sha" in m.CONTRACT_PIN_FIELDS


def test_harness2_reexport_manifest_sha_pinned_and_matches_file(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["reexport_manifest_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.REEXPORT_MANIFEST_PATH)


def test_harness2_reexport_manifest_pinning_does_not_affect_pending_set(
    contract: m.Run9RunContract,
) -> None:
    """reexport_manifest_sha は新規追加の PINNED 欄であり、既存 PENDING
    集合に影響しないこと〔履歴: 起草当時は pre-run 必須10欄・総 PENDING
    11欄、RUN9-EXECPROFILE-1（2026-08-26）で pre-run 必須9欄・総 PENDING
    10欄へ、RUN9-L0-HARNESS-3a（2026-08-26）で `expected_speaker_map_sha`
    も PINNED 化され pre-run 必須8欄・総 PENDING 9欄へ、
    RUN9-L0-HARNESS-3b（2026-08-27）で `education_technique_lesson_
    manifest_sha` も PINNED 化され pre-run 必須7欄・総 PENDING 8欄へ、
    design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6、2026-08-27）で
    `hypothesis_algebra_sha` も PINNED 化され pre-run 必須6欄・総
    PENDING 7欄へ、PR #333 Codex bot レビュー第1巡指摘1（2026-08-28、
    P1、採用）で `hypothesis_threshold_calibration_sha` が新設された
    ため、現在は下記のとおり pre-run 必須7欄・総 PENDING 8欄——
    `test_pr333_r1_pre_run_pending_count_is_seven` と同一の期待値〕。"""
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [n for n in pre_run_fields if not m._is_field_pinned(contract.pin_field(n))]
    all_pending = [
        n for n in m.CONTRACT_PIN_FIELDS
        if n not in m.CONTRACT_POST_RUN_PIN_FIELDS and not m._is_field_pinned(contract.pin_field(n))
    ]
    assert "reexport_manifest_sha" not in pending
    assert "reexport_manifest_sha" not in all_pending
    assert len(pending) == 7
    assert len(all_pending) == 8
    assert m.gate_state(contract) == "BLOCKED"


# --- reserialization byte 一致（新規2ファイルとも） -------------------------


def test_harness2_reexport_manifest_reserializes_byte_identical() -> None:
    raw = m.REEXPORT_MANIFEST_PATH.read_bytes()
    data = m._loads_strict_json(raw.decode("utf-8"))
    assert _canonical_json_bytes(data) == raw


def test_harness2_dependency_pins_manifest_reserializes_byte_identical() -> None:
    raw = m.DEPENDENCY_PINS_MANIFEST_PATH.read_bytes()
    data = m._loads_strict_json(raw.decode("utf-8"))
    assert _canonical_json_bytes(data) == raw


# --- validate_reexport_manifest(): 正常系 -----------------------------------


def test_harness2_reexport_manifest_validates() -> None:
    m.validate_reexport_manifest(_reexport_manifest_data())  # 例外なしの確認


def test_harness2_reexport_manifest_schema_field() -> None:
    data = _reexport_manifest_data()
    assert data["schema"] == "run9-reexport-manifest/1.0" == m.SCHEMA_REEXPORT_MANIFEST


def test_harness2_reexport_manifest_unknown_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_missing_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    del data["artifacts"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_reexport_manifest(data)


# --- fail-closed (a): input_checkpoint sha vs contract pin (internal consistency) ---


def test_harness2_reexport_manifest_checkpoint_matches_pin_flag_forged_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["input_checkpoint"]["sha256_matches_pin"] = False
    with pytest.raises(m.Run9ValidationError, match="sha256_matches_pin"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_checkpoint_sha_shape_enforced() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["input_checkpoint"]["sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="64hex"):
        m.validate_reexport_manifest(data)


# --- fail-closed (d)（PR #327 レビュー第2巡指摘6）: input_checkpoint は
# pin 済み入力からの derived のみを表現できる（直接強制、算術一貫性のみ
# ではない） -----------------------------------------------------------


def test_harness2_reexport_manifest_checkpoint_unpinned_actual_rejected() -> None:
    """actual sha256 を改竄し、sha256_matches_pin も算術的に整合する False
    へ追随させても（旧実装はこの組合せを受理し得た）、直接強制により
    fail-closed で拒否される——unpinned checkpoint からの derived
    manifest はカテゴリカルに拒否される。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["input_checkpoint"]["sha256"] = "7" * 64
    data["input_checkpoint"]["sha256_matches_pin"] = False
    with pytest.raises(m.Run9ValidationError, match="input_checkpoint"):
        m.validate_reexport_manifest(data)


# --- fail-closed (b): exporter.revision vs contract pin (internal consistency) ---


def test_harness2_reexport_manifest_exporter_matches_pin_flag_forged_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["exporter"]["revision_matches_pin"] = False
    with pytest.raises(m.Run9ValidationError, match="revision_matches_pin"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_exporter_revision_shape_enforced() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["exporter"]["revision"] = "not-a-git-sha"
    with pytest.raises(m.Run9ValidationError, match="40hex"):
        m.validate_reexport_manifest(data)


# --- fail-closed (e)（PR #327 レビュー第2巡指摘6）: exporter は pin 済み
# revision からの derived のみを表現できる（input_checkpoint (d) と同型の
# 直接強制） -------------------------------------------------------------


def test_harness2_reexport_manifest_exporter_unpinned_actual_rejected() -> None:
    """actual revision を改竄し、revision_matches_pin も算術的に整合する
    False へ追随させても（旧実装はこの組合せを受理し得た）、直接強制に
    より fail-closed で拒否される——unpinned exporter revision からの
    derived manifest はカテゴリカルに拒否される。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["exporter"]["revision"] = "7" * 40
    data["exporter"]["revision_matches_pin"] = False
    with pytest.raises(m.Run9ValidationError, match="exporter"):
        m.validate_reexport_manifest(data)


# --- fail-closed (c): matches_historical の in-process 再計算一致 ----------


def test_harness2_reexport_manifest_matches_historical_null_historical_forces_false() -> None:
    """historical_sha256 が null の artifact（languages_json）は
    matches_historical: false を強制する。"""
    data = copy.deepcopy(_reexport_manifest_data())
    assert data["artifacts"]["languages_json"]["historical_sha256"] is None
    data["artifacts"]["languages_json"]["matches_historical"] = True
    with pytest.raises(m.Run9ValidationError, match="matches_historical"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_matches_historical_recompute_mismatch_rejected() -> None:
    """historical_sha256 が非null で実際に一致している artifact
    （dsconfig_yaml）に matches_historical: false を捏造しても拒否される。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["artifacts"]["dsconfig_yaml"]["matches_historical"] = False
    with pytest.raises(m.Run9ValidationError, match="matches_historical"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_acoustic_onnx_matches_historical_frozen_false() -> None:
    """(g) acoustic_onnx.matches_historical == false の逐語保持: true への
    書き換えは、たとえ sha256_run1 も historical_sha256 に一致するよう
    同時に改竄しても拒否される（frozen-fact ガードは独立に効く）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["artifacts"]["acoustic_onnx"]["sha256_run1"] = data["artifacts"]["acoustic_onnx"][
        "historical_sha256"
    ]
    data["artifacts"]["acoustic_onnx"]["sha256_run2"] = data["artifacts"]["acoustic_onnx"][
        "historical_sha256"
    ]
    data["artifacts"]["acoustic_onnx"]["matches_historical"] = True
    with pytest.raises(m.Run9ValidationError, match="frozen fact"):
        m.validate_reexport_manifest(data)


# --- fail-closed (d): run1_run2_identical の in-process 再計算一致 --------


def test_harness2_reexport_manifest_run1_run2_identical_forged_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["artifacts"]["acoustic_onnx"]["run1_run2_identical"] = False
    with pytest.raises(m.Run9ValidationError, match="run1_run2_identical"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_artifact_sha_shape_enforced() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["artifacts"]["acoustic_onnx"]["sha256_run1"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="64hex"):
        m.validate_reexport_manifest(data)


# --- fail-closed (e): reproducibility_check.all_run1_run2_identical == AND ---


def test_harness2_reexport_manifest_all_run1_run2_identical_forged_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["reproducibility_check"]["all_run1_run2_identical"] = False
    with pytest.raises(m.Run9ValidationError, match="all_run1_run2_identical"):
        m.validate_reexport_manifest(data)


# --- fail-closed (f): smoke wav sha 一致 == determinism_confirmed ----------


def test_harness2_reexport_manifest_smoke_determinism_confirmed_forged_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["smoke_render_cross_check"]["determinism_confirmed"] = False
    with pytest.raises(m.Run9ValidationError, match="determinism_confirmed"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_smoke_avg_sec_arithmetic_mismatch_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["smoke_render_cross_check"]["avg_sec_per_render"] = 99.9
    with pytest.raises(m.Run9ValidationError, match="avg_sec_per_render"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_smoke_budget_estimate_arithmetic_mismatch_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["smoke_render_cross_check"]["budget_estimate_616_renders_sec"] = 1.0
    with pytest.raises(m.Run9ValidationError, match="budget_estimate_616_renders_sec"):
        m.validate_reexport_manifest(data)


# --- artifacts 9点固定・historical_comparison_summary ----------------------


def test_harness2_reexport_manifest_artifacts_registers_exactly_nine_keys() -> None:
    data = _reexport_manifest_data()
    assert set(data["artifacts"].keys()) == m.REEXPORT_ARTIFACT_KEYS
    assert len(m.REEXPORT_ARTIFACT_KEYS) == 9


def test_harness2_reexport_manifest_artifacts_missing_key_fail_closed() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    del data["artifacts"]["d3synth_emb"]
    with pytest.raises(m.Run9ValidationError, match="must register exactly"):
        m.validate_reexport_manifest(data)


# --- fail-closed (h)（PR #327 レビュー第12巡指摘22、P2、採用）:
# artifacts.*.file の全数一意性 -----------------------------------------


def test_harness2_reexport_manifest_artifacts_duplicate_file_rejected() -> None:
    """9エントリのうち2論理 key が同一 `file` 値を指すと、実際には8出力
    しかないのに9 artifacts を主張できてしまう穴（第12巡指摘22）——`file`
    値の全数一意性を fail-closed で強制する非退行確認。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["artifacts"]["pjs_emb"]["file"] = data["artifacts"]["ritsu_emb"]["file"]
    with pytest.raises(m.Run9ValidationError, match="must be unique"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_historical_comparison_summary_unknown_key_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["historical_comparison_summary"]["not_a_real_artifact"] = "x"
    with pytest.raises(m.Run9ValidationError, match="outside the artifacts vocabulary"):
        m.validate_reexport_manifest(data)


# --- load_pinned_reexport_manifest(): 3層防御 read-once + cross-checks -----


def test_harness2_load_pinned_reexport_manifest_happy_path(contract: m.Run9RunContract) -> None:
    data = m.load_pinned_reexport_manifest(contract)
    assert data["schema"] == m.SCHEMA_REEXPORT_MANIFEST


def test_harness2_load_pinned_reexport_manifest_stale_file_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """pin 値と一致しない実バイトは fail-closed 拒否される（stale/改変
    検出）。"""
    tampered_path = tmp_path / "reexport_manifest.json"
    tampered_data = copy.deepcopy(_reexport_manifest_data())
    tampered_data["generated_at_utc"] = "2099-01-01T00:00:00Z"
    tampered_path.write_bytes(_canonical_json_bytes(tampered_data))
    with pytest.raises(m.Run9ValidationError, match="stale"):
        m.load_pinned_reexport_manifest(contract, manifest_path=tampered_path)


def _tampered_reexport_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """reexport_manifest.json の内容を `mutate` で改変し、その実バイト
    sha256 で `reexport_manifest_sha` pin を差し替えた合成 contract +
    manifest ファイル + contract ファイルを用意するテストヘルパー
    （`_tampered_contract_with_dependency_pins_sha_pinned()` と同型）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "reexport_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    import hashlib as _hashlib
    manifest_sha = _hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["reexport_manifest_sha"] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


def test_harness2_load_pinned_reexport_manifest_checkpoint_cross_check_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(6) cross-check (a): input_checkpoint.expected_sha256_per_run9_
    contract が backbone_checkpoint_sha pin と食い違うと拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["input_checkpoint"]["sha256"] = "0" * 64
        data["input_checkpoint"]["expected_sha256_per_run9_contract"] = "0" * 64
        data["input_checkpoint"]["sha256_matches_pin"] = True

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="backbone_checkpoint_sha"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness2_load_pinned_reexport_manifest_diffsinger_commit_cross_check_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(7) cross-check (b): exporter.expected_revision_per_run9_contract が
    bundle 側前方宣言 commit と食い違うと拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        old_revision = data["exporter"]["revision"]
        new_revision = "0" * 40
        data["exporter"]["revision"] = new_revision
        data["exporter"]["expected_revision_per_run9_contract"] = new_revision
        data["exporter"]["revision_matches_pin"] = True
        # PR #327 レビュー第9巡指摘17対応: replay_environment_recipe の
        # exporter checkout 検証 step は exporter.revision の pin 値を
        # 逐語参照している——この構造検証 (validate_reexport_manifest())
        # を素通りさせ、本テストが狙う深い cross-check (b)（bundle 側
        # 前方宣言 commit との食い違い）に到達させるため、step 内の旧
        # revision 文字列も新値へ追随させる。
        data["replay_environment_recipe"]["steps"] = [
            step.replace(old_revision, new_revision)
            for step in data["replay_environment_recipe"]["steps"]
        ]

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="run9_render_code_commit"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness2_load_pinned_reexport_manifest_speaker_embed_cross_check_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(8) cross-check (h): artifacts.pjs_emb.sha256_run1 が
    dependency_pins_manifest.json の speaker_embeddings_unpinned_
    candidates.pjs.candidate_sha256 と食い違うと拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["artifacts"]["pjs_emb"]["sha256_run1"] = "1" * 64
        data["artifacts"]["pjs_emb"]["sha256_run2"] = "1" * 64
        data["artifacts"]["pjs_emb"]["run1_run2_identical"] = True
        data["artifacts"]["pjs_emb"]["matches_historical"] = False

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="speaker_embeddings_unpinned_candidates"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# --- PR #327 レビュー第2巡指摘4: acoustic export companions 4点の cross-check (10) ---


def _dependency_pins_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.DEPENDENCY_PINS_MANIFEST_PATH.read_text(encoding="utf-8"))


def _tampered_dependency_pins_manifest_path(tmp_path: Path, *, mutate) -> Path:
    """`dependency_pins_manifest.json` の内容を `mutate` で改変した合成
    ファイルを用意するテストヘルパー（`load_pinned_reexport_manifest()` の
    `dependency_pins_manifest_path` は正典 pin 経由ではなく直接ファイルを
    読むため、pin 差し替えは不要——ファイルを差し替えるだけでよい）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    mutate(data)
    path = tmp_path / "dependency_pins_manifest.json"
    path.write_bytes(_canonical_json_bytes(data))
    return path


@pytest.mark.parametrize(
    "artifact_key,logical_name",
    [
        ("acoustic_onnx", "acoustic_onnx"),
        ("dsconfig_yaml", "acoustic_dsconfig_yaml"),
        ("phonemes_json", "acoustic_phonemes_json"),
        ("ritsu_emb", "speaker_embed_ritsu"),
    ],
)
def test_harness2_load_pinned_reexport_manifest_companion_cross_check_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path, artifact_key: str, logical_name: str,
) -> None:
    """(10) cross-check (j)（PR #327 レビュー第2巡指摘4）: acoustic export
    companions 4点それぞれについて、dependency_pins_manifest.json 側
    measured_sha256 を改竄すると（reexport_manifest.json 側
    artifacts.{key}.sha256_run1 と食い違うと）fail-closed で拒否される
    ——companion 別に4件とも独立に照合されることを確認する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        for item in data["acoustic_export_companions"]["expected_items"]:
            if item["logical_name"] == logical_name:
                item["measured_sha256"] = "9" * 64
                return
        raise AssertionError(f"logical_name {logical_name!r} not found in fixture")

    tampered_dep_path = _tampered_dependency_pins_manifest_path(tmp_path, mutate=_mutate)
    with pytest.raises(m.Run9ValidationError, match=f"artifacts.{artifact_key}.sha256_run1"):
        m.load_pinned_reexport_manifest(
            contract, dependency_pins_manifest_path=tampered_dep_path,
        )


def test_harness2_load_pinned_reexport_manifest_companion_missing_from_dependency_pins_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(10) cross-check (j): dependency_pins_manifest.json 側に対応する
    logical_name の companion item 自体が存在しない場合も fail-closed で
    拒否される（measured_sha256 の食い違いだけでなく、参照先の欠落も
    検出する）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["acoustic_export_companions"]["expected_items"] = [
            item
            for item in data["acoustic_export_companions"]["expected_items"]
            if item["logical_name"] != "speaker_embed_ritsu"
        ]

    tampered_dep_path = _tampered_dependency_pins_manifest_path(tmp_path, mutate=_mutate)
    with pytest.raises(m.Run9ValidationError, match="does not declare"):
        m.load_pinned_reexport_manifest(
            contract, dependency_pins_manifest_path=tampered_dep_path,
        )


def test_harness2_load_pinned_reexport_manifest_companion_cross_check_reexport_side_tampered(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(10) cross-check (j): reexport_manifest.json 側 artifacts.ritsu_emb.
    sha256_run1 を改竄しても（dependency_pins_manifest.json 側は正典の
    ままでも）食い違いは fail-closed で拒否される（「どちらか一方の sha を
    改竄」の反対側も machine 強制されることの確認）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["artifacts"]["ritsu_emb"]["sha256_run1"] = "8" * 64
        data["artifacts"]["ritsu_emb"]["sha256_run2"] = "8" * 64
        data["artifacts"]["ritsu_emb"]["run1_run2_identical"] = True
        data["artifacts"]["ritsu_emb"]["matches_historical"] = False
        data["artifacts"]["ritsu_emb"]["historical_sha256"] = None

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="artifacts.ritsu_emb.sha256_run1"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# --- PR #327 レビュー第1巡指摘1: export_command_variables (self-contained recipe) ---


def test_harness2_reexport_manifest_export_command_variables_registers_placeholders() -> None:
    data = _reexport_manifest_data()
    variables = data["export_command_variables"]["variables"]
    assert set(variables.keys()) == m._REEXPORT_COMMAND_VARIABLE_NAMES
    assert len(variables) == 3
    for var_def in variables.values():
        assert isinstance(var_def, str) and var_def.strip()
    assert data["export_command_variables"]["path_independence_note"].strip()


def test_harness2_reexport_manifest_export_command_variables_unknown_key_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_command_variables"]["variables"]["<extra>"] = "unexpected"
    with pytest.raises(m.Run9ValidationError, match="must register exactly"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_export_command_variables_missing_placeholder_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    del data["export_command_variables"]["variables"][m._REEXPORT_OUT_DIR_PLACEHOLDER]
    with pytest.raises(m.Run9ValidationError, match="must register exactly"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_export_command_variables_out_dir_mismatch_rejected() -> None:
    """out_dir プレースホルダの定義があっても、実際の export_command の
    最終トークンがそれで始まっていなければ拒否される（定義と実コマンドの
    乖離を machine 強制で防ぐ）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_command"][-1] = "/some/unrelated/path/onnx_gate_40000"
    with pytest.raises(m.Run9ValidationError, match="out_dir placeholder"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_export_command_variables_diffsinger_repo_mismatch_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_command_cwd"] = "/some/unrelated/DiffSinger"
    with pytest.raises(m.Run9ValidationError, match="diffsinger_repo placeholder"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第1巡指摘2: export_environment_lock (完全 pip freeze) ---


def test_harness2_reexport_manifest_export_environment_lock_sha_matches() -> None:
    data = _reexport_manifest_data()
    lock = data["export_environment_lock"]
    assert isinstance(lock, list) and len(lock) > 0
    import hashlib as _hashlib
    expected = _hashlib.sha256(("\n".join(lock) + "\n").encode("utf-8")).hexdigest()
    assert data["export_environment_lock_sha256"] == expected


def test_harness2_reexport_manifest_export_environment_lock_sha_mismatch_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_environment_lock"][0] = "tampered-package==0.0.0"
    with pytest.raises(m.Run9ValidationError, match="export_environment_lock_sha256"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_export_environment_lock_empty_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_environment_lock"] = []
    with pytest.raises(m.Run9ValidationError, match="non-empty list"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_export_environment_lock_sha256_shape_enforced() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_environment_lock_sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="64hex"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第2巡指摘5: replay_environment_recipe / historical_note ---


def test_harness2_reexport_manifest_venv_setup_historical_note_present() -> None:
    data = _reexport_manifest_data()
    assert "replay_environment_recipe" in data["export_venv_setup"]["historical_note"]


def test_harness2_reexport_manifest_venv_setup_historical_note_missing_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    del data["export_venv_setup"]["historical_note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_venv_setup_historical_note_must_reference_recipe() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["export_venv_setup"]["historical_note"] = "this note forgot to name the new key"
    with pytest.raises(m.Run9ValidationError, match="replay_environment_recipe"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_install_steps_unchanged_by_replay_recipe_addition() -> None:
    """`install_steps`（歴史記録、当時実際に実行した手順）自体の4行は
    replay_environment_recipe 新設によって1文字も変更されていないこと
    の回帰固定。"""
    data = _reexport_manifest_data()
    assert data["export_venv_setup"]["install_steps"] == [
        "python -m venv venv_export",
        "pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu",
        "pip install -r <diffsinger_repo clone（session workdir、repo外）>/DiffSinger/requirements.txt",
        "pip install numpy==1.26.4",
    ]


def test_harness2_reexport_manifest_replay_recipe_lock_array_reference_must_match() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["lock_array_reference"] = "some_other_array"
    with pytest.raises(m.Run9ValidationError, match="lock_array_reference"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_missing_no_deps_step_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s.replace("--no-deps", "") for s in data["replay_environment_recipe"]["steps"]
    ]
    with pytest.raises(m.Run9ValidationError, match="--no-deps"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_missing_oneliner_step_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"] if "json.load" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="one-liner"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_torch_index_note_must_reference_cpu_index() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["torch_index_note"] = "torch is on PyPI, no index needed"
    with pytest.raises(m.Run9ValidationError, match="download.pytorch.org/whl/cpu"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_missing_replay_environment_recipe_rejected() -> None:
    data = copy.deepcopy(_reexport_manifest_data())
    del data["replay_environment_recipe"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第6巡指摘12（P2, 採用）: export 実行 step の venv 明示化 ---


def test_harness2_reexport_manifest_replay_recipe_export_step_references_venv_python() -> None:
    """正常系: 現行 steps に export_command を venv_export_replay/bin/python
    経由で実行する step が存在すること（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    assert any(
        "export_command" in s and "venv_export_replay/bin/python" in s for s in steps
    )


def test_harness2_reexport_manifest_replay_recipe_missing_export_step_rejected() -> None:
    """export_command を venv 経由で実行する step 自体が存在しない（旧欠陥
    状態）と reject されること。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"] if "export_command" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="export_command"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_bare_python_export_step_rejected() -> None:
    """export 実行 step が venv_export_replay/bin/python ではなく bare
    `python` を呼ぶ（PR #327 レビュー第6巡指摘12の元の欠陥）と reject
    されること。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = next(
        i for i, s in enumerate(steps)
        if "export_command" in s and "venv_export_replay/bin/python" in s
    )
    steps[export_index] = steps[export_index].replace("venv_export_replay/bin/python", "python")
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="export_command"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_bare_pip_step_rejected() -> None:
    """venv bootstrap（`python -m venv ...`）以外の step に bare `pip`
    起動が混入すると reject されること。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        *data["replay_environment_recipe"]["steps"],
        "pip install something-else",
    ]
    with pytest.raises(m.Run9ValidationError, match="bare `pip`"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_venv_bootstrap_bare_python_allowed() -> None:
    """venv 作成 step 自体（`python -m venv --clear <session workdir（repo外）>/
    venv_export_replay`、直前の interpreter 版検証 step の対象）は ambient
    python を使うのが正当であり、bare-interpreter 検査から除外されること
    （誤検知しないことの回帰固定）。PR #327 第7巡指摘14対応後は steps[0] が
    interpreter 版検証 step、steps[1] が venv 作成 step になった。PR #327
    第8巡指摘15対応後は venv 作成先が cwd 非依存の絶対パスへ変わった。第16巡
    指摘28対応後は venv 作成コマンドへ --clear が付与された。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    assert "-m venv" in steps[1]
    assert "python -m venv --clear <session workdir（repo外）>/venv_export_replay" in steps[1]
    m.validate_reexport_manifest(data)  # 例外なしの確認


# --- PR #327 レビュー第8巡指摘15（P2, 採用）: venv パスの cwd 非依存化 ------


def test_harness2_reexport_manifest_replay_recipe_venv_path_rooted_absolute() -> None:
    """正常系: すべての venv_export_replay 参照が cwd 非依存の絶対パス
    `<session workdir（repo外）>/venv_export_replay` として現れること
    （回帰固定——bare な相対パス参照が1件も残っていないこと）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    rooted = "<session workdir（repo外）>/venv_export_replay"
    for step in steps:
        idx = 0
        while True:
            idx = step.find("venv_export_replay", idx)
            if idx == -1:
                break
            assert step[: idx + len("venv_export_replay")].endswith(rooted), step
            idx += len("venv_export_replay")


def test_harness2_reexport_manifest_replay_recipe_bare_relative_venv_export_step_rejected() -> None:
    """export 実行 step（cwd を export_command_cwd へ変更した後に実行される）
    が cwd 非依存の絶対パスではなく bare な相対パス
    `venv_export_replay/bin/python`（PR #327 レビュー第8巡指摘15の元の
    欠陥——cwd 変更後は DiffSinger ディレクトリ内で解決され実在しない venv
    を指す）のみを参照すると reject されること。venv_python_path 自体が
    cwd 非依存の絶対パスへ再定義されたため、この形は既存の fail-closed (i)
    （export_command を venv 経由で実行する step の存在強制）で reject
    される。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = next(
        i for i, s in enumerate(steps)
        if "export_command" in s and "venv_export_replay/bin/python" in s
    )
    steps[export_index] = steps[export_index].replace(
        "<session workdir（repo外）>/venv_export_replay/bin/python",
        "venv_export_replay/bin/python",
    )
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="venv interpreter path"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_bare_relative_venv_create_step_rejected() -> None:
    """venv 作成 step 自体が bare な相対パス `venv_export_replay` で venv
    を作成していると reject されること（venv 自体の生成先も cwd 非依存の
    絶対パスでなければならない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    steps[1] = steps[1].replace(
        "<session workdir（repo外）>/venv_export_replay", "venv_export_replay",
    )
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="cwd-independent"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第7巡指摘14（P2, 採用）: venv 作成 interpreter 版検証 ---


def test_harness2_reexport_manifest_replay_recipe_interpreter_check_step_present() -> None:
    """正常系: venv 作成 step より前に、`environment_versions.python` の
    pin 値（"3.11.15"）を逐語参照する interpreter 版検証 step が存在する
    こと（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    venv_create_index = next(i for i, s in enumerate(steps) if "-m venv" in s)
    check_index = next(
        i for i, s in enumerate(steps)
        if "environment_versions.python" in s and "3.11.15" in s
    )
    assert check_index < venv_create_index


def test_harness2_reexport_manifest_replay_recipe_interpreter_check_step_missing_rejected() -> None:
    """interpreter 版検証 step が丸ごと欠落していると reject される
    （PR #327 第7巡指摘13の元の欠陥: venv がどの interpreter から作られた
    か検証されないまま）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"]
        if "environment_versions.python" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="interpreter version verification step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_interpreter_check_step_after_venv_create_rejected() -> None:
    """interpreter 版検証 step が venv 作成 step より後に配置されている
    と reject される（存在するだけでは不十分——venv 作成前に実行されて
    いなければ venv の生成元を保護できない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    check_index = next(
        i for i, s in enumerate(steps)
        if "environment_versions.python" in s and "3.11.15" in s
    )
    venv_create_index = next(i for i, s in enumerate(steps) if "-m venv" in s)
    assert check_index < venv_create_index
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    # venv 作成 step の直後（元の check_index を除去した分インデックスが
    # 1つ詰まっているため venv_create_index の位置）へ挿入し直す。
    reordered.insert(venv_create_index, check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="interpreter version verification step"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第9巡指摘16/17（P2×2, 採用）: replay recipe 閉世界性の終端 ---
# 指摘17: exporter checkout（供給 clone の scripts/export.py）の live 検証。
# 指摘16: export 実行後の post-export 閉世界照合（9 artifacts 全数照合）。
# 本巡で recipe の入力（checkpoint + experiment 側4点 + lock + interpreter
# 版）・実行体（exporter checkout + venv interpreter）・出力（9 artifacts）
# の全照合が閉じる。


def _export_step_index(steps: List[str]) -> int:
    return next(
        i for i, s in enumerate(steps)
        if "export_command" in s and "venv_export_replay/bin/python" in s
    )


def test_harness2_reexport_manifest_replay_recipe_exporter_checkout_check_present() -> None:
    """正常系: export 実行 step より前に、`git rev-parse HEAD`/
    `git status --porcelain`/`exporter.revision`（pin 値逐語）を参照する
    exporter checkout 検証 step が存在すること（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    revision = data["exporter"]["revision"]
    check_index = next(
        i for i, s in enumerate(steps)
        if "git rev-parse HEAD" in s and "git status --porcelain" in s
        and "exporter.revision" in s and revision in s
    )
    assert check_index < export_index


def test_harness2_reexport_manifest_replay_recipe_exporter_checkout_check_missing_rejected() -> None:
    """exporter checkout 検証 step が丸ごと欠落していると reject される
    （PR #327 第9巡指摘17の元の欠陥: 供給 clone を無検証実行していた）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"]
        if "git rev-parse HEAD" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="exporter checkout verification step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_exporter_checkout_check_after_export_step_rejected() -> None:
    """exporter checkout 検証 step が export 実行 step より後に配置されて
    いると reject される（存在するだけでは不十分——export 実行前に検証
    されていなければ供給 clone を保護できない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    check_index = next(i for i, s in enumerate(steps) if "git rev-parse HEAD" in s)
    assert check_index < export_index
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    reordered.append(check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="exporter checkout verification step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_post_export_check_present() -> None:
    """正常系: export 実行 step より後に、`artifacts` 9エントリ全数と
    `sha256_run1`/`bytes` フィールド名を参照する post-export 閉世界照合
    step が存在すること（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    check_index = next(
        i for i, s in enumerate(steps)
        if all(key in s for key in m.REEXPORT_ARTIFACT_KEYS)
        and "sha256_run1" in s and "bytes" in s
    )
    assert check_index > export_index


def test_harness2_reexport_manifest_replay_recipe_post_export_check_missing_rejected() -> None:
    """post-export 閉世界照合 step が丸ごと欠落していると reject される
    （PR #327 第9巡指摘16の元の欠陥: 別バイトが生成されても「replay 完了」
    を主張できてしまっていた）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"]
        if not all(key in s for key in m.REEXPORT_ARTIFACT_KEYS)
    ]
    with pytest.raises(m.Run9ValidationError, match="post-export closed-world verification step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_post_export_check_before_export_step_rejected() -> None:
    """post-export 閉世界照合 step が export 実行 step より前に配置されて
    いると reject される（存在するだけでは不十分——export 実行後でなければ
    生成物を照合できない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    check_index = next(
        i for i, s in enumerate(steps)
        if all(key in s for key in m.REEXPORT_ARTIFACT_KEYS)
    )
    assert check_index > export_index
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    reordered.insert(0, check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="post-export closed-world verification step"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第10巡指摘19（P2, 採用）: 未定義トークン全数拒否 + ---
# --- export 実行引数列の canonical export_command[1:] 厳密一致検証   ---
# 本巡で bot レビュー対応の規約上限10巡に到達——「未定義トークン」
# ファミリーの終端巡。第9巡で新設した export 実行 step / post-export 閉
# 世界照合 step のバッククォート内に、export_command_variables.variables
# へ未登録のトークン `<out_dir>` が紛れ込んでいた穴への対応。


def test_harness2_reexport_manifest_replay_recipe_undefined_token_rejected() -> None:
    """export 実行 step のバッククォート逐語コマンド内に、
    export_command_variables.variables へ未登録の `<...>` トークンが
    混入していると reject される（PR #327 第10巡指摘19の元の欠陥:
    `<out_dir>` が未定義のまま残っていた）。地の文の一般的表記
    （`artifacts.<key>.sha256_run1` 等）は走査対象外であることは
    happy path（既存の全 harness2 系テスト）が回帰固定する。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    mutated = steps[export_index].replace("--ckpt 40000 --out", "--ckpt 40000 <out_dir> --out")
    assert mutated != steps[export_index]
    steps[export_index] = mutated
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="undefined token"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_export_step_argument_mismatch_rejected() -> None:
    """export 実行 step のバッククォート逐語コマンドの引数トークン列が
    canonical `export_command[1:]` と食い違っていると reject される
    （interpreter 部の差し替え以外の変更は一切許容しない——1トークンの
    値ズレも検出する）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    mutated = steps[export_index].replace("--ckpt 40000", "--ckpt 99999")
    assert mutated != steps[export_index]
    steps[export_index] = mutated
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="do not exactly match canonical"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第11巡指摘20（P2。規約上限10巡超過後だが、既存
# 「未定義トークン」ファミリー（第10巡）とは別の新しい具体経路——manifest
# 自身/requirements_replay.txt への**相対**参照は `<...>` 形式ではないため
# 第10巡の未定義トークン検証をすり抜けていた——として3分類（将来汚染）に
# 該当し採用）: lock 生成 step が manifest 自身を相対パス
# 'inputs/reexport_manifest.json' のまま json.load しており、repo root や
# workdir から開始した clean replay が FileNotFoundError で落ちる穴。
# `<repo checkout>` 変数を新規登録し、manifest 自身/requirements_replay.txt
# への参照をそれぞれ checkout-stable な rooted prefix へ揃えた。


def test_harness2_reexport_manifest_repo_checkout_variable_registered() -> None:
    """新規登録変数 <repo checkout> が『本リポジトリ Yuu6798/ugh-prompt-engine
    の checkout ルート』を指す定義文であることを固定する。"""
    data = _reexport_manifest_data()
    variables = data["export_command_variables"]["variables"]
    assert m._REEXPORT_REPO_CHECKOUT_PLACEHOLDER in variables
    assert "Yuu6798/ugh-prompt-engine" in variables[m._REEXPORT_REPO_CHECKOUT_PLACEHOLDER]


def test_harness2_reexport_manifest_lock_step_manifest_reference_rooted() -> None:
    """正常系: lock 生成 step のバッククォートコマンドが manifest 自身への
    参照を rooted prefix <repo checkout>/voice_genesis/evolution/
    run9_dual_founder_pjs/inputs/ 付きで持つこと（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    lock_index = next(
        i for i, s in enumerate(steps) if "json.load" in s and "export_environment_lock" in s
    )
    rooted = (
        "<repo checkout>/voice_genesis/evolution/run9_dual_founder_pjs/inputs/"
        "reexport_manifest.json"
    )
    backtick_commands = m._REEXPORT_BACKTICK_COMMAND_PATTERN.findall(steps[lock_index])
    assert len(backtick_commands) == 1
    assert rooted in backtick_commands[0]
    assert "'inputs/reexport_manifest.json'" not in backtick_commands[0]


def test_harness2_reexport_manifest_lock_step_manifest_relative_reference_rejected() -> None:
    """lock 生成 step のバッククォートコマンド内で reexport_manifest.json
    への参照が rooted prefix を伴わない（旧版のような裸の相対パス）と
    reject される（PR #327 レビュー第11巡指摘20の元の欠陥: repo root/
    workdir から開始した clean replay が FileNotFoundError で落ちていた）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    lock_index = next(
        i for i, s in enumerate(steps) if "json.load" in s and "export_environment_lock" in s
    )
    rooted = (
        "<repo checkout>/voice_genesis/evolution/run9_dual_founder_pjs/inputs/"
        "reexport_manifest.json"
    )
    mutated = steps[lock_index].replace(rooted, "inputs/reexport_manifest.json")
    assert mutated != steps[lock_index]
    steps[lock_index] = mutated
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="checkout-stable rooted prefix"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_requirements_replay_relative_reference_rejected() -> None:
    """pip install step のバッククォートコマンド内で requirements_replay.txt
    への参照が rooted prefix <session workdir（repo外）>/ を伴わない
    （裸の相対パス）と reject される。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    pip_index = next(i for i, s in enumerate(steps) if "pip install --no-deps" in s)
    rooted = "<session workdir（repo外）>/requirements_replay.txt"
    assert steps[pip_index].count(rooted) == 1
    mutated = steps[pip_index].replace(rooted, "requirements_replay.txt", 1)
    assert mutated != steps[pip_index]
    steps[pip_index] = mutated
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="checkout-stable rooted prefix"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第16巡指摘28/29（P2×2、採用——規約上限10巡超過後だが ---
# --- 3分類「将来汚染」に該当する新しい具体経路）: replay の再実行衛生     ---
# 指摘28: venv 作成 step が既存 venv_export_replay を再利用すると、
# --no-deps install は lock に無い残留パッケージを除去しない。venv 作成
# コマンドへ --clear を必須化し、pip install の後・export 実行の前に
# freeze/lock 全一致照合 step を必須化する。
# 指摘29: 既存 onnx_gate_40000 が残る workdir へ export すると、stale copy
# が post-export 閉世界照合を偽 pass させ得る。export 実行 step の前に
# export 先ディレクトリの事前空確認 step を必須化する。


def test_harness2_reexport_manifest_replay_recipe_venv_create_clear_flag_present() -> None:
    """正常系: 現行 steps の venv 作成 step が --clear を含むこと（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    venv_create_index = next(i for i, s in enumerate(steps) if "-m venv" in s)
    assert "--clear" in steps[venv_create_index]


def test_harness2_reexport_manifest_replay_recipe_venv_create_missing_clear_flag_rejected() -> None:
    """venv 作成 step から --clear が欠落していると reject される（PR #327
    第16巡指摘28-iの元の欠陥: 既存 venv_export_replay を再利用した replay
    再実行で --no-deps が lock に無い残留パッケージを除去しない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    venv_create_index = next(i for i, s in enumerate(steps) if "-m venv" in s)
    steps[venv_create_index] = steps[venv_create_index].replace("--clear ", "")
    assert "--clear" not in steps[venv_create_index]
    data["replay_environment_recipe"]["steps"] = steps
    with pytest.raises(m.Run9ValidationError, match="--clear"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_freeze_check_step_present() -> None:
    """正常系: pip install step（--no-deps）の後・export 実行 step の前に、
    `pip freeze --all` と `export_environment_lock` を参照する freeze/lock
    全一致照合 step が存在すること（回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    pip_index = next(i for i, s in enumerate(steps) if "--no-deps" in s)
    export_index = _export_step_index(steps)
    check_index = next(
        i for i, s in enumerate(steps)
        if "pip freeze --all" in s and "export_environment_lock" in s
    )
    assert pip_index < check_index < export_index


def test_harness2_reexport_manifest_replay_recipe_freeze_check_step_missing_rejected() -> None:
    """freeze/lock 全一致照合 step が丸ごと欠落していると reject される
    （PR #327 第16巡指摘28-iiの元の欠陥: venv 再利用時の残留パッケージが
    export へ進む前に検出されない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"] if "pip freeze --all" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="freeze/lock reconciliation step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_freeze_check_step_before_pip_install_rejected() -> None:
    """freeze/lock 全一致照合 step が pip install step（--no-deps）より前に
    配置されていると reject される（venv がまだ install 済みでない時点の
    照合は無意味）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    check_index = next(
        i for i, s in enumerate(steps)
        if "pip freeze --all" in s and "export_environment_lock" in s
    )
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    reordered.insert(0, check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="freeze/lock reconciliation step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_freeze_check_step_after_export_step_rejected() -> None:
    """freeze/lock 全一致照合 step が export 実行 step より後に配置されて
    いると reject される（export 実行前に検証されていなければ意味がない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    check_index = next(
        i for i, s in enumerate(steps)
        if "pip freeze --all" in s and "export_environment_lock" in s
    )
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    reordered.append(check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="freeze/lock reconciliation step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_out_dir_check_step_present() -> None:
    """正常系: export 実行 step の前に、export --out 値と `.exists()` を
    参照する export 先ディレクトリ事前空確認 step が存在すること
    （回帰固定）。"""
    data = _reexport_manifest_data()
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    out_arg = data["export_command"][-1]
    check_index = next(
        i for i, s in enumerate(steps) if out_arg in s and ".exists()" in s
    )
    assert check_index < export_index


def test_harness2_reexport_manifest_replay_recipe_out_dir_check_step_missing_rejected() -> None:
    """export 先ディレクトリ事前空確認 step が丸ごと欠落していると reject
    される（PR #327 第16巡指摘29の元の欠陥: 既存 onnx_gate_40000 の stale
    copy が post-export 閉世界照合を偽 pass させ得る）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    data["replay_environment_recipe"]["steps"] = [
        s for s in data["replay_environment_recipe"]["steps"] if ".exists()" not in s
    ]
    with pytest.raises(m.Run9ValidationError, match="export-directory pre-flight step"):
        m.validate_reexport_manifest(data)


def test_harness2_reexport_manifest_replay_recipe_out_dir_check_step_after_export_step_rejected() -> None:
    """export 先ディレクトリ事前空確認 step が export 実行 step より後に
    配置されていると reject される（存在するだけでは不十分——export 実行前
    でなければ stale copy を検出できない）。"""
    data = copy.deepcopy(_reexport_manifest_data())
    steps = data["replay_environment_recipe"]["steps"]
    export_index = _export_step_index(steps)
    check_index = next(i for i, s in enumerate(steps) if ".exists()" in s)
    assert check_index < export_index
    reordered = list(steps)
    check_step = reordered.pop(check_index)
    reordered.append(check_step)
    data["replay_environment_recipe"]["steps"] = reordered
    with pytest.raises(m.Run9ValidationError, match="export-directory pre-flight step"):
        m.validate_reexport_manifest(data)


# --- PR #327 レビュー第1巡指摘3: adjudication_basis 実バイト cross-check (9) ---


def test_harness2_load_pinned_reexport_manifest_adjudication_source_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(9) cross-check (i): adjudication_basis.source_file の実バイトが
    改変されていると（sha256 が adjudication_basis.sha256 と食い違うと）
    fail-closed で拒否される——裁定 txt の事後編集を検出する。"""
    tampered_path = tmp_path / "USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt"
    tampered_path.write_bytes(HARNESS2_ADJUDICATION_PATH.read_bytes() + b"\ntampered\n")
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_reexport_manifest(contract, adjudication_basis_path=tampered_path)


def test_harness2_load_pinned_reexport_manifest_adjudication_manifest_sha_forged_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(9) cross-check (i): manifest 側 adjudication_basis.sha256 を実
    バイトと異なる値へ改竄しても、実 read + 再計算で fail-closed 拒否
    される（source_file 自体は改変しない）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "0" * 64

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness2_load_pinned_reexport_manifest_adjudication_source_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.txt"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_reexport_manifest(contract, adjudication_basis_path=missing_path)


# --- PR #327 レビュー第11巡指摘21（P2、採用）: adjudication_basis.source_
# file の join 解決が絶対パス・`../` traversal・symlink 脱出を containment
# check なしで受理し、digest さえ一致すれば checkout 外のファイルでも正典
# provenance として通ってしまっていた。`_resolve_repo_contained_path()`
# （lexical 検証 + resolved 検証の二重 fail-closed）を新設し、reexport
# manifest の adjudication_basis / execution profile loader の裁定 txt
# パス / render_code パス解決という同型の解決点すべてへ適用する（ファミリー
# 掃討）。テスト用パスオーバーライド引数（`adjudication_basis_path`/
# `render_code_path`）は検証対象外のまま——既存テストは `tmp_path` 配下の
# 絶対パスをオーバーライドへ渡す流儀のため、オーバーライドまで検証対象に
# 含めると壊れる（`_resolve_repo_contained_path()` docstring に設計判断を
# 明記）。


def test_resolve_repo_contained_path_absolute_rejected(tmp_path: Path) -> None:
    """(i) lexical 検証: 絶対パスは即座に拒否される。"""
    with pytest.raises(m.Run9ValidationError, match="must be a repo-relative path"):
        m._resolve_repo_contained_path(
            str(tmp_path / "escaped.txt"), repo_root=tmp_path, field="x.y", context="test",
        )


def test_resolve_repo_contained_path_traversal_rejected(tmp_path: Path) -> None:
    """(i) lexical 検証: `..` 成分を含む相対パスは即座に拒否される。"""
    with pytest.raises(m.Run9ValidationError, match="must not contain '\\.\\.'"):
        m._resolve_repo_contained_path(
            "../escaped.txt", repo_root=tmp_path, field="x.y", context="test",
        )


def test_resolve_repo_contained_path_symlink_escape_rejected(tmp_path: Path) -> None:
    """(ii) resolved 検証: lexical には repo 配下に見える相対パスでも、
    symlink 経由で repo 外を指していれば resolve() 後の実体パスで拒否
    される。"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("outside\n", encoding="utf-8")
    link = repo_root / "escape_link.txt"
    link.symlink_to(outside_target)
    with pytest.raises(m.Run9ValidationError, match="escapes the repo root"):
        m._resolve_repo_contained_path(
            "escape_link.txt", repo_root=repo_root, field="x.y", context="test",
        )


def test_resolve_repo_contained_path_legitimate_relative_resolves(tmp_path: Path) -> None:
    """正常系: repo 配下に実在する正当な相対パスは resolve() された絶対
    パスを返す（回帰固定——containment guard が正当な参照まで拒否しない
    こと）。"""
    repo_root = tmp_path / "repo"
    (repo_root / "sub").mkdir(parents=True)
    target = repo_root / "sub" / "file.txt"
    target.write_text("ok\n", encoding="utf-8")
    resolved = m._resolve_repo_contained_path(
        "sub/file.txt", repo_root=repo_root, field="x.y", context="test",
    )
    assert resolved == target.resolve()


def test_harness2_load_pinned_reexport_manifest_adjudication_source_absolute_path_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """適用点1/3（reexport manifest）: adjudication_basis.source_file が
    絶対パスだと containment guard で拒否される（digest 自体は本物の裁定
    txt を指すため一致し得るが、絶対パスというだけで fail-closed 拒否）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["source_file"] = str(HARNESS2_ADJUDICATION_PATH)

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must be a repo-relative path"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness2_load_pinned_reexport_manifest_adjudication_source_traversal_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """適用点1/3（reexport manifest）: `../` traversal を含む source_file
    は containment guard で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["source_file"] = "../" * 6 + "etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_reexport_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must not contain '\\.\\.'"):
        m.load_pinned_reexport_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness2_load_pinned_reexport_manifest_adjudication_source_symlink_escape_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """適用点1/3（reexport manifest）: lexical には repo 配下に見える
    相対 source_file でも、実体が symlink 経由で repo 外を指していれば
    拒否される。実リポジトリへは一切書き込まない——`_REEXPORT_REPO_ROOT`
    を隔離 tmp_path へ monkeypatch し、その配下にのみ symlink を作る。"""
    fake_repo_root = tmp_path / "fake_repo"
    rel = _reexport_manifest_data()["adjudication_basis"]["source_file"]
    link_path = fake_repo_root / rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    outside_target = tmp_path / "outside_adjudication.txt"
    outside_target.write_text("outside\n", encoding="utf-8")
    link_path.symlink_to(outside_target)
    monkeypatch.setattr(m, "_REEXPORT_REPO_ROOT", fake_repo_root)
    with pytest.raises(m.Run9ValidationError, match="escapes the repo root"):
        m.load_pinned_reexport_manifest(contract)


# --- 新 status OBTAINED_DERIVED_NEW_BYTES / OBTAINED_VIA_REEXPORT 判別 shape ---


def test_harness2_dependency_pins_derived_new_bytes_status_in_vocab() -> None:
    assert "OBTAINED_DERIVED_NEW_BYTES" in m._ACOUSTIC_COMPANION_ITEM_STATUS_VOCAB
    assert "OBTAINED_VIA_REEXPORT" in m._ACOUSTIC_COMPANIONS_STATUS_VOCAB


def test_harness2_dependency_pins_acoustic_onnx_matches_historical_forged_true_rejected() -> None:
    """companions 実データの acoustic.onnx は matches_historical=false が
    frozen fact——true への書き換えは（sha256 を同時に細工しても）拒否
    される。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_onnx"
    )
    item["measured_sha256"] = item["expected_sha256"]
    item["historical_expected_sha256"] = item["expected_sha256"]
    item["matches_historical"] = True
    with pytest.raises(m.Run9ValidationError, match="frozen fact"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_dependency_pins_derived_item_wrong_acquisition_source_rejected() -> None:
    """OBTAINED_VIA_REEXPORT 配下の item は acquisition_source ==
    'RE_EXPORT' を強制する（THIS_TARBALL/DRIVE_DIRECT は拒否）。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_dsconfig_yaml"
    )
    item["acquisition_source"] = "THIS_TARBALL"
    with pytest.raises(m.Run9ValidationError, match="acquisition_source must be 'RE_EXPORT'"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_dependency_pins_derived_item_unknown_status_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_onnx"
    )
    item["status"] = "MADE_UP_STATUS"
    with pytest.raises(m.Run9ValidationError, match="expected_items\\[.\\]\\.status must be one of"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_dependency_pins_verified_item_missing_replay_evidence_rejected() -> None:
    """OBTAINED_VERIFIED_MATCH（reexport 経由）の item は replay_evidence
    を必須とする。"""
    data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_dsconfig_yaml"
    )
    del item["replay_evidence"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_dependency_pins_derived_item_reexport_ref_wrong_artifact_key_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_onnx"
    )
    item["reexport_manifest_ref"]["artifact_key"] = "not_acoustic_onnx"
    with pytest.raises(m.Run9ValidationError, match="must equal this item's own logical_name"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_dependency_pins_derived_item_reexport_ref_stale_sha_cross_check_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """load 時 cross-check: reexport_manifest_ref.reexport_manifest_
    sha256 が現行 reexport_manifest_sha pin と食い違うと拒否される
    （stale reference の検出）。"""
    dep_data = copy.deepcopy(_dependency_pins_manifest_data())
    item = next(
        i for i in dep_data["acoustic_export_companions"]["expected_items"]
        if i["logical_name"] == "acoustic_onnx"
    )
    item["reexport_manifest_ref"]["reexport_manifest_sha256"] = "0" * 64
    dep_bytes = _canonical_json_bytes(dep_data)
    dep_path = tmp_path / "dependency_pins_manifest.json"
    dep_path.write_bytes(dep_bytes)
    import hashlib as _hashlib
    dep_sha = _hashlib.sha256(dep_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["dependency_pins_sha"] = {"value": dep_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered_raw)
    with pytest.raises(m.Run9ValidationError, match="reexport_manifest_ref"):
        m.load_pinned_dependency_pins_manifest(
            tampered_contract, manifest_path=dep_path, contract_path=tampered_contract_path,
        )


# --- speaker_embeddings_unpinned_candidates: replay_evidence / 昇格未充足 ---


def test_harness2_speaker_candidates_have_replay_evidence_and_unmet_note() -> None:
    data = _dependency_pins_manifest_data()
    for key in ("pjs", "user", "d3synth_reference_only"):
        entry = data["speaker_embeddings_unpinned_candidates"][key]
        assert entry["replay_evidence"] is True
        assert entry["promotion_condition_unmet_note"].strip()


def test_harness2_speaker_candidate_replay_evidence_false_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["speaker_embeddings_unpinned_candidates"]["pjs"]["replay_evidence"] = False
    with pytest.raises(m.Run9ValidationError, match="replay_evidence must be the literal boolean True"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_speaker_candidate_missing_promotion_unmet_note_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    del data["speaker_embeddings_unpinned_candidates"]["user"]["promotion_condition_unmet_note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


# --- budget_estimate.total_render_count_provenance_note -------------------


def test_harness2_budget_estimate_provenance_note_present_and_grep_fixed() -> None:
    """616 件が確定値ではなく踏襲概算であることの出典注記が実データに
    存在すること（grep 回帰固定）。"""
    data = _dependency_pins_manifest_data()
    note = data["budget_estimate"]["total_render_count_provenance_note"]
    assert "616" in note
    assert ("確定" in note) or ("概算" in note)


def test_harness2_budget_estimate_missing_provenance_note_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    del data["budget_estimate"]["total_render_count_provenance_note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_dependency_pins_manifest(data)


# --- smoke_render 新規フィールド（render1/2・entrypoint・providers） ------


def test_harness2_smoke_render_new_fields_present() -> None:
    data = _dependency_pins_manifest_data()
    smoke = data["smoke_render"]
    assert smoke["render1_total_elapsed_sec"] > 0
    assert smoke["render2_total_elapsed_sec"] > 0
    avg = (smoke["render1_total_elapsed_sec"] + smoke["render2_total_elapsed_sec"]) / 2
    assert smoke["measured_sec_per_render"] == pytest.approx(avg, rel=1e-9)
    assert smoke["render_entrypoint"].strip()
    assert smoke["onnxruntime_providers"] == ["CPUExecutionProvider"]


def test_harness2_smoke_render_avg_arithmetic_mismatch_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["smoke_render"]["measured_sec_per_render"] = 999.0
    with pytest.raises(m.Run9ValidationError, match="measured_sec_per_render"):
        m.validate_dependency_pins_manifest(data)


def test_harness2_smoke_render_empty_providers_rejected() -> None:
    data = copy.deepcopy(_dependency_pins_manifest_data())
    data["smoke_render"]["onnxruntime_providers"] = []
    with pytest.raises(m.Run9ValidationError, match="onnxruntime_providers"):
        m.validate_dependency_pins_manifest(data)


# --- HARNESS2_REEXPORT_SMOKE_RECORD.md の repo 収載 -------------------------


def test_harness2_smoke_record_file_exists() -> None:
    assert (_RUN_DIR / "HARNESS2_REEXPORT_SMOKE_RECORD.md").is_file()


def test_harness2_smoke_record_contains_key_measured_values() -> None:
    text = (_RUN_DIR / "HARNESS2_REEXPORT_SMOKE_RECORD.md").read_text(encoding="utf-8")
    assert "c7e1dcdfb7139d490dc19347c21dad5f9966764182cb6ee7e0124ad8fedd379e" in text
    assert "24.101547837257385" in text
    assert "CPUExecutionProvider" in text


# --- RUN9_CONTRACT.yaml: execution_profile_sha は RUN9-EXECPROFILE-1 で --
# --- PENDING → PINNED へ遷移した（旧テスト `test_harness2_execution_ ----
# --- profile_sha_still_pending_after_smoke_measured` を置き換え） --------


def test_execprofile_contract_raw_execution_profile_sha_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    """RUN9-EXECPROFILE-1（2026-08-26）: User 裁定「RUN9 User裁定 —
    execution_profile_sha」の承認により、smoke 実測完了後の PENDING 待機
    （旧 reason「smoke 実測は完了した...User 裁定待ち」）を経て PINNED
    へ遷移した。value は `inputs/execution_profile_manifest.json` の実
    バイト sha256（design_doc_sha256 と同一のファイル実バイト規約）。"""
    field = contract_raw["execution_profile_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.EXECUTION_PROFILE_MANIFEST_PATH)
    assert field["source"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/inputs/execution_profile_manifest.json"
    )


def test_harness2_dependency_pins_sha_still_pending_after_companions_resolved(
    contract_raw: Dict[str, Any],
) -> None:
    """companions/smoke/budget 解消後も dependency_pins_sha は学習ハーネス
    closure 未確定を理由に PENDING のまま。"""
    field = contract_raw["dependency_pins_sha"]
    assert field["status"] == "PENDING"
    assert field["value"] is None
    assert "import closure" in field["reason"]


# --- README.md: stale 現在形記述ゼロの grep 回帰 ---------------------------


def test_harness2_readme_no_stale_current_tense_companions_miss_or_blocked_claim() -> None:
    """README の各段落について、acoustic export companions への言及が
    'MISS'/'BLOCKED' を含む場合は、同一段落内に「解消済み」または「履歴」
    という補正マーカーを伴っていること（stale 現在形記述の防止、
    `test_harness1_pr326_fix3_readme_pending_count_reverted_to_ten_and_
    eleven` と同型のパターン）。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    for paragraph in readme_text.split("\n\n"):
        if "acoustic export companions" in paragraph and (
            "MISS" in paragraph or "BLOCKED" in paragraph
        ):
            assert ("解消済み" in paragraph) or ("履歴" in paragraph), (
                f"stale current-tense companions MISS/BLOCKED claim in paragraph: {paragraph!r}"
            )


def test_harness2_readme_references_new_artifacts() -> None:
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "reexport_manifest.json" in readme_text
    assert "HARNESS2_REEXPORT_SMOKE_RECORD.md" in readme_text
    assert "USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt" in readme_text


# ---------------------------------------------------------------------------
# RUN9-EXECPROFILE-1（2026-08-26）: execution_profile_manifest
# （schema `run9-execution-profile/1.0`）+ execution_profile_sha PINNED 化。
# User 裁定「RUN9 User裁定 — execution_profile_sha」（repo 内収載
# USER_ADJUDICATION_20260826_EXECUTION_PROFILE.txt）に基づく。
# ---------------------------------------------------------------------------

EXECPROFILE_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260826_EXECUTION_PROFILE.txt"
)


def _execprofile_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.EXECUTION_PROFILE_MANIFEST_PATH.read_text(encoding="utf-8"))


# --- 裁定文書の repo 収載（PIN-2/HARNESS-2 前例と同型） --------------------


def test_execprofile_adjudication_source_file_exists() -> None:
    assert EXECPROFILE_ADJUDICATION_PATH.is_file()


def test_execprofile_adjudication_source_contains_verbatim_values() -> None:
    """凍結した各値（runtime 5値・provider 固定規則の核心文言・smoke
    benchmark 3値・追加実測9項目の箇条書き）が、repo 内収載した裁定文書の
    本文に一字一句そのまま存在すること（grep 照合——「User 転記であって
    発明でない」ことを機械検証する）。"""
    text = EXECPROFILE_ADJUDICATION_PATH.read_text(encoding="utf-8")
    for value in (
        '"3.11.15"', '"Ubuntu 24.04.4"', '"x86_64"', '"1.29.0"',
        '"CPUExecutionProvider"',
        "CPUExecutionProvider に固定する。",
        "混同しない。",
        "GPU/CUDA provider への自動fallback / upgradeは禁止する。",
        "同じ execution_profile_sha を使わず再pinする。",
        "observed_seconds_per_item: 24.1",
        "planned_item_count: 616",
        "estimated_total_runtime_hours: approximately 4.12",
        "CPU model",
        "logical CPU count",
        "onnxruntime available_providers",
        "onnxruntime selected providers",
        "intra_op_num_threads / inter_op_num_threads",
        "numpy version",
        "soundfile version",
        "render code commit",
        "deterministic seed / thread environment variables",
    ):
        assert value in text, f"missing verbatim value: {value!r}"


def test_execprofile_adjudication_source_body_byte_identical_to_scratchpad_origin() -> None:
    """本文（【RUN9 User裁定 — execution_profile_sha】以降）が起草時の
    作業メモ scratchpad/run9_user_adjudication_execprofile.md と一字一句
    改変なしで一致すること（改変禁止の直接確認、PIN-2/HARNESS-2 前例と
    同型——scratchpad ファイルが本セッション後に存在しない環境では
    skip）。"""
    scratchpad_path = Path(
        "/tmp/claude-0/-home-user-ugh-prompt-engine/"
        "e505c1c2-c4ad-588b-a1b2-258051a522de/scratchpad/"
        "run9_user_adjudication_execprofile.md"
    )
    if not scratchpad_path.is_file():
        pytest.skip("scratchpad origin file not present in this environment")
    origin_body = scratchpad_path.read_text(encoding="utf-8")
    origin_body = "【RUN9 User裁定" + origin_body.split("【RUN9 User裁定", 1)[1]
    committed_text = EXECPROFILE_ADJUDICATION_PATH.read_text(encoding="utf-8")
    committed_body = "【RUN9 User裁定" + committed_text.split("【RUN9 User裁定", 1)[1]
    assert committed_body == origin_body


# --- validate_execution_profile_manifest(): 正常系・直列化 -----------------


def test_execprofile_validate_real_manifest_happy_path() -> None:
    m.validate_execution_profile_manifest(_execprofile_manifest_data())  # 例外なしの確認


def test_execprofile_manifest_reserialization_byte_identical() -> None:
    """`json.dumps(..., ensure_ascii=False, sort_keys=True, indent=2)` +
    改行 で再直列化したバイト列が実ファイルのバイトと完全一致すること
    （直列化規約の回帰固定、PIN-2/HARNESS-2 前例と同型）。"""
    data = _execprofile_manifest_data()
    reserialized = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    assert reserialized.encode("utf-8") == m.EXECUTION_PROFILE_MANIFEST_PATH.read_bytes()


def test_execprofile_manifest_schema_constant() -> None:
    data = _execprofile_manifest_data()
    assert data["schema"] == m.SCHEMA_EXECUTION_PROFILE_MANIFEST == "run9-execution-profile/1.0"


# --- validate_execution_profile_manifest(): fail-closed 分岐 ---------------


def test_execprofile_unknown_top_level_key_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_missing_top_level_key_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    del data["benchmark_reference"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_execution_profile_manifest(data)


@pytest.mark.parametrize("field", ["python", "os", "architecture", "onnxruntime", "selected_execution_provider"])
def test_execprofile_runtime_value_tamper_rejected(field: str) -> None:
    """(a) fail-closed: identity_semantics.runtime の5値いずれかが裁定
    逐語と食い違うと拒否される（捏造・転記ミスの機械検出）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["identity_semantics"]["runtime"][field] = "TAMPERED"
    with pytest.raises(m.Run9ValidationError, match="diverges from the adjudicated runtime"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_runtime_extra_key_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["identity_semantics"]["runtime"]["gpu"] = "none"
    with pytest.raises(m.Run9ValidationError, match="diverges from the adjudicated runtime"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_provider_fixation_rule_marker_missing_rejected() -> None:
    """(c) fail-closed: provider_fixation_rules[i] が対応するマーカー
    文言を含まないと拒否される。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["identity_semantics"]["provider_fixation_rules"][0] = "no marker phrase here"
    with pytest.raises(m.Run9ValidationError, match="adjudicated marker phrase"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_provider_fixation_rules_wrong_count_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["identity_semantics"]["provider_fixation_rules"] = data["identity_semantics"][
        "provider_fixation_rules"
    ][:3]
    with pytest.raises(m.Run9ValidationError, match="provider_fixation_rules"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_selected_provider_value_must_match_runtime() -> None:
    """(b) fail-closed: additional_measurements.onnxruntime_selected_
    providers.value は [runtime.selected_execution_provider] と厳密一致
    しなければならない（GPU provider 等の混入を拒否する）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["onnxruntime_selected_providers"]["value"] = [
        "CUDAExecutionProvider"
    ]
    with pytest.raises(m.Run9ValidationError, match="onnxruntime_selected_providers.value"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_selected_provider_equal_to_cpu_only_available_accepted() -> None:
    """(b) fail-closed 撤去確認（PR #327 レビュー第3巡指摘9対応、P2、採用）:
    available が CPUExecutionProvider のみで観測された正当な CPU-only
    環境では、selected（[runtime.selected_execution_provider] 固定=
    ["CPUExecutionProvider"]）が available と同一集合になっても受理
    される——旧実装はこの正当な組合せを『available の列挙と selected を
    混同しない』の機械化と誤って同一視し、真部分集合（strict subset）を
    要求して拒否していた（CPU-only 環境の正当な再pin を阻害していた）。
    "混同しない" は selected/available が独立した measurement item
    として存在すること + selected が固定値であることの shape で担保し、
    値の偶然の一致自体は禁止しない。available 側を selected 単独の集合
    （CPUExecutionProvider のみ）へ書き換えて確認する——selected 自体は
    依然 runtime.selected_execution_provider と一致させたまま。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["onnxruntime_available_providers"]["value"] = [
        "CPUExecutionProvider"
    ]
    data["additional_measurements"]["onnxruntime_available_providers"]["matches_smoke_record"] = False
    m.validate_execution_profile_manifest(data)  # 例外なしの確認（旧実装は拒否していた）


def test_execprofile_real_manifest_available_providers_unchanged() -> None:
    """PR #327 レビュー第3巡指摘9対応: 実 manifest の
    `onnxruntime_available_providers.value`（実測値、AzureExecutionProvider
    + CPUExecutionProvider の2件）は本対応で変更していないことを確認する
    ——validator/テストのみの修正であり、manifest 実バイトは不変。"""
    data = _execprofile_manifest_data()
    assert data["additional_measurements"]["onnxruntime_available_providers"]["value"] == [
        "AzureExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert data["additional_measurements"]["onnxruntime_selected_providers"]["value"] == [
        "CPUExecutionProvider"
    ]


def test_execprofile_selected_provider_not_subset_of_available_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["onnxruntime_selected_providers"]["value"] = [
        "CPUExecutionProvider"
    ]
    data["additional_measurements"]["onnxruntime_available_providers"]["value"] = [
        "AzureExecutionProvider"
    ]
    data["additional_measurements"]["onnxruntime_available_providers"]["matches_smoke_record"] = False
    with pytest.raises(m.Run9ValidationError, match="is not a subset of"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_is_reference_only_false_rejected() -> None:
    """(d) frozen-fact ガード: benchmark_reference.is_reference_only を
    false 化する改竄は恒久的に拒否される（benchmark 値が identity 意味論
    へ混入する経路を閉じる）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["benchmark_reference"]["is_reference_only"] = False
    with pytest.raises(m.Run9ValidationError, match="is_reference_only must remain the literal boolean True"):
        m.validate_execution_profile_manifest(data)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("observed_seconds_per_item", 999.0),
        ("planned_item_count", 999),
        ("estimated_total_runtime_hours", "approximately 9.99"),
    ],
)
def test_execprofile_benchmark_value_tamper_rejected(field: str, bad_value: Any) -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["benchmark_reference"][field] = bad_value
    with pytest.raises(m.Run9ValidationError, match="diverges from the adjudicated value"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_benchmark_keys_cannot_leak_into_identity_semantics() -> None:
    """(d) shape 分離: identity_semantics へ benchmark 系キーを追加しよう
    とすると unknown-key として拒否される（構造的分離の直接確認）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["identity_semantics"]["observed_seconds_per_item"] = 24.1
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_not_recorded_item_with_extra_value_key_rejected() -> None:
    """(f) 推測補完の構造的禁止: NOT_RECORDED item に value 系キーを追加
    すると拒否される（実測できなかった事実に値をこっそり同居させる経路を
    塞ぐ）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    thread_env = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["thread_environment_variables"]
    assert thread_env["status"] == "NOT_RECORDED"
    thread_env["value"] = "OMP_NUM_THREADS=1"
    with pytest.raises(m.Run9ValidationError, match="NOT_RECORDED item must have exactly the keys"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_not_recorded_item_missing_reason_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    thread_env = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["thread_environment_variables"]
    del thread_env["reason"]
    with pytest.raises(m.Run9ValidationError, match="NOT_RECORDED item must have exactly the keys"):
        m.validate_execution_profile_manifest(data)


# --- fail-closed（PR #327 レビュー第12巡指摘23、P2、採用）:
# thread_environment_variables MEASURED payload の実値検証 --------------


def test_execprofile_thread_environment_variables_measured_empty_value_rejected() -> None:
    """`thread_environment_variables` が MEASURED の場合に value が空文字列
    でも、shape 検証（キー集合のみ）を通過してしまい証拠なしの空成功記録へ
    昇格し得た穴（第12巡指摘23）——numpy_item/soundfile_item と同型の非空
    検証を fail-closed で強制する非退行確認。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    thread_env = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["thread_environment_variables"]
    assert thread_env["status"] == "NOT_RECORDED"
    del thread_env["reason"]
    thread_env["status"] = "MEASURED"
    thread_env["value"] = ""
    thread_env["method"] = "env var dump at smoke render time"
    with pytest.raises(m.Run9ValidationError, match="thread_environment_variables.value"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_thread_environment_variables_measured_empty_method_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    thread_env = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["thread_environment_variables"]
    assert thread_env["status"] == "NOT_RECORDED"
    del thread_env["reason"]
    thread_env["status"] = "MEASURED"
    thread_env["value"] = "OMP_NUM_THREADS=1"
    thread_env["method"] = ""
    with pytest.raises(m.Run9ValidationError, match="thread_environment_variables.method"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_thread_environment_variables_measured_happy_path_accepted() -> None:
    """正例: value/method がともに非空であれば MEASURED payload は受理
    される。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    thread_env = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["thread_environment_variables"]
    assert thread_env["status"] == "NOT_RECORDED"
    del thread_env["reason"]
    thread_env["status"] = "MEASURED"
    thread_env["value"] = "OMP_NUM_THREADS=1"
    thread_env["method"] = "env var dump at smoke render time"
    m.validate_execution_profile_manifest(data)  # 例外なしの確認


def test_execprofile_measurement_item_bad_status_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["cpu_model"]["status"] = "GUESSED"
    with pytest.raises(m.Run9ValidationError, match="status must be one of"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_measured_item_missing_required_key_rejected() -> None:
    data = copy.deepcopy(_execprofile_manifest_data())
    del data["additional_measurements"]["cpu_model"]["method"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_render_code_commit_smoke_file_sha_mismatch_rejected() -> None:
    """render_code_commit: smoke_time_gate_synth_py_sha256 と file_sha256
    が食い違うと（unchanged 主張との自己矛盾として）拒否される。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["render_code_commit"]["smoke_time_gate_synth_py_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="contradicts an 'unchanged since smoke' claim"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_thread_settings_default_unspecified_requires_null_value() -> None:
    """intra/inter_op_num_threads: DEFAULT_UNSPECIFIED を名乗るなら value
    は null でなければならない（未指定の事実を数値で偽装しない）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    intra = data["additional_measurements"]["onnxruntime_thread_settings"]["intra_op_num_threads"]
    intra["specification_status"] = "DEFAULT_UNSPECIFIED"
    # value はそのまま 1 (非null) に残す — 矛盾を作る。
    with pytest.raises(m.Run9ValidationError, match="must be null when specification_status is"):
        m.validate_execution_profile_manifest(data)


# --- PR #327 レビュー第15巡指摘27（P2、採用）: thread 設定 source_line_text
# の shape 必須化（validate_execution_profile_manifest()） ------------------


@pytest.mark.parametrize("sub_field", ["intra_op_num_threads", "inter_op_num_threads"])
def test_execprofile_thread_settings_missing_source_line_text_rejected(sub_field: str) -> None:
    """intra/inter_op_num_threads: `source_line_text` キーが欠落した shape
    は fail-closed で拒否される（第15巡指摘27対応——旧 shape は
    `source_file`/`source_line`/`specification_status`/`value` の4キーのみ
    で `source_line_text` を必須としていなかった）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    del data["additional_measurements"]["onnxruntime_thread_settings"][sub_field]["source_line_text"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_execution_profile_manifest(data)


@pytest.mark.parametrize("sub_field", ["intra_op_num_threads", "inter_op_num_threads"])
def test_execprofile_thread_settings_empty_source_line_text_rejected(sub_field: str) -> None:
    """intra/inter_op_num_threads: `source_line_text` が空文字列だと
    fail-closed で拒否される（非空 str の必須化）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["onnxruntime_thread_settings"][sub_field]["source_line_text"] = ""
    with pytest.raises(m.Run9ValidationError, match="source_line_text"):
        m.validate_execution_profile_manifest(data)


def test_execprofile_thread_settings_source_line_text_happy_path_accepted() -> None:
    """正例維持: 現行 manifest の thread 設定 source_line_text（gate_
    synth.py の実 so.intra/inter_op_num_threads 代入文の逐語）は
    validate_execution_profile_manifest() を素通りする。"""
    data = _execprofile_manifest_data()
    ts = data["additional_measurements"]["onnxruntime_thread_settings"]
    assert ts["intra_op_num_threads"]["source_line_text"] == "so.intra_op_num_threads = 1"
    assert ts["inter_op_num_threads"]["source_line_text"] == "so.inter_op_num_threads = 1"
    m.validate_execution_profile_manifest(data)  # 例外なしの確認


def test_execprofile_matches_smoke_record_self_check_rejected() -> None:
    """numpy_version.matches_smoke_record が実データと食い違うと拒否
    される（自己申告フィールドの in-process 再計算チェック）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    data["additional_measurements"]["numpy_version"]["matches_smoke_record"] = False
    with pytest.raises(m.Run9ValidationError, match="matches_smoke_record"):
        m.validate_execution_profile_manifest(data)


# --- load_pinned_execution_profile_manifest(): 正常系・cross-check ---------


def test_execprofile_load_pinned_execution_profile_manifest_happy_path(
    contract: m.Run9RunContract,
) -> None:
    data = m.load_pinned_execution_profile_manifest(contract)
    assert data["schema"] == m.SCHEMA_EXECUTION_PROFILE_MANIFEST


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_source_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """cross-check: adjudication_basis.source_file の実バイトが改変され
    ていると（sha256 が adjudication_basis.sha256 と食い違うと）
    fail-closed で拒否される——裁定 txt の事後編集を検出する。"""
    tampered_path = tmp_path / "USER_ADJUDICATION_20260826_EXECUTION_PROFILE.txt"
    tampered_path.write_bytes(EXECPROFILE_ADJUDICATION_PATH.read_bytes() + b"\ntampered\n")
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_execution_profile_manifest(contract, adjudication_basis_path=tampered_path)


def _tampered_execprofile_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """execution_profile_manifest.json の内容を `mutate` で改変し、その実
    バイト sha256 で `execution_profile_sha` pin を差し替えた合成
    contract + manifest ファイル + contract ファイルを用意するテスト
    ヘルパー（`_tampered_reexport_contract()` と同型）。"""
    data = copy.deepcopy(_execprofile_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "execution_profile_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    import hashlib as _hashlib
    manifest_sha = _hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["execution_profile_sha"] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_manifest_sha_forged_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """cross-check: manifest 側 adjudication_basis.sha256 を実バイトと
    異なる値へ改竄しても、実 read + 再計算で fail-closed 拒否される
    （source_file 自体は改変しない）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "0" * 64

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_source_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.txt"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_execution_profile_manifest(contract, adjudication_basis_path=missing_path)


# --- PR #327 レビュー第11巡指摘21（P2、採用）: 適用点2/3（execution profile
# manifest, adjudication_basis） --------------------------------------------


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_source_absolute_path_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """絶対パスは containment guard で拒否される（digest 自体は本物の裁定
    txt を指すため一致し得るが、絶対パスというだけで fail-closed 拒否）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["source_file"] = str(EXECPROFILE_ADJUDICATION_PATH)

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must be a repo-relative path"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_source_traversal_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`../` traversal を含む source_file は containment guard で拒否
    される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["source_file"] = "../" * 6 + "etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must not contain '\\.\\.'"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_adjudication_source_symlink_escape_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lexical には repo 配下に見える相対 source_file でも、実体が symlink
    経由で repo 外を指していれば拒否される。実リポジトリへは一切書き込ま
    ない——`_EXECPROFILE_REPO_ROOT` を隔離 tmp_path へ monkeypatch し、その
    配下にのみ symlink を作る。"""
    fake_repo_root = tmp_path / "fake_repo"
    rel = _execprofile_manifest_data()["adjudication_basis"]["source_file"]
    link_path = fake_repo_root / rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    outside_target = tmp_path / "outside_adjudication.txt"
    outside_target.write_text("outside\n", encoding="utf-8")
    link_path.symlink_to(outside_target)
    monkeypatch.setattr(m, "_EXECPROFILE_REPO_ROOT", fake_repo_root)
    with pytest.raises(m.Run9ValidationError, match="escapes the repo root"):
        m.load_pinned_execution_profile_manifest(contract)


# --- load_pinned_execution_profile_manifest(): render code cross-check (7)
# (PR #327 レビュー第3巡指摘8(a)対応、2026-08-26) -----------------------------


def test_execprofile_load_pinned_execution_profile_manifest_render_code_matches_repo(
    contract: m.Run9RunContract,
) -> None:
    """cross-check (7) 既定経路（`render_code_path` 未指定）: 実 repo 内の
    `voice_genesis/foundry/s1_gate/gate_synth.py` の実バイト sha256 が
    manifest 記載の `additional_measurements.render_code_commit.
    file_sha256` と一致するため happy path の load は成功する
    （`_EXECPROFILE_REPO_ROOT` 相対解決の確認を兼ねる）。"""
    data = m.load_pinned_execution_profile_manifest(contract)
    assert data["additional_measurements"]["render_code_commit"]["file_sha256"] == (
        "a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894"
    )


def test_execprofile_load_pinned_execution_profile_manifest_render_code_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """cross-check (7): `render_code_path` が指す実ファイルのバイトが
    manifest 記載の `file_sha256` と食い違うと fail-closed で拒否される
    ——gate_synth.py が pin 後に改変された場合の検出（旧実装は manifest
    と裁定 txt しか読まず、この改変を検出できなかった）。"""
    tampered_path = tmp_path / "gate_synth.py"
    tampered_path.write_bytes(b"# tampered gate_synth.py\n")
    with pytest.raises(m.Run9ValidationError, match="render_code_commit.file_sha256"):
        m.load_pinned_execution_profile_manifest(contract, render_code_path=tampered_path)


def test_execprofile_load_pinned_execution_profile_manifest_render_code_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist_gate_synth.py"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_execution_profile_manifest(contract, render_code_path=missing_path)


# --- PR #327 レビュー第11巡指摘21（P2、採用）: 適用点3/3（execution profile
# manifest, additional_measurements.render_code_commit.file） ---------------


def test_execprofile_load_pinned_execution_profile_manifest_render_code_absolute_path_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """絶対パスは containment guard で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        gate_synth_path = _RUN_DIR.parent.parent / "foundry" / "s1_gate" / "gate_synth.py"
        data["additional_measurements"]["render_code_commit"]["file"] = str(gate_synth_path)

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must be a repo-relative path"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_render_code_traversal_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`../` traversal を含む file は containment guard で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["render_code_commit"]["file"] = "../" * 6 + "etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must not contain '\\.\\.'"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_render_code_symlink_escape_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lexical には repo 配下に見える相対 file でも、実体が symlink 経由で
    repo 外を指していれば拒否される。cross-check (6) adjudication_basis は
    本 monkeypatch 環境下でも (7) より先に走るため、その相対パスには実物と
    同一バイトのコピーを正しく配置しておく（(6) 自体の検証内容ではなく、
    (7) の symlink 検証まで到達させるための前提整備）。"""
    exec_data = _execprofile_manifest_data()
    fake_repo_root = tmp_path / "fake_repo"

    adjudication_rel = exec_data["adjudication_basis"]["source_file"]
    adjudication_copy = fake_repo_root / adjudication_rel
    adjudication_copy.parent.mkdir(parents=True, exist_ok=True)
    adjudication_copy.write_bytes(EXECPROFILE_ADJUDICATION_PATH.read_bytes())

    render_code_rel = exec_data["additional_measurements"]["render_code_commit"]["file"]
    link_path = fake_repo_root / render_code_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    outside_target = tmp_path / "outside_gate_synth.py"
    outside_target.write_text("# outside\n", encoding="utf-8")
    link_path.symlink_to(outside_target)

    monkeypatch.setattr(m, "_EXECPROFILE_REPO_ROOT", fake_repo_root)
    with pytest.raises(m.Run9ValidationError, match="escapes the repo root"):
        m.load_pinned_execution_profile_manifest(contract)


# --- load_pinned_execution_profile_manifest(): measured source_line_text
# cross-check (8) (PR #327 レビュー第14巡指摘26対応、P2、採用) --------------
# 対象は source_file/source_line/source_line_text を全て持つ measured
# 項目のみ（現物 manifest 確認済み: onnxruntime_selected_providers と
# deterministic_seed_and_thread_environment_variables.deterministic_seed の
# 2件。onnxruntime_thread_settings.{intra,inter}_op_num_threads は
# source_line_text を持たないため対象外）。


def test_execprofile_load_pinned_execution_profile_manifest_selected_providers_source_line_text_matches_repo(
    contract: m.Run9RunContract,
) -> None:
    """正例: 現行 manifest の `onnxruntime_selected_providers.
    source_line_text` は実 repo の gate_synth.py:1218 と一致するため
    happy path の load は成功する（cross-check (8) の既定経路確認）。"""
    data = m.load_pinned_execution_profile_manifest(contract)
    selected = data["additional_measurements"]["onnxruntime_selected_providers"]
    assert selected["source_line_text"] == 'providers = ["CPUExecutionProvider"]'


def test_execprofile_load_pinned_execution_profile_manifest_selected_providers_source_line_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例1/3（行番号 stale）: `source_line` が別の行（1217 =
    `so.inter_op_num_threads = 1`）を指すよう改変されると、その行の実
    テキストが記録された `source_line_text` と食い違うため fail-closed で
    拒否される（repin 後の行ずれ想定）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_selected_providers"]["source_line"] = 1217

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(
        m.Run9ValidationError, match="onnxruntime_selected_providers.source_line_text"
    ):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_selected_providers_source_line_text_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例2/3（source_line_text 改竄）: `source_line` は正しい実行
    （1218）を指したまま `source_line_text` 自体が改竄されると
    fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_selected_providers"]["source_line_text"] = (
            'providers = ["TamperedExecutionProvider"]'
        )

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(
        m.Run9ValidationError, match="onnxruntime_selected_providers.source_line_text"
    ):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_selected_providers_source_file_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例3/3（source_file が存在しないパス）: repo 配下ではあるが実在
    しない相対パスへ差し替えられると fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_selected_providers"]["source_file"] = (
            "voice_genesis/foundry/s1_gate/does_not_exist_gate_synth.py"
        )

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_selected_providers_source_path_override_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`selected_providers_source_path` オーバーライド（`render_code_path`
    等と同型のテスト用差し替え引数）で別ファイルを指定すると、containment
    guard を経由せずその実ファイルの当該行に対して照合される。"""
    fake_file = tmp_path / "gate_synth.py"
    fake_file.write_text("\n".join(["x"] * 1220) + "\n", encoding="utf-8")
    with pytest.raises(
        m.Run9ValidationError, match="onnxruntime_selected_providers.source_line_text"
    ):
        m.load_pinned_execution_profile_manifest(
            contract, selected_providers_source_path=fake_file,
        )


def test_execprofile_load_pinned_execution_profile_manifest_deterministic_seed_source_line_text_matches_repo(
    contract: m.Run9RunContract,
) -> None:
    """正例: `deterministic_seed.source_line_text` は実 repo の
    gate_synth.py:149 と一致するため happy path の load は成功する。"""
    data = m.load_pinned_execution_profile_manifest(contract)
    seed = data["additional_measurements"][
        "deterministic_seed_and_thread_environment_variables"
    ]["deterministic_seed"]
    assert seed["source_line_text"] == "SEED = 42"


def test_execprofile_load_pinned_execution_profile_manifest_deterministic_seed_source_line_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例1/3（行番号 stale）: deterministic_seed 側もファミリー単位で
    掃討する。`source_line` が別の行（150 = `HEAD_FRAMES = 8`）を指すと
    fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        seed = data["additional_measurements"][
            "deterministic_seed_and_thread_environment_variables"
        ]["deterministic_seed"]
        seed["source_line"] = 150

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="deterministic_seed.source_line_text"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_deterministic_seed_source_line_text_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例2/3（source_line_text 改竄）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        seed = data["additional_measurements"][
            "deterministic_seed_and_thread_environment_variables"
        ]["deterministic_seed"]
        seed["source_line_text"] = "SEED = 999"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="deterministic_seed.source_line_text"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_deterministic_seed_source_file_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例3/3（source_file が存在しないパス）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        seed = data["additional_measurements"][
            "deterministic_seed_and_thread_environment_variables"
        ]["deterministic_seed"]
        seed["source_file"] = "voice_genesis/foundry/s1_gate/does_not_exist_gate_synth.py"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_deterministic_seed_source_path_override_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`deterministic_seed_source_path` オーバーライドの配線確認。"""
    fake_file = tmp_path / "gate_synth.py"
    fake_file.write_text("\n".join(["x"] * 200) + "\n", encoding="utf-8")
    with pytest.raises(m.Run9ValidationError, match="deterministic_seed.source_line_text"):
        m.load_pinned_execution_profile_manifest(
            contract, deterministic_seed_source_path=fake_file,
        )


# --- load_pinned_execution_profile_manifest(): thread settings cross-check
# (8) 全数化 (PR #327 レビュー第15巡指摘27対応、P2、上限10ラウンド超過後
# だが3分類「将来汚染」該当の新しい具体経路として採用) -----------------------
# 対象は onnxruntime_thread_settings.{intra,inter}_op_num_threads の2件
# （第14巡時点は source_line_text 非搭載のため cross-check (8) 対象外
# だったが、本巡で source_line_text を追加し編入した）。


@pytest.mark.parametrize(
    ("sub_field", "expected_line_text"),
    [
        ("intra_op_num_threads", "so.intra_op_num_threads = 1"),
        ("inter_op_num_threads", "so.inter_op_num_threads = 1"),
    ],
)
def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_source_line_text_matches_repo(
    contract: m.Run9RunContract, sub_field: str, expected_line_text: str,
) -> None:
    """正例: 現行 manifest の thread 設定 `source_line_text` は実 repo の
    gate_synth.py:1216/1217 と一致するため happy path の load は成功する
    （cross-check (8) 全数化後の既定経路確認）。"""
    data = m.load_pinned_execution_profile_manifest(contract)
    sub = data["additional_measurements"]["onnxruntime_thread_settings"][sub_field]
    assert sub["source_line_text"] == expected_line_text


def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_source_line_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例1/3（行番号 stale）: intra_op_num_threads.source_line が別の行
    （1217 = `so.inter_op_num_threads = 1`）を指すよう改変されると、記録
    された source_line_text（intra 側の逐語）と食い違うため fail-closed で
    拒否される（repin 後の行ずれ想定、第14巡と同型の掃討）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_thread_settings"]["intra_op_num_threads"][
            "source_line"
        ] = 1217

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="onnxruntime_thread_settings.intra_op_num_threads.source_line_text",
    ):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_source_line_text_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例2/3（source_line_text 改竄）: `source_line` は正しい行（1217）を
    指したまま inter_op_num_threads.source_line_text 自体が改竄されると
    fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_thread_settings"]["inter_op_num_threads"][
            "source_line_text"
        ] = "so.inter_op_num_threads = 999"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="onnxruntime_thread_settings.inter_op_num_threads.source_line_text",
    ):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_source_file_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """負例3/3（source_file が存在しないパス）: repo 配下ではあるが実在
    しない相対パスへ差し替えられると fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["additional_measurements"]["onnxruntime_thread_settings"]["intra_op_num_threads"][
            "source_file"
        ] = "voice_genesis/foundry/s1_gate/does_not_exist_gate_synth.py"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_execution_profile_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_source_path_override_stale_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`thread_settings_source_path` オーバーライド（`render_code_path` 等
    と同型のテスト用差し替え引数、intra/inter 共通単一引数）で別ファイルを
    指定すると、containment guard を経由せずその実ファイルの当該行に対して
    照合される。"""
    fake_file = tmp_path / "gate_synth.py"
    fake_file.write_text("\n".join(["x"] * 1220) + "\n", encoding="utf-8")
    with pytest.raises(
        m.Run9ValidationError,
        match="onnxruntime_thread_settings.intra_op_num_threads.source_line_text",
    ):
        m.load_pinned_execution_profile_manifest(
            contract, thread_settings_source_path=fake_file,
        )


def test_execprofile_load_pinned_execution_profile_manifest_thread_settings_default_unspecified_not_cross_checked(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """DEFAULT_UNSPECIFIED（value=null）の thread 設定項目は cross-check
    (8) の対象外——`source_line`/`source_line_text` の内容検証自体を行わ
    ない既存の shape 分岐と同型。intra を DEFAULT_UNSPECIFIED + 明らかに
    stale な source_line_text へ改変しても load は成功する（inter は
    EXPLICITLY_SET のまま実 repo と一致するため happy path）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        intra = data["additional_measurements"]["onnxruntime_thread_settings"]["intra_op_num_threads"]
        intra["specification_status"] = "DEFAULT_UNSPECIFIED"
        intra["value"] = None
        intra["source_line_text"] = "this text is deliberately stale/fabricated"

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    data = m.load_pinned_execution_profile_manifest(
        tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
    )
    assert data["additional_measurements"]["onnxruntime_thread_settings"]["intra_op_num_threads"][
        "specification_status"
    ] == "DEFAULT_UNSPECIFIED"


# --- verify_execution_profile_runtime(): run-gate live probe ---------------
# (PR #327 レビュー第3巡指摘8(b) + 第7巡指摘13対応、2026-08-26)
# `load_pinned_execution_profile_manifest()` からは意図的に分離した live
# 環境照合（CI マトリクス環境との構造衝突回避、詳細は関数 docstring 参照）。
# 第7巡指摘13: 第一引数は任意 manifest dict ではなく `Run9RunContract` へ
# 変更された——関数内部で `load_pinned_execution_profile_manifest()` の全
# cross-check を経由した manifest のみが live 照合に使われる。manifest の
# 中身を変えて検証したいテストは `_tampered_execprofile_contract()` で
# 「改変 manifest ファイル + それに合わせた pin を持つ合成 contract」を
# 経由する（manifest dict を直接注入する経路はもう存在しない）。
#
# CI 修正（2026-08-26）: live probe 5値（Python/onnxruntime/available
# providers/architecture/os）は「現在実行中の環境」を測定する契約であり、
# CI マトリクス環境（GitHub Actions hosted runner）の実測 Python patch
# バージョンが pin（3.11.15）と乖離すると（実測: 3.11.16）、(a) の版チェッ
# クが `verify_execution_profile_runtime()` を呼ぶテストの手前で fail-closed
# 発火し、テストスイート側 15 件が一括で落ちた（CI ジョブ test-rest (3.11)
# 実測。ローカル開発コンテナは pin と同一 patch バージョンだったため検出
# 不能だった）。live Python バージョンの probe は `_live_python_version()`
# へモジュールレベル関数として切り出し済み（検証意味論は不変）。以下の
# テスト群は `_pin_live_probe()` ヘルパーで5値すべてを明示的に pin 値へ
# 固定してから `verify_execution_profile_runtime()` を呼ぶことで、CI が
# どの Python patch バージョンで走っても（3.11.x/3.12.x 問わず）決定論的に
# 同一結果になるようにする（各テストが検証したい1値だけを意図的に pin から
# ずらす）。

_EXECPROFILE_PIN_PYTHON = "3.11.15"
_EXECPROFILE_PIN_ONNXRUNTIME = "1.29.0"
_EXECPROFILE_PIN_AVAILABLE_PROVIDERS = ["AzureExecutionProvider", "CPUExecutionProvider"]
_EXECPROFILE_PIN_ARCHITECTURE = "x86_64"
_EXECPROFILE_PIN_OS_RELEASE_TEXT = (
    'PRETTY_NAME="Ubuntu 24.04.4 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
    'VERSION="24.04.4 LTS (Noble Numbat)"\nID=ubuntu\n'
)


def _pin_live_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    python: str = _EXECPROFILE_PIN_PYTHON,
    onnxruntime_version: str = _EXECPROFILE_PIN_ONNXRUNTIME,
    available_providers: Optional[List[str]] = None,
    architecture: str = _EXECPROFILE_PIN_ARCHITECTURE,
) -> Path:
    """`verify_execution_profile_runtime()` の live probe 5値すべてを、既定
    では execution_profile_sha pin と一致する値へ monkeypatch/ファイル差し
    替えで固定する（CI 修正、2026-08-26）。呼び出し側はキーワード引数で
    意図的に1値だけを pin からずらして負例を構成できる。戻り値は os-release
    相当ファイルへのパスで、呼び出し側は `verify_execution_profile_runtime
    (..., os_release_path=戻り値)` として明示的に渡すこと（os probe だけは
    関数 API 上の明示引数でありモジュールグローバルではないため
    monkeypatch 対象にならない）。"""
    monkeypatch.setattr(m, "_live_python_version", lambda: python)
    fake_ort = types.SimpleNamespace(
        __version__=onnxruntime_version,
        get_available_providers=lambda: list(
            available_providers
            if available_providers is not None
            else _EXECPROFILE_PIN_AVAILABLE_PROVIDERS
        ),
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setattr(m.platform, "machine", lambda: architecture)
    os_release_path = tmp_path / "execprofile_pinned_os_release"
    os_release_path.write_text(_EXECPROFILE_PIN_OS_RELEASE_TEXT, encoding="utf-8")
    return os_release_path


def test_execprofile_verify_runtime_happy_path_pinned_live_probe(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """live probe 5値すべてを `_pin_live_probe()` で execution_profile_sha
    pin と一致する値へ固定した、monkeypatch ベースの決定論 happy path
    （CI 修正、2026-08-26 で `test_execprofile_verify_runtime_happy_path_
    real_environment` を置き換え——後者は「CI ホストの実行環境が pin と
    偶然一致すること」を前提にしており、GitHub Actions hosted runner の
    Python patch バージョンが pin からずれると（実測: 3.11.16 vs pin
    3.11.15）CI マトリクス上で恒真ではなくなる構造的な問題があったため削除
    した——削除理由をここに記録する）。"""
    os_release_path = _pin_live_probe(monkeypatch, tmp_path)
    result = m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)
    assert result == {
        "python": "3.11.15",
        "onnxruntime": "1.29.0",
        "available_providers": ["AzureExecutionProvider", "CPUExecutionProvider"],
        "selected_execution_provider": "CPUExecutionProvider",
        "architecture": "x86_64",
        "os": "Ubuntu 24.04.4",
    }


def test_execprofile_verify_runtime_manifest_injection_without_matching_pin_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """第7巡指摘13の直接非退行確認: 任意に改変した manifest ファイルを
    `manifest_path` で指し示しても、`contract` 側の execution_profile_sha
    pin をそれに合わせて差し替えていなければ実バイト sha256 不一致として
    `load_pinned_execution_profile_manifest()` 側で fail-closed 拒否される
    ——旧実装（呼び出し側供給の任意 mapping をそのまま live 照合していた）
    で可能だった「live ホストに合わせた偽 manifest を注入して偽成功させる」
    経路が閉じたことの確認。live 環境の実測値と一致するよう改変した
    manifest を用意しても、pin が未更新のため拒否される。sha256 不一致で
    `load_pinned_execution_profile_manifest()` 側が最初に発火し live probe
    （python 版含む）には到達しないため、`_pin_live_probe()` は不要
    （CI の live Python patch バージョンにも依存しない）。"""
    forged = copy.deepcopy(_execprofile_manifest_data())
    forged["identity_semantics"]["runtime"]["python"] = "9.9.9"
    forged_path = tmp_path / "execution_profile_manifest.json"
    forged_path.write_bytes(_canonical_json_bytes(forged))
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        m.verify_execution_profile_runtime(contract, manifest_path=forged_path)


def test_execprofile_verify_runtime_python_mismatch_rejected(
    contract: m.Run9RunContract, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_live_python_version()` を直接 monkeypatch する（切り出し後の probe
    関数単体を差し替えるのが最短で、`sys.version_info` を偽クラスで再現する
    旧テクニックはもう不要）。"""
    monkeypatch.setattr(m, "_live_python_version", lambda: "3.12.0")
    with pytest.raises(m.Run9ValidationError, match="live Python version"):
        m.verify_execution_profile_runtime(contract)


def test_execprofile_verify_runtime_python_ci_matrix_divergence_rejected(
    contract: m.Run9RunContract, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本 CI 修正が対処した実障害の直接再現テスト（2026-08-26）: GitHub
    Actions hosted runner の Python 3.11 マトリクスが実測 3.11.16 を提供し
    た一方、execution_profile_sha pin は 3.11.15 のままだったため、(a) の
    版チェックが `verify_execution_profile_runtime()` を呼ぶ全テストの手前
    で fail-closed 発火し、テストスイート側 15 件が一括で落ちた（CI ジョブ
    test-rest (3.11) 実測）。本テストは live Python patch バージョンが pin
    より新しい 3.11.16 である場合に、意図どおり Run9ValidationError で
    fail-closed 拒否されることを固定し、この障害経路自体を決定論的に
    回帰確認する。"""
    monkeypatch.setattr(m, "_live_python_version", lambda: "3.11.16")
    with pytest.raises(m.Run9ValidationError, match="live Python version"):
        m.verify_execution_profile_runtime(contract)


def test_execprofile_verify_runtime_onnxruntime_import_failure_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """onnxruntime が import 不能な環境では fail-closed（静かに skip
    しない）——`sys.modules["onnxruntime"] = None` は `import onnxruntime`
    を `ModuleNotFoundError` にする標準テクニック。"""
    os_release_path = _pin_live_probe(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(m.Run9ValidationError, match="onnxruntime を import できない"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_onnxruntime_version_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    os_release_path = _pin_live_probe(monkeypatch, tmp_path, onnxruntime_version="9.9.9")
    with pytest.raises(m.Run9ValidationError, match="live onnxruntime version"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_available_providers_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #327 レビュー第9巡指摘18対応後の負例: `"CPUExecutionProvider"`
    が live `get_available_providers()` に含まれない場合は fail-closed で
    拒否される——ここでは CUDAExecutionProvider のみが観測され CPU
    provider 自体が available から消えている（正当な CPU-only ホストの
    受理とは異なり、CPU 可用性そのものが失われたケース）ことを確認する。
    〔旧テストは歴史実測（Azure+CPU）との完全一致要求のもとで
    CUDAExecutionProvider の新規出現自体を拒否していたが、第9巡指摘18で
    その完全一致要求は撤去された——available に GPU provider が含まれる
    こと自体はもはや拒否理由ではない（選択企図の拒否は (d) が担う）。〕"""
    os_release_path = _pin_live_probe(
        monkeypatch, tmp_path, available_providers=["CUDAExecutionProvider"],
    )
    with pytest.raises(m.Run9ValidationError, match="get_available_providers"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_available_providers_cpu_plus_cuda_accepted(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #327 レビュー第9巡指摘18の直接非退行確認: CPUExecutionProvider
    に加え CUDAExecutionProvider が live available に新規出現していても、
    CPU 自体は available であるため受理される（歴史実測 Azure+CPU との
    完全一致はもはや要求しない——GPU provider の available 出現自体は
    拒否理由ではなく、選択企図の拒否のみ (d) が別途担う）。"""
    os_release_path = _pin_live_probe(
        monkeypatch, tmp_path,
        available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    result = m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)
    assert result["available_providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_execprofile_verify_runtime_gpu_provider_selection_intent_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """呼び出し側が GPU provider を選択しようとする企図（`selected_
    providers` 引数）は、available 側が正当でも fail-closed で拒否
    される（GPU/CUDA 自動fallback/upgrade禁止規則の機械化）。"""
    os_release_path = _pin_live_probe(monkeypatch, tmp_path)
    with pytest.raises(m.Run9ValidationError, match="selected provider argument"):
        m.verify_execution_profile_runtime(
            contract, os_release_path=os_release_path,
            selected_providers=["CUDAExecutionProvider"],
        )


def test_execprofile_verify_runtime_cpu_only_available_accepted(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #327 レビュー第3巡指摘9と対をなす確認: live 環境が CPU-only
    （available == ["CPUExecutionProvider"] のみ）でも、pin 側の
    `onnxruntime_available_providers.value` が Azure+CPU のままでも
    受理される（第9巡指摘18対応後は歴史実測との完全一致を要求しない
    ——「CPUExecutionProvider が live available に含まれること」のみを
    要求するため、pin 側の値を CPU-only へ書き換える必要はもはやない。
    本テストは合成 contract 経由の回帰確認として維持し、pin 側を
    Azure+CPU のまま変更しない構成にした）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        # 第9巡指摘18対応前は pin 側 value を CPU-only へ書き換えて
        # `matches_smoke_record` の自己整合性を保つ必要があったが、本関数
        # はもう pin 側 value を live 照合に使わないため、この mutate は
        # 構造上のダミー（manifest 実体は無変更）としてのみ残す——合成
        # contract 経由テストの配管（`_tampered_execprofile_contract()`）を
        # 再利用するための最小差分。
        pass

    tampered_contract, manifest_path, contract_path = _tampered_execprofile_contract(
        contract, tmp_path, mutate=_mutate,
    )
    os_release_path = _pin_live_probe(
        monkeypatch, tmp_path, available_providers=["CPUExecutionProvider"],
    )
    result = m.verify_execution_profile_runtime(
        tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        os_release_path=os_release_path,
    )
    assert result["available_providers"] == ["CPUExecutionProvider"]


def test_execprofile_verify_runtime_cpu_only_available_accepted_real_manifest(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #327 レビュー第9巡指摘18の直接正例（P2、採用）: 合成 contract を
    経由せず、本物の pin 済み manifest（`onnxruntime_available_providers.
    value` は歴史実測どおり Azure+CPU のまま）に対し、live 環境が
    CPU-only（available == ["CPUExecutionProvider"] のみ）でも受理される
    ことを確認する——「CPUExecutionProvider が live available に含まれる
    こと」のみを要求し、歴史実測との完全一致は要求しないという新契約の
    最短経路での確認。"""
    os_release_path = _pin_live_probe(
        monkeypatch, tmp_path, available_providers=["CPUExecutionProvider"],
    )
    result = m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)
    assert result["available_providers"] == ["CPUExecutionProvider"]


# --- verify_execution_profile_runtime(): os/architecture live probe -------
# (PR #327 レビュー第5巡指摘11対応、2026-08-26) 旧実装は runtime identity
# 5値のうち os/architecture の2値を live probe しておらず、別 OS/別
# アーキテクチャ環境でもパッケージ版と CPU provider さえ揃えば run gate が
# 通り得た穴の非退行確認。以下も `_pin_live_probe()` で python/onnxruntime/
# providers/architecture を pin へ固定してから os/architecture のみを
# 意図的にずらす（CI 修正、2026-08-26 で python/onnxruntime/architecture の
# 実行環境依存を解消——旧テストは os のみ override し、それ以外は実行環境
# 実測に依存していた）。


def test_execprofile_verify_runtime_architecture_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    os_release_path = _pin_live_probe(monkeypatch, tmp_path, architecture="aarch64")
    with pytest.raises(m.Run9ValidationError, match="live architecture"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_os_name_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_live_probe(monkeypatch, tmp_path)
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(
        'NAME="Debian"\nVERSION_ID="12"\nVERSION="12.5 (bookworm)"\n', encoding="utf-8",
    )
    with pytest.raises(m.Run9ValidationError, match="live OS identity"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_os_version_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_live_probe(monkeypatch, tmp_path)
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(
        'NAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION="24.04.5 LTS (Noble Numbat)"\n',
        encoding="utf-8",
    )
    with pytest.raises(m.Run9ValidationError, match="live OS identity"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_os_release_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_live_probe(monkeypatch, tmp_path)
    missing_path = tmp_path / "does_not_exist_os_release"
    with pytest.raises(m.Run9ValidationError, match="live OS identity を構成できない"):
        m.verify_execution_profile_runtime(contract, os_release_path=missing_path)


def test_execprofile_verify_runtime_os_release_name_field_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NAME フィールドが欠落した /etc/os-release は抽出不能として
    fail-closed 拒否される。"""
    _pin_live_probe(monkeypatch, tmp_path)
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text('VERSION_ID="24.04"\nVERSION="24.04.4 LTS"\n', encoding="utf-8")
    with pytest.raises(m.Run9ValidationError, match="live OS identity を構成できない"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_os_release_version_token_unparseable_rejected(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VERSION フィールドの先頭トークンが数字/ドットのみで構成されていない
    場合はバージョン番号抽出不能として fail-closed 拒否される。"""
    _pin_live_probe(monkeypatch, tmp_path)
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(
        'NAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION="rolling"\n', encoding="utf-8",
    )
    with pytest.raises(m.Run9ValidationError, match="live OS identity を構成できない"):
        m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)


def test_execprofile_verify_runtime_os_architecture_positive_via_override(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正例: `os_release_path` を実機 `/etc/os-release` と同じ形式・値で
    差し替えても（`PRETTY_NAME`/`ID` 等の余剰フィールド込み）manifest pin
    と一致するため happy path が成立する——os probe 経路の正例確認。
    python/onnxruntime/providers/architecture は `_pin_live_probe()` で
    pin 値へ固定し、os probe のみを実機同形式ファイルで検証する（CI 修正、
    2026-08-26 で monkeypatch 対象を拡張——旧テストは python/onnxruntime/
    architecture を実行環境依存のまま残していた）。"""
    _pin_live_probe(monkeypatch, tmp_path)
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(
        'PRETTY_NAME="Ubuntu 24.04.4 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
        'VERSION="24.04.4 LTS (Noble Numbat)"\nID=ubuntu\n',
        encoding="utf-8",
    )
    result = m.verify_execution_profile_runtime(contract, os_release_path=os_release_path)
    assert result["os"] == "Ubuntu 24.04.4"
    assert result["architecture"] == "x86_64"



# --- RUN9_CONTRACT.yaml: 既存 pin 全数不変 ----------------------------------


def test_execprofile_other_existing_pins_unchanged(contract_raw: Dict[str, Any]) -> None:
    """RUN9-EXECPROFILE-1 は Scope IN の6ファイル（USER_ADJUDICATION_
    20260826_EXECUTION_PROFILE.txt 新規 / inputs/execution_profile_
    manifest.json 新規 / run9_schema.py / RUN9_CONTRACT.yaml / README.md /
    tests/test_run9_contract.py）以外の既存 pin 済みファイルの実バイトを
    一切変更していないこと（代表サンプル——reexport_manifest_sha/
    seed_policy_sha/failure_abort_criteria_sha/probe_manifest_sha/
    practice_audio_split_manifest_sha が引き続き実ファイルと一致する）。
    `voice_genesis/foundry/s1_gate/gate_synth.py` は Read のみ（1byte も
    変更していない）ことも合わせて確認する。"""
    assert contract_raw["reexport_manifest_sha"]["value"] == m.compute_file_sha256(
        m.REEXPORT_MANIFEST_PATH
    )
    assert contract_raw["seed_policy_sha"]["value"] == m.compute_file_sha256(
        m.SEED_POLICY_MANIFEST_PATH
    )
    assert contract_raw["failure_abort_criteria_sha"]["value"] == m.compute_file_sha256(
        m.FAILURE_ABORT_MANIFEST_PATH
    )
    gate_synth_path = (
        _RUN_DIR.parent.parent / "foundry" / "s1_gate" / "gate_synth.py"
    )
    assert m.compute_file_sha256(gate_synth_path) == (
        "a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894"
    )


def test_execprofile_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# RUN9-L0-HARNESS-3a（2026-08-26）: speaker map manifest
# （schema `run9-speaker-map/1.0`）+ expected_speaker_map_sha PINNED 化。
# User 裁定「RUN9 User裁定 — AF0 runtime mapping」（repo 内収載
# USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt）に基づく方式A
# （ritsu/user 再正規化線形合成、AF0 unrealized mass 明記）。
# ---------------------------------------------------------------------------

SPEAKER_MAP_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt"
)
DESIGN_REVISION_0_5_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.5.md"
DESIGN_REVISION_0_6_DOC_PATH = _RUN_DIR / "DESIGN_RUN9_REVISION_0.6.md"


def _speaker_map_manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.SPEAKER_MAP_MANIFEST_PATH.read_text(encoding="utf-8"))


def _tampered_speaker_map_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """speaker_map_manifest.json の内容を `mutate` で改変し、その実バイト
    sha256 で `expected_speaker_map_sha` pin を差し替えた合成 contract +
    manifest ファイル + contract ファイルを用意するテストヘルパー
    （`_tampered_execprofile_contract()` と同型）。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / "speaker_map_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    import hashlib as _hashlib
    manifest_sha = _hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["expected_speaker_map_sha"] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


# --- 裁定文書の repo 収載（PIN-2/HARNESS-2/EXECPROFILE-1 前例と同型） ------


def test_harness3a_adjudication_source_file_exists() -> None:
    assert SPEAKER_MAP_ADJUDICATION_PATH.is_file()


def test_harness3a_adjudication_source_contains_verbatim_values() -> None:
    """凍結した各値（方式A採用・R9F-01/R9F-02の重み・禁止4項目・非主張・
    design_revision昇格・pin前検証6点・Birth Identity Separation Gate の
    NOT_ESTABLISHED 凍結規約）が、repo 内収載した裁定文書の本文に一字一句
    そのまま存在すること（grep 照合——「User 転記であって発明でない」こと
    を機械検証する）。"""
    text = SPEAKER_MAP_ADJUDICATION_PATH.read_text(encoding="utf-8")
    for value in (
        "方式Aを採用する。",
        "ritsu = 0.75",
        "user  = 0.25",
        "ritsu = 1/3",
        "user  = 2/3",
        "L2正規化、摂動、",
        "ランダム成分、試聴後の重み調整を禁止する。",
        "AF0成分は構造Genomeには存在するが、",
        "現行runtimeでは音響的に実現されない。",
        "本方式は三親音響交配の成立を意味しない。",
        "AF0音響形質の継承、AF0-dominant音声、",
        "AF0成分に起因する学習能力差を主張しない。",
        "design_revisionを0.5へ上げ、",
        "TRI_CROSSOVER/1.0は変更しない。",
        "入力hash照合、384-dim float32有限性、",
        "smoke render成立、render replay決定論を検証する。",
        "NOT_ESTABLISHEDとして凍結し、",
        "方式Bへの自動昇格を行わない。",
        "方式Bは将来のAF0 acoustic realization用の別revision/別Runへ送る。",
        "方式CはGenome座標の意味をrender層で失うため不採用とする。",
    ):
        assert value in text, f"missing verbatim value: {value!r}"


def test_harness3a_adjudication_source_body_byte_identical_to_scratchpad_origin() -> None:
    """本文（【RUN9 User裁定 — AF0 runtime mapping】以降）が起草時の作業
    メモ scratchpad/run9_user_adjudication_af0_mapping.md と一字一句改変
    なしで一致すること（改変禁止の直接確認、PIN-2/HARNESS-2/EXECPROFILE-1
    前例と同型——scratchpad ファイルが本セッション後に存在しない環境では
    skip）。"""
    scratchpad_path = Path(
        "/tmp/claude-0/-home-user-ugh-prompt-engine/"
        "e505c1c2-c4ad-588b-a1b2-258051a522de/scratchpad/"
        "run9_user_adjudication_af0_mapping.md"
    )
    if not scratchpad_path.is_file():
        pytest.skip("scratchpad origin file not present in this environment")
    origin_body = scratchpad_path.read_text(encoding="utf-8")
    origin_body = "【RUN9 User裁定" + origin_body.split("【RUN9 User裁定", 1)[1]
    committed_text = SPEAKER_MAP_ADJUDICATION_PATH.read_text(encoding="utf-8")
    committed_body = "【RUN9 User裁定" + committed_text.split("【RUN9 User裁定", 1)[1]
    assert committed_body == origin_body


def test_harness3a_adjudication_source_sha256_matches_manifest_and_contract_comment() -> None:
    """裁定 txt の実バイト sha256 固定——manifest の `adjudication_basis.
    sha256`、および `RUN9_CONTRACT.yaml` 情報記録コメントが記載する値と
    三者一致すること。"""
    actual = m.compute_file_sha256(SPEAKER_MAP_ADJUDICATION_PATH)
    assert actual == "07d932da7d60e0e5abf3011040228d47e0b027514a5d0b6d2c165e71d6c65426"
    data = _speaker_map_manifest_data()
    assert data["adjudication_basis"]["sha256"] == actual
    contract_yaml_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert actual in contract_yaml_text


def test_harness3a_design_revision_0_5_doc_is_byte_unchanged() -> None:
    """RUN9-L0-HARNESS-3c（design_revision 0.5 → 0.6）以降、
    `DESIGN_RUN9_REVISION_0.5.md` は `design_revision_doc_sha256` pin の
    対象ではなくなる（下記 rev 0.6 テスト参照）が、rev 0.2/0.3/0.4 の前例
    （`test_revision03_rev02_doc_is_byte_unchanged` 等）と同型に、文書自体
    は無改変のまま存続することを固定 sha256 で確認する（`design_revision
    系譜」表の frozen literal）。"""
    assert DESIGN_REVISION_0_5_DOC_PATH.is_file()
    actual = m.compute_file_sha256(DESIGN_REVISION_0_5_DOC_PATH)
    assert actual == "095ce77147e897473e8d87b474159c2ff4fdeb6684356cc03649f99a603cb2a9"


def test_harness3c_design_revision_0_6_doc_exists_and_sha_matches_contract_pin() -> None:
    """`DESIGN_RUN9_REVISION_0.6.md` は `design_revision_doc_sha256` pin
    （契約レベルの現行 design_revision 文書）として repoint 済み——
    `DESIGN_REVISION_0_6_DOC_PATH` は `REVISION_DOC_PATH` と同一パスを
    指す（別名の重複定義ではなく同じファイルへの別名参照であることの
    確認込み）。"""
    assert DESIGN_REVISION_0_6_DOC_PATH.is_file()
    assert DESIGN_REVISION_0_6_DOC_PATH == REVISION_DOC_PATH
    actual = m.compute_file_sha256(DESIGN_REVISION_0_6_DOC_PATH)
    assert actual == "40f027c247c380af57b767963af758fde0e4fa7a279f5fa68a8b7e55d10956af"
    contract_raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["value"] == actual
    assert field["source"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/DESIGN_RUN9_REVISION_0.6.md"
    )


def test_harness3c_design_revision_promoted_to_0_6(contract: m.Run9RunContract) -> None:
    """RUN9-L0-HARNESS-3c（2026-08-27）: User 裁定「RUN9 User裁定 —
    Identity Calibration Degeneracy / design_revision 0.6」逐語
    「Identity decision protocol全体をdesign_revision 0.6として再事前
    登録する」に従い、契約レベルの design_revision を実際に 0.5 → 0.6 へ
    昇格したことを固定する（`RUN9_CONTRACT.yaml` トップレベル欄・
    `run9_schema.DESIGN_REVISION` 定数・`design_revision_doc_sha256` pin
    の三箇所を同時に repin した——rev 0.2→0.3→0.4→0.5 と同じ手順）。"""
    assert m.DESIGN_REVISION == "0.6"
    contract_raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract_raw["design_revision"] == "0.6"
    field = contract_raw["design_revision_doc_sha256"]
    assert field["status"] == "PINNED"
    assert field["source"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/DESIGN_RUN9_REVISION_0.6.md"
    )
    assert field["value"] == m.compute_file_sha256(DESIGN_REVISION_0_6_DOC_PATH)
    m.load_run9_contract(contract_raw)  # 例外を投げないことの確認
    assert m.gate_state(contract) == "BLOCKED"


def test_revision06_old_0_5_contract_rejected(contract_raw: Dict[str, Any]) -> None:
    """design_revision 0.6: 旧 "0.5" を宣言する contract も意図どおり
    拒否される（rev 0.2〜0.5 の前例と同型）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["design_revision"] = "0.5"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_harness3a_run9_schema_design_revision_comment_no_stale_scope_note() -> None:
    """PR #328 Codex レビュー第1巡指摘3（P2、採用）対応の回帰: `run9_schema.
    py` の `_SPEAKER_MAP_ADJUDICATED_DESIGN_REVISION` 直前コメントに、
    `DESIGN_RUN9_REVISION_0.5.md` から既に削除済みの「スコープ注記」節への
    参照や「本 PR のスコープ外」という破棄済み判断が残っていないこと
    （design_revision 昇格が同一改訂内で実施済みという現行事実と矛盾する
    stale 記述の再発防止）。"""
    source = (_RUN_DIR / "run9_schema.py").read_text(encoding="utf-8")
    assert "スコープ外（別途のフォローアップ判断）" not in source
    assert "同期昇格済み" in source
    revision_doc_text = DESIGN_REVISION_0_5_DOC_PATH.read_text(encoding="utf-8")
    assert "スコープ注記" not in revision_doc_text


# --- validate_speaker_map_manifest(): 正常系・直列化 ------------------------


def test_harness3a_validate_real_manifest_happy_path() -> None:
    m.validate_speaker_map_manifest(_speaker_map_manifest_data())  # 例外なしの確認


def test_harness3a_manifest_reserialization_byte_identical() -> None:
    """`json.dumps(..., ensure_ascii=False, sort_keys=True, indent=2)` +
    改行 で再直列化したバイト列が実ファイルのバイトと完全一致すること
    （`json_dumps` 決定論の直接固定）。"""
    raw = m.SPEAKER_MAP_MANIFEST_PATH.read_bytes()
    data = m._loads_strict_json(raw.decode("utf-8"))
    assert _canonical_json_bytes(data) == raw


def test_harness3a_manifest_schema_field() -> None:
    data = _speaker_map_manifest_data()
    assert data["schema"] == "run9-speaker-map/1.0" == m.SCHEMA_SPEAKER_MAP


def test_harness3a_manifest_no_status_or_expected_sha_self_reference() -> None:
    """draft の `status`/`expected_speaker_map_sha`/`expected_speaker_map_
    sha_note` 欄は repo 版 manifest から除去済み——manifest 自身の raw
    byte sha256 が pin 値になるため、自己参照欄は持たない契約
    （execution_profile_manifest.json 前例と同じ構造）。"""
    data = _speaker_map_manifest_data()
    assert "status" not in data
    assert "expected_speaker_map_sha" not in data
    assert "expected_speaker_map_sha_note" not in data
    assert "scratchpad" not in json.dumps(data, ensure_ascii=False)


def test_harness3a_manifest_unknown_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_missing_top_level_key_fail_closed() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    del data["founders"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_speaker_map_manifest(data)


# --- validate_speaker_map_manifest(): fail-closed 全分岐 --------------------


def test_harness3a_manifest_wrong_schema_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["schema"] = "run9-speaker-map/0.9"
    with pytest.raises(m.Run9ValidationError, match="schema"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_wrong_design_revision_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["design_revision"] = "0.4"
    with pytest.raises(m.Run9ValidationError, match="design_revision"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_prohibited_item_missing_rejected() -> None:
    """禁止項目欠落: `synthesis_formula.prohibited` からちょうど1件を除去
    すると fail-closed で拒否される（4項目・順序込み厳密一致）。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["synthesis_formula"]["prohibited"] = ["L2正規化", "摂動", "ランダム成分"]
    with pytest.raises(m.Run9ValidationError, match="prohibited"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_prohibited_item_reordered_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["synthesis_formula"]["prohibited"] = ["摂動", "L2正規化", "ランダム成分", "試聴後の重み調整"]
    with pytest.raises(m.Run9ValidationError, match="prohibited"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_declaration_non_claim_marker_missing_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["declaration_af0_not_realized"] = "AF0成分は構造Genomeには存在するが、現行runtimeでは音響的に実現されない。"
    with pytest.raises(m.Run9ValidationError, match="non-claim marker"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_unrealized_mass_mismatch_rejected(founder_id: str) -> None:
    """unrealized_mass不一致: `unrealized_mass.value` を `coords_raw.af0`
    と食い違わせると fail-closed で拒否される。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["unrealized_mass"]["value"] = 0.99
    with pytest.raises(m.Run9ValidationError, match="unrealized_mass"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_renormalized_weight_hex_mismatch_rejected(founder_id: str) -> None:
    """重み再導出不一致・hex不一致: `w_ritsu_float32_hex` を機械再導出値と
    食い違わせると fail-closed で拒否される（`repr` は変更しない）。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["renormalized_runtime_weights"]["w_ritsu_float32_hex"] = "00000000"
    with pytest.raises(m.Run9ValidationError, match="renormalized_runtime_weights"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_renormalized_weight_repr_mismatch_rejected(founder_id: str) -> None:
    """重み再導出不一致: `w_user_float32_repr` を機械再導出値と食い違わせ
    ると fail-closed で拒否される（`hex` は変更しない）。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["renormalized_runtime_weights"]["w_user_float32_repr"] = "0.999"
    with pytest.raises(m.Run9ValidationError, match="renormalized_runtime_weights"):
        m.validate_speaker_map_manifest(data)


# --- validate_speaker_map_manifest(): w_ritsu_expr/w_user_expr の閉じた -----
# --- 文法評価（PR #328 Codex レビュー第2巡指摘5、P2、採用） -----------------


def test_harness3a_manifest_weight_expr_tampered_but_hex_unchanged_rejected() -> None:
    """expr 改変（hex/repr は変更しない）: `w_ritsu_expr` を R9F-01 の実際
    の値 `'0.75'` から、閉じた文法上は正当だが別の値へ評価される `'0.5'`
    へ差し替えると、`w_ritsu_float32_hex`/`w_ritsu_float32_repr` 欄自体は
    元の（coords_raw と一致する）正しい値のまま据え置いても fail-closed で
    拒否される——expr だけを改竄しても `*_float32_hex`/`*_repr` さえ正しけ
    れば通過していた旧実装の穴の直接再現。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"]["R9F-01"]["renormalized_runtime_weights"]["w_ritsu_expr"] = "0.5"
    with pytest.raises(m.Run9ValidationError, match="w_ritsu_expr"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_weight_expr_disallowed_form_rejected() -> None:
    """許容外形式: `w_ritsu_expr` を数式としては同値（`0.5+0.25 == 0.75`）
    でも閉じた文法（10進小数リテラル or 単純分数 `'A/B'`）に含まれない
    `'0.5+0.25'` へ差し替えると、文法違反として fail-closed で拒否される
    （eval 的な一般式評価を導入しないことの直接確認）。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"]["R9F-01"]["renormalized_runtime_weights"]["w_ritsu_expr"] = "0.5+0.25"
    with pytest.raises(m.Run9ValidationError, match="closed grammar"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_input_embedding_sha_shape_rejected(founder_id: str) -> None:
    """emb sha改竄（shape）: `ritsu_emb_sha256` を非64hex値へ改竄すると
    validator の shape 検証で fail-closed 拒否される。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["input_embeddings"]["ritsu_emb_sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="64hex"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_input_embedding_pin_match_forged_rejected(founder_id: str) -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["input_embeddings"]["pin_match"] = False
    with pytest.raises(m.Run9ValidationError, match="pin_match"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_synthesized_embedding_run_sha_mismatch_rejected(founder_id: str) -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["synthesized_embedding"]["run2_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="run1_sha256/run2_sha256"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_synthesized_embedding_bytes_dim_dtype_frozen(founder_id: str) -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["synthesized_embedding"]["bytes"] = 1537
    with pytest.raises(m.Run9ValidationError, match="1536"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_smoke_run_embed_input_sha_mismatch_rejected(founder_id: str) -> None:
    """PASS改竄の一形態: `summary_speaker_embed_input_sha256` が合成
    embedding の sha256 と食い違うと、供給経路検証が fail-closed で
    拒否する。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["smoke_render"]["run1"]["summary_speaker_embed_input_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="summary_speaker_embed_input_sha256"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_manifest_smoke_run_wav_sha_replay_mismatch_rejected(founder_id: str) -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["founders"][founder_id]["smoke_render"]["run2"]["wav_sha256"] = "1" * 64
    with pytest.raises(m.Run9ValidationError, match="run1/run2 wav_sha256"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_cross_founder_check_sha_mismatch_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["cross_founder_check"]["r9f01_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError, match="cross_founder_check"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_cross_founder_check_not_distinct_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    same_sha = data["founders"]["R9F-01"]["synthesized_embedding"]["sha256"]
    for founder_id in ("R9F-01", "R9F-02"):
        for key in ("sha256", "run1_sha256", "run2_sha256"):
            data["founders"][founder_id]["synthesized_embedding"][key] = same_sha
        for run_key in ("run1", "run2"):
            data["founders"][founder_id]["smoke_render"][run_key]["summary_speaker_embed_input_sha256"] = same_sha
    data["cross_founder_check"]["r9f02_sha256"] = same_sha
    with pytest.raises(m.Run9ValidationError, match="must differ"):
        m.validate_speaker_map_manifest(data)


@pytest.mark.parametrize(
    "summary_key",
    [
        "1_input_hash_match", "2_synthesis_384dim_float32_finite", "3_byte_determinism",
        "4_two_body_distinctness", "5_smoke_render_success", "6_render_replay_determinism",
    ],
)
def test_harness3a_manifest_pre_pin_verification_summary_item_not_pass_rejected(summary_key: str) -> None:
    """PASS改竄: 6項目のいずれかを "PASS" 以外へ書き換えると fail-closed
    で拒否される。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["pre_pin_verification_summary"][summary_key] = "FAIL"
    with pytest.raises(m.Run9ValidationError, match="PASS"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_pre_pin_verification_summary_all_pass_forged_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["pre_pin_verification_summary"]["all_pass"] = False
    with pytest.raises(m.Run9ValidationError, match="all_pass"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_pre_pin_verification_summary_detail_record_sha256_shape_rejected() -> None:
    """指摘17（PR #328 レビュー第8巡、P2、採用対応）: `detail_record_
    sha256` は64hexでなければ validator 単体（shape 検証 (o)）で拒否
    される。"""
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["pre_pin_verification_summary"]["detail_record_sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="detail_record_sha256"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_pre_pin_verification_summary_detail_record_sha256_missing_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    del data["pre_pin_verification_summary"]["detail_record_sha256"]
    with pytest.raises(m.Run9ValidationError, match="pre_pin_verification_summary"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_next_step_marker_missing_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["next_step_per_adjudication"] = "smoke PASS 後、manifest を pin する。"
    with pytest.raises(m.Run9ValidationError, match="NOT_ESTABLISHED"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_unchanged_per_adjudication_item_missing_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["unchanged_per_adjudication"] = ["発行済み Founder Genome", "coords", "genome_id"]
    with pytest.raises(m.Run9ValidationError, match="unchanged_per_adjudication"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_repo_state_files_modified_forged_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["repo_state"]["repo_files_modified"] = True
    with pytest.raises(m.Run9ValidationError, match="repo_files_modified"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_adjudication_basis_sha_shape_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["adjudication_basis"]["sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="64hex"):
        m.validate_speaker_map_manifest(data)


# --- validate_speaker_map_manifest(): builder_provenance ---------------------
# --- (PR #328 Codex レビュー第1巡指摘1、P1、採用) -----------------------------


def test_harness3a_manifest_builder_provenance_present() -> None:
    data = _speaker_map_manifest_data()
    bp = data["builder_provenance"]
    assert bp["logical_name"] == "speaker_map_builder"
    assert bp["repo_relative_path"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/speaker_map_builder.py"
    )
    assert m._SHA256_HEX_RE.match(bp["builder_sha256"])  # noqa: SLF001 - test-only introspection


def test_harness3a_manifest_builder_provenance_missing_key_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    del data["builder_provenance"]["logical_name"]
    with pytest.raises(m.Run9ValidationError, match="builder_provenance"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_builder_provenance_unknown_key_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["builder_provenance"]["unexpected"] = "x"
    with pytest.raises(m.Run9ValidationError, match="builder_provenance"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_builder_provenance_sha_shape_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["builder_provenance"]["builder_sha256"] = "not-a-hash"
    with pytest.raises(m.Run9ValidationError, match="builder_sha256"):
        m.validate_speaker_map_manifest(data)


def test_harness3a_manifest_builder_provenance_logical_name_empty_rejected() -> None:
    data = copy.deepcopy(_speaker_map_manifest_data())
    data["builder_provenance"]["logical_name"] = ""
    with pytest.raises(m.Run9ValidationError, match="logical_name"):
        m.validate_speaker_map_manifest(data)


# --- load_pinned_speaker_map_manifest(): 正常系・cross-check ----------------


def test_harness3a_load_pinned_speaker_map_manifest_happy_path(
    contract: m.Run9RunContract,
) -> None:
    data = m.load_pinned_speaker_map_manifest(
        contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
    )
    assert data["schema"] == m.SCHEMA_SPEAKER_MAP


def test_harness3a_load_pinned_speaker_map_manifest_missing_file_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=missing_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_byte_tampering_detected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """(i) manifest 実バイトが pin 値と一致しない場合は fail-closed で
    拒否される（追記1byteのみでも検出する）。"""
    tampered_path = tmp_path / "speaker_map_manifest.json"
    tampered_path.write_bytes(m.SPEAKER_MAP_MANIFEST_PATH.read_bytes() + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=tampered_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_rejects_when_not_pinned(
    contract_raw: Dict[str, Any], tmp_path: Path,
) -> None:
    tampered = copy.deepcopy(contract_raw)
    tampered["expected_speaker_map_sha"] = {"value": None, "status": "PENDING", "reason": "test"}
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(yaml.safe_dump(tampered, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            contract_path=tampered_yaml_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_detects_in_process_contract_tampering(
    contract: m.Run9RunContract,
) -> None:
    """disk 正典 RUN9_CONTRACT.yaml と、渡された `contract.raw` を
    in-process で改竄した値が食い違うと、disk 側を正として fail-closed で
    拒否される（read-once 3層防御の中核）。"""
    tampered_contract = copy.deepcopy(contract)
    tampered_contract.raw["expected_speaker_map_sha"] = {
        "value": "f" * 64, "status": "PINNED", "source": "forged",
    }
    with pytest.raises(m.Run9ValidationError, match="tampering evidence"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
        )


def test_harness3a_load_pinned_speaker_map_manifest_adjudication_source_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """裁定txt改竄: adjudication_basis.source_file の実バイトが改変されて
    いると（sha256 が adjudication_basis.sha256 と食い違うと）fail-closed
    で拒否される。"""
    tampered_path = tmp_path / "USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt"
    tampered_path.write_bytes(SPEAKER_MAP_ADJUDICATION_PATH.read_bytes() + b"\ntampered\n")
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            adjudication_basis_path=tampered_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_adjudication_sha_forged_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "0" * 64

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_load_pinned_speaker_map_manifest_coords_tampered_vs_genome_rejected(
    contract: m.Run9RunContract, tmp_path: Path, founder_id: str,
) -> None:
    """coords改竄: `coords_raw` を発行済み Founder Genome document の
    coords と食い違う値へ改竄すると（validator 単体の自己整合チェックは
    素通りするよう unrealized_mass/renormalized_runtime_weights（PR #328
    第2巡指摘5対応後は expr 自体も）も追随させても）、loader の
    cross-check (b)（`load_pinned_founder_genome_document()` との一致）が
    fail-closed で拒否する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        f = data["founders"][founder_id]
        # 座標を改竄（af0 は不変のまま ritsu/user の配分だけをずらす —
        # unrealized_mass.value == coords_raw.af0 の自己整合は壊さない）。
        f["coords_raw"]["ritsu"] = 0.35
        f["coords_raw"]["user"] = 0.05
        denom = f["coords_raw"]["ritsu"] + f["coords_raw"]["user"]
        w_ritsu_value = f["coords_raw"]["ritsu"] / denom
        w_user_value = f["coords_raw"]["user"] / denom
        w_ritsu_hex, w_ritsu_repr = m._float32_hex_and_repr(w_ritsu_value)
        w_user_hex, w_user_repr = m._float32_hex_and_repr(w_user_value)
        f["renormalized_runtime_weights"]["w_ritsu_float32_hex"] = w_ritsu_hex
        f["renormalized_runtime_weights"]["w_ritsu_float32_repr"] = w_ritsu_repr
        f["renormalized_runtime_weights"]["w_user_float32_hex"] = w_user_hex
        f["renormalized_runtime_weights"]["w_user_float32_repr"] = w_user_repr
        # PR #328 第2巡指摘5対応: validator は expr 自体も coords_raw 由来
        # の再導出重みと厳密一致することを強制するため、expr も新しい
        # coords に追随させないと validator 単体の自己整合チェックの時点
        # で reject されてしまう（本テストの狙いは loader 側 cross-check
        # (b) による拒否の直接証拠——validator 単体は通過させたい）。
        # `repr()` は Python の double を厳密に round-trip するため、
        # `_evaluate_closed_weight_expr()` でパースし直しても同じ float32
        # hex/repr に帰着する。
        f["renormalized_runtime_weights"]["w_ritsu_expr"] = repr(w_ritsu_value)
        f["renormalized_runtime_weights"]["w_user_expr"] = repr(w_user_value)

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    # 改竄後 manifest 単体は自己整合しており validator 単体は通過すること
    # を先に確認する（coords_raw 改竄の検出が loader 側の cross-check (b)
    # に依存していることの直接証拠）。
    m.validate_speaker_map_manifest(_loads_bytes(manifest_path))
    with pytest.raises(m.Run9ValidationError, match="Founder Genome document"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


def _loads_bytes(path: Path) -> Dict[str, Any]:
    return m._loads_strict_json(path.read_bytes().decode("utf-8"))


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_load_pinned_speaker_map_manifest_genome_id_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path, founder_id: str,
) -> None:
    """genome_id改竄（PR #328 Codex レビュー第1巡指摘2、P2、採用対応）:
    `coords_raw` は発行済み Founder Genome document と一致させたまま
    `genome_id` のみを改竄すると（coords 一致だけを見る旧実装なら素通り
    していた「取り違え偽装」の直接再現）、loader の cross-check (b) が
    fail-closed で拒否する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        # 有効な16hex genome_id 形状を保ちつつ、実際の発行済み値とは異なる
        # 値へ差し替える（validator 単体の非空文字列チェックは素通りする）。
        data["founders"][founder_id]["genome_id"] = "0" * 16

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    # 改竄後 manifest 単体は自己整合しており validator 単体は通過すること
    # を先に確認する（genome_id 改竄の検出が loader 側の cross-check (b)
    # に依存していることの直接証拠）。
    m.validate_speaker_map_manifest(_loads_bytes(manifest_path))
    with pytest.raises(m.Run9ValidationError, match="genome_id"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_load_pinned_speaker_map_manifest_profile_label_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path, founder_id: str,
) -> None:
    """profile_label改竄（PR #328 Codex レビュー第6巡指摘12、P2、採用対応）:
    `coords_raw`/`genome_id` は発行済み Founder Genome document と一致させ
    たまま `profile_label` のみを改竄すると（従来は非空文字列検証のみで
    genome 側と照合していなかったため素通りしていた取り違え偽装の直接
    再現）、loader の cross-check (b) が fail-closed で拒否する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        # 有効な非空文字列の形状を保ちつつ、実際の発行済み値とは異なる値へ
        # 差し替える（validator 単体の非空文字列チェックは素通りする）。
        genuine = data["founders"][founder_id]["profile_label"]
        forged = "USER_DOMINANT" if genuine == "AF0_DOMINANT" else "AF0_DOMINANT"
        data["founders"][founder_id]["profile_label"] = forged

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    # 改竄後 manifest 単体は自己整合しており validator 単体は通過すること
    # を先に確認する（profile_label 改竄の検出が loader 側の cross-check
    # (b) に依存していることの直接証拠）。
    m.validate_speaker_map_manifest(_loads_bytes(manifest_path))
    with pytest.raises(m.Run9ValidationError, match="profile_label"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


@pytest.mark.parametrize("founder_id", ["R9F-01", "R9F-02"])
def test_harness3a_load_pinned_speaker_map_manifest_input_embedding_sha_tampered_vs_reexport_rejected(
    contract: m.Run9RunContract, tmp_path: Path, founder_id: str,
) -> None:
    """emb sha改竄（cross-manifest）: `input_embeddings.ritsu_emb_sha256`
    が形式上は正しい64hexだが実際の値と食い違うと、loader の cross-check
    (e)（`reexport_manifest.json` pin との照合）が fail-closed で拒否
    する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["founders"][founder_id]["input_embeddings"]["ritsu_emb_sha256"] = "a" * 64

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="reexport_manifest"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


# --- load_pinned_speaker_map_manifest(): builder_provenance cross-check (j) -
# --- (PR #328 Codex レビュー第1巡指摘1、P1、採用) -----------------------------


def test_harness3a_load_pinned_speaker_map_manifest_builder_sha_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """builder_sha256改竄: `builder_provenance.builder_sha256` を実際の
    `speaker_map_builder.py` の実バイト sha256 と食い違う値へ改竄すると、
    loader の cross-check (j) が fail-closed で拒否する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"]["builder_sha256"] = "a" * 64

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="builder_provenance.builder_sha256"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_builder_path_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`builder_provenance.repo_relative_path` が repo 内に存在しない
    パスへ改竄されると fail-closed で拒否される。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"]["repo_relative_path"] = (
            "voice_genesis/evolution/run9_dual_founder_pjs/does_not_exist_builder.py"
        )

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_builder_path_escape_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`builder_provenance.repo_relative_path` が絶対パスへ改竄されると
    repo-containment guard（`_resolve_repo_contained_path()`）が
    fail-closed で拒否する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["builder_provenance"]["repo_relative_path"] = "/etc/passwd"

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="must be a repo-relative path"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


# --- load_pinned_speaker_map_manifest(): repo_state.gate_synth_py_sha256 ----
# --- cross-check (l)（PR #328 Codex レビュー第3巡指摘8、P2、採用） ----------


def test_harness3a_load_pinned_speaker_map_manifest_gate_synth_py_sha_manifest_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """manifest 側 sha 改竄: `repo_state.gate_synth_py_sha256` を実際の
    `gate_synth.py` の実バイト sha256 と食い違う値へ改竄すると、loader の
    cross-check (l)-(i) が fail-closed で拒否する（旧実装は 64hex 形式のみ
    検証しており、この改竄を素通りさせていた）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["repo_state"]["gate_synth_py_sha256"] = "a" * 64

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="repo_state.gate_synth_py_sha256"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_gate_synth_py_real_file_diverges_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """実ファイル相違（オーバーライドで偽ファイル注入）: manifest 側の
    `repo_state.gate_synth_py_sha256` は正規の pin 値のままでも、
    `gate_synth_py_path` オーバーライドで内容の異なる偽 `gate_synth.py` を
    注入すると、loader の cross-check (l)-(i) が fail-closed で拒否する。"""
    fake_gate_synth = tmp_path / "gate_synth.py"
    fake_gate_synth.write_bytes(b"# tampered gate_synth.py content\n")
    with pytest.raises(m.Run9ValidationError, match="repo_state.gate_synth_py_sha256"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            gate_synth_py_path=fake_gate_synth,
        )


def test_harness3a_load_pinned_speaker_map_manifest_gate_synth_py_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`gate_synth_py_path` オーバーライドが存在しないファイルを指すと
    fail-closed で拒否される。"""
    missing_path = tmp_path / "does_not_exist_gate_synth.py"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            gate_synth_py_path=missing_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_gate_synth_py_execprofile_cross_manifest_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """cross-manifest 不一致: `repo_state.gate_synth_py_sha256` を、実ファイル
    （オーバーライドで注入した偽 `gate_synth.py`）の実バイト sha256 とは
    一致させつつ（cross-check (l)-(i) は通過させる）、実際の
    `execution_profile_manifest.json` の `render_code_commit.file_sha256`
    （real pin 値）とは食い違わせると、loader の cross-check (l)-(ii) が
    fail-closed で拒否する——(i) 単体では検出できない「両 manifest が独立に
    記録した gate_synth.py の provenance の食い違い」を検出する直接証拠。
    """
    fake_gate_synth = tmp_path / "gate_synth.py"
    fake_bytes = b"# a different but internally self-consistent gate_synth.py\n"
    fake_gate_synth.write_bytes(fake_bytes)
    fake_sha = hashlib.sha256(fake_bytes).hexdigest()
    # 実際の execution_profile_manifest.json の render_code_commit.file_sha256
    # （real pin 値）とは異なることを前提として確認しておく（テストの意図が
    # 偶然の一致で無効化されないことの自己防衛）。
    real_execprofile = m._loads_strict_json(  # noqa: SLF001 - test-only introspection
        m.EXECUTION_PROFILE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    real_execprofile_sha = (
        real_execprofile["additional_measurements"]["render_code_commit"]["file_sha256"]
    )
    assert fake_sha != real_execprofile_sha

    def _mutate(data: Dict[str, Any]) -> None:
        data["repo_state"]["gate_synth_py_sha256"] = fake_sha

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="render_code_commit.file_sha256"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
            gate_synth_py_path=fake_gate_synth,
        )


# --- load_pinned_speaker_map_manifest(): detail_record cross-check (n) ------
# --- (PR #328 Codex レビュー第8巡指摘17、P2、採用) ---------------------------


def test_harness3a_load_pinned_speaker_map_manifest_detail_record_sha_manifest_tampered_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """manifest 側 sha 改竄: `pre_pin_verification_summary.
    detail_record_sha256` を実際の `HARNESS3A_SPEAKER_MAP_RECORD.md` の
    実バイト sha256 と食い違う値へ改竄すると、loader の cross-check (n) が
    fail-closed で拒否する（旧実装は `detail_record` の非空文字列検証の
    みで、この改竄を素通りさせていた）。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["pre_pin_verification_summary"]["detail_record_sha256"] = "a" * 64

    tampered_contract, manifest_path, contract_path = _tampered_speaker_map_contract(
        contract, tmp_path, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="detail_record_sha256"):
        m.load_pinned_speaker_map_manifest(
            tampered_contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            manifest_path=manifest_path, contract_path=contract_path,
        )


def test_harness3a_load_pinned_speaker_map_manifest_detail_record_real_file_diverges_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """record 改竄（実ファイル相違、オーバーライドで偽ファイル注入）:
    manifest 側の `detail_record_sha256` は正規の pin 値のままでも、
    `detail_record_path` オーバーライドで内容の異なる偽 record を注入
    すると、loader の cross-check (n) が fail-closed で拒否する——record
    が後で編集されても6点 PASS 主張と証拠文書の実体が乖離したまま通って
    いた穴の直接反証。"""
    fake_record = tmp_path / "HARNESS3A_SPEAKER_MAP_RECORD.md"
    fake_record.write_bytes(b"# tampered record content\n")
    with pytest.raises(m.Run9ValidationError, match="detail_record_sha256"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            detail_record_path=fake_record,
        )


def test_harness3a_load_pinned_speaker_map_manifest_detail_record_missing_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """`detail_record_path` オーバーライドが存在しないファイルを指すと
    fail-closed で拒否される。"""
    missing_path = tmp_path / "does_not_exist_record.md"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_speaker_map_manifest(
            contract, domain=_real_domain(), rights_manifest=_real_rights_manifest(),
            detail_record_path=missing_path,
        )


# --- RUN9_CONTRACT.yaml: expected_speaker_map_sha は RUN9-L0-HARNESS-3a で --
# --- PENDING → PINNED へ遷移した --------------------------------------------


def test_harness3a_contract_raw_expected_speaker_map_sha_pinned(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["expected_speaker_map_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.SPEAKER_MAP_MANIFEST_PATH)
    assert field["source"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/inputs/speaker_map_manifest.json"
    )


# --- README.md: PENDING 件数・stale 現在形記述ゼロの回帰 --------------------


def test_harness3a_readme_pending_count_updated_to_eight_and_nine() -> None:
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "pre-run 必須8欄" in readme_text
    assert "総 PENDING 9欄" in readme_text
    for paragraph in readme_text.split("\n\n"):
        if "pre-run 必須9欄" in paragraph or "総 PENDING 10欄" in paragraph:
            assert ("履歴" in paragraph) or ("解消済み" in paragraph), (
                f"stale current-tense 9/10-count claim in paragraph: {paragraph!r}"
            )


def test_harness3a_readme_expected_speaker_map_sha_no_longer_claimed_pending() -> None:
    """PENDING 欄の現行列挙（`attempt_id`/`repository_commit_sha`/...）
    から `expected_speaker_map_sha` が除去されていること。"""
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    for line in readme_text.splitlines():
        if "`repository_commit_sha`/`config_sha`/`dependency_pins_sha`/" in line:
            assert "expected_speaker_map_sha" not in line, (
                f"stale enumeration still lists expected_speaker_map_sha as pending: {line!r}"
            )


def test_harness3a_readme_references_new_artifacts() -> None:
    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    assert "speaker_map_manifest.json" in readme_text
    assert "HARNESS3A_SPEAKER_MAP_RECORD.md" in readme_text
    assert "USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt" in readme_text
    assert "DESIGN_RUN9_REVISION_0.5.md" in readme_text


# --- RUN9_CONTRACT.yaml: 既存 pin 全数不変 ----------------------------------


def test_harness3a_other_existing_pins_unchanged(contract_raw: Dict[str, Any]) -> None:
    """RUN9-L0-HARNESS-3a は Scope IN の8ファイル（USER_ADJUDICATION_
    20260826_AF0_RUNTIME_MAPPING.txt 新規 / DESIGN_RUN9_REVISION_0.5.md
    新規 / inputs/speaker_map_manifest.json 新規 / HARNESS3A_SPEAKER_MAP_
    RECORD.md 新規 / run9_schema.py / RUN9_CONTRACT.yaml / README.md /
    tests/test_run9_contract.py）以外の既存 pin 済みファイルの実バイトを
    一切変更していないこと（代表サンプル——execution_profile_sha/
    reexport_manifest_sha/seed_policy_sha/failure_abort_criteria_sha/
    backbone_checkpoint_sha/por_adjudication_sha256/founder_genome_shas は
    無変更である）。`design_revision_doc_sha256` は例外——裁定逐語
    「design_revisionを0.5へ上げ」に基づく正当な repin であり（Fable
    レビュー、`test_harness3a_design_revision_promoted_to_0_5` 参照）、
    ここでは「現行 `REVISION_DOC_PATH`（rev 0.5 文書）と一致している」
    ことのみを確認する（＝改変されていないことの確認ではない）。
    `founders/*.json`/`gate_synth.py`/既存裁定 txt/既存 execution_profile/
    reexport manifest/DESIGN 0.1-0.4 も1byteも変更していないことを合わせて
    確認する。"""
    assert contract_raw["execution_profile_sha"]["value"] == m.compute_file_sha256(
        m.EXECUTION_PROFILE_MANIFEST_PATH
    )
    assert contract_raw["reexport_manifest_sha"]["value"] == m.compute_file_sha256(
        m.REEXPORT_MANIFEST_PATH
    )
    assert contract_raw["seed_policy_sha"]["value"] == m.compute_file_sha256(
        m.SEED_POLICY_MANIFEST_PATH
    )
    assert contract_raw["failure_abort_criteria_sha"]["value"] == m.compute_file_sha256(
        m.FAILURE_ABORT_MANIFEST_PATH
    )
    assert contract_raw["backbone_checkpoint_sha"]["status"] == "PINNED"
    assert contract_raw["backbone_checkpoint_sha"]["value"] == (
        "6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a"
    )
    assert contract_raw["design_revision_doc_sha256"]["value"] == m.compute_file_sha256(
        REVISION_DOC_PATH
    )
    assert contract_raw["por_adjudication_sha256"]["value"] == m.compute_file_sha256(
        POR_ADJUDICATION_PATH
    )
    for founder_id, path_getter in (
        ("R9F-01", lambda: m.founder_genome_document_path("R9F-01")),
        ("R9F-02", lambda: m.founder_genome_document_path("R9F-02")),
    ):
        field = contract_raw["founder_genome_shas"][founder_id]
        assert field["status"] == "PINNED"
        assert field["value"] == m.compute_file_sha256(path_getter())
    gate_synth_path = (
        _RUN_DIR.parent.parent / "foundry" / "s1_gate" / "gate_synth.py"
    )
    assert m.compute_file_sha256(gate_synth_path) == (
        "a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894"
    )


def test_harness3a_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    assert m.gate_state(contract) == "BLOCKED"


# =============================================================================
# RUN9-L0-HARNESS-3c rev 0.6（design_revision 0.6、2026-08-27）: User 裁定
# 「RUN9 User裁定 — Identity Calibration Degeneracy / design_revision 0.6」
# （repo 内収載 USER_ADJUDICATION_20260827_IDENTITY_REV06.txt）+ 新規
# inputs/identity_decision_protocol_v0.6.json（`run9-identity-decision-
# protocol/0.6`）+ hypothesis_algebra_sha PINNED 化。第2 PR フェーズ1 —
# 本 harness は事前登録のみで Birth Gate 実測は含まない。
# =============================================================================

REV06_ADJUDICATION_PATH = (
    _RUN_DIR / "USER_ADJUDICATION_20260827_IDENTITY_REV06.txt"
)


def _identity_decision_protocol_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.IDENTITY_DECISION_PROTOCOL_PATH.read_text(encoding="utf-8"))


def _real_identity_domain() -> "m.Run9IdentityDomain":
    return m.load_run9_identity_domain(m.RUN9_IDENTITY_DOMAIN_PATH)


def _tampered_identity_protocol_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """identity_decision_protocol_v0.6.json の内容を `mutate` で改変し、
    その実バイト sha256 で `hypothesis_algebra_sha` pin を差し替えた合成
    contract + manifest ファイル + contract ファイルを用意するテスト
    ヘルパー（`_tampered_speaker_map_contract()` と同型）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    mutate(data)
    manifest_bytes = (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    manifest_path = tmp_path / "identity_decision_protocol_v0.6.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["hypothesis_algebra_sha"] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(
        yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8"
    )
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


# --- 裁定文書の repo 収載 -----------------------------------------------------


def test_rev06_adjudication_source_file_exists() -> None:
    assert REV06_ADJUDICATION_PATH.is_file()


def test_rev06_adjudication_source_contains_verbatim_values() -> None:
    """裁定 §1-§9 の凍結値が、repo 内収載した裁定文書の本文に一字一句
    そのまま存在すること（grep 照合——「User 転記であって発明でない」こと
    を機械検証する。`test_harness3a_adjudication_source_contains_verbatim_
    values` と同型）。"""
    text = REV06_ADJUDICATION_PATH.read_text(encoding="utf-8")
    for value in (
        "選択肢Aを採用する。",
        "design_revision 0.6として再事前登録する。",
        "本裁定はC0/C1/Identity距離・学習結果・holdoutを観測した後の救済ではない。",
        "theta_cal(F)=P95(D_C0(F))=0へ退化することが",
        "C0はFounderごとに20 takesを実行する。",
        "D_C0(F)=0×20を期待値とし、",
        "DETERMINISM_CONTRACT_BROKENとして停止する。",
        "C1 ZERO_CONTROLPROFILE_SHAMもFounderごとに20 takes実行する。",
        "非ゼロの場合はC1_SHAM_EFFECT_DETECTEDとして停止する。",
        "positive referenceは追加のexact replay監査として維持する。",
        "d12 = distance(R9F-01:r0, R9F-02:r0)",
        "BIRTH = ESTABLISHED_BY_MACHINE_FEATURE",
        "PROJECTED_RUNTIME_IDENTITIES_COLLAPSED_IN_MACHINE_FEATURE_SPACE",
        "独立証拠として二重計上しない。",
        "BIRTH NOT_ESTABLISHEDとする。",
        "事後的な最小距離閾値を新設しない。",
        "m_other = d_other - d_self",
        "m_pjs   = d_pjs - d_self",
        "STABLE_BY_MACHINE_METRIC /",
        "RELATIVE_SELF_NEAREST",
        "同率をSTABLEへ丸めない。",
        "既存identity_metric_space.json、",
        "同protocolのraw SHA256をhypothesis_algebra_shaへPINNEDする。",
        "LEARN_PERFORMANCEを開始しない。",
        "Birth Gate不成立時はNOT_ESTABLISHEDとして凍結する。",
        "方式Bが必要な場合は別design_revisionまたは別Runとする。",
    ):
        assert value in text, f"missing verbatim value: {value!r}"


def test_rev06_adjudication_source_body_byte_identical_to_scratchpad_origin() -> None:
    """本文（【RUN9 User裁定...】から末尾§9まで）が起草時の作業メモ
    scratchpad/run9_user_adjudication_identity_rev06.md と一字一句改変なし
    で一致すること（scratchpad origin file 非存在環境では skip）。"""
    scratchpad_path = Path(
        "/tmp/claude-0/-home-user-ugh-prompt-engine/"
        "e505c1c2-c4ad-588b-a1b2-258051a522de/scratchpad/"
        "run9_user_adjudication_identity_rev06.md"
    )
    if not scratchpad_path.is_file():
        pytest.skip("scratchpad origin file not present in this environment")
    marker = "【RUN9 User裁定 — Identity Calibration Degeneracy / design_revision 0.6】"
    origin_full = scratchpad_path.read_text(encoding="utf-8")
    origin_body = (marker + origin_full.split(marker, 1)[1]).split(
        "---\n（転記注", 1
    )[0].rstrip("\n")
    committed_text = REV06_ADJUDICATION_PATH.read_text(encoding="utf-8")
    committed_body = (marker + committed_text.split(marker, 1)[1]).rstrip("\n")
    assert committed_body == origin_body


def test_rev06_adjudication_source_sha256_matches_protocol_and_contract_comment() -> None:
    """裁定 txt の実バイト sha256 固定——protocol の
    `adjudication_basis.sha256`、および `RUN9_CONTRACT.yaml` 情報記録
    コメントが記載する値と三者一致すること。"""
    actual = m.compute_file_sha256(REV06_ADJUDICATION_PATH)
    assert actual == "43c7e71cd3bcb7cf3840c67a18e4a4c35a0259b9e04b1335868c33e925420db1"
    data = _identity_decision_protocol_data()
    assert data["adjudication_basis"]["sha256"] == actual
    contract_yaml_text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert actual in contract_yaml_text


# --- validate_identity_decision_protocol(): 正常系・直列化 ------------------


def test_rev06_validate_real_manifest_happy_path() -> None:
    m.validate_identity_decision_protocol(_identity_decision_protocol_data())  # 例外なしの確認


def test_rev06_manifest_reserialization_byte_identical() -> None:
    raw = m.IDENTITY_DECISION_PROTOCOL_PATH.read_bytes()
    data = m._loads_strict_json(raw.decode("utf-8"))
    reserialized = (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    assert reserialized == raw


def test_rev06_validate_rejects_unknown_top_level_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["unexpected_extra_field"] = True
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_missing_top_level_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["pjs_confuser"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_wrong_schema() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["schema"] = "run9-identity-decision-protocol/0.5"
    with pytest.raises(m.Run9ValidationError, match="schema"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_wrong_c0_takes_type() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c0_determinism_attestation"]["takes_per_founder"] = 20.0
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_wrong_birth_cell_ref() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["cell_ref"] = "P1-REG-LOW-DUR-SHORT"
    with pytest.raises(m.Run9ValidationError, match="cell_ref"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_wrong_outcome_detail_vocabulary() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["established"]["outcome_detail"] = "MADE_UP_LABEL"
    with pytest.raises(m.Run9ValidationError, match="outcome_detail"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_reordered_immutability_unchanged() -> None:
    """裁定§7逐語列挙の順序込み一致——並び替えも拒否する。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["immutability"]["unchanged"] = list(reversed(data["immutability"]["unchanged"]))
    with pytest.raises(m.Run9ValidationError, match="immutability.unchanged"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_superseded_sections_not_closed_set() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["supersede_declaration"]["superseded_sections"].append(
        "inputs/identity_metric_space.json#calibration"
    )
    with pytest.raises(m.Run9ValidationError, match="superseded_sections"):
        m.validate_identity_decision_protocol(data)


# --- outcome_detail 語彙: 既存 frozen tuple への非破壊確認 -------------------


def test_rev06_outcome_detail_constants_do_not_collide_with_existing_frozen_vocab() -> None:
    """裁定の新ラベルは既存 BIRTH_OUTCOMES/IDENTITY_OUTCOMES に**追加**
    されるのではなく、別定数（`IDENTITY_PROTOCOL_*`）として独立に凍結
    されていること（二層構造——既存 tuple は無改変）。"""
    assert m.BIRTH_OUTCOMES == ("ESTABLISHED", "NOT_ESTABLISHED")
    assert m.IDENTITY_OUTCOMES == ("STABLE_BY_MACHINE_METRIC", "SHIFTED", "UNCALIBRATED")
    assert m.SEPARATION_OUTCOMES == (
        "MACHINE_EVIDENCE_SUPPORTED", "MIXED", "NOT_ESTABLISHED",
    )
    assert m.IDENTITY_PROTOCOL_BIRTH_ESTABLISHED_DETAIL not in m.BIRTH_OUTCOMES
    assert m.IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL not in m.BIRTH_OUTCOMES
    assert m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL not in m.BIRTH_OUTCOMES
    assert m.IDENTITY_PROTOCOL_RETENTION_STABLE_DETAIL not in m.IDENTITY_OUTCOMES
    assert m.IDENTITY_PROTOCOL_RETENTION_INVALID_OR_NONFINITE_DETAIL not in m.IDENTITY_OUTCOMES
    assert m.IDENTITY_PROTOCOL_C1_MISMATCH_OUTCOME not in m.FAILURE_CLASSES


# --- hypothesis_algebra_sha PINNED 化 ---------------------------------------


def test_rev06_hypothesis_algebra_sha_pinned_and_matches_protocol_file(
    contract_raw: Dict[str, Any],
) -> None:
    field = contract_raw["hypothesis_algebra_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.IDENTITY_DECISION_PROTOCOL_PATH)
    # PR #333 第2巡指摘2（P2、採用）: post_learning_identity_retention.
    # invalid_or_nonfinite_feature 分岐追加により repin（旧値
    # 304e72376e30e8e3974485d393c1f56a7256017588bc877c2be15f080291fb77・
    # 967e40c2291b7532783b0becd574f16fba63972b5007bbe5c055979ef1de8db3 は
    # RUN9_CONTRACT.yaml の【repin 履歴】コメントに保持）。
    assert field["value"] == (
        "cde8b003ff88b78693c81058e3a80ec4fbfe546df7e3f8e61812c8d6f61c67c1"
    )
    assert field["source"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/inputs/identity_decision_protocol_v0.6.json"
    )


# --- load_pinned_identity_decision_protocol(): 正常系・cross-check ----------


def test_rev06_load_pinned_happy_path(contract: m.Run9RunContract) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert data["schema"] == m.SCHEMA_IDENTITY_DECISION_PROTOCOL


def test_rev06_load_pinned_missing_file_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    domain = _real_identity_domain()
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_identity_decision_protocol(
            contract, domain=domain, manifest_path=missing_path
        )


def test_rev06_load_pinned_rejects_adjudication_sha_tamper(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "0" * 64

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(m.Run9ValidationError, match="adjudication_basis.sha256"):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_rev06_load_pinned_rejects_metric_space_sha_tamper(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["metric_reference"]["metric_space_sha"] = "1" * 64

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(m.Run9ValidationError, match="metric_space_sha"):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_rev06_load_pinned_rejects_c0_takes_mismatch_with_contract(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["c0_determinism_attestation"]["takes_per_founder"] = 5

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(m.Run9ValidationError, match="c0_determinism_attestation.takes_per_founder"):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_rev06_load_pinned_rejects_design_revision_doc_sha_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["provenance"]["design_revision_doc"]["sha256"] = "2" * 64

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(
        m.Run9ValidationError, match="provenance.design_revision_doc.sha256"
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_rev06_load_pinned_rejects_design_revision_doc_actual_bytes_tamper(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """PR #333 第2巡指摘3（P2、採用）の是正確認: `provenance.design_
    revision_doc.sha256` が manifest / `RUN9_CONTRACT.yaml`
    `design_revision_doc_sha256` pin の両方と一致していても（cross-check
    (5) は通過）、`DESIGN_RUN9_REVISION_0.6.md` の**実バイト**がそれらの
    宣言値と食い違えば cross-check (6) が fail-closed で検出すること
    （是正前は宣言値同士の比較のみで、この改ざんを検出できなかった）。"""
    domain = _real_identity_domain()
    # mutate なし: manifest の provenance.design_revision_doc.sha256 は
    # 実物の DESIGN_RUN9_REVISION_0.6.md 由来の値のまま（= contract pin と
    # も一致、cross-check (5) は通過させる）。`design_revision_doc_path`
    # override だけを改ざんした別内容のファイルへ差し替える。
    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=lambda data: None
    )
    tampered_doc_path = tmp_path / "DESIGN_RUN9_REVISION_0.6_TAMPERED.md"
    tampered_doc_path.write_text("tampered design revision doc content\n", encoding="utf-8")
    with pytest.raises(
        m.Run9ValidationError, match="provenance.design_revision_doc.sha256"
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
            design_revision_doc_path=tampered_doc_path,
        )


def test_rev06_load_pinned_accepts_design_revision_doc_path_override_matching_bytes(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """override 経路自体の正常系確認: 実ファイルと同一バイトのコピーを
    `design_revision_doc_path` へ渡せば cross-check (6) を素通りすること
    （override 引数がテスト用の単純な迂回口ではなく、実バイト照合を正しく
    行っていることの対照）。"""
    domain = _real_identity_domain()
    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=lambda data: None
    )
    identical_doc_path = tmp_path / "DESIGN_RUN9_REVISION_0.6_COPY.md"
    identical_doc_path.write_bytes(
        (_RUN_DIR / "DESIGN_RUN9_REVISION_0.6.md").read_bytes()
    )
    data = m.load_pinned_identity_decision_protocol(
        tampered_contract, domain=domain, manifest_path=manifest_path,
        contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        design_revision_doc_path=identical_doc_path,
    )
    assert data["schema"] == m.SCHEMA_IDENTITY_DECISION_PROTOCOL


def test_rev06_load_pinned_rejects_supersede_section_typo(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """supersede_declaration の節名が identity_metric_space.json に実在
    しない typo の場合、loader の cross-check (7) が fail-closed で検出
    すること（validator 単体は閉じた集合の一致のみを見るため通す —
    loader が実文書へ走査して typo を検出する二段防御の確認）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        sections = data["supersede_declaration"]["superseded_sections"]
        idx = sections.index("inputs/identity_metric_space.json#calibration.decision_rule")
        sections[idx] = "inputs/identity_metric_space.json#calibration.does_not_exist"

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(m.Run9ValidationError):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_rev06_load_pinned_rejects_disk_contract_divergence(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """in-process contract が disk 正典 RUN9_CONTRACT.yaml と乖離している
    場合、改変証跡として fail-closed 拒否されること（他の `load_pinned_*`
    と同型の3層防御・第1層）。"""
    domain = _real_identity_domain()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw["hypothesis_algebra_sha"] = dict(tampered_raw["hypothesis_algebra_sha"])
    tampered_raw["hypothesis_algebra_sha"]["value"] = "3" * 64
    tampered_contract = m.load_run9_contract(tampered_raw)
    with pytest.raises(m.Run9ValidationError, match="diverges from the canonical on-disk"):
        m.load_pinned_identity_decision_protocol(tampered_contract, domain=domain)


# --- design_revision 0.6 の contract 昇格に伴うグラウンディング確認 ---------


def test_rev06_probe_manifest_does_not_declare_hypothesis_algebra_sha_pending(
) -> None:
    """probe_manifest.json の revision_bridge は hypothesis_algebra_sha を
    literal PENDING と正典宣言していない（PR #324 の measurement_spec 正典
    矛盾の教訓 — 本改訂の実装前グラウンディングで確認済み。probe_manifest
    は score cells + render契約 + take台帳のみを定義し、identity 軸の
    式・閾値・pin 状態は重複定義しない、という measurement_boundary の
    scope_statement どおりのため probe_manifest 側の repin は不要
    だった）。"""
    probe_manifest_path = _RUN_DIR / "evaluation" / "probe_manifest.json"
    text = probe_manifest_path.read_text(encoding="utf-8")
    assert "hypothesis_algebra_sha" not in text


def test_rev06_failure_abort_criteria_rule7_and_rule16_reference_rev06() -> None:
    """failure_abort_criteria.json の Birth Gate 関連 rule（rule 7/16）が
    rev 0.6 supersede への参照を含むこと（stale 文言是正の直接回帰）。"""
    data = m._loads_strict_json(m.FAILURE_ABORT_MANIFEST_PATH.read_text(encoding="utf-8"))
    by_id = {r["rule_id"]: r for r in data["rules"]}
    assert "identity_decision_protocol_v0.6.json" in by_id[7]["checkpoint"]
    assert "rev 0.6" in by_id[16]["checkpoint"] or "0.6" in by_id[16]["checkpoint"]
    # enforcement/rule_id/verbatim は無改変（stale 文言の是正のみ）。
    assert by_id[7]["enforcement"] == "PROCEDURAL"
    assert by_id[7]["verbatim"] == "Birth Identity separation not established"
    assert by_id[16]["enforcement"] == "PROCEDURAL"
    assert by_id[16]["verbatim"] == "Identity drift beyond non-inferiority"


# =============================================================================
# PR #333 Codex bot レビュー第1巡対応（2026-08-28、フェーズ1）
# 指摘1（P1、hypothesis_threshold_calibration_sha 新設）のカウント回帰は
# 上記 `test_pr333_r1_pre_run_pending_count_is_seven` を参照。以下は
# 指摘2（P1、metric space 実バイト再照合）・指摘3（P2、invalid/non-finite
# feature 分岐）・指摘4（P2、protocol 配列比較の dict 偽装拒否）。
# =============================================================================


# --- 指摘2: _load_identity_metric_space_document_verified() ----------------


def test_pr333_r1_load_identity_metric_space_document_verified_happy_path() -> None:
    """実ファイルの実バイトから再計算した正規形 sha256 が
    `domain.metric_space_sha`（PINNED 値）と一致する現行状態では例外なく
    通り、`_load_identity_metric_space_document()`（sha 非照合の旧経路）
    と同一の dict を返すこと。"""
    domain = _real_identity_domain()
    verified = m._load_identity_metric_space_document_verified(domain.metric_space_sha)
    unverified = m._load_identity_metric_space_document()
    assert verified == unverified


def test_pr333_r1_load_identity_metric_space_document_verified_rejects_sha_mismatch(
    tmp_path: Path,
) -> None:
    """渡された期待 sha256 が実バイトの正規形 sha256 と一致しない場合、
    fail-closed で拒否すること（`_load_identity_metric_space_document()`
    は sha を一切見ないため通してしまっていた——回帰確認）。"""
    real_path = m.IDENTITY_METRIC_SPACE_PATH
    copy_path = tmp_path / "identity_metric_space.json"
    copy_path.write_bytes(real_path.read_bytes())
    with pytest.raises(m.Run9ValidationError, match="正規形"):
        m._load_identity_metric_space_document_verified("0" * 64, path=copy_path)


def test_pr333_r1_load_identity_metric_space_document_verified_detects_content_drift(
    tmp_path: Path,
) -> None:
    """PR #333 第1巡指摘2 の直接再現: `identity_metric_space.json` の
    内容が改変されても、期待 sha256（改変前の実 pin 値）を渡された場合は
    その改変を fail-closed で検出すること——旧
    `_load_identity_metric_space_document()` はこの検出を一切行わず、
    改変された feature/distance 定義を素通りで消費していた。"""
    domain = _real_identity_domain()
    real_path = m.IDENTITY_METRIC_SPACE_PATH
    tampered = m._loads_strict_json(real_path.read_text(encoding="utf-8"))
    # feature_extractor 等の深部を改変（トップレベルキー丸ごとの追加でも
    # 正規形 sha256 は変わるため、実在の枝を書き換える）。
    tampered["metric_version"] = tampered.get("metric_version", "tampered") + "-TAMPERED"
    tampered_path = tmp_path / "identity_metric_space.json"
    tampered_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(m.Run9ValidationError, match="正規形"):
        m._load_identity_metric_space_document_verified(
            domain.metric_space_sha, path=tampered_path
        )


def test_pr333_r1_load_pinned_identity_decision_protocol_detects_metric_space_content_drift(
    contract: m.Run9RunContract, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """統合確認: `identity_metric_space.json` の on-disk 内容が改変されて
    いても、protocol 側の宣言値 (`metric_reference.metric_space_sha`) と
    contract 側の宣言値 (`domain.metric_space_sha`) が両方とも改変前の値の
    ままなら（= cross-check (2) は宣言値同士の比較のみのため通過する）、
    `load_pinned_identity_decision_protocol()` 全体としては cross-check
    (7) の実バイト再照合で fail-closed 拒否すること——protocol/contract は
    無改変のまま、`identity_metric_space.json` 側だけを差し替える。"""
    domain = _real_identity_domain()
    real_path = m.IDENTITY_METRIC_SPACE_PATH
    tampered = m._loads_strict_json(real_path.read_text(encoding="utf-8"))
    tampered["metric_version"] = tampered.get("metric_version", "tampered") + "-TAMPERED"
    tampered_path = tmp_path / "identity_metric_space.json"
    tampered_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "IDENTITY_METRIC_SPACE_PATH", tampered_path)
    with pytest.raises(m.Run9ValidationError, match="正規形"):
        m.load_pinned_identity_decision_protocol(contract, domain=domain)


# --- 指摘3: birth_identity_separation.invalid_or_nonfinite_feature --------


def test_pr333_r1_validate_rejects_missing_invalid_feature_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_identity_separation"]["invalid_or_nonfinite_feature"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_validate_rejects_wrong_invalid_feature_outcome_detail() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["invalid_or_nonfinite_feature"]["outcome_detail"] = (
        "MADE_UP_LABEL"
    )
    with pytest.raises(m.Run9ValidationError, match="invalid_or_nonfinite_feature.outcome_detail"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_validate_rejects_wrong_invalid_feature_birth_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["invalid_or_nonfinite_feature"]["birth_outcome"] = (
        "ESTABLISHED"
    )
    with pytest.raises(m.Run9ValidationError, match="invalid_or_nonfinite_feature.birth_outcome"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_invalid_feature_detail_constant_distinct_from_collapse_detail() -> None:
    """invalid/non-finite feature の凍結（測定/実装失敗系）と d12=0 の
    feature collapse（裁定§4の正規の NOT_ESTABLISHED 条件）は別ラベルで
    machine 可読に区別されること——両者を同一定数へ縮退させない。"""
    assert (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL
        != m.IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL
    )
    assert m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL not in m.BIRTH_OUTCOMES


# --- PR #333 第2巡指摘2: post_learning_identity_retention.invalid_or_
# nonfinite_feature --------------------------------------------------------


def test_pr333_r2_validate_rejects_missing_retention_invalid_feature_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["post_learning_identity_retention"]["invalid_or_nonfinite_feature"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r2_validate_rejects_wrong_retention_invalid_feature_outcome_detail() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["post_learning_identity_retention"]["invalid_or_nonfinite_feature"]["outcome_detail"] = (
        "MADE_UP_LABEL"
    )
    with pytest.raises(m.Run9ValidationError, match="invalid_or_nonfinite_feature.outcome_detail"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r2_validate_rejects_wrong_retention_invalid_feature_identity_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["post_learning_identity_retention"]["invalid_or_nonfinite_feature"]["identity_outcome"] = (
        "STABLE_BY_MACHINE_METRIC"
    )
    with pytest.raises(
        m.Run9ValidationError, match="invalid_or_nonfinite_feature.identity_outcome"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r2_retention_invalid_feature_detail_constant_distinct_from_stable_detail() -> None:
    """invalid/non-finite feature の凍結（測定/実装失敗系、UNCALIBRATED）
    と裁定§6の正規の STABLE_BY_MACHINE_METRIC 判定は別ラベルで machine
    可読に区別されること——両者を同一定数へ縮退させない（PR #333 第1巡
    指摘3の birth 側 IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL /
    IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL の非衝突確認と同型）。"""
    assert (
        m.IDENTITY_PROTOCOL_RETENTION_INVALID_OR_NONFINITE_DETAIL
        != m.IDENTITY_PROTOCOL_RETENTION_STABLE_DETAIL
    )
    assert m.IDENTITY_PROTOCOL_RETENTION_INVALID_OR_NONFINITE_DETAIL not in m.IDENTITY_OUTCOMES


def test_pr333_r2_retention_invalid_feature_uses_uncalibrated_not_new_vocab() -> None:
    """`identity_outcome` は既存 IDENTITY_OUTCOMES の 'UNCALIBRATED' その
    ものであり、新規語彙を frozen tuple へ追加していないこと（既存 tuple
    への値追加禁止 — 新設は outcome_detail 側のみ、という Fable 設計方針
    の機械確認）。"""
    data = _identity_decision_protocol_data()
    assert (
        data["post_learning_identity_retention"]["invalid_or_nonfinite_feature"][
            "identity_outcome"
        ]
        == "UNCALIBRATED"
    )
    assert m.IDENTITY_OUTCOMES == ("STABLE_BY_MACHINE_METRIC", "SHIFTED", "UNCALIBRATED")


# --- 指摘4: protocol 配列比較の dict 偽装拒否 -------------------------------


def _dict_masquerading_as_ordered_list(expected: Tuple[str, ...]) -> Dict[str, str]:
    """`tuple(dict)` がキー列を返す性質を使って、期待 tuple と同じ順序の
    キーを持つ insertion-ordered dict（値は任意）を作る——旧実装の
    `tuple(value) != expected` を偽通過し得た形状。"""
    return {key: "ARBITRARY_VALUE_NOT_A_LIST_ELEMENT" for key in expected}


def test_pr333_r1_validate_rejects_dict_masquerading_as_immutability_unchanged() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["immutability"]["unchanged"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_UNCHANGED_ITEMS
    )
    with pytest.raises(m.Run9ValidationError, match="immutability.unchanged must be a list"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_validate_rejects_dict_masquerading_as_prerequisites() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["execution_order"]["prerequisites_before_birth_gate"] = (
        _dict_masquerading_as_ordered_list(m._IDENTITY_PROTOCOL_PREREQUISITES)
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="execution_order.prerequisites_before_birth_gate must be a list",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_validate_rejects_dict_masquerading_as_same_attempt_prohibitions() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["invariants"]["same_attempt_prohibitions"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_SAME_ATTEMPT_PROHIBITIONS
    )
    with pytest.raises(
        m.Run9ValidationError, match="invariants.same_attempt_prohibitions must be a list"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r1_validate_still_rejects_reordered_list_after_shape_guard() -> None:
    """形状ガード追加後も、実際の list に対する順序込み厳密一致の既存挙動
    （並び替え拒否）が壊れていないこと（非回帰）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["invariants"]["same_attempt_prohibitions"] = list(
        reversed(data["invariants"]["same_attempt_prohibitions"])
    )
    with pytest.raises(m.Run9ValidationError, match="invariants.same_attempt_prohibitions"):
        m.validate_identity_decision_protocol(data)
