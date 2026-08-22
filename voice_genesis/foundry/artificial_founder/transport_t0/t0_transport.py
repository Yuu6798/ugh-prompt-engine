"""t0_transport.py — operator の合成と候補選択（設計書 §7 / §11 / §15 / §19）。

`t0_duration` / `t0_energy` / `t0_afterglow` の operator を **決定論的な順序**で
合成する。順序は `duration -> energy -> afterglow` に固定する（§21 T0-G8 の
`deterministic composition order`）。

候補選択（§11）は **最小介入優先・順序固定・最初に全条件 PASS した候補で停止**。
後続候補を試して「最良値」を選ばない。この規律は `select_first_passing` が
「PASS を見つけた瞬間に return する」形で表現され、全候補の成績表を作って
から比べる実装にはなっていない。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from t0_afterglow import AFTERGLOW_OPERATORS  # noqa: E402
from t0_duration import DURATION_OPERATORS  # noqa: E402
from t0_energy import ENERGY_OPERATORS  # noqa: E402
from t0_schema import FROZEN_CANDIDATE_MODES, FROZEN_CANDIDATES  # noqa: E402

#: §21 T0-G8。合成順は固定（実行ごとに変わってはならない）。
COMPOSITION_ORDER: Tuple[str, ...] = ("duration", "energy", "afterglow")


class TransportConfig:
    """1 回の transport を決める設定（duration / energy / afterglow の operator）。"""

    __slots__ = ("duration", "energy", "afterglow")

    def __init__(self, duration: str = "D0", energy: str = "E0",
                 afterglow: str = "A0") -> None:
        for trait, op in (("duration", duration), ("energy", energy),
                          ("afterglow", afterglow)):
            if op not in FROZEN_CANDIDATES[trait]:
                raise ValueError(f"unknown {trait} operator {op!r} "
                                 f"(frozen: {list(FROZEN_CANDIDATES[trait])})")
        self.duration = duration
        self.energy = energy
        self.afterglow = afterglow

    def as_dict(self) -> Dict[str, Any]:
        return {
            "composition_order": list(COMPOSITION_ORDER),
            "duration": {"operator": self.duration,
                         "mode": FROZEN_CANDIDATE_MODES[self.duration]},
            "energy": {"operator": self.energy,
                       "mode": FROZEN_CANDIDATE_MODES[self.energy]},
            "afterglow": {"operator": self.afterglow,
                          "mode": FROZEN_CANDIDATE_MODES[self.afterglow]},
        }

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return (f"TransportConfig({self.duration}+{self.energy}+{self.afterglow})")

    @property
    def is_all_native(self) -> bool:
        return all(FROZEN_CANDIDATE_MODES[op] == "native"
                   for op in (self.duration, self.energy, self.afterglow))


def transport_unit(capture: Mapping[str, Any], sidecar: Mapping[str, Any],
                   config: TransportConfig,
                   energy_calibration: Optional[Mapping[str, Any]] = None
                   ) -> Tuple[np.ndarray, int, Dict[str, Any]]:
    """1 unit を `config` の operator で輸送する。

    起点は段の観測（`t0_stage_capture`）。duration operator だけが 24 kHz の
    **入力**（S1）を必要とするため、それを渡す。energy / afterglow は
    duration の出力に対して順に掛かる。
    """
    x1, sr = capture["waveforms"]["S1_RESAMPLED_24K"]
    baseline, sr_b = capture["waveforms"]["S5_PCM16_ROUNDTRIP"]
    if int(sr) != int(sr_b):  # pragma: no cover - 経路上は常に一致
        raise ValueError("stage sample rates disagree")
    info: Dict[str, Any] = {"composition_order": list(COMPOSITION_ORDER)}

    y, d_info = DURATION_OPERATORS[config.duration](
        np.asarray(x1, dtype=np.float64), int(sr), sidecar,
        np.asarray(baseline, dtype=np.float64))
    info["duration"] = d_info

    y, e_info = ENERGY_OPERATORS[config.energy](y, int(sr), sidecar, energy_calibration)
    info["energy"] = e_info

    y, a_info = AFTERGLOW_OPERATORS[config.afterglow](y, int(sr), sidecar)
    info["afterglow"] = a_info

    return np.ascontiguousarray(np.asarray(y, dtype=np.float64)), int(sr), info


def transport_body(captures: Mapping[str, Mapping[str, Any]],
                   sidecars: Mapping[str, Mapping[str, Any]],
                   config: TransportConfig,
                   energy_calibration: Optional[Mapping[str, Any]] = None
                   ) -> Dict[str, Any]:
    """Body 全 unit を輸送する。**stem 昇順**で回す（決定論）。"""
    out: Dict[str, Any] = {}
    for stem in sorted(captures):
        y, sr, info = transport_unit(captures[stem], sidecars[stem], config,
                                     energy_calibration)
        out[stem] = {"signal": y, "sr": sr, "info": info}
    return out


def measure_transported(transported: Mapping[str, Mapping[str, Any]],
                        md: Mapping[str, Any], measure_unit: Callable[..., Any]
                        ) -> Dict[str, Dict[str, Any]]:
    """輸送後の波形を **blind meter** で測る。"""
    return {stem: measure_unit(row["signal"], int(row["sr"]), md, stem)
            for stem, row in sorted(transported.items())}


def select_first_passing(trait: str,
                         evaluate: Callable[[str], Dict[str, Any]],
                         allowed: Optional[Sequence[str]] = None
                         ) -> Dict[str, Any]:
    """§11。凍結順で候補を試し、**最初に全条件 PASS した候補で停止**する。

    `evaluate(op)` は `{"verdict": "PASS"|"FAIL", ...}` を返す。PASS を見つけたら
    その場で返し、後続候補は **試さない**。「全候補を回して最良を選ぶ」形には
    しない（§11 末尾）。
    """
    order = list(allowed) if allowed is not None else list(FROZEN_CANDIDATES[trait])
    attempts: List[Dict[str, Any]] = []
    for op in order:
        result = evaluate(op)
        attempts.append({"operator": op, "verdict": result.get("verdict"),
                         "detail": result})
        if result.get("verdict") == "PASS":
            return {"trait": trait, "selected": op,
                    "mode": FROZEN_CANDIDATE_MODES[op],
                    "attempts": attempts, "exhausted": False,
                    "verdict": "PASS"}
    return {"trait": trait, "selected": None, "mode": None, "attempts": attempts,
            "exhausted": True, "verdict": "FAIL",
            "reason": f"{trait}: candidate list exhausted (§36-6)"}


def build_transport_registry(selections: Mapping[str, Mapping[str, Any]]
                             ) -> Dict[str, Any]:
    """§30 `transport_registry.json`。選ばれた operator と mode を記録する。

    §7「SIDECAR PASS != WORLD native preservation」。mode を必ず併記し、
    sidecar を使った場合にそれが見えなくなることを防ぐ。
    """
    reg: Dict[str, Any] = {
        "schema": "voicegenesis-af-t0-transport-registry/1.0",
        "composition_order": list(COMPOSITION_ORDER),
        "note": ("SIDECAR PASS は WORLD native preservation ではない（§7）。"
                 "mode を必ず併記する。"),
        "traits": {},
    }
    for trait in COMPOSITION_ORDER:
        sel = selections.get(trait) or {}
        op = sel.get("selected")
        reg["traits"][trait] = {
            "operator": op,
            "mode": FROZEN_CANDIDATE_MODES.get(op) if op else None,
            "candidates_tried": [a["operator"] for a in (sel.get("attempts") or [])],
            "exhausted": bool(sel.get("exhausted")),
            "verdict": sel.get("verdict"),
        }
    modes = {t: reg["traits"][t]["mode"] for t in COMPOSITION_ORDER}
    reg["uses_sidecar"] = any(m == "sidecar" for m in modes.values())
    reg["all_native"] = all(m == "native" for m in modes.values())
    return reg


def config_from_selections(selections: Mapping[str, Mapping[str, Any]]
                           ) -> TransportConfig:
    """§19 Stage D。C1/C2/C3 の winner **だけ**を組み合わせる。"""
    return TransportConfig(
        duration=(selections.get("duration") or {}).get("selected") or "D0",
        energy=(selections.get("energy") or {}).get("selected") or "E0",
        afterglow=(selections.get("afterglow") or {}).get("selected") or "A0")


__all__ = ["COMPOSITION_ORDER", "TransportConfig", "transport_unit", "transport_body",
           "measure_transported", "select_first_passing", "build_transport_registry",
           "config_from_selections"]
