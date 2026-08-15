"""adapter/donor_bank.py — VG-F1: ドナー分析 + 音節規模の単位切り出し + npz キャッシュ。

設計書 (`DESIGN_F1_adapter_v0.md`) §2 donor_bank.py に対応する。

- WORLD (harvest/cheaptrick/d4c, 5ms) でドナー全体を分析する。
- 単位境界は vocadito 同梱の notes アノテーション CSV（note onset/offset）を
  主経路とし、CSV が無ければ有声連続区間をエネルギー谷で再帰分割する
  フォールバックに切り替える。
- 各 unit は接合コスト用に先頭/末尾フレームの sp を 24 log 帯域へ縮約した
  ベクトルを保持する。
- npz キャッシュはリポにコミットしない想定（呼び出し側が scratchpad 等の
  `cache_dir` を明示的に渡した場合のみ有効化される。既定は None = キャッシュ
  無効）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import replace as dataclasses_replace
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.signal import resample_poly

SR = 24000
FRAME_PERIOD_MS = 5.0
N_LOG_BANDS = 24
LOG_BAND_LO_HZ = 50.0

MIN_UNIT_SEC = 0.250
MAX_UNIT_SEC = 1.200
# CSV ノートが短すぎる（アノテーション上のグリッチに近い）場合は単位として
# 採用しない下限（15ms 未満。実装決定・record に記録）。
MIN_UNIT_FRAMES_ABS = 3

# vocadito データセット zip の既知 md5（Zenodo 再取得時の照合用）。
DONOR_ZIP_MD5 = "dea40fd18f14d899643c4ba221b33a46"


def sha256_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_donor_24k(wav_path: str | Path) -> Tuple[np.ndarray, int]:
    """44.1kHz ドナー wav を読み込み、24kHz へ有理比 80/147 でリサンプルする。

    F1b/F1c と同一手順（gcd(24000,44100)=300 -> 24000/300=80, 44100/300=147）。
    """
    x, sr = sf.read(str(wav_path))
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != 44100:
        raise ValueError(
            f"unexpected donor sr={sr} (expected 44100). "
            f"vocadito zip md5 should be {DONOR_ZIP_MD5} — 再取得時は照合すること。"
        )
    y = resample_poly(x, up=80, down=147)
    return np.ascontiguousarray(y.astype(np.float64)), SR


def analyze_donor_world(x: np.ndarray, sr: int, frame_period_ms: float = FRAME_PERIOD_MS) -> dict:
    """WORLD (harvest/cheaptrick/d4c) でドナー全体を分析する（プーリングなし）。"""
    f0, t = pw.harvest(x, sr, frame_period=frame_period_ms)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    voiced_mask = f0 > 0
    return dict(f0=f0, t=t, sp=sp, ap=ap, voiced_mask=voiced_mask)


def _log_band_vector(sp_row: np.ndarray, sr: int, n_bands: int = N_LOG_BANDS) -> np.ndarray:
    """1 フレームの sp（線形パワー領域）を n_bands 個の log 帯域エネルギーへ縮約する。

    帯域境界は LOG_BAND_LO_HZ〜Nyquist の対数等分割（geomspace）。接合コスト用の
    相対距離にのみ使うため絶対スケールは問題にしない。
    """
    n_bins = sp_row.shape[0]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    edges = np.geomspace(LOG_BAND_LO_HZ, sr / 2.0, n_bands + 1)
    out = np.empty(n_bands, dtype=np.float64)
    for i in range(n_bands):
        m = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if i == n_bands - 1:
            m = (freqs >= edges[i]) & (freqs <= edges[i + 1])
        s = float(np.sum(sp_row[m])) if np.any(m) else 0.0
        out[i] = np.log(s + 1e-12)
    return out


def load_notes_csv(path: str | Path) -> List[Tuple[float, float, float]]:
    """vocadito notes CSV（ヘッダ無し・列 = start_sec,pitch_hz,duration_sec）を読む。

    vocadito README: 「column 1: note start time (s) / column 2: note pitch (Hz) /
    column 3: note duration (s)」。start_sec 昇順にソートして返す（決定論）。
    """
    data = np.loadtxt(str(path), delimiter=",")
    if data.ndim == 1:
        data = data.reshape(1, -1)
    rows = [(float(r[0]), float(r[1]), float(r[2])) for r in data]
    rows.sort(key=lambda r: r[0])
    return rows


def units_from_notes(
    notes: Sequence[Tuple[float, float, float]],
    n_donor_frames: int,
    frame_period_ms: float = FRAME_PERIOD_MS,
) -> Tuple[List[Tuple[int, int]], dict]:
    """notes CSV の (start_sec, pitch_hz, dur_sec) を単位境界（フレーム index）へ変換する。

    MIN_UNIT_FRAMES_ABS フレーム未満（15ms 未満）になるノートは単位として
    採用しない（アノテーション上のグリッチに近いため。実装決定・record 記録）。
    """
    frame_period_s = frame_period_ms / 1000.0
    boundaries: List[Tuple[int, int]] = []
    n_dropped_short = 0
    n_clipped = 0
    for start_sec, _pitch_hz, dur_sec in notes:
        start_frame_raw = int(round(start_sec / frame_period_s))
        end_frame_raw = int(round((start_sec + dur_sec) / frame_period_s))
        if start_frame_raw < 0 or end_frame_raw > n_donor_frames:
            n_clipped += 1
        start_frame = max(0, min(start_frame_raw, n_donor_frames))
        end_frame = max(0, min(end_frame_raw, n_donor_frames))
        if end_frame - start_frame < MIN_UNIT_FRAMES_ABS:
            n_dropped_short += 1
            continue
        boundaries.append((start_frame, end_frame))
    stats = dict(
        n_notes_total=len(notes),
        n_units_kept=len(boundaries),
        n_dropped_short=n_dropped_short,
        n_clipped_to_donor_range=n_clipped,
        min_unit_frames_abs=MIN_UNIT_FRAMES_ABS,
    )
    return boundaries, stats


def _split_recursive(
    s: int, e: int, energy: np.ndarray, min_frames: int, max_frames: int
) -> List[Tuple[int, int]]:
    length = e - s
    if length <= max_frames:
        return [(s, e)]
    lo = s + min_frames
    hi = e - min_frames
    if lo >= hi:
        mid = (s + e) // 2
    else:
        rel_idx = int(np.argmin(energy[lo:hi]))
        mid = lo + rel_idx
    if mid <= s or mid >= e:
        mid = (s + e) // 2
    return _split_recursive(s, mid, energy, min_frames, max_frames) + _split_recursive(
        mid, e, energy, min_frames, max_frames
    )


def units_from_energy_valleys(
    f0: np.ndarray, sp: np.ndarray, frame_period_ms: float = FRAME_PERIOD_MS
) -> Tuple[List[Tuple[int, int]], dict]:
    """notes CSV が無い場合のフォールバック: 有声連続区間をエネルギー谷で
    250ms〜1.2s に収まるよう再帰分割する。

    250ms 未満の短い自然区間はそのまま残す（分割操作では拡張できないため）。
    """
    frame_period_s = frame_period_ms / 1000.0
    min_frames = int(round(MIN_UNIT_SEC / frame_period_s))
    max_frames = int(round(MAX_UNIT_SEC / frame_period_s))
    voiced = f0 > 0
    energy = np.log(np.sum(sp, axis=1) + 1e-12)

    runs: List[Tuple[int, int]] = []
    n = len(voiced)
    i = 0
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < n and voiced[j]:
            j += 1
        runs.append((i, j))
        i = j

    boundaries: List[Tuple[int, int]] = []
    n_split_ops = 0
    n_short_runs = 0
    for s, e in runs:
        if (e - s) < min_frames:
            n_short_runs += 1
        pieces = _split_recursive(s, e, energy, min_frames, max_frames)
        n_split_ops += max(0, len(pieces) - 1)
        boundaries.extend(pieces)

    stats = dict(
        n_voiced_runs=len(runs),
        n_units_kept=len(boundaries),
        n_split_ops=n_split_ops,
        n_short_runs_under_250ms=n_short_runs,
        min_unit_frames=min_frames,
        max_unit_frames=max_frames,
    )
    return boundaries, stats


@dataclass(frozen=True)
class DonorUnit:
    index: int
    start_frame: int
    end_frame: int  # exclusive
    median_f0: float
    duration_s: float
    head_log_bands: np.ndarray  # (N_LOG_BANDS,)
    tail_log_bands: np.ndarray  # (N_LOG_BANDS,)
    # 追補 F1.3-A: oto.ini 由来の overlap / preutterance をフレーム単位で保持する
    # スキーマ拡張。None = 情報なし（vocadito / pjs）。joins.py v2 は None の場合
    # 固定 40ms へフォールバックする（donor_bank_utau.py のみ実値を設定）。
    # preutterance_frames は F1.3 では保持のみだったが、F1.4 でフレーズ頭の
    # タイムライン配置消費に使われる（render.py 参照）。
    overlap_frames: Optional[int] = None
    preutterance_frames: Optional[int] = None
    # 追補 F1.4-A: VCV unit の内部構造。unit 先頭（= start_frame）からの相対
    # フレームオフセットで、[0, vowel_core_start_frame) が録音済み調音遷移
    # （前母音尾 + 子音 + 母音アタック・伸縮しない固定区間）、
    # [vowel_core_start_frame, end_frame-start_frame) が母音定常部（伸縮/
    # 往復ループの対象）。None = unit 全体が伸縮対象（vocadito/pjs/F1.3までの
    # 旧 UTAU unit の後方互換動作。resolve_unit_to_note がそのまま使う）。
    vowel_core_start_frame: Optional[int] = None


def _build_units(
    donor: dict, boundaries: Sequence[Tuple[int, int]], sr: int, frame_period_ms: float
) -> List[DonorUnit]:
    f0 = donor["f0"]
    sp = donor["sp"]
    units: List[DonorUnit] = []
    for idx, (s, e) in enumerate(boundaries):
        seg_f0 = f0[s:e]
        voiced_f0 = seg_f0[seg_f0 > 0]
        median_f0 = float(np.median(voiced_f0)) if len(voiced_f0) else 0.0
        duration_s = (e - s) * frame_period_ms / 1000.0
        head = _log_band_vector(sp[s], sr)
        tail = _log_band_vector(sp[e - 1], sr)
        units.append(
            DonorUnit(
                index=idx, start_frame=s, end_frame=e, median_f0=median_f0,
                duration_s=duration_s, head_log_bands=head, tail_log_bands=tail,
            )
        )
    return units


@dataclass
class DonorBank:
    sr: int
    frame_period_ms: float
    f0: np.ndarray
    sp: np.ndarray
    ap: np.ndarray
    units: List[DonorUnit]
    wav_sha256: str
    source: str  # "notes_csv" | "energy_valley_fallback"
    notes_csv_path: Optional[str]
    stats: dict


def _unit_vowel_core_range(unit: DonorUnit) -> Tuple[int, int]:
    """unit の「母音核区間」の絶対フレーム範囲 [start,end) を返す。

    追補 F1.4-A: `vowel_core_start_frame` が設定されている VCV unit は
    録音済み調音遷移（先頭側の固定区間）を除いた母音定常部のみを指す
    （F1.3-B item1 の「母音核区間の平均パワー」という規定に、VCV 化後も
    忠実であるための対応・[実装決定・record 記録]）。None（vocadito/pjs/
    旧 UTAU unit）は unit 全体をそのまま使う（後方互換）。
    """
    core_start = unit.start_frame
    if unit.vowel_core_start_frame is not None:
        core_start = unit.start_frame + unit.vowel_core_start_frame
    core_start = max(unit.start_frame, min(core_start, unit.end_frame))
    return core_start, unit.end_frame


def _unit_mean_power(sp: np.ndarray, unit: DonorUnit) -> float:
    """unit（母音核）区間の平均フレームパワー（sp 各ビン総和のフレーム平均）。"""
    s, e = _unit_vowel_core_range(unit)
    seg = sp[s:e]
    if seg.shape[0] == 0:
        return 0.0
    return float(np.mean(np.sum(seg, axis=1)))


def normalize_unit_energy(bank: DonorBank) -> Tuple[DonorBank, dict]:
    """追補 F1.3-B item1: 各 unit の収録時レベルを除去する（母音核区間の平均
    パワーで sp を正規化）。

    正規化先の基準レベルは bank 内の unit 平均パワーの中央値（データ駆動・
    決定論。恒常的な絶対スケール定数を置くより bank ごとの典型レベルに
    自然に合う・[実装決定・record 記録]）。各 unit の frame 範囲の sp に
    一様スケール係数 `target/unit_mean_power` を掛ける（ap はレベル非依存の
    比率量のため変更しない）。head/tail_log_bands は正規化後の値で再計算し、
    units.py の接合コスト（concat cost）が正規化後の実レンダー値と整合する
    ようにする。

    unit を持たない bank（units 空）はそのまま返す。unit 間で frame 範囲が
    重複しない前提（donor_bank / donor_bank_utau / donor_bank_lab のいずれも
    非重複区間で unit を切り出す設計）。
    """
    if not bank.units:
        return bank, dict(n_units=0, target_power=0.0, pre_variance=0.0, post_variance=0.0)

    pre_levels = np.array([_unit_mean_power(bank.sp, u) for u in bank.units], dtype=np.float64)
    positive = pre_levels[pre_levels > 0]
    target_power = float(np.median(positive)) if len(positive) else 0.0

    sp2 = bank.sp.copy()
    new_units: List[DonorUnit] = []
    n_skipped_zero_power = 0
    gains: List[float] = []
    for u, level in zip(bank.units, pre_levels):
        if level <= 0 or target_power <= 0:
            n_skipped_zero_power += 1
            new_units.append(u)
            gains.append(1.0)
            continue
        gain = target_power / level
        sp2[u.start_frame:u.end_frame] *= gain
        gains.append(gain)
        head = _log_band_vector(sp2[u.start_frame], bank.sr)
        tail = _log_band_vector(sp2[u.end_frame - 1], bank.sr)
        new_units.append(dataclasses_replace(u, head_log_bands=head, tail_log_bands=tail))

    post_levels = np.array([_unit_mean_power(sp2, u) for u in new_units], dtype=np.float64)

    def _variance_of(levels: np.ndarray) -> float:
        positive_levels = levels[levels > 0]
        return float(np.var(positive_levels)) if len(positive_levels) else 0.0

    stats = dict(
        n_units=len(bank.units),
        target_power=target_power,
        n_skipped_zero_power=n_skipped_zero_power,
        pre_variance=_variance_of(pre_levels),
        post_variance=_variance_of(post_levels),
        pre_mean=float(np.mean(pre_levels)) if len(pre_levels) else 0.0,
        post_mean=float(np.mean(post_levels)) if len(post_levels) else 0.0,
        gain_min=float(np.min(gains)) if gains else 1.0,
        gain_max=float(np.max(gains)) if gains else 1.0,
        gain_median=float(np.median(gains)) if gains else 1.0,
    )
    new_bank = dataclasses_replace(bank, sp=sp2, units=new_units)
    return new_bank, stats


def _cache_key(wav_sha256: str, notes_csv_path: Optional[str], frame_period_ms: float) -> str:
    material = (
        f"{wav_sha256}|{notes_csv_path or 'fallback'}|{frame_period_ms}|{N_LOG_BANDS}|"
        f"{MIN_UNIT_SEC}|{MAX_UNIT_SEC}|{MIN_UNIT_FRAMES_ABS}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _save_cache(path: Path, bank: DonorBank) -> None:
    starts = np.array([u.start_frame for u in bank.units], dtype=np.int64)
    ends = np.array([u.end_frame for u in bank.units], dtype=np.int64)
    medians = np.array([u.median_f0 for u in bank.units], dtype=np.float64)
    durations = np.array([u.duration_s for u in bank.units], dtype=np.float64)
    heads = np.stack([u.head_log_bands for u in bank.units], axis=0) if bank.units else np.zeros((0, N_LOG_BANDS))
    tails = np.stack([u.tail_log_bands for u in bank.units], axis=0) if bank.units else np.zeros((0, N_LOG_BANDS))
    np.savez(
        path, f0=bank.f0, sp=bank.sp, ap=bank.ap,
        unit_start=starts, unit_end=ends, unit_median_f0=medians, unit_duration_s=durations,
        unit_head_log_bands=heads, unit_tail_log_bands=tails,
        sr=np.array([bank.sr]), frame_period_ms=np.array([bank.frame_period_ms]),
        wav_sha256=np.array([bank.wav_sha256]), source=np.array([bank.source]),
        notes_csv_path=np.array([bank.notes_csv_path or ""]),
    )


def _load_cache(path: Path) -> DonorBank:
    z = np.load(path, allow_pickle=False)
    units = [
        DonorUnit(
            index=i, start_frame=int(z["unit_start"][i]), end_frame=int(z["unit_end"][i]),
            median_f0=float(z["unit_median_f0"][i]), duration_s=float(z["unit_duration_s"][i]),
            head_log_bands=z["unit_head_log_bands"][i], tail_log_bands=z["unit_tail_log_bands"][i],
        )
        for i in range(len(z["unit_start"]))
    ]
    notes_csv_path = str(z["notes_csv_path"][0]) or None
    return DonorBank(
        sr=int(z["sr"][0]), frame_period_ms=float(z["frame_period_ms"][0]),
        f0=z["f0"], sp=z["sp"], ap=z["ap"], units=units,
        wav_sha256=str(z["wav_sha256"][0]), source=str(z["source"][0]),
        notes_csv_path=notes_csv_path, stats=dict(cache_hit=True),
    )


def build_donor_bank(
    wav_path: str | Path,
    notes_csv_path: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
    frame_period_ms: float = FRAME_PERIOD_MS,
) -> DonorBank:
    """ドナー wav を分析し、単位切り出し済みの DonorBank を返す。

    notes_csv_path が指定されかつ存在すれば notes ベース（主経路）、
    それ以外はエネルギー谷フォールバックを使う。cache_dir を渡すと npz
    キャッシュを読み書きする（渡さなければ毎回フル計算・非コミット前提）。
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(
            f"donor wav not found: {wav_path}. vocadito zip md5={DONOR_ZIP_MD5} で再取得・照合すること。"
        )
    wav_sha256 = sha256_of(wav_path)

    notes_csv_str = str(notes_csv_path) if notes_csv_path is not None else None
    use_notes = notes_csv_path is not None and Path(notes_csv_path).exists()

    cache_path: Optional[Path] = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(wav_sha256, notes_csv_str if use_notes else None, frame_period_ms)
        cache_path = cache_dir / f"donor_bank_{key}.npz"
        if cache_path.exists():
            return _load_cache(cache_path)

    x, sr = load_donor_24k(wav_path)
    donor = analyze_donor_world(x, sr, frame_period_ms)
    n_donor_frames = len(donor["f0"])

    if use_notes:
        notes = load_notes_csv(notes_csv_path)  # type: ignore[arg-type]
        boundaries, stats = units_from_notes(notes, n_donor_frames, frame_period_ms)
        source = "notes_csv"
    else:
        boundaries, stats = units_from_energy_valleys(donor["f0"], donor["sp"], frame_period_ms)
        source = "energy_valley_fallback"
        notes_csv_str = None

    units = _build_units(donor, boundaries, sr, frame_period_ms)
    stats["cache_hit"] = False

    bank = DonorBank(
        sr=sr, frame_period_ms=frame_period_ms, f0=donor["f0"], sp=donor["sp"], ap=donor["ap"],
        units=units, wav_sha256=wav_sha256, source=source, notes_csv_path=notes_csv_str, stats=stats,
    )

    if cache_path is not None:
        _save_cache(cache_path, bank)

    return bank


def find_default_vocadito_paths(vocadito_root: str | Path, clip: int = 2, annotator: str = "A1") -> Tuple[Path, Path]:
    """scratchpad の vocadito 展開ディレクトリから wav / notes CSV の既定パスを返す。

    vocadito_root は `.../foundry_f1b/vocadito` を指す（Audio/, Annotations/ を含む）。
    """
    root = Path(vocadito_root)
    wav = root / "Audio" / f"vocadito_{clip}.wav"
    notes = root / "Annotations" / "Notes" / f"vocadito_{clip}_notes{annotator}.csv"
    return wav, notes
