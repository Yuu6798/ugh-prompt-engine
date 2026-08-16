"""D1: PJS (Phoneme-balanced Japanese Singing-voice corpus) -> DiffSinger 変換 CLI。

`UtaUtaUtau/nnsvs-db-converter`（外部 clone、`--converter-dir` で指定。pin は
`README.md` 参照）の `db_converter.py` を呼び出し、PJS の song wav を
DiffSinger acoustic 学習形式（`transcriptions.csv`: ph_seq/ph_dur/ph_num/
note_seq/note_dur）へ変換する。変換規則・障害の詳細は `s1a_conversion_record.md`
に逐語記録済みのため、本スクリプトは手順の再現のみを担う。

PJS は `pjsNNN.lab` に対し `pjsNNN_song.wav`/`pjsNNN_speech.wav` という命名の
ため、`db_converter.py` が期待する `pjsNNN.wav`（`.lab` と同名）と食い違い、
そのままでは `FileNotFoundError` になる（`glob('**/{lab.stem}.wav')` が 0 件
ヒット）。本スクリプトは PJS 本体には一切触れず、song wav のみをシンボリック
リンクで `pjsNNN.wav` へリネームしたステージングディレクトリを作ってから
変換器を呼ぶ（発話系 `_speech.wav` はステージングに含めない）。

symlink 群 + 変換出力（`diffsinger_db/`）は毎回 fresh な
`<staging-dir>.build-<pid>` に完全構築してから、成功時のみ `--staging-dir`
と原子的に swap する（review #263 R3 P1）。これにより途中失敗時の新旧世代
混在を防ぐと同時に、`pjs_root` から消えた曲の symlink が再実行後も
`--staging-dir` に残り続ける問題も解消する（build dir は毎回空から始まる
ため）。

`--lang-def` が `--staging-dir` 配下を明示的に指す場合、書き込み先を
`build_dir` 配下の同じ相対位置へ再マップする（review #263 R4 P2）。fresh
build では `--staging-dir` がまだ存在せず書き込みが失敗し、旧 `--staging-dir`
が残っている場合は build 外＝旧世代側に書かれて `_swap_into_place` の
`.old` 退避で消えてしまうため（`--staging-dir` 外を指す場合は従来通り）。

review #263 R5 で 2 件追加修正: (1) `.lab`/`_song.wav` の一方が欠けた song
ディレクトリを黙って skip せず、欠落曲名を全収集してから swap 前に
fail-closed 拒否する（契約=PJS 全 100 曲。完全素材では挙動不変）。
(2) `--staging-dir` が `--pjs-root`/`--converter-dir` と衝突・内包関係に
ある場合、symlink 構築を始める前に fail-closed で拒否する
（`build_dataset.py` R4 の `OutputCollisionError` と同型）。

review #263 R7 で 3 件追加修正: (1) 衝突ガードの包含判定を双方向化し、
保護 root が出力配下にある場合（例: `--staging-dir=/tmp/work`,
`--pjs-root=/tmp/work/pjs`）も拒否する。(2) `--staging-dir` 外を指す
`--lang-def` もガード対象に含め、書き込み自体も一時ファイル→
`os.replace()` の staged 方式にする。(3) `_swap_into_place` の 2 段
rename を `try/except BaseException` で保護し、新世代 rename 失敗時は
退避済み旧世代を元パスへ復元してから再送出する。

review #263 R9 で 2 件追加修正: (1) `stage_song_wavs` が「存在する
ディレクトリのみ検査」していたため、抽出が `pjsNNN` ディレクトリ自体を
丸ごと落とした場合（`.lab`/`_song.wav` の片方欠けではなく曲ディレクトリ
そのものが無い）を検出できなかった（実測: pjs001-pjs099 のみで実行すると
`(99, [])` が返り縮小コーパスが成功報告される）。期待 ID 集合
（`PJS_EXPECTED_IDS` = pjs001-pjs100）に対して走査する方式へ変更し、
存在しないディレクトリも `missing_pairs` へ含める（`convert_pjs.py` と
`convert_ritsu.py` の同型修正）。(2) 衝突ガードが `--staging-dir` 本体
のみを検査し、`_swap_into_place` が実際に削除・rename する派生パス
（`<staging-dir>.old`・`<staging-dir>.build-<pid>`）を対象外にしていた。
例えば `--staging-dir=/tmp/published`, `--pjs-root=/tmp/published.old`
は事前チェックを素通りしたのち、公開時に `old_dir` の `rmtree` が
`pjs_root`（保護入力）そのものを削除する。派生パスもガード対象に含める。

review #263 R10 で 1 件追加修正: `--lang-def` が `--staging-dir` 外の既存
ファイルを指す場合、`write_lang_def` は swap の外側（`_swap_into_place` に
よる新旧世代の原子的差し替えの対象外）で直接上書きする。この上書き後、
converter 実行〜swap 完了のいずれかが失敗すると、外部 lang-def だけが新
内容のまま取り残され、コーパスと外部 lang-def の世代が食い違う。上書き前に
元内容をメモリへバックアップし、以降のいずれかの失敗で元へ復元する
（成功時のみ確定）。

review #263 R11 で 1 件追加修正: 上記 R10 のバックアップは `--lang-def` が
**既存ファイル**を指す場合のみ機能し、**新規パス**（元ファイルなし）を指す
場合はバックアップが `None` のまま `_restore_lang_def_backup` が何もせず、
`write_lang_def` が新規作成したファイルが失敗経路でも残留してしまっていた。
書き込み前に対象パスの存在有無を記録し、新規パスだった場合は失敗時に
作成物を `unlink` して「実行前は存在しなかった」状態へ戻す（既存ファイル
ケースの復元動作は無変更）。

review #263 R14 P1: `stage_song_wavs` は `.lab`/`_song.wav` の存在判定に
`exists()` を使うが、`exists()` は symlink 解決先までは検査しない。期待
`.lab`/`_song.wav` 自体が `--pjs-root` 外を指す symlink（逃避）の場合、
`exists()` は素通りし、本スクリプトが張るステージング symlink もその逃避を
そのまま引き継ぐ。結果として `db_converter.py` が pjs-root 外の任意ラベル/
音声を黙って学習に取り込みつつ、100 曲ペアのゲートは通過してしまう。
`.lab`/`_song.wav` を `resolve()` し、pjs-root（resolve 済み）配下に収まら
ないものを全収集し、symlink を張る前に公開前 fail-closed で拒否する
（`missing_pairs` と同じ「全収集してから公開前に止める」設計）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# nnsvs-db-converter 同梱の `lang.sample.json` と同一内容
# （`s1a_conversion_record.md` §2: 日本語ラベルのモーラ分割規則としてそのまま
# 妥当だったため採用。本スクリプトはこの内容を明示的に書き出すことで、外部
# clone 内のサンプルファイルへの暗黙依存を避ける）。
DEFAULT_LANG_DEF = {
    "vowels": ["a", "i", "u", "e", "o", "N", "A", "I", "U", "E", "O"],
    "liquids": {"w": ["k", "g"], "y": True},
}

# PJS_corpus_ver1.1 の契約 = pjs001-pjs100 の 100 曲（review #263 R9 P2）。
# `stage_song_wavs` はこの期待 ID 集合に対して走査することで、抽出が
# `pjsNNN` ディレクトリ自体を丸ごと落とした場合も検出できる。
PJS_EXPECTED_IDS: Tuple[str, ...] = tuple(f"pjs{i:03d}" for i in range(1, 101))

# fix (2026-08-16, s1_poison_scan 捜査結果を受けての導入。review #264 R1 P1
# で修復方式を是正): 5/287 PJS セグメント（pjs004_seg003 / pjs016_seg000 /
# pjs029_seg002 / pjs030_seg001 / pjs031_seg001）で、`db_converter.py` が
# 書き出す `transcriptions.csv` の申告 ph_dur 合計が実 `.wav` 長を
# 1.10〜1.89 倍超過する構造不良が判明した。
#
# 原因（本 clone の `process_lab_wav_pair()` を精読して特定): 同関数は
# `.lab` の時刻をそのまま `segment.to_lengths_string()` 経由で ph_dur として
# 書き出す一方、実際の wav は `x[s:e]`（`s`/`e` は `.lab` 由来のサンプル位置）
# という numpy スライスで書き出す。`.lab` が想定する終端が実音声（PJS 配布
# `_song.wav`）の実長より後ろにある場合、numpy スライスは例外を出さず黙って
# 実音声の終端でクランプするため、ph_dur は「本来あるべきだった長さ」を、
# wav は「実際に存在した音声」を報告し、両者が食い違う。
#
# [P1 修正] (review #264 R1) 当初実装は全 ph_dur を wav_dur/total で比例
# 縮小し合計を実 wav 長へ正規化していたが、これは原因（末尾クランプ）とは
# 無関係な EOF（実音声終端）以前の正しい音素境界まで一様にズラしてしまう
# 副作用を持つ。原因の構造上、乖離は常に「実 wav 終端より後ろ」の申告時刻に
# 集中しており、終端以前の申告時刻はそもそも正しい。したがって EOF を跨ぐ
# 末尾側の音素だけを切り詰め/ゼロ化し、それ以前の境界は完全無変更のまま
# 返す方式（`_truncate_ph_dur_tail_to_wav_duration`）へ変更する。
#
# 実測（5件全数、末尾からの累積で EOF 跨ぎ位置を特定): pjs004/pjs029/
# pjs030/pjs031 は末尾 1 音素（全件 SP）がゼロ化されるのみで、EOF を跨ぐ
# 直前音素への浸食は 14〜34ms（フレーム量子化 ≈timestep 11.61ms 相当の
# 丸め誤差域、pjs031 のみ EOF 跨ぎが最終音素自体でゼロ化対象なし）。
# pjs016_seg000 のみ末尾から 2 番目の母音（"u"）が 226ms（+5.4%）短縮され
# 末尾 SP がゼロ化される（旧実装のコメントが「末尾 SP だけの補正では
# 収まらない」としていたのはこの 1 音素浸食を指しており、比例縮小の根拠には
# ならない — 前方の音素境界は本方式でも一切ズレない）。
#
# 申告合計が実 wav 長を下回る場合（undershoot）は、下記 R5 の通り自動修復
# しない（fail-closed で拒否する）。
#
# 閾値判定自体は `build_dataset.py` `check_ph_dur_duration` と同一（相対5%
# or 絶対0.1sの大きい方。両ファイルとも既存の record スクリプト群の慣例に
# 倣い、共有モジュール新設ではなく値を直接コピペする）。閾値内の行は完全
# 無変更のまま返す（282 件の既知良好セグメントは byte 単位で不変）。
#
# [P1 修正] (review #264 R2) 上記 R1 修正は EOF より完全に後ろの音素を
# `ph_dur=0.0` のまま `ph_seq`/`ph_num`/`note_seq`/`note_dur` に残す設計
# だったが、下流 `build_dataset.py` `validate_speaker` は `d <= 0` の
# ph_dur を持つ行を無条件に reject するため、修復 5 セグメントが
# `convert_pjs.py` 単体では成功しても `build_dataset.py` を通す時点で
# **必ず** validate 失敗する統合バグだった（R1 の単体テストは
# `normalize_ph_dur_to_wav_duration` の戻り値のみを検査し、
# `validate_speaker` まで通していなかったため検出できなかった）。
# `ph_dur=0.0` になった音素は「実質削除」ではなく実際に整列フィールド
# （`ph_seq`/`ph_dur`/`ph_num`/該当ノートの `note_seq`/`note_dur`）から
# 削除する方式へ変更する（`_drop_dead_phonemes` 参照）。EOF 以前の音素の
# 境界・値は本修正でも一切変更しない。
#
# [P2 修正] (review #264 R3) 上記 R2 修正は、ノート自体は音素を1個以上
# 残して生存するが末尾側の一部音素だけが truncate/drop されるケース
# （EOF がノートの途中を切る場合）で、生存ノートの `note_dur` を旧値の
# まま残していた。これにより ph_dur 合計/wav 長と note_dur 合計が食い違う
# （新設テストの 1.5s vs 3.5s ケース）。生存ノートの `note_dur` を、その
# ノートに属する生存音素の新 ph_dur 合計へ再計算するよう修正する
# （`_drop_dead_phonemes` 参照）。あわせて `build_dataset.py` の readback
# 検証に ph_dur合計↔note_dur合計のクロスフィールド不変量検査
# （`check_note_dur_consistency`）を追加する。
#
# [P2 修正] (review #264 R5) 上記 R1〜R3 は一貫して「申告合計が実 wav 長を
# 上回る場合（overshoot）は対称に、下回る場合（undershoot）も末尾音素へ
# 不足分を加算して合計を一致させる」という対称設計だったが、これは誤りだった
# ——冒頭の EOF クランプ診断（`db_converter.py` の numpy スライスが実音声
# 終端でクランプすることによる overshoot）は overshoot のみを説明しており、
# undershoot（例: 申告末尾に無音区間があるが実 wav にその無音が収録されて
# いない等）はそもそも原因未特定で、現行 PJS 素材でも未観測（overshoot 5件
# のみが実測されている）。原因不明の不足分を無条件に最後の音素へ加算すると、
# 実際には存在しない/性質不明の音声区間を特定の音素の持続時間として捏造して
# 教師信号に混入させることになり、`_drop_dead_phonemes` の note_dur 再計算
# まで通過するため下流の整合性検査も素通りしてしまう。したがって undershoot
# は自動修復せず、許容誤差（相対5%/絶対0.1s）を超えるものは違反として全収集
# した上で公開前に fail-closed で拒否する（許容誤差内の微差は従来通り無変更
# のまま許容——拒否対象は許容超過の undershoot のみ）。overshoot の末尾切り
# 詰め挙動（R1〜R3）自体は変更しない。
DURATION_REL_TOLERANCE = 0.05
DURATION_ABS_TOLERANCE_SEC = 0.1


def _is_safe_wav_name(name: str, wav_dir: Path, resolved_wav_dir: Path) -> bool:
    """`name` が `wav_dir` 配下の `<name>.wav` を安全に指すかを判定する
    （字句検査 + resolve 封じ込め検査）。

    fix (2026-08-16, review #264 R7): `normalize_ph_dur_to_wav_duration` は
    `db_converter.py`（外部変換器）が出力した `row["name"]` を無検査で
    `wav_dir / f"{name}.wav"` へ連結し `_wav_duration_seconds`（=
    `wave.open`）へ渡していた。`row["name"]` は外部生成物で信頼できない
    入力のため、`../../outside` のような name や wav_dir 外へ逃げる symlink
    経由で wav_dir 外の任意ファイル（特殊ファイルを含む）を duration
    チェック目的で読み取れてしまう穴があった（`build_dataset.py`
    `check_ph_dur_duration` で review #264 R4 P2 に修正済みの同型の穴が、
    別ファイルの別呼び出し箇所で再発したもの）。

    判定ロジックは `build_dataset._is_safe_wav_name` と同一（絶対パス・
    `..` セグメント・パス区切り文字を拒否する字句検査 + resolve 後の実
    パスが `wav_dir` 配下に収まることを確認する多層防御）だが、record
    スクリプト群の既存慣例（本ファイル冒頭の `DURATION_REL_TOLERANCE` 等、
    モジュール冒頭コメント参照）に倣い、共有モジュール新設ではなくロジック
    をこちらへコピペ実装する。両実装の挙動同値性は
    `tests/test_s1_dataprep_ph_dur_duration.py` の
    `test_is_safe_wav_name_matches_build_dataset_behavior` で担保する。
    """
    if not name or os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return False
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    candidate = wav_dir / f"{name}.wav"
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_wav_dir and resolved_wav_dir not in resolved_candidate.parents:
        return False
    return True


def _wav_duration_seconds(path: Path) -> Optional[float]:
    """`path` の WAV 実長を秒で返す。読み取れない場合は `None`
    （呼び出し側はこの ph_dur 正規化をスキップする）。"""
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            if rate <= 0:
                return None
            return w.getnframes() / rate
    except (wave.Error, OSError, EOFError):
        return None


def _truncate_ph_dur_tail_to_wav_duration(
    ph_dur: List[float], wav_dur: float
) -> Tuple[List[float], int]:
    """`ph_dur`（先頭からの各音素長 秒、開始時刻0の累積列）を、末尾側だけを
    調整して合計が実 wav 長 `wav_dur` に一致するようにした新しいリストを
    返す（[P1 修正] review #264 R1）。呼び出し元は申告合計が `wav_dur` を
    超過する行（overshoot）に対してのみこの関数を呼ぶ契約とする（undershoot
    行は [P2 修正] review #264 R5 により本関数を呼ばず、呼び出し元
    `normalize_ph_dur_to_wav_duration` が違反として収集し fail-closed で
    拒否する — 詳細はモジュール冒頭コメント参照）。

    先頭からの累積時刻が `wav_dur` に達するまでの音素は完全無変更で返す
    （＝前方の音素境界を一切ズラさない）。累積時刻が `wav_dur` を跨ぐ音素
    （EOF 跨ぎ）は、そこでちょうど `wav_dur` に達するよう長さを切り詰める。
    それより後ろの音素（EOF より完全に後ろ）は 0 にする。この関数自体は
    `ph_seq`/`ph_num`/`note_seq`/`note_dur` との位置対応を保つため要素を
    削除せず、常に入力と同じ長さのリストを返す（0.0 になった音素を整列
    フィールドから実際に削除するのは呼び出し元 `_drop_dead_phonemes` の
    役割 — review #264 R2）。

    2 個目の戻り値は EOF 跨ぎ以降で長さが変化した音素の個数（ログ用。
    跨ぎ音素自体 + ゼロ化された後続音素の合計）。
    """
    result = list(ph_dur)
    cum = 0.0
    crossed_at: Optional[int] = None
    for i, d in enumerate(ph_dur):
        if crossed_at is not None:
            result[i] = 0.0
            continue
        if cum + d > wav_dur:
            result[i] = wav_dur - cum
            crossed_at = i
            cum = wav_dur
        else:
            cum += d
    n_touched = len(ph_dur) - crossed_at if crossed_at is not None else 0
    return result, n_touched


def _drop_dead_phonemes(row: Dict[str, str], ph_dur: List[float]) -> Dict[str, str]:
    """`ph_dur`（`_truncate_ph_dur_tail_to_wav_duration` 適用後、長さ 0 の
    音素を含み得る）に基づき、長さ 0 になった音素を `ph_seq`/`ph_dur` および
    対応するノート単位フィールド（`ph_num` があれば、それが指すノートの
    `note_seq`/`note_dur` も）から実際に削除した新しい行を返す。

    [P1 修正] (review #264 R2) `ph_dur=0.0` の音素をゼロ長のまま整列
    フィールドに残すと、下流 `build_dataset.py` `validate_speaker` の
    `d <= 0` チェックが無条件に reject するため、修復セグメントが
    `build_dataset.py` を通す時点で必ず fail する統合バグになっていた。
    音素自体を要素ごと削除することでこれを解消する（EOF 以前の音素は
    `ph_dur` に 0.0 が含まれない限り一切削除・変更されない）。

    `ph_num`（ノートごとの音素数、`sum(ph_num) == len(ph_seq)` の対応関係）
    が存在する場合は、各ノートの残存音素数を数え直し、音素が 1 個も残らな
    かったノートは `ph_num`（および同じインデックス対応の `note_seq`/
    `note_dur`、存在すれば）から丸ごと除去する。`ph_num`/`note_seq`/
    `note_dur` が存在しない・空の行（例: PJS の生 `transcriptions.csv` には
    無いテスト用簡易行）は対象外としてそのままにする。

    [P2 修正] (review #264 R3) EOF がノートの途中を切る場合（ノート自体は
    音素を 1 個以上残して生存するが、末尾側の一部音素だけが truncate/drop
    される場合）、生存ノートの `note_dur` を旧値のまま残すと、そのノートに
    属する生存音素の ph_dur 合計と食い違ったまま（例: ph_dur 合計/wav 長
    1.5s に対し note_dur 合計が旧値 3.5s のまま）下流 DiffSinger 学習へ渡って
    しまう。生存ノートの `note_dur` は「そのノートに属する生存音素（keep）
    の新 ph_dur 合計」へ再計算する（`note_seq` は音高/歌詞情報でありタイミ
    ングと無関係のため、従来どおりフィルタのみで再計算はしない）。

    この再計算は「1個以上の音素が丸ごと drop されたノート」だけでなく、
    「1個も drop されていないが末尾の音素が truncate だけされたノート」
    （＝旧実装が `all(keep)` で早期 return し、`ph_dur` のみ更新して
    `note_seq`/`note_dur`/`ph_num` に一切触れなかったケース）にも及ぶ
    必要がある——実 PJS 素材の `pjs031_seg001`（EOF 跨ぎ音素が末尾音素
    そのものでゼロ化対象なし）で、旧実装のまま note_dur が更新されず
    `build_dataset.check_note_dur_consistency` に実測で reject されることを
    確認した。そのため本実装は `all(keep)` による早期 return を廃し、
    音素の要素削除が不要な場合（`keep` が全て True）でも
    `ph_num`/`note_seq`/`note_dur` の再計算パスを常に通す（削除対象が無い
    場合、`surviving_note_indices` は全ノートを含む恒等写像になり、
    `ph_num`/`note_seq` の値は結果的に無変更のまま返る）。
    """
    keep = [d > 0.0 for d in ph_dur]
    new_row = dict(row)

    ph_seq = row["ph_seq"].split()
    if len(ph_seq) != len(ph_dur):
        # 対応が取れない行は安全側（削除せず ph_dur のみ更新）に倒す。
        # 正常な `transcriptions.csv` では `ph_seq`/`ph_dur` は常に同長
        # （`validate_speaker` が別途 enforce する）ため通常到達しない。
        new_row["ph_dur"] = " ".join(str(round(d, 12)) for d in ph_dur)
        return new_row
    new_row["ph_seq"] = " ".join(p for p, k in zip(ph_seq, keep) if k)
    new_row["ph_dur"] = " ".join(str(round(d, 12)) for d, k in zip(ph_dur, keep) if k)

    ph_num_raw = row.get("ph_num")
    if not ph_num_raw:
        return new_row
    ph_num = [int(x) for x in ph_num_raw.split()]
    if sum(ph_num) != len(ph_dur):
        return new_row  # 対応が取れない場合は ph_num 以降には触れない

    # 各音素がどのノート（ph_num のインデックス）に属するかを展開する。
    note_of_phoneme: List[int] = []
    for note_idx, count in enumerate(ph_num):
        note_of_phoneme.extend([note_idx] * count)
    surviving_counts: Dict[int, int] = {}
    for note_idx, k in zip(note_of_phoneme, keep):
        if k:
            surviving_counts[note_idx] = surviving_counts.get(note_idx, 0) + 1
    surviving_note_indices = [i for i in range(len(ph_num)) if surviving_counts.get(i, 0) > 0]
    new_row["ph_num"] = " ".join(str(surviving_counts[i]) for i in surviving_note_indices)

    for note_field in ("note_seq", "note_dur"):
        values_raw = row.get(note_field)
        if not values_raw:
            continue
        values = values_raw.split()
        if len(values) != len(ph_num):
            continue  # 対応が取れない場合はそのノートフィールドには触れない
        if note_field == "note_dur":
            # [P2 修正] (review #264 R3) 生存ノートの note_dur を、その
            # ノートに属する生存音素の新 ph_dur 合計へ再計算する（ph_dur
            # 合計との不変量を常に保つ。詳細は関数 docstring 参照）。
            new_row[note_field] = " ".join(
                str(round(
                    sum(
                        d for note_idx, d, k in zip(note_of_phoneme, ph_dur, keep)
                        if note_idx == i and k
                    ),
                    12,
                ))
                for i in surviving_note_indices
            )
        else:
            new_row[note_field] = " ".join(values[i] for i in surviving_note_indices)

    return new_row


def normalize_ph_dur_to_wav_duration(
    rows: List[Dict[str, str]], wav_dir: Path
) -> Tuple[List[Dict[str, str]], List[str], List[str], List[str], List[str]]:
    """各行の ph_dur 合計が実 wav 長と許容誤差（相対5% or 絶対0.1sの大きい方）
    を超えて乖離している場合の是正/検出を行う。

    許容超過が **overshoot**（申告合計 > 実 wav 長）の場合のみ、末尾側を
    切り詰めて合計を実 wav 長へ正規化した新しい行を返す
    （`_truncate_ph_dur_tail_to_wav_duration` 参照。前方の音素境界は一切
    変更しない — [P1 修正] review #264 R1）。切り詰めの結果 `ph_dur=0.0`
    になった音素は `ph_seq`/`ph_num`/`note_seq`/`note_dur` から実際に削除
    する（`_drop_dead_phonemes` 参照 — [P1 修正] review #264 R2。ゼロ長の
    まま残すと下流 `validate_speaker` の `d <= 0` チェックで必ず reject
    されるため）。

    許容超過が **undershoot**（申告合計 < 実 wav 長）の場合は自動修復
    **しない**（[P2 修正] review #264 R5）。モジュール冒頭コメントの EOF
    クランプ診断は overshoot のみを説明しており、undershoot は原因未特定
    かつ現行 PJS 素材でも未観測のため、原因不明の不足分を音素の持続時間へ
    捏造して混入させることを避け、行を無変更のまま返しつつ 3 個目の戻り値
    `undershoot_violations` へ人間可読の説明とともに収集する（呼び出し元が
    公開前に fail-closed で拒否する）。

    fix (2026-08-16, review #264 R7): `row["name"]` は外部変換器
    `db_converter.py` の出力（信頼できない入力）であり、`_wav_duration_seconds`
    で WAV を open する前に安全 name 判定（`_is_safe_wav_name`。絶対パス・
    `..` トラバーサル・wav_dir 外へ逃げる symlink 経由を拒否）を通す。安全
    でない name は open せず、行は無変更のまま返しつつ 4 個目の戻り値
    `unsafe_name_violations` へ人間可読の説明とともに全件収集する
    （undershoot_violations と同じ「全収集してから呼び出し側が fail-closed
    で判定する」設計）。

    fix (2026-08-16, review #264 R9 P2, convert_pjs.py:449): `db_converter.py`
    が成功終了していても、CSV 行が指す WAV が欠落・破損・ゼロ長で
    `_wav_duration_seconds()` が `None`/非正を返す場合がある。従来はこの
    ケースを「検証不能 = skip」として行を無変更のまま黙って残していたが、
    後続の coverage ゲートは CSV から抽出した曲 ID しか見ておらず、
    `_swap_into_place()` がこの不完全な行を含むデータセットをそのまま公開
    してしまっていた（変換器は成功を報告するため、この不整合は気づかれずに
    通過し得る）。検証不能を「無視してよい」とはみなさず、5 個目の戻り値
    `missing_wav_violations` へ人間可読の説明とともに全件収集し、呼び出し元
    が公開前に fail-closed で拒否できるようにする（unsafe_name_violations/
    undershoot_violations と同型の「全収集してから呼び出し側が判定する」
    設計）。

    乖離が許容内の行は同一オブジェクトのまま（内容も無変更で）返す。
    2 個目の戻り値は overshoot 正規化を適用した行の人間可読ログ（適用 0 件
    なら空リスト。呼び出し側はこれを CSV 再公開の要否判定に使う）。
    """
    fixed_rows: List[Dict[str, str]] = []
    fix_log: List[str] = []
    undershoot_violations: List[str] = []
    unsafe_name_violations: List[str] = []
    missing_wav_violations: List[str] = []
    resolved_wav_dir = wav_dir.resolve()
    for row in rows:
        try:
            ph_dur = [float(x) for x in row["ph_dur"].split()]
        except ValueError:
            fixed_rows.append(row)
            continue
        if not ph_dur or not all(math.isfinite(d) for d in ph_dur):
            fixed_rows.append(row)
            continue
        name = row["name"]
        if not _is_safe_wav_name(name, wav_dir, resolved_wav_dir):
            # fix (2026-08-16, review #264 R7): 安全でない name は
            # `_wav_duration_seconds`（= wave.open）へ渡さず、公開前
            # fail-closed の判定材料として収集するのみに留める。
            unsafe_name_violations.append(
                f"{name}: unsafe wav name (absolute path / '..' segment / path "
                "separator rejected, or resolved path escapes wav_dir — "
                "possibly via symlink); not opened, fail-closed"
            )
            fixed_rows.append(row)
            continue
        wav_dur = _wav_duration_seconds(wav_dir / f"{name}.wav")
        if wav_dur is None or wav_dur <= 0:
            # fix (2026-08-16, review #264 R9 P2): 検証不能（WAV 欠落/破損/
            # ゼロ長）を skip せず、公開前 fail-closed の判定材料として収集
            # する（モジュール docstring・呼び出し元 fail-closed 判定を参照）。
            missing_wav_violations.append(
                f"{name}: wav missing, unreadable, or non-positive duration "
                f"({wav_dir / f'{name}.wav'} — cannot validate ph_dur against "
                "actual wav duration; not opened/verified, fail-closed)"
            )
            fixed_rows.append(row)
            continue
        total = math.fsum(ph_dur)
        tol = max(wav_dur * DURATION_REL_TOLERANCE, DURATION_ABS_TOLERANCE_SEC)
        diff = abs(total - wav_dur)
        if diff <= tol:
            fixed_rows.append(row)
            continue
        if total < wav_dur:
            # [P2 修正] (review #264 R5) undershoot は末尾音素へ不足分を
            # 加算して合成せず、違反として収集し行は無変更のまま返す。
            undershoot_violations.append(
                f"{name}: ph_dur total {total:.4f}s < wav duration "
                f"{wav_dur:.4f}s by {wav_dur - total:.4f}s (undershoot beyond "
                f"tolerance {tol:.4f}s; the EOF-clamp diagnosis this module "
                "documents explains overshoot only, undershoot cause is "
                "unobserved/unexplained — fail-closed, not auto-repaired)"
            )
            fixed_rows.append(row)
            continue
        new_ph_dur, n_touched = _truncate_ph_dur_tail_to_wav_duration(ph_dur, wav_dur)
        n_dropped = sum(1 for d in new_ph_dur if d <= 0.0)
        new_row = _drop_dead_phonemes(row, new_ph_dur)
        fixed_rows.append(new_row)
        surviving_dur = [float(x) for x in new_row["ph_dur"].split()] if new_row["ph_dur"] else []
        fix_log.append(
            f"{name}: ph_dur total {total:.4f}s -> {math.fsum(surviving_dur):.4f}s "
            f"(wav duration {wav_dur:.4f}s, tail-truncated: {n_touched} trailing "
            f"phoneme(s) adjusted, forward boundaries preserved"
            + (f", {n_dropped} zero-length phoneme(s) removed" if n_dropped else "")
            + ")"
        )
    return fixed_rows, fix_log, undershoot_violations, unsafe_name_violations, missing_wav_violations


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R5): `--staging-dir` が `--pjs-root`/
    `--converter-dir` と衝突する場合に送出する（fail-closed。`_swap_into_place`
    による rename/`.old` 退避・rmtree の前に検出する。`build_dataset.py` R4 の
    `OutputCollisionError` と同型判定（record スクリプト群の既存慣例に倣い、
    共有モジュール新設ではなく各ファイル内へコピペ実装。`f1_4_record_2026-08-15.md`
    §1 の Design Memo 判断を踏襲）。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """`out_paths`（resolve 後）を相互および `protected_roots`（存在する
    もののみ、resolve 後）と照合し、衝突があれば公開前に fail-closed で
    拒否する。

    `--staging-dir` が `--pjs-root`/`--converter-dir` と一致・内包関係に
    ある場合、`_swap_into_place` の旧 `staging_dir` を `.old` へ退避する
    処理や次回実行時の `.old` rmtree が、PJS コーパスや converter clone
    そのものを破壊し得る（`build_dataset.py` `_reject_output_collision`
    と同一の resolved 比較ロジック）。

    review #263 R7 P1: 包含判定は双方向で行う。出力が保護 root 配下にある
    場合（従来判定）に加え、**保護 root が出力配下にある場合**（例:
    `--staging-dir=/tmp/work`, `--pjs-root=/tmp/work/pjs`）も拒否する。
    後者を見落とすと、公開時に保護 root ごと `.old` へ退避されてしまい
    （`staging_dir.rename(old_dir)` は `staging_dir` 配下の `pjs_root` も
    丸ごと退避先へ連れて行く）、以後 symlink ターゲットが指す絶対パスが
    消失した状態でコーパスが破壊される。

    [P2 修正] (review #263 R16) `guarded_outputs` 相互の照合が等価判定のみ
    だったため、祖先/子孫関係（例: `--lang-def` が `<staging-dir>.old` 配下
    を指す）を見逃していた。`.old`/`.build-<pid>` は `_swap_into_place` が
    rename/rmtree する派生パスであり、`--lang-def` がその配下にあると公開時
    の退避・削除に巻き込まれて `write_lang_def`/`_restore_lang_def_backup`
    のバックアップ復元と食い違う。`build_dataset.py` R10 と同型のロジック
    （`relative_to` による双方向包含判定）を出力相互にも適用する。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )
            # [P2 修正] (review #263 R16) 等価判定のみでは出力パス同士が
            # 祖先/子孫関係にある場合（例: `--staging-dir` の派生パス
            # `<staging-dir>.old`/`<staging-dir>.build-<pid>` 配下を
            # `--lang-def` が指すケース）を見逃す。双方向で祖先/子孫関係も
            # 公開前に fail-closed で拒否する（`build_dataset.py`
            # `_reject_output_collision` R10 と同型ロジック）。
            try:
                r_j.relative_to(r_i)
            except ValueError:
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p_j} is inside output path {p_i}（fail-closed で拒否）"
                )
            try:
                r_i.relative_to(r_j)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"output path {p_i} is inside output path {p_j}（fail-closed で拒否）"
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
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
                )
            try:
                root_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input root {root} is inside output path {p}"
                f"（fail-closed で拒否。出力側の公開処理が保護 root を巻き込む）"
            )


def stage_song_wavs(
    pjs_root: Path,
    staging_dir: Path,
    missing_pairs: Optional[List[str]] = None,
    escaped_pairs: Optional[List[str]] = None,
) -> int:
    """`PJS_corpus_ver1.1/pjsNNN/` 配下の song 系のみをシンボリックリンクで
    `pjsNNN.lab` / `pjsNNN.wav` としてリネームする。冪等（既存リンクは張り
    直す）。戻り値はステージングできたペア数。

    `pjs_root` が相対パスの場合、シンボリックリンクのターゲットは
    `staging_dir` からの相対として解釈されて壊れるため、リンク先は必ず
    `resolve()` した絶対パスで張る。

    [P2 修正] (review #263 R5) `.lab` か `_song.wav` の一方が欠けた song
    ディレクトリを黙って skip すると、契約（PJS 全 100 曲）を満たさない
    不完全コーパスのまま成功報告してしまう。`missing_pairs` を渡した場合、
    欠落した song 名を収集するのみに留め（例外は送出しない）、呼び出し元が
    `_swap_into_place`（公開）の前に一括で fail-closed 判定できるようにする。

    [P2 修正] (review #263 R9) R5 の対応は「`pjs_root` 配下に実在するディレ
    クトリのみ」を検査していたため、抽出処理が `pjsNNN` ディレクトリ自体を
    丸ごと落とした場合（`.lab`/`_song.wav` の片方だけが欠けたのではなく
    曲ディレクトリそのものが存在しない）は `missing_pairs` に一切現れず
    `n_staged` も非ゼロのまま成功してしまう（実測: pjs001-pjs099 のみで
    `(99, [])` が返る）。`pjs_root.iterdir()` で発見したディレクトリを走査
    するのではなく、契約どおりの期待 ID 集合 `PJS_EXPECTED_IDS`
    （pjs001-pjs100）に対して走査することで、ディレクトリ丸ごと欠落と
    片方欠けの双方を同じ `missing_pairs` 経路で検出する。

    [P1 修正] (review #263 R14) `.lab`/`_song.wav` の存在判定に使う
    `exists()` は symlink 解決先までは検査しないため、期待 `.lab`/
    `_song.wav` 自体が `pjs_root` 外を指す symlink（逃避）であっても素通り
    し、本関数が張るステージング symlink もその逃避先をそのまま指してしまう
    （`db_converter.py` が pjs_root 外の任意ラベル/音声を黙って学習に取り
    込む）。両ソースを `resolve()` し、pjs_root（resolve 済み）配下に
    収まらないものは `escaped_pairs` へ収集して symlink を張らずスキップ
    する（`missing_pairs` と同じ「全収集してから公開前に止める」経路で
    呼び出し元が一括判定する）。
    """
    pjs_root = pjs_root.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    n_staged = 0
    for name in PJS_EXPECTED_IDS:
        d = pjs_root / name
        lab_src = d / f"{name}.lab"
        wav_src = d / f"{name}_song.wav"
        if not lab_src.exists() or not wav_src.exists():
            if missing_pairs is not None:
                missing_pairs.append(name)
            continue
        lab_resolved = lab_src.resolve()
        wav_resolved = wav_src.resolve()
        if not lab_resolved.is_relative_to(pjs_root) or not wav_resolved.is_relative_to(pjs_root):
            if escaped_pairs is not None:
                escaped_pairs.append(name)
            continue
        for dst, src in ((staging_dir / f"{name}.lab", lab_src), (staging_dir / f"{name}.wav", wav_src)):
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)
        n_staged += 1
    return n_staged


