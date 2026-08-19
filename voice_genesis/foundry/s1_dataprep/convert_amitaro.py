"""S6: あみたろ ITA コーパス読み上げ音声（実録音発音教師）の intake 変換。

正本: `DESIGN_S6_run7.md` §2-3（選定規則）/ §2-4（変換系）。run 7 の
単一介入 = 教師の実体交換（d3synth 引退 → amitaro 投入・同 dosage）の
教師側データを、**決定論・fail-closed・全数記帳**で生成する。

設計の要点（DESIGN_S6 §2-3 の逐条実装）:

1. **走査対象 = recitation 324 文のみ・文番号昇順**（emotion 100 文は
   不使用 — 感情演技の韻律変動は発音教師として交絡・§0-5）。スタイルは
   「ノーマル」（配布側の音声読み上げ向けスタイル・1 文 1 ファイル
   `recitationNNN.wav`）。
2. **除外は走査中に適用**: dsdict 未収載 grapheme・アラインメント不能は
   `convert_user.py` T2 と同じ fail-closed 流儀でその場で除外・記帳し、
   除外文の ph_dur は dosage 累積に**算入しない**。
3. **打ち切り = 採用文 ph_dur 累積が `DOSAGE_TARGET_S`（= D3 pin の
   csv_ph_dur_total_seconds 1200.5 s）に到達した文**（到達文を含む）。
4. **dosage の fail-closed 検査**: 最終合計が target ±10% に入らなければ
   エラー停止（過少/過大 dosage の教師セットを黙って出力しない — §0-3 の
   同 dosage 契約が Q12 帰属の前提）。
5. **被覆検査**: ①局所退行に対応する目標音素（さ行 onset「s」・文末
   「す」= 末尾 [s, u]）が採用セットに含まれることを機械検査。
6. 変換機構 = `convert_user.py` T2 の read-only 再利用（dsdict 最長一致
   音素化・RMS ラン分割 + 最小ギャップ併合・フレーズ毎 f0 中央値 MIDI・
   モーラ重み配分）。**T0/T1/T2 の実装は一切変更しない**（run 4/5/6 の
   user 出力バイト再現を壊さないため、本モジュールは import のみ行う）。
7. 出力 = `transcriptions.csv` + `wavs/`（pinned ffmpeg で 44.1kHz 化 →
   `--normalize-loudness-lufs` で BS.1770 正規化〔run 6 と同じ実装・
   目標値の単一ソースは run6/run7 dataset pins〕）+ `exclusions.json` +
   `loudness_normalization.json` + `selection.json`（選定来歴の全数記帳 —
   run 7 pins の pin 対象）。staging 完全構築 → 原子的 swap
   （`convert_d3._swap_into_place`）。

読みテキストの正本 = GitHub mmorise/ita-corpus の
`recitation_transcript_utf8.txt`（パブリックドメイン宣言の台本集・
commit/sha256 は `results_s3/amitaro_intake_ledger.json` に pin。repo には
`s1_dataprep/data/ita_recitation_transcript_utf8.txt` としてバイト同一で
収載する）。行形式は `RECITATION324_NNN:漢字文,カタカナ読み`。読みの
非カナ文字は実測で 「、」「。」「？」 の 3 種のみ（2026-08-19・324 行全数
走査）— この 3 種をフレーズ区切りとして凍結する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import convert_d3  # noqa: E402
import convert_user as cu  # noqa: E402

# --- 選定規則の凍結値（DESIGN_S6_run7.md §2-3） -----------------------------
N_RECITATION = 324
WAV_NAME_FMT = "recitation{:03d}.wav"
SENTENCE_ID_FMT = "RECITATION324_{:03d}"
# D3 pin（run4_dataset_pins.json d3.csv_ph_dur_total_seconds）と同 dosage。
DOSAGE_TARGET_S = 1200.5
DOSAGE_TOLERANCE = 0.10
# 実測（モジュール docstring 末尾）: recitation 読みの非カナ文字は 3 種のみ。
PHRASE_SPLIT_CHARS = "、。？"
# ITA コーパス読み上げ音声の配布仕様（intake ledger に逐語 pin 済み）。
EXPECTED_SOURCE_SR = 48000

SELECTION_REPORT_NAME = "selection.json"
SELECTION_SCHEMA = "amitaro-selection/0.1"
EXCLUSIONS_SCHEMA = "convert-amitaro-exclusions/0.1"
LOUDNESS_REPORT_NAME = cu.LOUDNESS_REPORT_NAME
LOUDNESS_REPORT_SCHEMA = "amitaro-loudness-normalization/0.1"


class ConvertAmitaroError(ValueError):
    """intake 契約違反（transcript 形状・staged 欠落・dosage 帯逸脱・被覆
    不足・duration 自己検査不一致）で送出する fail-closed 例外。`--out-dir`
    へは一切公開しない。"""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_transcript(path: Path) -> Dict[int, Dict[str, str]]:
    """`recitation_transcript_utf8.txt` を parse して
    `{文番号: {"sentence_id", "text", "reading"}}` を返す。行数 324・
    文番号 1..324 連続・`ID:text,reading` 形状を fail-closed で検査する
    （転写正本のドリフト = 変換出力が変わるため黙って通さない）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in lines if ln.strip()]
    if len(lines) != N_RECITATION:
        raise ConvertAmitaroError(
            f"transcript: expected {N_RECITATION} non-empty line(s), got {len(lines)} "
            f"({path.name}; fail-closed — 転写正本が pin と別物の疑い)"
        )
    out: Dict[int, Dict[str, str]] = {}
    for i, line in enumerate(lines, start=1):
        expected_id = SENTENCE_ID_FMT.format(i)
        if ":" not in line:
            raise ConvertAmitaroError(f"transcript line {i}: no ':' separator ({line!r})")
        sentence_id, rest = line.split(":", 1)
        if sentence_id != expected_id:
            raise ConvertAmitaroError(
                f"transcript line {i}: sentence id {sentence_id!r} != expected "
                f"{expected_id!r} (fail-closed — 行順/欠番の疑い)"
            )
        if "," not in rest:
            raise ConvertAmitaroError(
                f"transcript line {i}: no ',' separator between text and reading ({line!r})"
            )
        text, reading = rest.split(",", 1)
        if not reading.strip():
            raise ConvertAmitaroError(f"transcript line {i}: empty reading (fail-closed)")
        out[i] = {"sentence_id": sentence_id, "text": text, "reading": reading}
    return out


