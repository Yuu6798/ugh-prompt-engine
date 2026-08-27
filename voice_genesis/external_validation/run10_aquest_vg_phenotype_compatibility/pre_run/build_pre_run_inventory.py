"""build_pre_run_inventory.py — Pre-Run Inventory（DESIGN_RUN10 §29 手順 2/3/5、§21 R10-G2）。

R10-G2 PRE_RUN_INVENTORY_COMPLETE が実在確認を要求する項目:

```text
AQUEST voicebank files / metadata
A0 recorded pitch inventory
VoiceGenesis Evolution Theory v0.3 location
AF01 v1.0 complete bundle
AF01 FREEZE_REGISTRATION.json
AF01 PAYLOAD_SHA256SUMS.txt
AF01 canonical C4 25-unit Body
AF01 C3/C4/G4 unit fixtures
AF01-SF1 generator source / environment
AF01 E0 calibration fixtures / truth manifest
V1 generation route and destination
meter implementation
```

本スクリプトは各項目を 3 値（`PRESENT` / `ABSENT` / `UNRESOLVED`）で記録し、
R10-G2 の gate 状態を導出する。**存在しないものを PRESENT と書かない**ことが
本スクリプトの唯一の役目であり、欠落は欠落として記録して BLOCKED を出す。

AF01 payload ledger だけは bundle 実体が無くても検証できる（同梱台帳の実バイト
sha256 が凍結値と一致するか）。これは §29 手順 6 の第 1 段に相当する。

出力は `pre_run/inventory.json`（§24 の `dependency_presence_report.json` は
本 inventory の `items[].state` が同じ役割を果たすため別ファイルにしない）。
測定値・集計値は一切含まない（§2.2 公開境界 — private_boundary.py が別途強制）。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_THIS_DIR = Path(__file__).resolve().parent
_RUN10_DIR = _THIS_DIR.parent
if str(_RUN10_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR))

from af01_freeze_verifier import (  # noqa: E402
    PINNED_LEDGER_PATH,
    check_ledger_structure,
    parse_payload_ledger,
    verify_bundle,
    verify_ledger_bytes,
)
from run10_schema import canonical_json_bytes  # noqa: E402

PRESENT = "PRESENT"
ABSENT = "ABSENT"
UNRESOLVED = "UNRESOLVED"

# repository 内で解決を試みる Evolution Theory 参照（§29 手順 5）。
EVOLUTION_THEORY_CANDIDATES = (
    "voice_genesis/foundry/VISION_evolution_theory_v0.3.md",
    "voice_genesis/foundry/VISION_evolution_theory_v0.2.md",
    "voice_genesis/foundry/VISION_evolution_theory_v0.1.md",
    "voice_genesis/foundry/VISION_evolution_v0.3_supplementA_spr.md",
)

# §36 が実在を確認した v0.3 本体の題名。リポジトリ内に無い場合は UNRESOLVED。
EVOLUTION_THEORY_CANONICAL = "VoiceGenesis_Evolution_Theory_v0.3_ja.md"


@dataclass
class InventoryItem:
    """R10-G2 の 1 項目。"""

    item_id: str
    state: str
    detail: str
    blocking: bool

    def to_json(self) -> Dict[str, object]:
        return {
            "item_id": self.item_id,
            "state": self.state,
            "detail": self.detail,
            "blocking": self.blocking,
        }


def _repo_root() -> Path:
    for candidate in [_RUN10_DIR, *_RUN10_DIR.parents]:
        if (candidate / ".git").exists():
            return candidate
    return _RUN10_DIR.parents[2]


def inventory_af01(bundle_root: Optional[Path]) -> List[InventoryItem]:
    """AF01 v1.0 関連項目（§29 手順 6 の第 1 段を含む）。"""
    items: List[InventoryItem] = []

    ledger_report = verify_ledger_bytes(PINNED_LEDGER_PATH)
    items.append(
        InventoryItem(
            item_id="af01_payload_sha256sums",
            state=PRESENT if ledger_report.passed else ABSENT,
            detail=(
                "同梱凍結台帳の実バイト sha256 が af01_payload_ledger_sha256 と一致"
                if ledger_report.passed
                else f"台帳検証失敗: {ledger_report.to_json()}"
            ),
            blocking=not ledger_report.passed,
        )
    )

    if ledger_report.passed:
        entries = parse_payload_ledger(PINNED_LEDGER_PATH.read_text(encoding="utf-8"))
        checks, problems = check_ledger_structure(entries)
        failed = [name for name, state in checks.items() if state != "PASS"]
        items.append(
            InventoryItem(
                item_id="af01_ledger_structure",
                state=PRESENT if not failed else ABSENT,
                detail=(
                    f"構造検査 {len(checks)}/{len(checks)} PASS"
                    f"（75 unit WAV / 25 alias x C3,C4,G4 / 9 E0 fixture / 6 aggregate probe /"
                    f" canonical 4 点）"
                    if not failed
                    else f"構造検査 FAIL: {failed} / {problems}"
                ),
                blocking=bool(failed),
            )
        )

    if bundle_root is None:
        items.append(
            InventoryItem(
                item_id="af01_complete_bundle",
                state=UNRESOLVED,
                detail=(
                    "bundle 実体の場所が未指定（--af01-bundle-root）。台帳経由の pin までは"
                    "検証済みだが、§29 手順 6 の実体照合と手順 7 の決定論的 replay は未実行。"
                ),
                blocking=True,
            )
        )
        items.append(
            InventoryItem(
                item_id="af01_freeze_registration",
                state=UNRESOLVED,
                detail="FREEZE_REGISTRATION.json の実体照合は bundle 取得後に行う。",
                blocking=True,
            )
        )
        return items

    bundle_report = verify_bundle(bundle_root)
    items.append(
        InventoryItem(
            item_id="af01_complete_bundle",
            state=PRESENT if bundle_report.passed else ABSENT,
            detail=f"verdict={bundle_report.verdict} checks={bundle_report.checks}",
            blocking=not bundle_report.passed,
        )
    )
    registration = Path(bundle_root) / "FREEZE_REGISTRATION.json"
    items.append(
        InventoryItem(
            item_id="af01_freeze_registration",
            state=PRESENT if registration.is_file() else ABSENT,
            detail=str(registration),
            blocking=not registration.is_file(),
        )
    )
    return items


def inventory_aquest(voicebank_root: Optional[Path]) -> List[InventoryItem]:
    """A0 関連項目（§7.1 / §9.4）。実体は User 供給・machine-dependent。"""
    if voicebank_root is None or not Path(voicebank_root).is_dir():
        detail = (
            "A0 voicebank root が未指定または不在。§7.1 の必須 pin"
            "（voicebank version / file order / all WAV SHA256 / oto.ini / character.txt）"
            "を確定できない。§32 Stop Rule 3 の状態。"
        )
        return [
            InventoryItem("aquest_voicebank_files", UNRESOLVED, detail, blocking=True),
            InventoryItem("a0_recorded_pitch_inventory", UNRESOLVED, detail, blocking=True),
        ]

    root = Path(voicebank_root)
    wavs = sorted(p for p in root.rglob("*.wav") if p.is_file())
    oto = sorted(p for p in root.rglob("oto.ini") if p.is_file())
    character = sorted(p for p in root.rglob("character.txt") if p.is_file())
    present = bool(wavs) and bool(oto)
    return [
        InventoryItem(
            item_id="aquest_voicebank_files",
            state=PRESENT if present else ABSENT,
            detail=(
                f"raw WAV {len(wavs)} 件 / oto.ini {len(oto)} 件 / character.txt "
                f"{len(character)} 件"
            ),
            blocking=not present,
        ),
        InventoryItem(
            item_id="a0_recorded_pitch_inventory",
            state=UNRESOLVED,
            detail=(
                "収録ピッチ構造の判定には oto.ini マッピングと raw-unit F0 推定が要る"
                "（§9.4）。measurement 層未実装のため未確定。"
                "実質単一ピッチ収録なら cross-pitch persistence = NOT_EVALUABLE。"
            ),
            blocking=False,
        ),
    ]


def inventory_repository(repo: Path) -> List[InventoryItem]:
    """リポジトリ側の参照解決（§29 手順 2/5）。"""
    found = [c for c in EVOLUTION_THEORY_CANDIDATES if (repo / c).is_file()]
    canonical_present = any(c.endswith("VISION_evolution_theory_v0.3.md") for c in found)
    items = [
        InventoryItem(
            item_id="evolution_theory_reference",
            state=PRESENT if canonical_present else UNRESOLVED,
            detail=(
                f"リポジトリ内に存在する参照: {found}. "
                f"§36 が実在を確認した v0.3 本体 {EVOLUTION_THEORY_CANONICAL!r} は"
                "リポジトリ内に不在（Drive 側実体の sha 未取得）。"
            ),
            blocking=not canonical_present,
        ),
        InventoryItem(
            item_id="meter_implementation",
            state=ABSENT,
            detail=(
                "§11 の測定器 family（M0 Technical Integrity 〜 M6 Identity Signature）は"
                "未実装。measurement/ ディレクトリは本 PR の範囲外。"
            ),
            blocking=True,
        ),
        InventoryItem(
            item_id="v1_generation_route",
            state=UNRESOLVED,
            detail=(
                "§7.4 V1 = AF01 を pin 済み VoiceGenesis transport 経路へ一度だけ通した出力。"
                "使用する transport / renderer / vocoder の選定が未裁定。"
                "生成不能なら Phase A = BLOCKED_V1_UNAVAILABLE。"
            ),
            blocking=True,
        ),
    ]
    return items


def build_inventory(
    af01_bundle_root: Optional[Path] = None,
    aquest_voicebank_root: Optional[Path] = None,
    repo: Optional[Path] = None,
) -> Dict[str, object]:
    """R10-G2 inventory 文書を組み立てる。"""
    root = repo if repo is not None else _repo_root()
    items = (
        inventory_af01(af01_bundle_root)
        + inventory_aquest(aquest_voicebank_root)
        + inventory_repository(root)
    )
    blocking = [item for item in items if item.blocking]
    return {
        "schema": "voicegenesis-run10-pre-run-inventory/0.1",
        "run_id": "RUN10",
        "gate": "R10-G2",
        "gate_name": "PRE_RUN_INVENTORY_COMPLETE",
        "gate_state": "COMPLETE" if not blocking else "BLOCKED",
        "blocking_items": [item.item_id for item in blocking],
        "items": [item.to_json() for item in items],
        "note": (
            "測定値・集計値を含まない構造 inventory である（§2.2 公開境界）。"
            "AF-P0 / AF0 は §7.7 により optional historical reference のため、"
            "欠損しても本 gate を BLOCK しない（AF 固有 family のみ NOT_EVALUABLE）。"
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RUN10 Pre-Run Inventory（§29 手順 3）")
    parser.add_argument("--af01-bundle-root", default=None, help="AF01 v1.0 bundle 展開先。")
    parser.add_argument(
        "--aquest-voicebank-root",
        default=None,
        help="A0 = AquesTalk 由来 UTAU デフォルト音声のルート（private ストレージ）。",
    )
    parser.add_argument(
        "--out",
        default=str(_THIS_DIR / "inventory.json"),
        help="inventory.json の書き出し先。",
    )
    args = parser.parse_args(argv)

    inventory = build_inventory(
        af01_bundle_root=Path(args.af01_bundle_root) if args.af01_bundle_root else None,
        aquest_voicebank_root=(
            Path(args.aquest_voicebank_root) if args.aquest_voicebank_root else None
        ),
    )
    payload = canonical_json_bytes(inventory)
    Path(args.out).write_bytes(payload)
    print(payload.decode("utf-8"))
    return 0 if inventory["gate_state"] == "COMPLETE" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
