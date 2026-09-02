"""c0_freeze.py のテスト（設計正本 §3, §7, §18, IMPLEMENTATION_MAP §6.3）。

**授権境界**（IMPLEMENTATION_MAP §0）: `armed_freeze` を武装実行するテストは
すべて `tmp_path` 配下の test-local な approval_dir/secret_dir/campaigns_dir に
対してのみ行う。本リポジトリへの実 freeze・secret 生成は一切行わない。
`dry_run` は書込を行わないため本リポジトリに対して直接実行してよい。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from voice_genesis.calibration import approvals, c0_freeze, c0_validate
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


def _write_gate1(approval_dir: Path, *, nonce: str = _DEFAULT_NONCE) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _DESIGN_SHA,
        "memo_sha256": _MEMO_SHA,
        "authorization_nonce": nonce,
        "cost_caps": {"compute": 36000.0, "storage": 1_000_000_000, "budget": 1.0},
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"],
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
    published_campaign_ids = {p.name for p in campaigns_dir.iterdir()}

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
    assert {p.name for p in campaigns_dir.iterdir()} == published_campaign_ids
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
    # by `_publish_lock()`); everything else must be gone.
    remaining_secret = [
        p for p in (secret_dir.iterdir() if secret_dir.exists() else [])
        if p.name != c0_freeze._PUBLISH_LOCK_NAME
    ]
    remaining_campaign = list(campaigns_dir.iterdir()) if campaigns_dir.exists() else []
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
    remaining_campaign = list(campaigns_dir.iterdir()) if campaigns_dir.exists() else []
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
    """A single UNJUSTIFIED row (no gate1 dependency at all)."""
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
            ]
        ),
        encoding="utf-8",
    )


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
    # frozen_inputs is a non-core section: stripping it must not change manifest_core_sha.
    assert "frozen_inputs" not in c0_freeze.core_payload(manifest)
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

    def flaky_check(campaigns_dir_: Path, nonce: str) -> str | None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate the early (pre-lock) check racing past the just-published
            # campaign — the locked recheck (2nd call) must still catch it.
            return None
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
        lambda manifest: c0_validate.C0ValidationResult(),
    )

    campaign_staging = tmp_path / "campaign"
    secret_staging = tmp_path / "secret"
    campaign_staging.mkdir()
    secret_staging.mkdir()

    matrix_rows = build_matrix()
    row_inputs = c0_freeze._row_inputs_for_split(matrix_rows, c0_freeze.STRATUM_FACTOR_NAMES)
    split_secret = b"\x01" * 32
    realized = realize_split(row_inputs, split_secret, c0_freeze.STRATUM_FACTOR_NAMES)

    manifest = c0_freeze.build_manifest(_REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02")
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
        campaign_staging, secret_staging, row_inputs, split_secret, render_root_secret, commitments
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
