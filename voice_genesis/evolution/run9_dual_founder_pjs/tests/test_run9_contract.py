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
    del tampered["lesson_sha"]
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
    good = m.build_founder(_pinned_fixture_domain(), "R9F-01").to_dict()
    tampered = copy.deepcopy(good)
    tampered["coords"] = {"af0": 0.6, "ritsu": 0.3, "pjs": 0.1}
    with pytest.raises(m.Run9ValidationError):
        m.founder_genome_from_dict(tampered)


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
    reconstructed = m.founder_genome_from_dict(json.loads(text))
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


def test_item49_fully_pinned_synthetic_contract_is_ready(contract_raw: Dict[str, Any]) -> None:
    """item 49 対照実験: pre-run 欄を全て PINNED にした合成 contract は
    gate_state() == "READY" になる（BLOCKED が「常に BLOCKED を返す壊れた
    実装」でないことの実証）。"""
    fully_pinned = copy.deepcopy(contract_raw)
    for name in m.CONTRACT_PIN_FIELDS:
        if name in m.CONTRACT_POST_RUN_PIN_FIELDS:
            continue
        fully_pinned[name] = {"value": "x" * 64, "status": "PINNED", "source": "synthetic-fixture"}
    for founder_id in m.CONTRACT_FOUNDER_IDS:
        fully_pinned["founder_genome_shas"][founder_id] = {
            "value": "x" * 16,
            "status": "PINNED",
            "source": "synthetic-fixture",
        }
    contract = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract) == "READY"


def test_item49_single_pending_pre_run_field_blocks_gate(contract_raw: Dict[str, Any]) -> None:
    """item 49 補足: pre-run 欄が1つでも PENDING/BLOCKED なら gate は
    BLOCKED のまま（post-run 欄 artifact_manifest_sha/cost_record_sha は
    PENDING のままでも gate に影響しないことの対照確認）。"""
    fully_pinned = copy.deepcopy(contract_raw)
    for name in m.CONTRACT_PIN_FIELDS:
        if name in m.CONTRACT_POST_RUN_PIN_FIELDS:
            continue
        fully_pinned[name] = {"value": "x" * 64, "status": "PINNED", "source": "synthetic-fixture"}
    for founder_id in m.CONTRACT_FOUNDER_IDS:
        fully_pinned["founder_genome_shas"][founder_id] = {
            "value": "x" * 16,
            "status": "PINNED",
            "source": "synthetic-fixture",
        }
    # post-run 欄は PENDING のままでも READY を妨げないはず。
    contract_ready = m.load_run9_contract(fully_pinned)
    assert m.gate_state(contract_ready) == "READY"

    # 1つの pre-run 欄を PENDING へ戻すと BLOCKED になる。
    regressed = copy.deepcopy(fully_pinned)
    regressed["lesson_sha"] = {"value": None, "status": "PENDING", "reason": "regressed"}
    contract_blocked = m.load_run9_contract(regressed)
    assert m.gate_state(contract_blocked) == "BLOCKED"


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
