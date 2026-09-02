"""candidate algorithm family 実装群（設計正本 §8）。

1 module = 1 algorithm family。各 module は:

- 明示的な型付き引数を取る純粋な計算関数（単体テスト・直接呼び出し向け）
- `measure(signal, sr, params) -> candidates.adapter.MeterOutput` という
  `MeterAdapter` 準拠のアダプタ関数（`params` から必要なキーを取り出し、
  計算関数へ配線する）

の両方を提供する。
"""

from __future__ import annotations
