"""Recast Phase 0 melody spike (``docs/recast_phase0_melody_spike.md``) の
provenance pin fixture 突合テスト。

AGENTS.md §8「provenance 成果物の再現レシピは全入力の pin 検証接続後に emit」
emit 前チェックリスト項目 5「pin は fixture テストで全数突合: working tree の
実 sha256 と照合し、将来の入力変更を機械検出（エントリ追加で自動拡張される
ループ実装で）」に対応する恒久ガード（PR #205 Codex P2 第2巡指摘、以降複数巡で
測定コードの pin 粒度を段階的に是正 — 直接 import 手動 4 本列挙 → 手動 6 本
列挙 → 本ファイルの機械閉包検証で終端化）。

(A) memo の provenance 節（`` `<path>` ... sha256:\\n  `<hex>` `` 形式の箇条書き/表）を
正規表現で parse し、parse された全 (path, sha256) エントリをループで working
tree の実ファイルと再ハッシュ照合する（データ fixture 4 件: スクリプト本体・
S1 score・出力 JSON・測定コード manifest sidecar）。将来 memo に pin エントリが
追加・変更されても、本テストはハードコードした個別パスではなく parse 結果に
追従するため変更なしで自動拡張される。

(B) 測定コードの pin は「直接 import モジュールの手動列挙」ではなく
first-party 推移閉包の**機械列挙**で担保する。
``scripts/spike_melody_similarity.py`` から ``from (svp_rpe\\.[\\w.]+) import``
で直接 import 対象を正規表現抽出し、サブプロセスでその import を実行した
うえで ``sys.modules`` を走査して ``svp_rpe`` パッケージ配下のロード済み
モジュール全て（= 実行時の推移閉包）を集め直し、committed sidecar
``examples/recast/melody_spike_2026-07-22.modules.json`` と**両方向**
（欠落・余剰とも）で突合し、各モジュールファイルの sha256 も再照合する。
サブプロセスで再計算するのは、同一プロセス内の ``sys.modules`` はテスト
セッション全体で他のテストファイルが読み込んだ無関係な svp_rpe モジュールで
汚染され得るため（フルスイート実行時に閉包が過大に見えてしまう false
positive を避ける）。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMO_PATH = REPO_ROOT / "docs" / "recast_phase0_melody_spike.md"
SPIKE_SCRIPT_PATH = REPO_ROOT / "scripts" / "spike_melody_similarity.py"
MODULES_MANIFEST_PATH = REPO_ROOT / "examples" / "recast" / "melody_spike_2026-07-22.modules.json"

#: 箇条書き `` `<path>`（任意の注記）sha256:\n  `<64桁hex>` `` を parse する。
#: バッククォート区切りのパスと、直後（同一行内、途中に注記が挟まってもよい）の
#: `sha256:` ラベル、その次行にインデントされたバッククォート区切りの hex 64桁を
#: 1 エントリとして拾う。新しい pin エントリが同じ記法で追加されれば自動的に
#: マッチ対象へ加わる。
PIN_PATTERN = re.compile(r"`([^`\n]+)`[^\n`]*sha256:\s*\n\s*`([0-9a-f]{64})`")

#: parse が silent に空へ縮退していないことの下限（スクリプト本体・S1 score 入力・
#: 出力 JSON・測定コード manifest sidecar の 4 件は常に memo に記載されている想定。
#: 個々の測定モジュールは (B) の閉包検証テストが別途担保するため、ここでの
#: 下限には含めない）。
MIN_EXPECTED_PIN_COUNT = 4

#: スクリプトの直接 import 対象（第一者 svp_rpe モジュール）を機械抽出する正規表現。
DIRECT_IMPORT_PATTERN = re.compile(r"^from (svp_rpe\.[\w.]+) import", re.MULTILINE)

#: サブプロセス内で「direct import → sys.modules 走査」を実行し、svp_rpe 配下の
#: 推移閉包 {module_name: {path, sha256}} を JSON で標準出力へ吐く使い捨てコード。
_CLOSURE_WALK_CODE = r"""
import sys, json, hashlib, importlib
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))

direct_imports = json.loads(sys.argv[2])
for m in direct_imports:
    importlib.import_module(m)

closure = {}
for name, mod in sys.modules.items():
    if name == "svp_rpe" or name.startswith("svp_rpe."):
        f = getattr(mod, "__file__", None)
        if f is None:
            continue
        p = Path(f).resolve()
        rel = p.relative_to(root)
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        closure[name] = {"path": str(rel), "sha256": sha}

