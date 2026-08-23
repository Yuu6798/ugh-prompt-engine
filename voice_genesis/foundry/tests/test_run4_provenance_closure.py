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
- closure == measured_only のとき value が 64-hex の sha256 で非 null
- acceptance が宣言する run4_anchor_provenance.json の sha256 が実ファイルと一致
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
VALID_VALUE_KIND = {"file_sha256", "consumed_member_manifest_sha256"}
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
    required = {"ref", "prior_state", "closure", "value", "value_kind", "method", "source", "note"}
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


def test_measured_only_items_have_sha256_value(closure: Dict[str, Any]) -> None:
    """`measured_only` は「実測はしたが当時バイトとの同一性は未確立」の意であり、
    実測値そのものは必ず持つ。value が null のまま measured_only を名乗ると
    台帳の「4 件は実測値つきで記録」という記帳と矛盾する
    （PR #307 セルフレビュー指摘 5）。"""
    for item in closure["items"]:
        if item["closure"] == "measured_only":
            assert isinstance(item["value"], str) and SHA256_RE.match(item["value"]), (
                f"item {item['ref']} is closure=measured_only but value is not a "
                f"64-hex sha256: {item['value']!r}"
            )


def test_value_kind_matches_closure(closure: Dict[str, Any]) -> None:
    """`value` が何のハッシュなのかを item 自身が自己記述する（PR #307 Codex
    第 8 巡 P2）。canon のように「配布 zip 全体」と「合成が実際に消費する
    メンバー群のマニフェスト」が別物になる ref があるため、値だけを渡すと
    下流はランタイム入力を同定できない。not_closable は value が null なので
    value_kind も null。"""
    for item in closure["items"]:
        if item["closure"] == "not_closable":
            assert item["value_kind"] is None, (
                f"item {item['ref']} is not_closable but value_kind is not null: "
                f"{item['value_kind']!r}"
            )
        else:
            assert item["value_kind"] in VALID_VALUE_KIND, (
                f"item {item['ref']} has invalid value_kind {item['value_kind']!r}; "
                f"must be one of {sorted(VALID_VALUE_KIND)}"
            )


def test_canon_manifest_value_is_recomputable_from_recorded_members(
    closure: Dict[str, Any],
) -> None:
    """canon item の value は materials に記録された消費メンバーの sha256 から
    宣言された算法で再計算できなければならない。宣言だけして再計算不能だと、
    マニフェストハッシュが「由来不明の 64-hex」に退化する（PR #307 Codex
    第 8 巡 P2）。"""
    import hashlib

    canon = next(
        i for i in closure["items"]
        if i["ref"] == "canon_model.namine_ritsu_diffsinger.sha256"
    )
    assert canon["value_kind"] == "consumed_member_manifest_sha256"
    members = closure["materials"]["canon_consumed_members"]
    manifest = closure["materials"]["canon_consumed_member_manifest"]
    assert sorted(members) == sorted(manifest["members_in_order"]), (
        "materials.canon_consumed_members と manifest.members_in_order が不一致"
    )
    text = "".join(f"{members[m]}  {m}\n" for m in sorted(members))
    recomputed = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert recomputed == manifest["value"] == canon["value"], (
        f"manifest 再計算値 {recomputed} が記録値 "
        f"{manifest['value']}/{canon['value']} と一致しない"
    )


def test_acceptance_declared_provenance_sha256_matches_actual_file(
    closure: Dict[str, Any],
) -> None:
    """acceptance 節は run4_anchor_provenance.json の sha256 を宣言している。
    宣言だけして照合しないと、確定記録が変わっても本記録が気付けない
    （PR #307 セルフレビュー指摘 3）。"""
    import hashlib

    acceptance = closure["acceptance"]
    m = re.search(r"sha256=([0-9a-f]{64})", acceptance)
    assert m, "acceptance 節に run4_anchor_provenance.json の sha256 宣言が無い"
    declared = m.group(1)
    actual = hashlib.sha256(PROVENANCE_PATH.read_bytes()).hexdigest()
    assert actual == declared, (
        f"run4_anchor_provenance.json の実 sha256 {actual} が acceptance 節の宣言 "
        f"{declared} と違う（確定記録が変更されたか、本記録の宣言が古い）"
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


def test_phase4_env_contract_retest_section_shape(closure: Dict[str, Any]) -> None:
    """2026-08-23 追試（run4 環境契約 = NPY_DISABLE_CPU_FEATURES=X86_V4 の再検証）
    の形状。SIMD 実測値・WAV 再照合結果・列挙した未制御要因が揃っていること。"""
    p4 = closure.get("phase4_env_contract_retest")
    assert isinstance(p4, dict) and p4
    assert isinstance(p4.get("trigger"), str) and p4["trigger"].strip()
    assert isinstance(p4.get("gate_synth_simd_gate_check"), dict)
    assert isinstance(p4.get("simd_verification"), dict)

    wav2 = p4.get("wav_regeneration_v2_avx512_disabled")
    assert isinstance(wav2, dict)
    assert isinstance(wav2.get("match_count"), int)
    assert isinstance(wav2.get("total"), int)
    results2 = wav2.get("results")
    assert isinstance(results2, list)
    assert wav2["total"] == len(results2)
    for r in results2:
        # phase2 は "match" / phase4 は "match_recorded" とキー名が異なる。
        # 綴り違い・キー欠落を「0 件一致」として黙って通す vacuous pass を
        # 防ぐため、全 result に bool 型で存在することを明示検査する
        # （PR #307 セルフレビュー指摘 4）。
        assert isinstance(r.get("match_recorded"), bool), (
            f"phase4 result {r.get('speaker')}/{r.get('song')} に bool の "
            f"match_recorded が無い: {sorted(r)}"
        )
    matches2 = sum(1 for r in results2 if r["match_recorded"] is True)
    assert matches2 == wav2["match_count"]

    factors = p4.get("other_uncontrolled_factors_enumerated_not_swept")
    assert isinstance(factors, list) and len(factors) > 0
    assert all(isinstance(f, str) and f.strip() for f in factors)

    assert isinstance(p4.get("verdict"), str) and p4["verdict"].strip()


def test_pyproject_lists_this_test_module() -> None:
    """PR #299 セルフレビュー教訓（このファイルの流儀）: 収集しないと
    『緑なのに何も検査していない』状態になる。"""
    pyproject = _FOUNDRY.parent.parent / "pyproject.toml"
    assert pyproject.exists()
    text = pyproject.read_text(encoding="utf-8")
    assert "voice_genesis/foundry/tests/test_run4_provenance_closure.py" in text