def _kata_to_hira(text: str) -> str:
    """カタカナ（ァ..ヶ）を対応するひらがなへ写す（U+30A1..U+30F6 → -0x60）。
    ritsu dsdict は基本モーラを**ひらがな**グラフェムで、外来音結合
    （ティ/ファ 等）を**カタカナ**グラフェムで持つ（2026-08-19 実測: ITA
    カタカナ読みを生のまま引くと『オ』『ニ』等の基本モーラが unmapped に
    なり 324 文中 304 文が除外された）。`convert_user._normalize_sokuon_nasal`
    と同じ「同一音素の表記ゆれの正規化・辞書に無い別の音への代用ではない」
    原則の全カナ版。ヴ 等ひらがな側にも辞書にも無い音はこの写像後も
    unmapped のまま fail-closed 除外される（代用しない）。"""
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text
    )


def tokenize_reading_with_dsdict(
    dsdict: "cu.DsDict", phrase: str
) -> List[Tuple[str, List[str]]]:
    """`convert_user.tokenize_with_dsdict` と同じ最長一致だが、各候補
    substring を「**ひらがな正規化 → 生のまま**」の順で引く 2 段 lookup を
    行う。順序の根拠（2026-08-19 実測 2 点）:

    1. ritsu dsdict は基本モーラを**ひらがな = 小文字母音**（す→[s,u]・
       か→[k,a]）で、同じモーラの**カタカナ**を**大文字母音**（ス→[s,U]・
       カ→[k,A] — 別音素クラス）で持つ。ITA カタカナ読みを生優先で引くと
       教師の母音がほぼ全て大文字クラスへ落ち、歌唱コーパス（ritsu/pjs/
       user/ゲート曲）が使う通常母音と**不連続**になる — 発音教師の効果が
       標的音素（例: 語尾 s u）に届かない。よって既定はひらがな側。
    2. 外来音結合（ティ/ファ 等）は**カタカナ側にしか無い**（ひらがな写像
       「てぃ」等は未収載）。生 lookup はこのフォールバックとして残す —
       user T2 カードの表記慣行（基本モーラ = ひらがな・外来音 = カタカナ）
       と同じ音素割当になる。

    長音「ー」は同様に独立マーカー。どちらでも当たらない文字は
    `DsDictTokenizeError`（fail-closed・代用なし）。"""
    text = phrase
    n = len(text)
    out: List[Tuple[str, List[str]]] = []
    i = 0
    while i < n:
        if text[i] == "ー":
            out.append(("ー", []))
            i += 1
            continue
        matched = False
        max_try = min(dsdict.max_grapheme_len, n - i)
        for length in range(max_try, 0, -1):
            candidate = text[i:i + length]
            phonemes = dsdict.table.get(_kata_to_hira(candidate))
            if phonemes is None:
                phonemes = dsdict.table.get(candidate)
            if phonemes is not None:
                out.append((candidate, phonemes))
                i += length
                matched = True
                break
        if not matched:
            raise cu.DsDictTokenizeError(
                f"unmapped grapheme in dsdict lookup: {text[i]!r} (phrase={phrase!r})"
            )
    return out


