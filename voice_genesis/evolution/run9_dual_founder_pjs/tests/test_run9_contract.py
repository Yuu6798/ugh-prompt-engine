Y��x-���jם��i��+��j[h��ܢ�������x�}4o+^����ם"""test_run9_contract.py — RUN9 Phase 0 スキャフォールドの最低テスト
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


def�my��$z{-���jם["steps"]
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


# --- preserved_generation_definitions（PR #333 第3巡指摘1、P1、採用）------


def test_rev06_preserved_generation_definitions_matches_bridge_frozen_table() -> None:
    """supersede_declaration.preserved_generation_definitions（実 JSON）
    が、evaluation/probe_manifest.json revision_bridge の凍結表
    （`_REVISION_BRIDGE_EXPECTED_METRIC_REF` の C0/C1/positive/negative
    4エントリ、`_REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES`）から
    導出した閉じた集合と一致すること（single source of truth の確認）。"""
    data = _identity_decision_protocol_data()
    declared = set(data["supersede_declaration"]["preserved_generation_definitions"])
    assert declared == m._IDENTITY_PROTOCOL_PRESERVED_GENERATION_DEFINITIONS
    assert declared == {
        m._REVISION_BRIDGE_EXPECTED_METRIC_REF[name]
        for name in m._REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES
    }
    assert declared == {
        "inputs/identity_metric_space.json#calibration.freeze_threshold.d_c0_population",
        "inputs/identity_metric_space.json#calibration.validity_gates.c1_gate.d_c1_population",
        (
            "inputs/identity_metric_space.json#calibration.validity_gates."
            "negative_reference_gate.negative_reference_definition"
        ),
        (
            "inputs/identity_metric_space.json#calibration.validity_gates."
            "positive_reference_gate.positive_reference_definition"
        ),
    }


def test_rev06_validate_rejects_preserved_generation_definitions_extra_entry() -> None:
    """節丸ごと supersede されている calibration.decision_rule を生成定義
    として紛れ込ませても閉じた集合検査で拒否されること（decision_rule は
    判定式そのものであり生成定義ではない——preserved_generation_
    definitions への混入を防ぐ）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["supersede_declaration"]["preserved_generation_definitions"].append(
        "inputs/identity_metric_space.json#calibration.decision_rule"
    )
    with pytest.raises(m.Run9ValidationError, match="preserved_generation_definitions"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_preserved_generation_definitions_missing_entry() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["supersede_declaration"]["preserved_generation_definitions"].pop()
    with pytest.raises(m.Run9ValidationError, match="preserved_generation_definitions"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_preserved_generation_definitions_missing_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["supersede_declaration"]["preserved_generation_definitions"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_preserved_generation_definitions_note_missing_marker() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["supersede_declaration"]["preserved_generation_definitions_note"] = (
        "この文には要求されるマーカーのどちらも含まれない、無害な平文である。"
    )
    with pytest.raises(
        m.Run9ValidationError, match="preserved_generation_definitions_note"
    ):
        m.validate_identity_decision_protocol(data)


def test_rev06_validate_rejects_preserved_generation_definitions_note_empty() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["supersede_declaration"]["preserved_generation_definitions_note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_rev06_load_pinned_rejects_preserved_generation_definitions_typo(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """preserved_generation_definitions の1件を、同じ節配下だが実在しない
    typo path へ差し替えた場合の fail-closed 拒否（validator の閉じた集合
    検査、または loader cross-check (8) 系の dotted path 実在走査のいずれ
    かで検出される——`test_rev06_load_pinned_rejects_supersede_section_
    typo` と同型）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        items = data["supersede_declaration"]["preserved_generation_definitions"]
        idx = items.index(
            "inputs/identity_metric_space.json#calibration.freeze_threshold.d_c0_population"
        )
        items[idx] = "inputs/identity_metric_space.json#calibration.freeze_threshold.does_not_exist"

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(m.Run9ValidationError):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


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
    # PR #333 第16巡指摘1（P1、上限到達後、採用）: `birth_identity_
    # separation` へ `invalid_or_nonfinite_d12` 分岐を新設し、established
    # 条件へ d12 の finite 性要求を追加したため repin（旧値
    # e536845d424a3dc32b9f6e61f0e5028ffc7b0f65cea1e8da1fedb129699b6e18・
    # c10e4701677a285f36cb99823c83388da067a54e838f27c066c5b7e8c1110e03・
    # cf149cd5d897533d105f83523d23cfc8a8647ec5d6b72cb84e1fc5e395c7f887・
    # 027e3c04ff2978572e9e43ccfdae7314b2171a67f4536ae6a3a0c537153d1b25・
    # 2e47c7d6f093add787159d1a6325b70d308146280a3e8f40abdc08e1b10e59cd・
    # f626e309d187177800d33afabe6c81537faa3c59a5432e080f88e0d4854f1778・
    # 7525cd5ef484bfd94a234f25b44a48368d2f1607f334de1b868863c1bd133f4a・
    # cde8b003ff88b78693c81058e3a80ec4fbfe546df7e3f8e61812c8d6f61c67c1・
    # 304e72376e30e8e3974485d393c1f56a7256017588bc877c2be15f080291fb77・
    # 967e40c2291b7532783b0becd574f16fba63972b5007bbe5c055979ef1de8db3 は
    # RUN9_CONTRACT.yaml の【repin 履歴】コメントに保持）。
    assert field["value"] == (
        "f3caa566718f435d5fcf5f7408ed085194dea73b9f276d5d1e1576f498f4e04e"
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
    """probe_manifest.json のいかなる箇所も hypothesis_algebra_sha を
    literal PENDING と正典宣言していない（PR #324 の measurement_spec 正典
    矛盾——PINNED 済み欄が別正典で PENDING と主張される欠陥パターン——の
    再発防止）。

    〔履歴: 実装前グラウンディング（design_revision 0.6 着手時点）では
    revision_bridge が hypothesis_algebra_sha という文字列を一切含んで
    いなかった（probe_manifest 側の repin 不要と判定）。PR #333 第9巡
    指摘（P1、採用）で measurement_boundary.identity_axis_source/
    scope_statement へ「calibration・閾値・判定規則は rev 0.6 実行に
    ついて identity_decision_protocol_v0.6.json が正本（hypothesis_
    algebra_sha としてpin済み）」という二元宣言を追加したため、以後は
    文字列が出現する——ただし PINNED 状態を正しく宣言しており、PR #324 が
    禁じた「PENDING と偽る」矛盾ではない。本テストはその区別を機械的に
    強制する（絶対不在ではなく PENDING 併記の不在を検査する）よう改訂
    した。〕"""
    probe_manifest_path = _RUN_DIR / "evaluation" / "probe_manifest.json"
    text = probe_manifest_path.read_text(encoding="utf-8")
    if "hypothesis_algebra_sha" in text:
        # PR #324 型の正典矛盾（PENDING と偽る併記）だけを禁止する——
        # PINNED であることの正しい宣言（本改訂で追加）は許容する。同一文
        # （句点区切り、80文字以内の近傍）内で「PENDING」を主張していない
        # ことを機械的に確認する。
        for hit in re.finditer("hypothesis_algebra_sha[^。]{0,80}", text):
            assert "PENDING" not in hit.group(0)
        assert "hypothesis_algebra_shaとしてpin済み" in text


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
# PR #333 Codex bot レビュー第14巡対応（2026-08-28、フェーズ1、採否上限
# 10巡到達後 — 3分類「将来汚染」の新規具体経路〔第12巡対応自身が残した
# 欠陥〕として採用）
# 指摘1（P2）: rule 7 machine_promotion_condition が
# `birth_gate_aggregate_rule` に `completion_evidence_requirement`
# （実際には兄弟節 `birth_gate_overall_pass` 配下にのみ存在）を含めて
# 誤記述していたため、outcome 写像＝`birth_gate_aggregate_rule`／
# 最終 gate 判定＝`birth_gate_overall_pass` の両節参照へ訂正した。
# =============================================================================


def test_pr333_r14_rule7_machine_promotion_condition_references_both_gate_sections() -> None:
    """第14巡指摘1 の直接回帰: rule 7 machine_promotion_condition が
    outcome 写像（identity_establishment 層）を `birth_gate_aggregate_rule`
    へ、最終 gate 判定（completion_evidence_requirement を含む）を
    `birth_gate_overall_pass` へ、それぞれ実キー構成と一致する形で
    参照していること——是正前は前者1節のみへ両方の役割を誤って束ねて
    いた（completion_evidence_requirement は後者配下にのみ実在）。"""
    data = m._loads_strict_json(m.FAILURE_ABORT_MANIFEST_PATH.read_text(encoding="utf-8"))
    by_id = {r["rule_id"]: r for r in data["rules"]}
    condition = by_id[7]["machine_promotion_condition"]
    assert "birth_gate_aggregate_rule" in condition
    assert "birth_gate_overall_pass" in condition
    assert "completion_evidence_requirement" in condition
    assert "audit_stop_refs" in condition
    # 是正前の誤記述（completion_evidence_requirement を
    # birth_gate_aggregate_rule の括弧内に同梱）は履歴〔...〕として
    # append-only 保持するが、現行 (ii) 節本体では両節を分離参照する
    # ことを確認する（第14巡履歴ブロック自体には旧文言が残るため、
    # 履歴ブロックを除いた本体側の分離を直接照合する）。
    active_condition = condition.split("〔履歴:", 1)[0]
    assert "birth_gate_aggregate_rule" in active_condition
    assert "birth_gate_overall_pass" in active_condition
    assert "completion_evidence_requirement" in active_condition
    # enforcement/rule_id/verbatim・分類は無改変。
    assert by_id[7]["enforcement"] == "PROCEDURAL"
    assert by_id[7]["verbatim"] == "Birth Identity separation not established"
    protocol_data = m._loads_strict_json(
        m.IDENTITY_DECISION_PROTOCOL_PATH.read_text(encoding="utf-8")
    )
    # completion_evidence_requirement は実際に birth_gate_overall_pass
    # 配下にのみ存在し、birth_gate_aggregate_rule 配下には存在しない
    # ことを実キー構成で直接確認する（誤記述の再発防止）。
    assert "completion_evidence_requirement" in protocol_data["birth_gate_overall_pass"]
    assert "completion_evidence_requirement" not in protocol_data["birth_gate_aggregate_rule"]


def test_pr333_r9_canonical_source_declarations_reference_rev06_supersede() -> None:
    """PR #333 第9巡指摘（P1、採用）の直接回帰: 宣言文レベルの正典表明
    （probe_manifest.json measurement_boundary / measurement_spec_
    manifest.json scope_note）のいずれも、calibration・閾値・判定規則の
    現行正本が rev 0.6 実行について identity_decision_protocol_v0.6.json
    へ supersede 済みであることに言及していること（feature/distance 生成
    定義側は identity_metric_space.json のまま正本であることも両立して
    言及していること）。第2巡是正はエントリ単位の参照付け替えに留まり、
    この宣言文レベルの現在形主張自体は rev 0.6 以前のまま取り残されて
    いた（本テストが直接照合する対象）。"""
    probe_data = m._loads_strict_json(m.PROBE_MANIFEST_PATH.read_text(encoding="utf-8"))
    identity_axis_source = probe_data["measurement_boundary"]["identity_axis_source"]
    scope_statement = probe_data["measurement_boundary"]["scope_statement"]
    for text in (identity_axis_source, scope_statement):
        assert "inputs/identity_metric_space.json" in text
        assert "identity_decision_protocol_v0.6.json" in text
        assert "supersede" in text

    spec_data = m._loads_strict_json(m.MEASUREMENT_SPEC_MANIFEST_PATH.read_text(encoding="utf-8"))
    scope_note = spec_data["scope_note"]
    assert "inputs/identity_metric_space.json" in scope_note
    assert "identity_decision_protocol_v0.6.json" in scope_note
    assert "supersede" in scope_note

    readme_text = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    # README のプローズ2箇所（probe_manifest.json 7成果物の記述 /
    # measurement_spec_manifest.json extractor カタログの記述）双方が
    # 同型の二元宣言へ追随していること。
    assert readme_text.count("identity_decision_protocol_v0.6.json` が正本（supersede、") >= 1
    assert "calibration・閾値・判定規則は rev 0.6 実行について" in readme_text


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


# =============================================================================
# PR #333 Codex bot レビュー第4巡対応（2026-08-28、フェーズ1）
# 指摘1（P1、`birth_gate_aggregate_rule` 新設）・指摘2（P1、
# `positive_reference_audit.on_mismatch` 新設）。両者とも新規則の発明では
# なく裁定§4/§5/§9・§3+§9 の機械符号化——既存 established/not_established/
# invalid_or_nonfinite_feature/pjs_confuser/c0_determinism_attestation の
# 各分岐は無改変のまま参照するのみ。
# =============================================================================


# --- 指摘1: birth_gate_aggregate_rule ---------------------------------------


def test_pr333_r4_validate_real_manifest_happy_path_with_aggregate_rule() -> None:
    m.validate_identity_decision_protocol(_identity_decision_protocol_data())  # 例外なしの確認


def test_pr333_r4_aggregate_rule_established_reuses_existing_birth_outcomes() -> None:
    """新設節は既存 BIRTH_OUTCOMES/outcome_detail 定数を再利用するのみで、
    新規語彙を frozen tuple へ追加していないこと。"""
    data = _identity_decision_protocol_data()
    aggregate = data["birth_gate_aggregate_rule"]
    assert aggregate["established"]["birth_outcome"] == "ESTABLISHED"
    assert aggregate["established"]["birth_outcome"] in m.BIRTH_OUTCOMES
    assert aggregate["established"]["outcome_detail"] == m.IDENTITY_PROTOCOL_BIRTH_ESTABLISHED_DETAIL
    assert aggregate["not_established"]["birth_outcome"] == "NOT_ESTABLISHED"
    assert aggregate["not_established"]["birth_outcome"] in m.BIRTH_OUTCOMES
    assert m.BIRTH_OUTCOMES == ("ESTABLISHED", "NOT_ESTABLISHED")


def test_pr333_r4_aggregate_rule_verbatim_basis_matches_pjs_confuser_verbatim() -> None:
    """verbatim_basis は pjs_confuser.verbatim（裁定§5 逐語）と単一の正本を
    共有する——実データで一致確認。"""
    data = _identity_decision_protocol_data()
    assert (
        data["birth_gate_aggregate_rule"]["verbatim_basis"]
        == data["pjs_confuser"]["verbatim"]
    )
    assert data["pjs_confuser"]["verbatim"] == (
        "distance=0の場合はPJS confuserとのfeature collapseとしてBIRTH NOT_ESTABLISHEDとする。"
    )


def test_pr333_r4_validate_rejects_missing_aggregate_rule_top_level_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_aggregate_rule"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_verbatim_basis_mismatch() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["verbatim_basis"] = "改ざんされた逐語"
    with pytest.raises(m.Run9ValidationError, match="verbatim_basis"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_established_wrong_outcome_detail() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["established"]["outcome_detail"] = "MADE_UP_LABEL"
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_aggregate_rule.established.outcome_detail"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_conjunct_refs_reordered() -> None:
    """conjunct_refs は裁定 §4/§5 参照節の順序込み逐語列挙——並び替えも
    拒否する（他の3系列の frozen tuple 検査と同型）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["conjunct_refs"] = list(
        reversed(data["birth_gate_aggregate_rule"]["conjunct_refs"])
    )
    with pytest.raises(m.Run9ValidationError, match="birth_gate_aggregate_rule.conjunct_refs"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_conjunct_refs_extra_entry() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["conjunct_refs"].append("pjs_confuser.metric")
    with pytest.raises(m.Run9ValidationError, match="birth_gate_aggregate_rule.conjunct_refs"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_conjunct_refs_dict_masquerade() -> None:
    """指摘4（第1巡）と同型の dict 偽装拒否——本新設フィールドにも同じ
    形状ガードが効いていること。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["conjunct_refs"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_BIRTH_GATE_CONJUNCT_REFS
    )
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_aggregate_rule.conjunct_refs must be a list"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_priority_order_reordered() -> None:
    """outcome_detail_priority.order は決定論的優先順（(1) validity →
    (2) d12=0 → (3) PJS confuser distance=0）——並び替えも拒否する。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["order"] = list(reversed(priority["order"]))
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_aggregate_rule.not_established.outcome_detail_priority.order",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_priority_detail_by_key_tamper() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["detail_by_key"]["d12_zero_collapse"] = "MADE_UP_LABEL"
    with pytest.raises(
        m.Run9ValidationError,
        match=(
            "birth_gate_aggregate_rule.not_established.outcome_detail_priority.detail_by_key."
            "d12_zero_collapse"
        ),
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_priority_detail_by_key_unknown_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["detail_by_key"]["extra_unregistered_key"] = "SOMETHING"
    with pytest.raises(
        m.Run9ValidationError,
        match="outcome_detail_priority.detail_by_key has unknown key",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_aggregate_rule_gate_failure_action_ref_tamper() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["gate_failure_action_ref"] = "invariants.escape_hatch"
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_aggregate_rule.gate_failure_action_ref"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_aggregate_rule_priority_details_distinct_from_established_detail() -> None:
    """not_established 側の3ラベルは established 側ラベルとも互いとも
    衝突しない、既存 BIRTH_OUTCOMES へも追加されていないこと。"""
    established = m.IDENTITY_PROTOCOL_BIRTH_ESTABLISHED_DETAIL
    details = (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL,
        m.IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL,
        m.IDENTITY_PROTOCOL_BIRTH_PJS_CONFUSER_COLLAPSE_DETAIL,
    )
    assert len(set(details)) == 3
    assert established not in details
    for detail in details + (established,):
        assert detail not in m.BIRTH_OUTCOMES


def test_pr333_r4_aggregate_rule_pjs_confuser_section_byte_unchanged() -> None:
    """既存 pjs_confuser.on_zero/on_positive は第4巡改訂で無改変（1 byte も
    変更しない）—— on_zero に outcome_detail 等の新フィールドを追加して
    いないことの直接確認。pjs_confuser 自体のトップレベル key set は第8巡
    指摘3（P2、採用）で `invalid_or_nonfinite_distance` を新設したため、
    第4巡時点のクローズドセット（5項目）から6項目へ伸長している——本
    テストの対象は on_zero/on_positive の無改変確認に限定し、トップレベル
    key set は成長を許容する（新設分岐の追加は「既存分岐の無改変」の範囲
    外）。"""
    data = _identity_decision_protocol_data()
    assert set(data["pjs_confuser"].keys()) == {
        "verbatim", "metric", "pjs_reference_ref", "on_zero", "on_positive",
        "invalid_or_nonfinite_distance",
    }
    assert set(data["pjs_confuser"]["on_zero"].keys()) == {
        "condition", "birth_outcome", "reason",
    }
    assert set(data["pjs_confuser"]["on_positive"].keys()) == {"policy"}


# --- 指摘2: positive_reference_audit.on_mismatch ----------------------------


def test_pr333_r4_positive_reference_audit_on_mismatch_reuses_c0_vocabulary() -> None:
    """新設 on_mismatch は C0 側の停止語彙定数をそのまま再利用しているこ
    と（新語彙の発明はしない、という Fable 設計方針の機械確認）。"""
    data = _identity_decision_protocol_data()
    on_mismatch = data["positive_reference_audit"]["on_mismatch"]
    assert on_mismatch["wav_byte_mismatch"] == m.IDENTITY_PROTOCOL_C0_RENDER_MISMATCH_OUTCOME
    assert (
        on_mismatch["distance_nonzero_or_feature_mismatch_with_matching_wav"]
        == m.IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME
    )
    c0_on_mismatch = data["c0_determinism_attestation"]["on_mismatch"]
    assert on_mismatch["wav_byte_mismatch"] == c0_on_mismatch["render_byte_mismatch"]
    assert (
        on_mismatch["distance_nonzero_or_feature_mismatch_with_matching_wav"]
        == c0_on_mismatch["feature_computation_mismatch_with_matching_render"]
    )


def test_pr333_r4_validate_rejects_missing_positive_reference_on_mismatch_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["positive_reference_audit"]["on_mismatch"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_positive_reference_wav_byte_mismatch_wrong_value() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["positive_reference_audit"]["on_mismatch"]["wav_byte_mismatch"] = "IMPLEMENTATION_FAILURE"
    with pytest.raises(
        m.Run9ValidationError, match="positive_reference_audit.on_mismatch.wav_byte_mismatch"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_positive_reference_distance_mismatch_wrong_value() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["positive_reference_audit"]["on_mismatch"][
        "distance_nonzero_or_feature_mismatch_with_matching_wav"
    ] = "DETERMINISM_CONTRACT_BROKEN"
    with pytest.raises(
        m.Run9ValidationError,
        match=(
            "positive_reference_audit.on_mismatch.distance_nonzero_or_feature_mismatch_with_"
            "matching_wav"
        ),
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_positive_reference_on_mismatch_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["positive_reference_audit"]["on_mismatch"]["gate_effect"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_validate_rejects_positive_reference_on_mismatch_empty_note() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["positive_reference_audit"]["on_mismatch"]["note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r4_c0_c1_sections_byte_unchanged_by_positive_reference_fix() -> None:
    """C0/C1 側の停止語彙割当ては本改訂で無改変（再利用のみで書き換えて
    いないことの直接確認）。"""
    data = _identity_decision_protocol_data()
    assert data["c0_determinism_attestation"]["on_mismatch"] == {
        "render_byte_mismatch": "DETERMINISM_CONTRACT_BROKEN",
        "feature_computation_mismatch_with_matching_render": "IMPLEMENTATION_FAILURE",
    }
    assert data["c1_sham_attestation"]["on_nonzero"] == "C1_SHAM_EFFECT_DETECTED"


# --- load_pinned_identity_decision_protocol(): 第4巡フィールドの一貫性 ------


def test_pr333_r4_load_pinned_happy_path_with_new_sections(
    contract: m.Run9RunContract,
) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert data["schema"] == m.SCHEMA_IDENTITY_DECISION_PROTOCOL
    assert "birth_gate_aggregate_rule" in data
    assert "on_mismatch" in data["positive_reference_audit"]


def test_pr333_r4_load_pinned_rejects_aggregate_rule_tamper_via_hash_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """新設節を改ざんした合成 manifest は、manifest 実バイト sha256 が
    pin 済み `hypothesis_algebra_sha` と食い違うため fail-closed で拒否
    されること（他の tamper 系テストと同型の入口——validator 単体の拒否は
    上記の専用テストで、loader 経路はこの sha 不一致経路で検出される）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["birth_gate_aggregate_rule"]["gate_failure_action_ref"] = "invariants.escape_hatch"

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    # _tampered_identity_protocol_contract() は改ざん後バイトへ hypothesis_
    # algebra_sha を追随させるため、この経路では validate() 自体が拒否する
    # （上記 test_pr333_r4_validate_rejects_aggregate_rule_gate_failure_
    # action_ref_tamper と同一検出点、loader 経由での再確認）。
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_aggregate_rule.gate_failure_action_ref"
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


# =============================================================================
# PR #333 Codex bot レビュー第5巡対応（2026-08-28、フェーズ1）
# 指摘1（P1、`birth_gate_aggregate_rule.conjunct_refs` の排他ペア欠陥是正
# — 第4巡実装の欠陥）・指摘2（P1、`c1_sham_attestation.on_wav_byte_
# mismatch` 新設）・指摘3（P1、`birth_gate_overall_pass` 新設・二層分離）。
# いずれも新規則の発明ではなく裁定§4/§5/§9・§2/§9・§8 の機械符号化——既存
# established/invalid_or_nonfinite_feature/not_established/on_positive/
# on_zero/on_nonzero の各分岐は無改変のまま参照するのみ。
# =============================================================================


# --- 指摘1: birth_gate_aggregate_rule.conjunct_refs 是正 --------------------


def test_pr333_r5_validate_real_manifest_happy_path_with_r5_fields() -> None:
    m.validate_identity_decision_protocol(_identity_decision_protocol_data())  # 例外なしの確認


def test_pr333_r5_conjunct_refs_now_two_success_predicates_only() -> None:
    """第4巡実装の4項目（排他ペア2組）から、成功述語のみの2項目へ是正
    されたこと——実データで直接確認する。"""
    data = _identity_decision_protocol_data()
    conjunct_refs = data["birth_gate_aggregate_rule"]["conjunct_refs"]
    assert conjunct_refs == [
        "birth_identity_separation.established",
        "pjs_confuser.on_positive",
    ]
    assert tuple(conjunct_refs) == m._IDENTITY_PROTOCOL_BIRTH_GATE_CONJUNCT_REFS


def test_pr333_r5_conjunct_refs_and_failure_refs_disjoint_no_exclusive_pair() -> None:
    """指摘1 是正の直接確認（敵対的自己検査 (d)）: conjunct_refs と
    failure_refs が互いに素であり、`birth_identity_separation.*`／
    `pjs_confuser.*` それぞれについて conjunct_refs 側に1項目のみが
    存在すること——第4巡実装のように同じ問いの成立・不成立を同時に
    conjunct_refs へ要求する排他ペアが存在しないことの機械証明。"""
    data = _identity_decision_protocol_data()
    agg = data["birth_gate_aggregate_rule"]
    conjunct = set(agg["conjunct_refs"])
    failure = set(agg["not_established"]["outcome_detail_priority"]["failure_refs"])
    assert conjunct.isdisjoint(failure)

    birth_conjuncts = {r for r in conjunct if r.startswith("birth_identity_separation.")}
    birth_failures = {r for r in failure if r.startswith("birth_identity_separation.")}
    assert birth_conjuncts == {"birth_identity_separation.established"}
    assert birth_conjuncts.isdisjoint(birth_failures)

    pjs_conjuncts = {r for r in conjunct if r.startswith("pjs_confuser.")}
    pjs_failures = {r for r in failure if r.startswith("pjs_confuser.")}
    assert pjs_conjuncts == {"pjs_confuser.on_positive"}
    assert pjs_conjuncts.isdisjoint(pjs_failures)


def test_pr333_r5_failure_refs_matches_frozen_tuple_ordered() -> None:
    """PR #333 第8巡指摘3（P2、採用）で `pjs_confuser.invalid_or_nonfinite_
    distance` 分岐追加に伴い failure_refs/order は3項目→4項目へ伸長、
    第16巡指摘1（P1、上限到達後、採用）で `birth_identity_separation.
    invalid_or_nonfinite_d12` 分岐追加に伴い4項目→5項目へ再伸長した
    （テスト名はレビュー履歴保持のため改名しない）。"""
    data = _identity_decision_protocol_data()
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    assert tuple(priority["failure_refs"]) == m._IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_REFS
    assert priority["failure_refs"] == [
        "birth_identity_separation.invalid_or_nonfinite_feature",
        "birth_identity_separation.invalid_or_nonfinite_d12",
        "pjs_confuser.invalid_or_nonfinite_distance",
        "birth_identity_separation.not_established",
        "pjs_confuser.on_zero",
    ]
    # order と同順であること（対応関係の可読性——(1) validity（feature）→
    # (2) validity（d12）→ (3) validity（PJS distance）→ (4) d12=0 →
    # (5) PJS confuser distance=0）。
    assert len(priority["order"]) == len(priority["failure_refs"])


def test_pr333_r5_validate_rejects_conjunct_refs_missing_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_aggregate_rule"]["conjunct_refs"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_failure_refs_missing_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    del priority["failure_refs"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_failure_refs_reordered() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["failure_refs"] = list(reversed(priority["failure_refs"]))
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_aggregate_rule.not_established.outcome_detail_priority.failure_refs",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_failure_refs_extra_entry() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["failure_refs"].append("pjs_confuser.metric")
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_aggregate_rule.not_established.outcome_detail_priority.failure_refs",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_failure_refs_dict_masquerade() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    priority["failure_refs"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_REFS
    )
    with pytest.raises(
        m.Run9ValidationError,
        match=(
            "birth_gate_aggregate_rule.not_established.outcome_detail_priority.failure_refs "
            "must be a list"
        ),
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_conjunct_refs_reintroducing_old_exclusive_pair() -> None:
    """第4巡実装の欠陥そのもの（排他ペア4項目）を復元しても是正後の
    validator が拒否すること——回帰防止の直接確認。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["conjunct_refs"] = [
        "birth_identity_separation.established",
        "birth_identity_separation.invalid_or_nonfinite_feature",
        "pjs_confuser.on_positive",
        "pjs_confuser.on_zero",
    ]
    with pytest.raises(m.Run9ValidationError, match="birth_gate_aggregate_rule.conjunct_refs"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_identity_establishment_scope_note_missing_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_aggregate_rule"]["identity_establishment_scope_note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_identity_establishment_scope_note_empty() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["identity_establishment_scope_note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_aggregate_rule_existing_branches_byte_unchanged() -> None:
    """既存 birth_identity_separation/pjs_confuser.on_zero 節は本改訂で
    無改変（1 byte も変更していない）——第4巡回帰確認と同型。pjs_confuser
    トップレベル key set は第8巡指摘3（P2、採用）の
    `invalid_or_nonfinite_distance` 新設で伸長している（`test_pr333_r4_
    aggregate_rule_pjs_confuser_section_byte_unchanged` の docstring 参照）。
    birth_identity_separation トップレベル key set は第16巡指摘1（P1、
    上限到達後、採用）の `invalid_or_nonfinite_d12` 新設でさらに伸長して
    いる。"""
    data = _identity_decision_protocol_data()
    assert set(data["pjs_confuser"].keys()) == {
        "verbatim", "metric", "pjs_reference_ref", "on_zero", "on_positive",
        "invalid_or_nonfinite_distance",
    }
    assert set(data["pjs_confuser"]["on_zero"].keys()) == {
        "condition", "birth_outcome", "reason",
    }
    assert set(data["birth_identity_separation"].keys()) == {
        "verbatim", "cell_ref", "formula", "established", "not_established",
        "invalid_or_nonfinite_feature", "invalid_or_nonfinite_d12",
        "negative_reference_gate_note",
    }


# --- 指摘2: c1_sham_attestation.on_wav_byte_mismatch ------------------------


def test_pr333_r5_c1_on_wav_byte_mismatch_reuses_c0_vocabulary() -> None:
    """新設 on_wav_byte_mismatch は C0 側の停止語彙定数をそのまま再利用
    していること（新語彙の発明はしない）。"""
    data = _identity_decision_protocol_data()
    on_wav = data["c1_sham_attestation"]["on_wav_byte_mismatch"]
    assert on_wav["outcome"] == m.IDENTITY_PROTOCOL_C0_RENDER_MISMATCH_OUTCOME
    assert on_wav["outcome"] == "DETERMINISM_CONTRACT_BROKEN"
    c0_on_mismatch = data["c0_determinism_attestation"]["on_mismatch"]
    assert on_wav["outcome"] == c0_on_mismatch["render_byte_mismatch"]
    positive_on_mismatch = data["positive_reference_audit"]["on_mismatch"]
    assert on_wav["outcome"] == positive_on_mismatch["wav_byte_mismatch"]


def test_pr333_r5_validate_rejects_c1_on_wav_byte_mismatch_missing_top_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["on_wav_byte_mismatch"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_c1_on_wav_byte_mismatch_wrong_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["on_wav_byte_mismatch"]["outcome"] = "C1_SHAM_EFFECT_DETECTED"
    with pytest.raises(
        m.Run9ValidationError, match="c1_sham_attestation.on_wav_byte_mismatch.outcome"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_c1_on_wav_byte_mismatch_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["on_wav_byte_mismatch"]["cross_reference"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_c1_on_wav_byte_mismatch_empty_note() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["on_wav_byte_mismatch"]["note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_c1_on_nonzero_byte_unchanged() -> None:
    """既存 on_nonzero（C1_SHAM_EFFECT_DETECTED）は本改訂で無改変。"""
    data = _identity_decision_protocol_data()
    assert data["c1_sham_attestation"]["on_nonzero"] == "C1_SHAM_EFFECT_DETECTED"


# --- 第6巡指摘1: c1_sham_attestation.on_feature_mismatch --------------------


def test_pr333_r6_c1_on_feature_mismatch_reuses_c0_vocabulary() -> None:
    """新設 on_feature_mismatch は C0 側の feature-mismatch 停止語彙定数を
    そのまま再利用していること（新語彙の発明はしない）。"""
    data = _identity_decision_protocol_data()
    on_feature = data["c1_sham_attestation"]["on_feature_mismatch"]
    assert on_feature["outcome"] == m.IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME
    assert on_feature["outcome"] == "IMPLEMENTATION_FAILURE"
    c0_on_mismatch = data["c0_determinism_attestation"]["on_mismatch"]
    assert on_feature["outcome"] == c0_on_mismatch["feature_computation_mismatch_with_matching_render"]


def test_pr333_r6_validate_rejects_c1_on_feature_mismatch_missing_top_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["on_feature_mismatch"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r6_validate_rejects_c1_on_feature_mismatch_wrong_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["on_feature_mismatch"]["outcome"] = "C1_SHAM_EFFECT_DETECTED"
    with pytest.raises(
        m.Run9ValidationError, match="c1_sham_attestation.on_feature_mismatch.outcome"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r6_validate_rejects_c1_on_feature_mismatch_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["on_feature_mismatch"]["cross_reference"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r6_validate_rejects_c1_on_feature_mismatch_empty_note() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["on_feature_mismatch"]["note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r6_c1_on_nonzero_and_on_wav_byte_mismatch_unchanged() -> None:
    """既存 on_nonzero（C1_SHAM_EFFECT_DETECTED）・on_wav_byte_mismatch
    （DETERMINISM_CONTRACT_BROKEN）は本巡改訂で無改変。"""
    data = _identity_decision_protocol_data()
    assert data["c1_sham_attestation"]["on_nonzero"] == "C1_SHAM_EFFECT_DETECTED"
    assert (
        data["c1_sham_attestation"]["on_wav_byte_mismatch"]["outcome"]
        == "DETERMINISM_CONTRACT_BROKEN"
    )


# --- 指摘3: birth_gate_overall_pass（二層分離）------------------------------


def test_pr333_r5_overall_pass_identity_establishment_ref_points_to_aggregate_rule() -> None:
    data = _identity_decision_protocol_data()
    assert (
        data["birth_gate_overall_pass"]["identity_establishment_ref"]
        == "birth_gate_aggregate_rule"
    )


def test_pr333_r5_overall_pass_verbatim_basis_matches_execution_order_gate_sequencing() -> None:
    """verbatim_basis は execution_order.gate_sequencing（裁定§8 逐語）と
    単一の正本を共有する——実データで一致確認。"""
    data = _identity_decision_protocol_data()
    assert (
        data["birth_gate_overall_pass"]["verbatim_basis"]
        == data["execution_order"]["gate_sequencing"]
    )
    assert data["execution_order"]["gate_sequencing"] == (
        "rev 0.6のBirth GateがPASSした場合のみ、learning recipe freezeおよび学習実行へ進む。"
    )


def test_pr333_r5_overall_pass_audit_stop_refs_matches_frozen_tuple() -> None:
    data = _identity_decision_protocol_data()
    audit_stop_refs = data["birth_gate_overall_pass"]["audit_stop_refs"]
    assert tuple(audit_stop_refs) == m._IDENTITY_PROTOCOL_OVERALL_PASS_AUDIT_STOP_REFS
    assert audit_stop_refs == [
        "c0_determinism_attestation.on_mismatch",
        "c1_sham_attestation.on_nonzero",
        "c1_sham_attestation.on_wav_byte_mismatch",
        "c1_sham_attestation.on_feature_mismatch",
        "positive_reference_audit.on_mismatch",
    ]


def test_pr333_r5_validate_rejects_missing_birth_gate_overall_pass_top_level_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_overall_pass"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_wrong_identity_establishment_ref() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["identity_establishment_ref"] = "pjs_confuser"
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_overall_pass.identity_establishment_ref"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_audit_stop_refs_reordered() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["audit_stop_refs"] = list(
        reversed(data["birth_gate_overall_pass"]["audit_stop_refs"])
    )
    with pytest.raises(m.Run9ValidationError, match="birth_gate_overall_pass.audit_stop_refs"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_audit_stop_refs_extra_entry() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["audit_stop_refs"].append("pjs_confuser.on_zero")
    with pytest.raises(m.Run9ValidationError, match="birth_gate_overall_pass.audit_stop_refs"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_audit_stop_refs_dict_masquerade() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["audit_stop_refs"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_OVERALL_PASS_AUDIT_STOP_REFS
    )
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_overall_pass.audit_stop_refs must be a list"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_verbatim_basis_mismatch() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["verbatim_basis"] = "改ざんされた逐語"
    with pytest.raises(m.Run9ValidationError, match="birth_gate_overall_pass.verbatim_basis"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_overall_pass"]["note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r5_validate_rejects_overall_pass_empty_definition() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["definition"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


# --- load_pinned_identity_decision_protocol(): 第5巡フィールドの一貫性 ------


def test_pr333_r5_load_pinned_happy_path_with_new_sections(
    contract: m.Run9RunContract,
) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert data["schema"] == m.SCHEMA_IDENTITY_DECISION_PROTOCOL
    assert "birth_gate_overall_pass" in data
    assert "on_wav_byte_mismatch" in data["c1_sham_attestation"]
    assert "on_feature_mismatch" in data["c1_sham_attestation"]
    assert "failure_refs" in (
        data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    )


def test_pr333_r5_load_pinned_rejects_overall_pass_tamper_via_hash_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """新設節を改ざんした合成 manifest は、manifest 実バイト sha256 が
    pin 済み hypothesis_algebra_sha と食い違うため fail-closed で拒否
    されること（第4巡と同型の tamper 経路確認）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["birth_gate_overall_pass"]["identity_establishment_ref"] = "pjs_confuser"

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(
        m.Run9ValidationError, match="birth_gate_overall_pass.identity_establishment_ref"
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


# =============================================================================
# 敵対的自己検査（Task 指示必須）: protocol JSON だけを入力に「文字通りの
# 消費者」を演じるシミュレーション。ハードコードされた Python 分岐ロジック
# ではなく、`conjunct_refs`/`audit_stop_refs` の JSON 列挙をそのまま辿る
# ことで、protocol 側の宣言だけから Birth outcome / overall PASS を導出
# する——validator の意図と実データの整合を、validator 自身とは独立の
# 経路で二重確認する。
# =============================================================================


def _literal_consumer_birth_gate(
    protocol: Dict[str, Any],
    *,
    feature_valid: bool,
    d12_positive: bool,
    pjs_distance_positive: bool,
    d12_finite: bool = True,
    c0_mismatch: bool = False,
    c1_nonzero: bool = False,
    c1_wav_byte_mismatch: bool = False,
    c1_feature_mismatch: bool = False,
    positive_mismatch: bool = False,
    audit_complete: bool = True,
    positive_reference_audit_both_founders_complete: bool = True,
) -> Tuple[str, bool]:
    """protocol JSON の `birth_gate_aggregate_rule.conjunct_refs` 列挙を
    そのまま辿って BIRTH outcome を、`birth_gate_overall_pass` の定義を
    そのまま辿って overall PASS を導出する「文字通りの消費者」。

    conjunct ref 文字列 → 実世界の真偽値の対応表 (`conjunct_truth`) 以外は
    protocol 側の分岐定義（`established`/`not_established` の
    `birth_outcome` 値）だけを読む——本関数自身は ESTABLISHED/
    NOT_ESTABLISHED をハードコードしない。

    `audit_complete`（PR #333 第11巡指摘1、P1、採用、新設引数）: C0/C1/
    positive reference の各監査結果そのものが記録済みかどうか（take 数
    充足・positive 監査実行済み）を表す runtime fact——`c0_mismatch` 等の
    「不一致」フラグとは独立の第3の軸である（監査が実施されていなければ
    不一致判定自体が定義できない）。既定 True は「全監査完了」の世界線
    のみを表し、旧テスト群（第5-10巡）が既定値のまま『全成功』を意味して
    いた前提を維持する——第11巡はこれを明示引数へ格上げし、False の世界線
    を新規に追加検証する。

    `positive_reference_audit_both_founders_complete`（PR #333 第13巡
    指摘、P1、上限到達後、採用、新設引数）: `audit_complete` は監査
    3種（C0/C1/positive reference）を単一の粒度でしか表現できず、
    positive_reference_audit が「両 founder のうち片方だけ監査済み」
    という founder 単位の部分完了を単独で表現できなかった——第13巡是正
    後の protocol 側 condition（両 founder それぞれの positive_
    reference(F) 監査を要求する閉集合列挙）を literal に辿るため、本引数
    を `audit_complete` とは独立の第4の軸として追加する。既定 True は
    「両 founder とも positive reference 監査済み」の世界線を表し、
    第5-12巡の既定値依存テストの前提（全成功）を変えない。False は
    「片 founder のみ監査済み（例: R9F-01 のみ、R9F-02 は省略）」という
    第13巡指摘が指す具体的な偽成功経路の世界線を表す。

    `d12_finite`（PR #333 第16巡指摘1、P1、上限到達後、採用、新設引数）:
    `d12_positive` は「d12 と 0 の大小比較結果」のみを表し、d12 自体が
    有限の実数値であるかどうかを独立に表現できなかった——両 feature が
    valid/finite であっても Euclidean 距離計算の overflow 等で d12=+inf
    となり得る場合、素朴な `d12 > 0` 比較は真になるため、is (d12_positive
    を True のまま) established へ到達し得た偽成功経路を literal consumer
    でも再現できていなかった。既定 True は「d12 が有限」の世界線（第5-15巡
    の既定値依存テストの前提を変えない）、False は「d12=+inf」（有限性
    要求の不成立）を表す。
    """
    agg = protocol["birth_gate_aggregate_rule"]
    conjunct_truth = {
        "birth_identity_separation.established": feature_valid and d12_finite and d12_positive,
        "pjs_confuser.on_positive": pjs_distance_positive,
    }
    for ref in agg["conjunct_refs"]:
        assert ref in conjunct_truth, f"literal consumer: unknown conjunct ref {ref!r}"
    established = all(conjunct_truth[ref] for ref in agg["conjunct_refs"])
    birth_outcome = (
        agg["established"]["birth_outcome"] if established
        else agg["not_established"]["birth_outcome"]
    )

    audit_failed = (
        c0_mismatch or c1_nonzero or c1_wav_byte_mismatch or c1_feature_mismatch
        or positive_mismatch
    )
    # completion_evidence_requirement（第11巡指摘1）が protocol 側に実在
    # することを要求する——本節が欠落した protocol データに対しては、監査
    # 完了性の第3連言項そのものが存在しないため、本 literal consumer は
    # 「まだ判定できない」を検出できず、指摘1 が是正した欠陥をそのまま
    # 再現してしまう。この assert 自体が指摘1 の回帰ガードを兼ねる。
    assert "completion_evidence_requirement" in protocol["birth_gate_overall_pass"], (
        "literal consumer: birth_gate_overall_pass.completion_evidence_requirement is "
        "missing — this is the exact PR #333 第11巡指摘1 regression (audit completeness "
        "is not representable, so incomplete audits would silently pass)"
    )
    # PR #333 第13巡指摘（P1、上限到達後、採用）の回帰ガード: condition
    # プローズが positive_reference_audit を founder 単位（R9F-01/
    # R9F-02 双方）の閉集合列挙として要求していることを literal に確認
    # する。この assert が無いと、単数表現へ後退した場合に本 literal
    # consumer 自身が第13巡指摘の欠陥（片 founder のみの監査で
    # audit_complete=True 相当を許してしまう）を再現し得る。
    completion_condition = protocol["birth_gate_overall_pass"][
        "completion_evidence_requirement"
    ]["condition"]
    assert "positive_reference_audit" in completion_condition
    assert "R9F-01" in completion_condition and "R9F-02" in completion_condition, (
        "literal consumer: completion_evidence_requirement.condition does not enumerate "
        "both founders (R9F-01/R9F-02) — this is the exact PR #333 第13巡 regression "
        "(a single-founder positive_reference audit could satisfy a singular condition)"
    )
    # overall PASS の定義（`birth_gate_overall_pass.definition`）をそのまま
    # 辿る: identity_establishment = ESTABLISHED ∧ 監査停止が一件も無い
    # ∧ 監査結果そのものが完了している（第11巡追加の第3連言項、第13巡で
    # positive_reference_audit を founder 単位まで精緻化）。
    overall_pass = (
        established
        and not audit_failed
        and audit_complete
        and positive_reference_audit_both_founders_complete
    )
    return birth_outcome, overall_pass


def test_pr333_r5_adversarial_literal_consumer_all_success_establishes_and_passes() -> None:
    """(a) 全成功ケース → ESTABLISHED + overall PASS。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is True


def test_pr333_r5_adversarial_literal_consumer_pjs_zero_distance_not_established() -> None:
    """(b) PJS 距離 0（他は全て成功）→ NOT_ESTABLISHED。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=False,
    )
    assert birth_outcome == "NOT_ESTABLISHED"
    assert overall_pass is False


def test_pr333_r5_adversarial_literal_consumer_c1_byte_only_mismatch_broken_but_established() -> (
    None
):
    """(c) C1 バイトのみ不一致（identity_establishment は全て成功）→
    DETERMINISM_CONTRACT_BROKEN 相当の監査停止・overall 非PASS・ただし
    BIRTH=ESTABLISHED 判定自体は維持される（会計分離、指摘3 の核心）。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        c1_wav_byte_mismatch=True,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is False
    # 監査停止語彙が実際に protocol 側で DETERMINISM_CONTRACT_BROKEN へ
    # 割り当てられていることも直接確認する。
    assert (
        protocol["c1_sham_attestation"]["on_wav_byte_mismatch"]["outcome"]
        == "DETERMINISM_CONTRACT_BROKEN"
    )


def test_pr333_r6_adversarial_literal_consumer_c1_feature_only_mismatch_established_but_not_pass() -> (
    None
):
    """(e) C1 feature のみ不一致（WAV bytes は一致・identity_establishment
    は全て成功）→ ESTABLISHED 維持だが overall 非 PASS（第6巡指摘1 の核心
    ——on_nonzero/on_wav_byte_mismatch のいずれにも未発火だった経路）。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        c1_feature_mismatch=True,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is False
    # 監査停止語彙が実際に protocol 側で IMPLEMENTATION_FAILURE へ
    # 割り当てられていることも直接確認する（C0 側の対称分岐と同一語彙）。
    assert (
        protocol["c1_sham_attestation"]["on_feature_mismatch"]["outcome"]
        == "IMPLEMENTATION_FAILURE"
    )
    assert (
        protocol["c1_sham_attestation"]["on_feature_mismatch"]["outcome"]
        == protocol["c0_determinism_attestation"]["on_mismatch"][
            "feature_computation_mismatch_with_matching_render"
        ]
    )


def test_pr333_r5_adversarial_literal_consumer_no_exclusive_pair_all_conjuncts_satisfiable() -> (
    None
):
    """(d) conjunct_refs の全参照が同時成立可能であること（排他ペア非
    存在の直接シミュレーション確認）——全成功ワールドで conjunct_refs の
    各項が独立に True になることを、`_literal_consumer_birth_gate` が
    使う `conjunct_truth` 辞書を直接検査して確認する。"""
    protocol = _identity_decision_protocol_data()
    agg = protocol["birth_gate_aggregate_rule"]
    conjunct_truth = {
        "birth_identity_separation.established": True,
        "pjs_confuser.on_positive": True,
    }
    for ref in agg["conjunct_refs"]:
        assert conjunct_truth[ref] is True, (
            f"literal consumer: conjunct ref {ref!r} is not satisfiable simultaneously "
            "with the other conjuncts under the all-success world state"
        )
    assert all(conjunct_truth[ref] for ref in agg["conjunct_refs"])


def test_pr333_r16_adversarial_literal_consumer_d12_nonfinite_not_established() -> None:
    """(f) d12=+inf（両 feature は valid/finite・PJS 距離も正値）→
    NOT_ESTABLISHED（第16巡指摘1 の核心——d12 自体の finite 性を要求
    しない旧 established.condition の下では、素朴な d12 > 0 比較が真に
    なるため ESTABLISHED_BY_MACHINE_FEATURE へ到達し得た偽成功経路の
    回帰ガード）。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        d12_finite=False,
    )
    assert birth_outcome == "NOT_ESTABLISHED"
    assert overall_pass is False
    # 新設分岐が実際に protocol 側で登録されていることも直接確認する
    # （outcome_detail 定数はハードコードせず protocol JSON から読む）。
    invalid_d12 = protocol["birth_identity_separation"]["invalid_or_nonfinite_d12"]
    assert invalid_d12["birth_outcome"] == "NOT_ESTABLISHED"
    assert invalid_d12["outcome_detail"] == "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_D12"
    priority = protocol["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    assert "invalid_or_nonfinite_d12" in priority["order"]
    assert "birth_identity_separation.invalid_or_nonfinite_d12" in priority["failure_refs"]
    assert priority["detail_by_key"]["invalid_or_nonfinite_d12"] == invalid_d12["outcome_detail"]
    # 優先順内の位置: feature validity（先頭）の直後、PJS distance validity
    # の前（第16巡指摘1の依存順設計、order_note 参照）。
    assert priority["order"].index("invalid_or_nonfinite_feature") < priority["order"].index(
        "invalid_or_nonfinite_d12"
    ) < priority["order"].index("invalid_or_nonfinite_pjs_distance")


# =============================================================================
# PR #333 Codex bot レビュー第7巡対応（2026-08-28、フェーズ1）
# 指摘2: metric_reference.source_file の宣言 path 検証欠如
#
# 旧実装は metric_reference.source_file を非空文字列としてしか検証して
# おらず、実際に読む identity_metric_space.json は常に固定定数
# IDENTITY_METRIC_SPACE_PATH 経由で、source_file の宣言値自体はどの
# 読み込みにも使われていなかった——誤記・改ざんされても検出できない
# 乖離を、凍結期待 path との厳密一致（validator + loader 二層防御、
# birth_identity_separation.cell_ref と同型）で閉じる。
# =============================================================================


def test_pr333_r7_metric_reference_source_file_matches_frozen_expected_constant() -> None:
    """protocol 実データの現宣言値が、IDENTITY_METRIC_SPACE_PATH から
    導出した凍結期待 path と一致していること（是正時点で repin 不要
    だったことの回帰確認）。"""
    data = _identity_decision_protocol_data()
    assert (
        data["metric_reference"]["source_file"]
        == m._IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_SOURCE_FILE
    )
    assert m._IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_SOURCE_FILE == (
        "voice_genesis/evolution/run9_dual_founder_pjs/inputs/identity_metric_space.json"
    )


def test_pr333_r7_validate_rejects_metric_reference_source_file_typo() -> None:
    """metric_reference.source_file が凍結期待 path と一致しない場合、
    validate_identity_decision_protocol() が fail-closed で拒否すること
    （旧実装は非空文字列チェックのみで、この typo/改ざんを素通りさせて
    いた）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["metric_reference"]["source_file"] = (
        "voice_genesis/evolution/run9_dual_founder_pjs/inputs/identity_metric_space_TYPO.json"
    )
    with pytest.raises(m.Run9ValidationError, match="metric_reference.source_file"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r7_validate_rejects_metric_reference_source_file_pointing_elsewhere() -> None:
    """typo だけでなく、実在する別の repo 内ファイル（内容も無関係）を
    指す差し替えも同様に拒否すること——「実在するファイルを指してさえ
    いれば通る」ような緩い検証になっていないことの確認。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["metric_reference"]["source_file"] = (
        "voice_genesis/evolution/run9_dual_founder_pjs/RUN9_CONTRACT.yaml"
    )
    with pytest.raises(m.Run9ValidationError, match="metric_reference.source_file"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r7_load_pinned_identity_decision_protocol_rejects_metric_reference_source_file_tamper(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """統合確認: metric_reference.source_file が改ざんされた manifest を
    `load_pinned_identity_decision_protocol()` 経由で消費しようとすると、
    end-to-end で fail-closed 拒否されること。"""
    domain = _real_identity_domain()

    def _tamper(data: Dict[str, Any]) -> None:
        data["metric_reference"]["source_file"] = (
            "voice_genesis/evolution/run9_dual_founder_pjs/inputs/identity_metric_space_TYPO.json"
        )

    tampered_contract, manifest_path, _contract_path = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=_tamper,
    )
    with pytest.raises(m.Run9ValidationError, match="metric_reference.source_file"):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


def test_pr333_r7_load_pinned_identity_decision_protocol_cross_check_reuses_frozen_constant(
    contract: m.Run9RunContract,
) -> None:
    """loader 側 cross-check (2) が validator と同一の凍結正本
    （`_IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_SOURCE_FILE`）を再利用
    していること——現行の実 manifest / 実 contract では両者とも通過する
    ことを直接確認する（two-layer defense の非衝突確認）。"""
    domain = _real_identity_domain()
    result = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert (
        result["metric_reference"]["source_file"]
        == m._IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_SOURCE_FILE
    )


# =============================================================================
# PR #333 Codex bot レビュー第8巡対応（2026-08-28、フェーズ1）
# 指摘1（P2）: c1_sham_attestation の重複可能な述語対（on_nonzero ×
#   on_wav_byte_mismatch）に優先順・全該当会計が未定義だった穴の是正。
# 指摘3（P2）: pjs_confuser の distance が invalid/non-finite の場合に
#   on_positive/on_zero いずれにも該当しない未登録分岐の是正。
# =============================================================================


# --- 指摘1: c1_sham_attestation.outcome_priority ----------------------------


def test_pr333_r8_c1_outcome_priority_order_matches_frozen_tuple() -> None:
    data = _identity_decision_protocol_data()
    priority = data["c1_sham_attestation"]["outcome_priority"]
    assert tuple(priority["order"]) == m._IDENTITY_PROTOCOL_C1_OUTCOME_PRIORITY_ORDER
    assert priority["order"] == ["on_wav_byte_mismatch", "on_feature_mismatch", "on_nonzero"]


def test_pr333_r8_c1_outcome_priority_detail_by_key_matches_actual_outcomes() -> None:
    """detail_by_key の各値は c1_sham_attestation 側の実際の outcome 値と
    単一の正本を共有する（二重に書き起こさない）ことを実データで確認。"""
    data = _identity_decision_protocol_data()
    c1 = data["c1_sham_attestation"]
    detail_by_key = c1["outcome_priority"]["detail_by_key"]
    assert detail_by_key["on_wav_byte_mismatch"] == c1["on_wav_byte_mismatch"]["outcome"]
    assert detail_by_key["on_feature_mismatch"] == c1["on_feature_mismatch"]["outcome"]
    assert detail_by_key["on_nonzero"] == c1["on_nonzero"]
    assert detail_by_key["on_wav_byte_mismatch"] == "DETERMINISM_CONTRACT_BROKEN"
    assert detail_by_key["on_feature_mismatch"] == "IMPLEMENTATION_FAILURE"
    assert detail_by_key["on_nonzero"] == "C1_SHAM_EFFECT_DETECTED"


def test_pr333_r8_validate_rejects_c1_missing_outcome_priority_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["outcome_priority"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["c1_sham_attestation"]["outcome_priority"]["order_note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_order_reordered() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["outcome_priority"]["order"] = list(
        reversed(data["c1_sham_attestation"]["outcome_priority"]["order"])
    )
    with pytest.raises(
        m.Run9ValidationError, match="c1_sham_attestation.outcome_priority.order"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_order_dict_masquerade() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["outcome_priority"]["order"] = _dict_masquerading_as_ordered_list(
        m._IDENTITY_PROTOCOL_C1_OUTCOME_PRIORITY_ORDER
    )
    with pytest.raises(
        m.Run9ValidationError, match="c1_sham_attestation.outcome_priority.order must be a list"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_detail_by_key_mismatch() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["outcome_priority"]["detail_by_key"]["on_nonzero"] = (
        "MADE_UP_LABEL"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="c1_sham_attestation.outcome_priority.detail_by_key.on_nonzero",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_detail_by_key_unregistered_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["outcome_priority"]["detail_by_key"]["extra_key"] = "X"
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_c1_outcome_priority_order_note_empty() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["c1_sham_attestation"]["outcome_priority"]["order_note"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_c1_on_nonzero_on_wav_byte_mismatch_on_feature_mismatch_unchanged() -> None:
    """既存 on_nonzero/on_wav_byte_mismatch/on_feature_mismatch は本巡改訂
    で無改変（outcome_priority は新設フィールドの追加のみ）。"""
    data = _identity_decision_protocol_data()
    c1 = data["c1_sham_attestation"]
    assert c1["on_nonzero"] == "C1_SHAM_EFFECT_DETECTED"
    assert c1["on_wav_byte_mismatch"]["outcome"] == "DETERMINISM_CONTRACT_BROKEN"
    assert c1["on_feature_mismatch"]["outcome"] == "IMPLEMENTATION_FAILURE"


# --- 指摘3: pjs_confuser.invalid_or_nonfinite_distance ----------------------


def test_pr333_r8_pjs_invalid_or_nonfinite_distance_happy_path() -> None:
    data = _identity_decision_protocol_data()
    branch = data["pjs_confuser"]["invalid_or_nonfinite_distance"]
    assert branch["birth_outcome"] == "NOT_ESTABLISHED"
    assert branch["outcome_detail"] == m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL
    assert branch["outcome_detail"] == (
        "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_PJS_CONFUSER_INVALID_OR_NONFINITE_DISTANCE"
    )


def test_pr333_r8_pjs_invalid_distance_detail_does_not_collide_with_existing_vocab() -> None:
    assert m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL not in m.BIRTH_OUTCOMES
    assert m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL not in m.IDENTITY_OUTCOMES
    assert m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL != (
        m.IDENTITY_PROTOCOL_BIRTH_PJS_CONFUSER_COLLAPSE_DETAIL
    )
    assert m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL != (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL
    )


def test_pr333_r8_validate_rejects_pjs_missing_invalid_distance_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["pjs_confuser"]["invalid_or_nonfinite_distance"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_pjs_invalid_distance_wrong_birth_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["pjs_confuser"]["invalid_or_nonfinite_distance"]["birth_outcome"] = "ESTABLISHED"
    with pytest.raises(
        m.Run9ValidationError, match="pjs_confuser.invalid_or_nonfinite_distance.birth_outcome"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_pjs_invalid_distance_wrong_outcome_detail() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["pjs_confuser"]["invalid_or_nonfinite_distance"]["outcome_detail"] = "MADE_UP_LABEL"
    with pytest.raises(
        m.Run9ValidationError, match="pjs_confuser.invalid_or_nonfinite_distance.outcome_detail"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_pjs_invalid_distance_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["pjs_confuser"]["invalid_or_nonfinite_distance"]["note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r8_validate_rejects_pjs_invalid_distance_empty_condition() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["pjs_confuser"]["invalid_or_nonfinite_distance"]["condition"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


# --- 指摘3: birth_gate_aggregate_rule.not_established.outcome_detail_priority
# の3項目→4項目への拡張 ---------------------------------------------------


def test_pr333_r8_birth_gate_priority_order_extended_to_four_items() -> None:
    """第8巡実装時点の名残の関数名——第16巡指摘1で5項目へ再拡張された
    ため、期待値をその時点の実データ（`invalid_or_nonfinite_d12` 挿入後）
    へ更新する（関数名は既存回帰の追跡単位として維持、内容は現行仕様）。"""
    data = _identity_decision_protocol_data()
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    assert tuple(priority["order"]) == m._IDENTITY_PROTOCOL_BIRTH_GATE_PRIORITY_ORDER
    assert priority["order"] == [
        "invalid_or_nonfinite_feature",
        "invalid_or_nonfinite_d12",
        "invalid_or_nonfinite_pjs_distance",
        "d12_zero_collapse",
        "pjs_confuser_zero_distance",
    ]
    assert priority["detail_by_key"]["invalid_or_nonfinite_pjs_distance"] == (
        m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL
    )
    assert priority["detail_by_key"]["invalid_or_nonfinite_d12"] == (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL
    )


def test_pr333_r8_validate_rejects_birth_gate_priority_order_reverted_to_three_items() -> None:
    """第8巡以前の3項目 order を復元しても是正後の validator が拒否する
    こと——回帰防止の直接確認。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]["order"] = [
        "invalid_or_nonfinite_feature", "d12_zero_collapse", "pjs_confuser_zero_distance",
    ]
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_aggregate_rule.not_established.outcome_detail_priority.order",
    ):
        m.validate_identity_decision_protocol(data)


# --- load_pinned_identity_decision_protocol(): 第8巡フィールドの一貫性 ------


def test_pr333_r8_load_pinned_happy_path_with_new_fields(
    contract: m.Run9RunContract,
) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert "outcome_priority" in data["c1_sham_attestation"]
    assert "invalid_or_nonfinite_distance" in data["pjs_confuser"]
    assert "invalid_or_nonfinite_d12" in data["birth_identity_separation"]
    assert len(
        data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]["order"]
    ) == 5


def test_pr333_r8_load_pinned_rejects_new_field_tamper_via_hash_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """新設フィールドを改ざんした合成 manifest は、実バイト sha256 が
    pin 済み hypothesis_algebra_sha と食い違うため fail-closed で拒否
    されること（既存巡と同型の tamper 経路確認）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["pjs_confuser"]["invalid_or_nonfinite_distance"]["outcome_detail"] = "TAMPERED"

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(
        m.Run9ValidationError, match="pjs_confuser.invalid_or_nonfinite_distance.outcome_detail"
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


# =============================================================================
# PR #333 Codex bot レビュー第11巡対応（2026-08-28、フェーズ1）
# 指摘1（P1）: 監査結果の欠落（partial render・結果未発行・positive 監査
#   未実行）は audit_stop_refs（不一致述語のみ）に未該当のため、overall
#   PASS が無音のまま成立し得た穴の是正——`birth_gate_overall_pass` へ
#   `completion_evidence_requirement`（第3の連言項）を新設。
# =============================================================================


# --- 指摘1: birth_gate_overall_pass.completion_evidence_requirement --------


def test_pr333_r11_completion_evidence_requirement_audit_completeness_refs_matches_frozen_tuple() -> (
    None
):
    data = _identity_decision_protocol_data()
    refs = data["birth_gate_overall_pass"]["completion_evidence_requirement"][
        "audit_completeness_refs"
    ]
    assert tuple(refs) == m._IDENTITY_PROTOCOL_OVERALL_PASS_COMPLETION_REFS
    assert refs == [
        "c0_determinism_attestation",
        "c1_sham_attestation",
        "positive_reference_audit",
    ]


def test_pr333_r11_completion_evidence_requirement_on_incomplete_and_outcome_match_constants() -> (
    None
):
    """`on_incomplete` は新設 outcome_detail 定数、`outcome` は既存
    IMPLEMENTATION_FAILURE 系語彙（新規 frozen tuple 値の追加ではなく
    再利用）と単一の正本を共有する——実データで一致確認。"""
    data = _identity_decision_protocol_data()
    req = data["birth_gate_overall_pass"]["completion_evidence_requirement"]
    assert req["on_incomplete"] == m.IDENTITY_PROTOCOL_AUDIT_INCOMPLETE_DETAIL
    assert req["on_incomplete"] == "IDENTITY_PROTOCOL_AUDIT_INCOMPLETE"
    assert req["outcome"] == m.IDENTITY_PROTOCOL_AUDIT_INCOMPLETE_OUTCOME
    assert req["outcome"] == "IMPLEMENTATION_FAILURE"
    # IMPLEMENTATION_FAILURE 系の既存語彙を再利用しているだけであり、
    # FAILURE_CLASSES 自体は無改変（新規値を追加していない）ことの確認。
    assert req["outcome"] in m.FAILURE_CLASSES
    assert req["outcome"] == m.FAILURE_CLASSES[0]


def test_pr333_r11_validate_rejects_missing_completion_evidence_requirement_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_overall_pass"]["completion_evidence_requirement"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_gate_overall_pass"]["completion_evidence_requirement"]["note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_audit_completeness_refs_reordered() -> (
    None
):
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["completion_evidence_requirement"][
        "audit_completeness_refs"
    ] = list(
        reversed(
            data["birth_gate_overall_pass"]["completion_evidence_requirement"][
                "audit_completeness_refs"
            ]
        )
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_overall_pass.completion_evidence_requirement.audit_completeness_refs",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_audit_completeness_refs_dict_masquerade() -> (
    None
):
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["completion_evidence_requirement"][
        "audit_completeness_refs"
    ] = _dict_masquerading_as_ordered_list(m._IDENTITY_PROTOCOL_OVERALL_PASS_COMPLETION_REFS)
    with pytest.raises(
        m.Run9ValidationError,
        match="completion_evidence_requirement.audit_completeness_refs must be a list",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_wrong_on_incomplete() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["completion_evidence_requirement"]["on_incomplete"] = (
        "SOME_OTHER_LABEL"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_overall_pass.completion_evidence_requirement.on_incomplete",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_wrong_outcome() -> None:
    """`outcome` を IMPLEMENTATION_FAILURE 以外（例えば別の停止語彙）へ
    差し替えると拒否される——既存語彙の再利用が固定されていることの
    確認（新規 outcome 値の発明を防ぐ）。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["completion_evidence_requirement"]["outcome"] = (
        "DETERMINISM_CONTRACT_BROKEN"
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_overall_pass.completion_evidence_requirement.outcome",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_validate_rejects_completion_evidence_requirement_empty_condition() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_overall_pass"]["completion_evidence_requirement"]["condition"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r11_definition_mentions_completion_evidence_requirement() -> None:
    """`definition` 文言が第3連言項（completion_evidence_requirement）へ
    実際に言及していること——第5巡の audit_stop_refs 追加時と同様、
    definition テキスト自体が新設節を反映していることを確認する。"""
    data = _identity_decision_protocol_data()
    assert "completion_evidence_requirement" in data["birth_gate_overall_pass"]["definition"]
    assert (
        m.IDENTITY_PROTOCOL_AUDIT_INCOMPLETE_DETAIL
        in data["birth_gate_overall_pass"]["definition"]
    )


# --- 指摘1: 敵対的自己検査（literal consumer, audit completeness 軸）-------


def test_pr333_r11_adversarial_literal_consumer_audit_incomplete_established_but_not_pass() -> (
    None
):
    """(f) 監査結果が欠落（C0/C1 の一部 take 未記録・positive 監査未実行等
    を audit_complete=False として表す。identity_establishment は全て
    成功・不一致フラグも全て False）→ ESTABLISHED は維持されるが
    overall PASS は不成立（指摘1 の核心——是正前は本ケースが無音で
    overall PASS へ落ちていた）。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        audit_complete=False,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is False


def test_pr333_r11_adversarial_literal_consumer_all_success_and_complete_passes() -> None:
    """(g) 全成功 + 監査完了（明示的に audit_complete=True）→ ESTABLISHED +
    overall PASS——第5巡 all_success テスト（既定値依存）を明示引数で
    再確認する回帰確認。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        audit_complete=True,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is True


# --- load_pinned_identity_decision_protocol(): 第11巡フィールドの一貫性 ----


def test_pr333_r11_load_pinned_happy_path_with_completion_evidence_requirement(
    contract: m.Run9RunContract,
) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert "completion_evidence_requirement" in data["birth_gate_overall_pass"]
    assert tuple(
        data["birth_gate_overall_pass"]["completion_evidence_requirement"][
            "audit_completeness_refs"
        ]
    ) == m._IDENTITY_PROTOCOL_OVERALL_PASS_COMPLETION_REFS


def test_pr333_r11_load_pinned_rejects_completion_evidence_requirement_tamper_via_hash_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """新設節を改ざんした合成 manifest は、実バイト sha256 が pin 済み
    hypothesis_algebra_sha と食い違うため fail-closed で拒否されること
    （既存巡と同型の tamper 経路確認）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["birth_gate_overall_pass"]["completion_evidence_requirement"]["on_incomplete"] = (
            "TAMPERED"
        )

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_overall_pass.completion_evidence_requirement.on_incomplete",
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


# =============================================================================
# PR #333 Codex bot レビュー第13巡対応（2026-08-28、フェーズ1、上限到達後）
# 指摘（P1、上限到達後——3分類「致命的バグ（偽成功経路）」の新規具体経路）:
#   `birth_gate_overall_pass.completion_evidence_requirement.condition` の
#   positive_reference_audit 項が単数表現に留まり founder 単位の閉集合
#   列挙を欠いていたため、identity_metric_space.json が positive_
#   reference(F) を founder ごとに定義しているにもかかわらず、片 founder
#   （R9F-01 のみ）の positive 監査でも overall PASS が成立し得た穴の
#   是正——condition を c0/c1 と同型の per-founder 様式へ改訂した。
# =============================================================================


# --- 指摘: completion_evidence_requirement.condition の founder 単位列挙 ----


def test_pr333_r13_completion_evidence_requirement_condition_enumerates_both_founders_for_positive_reference() -> (
    None
):
    """positive_reference_audit 項が c0/c1 項と同型に両 founder
    （R9F-01/R9F-02）を明示列挙していること——第13巡是正の核心。"""
    data = _identity_decision_protocol_data()
    condition = data["birth_gate_overall_pass"]["completion_evidence_requirement"]["condition"]
    positive_clause_start = condition.index("positive_reference_audit は")
    positive_clause = condition[positive_clause_start:]
    assert "R9F-01" in positive_clause
    assert "R9F-02" in positive_clause
    assert "positive_reference(F)" in positive_clause
    # 是正前の単数表現（founder 列挙を欠く文言）が残置していないことの
    # 直接的な回帰ガード。
    assert "positive_reference_audit は実行され結果が記録済みであることを要求する" not in condition


def test_pr333_r13_completion_evidence_requirement_condition_references_per_founder_metric_definition() -> (
    None
):
    """positive_reference(F) の founder 単位定義への参照
    （inputs/identity_metric_space.json の positive_reference_definition）
    を condition が明示していること——裁定§3を founder 粒度で機械符号化
    したという設計意図の直接確認。"""
    data = _identity_decision_protocol_data()
    condition = data["birth_gate_overall_pass"]["completion_evidence_requirement"]["condition"]
    assert (
        "identity_metric_space.json#calibration.validity_gates.positive_reference_gate."
        "positive_reference_definition" in condition
    )


def test_pr333_r13_completion_evidence_requirement_note_documents_round13_fix() -> None:
    """note が第13巡の是正経緯（偽成功経路の具体例・founder 単位への
    改訂・on_incomplete/outcome 既存語彙の再利用）を記録していること。"""
    data = _identity_decision_protocol_data()
    note = data["birth_gate_overall_pass"]["completion_evidence_requirement"]["note"]
    assert "第13巡" in note
    assert "R9F-01" in note and "R9F-02" in note
    assert m.IDENTITY_PROTOCOL_AUDIT_INCOMPLETE_DETAIL in note


def test_pr333_r13_c0_and_c1_condition_clauses_already_enumerate_both_founders() -> None:
    """総点検（残余ゼロの確認）: c0_determinism_attestation/
    c1_sham_attestation 項は第13巡是正前から既に両 founder を明示列挙して
    おり、positive_reference_audit と同型の曖昧さを持たないこと。"""
    data = _identity_decision_protocol_data()
    condition = data["birth_gate_overall_pass"]["completion_evidence_requirement"]["condition"]
    c0_clause_start = condition.index("c0_determinism_attestation は")
    c1_clause_start = condition.index("c1_sham_attestation は")
    c0_clause = condition[c0_clause_start:c1_clause_start]
    assert "R9F-01" in c0_clause and "R9F-02" in c0_clause


# --- 指摘: 敵対的自己検査（literal consumer, positive reference founder 軸）-


def test_pr333_r13_adversarial_literal_consumer_single_founder_positive_reference_blocks_overall_pass() -> (
    None
):
    """(h) 片 founder（例: R9F-01）のみ positive_reference 監査済み・
    R9F-02 は省略（`positive_reference_audit_both_founders_complete=False`）
    → identity_establishment は ESTABLISHED を維持するが overall PASS は
    不成立——第13巡指摘の核心（是正前は本ケースが無音で overall PASS へ
    落ち得た偽成功経路）。他の監査完了フラグは全て True。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        audit_complete=True,
        positive_reference_audit_both_founders_complete=False,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is False


def test_pr333_r13_adversarial_literal_consumer_both_founders_positive_reference_passes() -> None:
    """(i) 両 founder とも positive_reference 監査済み
    （`positive_reference_audit_both_founders_complete=True`、既定値）→
    ESTABLISHED + overall PASS——第5/11巡 all_success テストを本軸でも
    明示引数で再確認する回帰確認。"""
    protocol = _identity_decision_protocol_data()
    birth_outcome, overall_pass = _literal_consumer_birth_gate(
        protocol,
        feature_valid=True, d12_positive=True, pjs_distance_positive=True,
        audit_complete=True,
        positive_reference_audit_both_founders_complete=True,
    )
    assert birth_outcome == "ESTABLISHED"
    assert overall_pass is True


# =============================================================================
# PR #333 Codex bot レビュー第16巡対応（2026-08-28、フェーズ1、上限到達後）
# 指摘1（P1、上限到達後——3分類「致命的バグ（偽成功経路）」の新規具体経路）:
#   `birth_identity_separation.established.condition` は「両 founder の
#   feature が valid/finite かつ d12 > 0」のみを要求し、d12 自体の finite
#   性を要求していなかった——両 feature が valid/finite であっても
#   Euclidean 距離計算の overflow 等で d12=+inf となる場合、比較演算子上は
#   d12 > 0 が真となるため ESTABLISHED_BY_MACHINE_FEATURE へ到達し得た
#   （偽成功経路）。pjs_confuser 側には同型の invalid_or_nonfinite_distance
#   分岐が第8巡指摘3で既設だったのに対し、d12 側にはこの被覆漏れが残って
#   いた非対称——第8巡の値域被覆表が導出値 d12 自体の非有限性を見落として
#   いたことを本節が正直に記録する。他の導出値（post_learning_identity_
#   retention の m_other/m_pjs）は第2巡指摘2で invalid/non-finite 分岐が
#   既設であることを再点検し、同型の被覆漏れが無いことを確認した（残余
#   ゼロ、詳細は RUN9_CONTRACT.yaml hypothesis_algebra_sha 【repin 履歴】
#   第16巡エントリ）。
# =============================================================================


# --- 指摘1: birth_identity_separation.invalid_or_nonfinite_d12 -------------


def test_pr333_r16_validate_real_manifest_happy_path_with_r16_fields() -> None:
    m.validate_identity_decision_protocol(_identity_decision_protocol_data())  # 例外なしの確認


def test_pr333_r16_established_condition_requires_d12_finite() -> None:
    """established.condition の文言が d12 の finite 性を明示的に要求して
    いること——是正前は『d12 > 0』のみで finite 性が欠落していた。"""
    data = _identity_decision_protocol_data()
    condition = data["birth_identity_separation"]["established"]["condition"]
    assert "d12 が finite" in condition
    assert "d12 > 0" in condition


def test_pr333_r16_validate_rejects_missing_invalid_d12_key() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_identity_separation"]["invalid_or_nonfinite_d12"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_validate_rejects_invalid_d12_wrong_birth_outcome() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["invalid_or_nonfinite_d12"]["birth_outcome"] = (
        "ESTABLISHED"
    )
    with pytest.raises(
        m.Run9ValidationError, match="birth_identity_separation.invalid_or_nonfinite_d12.birth_outcome"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_validate_rejects_invalid_d12_wrong_outcome_detail() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["invalid_or_nonfinite_d12"]["outcome_detail"] = (
        "MADE_UP_LABEL"
    )
    with pytest.raises(
        m.Run9ValidationError, match="birth_identity_separation.invalid_or_nonfinite_d12.outcome_detail"
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_validate_rejects_invalid_d12_missing_subkey() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    del data["birth_identity_separation"]["invalid_or_nonfinite_d12"]["note"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_validate_rejects_invalid_d12_empty_condition() -> None:
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_identity_separation"]["invalid_or_nonfinite_d12"]["condition"] = ""
    with pytest.raises(m.Run9ValidationError):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_invalid_d12_detail_constant_distinct_from_siblings() -> None:
    """invalid/non-finite d12 の凍結（測定/実装失敗系）は、d12=0 の feature
    collapse（established/not_established の正規条件）とも feature 自体の
    invalid/non-finite（invalid_or_nonfinite_feature）とも別ラベルで machine
    可読に区別されること——三者を同一定数へ縮退させない。"""
    assert (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL
        != m.IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL
    )
    assert (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL
        != m.IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL
    )
    assert (
        m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL
        != m.IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL
    )
    assert m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL not in m.BIRTH_OUTCOMES
    assert m.IDENTITY_PROTOCOL_BIRTH_INVALID_D12_DETAIL not in m.IDENTITY_OUTCOMES


def test_pr333_r16_birth_gate_priority_order_extended_to_five_items() -> None:
    data = _identity_decision_protocol_data()
    priority = data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]
    assert tuple(priority["order"]) == m._IDENTITY_PROTOCOL_BIRTH_GATE_PRIORITY_ORDER
    assert len(priority["order"]) == 5
    # 依存順設計: feature validity（先頭）の直後・PJS distance validity の前。
    assert priority["order"][0] == "invalid_or_nonfinite_feature"
    assert priority["order"][1] == "invalid_or_nonfinite_d12"
    assert priority["order"][2] == "invalid_or_nonfinite_pjs_distance"


def test_pr333_r16_validate_rejects_birth_gate_priority_order_reverted_to_four_items() -> None:
    """第16巡以前の4項目 order を復元しても是正後の validator が拒否する
    こと——回帰防止の直接確認。"""
    data = copy.deepcopy(_identity_decision_protocol_data())
    data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]["order"] = [
        "invalid_or_nonfinite_feature", "invalid_or_nonfinite_pjs_distance",
        "d12_zero_collapse", "pjs_confuser_zero_distance",
    ]
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_gate_aggregate_rule.not_established.outcome_detail_priority.order",
    ):
        m.validate_identity_decision_protocol(data)


def test_pr333_r16_necessary_and_sufficient_condition_mentions_d12_finite() -> None:
    data = _identity_decision_protocol_data()
    nsc = data["birth_gate_aggregate_rule"][
        "necessary_and_sufficient_condition_for_established"
    ]
    assert "d12 が finite" in nsc
    assert "第16巡" in nsc


# --- load_pinned_identity_decision_protocol(): 第16巡フィールドの一貫性 -----


def test_pr333_r16_load_pinned_happy_path_with_new_fields(
    contract: m.Run9RunContract,
) -> None:
    domain = _real_identity_domain()
    data = m.load_pinned_identity_decision_protocol(contract, domain=domain)
    assert "invalid_or_nonfinite_d12" in data["birth_identity_separation"]
    assert len(
        data["birth_gate_aggregate_rule"]["not_established"]["outcome_detail_priority"]["order"]
    ) == 5


def test_pr333_r16_load_pinned_rejects_new_field_tamper_via_hash_mismatch(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """新設フィールドを改ざんした合成 manifest は、実バイト sha256 が
    pin 済み hypothesis_algebra_sha と食い違うため fail-closed で拒否
    されること（既存巡と同型の tamper 経路確認）。"""
    domain = _real_identity_domain()

    def mutate(data: Dict[str, Any]) -> None:
        data["birth_identity_separation"]["invalid_or_nonfinite_d12"]["outcome_detail"] = (
            "TAMPERED"
        )

    tampered_contract, manifest_path, _ = _tampered_identity_protocol_contract(
        contract, tmp_path, mutate=mutate
    )
    with pytest.raises(
        m.Run9ValidationError,
        match="birth_identity_separation.invalid_or_nonfinite_d12.outcome_detail",
    ):
        m.load_pinned_identity_decision_protocol(
            tampered_contract, domain=domain, manifest_path=manifest_path,
            contract_path=tmp_path / "RUN9_CONTRACT.yaml",
        )


# ---------------------------------------------------------------------------
# PR #337 Codex bot レビュー第8巡対応（P2, 採用）+ 2026-08-30 alternate
# attempt: README の再実行ブロッカー記述が実状態と一致していること。
# ---------------------------------------------------------------------------


def _executor_impl_section() -> str:
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    section = readme.split("**executor/consumer 実装（本PR）**:", 1)
    assert len(section) == 2, "README の executor/consumer 実装節が見つからない"
    return section[1].split("## 設計判断の記録", 1)[0]


def test_alternate_attempt_readme_lists_both_formal_rerun_blockers() -> None:
    """C1 attachment 解消後の2ブロッカーを全数列挙していること。"""
    section = _executor_impl_section()
    assert "現在の再実行ブロッカー" in section
    assert "のみであり" not in section.split("現在の再実行ブロッカー", 1)[1].split(
        "machine-dependent な実装作業", 1
    )[0], "単独ブロッカー主張へ退行している"
    assert "dependency_pins_sha" in section
    assert "PENDING" in section
    assert "alternate diagnostic 実行と後続の正式再凍結" in section
    assert "80a40f" in section
    assert "cdbd779c" not in section.split("現在の再実行ブロッカー", 1)[1].split(
        "machine-dependent な実装作業", 1
    )[0]


def test_alternate_attempt_readme_records_c1_attachment_behavior() -> None:
    """production C1 attachmentとfail-closed監査が記録されていること。"""
    section = _executor_impl_section()
    assert "ZERO_CONTROLPROFILE_SHAM" in section
    assert "CONSUMED_INERT_ZERO_PROFILE" in section
    assert "fail-closed" in section


def test_pr337_r7_readme_remaining_work_does_not_reschedule_implemented_pipeline() -> None:
    """残作業リストが、本 PR で実装済みの 6 分類判定 / identity 距離 /
    固定実行順パイプラインを「未実装の残作業」として再掲していないこと
    （実装済み pipeline と未了の実行/実装を分離する）。"""
    readme = (_RUN_DIR / "README.md").read_text(encoding="utf-8")
    remaining = readme.split("machine-dependent な実装作業:", 1)
    assert len(remaining) == 2, "残作業リストの見出しが見つからない"
    remaining_text = remaining[1].split("**erratum", 1)[0]
    assert "現状は\n  語彙の凍結のみ" not in remaining_text
    assert "実装済み" in remaining_text
    # C0/C1 節は「実装済み部分」と「C1 に残る実装作業」を両方明示する
    c0c1 = remaining_text.split("- **C0/C1 の実 render**:", 1)
    assert len(c0c1) == 2
    c0c1_text = c0c1[1].split("\n- ", 1)[0]
    assert "実装済み" in c0c1_text
    assert "r_sham" in c0c1_text
