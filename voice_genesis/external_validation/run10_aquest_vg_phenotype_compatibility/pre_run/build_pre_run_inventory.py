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
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
_RUN10_DIR = _THIS_DIR.parent
if str(_RUN10_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR))

from af01_freeze_verifier import (  # noqa: E402
    PINNED_LEDGER_PATH,
    check_ledger_structure,
    containment_violation,
    read_and_verify_ledger,
    verify_bundle,
    verify_deterministic_replay,
)
from build_a0_manifest import (  # noqa: E402
    SCHEMA as A0_MANIFEST_SCHEMA,
    _assert_voicebank_snapshot_unchanged,
    _file_order_sha256,
)
from svp_rpe.utils.atomic_io import atomic_write_bytes  # noqa: E402

from run10_schema import (  # noqa: E402
    AF01_ALIAS_COUNT,
    AF01_E0_CALIBRATION_CASES,
    AF01_FROZEN_HASHES,
    AF01_PITCHES,
    AF01_UNIT_FILE_COUNT,
    Run10ContractError,
    DESIGN_DOC_SHA256,
    DESIGN_DOC_TITLE,
    canonical_json_bytes,
    compute_file_sha256,
    load_run10_contract,
    verify_design_document,
)

PRESENT = "PRESENT"
ABSENT = "ABSENT"
UNRESOLVED = "UNRESOLVED"

# repository 内で見つかった近縁の Evolution Theory 参照（§29 手順 5）。
#
# **これは報告用の情報であり、解決の判定材料ではない。** 近縁文書の発見から
# 正典の存在を推論すると、`VISION_evolution_theory_v0.3.md` のような別文書が
# 追加された瞬間に「解決済み」になり、§29 手順 5 が要求する v0.3 本体を
# 欠いたまま R10-G2 が COMPLETE になる（PR #330 Codex 第 16 巡 P1）。
EVOLUTION_THEORY_DISCOVERY_CANDIDATES = (
    "voice_genesis/foundry/VISION_evolution_theory_v0.3.md",
    "voice_genesis/foundry/VISION_evolution_theory_v0.2.md",
    "voice_genesis/foundry/VISION_evolution_theory_v0.1.md",
    "voice_genesis/foundry/VISION_evolution_v0.3_supplementA_spr.md",
)

# §36 が実在を確認した v0.3 本体の題名。§29 手順 5 が要求するのはこの実体である。
EVOLUTION_THEORY_CANONICAL = "VoiceGenesis_Evolution_Theory_v0.3_ja.md"

# 正典本体は **private storage に置き、リポジトリへ commit しない**。
# 判定は実行時に `--evolution-theory-path` で渡された実体を照合して行う
# （DESIGN_RUN10 本体に対する `verify_design_document()` と同じパターン）。
# repo 内の固定パスを正典位置にすると、本体を commit しない限り永久に
# 解決不能になる（User 裁定 2026-08-27: 本文書はリポジトリに載せない）。

# 正典本体の凍結 sha256 の**唯一の出所**は `RUN10_CONTRACT.yaml` の
# `vg_evolution_theory_ref_sha` である。ここに独立した定数を置いてはならない —
# 二重管理の pin が乖離すると、R10-G0 は片方の digest を検証しながら R10-G2 は
# 別バイトを PRESENT と書く、矛盾した来歴のまま Run が進む
# （PR #330 Codex 第 18 巡 P1）。pin を得たら契約 YAML 側に 1 箇所だけ書く。
CONTRACT_PATH = _RUN10_DIR / "RUN10_CONTRACT.yaml"
EVOLUTION_THEORY_PIN_FIELD = "vg_evolution_theory_ref_sha"


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
    replay_output_root: Optional[Path] = None,
) -> List[InventoryItem]:
    """AF01 v1.0 関連項目（§29 手順 6 の第 1 段を含む）。"""
    items: List[InventoryItem] = []

    # 台帳は **1 回だけ読む**。hash した後で parse のために読み直すと、その間に
    # 差し替えられた台帳から `af01_ledger_structure` を導きながら
    # `af01_payload_sha256sums` は元バイトを「認証済み」と書く、内部矛盾した
    # 正典 inventory を作れる（PR #330 Codex 第 17 巡 P1）。
    # `read_and_verify_ledger()` は同じバッファを hash して parse する。
    ledger_report, ledger_entries = read_and_verify_ledger(PINNED_LEDGER_PATH)
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

    if ledger_report.passed and ledger_entries is not None:
        checks, problems = check_ledger_structure(ledger_entries)
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
        replay_kwargs = (
            {"replay_output_root": replay_output_root}
            if replay_output_root is not None
            else {}
        )
        replay_report = verify_deterministic_replay(bundle_root, **replay_kwargs)
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
    "replacement_policy": "new version and new freeze registration required",
}