def weighted_tokens_for_reading(
    dsdict: "cu.DsDict", phrase: str
) -> List[Tuple[List[str], float]]:
    """`convert_user.weighted_tokens_for_phrase` と同一の重み規則
    （長音 = 直前トークンへ `CHOON_EXTRA_WEIGHT` 加算・促音 `cl` =
    `SOKUON_WEIGHT`・他 1.0）を、`tokenize_reading_with_dsdict`（2 段
    lookup 版）の上で適用する。"""
    raw = tokenize_reading_with_dsdict(dsdict, phrase)
    weighted: List[List[object]] = []
    for grapheme, phonemes in raw:
        if grapheme == "ー":
            if not weighted:
                raise cu.DsDictTokenizeError(
                    f"phrase starts with a long-vowel mark, no preceding token to "
                    f"extend: {phrase!r}"
                )
            weighted[-1][1] = weighted[-1][1] + cu.CHOON_EXTRA_WEIGHT
            continue
        weight = cu.SOKUON_WEIGHT if phonemes == ["cl"] else 1.0
        weighted.append([list(phonemes), weight])
    return [(phonemes, weight) for phonemes, weight in weighted]


def split_reading_phrases(reading: str) -> List[str]:
    """カタカナ読みを句読点（`PHRASE_SPLIT_CHARS`）でフレーズ列へ分割する
    （空要素は落とす）。読み全体が空になる場合は fail-closed。"""
    phrases: List[str] = []
    buf: List[str] = []
    for ch in reading:
        if ch in PHRASE_SPLIT_CHARS:
            if buf:
                phrases.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        phrases.append("".join(buf))
    if not phrases:
        raise ConvertAmitaroError(f"reading produced zero phrase(s): {reading!r}")
    return phrases


