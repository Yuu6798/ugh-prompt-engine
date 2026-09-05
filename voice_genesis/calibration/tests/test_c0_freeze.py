"""c0_freeze.py のテスト（設計正本 §3, §7, §18, IMPLEMENTATION_MAP §6.3）。

**授権境界**（IMPLEMENTATION_MAP §0）: `armed_freeze` を武装実行するテストは
すべて `tmp_path` 配下の test-local な approval_dir/secret_dir/campaigns_dir に
対してのみ行う。本リポジトリへの実 freeze・secret 生成は一切行わない。
`dry_run` は書込を行わないため本リポジトリに対して直接実行してよい。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from voice_genesis.calibration import approvals, c0_freeze, c0_validate
from voice_genesis.calibration.candidates import registry as candidate_registry
from voice_genesis.calibration.splitter import realize_split, verify_split

_REPO_ROOT = c0_freeze._REPO_ROOT
_DESIGN_SHA = hashlib.sha256(
    (_REPO_ROOT / approvals.DESIGN_DOC_RELATIVE_PATH).read_bytes()
).hexdigest()
_MEMO_SHA = hashlib.sha256((_REPO_ROOT / approvals.MEMO_RELATIVE_PATH).read_bytes()).hexdigest()

#: gate1-dependent の manifest 欠落理由（cost_caps/stop_rules 未承認時に必ず
#: 出るブロック理由。dry-run/armed の pre-Gate-1 状態で自明に現れる）。
#: `e_use_table:` も同じカテゴリに属する（Part A/D1b）: リポジトリ実物の
#: `config/e_use_table_v1.json` は `USER_ACCEPTED_USE_BOUND` 行を含むため、
#: Gate 1 未承認（`e_use_bound_accepted` 不在 → `False` 扱い）状態では
#: `_check_e_use_table()` が必ずこの prefix の違反を出す。
_GATE1_DEPENDENT_REASON_PREFIXES = (
    "frozen_design.cost_caps",
    "frozen_design.stop_rules",
    "e_use_table:",
)


@pytest.fixture()
def clean_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repo.dirty_tree=False` の確定 checkout 状態へ固定する（開発中の実
    checkout は常に dirty なため、armed 経路のテストはこれで隔離する）。"""

    def fake_identity(repo_root: Path | None = None) -> tuple[str, bool, None]:
        return "a" * 40, False, None

    monkeypatch.setattr(c0_validate, "_inspect_checkout_identity", fake_identity)


#: PR レビュー第 5 巡: gate1/gate2 は同一 authorization_nonce を要求する。
_DEFAULT_NONCE = "test-nonce-c0freeze-000000"


def _write_gate1(
    approval_dir: Path, *, nonce: str = _DEFAULT_NONCE, scope: list[str] | None = None
) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "authorization_nonce": nonce,
        "cost_caps": {
            "compute": 36000.0,
            "storage": 1_000_000_000,
            "budget": 1.0,
            "budget_accounting_mode": "local_zero_cost",
        },
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"] if scope is None else scope,
    }
    (approval_dir / "gate1_campaign_execution.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_gate2(approval_dir: Path, manifest_core_sha: str, *, nonce: str = _DEFAULT_NONCE) -> None:
    payload = {
        "gate": "GATE2_C0_FREEZE",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "authorization_nonce": nonce,
        "manifest_core_sha": manifest_core_sha,
    }
    (approval_dir / "gate2_c0_freeze.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# dry_run — real repo, no writes
# ---------------------------------------------------------------------------


def test_dry_run_builds_manifest_on_real_repo(tmp_path: Path) -> None:
    report = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    assert isinstance(report.manifest, dict)
    assert report.manifest_core_sha
    assert report.campaign_id.startswith("RUN10-CAL-")


def test_dry_run_blocking_reasons_are_only_gate1_dependent_or_dirty_tree(tmp_path: Path) -> None:
    """Gate 1/2 未承認の dry-run は EXPECTED に blocked。唯一許容されるブロック
    理由は gate1-dependent (cost_caps/stop_rules) と、テスト実行時の checkout
    が実際に dirty である場合の dirty_tree のみ（開発中の実 checkout は常に
    dirty なため、これをテスト失敗にしない — ロバストな assertion）。"""
    report = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    assert report.validation.is_blocked

    def _is_allowed(reason: str) -> bool:
        if reason.startswith(_GATE1_DEPENDENT_REASON_PREFIXES):
            return True
        if "dirty_tree" in reason or "dirty-tree" in reason or "checkout_identity" in reason:
            return True
        return False

    disallowed = [r for r in report.validation.missing_required_keys if not _is_allowed(r)]
    assert disallowed == [], f"unexpected blocking reason(s) pre-Gate-1: {disallowed}"


def test_dry_run_no_side_effects(tmp_path: Path) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    before = set(approval_dir.iterdir())
    c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    after = set(approval_dir.iterdir())
    assert before == after == set()


def test_dry_run_determinism_same_manifest_core_sha(tmp_path: Path) -> None:
    report1 = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    report2 = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    assert report1.manifest_core_sha == report2.manifest_core_sha


def test_fixture_spec_confound_axes_is_the_flat_invariance_axis_tuple() -> None:
    """UNDERSPEC-CAL-D76 (supersedes D75 ruling (1)): `confound_axes` reverts
    to the flat, family-uniform 6-tuple (gate4' invariance-axis declaration
    only) — D75's `declared_sweeps_by_family()`-as-`confound_axes` mapping
    was a category error (nuisance axis != DIRECTIONAL sweep)."""
    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    expected = ["f0_hz", "sr_hz", "gain_dbfs", "duration_s", "noise_snr_db", "context"]
    for entry in fixture_spec.values():
        assert list(entry["confound_axes"]) == expected


def test_fixture_spec_declared_sweeps_matches_declared_sweeps_by_family() -> None:
    """UNDERSPEC-CAL-D76 ruling (2): `frozen_design.fixture_spec.<FAMILY>.
    declared_sweeps` (a new key, separate from `confound_axes`) must be
    exactly `fixtures.matrix.declared_sweeps_by_family(build_matrix())
    [FAMILY]` (def A: truth-core block, nuisance-constant series) — the
    single canonical derivation `c0_freeze.py`/`campaign/cli.py` both share.
    """
    from voice_genesis.calibration.fixtures.axes import FixtureFamily
    from voice_genesis.calibration.fixtures.matrix import build_matrix, declared_sweeps_by_family

    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    declared = declared_sweeps_by_family(build_matrix())
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    for family in FixtureFamily:
        recorded = fixture_spec[family.value]["declared_sweeps"]
        expected = {
            sweep_id: list(row_ids) for sweep_id, row_ids in declared[family.value].items()
        }
        assert recorded == expected
        assert len(recorded) >= 1, f"{family.value} declares no sweeps"


def test_fixture_spec_claim_relevant_fields_matches_machine_derivation() -> None:
    """v1.1 §V2.2 5th bullet: `frozen_design.fixture_spec.<FAMILY>.
    claim_relevant_fields` must be exactly `fixtures.matrix.
    claim_relevant_fields_by_family(build_matrix())[FAMILY]` — same
    "declared value == machine-derived value" invariant as
    `declared_sweeps` above.
    """
    from voice_genesis.calibration.fixtures.axes import FixtureFamily
    from voice_genesis.calibration.fixtures.matrix import build_matrix, claim_relevant_fields_by_family

    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    derived = claim_relevant_fields_by_family(build_matrix())
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    for family in FixtureFamily:
        assert fixture_spec[family.value]["claim_relevant_fields"] == list(
            derived[family.value]
        )


# ---------------------------------------------------------------------------
# v1.1 §V3.3 (WP2e): U_GT / U_num C0 freeze
# ---------------------------------------------------------------------------

_U_ABSENT_FAMILIES = {"RESONANCE_GT", "IDENTITY_CAUSAL_SWEEP"}
_U_NUMERIC_FAMILIES = {
    "F0_CONTROL",
    "FORMANT_GT",
    "TILT_GT",
    "APERIODICITY_GT",
    "TRANSITION_GT",
}


def test_fixture_spec_u_gt_u_num_present_with_formula_for_all_families() -> None:
    """v1.1 §V3.3: 全 7 family が `u_gt_bound`/`u_num_bound` を、値と導出式
    (`*_formula`) + 単位 (`*_unit`) を併記して凍結する（v1.0 §10.2「値と
    導出式の両方」要件）。`manifest_core_sha` の対象である `frozen_design.
    fixture_spec` に載ることも確認する（dry-run/armed 双方が共有する
    `build_manifest()` の core payload そのもの）。"""
    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    assert set(fixture_spec) == _U_ABSENT_FAMILIES | _U_NUMERIC_FAMILIES
    for family_name, entry in fixture_spec.items():
        for key in ("u_gt_bound", "u_num_bound"):
            assert key in entry, f"{family_name} missing {key}"
            assert isinstance(entry[f"{key}_formula"], str) and entry[f"{key}_formula"]
            assert isinstance(entry[f"{key}_unit"], str) and entry[f"{key}_unit"]


def test_fixture_spec_u_gt_u_num_values_are_finite_nonneg_or_absent() -> None:
    """非 ABSENT family は non-negative finite number、ABSENT family は
    `declared_u_gt_u_num_for_family()` が非 numeric として弾く
    `"ABSENT:<reason>"` 文字列に限定される（v1.1 §V3.3）。"""
    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    for family_name in _U_NUMERIC_FAMILIES:
        entry = fixture_spec[family_name]
        for key in ("u_gt_bound", "u_num_bound"):
            value = entry[key]
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                family_name,
                key,
                value,
            )
            assert math.isfinite(value) and value >= 0.0, (family_name, key, value)
    for family_name in _U_ABSENT_FAMILIES:
        entry = fixture_spec[family_name]
        for key in ("u_gt_bound", "u_num_bound"):
            assert isinstance(entry[key], str) and entry[key].startswith("ABSENT:")


def test_fixture_spec_u_gt_u_num_canonical_values_regression_lock() -> None:
    """456 セル canonical fixture design での実値表を固定する（回帰ガード）。
    `c0_freeze.py`/`fixtures/axes.py` の凍結定数を変えたら、この期待値も
    設計判断として明示的に見直すこと（値の意味は WP2e 報告に記載）。"""
    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    fixture_spec = manifest["frozen_design"]["fixture_spec"]
    expected = {
        "F0_CONTROL": (0.0, pytest.approx(0.523251)),
        "FORMANT_GT": (0.0, pytest.approx(3.0)),
        "TILT_GT": (0.0, pytest.approx(0.024)),
        "APERIODICITY_GT": (pytest.approx(0.06363961030678927), pytest.approx(0.0006)),
        "TRANSITION_GT": (0.0, pytest.approx(0.00065)),
    }
    for family_name, (expected_u_gt, expected_u_num) in expected.items():
        entry = fixture_spec[family_name]
        assert entry["u_gt_bound"] == expected_u_gt, family_name
        assert entry["u_num_bound"] == expected_u_num, family_name


def test_dry_run_gate1_approved_reduces_blocking(tmp_path: Path) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert not any(
        r.startswith(_GATE1_DEPENDENT_REASON_PREFIXES) for r in report.validation.missing_required_keys
    )


# ---------------------------------------------------------------------------
# armed_freeze — AUTHORIZATION_REQUIRED (no side effects)
# ---------------------------------------------------------------------------


def test_armed_freeze_without_any_factor_is_authorization_required(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=False,
        env={},
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.AUTHORIZATION_REQUIRED
    assert result.campaign_dir is None
    assert result.secret_dir is None
    assert not secret_dir.exists()
    assert not campaigns_dir.exists()


def test_armed_freeze_cli_armed_but_no_env_or_file_is_authorization_required(
    tmp_path: Path, clean_checkout: None
) -> None:
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env={},
        approval_dir=tmp_path / "approvals",
        secret_dir=tmp_path / "secrets",
        campaigns_dir=tmp_path / "campaigns",
    )
    assert result.outcome == c0_freeze.FreezeOutcome.AUTHORIZATION_REQUIRED
    assert not (tmp_path / "secrets").exists()
    assert not (tmp_path / "campaigns").exists()


# ---------------------------------------------------------------------------
# armed_freeze — full publish path
# ---------------------------------------------------------------------------


def _prepare_armed(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()

    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert not report.validation.is_blocked, report.validation.missing_required_keys
    _write_gate2(approval_dir, report.manifest_core_sha)

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    return approval_dir, secret_dir, campaigns_dir, env


def test_armed_freeze_publishes(tmp_path: Path, clean_checkout: None) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    assert result.campaign_dir is not None and result.campaign_dir.is_dir()
    assert result.secret_dir is not None and result.secret_dir.is_dir()

    for name in ("c0_manifest.json", "realized_split.json", "ledger.jsonl"):
        assert (result.campaign_dir / name).is_file()

    # No .staging-* left behind anywhere.
    assert not any(p.name.startswith(".staging-") for p in campaigns_dir.iterdir())
    assert not any(p.name.startswith(".staging-") for p in secret_dir.iterdir())


def test_armed_freeze_secret_dir_and_file_permissions(tmp_path: Path, clean_checkout: None) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED
    assert oct(result.secret_dir.stat().st_mode & 0o777) == "0o700"
    for name in ("split_secret.bin", "render_root_secret.bin"):
        f = result.secret_dir / name
        assert oct(f.stat().st_mode & 0o777) == "0o600"


def test_armed_freeze_commitments_match_secret_file_hashes(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED
    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    commitments = manifest["commitments"]
    split_secret_bytes = (result.secret_dir / "split_secret.bin").read_bytes()
    render_secret_bytes = (result.secret_dir / "render_root_secret.bin").read_bytes()
    assert commitments["split_secret_sha256"] == hashlib.sha256(split_secret_bytes).hexdigest()
    assert commitments["render_root_secret_sha256"] == hashlib.sha256(render_secret_bytes).hexdigest()


def test_armed_freeze_no_secret_bytes_leak_into_published_manifest_or_ledger(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED
    split_secret_bytes = (result.secret_dir / "split_secret.bin").read_bytes()
    render_secret_bytes = (result.secret_dir / "render_root_secret.bin").read_bytes()

    for name in ("c0_manifest.json", "realized_split.json", "ledger.jsonl"):
        raw = (result.campaign_dir / name).read_bytes()
        assert split_secret_bytes not in raw
        assert render_secret_bytes not in raw


def test_armed_freeze_verify_split_round_trips(tmp_path: Path, clean_checkout: None) -> None:
    from voice_genesis.calibration.fixtures.matrix import build_matrix

    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED

    split_secret_bytes = (result.secret_dir / "split_secret.bin").read_bytes()
    split_raw = json.loads((result.campaign_dir / "realized_split.json").read_text(encoding="utf-8"))
    realized = c0_freeze._realized_split_from_dict(split_raw)

    matrix_rows = build_matrix()
    row_inputs = c0_freeze._row_inputs_for_split(matrix_rows, c0_freeze.STRATUM_FACTOR_NAMES)
    assert verify_split(row_inputs, split_secret_bytes, realized)

    # And the manifest's inlined realized_split must be byte-identical to the
    # convenience copy (`realized_split.json`).
    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert manifest["realized_split"] == split_raw
    assert manifest["realized_split_sha"] == realized.realized_sha


def test_manifest_core_sha_round_trips_from_full_manifest(
    tmp_path: Path, clean_checkout: None
) -> None:
    """PR レビュー第 4 巡: dry-run の core_sha は、armed 後の full manifest から
    `approvals`/`commitments`/`realized_split`/`realized_split_sha`/
    `campaign_id` を除いて再計算した sha と一致する。"""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    dry_report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, env)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED
    full_manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert c0_freeze.manifest_core_sha(full_manifest) == dry_report.manifest_core_sha
    stripped = c0_freeze.core_payload(full_manifest)
    assert set(stripped) & c0_freeze._CORE_ONLY_EXCLUDED_KEYS == set()


def test_armed_freeze_holdout_sweeps_is_non_core_and_matches_k_hold(
    tmp_path: Path, clean_checkout: None
) -> None:
    """v1.1 §V2.2: `holdout_sweeps` is a split_secret-dependent, non-core
    top-level key (same placement rationale as `realized_split` — see the
    `_CORE_ONLY_EXCLUDED_KEYS` docstring) attached only at `armed_freeze()`
    time. It must (a) be absent from `dry_run()`'s manifest (no secret
    exists yet there), (b) be stripped by `core_payload()`/excluded from
    `manifest_core_sha`, (c) declare exactly `k_hold` pinned sweeps per
    family (§V2.2 frozen table), and (d) have every member row_id assigned
    to HOLDOUT in the same manifest's `realized_split.assignment` (§V2.3).
    """
    from voice_genesis.calibration.fixtures.axes import FixtureFamily
    from voice_genesis.calibration.fixtures.matrix import build_matrix, holdout_pin_params_by_family

    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    dry_report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, env)
    assert "holdout_sweeps" not in dry_report.manifest

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED
    full_manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))

    holdout_sweeps = full_manifest["holdout_sweeps"]
    assert "holdout_sweeps" not in c0_freeze.core_payload(full_manifest)

    params = holdout_pin_params_by_family(build_matrix())
    assignment = full_manifest["realized_split"]["assignment"]
    for family in FixtureFamily:
        family_sweeps = holdout_sweeps[family.value]
        assert len(family_sweeps) == params[family.value].k_hold, family.value
        for member_row_ids in family_sweeps.values():
            for rid in member_row_ids:
                assert assignment[rid] == "HOLDOUT", (family.value, rid)

    # end-to-end: the secret-independent structural checks (matched against
    # `declared_sweeps`/k_hold) and the realized-membership check both pass
    # against this real, production-shaped manifest.
    validation = c0_validate.validate_c0_manifest(full_manifest)
    assert validation.holdout_pin_declaration_violations == ()
    assert validation.holdout_pin_membership_violations == ()
    assert validation.holdout_pin_feasibility_violations == ()


