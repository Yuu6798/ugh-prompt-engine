"""`campaign/render_stage.py` のテスト: fresh-process 2 重 render + determinism
+ resume + leakage 検査（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import render_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign

from ._campaign_fixture import build_tiny_campaign, small_matrix_subset


@pytest.mark.slow
def test_c4_render_refuses_leakage_pre_unseal(tmp_path: Path) -> None:
    """holdout render を unseal 前に試みると `BLOCKED_LEAKAGE` で拒否される
    （§7）。tiny subset は全 456 行を被覆しないため `check_leakage` は常に
    fail-closed する — これはテスト対象の性質そのもの（正当な fail-closed
    経路であり、フル matrix を使わずに検証できる）。"""
    subset = small_matrix_subset(6)
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(render_stage.RenderLeakageBlockedError):
        render_stage.run_render_stage(campaign, subset, stage="c4")

    # no renders/ledger side effects from the refused attempt
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())


@pytest.mark.slow
def test_c1_render_determinism_and_resume(tmp_path: Path) -> None:
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert outcomes
    assert all(o.status == "rendered" for o in outcomes)
    assert all(len(o.sha256) == 64 for o in outcomes)

    # each instance rendered exactly once with a byte-verified sha256 file
    for o in outcomes:
        pcm_path = campaign.renders_dir / o.row_id / f"{o.probe_index}.pcm"
        assert pcm_path.is_file()
        assert hashlib.sha256(pcm_path.read_bytes()).hexdigest() == o.sha256

    render_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert len(render_events) == len(outcomes)
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1

    # resume: second run skips every instance without re-rendering
    resumed = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in resumed)
    assert {o.sha256 for o in resumed} == {o.sha256 for o in outcomes}

    # a second fixture_valid event is appended per c1 run (procedural marker,
    # not a render side effect) — no new render events should appear though.
    render_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert len(render_events_after) == len(outcomes)


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_corrupted_file(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.write_bytes(b"\x00\x01corrupted-bytes")

    with pytest.raises(render_stage.RenderStaleError):
        render_stage.run_render_stage(campaign, subset, stage="c1")


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_missing_file(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.unlink()

    with pytest.raises(render_stage.RenderStaleError):
        render_stage.run_render_stage(campaign, subset, stage="c1")
