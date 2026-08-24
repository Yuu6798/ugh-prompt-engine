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
    # Fix 8: 両 founder が PINNED のとき value（genome_id）は相異が必須の
    # ため、R9F-01/R9F-02 で異なる16hex値を使う。
    fully_pinned["founder_genome_shas"]["R9F-01"] = {
        "value": "a" * 16,
        "status": "PINNED",
        "source": "synthetic-fixture",
    }
    fully_pinned["founder_genome_shas"]["R9F-02"] = {
        "value": "b" * 16,
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
    """Codex bot レビュー PR #315 指摘1: `founder_genome_shas.R9F-0x` は
    16hex genome_id 形式を要求する — 64hex sha256 値を入れると拒否される。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 64, "status": "PINNED", "source": "x"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


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
    fresh_raw["lesson_sha"]["status"] = "PINNED"
    fresh_raw["lesson_sha"]["value"] = "z" * 64  # 非hexだが元dict側だけの改変
    assert contract.raw["lesson_sha"]["status"] == "PENDING"
    assert contract.raw["lesson_sha"] is not fresh_raw["lesson_sha"]


def test_fix4_gate_state_revalidates_and_rejects_direct_raw_tampering(
    contract_raw: Dict[str, Any],
) -> None:
    """Codex bot レビュー PR #315 第2巡指摘1(b): 正常 load 後に
    `contract.raw["lesson_sha"]["status"]` を直接 "PINNED" へ書き換えても
    （value は null のまま）、`gate_state()` は毎回 `contract.raw` を
    `load_run9_contract()` で再検証するため Run9ValidationError を送出する
    （load 済みオブジェクトの raw を直接書き換えて READY を騙る経路の閉塞。
    共有 module fixture の汚染を避けるため、ここではローカルにコピーした
    contract を使う）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    contract.raw["lesson_sha"]["status"] = "PINNED"  # value は null のまま
    with pytest.raises(m.Run9ValidationError):
        m.gate_state(contract)


def test_fix4_gate_state_still_works_on_untampered_contract(contract_raw: Dict[str, Any]) -> None:
    """対照実験: 改変していない contract では `gate_state()` の再検証が
    正常系まで壊していないことの確認（現行 RUN9_CONTRACT.yaml は BLOCKED）。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# PR #315 Codex bot レビュー第2巡対応 — Fix 5: changed_edge の凍結値強制
# ---------------------------------------------------------------------------


def test_fix5_changed_edge_constant_matches_contract() -> None:
    assert m.CHANGED_EDGE == "LEARN_PERFORMANCE"


def test_fix5_current_contract_changed_edge_is_frozen_value(contract_raw: Dict[str, Any]) -> None:
    assert contract_raw["single_intervention"]["changed_edge"] == m.CHANGED_EDGE


def test_fix5_changed_edge_tampering_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘2: `changed_edge` を
    `"REPLACE_IDENTITY"` 等の別エッジへ差し替えた fixture は拒否される
    （DESIGN_RUN9 §23 で凍結された単一介入エッジの改変防止）。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["single_intervention"]["changed_edge"] = "REPLACE_IDENTITY"
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix5_blank_description_rejected(contract_raw: Dict[str, Any]) -> None:
    """Codex bot レビュー PR #315 第2巡指摘2 補足: `description` も
    非空文字列を強制する。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["single_intervention"]["description"] = "   "
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


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
    same_value = "a" * 16
    tampered["founder_genome_shas"]["R9F-01"] = {"value": same_value, "status": "PINNED", "source": "x"}
    tampered["founder_genome_shas"]["R9F-02"] = {"value": same_value, "status": "PINNED", "source": "y"}
    with pytest.raises(m.Run9ValidationError):
        m.load_run9_contract(tampered)


def test_fix8_distinct_founder_genome_shas_when_both_pinned_accepted(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: 相異する value なら両方 PINNED でも通る。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 16, "status": "PINNED", "source": "x"}
    tampered["founder_genome_shas"]["R9F-02"] = {"value": "b" * 16, "status": "PINNED", "source": "y"}
    m.load_run9_contract(tampered)  # 例外を投げないことの確認


def test_fix8_one_pending_one_pinned_does_not_trigger_distinctness_check(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: 片方だけ PINNED（もう片方 PENDING）の場合は相異判定その
    ものが発火しない — 正直な未 pin 表現を妨げない。"""
    tampered = copy.deepcopy(contract_raw)
    tampered["founder_genome_shas"]["R9F-01"] = {"value": "a" * 16, "status": "PINNED", "source": "x"}
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
