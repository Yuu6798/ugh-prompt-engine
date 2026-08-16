"""S1 早期ゲート受け皿: export -> さくら/うみ合成 -> WAV を 1 コマンドで走らせる。

対応: `voice_genesis/foundry/S1_GPU_RUNBOOK.md` §5.2-5.4。

--- 多話者 reflow acoustic 対応（2026-08-15、S1 5K 実 ckpt で追加） -------------
  S1 は `backbone_type: lynxnet2` / `diffusion_type: reflow` / `num_spk: 2` /
  `use_shallow_diffusion: true` の多話者 reflow-diffusion モデルであり、export
  された acoustic.onnx の入力 I/F は canon 配布（単一話者・DDPM 系・`speedup`）
  とは異なる（`spk_embed` [1,n_frames,384] 必須・`depth` は float スカラー・
  `speedup` でなく `steps`(int64 スカラー) 必須）。`run_pipeline` は
  acoustic.onnx の実際の入力名を実行時に検査し、`spk_embed`+`steps` が両方
  存在すれば reflow 多話者パス、なければ従来の canon DDPM パス（後方互換・
  無変更）を使う。

  サンプリング仕様の根拠（記録: `s1_5k_gate_record.md`）:
  - `depth`: OpenUtau `DiffSingerRenderer.cs` の既定動作
    （`Preferences.Default.DiffSingerDepth` 既定値 1.0 を
    `singer.dsConfig.maxDepth`（export 時の dsconfig.yaml `max_depth`）で
    クランプ）をそのまま採用。`RectifiedFlowONNX.forward`
    （`deployment/modules/rectified_flow.py`）の
    `t_start = max(1 - depth, self.t_start)` により、このアーキテクチャで
    到達可能な最深（=最も noise 依存度が高い＝shallow 開始を意味する値）
    になる。
  - `steps`: 学習時 `config.yaml` の `sampling_steps: 20` を採用。OpenUtau
    `Preferences.cs` の `DiffSingerSteps` 既定値も同じ 20 で独立に一致。
  - `spk_embed`: export 済み `<exp_name>.<speaker>.emb`（384-dim float32、
    話者ベクトル）を全フレームへ定数タイル化する
    （OpenUtau `DiffSingerSpeakerEmbedManager.PhraseSpeakerEmbedByFrame`
    の単一話者・ボイスカラーカーブなしケースと同一の畳み込み結果）。
  - `--speaker {ritsu,pjs}`: acoustic ディレクトリの `<exp_name>.<speaker>.emb`
    を検索して読み込む。reflow パスでは必須（見つからなければ fail-closed で
    停止）。canon DDPM パスでは無視される。

本スクリプトの CPU 推論チェーン（linguistic -> dur -> linguistic(2回目) -> pitch
-> acoustic -> vocoder）は、波音リツ DiffSinger CPU 直接推論スパイク（`s0_probe_record.md`、
scratchpad 完結・非コミット。実行日 2026-08-15、フル配線 sha256 決定論確認済み:
`42f459d5ec27b4b4b036a7e6415a93beabf5549e1943324a94fa88eb1f119b98`）の一般化・清書版
（全曲・全モーラ対応・export 配線・二重符号化フォールバック追加）であり、本ディレクトリ
（`voice_genesis/foundry/s1_gate/`）へ収載することでその実装を正本化する。モデル実体
（onnx/zip）の取得元・sha256 pin は本ディレクトリの `README.md` を参照（非コミット）。

--- 使い方（本番: GPU から ckpt が届いたら） -----------------------------------
    python gate_synth.py run \\
        --diffsinger-repo <e2307b1 clone> \\
        --ckpt-dir <checkpoints/<exp_name> を回収したディレクトリ (ckpt+config.yaml)> \\
        --step 5000 \\
        --exp-name s1_ritsu_pjs_acoustic_v1 \\
        --canon-model-dir <NamineRitsu_DiffSinger 展開先> \\
        --vocoder-dir <nsf_hifigan.onnx 展開先> \\
        --out-dir <出力先>

  内部で ①export.py acoustic 実行 → onnx_gate_<step>/acoustic.onnx (+
  acoustic.phonemes.json) を生成 ②linguistic/dur/pitch は canon 辞書のまま、
  acoustic だけ acoustic.phonemes.json の自前 ID 空間へ二重符号化 ③さくら
  （全 20 モーラ）・うみ（全 12 モーラ）を合成 ④sha256・長さ・RMS を記録。

  既定 --tokens own は fail-closed: acoustic.onnx と同じディレクトリに
  *.phonemes.json（export.py `_export_phonemes` の実出力）が無いとエラー終了
  する（runbook §5.3 が警告する「クラッシュしないが誤った音素へ着地する」
  サイレント不整合を canon 符号化への暗黙フォールバックで踏まないための安全弁）。

--- 使い方（事前検証: 自前 ckpt なし。canon acoustic.onnx を差し替え対象と見立てる） ---
    python gate_synth.py run \\
        --skip-export --tokens canon \\
        --acoustic-dir <NamineRitsu_DiffSinger 展開先（acoustic.onnx をそのまま使う）> \\
        --canon-model-dir <NamineRitsu_DiffSinger 展開先> \\
        --vocoder-dir <nsf_hifigan.onnx 展開先> \\
        --out-dir <出力先>

  `--tokens canon` を明示指定した場合のみ canon 符号化を許可する（acoustic.onnx
  が canon 本体そのものなので ID 空間が同一になり、S0 と bit-identical な出力に
  なるはず）。--tokens を省略/own のままだと *.phonemes.json が無いためエラー
  終了する（意図した fail-closed の挙動）。S0 との sha 比較用に
  --song sakura --notes-limit 6 を追加すると S0 と同一入力（冒頭2フレーズ）
  になる。

--- 写像テーブル検証（自前 phonemes 語彙 vs canon 617/46 語彙） -----------------
    python gate_synth.py mapping-check \\
        --diffsinger-repo <e2307b1 clone> \\
        --own-dictionary-ja <binarize 入力の dictionary-ja.txt (or merged_ja_dict.txt)> \\
        --canon-phonemes-txt <canon phonemes.txt>
"""
from __future__ import annotations

# [P2 修正] (review #264 R10, gate_synth.py:1200; R13, gate_synth.py:119)
# gate_synth.py 自身の provenance ハッシュを、モジュールのトップレベル
# コードが実行される中で最初に到達する文として（`from __future__ import
# annotations` の直後・NumPy/ONNX 等の重い import より前）1 回だけ
# read_bytes() して確定する。R10 まではこの計算を `cmd_run` の実行途中
# （synth ループ開始前）で行っており、「プロセス起動時に Python インタプリタ
# が実際に read/exec した本体スクリプトの bytes」ではなく「cmd_run がその
# 行に到達した時点でディスク上にある bytes」を記録していた。R10 でモジュール
# 先頭へ移したが、当時は NumPy/ONNXRuntime 等の低速 import の後段に置いて
# おり、それら import の所要時間だけ TOCTOU 窓が残っていた（R13 指摘）。
# これを import 文より前（hashlib/Path という stdlib のみで計算できる形）
# へ動かすことで、窓を「インタープリタが本ファイルを read/compile してから
# この行が実行されるまで」のマイクロ秒級に縮める。
#
# ここで縮められるのはあくまで実務上の最小化であり、ゼロにはならない
# （境界宣言）: このプロセス自身が自分の起動時に「ローダーが実際に消費した
# バイト」を内側から束縛することは原理的に不可能である。自己ハッシュより
# 前には必ずインタープリタによる read/compile が先行しており、外側にラン
# チャーを足しても、そのランチャー自身が「起動〜自己ハッシュ」という同型の
# 窓を新たに持つだけで問題を後退させるにすぎない（infinite regress）。した
# がって内側で可能な最小化（本移動）をもって自己ハッシュ系列の対応はここで
# 終端とし、残る irreducible な窓の担保は、呼び出し側が本プロセスの起動
# より前にファイルをハッシュする外部 attestation 層（`S1_GPU_RUNBOOK.md`
# §3.1 の manifest 方式）の担当領域とする。
#
# `cmd_run` はこの値をそのまま `input_sha256["gate_synth_py"]` へ転記し
# （再読み込みしない）、公開直前に同じパスを再ハッシュしてこの値と突き
# 合わせる事後照合を行う（score モジュールと同じ pre+post 二段方式。
# 不一致なら fail-closed で公開を止める。この事後照合ロジック自体は本
# 変更で無改変）。
import hashlib
from pathlib import Path

_GATE_SYNTH_PY_PATH = Path(__file__).resolve()
_GATE_SYNTH_PY_LOAD_TIME_SHA256 = hashlib.sha256(_GATE_SYNTH_PY_PATH.read_bytes()).hexdigest()

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
from typing import Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
import soundfile as sf  # noqa: E402
import yaml  # noqa: E402

# `voice_genesis/foundry/s1_gate/gate_synth.py` から見て `voice_genesis/singer/`
# は 2 階層上の兄弟ディレクトリ（parents[0]=s1_gate, [1]=foundry, [2]=voice_genesis）。
# ハードコード絶対パスは使わず、リポジトリのどこに clone されても動くように
# スクリプト自身の位置から相対導出する（`--singer-dir` で明示上書きも可能）。
DEFAULT_SINGER_DIR = Path(__file__).resolve().parents[2] / "singer"

SEED = 42
HEAD_FRAMES = 8
TAIL_FRAMES = 8
LEAD_PADDING_MS = 500.0
ACOUSTIC_STEPS = 20  # canon DDPM 経路専用（speedup 計算の除数、後方互換維持のため無変更）
PITCH_STEPS = 10
VOWEL_SET = {"a", "i", "u", "e", "o", "N"}

# reflow 多話者 acoustic 経路の sampling steps。根拠: 学習時 config.yaml の
# `sampling_steps: 20` と、OpenUtau `Preferences.cs` の `DiffSingerSteps` 既定値
# 20 が独立に一致（両方 S1 5K checkpoint で実測確認済み。docstring 冒頭参照）。
REFLOW_SAMPLING_STEPS = 20


# ============================================================================
# 0. スコア読み込み（さくら/うみ共通インタフェース）
# ============================================================================


def _exec_module_from_source(module_name: str, path: Path, source: bytes) -> types.ModuleType:
    """呼び出し側が一度だけ `read_bytes()` 済みの `source` を直接
    `compile()`/`exec()` してモジュールオブジェクトを構築し、
    `sys.modules[module_name]` へ登録して返す。

    [P2 修正] (review #264 R3) `import <module>` 文（標準 `SourceFileLoader`）
    は既定でタイムスタンプ方式の `__pycache__/*.pyc` キャッシュを条件付きで
    再利用する。R1/R2 の `sys.modules` evict は「同名モジュールが別内容で
    既にキャッシュ済み」の場合を封じるが、evict 後に実行される `import` 文
    自体は依然としてファイルシステム上の `.pyc` を信頼し得る——例えば
    score.py が同一サイズの内容へ差し替わり、かつファイルシステムの
    タイムスタンプ精度内（同一 tick）で書き換えられた場合、`.pyc` ヘッダの
    (mtime, size) がなお一致し、古いバイトコードがそのまま実行され得る。

    本関数は `import` 文を一切使わず、`compile()`/`exec()` する。
    `compile()` は `.pyc` キャッシュの生成/参照を一切行わない（そのキャッシュ
    機構は `importlib` の import 機構側にのみ存在する）ため stale `.pyc` を
    踏まない。

    [P2 修正] (review #264 R6) 従来は本関数が内部で `path.read_bytes()` して
    いたため、呼び出し側（`load_song_module`）が provenance sha256 用に読む
    read と、本関数が exec 用に読む read が別 read になり、両者の間にファイル
    が差し替えられると「記録した pin」と「実際に exec された内容」が食い違い
    得た（TOCTOU）。`source` を呼び出し側から受け取る形にすることで、
    ハッシュ計算に使ったバッファと exec するバッファが構造的に同一であること
    を保証する（`_read_and_exec_module` 参照）。
    """
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    code = compile(source, str(path), "exec")
    try:
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _read_and_exec_module(module_name: str, path: Path) -> Tuple[types.ModuleType, str]:
    """`path` を一度だけ `read_bytes()` し、同じバッファを sha256 化（provenance
    pin 用）と `compile()`/`exec()`（実行用）の両方に使う。戻り値は
    `(module, sha256_hex)`。

    [P2 修正] (review #264 R6) `load_song_module` の唯一の read 経路にする
    ことで、「pin したハッシュ」と「実際に exec された内容」が別 read に
    起因して食い違う TOCTOU 窓を構造的に閉じる。
    """
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    module = _exec_module_from_source(module_name, path, source)
    return module, digest


