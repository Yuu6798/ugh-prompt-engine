"""run8/s7_calib_amp_rungs.py — 本番振幅帯を張る校正 rung を作る（1.2 系列 (B)）。

事前登録 `s7_b1_calibration_set_1_2.json` の `amplitude_ladder` どおりに、base レンダ
（`rr_long_tail_160`）の**譜面境界より後の区間**へ定数ゲインを掛けた probe を作り、
既存 manifest を拡張した 1.2 用 manifest を書く。

**これはモデルの出力ではない。** 宣言済みの後処理を施した probe であり、測るのは
**検出器**の性質である（モデルがその振幅を出すかは本番セル側の実測が正本）。
比はゲインに線形なので、目標比 r には `g = r / base_ratio` で厳密に到達する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
CAL_1_2_PATH = RESULTS_DIR / "s7_b1_calibration_set_1_2.json"
MANIFEST_SCHEMA_1_2 = sp.REAL_RENDER_MANIFEST_SCHEMA_1_2


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sha_samples(y32: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(y32, dtype=np.float32).tobytes()).hexdigest()


def build_rungs(manifest_path: Path, out_dir: Path) -> Dict[str, Any]:
    cal, cal_sha, _ = s7_io.read_json_with_pin(CAL_1_2_PATH)
    ladder = cal["amplitude_ladder"]
    base_id = str(ladder["base"])

    man, man_sha, _ = s7_io.read_json_with_pin(manifest_path)
    by_id = {str(c["condition_id"]): c for c in man["conditions"]}
    if base_id not in by_id:
        raise KeyError(f"base {base_id!r} が manifest に無い")
    base = by_id[base_id]
    src_dir = Path(str(man["out_dir"]))

    # 派生 rung は base レンダの標本に定数ゲインを掛けて作る。base が差し替わると
    # rung 全体の由来が黙って変わるので、**読む前に manifest の pin を照合する**
    # （PR #303 レビュー指摘。同型穴を s7_io へ集約）。
    y, sr = s7_io.read_wav_with_pins(
        src_dir / str(base["wav"]), base["wav_sha256"], base["samples_sha256"]
    )
    boundary_s = float(base["score_boundary_s"])
    i0 = int(round(boundary_s * sr))

    out_dir.mkdir(parents=True, exist_ok=True)
    new_entries = []
    for rung in ladder["rungs"]:
        rid = str(rung["id"])
        g = float(rung["gain_applied_to_post_boundary_region"])
        z = y.copy()
        z[i0:] = z[i0:] * g
        z32 = z.astype(np.float32)
        wav = out_dir / f"{rid}.wav"
        sf.write(str(wav), z32, int(sr), subtype="FLOAT")
        entry = {
            k: base[k]
            for k in (
                "tempo_bpm", "beats", "pitch_midi_prereg", "pitch_midi_applied", "phones",
                "note_target_frames", "frame_ms", "head_frames", "note_onset_s",
                "commanded_note_end_s", "score_boundary_s", "tail_window_ms",
                "sample_rate_hz", "stage3_mode", "n_samples_rendered", "zero_padded_to_s",
                "rendered_duration_s",
            )
            if k in base
        }
        entry.update(
            {
                "condition_id": rid,
                "maps_to": "post_boundary_gain_scaled",
                "kind": "post_boundary_gain_scaled",
                "rendered": False,
                "derived": True,
                "derived_from": base_id,
                "gain_post_boundary": g,
                "target_tail_core_ratio": float(rung["target_tail_core_ratio"]),
                "calibration_only_intervention": True,
                "is_not_a_model_render": (
                    "宣言済み後処理の probe。検出器の性質を測る刺激であって"
                    "モデルの出力ではない"
                ),
                "wav": wav.name,
                "wav_sha256": _sha_file(wav),
                "samples_sha256": _sha_samples(z32),
                "n_samples": int(z32.size),
                "duration_s": float(z32.size) / int(sr),
                "peak_raw": float(np.max(np.abs(z32))) if z32.size else 0.0,
                "rms_raw": float(np.sqrt(np.mean(z.astype(np.float64) ** 2))) if z.size else 0.0,
                "normalized": False,
                "override_kind": None,
            }
        )
        new_entries.append(entry)
        print(f"  {rid:12s} target={rung['target_tail_core_ratio']:.3f} gain={g:.6f} "
              f"sha={entry['samples_sha256'][:12]}")

    # 既存条件の wav を新 out_dir へ複製する（1 つの manifest で完結させるため）
    for cid, c in by_id.items():
        src = src_dir / str(c["wav"])
        dst = out_dir / str(c["wav"])
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())

    doc = dict(man)
    doc["schema"] = MANIFEST_SCHEMA_1_2
    doc["series"] = "TRF measurement spec / 1.2"
    doc["extends"] = {"manifest": str(manifest_path), "sha256": man_sha}
    doc["calibration_set_1_2"] = {"path": CAL_1_2_PATH.name, "sha256": cal_sha}
    doc["out_dir"] = str(out_dir.resolve())
    doc["conditions"] = list(man["conditions"]) + new_entries
    doc["n_amplitude_rungs"] = len(new_entries)
    return doc


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="1.0 系列の real-render manifest")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--manifest-out", required=True)
    args = ap.parse_args(list(argv) if argv is not None else None)

    out_path = Path(args.manifest_out)
    s7_io.reject_output_collision([out_path], [CAL_1_2_PATH, Path(args.manifest)])
    doc = build_rungs(Path(args.manifest), Path(args.out_dir))
    s7_io.assert_json_finite(doc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"| manifest(1.2): {out_path}  conditions={len(doc['conditions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
