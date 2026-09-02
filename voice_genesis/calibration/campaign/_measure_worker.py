"""fresh-process meter call worker（`measure_stage.py` が
`python -m voice_genesis.calibration.campaign._measure_worker <json>` として
subprocess 起動する）。

JSON payload: `{"candidate_id": str, "pcm_path": str, "sr_hz": int,
"f0_hz": float|null}`。`candidate_id` を `candidates.registry.candidate_by_id`
で解決し、`implementation_ref` を import して呼ぶ。結果は
`measure_stage.meter_output_to_dict()` と同一形状の JSON に
`"cpu_seconds": <このプロセス自身の user+sys CPU 秒数>` を加えた object を
stdout へ出力する（ledger には一切触れない — 単一 writer 境界は呼び出し元
プロセスが担う。`measure_stage.py` モジュール docstring 参照）。
`cpu_seconds`（round 14 finding #2）: `--workers>1` で複数 fresh-process
worker が並行実行されるとき、親プロセスの wall-clock 経過時間は compute
cap が定義する CPU 秒数を過小計上する（並行分の CPU 時間が合算されない）
ため、各 worker が自身の `resource.getrusage`（RUSAGE_SELF + RUSAGE_CHILDREN
— 本 worker 自身は子プロセスを持たないが、将来 meter 実装が子プロセスを
spawn しても計上漏れしないよう両方合算する）を報告し、親がそれを合算して
compute counter へ課金する（`campaign/caps.py` の
`validate_worker_cpu_seconds()` docstring 参照）。
"""

from __future__ import annotations

import json
import resource
import sys

from voice_genesis.calibration.campaign.measure_stage import (
    load_pcm_signal,
    meter_output_to_dict,
    resolve_measure_callable,
)
from voice_genesis.calibration.candidates.registry import candidate_by_id


def _cpu_seconds() -> float:
    self_ru = resource.getrusage(resource.RUSAGE_SELF)
    children_ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (self_ru.ru_utime + self_ru.ru_stime) + (children_ru.ru_utime + children_ru.ru_stime)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: _measure_worker.py <json-payload>", file=sys.stderr)
        return 2
    payload = json.loads(args[0])
    candidate = candidate_by_id(str(payload["candidate_id"]))
    signal, sr = load_pcm_signal(str(payload["pcm_path"]), int(payload["sr_hz"]))
    params = dict(candidate.params_dict())
    f0_hz = payload.get("f0_hz")
    if f0_hz is not None:
        params["f0_hz"] = float(f0_hz)
    fn = resolve_measure_callable(candidate.implementation_ref)
    output = fn(signal, sr, params)
    result = meter_output_to_dict(output)
    result["cpu_seconds"] = _cpu_seconds()
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
