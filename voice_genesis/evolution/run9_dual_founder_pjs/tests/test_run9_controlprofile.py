"""test_run9_controlprofile.py — RUN9 Phase 3: `run9_controlprofile.py`
（ControlProfile 基盤・書込境界の機械強制・append-only 台帳）の最低テスト。

音声処理・実学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_controlprofile as cp  # noqa: E402
import run9_schema as m  # noqa: E402


# ---------------------------------------------------------------------------
# Run9ControlProfile / build_neutral_profile
# ---------------------------------------------------------------------------


def test_neutral_profile_deterministic_across_calls() -> None:
    """必須テスト「neutral profile 決定論（2回生成 bit 一致）」。"""
    a = cp.build_neutral_profile("R9F-01")
    b = cp.build_neutral_profile("R9F-01")
    assert a.to_dict() == b.to_dict()
    assert a.profile_id == b.profile_id


def test_neutral_profile_revision_is_r0_with_null_branch_and_parent() -> None:
    profile = cp.build_neutral_profile("R9F-01")
    assert profile.revision == cp.NEUTRAL_REVISION == "r0"
    assert profile.branch is None
    assert profile.parent_revision is None


def test_neutral_profile_partitions_are_empty_and_only_two_keys() -> None:
    profile = cp.build_neutral_profile("R9F-01")
    assert set(profile.partitions.keys()) == {"trait_control", "technique_control"}
    assert profile.partitions["trait_control"] == {}
    assert profile.partitions["technique_control"] == {}


def test_neutral_profile_differs_between_founders() -> None:
    """異なる voice_id からは異なる profile_id が出る（voice_id が
    profile_id 計算へ含まれることの確認）。"""
    a = cp.build_neutral_profile("R9F-01")
    b = cp.build_neutral_profile("R9F-02")
    assert a.profile_id != b.profile_id


def test_neutral_profile_rejects_unknown_voice_id() -> None:
    with pytest.raises(cp.Run9ControlProfileError):
        cp.build_neutral_profile("R9F-03")
    with pytest.raises(cp.Run9ControlProfileError):
        cp.build_neutral_profile("PJS")


# ---------------------------------------------------------------------------
# partitions の構造的制約: IDENTITY_STATE が profile schema に存在しない
# ---------------------------------------------------------------------------


def test_identity_state_key_structurally_absent_from_profile_partition_keys() -> None:
    assert "identity_state" not in cp.PROFILE_PARTITION_KEYS
    assert "IDENTITY_STATE" not in cp.PROFILE_PARTITION_KEYS
    assert set(cp.PROFILE_PARTITION_KEYS) == {"trait_control", "technique_control"}


def test_partitions_with_identity_state_key_rejected_by_from_dict() -> None:
    profile = cp.build_neutral_profile("R9F-01")
    data = profile.to_dict()
    data["partitions"]["identity_state"] = {"anything": 1}
    with pytest.raises(cp.Run9ControlProfileError, match="unknown key"):
        cp.control_profile_from_dict(data)


def test_partitions_missing_a_required_key_rejected() -> None:
    profile = cp.build_neutral_profile("R9F-01")
    data = profile.to_dict()
    del data["partitions"]["trait_control"]
    with pytest.raises(cp.Run9ControlProfileError, match="missing required key"):
        cp.control_profile_from_dict(data)


# ---------------------------------------------------------------------------
# derive_profile: 書込境界の機械強制
# ---------------------------------------------------------------------------


def test_derive_profile_control_rejects_nonempty_updates() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="empty writable partition set"):
        cp.derive_profile(
            r0, "CONTROL", {"technique_control": {}}, control_condition="NO_LEARNING_REPLAY"
        )


def test_derive_profile_control_requires_control_condition() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="control_condition"):
        cp.derive_profile(r0, "CONTROL", {})


def test_derive_profile_control_empty_updates_produces_c0_and_c1_revisions() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    c0 = cp.derive_profile(r0, "CONTROL", {}, control_condition="NO_LEARNING_REPLAY")
    c1 = cp.derive_profile(r0, "CONTROL", {}, control_condition="ZERO_CONTROLPROFILE_SHAM")
    assert c0.revision == "replay"
    assert c1.revision == "r_sham"
    assert c0.branch == "CONTROL"
    assert c1.branch == "CONTROL"
    assert c0.parent_revision == "r0"
    assert c1.parent_revision == "r0"
    # partitions は r0 の恒等値をそのまま引き継ぐ（中立 profile の複製）。
    assert c0.partitions == r0.partitions
    assert c1.partitions == r0.partitions
    assert c0.profile_id != c1.profile_id  # 異なる revision で異なる profile_id


def test_derive_profile_practice_can_write_trait_and_technique() -> None:
    """必須テスト「PRACTICE が Trait/Technique のみ書込可能」（両方成功する
    ことの確認）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(
        r0, "PRACTICE_FROM_AUDIO",
        {"trait_control": {"breathiness": 0.1}, "technique_control": {"vibrato_depth": 0.2}},
    )
    assert child.revision == "r_practice"
    assert child.branch == "PRACTICE_FROM_AUDIO"
    assert child.partitions["trait_control"] == {"breathiness": 0.1}
    assert child.partitions["technique_control"] == {"vibrato_depth": 0.2}


def test_derive_profile_practice_partial_update_preserves_untouched_partition() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    assert child.partitions["trait_control"] == {"x": 1}
    assert child.partitions["technique_control"] == {}  # 未指定 partition は親の値のまま


def test_derive_profile_education_can_write_technique_only() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})
    assert child.revision == "r_taught"
    assert child.partitions["technique_control"] == {"phrasing": "legato"}


