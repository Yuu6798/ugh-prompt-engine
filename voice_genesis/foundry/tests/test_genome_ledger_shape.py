"""test_genome_ledger_shape.py — `results_s2/genome_ledger.json` の形状テスト
(review #264 R1 P1)。

台帳はコード生成物ではなく人手で編集される記録ファイルであり、これまで
enforcing なテストが存在しなかった。将来の編集（追記・訂正・キー名変更等）
で台帳の必須構造が黙って壊れないよう、以下を固定 fixture として検証する:

- トップレベルスキーマ（必須キーの存在）
- genome ごとの必須キー（存在 + 最低限の型）
- `voice_id` の一意性
- `VG-S2-001`〜`VG-S2-010` の存在

値そのもの（sha256・alpha・note 等の記述内容）はここでは検証しない
（`results_s2/s2_record_2026-08-16.md` が記述内容の正本）。本テストは
「構造が壊れていないこと」のみを機械的に enforce する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

LEDGER_PATH = Path(__file__).resolve().parent.parent / "results_s2" / "genome_ledger.json"

# トップレベル必須キー（`note_*` は任意の自由記述メモのため必須に含めない）。
REQUIRED_TOP_LEVEL_KEYS = {
    "schema",
    "schema_ref",
    "batch",
    "date",
    "backbone_checkpoint",
    "genomes",
    "anchors",
}

# genome エントリごとの必須キー（`blind_label_batch3` はバッチ3のみの任意
# キーのため含めない）。
REQUIRED_GENOME_KEYS = {
    "voice_id",
    "generation",
    "parent_ids",
    "backbone_checkpoint",
    "identity_latent_ref",
    "identity",
    "phonation",
    "performance_prior",
    "mutation_ops",
    "seed",
    "rights_class",
    "status",
}

EXPECTED_VOICE_IDS = {f"VG-S2-{i:03d}" for i in range(1, 11)}


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    assert LEDGER_PATH.exists(), f"genome_ledger.json not found: {LEDGER_PATH}"
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def test_ledger_is_valid_json_object(ledger: Dict[str, Any]) -> None:
    assert isinstance(ledger, dict)


def test_top_level_required_keys_present(ledger: Dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - ledger.keys()
    assert not missing, f"missing top-level keys: {sorted(missing)}"


def test_schema_is_pinned_version(ledger: Dict[str, Any]) -> None:
    assert ledger["schema"] == "voicegenesis-genome-ledger/0.1"


def test_genomes_is_nonempty_list(ledger: Dict[str, Any]) -> None:
    genomes = ledger["genomes"]
    assert isinstance(genomes, list)
    assert len(genomes) > 0


def test_every_genome_has_required_keys(ledger: Dict[str, Any]) -> None:
    for genome in ledger["genomes"]:
        assert isinstance(genome, dict)
        missing = REQUIRED_GENOME_KEYS - genome.keys()
        assert not missing, f"genome {genome.get('voice_id', '<unknown>')} missing keys: {sorted(missing)}"


def test_every_genome_has_minimal_field_types(ledger: Dict[str, Any]) -> None:
    for genome in ledger["genomes"]:
        voice_id = genome["voice_id"]
        assert isinstance(voice_id, str) and voice_id, voice_id
        assert isinstance(genome["generation"], int)
        assert isinstance(genome["parent_ids"], list) and len(genome["parent_ids"]) > 0
        assert all(isinstance(p, str) and p for p in genome["parent_ids"])
        assert isinstance(genome["backbone_checkpoint"], str) and genome["backbone_checkpoint"]
        assert genome["identity_latent_ref"] is None or (
            isinstance(genome["identity_latent_ref"], str)
            and genome["identity_latent_ref"].startswith("blob:sha256:")
        )
        assert isinstance(genome["identity"], dict) and "method" in genome["identity"]
        assert isinstance(genome["phonation"], dict)
        assert isinstance(genome["performance_prior"], dict)
        assert isinstance(genome["mutation_ops"], list) and len(genome["mutation_ops"]) > 0
        assert all(isinstance(op, str) and op for op in genome["mutation_ops"])
        assert isinstance(genome["seed"], int)
        assert isinstance(genome["rights_class"], str) and genome["rights_class"]
        assert isinstance(genome["status"], str) and genome["status"]


def test_voice_id_is_unique(ledger: Dict[str, Any]) -> None:
    voice_ids = [g["voice_id"] for g in ledger["genomes"]]
    assert len(voice_ids) == len(set(voice_ids)), f"duplicate voice_id(s) in ledger: {voice_ids}"


def test_vg_s2_001_through_010_all_present(ledger: Dict[str, Any]) -> None:
    voice_ids = {g["voice_id"] for g in ledger["genomes"]}
    missing = EXPECTED_VOICE_IDS - voice_ids
    assert not missing, f"missing expected voice_id(s): {sorted(missing)}"


def test_anchors_section_has_both_anchor_embeddings(ledger: Dict[str, Any]) -> None:
    anchors = ledger["anchors"]
    assert isinstance(anchors, dict)
    for key in ("VG-S2-ANCHOR-RITSU", "VG-S2-ANCHOR-PJS"):
        assert key in anchors, f"missing anchor: {key}"
        anchor = anchors[key]
        assert isinstance(anchor, dict)
        assert isinstance(anchor.get("embed_l2_norm"), (int, float))
    assert isinstance(anchors.get("anchor_cosine_similarity"), (int, float))
