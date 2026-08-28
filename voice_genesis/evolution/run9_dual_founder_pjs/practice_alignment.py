#!/usr/bin/env python3
"""Founder-local PRACTICE score projection and deterministic DP alignment.

The actor-facing alignment path accepts exactly two byte sources: a 24-bit,
mono, 48 kHz PCM WAV and a sanitized score projection.  It deliberately does
not import ``education_lesson_builder``: that module owns the EDUCATION-only
``.lab`` APIs and must not enter the PRACTICE actor import graph.

The score projection builder is a pre-processing boundary.  It reads only
MusicXML and emits exactly the six score-context fields authorized by the
2026-08-28 PRACTICE Alignment adjudication.  The alignment function itself
cannot accept MusicXML, ``.lab`` annotations, external boundaries, or a random
seed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


PROJECTION_KEYS = frozenset(
    {
        "mora_order",
        "mora_count",
        "nominal_duration_ratio",
        "phrase_grouping",
        "lyrics_phoneme_sequence",
        "nominal_pitch",
    }
)
SAMPLE_RATE = 48_000
BITS_PER_SAMPLE = 24
CHANNELS = 1
FRAME_HOP_SAMPLES = 480  # 10 ms
FRAME_WINDOW_SAMPLES = 1_920  # 40 ms
MIN_MORA_FRAMES = 3
DURATION_RATIO_MIN = 0.35
DURATION_RATIO_MAX = 2.80
ACTIVE_DB_BELOW_PEAK = 30.0
MAX_NORMALIZED_COST = 3.0
TIE_EPSILON = 1e-12
_FORBIDDEN_ACTOR_FILENAMES = frozenset(
    {
        "practice_audit_annotation_manifest_v1.json",
        "education_technique_lesson_manifest.json",
        "pjs_consumed_inputs_sha256.json",
    }
)
_STEP_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class PracticeAlignmentError(ValueError):
    """A fail-closed PRACTICE input, projection, or alignment error."""


@dataclass(frozen=True)
class _RawNote:
    voice: str
    onset_beat: float
    duration_beat: float
    is_rest: bool
    step: Optional[str]
    alter: int
    octave: Optional[int]
    lyric: Optional[str]
    order: int


@dataclass
class _Mora:
    voice: str
    onset_beat: float
    end_beat: float
    step: str
    alter: int
    octave: int
    lyric: str
    order: int


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical UTF-8 JSON used for projection/result identity."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PracticeAlignmentError(f"{label}: expected a finite number")
    out = float(value)
    if not math.isfinite(out) or (positive and out <= 0.0):
        qualifier = "positive finite" if positive else "finite"
        raise PracticeAlignmentError(f"{label}: expected {qualifier}, got {value!r}")
    return out


def validate_score_projection(value: Any) -> Dict[str, Any]:
    """Validate the closed-world six-field actor score projection."""
    if not isinstance(value, dict):
        raise PracticeAlignmentError("score projection must be a JSON object")
    keys = frozenset(value)
    if keys != PROJECTION_KEYS:
        raise PracticeAlignmentError(
            f"score projection keys must be exactly {sorted(PROJECTION_KEYS)!r}; got {sorted(keys)!r}"
        )
    count = value["mora_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise PracticeAlignmentError("mora_count must be a positive integer")
    order = value["mora_order"]
    ratios = value["nominal_duration_ratio"]
    phrases = value["phrase_grouping"]
    lyrics = value["lyrics_phoneme_sequence"]
    pitches = value["nominal_pitch"]
    for name, seq in (
        ("mora_order", order),
        ("nominal_duration_ratio", ratios),
        ("phrase_grouping", phrases),
        ("lyrics_phoneme_sequence", lyrics),
        ("nominal_pitch", pitches),
    ):
        if not isinstance(seq, list) or len(seq) != count:
            raise PracticeAlignmentError(f"{name}: expected list length {count}")
    if order != list(range(count)):
        raise PracticeAlignmentError("mora_order must be the exact sequence 0..mora_count-1")
    normalized_ratios = [_finite_number(v, f"nominal_duration_ratio[{i}]", positive=True) for i, v in enumerate(ratios)]
    if abs(math.fsum(normalized_ratios) - 1.0) > 1e-12:
        raise PracticeAlignmentError("nominal_duration_ratio must sum to 1 within 1e-12")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in phrases):
        raise PracticeAlignmentError("phrase_grouping entries must be non-negative integers")
    if phrases[0] != 0 or any(phrases[i] not in (phrases[i - 1], phrases[i - 1] + 1) for i in range(1, count)):
        raise PracticeAlignmentError("phrase_grouping must start at 0 and be contiguous")
    for i, item in enumerate(lyrics):
        if not isinstance(item, dict) or frozenset(item) != frozenset({"lyric", "phoneme_sequence"}):
            raise PracticeAlignmentError(
                f"lyrics_phoneme_sequence[{i}] must have exactly lyric and phoneme_sequence"
            )
        if not isinstance(item["lyric"], str) or not item["lyric"]:
            raise PracticeAlignmentError(f"lyrics_phoneme_sequence[{i}].lyric must be non-empty")
        if not isinstance(item["phoneme_sequence"], list) or not all(
            isinstance(token, str) and token for token in item["phoneme_sequence"]
        ):
            raise PracticeAlignmentError(
                f"lyrics_phoneme_sequence[{i}].phoneme_sequence must be a string list"
            )
    normalized_pitches = [_finite_number(v, f"nominal_pitch[{i}]", positive=True) for i, v in enumerate(pitches)]
    # Return a deep canonicalized copy so callers cannot mutate a validated input.
    return json.loads(
        canonical_json_bytes(
            {
                "mora_order": order,
                "mora_count": count,
                "nominal_duration_ratio": normalized_ratios,
                "phrase_grouping": phrases,
                "lyrics_phoneme_sequence": lyrics,
                "nominal_pitch": normalized_pitches,
            }
        )
    )


def _parse_musicxml(musicxml_bytes: bytes, label: str) -> Tuple[List[Tuple[float, float]], List[_RawNote]]:
    try:
        root = ET.fromstring(musicxml_bytes)
    except ET.ParseError as exc:
        raise PracticeAlignmentError(f"{label}: MusicXML parse error: {exc}") from exc
    if root.tag != "score-partwise":
        raise PracticeAlignmentError(f"{label}: expected score-partwise root")
    parts = root.findall("part")
    if len(parts) != 1:
        raise PracticeAlignmentError(f"{label}: expected exactly one part")
    cursor = 0.0
    divisions: Optional[float] = None
    tempos: List[Tuple[float, float]] = []
    notes: List[_RawNote] = []
    order = 0
    for measure in parts[0].findall("measure"):
        for child in measure:
            if child.tag == "attributes":
                div = child.find("divisions")
                if div is not None:
                    divisions = _finite_number(float(div.text), f"{label}: divisions", positive=True)
            elif child.tag == "direction":
                sound = child.find("sound")
                if sound is not None and sound.get("tempo") is not None:
                    tempos.append((cursor, _finite_number(float(sound.get("tempo")), f"{label}: tempo", positive=True)))
            elif child.tag in ("backup", "forward"):
                duration = child.find("duration")
                if duration is None or divisions is None:
                    raise PracticeAlignmentError(f"{label}: malformed {child.tag} before divisions")
                delta = float(duration.text) / divisions
                cursor += -delta if child.tag == "backup" else delta
            elif child.tag == "note":
                if child.find("grace") is not None or child.find("chord") is not None:
                    raise PracticeAlignmentError(f"{label}: grace/chord notes are outside projection v1")
                duration = child.find("duration")
                if duration is None or divisions is None:
                    raise PracticeAlignmentError(f"{label}: note missing duration/divisions")
                duration_beat = _finite_number(float(duration.text) / divisions, f"{label}: note duration", positive=True)
                voice_element = child.find("voice")
                voice = voice_element.text if voice_element is not None and voice_element.text else "1"
                rest = child.find("rest") is not None
                step: Optional[str] = None
                alter = 0
                octave: Optional[int] = None
                if not rest:
                    pitch = child.find("pitch")
                    if pitch is None or pitch.find("step") is None or pitch.find("octave") is None:
                        raise PracticeAlignmentError(f"{label}: pitched note missing step/octave")
                    step = pitch.find("step").text
                    if step not in _STEP_BASE:
                        raise PracticeAlignmentError(f"{label}: unsupported pitch step {step!r}")
                    octave = int(pitch.find("octave").text)
                    alter_element = pitch.find("alter")
                    alter = int(alter_element.text) if alter_element is not None else 0
                lyric_element = child.find("lyric")
                lyric: Optional[str] = None
                if lyric_element is not None and lyric_element.find("text") is not None:
                    lyric = lyric_element.find("text").text
                notes.append(
                    _RawNote(
                        voice=voice,
                        onset_beat=cursor,
                        duration_beat=duration_beat,
                        is_rest=rest,
                        step=step,
                        alter=alter,
                        octave=octave,
                        lyric=lyric,
                        order=order,
                    )
                )
                order += 1
                cursor += duration_beat
    if not tempos:
        raise PracticeAlignmentError(f"{label}: no explicit tempo; projection must not invent one")
    return tempos, notes


def _merge_morae(notes: Sequence[_RawNote], label: str) -> List[_Mora]:
    by_voice: Dict[str, List[_RawNote]] = {}
    for note in notes:
        by_voice.setdefault(note.voice, []).append(note)
    result: List[_Mora] = []
    for voice, voice_notes in by_voice.items():
        current: Optional[_Mora] = None
        for note in voice_notes:
            if note.is_rest:
                current = None
                continue
            if note.lyric:
                current = _Mora(
                    voice=voice,
                    onset_beat=note.onset_beat,
                    end_beat=note.onset_beat + note.duration_beat,
                    step=note.step,
                    alter=note.alter,
                    octave=note.octave,
                    lyric=note.lyric,
                    order=note.order,
                )
                result.append(current)
            else:
                if current is None:
                    raise PracticeAlignmentError(f"{label}: lyric-less note has no preceding mora")
                if (note.step, note.alter, note.octave) != (current.step, current.alter, current.octave):
                    raise PracticeAlignmentError(f"{label}: pitch-changing lyric-less continuation is unsupported")
                current.end_beat = note.onset_beat + note.duration_beat
    result.sort(key=lambda item: (item.onset_beat, item.voice, item.order))
    if not result:
        raise PracticeAlignmentError(f"{label}: score contains no lyric morae")
    return result


def _tempo_segments(events: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    indexed = sorted(enumerate(events), key=lambda item: (item[1][0], item[0]))
    segments: List[Tuple[float, float]] = []
    for _, event in indexed:
        if segments and segments[-1][0] == event[0]:
            segments[-1] = event
        else:
            segments.append(event)
    if segments[0][0] != 0.0:
        segments.insert(0, (0.0, segments[0][1]))
    return segments


def _beats_to_seconds(beat: float, segments: Sequence[Tuple[float, float]]) -> float:
    if beat < -1e-9:
        raise PracticeAlignmentError(f"negative MusicXML beat cursor: {beat}")
    seconds = 0.0
    beat = max(0.0, beat)
    for index, (start, tempo) in enumerate(segments):
        end = segments[index + 1][0] if index + 1 < len(segments) else None
        if end is not None and beat >= end:
            seconds += (end - start) * 60.0 / tempo
        else:
            return seconds + (beat - start) * 60.0 / tempo
    return seconds


def _pitch_hz(mora: _Mora) -> float:
    midi = _STEP_BASE[mora.step] + mora.alter + 12 * (mora.octave + 1)
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def build_score_projection(musicxml_bytes: bytes, *, label: str = "<musicxml>") -> Dict[str, Any]:
    """Build the authorized six-field projection from score bytes only."""
    tempos, notes = _parse_musicxml(musicxml_bytes, label)
    morae = _merge_morae(notes, label)
    segments = _tempo_segments(tempos)
    durations = [
        _beats_to_seconds(mora.end_beat, segments) - _beats_to_seconds(mora.onset_beat, segments)
        for mora in morae
    ]
    if any(not math.isfinite(duration) or duration <= 0.0 for duration in durations):
        raise PracticeAlignmentError(f"{label}: non-positive or non-finite mora duration")
    total = math.fsum(durations)
    ratios = [duration / total for duration in durations]
    # Force the mathematically intended sum to the same float in every process.
    ratios[-1] += 1.0 - math.fsum(ratios)
    phrase_ids: List[int] = []
    phrase = 0
    previous: Optional[_Mora] = None
    for mora in morae:
        if previous is not None and (
            mora.voice != previous.voice or mora.onset_beat > previous.end_beat + 1e-9
        ):
            phrase += 1
        phrase_ids.append(phrase)
        previous = mora
    projection = {
        "mora_order": list(range(len(morae))),
        "mora_count": len(morae),
        "nominal_duration_ratio": ratios,
        "phrase_grouping": phrase_ids,
        # PJS MusicXML supplies orthographic lyric morae but no phoneme tags.
        # An empty sequence records absence; it does not infer phonemes.
        "lyrics_phoneme_sequence": [
            {"lyric": mora.lyric, "phoneme_sequence": []} for mora in morae
        ],
        "nominal_pitch": [_pitch_hz(mora) for mora in morae],
    }
    return validate_score_projection(projection)


def _reject_actor_path(path: Path, role: str, expected_suffix: str) -> Path:
    raw = Path(path)
    resolved = raw.resolve(strict=False)
    for candidate in (raw, resolved):
        if candidate.suffix.lower() == ".lab":
            raise PracticeAlignmentError(f"{role}: .lab is POST_FREEZE_AUDIT_ONLY and forbidden")
        if candidate.name in _FORBIDDEN_ACTOR_FILENAMES:
            raise PracticeAlignmentError(f"{role}: forbidden PRACTICE search input {candidate.name!r}")
    if raw.suffix.lower() != expected_suffix:
        raise PracticeAlignmentError(f"{role}: expected {expected_suffix} input, got {raw.suffix!r}")
    return raw


def _decode_wav_24bit_mono_48k(wav_bytes: bytes, label: str) -> np.ndarray:
    stream = io.BytesIO(wav_bytes)
    if stream.read(4) != b"RIFF":
        raise PracticeAlignmentError(f"{label}: not RIFF")
    stream.read(4)
    if stream.read(4) != b"WAVE":
        raise PracticeAlignmentError(f"{label}: not WAVE")
    fmt: Optional[Tuple[int, int, int, int]] = None
    pcm: Optional[bytes] = None
    while True:
        header = stream.read(8)
        if not header:
            break
        if len(header) != 8:
            raise PracticeAlignmentError(f"{label}: truncated WAV chunk header")
        chunk_id, size = header[:4], struct.unpack("<I", header[4:])[0]
        data = stream.read(size)
        if len(data) != size:
            raise PracticeAlignmentError(f"{label}: truncated WAV chunk {chunk_id!r}")
        if size % 2:
            stream.read(1)
        if chunk_id == b"fmt ":
            if len(data) < 16:
                raise PracticeAlignmentError(f"{label}: short fmt chunk")
            audio_format, channels, rate, _, block_align, bits = struct.unpack("<HHIIHH", data[:16])
            fmt = (audio_format, channels, rate, bits)
            if block_align != 3:
                raise PracticeAlignmentError(f"{label}: expected block_align=3")
        elif chunk_id == b"data":
            pcm = data
    if fmt != (1, CHANNELS, SAMPLE_RATE, BITS_PER_SAMPLE):
        raise PracticeAlignmentError(
            f"{label}: expected PCM/mono/48000Hz/24-bit WAV, got {fmt!r}"
        )
    if pcm is None or len(pcm) % 3:
        raise PracticeAlignmentError(f"{label}: missing or misaligned data chunk")
    packed = np.frombuffer(pcm, dtype=np.uint8).reshape(-1, 3)
    values = (
        packed[:, 0].astype(np.int32)
        | (packed[:, 1].astype(np.int32) << 8)
        | (packed[:, 2].astype(np.int32) << 16)
    )
    values = np.where(values >= (1 << 23), values - (1 << 24), values)
    return values.astype(np.float64) / float(1 << 23)


def _window_sums(values: np.ndarray, starts: np.ndarray, length: int) -> np.ndarray:
    cumulative = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(values, dtype=np.float64)))
    return cumulative[starts + length] - cumulative[starts]


def _frame_features(samples: np.ndarray, pitches: Sequence[float]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if samples.size < FRAME_WINDOW_SAMPLES:
        raise PracticeAlignmentError("ALIGNMENT_FAILED: WAV shorter than one feature window")
    starts = np.arange(0, samples.size - FRAME_WINDOW_SAMPLES + 1, FRAME_HOP_SAMPLES, dtype=np.int64)
    energy = _window_sums(samples * samples, starts, FRAME_WINDOW_SAMPLES)
    rms = np.sqrt(np.maximum(energy / FRAME_WINDOW_SAMPLES, 0.0))
    db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    peak = float(np.max(db))
    active_mask = db >= peak - ACTIVE_DB_BELOW_PEAK
    active_indices = np.flatnonzero(active_mask)
    if peak < -80.0 or active_indices.size == 0:
        raise PracticeAlignmentError("ALIGNMENT_FAILED: no finite active audio region")
    active_start = max(0, int(active_indices[0]) - 2)
    active_end = min(len(starts), int(active_indices[-1]) + 3)

    positive_delta = np.maximum(0.0, np.diff(db, prepend=db[0]))
    delta_scale = max(float(np.percentile(positive_delta, 95.0)), 1e-9)
    onset = np.clip(positive_delta / delta_scale, 0.0, 1.0)
    active_db = db[active_start:active_end]
    span = max(float(np.max(active_db) - np.min(active_db)), 1e-9)
    low_energy = np.clip((float(np.max(active_db)) - db) / span, 0.0, 1.0)
    boundary_strength = 0.7 * onset + 0.3 * low_energy

    unique_pitches = sorted(set(float(pitch) for pitch in pitches))
    evidence_by_pitch: Dict[float, np.ndarray] = {}
    sample_sq = samples * samples
    for pitch in unique_pitches:
        lag = int(round(SAMPLE_RATE / pitch))
        if lag < 1 or lag >= FRAME_WINDOW_SAMPLES // 2:
            raise PracticeAlignmentError(f"ALIGNMENT_FAILED: nominal pitch {pitch} Hz outside v1 range")
        product = samples[:-lag] * samples[lag:]
        numerator = _window_sums(product, starts, FRAME_WINDOW_SAMPLES - lag)
        left = _window_sums(sample_sq, starts, FRAME_WINDOW_SAMPLES - lag)
        right = _window_sums(sample_sq, starts + lag, FRAME_WINDOW_SAMPLES - lag)
        denominator = np.sqrt(np.maximum(left * right, 1e-24))
        evidence_by_pitch[pitch] = np.clip(numerator / denominator, 0.0, 1.0)
    pitch_evidence = np.stack([evidence_by_pitch[float(pitch)] for pitch in pitches], axis=0)
    return np.array([active_start, active_end], dtype=np.int64), boundary_strength, pitch_evidence


def align_wav_to_projection(wav_bytes: bytes, projection: Mapping[str, Any], *, label: str = "<wav>") -> Dict[str, Any]:
    """Align with fixed monotonic DP; returns ALIGNED or ALIGNMENT_FAILED."""
    score = validate_score_projection(dict(projection))
    samples = _decode_wav_24bit_mono_48k(wav_bytes, label)
    count = score["mora_count"]
    try:
        active, boundary_strength, pitch_evidence = _frame_features(samples, score["nominal_pitch"])
    except PracticeAlignmentError as exc:
        return {
            "status": "ALIGNMENT_FAILED",
            "reason": str(exc).removeprefix("ALIGNMENT_FAILED: "),
            "mora_count": count,
            "boundaries_s": [],
            "total_cost": None,
            "normalized_cost": None,
        }
    start, end = int(active[0]), int(active[1])
    total_frames = end - start
    if total_frames < count * MIN_MORA_FRAMES:
        return {
            "status": "ALIGNMENT_FAILED",
            "reason": "active audio cannot provide the minimum frames per mora",
            "mora_count": count,
            "boundaries_s": [],
            "total_cost": None,
            "normalized_cost": None,
        }
    ratios = np.asarray(score["nominal_duration_ratio"], dtype=np.float64)
    targets = ratios * total_frames
    prefix_evidence = np.concatenate(
        (np.zeros((count, 1), dtype=np.float64), np.cumsum(pitch_evidence[:, start:end], axis=1)),
        axis=1,
    )
    infinity = float("inf")
    previous = np.full(total_frames + 1, infinity, dtype=np.float64)
    previous[0] = 0.0
    predecessors: List[np.ndarray] = []
    for mora_index in range(count):
        current = np.full(total_frames + 1, infinity, dtype=np.float64)
        back = np.full(total_frames + 1, -1, dtype=np.int64)
        target = float(targets[mora_index])
        duration_min = max(MIN_MORA_FRAMES, int(math.ceil(target * DURATION_RATIO_MIN)))
        duration_max = max(duration_min, int(math.floor(target * DURATION_RATIO_MAX)))
        remaining_morae = count - mora_index - 1
        k_min = (mora_index + 1) * MIN_MORA_FRAMES
        k_max = total_frames - remaining_morae * MIN_MORA_FRAMES
        if mora_index == count - 1:
            candidate_ends = (total_frames,)
        else:
            candidate_ends = range(k_min, k_max + 1)
        for k in candidate_ends:
            j_low = max(0, k - duration_max)
            j_high = k - duration_min
            if j_high < j_low:
                continue
            best_cost = infinity
            best_j = -1
            for j in range(j_low, j_high + 1):
                if not math.isfinite(float(previous[j])):
                    continue
                duration = k - j
                duration_cost = 1.25 * math.log(duration / target) ** 2
                mean_pitch = float(
                    (prefix_evidence[mora_index, k] - prefix_evidence[mora_index, j]) / duration
                )
                pitch_cost = 1.0 - mean_pitch
                boundary_cost = 0.0
                if mora_index != count - 1:
                    boundary_cost = 0.35 * (1.0 - float(boundary_strength[start + k]))
                candidate = float(previous[j]) + duration_cost + pitch_cost + boundary_cost
                if candidate < best_cost - TIE_EPSILON or (
                    abs(candidate - best_cost) <= TIE_EPSILON and (best_j < 0 or j < best_j)
                ):
                    best_cost = candidate
                    best_j = j
            if best_j >= 0:
                current[k] = best_cost
                back[k] = best_j
        previous = current
        predecessors.append(back)
    total_cost = float(previous[total_frames])
    if not math.isfinite(total_cost):
        return {
            "status": "ALIGNMENT_FAILED",
            "reason": "no path satisfies the frozen duration constraints",
            "mora_count": count,
            "boundaries_s": [],
            "total_cost": None,
            "normalized_cost": None,
        }
    local_boundaries = [total_frames]
    cursor = total_frames
    for mora_index in range(count - 1, -1, -1):
        cursor = int(predecessors[mora_index][cursor])
        if cursor < 0:
            raise PracticeAlignmentError("internal DP backtrace failure")
        local_boundaries.append(cursor)
    local_boundaries.reverse()
    normalized_cost = total_cost / count
    if normalized_cost > MAX_NORMALIZED_COST:
        return {
            "status": "ALIGNMENT_FAILED",
            "reason": "normalized DP cost exceeds frozen maximum",
            "mora_count": count,
            "boundaries_s": [],
            "total_cost": total_cost,
            "normalized_cost": normalized_cost,
        }
    boundaries_s = [
        (start + boundary) * FRAME_HOP_SAMPLES / SAMPLE_RATE for boundary in local_boundaries
    ]
    return {
        "status": "ALIGNED",
        "reason": None,
        "mora_count": count,
        "boundaries_s": boundaries_s,
        "total_cost": total_cost,
        "normalized_cost": normalized_cost,
    }


def align_actor_files(wav_path: Path, projection_path: Path) -> Dict[str, Any]:
    """Preflight both paths before opening either; then read each exactly once."""
    wav = _reject_actor_path(wav_path, "wav", ".wav")
    projection = _reject_actor_path(projection_path, "score_projection", ".json")
    wav_bytes = wav.read_bytes()
    projection_bytes = projection.read_bytes()
    try:
        projection_value = json.loads(projection_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PracticeAlignmentError(f"score_projection: invalid JSON: {exc}") from exc
    return align_wav_to_projection(wav_bytes, projection_value, label=str(wav))


def _build_projection_file(source: Path) -> Dict[str, Any]:
    source = _reject_actor_path(source, "musicxml_source", source.suffix.lower())
    if source.suffix.lower() not in (".musicxml", ".xml"):
        raise PracticeAlignmentError("musicxml_source: expected .musicxml or .xml")
    return build_score_projection(source.read_bytes(), label=str(source))


def measure_w2(corpus_root: Path, song_ids: Sequence[str]) -> Dict[str, Any]:
    """Run a fixed real-song smoke without enumerating or opening ``.lab``."""
    songs: List[Dict[str, Any]] = []
    for song_id in song_ids:
        if not song_id.startswith("pjs") or len(song_id) != 6 or not song_id[3:].isdigit():
            raise PracticeAlignmentError(f"invalid PJS song id {song_id!r}")
        song_root = corpus_root / song_id
        musicxml = song_root / f"{song_id}.musicxml"
        wav = song_root / f"{song_id}_song.wav"
        projection = _build_projection_file(musicxml)
        projection_bytes = canonical_json_bytes(projection)
        wav_bytes = wav.read_bytes()
        result = align_wav_to_projection(wav_bytes, projection, label=str(wav))
        result_bytes = canonical_json_bytes(result)
        boundaries = result["boundaries_s"]
        monotonic = bool(boundaries) and all(
            boundaries[index] < boundaries[index + 1] for index in range(len(boundaries) - 1)
        )
        songs.append(
            {
                "song_id": song_id,
                "projection_sha256": sha256_bytes(projection_bytes),
                "alignment_sha256": sha256_bytes(result_bytes),
                "status": result["status"],
                "mora_count": result["mora_count"],
                "boundary_count": len(boundaries),
                "boundaries_strictly_monotonic": monotonic,
                "normalized_cost": result["normalized_cost"],
            }
        )
    return {
        "schema": "run9-practice-alignment-w2-measurement/1.0",
        "algorithm": "monotonic_segment_dp_v1",
        "song_ids": list(song_ids),
        "songs": songs,
        "overall_pass": all(
            row["status"] == "ALIGNED"
            and row["boundary_count"] == row["mora_count"] + 1
            and row["boundaries_strictly_monotonic"]
            for row in songs
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project")
    project.add_argument("musicxml", type=Path)
    align = commands.add_parser("align")
    align.add_argument("wav", type=Path)
    align.add_argument("projection", type=Path)
    measure = commands.add_parser("measure-w2")
    measure.add_argument("corpus_root", type=Path)
    measure.add_argument("song_ids", nargs="+")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "project":
            value = _build_projection_file(args.musicxml)
        elif args.command == "align":
            value = align_actor_files(args.wav, args.projection)
        else:
            value = measure_w2(args.corpus_root, args.song_ids)
        sys.stdout.buffer.write(canonical_json_bytes(value))
        return 0
    except (OSError, PracticeAlignmentError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
