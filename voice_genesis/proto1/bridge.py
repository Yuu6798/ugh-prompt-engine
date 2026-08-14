"""bridge.py — VoiceGenome v0.2（genome.py）↔ vt_harness の R0.1 レンダラ Genome の橋渡し。

`vt_harness/voice_r0.py`（凍結・無改変）と `vt_harness/voice_r0_1.py`（息ノイズ
整形順序のみ修正した R0.1、同じく無改変）を import 流用し、P1 の新スキーマから
`render_note` を呼べる形に変換する。vt_harness/ 配下には一切書き込まない。

変換方針は `underspec_log_p1.md` の [UNDERSPEC-P1-3]〜[UNDERSPEC-P1-5] に詳細。
要点だけここにも記す:

  - `resonance.formant_scale` / `formant_offsets[4]`:
    `effective_formant_hz[i] = base_formants_hz[i] * formant_scale * (1 + formant_offsets[i])`
    という乗算合成式で `voice_r0.ResonanceParams.base_formants_hz` へ織り込み、
    橋渡し後の `formant_scale` 自体は 1.0 を渡す（`formant_shift()` 内の
    二重スケーリング回避）。
  - `resonance.bandwidth_scale`: `bandwidths_hz` の全要素への一様乗算。
  - `microprosody.jitter_amount`（事前分布 [0, 0.02]）→
    `jitter_pct_of_period = jitter_amount * JITTER_BRIDGE_SCALE`
    （`JITTER_BRIDGE_SCALE = 30.0`。根拠は underspec log 参照。恣意的な線形
    写像であり、R0.1 のジッタ知覚感度を再測定するまでの暫定値）。
  - `source` / `noise` / `register` の残りのフィールドは 1:1 対応。
  - v0 の `SourceParams` のうち `reference_midi` / `reference_intensity` /
    `tilt_pitch_sensitivity_db_per_oct_per_oct` /
    `tilt_intensity_sensitivity_db_per_oct` / `source_mode_tilt_gain` /
    `n_harmonics` / `extra_decay_*`、`ResonanceParams` の
    `f1_tracking_*` は P1 スキーマに含まれないため v0 既定値のまま凍結する
    （§C-3 が対象とする R0.1 レンダラの実装詳細）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np

from genome import REGISTER_ORDER, VoiceGenome

_VT_HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
if str(_VT_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_VT_HARNESS_DIR))

import voice_r0 as _vr0  # noqa: E402  (vt_harness、変更禁止・import 流用のみ)
import voice_r0_1 as _vr1  # noqa: E402  (同上)

SR = _vr1.SR
NOTE_DUR_SEC = _vr1.NOTE_DUR_SEC

JITTER_BRIDGE_SCALE = 30.0  # underspec_log_p1.md [UNDERSPEC-P1-3]


def to_render_genome(genome: VoiceGenome) -> _vr0.VoiceGenome:
    """VoiceGenome v0.2 → voice_r0.VoiceGenome（R0.1 レンダラが受け取れる形）へ変換する。"""
    source = _vr0.SourceParams(
        base_tilt_db_per_oct=genome.source.tilt,
        source_mode=genome.source.source_mode,
    )

    defaults = _vr0.ResonanceParams()
    scaled_formants = tuple(
        base_hz * genome.resonance.formant_scale * (1.0 + offset)
        for base_hz, offset in zip(defaults.base_formants_hz, genome.resonance.formant_offsets)
    )
    scaled_bandwidths = tuple(bw * genome.resonance.bandwidth_scale for bw in defaults.bandwidths_hz)
    resonance = _vr0.ResonanceParams(
        formant_scale=1.0,  # スケールは base_formants_hz に織り込み済み（二重適用防止）
        base_formants_hz=scaled_formants,
        bandwidths_hz=scaled_bandwidths,
    )

    noise = _vr0.NoiseParams(
        base_breathiness=genome.noise.breathiness_base,
        register_gain=dict(zip(REGISTER_ORDER, genome.noise.register_gains)),
    )

    b0, b1, b2, b3 = genome.register.boundaries_midi
    register = _vr0.RegisterParams(
        chest_to_mix_midi=b0,
        mix_to_head_midi=b1,
        head_to_falsetto_midi=b2,
        falsetto_to_whistle_midi=b3,
        transition_width=genome.register.transition_width,
    )

    microprosody = _vr0.MicroprosodyParams(
        vibrato_rate_hz=genome.microprosody.vibrato_rate_hz,
        vibrato_depth_cents=genome.microprosody.vibrato_depth_cents,
        jitter_pct_of_period=genome.microprosody.jitter_amount * JITTER_BRIDGE_SCALE,
        jitter_seed=genome.microprosody.jitter_seed,
    )

    range_ = _vr0.RangeParams(midi_min=genome.range.lowest_midi, midi_max=genome.range.highest_midi)

    return _vr0.VoiceGenome(
        name=genome.name,
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range=range_,
    )


def render_note(
    genome: VoiceGenome,
    midi: float,
    intensity: float = 0.7,
    duration_sec: Optional[float] = None,
    sr: Optional[int] = None,
) -> np.ndarray:
    """VoiceGenome v0.2 を R0.1 (`voice_r0_1.render_note`) でレンダリングする。"""
    render_genome = to_render_genome(genome)
    kwargs = {}
    if duration_sec is not None:
        kwargs["duration_sec"] = duration_sec
    if sr is not None:
        kwargs["sr"] = sr
    return _vr1.render_note(render_genome, midi, intensity=intensity, **kwargs)
