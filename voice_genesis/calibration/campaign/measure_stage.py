"""meter call stage: within-process 3 call + fresh-process 3 call
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §6, §8）。

`implementation_ref`（`candidates.registry.Candidate.implementation_ref`、
`<module>:<function>` 形式）を `importlib` で解決し `measure(signal, sr,
params) -> MeterOutput` として呼ぶ。within-process repeat はプロセス内で
直接呼ぶ（同一 bytes・同一 process — 設計正本 §6「同一 artifact 再読の
within-process repeat」）。fresh-process repeat は subprocess worker
`_measure_worker.py` を都度起動し、PCM ファイルを再読込してから呼ぶ。

**単一 writer 境界**（`provenance.py` 契約）: worker（subprocess）は結果を
stdout 経由で親プロセスへ返すのみで ledger には一切触れない。ledger
`meter_call` event の append は常に呼び出し元プロセス（本モジュールの
`run_measurement_for_instance`）が直列に行う。`[UNDERSPEC-CAL-D14]`
memo の「per-worker JSONL → 直列 append 集約」は、worker が中間 JSONL
ファイルを書いてから別途集約する 2 段構成を示唆するが、本実装は
`subprocess.run(capture_output=True)` の同期 stdout 捕捉で worker の結果を
直接受け取り、単一 writer（呼び出し元プロセス）がそのまま `Ledger.append()`
する 1 段構成を採る（中間ファイル I/O を経ずに同じ契約——「ledger に触れる
のは 1 writer のみ」——を満たす、より単純な実装）。`max_workers>1` の場合、
`ThreadPoolExecutor` で複数 fresh-process subprocess を並行起動する（各
thread は subprocess の完了待ちで I/O-bound であり GIL の制約を受けない。
ledger への append は future 完了順に main thread から直列に行う）。
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.vocab import MissingReason

WITHIN_PROCESS_REPEATS = 3
FRESH_PROCESS_REPEATS = 3


class CostCapExceededError(RuntimeError):
    """`cost_caps.check()` が超過を検出した際の fail-closed error。"""


def resolve_measure_callable(implementation_ref: str) -> Callable[..., MeterOutput]:
    """`<module>:<function>` を解決する。`candidates.impl.<mod>` 形式は
    `voice_genesis.calibration.` を補って完全修飾する
    （`candidates.registry.Candidate.implementation_ref` docstring 参照）。
    M6 のような完全修飾 `voice_genesis.calibration.<mod>:<function>` 形式は
    そのまま解決する。"""
    module_path, sep, func_name = implementation_ref.partition(":")
    if not sep or not module_path or not func_name:
        raise ValueError(f"measure_stage: invalid implementation_ref {implementation_ref!r}")
    if module_path.startswith("candidates."):
        module_path = f"voice_genesis.calibration.{module_path}"
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def load_pcm_signal(pcm_path: str | Path, sr_hz: int) -> tuple[np.ndarray, int]:
    """render 済み PCM16 ファイルを読み、`[-1, 1]` 正規化 float64 signal へ
    変換する（`tests/test_generators.py` が使う `pcm.astype(np.float64) /
    32767.0` 規約と同一）。"""
    raw = Path(pcm_path).read_bytes()
    pcm = np.frombuffer(raw, dtype=np.int16)
    signal = pcm.astype(np.float64) / 32767.0
    return signal, sr_hz


#: `[UNDERSPEC-CAL-D12]` `candidates.adapter.MeterOutput.values` は候補ごとに
#: 異なるフィールド集合を持つ（`candidates/adapter.py` docstring）。設計正本は
#: どのフィールドを selection/holdout criteria の主要スカラー値として使うか
#: を規定しないため、各 `algorithm_family`（`candidates.registry.Candidate.
#: algorithm_family`）が実際に返す `values` のキーを実装から機械的に転記した
#: 対応表を用いる（`candidates/impl/*.py` の `MeterOutput(values={...})` を
#: 直接参照）。formant 系（BURG_LPC/CEPSTRAL_POLES）は F1（`f1_hz`）を代表値
#: として採る（F2/F3 個別 gate は本 D2 infra の範囲外 — フルの multi-formant
#: margin 計算は E_use を formant index 別に持つ拡張が必要）。
PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY: Mapping[str, str] = {
    "B0_CURRENT_NACF_YIN": "f0_hz",
    "PYIN": "f0_hz",
    "B0_CURRENT_CEPSTRAL_CENTROID": "f1_est_hz",
    "CEPSTRAL_POLES": "f1_hz",
    "BURG_LPC": "f1_hz",
    "B0_CURRENT_HYBRID": "value",
    "HARMONIC_OLS": "tilt_db_per_oct",
    "HARMONIC_THEILSEN": "tilt_db_per_oct",
    "B0_CURRENT_HNR_APPROX": "hnr_db",
    "HNR_ACF": "hnr_db",
    "HARMONIC_RESIDUAL": "residual_fraction",
    "D4C_WORLD": "aperiodicity",
    "LOCAL_PROMINENCE": "center_hz",
    "WAVE_DISCONTINUITY": "magnitude",
    "SPECTRAL_FLUX": "magnitude",
}


def primary_output_value(candidate: Candidate, output: MeterOutput) -> float | None:
    """`candidate.algorithm_family` の主要出力フィールド（`values` の 1 キー）
    を返す。missing/ineligible、または該当フィールド不在なら `None`。"""
    if output.missing_reason is not None or output.ineligible:
        return None
    field = PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY.get(candidate.algorithm_family)
    if field is None:
        return None
    value = output.values.get(field)
    return float(value) if value is not None else None


def meter_output_to_dict(output: MeterOutput) -> dict[str, object]:
    return {
        "values": dict(output.values),
        "missing_reason": output.missing_reason.value if output.missing_reason is not None else None,
        "ineligible": output.ineligible,
        "ineligible_reason": output.ineligible_reason,
    }


def meter_output_from_dict(d: Mapping[str, object]) -> MeterOutput:
    missing = d.get("missing_reason")
    values_raw = d.get("values") or {}
    if not isinstance(values_raw, Mapping):
        raise ValueError("measure_stage: MeterOutput payload 'values' must be an object")
    ineligible_reason = d.get("ineligible_reason")
    return MeterOutput(
        values={str(k): float(v) for k, v in values_raw.items()},
        missing_reason=MissingReason(missing) if isinstance(missing, str) else None,
        ineligible=bool(d.get("ineligible", False)),
        ineligible_reason=str(ineligible_reason) if isinstance(ineligible_reason, str) else None,
    )


@dataclass(frozen=True)
class MeasurementRecord:
    row_id: str
    probe_index: int
    candidate_id: str
    repeat_kind: str  # "within" | "fresh"
    repeat_index: int
    process_id: str
    output: MeterOutput


def _params_with_f0(candidate: Candidate, f0_hz: float | None) -> dict[str, object]:
    """F0 依存候補（D4C・harmonic-residual）へ選択済み F0 candidate の per-instance
    出力を注入する（memo §6.4: 「fixture の truth F0 ではなく c3a-f0-selection で
    選択された F0 candidate の実測出力を instance 単位で入力とする」）。"""
    params = dict(candidate.params_dict())
    if f0_hz is not None:
        params["f0_hz"] = float(f0_hz)
    return params


def run_within_process_calls(
    candidate: Candidate,
    signal: np.ndarray,
    sr: int,
    *,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    repeats: int = WITHIN_PROCESS_REPEATS,
) -> list[MeasurementRecord]:
    """同一 process・同一 signal bytes 上で `repeats` 回直接呼ぶ。"""
    fn = resolve_measure_callable(candidate.implementation_ref)
    params = _params_with_f0(candidate, f0_hz)
    records: list[MeasurementRecord] = []
    for i in range(repeats):
        output = fn(signal, sr, params)
        records.append(
            MeasurementRecord(
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=candidate.candidate_id,
                repeat_kind="within",
                repeat_index=i,
                process_id="within-process",
                output=output,
            )
        )
    return records


def _run_one_fresh_call(
    candidate_id: str, pcm_path: Path, sr: int, f0_hz: float | None, timeout_s: float
) -> MeterOutput:
    payload = {
        "candidate_id": candidate_id,
        "pcm_path": str(pcm_path),
        "sr_hz": sr,
        "f0_hz": f0_hz,
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_genesis.calibration.campaign._measure_worker",
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=True,
    )
    return meter_output_from_dict(json.loads(proc.stdout))


def run_fresh_process_calls(
    candidate: Candidate,
    pcm_path: Path,
    sr: int,
    *,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    repeats: int = FRESH_PROCESS_REPEATS,
    timeout_s: float = 60.0,
    max_workers: int = 1,
) -> list[MeasurementRecord]:
    """`repeats` 回、subprocess worker (`_measure_worker.py`) を起動して測定
    する。`max_workers>1` なら `ThreadPoolExecutor` で並行起動する（結果は
    repeat_index 順に整列して返す — ledger への append 順を決定論的に保つ
    ため）。"""
    if max_workers <= 1:
        outputs = [
            _run_one_fresh_call(candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s)
            for _ in range(repeats)
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _run_one_fresh_call, candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s
                )
                for _ in range(repeats)
            ]
            outputs = [f.result() for f in futures]
    return [
        MeasurementRecord(
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=i,
            process_id=f"fresh-process-{i}",
            output=output,
        )
        for i, output in enumerate(outputs)
    ]


def run_measurement_for_instance(
    campaign: FrozenCampaign,
    candidate: Candidate,
    *,
    row_id: str,
    probe_index: int,
    sr_hz: int,
    f0_hz: float | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    max_workers: int = 1,
) -> list[MeasurementRecord]:
    """1 instance × 1 candidate = within 3 + fresh 3 の 6 call を実行し、
    ledger `meter_call` event を直列に記帳する。cap 超過を検出したら
    `stop_event` を記帳し `CostCapExceededError` で fail-closed する。"""
    pcm_path = campaign.renders_dir / row_id / f"{probe_index}.pcm"
    if not pcm_path.is_file():
        raise FileNotFoundError(
            f"measure_stage: pcm not rendered for row_id={row_id!r} "
            f"probe_index={probe_index}: {pcm_path}"
        )
    signal, sr = load_pcm_signal(pcm_path, sr_hz)

    t0 = time.perf_counter()
    within = run_within_process_calls(
        candidate, signal, sr, f0_hz=f0_hz, row_id=row_id, probe_index=probe_index
    )
    fresh = run_fresh_process_calls(
        candidate,
        pcm_path,
        sr,
        f0_hz=f0_hz,
        row_id=row_id,
        probe_index=probe_index,
        max_workers=max_workers,
    )
    elapsed = time.perf_counter() - t0

    records = within + fresh
    storage_bytes = 0
    for record in records:
        payload = {
            "kind": "meter_call",
            "row_id": row_id,
            "probe_index": probe_index,
            "candidate_id": candidate.candidate_id,
            "repeat_kind": record.repeat_kind,
            "repeat_index": record.repeat_index,
            **meter_output_to_dict(record.output),
        }
        campaign.ledger.append(payload)
        storage_bytes += len(json.dumps(payload).encode("utf-8"))

    if cap_counters is not None:
        cap_counters.add(compute=elapsed, storage=storage_bytes)
        if cost_caps is not None:
            decision = cost_caps_check(cap_counters, cost_caps)
            if decision is not None:
                campaign.ledger.append(decision.event_payload)
                raise CostCapExceededError(decision.detail)
    return records


def run_measure_stage(
    campaign: FrozenCampaign,
    instances: Sequence[tuple[str, int]],
    candidates: Sequence[Candidate],
    *,
    sr_by_row: Mapping[str, int],
    f0_by_instance: Mapping[tuple[str, int], float] | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    max_workers: int = 1,
) -> list[MeasurementRecord]:
    """`instances × candidates` の全 work unit を決定論的順序（instance →
    candidate_id 昇順）で処理する。"""
    f0_map = f0_by_instance or {}
    all_records: list[MeasurementRecord] = []
    for row_id, probe_index in sorted(instances):
        for candidate in sorted(candidates, key=lambda c: c.candidate_id):
            f0_hz = f0_map.get((row_id, probe_index))
            all_records.extend(
                run_measurement_for_instance(
                    campaign,
                    candidate,
                    row_id=row_id,
                    probe_index=probe_index,
                    sr_hz=sr_by_row[row_id],
                    f0_hz=f0_hz,
                    cap_counters=cap_counters,
                    cost_caps=cost_caps,
                    max_workers=max_workers,
                )
            )
    return all_records


__all__ = [
    "WITHIN_PROCESS_REPEATS",
    "FRESH_PROCESS_REPEATS",
    "CostCapExceededError",
    "resolve_measure_callable",
    "load_pcm_signal",
    "PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY",
    "primary_output_value",
    "meter_output_to_dict",
    "meter_output_from_dict",
    "MeasurementRecord",
    "run_within_process_calls",
    "run_fresh_process_calls",
    "run_measurement_for_instance",
    "run_measure_stage",
]
