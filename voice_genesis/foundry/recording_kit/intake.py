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
"""
from __future__ import annotations

import argparse
import hashlib
import json
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


class LedgerSchemaError(RuntimeError):
    """既存台帳の `schema` が `LEDGER_SCHEMA` と一致しない、または `entries`
    がリストでない場合に送出する（R13 P2 対応）。旧実装は `entries` さえ
    存在すれば `schema` の値（誤植・別バージョン）を無視して現行版の
    エントリを追記・公開しており、この実装が解釈できない/意図しない
    フォーマットの台帳を静かに書き換えてしまっていた。`load_ledger` で
    fail-closed 拒否し、処理・公開のいずれも開始させない。
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


def load_ledger(ledger_path: Path) -> dict:
    """既存台帳を読み込む。存在しなければ新規スキーマの空台帳を返す。

    既存台帳がある場合は `schema == LEDGER_SCHEMA` の完全一致と `entries`
    がリストであることを検証する（R13 P2 対応）。どちらか不一致なら
    `LedgerSchemaError` を送出し fail-closed で拒否する（未知・旧バージョン
    ・破損した台帳への暗黙の追記・公開を防ぐ）。
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
    return ledger


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


def _check_ledger_path_collisions(
    ledger_path: Path,
    inputs: List[Path],
    filenames: Dict[Path, str],
    out_dir: Path,
    staging_dir: Path,
) -> None:
    """`--ledger` の resolve 済みパスが以下のいずれとも衝突しないことを検査する
    （R12 P1 対応。convert_pjs.py/gate_synth.py の衝突拒否ファミリーと同じ
    流儀 — fail-closed・resolve 済みパス比較で symlink 迂回を封じる）。

    - incoming の元音源ファイル（衝突すると `save_ledger()` がドナー原本を
      JSON で上書きし、原本を破壊する）
    - 今回バッチの導出出力（staging 内の一時パス・`out_dir` 内の最終正規化
      wav。衝突すると `save_ledger()` が台帳記録済みの音声 hash の実体を
      JSON で上書きする）
    - `out_dir` に既に公開済みの他バッチのファイル（同じ理由で上書き事故になる）
    - staging ディレクトリ自体（`--ledger` がその内部を指していると、
      `run()` の `finally` が staging を丸ごと `rmtree` する際に、直前に
      保存したはずの台帳ごと消え去る）

    `run()` 内で staging_dir 作成直後・実際の変換/移動/台帳保存より前に
    呼ぶこと（`--incoming-dir` を読むだけの preflight で、音声処理は一切
    発生しない）。
    """
    resolved_ledger = ledger_path.resolve()

    for src in inputs:
        if resolved_ledger == src.resolve():
            raise LedgerPathCollisionError(
                f"--ledger ({ledger_path}) は incoming の元音源ファイル ({src}) "
                f"と衝突しています（fail-closed で拒否。ドナー原本の破壊を防止）"
            )

    for src in inputs:
        filename = filenames[src]
        for derived_dir in (staging_dir, out_dir):
            candidate = derived_dir / filename
            if resolved_ledger == candidate.resolve():
                raise LedgerPathCollisionError(
                    f"--ledger ({ledger_path}) は導出出力 ({candidate}) と衝突"
                    f"しています（fail-closed で拒否。正規化 wav を JSON で"
                    f"上書きする事故を防止）"
                )

    if out_dir.exists():
        for existing in out_dir.iterdir():
            if existing.is_file() and resolved_ledger == existing.resolve():
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

    return LedgerEntry(
        card_id=card_id,
        source_filename=src.name,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        normalized_path=str(publish_dir / filename),
        sha256=sha256_of(staged_path),
        received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_sec=round(duration_sec, 3),
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
    """
    inputs = discover_inputs(incoming_dir)
    if not inputs:
        return []

    filenames = assign_normalized_filenames(inputs, out_dir)

    staging_root = out_dir.parent
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".intake-staging-", dir=str(staging_root)))
    try:
        _check_ledger_path_collisions(ledger_path, inputs, filenames, out_dir, staging_dir)

        # R13 P2 対応: 台帳の読み込み（schema 検証込み）は実際の変換・測定
        # （process_one）より前の preflight として行う。壊れた/未知スキーマの
        # 台帳が存在する場合はここで LedgerSchemaError が送出され、変換・
        # 公開のいずれも開始しない。
        ledger = load_ledger(ledger_path)

        entries = [
            process_one(src, staging_dir, filenames[src], out_dir) for src in inputs
        ]

        ledger.setdefault("entries", []).extend(asdict(e) for e in entries)

        # R13 P2 対応: 公開フェーズ開始前の台帳バイト列スナップショット
        # （無ければ None = 「無し」の印）。BaseException 巻き戻し時に
        # WAV と合わせてこれへ復元する。
        previous_ledger_bytes: Optional[bytes] = (
            ledger_path.read_bytes() if ledger_path.exists() else None
        )

        out_dir.mkdir(parents=True, exist_ok=True)
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
