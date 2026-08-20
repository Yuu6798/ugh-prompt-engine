"""S4: run 5（4 話者統合学習・spk_id map v2）向け raw データ構成のローカル組み立て。

正本: `DESIGN_S4_run5.md` §1（spk_id map v2 = D3 話者分離）・§2-1/2-2
（コード変更契約）。run 4 までの本スクリプトは D3（リツ歌唱合成）を
D2(ritsu) へ行連結マージする 3 話者構成だった（旧正本 =
`S3_RUN4_RUNBOOK.md` §3 / `DESIGN_S3_backfill.md` §2.4・§4）が、run 5 は
D3 を専用話者 **`d3synth`（spk_id=3・合成教師）** として分離する
（DESIGN_S4 §1.1・Q6 の検証対象）。これに伴い **D2/D3 マージサブシステム
（ヘッダ接頭辞検査・`_write_merged_ritsu_csv` の CSV 行連結・D2/D3 間
name/wav 衝突検査）は撤去**し、4 話者すべてを同型の「バイト単位コピー」
経路で組み立てる。v1（3 話者マージ版）との切替フラグは作らない —
run 5 以降は v2 のみとし、過去 run の再現は git 履歴が担う（DESIGN_S4 §2-1）。

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

1. **4 話者 raw 構成（全話者バイト単位コピー）**: `--out-dir` 配下に
   `ritsu/`（= D2 のみ。D3 はもうマージしない）・`pjs/`・`user/`・
   `d3synth/`（= `--d3-raw-dir` = `convert_d3.py` 出力）の 4 ディレクトリを、
   それぞれ `build_dataset.py` が期待する raw dir 契約
   （`transcriptions.csv` + `wavs/`）と同型に組み立てる。4 話者すべて
   入力 raw dir の内容をバイト単位でそのまま複製する（`--user-raw-dir`
   配下に `exclusions.json` があれば併せて複製する。`convert_user.py` の
   出力契約）。D2 の `transcriptions.csv` は `name,ph_seq,ph_dur` の 3 列
   のみだが、`build_dataset.py` の検証関数群は `csv.DictReader`（既定
   `restval=None`）経由で「`note_dur` 列そのものを持たない行はスキップ
   する」契約を持つため、3 列 CSV をそのまま複製してよい（run 4 の
   マージ実装が依拠していたのと同一の契約。マージ撤去後は加工そのものが
   無くなるので短い行の書き分け問題も消滅する）。

   `spk_id` は **ritsu=0, pjs=1, user=2, d3synth=3 で固定**する。根拠:
   run 3/run 4 checkpoint・spk_embed の割当順序と整合させるには既存話者の
   `spk_id` を変更してはならない（**追加は末尾のみ**の規律。DESIGN_S4
   §1.1）。d3synth は run 5 で新設される第 4 話者（合成教師 — リツの
   声色分布から合成音アーティファクトを退避しつつ共有デコーダ経由の
   音素矯正効果を保つ仮説 = Q6）のため、次の空き番号 `3` を割り当てる。

2. **検証ゲート**: 4 話者（ritsu(=D2) / pjs / user / d3synth）それぞれに
   対し `validate_speaker` / `check_ph_dur_duration` /
   `check_note_dur_consistency` を呼び、返る問題を全話者分集約する。
   `build_dataset.py` 本体の既定は `check_ph_dur_duration` の乖離を
   warning 止まり（`--strict-duration` 指定時のみ problems へ昇格）だが、
   本スクリプトは 3 検査すべてを無条件で problems へ合流させる
   （「全問題収集 → 1 件でもあれば fail-closed」）。1 件でもあれば
   `GateValidationError` を送出し、`--out-dir` には一切書き込まない
   （staging dir のみ破棄）。

3. **辞書統合**: `build_dataset.py` の `main()` が
   `build_merged_dict([...])` → `render_dict_text` → `write_dict` という
   手順で辞書ファイルを書く実装を一次ソース確認済み（run 4 時代から
   不変）。同じ 3 関数を 4 話者分のシンボル集合に対して呼び出し、
   `<out-dir>/dict.txt` として同型の成果物を生成する。

4. **assembly manifest**: `<out-dir>/assembly_manifest.json` に、話者ごとの
   row 数・wav 数・`ph_dur` 合計秒数・`transcriptions.csv` の sha256・
   **各 wav の `{name: sha256}`**（review #265 R5 P1 追加。公開した wav 実体
   そのものへのバイト束縛。staging 内の実測値のみで手打ちしない）・spk_id
   対応を書く。**user 話者の `exclusions.json`（存在する場合）は公開
   バイトから実測した sha256 も `exclusions_json_sha256` として記帳する**
   （review #265 R11 P2: 従来は `has_exclusions_json` の真偽値のみで、
   コピーしたファイル実体へのバイト束縛を持たなかった）。**生成した学習
   config（`config_sha256`/`normalized_config_sha256`）も併せて記帳する**
   （review #265 R11 P1、§5 参照）。schema は
   **`run4-assembly-manifest/0.4`**（4 話者化に伴い、D2/D3 マージ専用
   だった `collision_check` セクションと ritsu の `components`/
   `d2_row_count`/`d3_row_count`/`d2_wav_count_copied`/`d3_wav_count_copied`
   フィールドを撤去。それ以外は 0.3 と同一）。決定論を保つため
   ウォールクロック時刻等は含めない（同一入力 → 同一出力バイトを維持する）。

5. **4 話者学習 config 生成**（review #265 R7 P1 追加）: `build_dataset.py`
   `main()` が 2 話者 config を生成する箇所（`build_config_yaml()`
   `build_dataset.py:740-808`・`speakers` 引数は
   `(speaker_name, spk_id, raw_data_dir, test_prefixes)` の列で **話者数に
   依存しない汎用実装**であることを一次ソース確認済み）をそのまま
   read-only import で 4 話者分呼び出し、`<out-dir>/run4_config_datasets.yaml`
   （実行時 config・絶対パスのまま）+
   `<out-dir>/run4_config_datasets.yaml.normalized.yaml`（pin 用・
   `build_dataset.normalize_path_field()` で `<out-dir>` からの相対パスへ
   正規化したコピー。実行者の home ディレクトリ配置に digest が左右され
   ない）を書く。`datasets:` は ritsu(spk_id=0)/pjs(spk_id=1)/user(spk_id=2)/
   d3synth(spk_id=3) の 4 エントリ・`num_spk: 4`（`build_config_yaml` が
   `len(speakers)` から自動算出。spk_embed テーブルへの行 1 本追加 =
   DESIGN_S4 §1.1 が「1 変数」と数える差分の帰結）。`raw_data_dir`/`dict_path`/`binary_data_dir` はいずれも
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
   生成するのみ）。**生成した 2 ファイル（live/normalized）の sha256 は
   `assembly_manifest.json` の `config` セクションへ記帳する**（review #265
   R11 P1: 旧実装は `_write_run4_config()` の戻り値（sha256 2 値）を呼び出し
   側 `_assemble_into()` が握り潰しており、manifest 上には config の実体を
   証明する情報が一切残らなかった。`refresh-config-pin` 実行時（§
   `refresh-config-pin` サブコマンド節参照）もこの記帳値を実測で更新する）。

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
`datasets[].speaker`/`spk_id` が既定マッピング（ritsu=0/pjs=1/user=2/
d3synth=3）から
ずれていないかも検査する——**畳んだ `{speaker: spk_id}` マップではなく
`datasets` の生リスト形状そのもの**（エントリ数=4・speaker 重複無し・
順序/内容が一致）を見る（review #265 R11 P1: 旧実装は dict 内包で畳んで
から比較しており、同一 speaker の重複エントリが畳まれて一致判定を通過し、
壊れた config がそのまま pin として再発行され得た）。不一致はいずれも `ConfigPinMismatchError` で
fail-closed し、`.normalized.yaml` へは一切書き込まない。運用手順は
「手動編集 → `refresh-config-pin` → 学習開始」（`S3_RUN4_RUNBOOK.md` §4）。
成功時は `assembly_manifest.json`（config_path と同じディレクトリに
存在する場合）の `config.config_sha256`/`config.normalized_config_sha256`
も実測値へ更新する（review #265 R11 P1、§3 assembly manifest 節参照）。
`.normalized.yaml` と `assembly_manifest.json` の 2 ファイルは単一
トランザクション（両方 staging → 退避 rename → 両方 `os.replace`。片方の
失敗で両方ロールバック）で公開する（review #265 R12 P2、
§ `_publish_config_pin_transaction` docstring 参照）——中断・I/O エラーで
「新 config + 旧/破損 manifest」という不整合バンドルが残ることはない。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
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

# spk_id map v2（DESIGN_S4 §1.1）: 既存 3 話者（run 3/run 4 checkpoint の
# spk_embed 割当順序）を不変のまま維持し、run 5 で新設される d3synth
# （合成教師）へ次の空き番号を割り当てる（**追加は末尾のみ**の規律。
# モジュール docstring §1 参照）。
SPK_IDS: Dict[str, int] = {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}

# run 7（DESIGN_S6_run7.md §0-2）: d3synth（spk_id 3）引退 → **id 3 は恒久
# 欠番**・amitaro（実録音発音教師）を末尾 id 4 で追加・num_spk=5。id 3 の
# 再利用は永久禁止（歴史 checkpoint/ONNX/spk_map の「spk_id 3 = 合成教師」の
# 意味を保存する — 既存 ID 恒久不変・追加は末尾のみの規律）。DiffSinger
# binarizer は spk_id 未指定エントリへ**最小の空き番号を自動採番**するため
# （base_binarizer.py build_spk_map）、欠番 3 が黙って埋まる事故は
# bootstrap の spk_map.json 検査（verify_spk_map）で fail-closed に検出する。
SPK_IDS_RUN7: Dict[str, int] = {"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 4}
RUN7_NUM_SPK = 5

# profile → (教師話者名, 期待 spk_id マップ, num_spk 明示値〔None = len〕)。
# run5 プロファイルは run 5/6 の実走・出力バイトと完全同一（run 6 も
# データセット構成は run 5 と同じ 4 話者につき run5 プロファイルを使う）。
ASSEMBLE_PROFILES: Dict[str, Dict[str, object]] = {
    "run5": {"teacher_name": "d3synth", "spk_ids": SPK_IDS, "num_spk": None},
    "run7": {"teacher_name": "amitaro", "spk_ids": SPK_IDS_RUN7, "num_spk": RUN7_NUM_SPK},
}


class GateValidationError(ValueError):
    """4 話者ゲート検証（`validate_speaker`/`check_ph_dur_duration`/
    `check_note_dur_consistency`）が 1 件以上の問題を返した場合に送出する
    （fail-closed。`--out-dir` へは一切公開しない）。"""

    # NOTE: run 4 まで存在した NameCollisionError / HeaderMismatchError は
    # D2/D3 マージサブシステム専用の例外だったため、マージ撤去（DESIGN_S4
    # §2-2）と同時に削除した。

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
    検出する安全網。`.normalized.yaml` へは一切書き込まない）。

    review #265 R11 P1: `datasets` の**リスト形状そのもの**（エントリ数・
    speaker の重複無し・順序/内容が既定と一致）も検査対象に含む（§
    `_validate_live_datasets_shape` docstring 参照）。旧実装は
    `{speaker: spk_id}` の dict 内包で畳んでから比較していたため、同一
    speaker の重複エントリ（例: ritsu 2 行 + pjs + user の 4 行）が畳まれて
    `SPK_IDS` と一致してしまい、壊れた config がそのまま pin として
    再発行され得た。"""

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


