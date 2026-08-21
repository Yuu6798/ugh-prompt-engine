"""run8/s7_b1_calibration.py — B-1 校正ハーネス（`DESIGN_S7_run8.md` §12-0-B）。

**校正音源だけ**を通して TRF 主観測 4 値の測定仕様を確定し、
`TRF measurement spec` として凍結するための実行体。**本番 360 セルは
1 セルも通さない**（そもそも本番セルへ到達する入力口を持たない）。

事前登録 3 点（`results_s7/s7_b1_{candidate_space,calibration_set,selection_rule}.json`）は
**このモジュールの実装より前のコミット**で pin 済みであり、本モジュールは
それらを読むだけで、候補・刺激・選択規則を**足さない**（§12-0-C2）。

実行:

    python voice_genesis/foundry/run8/s7_b1_calibration.py \
        --out voice_genesis/foundry/results_s7/trf_measurement_spec.json

決定論: 乱数は事前登録の seed（PCG64 / 20260821）のみ。librosa / numpy は
`ANALYSIS_STACK_PIN`（librosa 0.11.0 / numba 0.66.0）で pin されている。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402
from s7_io import OutputCollisionError, reject_output_collision  # noqa: E402,F401

RESULTS_DIR = _HERE.parent / "results_s7"
CANDIDATE_SPACE_PATH = RESULTS_DIR / "s7_b1_candidate_space.json"
CALIBRATION_SET_PATH = RESULTS_DIR / "s7_b1_calibration_set.json"
SELECTION_RULE_PATH = RESULTS_DIR / "s7_b1_selection_rule.json"

#: 本 PR 時点の spec は「合成校正音源のみ」で作られた候補版である（§記録参照）。
SPEC_VERSION = "1.0-rc1"
SPEC_FREEZE_STATUS = "candidate"

_MS = 1000.0


# --- 事前登録の読み込み（pin 照合つき） ------------------------------------


@dataclass(frozen=True)
class Prereg:
    candidate_space: Dict[str, Any]
    calibration_set: Dict[str, Any]
    selection_rule: Dict[str, Any]
    pins: Dict[str, str]


def load_prereg(
    candidate_space_path: Path = CANDIDATE_SPACE_PATH,
    calibration_set_path: Path = CALIBRATION_SET_PATH,
    selection_rule_path: Path = SELECTION_RULE_PATH,
) -> Prereg:
    """事前登録 3 点を読み、schema を確認し、sha256 を pin として返す。"""
    # **一度だけ読み**、その同じバイト列から parse と sha256 の両方を作る
    # （読み直すと、間に差し替わったとき「古い文書で選んだ候補」を「新しい
    #  バイトの sha」で pin してしまう = PR #300 Codex 第 3 巡 P2）。
    cs, cs_sha, _ = s7_io.read_json_with_pin(candidate_space_path)
    cal, cal_sha, _ = s7_io.read_json_with_pin(calibration_set_path)
    rule, rule_sha, _ = s7_io.read_json_with_pin(selection_rule_path)
    for doc, expected, path in (
        (cs, sp.CANDIDATE_SPACE_SCHEMA, candidate_space_path),
        (cal, sp.CALIBRATION_SET_SCHEMA, calibration_set_path),
        (rule, sp.SELECTION_RULE_SCHEMA, selection_rule_path),
    ):
        if doc.get("schema") != expected:
            raise ValueError(f"{path.name}: schema {doc.get('schema')!r} != {expected!r}")
    return Prereg(
        candidate_space=cs,
        calibration_set=cal,
        selection_rule=rule,
        pins={
            candidate_space_path.name: cs_sha,
            calibration_set_path.name: cal_sha,
            selection_rule_path.name: rule_sha,
        },
    )


class AnalysisStackMismatch(RuntimeError):
    """宣言された `ANALYSIS_STACK_PIN` と実行時の版が食い違った（fail-closed）。"""


def verify_analysis_stack(prereg: Prereg) -> Dict[str, str]:
    """**測定前に**実行時の解析スタック版が事前登録の宣言と一致するか検査する。

    事前登録は `numba` / `librosa` / `numpy` の版を実名で宣言しているのに対し、
    `pyproject.toml` は広い範囲を許すので、**普通に互換な環境で走らせただけで
    宣言と違う実装が測った値を「宣言 pin の産物」として記帳してしまう**
    （PR #300 Codex P1）。しかも repo には
    「numba 0.67.0 × numpy 2.4.6 は `librosa.pyin` が SIGSEGV・0.66.0 で解消」
    という実測記録がある（`scripts/run5_bootstrap.py` の `ANALYSIS_STACK_PIN`）。
    したがって不一致は**警告ではなく停止**にする。
    """
    declared = {
        k: str(v)
        for k, v in prereg.candidate_space["analysis_stack_pin"].items()
        if k != "note"
    }
    observed = {pkg: importlib.metadata.version(pkg) for pkg in sorted(declared)}
    mismatch = {k: (declared[k], observed[k]) for k in declared if declared[k] != observed[k]}
    if mismatch:
        detail = ", ".join(f"{k}: declared {d} != installed {o}" for k, (d, o) in sorted(mismatch.items()))
        raise AnalysisStackMismatch(
            f"analysis stack pin mismatch ({detail}). "
            "B-1 は宣言 pin 以外で測らない（測ると provenance が壊れる）。"
        )
    return observed


# --- 校正刺激の合成（決定論） ----------------------------------------------


@dataclass(frozen=True)
class Stimulus:
    stim_id: str
    family: str
    samples: np.ndarray
    sr: int
    note_onset_s: float
    commanded_note_end_s: float
    score_boundary_s: float
    tail_window_ms: float


def _harmonic_amplitudes(tilt: str, n_harmonics: int) -> np.ndarray:
    k = np.arange(1, n_harmonics + 1, dtype=np.float64)
    if tilt == "vowel_i":
        # 1/k の基本傾斜に、前舌母音らしい高次の持ち上げ（k=8..12）を足す。
        a = 1.0 / k
        a[7:12] += 0.25
        return a
    if tilt == "nasal_N":
        # 低域優位（鼻音らしい急峻な減衰）+ 全体を控えめに。
        return 0.7 / (k**1.8)
    raise ValueError(f"unknown spectral tilt: {tilt!r}")


def _band_limited_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """32 点 Hann 核で平滑した帯域制限ノイズ（/r/ 相当の onset）。"""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    raw = rng.standard_normal(n + 64)
    kernel = np.hanning(32)
    kernel = kernel / kernel.sum()
    smoothed = np.convolve(raw, kernel, mode="same")[:n]
    peak = float(np.max(np.abs(smoothed))) if n else 0.0
    return (smoothed / peak * 0.15) if peak > 0 else smoothed


def build_calibration_set(prereg: Prereg) -> Dict[str, Stimulus]:
    """事前登録どおりの 13 刺激を決定論的に合成する。"""
    cal = prereg.calibration_set
    sr = int(cal["sample_rate_hz"])
    common = cal["common"]
    total_s = float(common["total_seconds"])
    onset_s = float(common["note_onset_s"])
    commanded_end_s = float(common["commanded_note_end_s"])
    boundary_s = float(common["score_boundary_s"])
    f0_hz = float(common["f0_hz"])
    n_harm = int(common["n_harmonics"])
    taper_ms = float(common["release_taper_ms"])
    tail_window_ms = float(common["tail_window_ms"])

    rng_cfg = cal["rng"]
    n_total = int(round(total_s * sr))
    t = np.arange(n_total, dtype=np.float64) / sr

    out: Dict[str, Stimulus] = {}
    for entry in cal["stimuli"]:
        stim_id = str(entry["id"])
        family = str(entry["family"])
        # 刺激ごとに seed を領域分離する（刺激間でノイズが共有されない）。
        rng = np.random.default_rng(
            [int(rng_cfg["seed"]), int(hashlib.sha256(stim_id.encode()).hexdigest()[:8], 16)]
        )
        y = np.zeros(n_total, dtype=np.float64)
        if family != "silence":
            voiced_end_s = boundary_s + float(entry.get("tail_voiced_ms", 0.0)) / _MS
            mora_s = voiced_end_s - onset_s
            if entry["onset_kind"] == "noise_burst":
                if "onset_ratio" in entry:
                    onset_len_s = float(entry["onset_ratio"]) * mora_s
                else:
                    onset_len_s = float(entry["onset_ms"]) / _MS
            else:
                onset_len_s = 0.0
            vowel_start_s = onset_s + onset_len_s

            # /r/ 相当の onset（帯域制限ノイズ）
            if onset_len_s > 0:
                i0, i1 = int(round(onset_s * sr)), int(round(vowel_start_s * sr))
                y[i0:i1] += _band_limited_noise(i1 - i0, rng)

            # 有声母音（調波和）
            amps = _harmonic_amplitudes(str(entry["spectral_tilt"]), n_harm)
            v0, v1 = int(round(vowel_start_s * sr)), int(round(voiced_end_s * sr))
            seg_t = t[v0:v1]
            vowel = np.zeros(seg_t.shape, dtype=np.float64)
            for k, a in enumerate(amps, start=1):
                vowel += a * np.sin(2.0 * math.pi * k * f0_hz * seg_t)
            vowel /= float(np.max(np.abs(vowel))) if vowel.size and np.max(np.abs(vowel)) > 0 else 1.0
            # 20 ms の立ち上がり
            n_attack = min(int(round(0.020 * sr)), vowel.size)
            if n_attack > 0:
                vowel[:n_attack] *= np.linspace(0.0, 1.0, n_attack)
            y[v0:v1] += vowel * 0.6

            # release taper（voiced_end から taper_ms かけて 0 へ）
            n_taper = int(round(taper_ms / _MS * sr))
            if n_taper > 0 and v1 < n_total:
                v2 = min(v1 + n_taper, n_total)
                ramp = np.linspace(1.0, 0.0, v2 - v1)
                tail_wave = np.zeros(v2 - v1, dtype=np.float64)
                for k, a in enumerate(amps, start=1):
                    tail_wave += a * np.sin(2.0 * math.pi * k * f0_hz * (t[v1:v2]))
                peak = float(np.max(np.abs(tail_wave))) if tail_wave.size else 0.0
                if peak > 0:
                    tail_wave = tail_wave / peak * 0.6
                y[v1:v2] += tail_wave * ramp
            y *= float(entry.get("gain", 1.0))

        out[stim_id] = Stimulus(
            stim_id=stim_id,
            family=family,
            samples=y,
            sr=sr,
            note_onset_s=onset_s,
            commanded_note_end_s=commanded_end_s,
            score_boundary_s=boundary_s,
            tail_window_ms=tail_window_ms,
        )
    return out


# --- 校正音源（合成 / 実レンダ）の切り替え ---------------------------------


@dataclass(frozen=True)
class CalibrationSource:
    """B-1 が測る校正音源 1 式。合成刺激と実レンダ校正を同じ形で扱う。

    `roles` は事前登録側（合成 = `roles` / 実レンダ = `real_render_set.roles`）
    をそのまま使う。役割の割り当てを測定側で作り替えない（作り替えると
    「事前登録どおりに測った」と言えなくなる）。
    """

    name: str
    stimuli: Dict[str, Stimulus]
    roles: Dict[str, Any]
    boundaries: Dict[str, float]
    sample_rate_hz: int
    worked_example_stim: str
    extra_pins: Dict[str, str]
    manifest_path: Optional[str] = None


def synthetic_source(prereg: Prereg) -> CalibrationSource:
    cal = prereg.calibration_set
    common = cal["common"]
    return CalibrationSource(
        name="synthetic_v1",
        stimuli=build_calibration_set(prereg),
        roles=cal["roles"],
        boundaries={
            "note_onset_s": float(common["note_onset_s"]),
            "commanded_note_end_s": float(common["commanded_note_end_s"]),
            "score_boundary_s": float(common["score_boundary_s"]),
            "tail_window_ms": float(common["tail_window_ms"]),
        },
        sample_rate_hz=int(cal["sample_rate_hz"]),
        worked_example_stim="long_tail_080",
        extra_pins={},
    )


class RealRenderManifestError(RuntimeError):
    """実レンダ manifest が事前登録／実ファイルと食い違った（fail-closed）。"""


def real_render_source(prereg: Prereg, manifest_path: Path) -> CalibrationSource:
    """`s7_calib_render.py` が書いた manifest から実レンダ校正音源を読む。

    照合は 3 段:「manifest が指す事前登録 sha == いま読んだ事前登録 sha」
    「manifest の条件集合 == 事前登録 `real_render_set` の条件集合」
    「各 WAV の sha256 == manifest の記録」。1 つでも外れたら測らない。
    """
    import soundfile as sf

    manifest, manifest_sha, _ = s7_io.read_json_with_pin(manifest_path)
    if manifest.get("schema") != sp.REAL_RENDER_MANIFEST_SCHEMA:
        raise RealRenderManifestError(
            f"schema {manifest.get('schema')!r} != {sp.REAL_RENDER_MANIFEST_SCHEMA!r}"
        )
    prereg_sha = prereg.pins[CALIBRATION_SET_PATH.name]
    if str(manifest["prereg"]["sha256"]) != prereg_sha:
        raise RealRenderManifestError(
            "manifest がレンダ時に見た事前登録 "
            f"{manifest['prereg']['sha256']} と現在の {prereg_sha} が違う"
        )
    rrs = prereg.calibration_set["real_render_set"]
    expected_ids = [str(c["id"]) for c in rrs["conditions"]]
    got_ids = [str(c["condition_id"]) for c in manifest["conditions"]]
    if sorted(expected_ids) != sorted(got_ids):
        raise RealRenderManifestError(
            f"条件集合が事前登録と違う: {sorted(got_ids)} != {sorted(expected_ids)}"
        )

    out_dir = Path(str(manifest["out_dir"]))
    stimuli: Dict[str, Stimulus] = {}
    sample_rates = set()
    for entry in manifest["conditions"]:
        cid = str(entry["condition_id"])
        wav_path = out_dir / str(entry["wav"])
        raw, wav_sha, _ = s7_io.read_bytes_with_pin(wav_path)
        if wav_sha != str(entry["wav_sha256"]):
            raise RealRenderManifestError(f"{cid}: {wav_path} sha256 {wav_sha} != manifest 記録")
        y, sr = sf.read(io.BytesIO(raw), dtype="float64", always_2d=False)
        sample_rates.add(int(sr))
        stimuli[cid] = Stimulus(
            stim_id=cid,
            family=str(entry["maps_to"]),
            samples=np.asarray(y, dtype=np.float64),
            sr=int(sr),
            note_onset_s=float(entry["note_onset_s"]),
            commanded_note_end_s=float(entry["commanded_note_end_s"]),
            score_boundary_s=float(entry["score_boundary_s"]),
            tail_window_ms=float(entry["tail_window_ms"]),
        )
    if len(sample_rates) != 1:
        raise RealRenderManifestError(f"標本化周波数が混在している: {sorted(sample_rates)}")

    ref = manifest["conditions"][0]
    return CalibrationSource(
        name="real_render_v1",
        stimuli=stimuli,
        roles=rrs["roles"],
        boundaries={
            "note_onset_s": float(ref["note_onset_s"]),
            "commanded_note_end_s": float(ref["commanded_note_end_s"]),
            "score_boundary_s": float(ref["score_boundary_s"]),
            "tail_window_ms": float(ref["tail_window_ms"]),
            "note": "レンダ命令から算出（波形から推定していない）。全条件で同一。",
        },
        sample_rate_hz=int(next(iter(sample_rates))),
        worked_example_stim="rr_long_tail_080",
        extra_pins={"s7_b1_real_render_manifest.json": manifest_sha},
        manifest_path=str(manifest_path.resolve()),
    )


def build_source(prereg: Prereg, real_render_manifest: Optional[Path]) -> CalibrationSource:
    if real_render_manifest is None:
        return synthetic_source(prereg)
    return real_render_source(prereg, Path(real_render_manifest))


# --- voicing 実装（候補 A / B） --------------------------------------------


def voicing_pyin(
    y: np.ndarray, sr: int, window_ms: float, hop_ms: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """候補 A: `librosa.pyin` の `voiced_flag` / `f0`（convert_user と同じ呼び方）。"""
    import librosa

    frame_length = int(round(window_ms / _MS * sr))
    hop_length = int(round(hop_ms / _MS * sr))
    f0, voiced_flag, _voiced_prob = librosa.pyin(
        y.astype(np.float64),
        sr=sr,
        fmin=float(librosa.note_to_hz("C2")),
        fmax=float(librosa.note_to_hz("C6")),
        frame_length=frame_length,
        hop_length=hop_length,
        center=True,
    )
    times = np.arange(f0.shape[0], dtype=np.float64) * hop_length / sr
    voiced = np.asarray(voiced_flag, dtype=bool)
    return voiced, np.asarray(f0, dtype=np.float64), times


def voicing_rms_autocorr(
    y: np.ndarray, sr: int, window_ms: float, hop_ms: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """候補 B: フレーム RMS >= 1e-3 かつ 正規化自己相関ピーク r > 0.35。

    ゲート（RMS 床 1e-3 / r > 0.35 / lag 範囲 sr/700〜sr/80）は
    `scripts/measure_bands.py::hnr_median_db` と同一。自己相関は FFT で
    計算する（`np.correlate` と数学的に同じ線形自己相関。窓長 300 ms を
    O(n^2) で回さないため）。フレーム中心は `center=True` 相当に揃える。
    """
    win = int(round(window_ms / _MS * sr))
    hop = int(round(hop_ms / _MS * sr))
    pad = win // 2
    ypad = np.concatenate(
        [np.zeros(pad, dtype=np.float64), y.astype(np.float64), np.zeros(pad + win, dtype=np.float64)]
    )
    n_frames = int(math.floor(len(y) / hop)) + 1
    lag_min, lag_max = int(sr / 700), int(sr / 80)
    voiced = np.zeros(n_frames, dtype=bool)
    f0 = np.full(n_frames, np.nan, dtype=np.float64)
    times = np.arange(n_frames, dtype=np.float64) * hop / sr
    nfft = 1 << int(math.ceil(math.log2(2 * win)))
    for i in range(n_frames):
        start = i * hop
        fr = ypad[start : start + win]
        if fr.size < win:
            break
        e = float(np.sqrt(np.mean(fr**2)))
        if e < 1e-3:
            continue
        fr = fr - fr.mean()
        spec = np.fft.rfft(fr, nfft)
        ac = np.fft.irfft(spec * np.conjugate(spec), nfft)[:win]
        ac = ac / (ac[0] + 1e-20)
        seg = ac[lag_min:lag_max]
        if seg.size == 0:
            continue
        k = int(np.argmax(seg)) + lag_min
        r = float(ac[k])
        if r <= 0.35:
            continue
        voiced[i] = True
        f0[i] = sr / k
    return voiced, f0, times


VOICING_IMPLS = {
    "A_pyin_voiced_flag": voicing_pyin,
    "B_rms_autocorr_gate": voicing_rms_autocorr,
}


# --- 主観測 4 値の測定 ------------------------------------------------------


def measure_voicing_axes(
    voiced: np.ndarray, f0: np.ndarray, times: np.ndarray, stim: Stimulus, hop_ms: float
) -> Dict[str, float]:
    """voicing 候補に依存する 3 軸。単位 = ms / ms / ratio。"""
    w_s = stim.tail_window_ms / _MS
    c, b = stim.commanded_note_end_s, stim.score_boundary_s

    excess_mask = (times > c) & (times <= c + w_s) & voiced
    excess_tail_voiced_ms = float(hop_ms * int(np.count_nonzero(excess_mask)))

    after_mask = (times > b) & (times <= b + w_s) & voiced
    if np.any(after_mask):
        release = float((float(np.max(times[after_mask])) - b) * _MS)
    else:
        release = 0.0
    release_after_score_boundary_ms = max(0.0, release)

    win_mask = (times > b) & (times <= b + w_s)
    denom = int(np.count_nonzero(win_mask))
    if denom == 0:
        tail_f0_persistence = 0.0
    else:
        num = int(np.count_nonzero(win_mask & voiced & np.isfinite(f0)))
        tail_f0_persistence = float(num) / float(denom)

    return {
        "excess_tail_voiced_ms": excess_tail_voiced_ms,
        "release_after_score_boundary_ms": release_after_score_boundary_ms,
        "tail_f0_persistence": tail_f0_persistence,
    }


def measure_mel_axis(stim: Stimulus, mel_cfg: Dict[str, Any]) -> float:
    """`terminal_mel_persistence` = 終端窓の mel パワー / ノート核の mel パワー。"""
    import librosa

    n_fft = int(mel_cfg["n_fft"])
    hop_length = int(mel_cfg["hop_length"])
    n_mels = int(mel_cfg["n_mels"])
    S = librosa.feature.melspectrogram(
        y=stim.samples.astype(np.float64),
        sr=stim.sr,
        n_fft=n_fft,
        hop_length=hop_length,
        n_mels=n_mels,
        power=2.0,
        htk=False,
        fmin=0.0,
        fmax=stim.sr / 2.0,
    )
    times = np.arange(S.shape[1], dtype=np.float64) * hop_length / stim.sr
    frame_power = np.asarray(S, dtype=np.float64).mean(axis=0)

    w_s = stim.tail_window_ms / _MS
    b, c = stim.score_boundary_s, stim.commanded_note_end_s
    tail_mask = (times > b) & (times <= b + w_s)
    core_mask = (times >= stim.note_onset_s + 0.10) & (times <= c - 0.05)
    if not np.any(core_mask):
        return 0.0
    core = float(frame_power[core_mask].mean())
    if core <= 1e-12 or not np.any(tail_mask):
        return 0.0
    tail = float(frame_power[tail_mask].mean())
    return float(tail / core)


# --- 候補の列挙と測定 -------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: str  # "voicing" | "mel"
    voicing_id: Optional[str] = None
    window_ms: Optional[float] = None
    hop_ms: Optional[float] = None
    mel: Optional[Tuple[int, int, int]] = None
    mel_id: Optional[str] = None


def enumerate_candidates(prereg: Prereg) -> List[Candidate]:
    cs = prereg.candidate_space
    out: List[Candidate] = []
    for v in cs["voicing"]:
        for win in cs["window_ms"]:
            for hop in cs["hop_ms"]:
                out.append(
                    Candidate(
                        candidate_id=f"{v['id']}|win{float(win):g}|hop{float(hop):g}",
                        kind="voicing",
                        voicing_id=str(v["id"]),
                        window_ms=float(win),
                        hop_ms=float(hop),
                    )
                )
    for m in cs["mel"]:
        out.append(
            Candidate(
                candidate_id=str(m["id"]),
                kind="mel",
                mel=(int(m["n_fft"]), int(m["hop_length"]), int(m["n_mels"])),
                mel_id=str(m["id"]),
            )
        )
    return out


def measure_candidate(cand: Candidate, stim: Stimulus) -> Dict[str, float]:
    """1 候補 × 1 刺激の測定。voicing 候補は 3 軸、mel 候補は 1 軸。"""
    if cand.kind == "voicing":
        impl = VOICING_IMPLS[str(cand.voicing_id)]
        voiced, f0, times = impl(stim.samples, stim.sr, float(cand.window_ms), float(cand.hop_ms))
        return measure_voicing_axes(voiced, f0, times, stim, float(cand.hop_ms))
    n_fft, hop_length, n_mels = cand.mel  # type: ignore[misc]
    value = measure_mel_axis(
        stim, {"n_fft": n_fft, "hop_length": hop_length, "n_mels": n_mels}
    )
    return {"terminal_mel_persistence": value}


def _axes_of(cand: Candidate) -> Tuple[str, ...]:
    return tuple(a for a in sp.PRIMARY_AXES if sp.AXIS_CANDIDATE_KIND[a] == cand.kind)


def run_measurements(
    prereg: Prereg,
    stimuli: Dict[str, Stimulus],
    candidates: Sequence[Candidate],
    cross_process: bool = True,
    roles: Optional[Dict[str, Any]] = None,
    real_render_manifest: Optional[str] = None,
) -> Dict[str, Any]:
    """全候補 × 全刺激 + 反復（同一プロセス / 独立プロセス）を測る。"""
    roles = prereg.calibration_set["roles"] if roles is None else roles
    repeat_ids = list(roles["reproducibility"])
    cross_ids = list(roles["cross_process_reproducibility"])

    table: Dict[str, Any] = {}
    for cand in candidates:
        first: Dict[str, Dict[str, float]] = {}
        repeat: Dict[str, Dict[str, float]] = {}
        cross: Dict[str, Dict[str, float]] = {}
        for stim_id, stim in stimuli.items():
            first[stim_id] = measure_candidate(cand, stim)
        for stim_id in repeat_ids:
            repeat[stim_id] = measure_candidate(cand, stimuli[stim_id])
        if cross_process:
            cross = _measure_in_subprocess(
                cand.candidate_id, cross_ids, real_render_manifest=real_render_manifest
            )
        table[cand.candidate_id] = {
            "candidate": cand,
            "first": first,
            "repeat": repeat,
            "cross": cross,
        }
    return table


def _measure_in_subprocess(
    candidate_id: str,
    stim_ids: Sequence[str],
    real_render_manifest: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """独立 OS プロセスで同じ測定を再計算する（§12-0-A-3 の 5 番目の要件）。

    1 候補ぶんの刺激をまとめて 1 プロセスで回す（numba の JIT 立ち上げを
    刺激ごとに払わないため）。**測定そのものは 1 刺激ずつ独立**で、
    同一プロセス内の 1 回目と比較される値は変わらない。
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--probe",
        candidate_id,
        ",".join(stim_ids),
    ]
    if real_render_manifest is not None:
        argv += ["--real-render", str(real_render_manifest)]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"cross-process probe failed ({candidate_id} / {list(stim_ids)}): {proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- 選択規則（§12-0-A-3・AUC 禁止） ---------------------------------------


