"""t0_gates.py — T0-G0..T0-G10 と総合判定（設計書 §21 / §22 / §28 / §29）。

AF-P0 の `af_gates` は **変更しない**（変えると P0 の `code_closure_sha256` が
動き、凍結済みの P0 成果物と食い違う = §3「AF-P0 結果上書き」に触れる）。
そのため closure 計算も含めて T0 側に持つ。`GateResult` の形だけは P0 と
揃える（記録の読み手が同じ目で読めるように）。
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_AF = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (str(_AF), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from af_spec import aggregate_digest, sha256_file  # noqa: E402

from t0_schema import (ALL_GATE_IDS, FROZEN_AF0_SPEC_SHA256,  # noqa: E402
                       FROZEN_BASE_COMMIT, FROZEN_P0_ARTIFACTS,
                       FROZEN_SENTINEL_FAMILIES)


@dataclass(frozen=True)
class GateResult:
    """1 Gate の判定（P0 の `af_gates.GateResult` と同じ形）。"""

    gate: str
    name: str
    verdict: str
    detail: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate, "name": self.name, "verdict": self.verdict,
                "detail": self.detail}


def _verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# ---------------------------------------------------------------------------
# §28 Code Closure（T0 版・推移的）
# ---------------------------------------------------------------------------
def _search_roots(t0_dir: Path) -> List[Tuple[str, Path]]:
    """flat module 名の探索根。実行時の `sys.path` 挿入と同じ並びにする。

    T0 は `transport_t0/` を起点に `artificial_founder/af_*.py` を辿り、そこから
    `adapter/*.py` -> `singer/phoneme_jp.py` まで届く（§28 の「最低」条件）。
    """
    af = t0_dir.parent
    foundry = af.parent
    voice_genesis = foundry.parent
    return [
        ("transport_t0", t0_dir),
        ("artificial_founder", af),
        ("adapter", foundry / "adapter"),
        ("singer", voice_genesis / "singer"),
    ]


def _imported_names(path: Path) -> List[str]:
    """1 ファイルが import するトップレベル名を `ast` で静的に集める。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):  # pragma: no cover
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module.split(".")[0])
    return names


def code_closure_digest(t0_dir: str | Path) -> Tuple[str, List[Tuple[str, str]]]:
    """§28。`transport_t0/*.py` を起点にした **実 call graph** の閉包。"""
    t0 = Path(t0_dir).resolve()
    roots = [(label, root.resolve()) for label, root in _search_roots(t0)]
    seen: Dict[str, Path] = {}
    queue: List[Path] = sorted(t0.glob("*.py"))

    def _label_for(path: Path) -> Optional[str]:
        for label, root in roots:
            if path.parent == root:
                return f"{label}/{path.name}"
        return None

    while queue:
        path = queue.pop(0)
        label = _label_for(path)
        if label is None or label in seen:
            continue
        seen[label] = path
        for name in _imported_names(path):
            for _, root in roots:
                candidate = root / f"{name}.py"
                if candidate.exists():
                    queue.append(candidate)
                    break
    rows = [(label, sha256_file(seen[label])) for label in sorted(seen)]
    return aggregate_digest(rows), rows


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
def gate_input_freeze(pins: Mapping[str, Any]) -> GateResult:
    """T0-G0 INPUT_FREEZE（§4 / §21）。

    AF0 spec SHA / base commit / P0 成果物の存在が pin と一致すること。
    不一致は `AF0_INPUT_DRIFT`。
    """
    detail: Dict[str, Any] = dict(pins)
    problems: List[str] = []
    if pins.get("af0_spec_sha256") != FROZEN_AF0_SPEC_SHA256:
        problems.append(
            f"AF0 spec sha256 drift: expected {FROZEN_AF0_SPEC_SHA256}, "
            f"got {pins.get('af0_spec_sha256')}")
    base = pins.get("base_commit")
    if base and base != FROZEN_BASE_COMMIT:
        problems.append(f"base commit drift: expected {FROZEN_BASE_COMMIT}, got {base}")
    missing = [name for name in FROZEN_P0_ARTIFACTS
               if name not in (pins.get("p0_artifacts") or {})]
    if missing:
        problems.append(f"missing pinned AF-P0 artifacts: {missing}")
    detail["problems"] = problems
    detail["reason_code"] = "AF0_INPUT_DRIFT" if problems else None
    return GateResult("T0-G0", "INPUT_FREEZE", _verdict(not problems), detail)


def gate_baseline_replay(body_ok: Mapping[str, Any],
                         replay: Mapping[str, Any]) -> GateResult:
    """T0-G1 BASELINE_REPLAY（§16 / §21）。

    Body 側が P0 と同じく全 PASS で、re-expression の失敗 3 形質が再現すること。
    """
    detail = {"body": dict(body_ok), "reexpression_replay": dict(replay)}
    ok = (body_ok.get("verdict") == "PASS" and replay.get("verdict") == "PASS")
    detail["reason_code"] = None if ok else "BASELINE_NOT_REPRODUCED"
    return GateResult("T0-G1", "BASELINE_REPLAY", _verdict(ok), detail)


