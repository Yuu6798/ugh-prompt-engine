"""adapter/donor_bank_utau.py — VG-F1.2-B: UTAU oto.ini バンクローダー（波音リツ）。

`DESIGN_F1_adapter_v0.md` 追補 F1.2「F1.2-B UTAU 銀行ローダー」に対応する。

- oto.ini を解析（offset/consonant/cutoff/preutterance/overlap、Shift-JIS 想定）。
- 各サンプルの **母音安定区間** を `donor_bank.DonorUnit` 互換の unit として
  切り出し、**子音区間は録音済み子音オンセット素材**として `ConsonantClip` へ
  別保持する（既存 `donor_bank.DonorBank` のスキーマはそのまま再利用 =
  units.py / joins.py は無改変で消費できる）。
- 音高は音源フォルダ構成（多音階サフィックス。例: A3, F4）から解決する。
- エイリアス（かな/CV/VCV「a か」形式）は `normalize_mora_kana` で正規化する。
- npz/pickle キャッシュは scratchpad 側にのみ書く（呼び出し側が cache_dir を
  渡した場合のみ有効化。既定 None = 無効・非コミット前提。donor_bank.py と
  同じ規約）。

[実装決定・要 record] **計算資源境界**: 波音リツ強連続音 Ver1.5.1 は
pitch フォルダ (A3/F4) あたり oto エイリアス約 1237 行・実 wav 約 228 個
（1 wav に平均 5-6 エイリアスが同居する VCV 連続音のため）。全 wav を
WORLD 分析すると 1 pitch あたり数百秒規模の音声を harvest/cheaptrick/d4c
する必要があり（実測: 10 ファイル/約 48.7 秒音声で約 19.6 秒）、全 456
ファイルでは 10 分規模になり実行予算を圧迫する。そのため
`_select_wav_subset` で **貪欲被覆選択**（必須子音オンセット
{s,k,t,g,r,n,m,h,y,w} を先に埋め、その後 母音あたり `min_units_per_vowel`
件を満たすまで追加、`max_wav_files` で上限）した部分集合のみを分析する
（帯域指標での最適化ではなく計算資源スコープの決定。実測被覆は record に
記録）。
"""
from __future__ import annotations

import hashlib
import io
import pickle
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

