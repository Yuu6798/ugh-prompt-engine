"""C1/C4 render の fresh-process worker（`render_stage.py` が
`python -m voice_genesis.calibration.campaign._render_worker <json>` として
subprocess 起動する）。

`fixtures.determinism.render_row_pcm_hex` をそのまま呼ぶ薄い CLI ラッパー
（generator 決定論ロジックの二重実装を避ける — determinism 検査の正本は
あくまで `fixtures/determinism.py`）。JSON payload の形は
`fixtures.determinism.check_determinism_fresh_process` の subprocess 契約
（`row_json`/`secret_hex`/`campaign_id`/`family`/`split`/`row_id`/
`probe_index`）と同一。1 回の起動で 1 回分の PCM hex を stdout へ出力する
（`render_stage.py` が 2 プロセス起動して byte 比較する）。
"""

from __future__ import annotations

import json
import sys

from voice_genesis.calibration.fixtures.determinism import render_row_pcm_hex


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: _render_worker.py <json-payload>", file=sys.stderr)
        return 2
    payload = json.loads(args[0])
    out = render_row_pcm_hex(
        payload["row_json"],
        payload["secret_hex"],
        campaign_id=payload["campaign_id"],
        family=payload["family"],
        split=payload["split"],
        row_id=payload["row_id"],
        probe_index=payload["probe_index"],
    )
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