def write_lang_def(path: Path) -> None:
    """`DEFAULT_LANG_DEF` を `path` へ書く。

    review #263 R7 P1: `path` が `--staging-dir` 外（保護入力の外）を指す
    場合、この書き込みは `_swap_into_place` の atomic swap の外側で発生する
    唯一の書き込みになる。直接 `write_text` すると変換器プロセスが書き込み
    途中で kill された場合等に破損 JSON が残り得るため、`path` と同一
    ディレクトリに一時ファイルを作ってから `os.replace()` で原子的に
    差し替える（POSIX の `rename(2)` は同一ファイルシステム内で atomic）。
    """
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp_path.write_text(json.dumps(DEFAULT_LANG_DEF, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _restore_lang_def_backup(path: Path, backup: Optional[bytes], *, created_new: bool = False) -> None:
    """[P2 修正] (review #263 R10, R11) `--lang-def` が `--staging-dir` 外を
    指す場合の巻き戻しヘルパー。`backup` が非 `None` なら元内容へ復元する。
    `backup` が `None` かつ `created_new=True`（= 書き込み前に存在しなかった
    新規パスへ `write_lang_def` が新規作成した）場合は、作成されたファイルを
    削除して「実行前は存在しなかった」状態へ戻す。それ以外（`backup=None` かつ
    `created_new=False` — 対象外パス、または `--staging-dir` 配下で
    build_dir 経由の swap/rmtree に委ねる既存ファイル）は何もしない。

    `--staging-dir` 外を指す `--lang-def` への書き込みは、`_swap_into_place`
    による新旧世代の原子的 swap の対象外（swap 前に直接上書きする）。この
    書き込み後、converter 実行〜swap 完了のいずれかが失敗すると:
    - 元ファイルが存在していた場合: 新内容のまま取り残され、コーパスと外部
      lang-def の世代が食い違う（呼び出し元がバックアップした元内容へ復元）
    - 元ファイルが存在しなかった場合（review #263 R11）: 新規作成された
      ファイルがそのまま残留し、「このパスには元々何もなかった」という実行前
      状態と食い違う（作成物を削除して原状回復する）
    いずれも成功時のみ確定（この関数は呼ばれない＝バックアップ/新規作成の
    どちらも破棄しない）。
    """
    if backup is not None:
        path.write_bytes(backup)
    elif created_new:
        path.unlink(missing_ok=True)


def _resolve_lang_def_path(lang_def_arg: Optional[Path], staging_dir: Path, build_dir: Path) -> Path:
    """`--lang-def` の実書き込み先を決める（P2 修正・review #263 R4）。

    `--lang-def` 省略時は既定どおり `build_dir / "lang.json"`。明示指定時、
    それが `staging_dir` 配下を指す場合は build 中に存在しない（fresh build
    では `<staging>.build-<pid>` しか無く書き込みが FileNotFoundError になる）
    か、旧 `staging_dir` が残っていれば build 外＝旧世代側に書かれてしまい
    `_swap_into_place` の `.old` 退避で消える（コーパスと一緒に staged→
    published されない）。`staging_dir` 配下を指す出力先は同じ相対位置の
    `build_dir` 配下へ再マップし、コーパスと一緒に swap されるようにする。
    `staging_dir` 外を指す場合は従来通りそのパスへ書く。
    """
    if lang_def_arg is None:
        return build_dir / "lang.json"
    try:
        rel = lang_def_arg.resolve().relative_to(staging_dir.resolve())
    except ValueError:
        return lang_def_arg
    return build_dir / rel


def run_converter(
    converter_dir: Path, staging_dir: Path, lang_def_path: Path, python_bin: str
) -> "subprocess.CompletedProcess[str]":
    """`db_converter.py -T 0 -L <lang_def> -m -c -B <staging_dir>` を実行する。

    フラグの意味（`s1a_conversion_record.md` §2 の 3 回目=最終版と同一）:
    `-T 0` 無音しきい値、`-L` 言語定義、`-m` MIDI 推定（note_seq/note_dur 取得）、
    `-c` ph_num 付与、`-B` breath 検出（AP 音素。付けないと binarize の
    `check_coverage()` が AP=0 件で `BinarizationError` になる）。

    `converter_dir`/`staging_dir`/`lang_def_path` のいずれかが相対パスの場合、
    `cwd=converter_dir` で実行すると `cwd` 変更後に相対パスが再解釈されて
    壊れる（`converter_dir` は `nnsvs-db-converter/nnsvs-db-converter/...` の
    二重連結、`staging_dir`/`lang_def_path` は呼び出し元の cwd ではなく
    `converter_dir` 基準で誤って再解釈される）ため、コマンド構築前に全て
    `resolve()` して絶対パス化する。
    """
    converter_dir = converter_dir.resolve()
    staging_dir = staging_dir.resolve()
    lang_def_path = lang_def_path.resolve()
    cmd = [
        python_bin, str(converter_dir / "db_converter.py"),
        "-T", "0", "-L", str(lang_def_path), "-m", "-c", "-B",
        str(staging_dir),
    ]
    return subprocess.run(cmd, cwd=str(converter_dir), capture_output=True, text=True, check=False)


def _swap_into_place(build_dir: Path, staging_dir: Path) -> None:
    """`build_dir`（symlink 群 + `diffsinger_db/` を完全に構築済み）を
    `staging_dir` へ原子的に差し替える。

    途中失敗時の新旧世代混在防止（review #263 R3 P1）: 旧 `staging_dir` は
    削除せず `<staging_dir>.old` へ退避してから `build_dir` を最終名へ
    rename する。`build_dir` を常に空の状態から作り直すことで、
    `pjs_root` から消えた曲の symlink が再実行後も残り続ける問題も併せて
    解消する。

    review #263 R7 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: 2 段 rename は独立した 2 操作のため、旧世代の退避
    （`staging_dir` -> `.old`）が成功した後に新世代の rename
    （`build_dir` -> `staging_dir`）が失敗（`KeyboardInterrupt` 含む）すると、
    正規パス `staging_dir` が消失したまま旧世代は `.old` にしか存在しない
    状態になる。新世代 rename を `except BaseException` で保護し、失敗時は
    退避済みの旧世代を `staging_dir` へ復元してから再送出する。

    review #263 R8 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: R7 の保護は新世代 rename のみを try で囲んでいたため、退避
    rename（`staging_dir` -> `.old`）が完了した直後・try 進入前にも中断窓が
    残っていた。加えて、退避完了の判定を `evicted_old = True` という後続
    代入で行っていたため、`rename(2)` 自体は成功しているのに代入文自体が
    実行されない極めて狭い窓も理論上残る。退避 rename から公開 rename
    までの遷移全体を単一の try/except BaseException で覆うと同時に、
    「退避が完了したか」の判定をフラグ変数ではなく `old_dir.exists()` と
    いう実ファイルシステム状態の観測へ置き換える（このメソッド冒頭で
    `.old` を消去済みのため、except 到達時点で `old_dir` が存在するのは
    今回の退避 rename が成功した場合のみであり、フラグの代入タイミングに
    依存しない）。これにより両方の中断窓を閉じる。
    """
    old_dir = staging_dir.parent / f"{staging_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    try:
        if staging_dir.exists():
            staging_dir.rename(old_dir)
        build_dir.rename(staging_dir)
    except BaseException:
        if old_dir.exists() and not staging_dir.exists():
            old_dir.rename(staging_dir)
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pjs-root", type=Path, required=True,
        help="展開済み 'PJS_corpus_ver1.1/' のパス (pjsNNN/ の親ディレクトリ)",
    )
    parser.add_argument(
        "--converter-dir", type=Path, required=True,
        help="clone 済み UtaUtaUtau/nnsvs-db-converter のパス (pin: README.md 参照)",
    )
    parser.add_argument(
        "--staging-dir", type=Path, required=True,
        help="song wav のリネーム済みステージング先。変換出力 "
             "'<staging-dir>/diffsinger_db/' もここに生成される (新規/上書き可、冪等)。"
             "内部では '<staging-dir>.build-<pid>' に完全構築してから成功時のみ"
             "原子的に swap する (review #263 R3 P1)",
    )
    parser.add_argument(
        "--lang-def", type=Path, default=None,
        help="言語定義 JSON の出力先 (既定: <staging-dir>/lang.json)",
    )
    parser.add_argument(
        "--python-bin", default=sys.executable,
        help="db_converter.py を実行する python (既定: 現在の interpreter)",
    )
    args = parser.parse_args(argv)

    staging_dir: Path = args.staging_dir

    # [P1 修正] (review #263 R5) --staging-dir が --pjs-root/--converter-dir
    # と重なる場合、symlink 構築や swap を始める前（公開開始前）に fail-closed
    # で拒否する。--staging-dir は成功時に丸ごと rename/`.old` 退避/rmtree
    # されるため、保護入力と重なっていると PJS コーパスや converter clone
    # そのものを破壊し得る。
    #
    # [P1 修正] (review #263 R7) --lang-def が --staging-dir 外を指す場合、
    # `_resolve_lang_def_path` はそのパスへ直接書く（swap の外側の書き込み）。
    # このパスが --pjs-root/--converter-dir と一致・内包関係にあると、
    # 変換前に保護入力（例: 曲の .lab ラベル）を JSON で上書きしてしまう
    # ため、--staging-dir 外を指す --lang-def もここで衝突ガード対象に含める
    # （--staging-dir 配下を指す場合は build_dir 経由で swap に含まれ、
    # 既存の --staging-dir チェックで保護済みのため対象外）。
    #
    # [P1 修正] (review #263 R9) 上記は --staging-dir 本体のみを検査して
    # いたが、`_swap_into_place` が実際に削除・rename するのは
    # `<staging-dir>.old`（毎回 rmtree）と `<staging-dir>.build-<pid>`
    # （成功時に --staging-dir へ rename）という派生パスであり、これらは
    # チェック対象外だった。例えば `--staging-dir=/tmp/published`,
    # `--pjs-root=/tmp/published.old` は本チェックを素通りしたのち、公開時
    # に `old_dir` の `rmtree` が `pjs_root`（保護入力）そのものを削除する。
    # 派生パスを算出してガード対象に加える（build_dir は後段の実際の構築
    # 先と同じ pid 由来の同一パスを再利用し、二重定義を避ける）。
    old_dir = staging_dir.parent / f"{staging_dir.name}.old"
    build_dir = staging_dir.parent / f"{staging_dir.name}.build-{os.getpid()}"

    guarded_outputs: List[Path] = [staging_dir, old_dir, build_dir]
    if args.lang_def is not None:
        try:
            args.lang_def.resolve().relative_to(staging_dir.resolve())
        except ValueError:
            guarded_outputs.append(args.lang_def)
    try:
        _reject_output_collision(guarded_outputs, protected_roots=[args.pjs_root, args.converter_dir])
    except OutputCollisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    swapped = False
    try:
        missing_pairs: List[str] = []
        escaped_pairs: List[str] = []
        n_staged = stage_song_wavs(args.pjs_root, build_dir, missing_pairs, escaped_pairs)
        if n_staged == 0:
            print(f"error: no pjsNNN song wav found under {args.pjs_root}", file=sys.stderr)
            return 1
        if missing_pairs:
            # [P2 修正] (review #263 R5) .lab/_song.wav の一方が欠けた song
            # ディレクトリを黙って skip すると縮小コーパス（契約=100 曲未満）
            # のまま成功報告してしまうため、swap 前に一括で fail-closed 拒否
            # する（完全な素材では missing_pairs は常に空のため挙動不変）。
            print(
                f"error: {len(missing_pairs)} pjsNNN song dir(s) missing .lab or "
                f"_song.wav under {args.pjs_root} (fail-closed, not staged/published): "
                + ", ".join(missing_pairs),
                file=sys.stderr,
            )
            return 1
        if escaped_pairs:
            # [P1 修正] (review #263 R14) .lab/_song.wav 自体が pjs_root 外を
            # 指す symlink（逃避）だった song を全収集し、symlink を張らず
            # swap 前に一括で fail-closed 拒否する（正常な素材では
            # escaped_pairs は常に空のため挙動不変）。
            print(
                f"error: {len(escaped_pairs)} pjsNNN song dir(s) have .lab or "
                f"_song.wav resolving outside {args.pjs_root} (symlink escape, "
                "fail-closed, not staged/published): " + ", ".join(escaped_pairs),
                file=sys.stderr,
            )
            return 1

        lang_def_path = _resolve_lang_def_path(args.lang_def, staging_dir, build_dir)

        # [P2 修正] (review #263 R10, R11) lang_def_path が --staging-dir 外を
        # 指す場合（= args.lang_def と同一パス）、write_lang_def はここで
        # swap の外側で直接書く。以降の converter 実行〜swap 完了のいずれかが
        # 失敗した場合に原状復帰できるよう、書き込み前の状態を記録しておく
        # （成功時のみ確定）:
        #   - 既存ファイルだった場合: 上書き前の内容をバックアップし、失敗時に
        #     復元する（review #263 R10）。
        #   - 新規パス（元ファイルなし）だった場合: 失敗時に write_lang_def が
        #     新規作成したファイルを削除し、「元々存在しなかった」状態へ戻す
        #     （review #263 R11。バックアップを取らないだけでは新規作成分が
        #     残留し続けてしまうため）。
        lang_def_is_external = args.lang_def is not None and lang_def_path == args.lang_def
        lang_def_pre_existed = lang_def_is_external and lang_def_path.exists()
        lang_def_backup: Optional[bytes] = None
        if lang_def_pre_existed:
            lang_def_backup = lang_def_path.read_bytes()
        lang_def_created_new = lang_def_is_external and not lang_def_pre_existed

        write_lang_def(lang_def_path)

        try:
            result = run_converter(args.converter_dir, build_dir, lang_def_path, args.python_bin)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode != 0:
                print(f"error: db_converter.py failed (exit {result.returncode})", file=sys.stderr)
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return result.returncode

            # [P2 修正] (review #263 R16) db_converter.py が exit 0 でも、
            # 内部で早期に空の結果を返して正常終了しているケース（Codex 再現:
            # 空出力 + lang.json のみが公開される）を exit code だけでは検出
            # できない。swap（公開）の前に、実際に生成された
            # `diffsinger_db/transcriptions.csv` の存在・行数 > 0・期待曲数
            # （今回ステージングした曲 ID 集合。完全素材では PJS 契約どおり
            # 100 曲）との整合を検証し、欠落・不整合なら fail-closed で拒否
            # する（`build_dataset.py` `validate_speaker` の下流検証と対の
            # 「生成側」ゲート）。曲数と行数は一致しない点に注意（実測: 実際の
            # `db_converter.py` は 1 曲を複数セグメント `pjsNNN_segXXX` へ
            # 分割するため 100 曲 -> 287 行が正常。したがって行数の等価判定
            # ではなく、行の `name` が指す曲 ID 集合による被覆判定で整合を見る
            # — 詳細は下記コメント参照）。
            out_csv_path = build_dir / "diffsinger_db" / "transcriptions.csv"
            if not out_csv_path.exists():
                print(
                    f"error: db_converter.py exited 0 but {out_csv_path} was not produced "
                    "(fail-closed, not published)",
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1
            with open(out_csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                out_fieldnames = reader.fieldnames
                out_rows = list(reader)
            if len(out_rows) == 0:
                print(
                    f"error: {out_csv_path} has 0 data rows (fail-closed, not published)",
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1
            # `db_converter.py` は 1 曲を複数セグメント（`pjsNNN_segXXX`）へ
            # 分割して書き出すため、行数は曲数（`n_staged`）と一致しない
            # （実測: 100 曲 -> 287 行が正常）。「行数 == 曲数」の等価判定は
            # 誤検知になるため、行の `name` 先頭が指す曲 ID（`pjsNNN` 部分）を
            # 集計し、今回ステージングした曲 ID 集合（`PJS_EXPECTED_IDS` から
            # missing_pairs/escaped_pairs を除いたもの）が全件カバーされて
            # いるかで整合を判定する（曲が丸ごと出力から欠落するケースを検出）。
            staged_ids = {
                n for n in PJS_EXPECTED_IDS if n not in missing_pairs and n not in escaped_pairs
            }
            covered_ids = {
                row["name"].split("_seg")[0] for row in out_rows if row.get("name")
            }
            uncovered = sorted(staged_ids - covered_ids)
            if uncovered:
                print(
                    f"error: {out_csv_path} has no row for {len(uncovered)} staged song(s) "
                    f"out of {len(staged_ids)} (fail-closed, not published), e.g. "
                    + ", ".join(uncovered[:5]),
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1

            # fix (2026-08-16, review #264 R1 P1 で修復方式を是正): ph_dur
            # 合計と実 wav 長が許容誤差を超えて乖離する行（s1_poison_scan
            # 捜査で判明した5件、いずれも overshoot）を、末尾側だけを切り
            # 詰めた ph_dur で正規化する（前方の音素境界は無変更 —
            # `_truncate_ph_dur_tail_to_wav_duration` 参照）。乖離が許容内の
            # 行は無変更のため、正規化対象が皆無なら CSV は書き換えない
            # （bytewise 不変を保つ）。
            #
            # [P2 修正] (review #264 R5) undershoot（申告合計 < 実 wav 長）は
            # 自動修復せず、公開前に fail-closed で拒否する（モジュール冒頭
            # コメント参照。EOF クランプ診断は overshoot のみを説明し、
            # undershoot は原因未特定・現行素材でも未観測のため）。
            #
            # fix (2026-08-16, review #264 R7) `out_rows` の `name` は外部
            # 変換器 `db_converter.py` の出力（信頼できない入力）であり、
            # 絶対パス・`..` トラバーサル・wav_dir 外へ逃げる symlink 経由の
            # name を `_is_safe_wav_name` で拒否する（`_wav_duration_seconds`
            # で open する前に判定するのは `normalize_ph_dur_to_wav_duration`
            # 内部の責務。ここでは違反が1件でもあれば公開前に fail-closed で
            # 拒否する）。
            out_wav_dir = build_dir / "diffsinger_db" / "wavs"
            (
                out_rows,
                ph_dur_fix_log,
                ph_dur_undershoot_violations,
                ph_dur_unsafe_name_violations,
                ph_dur_missing_wav_violations,
            ) = normalize_ph_dur_to_wav_duration(out_rows, out_wav_dir)
            if ph_dur_unsafe_name_violations:
                print(
                    f"error: {len(ph_dur_unsafe_name_violations)} row(s) have an unsafe wav "
                    "name (path traversal / symlink escape, fail-closed, not published): "
                    + "; ".join(ph_dur_unsafe_name_violations),
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1
            if ph_dur_missing_wav_violations:
                # fix (2026-08-16, review #264 R9 P2): db_converter.py が成功
                # 終了していても、CSV 行の WAV が欠落・破損・ゼロ長で検証
                # 不能な場合は「skip = 無視してよい」とみなさず公開前に
                # fail-closed で拒否する（モジュール docstring 参照）。
                print(
                    f"error: {len(ph_dur_missing_wav_violations)} row(s) have a missing/"
                    "unreadable/zero-length wav (cannot validate ph_dur, fail-closed, "
                    "not published): " + "; ".join(ph_dur_missing_wav_violations),
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1
            if ph_dur_undershoot_violations:
                print(
                    f"error: {len(ph_dur_undershoot_violations)} row(s) have ph_dur total "
                    "below wav duration beyond tolerance (undershoot, fail-closed, not "
                    "published): " + "; ".join(ph_dur_undershoot_violations),
                    file=sys.stderr,
                )
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return 1
            if ph_dur_fix_log:
                for msg in ph_dur_fix_log:
                    print(f"ph_dur normalized: {msg}")
                tmp_csv_fd, tmp_csv_name = tempfile.mkstemp(
                    dir=out_csv_path.parent, prefix=f"{out_csv_path.name}.", suffix=".tmp"
                )
                try:
                    with os.fdopen(tmp_csv_fd, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
                        writer.writeheader()
                        writer.writerows(out_rows)
                except BaseException:
                    os.unlink(tmp_csv_name)
                    raise
                os.replace(tmp_csv_name, out_csv_path)

            _swap_into_place(build_dir, staging_dir)
            swapped = True
        except BaseException:
            _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
            raise
    finally:
        if not swapped:
            shutil.rmtree(build_dir, ignore_errors=True)

    out_db = staging_dir / "diffsinger_db"
    print(f"n_song_staged={n_staged}")
    print(f"output_dir={out_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