def _validate_live_datasets_shape(
    live_datasets: object, expected_spk_ids: Optional[Dict[str, int]] = None
) -> List[str]:
    """`live_datasets`（live config の `datasets` フィールド生値）の**リスト
    形状そのもの**を検査する（review #265 R11 P1）。

    旧実装は `{entry["speaker"]: entry["spk_id"] for entry in datasets}` の
    dict 内包で畳んでから `SPK_IDS` と比較していたため、同一 speaker の
    重複エントリ（例: `ritsu` が誤って 2 行、計 4 エントリ）が dict へ畳まれる
    際に無言で 1 行へ収束し、畳んだ後のマップだけを見れば `SPK_IDS` と一致
    してしまい PASS していた——壊れた 4-dataset config がそのまま
    `.normalized.yaml` pin として再発行され得る fail-open 経路だった。

    本関数は畳む前のリストを直接検査する:

    1. 各エントリが `speaker`/`spk_id` を持つ mapping であること
    2. エントリ数が `len(SPK_IDS)`（=4）と一致すること（重複行があれば
       ここで確実に検出する — dict 内包のように黙って畳まれない）
    3. `speaker` 列に重複が無いこと
    4. `(speaker, spk_id)` の列が `SPK_IDS` の期待順序・期待内容
       （`ritsu=0, pjs=1, user=2, d3synth=3` の順）と完全一致すること

    問題があれば説明文字列のリストを返す（空リスト = 問題無し）。1 個でも
    構造的な問題（mapping でない・重複行）を検出したら、以降のチェックは
    スキップして早期にその diff だけを返す（誤解を招く二次的な diff の
    重畳を避ける）。
    """
    if not isinstance(live_datasets, list):
        return [f"datasets: not a list (got {type(live_datasets).__name__})"]

    entries: List[Tuple[object, object]] = []
    shape_problems: List[str] = []
    for i, entry in enumerate(live_datasets):
        if not isinstance(entry, dict) or "speaker" not in entry or "spk_id" not in entry:
            shape_problems.append(
                f"datasets[{i}]: not a mapping with 'speaker'/'spk_id' keys ({entry!r})"
            )
            continue
        entries.append((entry["speaker"], entry["spk_id"]))
    if shape_problems:
        return shape_problems

    # 既定 = SPK_IDS（run 5/6）。run 7 は SPK_IDS_RUN7 を渡す（`--profile`）。
    expected_order = list((expected_spk_ids or SPK_IDS).items())
    if len(entries) != len(expected_order):
        return [
            f"datasets: expected exactly {len(expected_order)} entries "
            f"(one per SPK_IDS speaker), got {len(entries)} (entries={entries!r}) "
            "— duplicate/missing speaker rows are not silently folded"
        ]

    speakers = [s for s, _ in entries]
    if len(set(speakers)) != len(speakers):
        dupes = sorted({s for s in speakers if speakers.count(s) > 1})
        return [
            f"datasets: duplicate speaker row(s) detected: {dupes} (entries={entries!r})"
        ]

    if entries != expected_order:
        return [
            "datasets: speaker/spk_id entries do not match the expected "
            f"order/content (expected={expected_order!r}, actual={entries!r})"
        ]
    return []


