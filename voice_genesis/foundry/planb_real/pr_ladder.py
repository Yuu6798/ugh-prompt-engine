"""planb_real/pr_ladder.py — 実コーパス R0–R4 + P0（instruction §7・§8）。

1 probe ペア（Ritsu 側の terminal /ri/ 等 × PJS 側の同種 probe）につき
6 条件を生成し、軸別に計測する。**WAV はリポジトリへ commit しない**ので、
記録に残すのは書き出したファイルのバイト列 sha256（§15）。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

_PLANB = Path(__file__).resolve().parent.parent / "planb"
if str(_PLANB) not in sys.path:
    sys.path.insert(0, str(_PLANB))

import pb_compose as pc  # noqa: E402
import pb_metrics as pm  # noqa: E402
import pb_world as pbw  # noqa: E402
from pb_tracks import IdentityBank, PerformanceToggles, PerformanceTrack  # noqa: E402

import pr_match  # noqa: E402
from pr_performance import RealPerformanceTrack  # noqa: E402

_EPS = 1e-12

#: instruction §7 のラダー。R4 は dynamics + release を同時に入れる。
LADDER: Tuple[Tuple[str, PerformanceToggles], ...] = (
    ("R0", PerformanceToggles()),
    ("R1", PerformanceToggles(f0=True)),
    ("R2", PerformanceToggles(duration=True)),
    ("R3", PerformanceToggles(f0=True, duration=True)),
    ("R4", PerformanceToggles(f0=True, duration=True, energy=True, release=True)),
)


def sha256_bytes_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class RungMeasurement:
    rung: str
    toggles: str
    wav_path: str
    wav_file_sha256: str          # 実際に書き出したバイト列（検証はこれで行う）
    samples_sha256: str           # float64 サンプル列（コンテナ非依存の決定論指標）
    duration_s: float
    trf: Dict[str, Any]
    identity_lsd_db: float
    donor_lsd_db: float
    f0_dev_rmse_cents: float
    note_split_mae_ms: float
    energy_corr: float
    taper_rmse_db: float
    # レベル正規化版（surrogate PoC で「生の距離は利得に交絡する」と実測済み。
    # 登録ゲートは生の版のまま、診断としてこちらも常に記録する）
    identity_shape_lsd_db: float = float("nan")
    donor_shape_lsd_db: float = float("nan")

    @property
    def identity_margin_db(self) -> float:
        return float(self.donor_lsd_db - self.identity_lsd_db)

    def as_dict(self) -> Dict[str, Any]:
        d = {k: (round(v, 4) if isinstance(v, float) else v)
             for k, v in self.__dict__.items()}
        d["identity_margin_db"] = round(self.identity_margin_db, 4)
        return d


@dataclass
class ProbePairResult:
    pair_key: str
    probe_kind: str
    ritsu_file: str
    pjs_file: str
    identity_sha256: str
    performance_sha256: str
    matching: Dict[str, Any]
    rungs: Dict[str, RungMeasurement] = field(default_factory=dict)
    p0_reference: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"pair_key": self.pair_key, "probe_kind": self.probe_kind,
                "ritsu_file": self.ritsu_file, "pjs_file": self.pjs_file,
                "identity_sha256": self.identity_sha256,
                "performance_sha256": self.performance_sha256,
                "matching": self.matching,
                "rungs": {k: v.as_dict() for k, v in self.rungs.items()},
                "P0_reference": self.p0_reference}


def _mean_sp(sp: np.ndarray, rng: Tuple[int, int]) -> np.ndarray:
    a, b = rng
    a = max(0, min(a, sp.shape[0] - 1))
    b = max(a + 1, min(b, sp.shape[0]))
    return np.mean(sp[a:b], axis=0)


def _shape(env: np.ndarray) -> np.ndarray:
    """総パワー 1 へ正規化（レベルを落として形状だけ残す）。"""
    e = np.asarray(env, dtype=np.float64)
    return e / max(float(np.sum(e)), _EPS)


def _core_window(fr: Tuple[int, int]) -> Tuple[int, int]:
    lo, hi = fr
    n = hi - lo
    return lo + int(0.35 * n), lo + max(int(0.35 * n) + 1, int(0.60 * n))


def _resample_to(a: np.ndarray, n: int) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    if a.size == n:
        return a
    return np.interp(np.linspace(0.0, 1.0, n), np.linspace(0.0, 1.0, max(2, a.size)),
                     a if a.size >= 2 else np.zeros(2))


#: 再解析の f0 探索域を合成 F0 の実レンジへ寄せる余裕（倍率）。
#: 実測（第 1 走行）で、広い既定レンジのまま再解析すると倍/半周期の誤検出が起き、
#: `f0_sag_cents` が ±1000cent 超の値を返した。合成 F0 は既知なので、
#: その範囲の外を探させない。**測る対象は合成結果の音のまま**であり、
#: 探索域だけを事前情報で絞る（意図の値を代入しているのではない）。
F0_SEARCH_MARGIN_LO = 0.75
F0_SEARCH_MARGIN_HI = 1.35


def _analyze_with_prior(result: pc.ComposeResult) -> pbw.WorldAnalysis:
    voiced = result.f0[result.f0 > 0.0]
    if voiced.size == 0:
        return pbw.analyze(result.wav, result.sr, frame_period_ms=result.frame_period_ms)
    lo = max(40.0, float(np.min(voiced)) * F0_SEARCH_MARGIN_LO)
    hi = min(result.sr / 2.0 * 0.9, float(np.max(voiced)) * F0_SEARCH_MARGIN_HI)
    if hi <= lo * 1.2:
        hi = lo * 1.2
    return pbw.analyze(result.wav, result.sr, frame_period_ms=result.frame_period_ms,
                       f0_floor=lo, f0_ceil=hi)


def measure_rung(
    rung: str, result: pc.ComposeResult, wav_path: Path, bank: IdentityBank,
    probe_pos: int, perf: RealPerformanceTrack, donor_core_sp: Optional[np.ndarray],
    target_track: PerformanceTrack,
) -> RungMeasurement:
    analysis = _analyze_with_prior(result)
    labels = [u.label for u in bank.units]
    trf = pm.measure_trf(analysis, result.unit_frames, [probe_pos], labels,
                         bank.nasal_envelope)
    fr = result.unit_frames[probe_pos]
    core = _core_window(fr)
    out_core = _mean_sp(analysis.sp, core)
    id_core = bank.core_mean_sp(probe_pos)
    identity_lsd = pbw.log_spectral_distance_db(out_core, id_core)
    donor_lsd = (pbw.log_spectral_distance_db(out_core, donor_core_sp)
                 if donor_core_sp is not None else float("nan"))
    identity_shape_lsd = pbw.log_spectral_distance_db(_shape(out_core), _shape(id_core))
    donor_shape_lsd = (pbw.log_spectral_distance_db(_shape(out_core), _shape(donor_core_sp))
                       if donor_core_sp is not None else float("nan"))

    # --- performance 軸 ---
    n = fr[1] - fr[0]
    f0 = analysis.f0[fr[0]:fr[1]]
    voiced = f0 > 0.0
    # 参照点は **抽出側と同じ「コア中央値」** にする。
    # 第 1 走行は unit 全体の中央値を使っており、Performance 側の定義
    # （コア中央値からの逸脱）とずれて RMSE に定数オフセットが乗っていた。
    c_lo, c_hi = _core_window(fr)
    core_v = analysis.f0[c_lo:c_hi]
    core_v = core_v[core_v > 0.0]
    if core_v.size == 0:
        core_v = f0[voiced]
    ref_hz = float(np.median(core_v)) if core_v.size else 0.0
    if ref_hz > 0.0:
        own = np.where(voiced, 1200.0 * np.log2(np.maximum(f0, _EPS) / ref_hz), 0.0)
        tgt = pm._interp_unit(target_track.f0_dev_cents, target_track.f0_dev_unit_index,
                              target_track.f0_dev_unit_pos, probe_pos, n)
        f0_rmse = float(np.sqrt(np.mean((own[voiced] - tgt[voiced]) ** 2))) \
            if np.any(voiced) else float("nan")
    else:
        f0_rmse = float("nan")

    fp = result.frame_period_ms / 1000.0
    lo, hi = pr_match.note_unit_span(bank, probe_pos)
    want = pr_match.transplanted_durations(bank, probe_pos, perf)
    got = [(result.unit_frames[i][1] - result.unit_frames[i][0]) * fp for i in range(lo, hi + 1)]
    note_split_mae = float(np.mean(np.abs(np.array(got) - want[lo:hi + 1])) * 1000.0)

    e = 10.0 * np.log10(np.maximum(np.sum(analysis.sp[fr[0]:fr[1]], axis=1), _EPS))
    e = e - float(np.mean(e))
    tgt_e = pm._interp_unit(target_track.energy_db, target_track.energy_unit_index,
                            target_track.energy_unit_pos, probe_pos, n)
    corr = float(np.corrcoef(e, tgt_e)[0, 1]) if e.size > 2 and np.std(tgt_e) > 0 else float("nan")

    r0f = fr[1] - max(2, int(round(n * perf.release.window_frac)))
    tail = 10.0 * np.log10(np.maximum(np.sum(analysis.sp[r0f:fr[1]], axis=1), _EPS))
    tail = tail - float(tail[0]) if tail.size else np.zeros(2)
    ref_taper = np.asarray(perf.release.terminal_decay_shape_db, dtype=np.float64)
    taper_rmse = float(np.sqrt(np.mean(
        (_resample_to(tail, ref_taper.size) - ref_taper) ** 2)))

    return RungMeasurement(
        rung=rung, toggles=result.toggles_label, wav_path=str(wav_path),
        wav_file_sha256=sha256_bytes_of_file(wav_path),
        samples_sha256=result.sha256(),
        duration_s=result.wav.shape[0] / result.sr,
        trf={t.label + f"@{t.unit_index}": t.as_dict() for t in trf},
        identity_lsd_db=identity_lsd, donor_lsd_db=donor_lsd,
        identity_shape_lsd_db=identity_shape_lsd, donor_shape_lsd_db=donor_shape_lsd,
        f0_dev_rmse_cents=f0_rmse, note_split_mae_ms=note_split_mae,
        energy_corr=corr, taper_rmse_db=taper_rmse,
    )


def run_probe_pair(
    *, pair_key: str, probe_kind: str, bank: IdentityBank, probe_pos: int,
    perf: RealPerformanceTrack, donor_core_sp: Optional[np.ndarray],
    ritsu_file: str, pjs_file: str, out_dir: Path,
    p0_reference: Optional[Dict[str, Any]] = None,
) -> ProbePairResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    track = pr_match.build_compose_track(bank, probe_pos, perf)
    res = ProbePairResult(
        pair_key=pair_key, probe_kind=probe_kind, ritsu_file=ritsu_file, pjs_file=pjs_file,
        identity_sha256=bank.content_sha256(), performance_sha256=perf.content_sha256(),
        matching=pr_match.matching_report(bank, probe_pos, perf),
        p0_reference=p0_reference or {},
    )
    for name, toggles in LADDER:
        composed = pc.compose(bank, track, toggles)
        wav_path = out_dir / f"{name}.wav"
        sf.write(wav_path, composed.wav.astype(np.float32), composed.sr, subtype="FLOAT")
        res.rungs[name] = measure_rung(name, composed, wav_path, bank, probe_pos,
                                       perf, donor_core_sp, track)
    return res


def compose_shas(bank: IdentityBank, track: PerformanceTrack) -> List[str]:
    """決定論検査用: ファイル書き出しを介さないサンプル列 sha（G1 の再計算側）。"""
    return [pc.compose(bank, track, tg).sha256() for _n, tg in LADDER]


def build_track(bank: IdentityBank, probe_pos: int,
                perf: RealPerformanceTrack) -> PerformanceTrack:
    return pr_match.build_compose_track(bank, probe_pos, perf)


def donor_core_envelope(analysis: pbw.WorldAnalysis, phones: Sequence[Any],
                        vowel_index: int, *, target_sr: Optional[int] = None,
                        target_bins: Optional[int] = None) -> np.ndarray:
    """PJS 側 probe 母音のコア包絡（**比較にのみ使う**。合成へは渡さない）。

    Ritsu は 44.1kHz、PJS は 48kHz なので、同じ bin index が別の周波数を指す。
    そのまま距離を測ると sr 差を「テクスチャの差」と誤読するため、
    identity 側の周波数グリッドへ張り替えてから返す。
    """
    fp = analysis.frame_period_ms / 1000.0
    p = phones[vowel_index]
    n0, n1 = int(p.start_s / fp), int(p.end_s / fp)
    span = max(1, n1 - n0)
    env = _mean_sp(analysis.sp, (n0 + int(0.35 * span), n0 + int(0.60 * span) + 1))
    if target_sr is None or target_bins is None or (
            target_sr == analysis.sr and target_bins == env.shape[0]):
        return env
    src_f = np.linspace(0.0, analysis.sr / 2.0, env.shape[0])
    dst_f = np.linspace(0.0, target_sr / 2.0, target_bins)
    return np.exp(np.interp(dst_f, src_f, np.log(np.maximum(env, _EPS))))