def gate_stage_ledger(completeness: Mapping[str, Any]) -> GateResult:
    """T0-G2 STAGE_LEDGER_COMPLETE（§17 / §21）。"""
    ok = completeness.get("verdict") == "PASS"
    return GateResult("T0-G2", "STAGE_LEDGER_COMPLETE", _verdict(ok),
                      dict(completeness))


def _transport_gate(gate_id: str, name: str, trait: str,
                    selection: Mapping[str, Any],
                    calibration: Mapping[str, Any],
                    holdout: Mapping[str, Any],
                    af0: Mapping[str, Any]) -> GateResult:
    """T0-G3/G4/G5 共通（§21）。calibration + holdout + AF0 がすべて PASS。"""
    parts = {"calibration": calibration.get("verdict"),
             "holdout": holdout.get("verdict"),
             "af0": af0.get("verdict")}
    ok = (selection.get("verdict") == "PASS"
          and all(v == "PASS" for v in parts.values()))
    detail = {
        "trait": trait,
        "selected_operator": selection.get("selected"),
        "mode": selection.get("mode"),
        "candidates_tried": [a["operator"] for a in (selection.get("attempts") or [])],
        "exhausted": bool(selection.get("exhausted")),
        "parts": parts,
        "calibration": dict(calibration),
        "holdout": dict(holdout),
        "af0": dict(af0),
    }
    if not ok:
        detail["reason_code"] = f"{trait.upper()}_NOT_TRANSPORTED"
    return GateResult(gate_id, name, _verdict(ok), detail)


def gate_duration_transport(selection: Mapping[str, Any], calibration: Mapping[str, Any],
                            holdout: Mapping[str, Any], af0: Mapping[str, Any]
                            ) -> GateResult:
    return _transport_gate("T0-G3", "DURATION_TRANSPORT", "duration",
                           selection, calibration, holdout, af0)


def gate_energy_transport(selection: Mapping[str, Any], calibration: Mapping[str, Any],
                          holdout: Mapping[str, Any], af0: Mapping[str, Any]
                          ) -> GateResult:
    return _transport_gate("T0-G4", "ENERGY_TRANSPORT", "energy",
                           selection, calibration, holdout, af0)


def gate_ag_transport(selection: Mapping[str, Any], calibration: Mapping[str, Any],
                      holdout: Mapping[str, Any], af0: Mapping[str, Any]) -> GateResult:
    return _transport_gate("T0-G5", "AG_TRANSPORT", "afterglow",
                           selection, calibration, holdout, af0)


def gate_sentinel_non_regression(sentinels: Mapping[str, Any]) -> GateResult:
    """T0-G6 SENTINEL_NON_REGRESSION（§14 / §21）。"""
    ok = sentinels.get("verdict") == "PASS"
    detail = dict(sentinels)
    detail["required_families"] = list(FROZEN_SENTINEL_FAMILIES)
    missing = [f for f in FROZEN_SENTINEL_FAMILIES
               if f not in (sentinels.get("per_family") or {})]
    if missing:
        detail["missing_families"] = missing
        ok = False
    if not ok:
        detail["reason_code"] = "REJECTED_BY_REGRESSION"
    return GateResult("T0-G6", "SENTINEL_NON_REGRESSION", _verdict(ok), detail)


def gate_combined_transport(combined: Mapping[str, Any]) -> GateResult:
    """T0-G7 COMBINED_TRANSPORT（§19 / §21）。"""
    ok = combined.get("verdict") == "PASS"
    detail = dict(combined)
    if not ok:
        detail["reason_code"] = "COMBINATION_NOT_ESTABLISHED"
    return GateResult("T0-G7", "COMBINED_TRANSPORT", _verdict(ok), detail)


def gate_determinism(same_process: Mapping[str, Any],
                     cross_process: Mapping[str, Any]) -> GateResult:
    """T0-G8 DETERMINISM（§21）。

    sidecar bytes / transport config / 出力 WAV SHA / measurements / verdict の
    **すべて**が一致すること。1 つでも欠けたら PASS にしない。
    """
    required = ("sidecar_digest", "transport_config_digest", "wav_digest",
                "measurements_digest", "verdict")
    detail = {"same_process": dict(same_process), "cross_process": dict(cross_process),
              "required_keys": list(required)}
    problems: List[str] = []
    for label, row in (("same_process", same_process), ("cross_process", cross_process)):
        missing = [k for k in required if k not in (row.get("compared") or {})]
        if missing:
            problems.append(f"{label}: missing compared keys {missing}")
        if not row.get("match"):
            problems.append(f"{label}: mismatch on "
                            f"{row.get('mismatched') or 'unknown keys'}")
    detail["problems"] = problems
    if problems:
        detail["reason_code"] = "DETERMINISM_FAILURE"
    return GateResult("T0-G8", "DETERMINISM", _verdict(not problems), detail)


