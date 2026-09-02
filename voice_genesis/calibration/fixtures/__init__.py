"""RUN10-CAL fixture matrix package (Phase B).

設計正本 §5 / IMPLEMENTATION_MAP §2.7 の 456 logical cell を明示列挙する。
`voice_genesis.calibration` の他モジュール同様、secret の生成・保存は行わない
（生成は常に呼び出し側から渡された secret を消費するのみ）。
"""

from __future__ import annotations