def test_armed_freeze_manifest_core_sha_mismatch_refused(tmp_path: Path, clean_checkout: None) -> None:
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    _write_gate1(approval_dir)
    _write_gate2(approval_dir, "f" * 64)  # wrong sha, doesn't match freshly built manifest

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.MANIFEST_CORE_SHA_MISMATCH
    assert not secret_dir.exists() or list(secret_dir.iterdir()) == []
    assert not campaigns_dir.exists() or list(campaigns_dir.iterdir()) == []


def test_armed_freeze_rejects_replayed_authorization_nonce(
    tmp_path: Path, clean_checkout: None
) -> None:
    """PR レビュー第 5 巡: 承認の一回性。同一承認ファイル（同一 nonce）で
    2 回 `armed_freeze` すると、2 回目は `NONCE_ALREADY_USED` で拒否され、
    新しい campaign は一切作られない。"""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    first = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert first.outcome == c0_freeze.FreezeOutcome.PUBLISHED, first.detail
    # `campaigns_dir/.publish.lock` (bug fix P2 #3: now the authoritative lock)
    # is legitimate bookkeeping, not a campaign — filter it out same as
    # `secret_dir`'s own `.publish.lock` below.
    published_campaign_ids = {
        p.name for p in campaigns_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME
    }

    second = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert second.outcome == c0_freeze.FreezeOutcome.NONCE_ALREADY_USED, second.detail
    assert second.campaign_dir is None
    assert second.secret_dir is None
    # No new campaign/secret directory was created by the rejected second call.
    assert {
        p.name for p in campaigns_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME
    } == published_campaign_ids
    assert {p.name for p in secret_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME} == (
        published_campaign_ids
    )


def test_dry_run_authorization_nonce_is_fresh_each_call(tmp_path: Path) -> None:
    report1 = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    report2 = c0_freeze.dry_run(_REPO_ROOT, tmp_path, os.environ)
    assert report1.authorization_nonce != report2.authorization_nonce
    assert len(report1.authorization_nonce) == 32  # secrets.token_hex(16)


# ---------------------------------------------------------------------------
# armed_freeze — publication failure injection
# ---------------------------------------------------------------------------


