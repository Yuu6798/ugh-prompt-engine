"""`python -m voice_genesis.meter_bench --meter X --out DIR`。"""

from __future__ import annotations

import sys

from voice_genesis.meter_bench.run import main

if __name__ == "__main__":
    sys.exit(main())
