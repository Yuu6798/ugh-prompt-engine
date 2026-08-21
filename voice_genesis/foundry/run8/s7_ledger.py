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
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_spec as sp  # noqa: E402

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


def parse_rows(csv_path: Path) -> List[Row]:
    """`transcriptions.csv` を読む。ph_seq と ph_dur の長さ不一致は fail-closed。"""
    rows: List[Row] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            ph_seq = tuple(str(raw["ph_seq"]).split())
            ph_dur = tuple(float(x) for x in str(raw["ph_dur"]).split())
            if len(ph_seq) != len(ph_dur):
                raise ValueError(
                    f"{csv_path}: row {raw.get('name')!r} has "
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
    """譜面ありの行: 終端モーラが載るノートの MIDI をそのまま使う（§3-1）。"""
    if not row.note_seq:
        return None
    note_i = _note_index_of_phone(row, phone_index)
    tokens = row.note_seq
    if note_i is None:
        candidates = [_note_token_to_midi(t) for t in tokens]
        vals = [v for v in candidates if v is not None]
        return float(sorted(vals)[len(vals) // 2]) if vals else None
    if note_i >= len(tokens):
        return None
    return _note_token_to_midi(tokens[note_i])


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


@dataclass
class SpeakerInput:
    speaker: str
    modality: str
    csv_path: Path
    midi_by_event: Dict[str, float] = field(default_factory=dict)


def _stratum_key(e: TerminalEvent, modality: str) -> str:
    return "|".join((modality, e.position, e.pitch_bin, duration_bin(e.preceding_duration_seconds)))


def build_speaker_section(inp: SpeakerInput) -> Dict[str, Any]:
    rows = parse_rows(inp.csv_path)
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
                }
            )
    for k in terminal_events:
        for cell in terminal_events[k].values():
            cell["duration_seconds"] = round(cell["duration_seconds"], 6)
    for k in denominator:
        denominator[k]["eligible_terminal_seconds"] = round(
            denominator[k]["eligible_terminal_seconds"], 6
        )

    return {
        "speaker": inp.speaker,
        "modality": inp.modality,
        "source_csv": str(inp.csv_path),
        "row_count": len(rows),
        "local_real_singing_seconds": round(voiced_s, 6),
        "local_real_singing_seconds_note": (
            "transcriptions.csv の非 SP/AP トークンの ph_dur 合計"
            "（convert_ritsu の total_voiced_s と同じ数え方）"
        ),
        "stratum_key_order": ["modality", "position", "pitch_bin", "preceding_duration_bin"],
        "terminal_events": terminal_events,
        "denominator": denominator,
        "pitch_bin_assignment": pitch_meta,
        "ri_medial_count": ri_medial,
        "note_duration_bin": None,
        "note_duration_bin_reason": (
            "1 | 2 | 4 beats の参考値は tempo を要するため本台帳では算出しない"
            "（§3 で参考値・単独では判定に使わないと事前登録されている）"
        ),
    }


def build_ledger(
    inputs: Sequence[SpeakerInput],
    breaking: Sequence[str],
    non_breaking: Sequence[str],
) -> Dict[str, Any]:
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
    n_sup = sum(1 for v in scored if v["verdict"] == sp.Verdict.SUPPORTED.value)
    n_ref = sum(1 for v in scored if v["verdict"] == sp.Verdict.REFUTED.value)
    if len(scored) >= sp.MIN_SCORED_STRATA and n_sup >= sp.MAJORITY_FRACTION * len(scored) and n_ref == 0:
        overall = sp.Verdict.SUPPORTED.value
    elif len(scored) >= sp.MIN_SCORED_STRATA and n_ref >= sp.MAJORITY_FRACTION * len(scored):
        overall = sp.Verdict.REFUTED.value
    else:
        overall = sp.Verdict.UNDETERMINED.value
    return {
        "per_stratum": per_stratum,
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
    lines.append("| speaker | modality | rows | voiced_s | ri_medial | pitch cut points (MIDI) | unknown pitch events |")
    lines.append("|---|---|---|---|---|---|---|")
    for speaker, section in sorted(ledger["speakers"].items()):
        pb = section["pitch_bin_assignment"]
        lines.append(
            f"| {speaker} | {section['modality']} | {section['row_count']} | "
            f"{section['local_real_singing_seconds']:.3f} | {section['ri_medial_count']} | "
            f"{pb.get('cut_points_midi')} | {pb.get('unknown_events')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="target_exposure_ledger/0.1 generator (S7 3)")
    parser.add_argument("--speaker", action="append", required=True, type=_parse_speaker_arg)
    parser.add_argument("--breaking", action="append", default=[])
    parser.add_argument("--non-breaking", action="append", default=[])
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
    if args.midi_json:
        table = json.loads(Path(args.midi_json).read_text(encoding="utf-8"))
        for inp in inputs:
            inp.midi_by_event = {
                k: float(v) for k, v in table.items() if k.startswith(f"{inp.speaker}/")
            }
    ledger = build_ledger(inputs, args.breaking, args.non_breaking)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table_path = args.table_out or args.out.with_suffix(".md")
    table_path.write_text(render_table(ledger), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"wrote {table_path}")
    print(f"  H-TTD overall = {ledger['h_ttd']['overall']} "
          f"(scored {ledger['h_ttd']['scored_strata']} / 6 strata)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