def test_armed_freeze_publication_failure_on_campaign_replace_rolls_back_everything(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2 回目の `os.replace`（campaign 側）で例外注入 → 公開済み secret dir も
    ロールバックされ、staging も secret も campaign も一切残らない。"""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src: object, dst: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("injected failure on campaign-side os.replace")
        real_replace(src, dst)

    monkeypatch.setattr(c0_freeze.os, "replace", flaky_replace)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLICATION_FAILED
    assert call_count["n"] == 2

    # `.publish.lock` itself is legitimate (empty, not secret material, created
    # by `_publish_lock()` — now also under `campaigns_dir` since it holds the
    # authoritative lock, bug fix P2 #3); everything else must be gone.
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = [
        p for p in (campaigns_dir.iterdir() if campaigns_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == [], f"secret dir not empty: {remaining_secret}"
    assert remaining_campaign == [], f"campaigns dir not empty: {remaining_campaign}"


def test_armed_freeze_publication_failure_on_first_replace_leaves_nothing(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    def always_fail(src: object, dst: object) -> None:
        raise OSError("injected failure")

    monkeypatch.setattr(c0_freeze.os, "replace", always_fail)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLICATION_FAILED
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = [
        p for p in (campaigns_dir.iterdir() if campaigns_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == []
    assert remaining_campaign == []


# ---------------------------------------------------------------------------
# detect_orphans
# ---------------------------------------------------------------------------


def test_detect_orphans_deletes_stale_secret_with_no_lock_held(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    orphan = secret_dir / "RUN10-CAL-STALE"
    orphan.mkdir()
    (orphan / ".publishing").write_text("", encoding="utf-8")

    report = c0_freeze.detect_orphans(secret_dir, campaigns_dir)
    assert report.deleted_orphan_secret_ids == ("RUN10-CAL-STALE",)
    assert not orphan.exists()


def test_detect_orphans_reports_campaign_without_secret(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    (campaigns_dir / "RUN10-CAL-ORPHANCAMP").mkdir()

    report = c0_freeze.detect_orphans(secret_dir, campaigns_dir)
    assert report.orphan_campaign_ids == ("RUN10-CAL-ORPHANCAMP",)
    assert report.deleted_orphan_secret_ids == ()
    # The campaign dir itself is never touched by detect_orphans.
    assert (campaigns_dir / "RUN10-CAL-ORPHANCAMP").exists()


def test_detect_orphans_skips_when_lock_held(tmp_path: Path) -> None:
    import fcntl

    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    orphan = secret_dir / "RUN10-CAL-STALE"
    orphan.mkdir()
    (orphan / ".publishing").write_text("", encoding="utf-8")

    lock_path = secret_dir / c0_freeze._PUBLISH_LOCK_NAME
    with open(lock_path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            report = c0_freeze.detect_orphans(secret_dir, campaigns_dir)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    assert report == c0_freeze.OrphanReport(orphan_campaign_ids=(), deleted_orphan_secret_ids=())
    assert orphan.exists()  # untouched while lock was held elsewhere


def test_detect_orphans_removes_leftover_marker_on_paired_dirs(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    paired = secret_dir / "RUN10-CAL-PAIRED"
    paired.mkdir()
    (paired / ".publishing").write_text("", encoding="utf-8")
    (campaigns_dir / "RUN10-CAL-PAIRED").mkdir()

    report = c0_freeze.detect_orphans(secret_dir, campaigns_dir)
    assert report.deleted_orphan_secret_ids == ()
    assert report.orphan_campaign_ids == ()
    assert paired.exists()
    assert not (paired / ".publishing").exists()


# ---------------------------------------------------------------------------
# Part A.1 — E_use evidence table as a C0-frozen input (`[UNDERSPEC-CAL-D10]`)
# ---------------------------------------------------------------------------


def _write_valid_e_use_table(path: Path) -> None:
    """全 `(construct_id, unit, domain)` キーを 1 行ずつ UNJUSTIFIED（no gate1
    dependency）でカバーする完全な E_use table（第 9 巡採用: `validate_e_use_table()`
    のキー集合完全一致チェックにより、単一行の table はもはや『valid』では
    ない — registry から機械導出した全キーを揃える）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "construct_id": construct,
            "unit": unit,
            "domain": domain,
            "intended_use": "u",
            "maximum_claim": "m",
            "e_use_value": None,
            "derivation_rule": "r",
            "evidence_class": "UNJUSTIFIED",
            "source_id_or_url": "s",
            "source_checked_at": "t",
            "source_hash_or_version": "v",
            "applicability_argument": "a",
            "review_status": "r",
            "e_use_mode": "absolute",
        }
        for construct, unit, domain in c0_freeze.e_use_table.unique_construct_unit_domain(
            candidate_registry.ALL_CANDIDATES
        )
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")


def _write_invalid_e_use_table(path: Path) -> None:
    """UNJUSTIFIED row illegally carrying a numeric e_use_value (bypasses the
    `EUseEvidenceRow.__post_init__` guard because this is raw JSON, not a
    dataclass construction call — this is exactly the shape `load_e_use_table`
    must reject)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "construct_id": "x",
                    "unit": "hz",
                    "domain": "d",
                    "intended_use": "u",
                    "maximum_claim": "m",
                    "e_use_value": 1.0,
                    "derivation_rule": "r",
                    "evidence_class": "UNJUSTIFIED",
                    "source_id_or_url": "s",
                    "source_checked_at": "t",
                    "source_hash_or_version": "v",
                    "applicability_argument": "a",
                    "review_status": "r",
                    "e_use_mode": "absolute",
                }
            ]
        ),
        encoding="utf-8",
    )


def test_dry_run_valid_e_use_table_has_no_e_use_block(tmp_path: Path) -> None:
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)
    report = c0_freeze.dry_run(
        _REPO_ROOT, tmp_path / "approvals", os.environ, e_use_table_path=table_path
    )
    assert not any(
        r.startswith("e_use_table:") for r in report.validation.missing_required_keys
    )


def test_dry_run_invalid_e_use_table_is_blocked_with_e_use_detail(tmp_path: Path) -> None:
    table_path = tmp_path / "e_use.json"
    _write_invalid_e_use_table(table_path)
    report = c0_freeze.dry_run(
        _REPO_ROOT, tmp_path / "approvals", os.environ, e_use_table_path=table_path
    )
    assert report.validation.is_blocked
    assert c0_validate.vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in report.validation.blocked_codes
    e_use_reasons = [r for r in report.validation.missing_required_keys if r.startswith("e_use_table:")]
    assert e_use_reasons, report.validation.missing_required_keys
    assert "UNJUSTIFIED" in e_use_reasons[0]


def test_dry_run_e_use_table_missing_file_is_blocked(tmp_path: Path) -> None:
    report = c0_freeze.dry_run(
        _REPO_ROOT,
        tmp_path / "approvals",
        os.environ,
        e_use_table_path=tmp_path / "does-not-exist.json",
    )
    e_use_reasons = [r for r in report.validation.missing_required_keys if r.startswith("e_use_table:")]
    assert e_use_reasons
    assert "cannot read" in e_use_reasons[0]


def test_armed_freeze_copies_e_use_table_and_records_sha(tmp_path: Path, clean_checkout: None) -> None:
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)

    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report.validation.is_blocked, report.validation.missing_required_keys
    _write_gate2(approval_dir, report.manifest_core_sha)

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
        e_use_table_path=table_path,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail

    expected_sha = hashlib.sha256(table_path.read_bytes()).hexdigest()

    copied = result.campaign_dir / "e_use_table.json"
    assert copied.is_file()
    assert copied.read_bytes() == table_path.read_bytes()

    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frozen_inputs"]["e_use_table_sha256"] == expected_sha
    # 第 12 巡採用: frozen_inputs is now part of the CORE payload (Gate 2 binds
    # it) -- it must survive core_payload() stripping, unlike approvals/
    # commitments/realized_split/campaign_id/authorization_nonce.
    assert "frozen_inputs" in c0_freeze.core_payload(manifest)
    assert c0_freeze.core_payload(manifest)["frozen_inputs"] == manifest["frozen_inputs"]
    assert c0_freeze.manifest_core_sha(manifest) == report.manifest_core_sha

    from voice_genesis.calibration.provenance import Ledger

    entries = Ledger(result.campaign_dir / "ledger.jsonl").entries
    freeze_events = [e for e in entries if e.payload.get("kind") == "c0_freeze"]
    assert len(freeze_events) == 1
    assert freeze_events[0].payload["e_use_table_sha256"] == expected_sha


# ---------------------------------------------------------------------------
# PR review round 6 (commit 6494395 review) — 6 adopted findings
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_authorization_nonce(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#1: CLI dry-run output must show `authorization_nonce` alongside
    `manifest_core_sha`/`campaign_id`."""
    rc = c0_freeze.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--approval-dir",
            str(tmp_path / "approvals"),
            "--secret-dir",
            str(tmp_path / "secrets"),
            "--campaigns-dir",
            str(tmp_path / "campaigns"),
        ]
    )
    out = capsys.readouterr().out
    assert "authorization_nonce:" in out
    assert rc in (0, 1)


def test_cli_dry_run_does_not_create_secret_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2: plain dry-run (no --armed, no --maintenance-orphans) must have zero
    filesystem side effects on secret_dir — `detect_orphans()` (which mkdirs
    `secret_dir` to take its lock) must not run on the default dry-run path."""
    secret_dir = tmp_path / "secrets"
    rc = c0_freeze.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--approval-dir",
            str(tmp_path / "approvals"),
            "--secret-dir",
            str(secret_dir),
            "--campaigns-dir",
            str(tmp_path / "campaigns"),
        ]
    )
    assert rc in (0, 1)
    assert not secret_dir.exists()


def test_cli_maintenance_orphans_runs_detect_orphans_standalone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#2: `--maintenance-orphans` runs orphan detection/cleanup only, no
    manifest build, no freeze."""
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    orphan = secret_dir / "RUN10-CAL-STALE"
    orphan.mkdir()
    (orphan / ".publishing").write_text("", encoding="utf-8")

    rc = c0_freeze.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--approval-dir",
            str(tmp_path / "approvals"),
            "--secret-dir",
            str(secret_dir),
            "--campaigns-dir",
            str(campaigns_dir),
            "--maintenance-orphans",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted orphan secret dir(s)" in out
    assert not orphan.exists()
    # No manifest-related output on this path.
    assert "manifest_core_sha" not in out
    assert "outcome:" not in out


