"""adapter/donor_bank_lab.py — VG-F1.2-C: PJS .lab（音素ラベル）バンクローダー。

`DESIGN_F1_adapter_v0.md` 追補 F1.2「F1.2-C PJS 銀行ローダー」に対応する。

- `.lab`（HTS 形式・時刻は 100ns 単位）の音素区間列を読み、mora
  （子音 + 母音核）へグルーピングする。
- [実装決定・要 record] F1.2-B（UTAU バンク）と対称の設計を採る:
  「mora unit」を母音核（vowel-core）の `DonorUnit` として切り出し、
  子音区間は `donor_bank_utau.ConsonantClip` 互換のクリップとして別保持
  する。F1.2-D の録音子音前置ロジック（render.py）を両ドナーで共用できる
  ようにするための整合（設計書 F1.2-C 本文は「mora 単位（子音+母音核）を
  unit化」とのみ記すが、Part 3 は両ドナー共通の「recorded 前置」ロジックを
  要求しており、UTAU 側と分割方式を揃えるのが自然な帰結と判断した）。
- 男声ドナーのため score との音域差をオクターブ単位の移調で吸収する
  （`compute_octave_transpose`）。移調は選択コスト用の `median_f0` にのみ
  適用する（f0 render は最終的に perf_genes が生成するため、実際の
  合成ピッチには影響しない。donor_bank.py の音素選択と同じ約定）。

[実装決定・要 record] **計算資源境界**: PJS 全 100 曲を WORLD 分析すると
音声合計 27.2 分規模になり実行予算を圧迫するため、`_select_lab_files` で
必須子音オンセット {s,k,t,g,r,n,m,h,y,w} と 5 母音を貪欲被覆する少数ファイル
のみ分析する（帯域指標での最適化ではなく計算資源スコープの決定）。
"""
from __future__ import annotations

import hashlib
import io
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

