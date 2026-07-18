"""`python -m svp_rpe.cli` エントリポイント。

cli.py 単一モジュール時代は `if __name__ == "__main__"` ガードで -m 実行に
応じていたが、パッケージ化後は Python が `svp_rpe/cli/__main__.py` を探すため
本ファイルが後継となる（console script `svprpe` と同じ `app` を起動）。
"""
from __future__ import annotations

from svp_rpe.cli import app

if __name__ == "__main__":
    app()
