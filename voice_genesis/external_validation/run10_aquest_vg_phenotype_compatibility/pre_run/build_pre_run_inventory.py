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
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
_RUN10_DIR = _THIS_DIR.parent
if str(_RUN10_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR))

from af01_freeze_verifier import (  # noqa: E402
    PINNED_LEDGER_PATH,
    check_ledger_structure,
    containment_violation,
    parse_payload_ledger,
    verify_bundle,
    verify_deterministic_replay,
    verify_ledger_bytes,
)
from svp_rpe.utils.atomic_io import atomic_write_bytes  # noqa: E402

from run10_schema import (  # noqa: E402
    AF01_ALIAS_COUNT,
    AF01_E0_CALIBRATION_CASES,
    AF01_FROZEN_HASHES,
    AF01_PITCHES,
    AF01_UNIT_FILE_COUNT,
    canonical_json_bytes,
)

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


def inventory_af01(
    bundle_root: Optional[Path],
    run_replay: bool = False,
) -> List[InventoryItem]:
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
        items.append(
            InventoryItem(
                item_id="af01_deterministic_replay",
                state=UNRESOLVED,
                detail="§29 手順 7 の決定論的 payload replay は bundle 取得後に行う。",
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
    # §29 手順 7 は手順 6 と独立の必須項目である。bundle 実体があるだけで
    # `af01_complete_bundle` を PRESENT にして終わると、凍結 generator が
    # payload を再生成できない参照の上で R10-G2 が COMPLETE になり得る
    # （PR #330 Codex 第 9 巡 P1）。replay は generator を実行するため既定では
    # 走らせず、未実行は UNRESOLVED かつ blocking として残す。
    if not run_replay:
        items.append(
            InventoryItem(
                item_id="af01_deterministic_replay",
                state=UNRESOLVED,
                detail=(
                    "§29 手順 7 未実行。`build_pre_run_inventory.py --af01-replay` で"
                    "決定論的 payload replay を実行するまで R10-G2 を塞ぐ。"
                ),
                blocking=True,
            )
        )
    else:
        replay_report = verify_deterministic_replay(bundle_root)
        items.append(
            InventoryItem(
                item_id="af01_deterministic_replay",
                state=PRESENT if replay_report.passed else ABSENT,
                detail=f"verdict={replay_report.verdict} checks={replay_report.checks}",
                blocking=not replay_report.passed,
            )
        )

    registration = Path(bundle_root) / "FREEZE_REGISTRATION.json"
    # bundle 外の JSON への symlink を「必須登録あり」と数えない（第 6 巡 P2）。
    breach = containment_violation(Path(bundle_root), "FREEZE_REGISTRATION.json")
    if breach is not None:
        state, detail = ABSENT, f"bundle に自己完結していない: {breach}"
    else:
        state, detail = _check_freeze_registration(registration)
    items.append(
        InventoryItem(
            item_id="af01_freeze_registration",
            state=state,
            detail=detail,
            blocking=state != PRESENT,
        )
    )
    return items


# FREEZE_REGISTRATION.json が宣言すべき固定値（閉世界契約）。存在するだけでは
# required item を満たさない — 空・stale・矛盾した登録が R10-G2 を通してしまう
# （PR #330 Codex 第 1 巡 P2 / AGENTS.md「parse 可能 ≠ 形状正しい」）。
FREEZE_REGISTRATION_EXPECTED: Dict[str, object] = {
    "schema": "voicegenesis-freeze-registration/1.0",
    "voice_id": "AF01",
    "specimen_version": "1.0",
    "freeze_status": "FROZEN",
    "unit_file_count": AF01_UNIT_FILE_COUNT,
    "pitch_fixture_count": len(AF01_PITCHES),
    "e0_calibration_cases": AF01_E0_CALIBRATION_CASES,
    "mutation_policy": "PROHIBITED_WITHIN_RUN10",
}


# FREEZE_REGISTRATION.json 側のキー名 → run10_schema の凍結 pin 名。
# 登録ファイルは `payload_ledger_sha256` / `generator_sha256` / `manifest_sha256`
# のように `af01_` 接頭辞を持たない欄があるため、名前の一致に頼らず明示写像する。
FREEZE_REGISTRATION_HASH_KEYS: Dict[str, str] = {
    "payload_ledger_sha256": "af01_payload_ledger_sha256",
    "af01_spec_sha256": "af01_spec_sha256",
    "generator_sha256": "af01_generator_sha256",
    "manifest_sha256": "af01_manifest_sha256",
}


def _check_freeze_registration(path: Path) -> Tuple[str, str]:
    """FREEZE_REGISTRATION.json を parse して宣言内容を凍結値と照合する。"""
    if not path.is_file():
        return ABSENT, f"{path} が存在しない"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ABSENT, f"{path} を読めない/parse できない: {exc}"
    if not isinstance(doc, dict):
        return ABSENT, f"{path}: JSON オブジェクトでない"

    problems: List[str] = []
    for key, expected in FREEZE_REGISTRATION_EXPECTED.items():
        if doc.get(key) != expected:
            problems.append(f"{key}: expected={expected!r} actual={doc.get(key)!r}")
    for doc_key, pin_key in FREEZE_REGISTRATION_HASH_KEYS.items():
        declared = doc.get(doc_key)
        expected_hash = AF01_FROZEN_HASHES[pin_key]
        if declared != expected_hash:
            problems.append(f"{doc_key}: expected={expected_hash} actual={declared!r}")
    body = doc.get("canonical_body")
    if not isinstance(body, dict):
        problems.append("canonical_body が無い")
    else:
        if body.get("aggregate_sha256") != AF01_FROZEN_HASHES["af01_canonical_c4_sha256"]:
            problems.append(
                f"canonical_body.aggregate_sha256: "
                f"expected={AF01_FROZEN_HASHES['af01_canonical_c4_sha256']} "
                f"actual={body.get('aggregate_sha256')!r}"
            )
        if body.get("aggregate_file") != "AF01_all25_units_C4.wav":
            problems.append(f"canonical_body.aggregate_file: {body.get('aggregate_file')!r}")
        if body.get("unit_count") != AF01_ALIAS_COUNT:
            problems.append(f"canonical_body.unit_count: {body.get('unit_count')!r}")

    if problems:
        return ABSENT, f"{path}: 宣言内容が凍結値と一致しない: {problems}"
    return PRESENT, f"{path}: schema / 凍結ハッシュ / 構造量の宣言が凍結値と一致"


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
    # §7.1 は character.txt SHA256 を必須 pin に挙げており、本関数の未取得
    # メッセージも rights_manifest も同じ 3 点を required と書いている。
    # WAV と oto.ini だけで PRESENT にすると、不完全な voicebank のまま
    # R10-G2 が COMPLETE になり得る（PR #330 Codex 第 12 巡 P2）。
    present = bool(wavs) and bool(oto) and bool(character)
    return [
        InventoryItem(
            item_id="aquest_voicebank_files",
            state=PRESENT if present else ABSENT,
            detail=(
                f"raw WAV {len(wavs)} 件 / oto.ini {len(oto)} 件 / character.txt "
                f"{len(character)} 件"
                + ("" if present else "（§7.1 必須 3 点のいずれかが欠けている）")
            ),
            blocking=not present,
        ),
        InventoryItem(
            item_id="a0_recorded_pitch_inventory",
            state=UNRESOLVED,
            detail=(
                "収録ピッチ構造の判定には oto.ini マッピングと raw-unit F0 推定が要る"
                "（§9.4）。measurement 層未実装のため **inventory 未実施**。"
                "§9.4 が要求するのは inventory を実行して収録ピッチ数を確定すること"
                "であり、未実施は R10-G2 を塞ぐ。inventory を実施した結果が実質単一"
                "ピッチであった場合に限り cross-pitch persistence = NOT_EVALUABLE と"
                "なり、そこで初めて非 blocking になる。"
            ),
            blocking=True,
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
    af01_replay: bool = False,
) -> Dict[str, object]:
    """R10-G2 inventory 文書を組み立てる。"""
    root = repo if repo is not None else _repo_root()
    items = (
        inventory_af01(af01_bundle_root, run_replay=af01_replay)
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
        "--af01-replay",
        action="store_true",
        help="§29 手順 7 の決定論的 payload replay を実行する（--af01-bundle-root 必須）。",
    )
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

    if args.af01_replay and args.af01_bundle_root is None:
        parser.error("--af01-replay には --af01-bundle-root が必要")
    inventory = build_inventory(
        af01_bundle_root=Path(args.af01_bundle_root) if args.af01_bundle_root else None,
        aquest_voicebank_root=(
            Path(args.aquest_voicebank_root) if args.aquest_voicebank_root else None
        ),
        af01_replay=args.af01_replay,
    )
    payload = canonical_json_bytes(inventory)
    # 既定の出力先は追跡中の正典 `pre_run/inventory.json` である。in-place の
    # truncate だと中断・容量不足で前の有効な inventory を壊して部分成果物を
    # 残す（PR #330 Codex 第 4 巡 P2）。リポジトリの atomic write 集約実装
    # （CLAUDE.md「atomic write 集約」— src/svp_rpe/utils/atomic_io.py）へ委譲する。
    atomic_write_bytes(Path(args.out), payload)
    print(payload.decode("utf-8"))
    return 0 if inventory["gate_state"] == "COMPLETE" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
