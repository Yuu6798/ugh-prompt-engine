"""test_run4_provenance_closure.py — VG-DEBT-008 run4 provenance closure の
形状テスト（2026-08-23 実測）。

`results_s3/run4_provenance_closure_2026-08-23.json` は
`run4_anchor_provenance.json`（確定記録・無改変）の missing/not_established
10 件に対する実測結果を記録した人手編集ファイルである。
`test_genome_ledger_shape.py` / `test_debt_ledger_shape.py` と同じ流儀で、
**構造のみ**を機械強制し、sha256 等の値の実体照合はしない（判読=設計判断は
Fable の職務）。

検証する不変条件:
- schema 文字列が固定版
- acceptance 節が必須で空でない
- items が 10 件で、各 ref が run4_anchor_provenance.json の実在パスを指す
  （既存の確定記録は本テストでは 1 バイトも変更しない — 参照のみ）
- closure が {reproduced, measured_only, not_closable} のいずれか
- closure == reproduced のとき value が 64-hex の sha256 で非 null
- closure == not_closable のとき value は null
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import pytest

_FOUNDRY = Path(__file__).resolve().parent.parent
CLOSURE_PATH = _FOUNDRY / "results_s3" / "run4_provenance_closure_2026-08-23.json"
PROVENANCE_PATH = _FOUNDRY / "results_s3" / "run4_anchor_provenance.json"

VALID_CLOSURE = {"reproduced", "measured_only", "not_closable"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def closure() -> Dict[str, Any]:
    assert CLOSURE_PATH.exists(), f"not found: {CLOSURE_PATH}"
    with CLOSURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def provenance() -> Dict[str, Any]:
    assert PROVENANCE_PATH.exists(), f"not found: {PROVENANCE_PATH}"
    with PROVENANCE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_ref(root: Dict[str, Any], ref: str) -> Any:
    """`ref` (dot-joined path) を root から解決する。

    `run4_anchor_provenance.json` のキー自体にリテラルの `.` を含むもの
    （例: `run3_gate_sakura_ritsu.wav`）があるため、単純な `ref.split(".")`
    では誤分割する。各階層で「残りのパスの先頭から最長一致するキー」を
    貪欲に選ぶことで、キー内蔵ドットとパス区切りドットの両方を正しく扱う。
    """
    parts = ref.split(".")
    cur: Any = root
    i = 0
    while i < len(parts):
        assert isinstance(cur, dict), f"cannot descend into non-dict at {parts[:i]} resolving {ref!r}"
        matched = False
        # 残り部分を後ろから貪欲に結合して、cur の実キーと最長一致するものを探す
        for j in range(len(parts), i, -1):
            candidate = ".".join(parts[i:j])
            if candidate in cur:
                cur = cur[candidate]
                i = j
                matched = True
                break
        assert matched, f"no key matches remaining path {parts[i:]!r} resolving {ref!r}"
    return cur


def test_schema_is_pinned_version(closure: Dict[str, Any]) -> None:
    assert closure["schema"] == "voicegenesis-run4-provenance-closure/0.1"


def test_acceptance_section_present_and_nonempty(closure: Dict[str, Any]) -> None:
    assert isinstance(closure.get("acceptance"), str)
    assert closure["acceptance"].strip()


def test_items_is_exactly_ten(closure: Dict[str, Any]) -> None:
    items = closure["items"]
    assert isinstance(items, list)
    assert len(items) == 10, f"expected 10 items, got {len(items)}"


def test_every_item_has_required_keys(closure: Dict[str, Any]) -> None:
    required = {"ref", "prior_state", "closure", "value", "method", "source", "note"}
    for item in closure["items"]:
        missing = required - item.keys()
        assert not missing, f"item {item.get('ref', '<unknown>')} missing keys: {sorted(missing)}"


def test_item_refs_are_unique(closure: Dict[str, Any]) -> None:
    refs = [item["ref"] for item in closure["items"]]
    assert len(refs) == len(set(refs)), f"duplicate refs: {refs}"


def test_item_refs_resolve_against_existing_provenance_record(
    closure: Dict[str, Any], provenance: Dict[str, Any]
) -> None:
    """各 ref は run4_anchor_provenance.json の実在パスを指す（転記のみ・無改変の
    確定記録を本テストでは一切変更せず参照するだけであることを保証する）。"""
    for item in closure["items"]:
        ref = item["ref"]
        try:
            _resolve_ref(provenance, ref)
        except AssertionError as exc:  # pragma: no cover - failure path
            pytest.fail(f"ref {ref!r} does not resolve against run4_anchor_provenance.json: {exc}")


def test_item_prior_state_matches_existing_provenance_record(
    closure: Dict[str, Any], provenance: Dict[str, Any]
) -> None:
    """prior_state はここで初めて主張するのではなく、既存記録の実値
    （'missing' または 'not_established'）と一致していなければならない。"""
    for item in closure["items"]:
        actual = _resolve_ref(provenance, item["ref"])
        assert item["prior_state"] == actual, (
            f"ref {item['ref']!r}: prior_state {item['prior_state']!r} != "
            f"actual value in run4_anchor_provenance.json {actual!r}"
        )
        assert item["prior_state"] in {"missing", "not_established"}


def test_closure_uses_only_valid_vocabulary(closure: Dict[str, Any]) -> None:
    for item in closure["items"]:
        assert item["closure"] in VALID_CLOSURE, (
            f"item {item['ref']} has invalid closure {item['closure']!r}; "
            f"must be one of {sorted(VALID_CLOSURE)}"
        )


def test_reproduced_items_have_nonnull_sha256_value(closure: Dict[str, Any]) -> None:
    for item in closure["items"]:
        if item["closure"] == "reproduced":
            assert isinstance(item["value"], str) and SHA256_RE.match(item["value"]), (
                f"item {item['ref']} is closure=reproduced but value is not a 64-hex "
                f"sha256: {item['value']!r}"
            )


def test_not_closable_items_have_null_value(closure: Dict[str, Any]) -> None:
    for item in closure["items"]:
        if item["closure"] == "not_closable":
            assert item["value"] is None, (
                f"item {item['ref']} is closure=not_closable but value is not null: "
                f"{item['value']!r}"
            )


def test_wav_regeneration_section_shape(closure: Dict[str, Any]) -> None:
    wav = closure.get("wav_regeneration")
    assert isinstance(wav, dict)
    assert isinstance(wav.get("attempted"), bool)
    assert isinstance(wav.get("match_count"), int)
    assert isinstance(wav.get("total"), int)
    results = wav.get("results")
    assert isinstance(results, list)
    assert wav["total"] == len(results)
    matches = sum(1 for r in results if r.get("match") is True)
    assert matches == wav["match_count"]
    for r in results:
        assert isinstance(r.get("match"), bool)


def test_materials_and_environment_sections_present(closure: Dict[str, Any]) -> None:
    assert isinstance(closure.get("materials"), dict) and closure["materials"]
    assert isinstance(closure.get("environment"), dict) and closure["environment"]


def test_pyproject_lists_this_test_module() -> None:
    """PR #299 セルフレビュー教訓（このファイルの流儀）: 収集しないと
    『緑なのに何も検査していない』状態になる。"""
    pyproject = _FOUNDRY.parent.parent / "pyproject.toml"
    assert pyproject.exists()
    text = pyproject.read_text(encoding="utf-8")
    assert "voice_genesis/foundry/tests/test_run4_provenance_closure.py" in text
