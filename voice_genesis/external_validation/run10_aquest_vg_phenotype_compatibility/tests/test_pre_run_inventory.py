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

import hashlib
import json
import sys
from pathlib import Path

import pytest

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
    (root / "character.txt").write_text("name=x", encoding="utf-8")
    items = _items(inv.build_inventory(aquest_voicebank_root=root))
    assert items["aquest_voicebank_files"]["state"] == inv.PRESENT
    assert "raw WAV 3 件" in items["aquest_voicebank_files"]["detail"]


def test_unperformed_pitch_inventory_keeps_blocking(tmp_path: Path) -> None:
    """§28-17 / §9.4: 未実施の収録ピッチ inventory は R10-G2 を塞ぎ続ける。

    §9.4 が要求するのは inventory を**実行して**収録ピッチ数を確定することで
    あり、「未実施だから cross-pitch を要求しない」は成立しない。未実施を
    非 blocking にすると、他項目が解決した時点で gate_state が COMPLETE へ
    落ちてしまう（PR #330 Codex 第 1 巡 P1）。

    単一ピッチが**確定した**場合に cross-pitch persistence = NOT_EVALUABLE と
    なる、という routing 自体は detail に記録される。
    """
    root = tmp_path / "voicebank"
    root.mkdir()
    (root / "a.wav").write_bytes(b"RIFF")
    (root / "oto.ini").write_text("", encoding="utf-8")
    items = _items(inv.build_inventory(aquest_voicebank_root=root))
    item = items["a0_recorded_pitch_inventory"]
    assert item["state"] == inv.UNRESOLVED
    assert item["blocking"] is True
    assert "NOT_EVALUABLE" in item["detail"]


def test_gate_cannot_complete_while_any_required_item_is_unresolved(tmp_path: Path) -> None:
    """未解決の required item が 1 つでも残る限り R10-G2 は COMPLETE にならない。"""
    root = tmp_path / "voicebank"
    root.mkdir()
    (root / "a.wav").write_bytes(b"RIFF")
    (root / "oto.ini").write_text("", encoding="utf-8")
    document = inv.build_inventory(aquest_voicebank_root=root)
    assert document["gate_state"] == "BLOCKED"
    assert "a0_recorded_pitch_inventory" in document["blocking_items"]


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


# --- FREEZE_REGISTRATION.json の形状検証（PR #330 Codex 第 1 巡 P2） -------


def _valid_registration() -> dict:
    return {
        "schema": "voicegenesis-freeze-registration/1.0",
        "voice_id": "AF01",
        "specimen_version": "1.0",
        "freeze_status": "FROZEN",
        "payload_ledger_sha256": m.AF01_FROZEN_HASHES["af01_payload_ledger_sha256"],
        "af01_spec_sha256": m.AF01_FROZEN_HASHES["af01_spec_sha256"],
        "generator_sha256": m.AF01_FROZEN_HASHES["af01_generator_sha256"],
        "manifest_sha256": m.AF01_FROZEN_HASHES["af01_manifest_sha256"],
        "canonical_body": {
            "pitch": "C4",
            "aggregate_file": "AF01_all25_units_C4.wav",
            "aggregate_sha256": m.AF01_FROZEN_HASHES["af01_canonical_c4_sha256"],
            "unit_directory": "C4/",
            "unit_count": 25,
        },
        "pitch_fixture_count": 3,
        "unit_file_count": 75,
        "e0_calibration_cases": 9,
        "mutation_policy": "PROHIBITED_WITHIN_RUN10",
        "replacement_policy": "new version and new freeze registration required",
    }


def test_freeze_registration_shape_is_validated(tmp_path: Path) -> None:
    """存在するだけでは required item を満たさない（parse 可能 ≠ 形状正しい）。

    本 fixture は Drive 上の実 FREEZE_REGISTRATION.json の宣言内容をそのまま
    写したものであり、凍結値と一致することを検査する。
    """
    path = tmp_path / "FREEZE_REGISTRATION.json"
    path.write_text(json.dumps(_valid_registration()), encoding="utf-8")
    state, detail = inv._check_freeze_registration(path)
    assert state == inv.PRESENT, detail


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(freeze_status="DRAFT"),
        lambda d: d.update(unit_file_count=74),
        lambda d: d.update(e0_calibration_cases=8),
        lambda d: d.update(generator_sha256="0" * 64),
        lambda d: d["canonical_body"].update(aggregate_sha256="0" * 64),
        lambda d: d["canonical_body"].update(unit_count=24),
        lambda d: d.update(mutation_policy="ALLOWED"),
        lambda d: d.pop("canonical_body"),
    ],
)
def test_stale_or_contradictory_registration_is_rejected(tmp_path: Path, mutate) -> None:
    """空・stale・矛盾した登録が R10-G2 を通らない。"""
    doc = _valid_registration()
    mutate(doc)
    path = tmp_path / "FREEZE_REGISTRATION.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    state, detail = inv._check_freeze_registration(path)
    assert state == inv.ABSENT, detail


