#!/usr/bin/env python3
"""make_vremix_fixtures.py — M2e V-remix 実ベッド帯の決定論ミックス生成。

設計: `docs/DESIGN_M2e_vremix_real_bed.md` §4（生成仕様・**自由変数ゼロ**）、
§9.2（`canonical decode`）、§3.4（スクリーニング指標）、§6.2（manifest 粒度・id 規約）。

vocadito の歌声 clip（正解 f0 注釈つき・既存 pin をそのまま使用）に MUSDB18-HQ の
器楽ベッド（`drums + bass + other`）を決定論ミックスして疑似フルミックスを作る。
**加算ミックスは歌声の f0 を変えないため、正解注釈はそのまま有効。**

規律（破ったら測定は無効・設計 §13）:

- seed 不使用・完全決定論。**波形は commit しない**（pin のみ）。
- `vocals.wav` は読み込まない（存在確認のみ）。読めば声が二重になり、正解注釈が
  どちらの声を指すのか定義できなくなる。本モジュールは読み込みの chokepoint で
  fail-closed の門を持つ。
- 暗黙のリサンプル禁止（rate 不一致は**停止**であって自動変換ではない）。
- リミッタ・ラウドネス正規化・水準ごとの個別正規化は禁止（宣言 SNR が壊れる）。
- 区間は常に**曲頭 0.0s 起点**（「良い区間を探す」余地を残さない）。
- vocadito の再取得・再エンコード禁止（pin と一致しなければ停止する）。

サブコマンド:

    stem-sha256   §9.2 の canonical decode 後 sha256 を印字する（pin 用）
    n-max         §3.5 の測定窓 `n_max`（40 clip の最長サンプル数）を算出する
    screen        §3.4 の `residual_db` を算出する（ベッド 1 曲）
    build         §4 のミックス生成 + manifest / fixtures / 生成記録の出力
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]

# --- 凍結定数（設計 §4・自由変数を残さない）---------------------------------
XFADE_SEC = 0.010          # §4.2 タイル連結のクロスフェード長
FRAME_LEN = 2048           # §4.3 RMS フレーム長
HOP = 512                  # §4.3 RMS ホップ
ACTIVE_FLOOR_DB = -40.0    # §4.3 非無音フレーム判定（ピークからの相対）
PEAK_TARGET = 0.98         # §4.5 クリップ回避のヘッドルーム
BED_STEMS = ("drums", "bass", "other")   # §3.2（`vocals` は**含めない**）
FORBIDDEN_STEM = "vocals"

# §3.6 水準ラダー（4 点・順序込みで凍結）と §6.2 の level_tag。
LEVELS: Tuple[str, ...] = ("+12dB", "+6dB", "0dB", "-6dB")
LEVEL_TAGS: Dict[str, str] = {"+12dB": "p12", "+6dB": "p06", "0dB": "p00", "-6dB": "m06"}
LEVEL_DB: Dict[str, float] = {"+12dB": 12.0, "+6dB": 6.0, "0dB": 0.0, "-6dB": -6.0}

# M2c の事前登録 pin（vocadito 40 clip）。**再取得禁止**なのでここを唯一の真とする。
M2C_FIXTURES_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"
# M2e r3 の事前登録 pin（採用ベッドの canonical decode 後 stem sha256）。
M2E_BED_FIXTURES_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2e_bed_fixtures.yaml"

# 生成した pin ファイルが名乗る schema（ハーネス側 `load_external_fixtures` が受理する）。
M2E_EXTERNAL_FIXTURES_SCHEMA = "m2e-external-fixtures/0.1"
# 読み込む側の schema discriminator（r3 で登録済み）。未知の schema は fail-closed。
M2E_BED_FIXTURES_SCHEMA = "m2e-bed-fixtures/0.1"
M2C_FIXTURES_SCHEMA = "m2c-external-fixtures/0.1"


class GenerationError(RuntimeError):
    """生成規約に反する入力・状態（すべて fail-closed で停止する）。"""


class _NoDuplicateKeyLoader(yaml.SafeLoader):
    """重複キーを **拒否する** YAML loader（ハーネス側 `_yaml_load_no_dup_keys` と同旨）。

    `yaml.safe_load` は同じキーが 2 度現れると黙って後勝ちにする。登録簿で同じ
    トラック/clip が矛盾する `accepted` や stem pin を持って 2 度書かれた場合、
    生成器は**隠れた解釈**で走ってしまう。曖昧な登録簿は読まずに止める。
    """


def _no_dup_construct_mapping(loader: "_NoDuplicateKeyLoader", node: Any) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise GenerationError(f"registry に重複キー {key!r} がある (fail-closed)")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_NoDuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def load_registry_yaml(path: Path) -> Dict[str, Any]:
    """登録簿 YAML を重複キー拒否で読む（`load_registered_*` の唯一の入口）。"""
    doc = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_NoDuplicateKeyLoader)
    if not isinstance(doc, dict):
        raise GenerationError(f"{path}: YAML mapping でない (fail-closed)")
    return doc


# ---------------------------------------------------------------------------
# 読み込みの chokepoint（`vocals.wav` を読まない門）
# ---------------------------------------------------------------------------


def _forbid_vocals(path: Path) -> None:
    """`vocals` stem の読み込みを禁じる（設計 §3.2 / §13）。

    「読まないように気をつける」ではなく**読めないようにする**。ベッド構成が将来
    変わっても、この門を通らない読み込み経路をモジュール内に作らない限り破れない。
    """
    if path.stem.lower() == FORBIDDEN_STEM:
        raise GenerationError(
            f"{path} は vocals stem である; ベッドに混ぜると vocadito の声と二重になり "
            "正解注釈がどちらの声を指すのか定義できなくなる (fail-closed)"
        )


def read_audio(path: Path) -> Tuple[np.ndarray, int]:
    """float64 で読む唯一の入口（§4.1）。**リサンプルしない。**"""
    path = Path(path)
    _forbid_vocals(path)
    data, sample_rate = sf.read(str(path), dtype="float64", always_2d=False)
    return np.asarray(data, dtype=np.float64), int(sample_rate)


# 2026-08-01 撤去: `read_vocals_stem_for_screening` / `--vocals-stem`（Codex 16 巡目 P2）。
# 外部から渡された vocals stem は**事前登録の何とも結び付けられない**——素材 pin も
# 窓 pin も通ったまま、別の分離器・古い stem から出た `residual_db` で採用コホートが
# 変わりうる。読める経路を残さない（`_forbid_vocals` を迂回する関数を置かない）。


# ---------------------------------------------------------------------------
# §9.2 canonical decode（ソース stem 専用・native 整数のまま hash する）
# ---------------------------------------------------------------------------

# 整数 PCM を float へ落とすときの除数（32768 か 32767 か）はライブラリで割れる。
# float 化を pin に含めると pin が外部ライブラリの内部規約に依存するので、
# **float 変換は §4 の生成手続きに属し、pin の外に置く**。
_CANONICAL_DTYPES: Dict[str, str] = {
    "PCM_16": "int16",
    "PCM_24": "int32",   # 上位詰め（soundfile の int32 読み出し規約）・スケーリング禁止
    "PCM_32": "int32",
    "PCM_S8": "int16",
    "PCM_U8": "int16",
}
CANONICAL_SAMPLE_RATE = 44100


def canonical_decode(path: Path) -> Tuple[np.ndarray, int, str]:
    """§9.2 の canonical decode。`(array, sample_rate, subtype)` を返す。

    reader は soundfile / libsndfile（バージョンは `env_digest` に記録する）。
    ファイル固有の PCM 整数幅のまま読み、resample 禁止（native rate != 44100 は
    fail-closed で停止）、downmix・チャンネル並べ替え禁止、C 連続・
    shape (n_frames, n_channels)・little endian。

    **bytes は 1 回だけ読む**（`read_stem_pinned` と同じ規律）。header 検証と
    サンプル読み出しを別々に open すると、その間に差し替えられた場合に
    「片方の header を検証して別の bytes の digest を出す」pin ができてしまう。
    """
    import io

    path = Path(path)
    _forbid_vocals(path)
    payload = path.read_bytes()          # ← 唯一の read。以降はこの snapshot だけを見る
    with sf.SoundFile(io.BytesIO(payload)) as handle:
        subtype, samplerate = str(handle.subtype), int(handle.samplerate)
        dtype = _CANONICAL_DTYPES.get(subtype)
        if dtype is None:
            raise GenerationError(
                f"{path}: subtype {subtype!r} は canonical decode の対象外; "
                "整数 PCM でない入力を pin しない (fail-closed)"
            )
        if samplerate != CANONICAL_SAMPLE_RATE:
            raise GenerationError(
                f"{path}: native rate {samplerate} != {CANONICAL_SAMPLE_RATE}; "
                "canonical decode は resample を禁じる (fail-closed)"
            )
        data = handle.read(dtype=dtype, always_2d=True)
    return np.ascontiguousarray(data), samplerate, subtype


def stem_sha256(path: Path) -> str:
    """§9.2 の `stem_sha256`（native 整数 PCM のバイト列に対する sha256）。"""
    data, _sr, _subtype = canonical_decode(path)
    return hashlib.sha256(np.ascontiguousarray(data).tobytes()).hexdigest()


def read_stem_pinned(
    path: Path, expected_sha256: Optional[str] = None
) -> Tuple[np.ndarray, int]:
    """**bytes を 1 回だけ読み**、その snapshot から pin 用の整数 decode と混合用の
    float 読みを行う。

    pin 照合と実際に混ぜるサンプルを別々に読むと、その間に stem が差し替えられた
    場合に **pin は通ったのに混ぜたのは別の音**という状態が成立する（生成物は内部
    整合するので下流から検出できない）。

    **同一 file handle を保持するだけでは足りない**（初版はそう書いていたが誤り）:
    fd を握っていても防げるのはパスの差し替え（rename/unlink→再作成）だけで、
    **同じ inode へ in-place 上書き**されれば `seek(0)` 後の 2 回目の read は
    書き換わった bytes を読む。メモリ上の snapshot を唯一の入力にすれば、
    どちらの経路でも 2 つの read が同じ bytes を見ることが保証される。
    """
    import io

    path = Path(path)
    _forbid_vocals(path)
    payload = path.read_bytes()          # ← 唯一の read。以降はこの snapshot だけを見る
    with sf.SoundFile(io.BytesIO(payload)) as handle:
        dtype = _CANONICAL_DTYPES.get(handle.subtype)
        if dtype is None:
            raise GenerationError(
                f"{path}: subtype {handle.subtype!r} は canonical decode の対象外; "
                "整数 PCM でない入力を pin しない (fail-closed)"
            )
        if int(handle.samplerate) != CANONICAL_SAMPLE_RATE:
            raise GenerationError(
                f"{path}: native rate {handle.samplerate} != {CANONICAL_SAMPLE_RATE}; "
                "canonical decode は resample を禁じる (fail-closed)"
            )
        native = np.ascontiguousarray(handle.read(dtype=dtype, always_2d=True))
        sample_rate = int(handle.samplerate)
    if expected_sha256 is not None:
        actual = hashlib.sha256(native.tobytes()).hexdigest()
        if actual != expected_sha256:
            raise GenerationError(
                f"{path}: canonical decode 後 sha256 {actual} が事前登録 pin "
                f"{expected_sha256} と不一致; 破損・差し替え・再取得ミスの可能性が "
                "あり、間違ったベッドで内部整合した manifest を作らない (fail-closed)"
            )
    # float 変換は soundfile に委ねる（除数の規約を自前で選ばない・§9.2）。
    with sf.SoundFile(io.BytesIO(payload)) as handle:
        data = np.asarray(handle.read(dtype="float64", always_2d=False), dtype=np.float64)
    return data, sample_rate


def waveform_sha256(y: np.ndarray) -> str:
    """§4.6 の `waveform_sha256`（既存 `m2_accuracy_bars.yaml` の規約と同一）。"""
    return hashlib.sha256(np.asarray(y, dtype=np.float32).tobytes()).hexdigest()


def serialize_wav_float32(y: np.ndarray, sample_rate: int) -> bytes:
    """モノラル float32 波形を**決定論的な** RIFF/WAVE bytes へ直列化する。

    `sf.write(..., subtype="FLOAT")`（libsndfile）は float WAV に PEAK チャンクを書き、
    そこに**壁時計タイムスタンプ**が入るため、**同一波形でも直列化 bytes が秒単位で
    変わる**（実測 2026-08-01: 1 秒跨ぎで書いた 2 つの WAV は offset 60 の 1 バイトのみ
    相違・PEAK チャンクは offset 48）。これを pin に使うと `expected_audio_sha256` が
    再生成のたびに変わり、**「同一入力 → 同一 bytes」という帯の前提が壊れる**。

    実装は `scripts/run_melody_accuracy.py::_serialize_wav_float32` と**同一構成**
    （fmt(IEEE float) / fact / data のみの最小 RIFF）。両者の bytes 一致は
    `tests/test_make_vremix_fixtures.py` が機械検証する——規約が 2 つに割れるより、
    同じものを 2 箇所で作って一致を強制するほうが安全である。
    """
    import struct

    samples = np.asarray(y, dtype=np.float32)
    if samples.ndim != 1:
        raise GenerationError(
            f"serialize_wav_float32: モノラル 1-D 波形のみ対応（shape {samples.shape!r}）"
        )
    data = samples.tobytes()
    rate = int(sample_rate)
    fmt_body = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)  # WAVE_FORMAT_IEEE_FLOAT
    fact_body = struct.pack("<I", samples.size)
    payload = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
        + b"fact" + struct.pack("<I", len(fact_body)) + fact_body
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


# ---------------------------------------------------------------------------
# §4.2 長さ合わせ（タイル連結・10ms 線形クロスフェード）
# ---------------------------------------------------------------------------


def tile_to_length(s: np.ndarray, n_target: int, sample_rate: int) -> np.ndarray:
    """`s` をタイル連結して `n_target` サンプルへ伸ばす（設計 §4.2 の擬似コード通り）。

    区間は常に曲頭 0.0s 起点。短ければ 10ms 線形クロスフェードで継ぎ足す。
    """
    if n_target <= 0:
        raise GenerationError(f"n_target {n_target!r} が正でない (fail-closed)")
    xf = int(round(XFADE_SEC * sample_rate))
    s = np.asarray(s, dtype=np.float64)
    if len(s) <= xf:
        raise GenerationError(
            f"ソースが短すぎる（{len(s)} サンプル <= クロスフェード長 {xf}） (fail-closed)"
        )
    w = np.arange(xf, dtype=np.float64) / xf   # [0, 1/xf, ..., (xf-1)/xf]（右端を含まない）
    out = s.copy()
    while len(out) < n_target:
        head = out[-xf:] * (1.0 - w) + s[:xf] * w
        out = np.concatenate([out[:-xf], head, s[xf:]])
    return out[:n_target]


# ---------------------------------------------------------------------------
# §4.3 RMS（非無音フレーム）
# ---------------------------------------------------------------------------


def frame_rms(x: np.ndarray) -> np.ndarray:
    """フレーム RMS（中央寄せなし・末尾の埋まらないフレームは破棄）。"""
    x = np.asarray(x, dtype=np.float64)
    n_frames = 1 + (len(x) - FRAME_LEN) // HOP if len(x) >= FRAME_LEN else 0
    if n_frames <= 0:
        raise GenerationError(
            f"信号が短すぎて RMS フレームが 1 つも取れない（{len(x)} サンプル） (fail-closed)"
        )
    return np.array(
        [
            float(np.sqrt(np.mean(x[k * HOP : k * HOP + FRAME_LEN] ** 2)))
            for k in range(n_frames)
        ],
        dtype=np.float64,
    )


def active_frames(frames: np.ndarray) -> np.ndarray:
    """非無音フレーム集合（ピークから -40 dB 以上）。空なら fail-closed。"""
    peak = float(np.max(frames))
    active = frames >= peak * (10.0 ** (ACTIVE_FLOOR_DB / 20.0))
    if not bool(np.any(active)):
        raise GenerationError("非無音フレームが 1 つも無い (fail-closed)")
    return active


def rms(x: np.ndarray, *, active: Optional[np.ndarray] = None) -> float:
    """非無音フレームのみで算出した RMS（§4.3）。

    `active` を渡すと**その集合をそのまま使う**——§3.4.2 の `residual_db` では
    vocals stem の RMS も bed 側で選ばれた `active` を使う（stem 自身で選び直すと
    器楽曲の漏れが自己正規化で持ち上がり、退役した旧指標と同じ罠に落ちる）。
    """
    frames = frame_rms(x)
    if active is None:
        active = active_frames(frames)
    elif len(active) != len(frames):
        raise GenerationError(
            f"active フレーム集合の長さ {len(active)} が信号のフレーム数 {len(frames)} と "
            "一致しない; 同一フレーム集合で評価するという規約が壊れている (fail-closed)"
        )
    return float(np.sqrt(np.mean(frames[active] ** 2)))


def residual_db(bed_mono: np.ndarray, vocals_mono: np.ndarray) -> float:
    """§3.4.2 の `residual_db`。**両者を同一フレーム集合で評価する。**"""
    bed_frames = frame_rms(bed_mono)
    active = active_frames(bed_frames)
    bed_rms = float(np.sqrt(np.mean(bed_frames[active] ** 2)))
    vocals_rms = rms(vocals_mono, active=active)
    if bed_rms <= 0.0:
        raise GenerationError("bed の RMS が 0 (fail-closed)")
    if vocals_rms <= 0.0:
        return float("-inf")
    return 20.0 * float(np.log10(vocals_rms / bed_rms))


# ---------------------------------------------------------------------------
# §4.1 入力と前処理
# ---------------------------------------------------------------------------


def load_bed_mono(
    bed_dir: Path, *, expected_stem_sha256: Optional[Dict[str, str]] = None
) -> Tuple[np.ndarray, int]:
    """`drums + bass + other` を生サンプル加算し、`0.5 * (L + R)` でモノ化する。

    `expected_stem_sha256` を渡すと、各 stem の §9.2 canonical decode 後 sha256 を
    **事前登録 pin と照合**する（不一致は停止）。破損・差し替え・再取得ミスがあっても
    manifest と fixtures は「間違ったベッド」に対して内部整合してしまうため、
    ここで素材そのものを committed pin へ繋いでおかないと、ハーネス側は
    「別の素材を測った」ことを検出できない。

    `vocals.wav` は**使わない**（存在確認のみ・設計 §3.2）。MUSDB18-HQ の stem は
    mixture へ厳密に加算合成されるので、`drums+bass+other` は当該曲の器楽のみの
    トラックに正確に一致する。
    """
    bed_dir = Path(bed_dir)
    vocals_path = bed_dir / f"{FORBIDDEN_STEM}.wav"
    if not vocals_path.is_file():
        # 存在確認のみ（読まない）。存在しないなら配布物の構造が想定と違う。
        raise GenerationError(
            f"{vocals_path} が無い; MUSDB18-HQ の想定構造と異なる (fail-closed)"
        )
    accumulated: Optional[np.ndarray] = None
    sample_rate: Optional[int] = None
    for name in BED_STEMS:
        expected: Optional[str] = None
        if expected_stem_sha256 is not None:
            expected = expected_stem_sha256.get(name)
            if not expected:
                raise GenerationError(
                    f"{bed_dir}: stem {name!r} の expected_stem_sha256 が事前登録に無い "
                    "(fail-closed)"
                )
        # pin 照合と混合サンプルは**同一 file handle**から取る（TOCTOU 除去）。
        data, rate = read_stem_pinned(bed_dir / f"{name}.wav", expected)
        if data.ndim != 2 or data.shape[1] != 2:
            raise GenerationError(
                f"{bed_dir / (name + '.wav')}: ステレオ 2ch でない（shape={data.shape}） "
                "(fail-closed)"
            )
        if sample_rate is None:
            sample_rate = rate
            accumulated = data
        else:
            if rate != sample_rate:
                raise GenerationError(
                    f"{bed_dir}: stem 間で sample rate が食い違う（{rate} != {sample_rate}）; "
                    "暗黙のリサンプルは禁止 (fail-closed)"
                )
            if data.shape != accumulated.shape:  # type: ignore[union-attr]
                raise GenerationError(
                    f"{bed_dir}: stem 間で長さが食い違う (fail-closed)"
                )
            accumulated = accumulated + data    # type: ignore[operator]
    assert accumulated is not None and sample_rate is not None
    bed_mono = 0.5 * (accumulated[:, 0] + accumulated[:, 1])
    return bed_mono, sample_rate


def load_voice(payload: bytes, *, where: str) -> Tuple[np.ndarray, int]:
    """**pin 照合済みの bytes そのもの**から vocadito clip をモノ float64 で復号する。

    パスを渡して開き直すと、照合したファイルと混ぜたファイルが別物になりうる
    （照合と読み出しの間に差し替えられるケース）。生成物は内部整合するので下流から
    検出できない。`resolve_clip_bytes` が読んだスナップショットを唯一の入力とする。
    """
    import io

    data, rate = sf.read(io.BytesIO(payload), dtype="float64", always_2d=False)
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 1:
        raise GenerationError(
            f"{where}: vocadito clip はモノでなければならない（shape={data.shape}） "
            "(fail-closed)"
        )
    return data, int(rate)


# ---------------------------------------------------------------------------
# §4.4 / §4.5 水準適用とクリップ回避
# ---------------------------------------------------------------------------


def apply_level(
    voice_seg: np.ndarray, bed_seg: np.ndarray, level: str
) -> Tuple[np.ndarray, Dict[str, float]]:
    """§4.4 の水準適用 + §4.5 のクリップ回避。`(mix_final, meta)` を返す。

    リミッタ・ラウドネス正規化・水準ごとの個別正規化は**いずれも**歌声/ベッドの比を
    変え、宣言した SNR を破壊するので行わない。`factor` は両成分に共通なので
    RMS 比は厳密に不変である。
    """
    if level not in LEVEL_DB:
        raise GenerationError(f"未登録の水準 {level!r}; 既知は {list(LEVELS)} (fail-closed)")
    if len(voice_seg) != len(bed_seg):
        raise GenerationError(
            f"voice_seg {len(voice_seg)} と bed_seg {len(bed_seg)} の長さが違う (fail-closed)"
        )
    r_db = LEVEL_DB[level]
    v = rms(voice_seg)
    b = rms(bed_seg)
    if b <= 0.0:
        raise GenerationError("bed の RMS が 0 (fail-closed)")
    g_v = 1.0                                        # 歌声は無加工（正解側を触らない）
    g_b = (v / b) * (10.0 ** (-r_db / 20.0))
    mix = voice_seg * g_v + bed_seg * g_b
    peak = float(np.max(np.abs(mix)))
    if peak <= 0.0:
        raise GenerationError("ミックスのピークが 0 (fail-closed)")
    factor = min(1.0, PEAK_TARGET / peak)
    mix_final = mix * factor
    return mix_final, {
        "voice_rms": v,
        "bed_rms": b,
        "g_v": g_v,
        "g_b": g_b,
        "peak_before_factor": peak,
        "factor": factor,
        "level_db": r_db,
    }


# ---------------------------------------------------------------------------
# id 規約（§6.2）
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


def bed_slug(track_name: str) -> str:
    """MUSDB のトラック名から entry id 用の安全な slug を**決定論的に**導出する。

    ハーネスの外部 id は `[A-Za-z0-9._-]+` に限られる一方、MUSDB18-HQ のトラック名は
    空白や記号を含む。実行者の裁量で別名を付けさせないため、置換規則を凍結する
    （非英数字の連続を 1 つの `-` へ、前後の `-` を除去）。**順序の決定（§3.3）に
    使うのは slug ではなく元のトラック名**であり、slug は id/ファイル名の安全性の
    ためだけに存在する。
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", track_name).strip("-")
    if not slug or not _SAFE_ID_RE.fullmatch(slug):
        raise GenerationError(
            f"トラック名 {track_name!r} から安全な bed_id を導出できない (fail-closed)"
        )
    return slug


