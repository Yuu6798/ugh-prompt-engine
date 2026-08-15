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
    text = decode_oto_bytes(Path(path).read_bytes())
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


def cutoff_position_ms(offset_ms: float, blank_ms: float, wav_duration_ms: float) -> float:
    """右ブランク (blank) からサンプル終端位置（ms, ファイル先頭起点）を求める。

    [実装決定・較正 1 回・record 記録] 波音リツ強連続音 Ver1.5.1 の oto.ini を
    実測すると、行の大多数（A3: 1235/1237, F4: 1236/1237）は blank が正号
    で書かれている。これを「ファイル先頭からの絶対位置」として解釈すると
    offset > cutoff になる矛盾行が複数発生する（例: offset=2166,blank=1733
    で絶対位置解釈だと長さ -433ms）。一方 `wav_duration_ms - abs(blank_ms)`
    （符号に関わらず「ファイル終端からの距離」として扱う）だと同エイリアス
    群で一貫して正の長さが得られる（実測・複数エイリアスで検算済み）。
    本関数はこの較正済み解釈を採用する。offset を下回らないよう安全にクランプする。
    """
    pos = wav_duration_ms - abs(blank_ms)
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
    x, sr = sf.read(str(path))
    if x.ndim > 1:
        x = x.mean(axis=1)
    duration_ms = len(x) / sr * 1000.0
    if sr != 44100:
        raise ValueError(f"unexpected UTAU wav sr={sr} (expected 44100): {path}")
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

    cache_path: Optional[Path] = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 追補 F1.3-A: unit スキーマへ overlap/preutterance フレームを追加した
        # ため、キー材料へバージョンマーカーを足して旧キャッシュ（フィールド無し
        # の DonorUnit を pickle 済み）との衝突を避ける（実装決定・record 記録）。
        key_material = (
            f"{root}|{','.join(pitch_dirs)}|{frame_period_ms}|{min_units_per_vowel}|{max_wav_files}"
            f"|schema=f1.3-overlap-preutt-v1"
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        cache_path = cache_dir / f"utau_bank_{key}.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                return pickle.load(f)

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
        oto_path = pdir / "oto.ini"
        pitch_hz_by_dir[pdir_name] = note_name_to_hz(pdir_name)
        entries = parse_oto_ini(oto_path)
        n_entries_total += len(entries)
        entries_by_wav: "OrderedDict[str, List[OtoEntry]]" = OrderedDict()
        for e in entries:
            entries_by_wav.setdefault(e.wav_filename, []).append(e)

        selected_files, sel_stats = _select_wav_subset(
            entries_by_wav, pitch_dirs, REQUIRED_ONSETS, min_units_per_vowel, max_wav_files
        )
        selection_stats_by_dir[pdir_name] = sel_stats

        for wav_filename in selected_files:
            wav_path = pdir / wav_filename
            if not wav_path.exists():
                continue
            x, sr, wav_duration_ms = _load_wav_24k(wav_path)
            wav_sha_parts.append(sha256_of(wav_path))
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
