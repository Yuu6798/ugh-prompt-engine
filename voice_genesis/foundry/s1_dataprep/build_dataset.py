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


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R4): `--out-dict`/`--out-config`/`--report` が
    互いに衝突する、または保護入力（Ritsu/PJS 両 raw dir 配下の
    transcriptions.csv・wavs/・参照ファイル）と衝突する場合に送出する
    （fail-closed。書き込み前に検出する。`adapter/render.py`/
    `scripts/measure_bands.py` の `OutputCollisionError` と対称の設計）。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """公開対象の全出力パス（resolve 後）を相互および保護入力ルートと照合し、
    衝突があれば公開前に fail-closed で拒否する。

    Codex 再現手順（`--out-dict` に Ritsu の入力 raw dir 配下の CSV を渡す）
    では、検証成功後の `_publish_outputs` がその CSV を辞書内容で上書きし、
    入力そのものを破壊してしまう。resolved 比較（symlink 解決後の完全一致・
    包含）で判定する（AGENTS.md Persistent Artifact Safety Gate 項目2）。

    - 出力同士: `--out-dict`/`--out-config`/`--report` が同一パスを指す場合
      （最後に書いたものが他方を無言で上書きする）。
    - 出力と保護入力: `--ritsu-raw-dir`/`--pjs-raw-dir` 配下（`transcriptions.csv`・
      `wavs/`・その他参照ファイルを含むルートごと）を出力先に指定した場合。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )

    for root in protected_roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p, r in resolved_outs:
            if r == root_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input root {root}（fail-closed で拒否）"
                )
            try:
                r.relative_to(root_resolved)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
            )


def _preflight_writable(paths: Sequence[Path]) -> None:
    """公開対象の全パスについて、書込可能性を事前検証する（実際の書き込みを
    始める前に全件をチェックし、途中失敗による「混合世代」— 辞書は新・config は
    旧のような公開状態 — を未然に防ぐ）。

    - 既存ディレクトリをそのまま出力先に指定した場合（`--out-config` に既存
      ディレクトリを渡す等。Codex 再現手順）は、`os.replace` が
      `IsADirectoryError` を投げて他の成果物だけ公開済みという事故になる前に
      ここで検出する。
    - 親ディレクトリの書込可否も、実際に probe ファイルを作成して確認する
      （`os.access` は root では常に True を返す等の既知の弱点があるため）。
    """
    for path in paths:
        if path.is_dir():
            raise IsADirectoryError(
                f"out path is an existing directory, not a file: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        probe_fd, probe_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".writetest.tmp"
        )
        try:
            os.close(probe_fd)
        finally:
            os.unlink(probe_name)


def _publish_outputs(items: Sequence[Tuple[Path, str]]) -> None:
    """複数出力（辞書/config/report）を検証してから一括で atomic 公開する
    （遷移性: 途中失敗時に混合世代の成果物が残らないようにする）。

    手順: (1) 全パスの書込可能性を事前検証（`_preflight_writable`） →
    (2) 新内容を staging tempfile へ書き込み → (3) 既存ファイルをバックアップ
    へ退避 → (4) staging を `os.replace` で一括公開。(3)/(4) の途中で失敗
    したら、公開済み分をバックアップから巻き戻し、staging/バックアップの
    残骸を片付けてから再送出する。
    """
    paths = [p for p, _ in items]
    _preflight_writable(paths)

    # Phase 1: 新内容を staging tempfile へ書き込む（実パスにはまだ触れない）。
    staged: List[Tuple[Path, str]] = []
    try:
        for path, content in items:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
            except BaseException:
                os.unlink(tmp_name)
                raise
            staged.append((path, tmp_name))
    except BaseException:
        for _path, tmp_name in staged:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise

    # Phase 2: 既存ファイルをバックアップへ退避する（無ければ None を記録）。
    backups: List[Tuple[Path, Optional[str]]] = []
    try:
        for path, _tmp_name in staged:
            if path.exists():
                bak_fd, bak_name = tempfile.mkstemp(
                    dir=path.parent, prefix=f"{path.name}.", suffix=".prepublish-bak"
                )
                os.close(bak_fd)
                os.unlink(bak_name)
                os.replace(path, bak_name)
                backups.append((path, bak_name))
            else:
                backups.append((path, None))
    except BaseException:
        # 退避中の失敗: ここまでの退避分を復元し、staging を破棄する。
        for path, bak_name in backups:
            if bak_name is not None:
                os.replace(bak_name, path)
        for _path, tmp_name in staged:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise

    # Phase 3: staging を実パスへ一括公開する。
    published = 0
    try:
        for path, tmp_name in staged:
            os.replace(tmp_name, path)
            published += 1
    except BaseException:
        # 途中失敗: 公開済み分をバックアップから巻き戻し、未公開分の
        # staging/バックアップを片付ける（旧成果物を確実に復元する）。
        for i, (path, tmp_name) in enumerate(staged):
            bak_name = backups[i][1]
            if i < published:
                if bak_name is not None:
                    os.replace(bak_name, path)
                else:
                    os.unlink(path)
            else:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                if bak_name is not None:
                    os.replace(bak_name, path)
        raise

    # 全件公開に成功: バックアップを削除する。
    for _path, bak_name in backups:
        if bak_name is not None:
            os.unlink(bak_name)


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

    # 全構築・全検証が終わり、成功が確定してからのみ、辞書/config/report を
    # 遷移的に（全て公開できるか事前検証してから一括で）公開する。
    outputs: List[Tuple[Path, str]] = [
        (args.out_dict, dict_text),
        (args.out_config, config_text),
    ]
    if args.report is not None:
        outputs.append((args.report, report_text + "\n"))

    # [P1 修正] (review #263 R4) 全出力パスを相互および両 raw dir（入力の
    # transcriptions.csv/wavs/参照ファイル）と照合し、衝突があれば公開前に
    # fail-closed で拒否する。
    _reject_output_collision(
        [p for p, _ in outputs],
        protected_roots=[args.ritsu_raw_dir, args.pjs_raw_dir],
    )

    try:
        _publish_outputs(outputs)
    except OSError as exc:
        # 公開失敗（既存ディレクトリを出力先に指定 / 書込不可等）。
        # `_publish_outputs` が事前検証・巻き戻しで混合世代を防いだ上での
        # 失敗であり、既存の有効な成果物は無事なはず（呼び出し側で再実行可能）。
        print(f"error: failed to publish outputs: {exc}", file=sys.stderr)
        return 1

    print("validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