def _process_sentence(
    name: str, samples, sr: int, phrases: Sequence[str], dsdict: "cu.DsDict"
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """1 文を `convert_user._process_t2_card` と同一の機構で行へ変換する
    （フレーズ列を外から渡す点だけが異なる — T2 は固定表 `T2_PHRASES` を
    引くが、本関数は transcript 由来のフレーズ列を使う）。戻り値は
    `(row, None)` または `(None, 除外理由)`。"""
    tokens_per_phrase: List[List[Tuple[List[str], float]]] = []
    for phrase in phrases:
        try:
            tokens_per_phrase.append(weighted_tokens_for_reading(dsdict, phrase))
        except cu.DsDictTokenizeError as exc:
            return None, (
                f"{name}: unmapped grapheme in dsdict for phrase {phrase!r}: {exc}"
            )

    segs = cu._segments_by_gap(
        samples, sr, silence_db=cu.SILENCE_DB, frame_sec=cu.FRAME_SEC,
        min_gap_sec=cu.T2_MIN_GAP_SEC, min_run_sec=cu.T2_MIN_RUN_SEC,
    )
    segs, err = cu._reconcile_t2_segment_count(segs, len(phrases))
    if err is not None:
        return None, f"{name}: {err}"
    assert segs is not None

    builder = cu._RowBuilder()
    total_dur = len(samples) / sr
    prev_end = 0.0
    for tokens, (start, end) in zip(tokens_per_phrase, segs):
        builder.add_rest(start - prev_end)
        seg_dur = end - start
        f0 = cu._f0_median_hz(samples, sr, start, end)
        if f0 is None:
            return None, (
                f"{name}: no voiced f0 found in phrase run "
                f"({start:.3f}-{end:.3f}s) (fail-closed)"
            )
        midi = cu._hz_to_midi_round(f0)
        if not tokens:
            return None, f"{name}: phrase produced zero dsdict tokens (fail-closed)"
        total_weight = sum(weight for _, weight in tokens)
        if total_weight <= 0:
            return None, f"{name}: phrase has non-positive total token weight (fail-closed)"
        unit_dur = seg_dur / total_weight
        for phonemes, weight in tokens:
            note_dur = unit_dur * weight
            if note_dur <= 0.0:
                return None, f"{name}: allocated non-positive note duration (fail-closed)"
            durations = cu._durations_for_phonemes(phonemes, note_dur)
            builder.add_note(phonemes, durations, midi, note_dur)
        prev_end = end
    builder.add_rest(total_dur - prev_end)
    return builder.to_row(name), None


def _row_ph_durs(row: Dict[str, str]) -> Tuple[float, float]:
    """行の `(total_ph_dur_s, voiced_ph_dur_s)`（voiced = SP を除く）。
    total は D3 pin `csv_ph_dur_total_seconds` と同基準（SP 込み）、voiced は
    規約の会計基準「無音部を除いた発話時間」（DESIGN_S6 §2-5）と同型。"""
    tokens = row["ph_seq"].split()
    durs = [float(x) for x in row["ph_dur"].split()]
    total = math.fsum(durs)
    voiced = math.fsum(d for tok, d in zip(tokens, durs) if tok != "SP")
    return total, voiced


def _coverage_problems(rows: Sequence[Dict[str, str]]) -> List[str]:
    """DESIGN_S6 §2-3-6 の被覆検査: ①局所退行の目標音素が採用セットに
    含まれるか。(a) さ行 onset「s」の存在、(b) 文末「す」= 行末尾（末尾の
    SP を除いた最後の 2 音素）が [s, u] の行の存在。"""
    problems: List[str] = []
    has_s_onset = any("s" in row["ph_seq"].split() for row in rows)
    if not has_s_onset:
        problems.append(
            "coverage: no 's' onset phoneme in the selected set "
            "(さ行 onset — ①局所退行の目標音素。選定規則の再裁定が必要)"
        )
    def _ends_with_su(row: Dict[str, str]) -> bool:
        tokens = [t for t in row["ph_seq"].split()]
        while tokens and tokens[-1] == "SP":
            tokens.pop()
        return len(tokens) >= 2 and tokens[-2] == "s" and tokens[-1] == "u"
    if not any(_ends_with_su(row) for row in rows):
        problems.append(
            "coverage: no sentence-final [s, u] row in the selected set "
            "(語尾「す」 — ①局所退行の目標音素。選定規則の再裁定が必要)"
        )
    return problems


def convert(
    staged_dir: Path,
    transcript_path: Path,
    dsdict_path: Path,
    out_dir: Path,
    normalize_loudness_lufs: float,
    ffmpeg_bin: Optional[str] = None,
    dosage_target_s: float = DOSAGE_TARGET_S,
    dosage_tolerance: float = DOSAGE_TOLERANCE,
    duration_tolerance_sec: float = cu.DEFAULT_DURATION_TOLERANCE_SEC,
) -> Dict[str, object]:
    """staged 48kHz raw（`recitationNNN.wav`）から教師データセットを生成する
    （モジュール docstring の逐条規則。正規化目標 `normalize_loudness_lufs`
    は **必須引数** — run 7 の教師 intake は正規化込みが契約であり、既定
    OFF の任意オプションにしない〔DESIGN_S6 §2-4 の正直会計注記どおり、
    d3synth との差分として selection.json にも記帳する〕）。"""
    staged_dir = Path(staged_dir)
    transcript_path = Path(transcript_path)
    dsdict_path = Path(dsdict_path)
    out_dir = Path(out_dir)

    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    convert_d3._reject_output_collision(
        [out_dir, old_dir, staging_dir],
        protected_roots=[staged_dir],
        protected_files=[transcript_path, dsdict_path],
    )

    transcript = parse_transcript(transcript_path)
    dsdict = cu.load_dsdict(dsdict_path)

    floor_s = dosage_target_s * (1.0 - dosage_tolerance)
    ceil_s = dosage_target_s * (1.0 + dosage_tolerance)

    rows: List[Dict[str, str]] = []
    included: List[str] = []
    excluded: List[Dict[str, str]] = []
    staged_sha: Dict[str, str] = {}
    per_card: Dict[str, Dict[str, float]] = {}
    wav_paths: Dict[str, Path] = {}
    cum_total = 0.0
    cum_voiced = 0.0
    last_scanned = 0

    for i in range(1, N_RECITATION + 1):
        wav_name = WAV_NAME_FMT.format(i)
        name = wav_name[: -len(".wav")]
        wav_path = staged_dir / wav_name
        if not wav_path.exists():
            raise ConvertAmitaroError(
                f"staged source missing: {wav_name} (走査は文番号昇順で欠落を"
                "許さない — 打ち切り前の全ファイルが staged されている必要が"
                "ある。fail-closed)"
            )
        last_scanned = i
        staged_sha[wav_name] = _sha256_file(wav_path)

        samples, sr = cu._load_mono(wav_path)
        if sr != EXPECTED_SOURCE_SR:
            raise ConvertAmitaroError(
                f"{wav_name}: sample rate {sr} != expected {EXPECTED_SOURCE_SR} "
                "(ITA 配布仕様と不一致 — 素材取り違えの疑い。fail-closed)"
            )

        # transcript 由来の異常（空読み等）は個別除外でなく全体停止
        # （正本ドリフトの疑い — split_reading_phrases が fail-closed 送出）。
        phrases = split_reading_phrases(transcript[i]["reading"])

        row, err = _process_sentence(name, samples, sr, phrases, dsdict)
        if err is not None:
            excluded.append({"card_id": name, "reason": err})
            continue
        assert row is not None

        total, voiced = _row_ph_durs(row)
        wav_dur = len(samples) / sr
        if abs(total - wav_dur) > duration_tolerance_sec:
            raise ConvertAmitaroError(
                f"{name}: ph_dur total {total:.4f}s != wav duration {wav_dur:.4f}s "
                f"(tolerance {duration_tolerance_sec}s; fail-closed)"
            )

        rows.append(row)
        included.append(name)
        wav_paths[name] = wav_path
        per_card[name] = {
            "total_ph_dur_s": round(total, 3),
            "voiced_ph_dur_s": round(voiced, 3),
        }
        cum_total += total
        cum_voiced += voiced
        if cum_total >= dosage_target_s:
            break

    # --- dosage fail-closed（DESIGN_S6 §2-3-5） ------------------------------
    if not (floor_s <= cum_total <= ceil_s):
        raise ConvertAmitaroError(
            f"dosage out of band: total ph_dur {cum_total:.1f}s not in "
            f"[{floor_s:.1f}, {ceil_s:.1f}] (target {dosage_target_s}s ±"
            f"{dosage_tolerance:.0%}; scanned {last_scanned}/{N_RECITATION}, "
            f"included {len(included)}, excluded {len(excluded)}) — 過少/過大 "
            "dosage の教師セットは出力しない。選定規則は DESIGN_S6 の改訂で"
            "裁定し直す (fail-closed)"
        )

    # --- 被覆検査（DESIGN_S6 §2-3-6） ----------------------------------------
    coverage = _coverage_problems(rows)
    if coverage:
        raise ConvertAmitaroError(
            "coverage check failed: " + "; ".join(coverage)
        )

    # --- 原子的公開 -----------------------------------------------------------
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        summary = _publish_into(
            rows, included, excluded, wav_paths, staging_dir,
            ffmpeg_bin=ffmpeg_bin, dsdict=dsdict,
            normalize_loudness_lufs=normalize_loudness_lufs,
            staged_sha=staged_sha, per_card=per_card,
            dosage={
                "target_s": dosage_target_s,
                "tolerance": dosage_tolerance,
                "floor_s": round(floor_s, 3),
                "ceil_s": round(ceil_s, 3),
                "actual_total_ph_dur_s": round(cum_total, 3),
                "actual_voiced_ph_dur_s": round(cum_voiced, 3),
            },
            scan={"n_scanned": last_scanned, "n_total": N_RECITATION},
            transcript_provenance={
                "path": transcript_path.name,
                "sha256": _sha256_file(transcript_path),
            },
        )
        convert_d3._swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return summary


def _publish_into(
    rows: List[Dict[str, str]],
    included: List[str],
    excluded: List[Dict[str, str]],
    wav_paths: Dict[str, Path],
    staging_dir: Path,
    *,
    ffmpeg_bin: Optional[str],
    dsdict: "cu.DsDict",
    normalize_loudness_lufs: float,
    staged_sha: Dict[str, str],
    per_card: Dict[str, Dict[str, float]],
    dosage: Dict[str, object],
    scan: Dict[str, object],
    transcript_provenance: Dict[str, str],
) -> Dict[str, object]:
    import csv as _csv

    out_wavs = staging_dir / "wavs"
    out_wavs.mkdir(parents=True, exist_ok=True)

    # recitationNNN は 3 桁ゼロ詰めのため、辞書順ソート = 文番号順。
    rows_sorted = sorted(rows, key=lambda r: r["name"])
    with open(staging_dir / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(
            f, fieldnames=["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]
        )
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow(row)

    for name in sorted(included):
        convert_d3.resample_to_44k1(
            wav_paths[name], out_wavs / f"{name}.wav", ffmpeg_bin=ffmpeg_bin
        )

    # 教師 intake の正規化（DESIGN_S6 §2-4・run 6 と同一実装の再利用）。
    entries: Dict[str, object] = {}
    for name in sorted(included):
        entries[f"{name}.wav"] = cu.normalize_wav_loudness_in_place(
            out_wavs / f"{name}.wav", normalize_loudness_lufs
        )
    loudness_report = {
        "schema": LOUDNESS_REPORT_SCHEMA,
        "target_lufs": normalize_loudness_lufs,
        "method": (
            "BS.1770 integrated loudness (pyloudnorm.Meter) -> linear gain only; "
            "peak guard = sample peak vs PCM_16 limit (fail-closed); "
            "post_lufs re-measured from the written file"
        ),
        "entries": entries,
    }
    (staging_dir / LOUDNESS_REPORT_NAME).write_text(
        json.dumps(loudness_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    dsdict_provenance = {
        "path": Path(dsdict.path).name, "sha256": dsdict.sha256,
        "n_graphemes": len(dsdict.table),
    }
    excluded_sorted = sorted(excluded, key=lambda e: e["card_id"])
    exclusions_report = {
        "schema": EXCLUSIONS_SCHEMA,
        "dsdict": dsdict_provenance,
        "n_included": len(included),
        "n_excluded": len(excluded_sorted),
        "included_cards": sorted(included),
        "excluded": excluded_sorted,
    }
    (staging_dir / "exclusions.json").write_text(
        json.dumps(exclusions_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    selection_report = {
        "schema": SELECTION_SCHEMA,
        "rule": (
            "DESIGN_S6_run7.md §2-3: recitation のみ・文番号昇順・ノーマル"
            "スタイル 1 文 1 ファイル・除外は走査中適用（累積に不算入）・"
            "採用文 total ph_dur 累積が target 到達で打ち切り・最終 dosage "
            "±tolerance を assert・被覆検査（さ行 onset / 文末 [s,u]）"
        ),
        "dosage": dosage,
        "scan": scan,
        "included": sorted(included),
        "excluded": excluded_sorted,
        "per_card_ph_dur": {k: per_card[k] for k in sorted(per_card)},
        "staged_source_sha256": {k: staged_sha[k] for k in sorted(staged_sha)},
        "loudness_note": (
            "教師 intake は -X LUFS 正規化込み（DESIGN_S6 §2-4 正直会計: "
            "引退した d3synth は無正規化 — 教師交換に随伴する差分として記帳）"
        ),
        "transcript": transcript_provenance,
        "dsdict": dsdict_provenance,
    }
    (staging_dir / SELECTION_REPORT_NAME).write_text(
        json.dumps(selection_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    total_ph_dur_s = math.fsum(per_card[name]["total_ph_dur_s"] for name in included)
    voiced_ph_dur_s = math.fsum(per_card[name]["voiced_ph_dur_s"] for name in included)
    return dict(
        n_segments=len(rows_sorted),
        included_cards=sorted(included),
        excluded_cards=excluded_sorted,
        total_ph_dur_s=round(total_ph_dur_s, 3),
        effective_minutes=round(total_ph_dur_s / 60.0, 3),
        voiced_ph_dur_s=round(voiced_ph_dur_s, 3),
        dosage=dosage,
        scan=scan,
        dsdict=dsdict_provenance,
        loudness_normalization=loudness_report,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged-dir", type=Path, required=True,
        help="staged 48kHz raw（recitationNNN.wav）のディレクトリ",
    )
    parser.add_argument(
        "--transcript", type=Path, required=True,
        help="ITA recitation 転写（recitation_transcript_utf8.txt・pin 対象）",
    )
    parser.add_argument(
        "--dsdict", type=Path, required=True,
        help="NamineRitsu_DiffSinger dsdur/dsdict.yaml（音素化辞書）",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--normalize-loudness-lufs", type=float, required=True,
        help="BS.1770 正規化目標（必須。単一ソース = run7 dataset pins の "
             "amitaro.normalization_target_lufs）",
    )
    parser.add_argument("--ffmpeg-bin", type=str, default=None)
    args = parser.parse_args(argv)

    summary = convert(
        staged_dir=args.staged_dir,
        transcript_path=args.transcript,
        dsdict_path=args.dsdict,
        out_dir=args.out_dir,
        normalize_loudness_lufs=args.normalize_loudness_lufs,
        ffmpeg_bin=args.ffmpeg_bin,
    )
    print(json.dumps(
        {k: summary[k] for k in
         ("n_segments", "total_ph_dur_s", "effective_minutes", "voiced_ph_dur_s",
          "dosage", "scan")},
        ensure_ascii=False, indent=2, sort_keys=True))
    print(f"included={len(summary['included_cards'])} "
          f"excluded={len(summary['excluded_cards'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