def test_derive_profile_education_writing_trait_control_rejected() -> None:
    """必須テスト「EDUCATION→trait 拒否」。fail-closed で
    `run9_schema.Run9ValidationError` が伝播する（`Run9ControlProfileError`
    へラップし直さない）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(m.Run9ValidationError, match="may not write to partition 'TRAIT_CONTROL'"):
        cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"trait_control": {"breathiness": 0.5}})


def test_derive_profile_education_writing_both_partitions_rejected() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(m.Run9ValidationError):
        cp.derive_profile(
            r0, "TRANSFER_TECHNIQUE",
            {"trait_control": {"x": 1}, "technique_control": {"y": 2}},
        )


def test_derive_profile_practice_with_control_condition_rejected() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="control_condition must be None"):
        cp.derive_profile(
            r0, "PRACTICE_FROM_AUDIO", {}, control_condition="NO_LEARNING_REPLAY"
        )


def test_derive_profile_unknown_branch_rejected() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError):
        cp.derive_profile(r0, "NOT_A_BRANCH", {})


def test_derive_profile_unknown_update_partition_key_rejected() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="unknown partition key"):
        cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"identity_state": {}})


def test_derive_profile_matches_branch_write_policy_json() -> None:
    """`derive_profile()` の実際の許可/拒否パターンが
    `inputs/branch_write_policy.json`（run9_schema.BRANCH_WRITABLE_
    PARTITIONS と同期済み）と一致することを、ファイル内容から直接
    駆動して確認する。"""
    policy_text = (_RUN_DIR / "inputs" / "branch_write_policy.json").read_text(encoding="utf-8")
    policy = m.load_branch_write_policy_json(policy_text)
    m.validate_branch_write_policy_manifest(policy)  # 前提: manifest 自体が定数と一致
    r0 = cp.build_neutral_profile("R9F-01")
    partition_key_by_state = {"TRAIT_CONTROL": "trait_control", "TECHNIQUE_CONTROL": "technique_control"}
    for branch, writable in policy["branch_writable_partitions"].items():
        if branch == "CONTROL":
            continue  # CONTROL は別経路（control_condition 必須）でテスト済み
        for state_partition, partition_key in partition_key_by_state.items():
            updates = {partition_key: {"probe": 1}}
            if state_partition in writable:
                child = cp.derive_profile(r0, branch, updates)
                assert child.partitions[partition_key] == {"probe": 1}
            else:
                with pytest.raises(m.Run9ValidationError):
                    cp.derive_profile(r0, branch, updates)


def test_derive_profile_rejects_non_r0_parent_cross_arm_contamination() -> None:
    """必須テスト（Codex bot レビュー PR #318 第1巡 Fix 1）: r_practice を
    親に TRANSFER_TECHNIQUE を導出しようとする枝汚染（cross-arm
    contamination）は拒否される。PoR §4 の all-arms-from-r0 フローの
    機械強制。"""
    r0 = cp.build_neutral_profile("R9F-01")
    practiced = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    with pytest.raises(cp.Run9ControlProfileError, match="全枝は r0 から独立分岐する"):
        cp.derive_profile(practiced, "TRANSFER_TECHNIQUE", {"technique_control": {"y": 2}})


def test_derive_profile_rejects_non_r0_parent_for_control_branch_too() -> None:
    """CONTROL 枝への導出も同じ r0-only 制約を受ける（practiced 済み
    profile を親に replay/r_sham を導出しようとする経路の拒否）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    practiced = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    with pytest.raises(cp.Run9ControlProfileError, match="全枝は r0 から独立分岐する"):
        cp.derive_profile(practiced, "CONTROL", {}, control_condition="NO_LEARNING_REPLAY")


def test_derive_profile_from_r0_still_succeeds_after_fix1() -> None:
    """回帰確認: r0 を親とする正規の導出経路は Fix 1 後も引き続き成功する。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})
    assert child.revision == "r_taught"
    assert child.parent_revision == "r0"


# ---------------------------------------------------------------------------
# derive_profile / partitions: 非有限値（NaN/inf）の拒否（Codex bot
# レビュー PR #318 第2巡 Fix 8）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_derive_profile_rejects_nan_in_updates_top_level(bad_value: float) -> None:
    """必須テスト（Fix 8, 負例1/2）: updates に直接 NaN/inf が含まれる
    derive は拒否される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="non-finite"):
        cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"breathiness": bad_value}})


def test_derive_profile_rejects_nan_nested_inside_updates() -> None:
    """NaN は partition 値の中の任意の深さ（list/dict のネスト）に紛れ込み
    得るため、再帰検証であることも確認する。"""
    r0 = cp.build_neutral_profile("R9F-01")
    with pytest.raises(cp.Run9ControlProfileError, match="non-finite"):
        cp.derive_profile(
            r0, "PRACTICE_FROM_AUDIO",
            {"trait_control": {"nested": {"list": [1, 2, {"deep": float("nan")}]}}},
        )


def test_derive_profile_accepts_ordinary_finite_values() -> None:
    """正例回帰: 通常の有限値（int/float/str/bool/None/nested list・dict）
    は Fix 8 後も引き続き受理される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(
        r0, "PRACTICE_FROM_AUDIO",
        {"trait_control": {"x": 1, "y": 0.5, "z": "ok", "w": True, "v": None, "nested": [1, {"a": 2}]}},
    )
    assert child.partitions["trait_control"]["x"] == 1


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_from_dict_rejects_nan_in_partitions(bad_value: float) -> None:
    """必須テスト（Fix 8, 負例2/2）: NaN/inf を partitions に含む文書は
    `control_profile_from_dict()` でも拒否される（profile_id の再計算
    チェックより前に検出される — 改ざんされた文書がたまたま profile_id を
    合わせ直していなくても、その手前で fail-closed）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    data = child.to_dict()
    data["partitions"]["trait_control"]["x"] = bad_value
    with pytest.raises(cp.Run9ControlProfileError, match="non-finite"):
        cp.control_profile_from_dict(data)


