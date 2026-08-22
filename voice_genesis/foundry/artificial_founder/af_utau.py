"""af_utau.py — AF0 Founder Body（UTAU voicebank）の生成・構造検査・原子的公開。

設計書 §11 / §19 G3 / §28 Artifact Safety。

```text
build complete -> validate all -> hash all -> publish atomically
```

失敗時は旧 valid bundle を保持し、partial generation を canonical 成果物として
残さない（§28）。**oto.ini は自動生成のみで人手編集禁止**（§11）。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

from af_schema import FROZEN_ALIASES
from af_spec import (MANIFEST_SCHEMA, UNIT_TRUTH_SCHEMA, AFStop, FounderGenome,
                     aggregate_digest, sha256_tree, sha256sums_text, write_json)
from af_synth import SynthesizedUnit, synthesize_body

#: oto.ini / character.txt の既定エンコーディング（UTAU 慣行）。
OTO_ENCODING = "cp932"

#: overlap の上限（§11）。
OVERLAP_CAP_MS = 15.0

CHARACTER_TXT_LINES: Tuple[str, ...] = (
    "name=AF0",
    "image=",
    "author=VoiceGenesis Artificial Founder Series",
    "web=",
    "version=AF-P0 / 0.1",
)

README_TXT_LINES: Tuple[str, ...] = (
    "AF0 - VoiceGenesis Artificial Founder (AF-P0)",
    "",
    "phenotype: Dual Resonance / Afterglow",
    "origin: procedural source-filter synthesis from founder_specs/AF0.json",
    "",
    "This voicebank contains NO human recording, NO existing voicebank material,",
    "and NO pretrained voice model output. Every sample is generated",
    "deterministically from a numeric specification.",
    "",
    "oto.ini is machine-generated. Do not hand-edit it: the P0 record pins its",
    "SHA-256, and any edit invalidates the determinism gate (G2) and the",
    "voicebank identity hash (G3).",
)


@dataclass(frozen=True)
class ParsedOtoEntry:
    """oto.ini 1 行の解析結果（`adapter.donor_bank_utau.OtoEntry` と同一フィールド）。"""

    wav_filename: str
    alias: str
    offset_ms: float
    consonant_ms: float
    blank_ms: float
    preutterance_ms: float
    overlap_ms: float


def decode_oto_bytes(data: bytes) -> str:
    """oto.ini のデコード（cp932 既定・UTF-8 フォールバック）。"""
    try:
        return data.decode(OTO_ENCODING)
    except UnicodeDecodeError:
        return data.decode("utf-8")


def parse_oto_text(text: str) -> List[ParsedOtoEntry]:
    """`filename=alias,offset,consonant,blank,preutterance,overlap` を解析する。

    **`adapter/donor_bank_utau.parse_oto_text` と同一規則**（フィールド数不一致・
    非数値の行は黙って skip）を、依存フリーで再実装したもの。共有実装を直接
    import しないのは、`donor_bank_utau` -> `donor_bank` -> `import pyworld` の
    連鎖で **G3（UTAU 構造検査）が WORLD 依存になってしまう**ため。G3 は
    「compiler が正しい形の Body を出したか」の検査であって取り込み経路の検査
    ではなく、pyworld 未導入環境（CI）でも評価できなければならない（§19 / §20 の
    BLOCKED 分類が dependency 欠落と構造不良を取り違える）。

    共有実装との同値は `tests/test_artificial_founder_p0.py::
    test_oto_parser_matches_adapter` が pyworld 導入環境で pin する。
    """
    entries: List[ParsedOtoEntry] = []
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
            entries.append(ParsedOtoEntry(
                wav_filename=wav_filename, alias=alias, offset_ms=float(offset),
                consonant_ms=float(consonant), blank_ms=float(blank),
                preutterance_ms=float(preutt), overlap_ms=float(overlap)))
        except ValueError:
            continue
    return entries


def _fmt(value: float) -> str:
    """oto.ini の数値表記（小数 3 桁固定）。丸めを 1 箇所へ集約する（§27）。"""
    return f"{value:.3f}"


def oto_line(unit_stem: str, alias: str, offset_ms: float, consonant_ms: float,
             blank_ms: float, preutterance_ms: float, overlap_ms: float) -> str:
    return (f"{unit_stem}.wav={alias},{_fmt(offset_ms)},{_fmt(consonant_ms)},"
            f"{_fmt(blank_ms)},{_fmt(preutterance_ms)},{_fmt(overlap_ms)}")


def build_oto_text(genome: FounderGenome, units: Sequence[SynthesizedUnit]) -> str:
    """§11 の規則で oto.ini 本文を組む（自動生成のみ）。"""
    lines: List[str] = []
    for su in units:
        onset_ms = su.truth.onset_ms
        if su.unit.has_onset:
            overlap = min(OVERLAP_CAP_MS, onset_ms / 2.0)
            lines.append(oto_line(su.unit.stem, su.unit.alias, genome.lead_zero_ms,
                                  onset_ms, genome.tail_zero_ms, onset_ms, overlap))
        else:
            lines.append(oto_line(su.unit.stem, su.unit.alias, genome.lead_zero_ms,
                                  0.0, genome.tail_zero_ms, 0.0, 0.0))
    return "\r\n".join(lines) + "\r\n"


@dataclass(frozen=True)
class BodyBuild:
    root: Path
    pitch_dir: Path
    units: List[SynthesizedUnit]
    manifest: Dict[str, Any]
    sha_rows: List[Tuple[str, str]]
    identity_digest: str


def write_body(genome: FounderGenome, staging_root: str | Path) -> BodyBuild:
    """staging へ Founder Body 一式を書き出す（§28: まず staging、公開は別）。"""
    root = Path(staging_root)
    if root.exists():
        shutil.rmtree(root)
    pitch_dir = root / genome.pitch_dir
    pitch_dir.mkdir(parents=True, exist_ok=True)

    units = synthesize_body(genome)
    for su in units:
        sf.write(str(pitch_dir / su.unit.wav_filename), su.pcm16, genome.sample_rate_hz,
                 subtype="PCM_16", format="WAV")
    (pitch_dir / "oto.ini").write_bytes(
        build_oto_text(genome, units).encode(OTO_ENCODING))
    (root / "character.txt").write_bytes(
        ("\r\n".join(CHARACTER_TXT_LINES) + "\r\n").encode(OTO_ENCODING))
    (root / "readme.txt").write_bytes(
        ("\r\n".join(README_TXT_LINES) + "\r\n").encode("ascii"))

    truth = {
        "schema": UNIT_TRUTH_SCHEMA,
        "founder_id": genome.founder_id,
        "spec_sha256": genome.sha256,
        "units": [su.truth.as_dict() for su in units],
    }
    write_json(root / "unit_truth.json", truth)

    manifest: Dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "founder_id": genome.founder_id,
        "phenotype_codename": genome.phenotype_codename,
        "spec_sha256": genome.sha256,
        "format": "utau-classic-cv",
        "sample_rate_hz": genome.sample_rate_hz,
        "pcm_format": "s16le",
        "pitch_dirs": [genome.pitch_dir],
        "aliases": [su.unit.alias for su in units],
        "wav_files": [f"{genome.pitch_dir}/{su.unit.wav_filename}" for su in units],
        "n_units": len(units),
        "peak_abs_max": max((su.truth.peak_abs for su in units), default=0.0),
        "origin": {
            "human_audio_used": False,
            "speaker_specific_parameters_used": False,
            "pretrained_voice_model_used": False,
            "external_voicebank_used": False,
            "network_access_used": False,
        },
    }
    write_json(root / "founder_manifest.json", manifest)

    rows = sha256_tree(root, exclude_names=("SHA256SUMS.txt",))
    (root / "SHA256SUMS.txt").write_text(sha256sums_text(rows), encoding="utf-8")
    rows = sha256_tree(root, exclude_names=("SHA256SUMS.txt",))
    return BodyBuild(root=root, pitch_dir=pitch_dir, units=units, manifest=manifest,
                     sha_rows=rows, identity_digest=aggregate_digest(rows))


# ---------------------------------------------------------------------------
# §19 G3 構造検査
# ---------------------------------------------------------------------------
def validate_body(genome: FounderGenome, root: str | Path) -> Dict[str, Any]:
    """`pitch dirs = 1 / entries = 25 / WAV = 25 / missing = 0 / orphan = 0 /
    malformed = 0` を fail-closed 検査する（§19 G3）。"""
    root = Path(root)
    detail: Dict[str, Any] = {}
    pitch_dirs = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "oto.ini").exists())
    detail["pitch_dirs"] = pitch_dirs

    if len(pitch_dirs) != 1 or pitch_dirs[0] != genome.pitch_dir:
        detail["error"] = f"expected exactly 1 pitch dir {genome.pitch_dir!r}, got {pitch_dirs}"
        detail["verdict"] = "FAIL"
        return detail

    pdir = root / pitch_dirs[0]
    raw = (pdir / "oto.ini").read_bytes()
    text = decode_oto_bytes(raw)
    # malformed 行 = `=` を含むのに 6 フィールドへ解けない行。`parse_oto_text` は
    # それを黙って skip するので、ここで別途数える（黙殺の検出が G3 の眼目）。
    non_empty = [ln for ln in text.splitlines() if ln.strip() and "=" in ln]
    entries = parse_oto_text(text)
    malformed = len(non_empty) - len(entries)
    wavs = sorted(p.name for p in pdir.glob("*.wav"))
    referenced = sorted({e.wav_filename for e in entries})
    missing = [w for w in referenced if not (pdir / w).exists()]
    orphan = [w for w in wavs if w not in set(referenced)]
    nested = [e.wav_filename for e in entries
              if ("/" in e.wav_filename or "\\" in e.wav_filename
                  or Path(e.wav_filename).name != e.wav_filename)]
    aliases = [e.alias for e in entries]

    detail.update({
        "n_entries": len(entries), "n_wav": len(wavs), "n_malformed_lines": malformed,
        "missing_wav": missing, "orphan_wav": orphan, "nested_wav_refs": nested,
        "duplicate_alias": sorted({a for a in aliases if aliases.count(a) > 1}),
        "aliases": aliases,
    })
    # 期待数は **凍結インベントリ** から取る（`len(genome.units)` だと、縮小した
    # genome が期待数まで一緒に縮んで 25 unit の Body 契約を検査できない）。
    expected = len(FROZEN_ALIASES)
    detail["expected_units"] = expected
    ok = (len(entries) == expected and len(wavs) == expected and malformed == 0
          and not missing and not orphan and not nested
          and sorted(aliases) == sorted(FROZEN_ALIASES)
          and not detail["duplicate_alias"])
    detail["verdict"] = "PASS" if ok else "FAIL"
    return detail


def check_sha256sums(root: str | Path) -> Dict[str, Any]:
    """`SHA256SUMS.txt` と実体の一致を検査する（§29 test 29/30 の土台）。"""
    root = Path(root)
    listed: Dict[str, str] = {}
    for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, rel = line.partition("  ")
        listed[rel] = sha
    actual = dict(sha256_tree(root, exclude_names=("SHA256SUMS.txt",)))
    mismatched = sorted(k for k in listed if k in actual and actual[k] != listed[k])
    return {
        "missing": sorted(set(listed) - set(actual)),
        "extra": sorted(set(actual) - set(listed)),
        "mismatched": mismatched,
        "verdict": "PASS" if not (mismatched or set(listed) ^ set(actual)) else "FAIL",
    }


# ---------------------------------------------------------------------------
# §28 原子的公開 / rollback
# ---------------------------------------------------------------------------
def publish_atomically(staging: str | Path, published: str | Path,
                       keep_rollback: bool = False) -> Dict[str, Any]:
    """staging を published へ原子的に差し替える。失敗したら旧世代を復元する。

    2 段 rename（published -> .old、staging -> published）。2 段目で落ちたら
    退避した旧世代を元へ戻してから送出するので、`published` が「無い」状態や
    partial な状態で残ることはない。

    `keep_rollback=True` のときは退避した旧世代を **消さずに残し**、
    `rollback_path` として返す。呼び出し側は公開後の検証（SHA256SUMS 照合・
    ダイジェスト一致）が通ってから `commit_publication` で捨て、落ちたら
    `rollback_publication` で戻す。旧世代を rename 直後に消してしまうと、
    検証が落ちたときに **未検証の新 bundle が canonical 位置に残り、直前の
    valid bundle は復元不能** になる（§28 に反する）。
    """
    staging, published = Path(staging), Path(published)
    if not staging.is_dir():
        raise AFStop(cause=f"staging bundle not found: {staging}",
                     impact="publication would leave no canonical artifact",
                     minimal_fix="re-run compilation before publishing",
                     status="FAILED", reason_code="PARTIAL_PUBLICATION")
    published.parent.mkdir(parents=True, exist_ok=True)
    old = published.with_name(published.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    rotated = False
    if published.exists():
        os.rename(published, old)
        rotated = True
    try:
        os.rename(staging, published)
    except BaseException:
        if rotated:
            os.rename(old, published)
        raise
    if rotated and not keep_rollback:
        shutil.rmtree(old)
    return {"published": str(published), "rolled_over_previous": rotated,
            "rollback_path": str(old) if (rotated and keep_rollback) else None}


def commit_publication(rollback_path: Optional[str | Path]) -> bool:
    """公開後検証が通ったので退避世代を破棄する。"""
    if not rollback_path:
        return False
    old = Path(rollback_path)
    if not old.exists():
        return False
    shutil.rmtree(old)
    return True


def rollback_publication(published: str | Path,
                         rollback_path: Optional[str | Path]) -> bool:
    """公開後検証が落ちたので、退避しておいた旧 valid bundle を戻す。"""
    if not rollback_path:
        return False
    old, published = Path(rollback_path), Path(published)
    if not old.exists():
        return False
    if published.exists():
        shutil.rmtree(published)
    os.rename(old, published)
    return True


def rollback_marker(published: str | Path) -> Optional[Path]:
    p = Path(published).with_name(Path(published).name + ".old")
    return p if p.exists() else None


def load_body_wavs(root: str | Path, pitch_dir: str) -> Dict[str, Tuple[np.ndarray, int]]:
    """Body の全 WAV を float64 で読む（測定入口。Ground Truth は読まない）。"""
    out: Dict[str, Tuple[np.ndarray, int]] = {}
    pdir = Path(root) / pitch_dir
    for wav in sorted(pdir.glob("*.wav")):
        data, sr = sf.read(str(wav), dtype="float64", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        out[wav.stem] = (np.ascontiguousarray(data, dtype=np.float64), int(sr))
    return out