_HERE = Path(__file__).resolve().parent
for _p in (_HERE,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from donor_bank import (  # noqa: E402
    DonorBank,
    DonorUnit,
    FRAME_PERIOD_MS,
    N_LOG_BANDS,
    SR,
    _log_band_vector,
    aggregate_content_hash,
    analyze_donor_world,
    atomic_pickle_dump,
    sha256_of,
)
from donor_bank_utau import ConsonantClip, REQUIRED_ONSETS  # noqa: E402  (adapter sibling import)

# .lab の時刻単位（HTS 標準 = 100ns）。
LAB_TIME_UNIT_S = 1e-7

MIN_UNIT_FRAMES_ABS = 3  # 15ms 未満は不採用（既存 donor_bank / donor_bank_utau と揃える）

VOWELS_5 = ("a", "i", "u", "e", "o")
# PJS lab のラベル語彙（実測: koguchi20 PJS ver1.1、100 ファイル走査で確認）。
# 母音: a,i,u,e,o / 撥音: N / 促音（閉鎖）: cl / 無音: pau / 不明瞭: xx。
# 残りは全て子音（拗音含む: ky,gy,sh,j,ch,ny,hy,by,py,my,ry,ts 等）。
_SPECIAL_LABELS = {"pau", "xx", "N", "cl"}

# 拗音等の音素ラベル -> sakura/umi onset 集合への正規化（[実装決定・record]）。
# sakura/umi の onset は非拗音の素朴な子音のみ（s,k,t,g,r,n,m,h,y,w）のため、
# ラベルがそのまま一致する場合のみ「対象」として扱う。拗音（ky,sh 等）は
# consonant_clips には保持しない（REQUIRED_ONSETS 外＝件数記録のみ）。


@dataclass(frozen=True)
class LabPhoneme:
    start_s: float
    end_s: float
    phoneme: str


def parse_lab(path: str | Path) -> List[LabPhoneme]:
    """HTS 形式 .lab（`start_100ns end_100ns phoneme` の空白区切り 3 列）を読む。"""
    return parse_lab_text(Path(path).read_text(encoding="utf-8"))


def parse_lab_text(text: str) -> List[LabPhoneme]:
    """P2 修正 (review #262 R3): `parse_lab` の解析本体をファイル read から
    切り離す（呼び出し側が既にハッシュ計算のために読み込んだバイト列/テキスト
    から直接解析でき、同一 .lab への二重 read を避けるため。
    § donor_bank_utau.parse_oto_text と対称の設計）。
    """
    out: List[LabPhoneme] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        start_raw, end_raw, phoneme = parts
        out.append(
            LabPhoneme(
                start_s=int(start_raw) * LAB_TIME_UNIT_S,
                end_s=int(end_raw) * LAB_TIME_UNIT_S,
                phoneme=phoneme,
            )
        )
    return out


@dataclass(frozen=True)
class LabMora:
    onset: Optional[str]  # 子音音素ラベル（複数子音が並んだ場合は最後の非 cl トークン）
    vowel: Optional[str]  # a/i/u/e/o、または "N"（撥音）。None = 母音核なし（例: 語末 cl 単独）
    onset_start_s: Optional[float]
    onset_end_s: Optional[float]
    vowel_start_s: float
    vowel_end_s: float


def group_lab_to_morae(phonemes: Sequence[LabPhoneme]) -> Tuple[List[LabMora], dict]:
    """音素区間列を mora（子音+母音核）へグルーピングする。

    pau/xx は境界として扱い、子音バッファをリセットする（無音を跨いで
    子音が母音へ結合しないようにする）。N（撥音）は単独 mora
    （onset=None, vowel="N"）。cl（促音の閉鎖）は直前の子音バッファへ
    積むが、onset ラベルとしては採用しない（次の実子音、または母音が
    続かず終端した場合は破棄・件数記録）。
    """
    morae: List[LabMora] = []
    pending_onset: Optional[str] = None
    pending_start: Optional[float] = None
    n_cl_dangling = 0
    n_pending_dropped_at_boundary = 0

    for ph in phonemes:
        if ph.phoneme in ("pau", "xx"):
            if pending_onset is not None:
                n_pending_dropped_at_boundary += 1
            pending_onset, pending_start = None, None
            continue
        if ph.phoneme == "cl":
            if pending_onset is None:
                pending_start = ph.start_s
            n_cl_dangling += 1  # 実子音で上書きされれば相殺されない件数として記録のみ
            continue
        if ph.phoneme == "N":
            morae.append(
                LabMora(
                    onset=None, vowel="N", onset_start_s=None, onset_end_s=None,
                    vowel_start_s=ph.start_s, vowel_end_s=ph.end_s,
                )
            )
            pending_onset, pending_start = None, None
            continue
        if ph.phoneme in VOWELS_5:
            morae.append(
                LabMora(
                    onset=pending_onset,
                    vowel=ph.phoneme,
                    onset_start_s=pending_start,
                    onset_end_s=(ph.start_s if pending_onset is not None else None),
                    vowel_start_s=ph.start_s, vowel_end_s=ph.end_s,
                )
            )
            pending_onset, pending_start = None, None
            continue
        # 子音（拗音含む）: バッファへ積む（cl の後に来た場合は cl の開始位置を保持）。
        if pending_start is None:
            pending_start = ph.start_s
        pending_onset = ph.phoneme

    if pending_onset is not None:
        n_pending_dropped_at_boundary += 1  # ファイル終端で母音に接続しなかった子音

    stats = dict(n_morae=len(morae), n_cl_seen=n_cl_dangling, n_dangling_onset_dropped=n_pending_dropped_at_boundary)
    return morae, stats


def compute_octave_transpose(donor_median_hz: float, target_median_hz: float) -> int:
    """donor の中央値ピッチを target の音域へ最寄りのオクターブ単位で合わせる
    半音移調量を返す（12 の倍数、丸め）。donor/target のいずれかが 0 以下なら 0。
    """
    if donor_median_hz <= 0 or target_median_hz <= 0:
        return 0
    semitone_diff = 12.0 * float(np.log2(target_median_hz / donor_median_hz))
    return int(round(semitone_diff / 12.0)) * 12


def corpus_identity_hash(corpus_root: str | Path) -> str:
    """P1 修正 (review #262 R2・R4 で WAV バイトを追加): PJS コーパスの
    「実体」ハッシュ（render.py の `spec.donor` provenance 照合用。
    § donor_bank_utau.voicebank_identity_hash と対称の設計）。

    全 `pjsNNN/pjsNNN.lab`（音素セグメンテーション。どのファイルがどの mora
    かを決める権威情報）+ 対応する `pjsNNN_song.wav`（存在すれば）を
    `(corpus_root からの相対パス, sha256)` ペアで `donor_bank.aggregate_content_hash`
    へ渡した集約値を返す。.lab だけでは donor の実体を代表しない（review
    #262 R3 指摘 `r3789341845`: .lab を変えずに WAV バイトだけ差し替えても
    検知できない）。WORLD が実際に分析するのは WAV サンプルそのものである
    ため、識別対象を「解析対象の annotation（.lab）+ WAV」の決定論集約へ
    拡張した（`_select_lab_files` の被覆選択より前に行う検証のため、選択
    部分集合ではなくコーパス全体を対象にする — § voicebank_identity_hash と
    同じ実装決定）。
    """
    root = Path(corpus_root)
    lab_paths = sorted(root.glob("pjs*/pjs*.lab"))
    if not lab_paths:
        raise FileNotFoundError(f"no pjs*/pjs*.lab found under {root}")
    pairs: List[Tuple[str, str]] = []
    for lab_path in lab_paths:
        pairs.append((str(lab_path.relative_to(root)), sha256_of(lab_path)))
        wav_path = lab_path.parent / f"{lab_path.stem}_song.wav"
        if wav_path.exists():
            pairs.append((str(wav_path.relative_to(root)), sha256_of(wav_path)))
    return aggregate_content_hash(pairs)


def _ms_to_frame(sec: float, frame_period_ms: float) -> int:
    return int(round(sec * 1000.0 / frame_period_ms))


def _load_wav_24k(path: str | Path) -> Tuple[np.ndarray, int]:
    """PJS song wav（48kHz/24bit）を読み込み 24kHz へリサンプルする（up=1,down=2）。"""
    return _load_wav_24k_bytes(Path(path).read_bytes(), source=str(path))


def _load_wav_24k_bytes(data: bytes, source: str = "<bytes>") -> Tuple[np.ndarray, int]:
    """P2 修正 (review #262 R3): `_load_wav_24k` のデコード本体をバイト列
    read から切り離す（§ donor_bank_utau._load_wav_24k_bytes と対称の設計。
    split-read を避けるため）。
    """
    x, sr = sf.read(io.BytesIO(data))
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != 48000:
        raise ValueError(f"unexpected PJS wav sr={sr} (expected 48000): {source}")
    y = resample_poly(x, up=1, down=2)
    return np.ascontiguousarray(y.astype(np.float64)), SR


def _usable_onsets_and_vowels(
    phonemes: Sequence[LabPhoneme], required: set, frame_period_ms: float
) -> Tuple[set, set]:
    """`group_lab_to_morae` でグルーピングした後、実際に unit/clip 化できる
    区間（子音 >= 1 フレーム、母音核 >= `MIN_UNIT_FRAMES_ABS` フレーム）から
    「この .lab で使用可能な onset/vowel 集合」を導く。`build_donor_bank_lab`
    の unit/clip 化条件（子音: `c_end - c_start >= 1`、母音:
    `v_end - v_start >= MIN_UNIT_FRAMES_ABS`）と一致させる。音声デコードは
    不要（時刻から算出したフレーム数のみで判定する）。
    """
    morae, _mstats = group_lab_to_morae(phonemes)
    usable_onsets: set = set()
    usable_vowels: set = set()
    for m in morae:
        if m.onset is not None and m.onset in required and m.onset_start_s is not None:
            c_start = _ms_to_frame(m.onset_start_s, frame_period_ms)
            c_end = _ms_to_frame(m.onset_end_s, frame_period_ms)
            if c_end - c_start >= 1:
                usable_onsets.add(m.onset)
        if m.vowel in VOWELS_5:
            v_start = _ms_to_frame(m.vowel_start_s, frame_period_ms)
            v_end = _ms_to_frame(m.vowel_end_s, frame_period_ms)
            if v_end - v_start >= MIN_UNIT_FRAMES_ABS:
                usable_vowels.add(m.vowel)
    return usable_onsets, usable_vowels


def _select_lab_files(
    lab_paths: Sequence[Path],
    required_onsets: Sequence[str] = REQUIRED_ONSETS,
    min_files: int = 6,
    max_files: int = 10,
    frame_period_ms: float = FRAME_PERIOD_MS,
) -> Tuple[List[Path], dict, Dict[Path, bytes]]:
    """必須子音オンセット + 5 母音を貪欲被覆する .lab ファイル部分集合を選ぶ
    （決定論・ファイル名（`pjsNNN`）昇順走査・音声デコード不要）。

    P2 修正 (review #262 R5・`r3789400804`): 選択判定のため各候補 .lab を
    `read_bytes()` し、その場で decode/parse してトークン化する（従来の
    `parse_lab(path)` 呼び出しは内部で独自に read していた）。選択された
    ファイルについては read 済みのバイト列を `lab_bytes_by_path` として
    返し、呼び出し側（`build_donor_bank_lab`）がハッシュ計算・mora 分解の
    両方でそのまま再利用できるようにする。旧実装は選択段の read がハッシュ/
    分析段の read と分離していたため（split-read）、選択→ハッシュ→分析の間に
    .lab が変わると `selection_stats`（coverage 報告）と実際に構築される
    bank の内容が乖離しうる TOCTOU 窓があった。

    P2 修正 (review #262 R10・`r3789520243`): coverage 判定を生ラベル
    トークンの集合演算（`{p.phoneme for p in phonemes}`）から、
    `_usable_onsets_and_vowels`（`group_lab_to_morae` でグルーピングし
    クランプ後に実際に unit/clip 化できる区間）ベースへ変更する。dangling
    な子音トークン（母音に接続しない、または境界で破棄される）や
    `MIN_UNIT_FRAMES_ABS` 未満に丸まる母音区間が偽って「covered」と報告され、
    貪欲選択が同じ音素を持つ後続ファイルの有効な収録を skip する実害を防ぐ
    （`build_donor_bank_lab` の実際の unit/clip 化条件と一致させる）。
    """
    required = set(required_onsets)
    covered_onsets: set = set()
    covered_vowels: set = set()
    selected: List[Path] = []
    lab_bytes_by_path: Dict[Path, bytes] = {}
    for path in sorted(lab_paths, key=lambda p: p.stem):
        lab_bytes = path.read_bytes()
        phonemes = parse_lab_text(lab_bytes.decode("utf-8"))
        usable_onsets, usable_vowels = _usable_onsets_and_vowels(phonemes, required, frame_period_ms)
        new_onsets = usable_onsets - covered_onsets
        new_vowels = usable_vowels - covered_vowels
        if new_onsets or new_vowels or len(selected) < min_files:
            selected.append(path)
            lab_bytes_by_path[path] = lab_bytes
            covered_onsets |= usable_onsets
            covered_vowels |= usable_vowels
        if len(selected) >= max_files:
            break
        if covered_onsets >= required and covered_vowels >= set(VOWELS_5) and len(selected) >= min_files:
            break
    stats = dict(
        n_files_selected=len(selected), n_files_total=len(lab_paths),
        covered_onsets=sorted(covered_onsets), missing_onsets=sorted(required - covered_onsets),
        covered_vowels=sorted(covered_vowels), min_files=min_files, max_files=max_files,
    )
    return selected, stats, lab_bytes_by_path


def build_donor_bank_lab(
    corpus_root: str | Path,
    target_median_hz: float,
    cache_dir: Optional[str | Path] = None,
    frame_period_ms: float = FRAME_PERIOD_MS,
    min_files: int = 6,
    max_files: int = 10,
) -> Tuple[DonorBank, Dict[int, str], Dict[str, List[ConsonantClip]], dict]:
    """PJS コーパスルート（`pjsNNN/pjsNNN.lab` + `pjsNNN_song.wav` を含む）を分析する。

    `target_median_hz`: 呼び出し側の score から求めた目標音域の中央値
    （render.py が targets の中央値を渡す）。donor の中央値ピッチとの差を
    最寄りのオクターブへ丸めた移調量として `median_f0` へ適用する。

    戻り値の形は `donor_bank_utau.build_donor_bank_utau` と同一（対称設計）。
    """
    root = Path(corpus_root)
    lab_paths = sorted(root.glob("pjs*/pjs*.lab"))
    if not lab_paths:
        raise FileNotFoundError(f"no pjs*/pjs*.lab found under {root}")

    # P1 修正 (review #262): .lab 選択（`_select_lab_files`）は音声デコード
    # 不要な軽量処理のため、キャッシュ判定より前倒しして選択された .lab /
    # 対応する _song.wav の内容ハッシュをキー材料へ含める（donor_bank_utau の
    # v1/v2 builder と同じ是正・同じ理由: root path + options のみのキーだと
    # `--cache-dir` 再利用時のコーパス編集が検知されない）。
    selected_paths, selection_stats, lab_bytes_by_path = _select_lab_files(
        lab_paths, REQUIRED_ONSETS, min_files, max_files, frame_period_ms=frame_period_ms
    )
    # P2 修正 (review #262 R6・`r3789428508`): 選択済み .lab に対応する
    # `_song.wav` が欠けている場合は fail-closed で拒否する。旧実装は後段の
    # 分析ループで黙って skip（`if wav_bytes is None: continue`）しつつ、
    # `selection_stats`（coverage 報告）と `stats["n_files_analyzed"]
    # =len(selected_paths)` は選択段の値をそのまま使い続けるため、実際には
    # 分析されなかった .lab の音素被覆・ファイル数を「達成済み」として偽って
    # 報告していた（部分的にしかコピーされていないコーパスで render が
    # 「録音済みカバレッジあり」と偽装したまま synthetic フォールバックへ
    # 静かに縮退しうる実害）。
    missing_wav = [
        str((p.parent / f"{p.stem}_song.wav").relative_to(root))
        for p in selected_paths
        if not (p.parent / f"{p.stem}_song.wav").exists()
    ]
    if missing_wav:
        raise FileNotFoundError(
            "selected PJS .lab files are missing their paired _song.wav "
            f"(partial corpus under {root}?): {missing_wav}"
        )
    # P2 修正 (review #262 R3・R5): 選択された .lab / _song.wav を「1 回 read
    # したバイト列から hash と decode/parse の両方を導出」する形に統一する
    # （measure_bands.py R1 修正・donor_bank_utau.py 同種修正と同じ流儀）。
    # .lab は `_select_lab_files` が選択判定のため既に read 済みのバイト列を
    # `lab_bytes_by_path` として返すため、ここで再度 read しない（R5
    # `r3789400804` 是正: 旧実装は選択段の read とは独立に、ここでもう一度
    # `read_bytes()` していた = split-read。TOCTOU 窓）。_song.wav は
    # ここが初読のため通常どおり read する。
    content_hashes: List[Tuple[str, str]] = []
    wav_bytes_by_lab: Dict[Path, bytes] = {}
    for lab_path in selected_paths:
        lab_bytes = lab_bytes_by_path[lab_path]
        rel_lab = str(lab_path.relative_to(root))
        content_hashes.append((rel_lab, hashlib.sha256(lab_bytes).hexdigest()))
        wav_path = lab_path.parent / f"{lab_path.stem}_song.wav"
        # fail-closed 検査（上）で選択済み .lab は全て対応 wav が存在すると
        # 確認済みのため、ここでは無条件に read する（黙って skip しない）。
        wav_bytes = wav_path.read_bytes()
        wav_bytes_by_lab[lab_path] = wav_bytes
        content_hashes.append((str(wav_path.relative_to(root)), hashlib.sha256(wav_bytes).hexdigest()))
    # P2 修正 (review #262 R2): (相対パス, sha256) ペアで集約する
    # （§ donor_bank_utau.py と同じ理由・同じ是正）。
    content_digest = aggregate_content_hash(content_hashes)

    cache_path: Optional[Path] = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        # P2 修正 (review #262 R9・`r3789495249`): 公開 `wav_sha256` の集約方式
        # 変更（パス非結合 -> パス結合）に伴い v2 -> v3 へマーカー更新
        # （§ donor_bank_utau.py と同じ理由・同じ是正）。
        key_material = (
            f"{root}|{frame_period_ms}|{min_files}|{max_files}|{round(target_median_hz, 3)}"
            f"|content={content_digest}|schema=content-hash-v3"
        )
        key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
        cache_path = cache_dir / f"lab_bank_{key}.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                cached_bank, cached_unit_vowels, cached_consonant_clips, cached_stats = pickle.load(f)
            # P2 修正 (review #262 R3): pickle 復元は保存時の `stats["cache_hit"]
            # =False` をそのまま返していた（donor_bank_utau.py の同種修正と揃える）。
            cached_stats["cache_hit"] = True
            cached_bank.stats["cache_hit"] = True
            return cached_bank, cached_unit_vowels, cached_consonant_clips, cached_stats

    all_f0: List[np.ndarray] = []
    all_sp: List[np.ndarray] = []
    all_ap: List[np.ndarray] = []
    raw_units: List[dict] = []  # index への確定前の中間表現（transpose 前）
    unit_vowels: Dict[int, str] = {}
    consonant_clips: Dict[str, List[ConsonantClip]] = {}
    frame_offset = 0
    # P2 修正 (review #262 R9・`r3789495249`): § donor_bank_utau.py と同じ理由・
    # 同じ是正（(相対パス, digest) ペアで集約する）。
    wav_sha_pairs: List[Tuple[str, str]] = []
    n_dropped_short_vowel = 0
    n_mora_no_vowel_skipped = 0
    n_cl_seen_total = 0
    n_dangling_onset_total = 0

    for lab_path in selected_paths:
        # P2 修正 (review #262 R3): prepass で既に read 済みのバイト列を
        # 再利用する（split-read を避ける。§ prepass コメント参照）。
        wav_bytes = wav_bytes_by_lab.get(lab_path)
        if wav_bytes is None:
            continue
        phonemes = parse_lab_text(lab_bytes_by_path[lab_path].decode("utf-8"))
        morae, mstats = group_lab_to_morae(phonemes)
        n_cl_seen_total += mstats["n_cl_seen"]
        n_dangling_onset_total += mstats["n_dangling_onset_dropped"]

        wav_path = lab_path.parent / f"{lab_path.stem}_song.wav"
        x, sr = _load_wav_24k_bytes(wav_bytes, source=str(wav_path))
        wav_sha_pairs.append((str(wav_path.relative_to(root)), hashlib.sha256(wav_bytes).hexdigest()))
        donor = analyze_donor_world(x, sr, frame_period_ms)
        n_donor_frames = len(donor["f0"])

        for mora in morae:
            if mora.vowel is None:
                n_mora_no_vowel_skipped += 1
                continue
            v_start = max(0, min(_ms_to_frame(mora.vowel_start_s, frame_period_ms), n_donor_frames))
            v_end = max(0, min(_ms_to_frame(mora.vowel_end_s, frame_period_ms), n_donor_frames))
            if v_end - v_start >= MIN_UNIT_FRAMES_ABS:
                seg_f0 = donor["f0"][v_start:v_end]
                voiced = seg_f0[seg_f0 > 0]
                raw_median_f0 = float(np.median(voiced)) if len(voiced) else 0.0
                duration_s = (v_end - v_start) * frame_period_ms / 1000.0
                head = _log_band_vector(donor["sp"][v_start], sr)
                tail = _log_band_vector(donor["sp"][v_end - 1], sr)
                raw_units.append(
                    dict(
                        start_frame=frame_offset + v_start, end_frame=frame_offset + v_end,
                        raw_median_f0=raw_median_f0, duration_s=duration_s,
                        head_log_bands=head, tail_log_bands=tail, vowel=mora.vowel,
                    )
                )
            else:
                n_dropped_short_vowel += 1

            if mora.onset is not None and mora.onset in REQUIRED_ONSETS and mora.onset_start_s is not None:
                c_start = max(0, min(_ms_to_frame(mora.onset_start_s, frame_period_ms), n_donor_frames))
                c_end = max(0, min(_ms_to_frame(mora.onset_end_s, frame_period_ms), n_donor_frames))
                if c_end - c_start >= 1:
                    clip = ConsonantClip(
                        onset=mora.onset, sp=donor["sp"][c_start:c_end].copy(), ap=donor["ap"][c_start:c_end].copy(),
                        n_frames=c_end - c_start, source_wav=wav_path.name,
                        source_alias=f"{lab_path.stem}:{mora.onset}@{mora.onset_start_s:.3f}",
                        is_phrase_initial=False,
                    )
                    consonant_clips.setdefault(mora.onset, []).append(clip)

        all_f0.append(donor["f0"])
        all_sp.append(donor["sp"])
        all_ap.append(donor["ap"])
        frame_offset += n_donor_frames

    # --- オクターブ移調（選択コスト用 median_f0 にのみ適用） ---
    voiced_raw_medians = [u["raw_median_f0"] for u in raw_units if u["raw_median_f0"] > 0]
    donor_median_hz = float(np.median(voiced_raw_medians)) if voiced_raw_medians else 0.0
    transpose_semitones = compute_octave_transpose(donor_median_hz, target_median_hz)
    transpose_ratio = 2.0 ** (transpose_semitones / 12.0)

    units: List[DonorUnit] = []
    for u in raw_units:
        idx = len(units)
        adj_f0 = u["raw_median_f0"] * transpose_ratio if u["raw_median_f0"] > 0 else 0.0
        units.append(
            DonorUnit(
                index=idx, start_frame=u["start_frame"], end_frame=u["end_frame"],
                median_f0=adj_f0, duration_s=u["duration_s"],
                head_log_bands=u["head_log_bands"], tail_log_bands=u["tail_log_bands"],
            )
        )
        unit_vowels[idx] = u["vowel"]

    for onset in list(consonant_clips.keys()):
        consonant_clips[onset] = sorted(consonant_clips[onset], key=lambda c: (c.source_wav, c.source_alias))

    bank_f0 = np.concatenate(all_f0) if all_f0 else np.zeros(0)
    bank_sp = np.concatenate(all_sp, axis=0) if all_sp else np.zeros((0, N_LOG_BANDS))
    bank_ap = np.concatenate(all_ap, axis=0) if all_ap else np.zeros((0, N_LOG_BANDS))
    # P2 修正 (review #262 R9・`r3789495249`): § donor_bank_utau.py と同じ
    # 理由・同じ是正（v1/v2/PJS 対称）。
    wav_sha256 = aggregate_content_hash(wav_sha_pairs)

    stats = dict(
        n_files_analyzed=len(selected_paths), n_units_kept=len(units),
        n_dropped_short_vowel=n_dropped_short_vowel, n_mora_no_vowel_skipped=n_mora_no_vowel_skipped,
        n_cl_seen_total=n_cl_seen_total, n_dangling_onset_dropped_total=n_dangling_onset_total,
        n_consonant_clips_by_onset={k: len(v) for k, v in sorted(consonant_clips.items())},
        vowel_distribution={v: sum(1 for lbl in unit_vowels.values() if lbl == v) for v in VOWELS_5}
        | {"N": sum(1 for lbl in unit_vowels.values() if lbl == "N")},
        donor_median_hz_raw=donor_median_hz, target_median_hz=target_median_hz,
        transpose_semitones=transpose_semitones, selection_stats=selection_stats,
        cache_hit=False,
    )

    bank = DonorBank(
        sr=SR, frame_period_ms=frame_period_ms, f0=bank_f0, sp=bank_sp, ap=bank_ap,
        units=units, wav_sha256=wav_sha256, source="pjs_lab", notes_csv_path=None, stats=stats,
    )

    result = (bank, unit_vowels, consonant_clips, stats)
    if cache_path is not None:
        # P2 修正 (review #262 R5・`r3789400805`): staging + `os.replace` で
        # atomic 公開する（§ donor_bank_utau.py の同種是正と対称の設計。
        # 直書きだと中断時に破損 pickle が残り、以後 `cache_path.exists()`
        # が真のまま全リクエストが読み込み失敗し続ける）。
        atomic_pickle_dump(result, cache_path)
    return result