# ---------------------------------------------------------------------------
# control_profile_from_dict: 改ざん検出・revision 語彙
# ---------------------------------------------------------------------------


def test_control_profile_from_dict_roundtrip() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    reconstructed = cp.control_profile_from_dict(child.to_dict())
    assert reconstructed.to_dict() == child.to_dict()


def test_control_profile_from_dict_rejects_profile_id_tampering() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    data = child.to_dict()
    data["partitions"]["trait_control"]["x"] = 999  # profile_id は再計算しない改ざん
    with pytest.raises(cp.Run9ControlProfileError, match="profile_id mismatch"):
        cp.control_profile_from_dict(data)


def test_control_profile_from_dict_rejects_unknown_top_level_key() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    data = r0.to_dict()
    data["extra"] = "unexpected"
    with pytest.raises(cp.Run9ControlProfileError, match="unknown key"):
        cp.control_profile_from_dict(data)


def test_control_profile_from_dict_rejects_invalid_revision() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {})
    data = child.to_dict()
    data["revision"] = "not_a_real_revision"
    with pytest.raises(cp.Run9ControlProfileError, match="revision must be one of"):
        cp.control_profile_from_dict(data)


def test_control_profile_from_dict_rejects_r0_with_nonnull_branch() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    data = r0.to_dict()
    data["branch"] = "CONTROL"
    with pytest.raises(cp.Run9ControlProfileError, match="birth-neutral origin"):
        cp.control_profile_from_dict(data)


def test_control_profile_from_dict_rejects_derived_with_null_parent() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {})
    data = child.to_dict()
    data["parent_revision"] = None
    with pytest.raises(cp.Run9ControlProfileError, match="branch-derived"):
        cp.control_profile_from_dict(data)


def test_valid_revisions_matches_branch_revisions_vocabulary() -> None:
    assert set(cp.VALID_REVISIONS) == {"r0", "replay", "r_sham", "r_practice", "r_taught"}


# ---------------------------------------------------------------------------
# control_profile_from_dict: parent_revision も r0 限定（Codex bot レビュー
# PR #318 第2巡 Fix 6 — derive 側 Fix 1 と対称の loader 側防御）
# ---------------------------------------------------------------------------


def test_from_dict_rejects_non_r0_parent_revision_on_derived_document() -> None:
    """必須テスト（Fix 6）: r_taught 文書が parent_revision=r_practice を
    宣言する（手で組み立てた/改ざんされた文書が、r0 以外を親と主張する）
    枝汚染は、`derive_profile()` を経由しなくても loader 側で拒否される。
    """
    r0 = cp.build_neutral_profile("R9F-01")
    taught = cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})
    data = taught.to_dict()
    assert data["parent_revision"] == "r0"
    data["parent_revision"] = "r_practice"  # r0 以外の親を主張する改ざん
    with pytest.raises(cp.Run9ControlProfileError, match="全枝は r0 から独立分岐する"):
        cp.control_profile_from_dict(data)


def test_from_dict_rejects_non_r0_parent_revision_for_control_branch_too() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    c0 = cp.derive_profile(r0, "CONTROL", {}, control_condition="NO_LEARNING_REPLAY")
    data = c0.to_dict()
    data["parent_revision"] = "r_taught"
    with pytest.raises(cp.Run9ControlProfileError, match="全枝は r0 から独立分岐する"):
        cp.control_profile_from_dict(data)


def test_from_dict_still_accepts_legitimate_r0_parented_documents_after_fix6() -> None:
    """正例回帰: 正当な r0-parented 文書は Fix 6 後も引き続き受理される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    reconstructed = cp.control_profile_from_dict(child.to_dict())
    assert reconstructed.parent_revision == "r0"


# ---------------------------------------------------------------------------
# control_profile_from_dict: branch↔revision 厳密対応（Codex bot レビュー
# PR #318 第1巡 Fix 3）
# ---------------------------------------------------------------------------


def test_from_dict_rejects_transfer_technique_branch_with_r_practice_revision() -> None:
    """必須テスト（Fix 3）: TRANSFER_TECHNIQUE + r_practice の取り違えは
    拒否される（各フィールド単体では正当でも、組合せとして矛盾する
    ケース）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    data = child.to_dict()
    assert data["branch"] == "PRACTICE_FROM_AUDIO" and data["revision"] == "r_practice"
    data["branch"] = "TRANSFER_TECHNIQUE"  # revision は r_practice のまま取り違える
    with pytest.raises(cp.Run9ControlProfileError, match="mismatched"):
        cp.control_profile_from_dict(data)


def test_from_dict_rejects_control_branch_with_r_taught_revision() -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})
    data = child.to_dict()
    data["branch"] = "CONTROL"  # revision は r_taught のまま取り違える
    with pytest.raises(cp.Run9ControlProfileError, match="mismatched"):
        cp.control_profile_from_dict(data)


