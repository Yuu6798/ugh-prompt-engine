"""MeterAdapter protocol + 共通 fail filter（設計正本 §8）。

C0 は dry-run 検証のみであり、本モジュールも実測 campaign の実行を含まない。
ここで定義するのは (1) 全 candidate impl が実装すべき呼び出し規約
(`MeterAdapter` Protocol と結果型 `MeterOutput`)、(2) 設計正本 §8 が挙げる
5 種の共通 fail filter を、記録済み出力に対する純関数（predicate）として
提供することの 2 点のみ。

`MeterOutput.values` は候補ごとに異なるフィールド集合を持ちうる
（例: F0 候補は `{"f0_hz": ...}`、formant 候補は見つかった poles の分だけ
`{"f1_hz": ..., "f2_hz": ...}` 等）。値が見つからないフィールドは
**キー自体を省略する**（NaN を詰めない）。これにより「非有限値の無説明返却」
という fail filter を「missing_reason なしで NaN/Inf が values に現れる」
という単純な述語として定義できる（[UNDERSPEC-CAL-C01] 参照、registry.py
docstring 側で集約）。

D4C など依存ライブラリ不在で原理的に測定不能な候補は `ineligible=True` +
`ineligible_reason="INELIGIBLE_DEPENDENCY_ABSENT"` を返す（例外を投げない）。
これは vocab.MissingReason の閉語彙（meter status 側の理由コード）とは別軸
であり、candidate の eligibility（C0 dry-run 側の概念）に属する。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .. import vocab

INELIGIBLE_DEPENDENCY_ABSENT = "INELIGIBLE_DEPENDENCY_ABSENT"
"""D4C 等、依存ライブラリ不在で ineligible となった候補の理由文字列（設計正本 §3.3 pyworld 特則）。"""


@dataclass(frozen=True)
class MeterOutput:
    """1 候補 × 1 instance の測定出力。

    - `values`: 見つかったフィールドのみを持つ有限値の mapping。
    - `missing_reason`: 何らかの理由で値が得られなかった場合の閉語彙理由
      （`vocab.MissingReason`）。設定されている場合 `values` は空、または
      部分的な診断値のみを持ちうる。
    - `ineligible` / `ineligible_reason`: 依存不在等、候補自体が原理的に
      測定を試みられない場合（例外を投げる代わりに typed result で表現）。
    - `diagnostics`: 主張には使わない補助情報（legacy 参照値・中間量など）。
    """

    values: Mapping[str, float] = field(default_factory=dict)
    missing_reason: vocab.MissingReason | None = None
    ineligible: bool = False
    ineligible_reason: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def is_finite_and_explained(self) -> bool:
        """values 中の全値が有限、または missing_reason/ineligible で説明されているか。"""
        if self.ineligible or self.missing_reason is not None:
            return True
        return all(math.isfinite(v) for v in self.values.values())


class MeterAdapter(Protocol):
    """全 candidate impl が満たす呼び出し規約（設計正本 §8）。"""

    def measure(
        self, signal: np.ndarray, sr: int, params: Mapping[str, object]
    ) -> MeterOutput: ...


# ---------------------------------------------------------------------------
# 共通 fail filter（設計正本 §8: schema 違反 / 非有限値の無説明返却 /
# within・fresh-process 不一致 / negative control 偽検出 / 対 positive
# control 不発火）。全て記録済み出力に対する純 predicate 関数として提供する
# （campaign 実行や instance 走査はこのモジュールの責務ではない）。
# ---------------------------------------------------------------------------


def schema_violation(output: MeterOutput, required_fields: Iterable[str]) -> bool:
    """`required_fields` が eligibility 判定に使う最小フィールド集合として
    宣言されている場合、通常出力（missing/ineligible でない）に対して
    その集合が values に揃っているか（=違反なし）を検査する。

    True = 違反（schema を満たさない）。missing/ineligible な出力は対象外
    （そちらは別の理由コードで既に説明されている）。
    """
    if output.ineligible or output.missing_reason is not None:
        return False
    required = set(required_fields)
    return not required.issubset(output.values.keys())


def unexplained_nonfinite(output: MeterOutput) -> bool:
    """missing_reason/ineligible による説明なしに非有限値が values に含まれるか。

    True = 違反（無説明の非有限値）。
    """
    if output.ineligible or output.missing_reason is not None:
        return False
    return any(not math.isfinite(v) for v in output.values.values())


def within_fresh_process_mismatch(
    within_process_values: Sequence[Mapping[str, float]],
    fresh_process_values: Sequence[Mapping[str, float]],
    *,
    field_name: str,
    tol: float = 0.0,
) -> bool:
    """同一 instance の within-process repeat 群と fresh-process repeat 群
    (`field_name` のみ比較) が `tol` を超えて食い違うかを検査する。

    True = 不一致（fail filter 発火）。片方の repeat 群が丸ごと空（1 件も
    call が記録されていない）場合は不一致として扱う（fail-closed。round 30
    ADOPT (`[UNDERSPEC-CAL-D67]`) 以降もここは変更していない — 「call 自体が
    無い」は「call はあったが値が missing」とは別の異常系）。

    round 30 ADOPT (`[UNDERSPEC-CAL-D67]`, Codex round 30 PR #343 finding #2
    「Allow stable negative-control non-detections」採用): `field_name` が
    call ごとに **有る/無い** かで missing-status の一致を先に判定する——
    within の全 call・fresh の全 call のいずれでも `field_name` が欠けている
    （`MeterOutput.missing_reason` が立ち values が空 dict のまま渡ってくる、
    negative control 上の正しい非検出結果）場合は、両側で一貫した
    non-detection であり不一致とはみなさない（旧実装は空 dict に対する
    `v[field_name]` の `KeyError` を単純に「不一致」とみなしており、
    negative control（例: silence）に対して `OUTPUT_MISSING` を正しく返す
    候補が構造的に `within_fresh_process_mismatch` で ineligible になり、
    どの候補も negative control を通過できなかった）。不一致は次の 2 パターン
    のみで発火する: (1) 一部の call のみ値を報告し他は報告しない（missing-
    status が call 間で食い違う）、(2) 両側とも値を報告したがその値が
    `tol` を超えて食い違う。この判定は positive control 行にも同様に適用
    する——positive 行で全 call が一貫して missing なのは
    `positive_control_non_fire` 側が拾うべき「陽性対照の不発火」であり、
    ここでの不一致ではない（既存 filter との役割分担を崩さない）。
    """
    if not within_process_values or not fresh_process_values:
        return True
    within_present = [field_name in v for v in within_process_values]
    fresh_present = [field_name in v for v in fresh_process_values]
    if not any(within_present) and not any(fresh_present):
        # consistent missing-status across every within/fresh call: the
        # correct negative-control non-detection (or, on a positive row, a
        # positive-control non-fire handled by a different filter) — not a
        # within/fresh mismatch.
        return False
    if not all(within_present) or not all(fresh_present):
        # some processes report a value, others do not: a real mismatch.
        return True
    try:
        within_vals = [float(v[field_name]) for v in within_process_values]
        fresh_vals = [float(v[field_name]) for v in fresh_process_values]
    except (KeyError, TypeError, ValueError):
        return True
    if not all(math.isfinite(v) for v in within_vals + fresh_vals):
        return True
    spread = max(within_vals + fresh_vals) - min(within_vals + fresh_vals)
    return spread > tol


def negative_control_false_fire(detections: Iterable[bool]) -> bool:
    """negative control instance 群での検出器出力に 1 件でも True (偽検出) があるか。"""
    return any(detections)


def positive_control_non_fire(detections: Iterable[bool]) -> bool:
    """対 positive control instance 群での検出器出力に 1 件でも False (不発火) があるか。"""
    return any(not d for d in detections)