def gate_provenance(pins: Mapping[str, Any], publication: Mapping[str, Any],
                    sidecar_binding: Mapping[str, Any]) -> GateResult:
    """T0-G9 PROVENANCE（§21 / §27 / §30）。"""
    required_pins = ("af0_spec_sha256", "p0_code_closure_sha256",
                     "t0_code_closure_sha256", "t0_criteria_sha256",
                     "calibration_sha256", "holdout_sha256", "sidecar_digest")
    missing = [k for k in required_pins if not pins.get(k)]
    detail: Dict[str, Any] = {
        "missing_pins": missing, "publication": dict(publication),
        "sidecar_binding": dict(sidecar_binding),
        "code_closure_verified": bool(pins.get("t0_code_closure_verified")),
    }
    ok = (not missing
          and bool(publication.get("published"))
          and bool(publication.get("bundle_verified"))
          and not publication.get("partial_artifacts")
          and sidecar_binding.get("verdict") == "PASS"
          and detail["code_closure_verified"])
    if not ok:
        detail["reason_code"] = "PROVENANCE_FAILURE"
    return GateResult("T0-G9", "PROVENANCE", _verdict(ok), detail)


def gate_fresh_confirmation(fresh: Mapping[str, Any]) -> GateResult:
    """T0-G10 FRESH_CONFIRMATION（§20 / §21）。

    **freeze 後**の holdout + AF0 confirmation。freeze 前に走らせた結果を
    ここへ流用してはならないので、`after_freeze` フラグを必須にする。
    """
    detail = dict(fresh)
    ok = (fresh.get("verdict") == "PASS" and bool(fresh.get("after_freeze")))
    if not fresh.get("after_freeze"):
        detail["problem"] = ("fresh confirmation must run after the combined package "
                             "is frozen (§20)")
    if not ok:
        detail["reason_code"] = "FRESH_CONFIRMATION_FAILED"
    return GateResult("T0-G10", "FRESH_CONFIRMATION", _verdict(ok), detail)


# ---------------------------------------------------------------------------
# §22 総合判定
# ---------------------------------------------------------------------------
#: §22。不成立で NOT_ESTABLISHED になる Gate。
NOT_ESTABLISHED_GATES: Tuple[str, ...] = (
    "T0-G3", "T0-G4", "T0-G5", "T0-G6", "T0-G7", "T0-G10")
#: §22 BLOCKED（baseline 不再現・依存・計器不能・共有経路変更要求）。
BLOCKED_GATES: Tuple[str, ...] = ("T0-G0", "T0-G1", "T0-G2")
#: §22 FAILED（AF0/P0 改変・provenance 虚偽・determinism 違反）。
FAILED_GATES: Tuple[str, ...] = ("T0-G8", "T0-G9")


def overall_verdict(gates: Sequence[GateResult],
                    blocked_reason: Optional[str] = None) -> Dict[str, Any]:
    """§22。Gate 集合の完全性を先に確かめてから判定する。

    欠落・重複・未知 ID があれば **判定不能**（BLOCKED）。「一部の Gate だけ
    通して PASS」を作らせない。
    """
    ids = [g.gate for g in gates]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    unknown = sorted(set(ids) - set(ALL_GATE_IDS))
    missing = [i for i in ALL_GATE_IDS if i not in ids]
    if dupes or unknown or missing:
        return {
            "verdict": "BLOCKED", "reason_codes": ["INCOMPLETE_GATE_SET"],
            "failed_gates": [], "missing_gates": missing,
            "duplicate_gates": dupes, "unknown_gates": unknown,
        }
    if blocked_reason:
        return {"verdict": "BLOCKED", "reason_codes": [blocked_reason],
                "failed_gates": [g.gate for g in gates if g.verdict != "PASS"],
                "missing_gates": [], "duplicate_gates": [], "unknown_gates": []}

    failed = [g for g in gates if g.verdict != "PASS"]
    if not failed:
        return {"verdict": "PASS", "reason_codes": [], "failed_gates": [],
                "missing_gates": [], "duplicate_gates": [], "unknown_gates": []}

    failed_ids = [g.gate for g in failed]
    reasons = [g.detail.get("reason_code") for g in failed if g.detail.get("reason_code")]
    if any(i in FAILED_GATES for i in failed_ids):
        verdict = "FAILED"
    elif any(i in BLOCKED_GATES for i in failed_ids):
        verdict = "BLOCKED"
    elif any(i in NOT_ESTABLISHED_GATES for i in failed_ids):
        verdict = "NOT_ESTABLISHED"
    else:  # pragma: no cover - 上の 3 集合で全 Gate を覆っている
        verdict = "NOT_ESTABLISHED"
    return {"verdict": verdict, "reason_codes": sorted(set(reasons)),
            "failed_gates": failed_ids, "missing_gates": [], "duplicate_gates": [],
            "unknown_gates": []}


__all__ = ["GateResult", "code_closure_digest", "gate_input_freeze",
           "gate_baseline_replay", "gate_stage_ledger", "gate_duration_transport",
           "gate_energy_transport", "gate_ag_transport",
           "gate_sentinel_non_regression", "gate_combined_transport",
           "gate_determinism", "gate_provenance", "gate_fresh_confirmation",
           "overall_verdict", "NOT_ESTABLISHED_GATES", "BLOCKED_GATES", "FAILED_GATES"]
