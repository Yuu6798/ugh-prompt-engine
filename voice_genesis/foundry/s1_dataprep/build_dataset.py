"""S1 データ工場: リツ (D2) + PJS (D1) の 2 話者統合 + 辞書生成 + 検証。

出力は `openvpi/DiffSinger` の `scripts/binarize.py` にそのまま渡せる多話者
acoustic config（`datasets:` に 2 エントリ・`spk_id`/`num_spk` 付き）と、統合
音素辞書（恒等写像）。

**実装決定**（`s1b_dataset_record.md` §5.1 の判断を再現）: 単一マージ CSV では
なく openvpi ネイティブの複数 `datasets:` エントリ方式を採用する。理由:
`AcousticBinarizer` は dataset 単位で `spk_map`/`test_prefixes` を解決する
設計であり、単一マージ CSV 方式は openvpi のネイティブな読み込み経路と整合
しないため。したがって本スクリプトは `convert_ritsu.py`/`convert_pjs.py` の
出力（各話者の `raw_data_dir`）をそのまま参照する config を書き出すのみで、
wav やラベルを新たにコピー・結合することはしない。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# AP/SP は DiffSinger の `PhonemeDictionary` が自動登録する特殊トークンであり、
# 辞書ファイルへは含めない（`s1a_conversion_record.md` §3 / `s1b_dataset_record.md`
# §5.1 と同じ扱い）。
SPECIAL_TOKENS: Set[str] = {"AP", "SP"}


def read_transcriptions(csv_path: Path) -> List[Dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def collect_phoneme_symbols(rows: Sequence[Dict[str, str]]) -> Set[str]:
    symbols: Set[str] = set()
    for row in rows:
        symbols.update(row["ph_seq"].split())
    return symbols


def select_test_prefixes(rows: Sequence[Dict[str, str]], n: int = 5) -> List[str]:
    """決定論的な検証用サブセット選択。

    `name` 昇順に並べ、区間を `n` 等分した位置（先頭・末尾を含む）から選ぶ。
    件数が `n` 以下ならそのまま全件を返す。乱数は使わない。
    """
    names = sorted(row["name"] for row in rows)
    if len(names) <= n:
        return names
    if n <= 1:
        return names[:1]
    picked: List[str] = []
    seen: Set[str] = set()
    for i in range(n):
        idx = round(i * (len(names) - 1) / (n - 1))
        name = names[idx]
        if name not in seen:
            seen.add(name)
            picked.append(name)
    # 等分位置が丸めで重複した分は末尾側から埋め直す（常に n 件を確保する）。
    i = len(names) - 1
    while len(picked) < n and i >= 0:
        if names[i] not in seen:
            seen.add(names[i])
            picked.append(names[i])
        i -= 1
    return sorted(picked)


def build_merged_dict(symbol_sets: Sequence[Set[str]]) -> List[Tuple[str, str]]:
    """全話者の phoneme symbol 和集合（AP/SP 除く）を恒等写像辞書として返す。"""
    union: Set[str] = set()
    for s in symbol_sets:
        union.update(s)
    union -= SPECIAL_TOKENS
    return [(sym, sym) for sym in sorted(union)]


def render_dict_text(pairs: Sequence[Tuple[str, str]]) -> str:
    """`write_dict` が書き出す辞書ファイルの内容をテキストとして組み立てる
    （atomic 公開のため、書き込み前に全文をメモリ上で確定させる）。"""
    return "".join(f"{sym}\t{mapped}\n" for sym, mapped in pairs)


def write_dict(path: Path, pairs: Sequence[Tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_dict_text(pairs))


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """staging tempfile へ書き込み、成功後にのみ `os.replace` で `path` へ
    atomic 公開する（`adapter/donor_bank.py _atomic_stage_and_replace` /
    `adapter/voice_spec.py save_voice_spec` と同じ流儀。AGENTS.md Persistent
    Artifact Safety Gate 項目6「全構築後公開」準拠）。

    [P2 修正] `problems` が非空でも辞書/config/report を書いてから失敗
    return していたため、失敗した再実行が既存の有効な成果物を壊し得た。
    呼び出し側 (`main`) で全構築・全検証を終えてから、成功時のみこの関数で
    公開する構造へ是正。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def validate_speaker(
    speaker_name: str, raw_dir: Path, rows: Sequence[Dict[str, str]]
) -> List[str]:
    """1 話者分の `transcriptions.csv` + `wavs/` の整合性を検査する。

    問題があっても例外にせず、問題メッセージのリストとして返す（呼び出し側が
    全話者分を集約してから最終的に fail-closed で止める）。
    """
    problems: List[str] = []
    wav_dir = raw_dir / "wavs"
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        dup = sorted({n for n in names if names.count(n) > 1})
        problems.append(f"{speaker_name}: duplicate name(s) in transcriptions.csv: {dup[:5]}")

    missing = [n for n in sorted(set(names)) if not (wav_dir / f"{n}.wav").exists()]
    if missing:
        problems.append(f"{speaker_name}: {len(missing)} wav missing under {wav_dir}, e.g. {missing[:3]}")

    for row in rows:
        ph_seq = row["ph_seq"].split()
        try:
            ph_dur = [float(x) for x in row["ph_dur"].split()]
        except ValueError:
            problems.append(f"{speaker_name}: {row['name']} has non-numeric ph_dur")
            continue
        if len(ph_seq) != len(ph_dur):
            problems.append(
                f"{speaker_name}: {row['name']} ph_seq/ph_dur length mismatch "
                f"({len(ph_seq)} vs {len(ph_dur)})"
            )
        if any(d <= 0 for d in ph_dur):
            problems.append(f"{speaker_name}: {row['name']} has non-positive ph_dur")
    return problems


