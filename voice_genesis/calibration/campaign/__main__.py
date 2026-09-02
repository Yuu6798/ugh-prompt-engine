"""`python -m voice_genesis.calibration.campaign ...` entry point。実体は
`cli.main()`（`IMPLEMENTATION_MAP_v1.md` §6.4 が要求する CLI 呼出し形）。
"""

from __future__ import annotations

import sys

from voice_genesis.calibration.campaign.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
