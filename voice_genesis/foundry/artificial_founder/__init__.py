"""VoiceGenesis Artificial Founder Series — AF-P0。

設計・実行契約は `DESIGN_AF_P0.md`。

このパッケージは **flat module layout** で書かれている（`af_spec` / `af_measure` …
を相対 import せず、パッケージディレクトリを `sys.path` へ入れて素の名前で import
する）。`voice_genesis/foundry/` 配下の既存系列（`genome_s4` など）と同じ流儀で、
`python -m voice_genesis.foundry.artificial_founder.p0_run` からも
`python p0_run.py` からも同一のモジュールが解決されるようにするため。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

__all__ = ["EXPERIMENT_ID", "FOUNDER_ID"]

EXPERIMENT_ID = "AF-P0"
FOUNDER_ID = "AF0"
