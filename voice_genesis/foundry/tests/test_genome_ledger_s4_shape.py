"""test_genome_ledger_s4_shape.py — `results_s4/genome_ledger.json`
（④ 三角形補間 = VG-E1 第 0 世代・VG-S3 系列 4 体）の形状テスト。

`test_genome_ledger_shape.py`（S2 台帳）の検証キー集合を **read-only import
でそのまま再利用**し、検証ロジックの複製・分岐を避ける（forge_triangle の
テストと同じ流儀）。値そのもの（判定 note 等）の正本は
`results_s4/s4_record_2026-08-19.md` §6。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from test_genome_ledger_shape import (  # noqa: F401 (再利用)
    REQUIRED_GENOME_KEYS,
    REQUIRED_TOP_LEVEL_KEYS,
)

LEDGER_PATH = (
    Path(__file__).resolve().parent.parent / "results_s4" / "genome_ledger.json"
)

EXPECTED_VOICE_IDS = [f"VG-S3-{i:03d}" for i in range(1, 5)]


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def test_top_level_required_keys_present(ledger: Dict[str, Any]) -> None:
    assert REQUIRED_TOP_LEVEL_KEYS <= set(ledger)


def test_schema_is_pinned_version(ledger: Dict[str, Any]) -> None:
    assert ledger["schema"] == "voicegenesis-genome-ledger/0.1"


def test_every_genome_has_required_keys(ledger: Dict[str, Any]) -> None:
    for genome in ledger["genomes"]:
        missing = REQUIRED_GENOME_KEYS - set(genome)
        assert not missing, f"{genome.get('voice_id')}: missing {sorted(missing)}"


def test_vg_s3_batch1_all_present_unique_and_judged(ledger: Dict[str, Any]) -> None:
    ids = [g["voice_id"] for g in ledger["genomes"]]
    assert ids == EXPECTED_VOICE_IDS
    assert len(set(ids)) == len(ids)
    # 2026-08-19 開封済み判定が status に刻まれていること（未判定 CANDIDATE の
    # まま main に居座らない — SPR 行 1 審査済み台帳だけが第 0 世代の正本）
    for g in ledger["genomes"]:
        assert "UNSEALED_2026-08-19" in g["status"], g["voice_id"]


def test_hidden_control_is_marked_and_weightless(ledger: Dict[str, Any]) -> None:
    control = [g for g in ledger["genomes"] if "HIDDEN_CONTROL" in g["status"]]
    assert len(control) == 1
    assert control[0]["voice_id"] == "VG-S3-004"
    # S2 VG-S2-008 と同型: 台帳上は identity_latent_ref を残さない（正体逆算防止）
    assert control[0]["identity_latent_ref"] is None


def test_backbone_is_run5_40k_checkpoint(ledger: Dict[str, Any]) -> None:
    expected = ("sha256:"
                "d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5")
    assert ledger["backbone_checkpoint"] == expected
    for g in ledger["genomes"]:
        assert g["backbone_checkpoint"] == expected
