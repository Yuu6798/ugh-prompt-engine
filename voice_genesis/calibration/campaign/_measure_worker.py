"""fresh-process meter call worker（`measure_stage.py` が
`python -m voice_genesis.calibration.campaign._measure_worker <json>` として
subprocess 起動する）。

JSON payload: `{"candidate_id": str, "pcm_path": str, "sr_hz": int,
"f0_hz": float|null}`。`candidate_id` を `candidates.registry.candidate_by_id`
で解決し、`implementation_ref` を import して呼ぶ。結果は
`measure_stage.meter_output_to_dict()` と同一形状の JSON を stdout へ出力
する（ledger には一切触れない — 単一 writer 境界は呼び出し元プロセスが
担う。`measure_stage.py` モジュール docstring 参照）。
"""

from __future__ import annotations

import json
import sys

from voice_genesis.calibration.campaign.measure_stage import (
    load_pcm_signal,
    meter_output_to_dict,
    resolve_measure_callable,
)
from voice_genesis.calibration.candidates.registry import candidate_by_id


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
    sys.stdout.write(json.dumps(meter_output_to_dict(output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
