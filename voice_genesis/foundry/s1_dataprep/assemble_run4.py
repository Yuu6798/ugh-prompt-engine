"""S3 Phase D: run 4（3 話者統合学習）向け raw データ構成のローカル組み立て。

正本: `S3_RUN4_RUNBOOK.md` §3（3 話者アセンブリのギャップ記述）/
`DESIGN_S3_backfill.md` §2.4・§4。本スクリプトは runbook §3 が指摘する
「`build_dataset.py` は ritsu/pjs の 2 話者専用にハードコードされており、
D3→ritsu 合流を行う結合スクリプトも存在しない」というギャップを埋める。

**制約（設計判断）**: `build_dataset.py` / `convert_d3.py` / `convert_ritsu.py` /
`convert_user.py` / `gate_synth.py` など既存ファイルには一切触れない。新規
ファイルとして本スクリプトを追加するのみ（`convert_d3.py`/`convert_user.py`
が採用したのと同じ「新規ファイル方式で回帰発火を回避」する設計判断を踏襲）。
既存ロジックの二重実装も避けるため、`build_dataset.py` の
`read_transcriptions`/`validate_speaker`/`check_ph_dur_duration`/
`check_note_dur_consistency`/`collect_phoneme_symbols`/`build_merged_dict`/
`render_dict_text`/`write_dict` は read-only import で再利用する。原子公開
（staging dir 構築 → 検証 → 丸ごと rename）と衝突ガードは `convert_d3.py` の
`_swap_into_place`/`_reject_output_collision`/`OutputCollisionError` を同じく
read-only import で再利用する（`convert_user.py` が `convert_d3.py` からこれら
2 関数を read-only import している既存の sibling-import 前例を踏襲。
`convert_d3.py` 自身は numpy/soundfile/librosa に依存せず軽量なため、本
スクリプトへ重い依存を持ち込まない）。

## 何を組み立てるか

1. **D3→ritsu 合流**: `--ritsu-raw-dir`（D2 = 波音リツ VCV 変換済み raw dir）と
   `--d3-raw-dir`（D3 = リツ歌唱合成 raw dir）の `transcriptions.csv` を行連結
   （ヘッダ 1 回）し、`wavs/` をマージする。合流前に **name 列 + 実 wav
   ファイル名の全数比較**で無衝突を実測確認し、衝突があれば fail-closed で
   拒否する（`S3_RUN4_RUNBOOK.md` §8.6）。D2 の `transcriptions.csv` は
   `name,ph_seq,ph_dur` の 3 列のみ（`note_dur`/`ph_num`/`note_seq` を持たない）
   だが、D3 は `name,ph_seq,ph_dur,ph_num,note_seq,note_dur` の 6 列。マージ後の
   ヘッダは D3 側の 6 列（上位集合）を採用し、D2 由来の行は元のまま **3 列の
   短い行として書く**（末尾 3 列を空文字で埋めない）。これにより
   `csv.DictReader`（既定 `restval=None`）で読み戻した際、D2 行の
   `ph_num`/`note_seq`/`note_dur` は「列が存在しない」を意味する `None` になり、
   `build_dataset.py` の `check_note_dur_consistency`/`check_ph_dur_duration` が
   docstring で明記する「`note_dur` 列そのものを持たない行はスキップする」
   契約と一致する（空文字列 `""` を書くと `note_dur` 列は「存在するが空」と
   解釈され、`check_note_dur_consistency` の R14 修正により誤って
   violation 扱いになるため、意図的に短い行のまま書く）。

2. **3 話者 raw 構成**: `--out-dir` 配下に `ritsu/`（= D2+D3 合流後）・`pjs/`・
   `user/` の 3 ディレクトリを、それぞれ `build_dataset.py` が期待する raw dir
   契約（`transcriptions.csv` + `wavs/`）と同型に組み立てる。pjs/user は
   `--pjs-raw-dir`/`--user-raw-dir` の内容をバイト単位でそのまま複製する
   （`--user-raw-dir` 配下に `exclusions.json` があれば併せて複製する。
   `convert_user.py` の出力契約）。`--out-dir` を自己完結にすることで、将来の
   3 話者対応 `build_dataset.py` 拡張がそのまま
   `--ritsu-raw-dir <out>/ritsu --pjs-raw-dir <out>/pjs --user-raw-dir <out>/user`
   を渡せる形にする。

   `spk_id` は **ritsu=0, pjs=1, user=2 で固定**する。根拠:
   `build_dataset.py` の既存 2 話者版がすでに `ritsu=0, pjs=1` で
   `spk_id`/`spk_map` を確定させており（`build_dataset.py:913-916`）、run 3
   までの checkpoint・spk_embed の割当順序と整合させるには既存 2 者の
   `spk_id` を変更してはならない。user は run 4 で新規追加される第 3 話者
   のため、次の空き番号 `2` を割り当てる。

3. **検証ゲート**: 3 話者（ritsu(=D2+D3) / pjs / user）それぞれに対し
   `validate_speaker` / `check_ph_dur_duration` / `check_note_dur_consistency`
   を呼び、返る問題を全話者分集約する。`build_dataset.py` 本体の既定は
   `check_ph_dur_duration` の乖離を warning 止まり（`--strict-duration`
   指定時のみ problems へ昇格）だが、本スクリプトは 3 検査すべてを無条件で
   problems へ合流させる（`S3_RUN4_RUNBOOK.md` の指示どおり「全問題収集 →
   1 件でもあれば fail-closed」）。1 件でもあれば `GateValidationError` を
   送出し、`--out-dir` には一切書き込まない（staging dir のみ破棄）。

4. **辞書統合**: `build_dataset.py` の `main()` が
   `build_merged_dict([ritsu_symbols, pjs_symbols])` → `render_dict_text` →
   `write_dict` という手順で辞書ファイルを書く実装を一次ソース確認し、
   同じ 3 関数を 3 話者分（ritsu(=D2+D3) 合流後のシンボル集合を含む）に対して
   呼び出し、`<out-dir>/dict.txt` として同型の成果物を生成する。

5. **assembly manifest**: `<out-dir>/assembly_manifest.json` に、話者ごとの
   row 数・wav 数・`ph_dur` 合計秒数・`transcriptions.csv` の sha256・
   **各 wav の `{name: sha256}`**（review #265 R5 P1 追加。公開した wav 実体
   そのものへのバイト束縛。staging 内の実測値のみで手打ちしない）・spk_id
   対応・衝突検査結果（0 件であることの実測記録）を書く。決定論を保つため
   ウォールクロック時刻等は含めない（同一入力 → 同一出力バイトを維持する）。

6. **3 話者学習 config 生成**（review #265 R7 P1 追加）: `build_dataset.py`
   `main()` が 2 話者 config を生成する箇所（`build_config_yaml()`
   `build_dataset.py:740-808`・`speakers` 引数は
   `(speaker_name, spk_id, raw_data_dir, test_prefixes)` の列で **話者数に
   依存しない汎用実装**であることを一次ソース確認済み）をそのまま
   read-only import で 3 話者分呼び出し、`<out-dir>/run4_config_datasets.yaml`
   （実行時 config・絶対パスのまま）+
   `<out-dir>/run4_config_datasets.yaml.normalized.yaml`（pin 用・
   `build_dataset.normalize_path_field()` で `<out-dir>` からの相対パスへ
   正規化したコピー。実行者の home ディレクトリ配置に digest が左右され
   ない）を書く。`datasets:` は ritsu(spk_id=0)/pjs(spk_id=1)/user(spk_id=2)
   の 3 エントリ・`num_spk: 3`（`build_config_yaml` が `len(speakers)` から
   自動算出）。`raw_data_dir`/`dict_path`/`binary_data_dir` はいずれも
   **公開後の最終パス**（`<out-dir>/ritsu` 等）を指す（staging 中の一時パス
   ではない——`_swap_into_place` で `staging_dir` が `out_dir` へ rename
   されるため、staging 中に書く config も最終レイアウトの絶対パスを
   参照する必要がある）。`--binary-data-dir`/`--n-test-prefixes`/
   `--max-updates`/`--val-check-interval`/`--num-ckpt-keep` で
   `build_dataset.py` 同名 CLI 引数と同じノブを上書きできる（既定値は
   `build_dataset.py` の `DEFAULT_MAX_UPDATES`/`DEFAULT_VAL_CHECK_INTERVAL`/
   `DEFAULT_NUM_CKPT_KEEP` をそのまま流用——run 3 が実際に使った 40K
   steps・5K 節目という値と一致）。**`S3_RUN4_RUNBOOK.md` §4 のとおり
   LR/finetune/精度/勾配クリップは `build_dataset.py`/本スクリプトいずれの
   CLI にもフックが無く、run 3 の実 `config.yaml` から手動移植が必要**
   （本関数はあくまで `datasets:`/`num_spk`/学習規模 3 フィールドの節を
   生成するのみ）。

## pjs フィクスチャに関する注意（★本番実行前に必ず確認）

D1 (PJS) の実体はローカルに存在しないため、テスト・ローカル実測では
「合成ミニ pjs フィクスチャ」（`build_dataset` の 3 ゲートを通る最小限の
正当な `transcriptions.csv` + 微小 wav）を `--pjs-raw-dir` に渡して実行する。
`--pjs-is-fixture` を指定すると `assembly_manifest.json` にその旨が明記される。
**本番実行時は `--pjs-raw-dir` を実 PJS 変換済み raw dir（`convert_pjs.py`
の出力）へ差し替えて `--pjs-is-fixture` を外し、再実行する必要がある。**

## `refresh-config-pin` サブコマンド（review #265 R9 P1 追加）

`S3_RUN4_RUNBOOK.md` §4 は「LR/finetune/precision/勾配クリップの 4 項目は
`run4_config_datasets.yaml`（live config）のみへ手動移植せよ」と指示するが、
その手動編集を `.normalized.yaml`（pin 副本。実行者 home 非依存の記録用
コピー）へ反映する手段が無く、pin が編集前のまま取り残されて「実際に
実行された実験」を証明できなくなっていた。本サブコマンドは手動編集後の
live config から `.normalized.yaml` を再生成する:

```bash
python voice_genesis/foundry/s1_dataprep/assemble_run4.py refresh-config-pin \
    --config <out-dir>/run4_config_datasets.yaml
```

パス系フィールド（`dictionaries.ja`/`datasets[].raw_data_dir`/
`binary_data_dir`）のみを正規化し、それ以外のキー・値（手動追記した
LR/finetune/precision/勾配クリップを含む）は live config と完全一致する
ことを書き込み後に再読込して検証する（`_semantic_diff`）。加えて
`datasets[].speaker`/`spk_id` が既定マッピング（ritsu=0/pjs=1/user=2）から
ずれていないかも検査する。不一致はいずれも `ConfigPinMismatchError` で
fail-closed し、`.normalized.yaml` へは一切書き込まない。運用手順は
「手動編集 → `refresh-config-pin` → 学習開始」（`S3_RUN4_RUNBOOK.md` §4）。
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

# --- sibling import: build_dataset.py / convert_d3.py（同ディレクトリ。
# read-only import で再利用する。既存ファイルには一切触れない。
# `convert_user.py` が `convert_d3.py` から `_swap_into_place`/
# `_reject_output_collision` を read-only import している既存の
# sibling-import 前例を踏襲する）。
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import build_dataset  # noqa: E402
import convert_d3  # noqa: E402

# `build_dataset.py` 既存の 2 話者 (`ritsu=0, pjs=1`) を不変のまま維持し、
# run 4 で新規追加される user へ次の空き番号を割り当てる（モジュール
# docstring §2 参照）。
SPK_IDS: Dict[str, int] = {"ritsu": 0, "pjs": 1, "user": 2}


class NameCollisionError(ValueError):
    """D2 (ritsu) と D3 の `transcriptions.csv` `name` 列、または実 wav
    ファイル名が衝突する場合に送出する（fail-closed。合流処理を一切始める前
    に検出する）。"""


class HeaderMismatchError(ValueError):
    """D2/D3 の `transcriptions.csv` ヘッダが、想定する
    `name,ph_seq,ph_dur[,ph_num,note_seq,note_dur]` の位置的接頭辞関係に
    ないと判定した場合に送出する（想定外のスキーマ変更を無警告で通さない）。"""


class GateValidationError(ValueError):
    """3 話者ゲート検証（`validate_speaker`/`check_ph_dur_duration`/
    `check_note_dur_consistency`）が 1 件以上の問題を返した場合に送出する
    （fail-closed。`--out-dir` へは一切公開しない）。"""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__(
            f"{len(self.problems)} problem(s) found during 3-speaker gate validation "
            f"(fail-closed, nothing published): {self.problems[:10]}"
        )


class RefreshConfigPinError(ValueError):
    """P1 修正 (review #265 R9): `refresh-config-pin` の入力 live config が
    存在しない・YAML として読めない・`datasets` を持たない等、再生成の前提が
    満たされない場合に送出する（fail-closed。`.normalized.yaml` へは一切
    書き込まない）。"""


class ConfigPinMismatchError(ValueError):
    """P1 修正 (review #265 R9): 再生成した `.normalized.yaml` が live config
    と「パス系フィールド（`dictionaries.ja`/`datasets[].raw_data_dir`/
    `binary_data_dir`）を除いて完全一致」しなかった場合、または
    `datasets[].speaker`/`spk_id` が既定マッピング（`SPK_IDS`）からずれて
    いた場合に送出する（fail-closed。YAML 往復でのデータ破損・正規化ロジック
    が意図しない箇所へ波及するバグ・手動編集での datasets 節の事故変更を
    検出する安全網。`.normalized.yaml` へは一切書き込まない）。"""

    def __init__(self, diffs: Sequence[str]) -> None:
        self.diffs = list(diffs)
        super().__init__(
            f"{len(self.diffs)} semantic difference(s) between live config and the "
            "regenerated normalized copy (fail-closed, normalized copy not published): "
            f"{self.diffs[:10]}"
        )


# パス系フィールドの位置（`build_dataset.build_config_yaml` の `path_fmt` が
# 触れるフィールドと同じ 3 箇所）。`_semantic_diff` はこれらの位置でのみ
# live/normalized の値の相違を許容する。
def _is_normalized_path_field_location(path: Tuple[object, ...]) -> bool:
    if path in (("dictionaries", "ja"), ("binary_data_dir",)):
        return True
    if len(path) == 3 and path[0] == "datasets" and path[2] == "raw_data_dir":
        return True
    return False


def _semantic_diff(
    live: object, normalized: object, path: Tuple[object, ...] = ()
) -> List[str]:
    """`live`/`normalized` を再帰的に比較し、パス系フィールド
    （`_is_normalized_path_field_location`）を除いて完全一致することを
    確認する。不一致箇所の説明文字列のリストを返す（空リスト = 完全一致）。
    """
    if _is_normalized_path_field_location(path):
        return []
    location = ".".join(str(p) for p in path) or "<root>"
    if isinstance(live, dict) and isinstance(normalized, dict):
        if set(live.keys()) != set(normalized.keys()):
            return [
                f"{location}: key set differs (live={sorted(live.keys())}, "
                f"normalized={sorted(normalized.keys())})"
            ]
        diffs: List[str] = []
        for key in live:
            diffs += _semantic_diff(live[key], normalized[key], path + (key,))
        return diffs
    if isinstance(live, list) and isinstance(normalized, list):
        if len(live) != len(normalized):
            return [f"{location}: list length differs ({len(live)} vs {len(normalized)})"]
        diffs = []
        for i, (a, b) in enumerate(zip(live, normalized)):
            diffs += _semantic_diff(a, b, path + (i,))
        return diffs
    if live != normalized:
        return [f"{location}: value differs (live={live!r}, normalized={normalized!r})"]
    return []


def _load_yaml_config(path: Path) -> Dict[str, object]:
    """YAML config を読み、`dict` かつ `datasets` がリストであることを
    検査する（`refresh_config_pin` の入力/自己検算再読込の両方から使う共通
    ヘルパー）。"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RefreshConfigPinError(f"{path}: not a YAML mapping (fail-closed)")
    if not isinstance(data.get("datasets"), list):
        raise RefreshConfigPinError(f"{path}: 'datasets' is not a list (fail-closed)")
    return data


def _normalize_config_dict(live_config: Dict[str, object], config_dir: Path) -> Dict[str, object]:
    """`live_config` の deep copy を作り、パス系フィールド
    （`dictionaries.ja`/`datasets[].raw_data_dir`/`binary_data_dir`）だけを
    `build_dataset.normalize_path_field` で置換した辞書を返す。それ以外の
    キー・値（LR/finetune/precision/勾配クリップ等、手動追記されたフィールド
    を含む）は一切変更しない。"""
    normalized = copy.deepcopy(live_config)
    dictionaries = normalized.get("dictionaries")
    if isinstance(dictionaries, dict) and "ja" in dictionaries:
        dictionaries["ja"] = build_dataset.normalize_path_field(Path(dictionaries["ja"]), config_dir)
    if "binary_data_dir" in normalized:
        normalized["binary_data_dir"] = build_dataset.normalize_path_field(
            Path(normalized["binary_data_dir"]), config_dir
        )
    for entry in normalized.get("datasets") or []:
        if isinstance(entry, dict) and "raw_data_dir" in entry:
            entry["raw_data_dir"] = build_dataset.normalize_path_field(
                Path(entry["raw_data_dir"]), config_dir
            )
    return normalized


def refresh_config_pin(config_path: Path, config_dir: Optional[Path] = None) -> Path:
    """P1 修正 (review #265 R9): 手動編集後の live config
    (`run4_config_datasets.yaml`) から `.normalized.yaml` pin 副本を再生成
    する（公開エントリポイント。モジュール docstring「`refresh-config-pin`
    サブコマンド」節参照）。

    live config を読み、パス系フィールドだけを `config_dir` 基準の相対パス
    へ正規化した版を staging tempfile へ書き出す。書き込み後に再読込し、
    live config と「パス系フィールドを除いて完全一致」することを
    `_semantic_diff` で検査する——一致しなければ `ConfigPinMismatchError` で
    fail-closed し、`.normalized.yaml` へは一切書き込まない（staging
    tempfile のみで完結・失敗時は削除。`assemble()` の「全構築してから公開」
    規約と同型）。加えて `datasets[].speaker`/`spk_id` が既定マッピング
    （`SPK_IDS`）と一致することも検査する（手動編集で `datasets:` 節を誤って
    触ってしまう典型的な事故の検出）。

    `config_dir`（省略時 `config_path.parent`）: パス正規化の基準ディレクトリ
    （`normalize_path_field` の `config_dir` 引数と同じ意味）。通常
    `run4_config_datasets.yaml` は `<out-dir>` 直下にあるため省略でよい。

    戻り値は書いた `.normalized.yaml` のパス。
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise RefreshConfigPinError(f"{config_path}: not found (fail-closed)")
    config_dir = Path(config_dir) if config_dir is not None else config_path.parent

    live = _load_yaml_config(config_path)

    live_spk_map = {
        entry["speaker"]: entry["spk_id"]
        for entry in live.get("datasets") or []
        if isinstance(entry, dict) and "speaker" in entry and "spk_id" in entry
    }
    if live_spk_map != SPK_IDS:
        raise ConfigPinMismatchError(
            [f"datasets speaker/spk_id mapping does not match SPK_IDS: "
             f"expected={SPK_IDS}, actual={live_spk_map}"]
        )

    expected_normalized = _normalize_config_dict(live, config_dir)

    normalized_path = config_path.with_name(config_path.name + ".normalized.yaml")
    tmp_path = normalized_path.parent / f"{normalized_path.name}.tmp-{os.getpid()}"
    text = yaml.safe_dump(expected_normalized, allow_unicode=True, sort_keys=False)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        reread = _load_yaml_config(tmp_path)
        diffs = _semantic_diff(live, reread)
        if diffs:
            raise ConfigPinMismatchError(diffs)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp_path, normalized_path)
    return normalized_path


def _read_csv_rows(path: Path) -> Tuple[List[str], List[List[str]]]:
    """`path` を生の行リストとして読む（`csv.DictReader` を経由せず、行ごとの
    実フィールド数をそのまま保持する）。D2 由来の短い行（末尾 3 列欠落）を
    パディングせずに扱うために使う。"""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f"empty transcriptions.csv: {path}")
    return rows[0], rows[1:]


def _wav_stems(wavs_dir: Path) -> List[str]:
    if not wavs_dir.is_dir():
        return []
    return sorted(p.stem for p in wavs_dir.glob("*.wav"))


def _check_header_prefix(short_header: Sequence[str], long_header: Sequence[str]) -> None:
    if list(long_header[: len(short_header)]) != list(short_header):
        raise HeaderMismatchError(
            f"ritsu(D2) header {list(short_header)} is not a positional prefix of "
            f"d3 header {list(long_header)} (fail-closed, assembly aborted)"
        )


def _check_no_name_or_wav_collision(
    ritsu_names: Sequence[str],
    d3_names: Sequence[str],
    ritsu_wav_stems: Sequence[str],
    d3_wav_stems: Sequence[str],
) -> None:
    """`name` 列・実 wav ファイル名の双方について、D2/D3 間の衝突を全数比較で
    検出する（`S3_RUN4_RUNBOOK.md` §8.6 の要求）。衝突が 1 件でもあれば
    fail-closed で拒否する。"""
    name_collisions = sorted(set(ritsu_names) & set(d3_names))
    wav_collisions = sorted(set(ritsu_wav_stems) & set(d3_wav_stems))
    if name_collisions or wav_collisions:
        raise NameCollisionError(
            "D2(ritsu)/D3 collision detected (fail-closed, nothing published): "
            f"{len(name_collisions)} name collision(s) {name_collisions[:20]}, "
            f"{len(wav_collisions)} wav filename collision(s) {wav_collisions[:20]}"
        )


def _write_merged_ritsu_csv(
    out_csv: Path,
    header: Sequence[str],
    ritsu_rows: Sequence[Sequence[str]],
    d3_rows: Sequence[Sequence[str]],
) -> None:
    """D2 行（短い行のまま）+ D3 行（フル 6 列）を、`name` 昇順に群内ソート
    してから ritsu ブロック → d3 ブロックの順で連結する。ソートは入力側
    CSV の行順に output が依存しないための決定論強化（同一内容であれば
    行順が変わっても同一バイト列になる）。"""
    ritsu_sorted = sorted(ritsu_rows, key=lambda r: r[0])
    d3_sorted = sorted(d3_rows, key=lambda r: r[0])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(list(header))
        for row in ritsu_sorted:
            writer.writerow(list(row))
        for row in d3_sorted:
            writer.writerow(list(row))


def _copy_file_bytes(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def _copy_wavs(src_dir: Path, dst_dir: Path) -> int:
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for wav_path in sorted(src_dir.glob("*.wav")):
        _copy_file_bytes(wav_path, dst_dir / wav_path.name)
        n += 1
    return n


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ph_dur_total_seconds(rows: Sequence[Dict[str, str]]) -> float:
    values: List[float] = []
    for row in rows:
        values.extend(float(x) for x in row["ph_dur"].split())
    return math.fsum(values)


def _run_gates(speaker_name: str, spk_dir: Path, rows: Sequence[Dict[str, str]]) -> List[str]:
    """`build_dataset.py` の 3 ゲートを 1 話者分呼び出す（read-only import）。
    `check_ph_dur_duration` の乖離は `build_dataset.py` 本体の既定（warning
    止まり）と異なり、本スクリプトでは無条件に problems へ合流させる
    （モジュール docstring §3 参照）。"""
    problems: List[str] = []
    problems += build_dataset.validate_speaker(speaker_name, spk_dir, rows)
    problems += build_dataset.check_ph_dur_duration(speaker_name, spk_dir / "wavs", rows)
    problems += build_dataset.check_note_dur_consistency(speaker_name, rows)
    return problems


def _wav_sha256_map(spk_dir: Path) -> Dict[str, str]:
    """`spk_dir/wavs/*.wav` 各ファイルの `{basename: sha256}` を実測する
    （P1 修正・review #265 R5: `assembly_manifest.json` が wav 数・合計秒数・
    CSV sha のみで、公開した wav 実体そのものへのバイト束縛を持たなかった
    ため、公開後に取り違え/破損した wav があっても manifest 側からは検出
    できなかった）。`_copy_wavs` 完了後・`_swap_into_place` より前の staging
    内 wav 実体に対して行う（`convert_d3.py`/`convert_user.py` の pin 照合と
    同じ「実測のみ・手打ちなし」規約。ファイル名昇順で決定論的に列挙する）。
    """
    wavs_dir = spk_dir / "wavs"
    return {p.name: _sha256_file(p) for p in sorted(wavs_dir.glob("*.wav"))}


def _speaker_manifest_entry(
    spk_dir: Path, rows: Sequence[Dict[str, str]], spk_id: int
) -> Dict[str, object]:
    wav_sha256 = _wav_sha256_map(spk_dir)
    csv_path = spk_dir / "transcriptions.csv"
    return {
        "spk_id": spk_id,
        "row_count": len(rows),
        "wav_count": len(wav_sha256),
        "ph_dur_total_seconds": round(_ph_dur_total_seconds(rows), 3),
        "transcriptions_csv_sha256": _sha256_file(csv_path),
        "wav_sha256": wav_sha256,
    }


def _write_run4_config(
    staging_dir: Path,
    out_dir: Path,
    speaker_rows: Dict[str, List[Dict[str, str]]],
    *,
    binary_data_dir: Optional[Path],
    n_test_prefixes: int,
    max_updates: int,
    val_check_interval: int,
    num_ckpt_keep: int,
) -> Dict[str, object]:
    """P1 修正 (review #265 R7): `build_dataset.py` `build_config_yaml()`
    （`speakers` 引数を話者数非依存の汎用リストとして受ける実装。モジュール
    docstring §6 で一次ソース確認済み）を 3 話者分呼び出し、staging 内へ
    `run4_config_datasets.yaml`（実行時 config・絶対パス）+
    `run4_config_datasets.yaml.normalized.yaml`（pin 用・`out_dir` 基準の
    相対パス）を書く。`raw_data_dir`/`dict_path`/`binary_data_dir` はいずれも
    公開後の最終パス（`out_dir` 基準）を指す——`_swap_into_place` で
    `staging_dir` が `out_dir` へ rename されるため、staging 中に書く config
    も最終レイアウトの絶対パスを参照する必要がある（`build_dataset.py`
    `main()` が呼び出し元から渡された `--*-raw-dir` をそのまま埋め込むのと
    同じ設計）。戻り値は書いた 2 ファイルのパス/sha256（呼び出し側が
    manifest 相当の記録に使いたければ利用できるが、本関数自体は
    `assembly_manifest.json` を変更しない——R5 検証方針「config 生成は
    新規ファイルのみ差分」に合わせ、既存 manifest 構造は不変のまま維持する）。
    """
    final_ritsu_dir = out_dir / "ritsu"
    final_pjs_dir = out_dir / "pjs"
    final_user_dir = out_dir / "user"
    final_dict_path = out_dir / "dict.txt"
    resolved_binary_data_dir = binary_data_dir if binary_data_dir is not None else out_dir / "binary"

    speakers = [
        ("ritsu", SPK_IDS["ritsu"], final_ritsu_dir,
         build_dataset.select_test_prefixes(speaker_rows["ritsu"], n_test_prefixes)),
        ("pjs", SPK_IDS["pjs"], final_pjs_dir,
         build_dataset.select_test_prefixes(speaker_rows["pjs"], n_test_prefixes)),
        ("user", SPK_IDS["user"], final_user_dir,
         build_dataset.select_test_prefixes(speaker_rows["user"], n_test_prefixes)),
    ]

    config_path = staging_dir / "run4_config_datasets.yaml"
    config_text = build_dataset.build_config_yaml(
        dict_path=final_dict_path,
        binary_data_dir=resolved_binary_data_dir,
        speakers=speakers,
        max_updates=max_updates,
        val_check_interval=val_check_interval,
        num_ckpt_keep=num_ckpt_keep,
    )
    config_path.write_text(config_text, encoding="utf-8")

    # `build_dataset.py` main() と同じ命名規約（`<out-config>.normalized.yaml`）
    # で、実行者 home 非依存のパス表現を持つ pin 用コピーを併せて書く。
    normalized_config_path = config_path.with_name(config_path.name + ".normalized.yaml")
    normalized_config_text = build_dataset.build_config_yaml(
        dict_path=final_dict_path,
        binary_data_dir=resolved_binary_data_dir,
        speakers=speakers,
        path_fmt=lambda p: build_dataset.normalize_path_field(p, out_dir),
        max_updates=max_updates,
        val_check_interval=val_check_interval,
        num_ckpt_keep=num_ckpt_keep,
    )
    normalized_config_path.write_text(normalized_config_text, encoding="utf-8")

    return {
        "config_path": config_path.name,
        "normalized_config_path": normalized_config_path.name,
        "config_sha256": _sha256_file(config_path),
        "normalized_config_sha256": _sha256_file(normalized_config_path),
    }


def _assemble_into(
    staging_dir: Path,
    out_dir: Path,
    ritsu_raw_dir: Path,
    d3_raw_dir: Path,
    pjs_raw_dir: Path,
    user_raw_dir: Path,
    *,
    pjs_is_fixture: bool,
    binary_data_dir: Optional[Path],
    n_test_prefixes: int,
    max_updates: int,
    val_check_interval: int,
    num_ckpt_keep: int,
) -> Dict[str, object]:
    """staging_dir 配下に 3 話者 raw 構成 + dict.txt + assembly_manifest.json
    + 3 話者学習 config（`run4_config_datasets.yaml` 系。review #265 R7 P1
    追加、§ `_write_run4_config` docstring 参照）を組み立てる。検証失敗時は
    例外を送出し、呼び出し側 (`assemble`) が staging_dir ごと破棄する
    （`--out-dir` は一切変更されない）。`out_dir` は config 内の
    `raw_data_dir`/`dict_path`/`binary_data_dir` を公開後の最終パスで書く
    ために必要（staging_dir 自身のパスは `_swap_into_place` 後に消える
    一時名のため使えない）。"""
    # --- 1. D2(ritsu)/D3 合流 -------------------------------------------------
    ritsu_csv = ritsu_raw_dir / "transcriptions.csv"
    d3_csv = d3_raw_dir / "transcriptions.csv"
    ritsu_header, ritsu_rows_raw = _read_csv_rows(ritsu_csv)
    d3_header, d3_rows_raw = _read_csv_rows(d3_csv)
    _check_header_prefix(ritsu_header, d3_header)

    ritsu_names = [r[0] for r in ritsu_rows_raw]
    d3_names = [r[0] for r in d3_rows_raw]
    ritsu_wav_stems = _wav_stems(ritsu_raw_dir / "wavs")
    d3_wav_stems = _wav_stems(d3_raw_dir / "wavs")
    _check_no_name_or_wav_collision(ritsu_names, d3_names, ritsu_wav_stems, d3_wav_stems)

    merged_ritsu_dir = staging_dir / "ritsu"
    _write_merged_ritsu_csv(
        merged_ritsu_dir / "transcriptions.csv", d3_header, ritsu_rows_raw, d3_rows_raw
    )
    n_ritsu_wavs_copied = _copy_wavs(ritsu_raw_dir / "wavs", merged_ritsu_dir / "wavs")
    n_d3_wavs_copied = _copy_wavs(d3_raw_dir / "wavs", merged_ritsu_dir / "wavs")

    # --- 2. pjs/user はバイト単位でそのまま複製 -------------------------------
    pjs_dir = staging_dir / "pjs"
    _copy_file_bytes(pjs_raw_dir / "transcriptions.csv", pjs_dir / "transcriptions.csv")
    _copy_wavs(pjs_raw_dir / "wavs", pjs_dir / "wavs")

    user_dir = staging_dir / "user"
    _copy_file_bytes(user_raw_dir / "transcriptions.csv", user_dir / "transcriptions.csv")
    _copy_wavs(user_raw_dir / "wavs", user_dir / "wavs")
    user_exclusions_src = user_raw_dir / "exclusions.json"
    user_has_exclusions = user_exclusions_src.exists()
    if user_has_exclusions:
        _copy_file_bytes(user_exclusions_src, user_dir / "exclusions.json")

    # --- 3. 3 話者ゲート検証（全問題収集 → 1 件でもあれば fail-closed） -------
    # P1 修正 (review #265): 話者の transcriptions.csv が行 0 件（空データ
    # セット）の場合、`validate_speaker`/`check_ph_dur_duration`/
    # `check_note_dur_consistency` はいずれも空リストに対して no-op で
    # `problems=[]` を返す（`convert_d3.discover_pairs()` 0 件と同型の
    # false-success 経路）。ゲートを回す前に明示的に検出し、空話者があれば
    # 他の gate 違反と合わせて fail-closed する（staging へは書き込み済みだが
    # `assemble()` 側で staging ごと破棄され `out_dir` は無変更のまま残る）。
    speaker_dirs = {"ritsu": merged_ritsu_dir, "pjs": pjs_dir, "user": user_dir}
    speaker_rows: Dict[str, List[Dict[str, str]]] = {}
    all_problems: List[str] = []
    for name, spk_dir in speaker_dirs.items():
        rows = build_dataset.read_transcriptions(spk_dir / "transcriptions.csv")
        speaker_rows[name] = rows
        if not rows:
            all_problems.append(
                f"{name}: transcriptions.csv has zero row(s) after assembly "
                "(fail-closed; refusing to publish an empty speaker corpus)"
            )
            continue
        all_problems += _run_gates(name, spk_dir, rows)
    if all_problems:
        raise GateValidationError(all_problems)

    # --- 4. 辞書統合（build_dataset.py main() と同じ 3 関数を read-only 再利用） ---
    symbol_sets = [
        build_dataset.collect_phoneme_symbols(speaker_rows[name]) for name in ("ritsu", "pjs", "user")
    ]
    merged_pairs = build_dataset.build_merged_dict(symbol_sets)
    dict_path = staging_dir / "dict.txt"
    build_dataset.write_dict(dict_path, merged_pairs)

    # --- 4.5. 3 話者学習 config 生成（review #265 R7 P1・§ _write_run4_config） ---
    _write_run4_config(
        staging_dir, out_dir, speaker_rows,
        binary_data_dir=binary_data_dir, n_test_prefixes=n_test_prefixes,
        max_updates=max_updates, val_check_interval=val_check_interval,
        num_ckpt_keep=num_ckpt_keep,
    )

    # --- 5. assembly manifest（決定論のためウォールクロック時刻を含めない） ---
    manifest: Dict[str, object] = {
        "schema": "run4-assembly-manifest/0.2",
        "spk_id": dict(SPK_IDS),
        "speakers": {
            "ritsu": {
                **_speaker_manifest_entry(merged_ritsu_dir, speaker_rows["ritsu"], SPK_IDS["ritsu"]),
                "components": ["d2", "d3"],
                "d2_row_count": len(ritsu_rows_raw),
                "d3_row_count": len(d3_rows_raw),
                "d2_wav_count_copied": n_ritsu_wavs_copied,
                "d3_wav_count_copied": n_d3_wavs_copied,
            },
            "pjs": {
                **_speaker_manifest_entry(pjs_dir, speaker_rows["pjs"], SPK_IDS["pjs"]),
                "is_fixture": pjs_is_fixture,
            },
            "user": {
                **_speaker_manifest_entry(user_dir, speaker_rows["user"], SPK_IDS["user"]),
                "has_exclusions_json": user_has_exclusions,
            },
        },
        "collision_check": {
            "ritsu_d3_name_collisions": [],
            "ritsu_d3_wav_filename_collisions": [],
            "ritsu_names_checked": len(ritsu_names),
            "d3_names_checked": len(d3_names),
        },
        "dict": {
            "path": "dict.txt",
            "symbol_count": len(merged_pairs),
            "sha256": _sha256_file(dict_path),
        },
        "gate": {
            "checks": ["validate_speaker", "check_ph_dur_duration", "check_note_dur_consistency"],
            "problems": [],
        },
    }
    if pjs_is_fixture:
        manifest.setdefault("notes", []).append(  # type: ignore[union-attr]
            "pjs はフィクスチャ実測（合成ミニ pjs）。本番実行には --pjs-raw-dir を"
            "実 PJS 変換済み raw dir（convert_pjs.py の出力）へ差し替えて再実行が必要。"
        )

    manifest_path = staging_dir / "assembly_manifest.json"
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    manifest_path.write_text(manifest_text, encoding="utf-8")

    return manifest


def assemble(
    ritsu_raw_dir: Path,
    d3_raw_dir: Path,
    pjs_raw_dir: Path,
    user_raw_dir: Path,
    out_dir: Path,
    *,
    pjs_is_fixture: bool = False,
    binary_data_dir: Optional[Path] = None,
    n_test_prefixes: int = 5,
    max_updates: int = build_dataset.DEFAULT_MAX_UPDATES,
    val_check_interval: int = build_dataset.DEFAULT_VAL_CHECK_INTERVAL,
    num_ckpt_keep: int = build_dataset.DEFAULT_NUM_CKPT_KEEP,
) -> Dict[str, object]:
    """`out_dir` に 3 話者 run4 raw 構成 + 3 話者学習 config を組み立てる
    （公開エントリポイント）。

    `<out_dir>.staging-<pid>` に完全構築 → 全検証を通過して初めて `out_dir`
    と原子的に swap する（`convert_d3.py` `_swap_into_place` を read-only
    import で再利用。途中失敗・検証失敗時は staging を破棄し、既存の
    `out_dir` はそのまま残る）。

    P1 修正 (review #265): 衝突検査 (`convert_d3._reject_output_collision`)
    はこの公開関数自身が行う（旧実装は CLI `main()` のみが preflight として
    呼んでおり、`assemble()` を非 CLI 経路から呼ぶと `--out-dir` が
    4 つの raw dir のいずれかと衝突していても無検査で通過し得た）。

    `binary_data_dir`/`n_test_prefixes`/`max_updates`/`val_check_interval`/
    `num_ckpt_keep`（review #265 R7 P1 追加）: `run4_config_datasets.yaml`
    生成用のノブ（§ `_write_run4_config` docstring 参照）。`binary_data_dir`
    省略時は `out_dir / "binary"` を使う。他は `build_dataset.py` の既定値
    （`n_test_prefixes` は同スクリプト `--n-test-prefixes` の既定 5、他は
    `DEFAULT_MAX_UPDATES`/`DEFAULT_VAL_CHECK_INTERVAL`/`DEFAULT_NUM_CKPT_KEEP`）
    をそのまま流用する。
    """
    ritsu_raw_dir = Path(ritsu_raw_dir)
    d3_raw_dir = Path(d3_raw_dir)
    pjs_raw_dir = Path(pjs_raw_dir)
    user_raw_dir = Path(user_raw_dir)
    out_dir = Path(out_dir)
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    convert_d3._reject_output_collision(
        [out_dir, old_dir, staging_dir],
        protected_roots=[ritsu_raw_dir, d3_raw_dir, pjs_raw_dir, user_raw_dir],
    )

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        manifest = _assemble_into(
            staging_dir, out_dir, ritsu_raw_dir, d3_raw_dir, pjs_raw_dir, user_raw_dir,
            pjs_is_fixture=pjs_is_fixture,
            binary_data_dir=Path(binary_data_dir) if binary_data_dir is not None else None,
            n_test_prefixes=n_test_prefixes, max_updates=max_updates,
            val_check_interval=val_check_interval, num_ckpt_keep=num_ckpt_keep,
        )
        convert_d3._swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return manifest


def _main_assemble(argv: Optional[Sequence[str]] = None) -> int:
    """既定のサブコマンド（省略可）: 3 話者アセンブリ + config 生成。旧
    `main()` の全内容（review #265 R9 でサブコマンド分岐の追加に伴い改名。
    後方互換のため引数・挙動は完全不変 — 既存の runbook 呼び出し
    （`--ritsu-raw-dir ...` から始まる従来どおりのフラット引数）は
    `main()` がそのままここへ委譲する）。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ritsu-raw-dir", type=Path, required=True,
        help="D2 = convert_ritsu.py の --out-dir (transcriptions.csv + wavs/)",
    )
    parser.add_argument(
        "--d3-raw-dir", type=Path, required=True,
        help="D3 = convert_d3.py の --out-dir (transcriptions.csv + wavs/)",
    )
    parser.add_argument(
        "--pjs-raw-dir", type=Path, required=True,
        help="D1 = convert_pjs.py の diffsinger_db/（本番）。ローカル実測では合成"
             "ミニ pjs フィクスチャで代用可（--pjs-is-fixture を併せて指定する）。",
    )
    parser.add_argument(
        "--user-raw-dir", type=Path, required=True,
        help="convert_user.py の --out-dir (transcriptions.csv + wavs/ + exclusions.json)",
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="run4 raw 構成の出力先")
    parser.add_argument(
        "--pjs-is-fixture", action="store_true",
        help="--pjs-raw-dir が合成ミニ pjs フィクスチャであることを "
             "assembly_manifest.json に明記する（本番実行では指定しない）。",
    )
    # review #265 R7 P1: 3 話者学習 config 生成ノブ（build_dataset.py 同名
    # CLI 引数と同じ意味・既定値。§ _write_run4_config / assemble docstring 参照）。
    parser.add_argument(
        "--binary-data-dir", type=Path, default=None,
        help="run4_config_datasets.yaml の binary_data_dir に書く binarize 出力先"
             "（未生成のパスでよい。省略時は <out-dir>/binary）。",
    )
    parser.add_argument(
        "--n-test-prefixes", type=int, default=5,
        help="話者ごとの検証用セグメント数（build_dataset.py --n-test-prefixes と同義。既定 5）。",
    )
    parser.add_argument(
        "--max-updates", type=int, default=build_dataset.DEFAULT_MAX_UPDATES,
        help=f"config へ書き込む max_updates (既定: {build_dataset.DEFAULT_MAX_UPDATES})",
    )
    parser.add_argument(
        "--val-check-interval", type=int, default=build_dataset.DEFAULT_VAL_CHECK_INTERVAL,
        help=f"config へ書き込む val_check_interval (既定: {build_dataset.DEFAULT_VAL_CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--num-ckpt-keep", type=int, default=build_dataset.DEFAULT_NUM_CKPT_KEEP,
        help=f"config へ書き込む num_ckpt_keep (既定: {build_dataset.DEFAULT_NUM_CKPT_KEEP})",
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    # P1 修正 (review #265): 衝突検査は `assemble()` 自身が行う（公開関数へ
    # 移設済み。CLI 側の preflight 二重実装はしない）。
    try:
        manifest = assemble(
            args.ritsu_raw_dir, args.d3_raw_dir, args.pjs_raw_dir, args.user_raw_dir, out_dir,
            pjs_is_fixture=args.pjs_is_fixture,
            binary_data_dir=args.binary_data_dir, n_test_prefixes=args.n_test_prefixes,
            max_updates=args.max_updates, val_check_interval=args.val_check_interval,
            num_ckpt_keep=args.num_ckpt_keep,
        )
    except (
        convert_d3.OutputCollisionError, NameCollisionError, HeaderMismatchError, GateValidationError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print("assembly OK")
    return 0


def _main_refresh_config_pin(argv: Optional[Sequence[str]] = None) -> int:
    """`refresh-config-pin` サブコマンド本体（review #265 R9 P1。モジュール
    docstring「`refresh-config-pin` サブコマンド」節参照）。"""
    parser = argparse.ArgumentParser(
        prog="assemble_run4.py refresh-config-pin",
        description=(
            "手動編集後の run4_config_datasets.yaml（live config）から "
            ".normalized.yaml pin 副本を再生成する（review #265 R9 P1 対応）。"
        ),
    )
    parser.add_argument(
        "--config", type=Path, required=True,
        help="編集済みの run4_config_datasets.yaml（live config）",
    )
    parser.add_argument(
        "--config-dir", type=Path, default=None,
        help="パス正規化の基準ディレクトリ（省略時 --config の親ディレクトリ）",
    )
    args = parser.parse_args(argv)

    try:
        normalized_path = refresh_config_pin(args.config, config_dir=args.config_dir)
    except (RefreshConfigPinError, ConfigPinMismatchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {normalized_path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI エントリポイント。第 1 引数が `refresh-config-pin`（`--` で始まら
    ない裸のトークン）なら R9 で追加したサブコマンドへ委譲し、それ以外
    （既存フラグの `--ritsu-raw-dir ...` から始まる従来どおりの呼び出し）は
    そのまま `_main_assemble` へ渡す——既存の全 CLI 呼び出し・`assemble()`
    呼び出しは完全不変（後方互換）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "refresh-config-pin":
        return _main_refresh_config_pin(argv[1:])
    return _main_assemble(argv)


if __name__ == "__main__":
    raise SystemExit(main())
