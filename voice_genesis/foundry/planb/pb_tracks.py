"""planb/pb_tracks.py — Identity Track / Performance Track の値オブジェクト。

プラン §4「分離の実装境界」を**型で**表現する。

    Identity Track  : spectral envelope / aperiodicity / VCV 遷移 / 母音・子音テクスチャ
    Performance Track: duration / F0 contour / vibrato / portamento / energy / terminal release

Performance 側は「**2 次元（時間 × 周波数）の配列を 1 つも持たない**」ことを
不変条件とする（`assert_no_spectral_payload`）。これはプラン §7.1
「PJS の声を混ぜただけでは成功扱いしない」を、耳ではなく型で担保するための
構造ゲートである。energy はフレームごとの**スカラ利得**であり、スペクトル
形状ではない（rank-1）。

Identity 側は逆に sp/ap（2 次元）を持つが、f0 は「音域の基準」としてのみ
保持し、合成の F0 は常に `pb_compose` が再構築する（Performance 側の
逸脱量を identity の音域へ載せ替えるため）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from typing import Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 単位（unit）= VCV / モーラ相当の 1 区間
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Unit:
    """1 単位の時間区画とラベル。

    `core_*` は母音コア（定常部）の区間で、伸縮時に「頭（子音・遷移）と尾は
    実尺、コアだけ伸ばす」ための境界として使う（unit-selection の慣行）。
    """

    label: str            # 例 "ri", "su", "N"
    onset: Optional[str]  # 子音（無ければ None）
    vowel: str            # a/i/u/e/o/N
    start_s: float
    end_s: float
    core_start_s: float
    core_end_s: float
    is_terminal: bool     # フレーズ末（terminal release の対象）

    @property
    def duration_s(self) -> float:
        return float(self.end_s - self.start_s)


# ---------------------------------------------------------------------------
# Identity Track
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IdentityBank:
    """Identity 側の供給物。**sp/ap のみ**が合成へ渡る。"""

    source_id: str
    sr: int
    frame_period_ms: float
    units: Tuple[Unit, ...]
    sp: Tuple[np.ndarray, ...]        # 各 (n_i, F)
    ap: Tuple[np.ndarray, ...]        # 各 (n_i, F)
    f0: Tuple[np.ndarray, ...]        # 各 (n_i,)  音域基準専用（転写しない）
    core_slices: Tuple[Tuple[int, int], ...]  # 各 unit 内のコアフレーム範囲
    nasal_envelope: Optional[np.ndarray]      # (F,) identity 自身の鼻音包絡（実測 or None）
    nasal_envelope_source: str                # "measured:<label>" / "absent"

    @property
    def n_units(self) -> int:
        return len(self.units)

    def note_target_hz(self, i: int) -> float:
        """unit i の音高基準 = コア区間の有声フレーム中央値（Hz）。"""
        lo, hi = self.core_slices[i]
        f0 = self.f0[i][lo:hi]
        voiced = f0[f0 > 0.0]
        if voiced.size == 0:
            voiced = self.f0[i][self.f0[i] > 0.0]
        if voiced.size == 0:
            return 0.0
        return float(np.median(voiced))

    def core_mean_sp(self, i: int) -> np.ndarray:
        lo, hi = self.core_slices[i]
        return np.mean(self.sp[i][lo:hi], axis=0)

    def content_sha256(self) -> str:
        h = hashlib.sha256()
        h.update(self.source_id.encode("utf-8"))
        h.update(np.asarray([self.sr, self.frame_period_ms], dtype=np.float64).tobytes())
        for u, sp, ap, f0 in zip(self.units, self.sp, self.ap, self.f0):
            h.update(u.label.encode("utf-8"))
            h.update(np.ascontiguousarray(sp, dtype=np.float64).tobytes())
            h.update(np.ascontiguousarray(ap, dtype=np.float64).tobytes())
            h.update(np.ascontiguousarray(f0, dtype=np.float64).tobytes())
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Performance Track
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReleaseSpec:
    """terminal release の制御記述（スカラ + 1 次元のみ）。"""

    window_frac: float        # 終端ユニット長に対する release 窓の比率 [0,1]
    taper_db: np.ndarray      # (R,) 正規化利得カーブ（dB、窓頭で 0dB）
    hold_core: bool           # True = release 窓でコア包絡を保持（drift させない）


@dataclass(frozen=True)
class PerformanceTrack:
    """Performance 側の供給物。

    **不変条件**: ここに現れる ndarray は全て 1 次元。2 次元（スペクトル）を
    持たないことを `assert_no_spectral_payload` が機械検査する。
    """

    source_id: str
    # 音高: note target からの逸脱のみ（絶対音域は identity 側が決める）
    f0_dev_cents: np.ndarray      # (T,)  ユニット正規化時間軸上の逸脱 [cent]
    f0_dev_unit_index: np.ndarray  # (T,) 各サンプルが属する unit index
    f0_dev_unit_pos: np.ndarray   # (T,)  unit 内正規化位置 [0,1]
    # 時間: unit ごとの尺
    unit_durations_s: np.ndarray  # (U,)
    # 強度: フレームごとのスカラ利得（dB, unit 内で平均 0 に正規化）
    energy_db: np.ndarray         # (T,)
    energy_unit_index: np.ndarray  # (T,)
    energy_unit_pos: np.ndarray   # (T,)
    # 終端処理
    release: ReleaseSpec

    @property
    def n_units(self) -> int:
        return int(self.unit_durations_s.shape[0])


_ALLOWED_PERFORMANCE_FIELDS = frozenset(
    {f.name for f in fields(PerformanceTrack)} | {"n_units"}
)


def assert_no_spectral_payload(track: PerformanceTrack) -> None:
    """構造ゲート G2: Performance Track に 2 次元配列が無いことを検査する。

    プラン受け入れ条件 6「PJS spectral texture を使わずに改善を再現」を、
    「そもそもテクスチャを運べる器が存在しない」という形で機械保証する。
    """
    def _check(name: str, value: object) -> None:
        if isinstance(value, np.ndarray):
            if value.ndim != 1:
                raise ValueError(
                    f"PerformanceTrack.{name}: ndim={value.ndim} "
                    "(spectral payload は禁止。1 次元制御系列のみ)"
                )
        elif isinstance(value, ReleaseSpec):
            for sub in ("taper_db",):
                _check(f"{name}.{sub}", getattr(value, sub))
        elif isinstance(value, (list, tuple)):
            for k, v in enumerate(value):
                _check(f"{name}[{k}]", v)

    for f in fields(track):
        _check(f.name, getattr(track, f.name))


def performance_field_names() -> Tuple[str, ...]:
    return tuple(sorted(_ALLOWED_PERFORMANCE_FIELDS))


# ---------------------------------------------------------------------------
# 成分トグル（プラン §4「各成分は独立に ON/OFF できるようにする」）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PerformanceToggles:
    f0: bool = False
    duration: bool = False
    energy: bool = False
    release: bool = False

    @property
    def label(self) -> str:
        on = [n for n in ("f0", "duration", "energy", "release") if getattr(self, n)]
        return "+".join(on) if on else "none"

    def any_on(self) -> bool:
        return self.f0 or self.duration or self.energy or self.release


# プラン §3 の R0–R4 ラダー
LADDER: Tuple[Tuple[str, PerformanceToggles], ...] = (
    ("R0", PerformanceToggles()),
    ("R1", PerformanceToggles(f0=True)),
    ("R2", PerformanceToggles(duration=True)),
    ("R3", PerformanceToggles(f0=True, duration=True)),
    ("R4", PerformanceToggles(f0=True, duration=True, energy=True, release=True)),
)

# プラン §7.2「F0 / duration / energy / release を別々に交換し、どれが効いたか
# 保持する」の要求は R0–R4 だけでは満たせない（R4 が energy と release を
# 同時に入れるため）。単独成分の効果を分離するための補助段。事前登録ゲートの
# 対象外（帰属の読み取り専用）。
LADDER_SUPPLEMENT: Tuple[Tuple[str, PerformanceToggles], ...] = (
    ("S1", PerformanceToggles(energy=True)),
    ("S2", PerformanceToggles(release=True)),
    ("S3", PerformanceToggles(energy=True, release=True)),
)


def units_from_boundaries(
    labels: Sequence[Tuple[str, Optional[str], str]],
    boundaries_s: Sequence[Tuple[float, float]],
    *,
    core_frac: Tuple[float, float] = (0.35, 0.85),
    terminal_flags: Optional[Sequence[bool]] = None,
) -> Tuple[Unit, ...]:
    """(label, onset, vowel) 列 + 区間境界 -> Unit 列。

    実資産経路（PJS / リツの lab ファイル）でも同じ関数で Unit を作れるよう、
    境界の出所に依存しないシグネチャにしてある。`core_frac` は子音・遷移を
    避けたコア推定の既定値（lab に音素境界がある場合は呼び出し側で厳密な
    値を渡す）。
    """
    if len(labels) != len(boundaries_s):
        raise ValueError("labels と boundaries_s の長さが一致しない")
    flags = list(terminal_flags) if terminal_flags is not None else [False] * len(labels)
    if len(flags) != len(labels):
        raise ValueError("terminal_flags の長さが一致しない")
    out = []
    for (label, onset, vowel), (s, e), term in zip(labels, boundaries_s, flags):
        dur = e - s
        out.append(Unit(
            label=label, onset=onset, vowel=vowel,
            start_s=float(s), end_s=float(e),
            core_start_s=float(s + core_frac[0] * dur),
            core_end_s=float(s + core_frac[1] * dur),
            is_terminal=bool(term),
        ))
    return tuple(out)
