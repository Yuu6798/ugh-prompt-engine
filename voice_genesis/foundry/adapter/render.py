"""adapter/render.py — VG-F1 パイプライン: score -> f0/units/joins/warp -> WORLD -> WAV。

設計書 §2 render.py に対応する。

CLI:
    python -m adapter.render --score sakura --voice presets/neutral.json \
        --wav <vocadito_2.wav> --notes-csv <vocadito_2_notesA1.csv> --out x.wav

同一 spec(JSON) + seed -> 同一バイト列（決定論契約。乱数は perf_genes 側のみで
`np.random.default_rng(seed)` を使用し、他に非決定要素（wall-clock 等）を
含まない）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pyworld as pw
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_SINGER_DIR = _HERE.parent.parent / "singer"
for _p in (_SINGER_DIR, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import score as sc  # noqa: E402  (singer、read-only import)
import score_umi as sc_umi  # noqa: E402  (singer、read-only import)
import performance as perf  # noqa: E402  (singer、read-only import)

import donor_bank as db  # noqa: E402
import joins as jn  # noqa: E402
import perf_genes as pg  # noqa: E402
import units as un  # noqa: E402
import voice_spec as vs  # noqa: E402

SR = 24000
FRAME_PERIOD_MS = 5.0
PEAK_NORM = 0.6


def _frame_period_s() -> float:
    return FRAME_PERIOD_MS / 1000.0


def frames_for_samples(n_samples: int, sr: int) -> int:
    return max(int(round(n_samples / sr / _frame_period_s())), 0)


def _midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def build_score(name: str) -> Tuple[list, float]:
    if name == "sakura":
        return sc.build_sakura_score(), sc.TEMPO_BPM
    if name == "umi":
        return sc_umi.build_umi_score(), sc_umi.TEMPO_BPM
    raise ValueError(f"unknown score: {name!r} (expected 'sakura' or 'umi')")


def render(
    score_name: str,
    voice_spec_path: str | Path,
    wav_path: str | Path,
    notes_csv_path: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
    out_path: Optional[str | Path] = None,
    w_p: float = un.DEFAULT_W_P,
    w_d: float = un.DEFAULT_W_D,
    w_c: float = un.DEFAULT_W_C,
) -> dict:
    spec = vs.load_voice_spec(voice_spec_path)
    notes, tempo_bpm = build_score(score_name)
    segments, total_samples = perf.build_timeline(notes, sr=SR, tempo_bpm=tempo_bpm)

    bank = db.build_donor_bank(wav_path, notes_csv_path=notes_csv_path, cache_dir=cache_dir)

    targets = [
        un.TargetNote(
            pitch_hz=_midi_to_hz(seg.note.midi),
            duration_sec=(seg.end_sample - seg.start_sample) / SR,
            label=seg.note.mora.kana,
        )
        for seg in segments
    ]
    selections, sel_stats = un.select_units(targets, bank.units, w_p=w_p, w_d=w_d, w_c=w_c)

    n_bins = bank.sp.shape[1]
    placements: List[jn.NotePlacement] = []
    resolved_list: List[un.ResolvedSegment] = []
    cursor = 0
    prev_end_sample = 0
    for note_index, (seg, sel) in enumerate(zip(segments, selections)):
        gap_samples = seg.start_sample - prev_end_sample
        gap_frames = frames_for_samples(gap_samples, SR) if gap_samples > 0 else 0
        cursor += gap_frames
        note_dur_frames = max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1)
        resolved = un.resolve_unit_to_note(bank, sel.unit, note_dur_frames, note_index=note_index)
        resolved_list.append(resolved)
        start_frame = cursor
        end_frame = start_frame + resolved.n_frames
        placements.append(
            jn.NotePlacement(
                start_frame=start_frame, end_frame=end_frame, sp=resolved.sp, ap=resolved.ap,
                has_join_to_prev=not seg.is_phrase_first,
            )
        )
        cursor = end_frame
        prev_end_sample = seg.end_sample

    n_total_frames = cursor
    sp_seq, ap_seq, join_stats = jn.assemble(
        n_total_frames, n_bins, placements, frame_period_ms=FRAME_PERIOD_MS
    )

    sp_seq, ap_seq = vs.apply_warp(sp_seq, ap_seq, SR, spec.warp)

    portamento_ms = float(spec.perf.get("portamento_ms", 55.0))
    base_f0_persample = pg.build_perf_f0(
        segments, total_samples, SR, spec.perf, seed=spec.seed, portamento_ms=portamento_ms
    )
    frame_t = np.arange(n_total_frames) * _frame_period_s()
    sample_idx = np.minimum(np.round(frame_t * SR).astype(np.int64), max(total_samples - 1, 0))
    f0_seq = base_f0_persample[sample_idx] if total_samples > 0 else np.zeros(n_total_frames)

    y = pw.synthesize(
        np.ascontiguousarray(f0_seq), np.ascontiguousarray(sp_seq), np.ascontiguousarray(ap_seq),
        SR, FRAME_PERIOD_MS,
    )
    y = np.asarray(y, dtype=np.float64)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * PEAK_NORM

    if out_path is not None:
        sf.write(str(out_path), y, SR, subtype="PCM_16")

    cap_modes = [r.cap_mode for r in resolved_list]
    return dict(
        y=y, sr=SR, n_total_frames=n_total_frames, total_samples_timeline=total_samples,
        selections=selections, selection_stats=sel_stats, join_stats=join_stats,
        donor_bank_stats=bank.stats, donor_bank_source=bank.source, wav_sha256=bank.wav_sha256,
        n_stretch_extended_looped=cap_modes.count("extended_looped"),
        n_stretch_compressed_truncated=cap_modes.count("compressed_truncated"),
        n_stretch_none=cap_modes.count("none"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="VG-F1 Foundry Adapter render CLI")
    parser.add_argument("--score", required=True, choices=["sakura", "umi"])
    parser.add_argument("--voice", required=True, help="voice spec JSON path")
    parser.add_argument("--out", required=True, help="output WAV path")
    parser.add_argument("--wav", required=True, help="donor WAV path (vocadito clip)")
    parser.add_argument("--notes-csv", default=None, help="donor notes annotation CSV（省略時 fallback）")
    parser.add_argument("--cache-dir", default=None, help="donor bank npz キャッシュディレクトリ")
    args = parser.parse_args()

    result = render(
        args.score, args.voice, args.wav, notes_csv_path=args.notes_csv,
        cache_dir=args.cache_dir, out_path=args.out,
    )
    print(f"wrote {args.out}: {len(result['y'])} samples ({len(result['y']) / result['sr']:.3f}s)")
    print(f"donor_bank_source={result['donor_bank_source']} wav_sha256={result['wav_sha256']}")
    print(f"donor_bank_stats={result['donor_bank_stats']}")
    print(f"selection_stats={result['selection_stats']}")
    print(f"join_stats={result['join_stats']}")
    print(
        f"stretch: none={result['n_stretch_none']} "
        f"extended_looped={result['n_stretch_extended_looped']} "
        f"compressed_truncated={result['n_stretch_compressed_truncated']}"
    )


if __name__ == "__main__":
    main()
