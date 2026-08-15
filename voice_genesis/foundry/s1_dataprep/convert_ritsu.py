"""D2: 波音リツ VCV 録音 (強連続音 Ver1.5.1) -> DiffSinger acoustic 学習形式変換器。

`DESIGN_S1_p2poc.md` D2（データ工場・リツ VCV -> 音素粒度変換）の実装。
`oto.ini`（offset/consonant/blank/preutterance/overlap）から音素粒度
ph_seq/ph_dur を、録音ストリング単位（1 wav = 1 セグメント）で組み立てる。
scratchpad スパイク `s1b_ritsu_dataset/d2_convert.py` の清書版
（ハードコードパス排除・argparse 化・型ヒント）。境界導出規則・fallback table・
実装決定の根拠は `s1b_dataset_record.md` に逐語記録済みのため、本 docstring は
規則の要約のみに留める。

境界導出規則（一次ソースは `s1b_dataset_record.md` §2）:

1. 1 wav ファイル内の全 oto エイリアスを offset_ms 昇順に並べる。この順序は
   録音ストリングのモーラ順序と一致する。
2. alias_i の対象モーラの音素列 phonemes_i を辞書（Ritsu 公式 617 語彙
   dsdur/dsdict.yaml + 本スクリプトの fallback table）から引く。
3. alias_i が占有する非重複区間を [offset_i, boundary_{i+1}) と定義する。
   boundary_{i+1} は次エイリアスの offset（存在すれば）、最終エイリアスは
   `cutoff_position_ms(offset_i, blank_i, wav_duration_ms)`
   （`donor_bank_utau.cutoff_position_ms` を read-only import）。
4. phonemes_i の内訳で分割: 1 音素は区間全体、2 音素は
   `consonant_end_ms = offset_i + consonant_ms_i` で子音/母音に分割、
   3 音素（fallback 由来のみ）は `[offset_i, consonant_end_ms)` を均等按分
   したうえで母音区間を追加する（oto の境界は 1 つしかないための近似。
   実装決定・`s1b_dataset_record.md` §2 参照）。
5. 先頭/末尾の無音は `SP_THRESHOLD_MS` を超えたときのみ `SP` として挿入する。
6. AP（息継ぎ）検出は本 D2 では実施しない（UTAU oto.ini に相当情報源が無い。
   既知の制限）。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import soundfile as sf

# donor_bank_utau は本リポジトリ内の既存アダプタを read-only import する
# （移植しない。境界計算のバグ修正 (review #262) を経た実績コードのため）。
_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))
import donor_bank_utau as dbu  # noqa: E402  read-only import

DEFAULT_PITCH_DIRS: Tuple[str, ...] = ("A3", "F4")
SP_THRESHOLD_MS = 20.0
MIN_PHONEME_MS = 1.0  # 退化区間 (<=0ms) のガード用フロア

# ---------------------------------------------------------------------------
# 1. Ritsu 公式辞書 (617 語彙) パーサ（PyYAML の bool 誤変換回避のため手書き）
# ---------------------------------------------------------------------------


def parse_dsdict(path: Path) -> Dict[str, List[str]]:
    """dsdur/dsdict.yaml (617 entries) を手書きパースする。

    PyYAML の `safe_load` は YAML1.1 の bool リテラル解決規則により grapheme
    "no" を `False` へ誤変換する（実測。`s1b_dataset_record.md` §2）。ここでは
    正規表現による行単位パースで回避する。重複キー（実測 29 件、"へ" のみ値が
    `[h,e]`/`[h,E]` で不一致・他は同一値の重複）は最初の出現を採用する
    （"へ" は非無声化の `[h,e]` が先に出現し、より基本的な発音のため妥当）。
    """
    entries: "OrderedDict[str, List[str]]" = OrderedDict()
    grapheme: Optional[str] = None
    phonemes: List[str] = []

    def flush() -> None:
        if grapheme is not None and grapheme not in entries:
            entries[grapheme] = phonemes

    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            m = re.match(r"^- grapheme:\s*(.*)$", s)
            if m:
                flush()
                grapheme = m.group(1).strip()
                phonemes = []
                continue
            m2 = re.match(r"^\s*-\s+(\S+)\s*$", s)
            if m2 and s.strip() != "phonemes:":
                phonemes.append(m2.group(1).strip())
    flush()
    return dict(entries)


# ---------------------------------------------------------------------------
# 2. fallback table（辞書 617 語彙に無い拡張カタカナ/2 文字グライド表記。
#    実測: A3 oto.ini 1237 エイリアス中 41 モーラ種・285 エイリアス (23.0%)
#    が該当。写像差分表は `s1b_dataset_record.md` §3.1 に転記済み）
# ---------------------------------------------------------------------------

FALLBACK_MORA_PHONEMES: Dict[str, List[str]] = {
    # う行 + 小書き母音（w オンセット。"を"->[w,o] の辞書パターンを外挿）
    "うぃ": ["w", "i"], "うぇ": ["w", "e"], "うぉ": ["w", "o"],
    # す/ず + 小書き（さ行 z/s オンセットの拡張。"ず"->[z,u] を外挿）
    "すぃ": ["s", "i"],
    "ずぃ": ["z", "i"], "ずぃぇ": ["z", "y", "e"], "ずゃ": ["z", "y", "a"],
    "ずゅ": ["z", "y", "u"], "ずょ": ["z", "y", "o"],
    # づ行（辞書実測: "づ"->[d,u]。"ず"[z,u]とは異なる=d オンセット系列）
    "づぁ": ["d", "a"], "づぃ": ["d", "i"], "づぃぇ": ["d", "y", "e"],
    "づぇ": ["d", "e"], "づぉ": ["d", "o"],
    "づゃ": ["d", "y", "a"], "づゅ": ["d", "y", "u"], "づょ": ["d", "y", "o"],
    # で行 2 文字グライド（"でぃ"->[d,i] は辞書にあるが "でぃぇ" は無い）
    "でぃぇ": ["d", "y", "e"],
    # ふ行拡張
    "ふぃぇ": ["f", "y", "e"], "ふゃ": ["f", "y", "a"], "ふょ": ["f", "y", "o"],
    # く/ぐ + 小書き母音（labialized kw/gw。標準ローマ字表記に準拠）
    "クァ": ["k", "w", "a"], "クィ": ["k", "w", "i"], "クゥ": ["k", "w", "u"],
    "クェ": ["k", "w", "e"], "クォ": ["k", "w", "o"],
    "グァ": ["g", "w", "a"], "グィ": ["g", "w", "i"], "グゥ": ["g", "w", "u"],
    "グェ": ["g", "w", "e"], "グォ": ["g", "w", "o"],
    # 撥音カタカナ表記（"ん"->[N] と同義）
    "ン": ["N"],
    # ヴ行（唇歯摩擦音 v。phonemes.txt に "vf" が予約済みだが dsdict 617 語彙
    # には ヴ 系エントリが 1 件も無い。"vf" 単独 + 母音/グライドで構成する）
    "ヴ": ["vf", "u"], "ヴぁ": ["vf", "a"], "ヴぃ": ["vf", "i"],
    "ヴぃぇ": ["vf", "y", "e"], "ヴぇ": ["vf", "e"], "ヴぉ": ["vf", "o"],
    "ヴゃ": ["vf", "y", "a"], "ヴゅ": ["vf", "y", "u"], "ヴょ": ["vf", "y", "o"],
}


def build_mora_dict(dsdict_path: Path) -> Dict[str, List[str]]:
    merged = dict(parse_dsdict(dsdict_path))
    merged.update(FALLBACK_MORA_PHONEMES)
    return merged


# ---------------------------------------------------------------------------
# 3. 1 wav ファイル -> ph_seq/ph_dur 変換
# ---------------------------------------------------------------------------


def _ms_to_s(ms: float) -> float:
    return max(0.0, ms) / 1000.0


def build_segment_for_wav(
    entries: List["dbu.OtoEntry"],
    pitch_dirs: Sequence[str],
    mora_dict: Dict[str, List[str]],
    wav_duration_ms: float,
) -> Tuple[List[str], List[float], dict]:
    """1 wav の全エイリアス（offset 昇順）から (ph_seq, ph_dur[s], stats) を返す。"""
    # 実測 (F4 _ががぎがぐげが.wav): alias 末尾に "↑"/"↓" が付く代替ピッチ
    # ベンドエイリアスが同一 offset に重複して存在する場合がある（1237×2
    # エントリ中 1 件のみ実測）。同一 offset は「同一音声位置への別名」なので
    # offset 昇順で最初に出現したエイリアス（矢印なし正規名が先）のみ採用し、
    # 以降の同一 offset エントリは破棄する（実装決定・record 記録）。
    ordered_all = sorted(entries, key=lambda e: e.offset_ms)
    ordered: List["dbu.OtoEntry"] = []
    seen_offsets: set = set()
    n_duplicate_offset = 0
    for e in ordered_all:
        if e.offset_ms in seen_offsets:
            n_duplicate_offset += 1
            continue
        seen_offsets.add(e.offset_ms)
        ordered.append(e)

    ph_seq: List[str] = []
    ph_dur: List[float] = []
    n_unmapped = 0
    n_sokuon = 0
    used_moras: List[str] = []

    boundaries: List[float] = []
    for i, e in enumerate(ordered):
        if i + 1 < len(ordered):
            boundaries.append(ordered[i + 1].offset_ms)
        else:
            boundaries.append(dbu.cutoff_position_ms(e.offset_ms, e.blank_ms, wav_duration_ms))

    first_offset = ordered[0].offset_ms if ordered else 0.0
    if first_offset > SP_THRESHOLD_MS:
        ph_seq.append("SP")
        ph_dur.append(_ms_to_s(first_offset))

    for e, boundary_ms in zip(ordered, boundaries):
        _prev, mora_kana, _is_init = dbu.parse_alias_mora(e.alias, pitch_dirs)
        if mora_kana == "っ":
            n_sokuon += 1
            continue
        phonemes = mora_dict.get(mora_kana)
        if phonemes is None:
            n_unmapped += 1
            continue
        used_moras.append(mora_kana)

        seg_start = max(0.0, e.offset_ms)
        seg_end = max(seg_start, boundary_ms)
        consonant_end = min(max(seg_start, e.offset_ms + e.consonant_ms), seg_end)

        if len(phonemes) == 1:
            spans = [(seg_start, seg_end)]
        elif len(phonemes) == 2:
            spans = [(seg_start, consonant_end), (consonant_end, seg_end)]
        else:  # len == 3 (fallback クラスタ、按分)
            mid = seg_start + (consonant_end - seg_start) / 2.0
            spans = [(seg_start, mid), (mid, consonant_end), (consonant_end, seg_end)]

        for ph, (s0, s1) in zip(phonemes, spans):
            dur_s = max(MIN_PHONEME_MS, (s1 - s0)) / 1000.0
            ph_seq.append(ph)
            ph_dur.append(dur_s)

    last_boundary = boundaries[-1] if boundaries else 0.0
    if wav_duration_ms - last_boundary > SP_THRESHOLD_MS:
        ph_seq.append("SP")
        ph_dur.append(_ms_to_s(wav_duration_ms - last_boundary))

    stats = dict(
        n_entries=len(ordered), n_unmapped=n_unmapped, n_sokuon=n_sokuon,
        n_duplicate_offset=n_duplicate_offset, used_moras=used_moras,
    )
    return ph_seq, ph_dur, stats


# ---------------------------------------------------------------------------
# 4. 全 pitch dir 走査 -> transcriptions.csv + 統計
# ---------------------------------------------------------------------------


def convert(
    voicebank_root: Path,
    dsdict_path: Path,
    out_dir: Path,
    pitch_dirs: Sequence[str] = DEFAULT_PITCH_DIRS,
) -> dict:
    """リツ voicebank の pitch dir 群を変換し、`out_dir` に
    `transcriptions.csv` / `wavs/` / `provenance.json` を書く。冪等
    （既存 out_dir を再実行すると同一内容で上書きされる）。統計 dict を返す
    （呼び出し側で JSON 保存・print する）。"""
    mora_dict = build_mora_dict(dsdict_path)
    out_wavs = out_dir / "wavs"
    out_wavs.mkdir(parents=True, exist_ok=True)

    rows: List[dict] = []
    global_stats = dict(
        n_wav_total=0, n_entries_total=0, n_unmapped_total=0, n_sokuon_total=0,
        total_audio_s=0.0, total_sp_s=0.0, total_voiced_s=0.0,
        phoneme_symbol_counter=Counter(), mora_counter=Counter(),
        per_dir=dict(),
    )

    for pdir_name in pitch_dirs:
        pdir = voicebank_root / pdir_name
        oto_entries = dbu.parse_oto_ini(pdir / "oto.ini")
        entries_by_wav: "OrderedDict[str, List[dbu.OtoEntry]]" = OrderedDict()
        for e in oto_entries:
            entries_by_wav.setdefault(e.wav_filename, []).append(e)

        dir_stats = dict(n_wav=0, n_entries=0, n_unmapped=0, n_sokuon=0, audio_s=0.0)

        for idx, (wav_filename, entries) in enumerate(sorted(entries_by_wav.items()), start=1):
            wav_path = pdir / wav_filename
            if not wav_path.exists():
                continue
            info = sf.info(str(wav_path))
            wav_duration_ms = info.frames / info.samplerate * 1000.0

            ph_seq, ph_dur, stats = build_segment_for_wav(
                entries, pitch_dirs, mora_dict, wav_duration_ms
            )
            if not ph_seq:
                continue

            seg_name = f"ritsu_{pdir_name}_{idx:03d}"
            data, sr = sf.read(str(wav_path))
            sf.write(str(out_wavs / f"{seg_name}.wav"), data, sr, subtype="PCM_16")

            rows.append(dict(
                name=seg_name,
                ph_seq=" ".join(ph_seq),
                ph_dur=" ".join(f"{d:.7g}" for d in ph_dur),
                source_wav=wav_filename,
                pitch_dir=pdir_name,
            ))

            dir_stats["n_wav"] += 1
            dir_stats["n_entries"] += stats["n_entries"]
            dir_stats["n_unmapped"] += stats["n_unmapped"]
            dir_stats["n_sokuon"] += stats["n_sokuon"]
            dir_stats["audio_s"] += wav_duration_ms / 1000.0

            global_stats["phoneme_symbol_counter"].update(ph_seq)
            global_stats["mora_counter"].update(stats["used_moras"])
            global_stats["total_sp_s"] += sum(d for p, d in zip(ph_seq, ph_dur) if p == "SP")
            global_stats["total_voiced_s"] += sum(d for p, d in zip(ph_seq, ph_dur) if p != "SP")

        global_stats["per_dir"][pdir_name] = dir_stats
        global_stats["n_wav_total"] += dir_stats["n_wav"]
        global_stats["n_entries_total"] += dir_stats["n_entries"]
        global_stats["n_unmapped_total"] += dir_stats["n_unmapped"]
        global_stats["n_sokuon_total"] += dir_stats["n_sokuon"]
        global_stats["total_audio_s"] += dir_stats["audio_s"]

    with open(out_dir / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in ("name", "ph_seq", "ph_dur")})

    with open(out_dir / "provenance.json", "w", encoding="utf-8") as f:
        json.dump(
            [
                {"name": r["name"], "source_wav": r["source_wav"], "pitch_dir": r["pitch_dir"]}
                for r in rows
            ],
            f, ensure_ascii=False, indent=1,
        )

    summary = dict(
        n_wav_total=global_stats["n_wav_total"],
        n_entries_total=global_stats["n_entries_total"],
        n_unmapped_total=global_stats["n_unmapped_total"],
        n_sokuon_total=global_stats["n_sokuon_total"],
        total_audio_s=round(global_stats["total_audio_s"], 3),
        total_sp_s=round(global_stats["total_sp_s"], 3),
        total_voiced_s=round(global_stats["total_voiced_s"], 3),
        effective_minutes=round(global_stats["total_voiced_s"] / 60.0, 3),
        n_segments=len(rows),
        per_dir=global_stats["per_dir"],
        phoneme_symbol_counts=dict(global_stats["phoneme_symbol_counter"]),
        n_unique_moras_used=len(global_stats["mora_counter"]),
        mora_counts=dict(global_stats["mora_counter"]),
    )
    with open(out_dir / "d2_stats.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voicebank-root", type=Path, required=True,
        help="展開済み '波音リツ強連続音Ver1.5.1/' のパス (A3/ F4/ の親ディレクトリ)",
    )
    parser.add_argument(
        "--dsdict", type=Path, required=True,
        help="リツ公式 DiffSinger 配布 zip 内の dsdur/dsdict.yaml のパス (617 語彙)",
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="出力先 (transcriptions.csv / wavs/ / provenance.json / d2_stats.json を書く)",
    )
    parser.add_argument(
        "--pitch-dirs", nargs="+", default=list(DEFAULT_PITCH_DIRS),
        help=f"走査する pitch dir 名 (既定: {list(DEFAULT_PITCH_DIRS)})",
    )
    args = parser.parse_args(argv)

    summary = convert(args.voicebank_root, args.dsdict, args.out_dir, args.pitch_dirs)

    print(f"n_segments={summary['n_segments']}")
    print(f"n_wav_total={summary['n_wav_total']}")
    print(f"n_entries_total={summary['n_entries_total']}")
    print(f"n_unmapped_total={summary['n_unmapped_total']}")
    print(f"n_sokuon_total={summary['n_sokuon_total']}")
    print(f"total_audio_s={summary['total_audio_s']:.2f}")
    print(
        f"total_voiced_s={summary['total_voiced_s']:.2f} "
        f"({summary['effective_minutes']:.2f} min)"
    )
    print(f"total_sp_s={summary['total_sp_s']:.2f}")
    print(f"n_unique_phoneme_symbols={len(summary['phoneme_symbol_counts'])}")
    print(f"n_unique_moras_used={summary['n_unique_moras_used']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
