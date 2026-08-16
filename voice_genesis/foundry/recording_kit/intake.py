"""第3ドナー積み立てキット — 取り込みスクリプト（骨格）。

`README.md` §6 の実装。受領ディレクトリ（チャット添付 or AI-Drive
`/user_donor/incoming/` からダウンロードしたもの）にある音声
（wav/m4a/mp3）を 24kHz mono wav へ正規化し、`user_donor_ledger.json`
へカード ID・sha256・受領日・長さ・ラウドネス概況を追記する。

**スコープ外（TODO）**: 音素/ノート境界のアラインメントは本 stub では
行わない。T0 カード（`cards.md`: UC-001/UC-002）は既存の凍結スコア
（`voice_genesis/singer/score.py`/`score_umi.py`）に一致するため、
後続タスクでアラインメント半自動化を行う想定。本スクリプトはその前段
（受領・正規化・台帳記録）のみを担う。

ffmpeg は外部バイナリ依存のため、実行時に `shutil.which("ffmpeg")` で
存在確認する（import ガード。ffmpeg 不在でもモジュール読み込み自体は
失敗しない — `python -c "import intake"` や pytest 収集を壊さない）。

カード ID はファイル名の先頭トークン（`_` または空白区切りの最初の要素）
を `cards.md` の ID 形式（`UC-\\d{3}`）として抽出する。3 桁の直後は
stem 終端か区切り文字（`_`/空白/`.`/`-`）のみ許可し、`UC-0010` や
`UC-001oops` のような非区切りの続きはマッチさせない（誤って別カードの
プレフィックスに誤帰属しないための境界チェック）。マッチしない場合は
`card_id: null` のまま記録し、後で手動補完できるようにする（fail-closed
にせず記録は残す — 積み立て運用の「録ったものは失わない」を優先）。

同一 stem（拡張子違いの再送・再録）は正規化後ファイル名が衝突するため、
事前に `assign_normalized_filenames` で全入力の派生ファイル名を予約し、
2 件目以降はテイク連番（`UC-001.take2.norm24k.wav` 等）で一意化する
（積み立て運用では同カードの再録が正常系のため fail-closed にはしない）。

正規化 wav の書き出しと台帳更新は staging ディレクトリ上でバッチ全体を
構築してから一括で `--out-dir` / `--ledger` へ公開する。途中で
変換・測定が失敗した場合は staging を破棄するだけで済み、公開済み
ディレクトリと台帳は失敗前の状態のまま変化しない（部分バッチを残さない）。

`--ledger` の解決パスは、処理開始前に incoming の元音源ファイル・
今回バッチの導出出力（staging 内の一時パス・`out_dir` 内の最終正規化
wav）・out_dir 内の既存公開済みファイル・staging ディレクトリ自体との
衝突を preflight で fail-closed 拒否する（symlink 迂回を防ぐため resolve
済みパスで比較。R12 P1 対応）。

公開フェーズ（`out_dir` へのファイル移動 + 台帳保存）は、移動済みファイルを
記録しながら進め、移動そのものが失敗した場合・全ファイル移動後に台帳保存が
失敗した場合のいずれも `except BaseException` で捕捉し、それまでに公開済み
だった wav を staging へ巻き戻し、台帳ファイルも公開フェーズ開始前のバイト
列（無かった場合は削除）へ復元してから re-raise する（`gate_synth.py` の
staged swap + `BaseException` 巻き戻しと同型パターン。R12 P2 対応。台帳の
巻き戻しは R13 P2 対応 — `save_ledger()` 成功直後・関数が返る前に
`KeyboardInterrupt`/`SystemExit` が届くケースを含む）。台帳エントリには
正規化後 wav の sha256 に加え、元 incoming ファイルのバイト列 sha256
（`source_sha256`）とサイズ（`source_size_bytes`）も記録する（incoming
ファイルは可変なファイル名でしか代表されておらず、削除・差し替え後に
「どの原本バイト列から正規化 wav が作られたか」を追跡できなくする穴を
防ぐ。R12 P2 対応）。この `source_sha256`/`source_size_bytes` は、変換
（ffmpeg）へ渡すバイト列と同一の単一 read から確定する（`process_one`
参照。R13 P2 対応 — 別々の read だと incoming ファイルが read 間で
差し替わった場合に台帳と実際の変換入力がねじれる）。

既存台帳を読み込む際は `schema == LEDGER_SCHEMA` の完全一致と `entries`
がリストであることを検証し、不一致は `LedgerSchemaError` で fail-closed
拒否する（`load_ledger` 参照。R13 P2 対応 — 未知/破損スキーマの台帳へ
現行版のエントリを無自覚に追記・公開してしまう事故を防ぐ）。

`entries` 各要素についても `LedgerEntry` の必須フィールド・型（`_LEDGER_
ENTRY_REQUIRED_FIELDS` 参照）を検証し、欠損・型不正は同じく
`LedgerSchemaError` で fail-closed 拒否する（`_validate_ledger_entry`
参照。R19 P2 対応 — schema バージョンが一致していてもエントリ単位で
`source_sha256` 等が欠けた/破損した台帳が無自覚に受理されると、
`_check_duplicate_sources` がそのエントリの重複検査を静かにスキップし、
重複ドナー音声や破損 provenance が再公開されてしまう）。schema が現行版
一致している台帳のエントリは現行形状であるべきであり、正式 intake 運用は
未実施のため後方互換の負担も無い。

さらに、型が合っているだけでは検出できない値レベルの不変条件も
`_validate_ledger_entry` が検証する（R22 P2 対応 — `source_sha256:
"garbage"`、`duration_sec: NaN`、`sample_rate: true`（`bool` は `int` の
サブクラスのため型検査を素通りする）、負の `source_size_bytes` 等、型
検査だけでは弾けない不正値が `_check_duplicate_sources` の重複検査を
すり抜けたり `json.dump()` で非有限値が再出力され続けたりする穴を防ぐ）。
これにより台帳検証階層は「schema 完全一致 (R13) → entries リスト (R13)
→ エントリ必須フィールド/型 (R19) → 値不変条件 (R22)」で完結し、
`LedgerEntry` が生成しうる全フィールドについて値域検証が揃った
（`_validate_ledger_entry` 参照）。

`--ledger` が `out_dir` 内にある配置（append ワークフロー）では、2 回目
以降のバッチの preflight で `--ledger` 自身が `out_dir` の既存エントリと
して見つかる。これは事故ではなく意図した配置のため、`_check_ledger_path_
collisions` は resolve 済みパスが `--ledger` 自身と完全一致し、かつ中身が
現行スキーマの台帳として読み込める場合に限りこの既存エントリを衝突対象
から除外する（`_is_existing_ledger_file` 参照。R19 P2 対応。中身が台帳と
して読み込めない場合＝`--ledger` が誤って正規化 wav 等の無関係な既存
ファイルを指しているケースは、従来通り衝突として fail-closed 拒否する）。

`run()` は本体（preflight の衝突検査〜変換〜公開〜台帳保存〜ロール
バック）全体を `<ledger>.lock` への `fcntl.flock(LOCK_EX | LOCK_NB)` で
直列化する（R21 P1 対応）。同一 `out_dir`/`--ledger` を対象に 2 つの
intake プロセスが並行実行されると、両方が同じ旧台帳を読み込み、それぞれ
別の wav を公開した上で `save_ledger()` を呼ぶ — 後発の save が先発の
追記済みエントリを丸ごと上書きし、先発の wav が `out_dir` には存在する
のに台帳には記録されない、というデータ損失がロールバック機構をすり抜けて
（両プロセスとも自分の中では正常終了するため）発生し得た。ロック取得は
`LOCK_NB`（ノンブロッキング）で行い、取得できない場合は「別の intake が
実行中」の `LedgerLockError` で即座に fail-closed 拒否する（静かに
待ち合わせない — 運用上は失敗を明示するほうが安全）。ロックファイルは
空ファイルのまま残置する（`unlink` すると、ある プロセスが unlink 直後・
別プロセスが同じ旧パスを開いて flock した直後という窓で二重ロックが
成立してしまうため。`flock` はプロセス終了・例外時にも OS が自動解放
する）。残置したロックファイル自身が `_check_ledger_path_collisions` の
既存ファイル走査に誤って引っかからないよう、同関数へ `lock_path` を渡し、
衝突検査対象の一員（incoming 原本・導出出力との衝突は拒否対象、
`out_dir` 内の「公開済み既存ファイル」走査では自分自身の予約物として
除外）として整合させる。

`<ledger>.lock` に加えて `<out_dir>/.intake.lock` への `fcntl.flock
(LOCK_EX | LOCK_NB)` も取得し、`out_dir`（公開名前空間）自体も別ロックで
直列化する（R22 P1 対応）。`<ledger>.lock` のロックパスは `--ledger` の
パスのみから導出されるため、**別の `--ledger` かつ同一 `--out-dir`** を
指す 2 つの intake プロセスは異なるロックを取得してしまい並行実行を防げ
ない。両者が `assign_normalized_filenames` のスナップショットに基づき
同じ正規化ファイル名を予約・`_check_publish_path` を通過した場合、後勝ちの
`shutil.move()` が先発の wav を上書きし、台帳に記録済みのハッシュと
`out_dir` 上の実バイト列が乖離する（片方の台帳だけが不整合を検出できない
まま残る）。ロック取得順序は **ledger → out_dir の固定順**（両ロックとも
片方向の順序でのみ取得するためデッドロックは起きない）。out_dir ロックも
`LOCK_NB` で取得し、既に別プロセスが保持している場合は `OutDirLockError`
で即座に fail-closed 拒否する。残置ポリシー（`unlink` しない）・
`_check_ledger_path_collisions` の予約物除外の考え方は `<ledger>.lock` と
同じ（`_out_dir_lock_path` 参照）。

`duration_sec` の非正/非有限検査（`NonPositiveDurationError`）は、正規化
後 wav の生の長さだけでなく、台帳へ実際に書き込む丸め後の値
（`round(duration_sec, 3)`）にも適用する（R23 P2 対応）。ffmpeg が
0.0005 秒未満の正の長さの WAV しか生成しなかった場合、生値は非正チェックを
素通りするが丸め後は `duration_sec: 0.0` となり、`_validate_ledger_entry`
自身の duration_sec > 0 不変条件に違反したエントリのまま公開されてしまう
（見かけ上成功した intake が次回 append の `load_ledger()` を fail させる
false-success。`process_one` 参照）。

append 実行（既存台帳へのエントリ追記）時は、実際の変換（`process_one`）を
始める前の preflight として、既存台帳の全エントリについて `normalized_path`
が `out_dir` 配下に収まること・指す正規化 wav の実在・実バイト列 sha256 が
台帳記録値と一致することを検証する（`_check_existing_artifacts` 参照。
R23 P2 / R24 P2 対応）。`_validate_ledger_entry` は `sha256`/`normalized_path`
の構文的妥当性しか検証しないため、公開済みの正規化 wav が `run()` の外側で
削除・差し替えられていても `load_ledger()` は通過してしまう。さらに
`normalized_path` の**ファイル名部分のみ**を信頼して現在の `out_dir` と
結合して再構築し、resolve 後の実パスが `out_dir` 配下に収まることを
検証してから初めて実在・sha256 照合を行う（絶対パスで out_dir 外を指す
エントリ・out_dir 内 symlink 経由で外側へ迂回するエントリの両方を拒否）。
この検証はロック（`<ledger>.lock` → `<out_dir>/.intake.lock`）取得後・変換
開始前に行うため、検証後に他プロセスが実体を差し替えるレースは out_dir
ロックが構造的に防ぐ。欠損・不一致・封じ込め違反は
`LedgerArtifactIntegrityError` で列挙付き fail-closed 拒否する。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import soundfile as sf

FFMPEG_PATH: Optional[str] = shutil.which("ffmpeg")

SUPPORTED_EXTENSIONS = (".wav", ".m4a", ".mp3")

TARGET_SAMPLE_RATE = 24000

# 3 桁の直後は stem 終端 ($) か区切り文字のみ許可（非有界マッチで
# `UC-0010` を `UC-001` に誤帰属させない。R11 P2 対応）。
CARD_ID_PATTERN = re.compile(r"^(UC-\d{3})(?=$|[_\s.\-])", re.IGNORECASE)

LEDGER_SCHEMA = "user-donor-ledger/0.1"


class FfmpegNotFoundError(RuntimeError):
    """ffmpeg バイナリが見つからない場合に送出する。"""


class LedgerPathCollisionError(RuntimeError):
    """`--ledger` が incoming/導出出力/staging ディレクトリと衝突する場合に
    送出する（R12 P1 対応。`_check_ledger_path_collisions` 参照）。
    """


class OutputPathCollisionError(RuntimeError):
    """公開直前の最終出力パス検証（`_check_publish_path`）が、既存エントリ
    （ディレクトリ・symlink 含む）との衝突、または `out_dir` の外側への
    脱出を検出した場合に送出する（R16 P1 対応）。
    """


class LedgerLockError(RuntimeError):
    """`run()` が `<ledger>.lock` の `fcntl.flock(LOCK_EX | LOCK_NB)` を
    ノンブロッキングで取得できなかった場合に送出する（R21 P1 対応）。

    別の intake プロセスが同一台帳を対象に実行中であることを意味する。
    待ち合わせず即座に fail-closed 拒否する（`run` docstring 参照）。
    """


class OutDirLockError(RuntimeError):
    """`run()` が `<out_dir>/.intake.lock` の `fcntl.flock(LOCK_EX | LOCK_NB)` を
    ノンブロッキングで取得できなかった場合に送出する（R22 P1 対応）。

    `<ledger>.lock` は `--ledger` のパスのみから導出されるため、別
    `--ledger` かつ同一 `--out-dir` を指す 2 つの intake プロセスは異なる
    ロックを取得してしまい並行実行を防げない（`run` docstring 参照）。
    公開名前空間である `out_dir` 自体を別ロックで直列化することでこの穴を
    塞ぐ。待ち合わせず即座に fail-closed 拒否する。
    """


class NonPositiveDurationError(RuntimeError):
    """正規化後 wav の `duration_sec` が 0 以下、または非有限（NaN/inf）の
    場合に送出する（R21 P2 対応）。

    ヘッダのみの WAV（フレーム数 0）や、ffmpeg が exit 0 でもフレームを
    一切書き出さなかった場合に `measure_loudness()` が `duration_sec ==
    0.0` を返すことがあり、旧実装はこれをそのまま有効な intake として
    台帳へ記録・公開していた。`convert_pjs.py`/`build_dataset.py` の
    「非正 duration は無条件で不正」という意味論と揃え、台帳エントリの
    構築・公開の前に fail-closed 拒否する。

    R23 P2 対応で検査を拡張: 生値が正でも、台帳へ実際に書き込む丸め後の
    値（`round(duration_sec, 3)`）が 0 になる場合（0.5ms 未満の録音）も
    同じ例外で拒否する（`process_one` 参照）。丸め後 0.0 のまま公開すると
    台帳が自身の validator（`_validate_ledger_entry` の duration_sec > 0
    不変条件）に違反した状態になり、次回 append の `load_ledger()` が
    `LedgerSchemaError` で失敗する false-success を防ぐ。
    """


class LedgerSchemaError(RuntimeError):
    """既存台帳の `schema` が `LEDGER_SCHEMA` と一致しない、または `entries`
    がリストでない場合に送出する（R13 P2 対応）。旧実装は `entries` さえ
    存在すれば `schema` の値（誤植・別バージョン）を無視して現行版の
    エントリを追記・公開しており、この実装が解釈できない/意図しない
    フォーマットの台帳を静かに書き換えてしまっていた。`load_ledger` で
    fail-closed 拒否し、処理・公開のいずれも開始させない。
    """


class DuplicateSourceError(RuntimeError):
    """今回バッチの入力の `source_sha256` が、既存台帳のエントリまたは同一
    バッチ内の他ファイルと一致する場合に送出する（R17 P2 対応）。

    incoming をクリアせずに再実行した場合や、同じ収録が別ファイル名で
    2 度届いた場合、無条件の追記だと同一バイト列の take が台帳へ重複
    記録され、コーパスが 1 ドナー収録を二重に計上してしまう（「本物の
    再録」＝バイト列が異なる場合と「重複送付」＝バイト列が同一の場合の
    区別を失う）。`_check_duplicate_sources` が staging → `out_dir` への
    一括公開より前に検査し、重複があれば部分公開せず fail-closed 拒否する。
    """


class LedgerArtifactIntegrityError(RuntimeError):
    """既存台帳のエントリが指す `normalized_path` の実体が欠損している、
    台帳記録値と実バイト列の sha256 が一致しない、または `normalized_path`
    が `out_dir` の外側を指している場合に送出する（R23 P2 / R24 P2 対応）。

    `_validate_ledger_entry` は `sha256`/`normalized_path` について
    「64 桁の小文字 hex 文字列である」「非空文字列である」という構文的
    妥当性しか検証しておらず、公開済みの正規化 wav が削除・差し替えられた
    後でも台帳自体は妥当な形として通過してしまう。この状態で append を
    実行すると、`run()` は実体との不整合に気づかないまま既存エントリを
    含んだ台帳を再公開し、壊れた/偽の provenance を固定化してしまう
    （旧実装は sha256 の再計算を一切行わなかった）。`_check_existing_
    artifacts` が append 実行時の preflight として全既存エントリを
    検証し、欠損・不一致があれば公開全体を fail-closed 拒否する。

    [P2 修正] (review #264 R24) `normalized_path` が絶対パスで out_dir の
    外側を指す、または out_dir 内の symlink 経由で外側へ迂回する場合、
    旧実装はその外部実体をそのまま追跡してしまい、記録済み sha256 と
    偶然一致すればそのまま append を許してしまっていた（`out_dir` という
    公開名前空間の外側にある実体を、あたかも公開済み成果物であるかのように
    台帳へ再固定化する穴）。`_check_existing_artifacts` は `normalized_path`
    の**ファイル名部分のみ**を信頼し（ディレクトリ部分は無視 — 記録された
    ディレクトリ部分は改ざんされ得る）、常に現在の `out_dir` と結合して
    再構築したパスに対して検証する。ファイル名部分は `..`/絶対パス/
    セパレータを含む場合に字句検査で拒否し（`_is_safe_ledger_artifact_name`
    参照。`s1_dataprep` の `_is_safe_wav_name` と同型）、続けて resolve 後の
    実パスが `out_dir` 配下に収まることを確認する（symlink 迂回拒否）。
    """


@dataclass(frozen=True)
class LedgerEntry:
    """`user_donor_ledger.json` の `entries` 1 件分。

    `source_sha256`/`source_size_bytes` は正規化前の incoming ファイル自体の
    バイト列 sha256 とサイズ（R12 P2 対応。`source_filename` だけでは
    ファイル名が可変で、incoming 削除・差し替え後に原本 provenance を
    追跡できないため、hash で固定する）。
    """

    card_id: Optional[str]
    source_filename: str
    source_sha256: str
    source_size_bytes: int
    normalized_path: str
    sha256: str
    received_at: str
    duration_sec: float
    sample_rate: int
    rms_dbfs: Optional[float]
    peak_dbfs: Optional[float]
    alignment_status: str  # 常に "not_started"（TODO: アラインメント実装後に更新）


def discover_inputs(incoming_dir: Path) -> List[Path]:
    """受領ディレクトリ直下の対応拡張子ファイルを名前順で列挙する。"""
    return sorted(
        p
        for p in incoming_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def extract_card_id(filename: str) -> Optional[str]:
    """ファイル名先頭の `UC-NNN` トークンをカード ID として抽出する。"""
    stem = Path(filename).stem
    match = CARD_ID_PATTERN.match(stem)
    if match is None:
        return None
    return match.group(1).upper()


def assign_normalized_filenames(inputs: List[Path], out_dir: Path) -> Dict[Path, str]:
    """入力ごとに一意な正規化後ファイル名（`out_dir` 直下、拡張子込み）を予約する。

    `UC-001.wav` と `UC-001.m4a` のように拡張子違いで stem が一致する入力は、
    そのままだと同じ `{stem}.norm24k.wav` に解決されて衝突する（2 回目の
    ffmpeg -y が 1 回目を上書きし、台帳側は 2 エントリのまま hash が
    不整合になる）。ここで全入力を事前に検査し、2 件目以降はテイク連番
    （`{stem}.take2.norm24k.wav`, `take3`, ...）で一意化する。

    既存の `out_dir` 内エントリ名とも衝突しないようにする（別バッチの
    既存収録を上書きしない）。`inputs` の順序（`discover_inputs` の
    名前順）がそのままテイク番号の割り当て順になるため決定論的。

    予約対象は通常ファイルに限らず、`out_dir` 直下の**全エントリ**
    （ディレクトリ・symlink を含む）とする（R16 P1 対応）。旧実装は
    `p.is_file()` でファイルのみを予約していたため、`out_dir` に同名の
    ディレクトリ（または外部を指す symlink ディレクトリ）が既に存在する
    場合にその名前を予約せず、後続の公開フェーズで `shutil.move` が
    そのディレクトリの**中へ**ファイルを移動してしまう（symlink 経由なら
    `out_dir` の外側へ書き込む）事故につながっていた。
    """
    taken: set[str] = set()
    if out_dir.exists():
        taken.update(p.name for p in out_dir.iterdir())

    assigned: Dict[Path, str] = {}
    for src in inputs:
        stem = src.stem
        candidate = f"{stem}.norm24k.wav"
        take_no = 2
        while candidate in taken:
            candidate = f"{stem}.take{take_no}.norm24k.wav"
            take_no += 1
        taken.add(candidate)
        assigned[src] = candidate
    return assigned


def normalize_to_wav(src: Path, dst: Path) -> None:
    """`src` を 24kHz mono PCM16 wav として `dst` へ書き出す（ffmpeg 経由）。

    TODO: 現状は ffmpeg CLI をそのまま subprocess 起動する薄いラッパー。
    ラウドネス正規化（EBU R128 等）は未実装 — 本スクリプトは「読める形式へ
    揃える」だけを担い、ラウドネス概況の記録は測るだけで補正はしない。
    """
    if FFMPEG_PATH is None:
        raise FfmpegNotFoundError(
            "ffmpeg が見つかりません（`shutil.which('ffmpeg')` が None）。"
            "ffmpeg をインストールしてから再実行してください。"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-sample_fmt",
        "s16",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure_loudness(wav_path: Path) -> tuple[float, Optional[float], Optional[float]]:
    """正規化後 wav の長さ秒・RMS dBFS 概況・peak dBFS 概況を返す。

    厳密なラウドネス規格（LUFS 等）ではなく「概況」（README §6 の記述通り）
    のための簡易指標。無音ファイル（全サンプル 0）は dBFS を -inf にせず
    `None` として記録する（下流の JSON シリアライズで NaN/inf を避ける）。
    """
    data, sample_rate = sf.read(str(wav_path), dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    duration_sec = len(data) / float(sample_rate) if sample_rate else 0.0

    rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0
    peak = float(np.max(np.abs(data))) if len(data) else 0.0

    rms_dbfs = 20.0 * np.log10(rms) if rms > 0.0 else None
    peak_dbfs = 20.0 * np.log10(peak) if peak > 0.0 else None

    return duration_sec, rms_dbfs, peak_dbfs


# `LedgerEntry` の各フィールドが台帳 JSON 上で満たすべき必須性・型
# （R19 P2 対応。`_validate_ledger_entry` 参照）。`Optional[X]` なフィールド
# は `type(None)` を許容型に含める。`float` 系フィールドは JSON 上で整数値
# （小数部無し）になり得るため `int` も許容する。`bool` は `int` の
# サブクラスのためこの型チェックだけでは素通りするが、値レベルの不変条件
# 検証（`_LEDGER_ENTRY_VALUE_VALIDATORS` 参照。R22 P2 対応）で別途弾く。
_LEDGER_ENTRY_REQUIRED_FIELDS: Dict[str, tuple] = {
    "card_id": (str, type(None)),
    "source_filename": (str,),
    "source_sha256": (str,),
    "source_size_bytes": (int,),
    "normalized_path": (str,),
    "sha256": (str,),
    "received_at": (str,),
    "duration_sec": (float, int),
    "sample_rate": (int,),
    "rms_dbfs": (float, int, type(None)),
    "peak_dbfs": (float, int, type(None)),
    "alignment_status": (str,),
}

# sha256/source_sha256 の値域: `hashlib.sha256(...).hexdigest()` が生成する
# 64 桁の小文字 hex 文字列のみを正とする（R22 P2 対応）。
_LEDGER_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# card_id の値域: `extract_card_id()` が返す `UC-NNN`（3 桁, 大文字）形式の
# 完全一致のみを正とする（R22 P2 対応。ファイル名中の接頭辞境界チェックを
# 行う `CARD_ID_PATTERN` とは異なり、値そのものの完全一致を要求する）。
_LEDGER_CARD_ID_PATTERN = re.compile(r"^UC-\d{3}$")

# alignment_status の値域: 現行実装 (`process_one`) が書き込む語彙のみ
# （R22 P2 対応。アラインメント実装後に語彙を拡張する場合はここも同期して
# 更新すること — `LedgerEntry.alignment_status` docstring 参照）。
_LEDGER_ALIGNMENT_STATUS_VALUES = frozenset({"not_started"})

# bool は int のサブクラスのため `_LEDGER_ENTRY_REQUIRED_FIELDS` の型検査
# だけでは `sample_rate: true` 等を素通りさせてしまう（R22 P2 レビュー
# 指摘）。数値であるべき全フィールドを対象に明示的に bool を拒否する。
_LEDGER_ENTRY_NUMERIC_FIELDS = (
    "duration_sec",
    "sample_rate",
    "source_size_bytes",
    "rms_dbfs",
    "peak_dbfs",
)

# 非空でなければならないパス/ファイル名系フィールド（R22 P2 対応）。
_LEDGER_ENTRY_NON_EMPTY_STRING_FIELDS = ("source_filename", "normalized_path")


def _validate_ledger_entry(entry: object, index: int, ledger_path: Path) -> None:
    """`entries[index]` が `LedgerEntry` の必須フィールド・型・値不変条件を
    満たすことを検証する（R19 P2: フィールド必須性・型 / R22 P2: 値不変条件）。
    違反は `LedgerSchemaError` で fail-closed 拒否する。

    旧実装は `entries` がリストであることしか検証しておらず、
    `source_sha256` を欠くエントリ（例: schema バージョンが一致するだけの
    破損/旧世代台帳）がそのまま通過していた。`_check_duplicate_sources` は
    `source_sha256` の無いエントリを黙ってスキップするため、そのエントリが
    表す重複ドナー音声/破損 provenance が検出されずに再公開される穴が
    あった（R19 P2 レビュー指摘）。

    R19 P2 のフィールド必須性・型検証は「型が合っているか」までしか見て
    おらず、型は正しいが値として不正（`source_sha256: "garbage"`、
    `duration_sec: NaN`、`sample_rate: true`、負の `source_size_bytes` 等）
    な場合はそのまま通過していた。無効な値のまま `_check_duplicate_sources`
    の重複検査をすり抜けたり、`json.dump()` が非有限値を再出力し続けたり
    することで、`load_ledger()` を通過しているにも関わらず台帳が実質的に
    壊れた状態を保つ穴があった（R22 P2 レビュー指摘）。`LedgerEntry` が
    生成コード（`process_one`/`extract_card_id`/`measure_loudness` 等）を
    通じて実際に生成しうる値域を正として、フィールド単位で不変条件を検証する
    （本関数が台帳検証ファミリーの最終段: schema 完全一致 (R13) → entries
    リスト (R13) → エントリ必須フィールド/型 (R19) → 値不変条件 (R22)）。
    """
    if not isinstance(entry, dict):
        raise LedgerSchemaError(
            f"{ledger_path} の entries[{index}] が dict ではありません "
            f"(type={type(entry).__name__})。fail-closed で拒否します"
        )
    for field, allowed_types in _LEDGER_ENTRY_REQUIRED_FIELDS.items():
        if field not in entry:
            raise LedgerSchemaError(
                f"{ledger_path} の entries[{index}] にフィールド {field!r} が"
                f"ありません。fail-closed で拒否します"
            )
        value = entry[field]
        if not isinstance(value, allowed_types):
            raise LedgerSchemaError(
                f"{ledger_path} の entries[{index}].{field} の型が不正です "
                f"(type={type(value).__name__})。fail-closed で拒否します"
            )

    def _reject(field: str, reason: str) -> None:
        raise LedgerSchemaError(
            f"{ledger_path} の entries[{index}].{field} が不正です（{reason}）。"
            f"fail-closed で拒否します（R22 P2 対応）"
        )

    # bool は int のサブクラスのため、数値フィールドはまず bool 混入を弾く。
    for field in _LEDGER_ENTRY_NUMERIC_FIELDS:
        if isinstance(entry[field], bool):
            _reject(field, f"bool 値です (value={entry[field]!r})")

    # sha256 / source_sha256: 64 桁の小文字 hex 文字列。
    for field in ("sha256", "source_sha256"):
        value = entry[field]
        if not _LEDGER_SHA256_PATTERN.fullmatch(value):
            _reject(field, f"64 桁の小文字 hex 文字列ではありません (value={value!r})")

    # duration_sec: 有限かつ正（0 以下・NaN・inf は不正）。
    duration_sec = entry["duration_sec"]
    if not math.isfinite(duration_sec) or duration_sec <= 0.0:
        _reject("duration_sec", f"有限かつ正の値ではありません (value={duration_sec!r})")

    # sample_rate: TARGET_SAMPLE_RATE (24kHz) との完全一致。
    # [P2 修正] (review #264 R24) 従来は「正の整数」のみを検査しており、
    # 型は正しいが値が異なる（例: `sample_rate: 44100`）エントリを素通り
    # させていた。`process_one` は常に `sample_rate=TARGET_SAMPLE_RATE` の
    # エントリしか生成しない契約のため、それ以外の値を持つエントリは
    # 台帳の記録内容自体が実装契約と矛盾しており、append で再公開すると
    # 「24kHz 正規化 wav の台帳」という契約が台帳内部で自己矛盾したまま
    # 固定化される。完全一致のみを許容する。
    sample_rate = entry["sample_rate"]
    if sample_rate != TARGET_SAMPLE_RATE:
        _reject(
            "sample_rate",
            f"TARGET_SAMPLE_RATE ({TARGET_SAMPLE_RATE}) と一致しません "
            f"(value={sample_rate!r})",
        )

    # source_size_bytes: 非負の整数（負のバイト数は物理的に無効）。
    source_size_bytes = entry["source_size_bytes"]
    if source_size_bytes < 0:
        _reject("source_size_bytes", f"負の値です (value={source_size_bytes!r})")

    # rms_dbfs / peak_dbfs: None または有限 float（無音時は None、それ以外は
    # `20.0 * log10(...)` の結果であり NaN/inf にはならないはず）。
    for field in ("rms_dbfs", "peak_dbfs"):
        value = entry[field]
        if value is not None and not math.isfinite(value):
            _reject(field, f"有限値でも None でもありません (value={value!r})")

    # received_at: ISO 8601 として parse 可能。
    received_at = entry["received_at"]
    try:
        datetime.fromisoformat(received_at)
    except ValueError:
        _reject("received_at", f"ISO 8601 として解釈できません (value={received_at!r})")

    # alignment_status: 現行実装が書き込む語彙に一致。
    alignment_status = entry["alignment_status"]
    if alignment_status not in _LEDGER_ALIGNMENT_STATUS_VALUES:
        _reject(
            "alignment_status",
            f"既知の語彙 {sorted(_LEDGER_ALIGNMENT_STATUS_VALUES)} に含まれません "
            f"(value={alignment_status!r})",
        )

    # card_id: None または `UC-NNN`（3 桁）形式の完全一致。
    card_id = entry["card_id"]
    if card_id is not None and not _LEDGER_CARD_ID_PATTERN.fullmatch(card_id):
        _reject("card_id", f"UC-NNN 形式ではありません (value={card_id!r})")

    # パス/ファイル名系フィールド: 非空文字列。
    for field in _LEDGER_ENTRY_NON_EMPTY_STRING_FIELDS:
        if entry[field] == "":
            _reject(field, "空文字列です")


def load_ledger(ledger_path: Path) -> dict:
    """既存台帳を読み込む。存在しなければ新規スキーマの空台帳を返す。

    既存台帳がある場合は `schema == LEDGER_SCHEMA` の完全一致と `entries`
    がリストであることを検証する（R13 P2 対応）。どちらか不一致なら
    `LedgerSchemaError` を送出し fail-closed で拒否する（未知・旧バージョン
    ・破損した台帳への暗黙の追記・公開を防ぐ）。さらに `entries` 各要素が
    `LedgerEntry` の必須フィールド・型を満たすことも検証する（R19 P2
    対応。`_validate_ledger_entry` 参照）。
    """
    if not ledger_path.exists():
        return {"schema": LEDGER_SCHEMA, "entries": []}
    with ledger_path.open("r", encoding="utf-8") as f:
        ledger = json.load(f)
    schema = ledger.get("schema")
    if schema != LEDGER_SCHEMA:
        raise LedgerSchemaError(
            f"{ledger_path} の schema ({schema!r}) が {LEDGER_SCHEMA!r} と一致しません"
            f"（fail-closed で拒否。未知/旧バージョンの台帳への誤った追記・公開を防止）"
        )
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise LedgerSchemaError(
            f"{ledger_path} の entries がリストではありません "
            f"(type={type(entries).__name__})。fail-closed で拒否します"
        )
    for index, entry in enumerate(entries):
        _validate_ledger_entry(entry, index, ledger_path)
    return ledger


def _is_existing_ledger_file(path: Path) -> bool:
    """`path` が現行スキーマの台帳ファイルとして読み込み可能かどうかを判定
    する（R19 P2 対応）。

    `_check_ledger_path_collisions` が `--ledger` 自身の既存ファイルを
    『正当な既存台帳（読み込んで追記する対象）』と『偶然そこにあった無関係
    なファイル（正規化 wav 等、上書きすると破壊する）』とで区別するために
    使う。`load_ledger` が送出し得る例外（JSON 解析失敗・スキーマ不一致・
    エントリ形状不正・デコード不能なバイト列）はいずれも「台帳として読め
    ない」ことを意味するため `False` として扱い、呼び出し側は従来通り衝突
    として fail-closed 拒否する。
    """
    try:
        load_ledger(path)
    except (LedgerSchemaError, ValueError, OSError, UnicodeDecodeError):
        return False
    return True


def _atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    """`path` の親ディレクトリに一意な一時ファイルを**排他生成**し、
    `writer(tmp_path)` にその中身を書かせてから `path` へ `replace()` する
    （R14 P1 対応。`save_ledger`/`_restore_ledger` の共通実装）。

    旧実装は `<ledger>.tmp`（`save_ledger`）/ `<ledger>.rollback.tmp`
    （`_restore_ledger`）という**決定論的**な一時パスを使っており、たとえば
    `out/user_donor_ledger.json` の隣に無関係な `out/user_donor_ledger.json.tmp`
    が既に存在した場合、`open("w")`/`write_bytes()` がそれを黙って
    truncate し、続く `replace()` がそのファイル名ごと消してしまう事故が
    あった。衝突 preflight（`_check_ledger_path_collisions`）は `--ledger`
    本体のみを検査対象としており、この決定論的な派生 tmp パスまでは
    対象に含んでいなかった。

    `tempfile.mkstemp` の排他生成（`O_CREAT | O_EXCL`）へ切り替えることで、
    既存ファイルへの書き込みが構造的に発生しなくなる（無関係ファイルとの
    衝突自体が原理的に起こらない）。書き込み中に失敗した場合は tmp ファイルを
    削除して残さない。

    tmp パスは常に `path.parent` 直下にランダム名で生成される。`--ledger`
    本体が `_check_ledger_path_collisions` の preflight を通過していれば
    （incoming 原本・導出出力・staging ディレクトリ自体のいずれとも resolve
    済みパスで衝突しない）、この tmp パスが同じ集合と衝突することは
    `mkstemp` の一意生成保証により実質的に起こらない — 本 docstring がその
    衝突 preflight の適用範囲宣言を兼ねる（R14 P1 レビュー指摘の要求どおり、
    一時公開パスも preflight の保護対象であることを明記する）。
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        writer(tmp_path)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def save_ledger(ledger_path: Path, ledger: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(tmp_path: Path) -> None:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
            f.write("\n")

    _atomic_write(ledger_path, _write)


def _restore_ledger(ledger_path: Path, previous_bytes: Optional[bytes]) -> None:
    """`ledger_path` を公開フェーズ開始前のバイト列 (`previous_bytes`) へ
    巻き戻す（R13 P2 対応）。`previous_bytes` が `None`（開始前は台帳が
    存在しなかった）場合は、このバッチが新規生成した台帳を削除する。

    `save_ledger()` と同じ `_atomic_write`（排他生成 tmp + `replace()`）で
    書き戻す（巻き戻し処理自体の中断でも中途半端な JSON を `ledger_path` に
    残さない。R14 P1 対応で決定論的 tmp パスを排した）。
    """
    if previous_bytes is None:
        if ledger_path.exists():
            ledger_path.unlink()
        return
    _atomic_write(ledger_path, lambda tmp_path: tmp_path.write_bytes(previous_bytes))


def _ledger_lock_path(ledger_path: Path) -> Path:
    """`ledger_path` に対応する排他制御用ロックファイルの決定論的パスを返す
    （R21 P1 対応。`run()` docstring 参照）。`ledger_path` と同じ親
    ディレクトリに `{ledger_path.name}.lock` として配置する
    （append ワークフローで `--ledger` が `out_dir` 内にある場合、ロック
    ファイルも `out_dir` 内に残置される — `_check_ledger_path_collisions`
    が `lock_path` を衝突検査の予約対象として扱うことで、この残置が誤って
    衝突として検出されないようにする）。
    """
    return ledger_path.parent / f"{ledger_path.name}.lock"


def _out_dir_lock_path(out_dir: Path) -> Path:
    """`out_dir` に対応する排他制御用ロックファイルの決定論的パスを返す
    （R22 P1 対応。`run()` docstring 参照）。`out_dir` 直下に `.intake.lock`
    として配置する（`out_dir` 自体が公開名前空間であり、ロックもその名前空間の
    一部として扱う）。`.norm24k.wav`/`.takeN.norm24k.wav` で終わる正規化後
    ファイル名の集合とは名前空間が構造的に交差しないため、通常の公開フローで
    このパスに実ファイルが衝突することはない。
    """
    return out_dir / ".intake.lock"


def _check_ledger_path_collisions(
    ledger_path: Path,
    inputs: List[Path],
    filenames: Dict[Path, str],
    out_dir: Path,
    staging_dir: Path,
    lock_path: Optional[Path] = None,
) -> None:
    """`--ledger` の resolve 済みパスが以下のいずれとも衝突しないことを検査する
    （R12 P1 対応。convert_pjs.py/gate_synth.py の衝突拒否ファミリーと同じ
    流儀 — fail-closed・resolve 済みパス比較で symlink 迂回を封じる）。

    - incoming の元音源ファイル（衝突すると `save_ledger()` がドナー原本を
      JSON で上書きし、原本を破壊する）
    - 今回バッチの導出出力（staging 内の一時パス・`out_dir` 内の最終正規化
      wav。衝突すると `save_ledger()` が台帳記録済みの音声 hash の実体を
      JSON で上書きする）
    - `out_dir` に既に公開済みの他バッチのファイル（同じ理由で上書き事故になる。
      ただし `--ledger` 自身が `out_dir` 内の既存台帳ファイルを指している
      場合（append ワークフロー）は例外 — `_is_existing_ledger_file` で
      中身が現行スキーマの台帳として読み込めることを確認できたときに限り、
      この衝突対象から除外する。R19 P2 対応）
    - staging ディレクトリ自体（`--ledger` がその内部を指していると、
      `run()` の `finally` が staging を丸ごと `rmtree` する際に、直前に
      保存したはずの台帳ごと消え去る）

    `lock_path`（`run()` が `_ledger_lock_path()` で決定論的に導出する
    `<ledger>.lock`）を渡した場合は、これも上記の incoming 原本・導出出力
    衝突検査に同じ資格で加える（R21 P1 対応 — ロックファイルという新しい
    永続アーティファクトを、ドナー原本や正規化済み wav を上書きし得る
    "書き込み先" の集合から構造的に除外する）。加えて `out_dir` 内の
    「公開済み既存ファイル」走査では、`lock_path` と一致する既存エントリを
    我々自身が残置したロックファイルとして予約済み扱いし、衝突対象から
    除外する（ロックファイルは残置する設計のため、2 回目以降のバッチで
    毎回この走査に引っかかっては preflight が壊れる）。

    `run()` 内で staging_dir 作成直後・実際の変換/移動/台帳保存より前に
    呼ぶこと（`--incoming-dir` を読むだけの preflight で、音声処理は一切
    発生しない）。
    """
    resolved_ledger = ledger_path.resolve()
    resolved_lock = lock_path.resolve() if lock_path is not None else None

    for src in inputs:
        resolved_src = src.resolve()
        if resolved_ledger == resolved_src:
            raise LedgerPathCollisionError(
                f"--ledger ({ledger_path}) は incoming の元音源ファイル ({src}) "
                f"と衝突しています（fail-closed で拒否。ドナー原本の破壊を防止）"
            )
        if resolved_lock is not None and resolved_lock == resolved_src:
            raise LedgerPathCollisionError(
                f"ロックファイル ({lock_path}) は incoming の元音源ファイル "
                f"({src}) と衝突しています（fail-closed で拒否。ドナー原本の "
                f"破壊を防止。R21 P1 対応）"
            )

    for src in inputs:
        filename = filenames[src]
        for derived_dir in (staging_dir, out_dir):
            candidate = derived_dir / filename
            resolved_candidate = candidate.resolve()
            if resolved_ledger == resolved_candidate:
                raise LedgerPathCollisionError(
                    f"--ledger ({ledger_path}) は導出出力 ({candidate}) と衝突"
                    f"しています（fail-closed で拒否。正規化 wav を JSON で"
                    f"上書きする事故を防止）"
                )
            if resolved_lock is not None and resolved_lock == resolved_candidate:
                raise LedgerPathCollisionError(
                    f"ロックファイル ({lock_path}) は導出出力 ({candidate}) と"
                    f"衝突しています（fail-closed で拒否。正規化 wav をロック"
                    f"ファイルで上書きする事故を防止。R21 P1 対応）"
                )

    if out_dir.exists():
        for existing in out_dir.iterdir():
            if not existing.is_file():
                continue
            resolved_existing = existing.resolve()
            if resolved_lock is not None and resolved_existing == resolved_lock:
                # R21 P1 対応: 我々自身が残置したロックファイル（append
                # ワークフローで `--ledger` が `out_dir` 内にある場合、
                # `<ledger>.lock` も `out_dir` 内に残る）。ロックファイルは
                # 削除しない設計（flock の意味が壊れる競合窓を避けるため）
                # なので、2 回目以降のバッチではこの走査に必ず現れる —
                # 予約済みの自己所有物として除外し、衝突対象にしない。
                continue
            if resolved_ledger != resolved_existing:
                continue
            if _is_existing_ledger_file(existing):
                # R19 P2 対応: `--ledger` が `out_dir` 内にある配置（append
                # ワークフロー）では、2 回目以降のバッチでこの preflight が
                # `--ledger` 自身を「out_dir に公開済みの既存ファイル」として
                # 見つけてしまう（resolved パスが完全一致するため）。これは
                # 事故ではなく意図した配置であり、中身が現行スキーマの台帳
                # として読み込める場合に限り衝突対象から除外する。中身が
                # 台帳として読み込めない場合（`--ledger` が誤って正規化 wav
                # 等の無関係な既存ファイルを指しているケース）は除外せず、
                # 従来通り下の衝突として拒否する。
                continue
            raise LedgerPathCollisionError(
                f"--ledger ({ledger_path}) は out_dir に公開済みの既存"
                f"ファイル ({existing}) と衝突しています（fail-closed で"
                f"拒否。過去バッチの正規化 wav を JSON で上書きする事故を"
                f"防止）"
            )

    resolved_staging = staging_dir.resolve()
    try:
        resolved_ledger.relative_to(resolved_staging)
    except ValueError:
        pass
    else:
        raise LedgerPathCollisionError(
            f"--ledger ({ledger_path}) は staging ディレクトリ ({staging_dir}) "
            f"自体または内部を指しています（fail-closed で拒否。バッチ終了時の "
            f"staging 削除で台帳ごと消失する事故を防止）"
        )
    if resolved_lock is not None:
        try:
            resolved_lock.relative_to(resolved_staging)
        except ValueError:
            pass
        else:
            raise LedgerPathCollisionError(
                f"ロックファイル ({lock_path}) は staging ディレクトリ "
                f"({staging_dir}) 自体または内部を指しています（fail-closed で"
                f"拒否。バッチ終了時の staging 削除でロックファイルごと消失"
                f"する事故を防止。R21 P1 対応）"
            )


def _check_publish_path(final_path: Path, out_dir: Path) -> None:
    """公開直前に `final_path` の安全性を検証する（R16 P1 対応）。

    `assign_normalized_filenames` の予約は `out_dir` の**スナップショット
    時点**の状態に基づく事前検査であり、予約から実際の `shutil.move` まで
    の間に `out_dir` の中身が変わる可能性（TOCTOU）や、予約ロジック自体の
    見落としに対する最終防御としてここでも検証する。

    - `final_path` が既に存在する場合（ディレクトリ・symlink 含む）は拒否
      する。`shutil.move` は移動先が既存ディレクトリだと"そのディレクトリの
      中へ"移動する挙動を持つため、ここを見落とすと `out_dir` に同名の
      ディレクトリ（または symlink ディレクトリ）が存在する場合に、想定と
      異なる場所へファイルが書き込まれる（ledger の `normalized_path` が
      指す実体と食い違う）。
    - `final_path.parent` の resolve 済みパスが `out_dir` の resolve 済み
      パスと一致することを検証する（`out_dir` 自体が symlink ディレクトリ
      経由になっている場合に、公開先が `out_dir` の外側へ脱出するケースを
      拒否する）。
    """
    if final_path.exists() or final_path.is_symlink():
        raise OutputPathCollisionError(
            f"公開先に既存エントリがあります ({final_path})。fail-closed で"
            f"拒否します（ディレクトリ/symlink 経由の意図しない書き込みを防止。"
            f"R16 P1 対応）"
        )
    resolved_parent = final_path.parent.resolve()
    resolved_out_dir = out_dir.resolve()
    if resolved_parent != resolved_out_dir:
        raise OutputPathCollisionError(
            f"公開先の親ディレクトリ ({resolved_parent}) が out_dir "
            f"({resolved_out_dir}) と一致しません。fail-closed で拒否します"
            f"（symlink ディレクトリ経由の out_dir 脱出を防止。R16 P1 対応）"
        )


def _check_duplicate_sources(entries: List[LedgerEntry], ledger: dict) -> None:
    """今回バッチ `entries` の `source_sha256` を、既存台帳の全エントリ①
    および同一バッチ内の他ファイル②の両方と突き合わせ、重複があれば
    fail-closed 拒否する（R17 P2 対応。`run` が staging → `out_dir` へ
    一括公開する直前の preflight として呼ぶ）。

    バイト列が異なる再録（`source_sha256` が異なる）は対象外 — 積み立て
    運用の「同カードの再録は正常系」という方針（本ファイル冒頭の docstring
    参照）を壊さない。重複が見つかった場合は、どのファイルがどのエントリ/
    ファイルと重複しているかを列挙した単一の例外で公開全体を拒否し、
    部分公開はしない。
    """
    existing_by_hash: Dict[str, List[str]] = {}
    for existing_entry in ledger.get("entries", []):
        existing_hash = existing_entry.get("source_sha256")
        if existing_hash is None:
            continue
        existing_by_hash.setdefault(existing_hash, []).append(
            existing_entry.get("source_filename", "<unknown>")
        )

    batch_by_hash: Dict[str, List[str]] = {}
    for entry in entries:
        batch_by_hash.setdefault(entry.source_sha256, []).append(entry.source_filename)

    problems: List[str] = []
    for source_sha256, batch_filenames in batch_by_hash.items():
        ledger_matches = existing_by_hash.get(source_sha256, [])
        if ledger_matches:
            problems.append(
                f"- source_sha256={source_sha256[:12]}...: バッチ内 {batch_filenames} が"
                f"既存台帳のエントリ {ledger_matches} と同一バイト列です"
            )
        if len(batch_filenames) > 1:
            problems.append(
                f"- source_sha256={source_sha256[:12]}...: バッチ内 {batch_filenames} 同士が"
                f"同一バイト列で重複しています"
            )

    if problems:
        raise DuplicateSourceError(
            "重複した source_sha256 を検出したため公開全体を拒否します"
            "（fail-closed。部分公開はしません。R17 P2 対応）:\n" + "\n".join(problems)
        )


def _is_safe_ledger_artifact_name(name: str) -> bool:
    """`name` が `out_dir` 配下の単一ファイル名として安全かを判定する
    （字句検査のみ。resolve 後の封じ込め検査は呼び出し側が別途行う）。

    review #264 R24 P2 対応。`s1_dataprep` の `_is_safe_wav_name` と同型の
    判定ロジック（絶対パス・`..`/`.` セグメント・パス区切り文字を拒否）。
    """
    if not name or os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return False
    if name in (".", ".."):
        return False
    return True


def _check_existing_artifacts(ledger: dict, ledger_path: Path, out_dir: Path) -> None:
    """append 実行時の preflight として、既存台帳の全エントリについて
    `normalized_path` が `out_dir` 配下に収まること・指す正規化 wav の実在・
    実バイト列 sha256 が台帳記録値と一致することを検証する（R23 P2 /
    R24 P2 対応）。

    `_validate_ledger_entry` は `sha256`/`normalized_path` の構文的妥当性
    （hex 文字列形式・非空文字列）しか検証しないため、公開済みの正規化 wav
    が `run()` の外側で削除・差し替えられた場合でも `load_ledger()` は
    通過してしまう。この状態で append すると、実体と食い違う既存エントリを
    含んだまま台帳が再公開され、壊れた/偽の provenance が固定化される
    （`LedgerArtifactIntegrityError` docstring 参照）。

    [P2 修正] (review #264 R24) 従来はハッシュ照合の**前に** `normalized_path`
    が `out_dir` の外側を指していないかを検証しておらず、絶対パスで
    out_dir 外を指すエントリや、out_dir 内の symlink 経由で外側へ迂回する
    エントリを、外部実体のバイト列が記録済み sha256 とたまたま一致すれば
    そのまま受理してしまっていた。ここでは記録された `normalized_path` の
    **ファイル名部分のみ**を信頼し（ディレクトリ部分は改ざんされ得るため
    無視）、`_is_safe_ledger_artifact_name` による字句検査（絶対パス・
    `..`/セパレータ拒否）を通過したファイル名を、呼び出し時点の `out_dir`
    と結合して再構築する。さらに resolve 後の実パスが `out_dir` 配下に
    収まることを確認してから（symlink 迂回拒否）はじめて実在・sha256 照合
    を行う。

    `run()` はロック（`<ledger>.lock` → `<out_dir>/.intake.lock`）取得後・
    変換（`process_one`）開始前に本関数を呼ぶ。out_dir ロック保持中に検査
    するため、検査完了後に他プロセスが同じ正規化 wav を差し替えるレースは
    ロックが構造的に防ぐ（本関数自体は out_dir へ一切書き込まない読み取り
    専用の preflight）。コーパス規模（数十ファイル）では全既存エントリの
    再ハッシュコスト（O(コーパス全体)）は無視できる。

    欠損・不一致・封じ込め違反は、どのエントリがどう壊れているかを列挙した
    単一の例外で公開全体を fail-closed 拒否する（`_check_duplicate_sources`
    と同様、部分的な黙認はしない）。
    """
    resolved_out_dir = out_dir.resolve()
    problems: List[str] = []
    for index, entry in enumerate(ledger.get("entries", [])):
        source_filename = entry.get("source_filename", "<unknown>")
        raw_normalized_path = entry["normalized_path"]
        recorded_sha256 = entry["sha256"]

        # [P2 修正] (review #264 R25) `Path(...).name` で basename を取り出す
        # 前に、記録された normalized_path **自体**を検証する。旧実装は
        # 絶対パス/`..` を含む raw 値であっても即座に basename だけを信頼
        # して切り詰め、`out_dir / <basename>` へ組み直した実体のバイト列が
        # たまたま記録済み sha256 と一致すれば、そのまま健全な既存エントリ
        # として受理し無変更のまま再公開していた（台帳の記録値自体が破損/
        # 改ざんされていたという事実そのものを隠蔽してしまう）。basename
        # 抽出より前に raw 値自体を resolve し、out_dir 配下に収まって
        # いることを要求する（収まらなければ「台帳の記録値自体が不正」
        # として列挙し fail-closed 拒否する。後段の
        # `_is_safe_ledger_artifact_name` + resolve 封じ込め検査は、この
        # 検査を通過した raw 値の basename に対する多層防御として維持する）。
        try:
            raw_resolved = Path(raw_normalized_path).resolve()
        except (OSError, ValueError):
            raw_resolved = None
        if raw_resolved is None or (
            raw_resolved != resolved_out_dir and resolved_out_dir not in raw_resolved.parents
        ):
            problems.append(
                f"- entries[{index}] ({source_filename}): normalized_path "
                f"({raw_normalized_path}) の記録値自体が out_dir "
                f"({resolved_out_dir}) の外側を指しています（台帳の記録値"
                f"自体が不正なため拒否します）"
            )
            continue

        artifact_name = Path(raw_normalized_path).name
        if not _is_safe_ledger_artifact_name(artifact_name):
            problems.append(
                f"- entries[{index}] ({source_filename}): normalized_path "
                f"({raw_normalized_path}) のファイル名部分 ({artifact_name!r}) "
                f"が安全な形式ではありません（絶対パス/`..`/セパレータ経由の "
                f"out_dir 外への逸脱の可能性があるため拒否します）"
            )
            continue
        normalized_path = out_dir / artifact_name
        resolved_candidate = normalized_path.resolve()
        if (
            resolved_candidate != resolved_out_dir
            and resolved_out_dir not in resolved_candidate.parents
        ):
            problems.append(
                f"- entries[{index}] ({source_filename}): normalized_path "
                f"({raw_normalized_path}) の実体解決先 ({resolved_candidate}) "
                f"が out_dir ({resolved_out_dir}) の外側です（symlink 経由の "
                f"迂回の可能性があるため拒否します）"
            )
            continue
        if not normalized_path.is_file():
            problems.append(
                f"- entries[{index}] ({source_filename}): normalized_path "
                f"({normalized_path}) が存在しません（削除された可能性が"
                f"あります）"
            )
            continue
        actual_sha256 = sha256_of(normalized_path)
        if actual_sha256 != recorded_sha256:
            problems.append(
                f"- entries[{index}] ({source_filename}): normalized_path "
                f"({normalized_path}) の実バイト列 sha256 "
                f"({actual_sha256[:12]}...) が台帳記録値 "
                f"({recorded_sha256[:12]}...) と一致しません（差し替えられた"
                f"可能性があります）"
            )

    if problems:
        raise LedgerArtifactIntegrityError(
            f"{ledger_path} の既存エントリに実体との不整合を検出したため "
            f"append を拒否します（fail-closed。部分公開はしません。"
            f"R23 P2 対応）:\n" + "\n".join(problems)
        )


def process_one(src: Path, staging_dir: Path, filename: str, publish_dir: Path) -> LedgerEntry:
    """`src` を `staging_dir/filename` へ正規化し、公開後の想定パスで台帳エントリを作る。

    実ファイルは呼び出し時点では `staging_dir` にしか存在しない
    （`run` が全件成功後に `publish_dir` へ一括移動する）。`normalized_path`
    には公開後の最終パス（`publish_dir/filename`）を記録するため、台帳の
    内容は公開完了後の状態と最初から一致する。

    `source_sha256`/`source_size_bytes` は `src`（incoming の元ファイル）を
    一度だけ読んだバイト列から確定し、同じバイト列を staging 内スナップ
    ショットとして書き出す（R13 P2 対応）。以後の変換（`normalize_to_wav`）
    はこのスナップショットを入力とし、`src` 原本には二度と触れない。旧
    実装は sha256/size を計算する read と ffmpeg が実際に読む read が別
    タイミングだったため、その間に incoming ファイルが差し替わると台帳が
    古いバイト列を、正規化 wav が新しいバイト列を反映するというねじれが
    起き得た。単一 read から得たバイト列をハッシュにも変換入力にも使う
    ことで、ハッシュ対象と変換入力が構造的に一致することを保証する。

    スナップショットは `staging_dir` 直下に置かず、専用サブディレクトリ
    `staging_dir/src_snapshots/{元ファイル名}` へ配置する（R14 P1 対応）。
    旧実装は `staging_dir/__src_snapshot__{元ファイル名}` というプレフィクス
    方式で、公開対象の staged 出力 `staging_dir/{filename}`（トップレベル）
    と同じ名前空間を共有していた。バッチに `__src_snapshot__z.wav` と
    `z.norm24k.wav` が同居すると、前者の派生出力名が
    `__src_snapshot__z.norm24k.wav` になり、これが後者のスナップショット
    パスと一致してしまう。後発の `write_bytes()` が先発の正規化済み出力を
    ledger hash 計算後に上書きし、公開されるバイト列が台帳エントリと
    食い違うミスラベルが発生していた。サブディレクトリへ分離することで、
    スナップショットのパス空間は staged 出力のパス空間と構造的に交差しない
    （`staging_dir` 直下の子はスナップショットに一切現れない）。

    正規化後 wav の `duration_sec` が 0 以下、または非有限（NaN/inf）の
    場合は台帳エントリの構築前に `NonPositiveDurationError` で fail-closed
    拒否する（R21 P2 対応）。ヘッダのみの WAV（フレーム数 0）や、ffmpeg が
    exit 0 でもフレームを一切書き出さなかった場合に `measure_loudness()`
    が `duration_sec == 0.0` を返すことがあり、旧実装はこれをそのまま
    有効な intake として台帳へ記録・公開していた（使用不能なドナー音声が
    「成功した intake」として記録される穴。`convert_pjs.py`/
    `build_dataset.py` の「非正 duration は無条件で不正」という意味論と
    揃える）。

    この生値検査に加え、台帳へ実際に書き込む丸め後の値
    (`round(duration_sec, 3)`) についても同じ 0 以下/非有限チェックを行う
    （R23 P2 対応）。ffmpeg が 0.0005 秒未満の正の長さの WAV を生成した
    場合、生値は非正チェックを素通りするが丸め後は `duration_sec: 0.0`
    となり、`_validate_ledger_entry` 自身の duration_sec > 0 不変条件に
    違反した状態のまま公開されてしまう（false-success で次回 append の
    `load_ledger()` が失敗する）。他の丸め対象フィールド
    (`rms_dbfs`/`peak_dbfs`) は丸めても有限性が保たれるため同型の穴は
    無い — 不正値になり得るのは下限 0 を持つ `duration_sec` のみ。
    """
    card_id = extract_card_id(src.name)

    source_bytes = src.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_size_bytes = len(source_bytes)

    snapshot_dir = staging_dir / "src_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / src.name
    snapshot_path.write_bytes(source_bytes)

    staged_path = staging_dir / filename
    normalize_to_wav(snapshot_path, staged_path)

    duration_sec, rms_dbfs, peak_dbfs = measure_loudness(staged_path)
    if not math.isfinite(duration_sec) or duration_sec <= 0.0:
        raise NonPositiveDurationError(
            f"{src.name}: 正規化後 wav ({staged_path}) の長さが 0 以下または"
            f"非有限です (duration_sec={duration_sec!r})。使用不能なドナー"
            f"音声のため fail-closed で拒否します（台帳への記録・公開のいずれも"
            f"行いません。R21 P2 対応）"
        )

    # R23 P2 対応: 生値 (duration_sec) は正でも、台帳へ実際に書き込む丸め後の
    # 値 (round(duration_sec, 3)) が 0 になり得る（例: ffmpeg が 0.0005 秒未満の
    # フレームしか書き出さなかった場合）。丸め後 0.0 のまま台帳へ記録すると
    # `_validate_ledger_entry` 自身の duration_sec > 0 不変条件に違反した
    # エントリを公開してしまい、次回 append の `load_ledger()` が
    # `LedgerSchemaError` で fail する。0.5ms 未満の録音は素材として無意味な
    # ため、生値の検査と同じく台帳構築前に fail-closed 拒否する。
    rounded_duration_sec = round(duration_sec, 3)
    if not math.isfinite(rounded_duration_sec) or rounded_duration_sec <= 0.0:
        raise NonPositiveDurationError(
            f"{src.name}: 正規化後 wav ({staged_path}) の長さは生値では正"
            f"(duration_sec={duration_sec!r}) ですが、台帳へ書き込む丸め後の値"
            f"(round(duration_sec, 3)={rounded_duration_sec!r}) が 0 以下です。"
            f"0.5ms 未満の録音は素材として無意味なため fail-closed で拒否します"
            f"（台帳への記録・公開のいずれも行いません。R23 P2 対応）"
        )

    return LedgerEntry(
        card_id=card_id,
        source_filename=src.name,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        normalized_path=str(publish_dir / filename),
        sha256=sha256_of(staged_path),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_sec=rounded_duration_sec,
        sample_rate=TARGET_SAMPLE_RATE,
        rms_dbfs=round(rms_dbfs, 2) if rms_dbfs is not None else None,
        peak_dbfs=round(peak_dbfs, 2) if peak_dbfs is not None else None,
        alignment_status="not_started",
    )


def run(incoming_dir: Path, out_dir: Path, ledger_path: Path) -> List[LedgerEntry]:
    """受領ディレクトリを取り込み、`out_dir` と `ledger_path` を一括更新する。

    変換・測定は `out_dir` の兄弟に作る一時 staging ディレクトリで行い、
    全入力の処理が成功した場合のみ staging から `out_dir` へファイルを
    移動し、続けて台帳を保存する（バッチ全体が 1 フェーズで公開される）。
    途中で例外（`KeyboardInterrupt`/`SystemExit` を含む）が発生した場合は
    `finally` で staging を丸ごと破棄するだけで、`out_dir`・`ledger_path`
    は呼び出し前の状態のまま変化しない（部分バッチを残さない。R10 P2 対応）。

    実際の変換・測定（`process_one`）を始める前に `_check_ledger_path_collisions`
    で `--ledger` の衝突を preflight 検査し（R12 P1 対応）、続けて既存台帳を
    `load_ledger` で読み込む（schema 不一致は `LedgerSchemaError` で
    fail-closed 拒否し、変換・公開のいずれも開始しない。R13 P2 対応）。

    公開フェーズ（`out_dir` へのファイル移動 + `save_ledger`）に入る直前に
    既存台帳のバイト列スナップショットを取っておく（無ければ `None`）。
    公開フェーズは移動済みファイルを `moved` に記録しながら進める。移動
    そのものが失敗した場合・全ファイル移動後に台帳保存が失敗した場合の
    いずれも `except BaseException` で捕捉し、それまでに公開済みだった
    wav を staging へ移動し直し（`out_dir` を呼び出し前の状態へ巻き戻して）
    、台帳もスナップショットへ復元してから re-raise する。`save_ledger()`
    が完走した直後・関数が呼び出し元へ返る前に `KeyboardInterrupt`/
    `SystemExit` が届いた場合（新台帳が既に `ledger_path` へ replace 済み）
    も同じ `except BaseException` で捕捉され、台帳は復元される（R13 P2
    対応）。巻き戻し後は外側の `finally` が staging ごと削除するため、
    失敗したバッチの痕跡は `out_dir`/`ledger_path` のどちらにも残らない
    （`gate_synth.py` の staged swap + `BaseException` 巻き戻しと同型パターン。
    R12 P2 対応）。

    上記全体（`assign_normalized_filenames` の out_dir スキャンから公開・
    台帳保存・ロールバックまで）は `<ledger>.lock` への `fcntl.flock
    (LOCK_EX | LOCK_NB)` で直列化される（R21 P1 対応。モジュール docstring
    参照）。ロック取得はノンブロッキングで行い、既に別プロセスが保持して
    いる場合は待たずに `LedgerLockError` で即座に fail-closed 拒否する。

    さらに `<out_dir>/.intake.lock` への同種の `flock` も、ledger ロック
    取得に続けて固定順で取得する（R22 P1 対応。モジュール docstring 参照）。
    別 `--ledger` かつ同一 `--out-dir` の並行実行を直列化するためで、取得
    できない場合は待ち合わせず `OutDirLockError` で fail-closed 拒否する。
    """
    inputs = discover_inputs(incoming_dir)
    if not inputs:
        return []

    # R21 P1 対応: ロック取得は preflight の最初（`_check_ledger_path_
    # collisions` より前）に行い、`assign_normalized_filenames` の out_dir
    # スキャンから公開・台帳 save・ロールバックまでの全トランザクションを
    # ロック保持中に実行する。プロセス終了・例外時は OS が flock を自動
    # 解放するため、ここでの `finally` はファイルディスクリプタの後始末の
    # みで良い（ロックファイル自体は削除しない — モジュール docstring 参照）。
    lock_path = _ledger_lock_path(ledger_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LedgerLockError(
                f"別の intake プロセスが同一台帳 ({ledger_path}) を対象に実行"
                f"中です（ロック: {lock_path}）。fail-closed で拒否します"
                f"（並行 intake による台帳データ損失を防止。R21 P1 対応。"
                f"待ち合わせず再実行してください）"
            ) from exc

        # R22 P1 対応: out_dir（公開名前空間）も別ロックで直列化する。取得
        # 順序は ledger → out_dir の固定順（両ロックとも同じ順序でしか取得
        # しないためデッドロックは起きない）。ロックファイルを置くために
        # out_dir をここで先に確保する（`assign_normalized_filenames` の
        # out_dir スキャンより前であることが直列化の前提）。
        out_dir.mkdir(parents=True, exist_ok=True)
        out_dir_lock_path = _out_dir_lock_path(out_dir)
        if ledger_path.resolve() == out_dir_lock_path.resolve():
            raise LedgerPathCollisionError(
                f"--ledger ({ledger_path}) は out_dir ロックファイル "
                f"({out_dir_lock_path}) と衝突しています（fail-closed で拒否。"
                f"R22 P1 対応）"
            )
        out_dir_lock_file = open(out_dir_lock_path, "a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(out_dir_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise OutDirLockError(
                    f"別の intake プロセスが同一 out_dir ({out_dir}) を対象に"
                    f"実行中です（ロック: {out_dir_lock_path}）。fail-closed で"
                    f"拒否します（並行 intake による公開名前空間の競合を防止。"
                    f"R22 P1 対応。待ち合わせず再実行してください）"
                ) from exc

            filenames = assign_normalized_filenames(inputs, out_dir)

            staging_root = out_dir.parent
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(
                tempfile.mkdtemp(prefix=".intake-staging-", dir=str(staging_root))
            )
            try:
                _check_ledger_path_collisions(
                    ledger_path, inputs, filenames, out_dir, staging_dir, lock_path=lock_path
                )

                # R13 P2 対応: 台帳の読み込み（schema 検証込み）は実際の変換・測定
                # （process_one）より前の preflight として行う。壊れた/未知スキーマの
                # 台帳が存在する場合はここで LedgerSchemaError が送出され、変換・
                # 公開のいずれも開始しない。
                ledger = load_ledger(ledger_path)

                # R23 P2 対応: append 実行時（既存台帳にエントリがある場合）は、
                # 実際の変換（process_one）を始める前に、既存エントリ全件の
                # normalized_path の実在とバイト列 sha256 一致を検証する。
                # out_dir ロック保持中の検査のため、検査後のレースはロックが防ぐ
                # （`_check_existing_artifacts` docstring 参照）。
                _check_existing_artifacts(ledger, ledger_path, out_dir)

                entries = [
                    process_one(src, staging_dir, filenames[src], out_dir) for src in inputs
                ]

                # R17 P2 対応: staging → out_dir への一括公開より前の preflight として、
                # 今回バッチ各ファイルの source_sha256 を既存台帳・同一バッチ内の他
                # ファイルの両方と突き合わせ、重複があれば公開全体を fail-closed 拒否
                # する（部分公開はしない）。
                _check_duplicate_sources(entries, ledger)

                ledger.setdefault("entries", []).extend(asdict(e) for e in entries)

                # R13 P2 対応: 公開フェーズ開始前の台帳バイト列スナップショット
                # （無ければ None = 「無し」の印）。BaseException 巻き戻し時に
                # WAV と合わせてこれへ復元する。
                previous_ledger_bytes: Optional[bytes] = (
                    ledger_path.read_bytes() if ledger_path.exists() else None
                )

                moved: List[tuple[Path, Path]] = []  # (final_path, staged_path) の公開済み一覧
                try:
                    for src in inputs:
                        staged_path = staging_dir / filenames[src]
                        final_path = out_dir / filenames[src]
                        # R16 P1 対応: shutil.move 直前に最終防御として検証する
                        # （assign_normalized_filenames の予約後に out_dir の中身が
                        # 変わった場合・予約ロジック自体の見落としに対する備え）。
                        _check_publish_path(final_path, out_dir)
                        shutil.move(str(staged_path), str(final_path))
                        moved.append((final_path, staged_path))

                    save_ledger(ledger_path, ledger)
                except BaseException:
                    # 巻き戻し: ここまでに out_dir へ公開済みの wav を staging へ戻す
                    # （逆順で戻すのは他ファイルとの依存関係はないが、失敗直近のものから
                    # 先に処理するほうが診断しやすいための慣習）。staging は外側の
                    # finally でまるごと削除されるため、戻した分は最終的に消える。
                    for final_path, staged_path in reversed(moved):
                        if final_path.exists():
                            shutil.move(str(final_path), str(staged_path))
                    # 台帳もスナップショット時点へ復元する（R13 P2 対応。
                    # save_ledger() 成功直後の中断で新台帳が既に replace 済みの
                    # ケースを含む）。
                    _restore_ledger(ledger_path, previous_ledger_bytes)
                    raise
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)

            return entries
        finally:
            # R22 P1 対応: `flock` はプロセス終了・例外時にも OS が自動解放
            # するため、ここでの明示 unlock はベルト。ロックファイル自体は
            # `unlink` しない（削除すると unlink 直後・別プロセスの open+
            # flock 直後という窓で二重ロックが成立し得るため。`<ledger>.lock`
            # と同じ残置ポリシー）。
            fcntl.flock(out_dir_lock_file, fcntl.LOCK_UN)
            out_dir_lock_file.close()
    finally:
        # R21 P1 対応: `flock` はプロセス終了・例外時にも OS が自動解放する
        # ため、ここでの明示 unlock はベルト（早期解放によるロック保持時間の
        # 最小化）。ロックファイル自体は `unlink` しない（削除すると、
        # あるプロセスの unlink 直後・別プロセスが同じ旧パスを開いて flock
        # した直後という窓で二重ロックが成立し得るため。空ファイルとして
        # 残置し続けることが `_check_ledger_path_collisions` の `lock_path`
        # 引数と整合する設計）。
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--incoming-dir", type=Path, required=True, help="受領ファイルを置いたディレクトリ"
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="正規化後 24kHz mono wav の出力先"
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path(__file__).resolve().parent / "user_donor_ledger.json",
        help="user_donor_ledger.json の出力先（既存なら追記）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.incoming_dir.is_dir():
        print(f"error: --incoming-dir が存在しません: {args.incoming_dir}", file=sys.stderr)
        return 1

    try:
        entries = run(args.incoming_dir, args.out_dir, args.ledger)
    except FfmpegNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for entry in entries:
        print(f"{entry.source_filename} -> card_id={entry.card_id} sha256={entry.sha256[:12]}...")
    print(f"{len(entries)} 件を {args.ledger} へ追記しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
