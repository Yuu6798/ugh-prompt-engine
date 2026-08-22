"""run8/s7_ledger.py — 標的被覆台帳 `target_exposure_ledger/0.1`（§3）。

**学習データ側を「分数」ではなく「標的イベント数」で数え直す**ための機械集計器。
入力は各話者の `transcriptions.csv`（`name` / `ph_seq` / `ph_dur` と、あれば
`ph_num` / `note_seq` / `note_dur`）だけで、**推定はしない**（§3 末尾）。

実行例:

    python voice_genesis/foundry/run8/s7_ledger.py \\
        --speaker ritsu:VCV:/path/ritsu/transcriptions.csv \\
        --speaker pjs:real_song:/path/pjs/transcriptions.csv \\
        --speaker user:real_song:/path/user/transcriptions.csv \\
        --speaker amitaro:speech:/path/amitaro/transcriptions.csv \\
        --breaking user --breaking ritsu --non-breaking pjs \\
        --out voice_genesis/foundry/results_s7/target_exposure_ledger.json

`--breaking` / `--non-breaking` は §3 の判定分岐 3/4 が要求する
「順序（破綻しない話者 > 破綻する話者）」の**事前登録入力**である。
本モジュールはこの分類を内蔵しない（分類を持つと台帳が結論を先取りする）。
未分類の話者を含む比較は `undetermined` になる。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402
from s7_io import (  # noqa: E402,F401
    OutputCollisionError,
    reject_duplicate_outputs,
    reject_output_collision,
    sha256_bytes,
)

SILENCE_TOKENS = frozenset({"SP", "AP"})
VOWELS = frozenset({"a", "i", "u", "e", "o", "A", "I", "U", "E", "O"})
NASAL_N = "N"


# --- 入力 ------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    name: str
    ph_seq: Tuple[str, ...]
    ph_dur: Tuple[float, ...]
    ph_num: Optional[Tuple[int, ...]] = None
    note_seq: Optional[Tuple[str, ...]] = None
    note_dur: Optional[Tuple[float, ...]] = None


def read_and_parse(csv_path: Path) -> Tuple[List[Row], str, int]:
    """入力を**一度だけ**読み、その同じバイト列を parse と sha256 の両方に使う。

    読み直すと、`parse_rows()` と `sha256_file()` の間で中身が差し替わった場合に
    「古いバイトから数えた count」を「新しいバイトの sha」で pin してしまう
    （PR #300 Codex 第 2 巡 P2）。
    """
    raw, sha, n = s7_io.read_bytes_with_pin(csv_path)
    return parse_text(raw.decode("utf-8"), source=str(csv_path)), sha, n


def parse_rows(csv_path: Path) -> List[Row]:
    """`transcriptions.csv` を読む（テスト・単体利用向けの薄いラッパ）。"""
    return read_and_parse(csv_path)[0]


def parse_text(text: str, source: str = "<text>") -> List[Row]:
    """CSV テキストから行を作る。ph_seq と ph_dur の長さ不一致は fail-closed。"""
    rows: List[Row] = []
    reader = csv.DictReader(io.StringIO(text, newline=""))
    for raw in reader:
        ph_seq = tuple(str(raw["ph_seq"]).split())
        ph_dur = tuple(float(x) for x in str(raw["ph_dur"]).split())
        # `nan` / `inf` / 負値は float() を通ってしまう。放置すると duration_bin()
        # の走査を素通りして最長層 d4 に入り、H-TTD を動かす（PR #300 Codex
        # 第 3 巡 P2）。黙って数えず fail-closed で止める。
        bad = [d for d in ph_dur if not math.isfinite(d) or d < 0.0]
        if bad:
            raise ValueError(
                f"{source}: row {raw.get('name')!r} has non-finite or negative ph_dur: {bad}"
            )
        if len(ph_seq) != len(ph_dur):
            raise ValueError(
                f"{source}: row {raw.get('name')!r} has "
                f"{len(ph_seq)} phones but {len(ph_dur)} durations"
            )
        ph_num = (
            tuple(int(x) for x in str(raw["ph_num"]).split())
            if raw.get("ph_num")
            else None
        )
        note_seq = tuple(str(raw["note_seq"]).split()) if raw.get("note_seq") else None
        note_dur = (
            tuple(float(x) for x in str(raw["note_dur"]).split())
            if raw.get("note_dur")
            else None
        )
        rows.append(
            Row(
                name=str(raw["name"]),
                ph_seq=ph_seq,
                ph_dur=ph_dur,
                ph_num=ph_num,
                note_seq=note_seq,
                note_dur=note_dur,
            )
        )
    return rows


# --- 終端イベントの抽出 ----------------------------------------------------


@dataclass
class TerminalEvent:
    speaker: str
    row_id: str
    phone_index: int          # SP トークンの直前（= 終端モーラの母音）の index
    transition: str           # ri_to_SP | i_to_SP | N_to_SP | su_to_SP | other_to_SP
    preceding_phoneme: str    # ri | i | N | su | other
    position: str             # utterance_final | internal
    onset_phone_seconds: float
    vowel_phone_seconds: float
    preceding_duration_seconds: float
    silence_token: str = "SP"     # 実際にその位置にあった無音トークン（SP | AP）
    midi: Optional[float] = None
    pitch_bin: str = sp.PITCH_BIN_UNKNOWN

    @property
    def event_id(self) -> str:
        return f"{self.speaker}/{self.row_id}/{self.phone_index}"

    @property
    def r_ratio(self) -> Optional[float]:
        total = self.onset_phone_seconds + self.vowel_phone_seconds
        return (self.onset_phone_seconds / total) if total > 0 else None


def duration_bin(seconds: float) -> str:
    for name, lo, hi in sp.DURATION_BINS:
        if lo <= seconds < hi:
            return name
    return sp.DURATION_BINS[-1][0]


def _classify(onset: Optional[str], vowel: str) -> Tuple[str, str]:
    """(preceding_phoneme, transition) を返す。"""
    if vowel == NASAL_N:
        return "N", "N_to_SP"
    if onset == "r" and vowel == "i":
        return "ri", "ri_to_SP"
    if onset == "s" and vowel == "u":
        return "su", "su_to_SP"
    if onset is None and vowel == "i":
        return "i", "i_to_SP"
    return "other", "other_to_SP"


def extract_terminal_events(speaker: str, row: Row) -> List[TerminalEvent]:
    """`ph_seq` 中の各 `SP`/`AP` 直前を 1 つの終端イベントとして数える。

    - `position` = `utterance_final`（当該 SP 以降が全て無音トークン）/ `internal`
    - 終端モーラ = 直前が母音なら (子音?, 母音)、`N` なら単独
    - **`AP`（pjs の breath トークン）も終端として数える**。フレーズ末の息継ぎは
      「語尾 → 無音」の実例であり、除くと pjs 側の終端が構造的に過少になる。
      ただし遷移名は `*_to_SP` のままなので、**どちらの無音トークンだったかを
      `silence_token` に残す**（後から AP 由来だけを分離できるようにする）。
    """
    events: List[TerminalEvent] = []
    n = len(row.ph_seq)
    for j, tok in enumerate(row.ph_seq):
        if tok not in SILENCE_TOKENS or j == 0:
            continue
        prev = row.ph_seq[j - 1]
        if prev in SILENCE_TOKENS:
            continue
        if prev not in VOWELS and prev != NASAL_N:
            # 子音で終わるモーラ（cl 等）。終端イベントとしては other に数える。
            onset, vowel = None, prev
            onset_sec, vowel_sec = 0.0, float(row.ph_dur[j - 1])
            phone_index = j - 1
        elif prev == NASAL_N:
            onset, vowel = None, NASAL_N
            onset_sec, vowel_sec = 0.0, float(row.ph_dur[j - 1])
            phone_index = j - 1
        else:
            vowel = prev
            phone_index = j - 1
            vowel_sec = float(row.ph_dur[j - 1])
            cand = row.ph_seq[j - 2] if j >= 2 else None
            if cand is not None and cand not in SILENCE_TOKENS and cand not in VOWELS and cand != NASAL_N:
                onset = cand
                onset_sec = float(row.ph_dur[j - 2])
            else:
                onset, onset_sec = None, 0.0
        preceding_phoneme, transition = _classify(onset, vowel)
        tail_is_silence = all(t in SILENCE_TOKENS for t in row.ph_seq[j:n])
        events.append(
            TerminalEvent(
                speaker=speaker,
                row_id=row.name,
                phone_index=phone_index,
                transition=transition,
                preceding_phoneme=preceding_phoneme,
                position="utterance_final" if tail_is_silence else "internal",
                onset_phone_seconds=onset_sec,
                vowel_phone_seconds=vowel_sec,
                preceding_duration_seconds=onset_sec + vowel_sec,
                silence_token=tok,
            )
        )
    return events


def count_ri_medial(row: Row) -> int:
    """語中 `/ri/`（直後が SP でない）の数。参考値であり判定には使わない（§3）。"""
    total = 0
    for j in range(1, len(row.ph_seq)):
        if row.ph_seq[j] == "i" and row.ph_seq[j - 1] == "r":
            after = row.ph_seq[j + 1] if j + 1 < len(row.ph_seq) else None
            if after is not None and after not in SILENCE_TOKENS:
                total += 1
    return total


def voiced_seconds(row: Row) -> float:
    return float(sum(d for p, d in zip(row.ph_seq, row.ph_dur) if p not in SILENCE_TOKENS))


# --- pitch_bin（話者内三分位・§3） -----------------------------------------


def _note_index_of_phone(row: Row, phone_index: int) -> Optional[int]:
    if not row.ph_num:
        return None
    acc = 0
    for note_i, count in enumerate(row.ph_num):
        acc += int(count)
        if phone_index < acc:
            return note_i
    return None


def _midi_from_note_seq(row: Row, phone_index: int) -> Optional[float]:
    """譜面ありの行: 終端モーラが**載るノート**の MIDI をそのまま使う（§3-1）。

    `ph_num` が無い / 終端音素を覆っていない行では **`None`（unknown）を返す**。
    行全体の中央値で埋めると、それは推定であって観測ではなく（§3 末尾「推定ではなく
    既知の境界から数える」）、しかもその推定値が話者内三分位に参加して露出を
    別の pitch 層へ動かしうる（PR #300 Codex 第 3 巡 P1）。
    """
    if not row.note_seq:
        return None
    note_i = _note_index_of_phone(row, phone_index)
    if note_i is None or note_i >= len(row.note_seq):
        return None
    return _note_token_to_midi(row.note_seq[note_i])


def _note_token_to_midi(token: str) -> Optional[float]:
    if token in ("rest", "SP", "AP", ""):
        return None
    try:
        return float(token)
    except ValueError:
        pass
    try:
        import librosa

        return float(librosa.note_to_midi(token))
    except Exception:
        return None


def assign_pitch_bins(events: Sequence[TerminalEvent]) -> Dict[str, Any]:
    """話者内三分位でカット点を作り、`pitch_bin` を各イベントへ書き込む。

    MIDI が取れなかったイベントは `unknown` のまま残し（黙って mid へ寄せない）、
    件数を返り値へ記帳する。
    """
    vals = sorted(e.midi for e in events if e.midi is not None)
    unknown = sum(1 for e in events if e.midi is None)
    if len(vals) < 3:
        for e in events:
            e.pitch_bin = sp.PITCH_BIN_UNKNOWN
        return {
            "cut_points_midi": None,
            "reason": "insufficient_midi_values",
            "unknown_events": len(events),
        }
    lo = _percentile(vals, sp.PITCH_QUANTILES[0])
    hi = _percentile(vals, sp.PITCH_QUANTILES[1])
    for e in events:
        if e.midi is None:
            e.pitch_bin = sp.PITCH_BIN_UNKNOWN
        elif e.midi < lo:
            e.pitch_bin = "low"
        elif e.midi < hi:
            e.pitch_bin = "mid"
        else:
            e.pitch_bin = "high"
    return {
        "cut_points_midi": [lo, hi],
        "method": "speaker-internal tertiles (linear percentile)",
        "unknown_events": unknown,
    }


def _percentile(sorted_vals: Sequence[float], pct: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (pct / 100.0) * (len(sorted_vals) - 1)
    lo_i = int(math.floor(pos))
    hi_i = int(math.ceil(pos))
    frac = pos - lo_i
    return float(sorted_vals[lo_i] * (1.0 - frac) + sorted_vals[hi_i] * frac)


# --- 台帳の組み立て --------------------------------------------------------


class InputPinMismatch(RuntimeError):
    """回収 / 再生成した CSV が canonical pin と一致しない（fail-closed）。"""


@dataclass
class SpeakerInput:
    speaker: str
    modality: str
    csv_path: Path
    midi_by_event: Dict[str, float] = field(default_factory=dict)
    #: `s7_ledger_inputs.json` の canonical sha256。**必須**（None のままでは集計しない）。
    expected_sha256: Optional[str] = None
    expected_row_count: Optional[int] = None
    #: `r_ratio` の出所（`s7_ledger_inputs.json` の r_ratio_provenance）。
    #: `imputed_fixed_allocation` を実測値として使わないための表示（User 裁定 §6）。
    r_ratio_provenance: Optional[Dict[str, str]] = None


def _stratum_key(e: TerminalEvent, modality: str) -> str:
    return "|".join((modality, e.position, e.pitch_bin, duration_bin(e.preceding_duration_seconds)))


def build_speaker_section(inp: SpeakerInput) -> Dict[str, Any]:
    rows, source_sha256, source_bytes = read_and_parse(inp.csv_path)
    # canonical pin 照合（User 裁定 2 訂正の方針 3〜5）。
    # 「完全一致したものだけ台帳入力として使う。不一致なら BLOCKED」。
    if not inp.expected_sha256:
        raise InputPinMismatch(
            f"{inp.speaker}: canonical sha256 が渡されていない。"
            "results_s7/s7_ledger_inputs.json を --inputs-pin で渡す（未 pin の CSV は使わない）"
        )
    if source_sha256 != inp.expected_sha256:
        raise InputPinMismatch(
            f"{inp.speaker}: transcriptions.csv の sha256 が canonical pin と一致しない "
            f"(expected {inp.expected_sha256}, got {source_sha256})。BLOCKED — "
            "run7 と同じ入力・同じ converter で再生成したものだけを使う"
        )
    if inp.expected_row_count is not None and len(rows) != inp.expected_row_count:
        raise InputPinMismatch(
            f"{inp.speaker}: row_count が canonical pin と一致しない "
            f"(expected {inp.expected_row_count}, got {len(rows)})"
        )
    events: List[TerminalEvent] = []
    ri_medial = 0
    voiced_s = 0.0
    for row in rows:
        voiced_s += voiced_seconds(row)
        ri_medial += count_ri_medial(row)
        for e in extract_terminal_events(inp.speaker, row):
            e.midi = inp.midi_by_event.get(e.event_id, _midi_from_note_seq(row, e.phone_index))
            events.append(e)
    pitch_meta = assign_pitch_bins(events)
    def _constant(rs: List[float]) -> Dict[str, Any]:
        uniq = {round(r, 9) for r in rs}
        return (
            {"detected": True, "value": round(rs[0], 6), "n_events": len(rs)}
            if rs and len(uniq) == 1
            else {"detected": False, "n_events": len(rs), "n_distinct": len(uniq)}
        )

    ratios = [e.r_ratio for e in events if e.r_ratio is not None]
    # baseline_r_ratio が実際に取られるのは**主層**（utterance_final × ri_to_SP）なので、
    # 全イベントの分布とは別に主層だけの定数検出も出す（User 裁定 §6）。
    primary_ratios = [
        e.r_ratio
        for e in events
        if e.r_ratio is not None
        and e.position == sp.EVENT_DETAIL_REQUIRED_POSITION
        and e.transition in sp.EVENT_DETAIL_REQUIRED_TRANSITIONS
    ]
    constant_r_ratio = {
        "all_events": _constant(ratios),
        "primary_stratum_events": _constant(primary_ratios),
        "note": (
            "primary_stratum_events = utterance_final × ri_to_SP のみ。"
            "baseline_r_ratio はここから取られるので、定率配分（imputed_fixed_allocation）が"
            "実測値のように見える経路はこの行で検出する"
        ),
    }
    silence_token_counts: Dict[str, int] = {}
    for e in events:
        silence_token_counts[e.silence_token] = silence_token_counts.get(e.silence_token, 0) + 1

    terminal_events: Dict[str, Dict[str, Any]] = {}
    denominator: Dict[str, Dict[str, float]] = {}
    for e in events:
        k = _stratum_key(e, inp.modality)
        den = denominator.setdefault(
            k, {"eligible_terminal_count": 0, "eligible_terminal_seconds": 0.0}
        )
        den["eligible_terminal_count"] += 1
        den["eligible_terminal_seconds"] += e.preceding_duration_seconds

        cell = terminal_events.setdefault(k, {}).setdefault(
            e.transition, {"count": 0, "duration_seconds": 0.0}
        )
        cell["count"] += 1
        cell["duration_seconds"] += e.preceding_duration_seconds
        if (
            e.position == sp.EVENT_DETAIL_REQUIRED_POSITION
            and e.transition in sp.EVENT_DETAIL_REQUIRED_TRANSITIONS
        ):
            cell.setdefault("events", []).append(
                {
                    "event_id": e.event_id,
                    "onset_phone_seconds": round(e.onset_phone_seconds, 6),
                    "vowel_phone_seconds": round(e.vowel_phone_seconds, 6),
                    "r_ratio": (round(e.r_ratio, 6) if e.r_ratio is not None else None),
                    "silence_token": e.silence_token,
                }
            )
    for k in terminal_events:
        for cell in terminal_events[k].values():
            cell["duration_seconds"] = round(cell["duration_seconds"], 6)
    for k in denominator:
        denominator[k]["eligible_terminal_seconds"] = round(
            denominator[k]["eligible_terminal_seconds"], 6
        )

    is_real_song = inp.modality == "real_song"
    return {
        "speaker": inp.speaker,
        "modality": inp.modality,
        "source_csv": str(inp.csv_path),
        # 入力の**バイト**を pin する。パス文字列だけでは、同じパスの中身が
        # 差し替わったときにどのバイトがこの台帳の count / H-TTD を作ったのかを
        # 後から言えない（PR #300 Codex P1）。
        "source_csv_sha256": source_sha256,
        "source_csv_bytes": source_bytes,
        "source_csv_pin_verified": True,
        "source_csv_pin_source": "results_s7/s7_ledger_inputs.json（canonical = run7/assembly_manifest.json）",
        "row_count": len(rows),
        "voiced_non_SP_AP_seconds": round(voiced_s, 6),
        "voiced_non_SP_AP_seconds_note": (
            "SP と AP の**両方**を除いた ph_dur 合計（modality を問わない）。"
            "run7 assembly_manifest の per_speaker_voiced_seconds は **SP のみ除外**"
            "（= AP を含む `non_SP_ph_dur_seconds`）で、pjs で 49.959 s 差が出る。"
            "同じ『voiced』という語が別の量を指すため、名前で分離する"
            "（User 裁定 2026-08-21・過去 manifest は遡及修正しない）"
        ),
        # VCV / speech / synthetic_song の発声秒を「実歌唱秒」と呼ぶと、
        # 8-B の収録量を決める量が汚染される（PR #300 Codex P2）。
        "local_real_singing_seconds": (round(voiced_s, 6) if is_real_song else None),
        "local_real_singing_seconds_note": (
            "modality == real_song のときのみ値を持つ。それ以外は null "
            f"（この話者の modality = {inp.modality}）"
        ),
        "stratum_key_order": ["modality", "position", "pitch_bin", "preceding_duration_bin"],
        "terminal_events": terminal_events,
        "denominator": denominator,
        "pitch_bin_assignment": pitch_meta,
        "r_ratio_provenance": inp.r_ratio_provenance,
        "constant_r_ratio_detected": constant_r_ratio,
        "silence_token_counts": silence_token_counts,
        "silence_token_note": (
            "終端イベントを取った無音トークンの内訳。AP（breath）も終端として数えるが、"
            "AP 由来だけを後から分離できるよう内訳を残す（遷移名は *_to_SP のまま）"
        ),
        "ri_medial_count": ri_medial,
        "note_duration_bin": None,
        "note_duration_bin_reason": (
            "1 | 2 | 4 beats の参考値は tempo を要するため本台帳では算出しない"
            "（§3 で参考値・単独では判定に使わないと事前登録されている）"
        ),
    }


@dataclass(frozen=True)
class MidiTable:
    """MIDI 対応表を**一度だけ**読んだ結果（parse と sha を同じバイト列から取る）。"""

    path: Path
    sha256: str
    n_bytes: int
    mapping: Dict[str, float]

    @classmethod
    def load(cls, path: Path) -> "MidiTable":
        table, sha, n = s7_io.read_json_with_pin(path)
        return cls(
            path=path,
            sha256=sha,
            n_bytes=n,
            mapping={str(k): float(v) for k, v in table.items()},
        )


def build_ledger(
    inputs: Sequence[SpeakerInput],
    breaking: Sequence[str],
    non_breaking: Sequence[str],
    midi_table: Optional[MidiTable] = None,
) -> Dict[str, Any]:
    # 同じ話者を 2 回渡すと dict 内包で片方が黙って消え、露出が過少に数えられて
    # H-TTD が変わる（PR #300 Codex 第 2 巡 P2）。fail-closed で弾く。
    seen: List[str] = [inp.speaker for inp in inputs]
    duplicates = sorted({s for s in seen if seen.count(s) > 1})
    if duplicates:
        raise ValueError(
            f"同じ話者が複数回指定されている: {duplicates}。"
            "1 話者 = 1 つの transcriptions.csv で渡す（黙って上書きしない）"
        )
    # 同じ話者を breaking と non_breaking の両方へ入れると、両群に同じ密度が入り
    # 順序判定が必ず偽になって「無効な事前登録から refuted が出る」（PR #300
    # Codex 第 3 巡 P2）。分類の矛盾と未知話者は裁定前に止める。
    contradictory = sorted(set(breaking) & set(non_breaking))
    if contradictory:
        raise ValueError(
            f"同じ話者が breaking と non-breaking の両方に指定されている: {contradictory}"
        )
    unknown = sorted((set(breaking) | set(non_breaking)) - set(seen))
    if unknown:
        raise ValueError(
            f"入力に存在しない話者が分類に指定されている: {unknown}（誤記の可能性）"
        )
    speakers = {inp.speaker: build_speaker_section(inp) for inp in inputs}
    verdict = httd_verdict(speakers, breaking, non_breaking)
    return {
        "schema": sp.LEDGER_SCHEMA,
        "authority": "DESIGN_S7_run8.md 3",
        "generated_by": "voice_genesis/foundry/run8/s7_ledger.py",
        "duration_bins_seconds": {
            name: [lo, (None if math.isinf(hi) else hi)] for name, lo, hi in sp.DURATION_BINS
        },
        "primary_stratum": {
            "position": sp.PRIMARY_POSITION,
            "transition": sp.PRIMARY_TRANSITION,
            "preceding_duration_bin": list(sp.PRIMARY_DURATION_BINS),
            "pitch_bin": list(sp.PITCH_BINS),
        },
        "prereg_speaker_classification": {
            "breaking": list(breaking),
            "non_breaking": list(non_breaking),
            "note": (
                "§3 判定分岐 3/4 の「順序（破綻しない話者 > 破綻する話者）」に使う"
                "事前登録入力。台帳生成器はこの分類を内蔵しない。"
            ),
        },
        "midi_json": (
            None
            if midi_table is None
            else {
                "path": str(midi_table.path),
                "sha256": midi_table.sha256,
                "bytes": midi_table.n_bytes,
            }
        ),
        "speakers": speakers,
        "h_ttd": verdict,
    }


# --- H-TTD 裁定（§3 の判定順・modality 規則・多数決） ----------------------


def density(section: Dict[str, Any], stratum: str, transition: str) -> Optional[float]:
    den = section["denominator"].get(stratum)
    if not den or den["eligible_terminal_count"] == 0:
        return None
    cell = section["terminal_events"].get(stratum, {}).get(transition)
    count = int(cell["count"]) if cell else 0
    return count / float(den["eligible_terminal_count"])


def _stratum_verdict(
    per_speaker: Dict[str, Dict[str, Any]],
    breaking: Sequence[str],
    non_breaking: Sequence[str],
) -> Dict[str, Any]:
    """1 層の裁定。`per_speaker[speaker] = {density, count, eligible, modality}`。

    §3「modality 交絡の扱い」に従い、**同一 modality 内の比較だけ**を行う。
    modality をまたぐ話者はその層の比較から外し（除外を記帳）、同一 modality の
    比較対が 1 つも無ければ層ごと `undetermined` にする。
    """
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for speaker, row in per_speaker.items():
        if speaker not in breaking and speaker not in non_breaking:
            continue  # 事前登録分類の無い話者は比較に入れない（除外は下で記帳する）
        groups.setdefault(row["modality"], {})[speaker] = row
    comparable = {
        modality: rows
        for modality, rows in groups.items()
        if any(s in breaking for s in rows) and any(s in non_breaking for s in rows)
    }
    if not comparable:
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "no_same_modality_comparison",
            "ratio": None,
        }
    if len(comparable) > 1:
        sub = {
            modality: _compare_within_modality(rows, breaking, non_breaking)
            for modality, rows in sorted(comparable.items())
        }
        verdicts = {v["verdict"] for v in sub.values()}
        if len(verdicts) == 1:
            out = dict(next(iter(sub.values())))
            out["modality_groups"] = sub
            return out
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "modality_groups_disagree",
            "ratio": None,
            "modality_groups": sub,
        }
    modality, rows = next(iter(comparable.items()))
    out = _compare_within_modality(rows, breaking, non_breaking)
    out["compared_modality"] = modality
    out["excluded_speakers"] = sorted(set(per_speaker) - set(rows))
    return out


def _compare_within_modality(
    per_speaker: Dict[str, Dict[str, Any]],
    breaking: Sequence[str],
    non_breaking: Sequence[str],
) -> Dict[str, Any]:
    """同一 modality 内の密度比較（§3 の判定順を上から評価する）。"""
    if any(v["eligible"] < sp.MIN_ELIGIBLE_TERMINAL_COUNT for v in per_speaker.values()):
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "insufficient_sample",
            "ratio": None,
        }
    densities = {s: v["density"] for s, v in per_speaker.items()}
    if all(d == 0.0 for d in densities.values()):
        return {"verdict": sp.Verdict.UNDETERMINED.value, "reason": "all_zero_density", "ratio": None}

    lo_group = [densities[s] for s in per_speaker if s in breaking]
    hi_group = [densities[s] for s in per_speaker if s in non_breaking]
    if not lo_group or not hi_group:
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "insufficient_prereg_classification",
            "ratio": None,
        }
    order_ok = min(hi_group) > max(lo_group)

    mn, mx = min(densities.values()), max(densities.values())
    if mn == 0.0 and mx > 0.0:
        return {
            "verdict": (sp.Verdict.SUPPORTED if order_ok else sp.Verdict.REFUTED).value,
            "reason": "separated_by_zero",
            "ratio": sp.RATIO_SEPARATED_BY_ZERO,
        }
    ratio = mx / mn
    supported = order_ok and ratio >= sp.DENSITY_RATIO_THRESHOLD
    return {
        "verdict": (sp.Verdict.SUPPORTED if supported else sp.Verdict.REFUTED).value,
        "reason": "ratio_rule",
        "ratio": round(ratio, 6),
    }


def support_audit(speakers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """主層ごとに「同一 modality で `eligible >= 20` を満たす話者が 2 名以上いるか」を数える。

    **事前登録分類（breaking / non_breaking）を一切参照しない**。分類が付いた場合でも
    density 比を計算できる層が存在するかどうか、という corpus 側の構造だけを見る。
    """
    per_stratum: Dict[str, Any] = {}
    any_ok = False
    for dbin in sp.PRIMARY_DURATION_BINS:
        for pbin in sp.PITCH_BINS:
            by_modality: Dict[str, List[str]] = {}
            eligible_by_speaker: Dict[str, int] = {}
            for speaker, section in speakers.items():
                key = "|".join((section["modality"], sp.PRIMARY_POSITION, pbin, dbin))
                den = section["denominator"].get(key)
                n = int(den["eligible_terminal_count"]) if den else 0
                eligible_by_speaker[speaker] = n
                if n >= sp.MIN_ELIGIBLE_TERMINAL_COUNT:
                    by_modality.setdefault(section["modality"], []).append(speaker)
            ok = any(len(v) >= 2 for v in by_modality.values())
            any_ok = any_ok or ok
            per_stratum[f"{pbin}/{dbin}"] = {
                "eligible_by_speaker": eligible_by_speaker,
                "speakers_meeting_minimum_by_modality": {
                    m: sorted(v) for m, v in sorted(by_modality.items())
                },
                "can_support_a_same_modality_pair": ok,
            }
    return {
        "minimum_eligible_terminal_count": sp.MIN_ELIGIBLE_TERMINAL_COUNT,
        "per_stratum": per_stratum,
        "any_stratum_can_support_a_pair": any_ok,
        "note": (
            "事前登録分類を参照せずに corpus の構造だけを見る監査。"
            "any_stratum_can_support_a_pair = false なら、分類が付いても H-TTD は"
            "裁定できない（= 標本の問題であって分類の問題ではない）"
        ),
    }


def httd_verdict(
    speakers: Dict[str, Dict[str, Any]],
    breaking: Sequence[str],
    non_breaking: Sequence[str],
) -> Dict[str, Any]:
    """主層（`utterance_final` × `ri_to_SP` × d3/d4 × pitch 3 値 = 最大 6 層）で裁定する。"""
    per_stratum: Dict[str, Any] = {}
    for dbin in sp.PRIMARY_DURATION_BINS:
        for pbin in sp.PITCH_BINS:
            label = f"{pbin}/{dbin}"
            rows: Dict[str, Dict[str, Any]] = {}
            for speaker, section in speakers.items():
                key = "|".join((section["modality"], sp.PRIMARY_POSITION, pbin, dbin))
                den = section["denominator"].get(key)
                if not den:
                    continue
                cell = section["terminal_events"].get(key, {}).get(sp.PRIMARY_TRANSITION)
                d = density(section, key, sp.PRIMARY_TRANSITION)
                rows[speaker] = {
                    "modality": section["modality"],
                    "density": d if d is not None else 0.0,
                    "count": int(cell["count"]) if cell else 0,
                    "duration_seconds": float(cell["duration_seconds"]) if cell else 0.0,
                    "eligible": int(den["eligible_terminal_count"]),
                    "eligible_seconds": float(den["eligible_terminal_seconds"]),
                }
            if len(rows) < 2:
                per_stratum[label] = {
                    "verdict": sp.Verdict.UNDETERMINED.value,
                    "reason": "fewer_than_two_speakers_in_stratum",
                    "ratio": None,
                    "rows": rows,
                }
                continue
            v = _stratum_verdict(rows, breaking, non_breaking)
            v["rows"] = rows
            per_stratum[label] = v

    scored = [v for v in per_stratum.values() if v["verdict"] != sp.Verdict.UNDETERMINED.value]
    # 「標本不足で裁定できなかった」を undetermined と同じ語で終わらせない
    # （User 裁定 2026-08-21）。閾値を下げる / d3+d4 を合算する / pitch 層を潰す、
    # といった救済はしない。仮説が反証されたという意味でもない。
    #
    # ★ 判定は**事前登録分類に依らない構造**で行う: どの層にも「同一 modality で
    #   eligible >= 20 を満たす話者が 2 名以上」存在しないなら、分類が付いても
    #   付かなくても density 比は計算できない。分類の不足（no_same_modality_comparison）
    #   と標本の不足を混同しないため、両方を support_audit に残す。
    support = support_audit(speakers)
    insufficient_only = not scored and not support["any_stratum_can_support_a_pair"]
    n_sup = sum(1 for v in scored if v["verdict"] == sp.Verdict.SUPPORTED.value)
    n_ref = sum(1 for v in scored if v["verdict"] == sp.Verdict.REFUTED.value)
    if len(scored) >= sp.MIN_SCORED_STRATA and n_sup >= sp.MAJORITY_FRACTION * len(scored) and n_ref == 0:
        overall = sp.Verdict.SUPPORTED.value
    elif len(scored) >= sp.MIN_SCORED_STRATA and n_ref >= sp.MAJORITY_FRACTION * len(scored):
        overall = sp.Verdict.REFUTED.value
    elif insufficient_only:
        overall = sp.NOT_EVALUABLE_INSUFFICIENT_SUPPORT
    else:
        overall = sp.Verdict.UNDETERMINED.value
    return {
        "per_stratum": per_stratum,
        "support_audit": support,
        "overall_note": (
            "NOT_EVALUABLE_INSUFFICIENT_SUPPORT = 現在の corpus に裁定できるだけの"
            "標的イベントが存在しなかった、という結果。**TTD 仮説の反証ではない**。"
            "閾値 20 を下げない / d3・d4 を合算しない / pitch 層を潰して水増ししない"
            "（User 裁定 2026-08-21）"
        ),
        "minimum_eligible_terminal_count": sp.MIN_ELIGIBLE_TERMINAL_COUNT,
        "scored_strata": len(scored),
        "supported_strata": n_sup,
        "refuted_strata": n_ref,
        "overall": overall,
    }


# --- CLI -------------------------------------------------------------------


def _parse_speaker_arg(value: str) -> SpeakerInput:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--speaker は NAME:MODALITY:CSV_PATH の形式で指定する")
    name, modality, path = parts
    if modality not in sp.MODALITIES:
        raise argparse.ArgumentTypeError(f"unknown modality {modality!r} (許容 = {sp.MODALITIES})")
    return SpeakerInput(speaker=name, modality=modality, csv_path=Path(path))


def render_table(ledger: Dict[str, Any]) -> str:
    """§3 が要求する「人間可読の表」。主層を層別に並べ、生値を必ず併記する。"""
    lines = [
        "# target_exposure_ledger — primary stratum",
        "",
        f"- primary stratum: `{sp.PRIMARY_POSITION}` × `{sp.PRIMARY_TRANSITION}` × "
        f"{list(sp.PRIMARY_DURATION_BINS)} × {list(sp.PITCH_BINS)}",
        f"- overall H-TTD verdict: **{ledger['h_ttd']['overall']}** "
        f"(scored {ledger['h_ttd']['scored_strata']} strata / "
        f"supported {ledger['h_ttd']['supported_strata']} / "
        f"refuted {ledger['h_ttd']['refuted_strata']})",
        "",
        "| stratum | speaker | modality | count | duration_s | eligible | density | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, stratum in ledger["h_ttd"]["per_stratum"].items():
        rows = stratum.get("rows", {})
        if not rows:
            lines.append(
                f"| {label} | — | — | — | — | — | — | {stratum['verdict']} "
                f"({stratum.get('reason', '')}) |"
            )
            continue
        for speaker, row in sorted(rows.items()):
            lines.append(
                f"| {label} | {speaker} | {row['modality']} | {row['count']} | "
                f"{row['duration_seconds']:.3f} | {row['eligible']} | "
                f"{row['density']:.4f} | {stratum['verdict']} |"
            )
    lines.append("")
    lines.append("## speakers")
    lines.append("")
    lines.append(
        "| speaker | modality | rows | voiced(SP,AP除外)_s | real_singing_s | ri_medial | "
        "pitch cut points (MIDI) | unknown pitch | r_ratio source | source sha256 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for speaker, section in sorted(ledger["speakers"].items()):
        pb = section["pitch_bin_assignment"]
        real_s = section["local_real_singing_seconds"]
        lines.append(
            f"| {speaker} | {section['modality']} | {section['row_count']} | "
            f"{section['voiced_non_SP_AP_seconds']:.3f} | "
            f"{'—' if real_s is None else f'{real_s:.3f}'} | {section['ri_medial_count']} | "
            f"{pb.get('cut_points_midi')} | {pb.get('unknown_events')} | "
            f"{(section.get('r_ratio_provenance') or {}).get('r_ratio_source', '—')} | "
            f"{section['source_csv_sha256'][:12]} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="target_exposure_ledger/0.1 generator (S7 3)")
    parser.add_argument("--speaker", action="append", required=True, type=_parse_speaker_arg)
    parser.add_argument("--breaking", action="append", default=[])
    parser.add_argument("--non-breaking", action="append", default=[])
    parser.add_argument(
        "--inputs-pin",
        type=Path,
        default=_HERE.parent / "results_s7" / "s7_ledger_inputs.json",
        help=(
            "canonical な transcriptions.csv pin（既定 = results_s7/s7_ledger_inputs.json）。"
            "ここに無い話者・sha 不一致の CSV は fail-closed で拒否する"
        ),
    )
    parser.add_argument(
        "--midi-json",
        type=Path,
        default=None,
        help="event_id -> MIDI の外部対応表（譜面の無いコーパス用。§3 の f0 中央値経路）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_HERE.parent / "results_s7" / "target_exposure_ledger.json",
    )
    parser.add_argument(
        "--table-out",
        type=Path,
        default=None,
        help="§3 が要求する人間可読の表（markdown）の出力先。既定は --out と同じ幹の .md",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    inputs: List[SpeakerInput] = list(args.speaker)

    pin_doc, pin_sha, _pin_bytes = s7_io.read_json_with_pin(args.inputs_pin)
    if pin_doc.get("schema") != "s7-ledger-inputs/0.1":
        raise ValueError(f"{args.inputs_pin}: unexpected schema {pin_doc.get('schema')!r}")
    for inp in inputs:
        pinned = pin_doc["speakers"].get(inp.speaker)
        if pinned is None:
            raise InputPinMismatch(
                f"{inp.speaker}: canonical pin に載っていない話者（{args.inputs_pin}）"
            )
        inp.expected_sha256 = str(pinned["transcriptions_csv_sha256"])
        inp.expected_row_count = int(pinned["row_count"])
        inp.r_ratio_provenance = pinned.get("r_ratio_provenance")
        if pinned.get("modality") and pinned["modality"] != inp.modality:
            raise InputPinMismatch(
                f"{inp.speaker}: modality が pin と食い違う "
                f"(pin {pinned['modality']} != 指定 {inp.modality})"
            )

    midi_table: Optional[MidiTable] = None
    if args.midi_json:
        midi_table = MidiTable.load(Path(args.midi_json))
        for inp in inputs:
            inp.midi_by_event = {
                k: v for k, v in midi_table.mapping.items() if k.startswith(f"{inp.speaker}/")
            }

    table_path = args.table_out or args.out.with_suffix(".md")
    protected = [inp.csv_path for inp in inputs] + (
        [midi_table.path] if midi_table is not None else []
    )
    # 入力との衝突に加えて、**出力先どうし**の衝突も拒否する
    # （--out == --table-out だと台帳 JSON を表 markdown が上書きする。
    #  PR #300 最終巡の未適用指摘 3 / User 裁定 3）。
    reject_duplicate_outputs([args.out, table_path])
    reject_output_collision([args.out, table_path], protected)

    ledger = build_ledger(inputs, args.breaking, args.non_breaking, midi_table=midi_table)
    ledger["inputs_pin"] = {
        "path": str(args.inputs_pin),
        "sha256": pin_sha,
        "canonical_source": pin_doc["canonical_source"]["file"],
        "canonical_source_sha256": pin_doc["canonical_source"]["sha256"],
    }
    s7_io.assert_json_finite(ledger)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table_path.write_text(render_table(ledger), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"wrote {table_path}")
    print(f"  H-TTD overall = {ledger['h_ttd']['overall']} "
          f"(scored {ledger['h_ttd']['scored_strata']} / 6 strata)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