def _parse_yaml_config_bytes(data: bytes, path: Path) -> Dict[str, object]:
    """`data`（すでに読み込み済みの生バイト列）を YAML として parse し、
    `dict` かつ `datasets` がリストであることを検査する。`path` はエラー
    メッセージ表示専用で、ここではファイルへは一切触れない（P1 修正・
    review #14: `refresh_config_pin` が live config を「parse 用」と
    「sha256 計算用」で 2 回 `read`/`read_bytes` していたため、2 読の間に
    ファイルが書き換わると「旧バイト由来の normalized コピー + 新バイトの
    config_sha256」という意味的に不整合な pin 束が成立し得た。呼び出し側は
    必ず 1 回だけ `read_bytes()` した同一バイト列をこの関数と sha256 計算の
    両方に渡すこと）。"""
    data_text = data.decode("utf-8")
    parsed = yaml.safe_load(data_text)
    if not isinstance(parsed, dict):
        raise RefreshConfigPinError(f"{path}: not a YAML mapping (fail-closed)")
    if not isinstance(parsed.get("datasets"), list):
        raise RefreshConfigPinError(f"{path}: 'datasets' is not a list (fail-closed)")
    return parsed


def _load_yaml_config(path: Path) -> Dict[str, object]:
    """`path` を 1 回だけ `read_bytes()` し、`_parse_yaml_config_bytes` で
    parse する（`refresh_config_pin` の自己検算再読込 (`verify_tmp_path`) から
    使う共通ヘルパー。`assemble()` 側の他の呼び出し元には影響しない —
    このモジュール内で `_load_yaml_config` を呼ぶのは
    `refresh_config_pin`（P1 修正で bytes 経由の直呼びへ変更済み）と
    verify reread のみ）。"""
    return _parse_yaml_config_bytes(Path(path).read_bytes(), path)


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


def _assembly_manifest_sibling(config_path: Path) -> Optional[Path]:
    """`config_path` と同じディレクトリにある `assembly_manifest.json` を
    返す（存在しなければ None）。`assemble()` の出力レイアウトでは
    `run4_config_datasets.yaml` と `assembly_manifest.json` は常に
    `<out-dir>` 直下の兄弟ファイルであるため、`refresh_config_pin()` が
    記帳更新先を推定するのに使う（review #265 R11 P1）。"""
    candidate = config_path.parent / "assembly_manifest.json"
    return candidate if candidate.exists() else None