# §7.3 の canonical pitch。canonical_body の全欄照合に使う。
AF01_CANONICAL_PITCH = "C4"

# 登録ファイルの top-level 許容欄（閉世界）。未知欄で宣言を骨抜きにさせない。
FREEZE_REGISTRATION_ALLOWED_KEYS: Tuple[str, ...] = tuple(FREEZE_REGISTRATION_EXPECTED) + (
    "payload_ledger_sha256",
    "af01_spec_sha256",
    "generator_sha256",
    "manifest_sha256",
    "canonical_body",
)


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
    unknown = set(doc) - set(FREEZE_REGISTRATION_ALLOWED_KEYS)
    if unknown:
        problems.append(f"未知の欄: {sorted(unknown)}")
    missing = set(FREEZE_REGISTRATION_ALLOWED_KEYS) - set(doc)
    if missing:
        problems.append(f"欄の欠落: {sorted(missing)}")
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
        # canonical_body も**全欄**を固定する。aggregate hash だけ一致していれば
        # 通る状態だと、`pitch: G4` や `unit_directory: C3/` を宣言した自己矛盾
        # 登録が R10-G2 を通過する（PR #330 Codex 第 14 巡 P1）。
        expected_body = {
            "pitch": AF01_CANONICAL_PITCH,
            "aggregate_file": "AF01_all25_units_C4.wav",
            "aggregate_sha256": AF01_FROZEN_HASHES["af01_canonical_c4_sha256"],
            "unit_directory": f"{AF01_CANONICAL_PITCH}/",
            "unit_count": AF01_ALIAS_COUNT,
        }
        unknown_body = set(body) - set(expected_body)
        if unknown_body:
            problems.append(f"canonical_body に未知の欄: {sorted(unknown_body)}")
        for key, expected in expected_body.items():
            if body.get(key) != expected:
                problems.append(
                    f"canonical_body.{key}: expected={expected!r} actual={body.get(key)!r}"
                )

    if problems:
        return ABSENT, f"{path}: 宣言内容が凍結値と一致しない: {problems}"
    return PRESENT, f"{path}: schema / 凍結ハッシュ / 構造量の宣言が凍結値と一致"


def _a0_contract_pins(
    contract_path: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, str]], str]:
    path = Path(contract_path) if contract_path is not None else CONTRACT_PATH
    fields = (
        "aquest_voicebank_manifest_sha",
        "aquest_raw_file_order_sha",
        "oto_ini_sha",
    )
    try:
        contract = load_run10_contract(path)
    except (OSError, Run10ContractError) as exc:
        return None, f"A0 pin を contract から解決できない: {exc}"
    values: Dict[str, str] = {}
    for field in fields:
        pin = contract.pins.get(field)
        if pin is None or not pin.pinned or not isinstance(pin.value, str):
            return None, f"RUN10_CONTRACT.yaml の {field} が PINNED でない"
        values[field] = pin.value
    return values, ""