def test_unparsable_registration_is_rejected(tmp_path: Path) -> None:
    """空ファイル・壊れた JSON も PRESENT にしない。"""
    path = tmp_path / "FREEZE_REGISTRATION.json"
    path.write_text("", encoding="utf-8")
    assert inv._check_freeze_registration(path)[0] == inv.ABSENT
    path.write_text("[]", encoding="utf-8")
    assert inv._check_freeze_registration(path)[0] == inv.ABSENT


# --- 第 9 巡: 決定論的 replay は独立の blocking 項目 -----------------------


def test_replay_is_a_separate_blocking_item_without_bundle() -> None:
    """§29 手順 7 は手順 6 と独立の必須項目として残る。"""
    items = _items(inv.build_inventory())
    item = items["af01_deterministic_replay"]
    assert item["state"] == inv.UNRESOLVED
    assert item["blocking"] is True


def test_bundle_presence_alone_does_not_clear_replay(tmp_path: Path, monkeypatch) -> None:
    """bundle 実体があるだけで手順 7 を済ませたことにしない。

    ここを非 blocking にすると、凍結 generator が payload を再生成できない
    参照の上で R10-G2 が COMPLETE になり得た（PR #330 Codex 第 9 巡 P1）。
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(
        inv,
        "verify_bundle",
        lambda root, ledger_path=None: _fake_report("PASS", {"payload_files_sha256": "PASS"}),
    )
    items = _items(inv.build_inventory(af01_bundle_root=bundle))
    assert items["af01_deterministic_replay"]["state"] == inv.UNRESOLVED
    assert items["af01_deterministic_replay"]["blocking"] is True
    assert "--af01-replay" in items["af01_deterministic_replay"]["detail"]


def _fake_report(verdict: str, checks: dict):
    import af01_freeze_verifier as v

    return v.Af01VerificationReport(verdict=verdict, checks=checks)


def test_replay_failure_blocks_the_gate(tmp_path: Path, monkeypatch) -> None:
    """replay が DRIFT なら R10-G2 を塞ぐ。"""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(
        inv,
        "verify_bundle",
        lambda root, ledger_path=None: _fake_report("PASS", {}),
    )
    monkeypatch.setattr(
        inv,
        "verify_deterministic_replay",
        lambda root: _fake_report("AF01_INPUT_DRIFT", {"deterministic_payload_replay": "FAIL"}),
    )
    items = _items(inv.build_inventory(af01_bundle_root=bundle, af01_replay=True))
    assert items["af01_deterministic_replay"]["state"] == inv.ABSENT
    assert items["af01_deterministic_replay"]["blocking"] is True


def test_replay_pass_clears_the_item(tmp_path: Path, monkeypatch) -> None:
    """replay が PASS なら当該項目は解消する（常時 blocking の張りぼてでない）。"""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(
        inv,
        "verify_bundle",
        lambda root, ledger_path=None: _fake_report("PASS", {}),
    )
    monkeypatch.setattr(
        inv,
        "verify_deterministic_replay",
        lambda root: _fake_report("PASS", {"deterministic_payload_replay": "PASS"}),
    )
    items = _items(inv.build_inventory(af01_bundle_root=bundle, af01_replay=True))
    assert items["af01_deterministic_replay"]["state"] == inv.PRESENT
    assert items["af01_deterministic_replay"]["blocking"] is False


# --- 第 12 巡: A0 presence は §7.1 の必須 3 点を要求する -----------------


@pytest.mark.parametrize("missing", ["oto.ini", "character.txt"])
def test_incomplete_voicebank_is_not_present(tmp_path: Path, missing: str) -> None:
    """§7.1: WAV と oto.ini だけで PRESENT にしない（character.txt も必須 pin）。

    不完全な voicebank のまま R10-G2 が COMPLETE になり得た
    （PR #330 Codex 第 12 巡 P2）。
    """
    root = tmp_path / "voicebank"
    root.mkdir()
    (root / "a.wav").write_bytes(b"RIFF")
    for name in ("oto.ini", "character.txt"):
        if name != missing:
            (root / name).write_text("x", encoding="utf-8")
    items = _items(inv.build_inventory(aquest_voicebank_root=root))
    item = items["aquest_voicebank_files"]
    assert item["state"] == inv.ABSENT
    assert item["blocking"] is True
    assert "必須 3 点" in item["detail"]


# --- 第 14 巡: canonical_body の全欄照合 ---------------------------------


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda d: d["canonical_body"].update(pitch="G4"), "pitch"),
        (lambda d: d["canonical_body"].update(unit_directory="C3/"), "unit_directory"),
        (lambda d: d["canonical_body"].update(extra=1), "未知の欄"),
        (lambda d: d["canonical_body"].pop("pitch"), "pitch"),
        (lambda d: d.update(replacement_policy="overwrite in place"), "replacement_policy"),
        (lambda d: d.update(surprise=True), "未知の欄"),
        (lambda d: d.pop("manifest_sha256"), "欠落"),
    ],
)
def test_self_contradictory_registration_is_rejected(tmp_path: Path, mutate, why: str) -> None:
    """aggregate hash だけ合っていれば通る、という状態を塞ぐ。

    `pitch: G4` と C4 の aggregate hash を同時に宣言した自己矛盾登録が
    R10-G2 を通過していた（PR #330 Codex 第 14 巡 P1）。
    """
    doc = _valid_registration()
    mutate(doc)
    path = tmp_path / "FREEZE_REGISTRATION.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    state, detail = inv._check_freeze_registration(path)
    assert state == inv.ABSENT
    assert why in detail


# --- 第 16 巡: Evolution Theory 正典の実体照合（PR #330 Codex 第 16 巡 P1）--


def _foundry(repo: Path) -> Path:
    d = repo / "voice_genesis" / "foundry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_near_miss_document_does_not_resolve_the_reference(tmp_path: Path) -> None:
    """別名の近縁文書が追加されても §29 手順 5 は解決しない。

    第 16 巡以前は `VISION_evolution_theory_v0.3.md` の実在で PRESENT に
    していたため、正典 `VoiceGenesis_Evolution_Theory_v0.3_ja.md` を欠いた
    まま R10-G2 が COMPLETE になり得た（来歴汚染）。
    """
    near_miss = _foundry(tmp_path) / "VISION_evolution_theory_v0.3.md"
    near_miss.write_text("x", encoding="utf-8")
    # 近縁文書を照合対象に渡しても、題名が正典と違う時点で解決しない。
    item = inv._check_evolution_theory(tmp_path, near_miss)
    assert item.state == inv.UNRESOLVED
    assert item.blocking is True


def test_detail_never_contradicts_the_state(tmp_path: Path) -> None:
    """PRESENT なのに detail が「不在」と言う自己矛盾を出さない。"""
    (_foundry(tmp_path) / "VISION_evolution_theory_v0.3.md").write_text("x", encoding="utf-8")
    item = inv._check_evolution_theory(tmp_path)
    if item.state == inv.PRESENT:
        assert "不在" not in item.detail
    else:
        assert "判定材料ではない" in item.detail


def _pin(monkeypatch, digest: str) -> None:
    """契約 pin の解決結果だけを差し替える（pin の出所は契約 1 箇所）。"""
    monkeypatch.setattr(inv, "_evolution_theory_pin", lambda contract_path=None: (digest, ""))


def test_canonical_name_alone_is_not_enough(tmp_path: Path, monkeypatch) -> None:
    """名前一致だけでは通さない — 同名の別内容は来歴汚染である。"""
    _pin(monkeypatch, "a" * 64)
    body = tmp_path / "private" / inv.EVOLUTION_THEORY_CANONICAL
    body.parent.mkdir(parents=True)
    body.write_text("wrong", encoding="utf-8")
    item = inv._check_evolution_theory(tmp_path, body)
    assert item.state == inv.UNRESOLVED
    assert item.blocking is True
    assert "一致しない" in item.detail


def test_wrong_title_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """別題名のファイルを照合対象に指定しても解決しない。"""
    _pin(monkeypatch, "a" * 64)
    body = tmp_path / "VISION_evolution_theory_v0.3.md"
    body.write_text("x", encoding="utf-8")
    item = inv._check_evolution_theory(tmp_path, body)
    assert item.state == inv.UNRESOLVED
    assert item.blocking is True


def test_private_body_with_matching_pin_resolves(tmp_path: Path, monkeypatch) -> None:
    """private storage 側の実体 + sha 一致でのみ解決する（偽陰性でないことの確認）。"""
    body_bytes = b"canonical evolution theory v0.3"
    _pin(monkeypatch, hashlib.sha256(body_bytes).hexdigest())
    body = tmp_path / "private" / inv.EVOLUTION_THEORY_CANONICAL
    body.parent.mkdir(parents=True)
    body.write_bytes(body_bytes)
    item = inv._check_evolution_theory(tmp_path, body)
    assert item.state == inv.PRESENT
    assert item.blocking is False


def test_private_path_is_never_recorded(tmp_path: Path, monkeypatch) -> None:
    """照合対象のパス文字列を inventory へ書かない（§2.2 / §26）。

    inventory.json は commit されるため、private ストレージの構成を
    そのまま公開することになる。
    """
    body_bytes = b"canonical evolution theory v0.3"
    _pin(monkeypatch, hashlib.sha256(body_bytes).hexdigest())
    secret = tmp_path / "very" / "private" / "place"
    secret.mkdir(parents=True)
    body = secret / inv.EVOLUTION_THEORY_CANONICAL
    body.write_bytes(body_bytes)
    for path in (body, secret / "missing.md", tmp_path / "nope.md"):
        item = inv._check_evolution_theory(tmp_path, path)
        assert "very" not in item.detail
        assert str(secret) not in item.detail


def test_pin_alone_resolves_the_reference(tmp_path: Path, monkeypatch) -> None:
    """R10-G2 が要求するのは参照の同定であり、契約 pin で成立する。

    実体照合を必須にすると、本体を commit しない限り永久に解決不能になる
    （User 裁定 2026-08-27: 本文書はリポジトリに載せない）。実体照合は
    `--evolution-theory-path` を渡したときの追加検査である。
    """
    _pin(monkeypatch, "a" * 64)
    item = inv._check_evolution_theory(tmp_path, None)
    assert item.state == inv.PRESENT
    assert item.blocking is False
    assert "--evolution-theory-path" in item.detail


def test_missing_pin_blocks_even_with_a_body(tmp_path: Path, monkeypatch) -> None:
    """pin が無ければ、実体を渡しても解決にしない（照合先が無いため）。"""
    monkeypatch.setattr(
        inv, "_evolution_theory_pin", lambda contract_path=None: (None, "の pin が無い。")
    )
    body = tmp_path / inv.EVOLUTION_THEORY_CANONICAL
    body.write_text("x", encoding="utf-8")
    item = inv._check_evolution_theory(tmp_path, body)
    assert item.state == inv.UNRESOLVED
    assert item.blocking is True


def test_committed_contract_pins_the_reference() -> None:
    """§29 手順 5 は完了している（User 照合 2026-08-27）。"""
    digest, why = inv._evolution_theory_pin()
    assert why == ""
    assert digest == "87f4208ffdd213099977c4b5a1ee5d06852524036c818e4b14ce6b0e355b2e93"


# --- 第 18 巡: pin の出所を契約 1 箇所に閉じる（PR #330 Codex 第 18 巡 P1）--


def test_module_has_no_independent_digest_constant() -> None:
    """独立した digest 定数を持たない（二重管理の pin は乖離する）。

    R10-G0 が検証する契約 pin と inventory が照合する digest が別物だと、
    片方の digest を検証しながら別バイトを PRESENT と書ける。
    """
    assert not hasattr(inv, "EVOLUTION_THEORY_CANONICAL_SHA256")
    assert inv.EVOLUTION_THEORY_PIN_FIELD == "vg_evolution_theory_ref_sha"


def test_pin_comes_from_the_contract(tmp_path: Path) -> None:
    """契約の PINNED 値がそのまま照合値になる。"""
    import yaml

    digest = "b" * 64
    doc = yaml.safe_load(inv.CONTRACT_PATH.read_text(encoding="utf-8"))
    doc[inv.EVOLUTION_THEORY_PIN_FIELD] = {"value": digest, "status": "PINNED"}
    path = tmp_path / "RUN10_CONTRACT.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    assert inv._evolution_theory_pin(path) == (digest, "")


def test_unreadable_contract_fails_closed(tmp_path: Path) -> None:
    """契約が読めない・壊れているときは解決しない（fail-closed）。"""
    missing = tmp_path / "absent.yaml"
    digest, why = inv._evolution_theory_pin(missing)
    assert digest is None
    assert why

    broken = tmp_path / "RUN10_CONTRACT.yaml"
    broken.write_text("schema: nonsense\n", encoding="utf-8")
    digest, why = inv._evolution_theory_pin(broken)
    assert digest is None
    assert why


def test_canonical_body_is_not_a_repository_path() -> None:
    """正典本体をリポジトリ内の固定パスで解決しない（User 裁定 2026-08-27）。"""
    assert not hasattr(inv, "EVOLUTION_THEORY_CANONICAL_PATH")
    assert inv.EVOLUTION_THEORY_CANONICAL not in inv.EVOLUTION_THEORY_DISCOVERY_CANDIDATES