def _compute_updated_manifest_text(
    manifest_path: Path, *, config_sha256: str, normalized_config_sha256: str
) -> Optional[str]:
    """`assembly_manifest.json` の `config.config_sha256`/
    `config.normalized_config_sha256` を実測値へ更新した**テキスト**を返す
    （純関数・ファイルへは一切書き込まない）。`refresh_config_pin()` の
    再生成直後に呼ばれる——手動編集で live config のバイト列自体が変わって
    いるため、`assemble()` 実行時に記帳した config sha256 は refresh 後は
    stale になる。それを放置すると「記帳された pin と実 config の一致」を
    学習開始前に確認する手順（`S3_RUN4_RUNBOOK.md` §4）が編集前の値と照合
    してしまう。

    `manifest_path` の `config` セクションが存在しない（`assembly_manifest.json`
    自体が無い、または旧 schema で `config` キーを持たない）場合は `None` を
    返す（no-op。`refresh_config_pin()` は manifest の有無に依存せず単独でも
    動くユーティリティであるため、manifest 不在を fail-closed の理由には
    しない）。

    P1 修正 (review #265 R12): 旧実装（`_update_manifest_config_pin`）は
    ここで直接 `write_text` して manifest を公開していた。`.normalized.yaml`
    の `os.replace` 公開との間に非トランザクション性があり（中断・I/O
    エラーで「新 config + 旧/破損 manifest」の不整合バンドルが残り得た）、
    本関数は**テキストを返すだけ**にして、実際の公開は
    `_publish_config_pin_transaction`（`.normalized.yaml` と単一トランザクション
    で公開する）へ委譲する。
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_section = data.get("config")
    if not isinstance(config_section, dict):
        return None
    config_section["config_sha256"] = config_sha256
    config_section["normalized_config_sha256"] = normalized_config_sha256
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _stage_text_bytes(dest_path: Path, text: str) -> str:
    """`dest_path` と同じディレクトリへ `text`（utf-8）を staging tempfile
    として書き、そのパスを返す（`dest_path` 自体へはまだ触れない）。
    `adapter/render.py` `_stage_wav_bytes`/`_stage_timing_csv_bytes` と同型の
    「staging だけ済ませ、最終的な公開 (`os.replace`/退避 rename) は呼び出し
    側が担う」分離。失敗時は staging tempfile を best-effort で削除してから
    re-raise する。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, prefix=f"{dest_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return tmp_name


class ConfigPinPublishError(ValueError):
    """P1 修正 (review #265 R12): `_publish_config_pin_transaction` が公開
    直前の検査（宛先が「不在 or 通常ファイル」であること）で異常を検出した
    場合に送出する（`adapter/render.py` `_atomic_write_wav_and_timing` の
    同名検査・review #265 R9 と同型 — 宛先が既存のディレクトリだと退避
    rename がその中身ごと巻き込んで喪失させ得るため、退避を試みる前に
    fail-closed で拒否する）。"""


def _publish_config_pin_transaction(
    normalized_path: Path,
    normalized_text: str,
    manifest_path: Optional[Path],
    manifest_text: Optional[str],
) -> None:
    """P1 修正 (review #265 R12): `.normalized.yaml`（pin 副本）と
    `assembly_manifest.json` の 2 ファイルを**単一トランザクション**で公開
    する（`adapter/render.py` `_atomic_write_wav_and_timing`（review #265
    R3/R5/R9 で確立済みの「両方 staging → 退避 rename → 両方 os.replace の
    4 ステップを単一 `try`/`except BaseException` 配下に置く」パターン）と
    同型。片方だけ公開されて「新 config + 旧/破損 manifest」のような不整合
    バンドルが残ることを防ぐ）。

    `manifest_path`/`manifest_text` がいずれも `None`（`assembly_manifest.json`
    が無い、または `config` セクションを持たない no-op ケース）の場合は
    `normalized_path` 単独の atomic 書き（staging tempfile -> `os.replace`）
    のみを行う。

    manifest 側の書き込み自体も（`_compute_updated_manifest_text` が返す
    テキストを）staging tempfile 経由で公開するため、旧実装の直接
    `write_text`（非 atomic・中断で破損 manifest が残り得た）は解消される。
    """
    normalized_path = Path(normalized_path)
    normalized_tmp = _stage_text_bytes(normalized_path, normalized_text)

    if manifest_path is None or manifest_text is None:
        try:
            os.replace(normalized_tmp, normalized_path)
        except BaseException:
            try:
                os.unlink(normalized_tmp)
            except OSError:
                pass
            raise
        return

    manifest_path = Path(manifest_path)
    try:
        manifest_tmp = _stage_text_bytes(manifest_path, manifest_text)
    except BaseException:
        try:
            os.unlink(normalized_tmp)
        except OSError:
            pass
        raise

    normalized_backup = normalized_path.parent / f"{normalized_path.name}.prev-{os.getpid()}.bak"
    manifest_backup = manifest_path.parent / f"{manifest_path.name}.prev-{os.getpid()}.bak"
    for stale in (normalized_backup, manifest_backup):
        if stale.exists():
            os.unlink(stale)
    # `.exists()` は副作用の無い読み取りのみ。
    normalized_had_previous = normalized_path.exists()
    manifest_had_previous = manifest_path.exists()

    try:
        for _path, _had_previous in (
            (normalized_path, normalized_had_previous),
            (manifest_path, manifest_had_previous),
        ):
            if _had_previous and not _path.is_file():
                raise ConfigPinPublishError(
                    f"{_path} は既存のディレクトリ（または通常ファイルではない何か）"
                    "です。退避 rename がその中身ごと喪失させ得るため fail-closed で"
                    "拒否します（review #265 R12 対応）。"
                )
        if normalized_had_previous:
            os.rename(normalized_path, normalized_backup)
        if manifest_had_previous:
            os.rename(manifest_path, manifest_backup)
        os.replace(normalized_tmp, normalized_path)
        os.replace(manifest_tmp, manifest_path)
    except BaseException:
        # 巻き戻し: 各宛先について「退避 rename が完了しているか」を
        # `backup_path.exists()` というファイルシステム状態で判定する
        # （`adapter/render.py` `_atomic_write_wav_and_timing` と同一の
        # 巻き戻しロジック）。
        for new_path, backup_path, had_previous in (
            (normalized_path, normalized_backup, normalized_had_previous),
            (manifest_path, manifest_backup, manifest_had_previous),
        ):
            if had_previous:
                if backup_path.exists():
                    if new_path.exists():
                        os.unlink(new_path)
                    os.rename(backup_path, new_path)
                # backup_path が無ければ退避 rename 自体が未着手 —
                # new_path は元のバイト列のまま残っているため何もしない。
            else:
                if new_path.exists():
                    os.unlink(new_path)
        for tmp in (normalized_tmp, manifest_tmp):
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        raise

    # 公開成功: 退避しておいた旧世代はもう不要。
    for backup, had_previous in (
        (normalized_backup, normalized_had_previous),
        (manifest_backup, manifest_had_previous),
    ):
        if had_previous:
            try:
                os.unlink(backup)
            except OSError:
                pass


