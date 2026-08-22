"""run8/s7_pipeline_silence_diagnostic.py — `pipeline_silence_residual` の記録器。

2026-08-21 amendment（User 裁定 STEP 6 = (A)）で `rr_silence`（SP のみの実レンダ）は
hard requirement から外し、**diagnostic observation** へ降格した。削除はしない:
「経路が SP 区間にどれだけ音を残すか」は TRF 本体に効く所見だからである。

本モジュールは pass / fail を一切出さない。事前登録
`real_render_set.diagnostics.pipeline_silence_residual.record_required` が要求する
項目を測って JSON に残すだけ:

- waveform RMS（全体 / 核 / 終端窓）
- dominant F0（終端窓のスペクトルピーク）と median F0（有声フレームの中央値）
- commanded pitch（MIDI と Hz）
- SP frame count / tail frame count（**命令から**算出。波形から推定しない）
- 全候補の TRF 4 軸実測値
- acoustic / pitch predictor / vocoder の provenance sha256

使い方:
    python voice_genesis/foundry/run8/s7_pipeline_silence_diagnostic.py \\
      --real-render <manifest.json> --out <diagnostic.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_b1_calibration as b1  # noqa: E402
import s7_calib_score as cs  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
DIAGNOSTIC_SCHEMA = "s7-b1-pipeline-silence-diagnostic/0.1"
DIAGNOSTIC_STIMULUS = "rr_silence"


def dominant_hz(y: np.ndarray, sr: int) -> Optional[float]:
    """窓かけ FFT のピーク周波数（60–2000 Hz 帯）。無音なら None。"""
    if y.size == 0 or not np.any(y):
        return None
    w = y * np.hanning(y.size)
    spec = np.abs(np.fft.rfft(w))
    freqs = np.fft.rfftfreq(y.size, 1.0 / sr)
    band = (freqs > 60.0) & (freqs < 2000.0)
    if not np.any(band):
        return None
    return float(freqs[band][int(np.argmax(spec[band]))])


def rms(y: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0


def build_diagnostic(
    prereg: b1.Prereg, manifest_path: Path
) -> Dict[str, Any]:

    source = b1.real_render_source(prereg, manifest_path)
    manifest, manifest_sha, _ = s7_io.read_json_with_pin(manifest_path)
    rrs = prereg.calibration_set["real_render_set"]
    declared = {d["id"]: d for d in rrs.get("diagnostics", [])}
    if sp.PIPELINE_SILENCE_DIAGNOSTIC not in declared:
        raise KeyError(
            f"事前登録に diagnostics.{sp.PIPELINE_SILENCE_DIAGNOSTIC} が無い"
            "（amendment を pin してから回すこと）"
        )
    if DIAGNOSTIC_STIMULUS in source.roles.get(sp.ZERO_INPUT_REQUIREMENT, []):
        raise ValueError(
            f"{DIAGNOSTIC_STIMULUS} がまだ hard requirement の担当刺激になっている"
        )

    entry = next(
        e for e in manifest["conditions"] if str(e["condition_id"]) == DIAGNOSTIC_STIMULUS
    )
    stim = source.stimuli[DIAGNOSTIC_STIMULUS]
    sr = stim.sr
    y = np.asarray(stim.samples, dtype=np.float64)
    b, c = stim.score_boundary_s, stim.commanded_note_end_s
    tail = y[int(round(b * sr)): int(round((b + stim.tail_window_ms / 1000.0) * sr))]
    core = y[int(round((stim.note_onset_s + 0.10) * sr)): int(round((c - 0.05) * sr))]
    rendered = y[: int(entry.get("n_samples_rendered", y.size))]

    # 有声フレームの median F0（候補 A = pyin。診断であって選定ではない）
    voiced, f0, _times = b1.voicing_pyin(y, sr, 100.0, 10.0)
    finite = np.isfinite(f0) & voiced
    median_f0 = float(np.median(f0[finite])) if np.any(finite) else None

    # 命令から算出するフレーム勘定（波形から推定しない）
    note_frames = int(sum(entry["note_target_frames"]))
    head_frames = int(entry["head_frames"])
    tail_frames = int(cs.HEAD_FRAMES)  # gate_synth: HEAD_FRAMES == TAIL_FRAMES
    phones = list(entry["phones"])
    sp_frame_count = head_frames + note_frames + tail_frames if phones == ["SP"] else head_frames + tail_frames

    candidates = b1.enumerate_candidates(prereg)
    axis_values: Dict[str, Dict[str, float]] = {}
    for cand in candidates:
        axis_values[cand.candidate_id] = b1.measure_candidate(cand, stim)

    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "id": sp.PIPELINE_SILENCE_DIAGNOSTIC,
        "authority": "User 裁定 2026-08-21 / STEP 6 = (A)（rr_silence を diagnostic へ降格）",
        "used_for_pass_fail": False,
        "why": (
            "SP-only render は無入力ではない。detector の false positive "
            f"（{sp.ZERO_INPUT_REQUIREMENT}）とは別物として、経路が SP 区間に残す音を記録する。"
        ),
        "stimulus": DIAGNOSTIC_STIMULUS,
        "prereg_pins": dict(prereg.pins)
        | {"s7_b1_real_render_manifest.json": manifest_sha},
        "waveform": {
            "sample_rate_hz": sr,
            "n_samples": int(y.size),
            "n_samples_rendered": int(entry.get("n_samples_rendered", y.size)),
            "rms_rendered": rms(rendered),
            "rms_core": rms(core),
            "rms_tail_window": rms(tail),
            "peak_rendered": float(np.max(np.abs(rendered))) if rendered.size else 0.0,
            "samples_sha256": entry.get("samples_sha256"),
        },
        "f0": {
            "dominant_hz_tail_window": dominant_hz(tail, sr),
            "dominant_hz_core": dominant_hz(core, sr),
            "median_hz_voiced_frames": median_f0,
            "voiced_frame_fraction": float(np.count_nonzero(voiced)) / float(voiced.size)
            if voiced.size
            else 0.0,
            "method": "librosa.pyin (win 100 ms / hop 10 ms) — 診断であって候補選定ではない",
        },
        "commanded": {
            "pitch_midi": entry.get("pitch_midi_applied"),
            "pitch_hz": 440.0 * 2.0 ** ((float(entry["pitch_midi_applied"]) - 69.0) / 12.0),
            "pitch_midi_prereg": entry.get("pitch_midi_prereg"),
            "phones": phones,
            "note_target_frames": list(entry["note_target_frames"]),
            "head_frames": head_frames,
            "tail_frames": tail_frames,
            "sp_frame_count": sp_frame_count,
            "frame_ms": entry["frame_ms"],
            "note": (
                "フレーム勘定は命令から算出（波形から推定していない）。SP のみの譜面なので "
                "head + note + tail の全フレームが SP トークンである。"
            ),
        },
        "trf_axis_values_per_candidate": axis_values,
        "provenance": {
            "acoustic_onnx": manifest["render_path"]["model_sha256"]["acoustic_onnx"],
            "pitch_predictor_onnx": manifest["render_path"]["model_sha256"][
                "canon_variance_pitch_onnx"
            ],
            "duration_predictor_onnx": manifest["render_path"]["model_sha256"][
                "canon_variance_dur_onnx"
            ],
            "linguistic_onnx": manifest["render_path"]["model_sha256"]["canon_linguistic_onnx"],
            "vocoder_onnx": manifest["render_path"]["model_sha256"]["vocoder_onnx"],
            "speaker": manifest["render_path"]["speaker"],
            "containers": manifest["render_path"]["containers"],
        },
        "mechanistic_note": (
            "SP → duration → pitch predictor（SP フレームにも非ゼロ F0）→ acoustic → "
            "NSF source（F0 で励振）→ vocoder という経路が存在するため、"
            "『score boundary 後の voicing』を『acoustic が release に失敗した』へ"
            "直結させてはならない。predicted-F0-on-SP contribution を交絡候補として記帳する。"
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real-render", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=RESULTS_DIR / "s7_b1_pipeline_silence_diagnostic.json"
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    prereg = b1.load_prereg()
    b1.verify_analysis_stack(prereg)
    s7_io.reject_output_collision([args.out], [args.real_render])
    doc = build_diagnostic(prereg, args.real_render)
    s7_io.assert_json_finite(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} (diagnostic only — pass/fail には使わない)")
    print(
        f"  rms_tail={doc['waveform']['rms_tail_window']:.6f} "
        f"dominant_tail={doc['f0']['dominant_hz_tail_window']} "
        f"commanded={doc['commanded']['pitch_hz']:.2f} Hz"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