def load_song_module(
    song: str, singer_dir: Path
) -> Tuple[Callable, Callable, float, Path, Dict[str, Tuple[Path, str]]]:
    """楽曲モジュール読み込み。戻り値に実際に import したモジュールファイルの
    絶対パスと、読み込んだ全モジュール（本体 + 推移的依存）の provenance
    sha256 も含める（PR #263 R6 レビュー指摘: gate summary の input_sha256 が
    合成実装そのもの — 本スクリプトと score.py/score_umi.py — の変更を検出
    できなかったため、呼び出し側でこのパスを sha256 化して記録する）。

    依存順（`phoneme_jp` -> `score` -> `score_umi`。`song == "sakura"` は
    `score_umi` を読まない）で `_read_and_exec_module` を呼び、常に
    `singer_dir` 配下の現在のソースバイトを compile/exec する。

    戻り値の 5 番目の要素 `module_shas` は
    `{pin_key: (実際に exec したファイルの絶対パス, その sha256 hex digest)}`。
    `pin_key` は `cmd_run` の `input_sha256` 既存キー命名（本体
    `score_module_{song}` / 依存 `score_module_{song}_dep_{stem}`）と同一。

    [P2 修正] (review #264 R1) 別 `singer_dir`（または以前の呼び出し）由来の
    `sys.modules` キャッシュを踏まない。
    [P2 修正] (review #264 R2) `score_umi.py` が推移的に依存する
    `score`/`phoneme_jp` も song に関わらず毎回フレッシュにする。
    [P2 修正] (review #264 R3) `import` 文自体が触れ得る `__pycache__` の
    stale `.pyc` 再利用を、`import` 文を使わない実装へ切り替えることで
    構造的に封じる（`_exec_module_from_source` docstring 参照）。
    `_exec_module_from_source` は呼ぶたびに `sys.modules[name]` を無条件で
    新しいモジュールオブジェクトへ上書きするため、R1/R2 が行っていた事前
    evict ループ（`sys.modules.pop`）は本実装では不要（上書き自体が evict を
    包含する、より強い保証のため）。
    [P2 修正] (review #264 R6) provenance sha256 の取得（旧: `cmd_run` 側で
    `sha256_file()` を個別に呼ぶ）と実際の exec を本関数 1 回の呼び出しへ
    統合した（`_read_and_exec_module` 参照）。あわせて `cmd_run` 側にあった
    「path 事前解決 → hash → 検証用 import → `synth_song` 内での再 import」
    という 4 段の別読み込みも、本関数 1 回の呼び出しへ統合する（`synth_song`
    は本関数が返す `build_fn` 等をそのまま使い、内部で再ロードしない）。
    """
    if song not in ("sakura", "umi"):
        raise ValueError(f"unknown song: {song}")

    module_shas: Dict[str, Tuple[Path, str]] = {}

    phoneme_jp_path = (singer_dir / "phoneme_jp.py").resolve()
    _, phoneme_jp_sha = _read_and_exec_module("phoneme_jp", phoneme_jp_path)
    module_shas[f"score_module_{song}_dep_phoneme_jp"] = (phoneme_jp_path, phoneme_jp_sha)

    score_path = (singer_dir / "score.py").resolve()
    sc_score, score_sha = _read_and_exec_module("score", score_path)

    if song == "sakura":
        module_shas[f"score_module_{song}"] = (score_path, score_sha)
        return (
            sc_score.build_sakura_score,
            sc_score.beats_to_seconds,
            sc_score.TEMPO_BPM,
            score_path,
            module_shas,
        )

    module_shas[f"score_module_{song}_dep_score"] = (score_path, score_sha)
    score_umi_path = (singer_dir / "score_umi.py").resolve()
    sc_umi, score_umi_sha = _read_and_exec_module("score_umi", score_umi_path)
    module_shas[f"score_module_{song}"] = (score_umi_path, score_umi_sha)
    return (
        sc_umi.build_umi_score,
        sc_umi.beats_to_seconds,
        sc_umi.TEMPO_BPM,
        score_umi_path,
        module_shas,
    )


def mora_phonemes(mora) -> List[str]:
    if mora.onset is not None:
        return [mora.onset, mora.vowel]
    return [mora.vowel]


def frames_from_ms(ms: float, frame_ms: float) -> int:
    return max(int(round(ms / frame_ms)), 0)


# ============================================================================
# 1. OpenUtau DiffSingerUtils 移植（`s0_probe_record.md` §2 と同一アルゴリズム）
# ============================================================================

def padded_word_div_dur(ph_dur: List[int], is_vowel_flags: List[bool]) -> Tuple[List[int], List[int]]:
    n = len(is_vowel_flags)
    assert len(ph_dur) == n + 2, (len(ph_dur), n)
    vowel_ids = [i for i, v in enumerate(is_vowel_flags) if v]
    if not vowel_ids:
        vowel_ids = [n - 1]
    word_div = [vowel_ids[0] + 1]
    for a, b in zip(vowel_ids, vowel_ids[1:]):
        word_div.append(b - a)
    word_div.append(n - vowel_ids[-1] + 1)
    assert all(d > 0 for d in word_div)
    assert sum(word_div) == len(ph_dur), (sum(word_div), len(ph_dur))
    word_dur = []
    offset = 0
    for length in word_div:
        word_dur.append(int(sum(ph_dur[offset: offset + length])))
        offset += length
    assert sum(word_dur) == sum(ph_dur)
    return word_div, word_dur


def fit_duration_sum(durations: List[int], total: int) -> List[int]:
    result = list(durations)
    delta = total - sum(result)
    result[-1] += delta
    if result[-1] < 0:
        deficit = -result[-1]
        result[-1] = 0
        for i in range(len(result) - 2, -1, -1):
            if deficit <= 0:
                break
            take = min(result[i], deficit)
            result[i] -= take
            deficit -= take
        if deficit > 0:
            raise ValueError("cannot fit durations to total")
    return result


# ============================================================================
# 2. 音素辞書 (二重符号化: variance=canon固定 / acoustic=自前 or canon fallback)
# ============================================================================