def refresh_config_pin(
    config_path: Path,
    config_dir: Optional[Path] = None,
    expected_spk_ids: Optional[Dict[str, int]] = None,
    expected_num_spk: Optional[int] = None,
) -> Path:
    """P1 修正 (review #265 R9): 手動編集後の live config
    (`run4_config_datasets.yaml`) から `.normalized.yaml` pin 副本を再生成
    する（公開エントリポイント。モジュール docstring「`refresh-config-pin`
    サブコマンド」節参照）。

    live config は **1 回だけ** `read_bytes()` し、その同一バイト列を
    parse・正規化・sha256 記帳のすべてに使う（review #14 P1 修正: 旧実装は
    parse 用の読み込みと `config.config_sha256` 記帳用の読み込みを別々に
    行っており、2 読の間にファイルが書き換わると「旧バイト由来の
    normalized コピー + 新バイトの config_sha256」という意味的に不整合な
    pin 束が成立し得た）。パス系フィールドだけを `config_dir` 基準の相対パス
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

    成功時、`config_path` と同じディレクトリに `assembly_manifest.json` が
    存在すれば（`assemble()` が書いたもの想定）、その `config.config_sha256`/
    `config.normalized_config_sha256` を実測値へ更新する（review #265 R11
    P1）。`.normalized.yaml` と `assembly_manifest.json` の 2 ファイルは
    **単一トランザクション**で公開する（review #265 R12 P2、§
    `_publish_config_pin_transaction` docstring 参照）——中断・I/O エラーで
    「新 config + 旧/破損 manifest」の不整合バンドルが残ることを防ぐ
    （manifest が無ければ `.normalized.yaml` 単独の atomic 書きのみ）。

    戻り値は書いた `.normalized.yaml` のパス。
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise RefreshConfigPinError(f"{config_path}: not found (fail-closed)")
    config_dir = Path(config_dir) if config_dir is not None else config_path.parent

    # P1 修正 (review #14): live config は 1 回だけ `read_bytes()` し、その
    # 同一バイト列を parse・正規化・sha256 計算のすべてに使う（ファイルパス
    # からの再読を排除する）。旧実装は `_load_yaml_config(config_path)` で
    # 一度読み、後段の manifest 更新で `_sha256_file(config_path)` により
    # 別途もう一度読んでいたため、2 読の間にファイルが書き換わると「旧
    # バイト由来の normalized コピー + 新バイトの config_sha256」という
    # 意味的に不整合な pin 束が成立し得た（トランザクション自体は成功する
    # ため検出されない）。
    live_bytes = config_path.read_bytes()
    live = _parse_yaml_config_bytes(live_bytes, config_path)

    # review #265 R11 P1: リスト形状そのもの（エントリ数・重複無し・
    # 順序/内容）を検査する（§ `_validate_live_datasets_shape` docstring
    # 参照。旧実装の dict 内包による畳み込みは重複行を黙って通していた）。
    # `expected_spk_ids`（run 7 追加）: 既定 None = SPK_IDS（run 5/6 の挙動
    # 不変）。run 7 config の pin 再発行は SPK_IDS_RUN7 を渡す（CLI --profile）。
    shape_problems = _validate_live_datasets_shape(
        live.get("datasets"), expected_spk_ids
    )
    # `num_spk` も §0-2 契約フィールド（run 7 = 5・欠番 3 を含む埋め込み行数。
    # セルフレビュー #6: datasets 節だけ検査すると num_spk の手動編集事故が
    # そのまま pin 再発行される）。既定期待値 = max(期待 spk_id)+1 —
    # 欠番の無い run 5/6 では話者数 4 と同値・欠番ありの run 7 では 5 になる
    # （_write_run4_config の num_spk 決定と同じ不変量）。
    _map = expected_spk_ids or SPK_IDS
    effective_expected_num_spk = (
        expected_num_spk if expected_num_spk is not None
        else max(_map.values()) + 1
    )
    if live.get("num_spk") != effective_expected_num_spk:
        shape_problems = shape_problems + [
            f"num_spk: expected {effective_expected_num_spk}, "
            f"got {live.get('num_spk')!r} (fail-closed)"
        ]
    if shape_problems:
        raise ConfigPinMismatchError(shape_problems)

    expected_normalized = _normalize_config_dict(live, config_dir)

    normalized_path = config_path.with_name(config_path.name + ".normalized.yaml")
    normalized_text = yaml.safe_dump(expected_normalized, allow_unicode=True, sort_keys=False)

    # 検証専用の verify tempfile へ書いて再読込・semantic diff する
    # （まだ `normalized_path`/`manifest_path` いずれの実ファイルにも一切
    # 触れない——公開前の検証フェーズ）。verify tempfile はこの検証にのみ
    # 使い、実際の公開用 staging（`_publish_config_pin_transaction` 内で
    # 新たに用意する）とは別物として都度削除する。
    verify_tmp_path = normalized_path.parent / f"{normalized_path.name}.verify-{os.getpid()}.tmp"
    try:
        verify_tmp_path.write_text(normalized_text, encoding="utf-8")
        reread = _load_yaml_config(verify_tmp_path)
        diffs = _semantic_diff(live, reread)
        if diffs:
            raise ConfigPinMismatchError(diffs)
    finally:
        try:
            verify_tmp_path.unlink()
        except OSError:
            pass

    # review #265 R11 P1 (§R12 で単一トランザクション化): manifest 記帳値を
    # 実測で更新する（存在すれば）。`normalized_config_sha256` は公開前の
    # `normalized_text`（検証済みバイト列）から直接計算する — 未公開の
    # normalized 実体を再度読み直す必要はない。
    manifest_path = _assembly_manifest_sibling(config_path)
    manifest_text: Optional[str] = None
    if manifest_path is not None:
        manifest_text = _compute_updated_manifest_text(
            manifest_path,
            # P1 修正 (review #14): ここで `config_path` を再度読み直さない。
            # `live_bytes`（この関数冒頭で 1 回だけ読み、parse にも使った
            # のと同一バイト列）から直接計算する — parse に使われたバイト列
            # と記帳される sha256 が常に一致することを保証する。
            config_sha256=hashlib.sha256(live_bytes).hexdigest(),
            normalized_config_sha256=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        )
        if manifest_text is None:
            manifest_path = None  # config セクション無し = no-op ケース

    _publish_config_pin_transaction(normalized_path, normalized_text, manifest_path, manifest_text)

    return normalized_path


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


def _voiced_ph_dur_seconds(rows: Sequence[Dict[str, str]]) -> float:
    """SP（rest）を除いた ph_dur 実測合計（秒）。run 7 構成比会計の基準
    （DESIGN_S6_run7.md §2-5 — 規約の「無音部を除いた発話時間」に同型）。"""
    total = 0.0
    for row in rows:
        tokens = row["ph_seq"].split()
        durs = [float(x) for x in row["ph_dur"].split()]
        total += math.fsum(d for tok, d in zip(tokens, durs) if tok != "SP")
    return total


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
    spk_ids: Optional[Dict[str, int]] = None,
    num_spk: Optional[int] = None,
) -> Dict[str, object]:
    """P1 修正 (review #265 R7): `build_dataset.py` `build_config_yaml()`
    （`speakers` 引数を話者数非依存の汎用リストとして受ける実装。モジュール
    docstring §5 で一次ソース確認済み）を 4 話者分呼び出し、staging 内へ
    `run4_config_datasets.yaml`（実行時 config・絶対パス）+
    `run4_config_datasets.yaml.normalized.yaml`（pin 用・`out_dir` 基準の
    相対パス）を書く。`raw_data_dir`/`dict_path`/`binary_data_dir` はいずれも
    公開後の最終パス（`out_dir` 基準）を指す——`_swap_into_place` で
    `staging_dir` が `out_dir` へ rename されるため、staging 中に書く config
    も最終レイアウトの絶対パスを参照する必要がある（`build_dataset.py`
    `main()` が呼び出し元から渡された `--*-raw-dir` をそのまま埋め込むのと
    同じ設計）。戻り値は書いた 2 ファイルのパス/sha256——本関数自体は
    `assembly_manifest.json` に触れない（単一責務: staging へ config を書く
    だけ）が、**呼び出し側 `_assemble_into()` はこの戻り値を
    `manifest["config"]` へ記帳する**（review #265 R11 P1 修正: 旧実装は
    この戻り値を握り潰し、生成した config の実体を manifest から一切
    検証できなかった。詳細はモジュール docstring §5/§6 参照）。
    """
    final_dict_path = out_dir / "dict.txt"
    resolved_binary_data_dir = binary_data_dir if binary_data_dir is not None else out_dir / "binary"

    # spk_id マップの宣言順（= spk_id 昇順）で datasets エントリを構成する
    # （`_validate_live_datasets_shape` が検査する期待順序と同一の単一ソース。
    # 既定 = SPK_IDS〔run 5/6 不変〕・run 7 = SPK_IDS_RUN7 + num_spk 明示）。
    effective_spk_ids = spk_ids or SPK_IDS
    speakers = [
        (name, spk_id, out_dir / name,
         build_dataset.select_test_prefixes(speaker_rows[name], n_test_prefixes))
        for name, spk_id in effective_spk_ids.items()
    ]

    config_path = staging_dir / "run4_config_datasets.yaml"
    config_text = build_dataset.build_config_yaml(
        dict_path=final_dict_path,
        binary_data_dir=resolved_binary_data_dir,
        speakers=speakers,
        max_updates=max_updates,
        val_check_interval=val_check_interval,
        num_ckpt_keep=num_ckpt_keep,
        num_spk=num_spk,
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
        num_spk=num_spk,
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
    teacher_raw_dir: Path,
    pjs_raw_dir: Path,
    user_raw_dir: Path,
    *,
    pjs_is_fixture: bool,
    binary_data_dir: Optional[Path],
    n_test_prefixes: int,
    max_updates: int,
    val_check_interval: int,
    num_ckpt_keep: int,
    teacher_name: str = "d3synth",
    spk_ids: Optional[Dict[str, int]] = None,
    num_spk: Optional[int] = None,
) -> Dict[str, object]:
    """staging_dir 配下に 4 話者 raw 構成 + dict.txt + assembly_manifest.json
    + 4 話者学習 config（`run4_config_datasets.yaml` 系。review #265 R7 P1
    追加、§ `_write_run4_config` docstring 参照）を組み立てる。検証失敗時は
    例外を送出し、呼び出し側 (`assemble`) が staging_dir ごと破棄する
    （`--out-dir` は一切変更されない）。`out_dir` は config 内の
    `raw_data_dir`/`dict_path`/`binary_data_dir` を公開後の最終パスで書く
    ために必要（staging_dir 自身のパスは `_swap_into_place` 後に消える
    一時名のため使えない）。"""
    effective_spk_ids = spk_ids or SPK_IDS

    # --- 1. 4 話者すべてバイト単位でそのまま複製 ------------------------------
    # run 5（DESIGN_S4 §2-2）: D2/D3 マージサブシステムは撤去済み。ritsu は
    # D2 のみを、教師話者（run 5/6 = d3synth〔convert_d3 出力〕・run 7 =
    # amitaro〔convert_amitaro 出力〕）は `teacher_raw_dir` を、いずれも
    # pjs/user と同型の「バイト単位コピー」経路で組み立てる。
    ritsu_dir = staging_dir / "ritsu"
    _copy_file_bytes(ritsu_raw_dir / "transcriptions.csv", ritsu_dir / "transcriptions.csv")
    _copy_wavs(ritsu_raw_dir / "wavs", ritsu_dir / "wavs")

    teacher_dir = staging_dir / teacher_name
    _copy_file_bytes(teacher_raw_dir / "transcriptions.csv", teacher_dir / "transcriptions.csv")
    _copy_wavs(teacher_raw_dir / "wavs", teacher_dir / "wavs")

    pjs_dir = staging_dir / "pjs"
    _copy_file_bytes(pjs_raw_dir / "transcriptions.csv", pjs_dir / "transcriptions.csv")
    _copy_wavs(pjs_raw_dir / "wavs", pjs_dir / "wavs")

    user_dir = staging_dir / "user"
    _copy_file_bytes(user_raw_dir / "transcriptions.csv", user_dir / "transcriptions.csv")
    _copy_wavs(user_raw_dir / "wavs", user_dir / "wavs")
    user_exclusions_src = user_raw_dir / "exclusions.json"
    user_has_exclusions = user_exclusions_src.exists()
    user_exclusions_sha256: Optional[str] = None
    if user_has_exclusions:
        user_exclusions_dst = user_dir / "exclusions.json"
        _copy_file_bytes(user_exclusions_src, user_exclusions_dst)
        # P2 修正 (review #265 R11): staged 出力バイトそのものから実測する
        # （公開後の実体へのバイト束縛。`wav_sha256` と同じ「実測のみ・
        # 手打ちなし」規約 — provenance binding ファミリーの掃討）。
        user_exclusions_sha256 = _sha256_file(user_exclusions_dst)

    # --- 2. 4 話者ゲート検証（全問題収集 → 1 件でもあれば fail-closed） -------
    # P1 修正 (review #265): 話者の transcriptions.csv が行 0 件（空データ
    # セット）の場合、`validate_speaker`/`check_ph_dur_duration`/
    # `check_note_dur_consistency` はいずれも空リストに対して no-op で
    # `problems=[]` を返す（`convert_d3.discover_pairs()` 0 件と同型の
    # false-success 経路）。ゲートを回す前に明示的に検出し、空話者があれば
    # 他の gate 違反と合わせて fail-closed する（staging へは書き込み済みだが
    # `assemble()` 側で staging ごと破棄され `out_dir` は無変更のまま残る）。
    speaker_dirs = {
        "ritsu": ritsu_dir, "pjs": pjs_dir, "user": user_dir, teacher_name: teacher_dir,
    }
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

    # --- 3. 辞書統合（build_dataset.py main() と同じ 3 関数を read-only 再利用） ---
    symbol_sets = [
        build_dataset.collect_phoneme_symbols(speaker_rows[name])
        for name in effective_spk_ids
    ]
    merged_pairs = build_dataset.build_merged_dict(symbol_sets)
    dict_path = staging_dir / "dict.txt"
    build_dataset.write_dict(dict_path, merged_pairs)

    # --- 3.5. 4 話者学習 config 生成（review #265 R7 P1・§ _write_run4_config） ---
    # P1 修正 (review #265 R11): 戻り値（live/normalized 双方の sha256）を
    # 握り潰さず manifest へ記帳する（§5 assembly manifest 節参照）。
    config_result = _write_run4_config(
        staging_dir, out_dir, speaker_rows,
        binary_data_dir=binary_data_dir, n_test_prefixes=n_test_prefixes,
        max_updates=max_updates, val_check_interval=val_check_interval,
        num_ckpt_keep=num_ckpt_keep, spk_ids=effective_spk_ids, num_spk=num_spk,
    )

    # --- 4. assembly manifest（決定論のためウォールクロック時刻を含めない） ---
    # schema 0.4（run 5/6・4 話者・d3synth 教師）: D2/D3 マージ専用だった
    # `collision_check` セクションと ritsu の `components`/`d2_*`/`d3_*`
    # フィールドを撤去（モジュール docstring §4 参照）。
    # schema 0.5（run 7・DESIGN_S6_run7.md §2-5）: 教師 = amitaro（spk_id 4・
    # id 3 恒久欠番）のとき。`num_spk` と `composition_accounting`（無音部を
    # 除いた発話時間 = SP を除く ph_dur 合計を基準とする構成比 — 規約の
    # 会計基準に同型）を追加し、amitaro share < 0.50 を assert する。
    is_run7_shape = teacher_name != "d3synth" or num_spk is not None
    manifest: Dict[str, object] = {
        "schema": (
            "run4-assembly-manifest/0.5" if is_run7_shape
            else "run4-assembly-manifest/0.4"
        ),
        "spk_id": dict(effective_spk_ids),
        "speakers": {
            "ritsu": _speaker_manifest_entry(
                ritsu_dir, speaker_rows["ritsu"], effective_spk_ids["ritsu"]
            ),
            "pjs": {
                **_speaker_manifest_entry(
                    pjs_dir, speaker_rows["pjs"], effective_spk_ids["pjs"]
                ),
                "is_fixture": pjs_is_fixture,
            },
            "user": {
                **_speaker_manifest_entry(
                    user_dir, speaker_rows["user"], effective_spk_ids["user"]
                ),
                "has_exclusions_json": user_has_exclusions,
                # P2 修正 (review #265 R11): 公開バイトから実測した sha256
                # （`has_exclusions_json` の真偽値だけでは実体へのバイト束縛が
                # 無かった）。`exclusions.json` を持たない場合は None。
                "exclusions_json_sha256": user_exclusions_sha256,
            },
            teacher_name: _speaker_manifest_entry(
                teacher_dir, speaker_rows[teacher_name], effective_spk_ids[teacher_name]
            ),
        },
        "dict": {
            "path": "dict.txt",
            "symbol_count": len(merged_pairs),
            "sha256": _sha256_file(dict_path),
        },
        # P1 修正 (review #265 R11): 生成 config の sha256 記帳（live/
        # normalized 双方）。`refresh_config_pin()` 実行時はこの値を実測で
        # 更新する（§ refresh_config_pin docstring 参照）。
        "config": dict(config_result),
        "gate": {
            "checks": ["validate_speaker", "check_ph_dur_duration", "check_note_dur_consistency"],
            "problems": [],
        },
    }
    if is_run7_shape:
        manifest["num_spk"] = (
            num_spk if num_spk is not None else len(effective_spk_ids)
        )
        # 構成比会計（DESIGN_S6 §2-5・DX §2-2-2）: 分子/分母とも同基準の
        # 「無音部を除いた発話時間」（SP を除く ph_dur 実測合計）。
        voiced_seconds = {
            name: round(_voiced_ph_dur_seconds(speaker_rows[name]), 3)
            for name in effective_spk_ids
        }
        total_voiced = math.fsum(voiced_seconds.values())
        if total_voiced <= 0.0:
            raise GateValidationError(
                ["composition_accounting: total voiced ph_dur is non-positive"]
            )
        shares = {
            name: round(v / total_voiced, 6) for name, v in voiced_seconds.items()
        }
        manifest["composition_accounting"] = {
            "basis": (
                "voiced ph_dur seconds（transcriptions.csv の SP を除く ph_dur "
                "実測合計 — 規約の会計基準「無音部を除いた発話時間」に同型・"
                "DESIGN_S6_run7.md §2-5）"
            ),
            "per_speaker_voiced_seconds": voiced_seconds,
            "total_voiced_seconds": round(total_voiced, 3),
            "shares": shares,
        }
        if "amitaro" in shares and shares["amitaro"] >= 0.50:
            raise GateValidationError([
                f"composition_accounting: amitaro share {shares['amitaro']:.4f} >= 0.50 "
                "(規約の配布比率ルール安全域を逸脱 — 教師枠 = 少量投入の契約違反。"
                "fail-closed・DESIGN_S6 §2-5)"
            ])
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
    profile: str = "run5",
) -> Dict[str, object]:
    """`out_dir` に 4 話者 raw 構成 + 4 話者学習 config を組み立てる
    （公開エントリポイント。run 5 = spk_id map v2・DESIGN_S4 §1.1。
    `d3_raw_dir` は**教師話者スロット**の入力 — `profile="run5"`（既定）では
    d3synth（convert_d3 出力・spk_id 3）、`profile="run7"`
    （DESIGN_S6_run7.md §0-2）では amitaro（convert_amitaro 出力・spk_id 4・
    id 3 恒久欠番・num_spk 5）として組み立てる。run5 プロファイルの出力は
    従来とバイト同一）。

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

    if profile not in ASSEMBLE_PROFILES:
        raise ValueError(
            f"unknown assemble profile {profile!r} "
            f"(expected one of {sorted(ASSEMBLE_PROFILES)})"
        )
    prof = ASSEMBLE_PROFILES[profile]

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
            teacher_name=str(prof["teacher_name"]),
            spk_ids=prof["spk_ids"],  # type: ignore[arg-type]
            num_spk=prof["num_spk"],  # type: ignore[arg-type]
        )
        convert_d3._swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return manifest