def _verify_a0_manifest_and_root(
    root: Path,
    manifest_path: Optional[Path],
    contract_path: Optional[Path],
) -> Tuple[bool, str, int]:
    if manifest_path is None or not Path(manifest_path).is_file():
        return False, "private A0 manifest が未指定または不在", 0
    pins, why = _a0_contract_pins(contract_path)
    if pins is None:
        return False, why, 0

    try:
        manifest_bytes = Path(manifest_path).read_bytes()
        actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_sha != pins["aquest_voicebank_manifest_sha"]:
            return False, "A0 manifest sha256 が contract pin と一致しない", 0
        document = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(document, dict) or document.get("schema") != A0_MANIFEST_SCHEMA:
            return False, "A0 manifest schema が不正", 0
        entries: Any = document.get("files")
        if not isinstance(entries, list) or not entries:
            return False, "A0 manifest files が空または不正", 0
        paths: List[str] = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                return False, "A0 manifest file entry が不正", 0
            path = entry.get("path")
            digest = entry.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                return False, "A0 manifest path/hash が不正", 0
            if path in seen:
                return False, "A0 manifest に重複 path がある", 0
            seen.add(path)
            paths.append(path)
        if _file_order_sha256(paths) != pins["aquest_raw_file_order_sha"]:
            return False, "A0 file-order sha256 が contract pin と一致しない", 0
        oto_entries = [entry for entry in entries if entry["path"] == "oto.ini"]
        if len(oto_entries) != 1 or oto_entries[0]["sha256"] != pins["oto_ini_sha"]:
            return False, "A0 oto.ini sha256 が contract pin と一致しない", 0
        _assert_voicebank_snapshot_unchanged(root, entries)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return False, f"A0 manifest/root 照合に失敗: {exc}", 0

    wav_count = sum(entry.get("kind") == "wav" for entry in entries)
    return True, "A0 manifest と展開 root の全 path/hash および3 contract pinが一致", wav_count


