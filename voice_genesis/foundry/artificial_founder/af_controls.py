"""af_controls.py — §17 Meter Controls（AF0 を見る前に測定器を検証する）。

各 metric family について、AF0 genome へ **low / high の patch** を当てた fixture を
合成し、測定器がその方向を弁別できるかだけを見る。弁別できない family があれば
`METER_NOT_CALIBRATED` で **AF0 を評価せず BLOCKED**（§17）。

control fixture は AF0 canonical run の成果物ではないので voicebank へは公開せず、
`results/.../measurements/controls.json` に測定値だけを残す。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import numpy as np

import af_measure
from af_spec import ALIAS_BY_STEM, apply_patch, genome_from_dict
from af_synth import synthesize_unit

#: family -> 測定値の取り出し方（`af_measure.measure_unit` の戻り値から）。
METRIC_EXTRACTORS: Dict[str, Callable[[Mapping[str, Any]], Optional[float]]] = {
    "hl_even_odd_ratio": lambda m: m.get("hl_even_odd_ratio"),
    "ar_alpha_center_hz": lambda m: m.get("ar_alpha_center_hz"),
    "beta_alpha_ratio": lambda m: m.get("beta_alpha_ratio"),
    "terminal_f0_delta_cents_abs": lambda m: (abs(m["terminal_f0_delta_cents"])
                                              if m.get("terminal_f0_delta_cents") is not None
                                              else None),
    "r_onset_share": lambda m: m.get("r_onset_share"),
    "sustain_dbfs": lambda m: m.get("sustain_dbfs"),
    "main_release_ms": lambda m: m.get("main_release_ms"),
    "afterglow_extra_ms": lambda m: m.get("afterglow_extra_ms"),
}


def _measure_fixture(spec: Mapping[str, Any], patch: Mapping[str, Any], stem: str,
                     metric_definitions: Mapping[str, Any]) -> Dict[str, Any]:
    genome = genome_from_dict(apply_patch(spec, patch))
    unit = ALIAS_BY_STEM[stem]
    su = synthesize_unit(genome, unit)
    x = su.pcm16.astype(np.float64) / 32768.0
    return af_measure.measure_unit(x, genome.sample_rate_hz, metric_definitions, stem)


def run_controls(spec: Mapping[str, Any], controls: Mapping[str, Any],
                 metric_definitions: Mapping[str, Any]) -> Dict[str, Any]:
    """§17 の全 family を回し、方向弁別の可否を返す。"""
    families: List[Dict[str, Any]] = []
    for ctrl in controls["controls"]:
        family = ctrl["family"]
        metric = ctrl["metric"]
        extract = METRIC_EXTRACTORS.get(metric)
        if extract is None:
            families.append({
                "family": family, "metric": metric, "unit_alias": ctrl.get("unit_alias"),
                "patch_paths": sorted(set(ctrl["low"]["patch"]) | set(ctrl["high"]["patch"])),
                "low_patch": dict(ctrl["low"]["patch"]),
                "high_patch": dict(ctrl["high"]["patch"]),
                "min_separation": float(ctrl["min_separation"]),
                "verdict": "FAIL",
                "error": f"no extractor registered for metric {metric!r}"})
            continue
        stem = ctrl["unit_alias"]
        lo = extract(_measure_fixture(spec, ctrl["low"]["patch"], stem, metric_definitions))
        hi = extract(_measure_fixture(spec, ctrl["high"]["patch"], stem, metric_definitions))
        sep = float(ctrl["min_separation"])
        ok = (lo is not None and hi is not None and np.isfinite(lo) and np.isfinite(hi)
              and (hi - lo) >= sep)
        patch_paths = sorted(set(ctrl["low"]["patch"]) | set(ctrl["high"]["patch"]))
        families.append({
            "family": family, "metric": metric, "unit_alias": stem,
            "patch_paths": patch_paths,
            "low_patch": dict(ctrl["low"]["patch"]),
            "high_patch": dict(ctrl["high"]["patch"]),
            "min_separation": float(ctrl["min_separation"]),
            "low_label": ctrl["low"]["label"], "high_label": ctrl["high"]["label"],
            "low": lo, "high": hi, "separation": (hi - lo) if (lo is not None
                                                               and hi is not None) else None,
            "verdict": "PASS" if ok else "FAIL",
        })
    return {"families": families,
            "n_failed": sum(1 for f in families if f["verdict"] != "PASS")}