def build_config_yaml(
    dict_path: Path,
    binary_data_dir: Path,
    speakers: Sequence[Tuple[str, int, Path, Sequence[str]]],
) -> str:
    """`scripts/binarize.py --config <this>` にそのまま渡せる acoustic config
    を組み立てる（`s1b_multispeaker_acoustic_config.yaml` と同一構造）。
    `speakers` は `(speaker_name, spk_id, raw_data_dir, test_prefixes)` の列。
    """
    lines: List[str] = []
    lines.append("base_config:")
    lines.append("  - configs/acoustic.yaml")
    lines.append("")
    lines.append("dictionaries:")
    lines.append(f"  ja: {dict_path}")
    lines.append("extra_phonemes: []")
    lines.append("merged_phoneme_groups: []")
    lines.append("")
    lines.append("datasets:")
    for name, spk_id, raw_dir, prefixes in speakers:
        lines.append(f"  - raw_data_dir: {raw_dir}")
        lines.append(f"    speaker: {name}")
        lines.append(f"    spk_id: {spk_id}")
        lines.append("    language: ja")
        lines.append("    test_prefixes:")
        for p in prefixes:
            lines.append(f"      - {p}")
    lines.append("")
    lines.append(f"binary_data_dir: {binary_data_dir}")
    lines.append("")
    lines.append("# CPU-only, checkpoint-free feature extraction")
    lines.append("# (S1a/S1b と同じ理由: hnsep 既定 'vr' は checkpoints/vr/model.pt を")
    lines.append("# 無条件ロードするが CPU オフライン環境では未取得のため 'world' へ上書き)")
    lines.append("pe: parselmouth")
    lines.append("hnsep: world")
    lines.append("")
    lines.append("use_lang_id: false")
    lines.append("num_lang: 1")
    lines.append("use_spk_id: true")
    lines.append(f"num_spk: {len(speakers)}")
    lines.append("")
    lines.append("binarization_args:")
    lines.append("  shuffle: true")
    lines.append("  num_workers: 0")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ritsu-raw-dir", type=Path, required=True,
        help="convert_ritsu.py の --out-dir (transcriptions.csv + wavs/ を含む)",
    )
    parser.add_argument(
        "--pjs-raw-dir", type=Path, required=True,
        help="convert_pjs.py が生成する '<staging-dir>/diffsinger_db/'",
    )
    parser.add_argument("--out-dict", type=Path, required=True, help="統合辞書の出力先")
    parser.add_argument("--out-config", type=Path, required=True, help="多話者 acoustic config の出力先")
    parser.add_argument(
        "--binary-data-dir", type=Path, required=True,
        help="config の binary_data_dir に書く binarize 出力先 (未生成のパスでよい)",
    )
    parser.add_argument("--n-test-prefixes", type=int, default=5, help="話者ごとの検証用セグメント数")
    parser.add_argument("--report", type=Path, default=None, help="検証レポート JSON の出力先 (省略可)")
    args = parser.parse_args(argv)

    ritsu_csv = args.ritsu_raw_dir / "transcriptions.csv"
    pjs_csv = args.pjs_raw_dir / "transcriptions.csv"
    if not ritsu_csv.exists():
        print(f"error: not found: {ritsu_csv}", file=sys.stderr)
        return 1
    if not pjs_csv.exists():
        print(f"error: not found: {pjs_csv}", file=sys.stderr)
        return 1

    ritsu_rows = read_transcriptions(ritsu_csv)
    pjs_rows = read_transcriptions(pjs_csv)

    problems: List[str] = []
    problems += validate_speaker("ritsu", args.ritsu_raw_dir, ritsu_rows)
    problems += validate_speaker("pjs", args.pjs_raw_dir, pjs_rows)

    ritsu_symbols = collect_phoneme_symbols(ritsu_rows)
    pjs_symbols = collect_phoneme_symbols(pjs_rows)
    merged_pairs = build_merged_dict([ritsu_symbols, pjs_symbols])
    dict_text = render_dict_text(merged_pairs)

    ritsu_prefixes = select_test_prefixes(ritsu_rows, args.n_test_prefixes)
    pjs_prefixes = select_test_prefixes(pjs_rows, args.n_test_prefixes)

    config_text = build_config_yaml(
        dict_path=args.out_dict,
        binary_data_dir=args.binary_data_dir,
        speakers=[
            ("ritsu", 0, args.ritsu_raw_dir, ritsu_prefixes),
            ("pjs", 1, args.pjs_raw_dir, pjs_prefixes),
        ],
    )

    report = {
        "ritsu_segments": len(ritsu_rows),
        "pjs_segments": len(pjs_rows),
        "total_segments": len(ritsu_rows) + len(pjs_rows),
        "merged_dict_symbols": len(merged_pairs),
        "ritsu_only_symbols": sorted(ritsu_symbols - pjs_symbols - SPECIAL_TOKENS),
        "pjs_only_symbols": sorted(pjs_symbols - ritsu_symbols - SPECIAL_TOKENS),
        "shared_symbols": sorted((ritsu_symbols & pjs_symbols) - SPECIAL_TOKENS),
        "ritsu_test_prefixes": ritsu_prefixes,
        "pjs_test_prefixes": pjs_prefixes,
        "problems": problems,
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(report_text)

    if problems:
        # [P2 修正] 検証失敗時は辞書/config/report のいずれも公開しない
        # （全構築・全検証をここまでで終え、失敗が確定したら成果物には
        # 触れずに return する。既存の有効な成果物を壊さない）。
        print(f"validation FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1

    # 全構築・全検証が終わり、成功が確定してからのみ atomic 公開する。
    _atomic_write_text(args.out_dict, dict_text)
    _atomic_write_text(args.out_config, config_text)
    if args.report is not None:
        _atomic_write_text(args.report, report_text + "\n")

    print("validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