def inventory_aquest(
    voicebank_root: Optional[Path],
    manifest_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
) -> List[InventoryItem]:
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

    root = Path(voicebank_root).resolve()
    present, verification_detail, wav_count = _verify_a0_manifest_and_root(
        root, manifest_path, contract_path
    )
    return [
        InventoryItem(
            item_id="aquest_voicebank_files",
            state=PRESENT if present else ABSENT,
            detail=(
                f"raw WAV {wav_count} 件。{verification_detail}"
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


def _check_design_document(body_path: Optional[Path] = None) -> InventoryItem:
    """§29 手順 1/2: 実行される設計文書の実バイトが凍結 pin と一致すること。

    設計文書はリポジトリに置かない（§2.2）ため、契約 YAML に書かれた
    `design_doc_sha256` は「宣言」でしかない。宣言と同じ digest を定数と
    突き合わせても、**その digest の文書が実在すること**は何も証明しない
    （PR #330 Codex 第 22 巡 P1: `verify_design_document()` がテストからしか
    呼ばれておらず、どの検収経路にも繋がっていなかった）。

    R10-G0 は §21 の定義どおり「Run Contract の pin 充足」のままにし、
    **実体照合はここ（R10-G2 の実在確認）で blocking 項目として要求する**。
    どちらの Gate も開かない限り測定は始まらないので、「どの設計を実行したか」
    が証明されないまま Run が進む経路は残らない。

    渡されたパス文字列は記録しない（§2.2 / §26 — inventory は commit される）。
    """
    if body_path is None:
        return InventoryItem(
            item_id="design_document_bytes",
            state=UNRESOLVED,
            detail=(
                f"正本設計 {DESIGN_DOC_TITLE!r} の実体が未照合"
                "（--design-doc-path 未指定）。本文書はリポジトリに載せないため"
                "（§2.2）、実行される文書が凍結 pin と同一であることは実行時に"
                "しか確かめられない。契約 YAML の宣言だけでは実在を証明しない。"
            ),
            blocking=True,
        )

    body = Path(body_path)
    if not body.is_file():
        return InventoryItem(
            item_id="design_document_bytes",
            state=ABSENT,
            detail="設計文書の照合対象として指定されたパスにファイルが無い。",
            blocking=True,
        )

    if verify_design_document(body):
        return InventoryItem(
            item_id="design_document_bytes",
            state=PRESENT,
            detail=(
                f"正本設計 {DESIGN_DOC_TITLE!r} の実バイト sha256 が凍結 pin"
                f" {DESIGN_DOC_SHA256} と一致（§29 手順 1/2）。"
                "本文書は private storage 側にあり commit しない。"
            ),
            blocking=False,
        )

    return InventoryItem(
        item_id="design_document_bytes",
        state=ABSENT,
        detail=(
            "設計文書の実バイト sha256 が凍結 pin と一致しない"
            f"（expected={DESIGN_DOC_SHA256} actual={compute_file_sha256(body)}）。"
            "どの設計を実行しているのかが確定しないため測定を始めない（§32 Stop Rule）。"
        ),
        blocking=True,
    )


def _evolution_theory_pin(contract_path: Optional[Path] = None) -> Tuple[Optional[str], str]:
    """契約から Evolution Theory 正典の凍結 sha256 を解決する。

    戻り値は `(digest, why)`。`digest` が None のときだけ `why` が理由を述べる。
    契約が読めない・壊れている場合も None（fail-closed）— 来歴 pin を解決
    できないこと自体が R10-G2 を塞ぐ事由である。
    """
    path = Path(contract_path) if contract_path is not None else CONTRACT_PATH
    try:
        contract = load_run10_contract(path)
    except (OSError, Run10ContractError) as exc:
        return None, f"の pin を契約から解決できない（{path.name}: {exc}）。"
    pin = contract.pins.get(EVOLUTION_THEORY_PIN_FIELD)
    if pin is None:
        return None, f"の pin 欄 {EVOLUTION_THEORY_PIN_FIELD} が契約に無い。"
    if pin.status != "PINNED" or not isinstance(pin.value, str):
        return None, (
            f"の凍結 sha256 が未取得（RUN10_CONTRACT.yaml"
            f" {EVOLUTION_THEORY_PIN_FIELD} = {pin.status}）。pin が無い限り"
            "「正しい実体が在る」ことは証明できないため解決にしない。"
        )
    return pin.value, ""


def _check_evolution_theory(
    repo: Path,
    body_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
) -> InventoryItem:
    """§29 手順 5: Evolution Theory v0.3 本体の解決。

    R10-G2 が要求するのは「Evolution Theory v0.3 **location**」であり、
    参照の同定である。同定が成立するのは契約 `vg_evolution_theory_ref_sha` が
    PINNED のときで、この pin が digest の唯一の出所である。近縁文書の発見
    リストは報告用であって判定材料ではない — 第 16 巡以前は
    `VISION_evolution_theory_v0.3.md` の実在で PRESENT にしていたため、
    別名の別文書が追加された瞬間に「解決済み」になり、しかも同じ detail が
    「v0.3 本体は不在」と言い続ける自己矛盾を出していた。

    本体は private storage 側にあり、リポジトリには載せない（User 裁定
    2026-08-27）。したがって実体バイトの照合は**任意**であり、
    `--evolution-theory-path` が渡されたときにだけ行う。渡されたのに
    一致しない場合は同名の別内容 = 来歴汚染なので UNRESOLVED へ落とす。
    実体照合を必須にすると、本体を commit しない限り永久に解決不能になる。

    **渡されたパス文字列は detail に書かない** — inventory.json は commit
    されるため、private ストレージの構成を公開することになる（§2.2 / §26）。
    """
    discovered = [c for c in EVOLUTION_THEORY_DISCOVERY_CANDIDATES if (repo / c).is_file()]
    found_note = f"リポジトリ内に存在する近縁参照（判定材料ではない）: {discovered}. "
    canonical_note = f"§36 が実在を確認した v0.3 本体 {EVOLUTION_THEORY_CANONICAL!r} "

    def unresolved(why: str) -> InventoryItem:
        return InventoryItem(
            item_id="evolution_theory_reference",
            state=UNRESOLVED,
            detail=found_note + canonical_note + why,
            blocking=True,
        )

    expected, why = _evolution_theory_pin(contract_path)
    if expected is None:
        return unresolved(why)

    if body_path is None:
        return InventoryItem(
            item_id="evolution_theory_reference",
            state=PRESENT,
            detail=(
                found_note + canonical_note
                + "は契約 pin により同定済み（§29 手順 5 解決）。本体は private"
                " storage 側にあり commit しない。実体バイトの照合が要るときは"
                " --evolution-theory-path を渡す。"
            ),
            blocking=False,
        )

    body = Path(body_path)
    if not body.is_file():
        return unresolved("の照合対象として指定されたパスにファイルが無い。")

    if body.name != EVOLUTION_THEORY_CANONICAL:
        return unresolved(
            f"の照合対象として題名の異なるファイルが指定された（{body.name!r}）。"
        )

    actual = compute_file_sha256(body)
    if actual != expected:
        return unresolved(
            "の実バイト sha256 が契約の pin と一致しない"
            f"（expected={expected} actual={actual}）。"
            "同名の別内容は来歴汚染である。"
        )

    return InventoryItem(
        item_id="evolution_theory_reference",
        state=PRESENT,
        detail=(
            found_note + canonical_note
            + "の実バイト sha256 が契約の pin と一致（§29 手順 5 解決・実体照合済み）。"
            "本体は private storage 側にあり commit しない。"
        ),
        blocking=False,
    )


def inventory_repository(
    repo: Path,
    evolution_theory_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
    design_doc_path: Optional[Path] = None,
) -> List[InventoryItem]:
    """リポジトリ側の参照解決（§29 手順 1/2/5）。"""
    items = [
        _check_design_document(design_doc_path),
        _check_evolution_theory(repo, evolution_theory_path, contract_path),
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
    aquest_voicebank_manifest_path: Optional[Path] = None,
    repo: Optional[Path] = None,
    af01_replay: bool = False,
    af01_replay_output_root: Optional[Path] = None,
    evolution_theory_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
    design_doc_path: Optional[Path] = None,
) -> Dict[str, object]:
    """R10-G2 inventory 文書を組み立てる。"""
    root = repo if repo is not None else _repo_root()
    items = (
        inventory_af01(
            af01_bundle_root,
            run_replay=af01_replay,
            replay_output_root=af01_replay_output_root,
        )
        + inventory_aquest(
            aquest_voicebank_root, aquest_voicebank_manifest_path, contract_path
        )
        + inventory_repository(root, evolution_theory_path, contract_path, design_doc_path)
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
        "--af01-replay-output-root",
        default=None,
        help=(
            "凍結 generator が cwd 外へ書く場合の再生成 payload root（絶対パス）。"
            "--af01-replay と同時に指定する。"
        ),
    )
    parser.add_argument(
        "--aquest-voicebank-root",
        default=None,
        help="A0 = AquesTalk 由来 UTAU デフォルト音声のルート（private ストレージ）。",
    )
    parser.add_argument(
        "--aquest-voicebank-manifest",
        default=None,
        help=(
            "build_a0_manifest.py が private staging に生成した A0 manifest。"
            "contract pin と展開 root の全 path/hash を照合する。"
        ),
    )
    parser.add_argument(
        "--design-doc-path",
        default=None,
        help=(
            "正本設計文書（private ストレージ）の実体パス。実バイト sha256 を"
            "凍結 pin と照合する。指定したパス文字列は inventory.json に記録しない。"
        ),
    )
    parser.add_argument(
        "--evolution-theory-path",
        default=None,
        help=(
            "Evolution Theory v0.3 本体（private ストレージ）の実体パス。"
            "本文書はリポジトリに載せないため、照合は実行時にのみ成立する。"
            "指定したパス文字列は inventory.json に記録しない（§2.2 / §26）。"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(_THIS_DIR / "inventory.json"),
        help="inventory.json の書き出し先。",
    )
    args = parser.parse_args(argv)

    if args.af01_replay and args.af01_bundle_root is None:
        parser.error("--af01-replay には --af01-bundle-root が必要")
    if args.af01_replay_output_root is not None and not args.af01_replay:
        parser.error("--af01-replay-output-root は --af01-replay と同時に指定する")
    inventory = build_inventory(
        af01_bundle_root=Path(args.af01_bundle_root) if args.af01_bundle_root else None,
        aquest_voicebank_root=(
            Path(args.aquest_voicebank_root) if args.aquest_voicebank_root else None
        ),
        aquest_voicebank_manifest_path=(
            Path(args.aquest_voicebank_manifest)
            if args.aquest_voicebank_manifest
            else None
        ),
        af01_replay=args.af01_replay,
        af01_replay_output_root=(
            Path(args.af01_replay_output_root) if args.af01_replay_output_root else None
        ),
        evolution_theory_path=(
            Path(args.evolution_theory_path) if args.evolution_theory_path else None
        ),
        design_doc_path=(
            Path(args.design_doc_path) if args.design_doc_path else None
        ),
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