def _tolerances(rule: Dict[str, Any], axis: str, cand: Candidate) -> Dict[str, float]:
    kind = sp.AXIS_KIND[axis]
    reqs = {r["id"]: r for r in rule["hard_requirements"]}
    if kind == "ms":
        gain_tol = float(cand.hop_ms) if cand.hop_ms is not None else 0.0
        silence_tol = float(reqs["silence_zero"]["tolerance"]["ms_axes"])
        min_span = float(reqs["monotone_response"]["tolerance"]["ms_axes_min_span"])
    else:
        gain_tol = float(reqs["gain_invariance"]["tolerance"]["ratio_axes"])
        silence_tol = float(reqs["silence_zero"]["tolerance"]["ratio_axes"])
        min_span = float(reqs["monotone_response"]["tolerance"]["ratio_axes_min_span"])
    return {"gain_tol": gain_tol, "silence_tol": silence_tol, "min_span": min_span}


def evaluate_axis_candidate(
    axis: str, entry: Dict[str, Any], prereg: Prereg, roles: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """1 軸 × 1 候補について 6 つの hard requirement を評価する。"""
    cand: Candidate = entry["candidate"]
    cal = prereg.calibration_set
    roles = cal["roles"] if roles is None else roles
    rule = prereg.selection_rule
    tol = _tolerances(rule, axis, cand)

    first = {k: v[axis] for k, v in entry["first"].items()}
    repeat = {k: v[axis] for k, v in entry["repeat"].items()}
    cross = {k: v[axis] for k, v in entry["cross"].items()}

    finite = all(math.isfinite(v) for v in first.values())
    repro_err = max((abs(first[k] - repeat[k]) for k in repeat), default=0.0)
    cross_err = max((abs(first[k] - cross[k]) for k in cross), default=0.0)
    repro_bound = max(repro_err, cross_err)

    gain_pairs = roles["gain_invariance"]
    gain_err = max(abs(first[a] - first[b]) for a, b in gain_pairs)
    silence_res = abs(first[str(roles["silence_zero"][0])])

    ladder = list(roles["monotone_ladder"]["order"])
    values = [first[s] for s in ladder]
    steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    span = values[-1] - values[0]
    monotone_ok = all(s >= -repro_bound for s in steps) and span >= tol["min_span"]

    checks = {
        "reproducibility": repro_err == 0.0,
        "gain_invariance": gain_err <= tol["gain_tol"],
        "silence_zero": silence_res <= tol["silence_tol"],
        "monotone_response": monotone_ok,
        # 独立プロセス反復を**回していない**場合は「未検証」であって「合格」ではない。
        # `--no-cross-process` で走らせた結果が frozen 扱いになると、要件を
        # 満たさない spec が凍結できてしまう（PR #300 Codex P2）。
        "cross_process_reproducibility": bool(cross) and cross_err == 0.0,
        "numerical_stability": finite,
    }
    return {
        "candidate_id": cand.candidate_id,
        "checks": checks,
        "survives": all(checks.values()),
        "metrics": {
            "reproducibility_error": repro_err,
            "cross_process_error": cross_err,
            "reproducibility_bound": repro_bound,
            "gain_invariance_error": gain_err,
            "silence_residual": silence_res,
            "monotone_values": values,
            "monotone_min_step": min(steps) if steps else 0.0,
            "monotone_span": span,
        },
        "tolerances": tol,
        "hop_ms": cand.hop_ms,
        "window_ms": cand.window_ms,
    }


#: 数値キーの比較精度（`s7_b1_selection_rule.json` の amendment 2026-08-21）。
#: これが無いと順位が 1e-16 の浮動小数点ノイズで決まる（物理量ではない）。
RANK_KEY_DECIMALS = 9


def rank_key(record: Dict[str, Any]) -> Tuple[float, float, float, float, float, str]:
    """事前登録の辞書式順位付け（`s7_b1_selection_rule.json`）。

    **分離性能（AUC / break-ok margin）は 1 つも入っていない。**
    数値キーは比較前に `RANK_KEY_DECIMALS` 桁へ丸める。
    """
    m = record["metrics"]

    def q(x: float) -> float:
        return round(float(x), RANK_KEY_DECIMALS)

    return (
        q(m["gain_invariance_error"]),
        q(m["silence_residual"]),
        q(-float(m["monotone_min_step"])),
        q(record["hop_ms"]) if record["hop_ms"] is not None else 0.0,
        q(record["window_ms"]) if record["window_ms"] is not None else 0.0,
        str(record["candidate_id"]),
    )


def select_for_axis(
    axis: str, table: Dict[str, Any], prereg: Prereg, roles: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    kind = sp.AXIS_CANDIDATE_KIND[axis]
    records = [
        evaluate_axis_candidate(axis, entry, prereg, roles=roles)
        for entry in table.values()
        if entry["candidate"].kind == kind
    ]
    survivors = [r for r in records if r["survives"]]
    if not survivors:
        failing = sorted(
            {c for r in records for c, ok in r["checks"].items() if not ok}
        )
        return {
            "axis": axis,
            "status": sp.AxisStatus.UNAVAILABLE.value,
            "selected": None,
            "failing_requirements": failing,
            "candidates": sorted(records, key=lambda r: r["candidate_id"]),
        }
    winner = sorted(survivors, key=rank_key)[0]
    return {
        "axis": axis,
        "status": sp.AxisStatus.FROZEN.value,
        "selected": winner["candidate_id"],
        "selected_record": winner,
        "candidates": sorted(records, key=lambda r: r["candidate_id"]),
    }


# --- ε 導出（B-2 への入力・§12-0-C） --------------------------------------


def epsilon_for_axis(
    axis: str,
    selection: Dict[str, Any],
    prereg: Prereg,
    source: Optional[CalibrationSource] = None,
) -> Dict[str, Any]:
    """ε = max(numerical_floor, reproducibility_bound)。本番ラベルは一切見ない。"""
    if selection["status"] != sp.AxisStatus.FROZEN.value:
        return {"axis": axis, "epsilon": None, "reason": "axis_unavailable"}
    rec = selection["selected_record"]
    if source is None:
        tail_window_ms = float(prereg.calibration_set["common"]["tail_window_ms"])
        sr = int(prereg.calibration_set["sample_rate_hz"])
    else:
        tail_window_ms = float(source.boundaries["tail_window_ms"])
        sr = int(source.sample_rate_hz)
    if sp.AXIS_KIND[axis] == "ms":
        floor = float(rec["hop_ms"])
        floor_note = "選定候補の hop 長（フレーム格子の量子化幅）"
    elif sp.AXIS_CANDIDATE_KIND[axis] == "voicing":
        n_frames = tail_window_ms / float(rec["hop_ms"])
        floor = 1.0 / n_frames
        floor_note = "終端窓に入るフレーム数の逆数（比の量子化幅）"
    else:
        mel_cfg = next(
            m for m in prereg.candidate_space["mel"] if m["id"] == selection["selected"]
        )
        n_frames = (tail_window_ms / _MS) * sr / float(mel_cfg["hop_length"])
        floor = 1.0 / n_frames
        floor_note = "終端窓に入る mel フレーム数の逆数（比の量子化幅）"
    bound = float(rec["metrics"]["reproducibility_bound"])
    return {
        "axis": axis,
        "epsilon": max(floor, bound),
        "numerical_floor": floor,
        "numerical_floor_note": floor_note,
        "reproducibility_bound": bound,
        "derived_from": "measurement-side only (calibration repeats + frame quantisation)",
    }


# --- spec の組み立て --------------------------------------------------------

AXIS_FORMULAS: Dict[str, Dict[str, str]] = {
    "excess_tail_voiced_ms": {
        "unit": "ms",
        "formula": "hop_ms * |{ i : commanded_note_end < t_i <= commanded_note_end + tail_window and voiced_i }|",
        "note": "命令終端を越えて有声と判定されたフレーム数 × hop 長。終端窓で打ち切る。",
    },
    "release_after_score_boundary_ms": {
        "unit": "ms",
        "formula": "max(0, 1000 * (max{ t_i : voiced_i and score_boundary < t_i <= score_boundary + tail_window } - score_boundary))",
        "note": "譜面境界を越えた最後の有声フレームまでの時間。有声が無ければ 0。",
    },
    "tail_f0_persistence": {
        "unit": "ratio (0..1)",
        "formula": "|{ i in tail : voiced_i and isfinite(f0_i) }| / |{ i in tail }|,  tail = (score_boundary, score_boundary + tail_window]",
        "note": "終端窓のうち f0 が取れている割合。窓にフレームが無ければ 0。",
    },
    "terminal_mel_persistence": {
        "unit": "ratio (>= 0)",
        "formula": "mean_mel_power(tail) / mean_mel_power(core),  core = [note_onset + 0.10 s, commanded_note_end - 0.05 s]",
        "note": "核が 1e-12 以下（= 無音）なら 0。比なので線形ゲインに不変。",
    },
}


def _freeze_state(source: CalibrationSource, selections: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """spec の版と凍結状態。**実レンダ校正で全 4 軸が生き残った場合のみ 1.0**。

    User 裁定 2026-08-21（裁定 1 = (b)）: 合成校正だけでは 1.0 へ昇格しない。
    実レンダで 1 軸でも落ちたら昇格させず、PR-2 は BLOCKED を維持する。
    """
    all_frozen = all(
        sel["status"] == sp.AxisStatus.FROZEN.value for sel in selections.values()
    )
    if source.name != "real_render_v1":
        return {
            "spec_version": SPEC_VERSION,
            "freeze_status": SPEC_FREEZE_STATUS,
            "freeze_status_note": (
                "校正音源が合成刺激のみ（vocoder 出力を 1 本も通していない）。"
                "User 裁定 2026-08-21 = (b) により、この音源では 1.0 へ昇格しない。"
                "昇格には校正専用 real-render セット（事前登録済み）での再校正が要る。"
            ),
        }
    if all_frozen:
        return {
            "spec_version": "1.0",
            "freeze_status": "frozen",
            "freeze_status_note": (
                "校正専用 real-render セット（同じ DiffSinger / vocoder 経路・本番 360 セル"
                "とは完全分離）で 4 軸とも 6 要件を通過した。§12-0-D の PR-2 開始 Gate を"
                "再確認する。"
            ),
        }
    return {
        "spec_version": "1.0-rc2",
        "freeze_status": "blocked",
        "freeze_status_note": (
            "実レンダ校正で 6 要件を通過しない軸がある。事前登録 acceptance.on_failure "
            "のとおり 1.0 へ昇格させず、PR-2 は BLOCKED を維持する（刺激の追加は"
            "**別 amendment**でのみ検討する）。"
        ),
    }


def build_spec(
    prereg: Prereg,
    selections: Dict[str, Dict[str, Any]],
    epsilons: Dict[str, Dict[str, Any]],
    stimuli: Dict[str, Stimulus],
    table: Dict[str, Any],
    analysis_stack_observed: Optional[Dict[str, str]] = None,
    source: Optional[CalibrationSource] = None,
) -> Dict[str, Any]:
    source = synthetic_source(prereg) if source is None else source
    freeze = _freeze_state(source, selections)
    worked: Dict[str, Any] = {}
    reference_output: Dict[str, Any] = {}
    for axis, sel in selections.items():
        if sel["status"] != sp.AxisStatus.FROZEN.value:
            continue
        cid = sel["selected"]
        first = table[cid]["first"]
        worked[axis] = {
            "candidate_id": cid,
            "stimulus": source.worked_example_stim,
            "value": first[source.worked_example_stim][axis],
            "why": "終端窓に 80 ms の有声継続を注入した刺激。手計算で追える単調ラダーの中点。",
        }
        reference_output[axis] = {
            stim_id: round(float(vals[axis]), 9) for stim_id, vals in sorted(first.items())
        }
    return {
        "schema": sp.TRF_SPEC_SCHEMA,
        "spec_version": freeze["spec_version"],
        "freeze_status": freeze["freeze_status"],
        "freeze_status_note": freeze["freeze_status_note"],
        "authority": "DESIGN_S7_run8.md 12-0-B",
        "generated_by": "voice_genesis/foundry/run8/s7_b1_calibration.py",
        "calibration_source": {
            "name": source.name,
            "manifest": source.manifest_path,
            "roles": source.roles,
            "stimulus_ids": sorted(source.stimuli),
            "note": (
                "実レンダ校正は本番 360 セルを 1 セルも通さない（音高 C4 / 0.75・1.5・3.0 拍 "
                "は本番の帯と交わらない）。ラベル・P-ANCHOR 結果は入力していない。"
            ),
        },
        "prereg_pins": dict(prereg.pins) | dict(source.extra_pins),
        "analysis_stack": {
            "declared": {
                k: v
                for k, v in prereg.candidate_space["analysis_stack_pin"].items()
                if k != "note"
            },
            "observed": analysis_stack_observed,
            "verified": analysis_stack_observed is not None,
            "rule": "測定前に fail-closed で照合する（verify_analysis_stack）。不一致なら測らない。",
        },
        "sample_rate_hz": int(source.sample_rate_hz),
        "boundaries": source.boundaries,
        "measured_on": "raw pre-normalisation waveform (DESIGN_S7_run8.md 5-2)",
        "axes": {
            axis: {
                "status": sel["status"],
                "selected_candidate": sel["selected"],
                "unit": AXIS_FORMULAS[axis]["unit"],
                "formula": AXIS_FORMULAS[axis]["formula"],
                "note": AXIS_FORMULAS[axis]["note"],
                "epsilon": epsilons[axis]["epsilon"],
                "epsilon_derivation": {
                    k: v for k, v in epsilons[axis].items() if k not in ("axis", "epsilon")
                },
                "hard_requirement_checks": (
                    sel["selected_record"]["checks"]
                    if sel["status"] == sp.AxisStatus.FROZEN.value
                    else None
                ),
                "selection_metrics": (
                    sel["selected_record"]["metrics"]
                    if sel["status"] == sp.AxisStatus.FROZEN.value
                    else None
                ),
                "failing_requirements": sel.get("failing_requirements"),
                "candidate_survival": {
                    r["candidate_id"]: r["survives"] for r in sel["candidates"]
                },
                # 勝者だけでなく**全候補の実測値**を残す。どのキーで決着したかを
                # 後から独立に検算できないと、順位付け規則を守った証拠にならない。
                "candidate_records": {
                    r["candidate_id"]: {"checks": r["checks"], "metrics": r["metrics"]}
                    for r in sel["candidates"]
                },
                "rank_order": [
                    r["candidate_id"]
                    for r in sorted(
                        [x for x in sel["candidates"] if x["survives"]], key=rank_key
                    )
                ],
            }
            for axis, sel in selections.items()
        },
        "worked_example": worked,
        "reference_output": reference_output,
        "prohibitions": {
            "no_label_input": "B-1 は聴取ラベル・本番 360 セルへの入力口を持たない。",
            "no_auc": "順位付けキーに分離性能は 1 つも入っていない（rank_key を参照）。",
            "no_new_axes": "Gate の primary 候補は本 spec が凍結した軸のみ（§7-0-(2b)）。",
        },
        "auxiliary_axes_not_in_gate": list(sp.AUXILIARY_AXES_NOT_IN_GATE),
    }


def _canonical_json(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def run_b1(
    cross_process: bool = True, real_render_manifest: Optional[Path] = None
) -> Dict[str, Any]:
    prereg = load_prereg()
    observed = verify_analysis_stack(prereg)   # 測定前に fail-closed
    source = build_source(prereg, real_render_manifest)
    candidates = enumerate_candidates(prereg)
    table = run_measurements(
        prereg,
        source.stimuli,
        candidates,
        cross_process=cross_process,
        roles=source.roles,
        real_render_manifest=(None if real_render_manifest is None else str(real_render_manifest)),
    )
    selections = {
        axis: select_for_axis(axis, table, prereg, roles=source.roles) for axis in sp.PRIMARY_AXES
    }
    epsilons = {
        axis: epsilon_for_axis(axis, selections[axis], prereg, source=source)
        for axis in sp.PRIMARY_AXES
    }
    return build_spec(
        prereg,
        selections,
        epsilons,
        source.stimuli,
        table,
        analysis_stack_observed=observed,
        source=source,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="B-1 calibration harness (DESIGN_S7_run8 12-0-B)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "trf_measurement_spec.json")
    parser.add_argument(
        "--no-cross-process",
        action="store_true",
        help=(
            "独立プロセス反復を回さない（開発用）。この場合 hard requirement の "
            "cross_process_reproducibility は**未検証 = 不合格**として扱われ、"
            "どの軸も frozen にならない"
        ),
    )
    parser.add_argument(
        "--real-render",
        type=Path,
        default=None,
        help=(
            "校正専用 real-render セットの manifest（`s7_calib_render.py` の出力）で"
            "校正する。指定時のみ spec は 1.0 / frozen へ昇格し得る"
        ),
    )
    parser.add_argument(
        "--probe",
        nargs=2,
        metavar=("CANDIDATE_ID", "STIMULUS_IDS"),
        help=(
            "独立プロセス再現性検査の内部用: 1 候補 × カンマ区切り刺激の測定値を "
            "JSON で 1 行出力する"
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.probe:
        prereg = load_prereg()
        verify_analysis_stack(prereg)   # 独立プロセス側でも同じ pin を強制する
        source = build_source(prereg, args.real_render)
        cand = next(c for c in enumerate_candidates(prereg) if c.candidate_id == args.probe[0])
        payload = {
            stim_id: measure_candidate(cand, source.stimuli[stim_id])
            for stim_id in args.probe[1].split(",")
        }
        print(json.dumps(payload, sort_keys=True))
        return 0

    protected = [CANDIDATE_SPACE_PATH, CALIBRATION_SET_PATH, SELECTION_RULE_PATH]
    if args.real_render is not None:
        # 校正音源の manifest も入力である（spec の出力先に指定させない）。
        protected.append(Path(args.real_render))
    reject_output_collision([args.out], protected)
    spec = run_b1(
        cross_process=not args.no_cross_process, real_render_manifest=args.real_render
    )
    s7_io.assert_json_finite(spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_canonical_json(spec), encoding="utf-8")
    frozen = [a for a, v in spec["axes"].items() if v["status"] == sp.AxisStatus.FROZEN.value]
    print(
        f"wrote {args.out} ({len(frozen)}/{len(sp.PRIMARY_AXES)} axes frozen) "
        f"source={spec['calibration_source']['name']} "
        f"version={spec['spec_version']} status={spec['freeze_status']}"
    )
    for axis, v in spec["axes"].items():
        print(f"  {axis}: {v['status']} {v['selected_candidate']} eps={v['epsilon']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
