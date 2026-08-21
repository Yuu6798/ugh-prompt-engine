"""convert_founder.py — Founder Body -> DiffSinger 互換データセット（設計書 §13）。

`ph_seq` / `ph_dur` は **Founder Genome から決定論導出**する。音響推定で
annotation を作らない（§13）。DiffSinger の学習自体は P0 の範囲外。

```text
dataset/
├── transcriptions.csv   name,ph_seq,ph_dur
├── wavs/
├── provenance.json
└── dataset_stats.json
```
"""
from __future__ import annotations

import argparse
import csv
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from af_spec import (AliasUnit, FounderGenome, UnitTimeline, load_genome, sha256_file,
                     timeline_for, write_json)

DATASET_SCHEMA = "voicegenesis-artificial-founder-dataset/0.1"

#: 無音記号（DiffSinger 慣行）。
SP = "SP"


def phoneme_segments(genome: FounderGenome, unit: AliasUnit,
                     tl: UnitTimeline) -> Tuple[List[str], List[float]]:
    """1 unit の `(ph_seq, ph_dur[秒])` を Genome から決定論導出する。

    ```text
    SP(lead_zero) [onset(onset_ms)] vowel(vowel + main_taper + afterglow) SP(tail_zero)
    ```

    母音の長さに release と残光を含めるのは、その区間が有声の母音成分そのもの
    （振幅包絡と AR-α 残光）で、無音でも別音素でもないため。合計は WAV の全長に
    一致する（丸め誤差は最後の SP で吸収する）。
    """
    total_s = tl.total / tl.sr
    seq: List[str] = [SP]
    dur: List[float] = [tl.lead / tl.sr]
    if unit.has_onset:
        seq.append(unit.onset)
        dur.append(tl.onset_len / tl.sr)
    seq.append(unit.vowel)
    dur.append((tl.ag_end - tl.onset_end) / tl.sr)
    seq.append(SP)
    dur.append(max(0.0, total_s - sum(dur)))
    return seq, dur


def _fmt(seconds: float) -> str:
    return f"{seconds:.7g}"


def convert(spec_path: str | Path, voicebank_root: str | Path,
            out_dir: str | Path) -> Dict[str, Any]:
    """Founder Body を DiffSinger 互換データセットへ変換する。"""
    genome = load_genome(spec_path)
    return convert_from_genome(genome, voicebank_root, out_dir)


def convert_from_genome(genome: FounderGenome, voicebank_root: str | Path,
                        out_dir: str | Path) -> Dict[str, Any]:
    src = Path(voicebank_root) / genome.pitch_dir
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "wavs").mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, str]] = []
    phoneme_counter: Counter = Counter()
    total_voiced = 0.0
    total_sp = 0.0
    wav_hashes: List[Dict[str, str]] = []
    for unit in genome.units:
        tl = timeline_for(genome, unit)
        seq, dur = phoneme_segments(genome, unit, tl)
        rows.append({"name": unit.stem, "ph_seq": " ".join(seq),
                     "ph_dur": " ".join(_fmt(d) for d in dur)})
        phoneme_counter.update(seq)
        total_sp += sum(d for p, d in zip(seq, dur) if p == SP)
        total_voiced += sum(d for p, d in zip(seq, dur) if p != SP)
        wav_src = src / unit.wav_filename
        shutil.copy2(wav_src, out / "wavs" / unit.wav_filename)
        wav_hashes.append({"file": f"wavs/{unit.wav_filename}", "sha256": sha256_file(wav_src)})

    with open(out / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"],
                                lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    provenance = {
        "schema": DATASET_SCHEMA,
        "founder_id": genome.founder_id,
        "spec_sha256": genome.sha256,
        "derivation": "deterministic_from_founder_genome",
        "acoustic_annotation_used": False,
        "human_audio_used": False,
        "sample_rate_hz": genome.sample_rate_hz,
        "wav_files": wav_hashes,
    }
    write_json(out / "provenance.json", provenance)

    stats = {
        "schema": DATASET_SCHEMA,
        "n_items": len(rows),
        "phoneme_symbols": sorted(phoneme_counter),
        "phoneme_counts": dict(sorted(phoneme_counter.items())),
        "total_voiced_s": total_voiced,
        "total_sp_s": total_sp,
    }
    write_json(out / "dataset_stats.json", stats)

    return {"out_dir": str(out), "n_items": len(rows),
            "phoneme_symbols": stats["phoneme_symbols"],
            "n_wavs": len(wav_hashes),
            "verdict": "PASS" if (len(rows) == len(genome.units)
                                  and len(wav_hashes) == len(genome.units)) else "FAIL"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AF0 Founder Body -> DiffSinger dataset (§13)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--voicebank", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    result = convert(args.spec, args.voicebank, args.out)
    print(result)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
