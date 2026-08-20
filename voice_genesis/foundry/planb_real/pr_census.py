"""planb_real/pr_census.py — 展開後コーパスの自動 census（instruction §3）。

**この段階ではレンダリングしない。** 何が何件あり、probe がどれだけ取れるかを
数えるだけ。構造が想定と違えば数え上げの結果としてそれが見えるようにする
（決め打ちの階層を仮定せず、拡張子で発見する）。

出力: corpus_inventory_<name>.json / coverage_comparison.json
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import soundfile as sf

import pr_lab
from pr_status import BLOCKED, PASS, GateResult

WAV_EXT = (".wav", ".flac")
LAB_EXT = (".lab",)
XML_EXT = (".musicxml", ".xml", ".mxl")

#: lab が wav より長いときの許容比。超えたら **fail-closed 方向**の不整合として警告。
#: （lab が wav より短いのは末尾の未ラベル無音であって不整合ではない = info 扱い）
LAB_LONGER_TOLERANCE = 1.005
#: lab が wav より極端に短い場合の目安（時間単位の誤推定を疑う）
LAB_SHORTER_SUSPECT = 0.4

#: lab に対応する歌唱 wav の探索順。
#: PJS は `<stem>.lab` ↔ `<stem>_song.wav`（`_speech.wav` は**読み上げ**なので
#: 歌唱 Performance には使わない）。リツ DB は `<stem>.wav`。
WAV_SUFFIX_PREFERENCE = ("", "_song")


def wav_for_lab(lab_path: Path) -> Optional[Path]:
    """lab に対応する歌唱 wav を、明示した命名規則だけで解決する（推測しない）。"""
    for suffix in WAV_SUFFIX_PREFERENCE:
        for ext in WAV_EXT:
            cand = lab_path.with_name(lab_path.stem + suffix + ext)
            if cand.exists():
                return cand
    return None


@dataclass
class ProbeEvent:
    """probe 1 件（§8）。terminal 判定と尺を持ち、後段の層別に使える。"""

    kind: str            # "terminal_ri" / "terminal_su" / "terminal_i" / "terminal_N" / "medial_ri"
    file: str
    phone_index: int
    start_s: float
    end_s: float
    vowel_duration_s: float

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "file": self.file, "phone_index": self.phone_index,
                "start_s": round(self.start_s, 4), "end_s": round(self.end_s, 4),
                "vowel_duration_s": round(self.vowel_duration_s, 4)}


@dataclass
class CorpusInventory:
    name: str
    root: str
    wav_count: int = 0
    lab_count: int = 0
    musicxml_count: int = 0
    sample_rates: Dict[str, int] = field(default_factory=dict)
    total_duration_s: float = 0.0
    paired_count: int = 0
    unpaired_wav: List[str] = field(default_factory=list)
    unpaired_lab: List[str] = field(default_factory=list)
    phoneme_vocabulary: Dict[str, int] = field(default_factory=dict)
    lab_time_units: Dict[str, int] = field(default_factory=dict)
    lab_styles: Dict[str, int] = field(default_factory=dict)
    phrase_final_events: int = 0
    devoiced_vowel_events: int = 0
    excluded_probe_events: Dict[str, int] = field(default_factory=dict)
    unreliable_files: List[str] = field(default_factory=list)
    probes: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    structure_warnings: List[str] = field(default_factory=list)
    lab_wav_pairing_rule: str = "|".join(
        f"<stem>{s}.wav" for s in ("", "_song"))

    def probe_counts(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self.probes.items()}

    def as_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["probe_counts"] = self.probe_counts()
        return d

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")


def _discover(root: Path, exts: Tuple[str, ...]) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in exts and p.is_file())


def _probe_events(lab: pr_lab.LabFile) -> List[ProbeEvent]:
    """probe を数える。**無声化母音（大文字 AIUEO）は除外**する（F0 が測れない）。"""
    phones = lab.phones
    finals = set(pr_lab.phrase_final_indices(phones))
    events: List[ProbeEvent] = []

    def add(kind: str, idx: int) -> None:
        p = phones[idx]
        if p.is_devoiced_vowel:
            return
        events.append(ProbeEvent(kind=kind, file=lab.path, phone_index=idx,
                                 start_s=p.start_s, end_s=p.end_s,
                                 vowel_duration_s=p.duration_s))

    for idx in pr_lab.find_bigram(phones, "r", "i"):
        add("terminal_ri" if idx in finals else "medial_ri", idx)
    for idx in pr_lab.find_bigram(phones, "s", "u"):
        if idx in finals:
            add("terminal_su", idx)
    for idx in pr_lab.find_phone(phones, "i"):
        if idx in finals and not (idx > 0 and phones[idx - 1].label == "r"):
            add("terminal_i", idx)
    for idx in pr_lab.find_phone(phones, "N"):
        if idx in finals:
            add("terminal_N", idx)
    return events


def census(root: Path, name: str) -> CorpusInventory:
    root = Path(root)
    inv = CorpusInventory(name=name, root=str(root))
    wavs = _discover(root, WAV_EXT)
    labs = _discover(root, LAB_EXT)
    xmls = _discover(root, XML_EXT)
    inv.wav_count, inv.lab_count, inv.musicxml_count = len(wavs), len(labs), len(xmls)

    sr_counter: Counter = Counter()
    wav_by_stem: Dict[str, Path] = {}
    for w in wavs:
        wav_by_stem.setdefault(w.stem, w)
        try:
            info = sf.info(str(w))
        except Exception as exc:  # noqa: BLE001 - 壊れた素材も census 対象
            inv.structure_warnings.append(f"wav 読めず: {w.name} ({type(exc).__name__})")
            continue
        sr_counter[str(info.samplerate)] += 1
        inv.total_duration_s += float(info.frames) / float(info.samplerate)
    inv.sample_rates = dict(sr_counter)

    vocab: Counter = Counter()
    units: Counter = Counter()
    styles: Counter = Counter()
    excluded: Counter = Counter()
    probes: Dict[str, List[Dict[str, Any]]] = {
        k: [] for k in ("terminal_ri", "terminal_su", "terminal_i", "terminal_N", "medial_ri")
    }
    lab_stems = set()
    for lb in labs:
        lab_stems.add(lb.stem)
        parsed = pr_lab.read_lab(lb)
        units[parsed.time_unit] += 1
        styles[parsed.style] += 1
        for p in parsed.phones:
            vocab[p.label] += 1
        inv.phrase_final_events += len(pr_lab.phrase_final_indices(parsed.phones))
        inv.devoiced_vowel_events += sum(1 for p in parsed.phones if p.is_devoiced_vowel)
        # lab 尺 vs wav 尺の整合（時間単位の推定が正しいかの独立検算）
        w = wav_for_lab(lb)
        reliable = True
        if w is not None and parsed.total_s > 0:
            try:
                info = sf.info(str(w))
                wav_s = float(info.frames) / float(info.samplerate)
                if parsed.total_s > wav_s * LAB_LONGER_TOLERANCE:
                    reliable = False
                    inv.structure_warnings.append(
                        f"lab が wav より長い: {lb.name} "
                        f"(lab {parsed.total_s:.2f}s > wav {wav_s:.2f}s) — "
                        "probe を除外した")
                elif parsed.total_s < wav_s * LAB_SHORTER_SUSPECT:
                    inv.structure_warnings.append(
                        f"lab が wav より極端に短い: {lb.name} "
                        f"(lab {parsed.total_s:.2f}s << wav {wav_s:.2f}s, unit={parsed.time_unit})")
            except Exception as exc:  # noqa: BLE001
                # wav が開けないなら、その lab の probe は信用できない
                reliable = False
                inv.structure_warnings.append(
                    f"wav を読めない: {lb.name} ({type(exc).__name__}) — probe を除外した")
        if parsed.parse_warnings:
            # 行が落ちた lab は音素列が欠けており、終端判定が偽になりうる
            reliable = False
        events = _probe_events(parsed)
        if reliable:
            for ev in events:
                probes[ev.kind].append(ev.as_dict())
        else:
            inv.unreliable_files.append(lb.name)
            for ev in events:
                excluded[ev.kind] += 1
        if parsed.parse_warnings:
            inv.structure_warnings.append(
                f"{lb.name}: パース警告 {len(parsed.parse_warnings)} 件 — probe を除外した")

    inv.phoneme_vocabulary = dict(vocab.most_common())
    inv.lab_time_units = dict(units)
    inv.lab_styles = dict(styles)
    inv.probes = probes
    inv.excluded_probe_events = dict(excluded)
    paired_lab_stems = set()
    for lb in labs:
        w = wav_for_lab(lb)
        if w is None:
            continue
        try:
            sf.info(str(w))          # 開けることまで確かめる（存在だけでは足りない）
        except Exception:  # noqa: BLE001
            continue
        paired_lab_stems.add(lb.stem)
    inv.paired_count = len(paired_lab_stems)
    inv.unpaired_lab = sorted(lab_stems - paired_lab_stems)[:50]
    used = {wav_for_lab(lb).stem for lb in labs if wav_for_lab(lb) is not None}
    inv.unpaired_wav = sorted(set(wav_by_stem) - used)[:50]
    return inv


def coverage_comparison(ritsu: CorpusInventory, pjs: CorpusInventory) -> Dict[str, Any]:
    kinds = ("terminal_ri", "terminal_su", "terminal_i", "terminal_N", "medial_ri")
    rc, pc = ritsu.probe_counts(), pjs.probe_counts()
    return {
        "probe_counts": {k: {"ritsu": rc.get(k, 0), "pjs": pc.get(k, 0)} for k in kinds},
        "usable_for_ladder": {
            k: bool(rc.get(k, 0) > 0 and pc.get(k, 0) > 0) for k in kinds
        },
        "identity_side_only": [k for k in kinds if rc.get(k, 0) > 0 and pc.get(k, 0) == 0],
        "performance_side_only": [k for k in kinds if pc.get(k, 0) > 0 and rc.get(k, 0) == 0],
        "absent_both": [k for k in kinds if rc.get(k, 0) == 0 and pc.get(k, 0) == 0],
        "phoneme_vocabulary_overlap": sorted(
            set(ritsu.phoneme_vocabulary) & set(pjs.phoneme_vocabulary)),
        "ritsu_only_phonemes": sorted(
            set(ritsu.phoneme_vocabulary) - set(pjs.phoneme_vocabulary)),
        "pjs_only_phonemes": sorted(
            set(pjs.phoneme_vocabulary) - set(ritsu.phoneme_vocabulary)),
    }


def gate_census(ritsu: CorpusInventory, pjs: CorpusInventory,
                comparison: Dict[str, Any]) -> GateResult:
    """G-CENSUS。primary probe（terminal /ri/）が両側に無ければ先へ進めない。"""
    problems: List[str] = []
    if ritsu.paired_count == 0:
        problems.append("Ritsu: wav と lab の対応が 1 件も取れない")
    if pjs.paired_count == 0:
        problems.append("PJS: wav と lab の対応が 1 件も取れない")
    if "unknown" in ritsu.lab_time_units or "unknown" in pjs.lab_time_units:
        problems.append("lab の時間単位を推定できないファイルがある")
    if ritsu.probe_counts().get("terminal_ri", 0) == 0:
        problems.append("Ritsu 側に primary probe (terminal /ri/) が 1 件も無い")
    if pjs.probe_counts().get("terminal_ri", 0) == 0:
        problems.append("PJS 側に primary probe (terminal /ri/) が 1 件も無い")
    ev = {"ritsu": ritsu.probe_counts(), "pjs": pjs.probe_counts(),
          "ritsu_warnings": ritsu.structure_warnings[:10],
          "pjs_warnings": pjs.structure_warnings[:10],
          "usable_for_ladder": comparison["usable_for_ladder"]}
    if problems:
        return GateResult(gate="G-CENSUS", status=BLOCKED, detail="; ".join(problems),
                          next_action=(
                              "corpus_inventory_*.json の phoneme_vocabulary と "
                              "structure_warnings を確認し、ラベル書式（時間単位・音素表記）"
                              "に合わせて pr_lab の SILENCE_PHONES / MORAIC_NASAL_ALIASES を"
                              "明示的に拡張すること"),
                          evidence=ev)
    return GateResult(gate="G-CENSUS", status=PASS,
                      detail=f"Ritsu {ritsu.paired_count} 対 / PJS {pjs.paired_count} 対、"
                             f"primary probe 両側に実在",
                      evidence=ev)
