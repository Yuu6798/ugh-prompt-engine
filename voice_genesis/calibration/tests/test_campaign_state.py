"""`campaign/state.py` のテスト: 凍結 campaign dir の読み込み + orphan 拒否 +
手続フェーズ導出（IMPLEMENTATION_MAP_v1.md §6.4）。tmp_path 配下のみ操作する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration.campaign.state import (
    CampaignPhase,
    CampaignStateError,
    current_phase,
    gate_monotonicity_ok,
    load_frozen_campaign,
    phases_passed,
)
from voice_genesis.calibration.provenance import Ledger

from ._campaign_fixture import build_tiny_campaign


def test_load_frozen_campaign_succeeds(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert campaign.campaign_id == "RUN10-CAL-TESTFIXTURE"
    assert campaign.split_secret == b"S" * 32
    assert campaign.render_root_secret == b"R" * 32
    assert campaign.realized_split.assignment
    assert CampaignPhase.PREPARATION_VALID in campaign.phases_passed()
    assert campaign.current_phase() == CampaignPhase.PREPARATION_VALID


def test_orphan_campaign_without_secret_dir_refused(tmp_path: Path) -> None:
    campaign_dir, _secret_root = build_tiny_campaign(tmp_path, write_secrets=False)
    empty_secret_root = tmp_path / "empty-secrets"
    empty_secret_root.mkdir()
    with pytest.raises(CampaignStateError, match="orphan campaign"):
        load_frozen_campaign(campaign_dir, empty_secret_root)


def test_missing_manifest_refused(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "campaigns" / "NOPE"
    campaign_dir.mkdir(parents=True)
    with pytest.raises(CampaignStateError, match="missing c0_manifest.json"):
        load_frozen_campaign(campaign_dir, tmp_path / "secrets")


def test_secret_commitment_mismatch_refused(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    secret_dir = secret_root / "RUN10-CAL-TESTFIXTURE"
    (secret_dir / "split_secret.bin").write_bytes(b"X" * 32)
    with pytest.raises(CampaignStateError, match="commitment"):
        load_frozen_campaign(campaign_dir, secret_root)


def test_ledger_must_open_with_c0_freeze(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    ledger_path = campaign_dir / "ledger.jsonl"
    ledger_path.write_text("", encoding="utf-8")
    Ledger(ledger_path).append({"kind": "not_a_freeze_event"})
    with pytest.raises(CampaignStateError, match="c0_freeze"):
        load_frozen_campaign(campaign_dir, secret_root)


def test_gate_monotonicity_ok_for_prefix_and_violated_for_gap() -> None:
    ok = frozenset({CampaignPhase.PREPARATION_VALID, CampaignPhase.FIXTURE_VALID})
    assert gate_monotonicity_ok(ok)

    gapped = frozenset({CampaignPhase.PREPARATION_VALID, CampaignPhase.BASELINE_AUDITED})
    assert not gate_monotonicity_ok(gapped)


def test_phases_passed_and_current_phase_from_synthetic_ledger_entries() -> None:
    class _FakeEntry:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

    entries = [
        _FakeEntry({"kind": "c0_freeze"}),
        _FakeEntry({"kind": "fixture_valid"}),
        _FakeEntry({"kind": "baseline_audited"}),
    ]
    passed = phases_passed(entries)  # type: ignore[arg-type]
    assert passed == {
        CampaignPhase.PREPARATION_VALID,
        CampaignPhase.FIXTURE_VALID,
        CampaignPhase.BASELINE_AUDITED,
    }
    assert current_phase(passed) == CampaignPhase.BASELINE_AUDITED
    assert gate_monotonicity_ok(passed)


def test_load_frozen_campaign_reads_ledger_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding #12 regression: `load_frozen_campaign()` reads `ledger.jsonl`
    exactly once (`Ledger.load_with_verification` builds entries + runs
    chain verification from the same buffer), rather than once via
    `Ledger(path)` and a second time via `ledger.verify_chain()`.

    Read-counting hook (`#345` 指摘③ 追補): `_read_and_verify()` used to read
    the ledger via `Path.read_text()`; it now opens the file itself
    (`path.open("rb")`) so it can `os.fstat()` the same file descriptor the
    content was read from (needed for the G1 stat-based change detector —
    `read_text()` doesn't expose the underlying fd). This test therefore
    counts `Path.open()` calls on `ledger_path` rather than `read_text()`
    calls; the invariant under test (the ledger bytes are read from disk
    exactly once) is unchanged, only the API used to do the read changed."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    ledger_path = campaign_dir / "ledger.jsonl"

    read_count = {"n": 0}
    original_open = Path.open

    def _counting_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self == ledger_path:
            read_count["n"] += 1
        return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _counting_open)

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert campaign.ledger.entries  # sanity: the ledger still loaded correctly
    assert read_count["n"] == 1
