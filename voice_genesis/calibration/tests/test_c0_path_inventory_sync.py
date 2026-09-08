"""`c0_path_inventory.json`（版管理されたクローズド inventory）と実ファイル
ツリーとの同期検査（Codex レビュー 2026-09-01 P1 #2 / round 21 `[UNDERSPEC-CAL-D49]`）。

`c0_validate.calibration_path_inventory()` は検証対象 checkout の実ツリーに
依存しない委員会済み inventory ファイルを読むため、そのファイルが実ツリーと
乖離（ドリフト）していないかは、この開発時 sync test のみが検出する
（validate 実行時には検出されない — それが本 fix の意図: 検証対象 checkout
自身が壊れていても inventory は正しい値のままであってほしいため）。

`c0_path_inventory.json` がカバーする集合は 2 つの独立した由来を持つ union
である: (1) `voice_genesis/calibration/` パッケージ自身の `rglob`
（`scan_calibration_tree_inventory()`）、(2) `candidates/impl/b0_wrappers.py`
が無改変 import で実行する `voice_genesis/harness/` 配下の meter 実装の
transitive closure（`resolve_b0_wrapper_harness_paths()`。`[UNDERSPEC-CAL-D49]`。
harness/ 自体は `voice_genesis/calibration/` の外にあるため (1) には現れない
— Codex round 21 レビュー finding: 従来この (2) が inventory のどこにも
含まれておらず、C0 freeze 後の harness meter 改変が
`campaign/cli.py::_canonical_path_violations` の canonical-path 照合を
素通りしていた）。
"""

from __future__ import annotations

from voice_genesis.calibration import approvals, c0_validate


def test_committed_inventory_matches_live_tree_scan() -> None:
    """コミット済み `c0_path_inventory.json` の内容が、`rglob` による実ツリー
    走査 (`scan_calibration_tree_inventory()`) ∪ B0 wrapper が実行する harness
    meter 実装の transitive closure (`resolve_b0_wrapper_harness_paths()`) と
    厳密一致すること。乖離（新規ファイル追加・削除後に inventory 再生成を
    忘れた等、あるいは harness 側 import の変化を追随し忘れた等）はここで
    検出する。
    """
    committed = c0_validate.calibration_path_inventory()
    live = (
        c0_validate.scan_calibration_tree_inventory()
        | c0_validate.resolve_b0_wrapper_harness_paths()
    )

    missing_from_committed = sorted(live - committed)
    extra_in_committed = sorted(committed - live)
    assert not missing_from_committed and not extra_in_committed, (
        "voice_genesis/calibration/c0_path_inventory.json is out of sync with the "
        "live tree; regenerate it (missing from committed file: "
        f"{missing_from_committed}, stale entries no longer on disk: "
        f"{extra_in_committed})"
    )


def test_governance_documents_are_inventoried() -> None:
    """v1.2 の pin は 2 段連鎖（v1.2 統治正本 -> 基底 v1.1 -> 基底の基底 v1.0）
    であり、`.md` は `rglob("*.py")` に載らないため union の取りこぼしが起きる
    ——3 本すべてが scan 結果とコミット済み inventory の双方に含まれること。"""
    scanned = c0_validate.scan_calibration_tree_inventory()
    committed = c0_validate.calibration_path_inventory()
    for doc in (
        approvals.DESIGN_DOC_RELATIVE_PATH,
        approvals.BASE_DESIGN_DOC_RELATIVE_PATH,
        approvals.BASE_BASE_DESIGN_DOC_RELATIVE_PATH,
    ):
        assert doc in scanned, doc
        assert doc in committed, doc


def test_b0_wrapper_harness_imports_are_inventoried() -> None:
    """`candidates/impl/b0_wrappers.py` が実行する harness meter 実装
    （import の transitive closure、AST 静的解析。third-party import は
    `voice_genesis/harness/` に同名ファイルが無いため自動的に除外される）が
    全て `c0_path_inventory.json` に含まれること（`[UNDERSPEC-CAL-D49]`。
    Codex round 21 レビュー finding, ADOPT: 未収載だと C0 freeze 後の harness
    改変が `campaign/cli.py::_canonical_path_violations` の canonical-path
    照合を素通りする。B0 wrapper が将来新たな harness import を増やしても、
    inventory への追記漏れがあればこのテストが即座に検出する）。
    """
    used = c0_validate.resolve_b0_wrapper_harness_paths()
    assert used, "b0_wrappers.py should statically import at least one harness-local module"

    committed = c0_validate.calibration_path_inventory()
    missing = sorted(used - committed)
    assert not missing, (
        "candidates/impl/b0_wrappers.py transitively imports harness meter file(s) not "
        f"present in voice_genesis/calibration/c0_path_inventory.json: {missing}"
    )


def test_committed_inventory_file_itself_is_listed() -> None:
    """inventory ファイル自身の path も inventory 集合に含まれること
    （設計正本レビュー指摘: inventory file 自体も版管理・監査対象に含める）。"""
    committed = c0_validate.calibration_path_inventory()
    assert (
        f"voice_genesis/calibration/{c0_validate.PATH_INVENTORY_FILENAME}" in committed
    )


def test_committed_inventory_is_sorted_and_deduplicated() -> None:
    """`calibration_path_inventory()` が parse-strict であることの直接確認
    （sorted + no duplicates はロード時に検証される）。"""
    committed = sorted(c0_validate.calibration_path_inventory())
    assert committed == sorted(set(committed))
