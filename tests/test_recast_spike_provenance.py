"""Recast Phase 0 melody spike (``docs/recast_phase0_melody_spike.md``) の
provenance pin fixture 突合テスト。

AGENTS.md §8「provenance 成果物の再現レシピは全入力の pin 検証接続後に emit」
emit 前チェックリスト項目 5「pin は fixture テストで全数突合: working tree の
実 sha256 と照合し、将来の入力変更を機械検出（エントリ追加で自動拡張される
ループ実装で）」に対応する恒久ガード（PR #205 Codex P2 第2巡指摘、以降複数巡で
測定コードの pin 粒度を段階的に是正 — 直接 import 手動 4 本列挙 → 手動 6 本
列挙 → importlib 閉包の事前列挙・サブプロセス再計算 → 実行前スナップショット
（TOCTOU 排除）→ 本ファイルの「実行前スナップショット × sys.setprofile 実行時
消費トレースの交差 manifest + hash-only 検証」で終端化。旧版はロードされた
だけで一度も呼ばれない package `__init__` 副作用 export を pin してしまい
（29 モジュール）無関係変更で偽の再実測強制を招いていたため、実行時消費
granularity（7 モジュール）に絞った）。

(A) memo の provenance 節（`` `<path>` ... sha256:\\n  `<hex>` `` 形式の箇条書き/表）を
正規表現で parse し、parse された全 (path, sha256) エントリをループで working
tree の実ファイルと再ハッシュ照合する（データ fixture 4 件: スクリプト本体・
S1 score・出力 JSON・測定コード manifest sidecar）。将来 memo に pin エントリが
追加・変更されても、本テストはハードコードした個別パスではなく parse 結果に
追従するため変更なしで自動拡張される。

(B) 測定コードの pin は「直接 import モジュールの手動列挙」ではなく、
``scripts/spike_melody_similarity.py --dump-modules`` で**実行前スナップショット
（TOCTOU 排除）× 実行時消費トレース（sys.setprofile call イベント）の交差**
として機械生成した committed sidecar
``examples/recast/melody_spike_2026-07-22.modules.json`` で担保する。
closed-loop 論証（memo 参照）により、実行経路への新規消費追加は pin 済み
ファイルの編集を経由してしか起こり得ず、その編集が本テストの hash アラームを
踏むため、閉包を CI 内でサブプロセス再実行して都度再検証する必要はない
（重い実行コスト無しの hash-only 検証で足りる）。本テストは
(b1) manifest が列挙する全ファイルが working tree に存在し sha256 が一致する
こと、(b2) スクリプトから機械抽出した直接 import 6 モジュールが manifest の
`direct_imports` の部分集合であること、の 2 点のみを検証する。
"""
from __future__ import annotations

import hashlib
import json
import re
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
#: 個々の測定モジュールは (B) の manifest 突合テストが別途担保するため、ここでの
#: 下限には含めない）。
MIN_EXPECTED_PIN_COUNT = 4

#: スクリプトの直接 import 対象（第一者 svp_rpe モジュール）を機械抽出する正規表現。
DIRECT_IMPORT_PATTERN = re.compile(r"^from (svp_rpe\.[\w.]+) import", re.MULTILINE)


def _parse_provenance_pins(memo_text: str) -> list[tuple[str, str]]:
    return PIN_PATTERN.findall(memo_text)


def _extract_direct_imports(script_text: str) -> list[str]:
    return DIRECT_IMPORT_PATTERN.findall(script_text)


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
# (B) 測定コード manifest（実行トレース由来）の hash-only 検証
# ---------------------------------------------------------------------------


def test_module_manifest_entries_match_working_tree_sha256() -> None:
    """`examples/recast/melody_spike_2026-07-22.modules.json`（実行トレース由来の
    svp_rpe 推移閉包 manifest）が列挙する全モジュールについて、記録された
    ``path`` が working tree に実在し、その sha256 が manifest の pin と
    一致することを assert する。将来 pin 済みファイルのいずれかが変更されると
    本テストが赤くなり、silent stale を防止する（closed-loop 論証は memo 参照）。
    """
    manifest = json.loads(MODULES_MANIFEST_PATH.read_text(encoding="utf-8"))
    modules: dict[str, dict[str, str]] = manifest["modules"]
    assert modules, f"{MODULES_MANIFEST_PATH.name} has no modules -- manifest may be stale/empty"

    for name, entry in modules.items():
        file_path = REPO_ROOT / entry["path"]
        assert file_path.is_file(), (
            f"{name}: manifest path {entry['path']!r} does not exist in the working tree"
        )
        actual_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert actual_sha256 == entry["sha256"], (
            f"{name}: working tree sha256 {actual_sha256!r} does not match "
            f"{MODULES_MANIFEST_PATH.name} pin {entry['sha256']!r} (stale pin -- "
            "regenerate via `scripts/spike_melody_similarity.py --dump-modules`)"
        )


def test_direct_imports_extracted_from_script_are_subset_of_manifest() -> None:
    """スクリプトの `from svp_rpe... import` 行から機械抽出した直接 import 対象が、
    committed manifest sidecar の `direct_imports` フィールド、および
    `modules` のキー集合の部分集合であることを assert する（列挙が手作業でなく
    機械抽出であることの相互検算 + 直接 import が閉包から漏れていないことの検算）。
    """
    script_text = SPIKE_SCRIPT_PATH.read_text(encoding="utf-8")
    extracted = _extract_direct_imports(script_text)
    assert extracted, "no `from svp_rpe.* import` lines found -- extraction regex may be stale"

    manifest = json.loads(MODULES_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_direct = set(manifest["direct_imports"])
    manifest_modules = set(manifest["modules"])

    assert set(extracted) == manifest_direct, (
        f"direct imports extracted from {SPIKE_SCRIPT_PATH.name} {sorted(extracted)} "
        f"do not match manifest direct_imports {sorted(manifest_direct)}"
    )
    missing_from_modules = manifest_direct - manifest_modules
    assert not missing_from_modules, (
        f"manifest direct_imports references modules absent from its own `modules` "
        f"closure: {sorted(missing_from_modules)}"
    )
