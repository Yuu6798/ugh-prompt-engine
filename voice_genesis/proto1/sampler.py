"""sampler.py — P3 (VG-009): Genome sampler / mutation。

`sample(seed)`: 物理事前分布内の一様サンプル（決定論）。
`mutate(genome, seed, scale)`: ガウス摂動 + 事前分布境界でのフラグ更新
  （摂動が事前分布を外れても拒否せず `out_of_physio_range` を更新するのみ）。
`crossover(a, b, seed)`: フィールド単位（=トップレベルの各リーフフィールド
  丸ごと）ランダム継承。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from genome import (
    PHYSIO_PRIOR_RANGES,
    REGISTER_BOUNDARY_RANGE,
    MicroprosodySection,
    NoiseSection,
    RangeSection,
    RegisterSection,
    ResonanceSection,
    SourceSection,
    VoiceGenome,
    build_genome,
)

# sample() が一様分布から引く際の事前分布区間。凍結表 (PHYSIO_PRIOR_RANGES) に
# 無いフィールド（formant_offsets, bandwidth_scale, source_mode, jitter_seed,
# register_gains, range）は本モジュールが独自に「サンプル用の妥当域」を定義する
# （out_of_physio_range 判定には使われない。あくまでサンプラーの都合）。
_FORMANT_OFFSET_SAMPLE_RANGE: Tuple[float, float] = (-0.10, 0.10)
_BANDWIDTH_SCALE_SAMPLE_RANGE: Tuple[float, float] = (0.85, 1.20)
_REGISTER_GAIN_SAMPLE_RANGE: Tuple[float, float] = (0.0, 0.8)
_SOURCE_MODES: Tuple[str, ...] = ("modal", "breathy", "pressed")
_REGISTER_BOUNDARY_MIN_GAP: float = 4.0  # 隣接境界の最小間隔（半音）


def _sample_ascending_boundaries(rng: np.random.Generator) -> Tuple[float, float, float, float]:
    """[40, 96] の中で昇順 4 点を、隣接間隔 >= _REGISTER_BOUNDARY_MIN_GAP でサンプルする。

    区間 [lo, hi] を「境界前の余白 e0 / 境界間 3 区間（各 min_gap + e_i）/ 境界後の
    余白 e4」の 5 スロットに分割する（隣接差の下限は b0-b1, b1-b2, b2-b3 の 3 箇所
    のみに課される。lo→b0 と b3→hi には下限を課さない）。d = hi-lo-3*min_gap を
    5 スロットへ Dirichlet 的に配分し、e0..e3 を使って b0..b3 を積み上げる。
    e4（余り）は使わないことで b3 <= hi が数学的に保証される
    （b3 = lo + e0+e1+e2+e3 + 3*min_gap <= lo + d + 3*min_gap = hi）。
    """
    lo, hi = REGISTER_BOUNDARY_RANGE
    d = hi - lo - 3 * _REGISTER_BOUNDARY_MIN_GAP
    if d <= 0:
        # 事前分布が狭すぎて等間隔しか取れない場合のフォールバック（本表では発生しない）。
        step = (hi - lo) / 5.0
        return tuple(lo + step * (i + 1) for i in range(4))  # type: ignore[return-value]
    parts = rng.uniform(0.0, 1.0, size=5)
    parts = parts / parts.sum() * d
    b0 = lo + parts[0]
    b1 = b0 + _REGISTER_BOUNDARY_MIN_GAP + parts[1]
    b2 = b1 + _REGISTER_BOUNDARY_MIN_GAP + parts[2]
    b3 = b2 + _REGISTER_BOUNDARY_MIN_GAP + parts[3]
    return (float(b0), float(b1), float(b2), float(b3))


def sample(seed: int, name: Optional[str] = None) -> VoiceGenome:
    """物理事前分布内の一様サンプルで新しい VoiceGenome を作る（決定論: 同一 seed → 同一 Genome）。"""
    rng = np.random.default_rng(seed)
    genome_name = name if name is not None else f"sampled-{seed}"

    tilt_lo, tilt_hi = PHYSIO_PRIOR_RANGES["source.tilt"]
    source = SourceSection(
        tilt=float(rng.uniform(tilt_lo, tilt_hi)),
        source_mode=str(rng.choice(_SOURCE_MODES)),
    )

    fs_lo, fs_hi = PHYSIO_PRIOR_RANGES["resonance.formant_scale"]
    resonance = ResonanceSection(
        formant_scale=float(rng.uniform(fs_lo, fs_hi)),
        formant_offsets=tuple(
            float(v) for v in rng.uniform(*_FORMANT_OFFSET_SAMPLE_RANGE, size=4)
        ),  # type: ignore[arg-type]
        bandwidth_scale=float(rng.uniform(*_BANDWIDTH_SCALE_SAMPLE_RANGE)),
    )

    breath_lo, breath_hi = PHYSIO_PRIOR_RANGES["noise.breathiness_base"]
    noise = NoiseSection(
        breathiness_base=float(rng.uniform(breath_lo, breath_hi)),
        register_gains=tuple(
            float(v) for v in rng.uniform(*_REGISTER_GAIN_SAMPLE_RANGE, size=5)
        ),  # type: ignore[arg-type]
    )

    tw_lo, tw_hi = PHYSIO_PRIOR_RANGES["register.transition_width"]
    register = RegisterSection(
        boundaries_midi=_sample_ascending_boundaries(rng),
        transition_width=float(rng.uniform(tw_lo, tw_hi)),
    )

    vr_lo, vr_hi = PHYSIO_PRIOR_RANGES["microprosody.vibrato_rate_hz"]
    vd_lo, vd_hi = PHYSIO_PRIOR_RANGES["microprosody.vibrato_depth_cents"]
    ja_lo, ja_hi = PHYSIO_PRIOR_RANGES["microprosody.jitter_amount"]
    microprosody = MicroprosodySection(
        vibrato_rate_hz=float(rng.uniform(vr_lo, vr_hi)),
        vibrato_depth_cents=float(rng.uniform(vd_lo, vd_hi)),
        jitter_amount=float(rng.uniform(ja_lo, ja_hi)),
        jitter_seed=int(rng.integers(0, 2**31 - 1)),
    )

    range_ = RangeSection()  # sample() は range を凍結表対象外として既定値のまま据え置く

    return build_genome(
        genome_name,
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range_=range_,
    )


# ---------------------------------------------------------------------------
# mutate
# ---------------------------------------------------------------------------

# ガウス摂動の標準偏差 = scale * (フィールドの「典型的な広がり」)。
# 凍結表があるフィールドは区間幅 * scale、無いフィールドはサンプラー範囲幅 * scale。
_MUTATE_SPREAD = {
    "source.tilt": PHYSIO_PRIOR_RANGES["source.tilt"][1] - PHYSIO_PRIOR_RANGES["source.tilt"][0],
    "resonance.formant_scale": PHYSIO_PRIOR_RANGES["resonance.formant_scale"][1]
    - PHYSIO_PRIOR_RANGES["resonance.formant_scale"][0],
    "resonance.formant_offsets": _FORMANT_OFFSET_SAMPLE_RANGE[1] - _FORMANT_OFFSET_SAMPLE_RANGE[0],
    "resonance.bandwidth_scale": _BANDWIDTH_SCALE_SAMPLE_RANGE[1] - _BANDWIDTH_SCALE_SAMPLE_RANGE[0],
    "noise.breathiness_base": PHYSIO_PRIOR_RANGES["noise.breathiness_base"][1]
    - PHYSIO_PRIOR_RANGES["noise.breathiness_base"][0],
    "noise.register_gains": _REGISTER_GAIN_SAMPLE_RANGE[1] - _REGISTER_GAIN_SAMPLE_RANGE[0],
    "register.boundaries_midi": 6.0,  # 半音。境界を大きく動かしすぎないための固定スプレッド
    "register.transition_width": PHYSIO_PRIOR_RANGES["register.transition_width"][1]
    - PHYSIO_PRIOR_RANGES["register.transition_width"][0],
    "microprosody.vibrato_rate_hz": PHYSIO_PRIOR_RANGES["microprosody.vibrato_rate_hz"][1]
    - PHYSIO_PRIOR_RANGES["microprosody.vibrato_rate_hz"][0],
    "microprosody.vibrato_depth_cents": PHYSIO_PRIOR_RANGES["microprosody.vibrato_depth_cents"][1]
    - PHYSIO_PRIOR_RANGES["microprosody.vibrato_depth_cents"][0],
    "microprosody.jitter_amount": PHYSIO_PRIOR_RANGES["microprosody.jitter_amount"][1]
    - PHYSIO_PRIOR_RANGES["microprosody.jitter_amount"][0],
}


def mutate(genome: VoiceGenome, seed: int, scale: float = 0.1) -> VoiceGenome:
    """ガウス摂動 + 事前分布境界でのフラグ更新（範囲外への逸脱は拒否せずフラグ化する）。"""
    rng = np.random.default_rng(seed)

    def _perturb(value: float, key: str) -> float:
        return float(value + rng.normal(0.0, scale * _MUTATE_SPREAD[key]))

    def _perturb_tuple(values: Tuple[float, ...], key: str) -> Tuple[float, ...]:
        spread = scale * _MUTATE_SPREAD[key]
        return tuple(float(v + rng.normal(0.0, spread)) for v in values)

    source = SourceSection(
        tilt=_perturb(genome.source.tilt, "source.tilt"),
        source_mode=genome.source.source_mode,
    )
    resonance = ResonanceSection(
        formant_scale=_perturb(genome.resonance.formant_scale, "resonance.formant_scale"),
        formant_offsets=_perturb_tuple(genome.resonance.formant_offsets, "resonance.formant_offsets"),
        bandwidth_scale=_perturb(genome.resonance.bandwidth_scale, "resonance.bandwidth_scale"),
    )
    noise = NoiseSection(
        breathiness_base=_perturb(genome.noise.breathiness_base, "noise.breathiness_base"),
        register_gains=tuple(
            max(0.0, v) for v in _perturb_tuple(genome.noise.register_gains, "noise.register_gains")
        ),  # type: ignore[arg-type]
    )
    new_boundaries = _perturb_tuple(genome.register.boundaries_midi, "register.boundaries_midi")
    # 昇順を壊さないよう、摂動後にソートする（順序自体が壊れる摂動は crossover の方で扱う）。
    new_boundaries = tuple(sorted(new_boundaries))
    register = RegisterSection(
        boundaries_midi=new_boundaries,  # type: ignore[arg-type]
        transition_width=_perturb(genome.register.transition_width, "register.transition_width"),
    )
    microprosody = MicroprosodySection(
        vibrato_rate_hz=_perturb(genome.microprosody.vibrato_rate_hz, "microprosody.vibrato_rate_hz"),
        vibrato_depth_cents=max(
            0.0, _perturb(genome.microprosody.vibrato_depth_cents, "microprosody.vibrato_depth_cents")
        ),
        jitter_amount=max(0.0, _perturb(genome.microprosody.jitter_amount, "microprosody.jitter_amount")),
        jitter_seed=genome.microprosody.jitter_seed,
    )

    return build_genome(
        f"{genome.name}-mut{seed}",
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range_=genome.range,
        audit=genome.audit,
    )


# ---------------------------------------------------------------------------
# crossover
# ---------------------------------------------------------------------------


def crossover(a: VoiceGenome, b: VoiceGenome, seed: int) -> VoiceGenome:
    """フィールド単位（トップレベルの各リーフフィールド丸ごと）でランダムに a/b から継承する。"""
    rng = np.random.default_rng(seed)

    def _pick(x, y):
        return x if rng.uniform() < 0.5 else y

    source = SourceSection(
        tilt=_pick(a.source.tilt, b.source.tilt),
        source_mode=_pick(a.source.source_mode, b.source.source_mode),
    )
    resonance = ResonanceSection(
        formant_scale=_pick(a.resonance.formant_scale, b.resonance.formant_scale),
        formant_offsets=_pick(a.resonance.formant_offsets, b.resonance.formant_offsets),
        bandwidth_scale=_pick(a.resonance.bandwidth_scale, b.resonance.bandwidth_scale),
    )
    noise = NoiseSection(
        breathiness_base=_pick(a.noise.breathiness_base, b.noise.breathiness_base),
        register_gains=_pick(a.noise.register_gains, b.noise.register_gains),
    )
    register = RegisterSection(
        boundaries_midi=_pick(a.register.boundaries_midi, b.register.boundaries_midi),
        transition_width=_pick(a.register.transition_width, b.register.transition_width),
    )
    microprosody = MicroprosodySection(
        vibrato_rate_hz=_pick(a.microprosody.vibrato_rate_hz, b.microprosody.vibrato_rate_hz),
        vibrato_depth_cents=_pick(a.microprosody.vibrato_depth_cents, b.microprosody.vibrato_depth_cents),
        jitter_amount=_pick(a.microprosody.jitter_amount, b.microprosody.jitter_amount),
        jitter_seed=_pick(a.microprosody.jitter_seed, b.microprosody.jitter_seed),
    )
    range_ = RangeSection(
        lowest_midi=_pick(a.range.lowest_midi, b.range.lowest_midi),
        highest_midi=_pick(a.range.highest_midi, b.range.highest_midi),
    )

    return build_genome(
        f"{a.name}x{b.name}-{seed}",
        source=source,
        resonance=resonance,
        noise=noise,
        register=register,
        microprosody=microprosody,
        range_=range_,
    )