def test_armed_freeze_loads_approvals_exactly_once(tmp_path: Path, clean_checkout: None) -> None:
    """#5: armed_freeze() must call load_all_approvals()/load_approval() only
    once per approval file (no duplicate disk reads across check_armed() +
    the manifest-building load)."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    from voice_genesis.calibration import approvals as approvals_mod

    calls: list[approvals_mod.Gate] = []
    real_load_approval = approvals_mod.load_approval

    def counting_load_approval(gate: approvals_mod.Gate, approval_dir_: Path, *, repo_root: Path | None = None):
        calls.append(gate)
        return real_load_approval(gate, approval_dir_, repo_root=repo_root)

    import voice_genesis.calibration.approvals as approvals_module

    orig = approvals_module.load_approval
    approvals_module.load_approval = counting_load_approval  # type: ignore[assignment]
    try:
        result = c0_freeze.armed_freeze(
            _REPO_ROOT,
            cli_armed=True,
            env=env,
            approval_dir=approval_dir,
            secret_dir=secret_dir,
            campaigns_dir=campaigns_dir,
        )
    finally:
        approvals_module.load_approval = orig  # type: ignore[assignment]

    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    # Exactly one load per gate (3 gates total: gate1/gate2/gate3), from the
    # single `load_all_approvals()` call — `check_armed()` must reuse that
    # snapshot via `preloaded=` rather than re-reading gate1/gate2 itself.
    assert len(calls) == 3
    assert sorted(calls, key=lambda g: g.value) == sorted(list(approvals_mod.Gate), key=lambda g: g.value)


def test_armed_freeze_nonce_recheck_inside_publish_lock_catches_toctou(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4: even if the early (unlocked) nonce-uniqueness check misses a
    concurrent publish, the authoritative recheck performed once the publish
    lock is held must still reject the replay."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    first = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert first.outcome == c0_freeze.FreezeOutcome.PUBLISHED, first.detail
    published_ids_after_first = {p.name for p in campaigns_dir.iterdir()}

    real_check = c0_freeze._find_existing_nonce_usage
    call_count = {"n": 0}

    def flaky_check(campaigns_dir_: Path, nonce: str) -> c0_freeze.NonceRegistryScan:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate the early (pre-lock) check racing past the just-published
            # campaign — the locked recheck (2nd call) must still catch it.
            return c0_freeze.NonceRegistryScan(existing_campaign_id=None, uninspectable_dirs=())
        return real_check(campaigns_dir_, nonce)

    monkeypatch.setattr(c0_freeze, "_find_existing_nonce_usage", flaky_check)

    second = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert second.outcome == c0_freeze.FreezeOutcome.NONCE_ALREADY_USED, second.detail
    assert call_count["n"] == 2
    assert {p.name for p in campaigns_dir.iterdir()} == published_ids_after_first
    assert not any(p.name.startswith(".staging-") for p in campaigns_dir.iterdir())
    assert not any(p.name.startswith(".staging-") for p in secret_dir.iterdir())


def test_readback_verify_detects_corrupted_render_root_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#6: `_readback_verify()` must independently read back and verify
    `render_root_secret.bin` (length 32 + commitment match), not just
    `split_secret.bin`. Manifest-content validity is covered by other tests;
    stub it out here so this test isolates the secret-bytes readback path."""
    import hashlib as hashlib_mod

    from voice_genesis.calibration.fixtures.matrix import build_matrix

    monkeypatch.setattr(
        c0_freeze.c0_validate,
        "validate_c0_manifest",
        lambda manifest, **kwargs: c0_validate.C0ValidationResult(),
    )

    campaign_staging = tmp_path / "campaign"
    secret_staging = tmp_path / "secret"
    campaign_staging.mkdir()
    secret_staging.mkdir()

    matrix_rows = build_matrix()
    row_inputs = c0_freeze._row_inputs_for_split(matrix_rows, c0_freeze.STRATUM_FACTOR_NAMES)
    split_secret = b"\x01" * 32
    realized = realize_split(row_inputs, split_secret, c0_freeze.STRATUM_FACTOR_NAMES)

    # 第 11 巡採用: `_readback_verify()` も e_use_table.json を読み戻して
    # frozen_inputs.e_use_table_sha256 pin と照合するため、この test-isolated
    # readback にも一貫した E_use table + pin を用意する（このテストが検証
    # したいのは render_root_secret 経路のみなので、内容自体は単に「通る」
    # ことだけが目的 — `_write_valid_e_use_table()` の完全なテーブルを使う）。
    e_use_table_path = campaign_staging / "e_use_table.json"
    _write_valid_e_use_table(e_use_table_path)
    e_use_table_sha256 = hashlib.sha256(e_use_table_path.read_bytes()).hexdigest()

    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    manifest["frozen_inputs"] = {"e_use_table_sha256": e_use_table_sha256}
    (campaign_staging / "c0_manifest.json").write_text(
        c0_freeze.canonical_json(manifest), encoding="utf-8"
    )
    (campaign_staging / "realized_split.json").write_text(
        c0_freeze.canonical_json(c0_freeze._realized_split_to_dict(realized)), encoding="utf-8"
    )
    from voice_genesis.calibration.provenance import Ledger

    Ledger(campaign_staging / "ledger.jsonl").append({"kind": "c0_freeze"})

    render_root_secret = b"\x02" * 32
    (secret_staging / "split_secret.bin").write_bytes(split_secret)
    # Write a *corrupted* render_root_secret.bin (differs from what will be
    # asserted as "expected") to simulate a partial/incorrect write.
    (secret_staging / "render_root_secret.bin").write_bytes(b"\x99" * 32)

    commitments = {
        "split_secret_sha256": hashlib_mod.sha256(split_secret).hexdigest(),
        "render_root_secret_sha256": hashlib_mod.sha256(render_root_secret).hexdigest(),
    }

    ok, detail = c0_freeze._readback_verify(
        campaign_staging,
        secret_staging,
        row_inputs,
        split_secret,
        render_root_secret,
        commitments,
        gate1_e_use_bound_accepted=False,
    )
    assert ok is False
    assert "render_root_secret" in detail


def test_write_secret_file_rejects_short_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#6: `_write_secret_file()` must raise (not silently truncate) if
    `os.write()` reports fewer bytes written than requested."""
    real_write = os.write

    def short_write(fd: int, data: bytes) -> int:
        # Report writing one byte fewer than requested (still calls the real
        # write with the full buffer so the underlying fd/file stays valid,
        # but the *return value* claims a short write).
        real_write(fd, data)
        return max(0, len(data) - 1)

    monkeypatch.setattr(c0_freeze.os, "write", short_write)
    target = tmp_path / "secret.bin"
    with pytest.raises(OSError, match="short write"):
        c0_freeze._write_secret_file(target, b"\x00" * 32)


# ---------------------------------------------------------------------------
# bug fix P2 #1 — E_use table read exactly once (validation + staging share
# the same buffer, no read-then-reread TOCTOU)
# ---------------------------------------------------------------------------


def test_armed_freeze_reads_e_use_table_bytes_exactly_once(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`armed_freeze()` must call `Path.read_bytes()` on the E_use table path
    exactly once (validation, sha256 pin, and staging copy all reuse that one
    buffer). Reads of unrelated paths are not counted."""
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)

    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report.validation.is_blocked, report.validation.missing_required_keys
    _write_gate2(approval_dir, report.manifest_core_sha)

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"

    real_read_bytes = Path.read_bytes
    call_count = {"n": 0}

    def counting_read_bytes(self: Path) -> bytes:
        if self == table_path:
            call_count["n"] += 1
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
        e_use_table_path=table_path,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    assert call_count["n"] == 1