_HERE = Path(__file__).resolve().parent
_SINGER_DIR = _HERE.parent.parent / "singer"
for _p in (_SINGER_DIR, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import phoneme_jp as pj  # noqa: E402  (singer、read-only import)

from donor_bank import (  # noqa: E402
    DonorBank,
    DonorUnit,
    FRAME_PERIOD_MS,
    N_LOG_BANDS,
    SR,
    _log_band_vector,
    aggregate_content_hash,
    analyze_donor_world,
    sha256_of,
)

MIN_UNIT_FRAMES_ABS = 3  # 15ms 未満は不採用（既存 donor_bank と揃える・実装決定）

# 対象子音（sakura/umi の onset 和集合、consonants.py と揃える）。
REQUIRED_ONSETS: Tuple[str, ...] = ("s", "k", "t", "g", "m", "n", "r", "h", "y", "w")

# --- かな正規化表 ---
# singer/phoneme_jp.kana_to_morae（sakura/umi 専用の縮小テーブル）で
# 未対応（濁音の一部・半濁音・小書き母音・撥音以外の特殊表記等）の文字を
# 補う追加テーブル。sakura/umi の onset 集合外（b,p,z,d 等）は render の
# 選択には使われないが、bank 統計・record のために正規化しておく
# （[実装決定・record 記録]）。
_EXTRA_TABLE: Dict[str, Tuple[Optional[str], str]] = {
    "ば": ("b", "a"), "び": ("b", "i"), "ぶ": ("b", "u"), "べ": ("b", "e"), "ぼ": ("b", "o"),
    "ぱ": ("p", "a"), "ぴ": ("p", "i"), "ぷ": ("p", "u"), "ぺ": ("p", "e"), "ぽ": ("p", "o"),
    "ざ": ("z", "a"), "じ": ("z", "i"), "ず": ("z", "u"), "ぜ": ("z", "e"), "ぞ": ("z", "o"),
    "だ": ("d", "a"), "ぢ": ("d", "i"), "づ": ("d", "u"), "で": ("d", "e"), "ど": ("d", "o"),
    "ゔ": ("b", "u"),  # ヴ -> バ行近似（[実装決定・出典なし]）
    "ぁ": (None, "a"), "ぃ": (None, "i"), "ぅ": (None, "u"), "ぇ": (None, "e"), "ぉ": (None, "o"),
    "ゐ": (None, "i"), "ゑ": (None, "e"),
}


def normalize_mora_kana(kana: str) -> Tuple[Optional[str], Optional[str], str]:
    """1 モーラ分のかな文字列を (onset, vowel, status) へ正規化する。

    まず `phoneme_jp.kana_to_morae` を試み（sakura/umi 用の直音+濁音+拗音+
    長音+撥音テーブル）、未対応文字なら `_EXTRA_TABLE` を試す。それでも
    未対応なら (None, None, "unmapped")。

    status: "ok" | "long_vowel" | "moraic_nasal" | "sokuon" | "unmapped"
    """
    if kana == "っ":
        return None, None, "sokuon"
    try:
        morae = pj.kana_to_morae(kana)
    except ValueError:
        morae = None
    if morae is not None and len(morae) == 1:
        m = morae[0]
        if m.is_long_vowel_mark:
            return None, m.vowel, "long_vowel"
        if m.is_moraic_nasal:
            return None, "N", "moraic_nasal"
        return m.onset, m.vowel, "ok"
    if kana in _EXTRA_TABLE:
        onset, vowel = _EXTRA_TABLE[kana]
        return onset, vowel, "ok"
    return None, None, "unmapped"


_NOTE_SEMITONE: Dict[str, int] = {
    "C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
    "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11,
}


def note_name_to_hz(name: str) -> float:
    """科学的音高表記（例 "A3", "F4", "C#5"）を Hz へ変換する（A4=440Hz 規約）。"""
    name = name.strip()
    if len(name) >= 2 and name[1] == "#":
        pitch_class, octave_str = name[:2], name[2:]
    else:
        pitch_class, octave_str = name[:1], name[1:]
    semitone = _NOTE_SEMITONE[pitch_class]
    octave = int(octave_str)
    midi = (octave + 1) * 12 + semitone
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


@dataclass(frozen=True)
class OtoEntry:
    wav_filename: str
    alias: str
    offset_ms: float
    consonant_ms: float
    blank_ms: float  # 右ブランク（生の符号のまま保持。解釈は cutoff_position_ms）
    preutterance_ms: float
    overlap_ms: float


def decode_oto_bytes(data: bytes) -> str:
    """oto.ini は Shift-JIS（cp932）が既定（実測・record）。UTF-8 の可能性にも
    フォールバックする。"""
    try:
        return data.decode("cp932")
    except UnicodeDecodeError:
        return data.decode("utf-8")


def parse_oto_ini(path: str | Path) -> List[OtoEntry]:
    """oto.ini（`filename=alias,offset,consonant,blank,preutterance,overlap`
    形式・1 行 1 エイリアス）を解析する。壊れた行（フィールド数不一致）は
    スキップする（実装決定）。"""
    return parse_oto_text(decode_oto_bytes(Path(path).read_bytes()))


def parse_oto_text(text: str) -> List[OtoEntry]:
    """P2 修正 (review #262 R3): `parse_oto_ini` の解析本体をバイト列 read から
    切り離す（呼び出し側が既にハッシュ計算のために読み込んだバイト列から
    そのまま decode してこの関数へ渡せるようにし、同一ファイルへの二重
    read（split-read。TOCTOU 窓）を避けるため。§ builder の prepass 参照）。
    """
    entries: List[OtoEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        wav_filename, _, rest = line.partition("=")
        parts = rest.split(",")
        if len(parts) != 6:
            continue
        alias, offset, consonant, blank, preutt, overlap = parts
        try:
            entries.append(
                OtoEntry(
                    wav_filename=wav_filename, alias=alias,
                    offset_ms=float(offset), consonant_ms=float(consonant),
                    blank_ms=float(blank), preutterance_ms=float(preutt),
                    overlap_ms=float(overlap),
                )
            )
        except ValueError:
            continue
    return entries


def voicebank_identity_hash(
    voicebank_root: str | Path, pitch_dirs: Optional[Sequence[str]] = None
) -> Tuple[str, List[str]]:
    """P1 修正 (review #262 R2・R4 で WAV バイトを追加): UTAU voicebank の
    「実体」ハッシュ（render.py の `spec.donor` provenance 照合用の軽量前段。
    重い WORLD 分析より前に計算する）。

    全 pitch dir の oto.ini + 配下の全 `*.wav` を `(相対パス, sha256)` ペアで
    `donor_bank.aggregate_content_hash` へ渡した集約値を返す。oto.ini は
    unit のセグメンテーション（どのバイトがどの mora か）を決める権威情報
    だが、それだけでは donor の実体を代表しない（review #262 R3 指摘
    `r3789341845`: oto.ini を変えずに WAV バイトだけ差し替えても検知でき
    ない）。WORLD が実際に分析するのは WAV サンプルそのものであるため、
    識別対象を「解析対象の annotation（oto.ini）+ WAV」の決定論集約へ拡張
    した。wav 全数ハッシュはバイト読み出しのみ（WORLD 分析は伴わない）ため
    「軽量前段」の設計意図は維持される。

    [実装決定・record 記録] `_validate_spec_donor` はこの identity 検証を
    `_select_wav_subset_for_contexts` の被覆選択（render 対象スコアが要求
    する文脈次第で結果が変わる）より**前**に行うため、「選択された wav
    部分集合」ではなく pitch dir 配下の**全** wav を対象にする（選択に
    依存しない安定した identity。差し替え検知の網羅性も選択部分集合より
    広く、fail-closed の意図に合致する）。

    戻り値は `(digest, pitch_dirs)`（record/エラーメッセージ用に pitch_dirs
    も返す）。
    """
    root = Path(voicebank_root)
    if pitch_dirs is None:
        pitch_dirs = sorted(
            p.name for p in root.iterdir() if p.is_dir() and (p / "oto.ini").exists()
        )
    if not pitch_dirs:
        raise FileNotFoundError(f"no pitch dir with oto.ini found under {root}")
    pairs: List[Tuple[str, str]] = []
    for pdir_name in pitch_dirs:
        pdir = root / pdir_name
        pairs.append((f"{pdir_name}/oto.ini", sha256_of(pdir / "oto.ini")))
        for wav_path in sorted(pdir.glob("*.wav")):
            pairs.append((f"{pdir_name}/{wav_path.name}", sha256_of(wav_path)))
    return aggregate_content_hash(pairs), list(pitch_dirs)


def cutoff_position_ms(offset_ms: float, blank_ms: float, wav_duration_ms: float) -> float:
    """右ブランク (blank) からサンプル終端位置（ms, ファイル先頭起点）を求める。

    [P1 修正・review #262 R2] UTAU oto.ini の右ブランクは符号で意味が変わる
    （出典: 実測較正 + 一次資料 https://utaudb.sakura.ne.jp/knowledge.php?id=21
    「ブランクが正の数の場合、wavファイルの末尾からの時間[ms]で定義される」
    「ブランクが負の数の場合、オフセットからの時間[ms]に-1を乗じたもので
    定義される」= `blank_ms = -(cutoff_ms - offset_ms)` すなわち
    `cutoff_ms = offset_ms - blank_ms`）。

    - **blank>=0（実データの大多数、A3:1235/1237・F4:1236/1237）**:
      ファイル終端からの距離。`cutoff = wav_duration_ms - blank_ms`
      （旧実装から不変。実測較正: offset=2166,blank=1733,dur=4667 ->
      cutoff=2934。絶対位置解釈だと offset>cutoff の矛盾行が発生するため
      この解釈を採る）。
    - **blank<0（実データでは稀。波音リツ強連続音 Ver1.5.1 実測: A3 2 件
      `n ぬA3`(offset=2749,blank=-600)・`- ぽA3`(offset=79,blank=-600)、
      F4 1 件 `- ちょF4`(offset=714,blank=-600)。VCV では上記のとおり数件
      レベルだが、他 UTAU 音源では CVVC/連続音の設計次第で多数になりうる）**:
      offset からの固定長。`cutoff = offset_ms - blank_ms`（= offset +
      |blank|）。旧実装は blank の符号を無視して両方とも
      `wav_duration_ms - abs(blank_ms)` として扱っていたため、この分岐では
      cutoff が実際より大きくなり隣接収録材料を巻き込んでいた（review #262
      R2 P2 指摘・実測: `n ぬA3` は旧実装 3996ms・正しくは 3349ms、647ms も
      過大に長い区間を切り出していた）。

    いずれの分岐も offset を下回らず wav_duration_ms を上回らないよう安全に
    クランプする（破損値対策・従来どおり）。
    """
    if blank_ms >= 0:
        pos = wav_duration_ms - blank_ms
    else:
        pos = offset_ms - blank_ms  # == offset_ms + abs(blank_ms)
    return max(offset_ms, min(pos, wav_duration_ms))


def _strip_pitch_suffix(token: str, pitch_dirs: Sequence[str]) -> str:
    for suf in sorted(pitch_dirs, key=len, reverse=True):
        if token.endswith(suf):
            return token[: -len(suf)]
    return token


def parse_alias_mora(alias: str, pitch_dirs: Sequence[str]) -> Tuple[Optional[str], str, bool]:
    """VCV/CV エイリアス文字列から (前接母音 or None, 対象モーラかな, is_phrase_initial)
    を取り出す。

    "a あA3" -> (prev="a", mora="あ", False) / "- あA3" -> (None, "あ", True)。
    末尾のピッチサフィックス（フォルダ名。例 "A3"）を除去する。
    """
    toks = alias.split(" ")
    mora_tok = toks[-1]
    mora_kana = _strip_pitch_suffix(mora_tok, pitch_dirs)
    prev_tok = toks[0] if len(toks) > 1 else "-"
    is_phrase_initial = prev_tok == "-"
    prev_vowel = None if is_phrase_initial else prev_tok
    return prev_vowel, mora_kana, is_phrase_initial


@dataclass(frozen=True)
class ConsonantClip:
    onset: str
    sp: np.ndarray
    ap: np.ndarray
    n_frames: int
    source_wav: str
    source_alias: str
    is_phrase_initial: bool


def _ms_to_frame(ms: float, frame_period_ms: float) -> int:
    return int(round(ms / frame_period_ms))


def _load_wav_24k(path: str | Path) -> Tuple[np.ndarray, int, float]:
    """UTAU wav（想定 44.1kHz）を読み込み 24kHz へリサンプルする。

    donor_bank.load_donor_24k と同じ比（80/147）を使うが、vocadito 専用の
    md5 照合メッセージを含まないローカル版（[実装決定]）。戻り値に元ファイルの
    実測 duration_ms も含める（oto.ini の ms 値と同じ基準で cutoff を解決するため）。
    """
    return _load_wav_24k_bytes(Path(path).read_bytes(), source=str(path))


def _load_wav_24k_bytes(data: bytes, source: str = "<bytes>") -> Tuple[np.ndarray, int, float]:
    """P2 修正 (review #262 R3): `_load_wav_24k` のデコード本体をバイト列
    read から切り離す（呼び出し側が既にハッシュ計算のために読み込んだバイト
    列から `io.BytesIO` 経由でそのままデコードし、同一 wav への二重 read
    （split-read）を避けるため。measure_bands.py の R1 修正と同じ流儀）。
    """
    x, sr = sf.read(io.BytesIO(data))
    if x.ndim > 1:
        x = x.mean(axis=1)
    duration_ms = len(x) / sr * 1000.0
    if sr != 44100:
        raise ValueError(f"unexpected UTAU wav sr={sr} (expected 44100): {source}")
    y = resample_poly(x, up=80, down=147)
    return np.ascontiguousarray(y.astype(np.float64)), SR, duration_ms


def _select_wav_subset(
    entries_by_wav: "OrderedDict[str, List[OtoEntry]]",
    pitch_dirs: Sequence[str],
    required_onsets: Sequence[str] = REQUIRED_ONSETS,
    min_units_per_vowel: int = 8,
    max_wav_files: int = 40,
) -> Tuple[List[str], dict]:
    """計算資源境界内で必須子音 + 母音被覆を満たす wav ファイル部分集合を
    貪欲選択する（決定論・音声デコード不要）。

    順序: ファイル名昇順を基本走査順とし、(1) 必須子音オンセットを 1 件も
    含まない間は「新規オンセットを含む最初のファイル」を優先的に前出しで
    選択、(2) 子音被覆が揃った後は各母音 `min_units_per_vowel` 件に届くまで
    ファイル名昇順で追加、(3) `max_wav_files` で打ち切る。
    """
    all_files = sorted(entries_by_wav.keys())
    required = set(required_onsets)
    covered_onsets: set = set()
    vowel_counts: Dict[str, int] = {v: 0 for v in "aiueo"}
    selected: List[str] = []
    remaining = list(all_files)

    def _file_summary(fname: str) -> Tuple[set, Dict[str, int]]:
        onsets_here: set = set()
        vcount: Dict[str, int] = {v: 0 for v in "aiueo"}
        for e in entries_by_wav[fname]:
            _, mora_kana, _ = parse_alias_mora(e.alias, pitch_dirs)
            onset, vowel, status = normalize_mora_kana(mora_kana)
            if status == "ok" and onset:
                onsets_here.add(onset)
            if status == "ok" and vowel in vcount:
                vcount[vowel] += 1
        return onsets_here, vcount

    # フェーズ 1: 必須子音オンセットを埋める（ファイル名昇順で最初に見つかった
    # 未被覆オンセットを含むファイルを選ぶ）。
    for fname in list(remaining):
        if covered_onsets >= required or len(selected) >= max_wav_files:
            break
        onsets_here, vcount = _file_summary(fname)
        if onsets_here - covered_onsets:
            selected.append(fname)
            remaining.remove(fname)
            covered_onsets |= onsets_here
            for v, c in vcount.items():
                vowel_counts[v] += c

    # フェーズ 2: 各母音 min_units_per_vowel 件を満たすまでファイル名昇順で追加。
    for fname in remaining:
        if len(selected) >= max_wav_files:
            break
        if all(vowel_counts[v] >= min_units_per_vowel for v in vowel_counts):
            break
        onsets_here, vcount = _file_summary(fname)
        if any(vowel_counts[v] < min_units_per_vowel for v in vcount):
            selected.append(fname)
            covered_onsets |= onsets_here
            for v, c in vcount.items():
                vowel_counts[v] += c

    selected.sort()  # 決定論のため最終的にファイル名昇順で固定
    stats = dict(
        n_wav_selected=len(selected), n_wav_total=len(all_files),
        covered_onsets=sorted(covered_onsets), missing_onsets=sorted(required - covered_onsets),
        vowel_counts_selected_files=vowel_counts, max_wav_files=max_wav_files,
        min_units_per_vowel=min_units_per_vowel,
    )
    return selected, stats


# ---------------------------------------------------------------------------
# 追補 F1.4-A: VCV unit 化（donor_bank_utau v2）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VCVContext:
    """VCV unit の文脈キー（追補 F1.4-A item2）。"""
    prev_vowel: Optional[str]  # None = フレーズ先頭（oto "-" 形）
    mora: str  # 対象モーラかな（oto エイリアス右トークンと同じ表記）
    is_phrase_initial: bool


def _select_wav_subset_for_contexts(
    entries_by_wav: "OrderedDict[str, List[OtoEntry]]",
    pitch_dirs: Sequence[str],
    required_contexts: Optional[Sequence[Tuple[Optional[str], str]]],
    max_wav_files: int = 200,
) -> Tuple[List[str], dict]:
    """追補 F1.4-A: 必須 VCV 文脈（(prev_vowel, mora) のタプル列）を含む wav
    ファイルをファイル名昇順の決定論走査で選択する（計算資源境界を「必要な
    文脈を過不足なく含む最小集合」に絞ることで、旧 `_select_wav_subset` の
    汎用オンセット被覆ヒューリスティックに依らず sakura/umi の全 mora を
    確実に解決対象にする・[実装決定・record 記録]）。

    `required_contexts=None` の場合は文脈非依存の汎用選択（ファイル名昇順で
    `max_wav_files` 件、旧関数のフォールバック相当）にフォールバックする
    （合成 fixture でのテスト・将来スコア向けの汎用利用のため）。見つから
    なかった文脈は `missing_contexts` に列挙する。
    """
    all_files = sorted(entries_by_wav.keys())
    if required_contexts is None:
        selected = all_files[:max_wav_files]
        return selected, dict(
            n_wav_selected=len(selected), n_wav_total=len(all_files),
            n_contexts_required=0, n_contexts_found=0, missing_contexts=[], mode="generic",
        )

    remaining = set(required_contexts)
    n_required = len(remaining)
    selected: List[str] = []
    for fname in all_files:
        if not remaining or len(selected) >= max_wav_files:
            break
        file_has_needed = False
        for e in entries_by_wav[fname]:
            prev, mora, _is_initial = parse_alias_mora(e.alias, pitch_dirs)
            ctx = (prev, mora)
            if ctx in remaining:
                file_has_needed = True
                remaining.discard(ctx)
        if file_has_needed:
            selected.append(fname)
    selected.sort()  # 決定論のため最終的にファイル名昇順で固定
    missing = sorted(remaining, key=lambda c: (c[0] or "", c[1]))
    stats = dict(
        n_wav_selected=len(selected), n_wav_total=len(all_files),
        n_contexts_required=n_required, n_contexts_found=n_required - len(missing),
        missing_contexts=missing, mode="context_coverage",
    )
    return selected, stats


def build_donor_bank_utau_vcv(
    voicebank_root: str | Path,
    pitch_dirs: Optional[Sequence[str]] = None,
    cache_dir: Optional[str | Path] = None,
    frame_period_ms: float = FRAME_PERIOD_MS,
    required_contexts: Optional[Sequence[Tuple[Optional[str], str]]] = None,
    max_wav_files: int = 200,
) -> Tuple[DonorBank, Dict[int, VCVContext], dict]:
    """追補 F1.4-A: UTAU 音源ルートを VCV unit として分析する（donor_bank_utau v2）。

    v1（`build_donor_bank_utau`）との違い: unit の frame 範囲が
    `[offset, cutoff)`（母音安定区間だけでなく、録音済みの前母音尾+子音+
    母音アタックの遷移全体）へ拡張される。子音は unit 内に録り込まれる
    ため `ConsonantClip` 別保持は行わない（F1.4-B item3「録音子音クリップ
    挿入経路も VCV 経路では廃止」）。

    戻り値: (DonorBank, unit_contexts, stats)。unit_contexts は
    unit.index -> `VCVContext`（選択は `units.select_vcv_units` が使う）。
    """
    root = Path(voicebank_root)
    if pitch_dirs is None:
        pitch_dirs = sorted(
            p.name for p in root.iterdir() if p.is_dir() and (p / "oto.ini").exists()
        )
    if not pitch_dirs:
        raise FileNotFoundError(f"no pitch dir with oto.ini found under {root}")

    # P1 修正 (review #262): キャッシュキーに WAV/oto.ini の**内容**ハッシュを
    # 含めるため、oto.ini 解析 + wav 部分集合選択（どちらも軽量・WORLD 分析を
    # 伴わない）をキャッシュ判定より前に前倒しする。旧実装は root path +
    # options のみをキー材料にしていたため、`--cache-dir` 再利用時に
    # voicebank_root 配下の WAV/oto.ini を編集しても古い pickle を返す
    # サイレントな provenance 破損があった。
    # P2 修正 (review #262 R3): oto.ini/wav とも「1 回 read したバイト列から
    # hash と decode/parse の両方を導出」する（measure_bands.py R1 修正と同じ
    # 流儀）。旧実装は oto.ini を prepass の hash 用 read の後で `parse_oto_ini`
    # がもう一度 read し、選択された各 wav も prepass の hash 用 read の後で
    # メイン loop の `_load_wav_24k`/`sha256_of` がさらに 2 回 read していた
    # （split-read。prepass〜メイン loop の間にファイルが書き換わると記録
    # される sha256 と実際に解析したデータが食い違う TOCTOU 窓）。ここで
    # prepass 時に読んだバイト列を `pitch_dir_prep` へ保持し、メイン loop
    # ではそのバイト列から直接 decode する（wav 選択に使う量に限られるため
    # メモリ増分は計算資源境界方針の範囲内・[実装決定・record 記録]）。
    pitch_dir_prep: Dict[str, dict] = {}
    content_hashes: List[Tuple[str, str]] = []
    for pdir_name in pitch_dirs:
        pdir = root / pdir_name
        oto_path = pdir / "oto.ini"
        oto_bytes = oto_path.read_bytes()
        oto_sha256 = hashlib.sha256(oto_bytes).hexdigest()
        content_hashes.append((f"{pdir_name}/oto.ini", oto_sha256))
        entries = parse_oto_text(decode_oto_bytes(oto_bytes))
        entries_by_wav: "OrderedDict[str, List[OtoEntry]]" = OrderedDict()
        for e in entries:
            entries_by_wav.setdefault(e.wav_filename, []).append(e)
        selected_files, sel_stats = _select_wav_subset_for_contexts(
            entries_by_wav, pitch_dirs, required_contexts, max_wav_files
        )
        wav_bytes_by_file: Dict[str, bytes] = {}
        for wav_filename in selected_files:
            wav_path = pdir / wav_filename
            if wav_path.exists():
                data = wav_path.read_bytes()
                wav_bytes_by_file[wav_filename] = data
                content_hashes.append((f"{pdir_name}/{wav_filename}", hashlib.sha256(data).hexdigest()))
        pitch_dir_prep[pdir_name] = dict(
            entries=entries, entries_by_wav=entries_by_wav,
            selected_files=selected_files, sel_stats=sel_stats,
            wav_bytes_by_file=wav_bytes_by_file,
        )
    # P2 修正 (review #262 R2): (相対パス, sha256) ペアで集約する（パス非結合の
    # hash 集合だけでは、2 ファイルが中身を入れ替えても同一集約ダイジェストに
    # なってしまう = provenance 破損。§ donor_bank.aggregate_content_hash 参照）。
    content_digest = aggregate_content_hash(content_hashes)

    cache_path: Optional[Path] = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        ctx_material = "|".join(f"{c[0] or '-'}:{c[1]}" for c in sorted(
            required_contexts or (), key=lambda c: (c[0] or "", c[1])
        ))
        key_material = (
            f"{root}|{','.join(pitch_dirs)}|{frame_period_ms}|{max_wav_files}|{ctx_material}"
            f"|content={content_digest}|schema=f1.4-vcv-content-hash-v3"
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        cache_path = cache_dir / f"utau_bank_vcv_{key}.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                cached_bank, cached_unit_contexts, cached_stats = pickle.load(f)
            # P2 修正 (review #262 R3): pickle 復元は保存時にダンプされた
            # `stats["cache_hit"]=False` をそのまま返していた（record の
            # cache_hit が常に False のまま = キャッシュ命中を record 上で
            # 判別できない）。復元パスでは True へ上書きする。
            cached_stats["cache_hit"] = True
            cached_bank.stats["cache_hit"] = True
            return cached_bank, cached_unit_contexts, cached_stats

    all_f0: List[np.ndarray] = []
    all_sp: List[np.ndarray] = []
    all_ap: List[np.ndarray] = []
    units: List[DonorUnit] = []
    unit_contexts: Dict[int, VCVContext] = {}
    frame_offset = 0
    wav_sha_parts: List[str] = []

    n_dropped_short = 0
    n_unmapped_kana = 0
    n_sokuon_skipped = 0
    n_negative_overlap_clamped = 0
    n_entries_total = 0
    pitch_hz_by_dir: Dict[str, float] = {}
    n_wav_files_analyzed = 0
    selection_stats_by_dir: Dict[str, dict] = {}

    for pdir_name in pitch_dirs:
        pdir = root / pdir_name
        pitch_hz_by_dir[pdir_name] = note_name_to_hz(pdir_name)
        prep = pitch_dir_prep[pdir_name]
        entries_by_wav = prep["entries_by_wav"]
        n_entries_total += len(prep["entries"])
        selected_files, sel_stats = prep["selected_files"], prep["sel_stats"]
        wav_bytes_by_file = prep["wav_bytes_by_file"]
        selection_stats_by_dir[pdir_name] = sel_stats

        for wav_filename in selected_files:
            # P2 修正 (review #262 R3): prepass で既に read 済みのバイト列を
            # 再利用する（split-read を避ける。§ prepass コメント参照）。
            wav_bytes = wav_bytes_by_file.get(wav_filename)
            if wav_bytes is None:
                continue
            wav_sha = hashlib.sha256(wav_bytes).hexdigest()
            wav_path = pdir / wav_filename
            x, sr, wav_duration_ms = _load_wav_24k_bytes(wav_bytes, source=str(wav_path))
            wav_sha_parts.append(wav_sha)
            donor = analyze_donor_world(x, sr, frame_period_ms)
            n_donor_frames = len(donor["f0"])
            n_wav_files_analyzed += 1

            for e in sorted(entries_by_wav[wav_filename], key=lambda e: (e.offset_ms, e.alias)):
                prev_vowel, mora_kana, is_phrase_initial = parse_alias_mora(e.alias, pitch_dirs)
                onset, vowel, status = normalize_mora_kana(mora_kana)  # noqa: F841 (vowel は record 目的のみ未使用)
                if status == "unmapped":
                    n_unmapped_kana += 1
                    continue
                if status == "sokuon":
                    n_sokuon_skipped += 1
                    continue

                consonant_end_ms = e.offset_ms + e.consonant_ms
                cutoff_ms = cutoff_position_ms(e.offset_ms, e.blank_ms, wav_duration_ms)

                seg_start = max(0, min(_ms_to_frame(e.offset_ms, frame_period_ms), n_donor_frames))
                seg_end = max(0, min(_ms_to_frame(cutoff_ms, frame_period_ms), n_donor_frames))
                if seg_end - seg_start < MIN_UNIT_FRAMES_ABS:
                    n_dropped_short += 1
                    continue
                vcore_abs = max(0, min(_ms_to_frame(consonant_end_ms, frame_period_ms), n_donor_frames))
                vcore_rel = max(0, min(vcore_abs - seg_start, seg_end - seg_start))

                seg_f0 = donor["f0"][seg_start:seg_end]
                voiced = seg_f0[seg_f0 > 0]
                median_f0 = float(np.median(voiced)) if len(voiced) else pitch_hz_by_dir[pdir_name]
                duration_s = (seg_end - seg_start) * frame_period_ms / 1000.0
                head = _log_band_vector(donor["sp"][seg_start], sr)
                tail = _log_band_vector(donor["sp"][seg_end - 1], sr)

                raw_overlap_frames = _ms_to_frame(e.overlap_ms, frame_period_ms)
                if raw_overlap_frames < 0:
                    n_negative_overlap_clamped += 1
                overlap_frames = max(0, raw_overlap_frames)
                preutterance_frames = max(0, _ms_to_frame(e.preutterance_ms, frame_period_ms))

                idx = len(units)
                units.append(
                    DonorUnit(
                        index=idx, start_frame=frame_offset + seg_start, end_frame=frame_offset + seg_end,
                        median_f0=median_f0, duration_s=duration_s,
                        head_log_bands=head, tail_log_bands=tail,
                        overlap_frames=overlap_frames, preutterance_frames=preutterance_frames,
                        vowel_core_start_frame=vcore_rel,
                    )
                )
                unit_contexts[idx] = VCVContext(
                    prev_vowel=prev_vowel, mora=mora_kana, is_phrase_initial=is_phrase_initial
                )

            all_f0.append(donor["f0"])
            all_sp.append(donor["sp"])
            all_ap.append(donor["ap"])
            frame_offset += n_donor_frames

    bank_f0 = np.concatenate(all_f0) if all_f0 else np.zeros(0)
    bank_sp = np.concatenate(all_sp, axis=0) if all_sp else np.zeros((0, N_LOG_BANDS))
    bank_ap = np.concatenate(all_ap, axis=0) if all_ap else np.zeros((0, N_LOG_BANDS))
    wav_sha256 = hashlib.sha256("|".join(sorted(wav_sha_parts)).encode("utf-8")).hexdigest()

    stats = dict(
        n_pitch_dirs=len(pitch_dirs), pitch_dirs=list(pitch_dirs), pitch_hz_by_dir=pitch_hz_by_dir,
        n_entries_total=n_entries_total, n_units_kept=len(units),
        n_dropped_short=n_dropped_short, n_unmapped_kana=n_unmapped_kana,
        n_sokuon_skipped=n_sokuon_skipped, n_negative_overlap_clamped=n_negative_overlap_clamped,
        n_wav_files_analyzed=n_wav_files_analyzed,
        selection_stats_by_dir=selection_stats_by_dir,
        cache_hit=False,
    )

    bank = DonorBank(
        sr=SR, frame_period_ms=frame_period_ms, f0=bank_f0, sp=bank_sp, ap=bank_ap,
        units=units, wav_sha256=wav_sha256, source="utau_oto_vcv", notes_csv_path=None, stats=stats,
    )

    result = (bank, unit_contexts, stats)
    if cache_path is not None:
        with open(cache_path, "wb") as f:
            pickle.dump(result, f)
    return result


def build_donor_bank_utau(
    voicebank_root: str | Path,
    pitch_dirs: Optional[Sequence[str]] = None,
    cache_dir: Optional[str | Path] = None,
    frame_period_ms: float = FRAME_PERIOD_MS,
    min_units_per_vowel: int = 8,
    max_wav_files: int = 40,
) -> Tuple[DonorBank, Dict[int, str], Dict[str, List[ConsonantClip]], dict]:
    """UTAU 音源ルート（character.txt / oto.ini / 音階フォルダ群）を分析する。

    戻り値: (DonorBank, unit_vowels, consonant_clips, stats)。
    - DonorBank.units は母音安定区間の unit（`donor_bank.DonorUnit` と同一
      スキーマ・units.py / joins.py へそのまま渡せる）。bank.f0/sp/ap は
      選択された wav ファイル群を決定論順（ファイル名昇順）で連結した
      仮想フレーム列で、unit の start/end はこの連結後インデックスを指す。
    - unit_vowels は unit.index -> 母音ラベル（oto エイリアス由来の正解
      ラベル。vowel_class.py の音響推定は使わない）。
    - consonant_clips は onset(str) -> 録音済み子音クリップのリスト
      （句頭 "- X" エイリアス由来を優先、同率は (wav名, alias) 昇順で決定論
      ソート）。
    """
    root = Path(voicebank_root)
    if pitch_dirs is None:
        pitch_dirs = sorted(
            p.name for p in root.iterdir() if p.is_dir() and (p / "oto.ini").exists()
        )
    if not pitch_dirs:
        raise FileNotFoundError(f"no pitch dir with oto.ini found under {root}")

    # P1 修正 (review #262): build_donor_bank_utau_vcv と同じ理由で、oto.ini
    # 解析 + wav 部分集合選択をキャッシュ判定より前倒しし、選択された WAV/oto.ini
    # の内容ハッシュをキー材料へ含める。
    # P2 修正 (review #262 R3): oto.ini/wav とも「1 回 read したバイト列から
    # hash と decode/parse の両方を導出」する（§ build_donor_bank_utau_vcv の
    # prepass と同じ是正・同じ理由）。
    pitch_dir_prep: Dict[str, dict] = {}
    content_hashes: List[Tuple[str, str]] = []
    for pdir_name in pitch_dirs:
        pdir = root / pdir_name
        oto_path = pdir / "oto.ini"
        oto_bytes = oto_path.read_bytes()
        content_hashes.append((f"{pdir_name}/oto.ini", hashlib.sha256(oto_bytes).hexdigest()))
        entries = parse_oto_text(decode_oto_bytes(oto_bytes))
        entries_by_wav: "OrderedDict[str, List[OtoEntry]]" = OrderedDict()
        for e in entries:
            entries_by_wav.setdefault(e.wav_filename, []).append(e)
        selected_files, sel_stats = _select_wav_subset(
            entries_by_wav, pitch_dirs, REQUIRED_ONSETS, min_units_per_vowel, max_wav_files
        )
        wav_bytes_by_file: Dict[str, bytes] = {}
        for wav_filename in selected_files:
            wav_path = pdir / wav_filename
            if wav_path.exists():
                data = wav_path.read_bytes()
                wav_bytes_by_file[wav_filename] = data
                content_hashes.append((f"{pdir_name}/{wav_filename}", hashlib.sha256(data).hexdigest()))
        pitch_dir_prep[pdir_name] = dict(
            entries=entries, entries_by_wav=entries_by_wav,
            selected_files=selected_files, sel_stats=sel_stats,
            wav_bytes_by_file=wav_bytes_by_file,
        )
    # P2 修正 (review #262 R2): (相対パス, sha256) ペアで集約する（§ 上の
    # build_donor_bank_utau_vcv と同じ理由）。
    content_digest = aggregate_content_hash(content_hashes)

    cache_path: Optional[Path] = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 追補 F1.3-A: unit スキーマへ overlap/preutterance フレームを追加した
        # ため、キー材料へバージョンマーカーを足して旧キャッシュ（フィールド無し
        # の DonorUnit を pickle 済み）との衝突を避ける（実装決定・record 記録）。
        # P1 修正 (review #262): 内容ハッシュ追加に伴いマーカーを v2 へ更新。
        # P2 修正 (review #262 R2): 集約方式変更（パス非結合 -> パス結合）に伴い v3 へ更新。
        key_material = (
            f"{root}|{','.join(pitch_dirs)}|{frame_period_ms}|{min_units_per_vowel}|{max_wav_files}"
            f"|content={content_digest}|schema=f1.3-overlap-preutt-content-hash-v3"
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        cache_path = cache_dir / f"utau_bank_{key}.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                cached_bank, cached_unit_vowels, cached_consonant_clips, cached_stats = pickle.load(f)
            # P2 修正 (review #262 R3): § build_donor_bank_utau_vcv と同じ是正。
            cached_stats["cache_hit"] = True
            cached_bank.stats["cache_hit"] = True
            return cached_bank, cached_unit_vowels, cached_consonant_clips, cached_stats

    all_f0: List[np.ndarray] = []
    all_sp: List[np.ndarray] = []
    all_ap: List[np.ndarray] = []
    units: List[DonorUnit] = []
    unit_vowels: Dict[int, str] = {}
    consonant_clips: Dict[str, List[ConsonantClip]] = {}
    frame_offset = 0
    wav_sha_parts: List[str] = []

    n_dropped_short_vowel = 0
    n_unmapped_kana = 0
    n_sokuon_skipped = 0
    n_negative_overlap_clamped = 0
    n_entries_total = 0
    pitch_hz_by_dir: Dict[str, float] = {}
    n_wav_files_analyzed = 0
    selection_stats_by_dir: Dict[str, dict] = {}

    for pdir_name in pitch_dirs:
        pdir = root / pdir_name
        pitch_hz_by_dir[pdir_name] = note_name_to_hz(pdir_name)
        prep = pitch_dir_prep[pdir_name]
        entries_by_wav = prep["entries_by_wav"]
        n_entries_total += len(prep["entries"])
        selected_files, sel_stats = prep["selected_files"], prep["sel_stats"]
        wav_bytes_by_file = prep["wav_bytes_by_file"]
        selection_stats_by_dir[pdir_name] = sel_stats

        for wav_filename in selected_files:
            # P2 修正 (review #262 R3): prepass で既に read 済みのバイト列を
            # 再利用する（split-read を避ける）。
            wav_bytes = wav_bytes_by_file.get(wav_filename)
            if wav_bytes is None:
                continue
            wav_sha = hashlib.sha256(wav_bytes).hexdigest()
            wav_path = pdir / wav_filename
            x, sr, wav_duration_ms = _load_wav_24k_bytes(wav_bytes, source=str(wav_path))
            wav_sha_parts.append(wav_sha)
            donor = analyze_donor_world(x, sr, frame_period_ms)
            n_donor_frames = len(donor["f0"])
            n_wav_files_analyzed += 1

            for e in sorted(entries_by_wav[wav_filename], key=lambda e: (e.offset_ms, e.alias)):
                _, mora_kana, is_phrase_initial = parse_alias_mora(e.alias, pitch_dirs)
                onset, vowel, status = normalize_mora_kana(mora_kana)
                if status == "unmapped":
                    n_unmapped_kana += 1
                    continue
                if status == "sokuon":
                    n_sokuon_skipped += 1
                    continue

                consonant_end_ms = e.offset_ms + e.consonant_ms
                cutoff_ms = cutoff_position_ms(e.offset_ms, e.blank_ms, wav_duration_ms)

                v_start = max(0, min(_ms_to_frame(consonant_end_ms, frame_period_ms), n_donor_frames))
                v_end = max(0, min(_ms_to_frame(cutoff_ms, frame_period_ms), n_donor_frames))
                if v_end - v_start < MIN_UNIT_FRAMES_ABS:
                    n_dropped_short_vowel += 1
                else:
                    seg_f0 = donor["f0"][v_start:v_end]
                    voiced = seg_f0[seg_f0 > 0]
                    median_f0 = float(np.median(voiced)) if len(voiced) else pitch_hz_by_dir[pdir_name]
                    duration_s = (v_end - v_start) * frame_period_ms / 1000.0
                    head = _log_band_vector(donor["sp"][v_start], sr)
                    tail = _log_band_vector(donor["sp"][v_end - 1], sr)
                    idx = len(units)
                    # 追補 F1.3-A item1: oto overlap/preutterance をフレーム単位で保持する
                    # （スキーマ拡張）。overlap_ms が負値の行は 0 にクランプする（負の
                    # overlap は一部 UTAU 音源で「重ねない」意図の表記だが、joins.py v2
                    # は非負のクロスフェード長として扱うため。件数を record 記録する）。
                    raw_overlap_frames = _ms_to_frame(e.overlap_ms, frame_period_ms)
                    if raw_overlap_frames < 0:
                        n_negative_overlap_clamped += 1
                    overlap_frames = max(0, raw_overlap_frames)
                    preutterance_frames = max(0, _ms_to_frame(e.preutterance_ms, frame_period_ms))
                    units.append(
                        DonorUnit(
                            index=idx, start_frame=frame_offset + v_start, end_frame=frame_offset + v_end,
                            median_f0=median_f0, duration_s=duration_s,
                            head_log_bands=head, tail_log_bands=tail,
                            overlap_frames=overlap_frames, preutterance_frames=preutterance_frames,
                        )
                    )
                    if vowel:
                        unit_vowels[idx] = vowel

                if onset and onset in REQUIRED_ONSETS:
                    c_start = max(0, min(_ms_to_frame(e.offset_ms, frame_period_ms), n_donor_frames))
                    c_end = max(0, min(_ms_to_frame(consonant_end_ms, frame_period_ms), n_donor_frames))
                    if c_end - c_start >= 1:
                        clip = ConsonantClip(
                            onset=onset, sp=donor["sp"][c_start:c_end].copy(), ap=donor["ap"][c_start:c_end].copy(),
                            n_frames=c_end - c_start, source_wav=wav_filename, source_alias=e.alias,
                            is_phrase_initial=is_phrase_initial,
                        )
                        consonant_clips.setdefault(onset, []).append(clip)

            all_f0.append(donor["f0"])
            all_sp.append(donor["sp"])
            all_ap.append(donor["ap"])
            frame_offset += n_donor_frames

    for onset in list(consonant_clips.keys()):
        consonant_clips[onset] = sorted(
            consonant_clips[onset],
            key=lambda c: (0 if c.is_phrase_initial else 1, c.source_wav, c.source_alias),
        )

    bank_f0 = np.concatenate(all_f0) if all_f0 else np.zeros(0)
    bank_sp = np.concatenate(all_sp, axis=0) if all_sp else np.zeros((0, N_LOG_BANDS))
    bank_ap = np.concatenate(all_ap, axis=0) if all_ap else np.zeros((0, N_LOG_BANDS))
    # bank 全体を代表する「合成 sha256」= 選択された全 wav ファイルの
    # sha256 を昇順連結してハッシュ化したもの（vocadito 版の単一ファイル
    # sha256 と役割を揃えるための決定論合成値・[実装決定]）。
    wav_sha256 = hashlib.sha256("|".join(sorted(wav_sha_parts)).encode("utf-8")).hexdigest()

    stats = dict(
        n_pitch_dirs=len(pitch_dirs), pitch_dirs=list(pitch_dirs), pitch_hz_by_dir=pitch_hz_by_dir,
        n_entries_total=n_entries_total, n_units_kept=len(units),
        n_dropped_short_vowel=n_dropped_short_vowel, n_unmapped_kana=n_unmapped_kana,
        n_sokuon_skipped=n_sokuon_skipped, n_negative_overlap_clamped=n_negative_overlap_clamped,
        n_wav_files_analyzed=n_wav_files_analyzed,
        n_consonant_clips_by_onset={k: len(v) for k, v in sorted(consonant_clips.items())},
        vowel_distribution={v: sum(1 for lbl in unit_vowels.values() if lbl == v) for v in "aiueo"},
        selection_stats_by_dir=selection_stats_by_dir,
        cache_hit=False,
    )

    bank = DonorBank(
        sr=SR, frame_period_ms=frame_period_ms, f0=bank_f0, sp=bank_sp, ap=bank_ap,
        units=units, wav_sha256=wav_sha256, source="utau_oto", notes_csv_path=None, stats=stats,
    )

    result = (bank, unit_vowels, consonant_clips, stats)
    if cache_path is not None:
        with open(cache_path, "wb") as f:
            pickle.dump(result, f)
    return result
