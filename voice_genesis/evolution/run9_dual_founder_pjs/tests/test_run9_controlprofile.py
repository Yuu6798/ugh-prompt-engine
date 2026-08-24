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