def test_armed_freeze_e_use_table_staged_bytes_survive_a_swap_between_reads(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for bug fix P2 #1: if `armed_freeze()` ever went back
    to reading the E_use table path a second time (rather than reusing the
    bytes already read for validation), a file swapped out between the two
    reads would silently change what gets pinned/staged without ever being
    re-validated. Simulate that swap by making any read of `table_path`
    *after* the first return different (but still individually valid) bytes,
    then assert the staged copy + sha256 pin equal the bytes that were
    actually read/validated — never the swapped-in content. With the fix
    (single read, reused), the swap branch is never reached at all."""
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)
    validated_bytes = table_path.read_bytes()

    # A second, differently-formatted but equally-valid table: same semantic
    # content, different exact bytes (so a real "second read returns this"
    # bug would still pass validation but pin/stage the wrong bytes).
    swapped_bytes = json.dumps(json.loads(validated_bytes), indent=2).encode("utf-8")
    assert swapped_bytes != validated_bytes

    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report.validation.is_blocked, report.validation.missing_required_keys
    _write_gate2(approval_dir, report.manifest_core_sha)

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"

    real_read_bytes = Path.read_bytes
    read_count = {"n": 0}

    def swap_after_first_read(self: Path) -> bytes:
        if self == table_path:
            read_count["n"] += 1
            if read_count["n"] > 1:
                return swapped_bytes
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", swap_after_first_read)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
        e_use_table_path=table_path,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    assert read_count["n"] == 1  # the fix never triggers the swap branch

    staged = (result.campaign_dir / "e_use_table.json").read_bytes()
    assert staged == validated_bytes
    assert staged != swapped_bytes

    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frozen_inputs"]["e_use_table_sha256"] == hashlib.sha256(
        validated_bytes
    ).hexdigest()


# ---------------------------------------------------------------------------
# bug fix P2 #2 — publication rollback on any BaseException, not just OSError
# ---------------------------------------------------------------------------


def test_armed_freeze_keyboard_interrupt_between_renames_rolls_back_and_propagates(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject `KeyboardInterrupt` (not `OSError`) on the second `os.replace`
    (campaign-side) call. Before the fix this bypassed rollback entirely
    (only `OSError` was caught) and left a published secret dir with no
    matching campaign dir. After the fix: nothing is published, the secret
    dir is rolled back, and the interrupt still propagates to the caller."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src: object, dst: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise KeyboardInterrupt("injected interrupt on campaign-side os.replace")
        real_replace(src, dst)

    monkeypatch.setattr(c0_freeze.os, "replace", flaky_replace)

    with pytest.raises(KeyboardInterrupt):
        c0_freeze.armed_freeze(
            _REPO_ROOT,
            cli_armed=True,
            env=env,
            approval_dir=approval_dir,
            secret_dir=secret_dir,
            campaigns_dir=campaigns_dir,
        )
    assert call_count["n"] == 2

    # Nothing published: only the (legitimate) lock files may remain.
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = [
        p for p in (campaigns_dir.iterdir() if campaigns_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == [], f"secret dir not empty: {remaining_secret}"
    assert remaining_campaign == [], f"campaigns dir not empty: {remaining_campaign}"


def test_armed_freeze_system_exit_on_first_rename_rolls_back_and_propagates(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same as above but `SystemExit` on the *first* (secret-side) rename, to
    confirm the rollback-and-reraise path is not specific to the second
    rename or to `KeyboardInterrupt`."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    def always_raise(src: object, dst: object) -> None:
        raise SystemExit("injected exit on secret-side os.replace")

    monkeypatch.setattr(c0_freeze.os, "replace", always_raise)

    with pytest.raises(SystemExit):
        c0_freeze.armed_freeze(
            _REPO_ROOT,
            cli_armed=True,
            env=env,
            approval_dir=approval_dir,
            secret_dir=secret_dir,
            campaigns_dir=campaigns_dir,
        )

    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = [
        p for p in (campaigns_dir.iterdir() if campaigns_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == []
    assert remaining_campaign == []


def test_armed_freeze_keyboard_interrupt_before_first_rename_preserves_existing_campaign_dir(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 9 巡改訂 regression guard: the `except BaseException` rollback must
    remove only destinations *this call itself* published — never a
    pre-existing, unrelated directory that happens to already sit at
    `campaigns_dir/<campaign_id>` (e.g. left over from a previous, entirely
    different run). Pre-seed such a directory, then inject `KeyboardInterrupt`
    *before* the first `os.replace` call (secret-side) ever runs — since this
    call never published anything, the pre-existing campaign dir must survive
    completely untouched."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    dry = c0_freeze.dry_run(_REPO_ROOT, approval_dir, env)
    campaign_id = dry.campaign_id

    campaigns_dir.mkdir(parents=True, exist_ok=True)
    existing_campaign_dir = campaigns_dir / campaign_id
    existing_campaign_dir.mkdir()
    # 第 12 巡採用: `_find_existing_nonce_usage()` now fail-closed refuses the
    # whole freeze if it finds a non-staging campaign dir with a missing/
    # malformed manifest ("nonce_registry_uninspectable"). Give this
    # pre-existing dir a *valid*, parseable manifest with a *different*
    # authorization_nonce, so it registers as an unrelated, already-published
    # campaign (neither uninspectable nor a nonce match) and this test can
    # still reach the KeyboardInterrupt-before-first-rename scenario it's
    # actually meant to guard.
    (existing_campaign_dir / "c0_manifest.json").write_text(
        json.dumps({"authorization_nonce": "unrelated-foreign-nonce"}), encoding="utf-8"
    )
    sentinel = existing_campaign_dir / "sentinel.txt"
    sentinel.write_text("pre-existing, must survive", encoding="utf-8")

    def always_raise(src: object, dst: object) -> None:
        raise KeyboardInterrupt("injected interrupt before the first rename ever runs")

    monkeypatch.setattr(c0_freeze.os, "replace", always_raise)

    with pytest.raises(KeyboardInterrupt):
        c0_freeze.armed_freeze(
            _REPO_ROOT,
            cli_armed=True,
            env=env,
            approval_dir=approval_dir,
            secret_dir=secret_dir,
            campaigns_dir=campaigns_dir,
        )

    # The pre-existing campaign dir (never touched by this call) must be
    # completely untouched — the bug this guards against unconditionally
    # `_rmtree_if_exists()`'d `campaign_final`/`secret_final` on any
    # exception, which would have deleted it even though this call never
    # renamed anything into it.
    assert existing_campaign_dir.exists()
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "pre-existing, must survive"
    # No secret dir was ever published by this call either.
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == []


# ---------------------------------------------------------------------------
# bug fix 第10巡 #1 — staging construction (mkdir/manifest+split+ledger
# writes/secret writes) also rolls back on any BaseException, not just
# OSError. Before this fix, KeyboardInterrupt/SystemExit during staging left
# a `.staging-<campaign_id>` dir behind (potentially containing already-
# written secret bytes) that `detect_orphans()` never touches — `.staging-*`
# is excluded by `_published_ids()` by construction, so it lingered forever.
# ---------------------------------------------------------------------------


def test_armed_freeze_keyboard_interrupt_during_staging_write_removes_both_staging_roots(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject `KeyboardInterrupt` immediately *after* `split_secret.bin` has
    actually been written to disk (the most dangerous timing: real secret
    bytes already exist under the `.staging-<campaign_id>` secret dir) but
    before `render_root_secret.bin` is written. Both staging roots must be
    gone afterward and the interrupt must still propagate — this predates
    (and is entirely independent of) the publish lock / rename section
    covered by the P2 #2 tests above."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    real_write_secret_file = c0_freeze._write_secret_file
    call_count = {"n": 0}

    def flaky_write_secret_file(path: Path, data: bytes) -> None:
        call_count["n"] += 1
        real_write_secret_file(path, data)  # actually write it to disk first
        if call_count["n"] == 1:
            raise KeyboardInterrupt("injected interrupt right after split_secret.bin write")

    monkeypatch.setattr(c0_freeze, "_write_secret_file", flaky_write_secret_file)

    with pytest.raises(KeyboardInterrupt):
        c0_freeze.armed_freeze(
            _REPO_ROOT,
            cli_armed=True,
            env=env,
            approval_dir=approval_dir,
            secret_dir=secret_dir,
            campaigns_dir=campaigns_dir,
        )
    # Only one secret file was ever written (the interrupt fired before the
    # second `_write_secret_file()` call for render_root_secret.bin).
    assert call_count["n"] == 1

    # Neither staging root survives, and — since this happens well before the
    # publish-lock section — no `.publish.lock` file exists yet either, so
    # both dirs must be entirely empty (or not exist at all).
    remaining_secret = list(secret_dir.iterdir()) if secret_dir.exists() else []
    remaining_campaign = list(campaigns_dir.iterdir()) if campaigns_dir.exists() else []
    assert remaining_secret == [], f"secret dir not empty: {remaining_secret}"
    assert remaining_campaign == [], f"campaigns dir not empty: {remaining_campaign}"
    assert not any(secret_dir.glob(".staging-*"))
    assert not any(campaigns_dir.glob(".staging-*"))


# ---------------------------------------------------------------------------
# bug fix P2 #3 — authoritative nonce/publication lock lives under
# campaigns_dir (shared registry), not the caller-selected secret_dir
# ---------------------------------------------------------------------------


def test_armed_freeze_authoritative_lock_lives_under_campaigns_dir(
    tmp_path: Path, clean_checkout: None
) -> None:
    """The authoritative publish lock must be `campaigns_dir/.publish.lock`
    (the shared campaign registry both processes necessarily agree on).
    `secret_dir/.publish.lock` remains as a secondary lock."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    assert (campaigns_dir / c0_freeze._PUBLISH_LOCK_NAME).is_file()
    assert (secret_dir / c0_freeze._PUBLISH_LOCK_NAME).is_file()


def test_armed_freeze_nonce_recheck_inside_publish_lock_catches_toctou_with_different_secret_dirs(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for bug fix P2 #3: two processes sharing the same
    `campaigns_dir` but passing *different* `secret_dir` values must still be
    correctly serialized by the authoritative (campaigns_dir-keyed) lock —
    same scenario as
    `test_armed_freeze_nonce_recheck_inside_publish_lock_catches_toctou`
    above, but with `secret_dir` deliberately varied between the two calls.
    Before the fix, keying the lock off `secret_dir` alone meant these two
    calls would take unrelated locks and the second could publish a replayed
    nonce right past the TOCTOU-catching recheck."""
    approval_dir, secret_dir_1, campaigns_dir, env = _prepare_armed(tmp_path)
    secret_dir_2 = tmp_path / "secrets-2"

    first = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir_1,
        campaigns_dir=campaigns_dir,
    )
    assert first.outcome == c0_freeze.FreezeOutcome.PUBLISHED, first.detail
    published_ids_after_first = {
        p.name for p in campaigns_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME
    }

    real_check = c0_freeze._find_existing_nonce_usage
    call_count = {"n": 0}

    def flaky_check(campaigns_dir_: Path, nonce: str) -> c0_freeze.NonceRegistryScan:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate the early (pre-lock) check racing past the just-published
            # campaign — the locked recheck (2nd call) must still catch it.
            return c0_freeze.NonceRegistryScan(existing_campaign_id=None, uninspectable_dirs=())
        return real_check(campaigns_dir_, nonce)

    monkeypatch.setattr(c0_freeze, "_find_existing_nonce_usage", flaky_check)

    second = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir_2,  # different secret_dir than the first call
        campaigns_dir=campaigns_dir,
    )
    assert second.outcome == c0_freeze.FreezeOutcome.NONCE_ALREADY_USED, second.detail
    assert call_count["n"] == 2
    assert {
        p.name for p in campaigns_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME
    } == published_ids_after_first
    assert not any(p.name.startswith(".staging-") for p in campaigns_dir.iterdir())
    # The rejected second call must not have published (or left staging
    # behind in) the different secret_dir it was given.
    remaining_secret_dir_2 = [
        p for p in (secret_dir_2.iterdir() if secret_dir_2.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret_dir_2 == []


def test_detect_orphans_takes_campaigns_dir_lock_too(tmp_path: Path) -> None:
    """Regression guard for bug fix P2 #3: `detect_orphans()` reads the
    `campaigns_dir` registry, so it must take the authoritative
    `campaigns_dir/.publish.lock` (in addition to the secondary
    `secret_dir` lock) — not just the secret_dir lock as before. Holding
    only the `campaigns_dir` lock externally must make `detect_orphans()`
    skip (empty report, no side effects), proving it actually contends for
    that lock."""
    import fcntl

    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    secret_dir.mkdir()
    campaigns_dir.mkdir()
    orphan = secret_dir / "RUN10-CAL-STALE"
    orphan.mkdir()
    (orphan / ".publishing").write_text("", encoding="utf-8")

    lock_path = campaigns_dir / c0_freeze._PUBLISH_LOCK_NAME
    with open(lock_path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            report = c0_freeze.detect_orphans(secret_dir, campaigns_dir)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    assert report == c0_freeze.OrphanReport(orphan_campaign_ids=(), deleted_orphan_secret_ids=())
    assert orphan.exists()  # untouched while the campaigns_dir lock was held elsewhere


# ---------------------------------------------------------------------------
# 第 11 巡採用 #1 — staging paths are namespaced by a per-invocation token
# (`.staging-<campaign_id>-<invocation_token>`), so rollback never touches a
# differently-tokened staging dir (e.g. a concurrent process's).
# ---------------------------------------------------------------------------


def test_armed_freeze_never_touches_a_differently_tokened_staging_dir(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-seed a foreign-token `.staging-<campaign_id>-<other-token>` dir on
    both roots (simulating a concurrent/other process's in-flight staging for
    the same `campaign_id`), then inject a staging-write failure so *this*
    call's own rollback path actually runs. The foreign staging dirs must
    come out completely untouched — before the fix, both this call and a
    hypothetical concurrent one shared the exact same `.staging-<campaign_id>`
    path, so this call's rollback (`_rmtree_if_exists`) could delete the
    other's in-progress staging (including already-written secret bytes)."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    dry = c0_freeze.dry_run(_REPO_ROOT, approval_dir, env)
    campaign_id = dry.campaign_id

    campaigns_dir.mkdir(parents=True, exist_ok=True)
    secret_dir.mkdir(parents=True, exist_ok=True)
    foreign_suffix = ".staging-" + campaign_id + "-deadbeefdeadbeef"
    foreign_campaign_staging = campaigns_dir / foreign_suffix
    foreign_secret_staging = secret_dir / foreign_suffix
    foreign_campaign_staging.mkdir()
    foreign_secret_staging.mkdir()
    (foreign_campaign_staging / "sentinel.txt").write_text(
        "foreign staging (campaign side), must survive", encoding="utf-8"
    )
    (foreign_secret_staging / "sentinel.txt").write_text(
        "foreign staging (secret side), must survive", encoding="utf-8"
    )

    def always_fail(path: Path, data: bytes) -> None:
        raise OSError("injected staging write failure")

    monkeypatch.setattr(c0_freeze, "_write_secret_file", always_fail)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLICATION_FAILED, result.detail

    assert foreign_campaign_staging.exists()
    assert foreign_secret_staging.exists()
    assert (foreign_campaign_staging / "sentinel.txt").read_text(encoding="utf-8") == (
        "foreign staging (campaign side), must survive"
    )
    assert (foreign_secret_staging / "sentinel.txt").read_text(encoding="utf-8") == (
        "foreign staging (secret side), must survive"
    )

    # This call's *own* staging (a different, freshly-generated token) must
    # still have been cleaned up.
    own_campaign_staging = [
        p for p in campaigns_dir.glob(".staging-*") if p.name != foreign_suffix
    ]
    own_secret_staging = [
        p for p in secret_dir.glob(".staging-*") if p.name != foreign_suffix
    ]
    assert own_campaign_staging == []
    assert own_secret_staging == []


def test_armed_freeze_staging_paths_are_namespaced_by_invocation_token(
    tmp_path: Path, clean_checkout: None
) -> None:
    """Two successive `armed_freeze()` calls that reach the staging phase
    (even though the second is ultimately rejected for reusing the nonce)
    must never collide on the same staging path. This is a narrower,
    behavior-level check that the per-call token actually varies (rather than
    e.g. being derived deterministically from `campaign_id` alone)."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    secret_dir.mkdir(parents=True, exist_ok=True)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    # Published campaign_dir/secret_dir names are the bare campaign_id (no
    # token suffix) — the token is staging-only bookkeeping, never part of
    # the final published identity.
    assert result.campaign_dir is not None
    assert result.campaign_dir.name == result.campaign_id
    assert result.secret_dir is not None
    assert result.secret_dir.name == result.campaign_id


# ---------------------------------------------------------------------------
# 第 11 巡採用 #2 — `_readback_verify()` also verifies the staged
# `e_use_table.json` bytes (sha256 matches `frozen_inputs.e_use_table_sha256`,
# and the content re-passes `validate_e_use_table`); a mismatch refuses to
# publish.
# ---------------------------------------------------------------------------


def test_armed_freeze_e_use_table_corrupted_after_staging_write_is_not_published(
    tmp_path: Path, clean_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt the staged `e_use_table.json` strictly *after* it has been
    written (but before `_readback_verify()` runs) by hooking the first
    `_write_secret_file()` call (which happens right after the e_use_table
    write, per the staging block's statement order) to overwrite it as a side
    effect. `_readback_verify()`'s sha256-pin check must catch the mismatch
    and refuse to publish — nothing ends up in `campaigns_dir`/`secret_dir`."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    real_write_secret_file = c0_freeze._write_secret_file
    call_count = {"n": 0}

    def corrupting_write_secret_file(path: Path, data: bytes) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # `path` is `secret_staging / "split_secret.bin"`; both staging
            # roots share the same `.staging-<campaign_id>-<token>` name.
            staging_name = path.parent.name
            campaign_staging = campaigns_dir / staging_name
            (campaign_staging / "e_use_table.json").write_bytes(b"CORRUPTED-AFTER-STAGING")
        real_write_secret_file(path, data)

    monkeypatch.setattr(c0_freeze, "_write_secret_file", corrupting_write_secret_file)

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert call_count["n"] == 2  # both secret files were still written normally
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLICATION_FAILED, result.detail
    assert "e_use_table.json" in result.detail

    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = [
        p for p in (campaigns_dir.iterdir() if campaigns_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == [], f"secret dir not empty: {remaining_secret}"
    assert remaining_campaign == [], f"campaigns dir not empty: {remaining_campaign}"


def test_readback_verify_detects_e_use_table_sha256_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrower, direct-call regression guard: `_readback_verify()` itself
    (independent of the full `armed_freeze()` staging sequence) must reject a
    staged `e_use_table.json` whose sha256 does not match the staged
    manifest's `frozen_inputs.e_use_table_sha256` pin. Manifest-content
    validity is covered by other tests; stub it out here (same pattern as
    `test_readback_verify_detects_corrupted_render_root_secret`) so this test
    isolates the e_use_table readback path."""
    from voice_genesis.calibration.fixtures.matrix import build_matrix
    from voice_genesis.calibration.provenance import Ledger

    monkeypatch.setattr(
        c0_freeze.c0_validate,
        "validate_c0_manifest",
        lambda manifest, **kwargs: c0_validate.C0ValidationResult(),
    )

    campaign_staging = tmp_path / "campaign"
    secret_staging = tmp_path / "secret"
    campaign_staging.mkdir()
    secret_staging.mkdir()

    matrix_rows = build_matrix()
    row_inputs = c0_freeze._row_inputs_for_split(matrix_rows, c0_freeze.STRATUM_FACTOR_NAMES)
    split_secret = b"\x01" * 32
    render_root_secret = b"\x02" * 32
    realized = realize_split(row_inputs, split_secret, c0_freeze.STRATUM_FACTOR_NAMES)

    _write_valid_e_use_table(campaign_staging / "e_use_table.json")
    correct_sha256 = hashlib.sha256((campaign_staging / "e_use_table.json").read_bytes()).hexdigest()
    # Pin the manifest to the *correct* sha256, then corrupt the staged file
    # afterward -- exactly the TOCTOU-shaped tampering `_readback_verify()`
    # must catch.
    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
    manifest["frozen_inputs"] = {"e_use_table_sha256": correct_sha256}
    (campaign_staging / "c0_manifest.json").write_text(
        c0_freeze.canonical_json(manifest), encoding="utf-8"
    )
    (campaign_staging / "realized_split.json").write_text(
        c0_freeze.canonical_json(c0_freeze._realized_split_to_dict(realized)), encoding="utf-8"
    )
    Ledger(campaign_staging / "ledger.jsonl").append({"kind": "c0_freeze"})
    (secret_staging / "split_secret.bin").write_bytes(split_secret)
    (secret_staging / "render_root_secret.bin").write_bytes(render_root_secret)

    (campaign_staging / "e_use_table.json").write_bytes(b"tampered after the pin was computed")

    commitments = {
        "split_secret_sha256": hashlib.sha256(split_secret).hexdigest(),
        "render_root_secret_sha256": hashlib.sha256(render_root_secret).hexdigest(),
    }
    ok, detail = c0_freeze._readback_verify(
        campaign_staging,
        secret_staging,
        row_inputs,
        split_secret,
        render_root_secret,
        commitments,
        gate1_e_use_bound_accepted=False,
    )
    assert ok is False
    assert "e_use_table.json" in detail
    assert "sha256" in detail


# ---------------------------------------------------------------------------
# 第 11 巡採用 #3 — Gate 1 `max_claim_scope` is validated against the
# candidates registry (subset check; empty/unknown ids BLOCK) and recorded in
# `frozen_design.max_claim_scope`, part of the *core* manifest payload.
# ---------------------------------------------------------------------------


def test_dry_run_max_claim_scope_unknown_construct_is_blocked(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1(approval_dir, scope=["not_a_real_construct"])
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert report.validation.is_blocked
    reasons = [
        r for r in report.validation.missing_required_keys if r.startswith("max_claim_scope:")
    ]
    assert reasons, report.validation.missing_required_keys
    assert "not_a_real_construct" in reasons[0]


def test_dry_run_max_claim_scope_empty_is_blocked(tmp_path: Path, clean_checkout: None) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1(approval_dir, scope=[])
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert report.validation.is_blocked
    reasons = [
        r for r in report.validation.missing_required_keys if r.startswith("max_claim_scope:")
    ]
    assert reasons, report.validation.missing_required_keys
    assert "non-empty" in reasons[0]


def test_dry_run_valid_max_claim_scope_is_recorded_in_manifest(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1(approval_dir, scope=["formant_frequency", "harmonic_to_noise_ratio"])
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert not any(
        r.startswith("max_claim_scope:") for r in report.validation.missing_required_keys
    ), report.validation.missing_required_keys
    frozen_design = report.manifest["frozen_design"]
    assert isinstance(frozen_design, dict)
    assert frozen_design["max_claim_scope"] == ["formant_frequency", "harmonic_to_noise_ratio"]
    # Part of the CORE payload -- Gate 2's manifest_core_sha binds it, unlike
    # e_use_table's frozen_inputs (deliberately non-core).
    core_frozen_design = c0_freeze.core_payload(report.manifest)["frozen_design"]
    assert isinstance(core_frozen_design, dict)
    assert "max_claim_scope" in core_frozen_design


def test_max_claim_scope_is_part_of_manifest_core_sha(tmp_path: Path, clean_checkout: None) -> None:
    """Changing only `max_claim_scope` must change `manifest_core_sha` --
    proof that it is bound by Gate 2's approval (core payload), unlike
    `e_use_table`'s `frozen_inputs` section which is deliberately excluded."""
    approval_dir_1 = tmp_path / "approvals-1"
    approval_dir_1.mkdir()
    _write_gate1(approval_dir_1, scope=["formant_frequency"])
    report_1 = c0_freeze.dry_run(_REPO_ROOT, approval_dir_1, os.environ)

    approval_dir_2 = tmp_path / "approvals-2"
    approval_dir_2.mkdir()
    _write_gate1(approval_dir_2, scope=["harmonic_to_noise_ratio"])
    report_2 = c0_freeze.dry_run(_REPO_ROOT, approval_dir_2, os.environ)

    assert report_1.manifest_core_sha != report_2.manifest_core_sha


def test_armed_freeze_published_manifest_records_max_claim_scope(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frozen_design"]["max_claim_scope"] == ["formant_frequency"]


def test_cli_dry_run_prints_max_claim_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = c0_freeze.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--approval-dir",
            str(tmp_path / "approvals"),
            "--secret-dir",
            str(tmp_path / "secrets"),
            "--campaigns-dir",
            str(tmp_path / "campaigns"),
        ]
    )
    out = capsys.readouterr().out
    assert "max_claim_scope:" in out
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# 第 12 巡採用 #1 — nonce uniqueness scan fail-closes when it cannot inspect a
# campaign dir's manifest (missing / unreadable / malformed JSON / missing
# authorization_nonce field), instead of silently ignoring it.
# ---------------------------------------------------------------------------


def test_armed_freeze_refuses_when_a_campaign_dir_has_a_truncated_manifest(
    tmp_path: Path, clean_checkout: None
) -> None:
    """A campaign dir with a truncated/malformed `c0_manifest.json` makes the
    nonce registry uninspectable -> `armed_freeze()` must refuse with
    `NONCE_REGISTRY_UNINSPECTABLE` and have zero side effects (no staging, no
    publish), regardless of whether the actual nonce would otherwise have been
    unused."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    campaigns_dir.mkdir(parents=True, exist_ok=True)
    broken = campaigns_dir / "RUN10-CAL-BROKEN"
    broken.mkdir()
    (broken / "c0_manifest.json").write_text('{"campaign_meta": {truncated', encoding="utf-8")

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.NONCE_REGISTRY_UNINSPECTABLE, result.detail
    assert "nonce_registry_uninspectable" in result.detail
    assert "RUN10-CAL-BROKEN" in result.detail
    assert result.campaign_dir is None
    assert result.secret_dir is None

    # Zero side effects: the broken dir survives untouched, no new campaign
    # was published, and no staging/secret material was ever created.
    assert (broken / "c0_manifest.json").read_text(encoding="utf-8") == '{"campaign_meta": {truncated'
    assert {p.name for p in campaigns_dir.iterdir() if p.name != c0_freeze._PUBLISH_LOCK_NAME} == {
        "RUN10-CAL-BROKEN"
    }
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    assert remaining_secret == []


def test_armed_freeze_refuses_when_a_campaign_dir_is_missing_its_manifest_file(
    tmp_path: Path, clean_checkout: None
) -> None:
    """Same as above but the manifest file is simply absent (dir exists,
    `c0_manifest.json` does not) -- also uninspectable, also refused."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    campaigns_dir.mkdir(parents=True, exist_ok=True)
    (campaigns_dir / "RUN10-CAL-NOMANIFEST").mkdir()

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.NONCE_REGISTRY_UNINSPECTABLE, result.detail
    assert "RUN10-CAL-NOMANIFEST" in result.detail


def test_armed_freeze_refuses_when_a_campaign_manifest_is_missing_the_nonce_field(
    tmp_path: Path, clean_checkout: None
) -> None:
    """A campaign dir with a syntactically valid but nonce-less manifest is
    also uninspectable for nonce-uniqueness purposes -- also refused."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)

    campaigns_dir.mkdir(parents=True, exist_ok=True)
    no_nonce = campaigns_dir / "RUN10-CAL-NONONCE"
    no_nonce.mkdir()
    (no_nonce / "c0_manifest.json").write_text(
        json.dumps({"campaign_meta": {"campaign_date_utc": "2026-01-01"}}), encoding="utf-8"
    )

    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.NONCE_REGISTRY_UNINSPECTABLE, result.detail
    assert "RUN10-CAL-NONONCE" in result.detail


def test_find_existing_nonce_usage_still_ignores_staging_dirs(tmp_path: Path) -> None:
    """`.staging-*` dirs are never considered part of the published registry,
    so they must not trigger `uninspectable_dirs` even with no manifest at
    all (they never have one)."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir()
    (campaigns_dir / ".staging-RUN10-CAL-X-deadbeef").mkdir()

    scan = c0_freeze._find_existing_nonce_usage(campaigns_dir, "some-nonce")
    assert scan.existing_campaign_id is None
    assert scan.uninspectable_dirs == ()


def test_find_existing_nonce_usage_finds_match_and_reports_uninspectable_together(
    tmp_path: Path,
) -> None:
    """A clean match and an uninspectable sibling can coexist in one scan;
    both must be reported (the match does not suppress the uninspectable
    finding, and vice versa)."""
    campaigns_dir = tmp_path / "campaigns"
    campaigns_dir.mkdir()
    matching = campaigns_dir / "RUN10-CAL-MATCH"
    matching.mkdir()
    (matching / "c0_manifest.json").write_text(
        json.dumps({"authorization_nonce": "target-nonce"}), encoding="utf-8"
    )
    broken = campaigns_dir / "RUN10-CAL-BROKEN"
    broken.mkdir()
    (broken / "c0_manifest.json").write_text("not json", encoding="utf-8")

    scan = c0_freeze._find_existing_nonce_usage(campaigns_dir, "target-nonce")
    assert scan.existing_campaign_id == "RUN10-CAL-MATCH"
    assert scan.uninspectable_dirs == ("RUN10-CAL-BROKEN",)


# ---------------------------------------------------------------------------
# 第 12 巡採用 #2 — the E_use table digest (`frozen_inputs.e_use_table_sha256`)
# is now part of the *core* payload, so changing the table changes
# `manifest_core_sha`, and a Gate 2 approval that pinned the old core_sha gets
# invalidated (`MANIFEST_CORE_SHA_MISMATCH`) once the table changes.
# ---------------------------------------------------------------------------


def _reformat_json_file(path: Path) -> bytes:
    """Rewrite `path`'s JSON content with different formatting (different
    exact bytes, same semantic/parsed content) and return the new bytes."""
    original = path.read_bytes()
    reformatted = json.dumps(json.loads(original), indent=2).encode("utf-8")
    assert reformatted != original, "reformat must actually change the bytes"
    path.write_bytes(reformatted)
    return reformatted


def test_e_use_table_content_change_alters_manifest_core_sha(
    tmp_path: Path, clean_checkout: None
) -> None:
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)
    _write_gate1(approval_dir)

    report_1 = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report_1.validation.is_blocked, report_1.validation.missing_required_keys

    _reformat_json_file(table_path)  # different bytes, same semantic content

    report_2 = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report_2.validation.is_blocked, report_2.validation.missing_required_keys

    assert (
        report_1.manifest["frozen_inputs"]["e_use_table_sha256"]
        != report_2.manifest["frozen_inputs"]["e_use_table_sha256"]
    )
    assert report_1.manifest_core_sha != report_2.manifest_core_sha


def test_armed_freeze_refuses_when_e_use_table_changes_after_gate2_pinned(
    tmp_path: Path, clean_checkout: None
) -> None:
    """A Gate 2 approval binds `manifest_core_sha` at dry-run time. If the
    E_use table is swapped afterward (before `armed_freeze()` runs),
    `manifest_core_sha` recomputed at armed time no longer matches what Gate 2
    pinned -- `armed_freeze()` must refuse with `MANIFEST_CORE_SHA_MISMATCH`,
    with zero side effects."""
    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    table_path = tmp_path / "e_use.json"
    _write_valid_e_use_table(table_path)

    _write_gate1(approval_dir)
    report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ, e_use_table_path=table_path)
    assert not report.validation.is_blocked, report.validation.missing_required_keys
    _write_gate2(approval_dir, report.manifest_core_sha)

    _reformat_json_file(table_path)  # table changed *after* Gate 2 pinned the old sha

    env = dict(os.environ)
    env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
        e_use_table_path=table_path,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.MANIFEST_CORE_SHA_MISMATCH, result.detail
    assert result.campaign_dir is None
    assert result.secret_dir is None
    assert not secret_dir.exists() or list(secret_dir.iterdir()) == []
    assert not campaigns_dir.exists() or list(campaigns_dir.iterdir()) == []


def test_cli_dry_run_prints_e_use_table_sha256(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = c0_freeze.main(
        [
            "--repo-root",
            str(_REPO_ROOT),
            "--approval-dir",
            str(tmp_path / "approvals"),
            "--secret-dir",
            str(tmp_path / "secrets"),
            "--campaigns-dir",
            str(tmp_path / "campaigns"),
        ]
    )
    out = capsys.readouterr().out
    assert "e_use_table_sha256:" in out
    assert rc in (0, 1)


def test_core_payload_excludes_only_secret_and_identity_bookkeeping(
    tmp_path: Path, clean_checkout: None
) -> None:
    """Regression guard for the 第 12 巡 design change: `frozen_inputs` must
    now survive `core_payload()` stripping (Gate 2 binds it), while the
    genuinely non-core, per-invocation/secret-derived sections
    (`approvals`/`commitments`/`realized_split`/`realized_split_sha`/
    `campaign_id`/`authorization_nonce`) are still stripped."""
    approval_dir, secret_dir, campaigns_dir, env = _prepare_armed(tmp_path)
    result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, result.detail
    manifest = json.loads((result.campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    stripped = c0_freeze.core_payload(manifest)
    assert "frozen_inputs" in stripped
    for key in (
        "approvals",
        "commitments",
        "realized_split",
        "realized_split_sha",
        "campaign_id",
        "authorization_nonce",
    ):
        assert key not in stripped


# ---------------------------------------------------------------------------
# round 14 finding #1: armed_freeze() must emit split_frozen so the real
# C0 -> C1 -> C2 -> C3a -> C3b -> unseal -> C4 -> close production path
# (driven through campaign/cli.py exactly like an operator would run it)
# never hits BLOCKED_LEAKAGE.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_armed_freeze_through_full_campaign_cli_never_hits_blocked_leakage(
    tmp_path: Path,
    clean_checkout: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Before round 14 finding #1, no production code ever emitted a
    ``split_frozen`` ledger event, so `provenance.Ledger.check_leakage`
    (`_verified_split_freeze_commitment`) unconditionally returned
    `BLOCKED_LEAKAGE` for *every* real armed campaign — the C0 freeze
    manifest was internally valid but the campaign could never actually run
    a C4 holdout render. This test drives the real production path (real
    `armed_freeze()`, real `campaign/cli.py` subcommand dispatch, real
    `provenance.Ledger.check_leakage`) end to end and asserts it never
    blocks on leakage.

    Rendering the full 456-row canonical matrix here would take well over
    an hour, so `build_matrix()` is monkeypatched at every call site that
    the freeze/CLI/leakage-check path actually uses (`c0_freeze.py`,
    `campaign/cli.py`, and `fixtures.matrix.build_matrix` itself — the
    latter covers `provenance.check_leakage`'s own local re-import) to a
    tiny *real* 4-row F0_CONTROL TRUTH_CORE slice. Because every one of
    those call sites is patched to the same tiny matrix, `check_leakage`'s
    canonical-row-coverage checks (§7) stay internally self-consistent —
    the point under test (split_frozen wiring) is exercised exactly as in
    production, only the row *count* is reduced for tractability.

    Note (§V2.2 縮退規則 fix, `ci_fail_994fb24.md`): `c0_validate.py` now
    resolves `build_matrix` through two independent bindings, mirroring
    `c0_freeze.py`'s own `build_matrix`/`_canonical_build_matrix` split:
    its sweep-declaration/claim-relevant-field checks (which compare
    against `frozen_design.fixture_spec`, itself always derived from the
    real matrix by `c0_freeze._fixture_specs()`) read the fixed
    `_canonical_build_matrix` alias, while only its two holdout-sweep-pin
    checks (`_check_holdout_pin_feasibility`/`_check_holdout_sweeps_
    declaration_match` — which compare against `holdout_sweeps`, itself
    derived from whatever `build_matrix()` `armed_freeze()` actually pinned
    against) read the swappable `build_matrix` name. So `c0_validate` is
    added as a 4th monkeypatch site below, safely — it only affects the
    two holdout-pin checks now, matching what `armed_freeze()` pinned
    against in this test, without disturbing the other families' (still
    real-matrix-based) frozen declarations.

    Before that split existed, `armed_freeze()`'s holdout-sweep pin
    (`fixtures.matrix.pin_holdout_sweeps_by_family()`), applied to this
    degenerate 4-row F0_CONTROL slice, non-deterministically hit either a
    stage-2 `CoverageRepairInfeasible` (uncaught) or a spurious
    `BLOCKED_C0_MANIFEST_INCOMPLETE` (this test's `c0_validate` binding was
    reading the *real* 456-row matrix while `armed_freeze()` had pinned
    against the tiny one). §V2.2's `cap < 1` pin-exemption + stage-2 k
    degradation (`fixtures.matrix.holdout_pin_params_by_family()`/
    `c0_freeze._pin_and_realize_holdout()`) now makes
    `holdout_sweeps["F0_CONTROL"]` freeze empty deterministically for this
    matrix regardless of `split_secret`, and the `c0_validate` binding
    split above lets its re-derivation agree with that. See
    `DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V2.2「縮退規則」.
    """
    import voice_genesis.calibration.fixtures.matrix as matrix_mod
    from voice_genesis.calibration.campaign import cli as campaign_cli
    from voice_genesis.calibration.fixtures.matrix import build_matrix as real_build_matrix
    from voice_genesis.calibration.provenance import Ledger
    from voice_genesis.calibration.vocab import BlockedCode

    tiny_matrix = tuple(
        mr
        for mr in real_build_matrix()
        if mr.row.family == "F0_CONTROL" and mr.row.block == "TRUTH_CORE"
    )[:4]
    assert len(tiny_matrix) == 4

    def fake_build_matrix() -> list[object]:
        return list(tiny_matrix)

    # All 4 binding sites `build_matrix()` reaches from the freeze/CLI/
    # validate/leakage-check path (see docstring above — `c0_validate`'s
    # *swappable* `build_matrix` binding only feeds its two holdout-pin
    # checks; its `_canonical_build_matrix` binding for the other checks
    # stays real, unaffected by this patch).
    monkeypatch.setattr(matrix_mod, "build_matrix", fake_build_matrix)
    monkeypatch.setattr(c0_freeze, "build_matrix", fake_build_matrix)
    monkeypatch.setattr(campaign_cli, "build_matrix", fake_build_matrix)
    monkeypatch.setattr(c0_validate, "build_matrix", fake_build_matrix)

    approval_dir = tmp_path / "approvals"
    secret_dir = tmp_path / "secrets"
    campaigns_dir = tmp_path / "campaigns"
    approval_dir.mkdir()
    # F0_CONTROL candidates claim construct "fundamental_frequency", not the
    # module default scope ("formant_frequency") — freeze against the scope
    # this campaign actually needs.
    _write_gate1(approval_dir, scope=["fundamental_frequency"])
    dry_report = c0_freeze.dry_run(_REPO_ROOT, approval_dir, os.environ)
    assert not dry_report.validation.is_blocked, dry_report.validation.missing_required_keys
    _write_gate2(approval_dir, dry_report.manifest_core_sha)

    freeze_env = dict(os.environ)
    freeze_env["VG_CAL_C0_FREEZE_AUTHORIZED"] = "1"
    freeze_result = c0_freeze.armed_freeze(
        _REPO_ROOT,
        cli_armed=True,
        env=freeze_env,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    assert freeze_result.outcome == c0_freeze.FreezeOutcome.PUBLISHED, freeze_result.detail
    campaign_dir = freeze_result.campaign_dir
    assert campaign_dir is not None

    # The fix itself: the freeze event sequence must now be exactly
    # [c0_freeze, split_frozen] — this is the event `check_leakage` requires
    # and that no production code emitted before round 14 finding #1.
    frozen_entries = Ledger(campaign_dir / "ledger.jsonl").entries
    assert [e.payload.get("kind") for e in frozen_entries] == ["c0_freeze", "split_frozen"]
    manifest = json.loads((campaign_dir / "c0_manifest.json").read_text(encoding="utf-8"))
    split_frozen_payload = frozen_entries[1].payload
    assert split_frozen_payload["realized_split_map_hash"] == manifest["realized_split_sha"]
    assert split_frozen_payload["seal_commitment"] == manifest["commitments"]["split_secret_sha256"]

    monkeypatch.setenv(campaign_cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    def run_stage(subcommand: str) -> dict[str, object]:
        exit_code = campaign_cli.main(
            [
                subcommand,
                "--campaign-dir",
                str(campaign_dir),
                "--secret-dir",
                str(secret_dir),
                "--approval-dir",
                str(approval_dir),
                "--armed",
            ]
        )
        out = json.loads(capsys.readouterr().out)
        assert exit_code == 0, out
        assert out.get("result") == "OK", out
        assert out.get("result") != "BLOCKED_LEAKAGE", out
        return out

    # plan (read-only, not gated by --armed dispatch at all).
    plan_exit = campaign_cli.main(
        ["plan", "--campaign-dir", str(campaign_dir), "--secret-dir", str(secret_dir)]
    )
    assert plan_exit == 0
    capsys.readouterr()

    run_stage("c1-fixtures")
    run_stage("c2-baseline")
    run_stage("c3a-f0-selection")
    run_stage("c3b-selection")

    from ._campaign_fixture import write_gate3_approval

    # `write_gate3_approval()`'s default `approved_at_utc` is tuned for the
    # synthetic `build_tiny_campaign()` fixture's fixed freeze time, but this
    # test drives a *real* `c0_freeze.armed_freeze()` whose `c0_freeze`
    # ledger event carries a wall-clock `event_time_utc`. `unseal_campaign()`
    # (D85) requires `freeze_time < gate3_time <= now`, so the approval must
    # be timestamped strictly after the real freeze event instead. Read the
    # freeze event's actual time and prefer wall-clock `now` (several real
    # campaign stages have run since the freeze, so `now` is normally
    # already later); fall back to `freeze_time + 1s` only if clock
    # resolution ever makes `now` appear no later than the freeze event.
    freeze_event_time_str = frozen_entries[0].payload.get("event_time_utc")
    assert isinstance(freeze_event_time_str, str)
    freeze_event_time = datetime.fromisoformat(
        freeze_event_time_str.removesuffix("Z") + "+00:00"
        if freeze_event_time_str.endswith("Z")
        else freeze_event_time_str
    )
    now_utc = datetime.now(timezone.utc)
    gate3_approved_at = now_utc if now_utc > freeze_event_time else freeze_event_time + timedelta(
        seconds=1
    )
    write_gate3_approval(
        approval_dir,
        approved_at_utc=gate3_approved_at.isoformat().replace("+00:00", "Z"),
    )
    run_stage("unseal")

    # This is the actual regression assertion: before round 14 finding #1,
    # `check_leakage` inside c4-holdout's pre-render leakage gate always
    # returned BLOCKED_LEAKAGE here (no `split_frozen` event existed for it
    # to authenticate the realized holdout set against), and render_stage
    # raised `RenderLeakageBlockedError` — this call would never reach
    # `result == "OK"`.
    c4_out = run_stage("c4-holdout")
    assert "holdout_executed_valid_entry_sha" in c4_out

    run_stage("close")

    # Independent confirmation, calling `check_leakage` directly the same
    # way `render_stage._refuse_if_pre_unseal_holdout` does in production.
    from voice_genesis.calibration.campaign.state import load_frozen_campaign
    from voice_genesis.calibration.campaign.render_stage import (
        STRATUM_FACTOR_NAMES as render_stratum_names,
    )
    from voice_genesis.calibration.campaign.render_stage import _row_inputs_for_split
    from voice_genesis.calibration.fixtures.controls import control_row_ids
    from voice_genesis.calibration.vocab import Split

    campaign = load_frozen_campaign(campaign_dir, secret_dir)
    control_ids = control_row_ids(tiny_matrix)
    holdout_row_ids = frozenset(
        rid for rid, split in campaign.realized_split.assignment.items() if split == Split.HOLDOUT
    )
    assert holdout_row_ids - control_ids, "test setup must realize a non-control holdout row"
    result = Ledger.check_leakage(
        campaign.ledger.entries,
        holdout_row_ids,
        None,
        control_row_ids=control_ids,
        realized_split_map=campaign.realized_split,
        split_verification_rows=_row_inputs_for_split(tiny_matrix, render_stratum_names),
        split_secret=campaign.split_secret,
    )
    assert result.blocked is None, result.blocked
    assert result.blocked != BlockedCode.BLOCKED_LEAKAGE