def entry_id(clip_id: str, bed_id: str, level: str) -> str:
    """§6.2 の entry id 規約: `vremix_{clip_id}_{bed_id}_{level_tag}`。"""
    value = f"vremix_{clip_id}_{bed_id}_{LEVEL_TAGS[level]}"
    if not _SAFE_ID_RE.fullmatch(value):
        raise GenerationError(f"entry id {value!r} が id 規約に反する (fail-closed)")
    return value


# ---------------------------------------------------------------------------
# vocadito の既存 pin（再取得禁止）
# ---------------------------------------------------------------------------


def require_valid_registered_utc(value: str) -> None:
    """`registered_utc` をハーネスと**同じ意味論**で検証する（fail-closed）。

    受理する形（`run_melody_accuracy._parse_registered_utc` と同一）:
    `YYYY-MM-DD`（UTC 深夜と解釈）または **UTC offset 0** の ISO 8601 timestamp。
    未来日は拒否する（まだ到来していない時点の「事前登録」を主張させない）。
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    if not isinstance(value, str) or not value:
        raise GenerationError("registered_utc が非空文字列でない (fail-closed)")
    try:
        parsed = datetime.combine(_date.fromisoformat(value), datetime.min.time(), timezone.utc)
    except ValueError:
        try:
            candidate = datetime.fromisoformat(value)
        except ValueError as exc:
            raise GenerationError(
                f"registered_utc {value!r} は日付 (YYYY-MM-DD) でも ISO 8601 timestamp "
                f"でもない (fail-closed): {exc}"
            ) from exc
        offset = candidate.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise GenerationError(
                f"registered_utc {value!r} は UTC でない（tz 無しまたは offset≠0）"
                " (fail-closed)"
            )
        parsed = candidate
    if parsed > datetime.now(timezone.utc):
        raise GenerationError(
            f"registered_utc {value!r} が未来; まだ到来していない時点の「事前登録」を "
            "主張させない (fail-closed)"
        )


def load_bed_fixtures_doc() -> Dict[str, Any]:
    """`m2e_bed_fixtures.yaml` を schema 検証つきで読む（**この登録簿の唯一の入口**）。

    schema discriminator の照合をここに集約する。読む側ごとに素の
    `load_registry_yaml` を呼ぶと、片方（`load_registered_beds`）は未知 schema を
    拒否するのに片方（窓の読み出し）は素通しする、という不揃いが生まれる。
    """
    if not M2E_BED_FIXTURES_PATH.is_file():
        raise GenerationError(
            f"{M2E_BED_FIXTURES_PATH} が無い; ベッドの事前登録 pin なしにミックスを "
            "生成しない (fail-closed)"
        )
    doc = load_registry_yaml(M2E_BED_FIXTURES_PATH)
    version = doc.get("schema_version")
    if version != M2E_BED_FIXTURES_SCHEMA:
        raise GenerationError(
            f"{M2E_BED_FIXTURES_PATH}: schema_version {version!r} が "
            f"{M2E_BED_FIXTURES_SCHEMA!r} でない; 意味の分からない pin から "
            "ミックスを publish しない (fail-closed)"
        )
    return doc


def load_registered_beds() -> Dict[str, Dict[str, Any]]:
    """`m2e_bed_fixtures.yaml` の採用ベッド pin を読む（§9・r3 で登録済み）。"""
    beds = load_bed_fixtures_doc().get("beds")
    if not isinstance(beds, dict) or not beds:
        raise GenerationError(f"{M2E_BED_FIXTURES_PATH}: beds が空 (fail-closed)")
    for track, pin in beds.items():
        # `accepted` は採用コホートの境界そのものである。`"false"` のような
        # 非 bool が入ると truthiness で**採用側へ倒れ**、生成器は登録より広い
        # コホートを内部整合したまま publish してしまう。型で止める。
        if not isinstance(pin, dict):
            raise GenerationError(
                f"{M2E_BED_FIXTURES_PATH}: {track!r} の登録が mapping でない (fail-closed)"
            )
        if not isinstance(pin.get("accepted"), bool):
            raise GenerationError(
                f"{M2E_BED_FIXTURES_PATH}: {track!r} の accepted が bool でない "
                f"({pin.get('accepted')!r}); 採否を truthiness で解釈しない (fail-closed)"
            )
        if not isinstance(pin.get("lexical_index"), int):
            raise GenerationError(
                f"{M2E_BED_FIXTURES_PATH}: {track!r} の lexical_index が整数でない "
                "(fail-closed)"
            )
    return beds


def load_registered_clips() -> Dict[str, Dict[str, str]]:
    """`m2c_external_fixtures.yaml` の 40 clip pin を読む（**唯一の真**）。"""
    doc = load_registry_yaml(M2C_FIXTURES_PATH)
    version = doc.get("schema_version")
    if version != M2C_FIXTURES_SCHEMA:
        raise GenerationError(
            f"{M2C_FIXTURES_PATH}: schema_version {version!r} が "
            f"{M2C_FIXTURES_SCHEMA!r} でない; 意味の分からない registry から "
            "ミックスを publish しない (fail-closed)"
        )
    fixtures = doc.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise GenerationError(
            f"{M2C_FIXTURES_PATH}: fixtures が空; 事前登録済み clip なしに生成しない "
            "(fail-closed)"
        )
    return fixtures


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_clip_bytes(
    vocadito_root: Path, clip_id: str, pin: Dict[str, str]
) -> Tuple[bytes, bytes]:
    """clip の音声/注釈を **1 回だけ読み**、その bytes が既存 pin と一致することを要求する。

    返すのはパスではなく **bytes スナップショット**である。照合したファイルを後から
    開き直すと、その間に差し替えられた場合に「pin は通ったが混ぜた/複製したのは別の
    bytes」が成立し、生成物は内部整合するので下流から検出できない（ベッド stem 側の
    `read_stem_pinned` と同じ規律）。
    """
    audio = Path(vocadito_root) / "Audio" / f"{clip_id}.wav"
    annotation = Path(vocadito_root) / "Annotations" / "F0" / f"{clip_id}_f0.csv"
    payloads: List[bytes] = []
    for path, key in ((audio, "expected_audio_sha256"), (annotation, "expected_annotation_sha256")):
        if not path.is_file():
            raise GenerationError(f"{path} が無い (fail-closed)")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != pin[key]:
            raise GenerationError(
                f"{path}: sha256 {actual} が既存 pin {pin[key]} と不一致; vocadito の "
                "再取得・再エンコードは禁止であり、pin 側を実体へ合わせることもしない "
                "(fail-closed)"
            )
        payloads.append(data)
    return payloads[0], payloads[1]


def registered_n_max_samples() -> int:
    """`m2e_bed_fixtures.yaml` に登録された測定窓 `n_max`（§3.5・凍結量）。"""
    window = load_bed_fixtures_doc().get("window")
    if not isinstance(window, dict) or not isinstance(window.get("n_max_samples"), int):
        raise GenerationError(
            f"{M2E_BED_FIXTURES_PATH}: window.n_max_samples が無い/整数でない (fail-closed)"
        )
    return int(window["n_max_samples"])


def registered_screening() -> Dict[str, Any]:
    """`m2e_bed_fixtures.yaml` の `screening` block（凍結された screening 条件）。"""
    screening = load_bed_fixtures_doc().get("screening")
    required = ("separation_model", "separation_version", "separation_weights_sha256")
    if not isinstance(screening, dict) or any(not screening.get(k) for k in required):
        raise GenerationError(
            f"{M2E_BED_FIXTURES_PATH}: screening に {required} が揃っていない (fail-closed)"
        )
    return screening


def require_registered_separator(separation: Any, screening: Dict[str, Any]) -> None:
    """分離器の実体（model / version / weights digest）を登録値へ束縛する。

    ソース stem を pin へ束縛しても、**分離器が drift すれば `residual_db` は変わる**
    ——チェックポイントや demucs のバージョンが入れ替わった環境で回し直すと、
    すべての pin を通過したままもっともらしい別の値が出て、採用コホートが変わりうる。
    """
    actual = {
        "separation_model": separation.model,
        "separation_version": separation.version,
        "separation_weights_sha256": separation.weights_sha256,
    }
    mismatched = {k: (v, screening.get(k)) for k, v in actual.items() if v != screening.get(k)}
    if mismatched:
        detail = "; ".join(f"{k}: 実体 {a!r} != 登録 {e!r}" for k, (a, e) in sorted(mismatched.items()))
        raise GenerationError(
            f"分離器が事前登録と一致しない（{detail}）; 別の分離器から canonical な "
            "screening 値を出さない (fail-closed)"
        )


def require_registered_track_list(tracks: List[str]) -> None:
    """トラック一覧が**事前登録の全 50 曲**と（順序込みで）一致することを要求する。

    呼び出し側が渡す JSON が 49 件へ切り詰められていても、ループはその 49 件しか
    要求しないので「49 枚の digest map を出して正常終了」してしまう。**揃っている
    ことが完了の証拠になる**成果物では、コホートそのものを登録簿へ結び付ける。
    """
    beds = load_registered_beds()
    expected = [
        name for name, _ in sorted(beds.items(), key=lambda kv: kv[1]["lexical_index"])
    ]
    if list(tracks) != expected:
        missing = sorted(set(expected) - set(tracks))
        extra = sorted(set(tracks) - set(expected))
        raise GenerationError(
            f"トラック一覧が事前登録コホート（{len(expected)} 件・lexical order）と一致 "
            f"しない（欠落={missing} / 余分={extra} / 件数={len(tracks)}）; 部分コホートで "
            "成果物を出さない (fail-closed)"
        )


def compute_n_max(vocadito_root: Path, clips: Dict[str, Dict[str, str]]) -> Tuple[int, float]:
    """§3.5 の `n_max`（40 clip の最長サンプル数）。常に 40 clip 基準で固定する。

    `n_max` は測定窓そのもの（凍結量）なので、**pin 照合していない bytes から
    算出しない**——差し替え/再エンコードされた clip でも header だけは読めてしまい、
    別の窓長を「正規の 40 clip から出した」顔で凍結できる。
    """
    import io

    longest = 0
    rate: Optional[int] = None
    for clip_id in sorted(clips):
        path = Path(vocadito_root) / "Audio" / f"{clip_id}.wav"
        if not path.is_file():
            raise GenerationError(f"{path} が無い (fail-closed)")
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        expected = clips[clip_id]["expected_audio_sha256"]
        if actual != expected:
            raise GenerationError(
                f"{path}: sha256 {actual} が既存 pin {expected} と不一致; 事前登録して "
                "いない bytes から n_max を凍結しない (fail-closed)"
            )
        with sf.SoundFile(io.BytesIO(payload)) as handle:
            samplerate, frames = int(handle.samplerate), int(handle.frames)
        if rate is None:
            rate = samplerate
        elif samplerate != rate:
            raise GenerationError(
                f"clip {clip_id!r} の sample rate {samplerate} が他の clip と食い違う "
                "(fail-closed)"
            )
        longest = max(longest, frames)
    if rate is None or longest <= 0:
        raise GenerationError("n_max を算出できない (fail-closed)")
    return longest, longest / float(rate)


# ---------------------------------------------------------------------------
# build（§4 + §6.2）
# ---------------------------------------------------------------------------


def build(
    *,
    vocadito_root: Path,
    bed_root: Path,
    tracks: List[str],
    levels: List[str],
    out_dir: Path,
    registered_utc: str,
) -> Dict[str, Any]:
    """全 (clip × bed × level) のミックスと manifest / fixtures / 生成記録を書く。

    生成は **staging → atomic rename**。途中で失敗しても外から見える場所に部分
    bundle を残さない（消費側は out_dir を「1 つの fixture bundle」として扱う）。
    加えて「空でない出力先は拒否」と噛み合うと、中断が次回実行を塞いでしまう——
    staging に閉じ込めておけば再実行がそのまま通る。

    rename を atomic にするため staging は**同一親ディレクトリ**へ置く。名前は
    `mkdtemp` で**この実行が所有する一意な名前**にする——固定名にすると、同じ
    出力先を狙う 2 本目が 1 本目の生きた staging を消してしまう（かつ、たまたま
    同名で存在していた無関係のディレクトリを再帰削除しうる）。一意名にした以上、
    **後片付けはこの実行が自分でやる**（`BaseException` を捕まえるのは
    `KeyboardInterrupt` で数百本の WAV を置き去りにしないため。再送出はする）。
    """
    # P1-2: `registered_utc: null` は `load_external_fixtures_with_raw` の
    # `_parse_registered_utc` を通らない——生成した fixtures をそのまま
    # `--external-fixtures` へ渡す documented flow が必ず落ちていた。実値を要求する。
    # 非空チェックだけでは `garbage` や未来日が通り、**数百本の WAV を生成した後**に
    # ハーネス側の `_parse_registered_utc` で落ちる。同じ意味論で先に検証する。
    require_valid_registered_utc(registered_utc)
    published = Path(out_dir)
    # 生成物（320 本の WAV）は **リポジトリ外**に置く規律（runbook §5）。ミックスは
    # MUSDB18-HQ 由来の非コミット素材を含み、WAV の一括 ignore も無いので、
    # checkout 内へ出すと通常の `git add` で licensed audio が入りうる。
    resolved = published.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise GenerationError(
            f"--out-dir {resolved} がリポジトリ（{ROOT}）配下; 生成物は非コミット素材を "
            "含むためリポジトリ外へ出す (fail-closed)"
        )
    if published.exists() and any(published.iterdir()):
        raise GenerationError(
            f"{published} が空でない; 前回実行の残骸と混ざるのを避けるため、空の出力先を "
            "指定するか既存を削除すること (fail-closed)"
        )
    published.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(dir=str(published.parent), prefix=f".{published.name}.staging-")
    )
    try:
        return _build_into_staging(
            staging=staging,
            published=published,
            vocadito_root=Path(vocadito_root),
            bed_root=Path(bed_root),
            tracks=tracks,
            levels=levels,
            registered_utc=registered_utc,
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build_into_staging(
    *,
    staging: Path,
    published: Path,
    vocadito_root: Path,
    bed_root: Path,
    tracks: List[str],
    levels: List[str],
    registered_utc: str,
) -> Dict[str, Any]:
    """`build` の本体（この実行が所有する `staging` の中だけで完結する）。"""
    clips = load_registered_clips()
    registered_beds = load_registered_beds()
    out_dir = staging
    mix_dir = out_dir / "mix"
    annotation_dir = out_dir / "annotations"
    mix_dir.mkdir(parents=True, exist_ok=True)
    annotation_dir.mkdir(parents=True, exist_ok=True)

    # 採用ベッドは**全件揃っていること**を要求する（部分コホートを publish しない）。
    # `--bed` を 1 本落としても、生成される manifest と fixtures は互いに整合するため、
    # ハーネスの cohort 完全一致チェックは通ってしまう——40 entry の帯から
    # 「1280 セルの判定」が出てしまい、欠けたことを誰も検出できない。
    # （accepted でない track を渡した場合は下の per-track 検査が個別の理由で落とす。
    #   ここで見るのは「accepted なのに渡されていない」= 欠落のみ。）
    accepted = {name for name, pin in registered_beds.items() if pin.get("accepted")}
    missing = sorted(accepted - set(tracks))
    if missing:
        raise GenerationError(
            f"事前登録の accepted ベッド {missing} が --bed に無い; 部分コホートで "
            "内部整合した fixture bundle を publish しない (fail-closed)"
        )

    slugs: Dict[str, str] = {}
    for track in tracks:
        slug = bed_slug(track)
        if slug in slugs.values():
            raise GenerationError(
                f"bed_id {slug!r} が衝突する; トラック名から一意な id を導出できない "
                "(fail-closed)"
            )
        slugs[track] = slug

    beds: Dict[str, Tuple[np.ndarray, int]] = {}
    for track in tracks:
        pin = registered_beds.get(track)
        if pin is None:
            raise GenerationError(
                f"track {track!r} が {M2E_BED_FIXTURES_PATH.name} に事前登録されていない "
                "(fail-closed)"
            )
        if not pin.get("accepted"):
            raise GenerationError(
                f"track {track!r} は事前登録上 accepted ではない; スクリーニングを通って "
                "いないベッドでミックスを作らない (fail-closed)"
            )
        bed_mono, bed_rate = load_bed_mono(
            Path(bed_root) / track, expected_stem_sha256=pin.get("expected_stem_sha256")
        )
        # stem pin は**素材**を同定するが、そこから作る `[0, n_max]` の窓は
        # decode → downmix → tile の実装を通って出てくる。実装（や復号依存）が
        # 変われば、すべての stem pin を通したまま**screening を通した窓とは別の
        # 波形**でミックスを作りうる。screening 時に登録した窓 digest へ結び付ける。
        window_pin = pin.get("bed_window_sha256")
        if not window_pin:
            raise GenerationError(
                f"track {track!r}: bed_window_sha256 が事前登録に無い (fail-closed)"
            )
        actual_window = waveform_sha256(
            tile_to_length(bed_mono, registered_n_max_samples(), bed_rate)
        )
        if actual_window != window_pin:
            raise GenerationError(
                f"track {track!r}: 再構成したベッド窓の sha256 {actual_window} が事前登録 "
                f"pin {window_pin} と不一致; screening を通した窓と別の波形でミックスを "
                "作らない (fail-closed)"
            )
        beds[track] = (bed_mono, bed_rate)

    # 注釈はミックスで変わらない（加算ミックスは f0 を変えない）。manifest 位置基準で
    # 解決できる必要があるので out_dir 配下へ **bytes 複製**する（再エンコードしない）。
    annotation_pins: Dict[str, str] = {}
    for clip_id in sorted(clips):
        _audio, annotation = resolve_clip_bytes(vocadito_root, clip_id, clips[clip_id])
        destination = annotation_dir / f"{clip_id}_f0.csv"
        destination.write_bytes(annotation)   # 照合した bytes をそのまま複製する
        annotation_pins[clip_id] = clips[clip_id]["expected_annotation_sha256"]

    summary: Dict[str, Any] = {"levels": {}, "beds": slugs}
    for level in levels:
        tag = LEVEL_TAGS[level]
        manifest: List[Dict[str, str]] = []
        fixtures: Dict[str, Dict[str, str]] = {}
        records: Dict[str, Dict[str, Any]] = {}
        for track in tracks:
            bed_mono, bed_rate = beds[track]
            bed_id = slugs[track]
            for clip_id in sorted(clips):
                audio_payload, _annotation = resolve_clip_bytes(
                    vocadito_root, clip_id, clips[clip_id]
                )
                voice, voice_rate = load_voice(audio_payload, where=f"clip {clip_id!r}")
                if bed_rate != voice_rate:
                    raise GenerationError(
                        f"sample rate 不一致（bed {bed_rate} != clip {voice_rate}）; "
                        "暗黙のリサンプルは禁止 (fail-closed)"
                    )
                bed_seg = tile_to_length(bed_mono, len(voice), voice_rate)
                mix_final, meta = apply_level(voice, bed_seg, level)
                eid = entry_id(clip_id, bed_id, level)
                mix_path = mix_dir / f"{eid}.wav"
                # P1-1: libsndfile の PEAK チャンクに壁時計が入り bytes が秒単位で
                # 変わるため、`sf.write(subtype="FLOAT")` は pin 対象に使えない。
                mix_path.write_bytes(serialize_wav_float32(mix_final, voice_rate))
                manifest.append(
                    {
                        "id": eid,
                        "audio_path": f"mix/{eid}.wav",
                        "annotation_path": f"annotations/{clip_id}_f0.csv",
                    }
                )
                fixtures[eid] = {
                    "expected_audio_sha256": _sha256_file(mix_path),
                    "expected_annotation_sha256": annotation_pins[clip_id],
                }
                records[eid] = {
                    "clip_id": clip_id,
                    "bed_id": bed_id,
                    "bed_track_name": track,
                    "level": level,
                    "waveform_sha256": waveform_sha256(mix_final),
                    "n_samples": int(len(mix_final)),
                    "sample_rate": voice_rate,
                    **{k: float(v) for k, v in meta.items()},
                }
        manifest_path = out_dir / f"manifest_{tag}.json"
        manifest_path.write_text(
            json.dumps(sorted(manifest, key=lambda e: e["id"]), indent=2), encoding="utf-8"
        )
        fixtures_doc = {
            "schema_version": M2E_EXTERNAL_FIXTURES_SCHEMA,
            "registered_utc": registered_utc,
            "level": level,
            "fixtures": {k: fixtures[k] for k in sorted(fixtures)},
        }
        fixtures_path = out_dir / f"fixtures_{tag}.yaml"
        fixtures_path.write_text(
            yaml.safe_dump(fixtures_doc, sort_keys=True, allow_unicode=True), encoding="utf-8"
        )
        record_path = out_dir / f"generation_record_{tag}.json"
        record_path.write_text(
            json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
        )
        summary["levels"][level] = {
            "n_entries": len(manifest),
            "manifest": str(published / manifest_path.name),
            "fixtures": str(published / fixtures_path.name),
            "generation_record": str(published / record_path.name),
            "n_factor_below_one": sum(1 for r in records.values() if r["factor"] < 1.0),
        }
    # 全水準を書き終えてから**一度だけ**公開する（ここまでは外から見えない）。
    published.parent.mkdir(parents=True, exist_ok=True)
    if published.exists():
        published.rmdir()   # 空であることは入口で確認済み
    os.rename(staging, published)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_stem_sha256(args: argparse.Namespace) -> int:
    for path in args.paths:
        print(f"{stem_sha256(Path(path))}  {path}")
    return 0


def _cmd_n_max(args: argparse.Namespace) -> int:
    clips = load_registered_clips()
    samples, seconds = compute_n_max(Path(args.vocadito_root), clips)
    print(json.dumps({"n_max_samples": samples, "n_max_seconds": seconds}, indent=2))
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    # 登録後に `screen` を回し直す場合、素材が古い/差し替わっていても `residual_db` は
    # もっともらしい値で出る（窓長の照合は素材を同定しない）。50 曲すべてに
    # `expected_stem_sha256` が登録済みなので、ここでも pin へ束縛する。
    registered_beds = load_registered_beds()
    screening = registered_screening()
    pin = registered_beds.get(args.track)
    if pin is None:
        raise GenerationError(
            f"track {args.track!r} が {M2E_BED_FIXTURES_PATH.name} に事前登録されていない "
            "(fail-closed)"
        )
    bed_mono, bed_rate = load_bed_mono(
        Path(args.bed_root) / args.track, expected_stem_sha256=pin.get("expected_stem_sha256")
    )
    # CLI の値をそのまま信じない——古い値や打ち間違いでも「成功して」別区間の
    # `residual_db` を出し、採用コホートが変わりうる。登録値との一致を要求する
    # （明示させたうえで照合する: 宣言と実体の両方を残すため）。
    registered = registered_n_max_samples()
    if int(args.n_max) != registered:
        raise GenerationError(
            f"--n-max {args.n_max} が事前登録の n_max_samples {registered} と不一致; "
            "測定窓は凍結量であり、呼び出し側の値で上書きしない (fail-closed)"
        )
    window = tile_to_length(bed_mono, registered, bed_rate)
    # §4.7: 分離器の投入は既存 `demucs_vocals_then_crepe` 経路の実装を流用する
    # （新しい規約を発明しない）。未導入・重み未取得なら fail-closed で停止する。
    # **provenance を捨てる `isolate_vocals` は使わない**——ソース stem を pin へ
    # 束縛しても、分離器そのものが drift すれば `residual_db` は変わりうる
    # （もっともらしい値が出るので下流から見えない）。登録された model / version /
    # weights digest と照合する。
    #
    # **外部 stem を受け取る経路は持たない**（旧 `--vocals-stem` は撤去した）。
    # 渡された WAV を pin へ結び付ける手段が無い以上、それは「登録されていない
    # 信号から canonical な screening 値を出す」入口にしかならない——素材 pin も
    # 窓 pin も通ったまま、採用コホートだけが変わりうる。
    from svp_rpe.rpe.learned.source_separation_adapter import isolate_vocals_with_provenance

    with tempfile.TemporaryDirectory(prefix="m2e-screen-") as tmp:
        probe = Path(tmp) / "bed.wav"
        sf.write(str(probe), np.asarray(window, dtype=np.float32), bed_rate, subtype="FLOAT")
        separation = isolate_vocals_with_provenance(probe, model=screening["separation_model"])
    require_registered_separator(separation, screening)
    stem = np.asarray(separation.waveform, dtype=np.float64)
    stem_rate = int(separation.sample_rate)
    if stem.ndim == 2:
        stem = stem.mean(axis=0) if stem.shape[0] < stem.shape[1] else stem.mean(axis=1)
    if stem_rate != bed_rate:
        raise GenerationError(
            f"分離 stem の rate {stem_rate} が bed の {bed_rate} と違う (fail-closed)"
        )
    stem = np.asarray(stem, dtype=np.float64)[: len(window)]
    value = residual_db(window, stem)
    print(
        json.dumps(
            {
                "track": args.track,
                "bed_id": bed_slug(args.track),
                "residual_db": value,
                "n_window_samples": int(len(window)),
                "sample_rate": bed_rate,
            },
            indent=2,
        )
    )
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    # 水準ラダーは §3.6 で 4 点・順序込みで凍結されている。1 点落としても manifest /
    # fixtures / 以降の run・evaluate はすべて level-local なので、**960 セルで
    # 完走した帯が 1280 セルの帯と見分けられない**。CLI で全点を要求する。
    if list(args.level) != list(LEVELS):
        raise GenerationError(
            f"--level {list(args.level)} が凍結ラダー {list(LEVELS)} と（順序込みで）"
            "一致しない; 部分ラダーで内部整合した帯を publish しない (fail-closed)"
        )
    summary = build(
        vocadito_root=Path(args.vocadito_root),
        bed_root=Path(args.bed_root),
        tracks=list(args.bed),
        levels=list(args.level),
        out_dir=Path(args.out_dir),
        registered_utc=args.registered_utc,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_stem = sub.add_parser("stem-sha256", help="§9.2 canonical decode 後 sha256")
    p_stem.add_argument("paths", nargs="+")
    p_stem.set_defaults(func=_cmd_stem_sha256)

    p_nmax = sub.add_parser("n-max", help="§3.5 測定窓 n_max")
    p_nmax.add_argument("--vocadito-root", required=True)
    p_nmax.set_defaults(func=_cmd_n_max)

    p_screen = sub.add_parser("screen", help="§3.4 residual_db")
    p_screen.add_argument("--bed-root", required=True)
    p_screen.add_argument("--track", required=True)
    # `--n-max` は**必須**。省略すると窓が曲全体になり、`active` 集合も `residual_db`
    # も凍結窓 `[0, n_max]` の外の音を含んでしまう——同じ曲の判定が変わり、採用コホート
    # が変わりうる（§3.5 の測定窓は凍結量であって既定値ではない）。
    p_screen.add_argument("--n-max", type=int, required=True)
    p_screen.set_defaults(func=_cmd_screen)

    p_build = sub.add_parser("build", help="§4 ミックス生成 + manifest/fixtures/生成記録")
    p_build.add_argument("--vocadito-root", required=True)
    p_build.add_argument("--bed-root", required=True)
    p_build.add_argument("--bed", action="append", required=True, help="MUSDB トラック名")
    p_build.add_argument(
        "--level", action="append", required=True, choices=list(LEVELS),
        help=f"§3.6 の凍結ラダー。**4 点すべてを凍結順で**指定する: {' '.join(LEVELS)}",
    )
    p_build.add_argument("--out-dir", required=True, help="**空**のディレクトリ（残骸混入を防ぐ）")
    p_build.add_argument(
        "--registered-utc", required=True, metavar="YYYY-MM-DD",
        help="生成する fixtures の事前登録日。ハーネスは `registered_utc` を必須検証する。"
        "**生成した fixtures yaml は測定前に repo へ commit すること**——evaluate の "
        "`_require_attested_external_fixtures_registration` が git 祖先を要求する"
        "（波形はリポジトリ外のまま。pin ファイルだけを commit する）",
    )
    p_build.set_defaults(func=_cmd_build)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
