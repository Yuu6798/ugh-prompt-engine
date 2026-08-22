"""af_ingest.py — VoiceGenesis 取り込みと WORLD 再表現（設計書 §12 / §19 G4）。

既存経路は **read-only 利用**する。`donor_bank_utau.py` / `donor_bank.py` /
`units.py` / `joins.py` を一切変更せず、Ritsu / PJS 向けの subset optimization も
AF0 へ適用しない（`selected AF0 WAV = all`）。

`pyworld` は本リポジトリの必須依存ではない。未導入環境では G4 を **BLOCKED**
として扱い、NOT_ESTABLISHED（形質不成立）と取り違えない（§20）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

_ADAPTER = Path(__file__).resolve().parents[1] / "adapter"
if str(_ADAPTER) not in sys.path:
    sys.path.insert(0, str(_ADAPTER))

from af_spec import AFStop, FounderGenome  # noqa: E402

#: WORLD 再表現のサンプルレート（既存 donor 経路と同じ 24 kHz）。
REEXPRESSION_SR = 24000
FRAME_PERIOD_MS = 5.0


def pyworld_available() -> bool:
    try:
        import pyworld  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def require_pyworld() -> Any:
    try:
        import pyworld as pw
    except ModuleNotFoundError as exc:
        raise AFStop(
            cause=f"pyworld is not installed ({exc})",
            impact="G4 VOICEGENESIS_INGESTION と re-expression 測定が実行できない",
            minimal_fix="pip install pyworld （P0 は CPU のみで完走する）",
            status="BLOCKED", reason_code="DEPENDENCY_MISSING") from exc
    return pw


# ---------------------------------------------------------------------------
# §12 DonorBank 取り込み
# ---------------------------------------------------------------------------
def build_bank(genome: FounderGenome, voicebank_root: str | Path,
               ingestion_criteria: Dict[str, Any]) -> Tuple[Any, Dict[int, str],
                                                            Dict[str, list], Dict[str, Any]]:
    """既存 `build_donor_bank_utau` で AF0 の **全 25 WAV** を分析する。"""
    require_pyworld()
    from donor_bank_utau import build_donor_bank_utau

    n_units = len(genome.units)
    bank, unit_vowels, clips, stats = build_donor_bank_utau(
        str(voicebank_root), pitch_dirs=[genome.pitch_dir],
        max_wav_files=n_units,
        # 全 WAV を選ばせるための下限（母音あたりの unit 数 = alias 数 / 5）。
        min_units_per_vowel=max(1, n_units // 5),
        required_onsets=list(ingestion_criteria["required_onsets"]),
        required_vowels=list(ingestion_criteria["required_vowels"]))
    return bank, unit_vowels, clips, stats


def bank_health(bank: Any, unit_vowels: Dict[int, str], clips: Dict[str, list],
                stats: Dict[str, Any], ingestion_criteria: Dict[str, Any]) -> Dict[str, Any]:
    """§19 G4 の取り込み健全性（全 WAV 解析 / 母音・onset 被覆 / WORLD 有限性）。"""
    want_wav = int(ingestion_criteria["expected_wav_count"])
    vowels = sorted(set(unit_vowels.values()))
    onsets = sorted(k for k, v in clips.items() if v)
    detail = {
        "n_wav_files_analyzed": int(stats.get("n_wav_files_analyzed", 0)),
        "n_units": int(len(bank.units)),
        "vowels_covered": vowels,
        "onsets_covered": onsets,
        "world_f0_finite": bool(np.all(np.isfinite(bank.f0))),
        "world_sp_finite": bool(np.all(np.isfinite(bank.sp))),
        "world_ap_finite": bool(np.all(np.isfinite(bank.ap))),
        "world_sp_positive": bool(np.all(bank.sp > 0.0)),
    }
    ok = (detail["n_wav_files_analyzed"] == want_wav
          and detail["n_units"] >= want_wav
          and set(ingestion_criteria["required_vowels"]).issubset(vowels)
          and set(ingestion_criteria["required_onsets"]).issubset(onsets)
          and detail["world_f0_finite"] and detail["world_sp_finite"]
          and detail["world_ap_finite"] and detail["world_sp_positive"])
    detail["verdict"] = "PASS" if ok else "FAIL"
    return detail


# ---------------------------------------------------------------------------
# WORLD 再表現（unit ごと）
# ---------------------------------------------------------------------------
def reexpress_unit(wav_path: str | Path) -> Tuple[np.ndarray, int, Dict[str, Any]]:
    """1 WAV を 24 kHz へ落として WORLD 分析 -> 再合成する（§12）。"""
    pw = require_pyworld()
    from donor_bank import analyze_donor_world, load_donor_24k

    x, sr = load_donor_24k(wav_path)
    analysis = analyze_donor_world(x, sr, frame_period_ms=FRAME_PERIOD_MS)
    y = pw.synthesize(analysis["f0"], analysis["sp"], analysis["ap"], sr,
                      frame_period=FRAME_PERIOD_MS)
    diag = {
        "n_frames": int(analysis["f0"].size),
        "voiced_frames": int(np.count_nonzero(analysis["voiced_mask"])),
        "f0_finite": bool(np.all(np.isfinite(analysis["f0"]))),
        "sp_finite": bool(np.all(np.isfinite(analysis["sp"]))),
        "ap_finite": bool(np.all(np.isfinite(analysis["ap"]))),
        "out_finite": bool(np.all(np.isfinite(y))),
        "out_samples": int(y.size),
    }
    return np.ascontiguousarray(y, dtype=np.float64), int(sr), diag


def reexpress_body(genome: FounderGenome, voicebank_root: str | Path,
                   out_dir: str | Path) -> Dict[str, Any]:
    """Body 全 unit を再表現し、24 kHz WAV として書き出す。"""
    root = Path(voicebank_root) / genome.pitch_dir
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    diags: Dict[str, Any] = {}
    for unit in genome.units:
        y, sr, diag = reexpress_unit(root / unit.wav_filename)
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        if peak > 1.0:
            y = y / peak
            diag["normalized_peak"] = peak
        sf.write(str(out / unit.wav_filename), y, sr, subtype="PCM_16", format="WAV")
        diags[unit.stem] = diag
    return {"sr": REEXPRESSION_SR, "units": diags,
            "all_finite": all(d["out_finite"] for d in diags.values())}


# ---------------------------------------------------------------------------
# §12 join smoke（か -> り -> あ）
# ---------------------------------------------------------------------------
def join_smoke(genome: FounderGenome, bank: Any, unit_vowels: Dict[int, str],
               sequence: Sequence[str], out_wav: Optional[str | Path] = None) -> Dict[str, Any]:
    """既存 `units.select_units` + `joins.assemble` で 1 ケースだけ接合する。

    **これは trait Gate ではなく ingestion viability の確認**（§12）。
    """
    pw = require_pyworld()
    from joins import NotePlacement, assemble
    from units import TargetNote, resolve_unit_to_note, select_units

    from af_spec import ALIAS_BY_KANA

    note_frames = int(round(200.0 / FRAME_PERIOD_MS))
    # 母音ターゲットは Founder Genome の alias 表から引く（adapter の
    # `mora_to_vowel_target` は Mora オブジェクトを取るため、かな文字列からは
    # 直接引けない）。
    targets = [TargetNote(pitch_hz=genome.core_f0_hz, duration_sec=0.2, label=mora,
                          vowel_target=ALIAS_BY_KANA[mora].vowel) for mora in sequence]
    selections, sel_stats = select_units(targets, bank.units, unit_vowels=unit_vowels)
    placements: List[NotePlacement] = []
    cursor = 0
    for i, sel in enumerate(selections):
        seg = resolve_unit_to_note(bank, sel.unit, note_frames, note_index=i,
                                   frame_period_ms=FRAME_PERIOD_MS)
        placements.append(NotePlacement(start_frame=cursor, end_frame=cursor + seg.n_frames,
                                        sp=seg.sp, ap=seg.ap, has_join_to_prev=(i > 0)))
        cursor += seg.n_frames
    sp_seq, ap_seq, join_stats = assemble(cursor, bank.sp.shape[1], placements,
                                          frame_period_ms=FRAME_PERIOD_MS)
    f0 = np.full(cursor, float(genome.core_f0_hz), dtype=np.float64)
    y = pw.synthesize(f0, np.ascontiguousarray(sp_seq), np.ascontiguousarray(ap_seq),
                      REEXPRESSION_SR, frame_period=FRAME_PERIOD_MS)
    if out_wav is not None:
        peak = float(np.max(np.abs(y))) or 1.0
        sf.write(str(out_wav), y / max(peak, 1.0), REEXPRESSION_SR, subtype="PCM_16",
                 format="WAV")
    detail = {
        "sequence": list(sequence),
        "n_notes": len(selections),
        "selected_vowels": [unit_vowels.get(s.unit.index) for s in selections],
        "n_frames": int(cursor),
        "out_samples": int(y.size),
        "finite": bool(np.all(np.isfinite(y))),
        "nonzero": bool(float(np.max(np.abs(y))) > 0.0),
        "join_stats": {k: v for k, v in join_stats.items() if isinstance(v, (int, float))},
        "selection_expanded": bool(any(s.expanded for s in selections)),
        "selection_stats": {k: v for k, v in sel_stats.items() if isinstance(v, (int, float))},
    }
    detail["verdict"] = "PASS" if (detail["finite"] and detail["nonzero"]
                                   and detail["n_notes"] == len(sequence)) else "FAIL"
    return detail
