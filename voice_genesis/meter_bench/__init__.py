"""Meter Bench — 計器を **直すため** の小さく速い台（RUN10-CAL リセット設計 v0 §1）。

campaign 基盤（`voice_genesis/calibration/campaign/*` 以下、Tier F）は
「計器が正しいかを判定する装置」であって「直す装置」ではない。本パッケージは
その手前の層 = **計器開発** を担う: 正解が分かっている合成音に対して meter が
正しい値を返すかを、承認・nonce・台帳・封印を一切持たずに pytest と JSON だけで
回す。

**PASS は校正証拠ではない**。`BenchReport.claimable` は定数 `False` であり、
Bench の PASS が意味するのは「確定 campaign に計算資源を使ってよい」という
一点のみ。校正の主張は既存 campaign 基盤（Tier F、凍結）の Gate だけが行う。

統治予算（リセット設計 v0 §4）:

- 本パッケージは **tests・criteria.yaml 込みで 800 行上限**
  （`tests/test_bench.py::test_line_budget` が enforce）。
- 判定式は `abs_error` / `monotone` / `no_fire` の 3 種のみ。増やさない。
- Tier F（campaign / freeze / 承認 / 台帳 / 分割 / Gate / 選抜）は import 禁止
  （`tests/test_bench.py::test_import_boundary` が ast 走査で機械的に拒否）。
"""

from __future__ import annotations

SCHEMA = "meter-bench/0"

#: `BenchReport.claimable` の定数。Bench の PASS は校正証拠ではない（§4-5）。
CLAIMABLE = False