def test_from_dict_accepts_all_valid_branch_revision_pairs() -> None:
    """正例側の網羅回帰: branch↔revision の正当な全組合せは引き続き
    受理される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    valid_pairs = [
        ("PRACTICE_FROM_AUDIO", "r_practice", {"trait_control": {"x": 1}}),
        ("TRANSFER_TECHNIQUE", "r_taught", {"technique_control": {"y": 2}}),
    ]
    for branch, expected_revision, updates in valid_pairs:
        child = cp.derive_profile(r0, branch, updates)
        assert child.revision == expected_revision
        reconstructed = cp.control_profile_from_dict(child.to_dict())
        assert reconstructed.branch == branch
    for control_condition, expected_revision in [
        ("NO_LEARNING_REPLAY", "replay"),
        ("ZERO_CONTROLPROFILE_SHAM", "r_sham"),
    ]:
        c = cp.derive_profile(r0, "CONTROL", {}, control_condition=control_condition)
        assert c.revision == expected_revision
        reconstructed = cp.control_profile_from_dict(c.to_dict())
        assert reconstructed.branch == "CONTROL"


def test_valid_revisions_for_branch_r0_is_dedicated_neutral_sentinel() -> None:
    """`branch=None`（r0 = 出生中立・枝分岐前）は `CONTROL` 枝の一部では
    なく、r0 専用の扱いとして schema 上明確化されている（Fix 3）。"""
    assert cp._valid_revisions_for_branch(None) == {"r0"}
    assert cp._valid_revisions_for_branch("CONTROL") == {"replay", "r_sham"}
    assert "r0" not in cp._valid_revisions_for_branch("CONTROL")


# ---------------------------------------------------------------------------
# Run9ProfileLedger: append-only・冪等・conflict・親実在検証
# ---------------------------------------------------------------------------


@pytest.fixture()
def ledger(tmp_path: Path) -> cp.Run9ProfileLedger:
    return cp.Run9ProfileLedger(tmp_path / "control_profiles")


def test_ledger_write_creates_file(ledger: cp.Run9ProfileLedger) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    result = ledger.write(r0)
    assert result.created is True
    assert result.path.exists()
    assert result.path.name == f"{r0.profile_id}.json"


def test_ledger_write_is_idempotent_for_identical_content(ledger: cp.Run9ProfileLedger) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    first = ledger.write(r0)
    second = ledger.write(r0)
    assert first.created is True
    assert second.created is False
    assert first.path == second.path


def test_ledger_write_conflict_on_byte_differing_same_id(ledger: cp.Run9ProfileLedger) -> None:
    """必須テスト「ledger の conflict」: 同一 profile_id へ異なるバイト列で
    書き込もうとすると `Run9ProfileLedgerConflictError`。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    path = ledger.path_for(r0.profile_id)
    # 同じ内容だが異なるバイト列（indent違い）を先に手動で置く。
    path.write_text(json.dumps(r0.to_dict(), sort_keys=True, indent=4) + "\n", encoding="utf-8")
    with pytest.raises(cp.Run9ProfileLedgerConflictError):
        ledger.write(r0)


def test_ledger_read_after_write_matches(ledger: cp.Run9ProfileLedger) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    read_back = ledger.read(r0.profile_id)
    assert read_back.to_dict() == r0.to_dict()


