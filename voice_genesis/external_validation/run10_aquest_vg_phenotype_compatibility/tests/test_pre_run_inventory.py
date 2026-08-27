"""test_pre_run_inventory.py — Pre-Run Inventory（DESIGN_RUN10 §29 手順 3/5、§21 R10-G2）。

§28 最低テストのうち本ファイルが担当する項目:

```text
14 Pre-Run inventory generated before measurement
15 A0 raw unit count recorded
17 single-pitch A0 does not fail cross-pitch gate; marks NOT_EVALUABLE
18 Evolution Theory reference resolved or explicit BLOCKED state
```

inventory は「存在しないものを PRESENT と書かない」ことだけを役目とする。
現時点では A0 未取得・meter 未実装のため R10-G2 = BLOCKED が正しい状態である。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))
if str(_RUN_DIR / "pre_run") not in sys.path:
    sys.path.insert(0, str(_RUN_DIR / "pre_run"))

import build_pre_run_inventory as inv  # noqa: E402
import run10_schema as m  # noqa: E402

COMMITTED_INVENTORY = _RUN_DIR / "pre_run" / "inventory.json"


def _items(document: dict) -> dict:
    return {item["item_id"]: item for item in document["items"]}


def test_inventory_reports_blocked_without_a0_and_meters() -> None:
    """§28-14: 本測定前の inventory は現状を BLOCKED として記録する。"""
    document = inv.build_inventory()
    assert document["gate"] == "R10-G2"
    assert document["gate_state"] == "BLOCKED"
    blocking = set(document["blocking_items"])
    assert "aquest_voicebank_files" in blocking
    assert "meter_implementation" in blocking


def test_af01_ledger_items_are_present_without_bundle() -> None:
    """bundle 実体なしでも台帳段階までは PRESENT になる（§29 手順 6 の第 1 段）。"""
    items = _items(inv.build_inventory())
    assert items["af01_payload_sha256sums"]["state"] == inv.PRESENT
    assert items["af01_ledger_structure"]["state"] == inv.PRESENT
    # 実体照合と replay は未実行であることを UNRESOLVED として残す。
    assert items["af01_complete_bundle"]["state"] == inv.UNRESOLVED
    assert items["af01_freeze_registration"]["state"] == inv.UNRESOLVED


def test_a0_items_are_unresolved_when_voicebank_absent() -> None:
    """§28-15: A0 未取得を PRESENT と書かない。"""
    items = _items(inv.build_inventory(aquest_voicebank_root=None))
    assert items["aquest_voicebank_files"]["state"] == inv.UNRESOLVED
    assert items["a0_recorded_pitch_inventory"]["state"] == inv.UNRESOLVED


def test_a0_raw_unit_count_is_recorded_when_voicebank_present(tmp_path: Path) -> None:
    """§28-15: voicebank があれば raw WAV 件数を記録する。"""
    root = tmp_path / "voicebank"
    root.mkdir()
    for name in ("a.wav", "i.wav", "ka.wav"):
        (root / name).write_bytes(b"RIFF")
    (root / "oto.ini").write_text("", encoding="utf-8")
    items = _items(inv.build_inventory(aquest_voicebank_root=root))
    assert items["aquest_voicebank_files"]["state"] == inv.PRESENT
    assert "raw WAV 3 件" in items["aquest_voicebank_files"]["detail"]


def test_single_pitch_a0_does_not_block_the_gate(tmp_path: Path) -> None:
    """§28-17 / §9.4 / H4: A0 に存在しないピッチ軸を必須化しない。

    `a0_recorded_pitch_inventory` は blocking=False であり、単一ピッチ収録でも
    R10-G2 を落とさない（cross-pitch persistence は NOT_EVALUABLE となる）。
    """
    root = tmp_path / "voicebank"
    root.mkdir()
    (root / "a.wav").write_bytes(b"RIFF")
    (root / "oto.ini").write_text("", encoding="utf-8")
    items = _items(inv.build_inventory(aquest_voicebank_root=root))
    assert items["a0_recorded_pitch_inventory"]["blocking"] is False
    assert "NOT_EVALUABLE" in items["a0_recorded_pitch_inventory"]["detail"]


def test_evolution_theory_reference_has_explicit_state() -> None:
    """§28-18: v0.3 本体が不在であることを解決済みと書かない。"""
    items = _items(inv.build_inventory())
    item = items["evolution_theory_reference"]
    assert item["state"] in (inv.PRESENT, inv.UNRESOLVED)
    if item["state"] == inv.UNRESOLVED:
        assert inv.EVOLUTION_THEORY_CANONICAL in item["detail"]
        assert item["blocking"] is True


def test_committed_inventory_matches_current_generation() -> None:
    """commit した inventory.json が実装の出力と一致する（stale 化を防ぐ）。"""
    assert COMMITTED_INVENTORY.is_file()
    committed = json.loads(COMMITTED_INVENTORY.read_text(encoding="utf-8"))
    assert committed == inv.build_inventory()


def test_inventory_is_canonical_json_bytes() -> None:
    """成果物 JSON の正規化バイト規約に従う（replay 可能性）。"""
    raw = COMMITTED_INVENTORY.read_bytes()
    assert raw == m.canonical_json_bytes(json.loads(raw.decode("utf-8")))


def test_inventory_carries_no_measured_values() -> None:
    """§2.2: inventory は構造情報のみで、測定値・集計値を含まない。"""
    raw = COMMITTED_INVENTORY.read_text(encoding="utf-8")
    m.assert_no_forbidden_score_field(json.loads(raw))
    for banned in ("f0_hz", "formant", "hnr", "cpp", "spectral_tilt"):
        assert banned not in raw
