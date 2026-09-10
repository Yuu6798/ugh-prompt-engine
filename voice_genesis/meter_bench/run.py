"""Bench 実行 + BenchReport（リセット設計 v0 §1）。

`run(meter)` は (a) case ごとに Tier K の凍結 generator で 1 本の音を作り、
(b) registry が宣言する当該 meter の全候補で測り、(c) 3 種の判定式で PASS/FAIL
を出して JSON へ書く。freeze も承認も封印も台帳も持たない。

    python -m voice_genesis.meter_bench --meter M2_SPECTRAL_TILT --out DIR

`verdict` = 「その meter の候補のうち少なくとも 1 つが全 case を通ったか」。
**PASS は校正証拠ではない**（`claimable` は定数 False）。
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import kendalltau

from voice_genesis.calibration import streams, vocab
from voice_genesis.calibration.candidates import registry
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.fixtures import controls
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.meter_bench import CLAIMABLE, SCHEMA, cases as bench_cases

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]
CRITERIA_PATH = PACKAGE_DIR / "criteria.yaml"
RESULTS_DIR = PACKAGE_DIR / "results"

BENCH_SECRET = b"voice_genesis.meter_bench/0"
"""**公開定数**。campaign の split/render secret とは無関係で、封印も分割も
しない Bench の再現性のためだけに置く（誰でも同じ音を再生成できてよい）。"""

BENCH_CAMPAIGN_ID = "METER-BENCH"

_FAMILY_MODULE: Mapping[FixtureFamily, str] = {
    FixtureFamily.F0_CONTROL: "f0_control",
    FixtureFamily.FORMANT_GT: "formant",
    FixtureFamily.TILT_GT: "tilt",
    FixtureFamily.APERIODICITY_GT: "aperiodicity",
    FixtureFamily.TRANSITION_GT: "transition",
}

PASS = "PASS"
FAIL = "FAIL"
_ROW_KEYS = ("candidates", "cases", "groups")


# --- 生成 / 測定 ----------------------------------------------------------


def render(case: bench_cases.BenchCase) -> tuple[np.ndarray, int]:
    """case を `[-1, 1]` 正規化 float64 signal へ（PCM16 量子化まで Tier K
    generator と同一経路）。"""
    module = importlib.import_module(
        f"voice_genesis.calibration.fixtures.generators.{_FAMILY_MODULE[case.family]}"
    )
    row = case.row()
    rng = streams.derive_generator(
        BENCH_SECRET,
        campaign_id=BENCH_CAMPAIGN_ID,
        family=case.family.value,
        split="BENCH",
        row_id=case.case_id,
        probe_index=0,
        purpose="render",
    )
    return np.asarray(module.render(row, rng), dtype=np.float64) / 32767.0, row.sr_hz


def resolve_measure(implementation_ref: str):
    """`<module>:<function>` を解決する（`candidates.registry` の短縮形規約）。"""
    module_path, _, func_name = implementation_ref.partition(":")
    if module_path.startswith("candidates."):
        module_path = f"voice_genesis.calibration.{module_path}"
    return getattr(importlib.import_module(module_path), func_name)


# --- 判定式（3 種のみ） ---------------------------------------------------


def scaled_error(observed: float, truth: float, scale: str) -> float:
    if scale == bench_cases.SCALE_CENTS:
        if observed <= 0.0 or truth <= 0.0:
            return math.inf
        return abs(1200.0 * math.log2(observed / truth))
    if scale == bench_cases.SCALE_RELATIVE:
        return math.inf if truth == 0.0 else abs(observed - truth) / abs(truth)
    return abs(observed - truth)


def observed_value(output: MeterOutput, field_name: str) -> tuple[float | None, str]:
    """値が読めない理由を閉じた短い語彙で返す（読めたら reason は空文字）。"""
    if output.ineligible:
        return None, f"INELIGIBLE:{output.ineligible_reason}"
    if output.missing_reason is not None:
        return None, f"MISSING:{output.missing_reason.value}"
    if field_name not in output.values:
        return None, "FIELD_ABSENT"
    value = float(output.values[field_name])
    return (value, "") if math.isfinite(value) else (None, "NONFINITE")


def check_abs_error(
    case: bench_cases.BenchCase, output: MeterOutput, tol: float
) -> tuple[bool, float | None, float | None, str]:
    observed, reason = observed_value(output, case.field_name or "")
    if observed is None:
        return False, None, None, reason
    error = scaled_error(observed, float(case.truth), case.scale)
    ok = error <= tol
    return ok, observed, error, "" if ok else "TOL_EXCEEDED"


def check_no_fire(
    output: MeterOutput, predicate: controls.DetectionPredicate | None
) -> tuple[bool, str]:
    fired = controls.detected(output, predicate)
    return (not fired), ("FIRED" if fired else "")


def check_monotone(
    truths: Sequence[float], observed: Sequence[float], polarity: int, tau_min: float
) -> tuple[bool, float]:
    tau = float(kendalltau(truths, observed).statistic) if len(truths) >= 3 else float("nan")
    return (math.isfinite(tau) and (polarity * tau) >= tau_min), tau


# --- Report ---------------------------------------------------------------


def _round(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, 4)


@dataclass(frozen=True)
class BenchReport:
    meter: vocab.MeterId
    criteria: Mapping[str, object]
    candidates: list[dict[str, object]]
    cases: list[dict[str, object]]
    groups: list[dict[str, object]]
    verdict: str
    git_sha: str
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "meter": self.meter.value,
            "git_sha": self.git_sha,
            "claimable": CLAIMABLE,
            "generated_at": self.generated_at,
            "criteria": dict(self.criteria),
            "candidate_count": len(self.candidates),
            "case_count": len(bench_cases.cases_for(self.meter)),
            "verdict": self.verdict,
            "candidates": self.candidates,
            "cases": self.cases,
            "groups": self.groups,
        }

    def write(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.meter.value}.json"
        path.write_text(dumps(self.to_dict()), encoding="utf-8")
        return path


def dumps(payload: dict[str, object]) -> str:
    """レコード配列は 1 行 1 レコードで書く（diff が読める粒度に保つ）。"""
    rows = {key: payload.pop(key) for key in _ROW_KEYS}
    body = json.dumps(payload, ensure_ascii=False, indent=1)[:-2]
    for key, values in rows.items():
        joined = ",\n  ".join(json.dumps(v, ensure_ascii=False) for v in values)
        body += f',\n "{key}": [\n  {joined}\n ]' if values else f',\n "{key}": []'
    return body + "\n}\n"


def load_criteria(path: Path = CRITERIA_PATH) -> Mapping[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["meters"]


def _git(*args: str) -> str | None:
    cmd = ["git", "-C", str(REPO_ROOT), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def git_sha() -> str:
    """実行時 HEAD。作業ツリーが汚れていれば `-dirty` を付ける（その JSON が
    どの commit の tree でも再現できないことを隠さない）。`results/` 自身は
    この run の出力先なので除外する — 見たいのは「測ったコードが commit 済みか」。"""
    head = _git("rev-parse", "HEAD")
    if head is None or not head.strip():
        return "unknown"
    excluded = f":(exclude){RESULTS_DIR.relative_to(REPO_ROOT).as_posix()}"
    dirty = _git("status", "--porcelain", "--", ".", excluded)
    return head.strip() + ("-dirty" if dirty else "")


def _evaluate(candidate, cases_, rendered, spec) -> tuple[dict, list[dict], list[dict]]:
    """1 候補 × 全 case。(候補サマリ, case 行, group 行) を返す。"""
    measure = resolve_measure(candidate.implementation_ref)
    params = candidate.params_dict()
    case_rows: list[dict[str, object]] = []
    groups: dict[str, list[tuple[float, float, int]]] = {}
    n_pass = n_fail = 0
    worst: tuple[float, str] | None = None
    for case in cases_:
        signal, sr = rendered[case.case_id]
        output = measure(signal, sr, {**params, **case.measure_params})
        row: dict[str, object] = {
            "candidate_id": candidate.candidate_id,
            "case_id": case.case_id,
            "check": case.check,
        }
        if case.check == bench_cases.NO_FIRE:
            ok, reason = check_no_fire(output, candidate.detection_predicate)
        elif case.check == bench_cases.ABS_ERROR:
            ok, observed, error, reason = check_abs_error(case, output, float(spec["tol"]))
            row["observed"], row["error"] = _round(observed), _round(error)
            if error is not None and math.isfinite(error) and (not worst or error > worst[0]):
                worst = (error, case.case_id)
        else:  # MONOTONE は group 単位で判定するので case 行は observed のみ
            observed, reason = observed_value(output, case.field_name or "")
            row["observed"] = _round(observed)
            ok = observed is not None
            if observed is not None:
                key = case.group or ""
                groups.setdefault(key, []).append((float(case.truth), observed, case.polarity))
        row["ok"] = ok
        if reason:
            row["reason"] = reason
        n_pass, n_fail = (n_pass + 1, n_fail) if ok else (n_pass, n_fail + 1)
        case_rows.append(row)

    group_rows: list[dict[str, object]] = []
    for group_id, points in sorted(groups.items()):
        ok, tau = check_monotone(
            [p[0] for p in points], [p[1] for p in points], points[0][2], float(spec["tau_min"])
        )
        group_rows.append(
            {"candidate_id": candidate.candidate_id, "group": group_id,
             "n": len(points), "tau": _round(tau), "ok": ok}
        )
    summary = {
        "candidate_id": candidate.candidate_id,
        "verdict": PASS if (n_fail == 0 and all(g["ok"] for g in group_rows)) else FAIL,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "worst_error": _round(worst[0]) if worst else None,
        "worst_case_id": worst[1] if worst else None,
    }
    return summary, case_rows, group_rows


def run(meter: vocab.MeterId, *, criteria: Mapping[str, object] | None = None) -> BenchReport:
    cases_ = bench_cases.cases_for(meter)
    spec = dict((criteria or load_criteria())[meter.value])
    if spec["check"] not in bench_cases.CHECKS:
        raise ValueError(f"criteria.yaml: {meter.value} の check {spec['check']!r} は未定義")
    rendered = {case.case_id: render(case) for case in cases_}

    summaries: list[dict[str, object]] = []
    case_rows: list[dict[str, object]] = []
    group_rows: list[dict[str, object]] = []

    for candidate in (c for c in registry.ALL_CANDIDATES if c.meter == meter):
        summary, rows, groups = _evaluate(candidate, cases_, rendered, spec)
        summaries.append(summary)
        case_rows.extend(rows)
        group_rows.extend(groups)
    return BenchReport(
        meter=meter,
        criteria=spec,
        candidates=summaries,
        cases=case_rows,
        groups=group_rows,
        verdict=PASS if any(s["verdict"] == PASS for s in summaries) else FAIL,
        git_sha=git_sha(),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voice_genesis.meter_bench")
    parser.add_argument("--meter", required=True, choices=sorted(m.value for m in vocab.MeterId))
    parser.add_argument("--out", default=str(RESULTS_DIR), help="JSON 出力先ディレクトリ")
    args = parser.parse_args(argv)
    report = run(vocab.MeterId(args.meter))
    path = report.write(Path(args.out))
    passed = [str(s["candidate_id"]) for s in report.candidates if s["verdict"] == PASS]
    print(f"{report.meter.value}: {report.verdict} (claimable=False) -> {path}")
    print(f"  PASS candidates: {', '.join(passed) if passed else 'none'}")
    return 0