def test_ledger_rejects_publish_of_child_with_unpublished_parent(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """必須テスト「親 revision の実在検証」: r0 を publish せずに、その
    r0 から導出した子だけを publish しようとすると拒否される。"""
    r0 = cp.build_neutral_profile("R9F-01")  # publish しない
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    with pytest.raises(cp.Run9ControlProfileError, match="does not exist in the ledger"):
        ledger.write(child)


def test_ledger_accepts_child_after_parent_published(ledger: cp.Run9ProfileLedger) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    result = ledger.write(child)
    assert result.created is True


def test_ledger_r0_cannot_be_updated_in_place(ledger: cp.Run9ProfileLedger) -> None:
    """必須テスト「r0/親の in-place 更新不可」: 一度 publish した r0 を、
    異なる partitions 内容で再度 write しようとすると conflict になる
    （更新 API 自体が存在しないため append-only が構造的に強制される —
    ここでは file レベルの conflict として現れることを実証する）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    # r0 と同じ profile_id を持つファイルへ、異なる中身を直接書き込む
    # （= r0 を in-place で書き換えようとする攻撃のシミュレーション）。
    path = ledger.path_for(r0.profile_id)
    tampered_dict = r0.to_dict()
    tampered_dict["partitions"]["trait_control"] = {"snuck_in": True}
    path.write_bytes((json.dumps(tampered_dict, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    # 元の r0 を改めて write しようとすると、ディスク上は改ざんされた
    # バイト列と異なるため conflict になる（元の内容へ戻すことも
    # 「上書き」であり、そのまま通せば in-place 書き換えを許すのと同じに
    # なるため、意図どおりの拒否）。
    with pytest.raises(cp.Run9ProfileLedgerConflictError):
        ledger.write(r0)


def test_ledger_write_rejects_profile_with_tampered_partitions_via_round_trip(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """`Run9ControlProfile` を直接構築（dataclass コンストラクタ経由）して
    `profile_id` を実際の内容と矛盾させた場合、`write()` の round-trip
    検証が拒否することを確認する。"""
    tampered = cp.Run9ControlProfile(
        schema=cp.SCHEMA_CONTROL_PROFILE, voice_id="R9F-01", branch=None, revision="r0",
        parent_revision=None, partitions={"trait_control": {}, "technique_control": {}},
        profile_id="0" * 16,
    )
    with pytest.raises(cp.Run9ControlProfileError, match="round-trip validation"):
        ledger.write(tampered)


def test_ledger_list_profile_ids_empty_for_fresh_directory(ledger: cp.Run9ProfileLedger) -> None:
    assert ledger.list_profile_ids() == []


def test_ledger_list_profile_ids_after_writes(ledger: cp.Run9ProfileLedger) -> None:
    r0_a = cp.build_neutral_profile("R9F-01")
    r0_b = cp.build_neutral_profile("R9F-02")
    ledger.write(r0_a)
    ledger.write(r0_b)
    assert set(ledger.list_profile_ids()) == {r0_a.profile_id, r0_b.profile_id}


def test_ledger_symlink_escape_rejected(ledger: cp.Run9ProfileLedger, tmp_path: Path) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    outside_target = tmp_path / "outside.json"
    outside_target.write_text(
        json.dumps(r0.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    symlink_path = ledger.path_for(r0.profile_id)
    symlink_path.symlink_to(outside_target)
    with pytest.raises(cp.Run9ControlProfileError, match="symlink"):
        ledger.read(r0.profile_id)


def test_ledger_read_rejects_duplicate_json_keys(ledger: cp.Run9ProfileLedger) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    path = ledger.path_for(r0.profile_id)
    duplicate_key_text = (
        '{"schema": "%s", "schema": "%s", "voice_id": "R9F-01", "branch": null, '
        '"revision": "r0", "parent_revision": null, '
        '"partitions": {"trait_control": {}, "technique_control": {}}, '
        '"profile_id": "%s"}'
    ) % (cp.SCHEMA_CONTROL_PROFILE, cp.SCHEMA_CONTROL_PROFILE, r0.profile_id)
    path.write_text(duplicate_key_text, encoding="utf-8")
    with pytest.raises(cp.Run9ControlProfileError, match="duplicate key"):
        ledger.read(r0.profile_id)


def test_ledger_path_for_rejects_invalid_profile_id(ledger: cp.Run9ProfileLedger) -> None:
    with pytest.raises(cp.Run9ControlProfileError):
        ledger.path_for("not-hex")
    with pytest.raises(cp.Run9ControlProfileError):
        ledger.path_for("a" * 15)  # 1桁不足


def test_ledger_find_by_voice_and_revision_returns_none_when_absent(
    ledger: cp.Run9ProfileLedger,
) -> None:
    assert ledger._find_by_voice_and_revision("R9F-01", "r0") is None


def test_ledger_c0_and_c1_can_both_be_published_from_same_r0(ledger: cp.Run9ProfileLedger) -> None:
    """C0/C1 が両方とも r0 を親として独立に publish できることの確認
    （CONTROL 枝の二条件は互いに衝突しない別 revision）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    c0 = cp.derive_profile(r0, "CONTROL", {}, control_condition="NO_LEARNING_REPLAY")
    c1 = cp.derive_profile(r0, "CONTROL", {}, control_condition="ZERO_CONTROLPROFILE_SHAM")
    result_c0 = ledger.write(c0)
    result_c1 = ledger.write(c1)
    assert result_c0.created is True
    assert result_c1.created is True
    assert set(ledger.list_profile_ids()) == {r0.profile_id, c0.profile_id, c1.profile_id}


# ---------------------------------------------------------------------------
# Run9ProfileLedger: (voice_id, revision) 一意性（Codex bot レビュー PR #318
# 第1巡 Fix 4）
# ---------------------------------------------------------------------------


def test_ledger_rejects_second_publish_with_same_tuple_but_different_content(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """必須テスト（Fix 4, a）: 同一 (voice_id, revision) で内容が異なる
    （= 異なる profile_id を持つ）2 件目の publish は conflict として
    拒否される。1 件目はファイル名（profile_id）が異なるため既存の
    `os.link()` 衝突検出には引っかからない — この一意性検証が無ければ
    静かに両方 publish できてしまっていたはずの経路。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    first = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    second = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 2}})
    assert first.voice_id == second.voice_id == "R9F-01"
    assert first.revision == second.revision == "r_practice"
    assert first.profile_id != second.profile_id  # 内容が違うので profile_id も違う

    result_first = ledger.write(first)
    assert result_first.created is True

    with pytest.raises(cp.Run9ProfileLedgerConflictError, match="already has a different profile_id"):
        ledger.write(second)

    # 拒否された2件目はディスク上に残っていないこと（部分書込み無し）。
    assert set(ledger.list_profile_ids()) == {r0.profile_id, first.profile_id}


def test_ledger_republish_of_same_tuple_same_content_remains_idempotent(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """必須テスト（Fix 4, b）: 同一 (voice_id, revision)・同一内容
    （= 同一 profile_id）を再 publish しても、Fix 4 の一意性検証は
    冪等 no-op の経路を壊さない。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    first = ledger.write(child)
    second = ledger.write(child)
    assert first.created is True
    assert second.created is False
    assert first.path == second.path


# ---------------------------------------------------------------------------
# Run9ProfileLedger: (voice_id, revision) 一意性の原子化 — tuple-alias
# hard link 方式（Codex bot レビュー PR #318 第2巡 Fix 5、第1巡 Fix 4 の
# 是正）。並行 publish の競合は決定論的なユニットテストで直接は書けない
# ため、「tuple-alias ファイルが既に別 profile_id を claim している」
# 分岐を直接叩く単体負例で代替する（docstring の設計判断も参照）。
# ---------------------------------------------------------------------------


def test_ledger_alias_path_naming_convention(ledger: cp.Run9ProfileLedger) -> None:
    path = ledger._alias_path_for("R9F-01", "r_practice")
    assert path == ledger.directory / "byrev_R9F-01_r_practice.link"


def test_ledger_write_creates_tuple_alias_file_claiming_profile_id(
    ledger: cp.Run9ProfileLedger,
) -> None:
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    alias_path = ledger._alias_path_for("R9F-01", "r0")
    assert alias_path.exists()
    assert alias_path.read_bytes() == r0.profile_id.encode("ascii")


def test_ledger_rejects_publish_when_tuple_alias_already_claimed_by_different_profile_id(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """必須テスト（Fix 5）: tuple-alias ファイルが既に別の profile_id を
    claim している状態で publish しようとすると、`os.link()` 衝突検出には
    引っかからない本体ファイル（新しい profile_id なので既存ファイルとは
    無衝突）でも conflict として拒否され、かつこの呼び出しが新規作成した
    本体ファイルは後始末（削除）される（部分生成物を残さない）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})

    # (R9F-01, r_practice) を、child とは無関係な profile_id で先に
    # fabricate して claim しておく（並行 publish が先着した状況の代替）。
    alias_path = ledger._alias_path_for("R9F-01", "r_practice")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    fabricated_profile_id = "0" * 16
    assert fabricated_profile_id != child.profile_id
    alias_path.write_bytes(fabricated_profile_id.encode("ascii"))

    with pytest.raises(cp.Run9ProfileLedgerConflictError, match="already has a different profile_id"):
        ledger.write(child)

    # 後始末の確認: child 用に新規作成されたはずの本体ファイルが残って
    # いないこと（部分生成物を残さない）。
    assert not ledger.path_for(child.profile_id).exists()
    assert set(ledger.list_profile_ids()) == {r0.profile_id}
    # fabricate した alias 自体は（他の書込みの所有物ではないため）ここでは
    # 変更されない — write() は自分が新規作成した本体ファイルのみ後始末する。
    assert alias_path.read_bytes() == fabricated_profile_id.encode("ascii")


def test_ledger_publish_succeeds_after_removing_fabricated_conflicting_alias(
    ledger: cp.Run9ProfileLedger,
) -> None:
    """上記の conflict は恒久的なものではなく、tuple-alias の claim が
    解消されれば同じ内容の publish が正常に成功することを確認する
    （fail-closed が過剰に恒久拒否化していないことの確認）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    alias_path = ledger._alias_path_for("R9F-01", "r_practice")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    alias_path.write_bytes(("0" * 16).encode("ascii"))
    with pytest.raises(cp.Run9ProfileLedgerConflictError):
        ledger.write(child)

    alias_path.unlink()  # fabricated claim を除去（実運用では起こらない手動復旧の代替）
    result = ledger.write(child)
    assert result.created is True
    assert alias_path.read_bytes() == child.profile_id.encode("ascii")


# ---------------------------------------------------------------------------
# practice trace
# ---------------------------------------------------------------------------


def _valid_practice_trace() -> Dict[str, Any]:
    return {
        "schema": cp.SCHEMA_PRACTICE_TRACE,
        "voice_id": "R9F-01",
        "imitation_target_selection_log": [],
        "internal_diff_estimation_log": [],
        "search_history": [],
    }


def test_practice_trace_valid_passes() -> None:
    cp.validate_practice_trace(_valid_practice_trace())


def test_practice_trace_missing_key_rejected() -> None:
    for key in cp.PRACTICE_TRACE_REQUIRED_KEYS:
        trace = _valid_practice_trace()
        del trace[key]
        with pytest.raises(cp.Run9ControlProfileError, match="missing required key"):
            cp.validate_practice_trace(trace)


def test_practice_trace_wrong_schema_rejected() -> None:
    trace = _valid_practice_trace()
    trace["schema"] = "wrong-schema/9.9"
    with pytest.raises(cp.Run9ControlProfileError, match="schema must be exactly"):
        cp.validate_practice_trace(trace)


def test_practice_trace_log_field_must_be_list() -> None:
    trace = _valid_practice_trace()
    trace["search_history"] = "not-a-list"
    with pytest.raises(cp.Run9ControlProfileError, match="must be a list"):
        cp.validate_practice_trace(trace)


def test_practice_trace_invalid_voice_id_rejected() -> None:
    trace = _valid_practice_trace()
    trace["voice_id"] = "PJS"
    with pytest.raises(cp.Run9ControlProfileError):
        cp.validate_practice_trace(trace)


# ---------------------------------------------------------------------------
# Run9ProfileLedger: publish の原子化 — alias をコミットポイント化
# （Codex bot レビュー PR #318 第3巡 Fix 9）。本体書込み①→alias claim②の
# 間の crash / I/O 失敗で生じる「alias 無し孤児本体」を、全読者経路
# （list_profile_ids() / _find_by_voice_and_revision()）から不可視にする
# ことを failure injection で直接検証する。
# ---------------------------------------------------------------------------


def _induce_alias_claim_crash(
    monkeypatch: pytest.MonkeyPatch, ledger: cp.Run9ProfileLedger, profile: cp.Run9ControlProfile,
) -> None:
    """`profile` の本体書込み①（`os.link(tmp_name, path)`）を正常に完了
    させたうえで、alias claim②（`os.link(tmp_alias_name, alias_path)`）の
    直前で `os.link` を crash させる。呼び出し後、本体ファイルは存在するが
    alias が存在しない「孤児」状態になる（Fix 9 failure injection ヘルパー
    — monkeypatch で os.link を例外化する、というレビュー指摘の実装）。"""
    alias_path = ledger._alias_path_for(profile.voice_id, profile.revision)
    real_link = cp.os.link

    def crashing_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if Path(dst) == alias_path:
            raise OSError("simulated crash before alias claim")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(cp.os, "link", crashing_link)
    with pytest.raises(OSError, match="simulated crash"):
        ledger.write(profile)
    monkeypatch.undo()  # 以降の呼び出しは crash させない（実 os.link へ戻す）

    assert ledger.path_for(profile.profile_id).exists()  # ① 本体は書かれている
    assert not alias_path.exists()  # ② alias は claim されていない（孤児）


def test_ledger_orphan_body_invisible_to_readers_after_crash_before_alias_claim(
    ledger: cp.Run9ProfileLedger, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須テスト（Fix 9, ①）: 本体書込み後・alias claim 前の crash で
    生じた孤児本体は、`list_profile_ids()` / `_find_by_voice_and_revision()`
    のどちらの読者経路からも不可視である。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    _induce_alias_claim_crash(monkeypatch, ledger, child)

    assert child.profile_id not in ledger.list_profile_ids()
    assert ledger._find_by_voice_and_revision(child.voice_id, child.revision) is None


def test_ledger_second_profile_claims_tuple_after_orphan_crash(
    ledger: cp.Run9ProfileLedger, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須テスト（Fix 9, ②）: 孤児が存在する状態で同一 (voice_id,
    revision) tuple の別 profile を publish すると、新 profile が alias を
    claim でき、それが唯一の live になる（孤児はそのまま不可視のまま残る
    — 掃除は必須でない）。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    orphan = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    _induce_alias_claim_crash(monkeypatch, ledger, orphan)

    second = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 2}})
    assert second.profile_id != orphan.profile_id
    result = ledger.write(second)
    assert result.created is True

    found = ledger._find_by_voice_and_revision(second.voice_id, second.revision)
    assert found is not None and found.profile_id == second.profile_id
    assert orphan.profile_id not in ledger.list_profile_ids()
    assert second.profile_id in ledger.list_profile_ids()


def test_ledger_republish_of_orphaned_profile_recovers_idempotently(
    ledger: cp.Run9ProfileLedger, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """必須テスト（Fix 9, ③）: 孤児化した profile 自身の republish（crash
    しなかった経路の再実行に相当）は正常に成功し、alias claim から冪等に
    回復する。"""
    r0 = cp.build_neutral_profile("R9F-01")
    ledger.write(r0)
    orphan = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    _induce_alias_claim_crash(monkeypatch, ledger, orphan)

    result = ledger.write(orphan)
    assert result.path == ledger.path_for(orphan.profile_id)
    assert orphan.profile_id in ledger.list_profile_ids()
    found = ledger._find_by_voice_and_revision(orphan.voice_id, orphan.revision)
    assert found is not None and found.profile_id == orphan.profile_id


# ---------------------------------------------------------------------------
# loader (control_profile_from_dict): 枝書き込み境界をパーティション内容へ
# 適用する（Codex bot レビュー PR #318 第3巡 Fix 10）。derive_profile() を
# 経由しない手組み/改ざん文書が、profile_id さえ再計算に通せば禁止
# パーティションへ非 neutral な内容を直接公開できてしまう不備の是正。
# ---------------------------------------------------------------------------


def test_from_dict_rejects_hand_assembled_r_taught_with_nonempty_trait_control() -> None:
    """必須テスト（Fix 10, 負例1）: `derive_profile()` を経由しない手組みの
    TRANSFER_TECHNIQUE/r_taught 文書に非空 trait_control を入れ、
    profile_id もその内容に対して正しく再計算し直した場合でも拒否される
    （id 再計算の正しさとは独立に、書込境界違反そのもので拒否されることの
    確認）。"""
    partitions = {"trait_control": {"breathiness": 0.9}, "technique_control": {"phrasing": "legato"}}
    profile_id = cp._compute_profile_id(
        voice_id="R9F-01", branch="TRANSFER_TECHNIQUE", revision="r_taught",
        parent_revision="r0", partitions=partitions,
    )
    data = {
        "schema": cp.SCHEMA_CONTROL_PROFILE, "voice_id": "R9F-01", "branch": "TRANSFER_TECHNIQUE",
        "revision": "r_taught", "parent_revision": "r0", "partitions": partitions,
        "profile_id": profile_id,
    }
    with pytest.raises(cp.Run9ControlProfileError, match="not writable"):
        cp.control_profile_from_dict(data)


def test_from_dict_rejects_control_branch_document_with_nonneutral_partition() -> None:
    """必須テスト（Fix 10, 負例2）: CONTROL 枝（writable partition 集合は
    空）の revision 文書に非 neutral な partition（trait/technique どちらも
    書込不可）が入っていれば、profile_id が正しく再計算されていても
    拒否される。"""
    partitions = {"trait_control": {}, "technique_control": {"vibrato_depth": 0.3}}
    profile_id = cp._compute_profile_id(
        voice_id="R9F-01", branch="CONTROL", revision="replay",
        parent_revision="r0", partitions=partitions,
    )
    data = {
        "schema": cp.SCHEMA_CONTROL_PROFILE, "voice_id": "R9F-01", "branch": "CONTROL",
        "revision": "replay", "parent_revision": "r0", "partitions": partitions,
        "profile_id": profile_id,
    }
    with pytest.raises(cp.Run9ControlProfileError, match="not writable"):
        cp.control_profile_from_dict(data)


def test_from_dict_rejects_r0_document_with_nonempty_partition() -> None:
    """必須テスト（Fix 10 拡張）: r0（出生中立）文書はどちらの partition も
    neutral（空 dict）でなければならない — writable 集合を持たない起点
    そのものへの直接汚染も拒否する。"""
    partitions = {"trait_control": {"breathiness": 0.1}, "technique_control": {}}
    profile_id = cp._compute_profile_id(
        voice_id="R9F-01", branch=None, revision="r0", parent_revision=None, partitions=partitions,
    )
    data = {
        "schema": cp.SCHEMA_CONTROL_PROFILE, "voice_id": "R9F-01", "branch": None,
        "revision": "r0", "parent_revision": None, "partitions": partitions,
        "profile_id": profile_id,
    }
    with pytest.raises(cp.Run9ControlProfileError, match="not writable"):
        cp.control_profile_from_dict(data)


def test_from_dict_accepts_education_document_changing_only_technique_control() -> None:
    """正例（Fix 10）: EDUCATION（TRANSFER_TECHNIQUE）が writable な
    technique_control のみを変更した正当な文書は引き続き受理される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    taught = cp.derive_profile(r0, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})
    reconstructed = cp.control_profile_from_dict(taught.to_dict())
    assert reconstructed.partitions["technique_control"] == {"phrasing": "legato"}
    assert reconstructed.partitions["trait_control"] == {}


# ---------------------------------------------------------------------------
# derive_profile: parent 検証を revision ラベルから正準全形照合へ
# （Codex bot レビュー PR #318 第3巡 Fix 11）。合わせて Run9ControlProfile
# 構築時の partitions 深いコピー + 不変ビュー化（防御的二重検証）。
# ---------------------------------------------------------------------------


def test_derive_profile_rejects_parent_with_mutated_neutral_partitions() -> None:
    """必須テスト（Fix 11 項目1, 負例）: `revision=="r0"` というラベルは
    正しくても、partitions の中身が正準 neutral 全形と一致しない parent
    （改変コピーを手組みして直接構築した Run9ControlProfile — nested dict
    の凍結により in-place 変異が不可能な場合の代替）からの derive は拒否
    される。"""
    r0 = cp.build_neutral_profile("R9F-01")
    tampered = cp.Run9ControlProfile(
        schema=r0.schema, voice_id=r0.voice_id, branch=r0.branch, revision=r0.revision,
        parent_revision=r0.parent_revision,
        partitions={"trait_control": {"injected": True}, "technique_control": {}},
        profile_id=r0.profile_id,  # revision ラベル・profile_id は r0 のまま温存
    )
    with pytest.raises(cp.Run9ControlProfileError, match="canonical"):
        cp.derive_profile(tampered, "TRANSFER_TECHNIQUE", {"technique_control": {"phrasing": "legato"}})


def test_derive_profile_from_canonical_r0_still_succeeds_after_fix11() -> None:
    """正例回帰: 正規の（改変されていない）neutral parent からの derive は
    Fix 11 後も引き続き成功する。"""
    r0 = cp.build_neutral_profile("R9F-01")
    child = cp.derive_profile(r0, "PRACTICE_FROM_AUDIO", {"trait_control": {"x": 1}})
    assert child.revision == "r_practice"
    assert child.parent_revision == "r0"


def test_run9_control_profile_partitions_reject_direct_mutation() -> None:
    """必須テスト（Fix 11 項目2, 防御的二重検証）: 構築後に
    `profile.partitions["trait_control"]["injected"] = ...` のような直接
    変異を試みると、`Run9ControlProfile.__post_init__` の不変ビュー化
    （`types.MappingProxyType`）により `TypeError` で拒否され、profile
    自身の内部状態は変化しない。"""
    r0 = cp.build_neutral_profile("R9F-01")
    original = dict(r0.partitions["trait_control"])
    with pytest.raises(TypeError):
        r0.partitions["trait_control"]["injected"] = True
    assert dict(r0.partitions["trait_control"]) == original


def test_run9_control_profile_deep_copies_partitions_at_construction_time() -> None:
    """必須テスト（Fix 11 項目2）: `Run9ControlProfile` を直接構築する際に
    渡した dict を、呼び出し元がその後に書き換えても profile 自身の内部
    状態には影響しない（構築時の deep copy によるエイリアス切断）。"""
    original = {"trait_control": {"x": 1}, "technique_control": {}}
    profile = cp.Run9ControlProfile(
        schema=cp.SCHEMA_CONTROL_PROFILE, voice_id="R9F-01", branch=None, revision="r0",
        parent_revision=None, partitions=original, profile_id="0" * 16,
    )
    original["trait_control"]["x"] = 999  # 呼び出し元がその後 dict を書き換える
    assert profile.partitions["trait_control"]["x"] == 1  # profile 自身の状態は不変


# ---------------------------------------------------------------------------
# sibling import 流儀 / VG-E0 非依存の確認
# ---------------------------------------------------------------------------


def test_run9_controlprofile_does_not_import_vg_e0_modules() -> None:
    """`run9_controlprofile.py` が VG-E0 の `models`/`ledger`/`operators`/
    `simplex` をモジュールレベルで import しないことの確認（run9_schema.py
    の既存回帰テストと同型の非依存性検証 — run-local な意味論の独立実装
    という設計判断をコード上でも保証する）。"""
    source = (_RUN_DIR / "run9_controlprofile.py").read_text(encoding="utf-8")
    forbidden_imports = ("import models", "import ledger", "import operators", "import simplex")
    for forbidden in forbidden_imports:
        assert forbidden not in source, f"run9_controlprofile.py imports VG-E0 module: {forbidden!r}"


def test_run9_controlprofile_module_docstring_present() -> None:
    import run9_controlprofile as module

    assert module.__doc__ is not None and "ControlProfile" in module.__doc__
