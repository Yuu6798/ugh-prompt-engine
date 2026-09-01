"""`c0_path_inventory.json`（版管理されたクローズド inventory）と実ファイル
ツリーとの同期検査（Codex レビュー 2026-09-01 P1 #2）。

`c0_validate.calibration_path_inventory()` は検証対象 checkout の実ツリーに
依存しない委員会済み inventory ファイルを読むため、そのファイルが実ツリーと
乖離（ドリフト）していないかは、この開発時 sync test のみが検出する
（validate 実行時には検出されない — それが本 fix の意図: 検証対象 checkout
自身が壊れていても inventory は正しい値のままであってほしいため）。
"""

from __future__ import annotations

from voice_genesis.calibration import c0_validate


def test_committed_inventory_matches_live_tree_scan() -> None:
    """コミット済み `c0_path_inventory.json` の内容が、`rglob` による実ツリー
    走査 (`scan_calibration_tree_inventory()`) と厳密一致すること。乖離
    （新規ファイル追加・削除後に inventory 再生成を忘れた等）はここで検出する。
    """
    committed = c0_validate.calibration_path_inventory()
    live = c0_validate.scan_calibration_tree_inventory()

    missing_from_committed = sorted(live - committed)
    extra_in_committed = sorted(committed - live)
    assert not missing_from_committed and not extra_in_committed, (
        "voice_genesis/calibration/c0_path_inventory.json is out of sync with the "
        "live tree; regenerate it (missing from committed file: "
        f"{missing_from_committed}, stale entries no longer on disk: "
        f"{extra_in_committed})"
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