print(json.dumps(closure))
"""


def _parse_provenance_pins(memo_text: str) -> list[tuple[str, str]]:
    return PIN_PATTERN.findall(memo_text)


def _extract_direct_imports(script_text: str) -> list[str]:
    return DIRECT_IMPORT_PATTERN.findall(script_text)


def _recompute_closure(direct_imports: list[str]) -> dict[str, dict[str, str]]:
    """direct_imports を新規サブプロセスで import し、svp_rpe 配下の推移閉包
    {module_name: {path, sha256}} を再計算する（現プロセスの sys.modules
    汚染から独立させるためサブプロセスを使う）。
    """
    result = subprocess.run(
        [sys.executable, "-c", _CLOSURE_WALK_CODE, str(REPO_ROOT), json.dumps(direct_imports)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# (A) データ fixture の pin 突合
# ---------------------------------------------------------------------------


def test_provenance_pins_parse_at_least_minimum_count() -> None:
    memo_text = MEMO_PATH.read_text(encoding="utf-8")
    pins = _parse_provenance_pins(memo_text)
    assert len(pins) >= MIN_EXPECTED_PIN_COUNT, (
        f"parsed only {len(pins)} (path, sha256) provenance pin(s) from {MEMO_PATH} "
        f"(expected >= {MIN_EXPECTED_PIN_COUNT}) -- if the memo's pin notation changed, "
        "PIN_PATTERN must be updated in lockstep so this doesn't silently pass empty"
    )


def test_provenance_pins_match_working_tree_sha256() -> None:
    """memo の provenance 節から parse した全 (path, sha256) エントリを working
    tree の実ファイルと再ハッシュ照合する。エントリ追加時もこのループはコード
    変更なしで新エントリを自動的に検証対象へ含める。
    """
    memo_text = MEMO_PATH.read_text(encoding="utf-8")
    pins = _parse_provenance_pins(memo_text)
    assert len(pins) >= MIN_EXPECTED_PIN_COUNT

    for raw_path, pinned_sha256 in pins:
        path = REPO_ROOT / raw_path
        assert path.is_file(), (
            f"{MEMO_PATH}: provenance pin references {raw_path!r}, "
            "which does not exist in the working tree"
        )
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha256 == pinned_sha256, (
            f"{raw_path}: working tree sha256 {actual_sha256!r} does not match "
            f"{MEMO_PATH} provenance pin {pinned_sha256!r} (stale pin -- "
            "regenerate the memo's provenance block, do not edit the pin by hand)"
        )


# ---------------------------------------------------------------------------
# (B) 測定コード first-party 推移閉包の機械検証
# ---------------------------------------------------------------------------


def test_direct_imports_extracted_from_script_match_manifest() -> None:
    """スクリプトの `from svp_rpe... import` 行から機械抽出した直接 import 対象が、
    committed manifest sidecar の `direct_imports` フィールドと一致することを
    assert する（列挙が手作業でなく機械抽出であることの相互検算）。
    """
    script_text = SPIKE_SCRIPT_PATH.read_text(encoding="utf-8")
    extracted = _extract_direct_imports(script_text)
    assert extracted, "no `from svp_rpe.* import` lines found -- extraction regex may be stale"

    manifest = json.loads(MODULES_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(extracted) == set(manifest["direct_imports"]), (
        f"direct imports extracted from {SPIKE_SCRIPT_PATH.name} {sorted(extracted)} "
        f"do not match manifest direct_imports {sorted(manifest['direct_imports'])}"
    )


def test_module_closure_recomputes_and_matches_manifest_bidirectionally() -> None:
    """スクリプトの直接 import 対象を新規サブプロセスで import し、
    `sys.modules` 走査で svp_rpe 配下の推移閉包を再計算、committed manifest
    sidecar と (a) キー集合が完全一致（欠落も余剰も fail）、(b) 各モジュールの
    sha256 が working tree の実ファイルと一致、することを assert する。
    将来 transitive 依存が増減・変更されると本テストが自動的に赤くなる。
    """
    script_text = SPIKE_SCRIPT_PATH.read_text(encoding="utf-8")
    direct_imports = _extract_direct_imports(script_text)
    assert direct_imports, "no direct imports extracted -- cannot recompute closure"

    manifest = json.loads(MODULES_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_modules: dict[str, dict[str, str]] = manifest["modules"]

    recomputed = _recompute_closure(direct_imports)

    recomputed_keys = set(recomputed)
    manifest_keys = set(manifest_modules)
    missing_from_manifest = recomputed_keys - manifest_keys
    extra_in_manifest = manifest_keys - recomputed_keys
    assert not missing_from_manifest, (
        f"recomputed closure has modules absent from {MODULES_MANIFEST_PATH.name}: "
        f"{sorted(missing_from_manifest)} -- manifest is stale, regenerate it"
    )
    assert not extra_in_manifest, (
        f"{MODULES_MANIFEST_PATH.name} lists modules no longer part of the recomputed "
        f"closure: {sorted(extra_in_manifest)} -- manifest is stale, regenerate it"
    )

    for name in sorted(recomputed_keys):
        recomputed_entry = recomputed[name]
        manifest_entry = manifest_modules[name]
        assert recomputed_entry["path"] == manifest_entry["path"], (
            f"{name}: recomputed path {recomputed_entry['path']!r} != "
            f"manifest path {manifest_entry['path']!r}"
        )
        # re-hash the working-tree file directly (not just trusting the subprocess's
        # own hashing) so a corrupted subprocess computation can't mask drift.
        file_path = REPO_ROOT / recomputed_entry["path"]
        assert file_path.is_file(), f"{name}: {file_path} does not exist in the working tree"
        actual_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert actual_sha256 == manifest_entry["sha256"], (
            f"{name}: working tree sha256 {actual_sha256!r} does not match "
            f"{MODULES_MANIFEST_PATH.name} pin {manifest_entry['sha256']!r} (stale pin)"
        )
        assert recomputed_entry["sha256"] == manifest_entry["sha256"], (
            f"{name}: subprocess-recomputed sha256 {recomputed_entry['sha256']!r} does not "
            f"match {MODULES_MANIFEST_PATH.name} pin {manifest_entry['sha256']!r}"
        )
