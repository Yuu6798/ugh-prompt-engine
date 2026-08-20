"""planb/pb_compose.py — Identity × Performance の合成器（プラン §4 の実装）。

合成器が触れる入力は 2 つだけ:

- `IdentityBank`   : sp / ap（テクスチャ）
- `PerformanceTrack`: f0 逸脱 / 尺 / energy / release（**1 次元の制御のみ**）

Performance 側から 2 次元配列が入る経路は存在しない（`pb_tracks` の
構造ゲートと `pb_gates` の tripwire が機械検査する）。

決定論契約: 同一入力 + 同一トグル -> 同一 float64 サンプル列（乱数不使用）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

import pb_world as w
from pb_tracks import IdentityBank, PerformanceToggles, PerformanceTrack

_EPS = 1e-12
TRANSITION_MS = 40.0
ENERGY_SMOOTH_FRAMES = 9
ENERGY_GAIN_CLAMP_DB = 12.0


@dataclass(frozen=True)
class ComposeResult:
    wav: np.ndarray
    sr: int
    frame_period_ms: float
    f0: np.ndarray
    sp: np.ndarray
    ap: np.ndarray
    unit_frames: Tuple[Tuple[int, int], ...]       # 出力タイムライン上の unit フレーム範囲
    unit_core_frames: Tuple[Tuple[int, int], ...]  # 同、コア範囲
    toggles_label: str

    def sha256(self) -> str:
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.wav, dtype=np.float64).tobytes())
        return h.hexdigest()


# ---------------------------------------------------------------------------
# コア保存型タイムワープ（頭と尾は実尺、母音コアだけ伸縮）
# ---------------------------------------------------------------------------
def warp_unit(
    arr: np.ndarray, core: Tuple[int, int], n_target: int, *, log_domain: bool = False,
) -> Tuple[np.ndarray, Tuple[int, int]]:
    """(n, ...) 配列を n_target フレームへ伸縮する。頭 [0,c0) と尾 [c1,n) は実尺。

    unit-selection の慣行どおり、子音・VCV 遷移（頭）と終端処理（尾）の
    絶対長を保ち、定常母音コアだけを伸縮する。伸縮が頭尾の実尺を確保
    できないほど短い場合は全体を線形圧縮する（縮退時のフォールバック）。

    戻り値は (warped, 新しいコア範囲)。
    """
    n = int(arr.shape[0])
    c0, c1 = int(core[0]), int(core[1])
    c0 = max(0, min(c0, n - 1))
    c1 = max(c0 + 1, min(c1, n))
    head, tail = c0, n - c1
    n_target = max(2, int(n_target))
    if head + tail + 1 > n_target:
        return _resample_axis0(arr, n_target, log_domain=log_domain), (0, n_target)
    core_target = n_target - head - tail
    parts = []
    if head > 0:
        parts.append(arr[:c0])
    parts.append(_resample_axis0(arr[c0:c1], core_target, log_domain=log_domain))
    if tail > 0:
        parts.append(arr[c1:])
    out = np.concatenate(parts, axis=0)
    return out, (head, head + core_target)


def _resample_axis0(arr: np.ndarray, n_target: int, *, log_domain: bool) -> np.ndarray:
    n = int(arr.shape[0])
    if n == n_target:
        return np.array(arr, dtype=np.float64)
    src = np.linspace(0.0, 1.0, n)
    dst = np.linspace(0.0, 1.0, n_target)
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 1:
        if log_domain:
            return np.exp(np.interp(dst, src, np.log(np.maximum(a, _EPS))))
        return np.interp(dst, src, a)
    flat = a.reshape(n, -1)
    if log_domain:
        flat = np.log(np.maximum(flat, _EPS))
    out = np.empty((n_target, flat.shape[1]), dtype=np.float64)
    for j in range(flat.shape[1]):
        out[:, j] = np.interp(dst, src, flat[:, j])
    if log_domain:
        out = np.exp(out)
    return out.reshape((n_target,) + a.shape[1:])


def _warp_voicing(f0: np.ndarray, core: Tuple[int, int], n_target: int) -> np.ndarray:
    """有声/無声マスクを最近傍で伸縮する（補間で偽の有声を作らない）。"""
    voiced = (np.asarray(f0) > 0.0).astype(np.float64)
    n = voiced.shape[0]
    c0, c1 = core
    head, tail = c0, n - c1
    if head + tail + 1 > n_target:
        idx = np.clip(np.round(np.linspace(0, n - 1, n_target)).astype(int), 0, n - 1)
        return voiced[idx] > 0.5
    core_target = n_target - head - tail
    core_src = voiced[c0:c1]
    idx = np.clip(np.round(np.linspace(0, core_src.shape[0] - 1, core_target)).astype(int),
                  0, max(0, core_src.shape[0] - 1))
    return np.concatenate([voiced[:c0], core_src[idx], voiced[c1:]]) > 0.5


# ---------------------------------------------------------------------------
# 合成本体
# ---------------------------------------------------------------------------
def compose(
    identity: IdentityBank,
    performance: Optional[PerformanceTrack],
    toggles: PerformanceToggles,
    *,
    transition_ms: float = TRANSITION_MS,
) -> ComposeResult:
    """Identity テクスチャ + Performance 制御 -> 波形。"""
    if toggles.any_on() and performance is None:
        raise ValueError("トグルが ON だが PerformanceTrack が無い")
    if performance is not None and performance.n_units != identity.n_units:
        raise ValueError(
            f"unit 数不一致: identity={identity.n_units} performance={performance.n_units}"
        )

    fp = identity.frame_period_ms / 1000.0
    sp_parts: List[np.ndarray] = []
    ap_parts: List[np.ndarray] = []
    f0_parts: List[np.ndarray] = []
    unit_frames: List[Tuple[int, int]] = []
    unit_core: List[Tuple[int, int]] = []
    cursor = 0

    for i, unit in enumerate(identity.units):
        if toggles.duration and performance is not None:
            dur = float(performance.unit_durations_s[i])
        else:
            dur = unit.duration_s
        n_target = max(2, int(round(dur / fp)))

        sp_i, core_i = warp_unit(identity.sp[i], identity.core_slices[i], n_target, log_domain=True)
        ap_i, _ = warp_unit(identity.ap[i], identity.core_slices[i], n_target)
        voiced = _warp_voicing(identity.f0[i], identity.core_slices[i], n_target)

        # --- F0: identity の音域基準（note target）を土台にする ---
        target_hz = identity.note_target_hz(i)
        f0_i = np.where(voiced, target_hz, 0.0)

        # --- F0 逸脱（vibrato / portamento / 立ち上がり）の移植 ---
        if toggles.f0 and performance is not None:
            cents = _interp_unit_series(
                performance.f0_dev_cents, performance.f0_dev_unit_index,
                performance.f0_dev_unit_pos, i, n_target,
            )
            f0_i = np.where(voiced, f0_i * np.power(2.0, cents / 1200.0), 0.0)

        # --- energy: identity 自身の unit 内エンベロープを reference の形へ**置換**する
        #     （加算ではない。加算だと二重計上で発散する = 初回実測で確認）。
        #     利得はフレームごとのスカラであり、スペクトル形状は identity のまま。
        if toggles.energy and performance is not None and bool(np.any(voiced)):
            e_db = _interp_unit_series(
                performance.energy_db, performance.energy_unit_index,
                performance.energy_unit_pos, i, n_target,
            )
            own_db = 10.0 * np.log10(np.maximum(np.sum(sp_i, axis=1), _EPS))
            own_db = _smooth1d(own_db, ENERGY_SMOOTH_FRAMES)
            own_db = own_db - float(np.mean(own_db))
            gain_db = np.clip(e_db - own_db, -ENERGY_GAIN_CLAMP_DB, ENERGY_GAIN_CLAMP_DB)
            sp_i = sp_i * np.power(10.0, gain_db / 10.0)[:, None]

        # --- terminal release skill: コア保持 + taper ---
        if toggles.release and performance is not None and unit.is_terminal:
            sp_i, ap_i = _apply_release_skill(
                sp_i, ap_i, core_i, performance.release.window_frac,
                performance.release.taper_db, performance.release.hold_core,
            )

        sp_parts.append(sp_i)
        ap_parts.append(ap_i)
        f0_parts.append(f0_i)
        unit_frames.append((cursor, cursor + n_target))
        unit_core.append((cursor + core_i[0], cursor + core_i[1]))
        cursor += n_target

    sp = np.concatenate(sp_parts, axis=0)
    ap = np.concatenate(ap_parts, axis=0)
    f0 = np.concatenate(f0_parts, axis=0)
    f0 = _smooth_transitions(f0, int(round(transition_ms / identity.frame_period_ms)))

    wav = w.synthesize(f0, sp, ap, identity.sr, identity.frame_period_ms)
    return ComposeResult(
        wav=wav, sr=identity.sr, frame_period_ms=identity.frame_period_ms,
        f0=f0, sp=sp, ap=ap,
        unit_frames=tuple(unit_frames), unit_core_frames=tuple(unit_core),
        toggles_label=toggles.label,
    )


def _smooth1d(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return np.asarray(x, dtype=np.float64)
    k = np.ones(int(n), dtype=np.float64) / float(n)
    pad = int(n) // 2
    xp = np.pad(np.asarray(x, dtype=np.float64), (pad, pad), mode="edge")
    return np.convolve(xp, k, mode="valid")[: x.shape[0]]


def _interp_unit_series(
    values: np.ndarray, unit_index: np.ndarray, unit_pos: np.ndarray,
    i: int, n_target: int,
) -> np.ndarray:
    """Performance の 1 次元系列から unit i ぶんを取り出し n_target へ写す。"""
    mask = unit_index == i
    if not np.any(mask):
        return np.zeros(n_target, dtype=np.float64)
    src_pos = np.asarray(unit_pos[mask], dtype=np.float64)
    src_val = np.asarray(values[mask], dtype=np.float64)
    order = np.argsort(src_pos, kind="stable")
    src_pos, src_val = src_pos[order], src_val[order]
    dst = (np.arange(n_target, dtype=np.float64) + 0.5) / float(n_target)
    return np.interp(dst, src_pos, src_val)


def _apply_release_skill(
    sp: np.ndarray, ap: np.ndarray, core: Tuple[int, int],
    window_frac: float, taper_db: np.ndarray, hold_core: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    """終端 release 窓に「コア保持 + taper」を適用する。

    - **hold**: release 窓のスペクトル包絡 / 非周期性を **コア前半の平均**へ
      固定する。コア前半を参照にするのは「立ち上がり直後の母音の姿」であり、
      終端側の変質（り→ん破綻ならここが鼻音へ流れる）を参照に含めないため。
    - **taper**: Performance 側の 1 次元利得カーブを掛ける。

    テクスチャの供給源は **identity 自身のコア**のみ。Performance からは
    窓長（スカラ）と利得カーブ（1 次元）しか来ない。
    """
    n = sp.shape[0]
    r0 = n - max(2, int(round(n * float(window_frac))))
    r0 = max(0, min(r0, n - 2))
    out_sp = np.array(sp, dtype=np.float64)
    out_ap = np.array(ap, dtype=np.float64)

    if hold_core:
        c0, c1 = core
        h1 = c0 + max(1, (c1 - c0) // 2)
        hold_sp = np.mean(sp[c0:h1], axis=0)
        hold_ap = np.mean(ap[c0:h1], axis=0)
        # 窓頭で滑らかに接続する（クロスフェード 20% 区間）
        m = n - r0
        blend = np.clip(np.linspace(0.0, 1.0, m) / 0.2, 0.0, 1.0)[:, None]
        log_now = np.log(np.maximum(out_sp[r0:], _EPS))
        log_hold = np.log(np.maximum(hold_sp, _EPS))[None, :]
        out_sp[r0:] = np.exp(log_now * (1.0 - blend) + log_hold * blend)
        out_ap[r0:] = out_ap[r0:] * (1.0 - blend) + hold_ap[None, :] * blend

    m = n - r0
    taper = np.interp(np.linspace(0.0, 1.0, m),
                      np.linspace(0.0, 1.0, taper_db.shape[0]),
                      np.asarray(taper_db, dtype=np.float64))
    out_sp[r0:] = out_sp[r0:] * np.power(10.0, taper / 10.0)[:, None]
    return out_sp, out_ap


def _smooth_transitions(f0: np.ndarray, win: int) -> np.ndarray:
    """有声フレームの log-F0 のみ移動平均し、ノート境界のポルタメントを作る。"""
    if win <= 1:
        return np.asarray(f0, dtype=np.float64)
    f0 = np.asarray(f0, dtype=np.float64)
    voiced = f0 > 0.0
    if not np.any(voiced):
        return f0
    logf = np.zeros_like(f0)
    logf[voiced] = np.log(f0[voiced])
    # 無声区間は直近の有声値で埋めてから平滑（境界のジャンプを持ち込まない）
    idx = np.where(voiced, np.arange(f0.shape[0]), -1)
    np.maximum.accumulate(idx, out=idx)
    idx[idx < 0] = int(np.argmax(voiced))
    filled = logf[idx]
    k = np.ones(int(win), dtype=np.float64) / float(win)
    pad = int(win) // 2
    sm = np.convolve(np.pad(filled, (pad, pad), mode="edge"), k, mode="valid")[: f0.shape[0]]
    out = np.zeros_like(f0)
    out[voiced] = np.exp(sm[voiced])
    return out


def unit_frame_map(result: ComposeResult) -> Dict[int, Tuple[int, int]]:
    return {i: fr for i, fr in enumerate(result.unit_frames)}
