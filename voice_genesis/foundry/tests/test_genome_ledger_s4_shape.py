"""test_genome_ledger_s4_shape.py — `results_s4/genome_ledger.json`
（④ 三角形補間 = VG-E1 founding batch・VG-S3 系列 4 エントリ）の形状テスト。

`test_genome_ledger_shape.py`（S2 台帳）の**検証関数そのものを read-only
import で呼び出して**本台帳へ適用する（キー集合だけの再利用では型検証が
落ちる — セルフレビュー #6。forge_triangle のテストと同じ流儀）。
値そのもの（判定 note 等）の正本は `results_s4/s4_record_2026-08-19.md` §6。

本ファイルと S2 側の shape テストは pyproject の testpaths にファイル単位で
登録されており CI で常時収集される。Foundry の任意依存は個別テスト
単位で分離され、本台帳テストには影響しない。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest

# 同ディレクトリの S2 shape テストをファイルパスで明示ロードする（素の
# `import test_genome_ledger_shape` は pytest 既定の prepend import mode で
# しか解決できず、--import-mode=importlib では collection error になる —
# 今回セルフレビューで実測。パスロードは両モードで動く）。
_S2_SHAPE_PATH = Path(__file__).resolve().parent / "test_genome_ledger_shape.py"
_spec = importlib.util.spec_from_file_location("_s2_ledger_shape", _S2_SHAPE_PATH)
assert _spec is not None and _spec.loader is not None
s2_shape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s2_shape)

LEDGER_PATH = (
    Path(__file__).resolve().parent.parent / "results_s4" / "genome_ledger.json"
)

EXPECTED_VOICE_IDS = [f"VG-S3-{i:03d}" for i in range(1, 5)]


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def test_s2_validators_hold_for_s4_ledger(ledger: Dict[str, Any]) -> None:
    """S2 側の検証関数を直接適用（必須キー・genome キー・最低限の型・
    voice_id 一意性）。ロジックの複製・分岐を避ける。"""
    s2_shape.test_ledger_is_valid_json_object(ledger)
    s2_shape.test_top_level_required_keys_present(ledger)
    s2_shape.test_schema_is_pinned_version(ledger)
    s2_shape.test_genomes_is_nonempty_list(ledger)
    s2_shape.test_every_genome_has_required_keys(ledger)
    s2_shape.test_every_genome_has_minimal_field_types(ledger)
    s2_shape.test_voice_id_is_unique(ledger)


def test_vg_s3_batch1_exact_order_and_judged(ledger: Dict[str, Any]) -> None:
    ids = [g["voice_id"] for g in ledger["genomes"]]
    assert ids == EXPECTED_VOICE_IDS
    # 2026-08-19 開封済み判定が status に刻まれていること（未判定 CANDIDATE の
    # まま main に居座らない — SPR 行 1 審査済み台帳だけが founding batch の正本）
    for g in ledger["genomes"]:
        assert "UNSEALED_2026-08-19" in g["status"], g["voice_id"]


def test_hidden_control_is_marked_weightless_and_unreferenced(
    ledger: Dict[str, Any],
) -> None:
    control = [g for g in ledger["genomes"] if "HIDDEN_CONTROL" in g["status"]]
    assert len(control) == 1
    assert control[0]["voice_id"] == "VG-S3-004"
    # S2 VG-S2-008 と同型: 正体逆算防止のため latent 参照を残さない
    assert control[0]["identity_latent_ref"] is None
    # アンカー素通しに補間重みは存在しない（weights キー自体が無い or None —
    # セルフレビュー #6: 「weightless」を実際に検査する）
    assert control[0]["identity"].get("weights") is None
    # rights はリツ配布規約準拠（三角形合成の既定値を継承しない —
    # セルフレビュー #3）
    assert "RITSU" in control[0]["rights_class"]


def test_anchors_present_typed_and_parent_ids_resolve(
    ledger: Dict[str, Any],
) -> None:
    """anchors の実在・型と、genome の系譜参照（parent_ids）が実在アンカーへ
    解決することを固定する（手編集での頂点削除・parent_id typo の機械検出 —
    S2 スイートの anchors 検証に相当する S4 版）。"""
    anchors = ledger["anchors"]
    expected = {"VG-S3-ANCHOR-RITSU", "VG-S3-ANCHOR-PJS", "VG-S3-ANCHOR-USER"}
    assert expected <= set(anchors)
    for name in expected:
        entry = anchors[name]
        assert isinstance(entry["embed_sha256"], str) and len(entry["embed_sha256"]) == 64
        assert isinstance(entry["embed_l2_norm"], float)
    for genome in ledger["genomes"]:
        for parent in genome["parent_ids"]:
            assert parent in expected, (
                f"{genome['voice_id']}: parent_id {parent} が実在アンカーに解決しない")


def test_backbone_is_run5_40k_checkpoint(ledger: Dict[str, Any]) -> None:
    expected = ("sha256:"
                "d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5")
    assert ledger["backbone_checkpoint"] == expected
    for g in ledger["genomes"]:
        assert g["backbone_checkpoint"] == expected
    # anchors の説明文が run 5 export を指すこと（run-4 期固定文言の再発防止）
    for name, anchor in ledger["anchors"].items():
        if isinstance(anchor, dict) and "description" in anchor:
            assert "run 5" in anchor["description"], name
