"""RUN10-CAL candidate measurement space（設計正本 §8。Phase C）。

`registry.py` が候補の宣言的定義（凍結 99 + リセット設計 v0 §2 の追加 12）を
持ち、`adapter.py` が呼び出し規約と共通 fail filter を、`impl/` が実際の測定
アルゴリズムを実装する。

このパッケージは C0 dry-run 検証（`voice_genesis.calibration.c0_validate`）
の対象であって、candidate 選択・freeze・holdout 実行そのものは含まない
（設計正本 §0 授権境界）。
"""

from __future__ import annotations
