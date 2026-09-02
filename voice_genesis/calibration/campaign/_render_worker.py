"""C1/C4 render の fresh-process worker（`render_stage.py` が
`python -m voice_genesis.calibration.campaign._render_worker <json>` として
subprocess 起動する）。

`fixtures.determinism.render_row_pcm_hex` をそのまま呼ぶ薄い CLI ラッパー
（generator 決定論ロジックの二重実装を避ける — determinism 検査の正本は
あくまで `fixtures/determinism.py`）。JSON payload の形は
`fixtures.determinism.check_determinism_fresh_process` の subprocess 契約
（`row_json`/`secret_hex`/`campaign_id`/`family`/`split`/`row_id`/
`probe_index`）と同一。1 回の起動で `{"pcm_hex": <1 回分の PCM hex>,
"cpu_seconds": <このプロセス自身の user+sys CPU 秒数>}` の JSON を stdout へ
出力する（`render_stage.py` が 2 プロセス起動して `pcm_hex` を byte 比較し、
`cpu_seconds` を compute cap へ課金する — round 14 finding #2:
`campaign/caps.py` の `validate_worker_cpu_seconds()` docstring 参照。
`resource.getrusage` は RUSAGE_SELF + RUSAGE_CHILDREN（本 worker 自身が
子プロセスを持つことは無いが、将来レンダラが子プロセスを spawn しても
計上漏れしないよう両方を合算する）。
"""

from __future__ import annotations

import json
import resource
import sys

from voice_genesis.calibration.fixtures.determinism import render_row_pcm_hex


def _cpu_seconds() -> float:
    self_ru = resource.getrusage(resource.RUSAGE_SELF)
    children_ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (self_ru.ru_utime + self_ru.ru_stime) + (children_ru.ru_utime + children_ru.ru_stime)


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
    sys.stdout.write(json.dumps({"pcm_hex": out, "cpu_seconds": _cpu_seconds()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