def sha256_file(path: Path) -> str:
    """ファイル全体の sha256 hex digest（gate summary の入力側 pin 用）。

    呼び出し側がパース/ロードに使うのとは別の read になる（TOCTOU 窓を
    開ける）ため、実際にパース/ロードされるバッファをそのままハッシュしたい
    呼び出し元は `_read_bytes_and_sha256` を使うこと。本関数は「別途 hash
    するだけで内容は使わない」用途（ckpt・train config・onnx モデル束など、
    このスクリプト自身がバイト列をパースしない入力）向けに残す。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_bytes_and_sha256(path: Path) -> Tuple[bytes, str]:
    """`path` を一度だけ `read_bytes()` し、そのバッファ自体と sha256 hex
    digest を返す。

    [P2 修正] (review #264 R9, gate_synth.py:1168) `variance_phonemes`
    （canon `phonemes.txt`）・`acoustic_phonemes`（`*.phonemes.json`）・
    話者 embedding（`*.<speaker>.emb`）は、従来 `cmd_run` がパース/ロード用に
    1 回 read し、`collect_input_sha256` が provenance pin 用に `sha256_file()`
    でもう 1 回 read していた（score モジュールで既に修正済みだった構造と
    同型の TOCTOU: 2 回の read の間にファイルが差し替わると、「pin した
    ハッシュ」と「実際にパース/ロードされた内容」が食い違い得る）。本関数を
    介して 1 回の read で得たバッファを両用途（パース/ロードと sha256 化）に
    使うことで、この窓を構造的に閉じる（`_read_and_exec_module` と同じ方式）。
    """
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def load_model_bundle_bytes(
    canon_model_dir: Path,
    vocoder_dir: Path,
    acoustic_onnx_path: Path,
    acoustic_dsconfig_path: Path,
) -> Tuple[Dict[str, bytes], Dict[str, str]]:
    """`run_pipeline` が load する ONNX モデル束 + `dsconfig.yaml` を 1 回だけ
    read し、そのバッファ（そのまま `InferenceSession`/`yaml.safe_load` に
    渡す）と sha256 を返す。

    [P2 修正] (review #264 R12, gate_synth.py:1447) 従来は `collect_input_
    sha256` が pre-load hash 用に `sha256_file()` で 1 回読み、`run_pipeline`
    が `onnxruntime.InferenceSession`/`yaml.safe_load` 用に別 read でもう
    一度読んでいた。この構成では、2 回の read の間にファイルが差し替えられ、
    かつ公開直前の事後照合（`sha256_file()` での再読み込み）前に元へ戻され
    ると、記録される pre/post ハッシュはどちらも「差し替え前後で一致する」
    内容になり、実際に推論/パースへ使われたバイト列とは食い違う ——
    pre/post 二段照合はこの TOCTOU 窓を閉じない（指摘の通り）。

    `cmd_run` が本関数で 1 回だけ `read_bytes()` したバッファを `run_pipeline`
    （`InferenceSession`/`yaml.safe_load`）へそのまま渡し、同じバッファの
    sha256 を `collect_input_sha256` の pin としても使うことで、TOCTOU 窓を
    構造的に閉じる（score モジュールの `_read_and_exec_module`/
    `_read_bytes_and_sha256` と同型パターン）。既存の pre/post 二段照合
    （`model_config_paths` 事後再ハッシュ）は、長時間の合成中に on-disk 実装
    が書き換えられていないかを検出する belt として残す（正式な記録用ハッシュ
    は本関数のバッファ由来になったため、この belt は「消費バッファ由来の
    pin」と「公開直前のディスク上の内容」の食い違い検出に役割が変わる）。

    返す dict のキーは `collect_input_sha256`/`model_config_paths`（事後
    照合）の pin_key と揃えてある。
    """
    linguistic_bytes, linguistic_sha = _read_bytes_and_sha256(canon_model_dir / "linguistic.onnx")
    dur_bytes, dur_sha = _read_bytes_and_sha256(canon_model_dir / "dsdur" / "dur.onnx")
    pitch_bytes, pitch_sha = _read_bytes_and_sha256(canon_model_dir / "dspitch" / "pitch.onnx")
    acoustic_bytes, acoustic_sha = _read_bytes_and_sha256(acoustic_onnx_path)
    vocoder_bytes, vocoder_sha = _read_bytes_and_sha256(vocoder_dir / "nsf_hifigan.onnx")
    dsconfig_bytes, dsconfig_sha = _read_bytes_and_sha256(acoustic_dsconfig_path)

    model_bytes: Dict[str, bytes] = {
        "canon_linguistic_onnx": linguistic_bytes,
        "canon_variance_dur_onnx": dur_bytes,
        "canon_variance_pitch_onnx": pitch_bytes,
        "acoustic_onnx": acoustic_bytes,
        "vocoder_onnx": vocoder_bytes,
        "acoustic_dsconfig_yaml": dsconfig_bytes,
    }
    model_shas: Dict[str, str] = {
        "canon_linguistic_onnx": linguistic_sha,
        "canon_variance_dur_onnx": dur_sha,
        "canon_variance_pitch_onnx": pitch_sha,
        "acoustic_onnx": acoustic_sha,
        "vocoder_onnx": vocoder_sha,
        "acoustic_dsconfig_yaml": dsconfig_sha,
    }
    return model_bytes, model_shas


def collect_input_sha256(
    args,
    canon_model_dir: Path,
    vocoder_dir: Path,
    acoustic_onnx_path: Path,
    acoustic_dsconfig_path: Path,
    own_json: Optional[Path],
    speaker_embed_path: Optional[Path] = None,
    canon_phonemes_txt_sha: Optional[str] = None,
    own_json_sha: Optional[str] = None,
    speaker_embed_sha: Optional[str] = None,
    model_shas: Optional[Dict[str, str]] = None,
    ckpt_sha: Optional[str] = None,
    train_config_sha: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """gate 判定を駆動した入力モデル束の sha256 を集約する。

    出力 WAV の sha256 だけでは「どのモデル束からその判定が出たか」を後日
    一意に特定できない（ckpt・config・export 済み acoustic.onnx・
    phonemes.json・acoustic dsconfig.yaml（`run_pipeline` が `max_depth` を
    読む）・canon phonemes.txt（variance トークン ID 割当に使う）・canon
    variance 各 onnx・vocoder onnx・話者 embedding のいずれかが差し替わって
    いても出力 WAV との対応関係が追えない）ため、gate summary へ入力側 sha256
    も併記する。実際に `run_pipeline`/`cmd_run` が load する全ファイルを網羅する
    （PR #263 R3 レビュー指摘: dsconfig と canon phonemes.txt が抜けていた）。
    ファイルが存在しない場合（`--skip-export` で ckpt/config が対象外、
    own_json が canon フォールバック時、canon DDPM 経路で speaker_embed_path が
    None など）は `None` を記録する。

    [P2 修正] (review #264 R9, gate_synth.py:1168) `canon_phonemes_txt_sha`/
    `own_json_sha`/`speaker_embed_sha` は、呼び出し側が `_read_bytes_and_sha256`
    経由でパース/ロードと同一 read から得た sha256 を渡すための引数（実際に
    使われたバッファそのものの hash）。渡された場合はそれを優先して記録し、
    本関数内での再 read（`sha256_file`）は行わない。未指定（None）の場合の
    み従来どおり `sha256_file()` で個別に読み直す（own_json が `--tokens
    canon` でパースされずに存在するだけのケースなど、パース由来のバッファが
    存在しない場合の後方互換フォールバック）。

    [P2 修正] (review #264 R12, gate_synth.py:1447) `model_shas` は、呼び出し
    側が `load_model_bundle_bytes` で 1 回だけ read し `InferenceSession`/
    `yaml.safe_load` にそのまま渡したバッファの sha256（`acoustic_onnx`/
    `acoustic_dsconfig_yaml`/`canon_linguistic_onnx`/`canon_variance_dur_onnx`/
    `canon_variance_pitch_onnx`/`vocoder_onnx` の 6 key）。渡された場合は
    それを優先して記録し、本関数内での再 read（`sha256_file`）は行わない
    （従来は常に別 read だったため、記録した hash と実際に load されたバッファ
    が食い違い得る TOCTOU 窓があった — 指摘の通り、公開直前の pre/post 照合
    だけではこの窓は閉じない）。未指定（None）の場合のみ従来どおり
    `sha256_file()` で個別に読み直す（`load_model_bundle_bytes` を経由しない
    呼び出し向けの後方互換フォールバック）。

    [P2 修正] (review #264 R20) `ckpt_sha`/`train_config_sha` は、
    `run_export_acoustic()` が exporter に実際に消費させたバイト列（1 回だけ
    `read_bytes()` したバッファ）から得た sha256。渡された場合はそれを優先
    して記録し、本関数内での再 read は行わない（従来は `run_export_acoustic`
    が checkpoint/config をコピー・消費した後に、この関数が元ディレクトリ
    から改めて `sha256_file()` で読み直しており、コピー〜re-read の間の
    差し替えを検出できない TOCTOU 窓があった）。未指定（None）の場合のみ
    従来どおり `sha256_file()` で個別に読み直す（`run_export_acoustic` を
    経由しない呼び出し向けの後方互換フォールバック）。
    """
    shas: Dict[str, Optional[str]] = {}
    model_shas = model_shas or {}

    if not args.skip_export:
        ckpt_dir = Path(args.ckpt_dir)
        ckpt_file = ckpt_dir / f"model_ckpt_steps_{args.step}.ckpt"
        train_config_file = ckpt_dir / "config.yaml"
        shas["ckpt"] = (
            ckpt_sha if ckpt_sha is not None
            else (sha256_file(ckpt_file) if ckpt_file.exists() else None)
        )
        shas["train_config_yaml"] = (
            train_config_sha if train_config_sha is not None
            else (sha256_file(train_config_file) if train_config_file.exists() else None)
        )

    shas["acoustic_onnx"] = (
        model_shas["acoustic_onnx"] if "acoustic_onnx" in model_shas
        else (sha256_file(acoustic_onnx_path) if acoustic_onnx_path.exists() else None)
    )
    shas["acoustic_phonemes_json"] = (
        own_json_sha if own_json_sha is not None
        else (sha256_file(own_json) if own_json is not None and own_json.exists() else None)
    )
    shas["acoustic_dsconfig_yaml"] = (
        model_shas["acoustic_dsconfig_yaml"] if "acoustic_dsconfig_yaml" in model_shas
        else (sha256_file(acoustic_dsconfig_path) if acoustic_dsconfig_path.exists() else None)
    )
    shas["speaker_embed"] = (
        speaker_embed_sha if speaker_embed_sha is not None
        else (
            sha256_file(speaker_embed_path)
            if speaker_embed_path is not None and speaker_embed_path.exists()
            else None
        )
    )

    linguistic_onnx = canon_model_dir / "linguistic.onnx"
    variance_dur_onnx = canon_model_dir / "dsdur" / "dur.onnx"
    variance_pitch_onnx = canon_model_dir / "dspitch" / "pitch.onnx"
    canon_phonemes_txt = canon_model_dir / "phonemes.txt"
    vocoder_onnx = vocoder_dir / "nsf_hifigan.onnx"
    shas["canon_linguistic_onnx"] = (
        model_shas["canon_linguistic_onnx"] if "canon_linguistic_onnx" in model_shas
        else (sha256_file(linguistic_onnx) if linguistic_onnx.exists() else None)
    )
    shas["canon_variance_dur_onnx"] = (
        model_shas["canon_variance_dur_onnx"] if "canon_variance_dur_onnx" in model_shas
        else (sha256_file(variance_dur_onnx) if variance_dur_onnx.exists() else None)
    )
    shas["canon_variance_pitch_onnx"] = (
        model_shas["canon_variance_pitch_onnx"] if "canon_variance_pitch_onnx" in model_shas
        else (sha256_file(variance_pitch_onnx) if variance_pitch_onnx.exists() else None)
    )
    shas["canon_phonemes_txt"] = (
        canon_phonemes_txt_sha if canon_phonemes_txt_sha is not None
        else (sha256_file(canon_phonemes_txt) if canon_phonemes_txt.exists() else None)
    )
    shas["vocoder_onnx"] = (
        model_shas["vocoder_onnx"] if "vocoder_onnx" in model_shas
        else (sha256_file(vocoder_onnx) if vocoder_onnx.exists() else None)
    )
    return shas


def _parse_canon_phonemes(text: str) -> Dict[str, int]:
    lines = text.splitlines()
    return {line: i for i, line in enumerate(lines)}


def load_canon_phonemes(path: Path) -> Dict[str, int]:
    """canon 配布 phonemes.txt（改行区切りリスト、行番号=ID、0=<PAD>）。"""
    return _parse_canon_phonemes(path.read_text(encoding="utf-8"))


def load_canon_phonemes_with_sha(path: Path) -> Tuple[Dict[str, int], str]:
    """`load_canon_phonemes` と同じパースを行いつつ、パースに使ったバッファの
    sha256 も返す（`_read_bytes_and_sha256` 参照。`cmd_run` の provenance pin
    用）。"""
    data, digest = _read_bytes_and_sha256(path)
    return _parse_canon_phonemes(data.decode("utf-8")), digest


def _parse_own_phonemes(text: str) -> Dict[str, int]:
    return json.loads(text)


def load_own_phonemes_json(path: Path) -> Dict[str, int]:
    """export.py `_export_phonemes` が書き出す `<model_name>.phonemes.json`
    （phone_to_id の flat dict）。実 ckpt が届いた場合の本番経路。"""
    return _parse_own_phonemes(path.read_text(encoding="utf-8"))


def load_own_phonemes_json_with_sha(path: Path) -> Tuple[Dict[str, int], str]:
    """`load_own_phonemes_json` と同じパースを行いつつ、パースに使ったバッファ
    の sha256 も返す（`_read_bytes_and_sha256` 参照。`cmd_run` の provenance
    pin 用）。"""
    data, digest = _read_bytes_and_sha256(path)
    return _parse_own_phonemes(data.decode("utf-8")), digest


def acoustic_export_basename(acoustic_dir: Path, acoustic_onnx_path: Path) -> Optional[str]:
    """acoustic.onnx が指す export の basename（`<exp_name>`）を推定する。

    review #263 R12 P1: `find_own_phonemes_json`/`find_speaker_embed` が
    複数候補から辞書順先頭を暗黙選択していた（`--acoustic-dir` を使い回すと
    別 export 由来の *.phonemes.json/*.emb が紛れ込み得る）。`run_export_acoustic`
    は実 export 出力 `<exp_name>.onnx` を `acoustic.onnx` へエイリアスコピー
    するため、`acoustic_dir` 内の *.onnx のうち `acoustic_onnx_path`（resolve
    後）と異なるものがちょうど 1 個あれば、その stem を export basename として
    返す（`<exp_name>.phonemes.json`/`<exp_name>.<speaker>.emb` の対応付けに使う）。
    0 個/複数個で決め打ちできない場合（canon 配布ディレクトリ = alias 無し、
    または alias 元が複数残存）は None を返し、呼び出し側が単一候補フォール
    バックまたは fail-closed で処理する。
    """
    resolved_target = acoustic_onnx_path.resolve() if acoustic_onnx_path.exists() else None
    onnx_candidates = [
        p for p in sorted(acoustic_dir.glob("*.onnx"))
        if resolved_target is None or p.resolve() != resolved_target
    ]
    if len(onnx_candidates) == 1:
        return onnx_candidates[0].stem
    return None


def find_own_phonemes_json(
    acoustic_dir: Path, export_basename: Optional[str] = None
) -> Optional[Path]:
    """review #263 R12 P1: `*.phonemes.json` が複数存在する場合、辞書順先頭の
    暗黙選択をやめる。`export_basename`（acoustic.onnx の export basename）が
    分かっていれば `<export_basename>.phonemes.json` を優先的に選ぶ。単一候補
    しかなければ従来どおりそれを返す（対応付け不能かつ複数候補の場合のみ
    fail-closed で停止する — 誤った音素 ID 空間へサイレントに着地するのを防ぐ）。
    """
    candidates = sorted(acoustic_dir.glob("*.phonemes.json"))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if export_basename is not None:
        matched = [p for p in candidates if p.name == f"{export_basename}.phonemes.json"]
        if len(matched) == 1:
            return matched[0]
    raise SystemExit(
        f"ERROR: multiple *.phonemes.json candidates found in '{acoustic_dir}' "
        f"({[p.name for p in candidates]}) and none matched the acoustic export "
        f"basename ({export_basename!r}) unambiguously. Refusing to silently pick "
        f"the alphabetically-first candidate (fail-closed, review #263 R12 P1) — "
        f"clean the directory so only the intended export's companion files remain."
    )


def find_speaker_embed(
    acoustic_dir: Path, speaker: str, export_basename: Optional[str] = None
) -> Optional[Path]:
    """export.py `_export_spk_embed` が書き出す `<exp_name>.<speaker>.emb`
    （384-dim float32 raw バイナリ、話者ベクトル 1 本）を探す。reflow 多話者
    acoustic の `spk_embed` 入力構築に使う（見つからなければ None、呼び出し側
    が fail-closed で停止する）。

    review #263 R12 P1: 同一話者の `*.<speaker>.emb` が複数存在する場合、
    辞書順先頭の暗黙選択をやめる。`find_own_phonemes_json` と同じ
    export_basename 対応付けを適用し、対応付け不能かつ複数候補なら
    fail-closed（単一候補時は従来どおり）。
    """
    candidates = sorted(acoustic_dir.glob(f"*.{speaker}.emb"))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if export_basename is not None:
        matched = [p for p in candidates if p.name == f"{export_basename}.{speaker}.emb"]
        if len(matched) == 1:
            return matched[0]
    raise SystemExit(
        f"ERROR: multiple *.{speaker}.emb candidates found in '{acoustic_dir}' "
        f"({[p.name for p in candidates]}) and none matched the acoustic export "
        f"basename ({export_basename!r}) unambiguously. Refusing to silently pick "
        f"the alphabetically-first candidate (fail-closed, review #263 R12 P1) — "
        f"clean the directory so only the intended export's companion files remain."
    )


def load_speaker_embed_vector(path: Path) -> np.ndarray:
    """`<exp_name>.<speaker>.emb` を 1 次元 float32 ベクトルとして読む
    （export 側は `spk_embed.cpu().numpy().tobytes()` で書き出す raw バイナリ、
    ヘッダなし）。"""
    return np.frombuffer(path.read_bytes(), dtype=np.float32).copy()


def load_speaker_embed_vector_with_sha(path: Path) -> Tuple[np.ndarray, str]:
    """`load_speaker_embed_vector` と同じロードを行いつつ、ロードに使った
    バッファの sha256 も返す（`_read_bytes_and_sha256` 参照。`cmd_run` の
    provenance pin 用）。"""
    data, digest = _read_bytes_and_sha256(path)
    return np.frombuffer(data, dtype=np.float32).copy(), digest


def build_own_dictionary_from_binarize(diffsinger_repo: Path, dictionary_ja: Path) -> Dict[str, int]:
    """binarize 入力の dictionary-ja.txt (= merged_ja_dict.txt 相当) から、export.py
    が使うのと同じ `utils.phoneme_utils.PhonemeDictionary` で自前語彙を再構築する。
    実 phonemes.json が無い段階（＝本検証）でも「本番なら何 ID になるか」を
    シミュレートできる（mapping-check 専用。run の acoustic 符号化には使わない
    — run は実際に export.py が書き出した .phonemes.json のみを信頼する）。
    """
    sys.path.insert(0, str(diffsinger_repo))
    from utils.phoneme_utils import PhonemeDictionary  # noqa: E402
    d = PhonemeDictionary(dictionaries={"ja": dictionary_ja}, extra_phonemes=[], merged_groups=[])
    return {d.decode_one(i): i for i in range(1, d.vocab_size)}


def build_phoneme_mapping(own_phonemes: Dict[str, int], canon_phonemes: Dict[str, int]) -> dict:
    """自前語彙 <-> canon 617/46 語彙の対応表 + 欠落音素列挙。"""
    own_symbols = sorted(k for k in own_phonemes if k != "<PAD>")
    canon_symbols = sorted(k for k in canon_phonemes if k != "<PAD>")
    mapping = {}
    unmapped_own = []
    for sym in own_symbols:
        if sym in canon_phonemes:
            mapping[sym] = dict(own_id=own_phonemes[sym], canon_id=canon_phonemes[sym])
        else:
            unmapped_own.append(sym)
            mapping[sym] = dict(own_id=own_phonemes[sym], canon_id=None)
    canon_uncovered = [s for s in canon_symbols if s not in own_phonemes]
    return dict(
        own_vocab_size=len(own_symbols),
        canon_vocab_size=len(canon_symbols),
        mapped_count=len(own_symbols) - len(unmapped_own),
        unmapped_own_count=len(unmapped_own),
        unmapped_own=unmapped_own,
        canon_uncovered_count=len(canon_uncovered),
        canon_uncovered=canon_uncovered,
        mapping=mapping,
    )


# ============================================================================
# 3. export.py acoustic 実行（GPU 側 ckpt が届いた本番経路のみ）
# ============================================================================

def run_export_acoustic(
    diffsinger_repo: Path, ckpt_dir: Path, exp_name: str, step: int, out_dir: Path
) -> Tuple[Path, str, str]:
    """`checkpoints/<exp_name>/` を用意して `scripts/export.py acoustic` を実行する。
    §5.2 の手順そのもの。CPU で足りる（export はモデルロード+グラフ変換のみ）。

    返り値は `(out_path, ckpt_sha256, train_config_sha256)`。後者2つは
    exporter に実際に消費させたバイト列（下記 R20 P2 参照）の sha256 hex
    digest で、`collect_input_sha256` が `sha256_file()` の別 read へ
    フォールバックせずそのまま summary へ pin できるようにする。
    """
    ckpt_target = diffsinger_repo / "checkpoints" / exp_name
    ckpt_target.mkdir(parents=True, exist_ok=True)
    ckpt_file = ckpt_dir / f"model_ckpt_steps_{step}.ckpt"
    config_file = ckpt_dir / "config.yaml"
    if not ckpt_file.exists() or not config_file.exists():
        raise FileNotFoundError(f"expected {ckpt_file} and {config_file} in {ckpt_dir}")

    # review #264 R20 P2 (checkpoint TOCTOU): 従来は `collect_input_sha256`
    # 側が `run_export_acoustic()` 完了後（＝ここでの `shutil.copy2` による
    # コピー・exporter subprocess による消費が終わった後）に、元の ckpt_dir
    # から改めて `sha256_file()` で読み直して summary の pin ハッシュを得て
    # いた。コピー〜その re-read の間に学習/同期プロセス等が元ファイルを
    # 差し替えると、「ONNX は旧バイトから生成されたのに summary は新バイトを
    # pin する」乖離が生じ得た。ここで一度だけ `read_bytes()` し、その同じ
    # バイト列を (a) sha256 化、(b) `ckpt_target` への書き出し（exporter が
    # 消費する実体）の両方に使うことで、「記録したハッシュ = exporter が
    # 実際に消費したバイト列」を構造的に保証する（score モジュールの
    # `_read_and_exec_module`/`_read_bytes_and_sha256` と同型パターン）。
    ckpt_bytes, ckpt_sha = _read_bytes_and_sha256(ckpt_file)
    config_bytes, config_sha = _read_bytes_and_sha256(config_file)
    (ckpt_target / ckpt_file.name).write_bytes(ckpt_bytes)
    (ckpt_target / "config.yaml").write_bytes(config_bytes)

    out_path = out_dir / f"onnx_gate_{step}"

    # review #264 R20 P1 (stale ONNX candidates): 従来は `out_path`
    # （`--out-dir`/`--step` の組み合わせから決定論的に決まる固定パス）へ
    # 直接 export しており、同じ組み合わせを使い回す再実行（例: 5K -> 10K
    # checkpoint への差し替え）で `out_path` に前回実行の残置ファイルが
    # 混在し得た。この状態では下記の glob が「今回の subprocess が生成した
    # ファイル」と「過去の残置ファイル」を区別できず、exporter が exit 0
    # でも stale な非 alias ONNX を新 alias として採用してしまう窓があった
    # （summary の checkpoint/config ハッシュは新しいのに、実際に読み込む
    # acoustic.onnx だけ古いままという provenance 崩壊）。
    #
    # 修正: 呼び出しごとに一意な fresh staging ディレクトリへ export し、
    # 今回の invocation が生成したファイルのみを検査・alias 化してから、
    # `_swap_step_dir_into_place`（review #263 R5/R7/R8 P1/P2 で確立済みの
    # 2 段 rename + BaseException 巻き戻しパターン）で `out_path` へ原子的に
    # 差し替える。旧世代は削除されず `out_path.old` へ退避される
    # （swap 自体の意味論はそのまま流用）。staging 再利用時の残置物という
    # 概念自体を構造的に消す — glob 対象ディレクトリは常に空から始まる。
    out_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(dir=str(out_dir), prefix=f".onnx_gate_{step}.build-")
    )
    try:
        cmd = [
            sys.executable, "scripts/export.py", "acoustic",
            "--exp", exp_name,
            "--ckpt", str(step),
            "--out", str(staging_dir),
        ]
        print("| export cmd:", " ".join(cmd))
        subprocess.run(cmd, cwd=str(diffsinger_repo), check=True)

        # BUGFIX (5K gate 実測, 2026-08-15): scripts/export.py acoustic の実出力は
        # `acoustic.onnx` 固定名ではなく `<exp_name>.onnx`（+ freeze_spk 付与時は
        # `<exp_name>.<speaker>.onnx`）。1 個だけ *.onnx が見つかった場合に限り
        # `acoustic.onnx` へエイリアスコピーする（複数ある場合は決め打ちできない
        # ため何もせず後続の存在チェックで fail-closed に停止させる）。
        alias_path = staging_dir / "acoustic.onnx"
        onnx_candidates = sorted(
            p for p in staging_dir.glob("*.onnx") if p.resolve() != alias_path.resolve()
        )
        if len(onnx_candidates) == 1:
            if alias_path.exists() or alias_path.is_symlink():
                alias_path.unlink()
            shutil.copy2(onnx_candidates[0], alias_path)
            print(f"| aliased {onnx_candidates[0].name} -> acoustic.onnx "
                  f"(gate_synth.py naming-assumption bugfix)")
        elif len(onnx_candidates) > 1:
            # review #263 R8 P2: 非 alias *.onnx が複数存在する場合、今回の
            # export 由来のどれを採るか決め打ちできない。fresh staging
            # ディレクトリのため候補は必ず今回の invocation 由来だが、
            # それでも複数あれば曖昧なので fail-closed で停止する。
            names = ", ".join(p.name for p in onnx_candidates)
            raise RuntimeError(
                f"ambiguous export output in {staging_dir}: multiple non-alias "
                f"*.onnx candidates ({names}) found — cannot determine which "
                f"one this export produced. Refusing to fall back to any "
                f"pre-existing acoustic.onnx alias (fail-closed); clean "
                f"{staging_dir} and re-export."
            )
        if not alias_path.exists():
            raise RuntimeError(f"export.py did not produce {alias_path}")
    except BaseException:
        # exporter が期待物を生成しなかった／曖昧だった場合、staging を
        # 掃除して再送出する。`out_path`（前回世代があれば）には一切触れて
        # いないため、失敗時に stale な前回世代が誤って公開されることはない
        # （fail-closed）。
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    _swap_step_dir_into_place(staging_dir, out_path)
    return out_path, ckpt_sha, config_sha


# ============================================================================
# 4. 合成パイプライン本体（全曲・全モーラ対応）
# ============================================================================

def build_inputs(notes, frame_ms: float):
    real_phones = []
    note_phone_counts = []
    note_tones = []
    note_target_frames = []
    for note in notes:
        phs = mora_phonemes(note.mora)
        real_phones.extend(phs)
        note_phone_counts.append(len(phs))
        note_tones.append(float(note.midi))
        note_target_frames.append(frames_from_ms(note._dur_ms, frame_ms))
    vowel_flags = [p in VOWEL_SET for p in real_phones]
    return dict(
        real_phones=real_phones,
        note_phone_counts=note_phone_counts,
        note_tones=note_tones,
        note_target_frames=note_target_frames,
        is_vowel_flags=vowel_flags,
    )


class _NoteWithMs:
    """ScoreNote は sec 換算関数が song module 側にあるため、durations_ms を
    先に計算してから notes に付与するラッパ。"""

    def __init__(self, note, dur_ms: float):
        self.midi = note.midi
        self.mora = note.mora
        self._dur_ms = dur_ms


def run_pipeline(
    notes_raw,
    beats_to_seconds,
    tempo_bpm: float,
    model_bytes: Dict[str, bytes],
    variance_phonemes: Dict[str, int],
    acoustic_phonemes: Dict[str, int],
    record: dict,
    speaker_name: Optional[str] = None,
    speaker_embed_vector: Optional[np.ndarray] = None,
) -> np.ndarray:
    """`model_bytes` は `load_model_bundle_bytes` が 1 回だけ read したモデル束
    + dsconfig.yaml のバッファ（`canon_linguistic_onnx`/`canon_variance_dur_onnx`/
    `canon_variance_pitch_onnx`/`acoustic_onnx`/`vocoder_onnx`/
    `acoustic_dsconfig_yaml` の 6 key）。

    [P2 修正] (review #264 R12, gate_synth.py:1447) 従来はここで各モデル/
    config を path から個別に read しており、`collect_input_sha256` が
    hash 用に読む read とは別 read だった（TOCTOU: 差し替え→hash→
    元に戻す→ここで load、という順で差し替えられると、記録される
    pre-load hash と実際に推論へ使われたバイト列が食い違う。公開直前の
    pre/post 照合もこの窓を閉じない — 指摘の通り）。呼び出し側
    （`cmd_run`）が `load_model_bundle_bytes` で 1 回だけ read したバッファを
    そのまま `InferenceSession`/`yaml.safe_load` へ渡すことで、hash と
    load が同一バッファ由来であることを構造的に保証する
    （`_read_and_exec_module` と同型パターン）。
    """
    ort.set_seed(SEED)
    record["seed"] = SEED
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    providers = ["CPUExecutionProvider"]

    t_load0 = time.time()
    sess_linguistic = ort.InferenceSession(model_bytes["canon_linguistic_onnx"], sess_options=so, providers=providers)
    sess_dur = ort.InferenceSession(model_bytes["canon_variance_dur_onnx"], sess_options=so, providers=providers)
    sess_pitch = ort.InferenceSession(model_bytes["canon_variance_pitch_onnx"], sess_options=so, providers=providers)
    sess_acoustic = ort.InferenceSession(model_bytes["acoustic_onnx"], sess_options=so, providers=providers)
    sess_vocoder = ort.InferenceSession(model_bytes["vocoder_onnx"], sess_options=so, providers=providers)
    record["model_load_sec"] = time.time() - t_load0

    # acoustic.onnx の実際の入力名で経路を判定する（canon 単一話者 DDPM か、
    # S1 多話者 reflow か）。§docstring 冒頭「多話者 reflow acoustic 対応」参照。
    acoustic_input_names = {i.name for i in sess_acoustic.get_inputs()}
    is_reflow_multi_speaker = {"spk_embed", "steps"}.issubset(acoustic_input_names)
    record["stage3_mode"] = "reflow_multi_speaker" if is_reflow_multi_speaker else "canon_ddpm"

    dsconfig = yaml.safe_load(model_bytes["acoustic_dsconfig_yaml"].decode("utf-8"))
    hop_size = 512
    sample_rate = 44100
    frame_ms = 1000.0 * hop_size / sample_rate

    notes = [
        _NoteWithMs(n, beats_to_seconds(n.duration_beats, tempo_bpm) * 1000.0)
        for n in notes_raw
    ]
    built = build_inputs(notes, frame_ms)
    real_phones = built["real_phones"]
    note_phone_counts = built["note_phone_counts"]
    note_tones = built["note_tones"]
    note_target_frames = built["note_target_frames"]
    is_vowel_flags = built["is_vowel_flags"]
    n_real = len(real_phones)

    record["score"] = dict(
        n_notes=len(notes_raw),
        n_phonemes=n_real,
        tempo_bpm=tempo_bpm,
        real_phones=real_phones,
    )

    # --- Stage 1: duration predictor (variance 系、canon 符号化) ---
    t0 = time.time()
    v_tokens1 = [variance_phonemes["SP"]] + [variance_phonemes[p] for p in real_phones]
    word_div1 = [1] + note_phone_counts
    lead_frames = frames_from_ms(LEAD_PADDING_MS, frame_ms)
    word_dur1 = [lead_frames] + note_target_frames
    ph_midi1 = [note_tones[0]] + [t for t, c in zip(note_tones, note_phone_counts) for _ in range(c)]

    lin1_out = sess_linguistic.run(None, {
        "tokens": np.array([v_tokens1], dtype=np.int64),
        "word_div": np.array([word_div1], dtype=np.int64),
        "word_dur": np.array([word_dur1], dtype=np.int64),
    })
    lin1_names = [o.name for o in sess_linguistic.get_outputs()]
    encoder_out1 = lin1_out[lin1_names.index("encoder_out")]
    x_masks1 = lin1_out[lin1_names.index("x_masks")]

    dur_out = sess_dur.run(None, {
        "encoder_out": encoder_out1, "x_masks": x_masks1,
        "ph_midi": np.array([ph_midi1], dtype=np.int64),
    })
    dur_names = [o.name for o in sess_dur.get_outputs()]
    ph_dur_pred1 = dur_out[dur_names.index("ph_dur_pred")][0]

    final_phone_dur = []
    offset = 1
    for count, target in zip(note_phone_counts, note_target_frames):
        pred_slice = ph_dur_pred1[offset: offset + count]
        pred_sum = float(pred_slice.sum())
        if pred_sum <= 0:
            rescaled = [target / count] * count
        else:
            ratio = target / pred_sum
            rescaled = [float(x) * ratio for x in pred_slice]
        rounded = [int(round(x)) for x in rescaled]
        resid = target - sum(rounded)
        rounded[-1] += resid
        final_phone_dur.extend(rounded)
        offset += count
    assert len(final_phone_dur) == n_real
    assert sum(final_phone_dur) == sum(note_target_frames)
    record["stage1_elapsed_sec"] = time.time() - t0

    # --- Stage 2: pitch predictor (variance 系、canon 符号化) ---
    t0 = time.time()
    sp_idx_v = variance_phonemes["SP"]
    v_tokens2 = [sp_idx_v] + [variance_phonemes[p] for p in real_phones] + [sp_idx_v]
    ph_dur2 = [HEAD_FRAMES] + final_phone_dur + [TAIL_FRAMES]
    total_frames = int(sum(ph_dur2))
    word_div2, word_dur2 = padded_word_div_dur(ph_dur2, is_vowel_flags)

    lin2_out = sess_linguistic.run(None, {
        "tokens": np.array([v_tokens2], dtype=np.int64),
        "word_div": np.array([word_div2], dtype=np.int64),
        "word_dur": np.array([word_dur2], dtype=np.int64),
    })
    lin2_names = [o.name for o in sess_linguistic.get_outputs()]
    encoder_out2 = lin2_out[lin2_names.index("encoder_out")]

    note_midi2 = [note_tones[0]] + note_tones + [note_tones[-1]]
    note_dur_raw = [HEAD_FRAMES] + note_target_frames + [TAIL_FRAMES]
    note_dur2 = fit_duration_sum(note_dur_raw, total_frames)

    pitch_flat = np.full((1, total_frames), 60.0, dtype=np.float32)
    retake_flat = np.ones((1, total_frames), dtype=bool)
    pitch_speedup = np.array(max(1, 1000 // PITCH_STEPS), dtype=np.int64)

    pitch_out = sess_pitch.run(None, {
        "encoder_out": encoder_out2,
        "ph_dur": np.array([ph_dur2], dtype=np.int64),
        "note_midi": np.array([note_midi2], dtype=np.float32),
        "note_dur": np.array([note_dur2], dtype=np.int64),
        "pitch": pitch_flat,
        "retake": retake_flat,
        "speedup": pitch_speedup,
    })
    pitch_names = [o.name for o in sess_pitch.get_outputs()]
    pitch_pred = pitch_out[pitch_names.index("pitch_pred")][0]
    assert pitch_pred.shape[0] == total_frames
    record["stage2_elapsed_sec"] = time.time() - t0

    # --- Stage 3: acoustic model（★二重符号化ポイント★） ---
    # durations（ph_dur2）は並び順共有で流用可（ID 空間に依存しない、runbook §5.3-3）。
    # tokens だけ acoustic_phonemes（自前 or canon fallback）で再符号化する。
    t0 = time.time()
    sp_idx_a = acoustic_phonemes["SP"]
    a_tokens2 = [sp_idx_a] + [acoustic_phonemes[p] for p in real_phones] + [sp_idx_a]

    f0_hz = (440.0 * (2.0 ** ((pitch_pred - 69.0) / 12.0))).astype(np.float32)

    if is_reflow_multi_speaker:
        # --- reflow 多話者 acoustic（S1）: depth(float scalar)/steps(int64 scalar)/
        # spk_embed([1,n_frames,384]) 必須。根拠は docstring 冒頭・
        # `s1_5k_gate_record.md` 参照。
        if speaker_embed_vector is None:
            raise SystemExit(
                "ERROR: acoustic.onnx requires 'spk_embed' (S1 多話者 reflow "
                "モデル)だが、話者 embedding が読み込まれていない。"
                "--speaker {ritsu,pjs} が acoustic ディレクトリの "
                "*.<speaker>.emb と一致しているか確認すること（fail-closed）。"
            )
        # dsconfig の max_depth は既に 0-1 float（reflow exporter:
        # `dsconfig['max_depth'] = 1 - self.model.diffusion.t_start`）。
        # OpenUtau 既定と同じく Preferences 側の希望値 1.0 を max_depth でクランプする。
        max_depth = float(dsconfig.get("max_depth", 1.0))
        depth_value = min(1.0, max_depth)
        steps_value = REFLOW_SAMPLING_STEPS
        spk_embed_frames = np.tile(
            speaker_embed_vector.astype(np.float32).reshape(1, 1, -1),
            (1, total_frames, 1),
        )
        acoustic_out = sess_acoustic.run(None, {
            "tokens": np.array([a_tokens2], dtype=np.int64),
            "durations": np.array([ph_dur2], dtype=np.int64),
            "f0": f0_hz.reshape(1, -1),
            "spk_embed": spk_embed_frames,
            "depth": np.array(depth_value, dtype=np.float32),
            "steps": np.array(steps_value, dtype=np.int64),
        })
        record["stage3_speaker"] = speaker_name
        record["stage3_depth"] = depth_value
        record["stage3_steps"] = steps_value
        record["stage3_dsconfig_max_depth"] = max_depth
    else:
        max_depth_raw = float(dsconfig.get("max_depth", 1000))
        depth_float = max_depth_raw / 1000.0
        int64_depth = int(round(depth_float * 1000))
        acoustic_speedup = max(1, int64_depth // ACOUSTIC_STEPS)
        int64_depth = (int64_depth // acoustic_speedup) * acoustic_speedup

        acoustic_out = sess_acoustic.run(None, {
            "tokens": np.array([a_tokens2], dtype=np.int64),
            "durations": np.array([ph_dur2], dtype=np.int64),
            "f0": f0_hz.reshape(1, -1),
            "depth": np.array(int64_depth, dtype=np.int64),
            "speedup": np.array(acoustic_speedup, dtype=np.int64),
        })
        record["stage3_depth"] = int64_depth
        record["stage3_speedup"] = acoustic_speedup

    acoustic_names = [o.name for o in sess_acoustic.get_outputs()]
    mel = acoustic_out[acoustic_names.index("mel")]
    record["stage3_elapsed_sec"] = time.time() - t0
    record["stage3_tokens_variance"] = v_tokens2
    record["stage3_tokens_acoustic"] = a_tokens2
    record["stage3_dual_encoding_diverged"] = (v_tokens2 != a_tokens2)

    # --- Stage 4: vocoder ---
    t0 = time.time()
    vocoder_out = sess_vocoder.run(None, {"mel": mel.astype(np.float32), "f0": f0_hz.reshape(1, -1)})
    vocoder_names = [o.name for o in sess_vocoder.get_outputs()]
    waveform = vocoder_out[vocoder_names.index("waveform")]
    y = np.asarray(waveform, dtype=np.float64).reshape(-1)
    record["stage4_elapsed_sec"] = time.time() - t0
    return y


def synth_song(
    song: str,
    notes_limit: Optional[int],
    build_fn: Callable,
    beats_to_seconds: Callable,
    tempo_bpm: float,
    model_bytes: Dict[str, bytes],
    variance_phonemes: Dict[str, int],
    acoustic_phonemes: Dict[str, int],
    out_dir: Path,
    speaker_name: Optional[str] = None,
    speaker_embed_vector: Optional[np.ndarray] = None,
    final_out_dir: Optional[Path] = None,
) -> dict:
    # [P2 修正] (review #264 R6) 従来は `singer_dir` を受け取りここで
    # `load_song_module` を再び呼んでいた（`cmd_run` が provenance pin 用に
    # 既に 1 回読み込み済みの score モジュールを、ここでもう一度別 read で
    # ロードし直す — TOCTOU 窓を増やす redundant reload）。呼び出し元
    # （`cmd_run`）が `load_song_module` で 1 回だけ読み込んだ `build_fn` /
    # `beats_to_seconds` / `tempo_bpm` をそのまま受け取ることで、song モジュール
    # のファイル read はプロセス全体を通じて 1 回のみになる。
    notes_raw = build_fn()
    if notes_limit is not None:
        notes_raw = notes_raw[:notes_limit]

    record: dict = {"song": song, "notes_limit": notes_limit}
    t_total0 = time.time()
    y = run_pipeline(
        notes_raw, beats_to_seconds, tempo_bpm,
        model_bytes,
        variance_phonemes, acoustic_phonemes, record,
        speaker_name=speaker_name, speaker_embed_vector=speaker_embed_vector,
    )
    record["total_elapsed_sec"] = time.time() - t_total0

    peak_raw = float(np.max(np.abs(y))) if y.size else 0.0
    rms_raw = float(np.sqrt(np.mean(y ** 2))) if y.size else 0.0
    record["wav_peak_raw"] = peak_raw
    record["wav_rms_raw"] = rms_raw
    record["wav_duration_sec"] = y.shape[0] / 44100.0

    if peak_raw > 0:
        y = y / peak_raw * 0.6
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms = float(np.sqrt(np.mean(y ** 2))) if y.size else 0.0
    record["wav_peak"] = peak
    record["wav_rms"] = rms

    suffix = f"_n{notes_limit}" if notes_limit is not None else ""
    # reflow 多話者経路のみファイル名に話者を含める（canon 経路は既存命名を維持
    # — S0 回帰 sha256 が canon 出力ファイル名に依存しないよう互換性を壊さない）。
    speaker_suffix = f"_{speaker_name}" if record.get("stage3_mode") == "reflow_multi_speaker" and speaker_name else ""
    out_name = f"gate_{song}{speaker_suffix}{suffix}.wav"
    out_path = out_dir / out_name  # 実際の書き込み先（build_dir 配下、swap 前）
    out_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), y.astype(np.float32), 44100, subtype="PCM_16")
    wav_bytes = out_path.read_bytes()
    record["wav_sha256"] = hashlib.sha256(wav_bytes).hexdigest()

    # R6 レビュー指摘 (PR #263, gate_synth.py:879, P2): record 内の wav_path は
    # swap 後に利用者が参照する最終パスをここで直接記載する（build_dir 配下の
    # 一時パスを書いてから swap 後に文字列置換で書き換える方式は、「公開 = 完成
    # 済み束の rename のみ・公開済み世代への追記/書換ゼロ」という原則に反する
    # ため廃止した）。final_out_dir 未指定（--skip-export の S0 互換検証パス等、
    # 呼び出し側が swap 自体を行わない場合）は out_dir をそのまま最終パスとみなす。
    final_dir = final_out_dir if final_out_dir is not None else out_dir
    record["wav_path"] = str(final_dir / out_name)

    record_path = out_dir / (out_name.replace(".wav", "") + "_record.json")
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R9): `cmd_run` の公開先（`synth_out_dir` および
    `_swap_step_dir_into_place` が実際に削除・rename する派生パス
    `<synth_out_dir>.old`/`<synth_out_dir>.build-<pid>`）が、resolve 済みの
    モデル入力（`--acoustic-dir`/`--canon-model-dir`/`--vocoder-dir`/
    `--ckpt-dir`）と衝突する場合に送出する（fail-closed。公開前 preflight
    で検出する）。`convert_pjs.py`/`convert_ritsu.py` の `OutputCollisionError`
    と同型判定（record スクリプト群の既存慣例に倣い、共有モジュール新設
    ではなく各ファイル内へコピペ実装）。

    `--step` を省略した `--skip-export` の S0 互換検証パスでは
    `synth_out_dir == out_dir` になるが、`--acoustic-dir`/`--ckpt-dir` を
    使い回して過去の export 成果物を `--out-dir` 配下に置いたまま同じ木を
    `--out-dir` に再指定すると、`_swap_step_dir_into_place` の rename が
    既に読み込み済みのモデル束を `.old` へ退避し、次回実行時にその `.old`
    が rmtree される（モデル束の消失）。

    review #263 R14 P1: モデル重み（onnx/ckpt）だけでなく、`--singer-dir`
    （score.py/score_umi.py の実装ルート）や `gate_synth.py` 自身の親
    ディレクトリも `--out-dir` に指定され得る load 済み実装ルートであり、
    同様に `.old` 退避 -> 次回 rmtree で消失し得るため保護対象に含める。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """`out_paths`（resolve 後）を相互および `protected_roots`（存在する
    もののみ、resolve 後）と照合し、衝突があれば公開前に fail-closed で
    拒否する（`convert_pjs.py`/`convert_ritsu.py` の同名ヘルパーと同一の
    resolved 比較ロジック。双方向の内包判定を含む）。
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


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """staging tempfile へ書き込み、成功後にのみ `os.replace` で `path` へ
    atomic 公開する（`s1_dataprep/build_dataset.py` `_atomic_write_text` /
    `adapter/donor_bank.py` `_atomic_stage_and_replace` と同じ流儀）。

    review #264 R15 追い掃討 (PR #264, gate_synth.py:1718, P2):
    `cmd_mapping_check` の従来実装は `os.getpid()` を tmp ファイル名に含める
    決定論的パスへ直接 `write_text` していたため、(a) 同一プロセス内で同じ
    `--out` に対し複数回呼ばれると tmp パスが衝突し得る、(b)
    `write_text`（内部 `open()`）は `O_CREAT` のみで既存ファイルを黙って
    truncate するため、シンボリックリンク経由の攻撃や他プロセスが同名 tmp
    を残していた場合に排他性がない。`tempfile.mkstemp`（`O_CREAT | O_EXCL`
    で一意生成保証）+ 失敗時 tmp 削除 + `os.replace` の原子的差し替えへ
    是正（`s1_dataprep`/`recording_kit`/`adapter` 各所の同型ヘルパーと同じ
    パターン。依存方向の不自然さを避けるため、共有モジュール化ではなく
    本ファイル内へコピペ実装する既存慣例に倣う。上の `OutputCollisionError`
    docstring 参照）。
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


def _swap_step_dir_into_place(build_dir: Path, out_dir: Path) -> None:
    """`build_dir`（全曲の wav/record + summary を完全に構築済み）を
    `out_dir`（`step_<N>/` 等の合成成果物ディレクトリ）へ原子的に差し替える
    （review #263 R5 P2）。

    途中失敗時の新旧世代混在防止: step ディレクトリへ曲ごとに直接書き込むと、
    複数曲合成中の 1 曲目以降で失敗した場合に部分的な wav/summary が残り、
    「どの曲がどの checkpoint 由来か」が判別不能な gate 判定証跡になる。
    全曲 + summary を fresh な一時ディレクトリへ完全に生成してから、成功時
    のみ atomic に `out_dir` と swap する（`convert_pjs.py`/`convert_ritsu.py`
    の R3 P1 と同型パターン。POSIX の `rename(2)` はディレクトリの置換を
    アトミックに行う）。旧世代は削除せず `<out_dir>.old` へ退避する。

    review #263 R7 P2: 2 段 rename は独立した 2 操作のため、旧世代の退避
    （`out_dir` -> `.old`）が成功した後に新世代の rename（`build_dir` ->
    `out_dir`）が失敗（`KeyboardInterrupt` 含む）すると、正規パス `out_dir`
    が消失したまま旧世代は `.old` にしか存在しない状態になる（直前まで有効
    だった成果物が canonical パスから見えなくなる）。新世代 rename を
    `except BaseException` で保護し、失敗時は退避済みの旧世代を `out_dir`
    へ復元してから再送出する。旧世代が存在しなかった場合（初回実行等）は
    復元対象が無いためそのまま再送出する。

    review #263 R8 P2: R7 の保護は新世代 rename のみを try で囲んでいたため、
    退避 rename（`out_dir` -> `.old`）が完了した直後・try 進入前にも中断窓
    （`KeyboardInterrupt` 等）が残っていた。加えて、退避完了の判定を
    `evicted_old = True` という後続代入で行っていたため、`rename(2)` 自体は
    成功しているのに（Python の呼び出しフレームへ制御が戻る前に割り込みが
    入るなどして）その代入文自体が実行されない極めて狭い窓も理論上残る。
    退避 rename から公開 rename までの遷移全体を単一の try/except
    BaseException で覆うと同時に、「退避が完了したか」の判定をフラグ変数
    ではなく `old_dir.exists()` という実ファイルシステム状態の観測へ置き換
    える（このメソッド冒頭で `.old` を消去済みのため、except 到達時点で
    `old_dir` が存在するのは今回の退避 rename が成功した場合のみであり、
    フラグの代入タイミングに依存しない）。これにより両方の中断窓を閉じる。
    """
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    try:
        if out_dir.exists():
            out_dir.rename(old_dir)
        build_dir.rename(out_dir)
    except BaseException:
        if old_dir.exists() and not out_dir.exists():
            old_dir.rename(out_dir)
        raise


# ============================================================================
# 5. CLI
# ============================================================================

def cmd_run(args):
    canon_model_dir = Path(args.canon_model_dir)
    vocoder_dir = Path(args.vocoder_dir)
    # BUGFIX (5K gate 実測, 2026-08-15): --out-dir を相対パスのまま run_export_acoustic
    # に渡すと、subprocess.run(cmd, cwd=diffsinger_repo) の cwd 基準で解決されてしまい
    # 意図した出力先に書かれない。resolve() で絶対パス化してから使う。
    out_dir = Path(args.out_dir).resolve()
    singer_dir = Path(args.singer_dir) if args.singer_dir else DEFAULT_SINGER_DIR
    # [P2 修正] (review #264 R9, gate_synth.py:1168) パースに使うバッファと
    # provenance pin 用ハッシュのバッファを同一 read から得る
    # （`_read_bytes_and_sha256` 参照。従来は下記 `collect_input_sha256` が
    # 別 read で `sha256_file()` していた）。
    variance_phonemes, canon_phonemes_txt_sha = load_canon_phonemes_with_sha(
        canon_model_dir / "phonemes.txt"
    )

    # R3 レビュー指摘 (PR #263, gate_synth.py:544): 5K/10K/20K が同じ --out-dir を
    # 使い回すと gate_<song>.wav / summary が上書きされ、前段ゲートの判定証跡が
    # 破壊される。--step 指定時は成果物を out_dir/step_<N>/ 配下に分離する
    # （--skip-export の S0 互換検証パスは step 未指定のままなので out_dir 直下 = 無変更）。
    synth_out_dir = (out_dir / f"step_{args.step}") if args.step is not None else out_dir

    # R5 レビュー指摘 (PR #263, gate_synth.py:779, P2): 曲ごとに synth_out_dir
    # へ直接書き込むと、複数曲合成中の 1 曲目以降で失敗した場合に部分的な
    # wav/summary が新旧世代で混在する。全曲 + summary を fresh な一時
    # ディレクトリへ完全に生成してから、成功時のみ atomic に synth_out_dir
    # と swap する（`_swap_step_dir_into_place`）。build_dir/old_dir はここで
    # 前倒しして算出し、下記 R9 preflight のガード対象に含める。
    build_dir = synth_out_dir.parent / f"{synth_out_dir.name}.build-{os.getpid()}"
    old_dir = synth_out_dir.parent / f"{synth_out_dir.name}.old"

    # R9 レビュー指摘 (PR #263, gate_synth.py:778, P1): `--skip-export` 省略時
    # に `synth_out_dir` が `--acoustic-dir`/`--canon-model-dir`/
    # `--vocoder-dir`/`--ckpt-dir` のいずれかと同じ（または内包関係にある）
    # ディレクトリに指定されると、既にロード済みのモデル束が
    # `_swap_step_dir_into_place` の rename で `.old` へ退避され gate 成果物に
    # 差し替わり、次回実行時にその `.old` が rmtree されてモデル束が消失する。
    # resolve 済みの全モデル入力を、公開（swap）を始める前に `synth_out_dir`
    # 本体・その派生パス（`.old`/`.build-<pid>`）・`out_dir`（export 自体が
    # 直接書き込む先）に対して衝突拒否する。
    #
    # R14 レビュー指摘 (PR #263, gate_synth.py:955, P1): 上記はモデル重み
    # （onnx/ckpt）のみを保護しており、`--skip-export` かつ `--step` 省略の
    # S0 互換検証パスでは `--singer-dir`（score.py/score_umi.py の実装
    # ルート。`load_song_module` が本体をロードする）や本スクリプト自身の
    # 親ディレクトリ（`gate_synth.py` 自体もこの後 `sha256_file(__file__)`
    # で読み直す）が衝突ガード対象外だった。`--out-dir` をこれらへ指定すると
    # synth_out_dir==out_dir のまま preflight を素通りし、合成完了後に
    # `_swap_step_dir_into_place` がスコア実装/本スクリプトの所在ディレクトリ
    # を gate 成果物へ差し替え、次回実行時の `.old` rmtree で実装コード
    # そのものが消失し得る。load する実装ルート（`singer_dir` と
    # `Path(__file__).resolve().parent`）も保護対象へ追加する。
    protected_model_roots: List[Path] = [
        canon_model_dir, vocoder_dir, singer_dir, Path(__file__).resolve().parent,
    ]
    if args.skip_export:
        protected_model_roots.append(Path(args.acoustic_dir))
    elif args.ckpt_dir:
        protected_model_roots.append(Path(args.ckpt_dir))
    guarded_publish_paths: List[Path] = [synth_out_dir, old_dir, build_dir]
    if out_dir.resolve() != synth_out_dir.resolve():
        guarded_publish_paths.append(out_dir)
    try:
        _reject_output_collision(guarded_publish_paths, protected_roots=protected_model_roots)
    except OutputCollisionError as exc:
        raise SystemExit(f"error: {exc}")

    ckpt_sha: Optional[str] = None
    train_config_sha: Optional[str] = None
    if args.skip_export:
        acoustic_dir = Path(args.acoustic_dir)
        acoustic_onnx_path = acoustic_dir / "acoustic.onnx"
        acoustic_dsconfig_path = acoustic_dir / "dsconfig.yaml"
        if not acoustic_dsconfig_path.exists():
            # canon 配布 zip はトップレベルにも dsconfig.yaml を持つ
            acoustic_dsconfig_path = canon_model_dir / "dsconfig.yaml"
    else:
        exported_dir, ckpt_sha, train_config_sha = run_export_acoustic(
            Path(args.diffsinger_repo), Path(args.ckpt_dir), args.exp_name, args.step, out_dir,
        )
        acoustic_dir = exported_dir
        acoustic_onnx_path = exported_dir / "acoustic.onnx"
        acoustic_dsconfig_path = exported_dir / "dsconfig.yaml"
        if not acoustic_dsconfig_path.exists():
            acoustic_dsconfig_path = canon_model_dir / "dsconfig.yaml"

    # review #263 R12 P1: 複数候補（*.phonemes.json / *.<speaker>.emb）の対応
    # 付けに使う export basename を先に推定する（`acoustic_export_basename`
    # docstring 参照）。
    export_basename = acoustic_export_basename(acoustic_dir, acoustic_onnx_path)
    own_json = find_own_phonemes_json(acoustic_dir, export_basename)

    # 話者 embedding（reflow 多話者 acoustic 用）。canon DDPM acoustic には
    # 対応する *.emb が存在しないため speaker_embed_path/vector は None のままで、
    # run_pipeline 側が acoustic.onnx の入力名から不要と判定して無視する。
    speaker_embed_path = (
        find_speaker_embed(acoustic_dir, args.speaker, export_basename) if args.speaker else None
    )
    # [P2 修正] (review #264 R9, gate_synth.py:1168) ロードに使うバッファと
    # provenance pin 用ハッシュのバッファを同一 read から得る（`_read_bytes_
    # and_sha256` 参照）。
    speaker_embed_vector = None
    speaker_embed_sha: Optional[str] = None
    if speaker_embed_path is not None:
        speaker_embed_vector, speaker_embed_sha = load_speaker_embed_vector_with_sha(
            speaker_embed_path
        )
    if args.speaker and speaker_embed_path is not None:
        print(f"| speaker embed: {speaker_embed_path} ({speaker_embed_vector.shape[0]}-dim)")

    # [P2 修正] (review #264 R9, gate_synth.py:1168) own_json が実際にパース
    # されて acoustic_phonemes を構築した場合のみ、そのパースに使ったバッファ
    # の sha256 を記録する（`--tokens canon` で own_json が存在しても無視され
    # るケースは「パースされたバッファ」が存在しないため None のまま —
    # collect_input_sha256 側が sha256_file() による従来の別 read へ
    # フォールバックする）。
    own_json_sha: Optional[str] = None
    if args.tokens == "canon":
        # 明示的な S0 互換検証専用パス。事故で本番に紛れ込まないよう、
        # own_json が存在するのに --tokens canon を指定した場合も警告する
        # （「本物の自前語彙があるのに意図的に無視している」ことを可視化する）。
        acoustic_phonemes = variance_phonemes
        encoding_mode = "canon (--tokens canon, explicit S0-compat verification mode)"
        if own_json is not None:
            print(f"| WARNING: --tokens canon specified but {own_json} exists — "
                  f"ignoring it and using canon encoding anyway (verification-only path).")
    else:
        # 既定 (--tokens own): fail-closed。*.phonemes.json が無ければ
        # 「本番の自前語彙が未到着」とみなしここで停止する（runbook §5.3 が警告する
        # 「クラッシュしないが誤った音素へ着地する」サイレント不整合を未然に防ぐ。
        # S0 互換検証で canon acoustic.onnx を差し替え対象と見立てる場合は
        # --tokens canon を明示指定すること）。
        if own_json is None:
            raise SystemExit(
                f"ERROR: no *.phonemes.json found next to acoustic.onnx in '{acoustic_dir}'.\n"
                f"  --tokens own (既定) は自前 acoustic 語彙が届いていることを前提とする "
                f"fail-closed モードのため、ここで停止する（canon 符号化への暗黙フォール"
                f"バックはしない — runbook §5.3 のサイレント不整合を防ぐため）。\n"
                f"  S0 互換検証（canon acoustic.onnx をそのまま使う）が目的なら "
                f"--tokens canon を明示指定すること。"
            )
        acoustic_phonemes, own_json_sha = load_own_phonemes_json_with_sha(own_json)
        encoding_mode = f"own ({own_json.name})"
    print(f"| acoustic token encoding: {encoding_mode}")

    # R3 レビュー指摘 (PR #263, gate_synth.py:616): run_pipeline が実際に load する
    # dsconfig.yaml と canon phonemes.txt も入力 sha256 に含める（+ speaker_embed）。
    # [P2 修正] (review #264 R9, gate_synth.py:1168) canon_phonemes_txt_sha/
    # own_json_sha/speaker_embed_sha は上記で実際にパース/ロードしたバッファ
    # から得た sha256（未パースの場合は None、内部で従来の別 read にフォール
    # バック）。
    #
    # [P2 修正] (review #264 R12, gate_synth.py:1447) acoustic_onnx / canon 各
    # onnx / vocoder_onnx / dsconfig.yaml は、従来は「hash 用の sha256_file()
    # 別 read」と「run_pipeline 内の InferenceSession/yaml.safe_load 用の別
    # read」に分かれており、両 read の間の差し替えを公開直前の pre/post 照合
    # だけでは検出できなかった（指摘の通り）。`load_model_bundle_bytes` で
    # ここで 1 回だけ read したバッファを model_shas として pin し、同じ
    # バッファ（model_bytes）を下記ループの `synth_song`/`run_pipeline` へ
    # そのまま渡す（hash と load が同一バッファ由来であることを構造的に
    # 保証。score モジュールの `_read_and_exec_module` と同型）。長時間の
    # 合成中に on-disk 実装が書き換えられていないかは、従来通り合成完了後・
    # 公開直前に再ハッシュして pre-load hash と突き合わせる belt を残す
    # （下記「モデル/config 束の事後照合」参照）。
    model_bytes, model_shas = load_model_bundle_bytes(
        canon_model_dir, vocoder_dir, acoustic_onnx_path, acoustic_dsconfig_path,
    )
    input_sha256 = collect_input_sha256(
        args, canon_model_dir, vocoder_dir, acoustic_onnx_path, acoustic_dsconfig_path,
        own_json, speaker_embed_path,
        canon_phonemes_txt_sha=canon_phonemes_txt_sha,
        own_json_sha=own_json_sha,
        speaker_embed_sha=speaker_embed_sha,
        model_shas=model_shas,
        ckpt_sha=ckpt_sha,
        train_config_sha=train_config_sha,
    )

    # R6 レビュー指摘 (PR #263, gate_synth.py:804, P2): 上記はモデル/config 束の
    # sha256 のみで、合成パイプライン自体を定義する gate_synth.py 本体や
    # load_song_module が実際に import する score モジュール（score.py /
    # score_umi.py 等）が変更されても input_sha256 は不変のまま — 出力 WAV が
    # 変わっても「どの実装から出たか」が入力側 pin から追えなくなる。合成実装側
    # の sha256 も input_sha256 へ追加する（実行中の本スクリプト自身・実際に
    # load した score モジュール・可能ならリポの HEAD commit）。
    #
    # [P2 修正] (review #263 R16) import 前にファイルパスを確定 sha256 化して
    # から import することで pin を import 直前の内容へ固定する（load 時 pin）。
    # さらに合成完了後に同じパスを再ハッシュし、不一致なら公開
    # （`_swap_step_dir_into_place`）前に fail-closed で止める（事後照合。
    # 長時間走る合成の最中に score モジュールが書き換えられて「記録された pin
    # と実際に使われた実装が食い違ったまま公開される」事故を防ぐ）。
    #
    # [P2 修正] (review #264 R6) 従来は「hash 用に `sha256_file()` で読む read」
    # と「`load_song_module`（内部の `_exec_module_from_source`）が exec 用に
    # 読む read」が別 read だったため、両者の間にファイルが差し替えられると
    # 記録した pin と実際に exec された内容が食い違い得た（TOCTOU）。さらに
    # そのあと `synth_song` 内でも同じモジュールをもう一度 `load_song_module`
    # で読み直しており（redundant reload）、read 回数・TOCTOU 窓の両方が
    # 不必要に多かった。`load_song_module` 自身が「1 回だけ read_bytes() した
    # バッファをハッシュにも compile/exec にも使う」ように統合された
    # （`_read_and_exec_module` 参照）ため、ここでは `load_song_module` を
    # song ごとに 1 回だけ呼び、返ってきた `module_shas`（exec に実際使った
    # バッファの sha256）をそのまま `input_sha256` へ転記し、`build_fn` 等は
    # `synth_song` へそのまま渡す（`synth_song` 側の再ロードを廃止）。
    #
    # [P2 修正] (review #264 R10, gate_synth.py:1200) ここで `sha256_file()`
    # による再読み込みは行わず、モジュール実行開始直後に確定済みの
    # `_GATE_SYNTH_PY_LOAD_TIME_SHA256`（ファイル先頭のコメント参照）を
    # そのまま転記する。公開直前の事後照合は下記「gate_synth.py 自身の
    # 事後照合」ブロックで行う。
    input_sha256["gate_synth_py"] = _GATE_SYNTH_PY_LOAD_TIME_SHA256
    score_module_paths: Dict[str, Path] = {}
    dependency_module_paths: Dict[str, Path] = {}
    song_modules: Dict[str, Tuple[Callable, Callable, float]] = {}
    for song_name in args.song.split(","):
        build_fn, beats_to_seconds, tempo_bpm, module_path, module_shas = load_song_module(
            song_name, singer_dir
        )
        song_modules[song_name] = (build_fn, beats_to_seconds, tempo_bpm)
        score_module_paths[song_name] = module_path
        main_pin_key = f"score_module_{song_name}"
        for pin_key, (pinned_path, pinned_sha) in module_shas.items():
            input_sha256[pin_key] = pinned_sha
            if pin_key != main_pin_key:
                dependency_module_paths[pin_key] = pinned_path
    try:
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        input_sha256["repo_git_head"] = git_head
    except Exception:
        pass  # 取得失敗時は省略する（キー無し。fail-closed にはしない）

    # [P2 修正] (review #264 R20) 上記 `repo_git_head` は gate_synth.py 自身が
    # 属する ugh リポの HEAD であり、非 `--skip-export` 経路で実際に
    # `scripts/export.py` を実行した `args.diffsinger_repo` checkout の pin
    # ではなかった（export の実装は DiffSinger 側リポにあり、そちらの
    # checkout がどのコミット・作業ツリー状態かで生成される acoustic.onnx が
    # 変わり得るのに、summary からは追跡できなかった）。export 実行時
    # （`--skip-export` 経路では export 自体を行わないため対象外 — 既存
    # summary 構造は変えず、キー自体を省く）に限り
    # `git -C <diffsinger_repo> rev-parse HEAD` / `git status --porcelain`
    # を取得し、`diffsinger_repo_git_head`/`diffsinger_repo_dirty` として
    # 記録する。git リポでない・取得失敗の場合は fail-closed にはせず
    # （export 自体の既存挙動は変えない）、代わりに明示の `"unavailable"`
    # をそのまま summary へ記録して「pin が取れなかったこと」自体を正直に
    # 可視化する（サイレントにキーを省いて「未検査」を「pin 済み」と区別
    # できなくしない）。
    if not args.skip_export:
        diffsinger_repo_path = Path(args.diffsinger_repo)
        try:
            diffsinger_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(diffsinger_repo_path),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            dirty_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(diffsinger_repo_path),
                capture_output=True, text=True, check=True,
            ).stdout
            input_sha256["diffsinger_repo_git_head"] = diffsinger_head
            input_sha256["diffsinger_repo_dirty"] = bool(dirty_status.strip())
        except Exception:
            input_sha256["diffsinger_repo_git_head"] = "unavailable"
            input_sha256["diffsinger_repo_dirty"] = "unavailable"

    # synth_out_dir/build_dir/old_dir は cmd_run 冒頭（R9 preflight）で算出済み。
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    swapped = False
    try:
        songs = args.song.split(",")
        results = {}
        for song in songs:
            print(f"| synthesizing: {song} (notes_limit={args.notes_limit}, speaker={args.speaker})")
            build_fn, beats_to_seconds, tempo_bpm = song_modules[song]
            rec = synth_song(
                song, args.notes_limit, build_fn, beats_to_seconds, tempo_bpm,
                model_bytes,
                variance_phonemes, acoustic_phonemes, build_dir,
                speaker_name=args.speaker, speaker_embed_vector=speaker_embed_vector,
                final_out_dir=synth_out_dir,
            )
            # rec["wav_path"] は synth_song 内で既に最終パス（synth_out_dir 配下）
            # として記載済み（R6 P2 修正）。ここでの書き換えは不要。
            results[song] = dict(
                wav_path=rec["wav_path"], wav_sha256=rec["wav_sha256"],
                wav_duration_sec=rec["wav_duration_sec"], wav_rms=rec["wav_rms"],
                wav_peak=rec["wav_peak"], n_phonemes=rec["score"]["n_phonemes"],
                dual_encoding_diverged=rec["stage3_dual_encoding_diverged"],
                stage3_mode=rec["stage3_mode"],
                stage3_depth=rec.get("stage3_depth"),
                stage3_steps=rec.get("stage3_steps"),
                stage3_speaker=rec.get("stage3_speaker"),
            )
            print(f"|   sha256={rec['wav_sha256']} dur={rec['wav_duration_sec']:.3f}s "
                  f"rms={rec['wav_rms']:.4f} diverged={rec['stage3_dual_encoding_diverged']} "
                  f"mode={rec['stage3_mode']}")

        # [P2 修正] (review #263 R16) 事後照合: `load_song_module` が exec に
        # 実際使ったバッファの sha256（`input_sha256[f"score_module_{song}"]`。
        # review #264 R6 により「hash した内容」と「exec した内容」は同一
        # read から得られる構造的保証がある）を、合成完了後（公開直前）に
        # ディスクを再読み込みして再計算した sha256 と突き合わせる。長時間の
        # 合成中に score モジュールが書き換えられていた場合、記録済み pin と
        # ディスク上の現在の実装が食い違うため、公開（`_swap_step_dir_into_place`）
        # 前に fail-closed で止める。
        for song_name, module_path in score_module_paths.items():
            recorded_sha = input_sha256[f"score_module_{song_name}"]
            current_sha = sha256_file(module_path)
            if current_sha != recorded_sha:
                raise SystemExit(
                    f"ERROR: score module '{module_path}' changed during synthesis "
                    f"(pinned sha256={recorded_sha} — sha256 of the buffer actually "
                    f"compiled/exec'd at load time — now={current_sha}). "
                    f"Refusing to publish a summary whose provenance pin no longer "
                    f"matches the on-disk implementation."
                )
        # [P2 修正] (review #264 R2) 依存モジュール（score.py/phoneme_jp.py）
        # も song モジュール本体と同じ事後照合の対象にする。
        for pin_key, dep_path in dependency_module_paths.items():
            recorded_sha = input_sha256[pin_key]
            current_sha = sha256_file(dep_path) if dep_path.exists() else None
            if current_sha != recorded_sha:
                raise SystemExit(
                    f"ERROR: score module dependency '{dep_path}' changed during "
                    f"synthesis (pinned sha256={recorded_sha} — sha256 of the buffer "
                    f"actually compiled/exec'd at load time — now={current_sha}). "
                    f"Refusing to publish a summary whose provenance pin no longer "
                    f"matches the on-disk implementation."
                )

        # [P2 修正] (review #264 R10, gate_synth.py:1200) gate_synth.py 自身
        # の事後照合: モジュール実行開始直後に固定した `_GATE_SYNTH_PY_
        # LOAD_TIME_SHA256`（ファイル先頭のコメント参照）を、合成完了後・
        # 公開（`_swap_step_dir_into_place`）直前に同じパスを再読み込みして
        # 突き合わせる。長時間の合成中に本スクリプト自身が書き換えられて
        # いた場合、記録済み pin（プロセス起動時点の実装）とディスク上の
        # 現在の実装が食い違うため fail-closed で止める（score モジュールと
        # 同じ pre+post 二段方式）。
        gate_synth_py_current_sha = sha256_file(_GATE_SYNTH_PY_PATH)
        if gate_synth_py_current_sha != _GATE_SYNTH_PY_LOAD_TIME_SHA256:
            raise SystemExit(
                f"ERROR: gate_synth.py itself changed during synthesis "
                f"(pinned sha256={_GATE_SYNTH_PY_LOAD_TIME_SHA256} — sha256 captured "
                f"at module load time — now={gate_synth_py_current_sha}). Refusing to "
                f"publish a summary whose provenance pin no longer matches the "
                f"on-disk implementation that (may have) run."
            )

        # [P2 修正] (review #264 R9, gate_synth.py:1168; R12, gate_synth.py:1447)
        # モデル/config 束の事後照合: `run_pipeline`（`synth_song` 経由で上記
        # ループ内で既に呼ばれている）が実際に open した onnx モデル群と
        # `acoustic_dsconfig_path` は、`load_model_bundle_bytes` が 1 回だけ
        # read したバッファ（`model_bytes`）を `InferenceSession`/
        # `yaml.safe_load` にそのまま渡して使用済みで、その同一バッファの
        # sha256（`model_shas`）が `input_sha256` へ既に記録されている
        # （R12 対応: hash と load が同一 read 由来であることが構造的に保証
        # されるため、ここより前の TOCTOU 窓は既に閉じている）。以下の再読み込み
        # + 突き合わせは、長時間走る合成の最中に on-disk の実装/モデル資材が
        # 書き換えられていないかを検出する belt（score modules と同じ
        # pre+post 二段方式）であり、不一致（差し替え）または消失を検出したら
        # 合成完了後・公開（`_swap_step_dir_into_place`）前に fail-closed で
        # 止める。
        model_config_paths: Dict[str, Path] = {
            "canon_linguistic_onnx": canon_model_dir / "linguistic.onnx",
            "canon_variance_dur_onnx": canon_model_dir / "dsdur" / "dur.onnx",
            "canon_variance_pitch_onnx": canon_model_dir / "dspitch" / "pitch.onnx",
            "acoustic_onnx": acoustic_onnx_path,
            "vocoder_onnx": vocoder_dir / "nsf_hifigan.onnx",
            "acoustic_dsconfig_yaml": acoustic_dsconfig_path,
        }
        for pin_key, model_path in model_config_paths.items():
            recorded_sha = input_sha256.get(pin_key)
            if recorded_sha is None:
                # pre-load hash 取得時点で存在しなかった（= 今回の実行で使わ
                # れなかった）入力。事後照合の対象外。
                continue
            if not model_path.exists():
                raise SystemExit(
                    f"ERROR: model/config input '{model_path}' (pin_key={pin_key}) "
                    f"went missing during synthesis (pre-load pinned sha256="
                    f"{recorded_sha}). Refusing to publish a summary whose "
                    f"provenance pin no longer matches an existing on-disk input."
                )
            current_sha = sha256_file(model_path)
            if current_sha != recorded_sha:
                raise SystemExit(
                    f"ERROR: model/config input '{model_path}' (pin_key={pin_key}) "
                    f"changed during synthesis (pre-load pinned sha256={recorded_sha} "
                    f"— now={current_sha}). Refusing to publish a summary whose "
                    f"provenance pin no longer matches the on-disk model/config "
                    f"actually used by run_pipeline."
                )

        summary_path = build_dir / "gate_synth_summary.json"
        summary_path.write_text(json.dumps({
            "step": args.step,
            "acoustic_encoding_mode": encoding_mode,
            "acoustic_onnx_path": str(acoustic_onnx_path),
            "input_sha256": input_sha256,
            # (review #264 R9, gate_synth.py:1168; R12, gate_synth.py:1447)
            # input_sha256 の各 key がどの方式で「実際に使われたバイト」で
            # あることを保証しているかの注記。score_module_* / *_dep_* は
            # review #264 R6 の単一 read 方式と同様の構造。onnx/dsconfig 系は
            # R12 で load_model_bundle_bytes による単一 read 方式へ移行済み
            # （hash した buffer をそのまま InferenceSession/yaml.safe_load に
            # 渡す）。pre-publish re-hash は on-disk 実装の書き換え検出用 belt。
            "input_sha256_provenance_method": {
                "canon_phonemes_txt": "single-read (hash == buffer parsed into variance_phonemes)",
                "acoustic_phonemes_json": "single-read (hash == buffer parsed into acoustic_phonemes, "
                                           "when actually parsed; else sha256_file() re-read)",
                "speaker_embed": "single-read (hash == buffer loaded into spk_embed vector)",
                "score_module_*": "single-read (hash == buffer compiled/exec'd; review #264 R6)",
                "*_dep_*": "single-read (hash == buffer compiled/exec'd; review #264 R6)",
                "gate_synth_py": "load-time hash (captured at module exec start) + "
                                  "pre-publish re-hash (fail-closed on mismatch; review #264 R10)",
                "canon_linguistic_onnx": "single-read (hash == buffer passed to InferenceSession) + "
                                          "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "canon_variance_dur_onnx": "single-read (hash == buffer passed to InferenceSession) + "
                                            "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "canon_variance_pitch_onnx": "single-read (hash == buffer passed to InferenceSession) + "
                                              "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "acoustic_onnx": "single-read (hash == buffer passed to InferenceSession) + "
                                  "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "vocoder_onnx": "single-read (hash == buffer passed to InferenceSession) + "
                                 "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "acoustic_dsconfig_yaml": "single-read (hash == buffer passed to yaml.safe_load) + "
                                           "pre-publish re-hash belt (fail-closed on mismatch; review #264 R12)",
                "ckpt": "sha256_file() only (export.py が別プロセスで消費、compile/exec 対象外)",
                "train_config_yaml": "sha256_file() only (export.py が別プロセスで消費、compile/exec 対象外)",
            },
            "sampling_params": {
                "seed": SEED,
                "speaker": args.speaker,
                "reflow_sampling_steps_constant": REFLOW_SAMPLING_STEPS,
            },
            "results": results,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        _swap_step_dir_into_place(build_dir, synth_out_dir)
        swapped = True
    finally:
        if not swapped:
            shutil.rmtree(build_dir, ignore_errors=True)

    # R6 レビュー指摘 (PR #263, gate_synth.py:879, P2) 対応: 個別 *_record.json
    # の wav_path は synth_song 内で既に最終パス（synth_out_dir 配下）として
    # 完成済みで rename されてくるため、ここでの post-swap 書き換えは不要
    # （公開 = 完成済み束の rename のみ・公開済み世代への追記/書換ゼロ）。

    print(f"| summary: {synth_out_dir / 'gate_synth_summary.json'}")


def cmd_mapping_check(args):
    # review #263 R16 P2: README `s1_gate/README.md` §3 の運用記述
    # （「mapping-check を実 ckpt の acoustic.phonemes.json に対しても別途
    # 走らせ、unmapped_own_count を確認する」）は、従来 CLI が
    # `--own-dictionary-ja`（binarize 入力の辞書テキストから
    # `PhonemeDictionary` でシミュレートした ID 空間）しか受け付けず、export
    # 済みの実 `*.phonemes.json`（`run` が実際に消費する語彙そのもの）を
    # 直接照合できなかった。`--export-phonemes-json` を追加し、実消費写像を
    # そのまま検査できるようにする（両モードは排他 — どちらの ID 空間を
    # 検査しているかを呼び出し側に必ず明示させる）。
    own_dictionary_ja = Path(args.own_dictionary_ja) if args.own_dictionary_ja else None
    export_phonemes_json = Path(args.export_phonemes_json) if args.export_phonemes_json else None
    if (own_dictionary_ja is None) == (export_phonemes_json is None):
        raise SystemExit(
            "error: specify exactly one of --own-dictionary-ja (binarize 入力の "
            "dictionary-ja.txt から PhonemeDictionary でシミュレート) or "
            "--export-phonemes-json (export.py が実際に書き出した *.phonemes.json "
            "= run が実際に消費する写像そのもの)"
        )
    diffsinger_repo = Path(args.diffsinger_repo) if args.diffsinger_repo else None
    if own_dictionary_ja is not None and diffsinger_repo is None:
        raise SystemExit("error: --own-dictionary-ja の指定時は --diffsinger-repo も必須です")

    canon_phonemes_txt = Path(args.canon_phonemes_txt)
    out_path = Path(args.out) if args.out else Path("mapping_check.json")

    # R14 レビュー指摘 (PR #263, gate_synth.py:1123, P2): --out が入力ファイルの
    # いずれかと一致すると、両入力をメモリへ読み込み済みのこの後の書き込みが
    # 入力ファイルそのものを mapping JSON で黙って置き換え・破壊してしまう。
    # resolve 済みの全入力（存在するもののみ）と --out を、実際の読み込みを
    # 始める前に衝突照合し fail-closed で拒否する（入力は読み込み前のため
    # 無傷のまま失敗する）。
    protected_roots = [canon_phonemes_txt]
    for p in (diffsinger_repo, own_dictionary_ja, export_phonemes_json):
        if p is not None:
            protected_roots.append(p)
    try:
        _reject_output_collision([out_path], protected_roots=protected_roots)
    except OutputCollisionError as exc:
        raise SystemExit(f"error: {exc}")

    canon_phonemes = load_canon_phonemes(canon_phonemes_txt)
    if export_phonemes_json is not None:
        own_phonemes = load_own_phonemes_json(export_phonemes_json)
        own_source = f"export-phonemes-json:{export_phonemes_json}"
    else:
        own_phonemes = build_own_dictionary_from_binarize(diffsinger_repo, own_dictionary_ja)
        own_source = f"own-dictionary-ja:{own_dictionary_ja} (simulated via PhonemeDictionary)"
    result = build_phoneme_mapping(own_phonemes, canon_phonemes)
    result["own_source"] = own_source

    # [P2 修正] (review #263 R14; review #264 R15 追い掃討で決定論 tmp パス
    # を排他生成へ是正) 直接 write_text は書き込み途中で kill された場合に
    # 破損 JSON が out_path へ残り得る。`_atomic_write_text`（本ファイル内
    # 定義。`tempfile.mkstemp` の排他生成 + 失敗時 tmp 削除 + `os.replace`
    # の原子的差し替え）で公開する。
    _atomic_write_text(out_path, json.dumps(result, indent=2, ensure_ascii=False))

    print(json.dumps({k: v for k, v in result.items() if k != "mapping"}, indent=2, ensure_ascii=False))
    print(f"| full mapping table: {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="export(任意) -> さくら/うみ合成 -> WAV")
    p_run.add_argument("--diffsinger-repo", help="openvpi/DiffSinger clone (e2307b1)")
    p_run.add_argument("--ckpt-dir", help="回収した checkpoints/<exp_name>/ (ckpt+config.yaml)")
    p_run.add_argument("--exp-name", default="s1_gate")
    p_run.add_argument("--step", type=int)
    p_run.add_argument("--skip-export", action="store_true",
                        help="export.py を走らせず --acoustic-dir の acoustic.onnx をそのまま使う（事前検証用）")
    p_run.add_argument("--acoustic-dir", help="--skip-export 時: acoustic.onnx (+任意で *.phonemes.json) の所在")
    p_run.add_argument("--canon-model-dir", required=True, help="NamineRitsu_DiffSinger 展開先")
    p_run.add_argument("--vocoder-dir", required=True, help="nsf_hifigan.onnx 展開先")
    p_run.add_argument("--out-dir", required=True)
    p_run.add_argument("--song", default="sakura,umi", help="カンマ区切り (sakura,umi)")
    p_run.add_argument("--notes-limit", type=int, default=None,
                        help="先頭 N ノートのみ合成（S0 互換検証用。省略時は全曲）")
    p_run.add_argument("--tokens", choices=["own", "canon"], default="own",
                        help="acoustic への tokens 符号化方式。既定 'own' は fail-closed: "
                             "acoustic.onnx と同じディレクトリに *.phonemes.json が無いと"
                             "エラー終了する（サイレントな誤音素着地を防ぐ）。'canon' は"
                             "canon 符号化を明示許可する S0 互換検証専用モード。")
    p_run.add_argument("--singer-dir", default=None,
                        help="score.py/score_umi.py の所在 (既定: このスクリプトから見た "
                             f"'voice_genesis/singer/' = {DEFAULT_SINGER_DIR})")
    p_run.add_argument("--speaker", choices=["ritsu", "pjs"], default="ritsu",
                        help="reflow 多話者 acoustic 用の話者選択。acoustic ディレクトリの "
                             "'*.<speaker>.emb' を読み込んで spk_embed を構築する（既定 ritsu"
                             "＝主判定話者）。canon 単一話者 DDPM acoustic では無視される。")
    p_run.set_defaults(func=cmd_run)

    p_map = sub.add_parser("mapping-check", help="自前語彙 <-> canon 617/46 語彙の写像テーブル検証")
    p_map.add_argument("--diffsinger-repo", default=None,
                        help="openvpi/DiffSinger clone (e2307b1)。--own-dictionary-ja 使用時のみ必須")
    p_map.add_argument("--own-dictionary-ja", default=None,
                        help="binarize 入力 dictionary-ja.txt / merged_ja_dict.txt から "
                             "PhonemeDictionary でシミュレート（--export-phonemes-json と排他）")
    p_map.add_argument("--export-phonemes-json", default=None,
                        help="review #263 R16 P2: export.py が書き出した実 "
                             "<exp_name>.phonemes.json をそのまま検査する（run が実際に消費する "
                             "写像そのものと一致。--own-dictionary-ja と排他、いずれか一方を指定）")
    p_map.add_argument("--canon-phonemes-txt", required=True)
    p_map.add_argument("--out", default=None)
    p_map.set_defaults(func=cmd_mapping_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