def _main_assemble(argv: Optional[Sequence[str]] = None) -> int:
    """既定のサブコマンド（省略可）: 4 話者アセンブリ + config 生成。旧
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
        "--d3-raw-dir", type=Path, default=None,
        help="D3 = convert_d3.py の --out-dir (transcriptions.csv + wavs/)。"
             "run 5 からは d3synth 話者（spk_id=3・第 4 エントリ）として"
             "バイト単位コピーされる（ritsu へのマージは撤去済み）。"
             "--profile run5（既定）では必須。",
    )
    parser.add_argument(
        "--amitaro-raw-dir", type=Path, default=None,
        help="run 7 教師 = convert_amitaro.py の --out-dir。--profile run7 では"
             "必須（d3synth 引退 = --d3-raw-dir と排他。DESIGN_S6_run7.md §1.1）。",
    )
    parser.add_argument(
        "--profile", choices=sorted(ASSEMBLE_PROFILES), default="run5",
        help="spk_id マップのプロファイル（run5 = 4 話者 d3synth 教師〔既定・"
             "run 5/6 と同一出力〕/ run7 = amitaro 教師 spk_id 4・id 3 恒久欠番・"
             "num_spk 5）。",
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
    parser.add_argument("--out-dir", type=Path, required=True, help="4 話者 raw 構成の出力先")
    parser.add_argument(
        "--pjs-is-fixture", action="store_true",
        help="--pjs-raw-dir が合成ミニ pjs フィクスチャであることを "
             "assembly_manifest.json に明記する（本番実行では指定しない）。",
    )
    # review #265 R7 P1: 学習 config 生成ノブ（build_dataset.py 同名
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
    # 教師スロットの入力をプロファイルで解決する（run5 = --d3-raw-dir /
    # run7 = --amitaro-raw-dir。取り違え・二重指定は fail-closed）。
    if args.profile == "run5":
        if args.d3_raw_dir is None or args.amitaro_raw_dir is not None:
            print("error: --profile run5 requires --d3-raw-dir "
                  "(and must not have --amitaro-raw-dir)", file=sys.stderr)
            return 1
        teacher_raw_dir = args.d3_raw_dir
    else:
        if args.amitaro_raw_dir is None or args.d3_raw_dir is not None:
            print("error: --profile run7 requires --amitaro-raw-dir "
                  "(and must not have --d3-raw-dir — d3synth は引退・"
                  "DESIGN_S6_run7.md §1.1)", file=sys.stderr)
            return 1
        teacher_raw_dir = args.amitaro_raw_dir
    # P1 修正 (review #265): 衝突検査は `assemble()` 自身が行う（公開関数へ
    # 移設済み。CLI 側の preflight 二重実装はしない）。
    try:
        manifest = assemble(
            args.ritsu_raw_dir, teacher_raw_dir, args.pjs_raw_dir, args.user_raw_dir, out_dir,
            pjs_is_fixture=args.pjs_is_fixture,
            binary_data_dir=args.binary_data_dir, n_test_prefixes=args.n_test_prefixes,
            max_updates=args.max_updates, val_check_interval=args.val_check_interval,
            num_ckpt_keep=args.num_ckpt_keep, profile=args.profile,
        )
    except (convert_d3.OutputCollisionError, GateValidationError) as exc:
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
    parser.add_argument(
        "--profile", choices=sorted(ASSEMBLE_PROFILES), default="run5",
        help="datasets 節の期待 spk_id マップ（run5 = SPK_IDS〔既定〕/ "
             "run7 = SPK_IDS_RUN7）。",
    )
    args = parser.parse_args(argv)

    prof = ASSEMBLE_PROFILES[args.profile]
    expected_spk_ids = prof["spk_ids"]
    expected_num_spk = prof["num_spk"]  # None = len(spk_ids)（run5 既定）
    try:
        normalized_path = refresh_config_pin(
            args.config, config_dir=args.config_dir,
            expected_spk_ids=expected_spk_ids,  # type: ignore[arg-type]
            expected_num_spk=expected_num_spk,  # type: ignore[arg-type]
        )
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
