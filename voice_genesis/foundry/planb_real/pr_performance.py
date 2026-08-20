"""planb_real/pr_performance.py — 実歌唱からの Performance 抽出（instruction §4）。

instruction が名指しした 4 群を、**1 次元制御系列とスカラのみ**で表現する。

    timing   : note_duration / phoneme_duration / consonant_duration / vowel_duration
    pitch    : f0_contour / onset_glide / vibrato_rate / vibrato_depth / terminal_pitch_motion
    dynamics : energy_envelope / attack / sustain / terminal_decay
    release  : voiced_tail / terminal_decay_shape / release_to_silence / terminal_f0_persistence

禁止ペイロード（§4）: raw waveform / spectral envelope / mel texture /
speaker embedding / 高次元音響特徴の丸ごとコピー。
`assert_no_spectral_payload` が 2 次元以上の配列を型で拒否する。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import pr_lab

_EPS = 1e-12
#: unit 内の音高逸脱の外れ値しきい [cent]。
#: これを超えるフレームは歌い方ではなく f0 推定の倍/半周期誤りとみなし、
#: **clamp（上限で止める）ではなく除外**して 0（= ノート目標）へ戻す。
#: 実測（実コーパス第 2 走行）で、clamp は誤検出を「+700cent の意図的な跳ね」
#: として移植してしまい、合成側の終端 f0 が 1 オクターブ近く跳ねていた。
F0_DEV_OUTLIER_CENTS = 400.0


@dataclass(frozen=True)
class TimingSkill:
    note_duration_s: float
    phoneme_duration_s: Tuple[float, ...]
    phoneme_labels: Tuple[str, ...]
    consonant_duration_s: float
    vowel_duration_s: float

    @property
    def consonant_share(self) -> float:
        total = self.consonant_duration_s + self.vowel_duration_s
        return float(self.consonant_duration_s / total) if total > 0 else 0.0

    @property
    def vowel_share(self) -> float:
        return 1.0 - self.consonant_share


@dataclass(frozen=True)
class PitchSkill:
    f0_dev_cents: np.ndarray      # (T,) note target からの逸脱
    f0_dev_pos: np.ndarray        # (T,) unit 内正規化位置 [0,1]
    onset_glide_cents: float      # 立ち上がり 60ms の到達量
    vibrato_rate_hz: float
    vibrato_depth_cents: float
    terminal_pitch_motion_cents: float


@dataclass(frozen=True)
class DynamicsSkill:
    energy_db: np.ndarray         # (T,) unit 内平均 0 のスカラ利得
    energy_pos: np.ndarray        # (T,)
    attack_db_per_s: float
    sustain_db: float
    terminal_decay_db: float


@dataclass(frozen=True)
class ReleaseSkill:
    voiced_tail_s: float                # 終端の有声継続長
    terminal_decay_shape_db: np.ndarray  # (R,) 正規化利得カーブ
    release_to_silence_s: float          # 有声終了から無音開始まで
    terminal_f0_persistence: float       # release 窓の有声フレーム率 [0,1]
    window_frac: float = 0.35
    hold_core: bool = True


@dataclass(frozen=True)
class RealPerformanceTrack:
    """1 probe（= 1 終端ユニット）ぶんの Performance 記述。"""

    source_id: str
    source_file: str
    probe_kind: str
    timing: TimingSkill
    pitch: PitchSkill
    dynamics: DynamicsSkill
    release: ReleaseSkill

    def content_sha256(self) -> str:
        h = hashlib.sha256()
        h.update(f"{self.source_id}|{self.source_file}|{self.probe_kind}".encode("utf-8"))
        for arr in (self.pitch.f0_dev_cents, self.pitch.f0_dev_pos,
                    self.dynamics.energy_db, self.dynamics.energy_pos,
                    self.release.terminal_decay_shape_db):
            h.update(np.ascontiguousarray(arr, dtype=np.float64).tobytes())
        h.update(np.asarray([
            self.timing.note_duration_s, self.timing.consonant_duration_s,
            self.timing.vowel_duration_s, self.pitch.onset_glide_cents,
            self.pitch.vibrato_rate_hz, self.pitch.vibrato_depth_cents,
            self.pitch.terminal_pitch_motion_cents, self.dynamics.attack_db_per_s,
            self.dynamics.sustain_db, self.dynamics.terminal_decay_db,
            self.release.voiced_tail_s, self.release.release_to_silence_s,
            self.release.terminal_f0_persistence, self.release.window_frac,
            float(self.release.hold_core),   # 合成器が読む値なので digest に含める
        ], dtype=np.float64).tobytes())
        h.update(np.asarray(self.timing.phoneme_duration_s, dtype=np.float64).tobytes())
        h.update("|".join(self.timing.phoneme_labels).encode("utf-8"))
        return h.hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        def _ser(obj: Any) -> Any:
            if isinstance(obj, np.ndarray):
                return [round(float(x), 5) for x in obj]
            if isinstance(obj, tuple):
                return list(obj)
            return obj
        out: Dict[str, Any] = {"source_id": self.source_id, "source_file": self.source_file,
                               "probe_kind": self.probe_kind,
                               "content_sha256": self.content_sha256()}
        for name in ("timing", "pitch", "dynamics", "release"):
            out[name] = {k: _ser(v) for k, v in asdict(getattr(self, name)).items()}
        out["timing"]["consonant_share"] = round(self.timing.consonant_share, 5)
        out["timing"]["vowel_share"] = round(self.timing.vowel_share, 5)
        return out


def assert_no_spectral_payload(track: RealPerformanceTrack) -> None:
    """構造 tripwire（§4）: 2 次元以上の時系列テンソルを禁止する。"""
    def _check(prefix: str, obj: Any) -> None:
        for f in fields(obj):
            v = getattr(obj, f.name)
            if isinstance(v, np.ndarray) and v.ndim != 1:
                raise ValueError(
                    f"{prefix}.{f.name}: ndim={v.ndim}（Performance に spectral tensor は禁止）")
            if isinstance(v, (list, tuple)):
                for k, item in enumerate(v):
                    if isinstance(item, np.ndarray) and item.ndim != 0:
                        raise ValueError(f"{prefix}.{f.name}[{k}]: 配列要素は禁止")
    for name in ("timing", "pitch", "dynamics", "release"):
        _check(name, getattr(track, name))


# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------
def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return np.asarray(x, dtype=np.float64)
    k = np.ones(int(n), dtype=np.float64) / float(n)
    pad = int(n) // 2
    xp = np.pad(np.asarray(x, dtype=np.float64), (pad, pad), mode="edge")
    return np.convolve(xp, k, mode="valid")[: x.shape[0]]


def _vibrato(dev_cents: np.ndarray, frame_period_s: float) -> Tuple[float, float]:
    """逸脱系列から vibrato の周波数と深さを推定する（FFT ピーク・4–9Hz 帯）。"""
    x = np.asarray(dev_cents, dtype=np.float64)
    if x.size < 8:
        return 0.0, 0.0
    x = x - float(np.mean(x))
    win = np.hanning(x.size)
    spec = np.abs(np.fft.rfft(x * win))
    freqs = np.fft.rfftfreq(x.size, d=frame_period_s)
    band = (freqs >= 4.0) & (freqs <= 9.0)
    if not np.any(band) or float(np.max(spec[band])) <= 0.0:
        return 0.0, 0.0
    idx = int(np.argmax(np.where(band, spec, 0.0)))
    depth = float(2.0 * spec[idx] / max(np.sum(win), _EPS))
    return float(freqs[idx]), depth


def extract_performance(
    *, f0: np.ndarray, power_db: np.ndarray, frame_period_ms: float,
    phones: Sequence[pr_lab.Phone], vowel_index: int, source_id: str,
    source_file: str, probe_kind: str, release_window_frac: float = 0.35,
    taper_points: int = 24,
) -> RealPerformanceTrack:
    """f0 / パワー系列 + 音素境界から 1 probe ぶんの Performance を抽出する。

    `vowel_index` = probe の母音音素の index（terminal /ri/ なら /i/ の位置）。
    直前が子音ならそれを consonant として同じ「ノート」に含める。
    """
    fp = frame_period_ms / 1000.0
    vowel = phones[vowel_index]
    cons: Optional[pr_lab.Phone] = None
    if vowel_index > 0 and not phones[vowel_index - 1].is_silence:
        prev = phones[vowel_index - 1]
        if pr_lab.normalize_vowel(prev.label) is None:
            cons = prev

    note_start = cons.start_s if cons is not None else vowel.start_s
    note_end = vowel.end_s
    lo, hi = int(np.floor(note_start / fp)), int(np.ceil(note_end / fp))
    lo = max(0, min(lo, f0.shape[0] - 1))
    hi = max(lo + 2, min(hi, f0.shape[0]))
    n = hi - lo

    seg_f0 = np.asarray(f0[lo:hi], dtype=np.float64)
    seg_db = _smooth(np.asarray(power_db[lo:hi], dtype=np.float64), 9)
    pos = (np.arange(n, dtype=np.float64) + 0.5) / float(n)

    # --- pitch ---
    voiced = seg_f0 > 0.0
    core_lo, core_hi = int(0.35 * n), max(int(0.35 * n) + 1, int(0.85 * n))
    core_v = seg_f0[core_lo:core_hi][seg_f0[core_lo:core_hi] > 0.0]
    ref_hz = float(np.median(core_v)) if core_v.size else 0.0
    if ref_hz > 0.0:
        dev = np.where(voiced, 1200.0 * np.log2(np.maximum(seg_f0, _EPS) / ref_hz), 0.0)
        dev = np.where(np.abs(dev) > F0_DEV_OUTLIER_CENTS, 0.0, dev)
    else:
        dev = np.zeros(n, dtype=np.float64)
    onset_frames = max(1, int(round(0.060 / fp)))
    onset_glide = float(dev[min(onset_frames, n - 1)] - dev[0])
    vib_rate, vib_depth = _vibrato(dev[core_lo:core_hi], fp)
    r0 = n - max(2, int(round(n * release_window_frac)))
    terminal_motion = float(np.median(dev[r0:]) - (np.median(dev[core_lo:core_hi])
                                                   if core_hi > core_lo else 0.0))

    # --- dynamics ---
    energy = seg_db - float(np.mean(seg_db))
    attack_frames = max(2, int(round(0.080 / fp)))
    attack = float((energy[min(attack_frames, n - 1)] - energy[0]) / (attack_frames * fp))
    sustain = float(np.median(energy[core_lo:core_hi])) if core_hi > core_lo else 0.0
    terminal_decay = float(np.median(energy[r0:]) - sustain)

    # --- release ---
    tail_voiced = voiced[r0:]
    persistence = float(np.mean(tail_voiced)) if tail_voiced.size else 0.0
    voiced_idx = np.flatnonzero(voiced)
    voiced_end_frame = int(voiced_idx[-1]) if voiced_idx.size else n - 1
    voiced_tail_s = float((voiced_end_frame - r0 + 1) * fp) if voiced_end_frame >= r0 else 0.0
    nxt = phones[vowel_index + 1] if vowel_index + 1 < len(phones) else None
    if nxt is not None and nxt.is_silence:
        release_to_silence = float(max(0.0, nxt.start_s - (note_start + voiced_end_frame * fp)))
    else:
        release_to_silence = 0.0
    taper_src = energy[r0:] - float(energy[r0])
    taper = np.interp(np.linspace(0.0, 1.0, taper_points),
                      np.linspace(0.0, 1.0, max(2, taper_src.size)),
                      taper_src if taper_src.size >= 2 else np.zeros(2))

    timing = TimingSkill(
        note_duration_s=float(note_end - note_start),
        phoneme_duration_s=tuple(
            float(p.duration_s) for p in ([cons] if cons is not None else []) + [vowel]),
        phoneme_labels=tuple(
            p.label for p in ([cons] if cons is not None else []) + [vowel]),
        consonant_duration_s=float(cons.duration_s) if cons is not None else 0.0,
        vowel_duration_s=float(vowel.duration_s),
    )
    pitch = PitchSkill(f0_dev_cents=dev, f0_dev_pos=pos, onset_glide_cents=onset_glide,
                       vibrato_rate_hz=vib_rate, vibrato_depth_cents=vib_depth,
                       terminal_pitch_motion_cents=terminal_motion)
    dynamics = DynamicsSkill(energy_db=energy, energy_pos=pos, attack_db_per_s=attack,
                             sustain_db=sustain, terminal_decay_db=terminal_decay)
    release = ReleaseSkill(voiced_tail_s=voiced_tail_s, terminal_decay_shape_db=taper,
                           release_to_silence_s=release_to_silence,
                           terminal_f0_persistence=persistence,
                           window_frac=float(release_window_frac), hold_core=True)
    track = RealPerformanceTrack(source_id=source_id, source_file=source_file,
                                 probe_kind=probe_kind, timing=timing, pitch=pitch,
                                 dynamics=dynamics, release=release)
    assert_no_spectral_payload(track)
    return track


def performance_field_inventory() -> Dict[str, List[str]]:
    """§4 が要求するフィールドが実在することを機械的に示す（record 用）。"""
    return {name: [f.name for f in fields(cls)] for name, cls in (
        ("timing", TimingSkill), ("pitch", PitchSkill),
        ("dynamics", DynamicsSkill), ("release", ReleaseSkill))}
