"""run8/s7_calib_score.py — B-1「校正専用 real-render セット」の譜面と命令。

`results_s7/s7_b1_calibration_set.json` の `real_render_set`（**生成前に
commit/pin 済み**の事前登録）が要求する 11 レンダ条件を、DiffSinger 経路
（`s1_gate/gate_synth.py`）へ渡せる形へ翻訳するだけのモジュール。

本モジュールは**事前登録に書かれていない条件を足さない**。条件 id・音高・
拍・延長 ms・r:i 配分はすべて事前登録 JSON から読み、ここでは
「その条件をこの合成経路でどう命令するか」だけを決める:

- 音素列（`/ri/` = [r, i] / `/i/` = [i] / `/N/` = [N] / 無音 = [SP]）
- `terminal_extension_ms` → 終端音素へ加算するフレーム数
- `onset_ratio` → 終端 `/ri/` の r:i フレーム配分

なぜフックが要るか: 譜面が命令できるのは**ノートの尺**までで、`r:i` の
内部配分は `dsdur/dur.onnx` の**予測**である（`gate_synth.run_pipeline`
Stage 1）。事前登録の `rr_dur_perturb_*` は配分そのものを水準にする条件
なので、譜面では実現できない。`rr_long_tail_*` も同様に「譜面の境界を
越えて有声を伸ばす」条件であり、ノート尺を伸ばすと境界自体が動いてしまう。
どちらも `run_pipeline(final_phone_dur_override=...)`（既定 None = 本番と
同一経路）で命令する。

境界（`score_boundary_s` / `commanded_note_end_s`）は**レンダ命令から**
決める（波形から推定しない = 事前登録 `boundaries.note`）。梯子
`rr_long_tail_*` は 4 条件とも**同じ譜面・同じ境界**で、終端フレームの
延長量だけが増える（合成刺激側の `tail_voiced_ms` 梯子と同型: 境界固定・
有声のはみ出しだけが増える）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# `gate_synth` と同じフレーム格子（hop 512 / 44.1 kHz）。
HOP_SIZE = 512
SAMPLE_RATE = 44100
FRAME_MS = 1000.0 * HOP_SIZE / SAMPLE_RATE
HEAD_FRAMES = 8  # gate_synth.HEAD_FRAMES（Stage 2 の先頭 SP パディング）
TEMPO_BPM = 72.0  # singer/score.py と同じ（校正は絶対テンポを問わない）

# 条件 id -> 終端モーラの音素列。事前登録の `score` 文字列と
# `assert_consistent_with_prereg()` で相互照合する（食い違ったら停止）。
MORA_PHONES: Dict[str, Tuple[str, ...]] = {
    "rr_clean_terminal_ri": ("r", "i"),
    "rr_clean_i": ("i",),
    "rr_clean_N": ("N",),
    "rr_long_tail_000": ("i",),
    "rr_long_tail_040": ("i",),
    "rr_long_tail_080": ("i",),
    "rr_long_tail_160": ("i",),
    "rr_dur_perturb_r020": ("r", "i"),
    "rr_dur_perturb_r035": ("r", "i"),
    "rr_dur_perturb_r050": ("r", "i"),
    "rr_silence": ("SP",),
}

SILENCE_ID = "rr_silence"
# 無音条件は「ノート無し」だが、合成経路は最低 1 ノートを要求する。SP のみの
# ノートを 1 つ置いて**同じ経路**を通す（音高は経路上必須なので 60 を置くが、
# SP トークンに音高の意味は無い。事前登録の `pitch_midi: null` との差は
# manifest に `pitch_midi_prereg` / `pitch_midi_applied` として両方記帳する）。
SILENCE_PITCH_MIDI_APPLIED = 60.0

# 全条件を**同じ時間支持**へそろえるためのゼロ詰め余白（境界 + 終端窓 + 余白）。
# 実レンダは条件ごとに長さが違う（終端延長した条件ほど長い）ため、詰めないと
# `tail_f0_persistence` の分母（終端窓に入るフレーム数）が条件ごとに変わり、
# 「有声が伸びた」ことと「音源が長い」ことが分離できなくなる。ゼロ詰めは
# 有声を足さない（無音を足すだけ）ので、合成刺激側の固定長バッファ
# （`common.total_seconds` = 1.6 s）と同じ役割を実レンダ側に与える。
TOTAL_PAD_MARGIN_MS = 50.0


@dataclass(frozen=True)
class CalibMora:
    """`gate_synth.mora_phonemes` が読むのは `onset`/`vowel` だけ。"""

    onset: Optional[str]
    vowel: str


@dataclass(frozen=True)
class CalibNote:
    """`gate_synth.run_pipeline` が読むのは `midi`/`duration_beats`/`mora`。"""

    midi: float
    duration_beats: float
    mora: CalibMora


def beats_to_seconds(beats: float, tempo_bpm: float) -> float:
    return float(beats) * 60.0 / float(tempo_bpm)


def frames_from_ms(ms: float) -> int:
    """`gate_synth.frames_from_ms` と同一（丸め方を変えない）。"""
    return max(int(round(ms / FRAME_MS)), 0)


def mora_from_phones(phones: Tuple[str, ...]) -> CalibMora:
    if len(phones) == 1:
        return CalibMora(onset=None, vowel=phones[0])
    if len(phones) == 2:
        return CalibMora(onset=phones[0], vowel=phones[1])
    raise ValueError(f"unsupported phone sequence: {phones!r}")


def assert_consistent_with_prereg(
    condition: Dict[str, Any], previous_phones: Optional[Tuple[str, ...]] = None
) -> None:
    """事前登録の `score` 記述と `MORA_PHONES` の食い違いを停止させる。

    表を書き換えて事前登録と別の音素を鳴らす事故（= 事前登録の意味が
    後から変わる）を構造的に防ぐための照合。事前登録は継続記法
    （`同上` / `同 0.35`）を使うので、その場合は**直前条件と同じ音素で
    あること**を要求する（直前が無ければ停止）。
    """
    cid = str(condition["id"])
    if cid not in MORA_PHONES:
        raise KeyError(f"condition {cid!r} is not in MORA_PHONES")
    score = str(condition.get("score", ""))
    phones = MORA_PHONES[cid]
    if cid == SILENCE_ID:
        if "SP" not in score:
            raise ValueError(f"{cid}: prereg score {score!r} does not mention SP")
        return
    if score.startswith("同"):
        # 継続記法。直前条件と同じ音素であることを要求する。
        if previous_phones is None:
            raise ValueError(f"{cid}: 継続記法 {score!r} だが直前条件が無い")
        if previous_phones != phones:
            raise ValueError(
                f"{cid}: 継続記法 {score!r} だが音素が直前 {previous_phones} と違う（{phones}）"
            )
        return
    for token, expected in (("/ri/", ("r", "i")), ("/N/", ("N",)), ("/i/", ("i",))):
        if token in score:
            if phones != expected:
                raise ValueError(
                    f"{cid}: prereg score {score!r} says {token} but table has {phones}"
                )
            return
    raise ValueError(f"{cid}: cannot verify prereg score {score!r} against {phones}")


# --- 命令フレーム配分の override --------------------------------------------


def make_tail_extension_override(extension_frames: int) -> Callable[[List[int], dict], List[int]]:
    """終端音素へ `extension_frames` を加算する（梯子 `rr_long_tail_*`）。"""

    def _apply(final_phone_dur: List[int], _ctx: dict) -> List[int]:
        out = list(final_phone_dur)
        out[-1] = int(out[-1]) + int(extension_frames)
        return out

    return _apply


def make_onset_ratio_override(onset_ratio: float) -> Callable[[List[int], dict], List[int]]:
    """終端ノートの 2 音素（r:i）を `onset_ratio` で配分する。

    総和はノート命令のまま（`rr_dur_perturb_*` は尺ではなく**配分**の条件）。
    """

    def _apply(final_phone_dur: List[int], ctx: dict) -> List[int]:
        counts = list(ctx["note_phone_counts"])
        if counts[-1] != 2:
            raise ValueError(f"onset_ratio override needs a 2-phone terminal note, got {counts[-1]}")
        out = list(final_phone_dur)
        total = int(out[-2]) + int(out[-1])
        if total < 2:
            raise ValueError(f"terminal note too short to split: {total} frames")
        onset_frames = int(round(float(onset_ratio) * total))
        onset_frames = max(1, min(total - 1, onset_frames))
        out[-2] = onset_frames
        out[-1] = total - onset_frames
        return out

    return _apply


# --- 条件 -> 譜面 + 命令 ------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRender:
    condition_id: str
    maps_to: str
    notes: Tuple[CalibNote, ...]
    tempo_bpm: float
    pitch_midi_prereg: Optional[float]
    pitch_midi_applied: float
    beats: float
    note_target_frames: Tuple[int, ...]
    note_onset_s: float
    commanded_note_end_s: float
    score_boundary_s: float
    tail_window_ms: float
    terminal_extension_ms: float
    terminal_extension_frames: int
    onset_ratio: Optional[float]
    override: Optional[Callable[[List[int], dict], List[int]]]

    def command_summary(self) -> Dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "maps_to": self.maps_to,
            "tempo_bpm": self.tempo_bpm,
            "beats": self.beats,
            "pitch_midi_prereg": self.pitch_midi_prereg,
            "pitch_midi_applied": self.pitch_midi_applied,
            "phones": [p for n in self.notes for p in _phones_of(n.mora)],
            "note_target_frames": list(self.note_target_frames),
            "frame_ms": FRAME_MS,
            "head_frames": HEAD_FRAMES,
            "note_onset_s": self.note_onset_s,
            "commanded_note_end_s": self.commanded_note_end_s,
            "score_boundary_s": self.score_boundary_s,
            "tail_window_ms": self.tail_window_ms,
            "terminal_extension_ms_prereg": self.terminal_extension_ms,
            "terminal_extension_frames_applied": self.terminal_extension_frames,
            "terminal_extension_ms_applied": self.terminal_extension_frames * FRAME_MS,
            "onset_ratio_prereg": self.onset_ratio,
            "override_kind": (
                "tail_extension_frames"
                if self.terminal_extension_frames > 0
                else ("terminal_onset_ratio" if self.onset_ratio is not None else None)
            ),
        }


def _phones_of(mora: CalibMora) -> List[str]:
    return [mora.onset, mora.vowel] if mora.onset is not None else [mora.vowel]


def build_render(
    condition: Dict[str, Any],
    tail_window_ms: float,
    previous_phones: Optional[Tuple[str, ...]] = None,
) -> CalibrationRender:
    """事前登録 1 条件 -> 譜面 + override。**派生条件（gain）は含まない**。"""
    assert_consistent_with_prereg(condition, previous_phones)
    cid = str(condition["id"])
    if "derived_from" in condition:
        raise ValueError(f"{cid}: derived condition is not a render (gain は再レンダしない)")

    beats = float(condition["beats"])
    pitch_prereg = condition.get("pitch_midi")
    pitch_applied = (
        SILENCE_PITCH_MIDI_APPLIED if pitch_prereg is None else float(pitch_prereg)
    )
    mora = mora_from_phones(MORA_PHONES[cid])
    note = CalibNote(midi=pitch_applied, duration_beats=beats, mora=mora)

    dur_ms = beats_to_seconds(beats, TEMPO_BPM) * 1000.0
    note_frames = frames_from_ms(dur_ms)
    note_onset_s = HEAD_FRAMES * FRAME_MS / 1000.0
    commanded_end_s = note_onset_s + note_frames * FRAME_MS / 1000.0
    boundary_s = note_onset_s + beats_to_seconds(beats, TEMPO_BPM)

    ext_ms = float(condition.get("terminal_extension_ms", 0.0) or 0.0)
    ext_frames = frames_from_ms(ext_ms) if ext_ms > 0 else 0
    onset_ratio = condition.get("onset_ratio")
    onset_ratio = None if onset_ratio is None else float(onset_ratio)

    if ext_frames > 0 and onset_ratio is not None:
        raise ValueError(f"{cid}: 延長と配分を同時に命令する条件は事前登録に無い")
    override: Optional[Callable[[List[int], dict], List[int]]] = None
    if ext_frames > 0:
        override = make_tail_extension_override(ext_frames)
    elif onset_ratio is not None:
        override = make_onset_ratio_override(onset_ratio)

    return CalibrationRender(
        condition_id=cid,
        maps_to=str(condition["maps_to"]),
        notes=(note,),
        tempo_bpm=TEMPO_BPM,
        pitch_midi_prereg=None if pitch_prereg is None else float(pitch_prereg),
        pitch_midi_applied=pitch_applied,
        beats=beats,
        note_target_frames=(note_frames,),
        note_onset_s=note_onset_s,
        commanded_note_end_s=commanded_end_s,
        score_boundary_s=boundary_s,
        tail_window_ms=float(tail_window_ms),
        terminal_extension_ms=ext_ms,
        terminal_extension_frames=ext_frames,
        onset_ratio=onset_ratio,
        override=override,
    )


def build_all_renders(real_render_set: Dict[str, Any]) -> List[CalibrationRender]:
    """事前登録の `conditions` から**レンダする 11 条件**だけを組み立てる。"""
    tail_window_ms = float(real_render_set["boundaries"]["tail_window_ms"])
    out: List[CalibrationRender] = []
    previous_phones: Optional[Tuple[str, ...]] = None
    for cond in real_render_set["conditions"]:
        if "derived_from" in cond:
            continue
        out.append(build_render(cond, tail_window_ms, previous_phones))
        previous_phones = MORA_PHONES[str(cond["id"])]
    expected = int(real_render_set["n_renders"])
    if len(out) != expected:
        raise ValueError(f"render count {len(out)} != prereg n_renders {expected}")
    return out


def derived_conditions(real_render_set: Dict[str, Any]) -> List[Dict[str, Any]]:
    """再レンダしない派生条件（線形ゲイン）。"""
    return [dict(c) for c in real_render_set["conditions"] if "derived_from" in c]


def padded_total_samples(renders: List["CalibrationRender"]) -> int:
    """全条件共通のゼロ詰め後サンプル数（決定論・命令からのみ算出）。"""
    if not renders:
        raise ValueError("no renders")
    ends = [r.score_boundary_s + r.tail_window_ms / 1000.0 for r in renders]
    total_s = max(ends) + TOTAL_PAD_MARGIN_MS / 1000.0
    return int(math.ceil(total_s * SAMPLE_RATE))


def frames_to_seconds(n_frames: int) -> float:
    return n_frames * FRAME_MS / 1000.0


assert math.isclose(FRAME_MS, 1000.0 * 512 / 44100)
