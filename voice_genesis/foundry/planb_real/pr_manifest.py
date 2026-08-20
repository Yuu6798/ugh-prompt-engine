"""planb_real/pr_manifest.py — 資材の来歴固定と許諾台帳（instruction §1・§2）。

2 つの正本を作る。

- `source_manifest.json` — 何をどこから取り、そのバイト列の sha256 は何か
- `LICENSE_LEDGER.json`  — どの規約文書を正本とし、各確認項目の答えは何か

いずれも **fail-closed**。version 不明・SHA 不明・規約文書不明のいずれかがあれば
`G-MATERIAL` / `G-LICENSE` は BLOCKED を返し、実験は開始できない。

許諾の確認項目は**自動で埋めない**。規約は人が読んで答える対象であり、
未記入は「未確認」であって「問題なし」ではない（§2「推定して進まない」）。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pr_status import BLOCKED, PASS, GateResult

CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# source_manifest.json
# ---------------------------------------------------------------------------
@dataclass
class SourceEntry:
    source: str                       # "ritsu_singing_db" / "pjs_corpus" / ...
    version: Optional[str]            # "Ver2.0.2" / "ver1.1"
    download_origin: Optional[str]    # 配布元 URL（正本ページ）
    archive_sha256: Optional[str]
    size_bytes: Optional[int]
    acquired_at: Optional[str]
    license_document_sha256: Optional[str]
    local_material_path: Optional[str]
    redistribution: bool = False
    extracted_path: Optional[str] = None
    notes: str = ""

    def missing_fields(self) -> List[str]:
        req = ("version", "download_origin", "archive_sha256", "size_bytes",
               "acquired_at", "license_document_sha256", "local_material_path")
        return [k for k in req if not getattr(self, k)]


def build_source_entry(
    *, source: str, version: str, download_origin: str,
    archive_path: Path, license_document_path: Path,
    extracted_path: Optional[Path] = None, notes: str = "",
) -> SourceEntry:
    """実ファイルから来歴を実測する。ファイルが無ければ FileNotFoundError。"""
    archive_path = Path(archive_path)
    license_document_path = Path(license_document_path)
    return SourceEntry(
        source=source, version=version, download_origin=download_origin,
        archive_sha256=sha256_file(archive_path),
        size_bytes=archive_path.stat().st_size,
        acquired_at=utc_now_iso(),
        license_document_sha256=sha256_file(license_document_path),
        local_material_path=str(archive_path.resolve()),
        redistribution=False,
        extracted_path=str(Path(extracted_path).resolve()) if extracted_path else None,
        notes=notes,
    )


@dataclass
class SourceManifest:
    entries: Dict[str, SourceEntry] = field(default_factory=dict)

    def add(self, entry: SourceEntry) -> None:
        self.entries[entry.source] = entry

    def as_dict(self) -> Dict[str, Any]:
        return {"generated_at": utc_now_iso(),
                "entries": {k: asdict(v) for k, v in self.entries.items()}}

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    @staticmethod
    def read(path: Path) -> "SourceManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        m = SourceManifest()
        for k, v in data.get("entries", {}).items():
            m.entries[k] = SourceEntry(**v)
        return m


REQUIRED_SOURCES = ("ritsu_singing_db", "pjs_corpus")


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def gate_material(manifest: SourceManifest, repo_root: Path) -> GateResult:
    """G-MATERIAL（§1）。6 項目すべてを機械確認する。"""
    checks: Dict[str, Any] = {}
    problems: List[str] = []

    for src in REQUIRED_SOURCES:
        entry = manifest.entries.get(src)
        if entry is None:
            checks[src] = "absent"
            problems.append(f"{src}: manifest に項目が無い（未取得）")
            continue
        missing = entry.missing_fields()
        item: Dict[str, Any] = {"version": entry.version,
                                "archive_sha256": entry.archive_sha256,
                                "size_bytes": entry.size_bytes}
        if missing:
            problems.append(f"{src}: 必須項目が空 = {missing}")
        # 実体の存在と sha 一致（pin が実体へ解決できること）
        p = Path(entry.local_material_path) if entry.local_material_path else None
        if p is None or not p.exists():
            problems.append(f"{src}: local_material_path が実在しない")
            item["material_exists"] = False
        else:
            item["material_exists"] = True
            actual = sha256_file(p)
            item["sha_matches"] = actual == entry.archive_sha256
            if not item["sha_matches"]:
                problems.append(f"{src}: archive_sha256 が実体と不一致（{actual}）")
        # raw corpus をリポジトリへ置いていないこと
        if p is not None and _is_inside(p, repo_root):
            problems.append(f"{src}: 資材がリポジトリ内にある（raw corpus 非収載違反）")
            item["outside_repo"] = False
        else:
            item["outside_repo"] = True
        # 展開後の主要ファイル存在
        ex = Path(entry.extracted_path) if entry.extracted_path else None
        item["extracted_present"] = bool(ex and ex.exists() and any(ex.rglob("*")))
        if not item["extracted_present"]:
            problems.append(f"{src}: 展開後ディレクトリが空または未指定")
        checks[src] = item

    if problems:
        return GateResult(
            gate="G-MATERIAL", status=BLOCKED,
            detail="; ".join(problems),
            next_action=(
                "配布元から資材を取得し、`planb_real/pr_run.py acquire` で "
                "source_manifest.json を作成すること（archive・展開先・規約文書の 3 点が要る）"
            ),
            evidence=checks,
        )
    return GateResult(gate="G-MATERIAL", status=PASS,
                      detail="必須 2 資材の取得・SHA 固定・非収載・展開を確認",
                      evidence=checks)


# ---------------------------------------------------------------------------
# LICENSE_LEDGER.json
# ---------------------------------------------------------------------------
#: Ritsu 歌声データベースの確認項目（instruction §2）。値は None = 未確認。
RITSU_CHECKLIST = (
    "individual_internal_research_modification_allowed",
    "raw_db_redistribution_avoided",
    "credit_conditions_for_public_artifacts_recorded",
    "namine_ritsu_and_canon_credit_requirement_recorded",
    "model_publication_conditions_deferred_to_separate_gate",
    "recorded_song_and_midi_usage_restrictions_observed",
)

PJS_CHECKLIST = (
    "license_pinned_at_acquisition_time",
    "share_alike_inheritance_recorded",
    "attribution_requirement_recorded",
)


@dataclass
class LicenseRecord:
    subject: str
    canonical_document: Optional[str]        # 正本規約のパス or URL
    document_sha256: Optional[str]
    retrieved_at: Optional[str]
    license_name: Optional[str]              # "CC BY-SA 4.0" / "専用規約" 等
    checklist: Dict[str, Optional[bool]] = field(default_factory=dict)
    verbatim_excerpts: List[str] = field(default_factory=list)
    answered_by: Optional[str] = None   # 誰が規約を読んで答えたか（未記入は未確認）
    notes: str = ""

    def unanswered(self) -> List[str]:
        return [k for k, v in self.checklist.items() if v is None]


def new_license_record(subject: str, checklist_keys) -> LicenseRecord:
    return LicenseRecord(subject=subject, canonical_document=None, document_sha256=None,
                         retrieved_at=None, license_name=None,
                         checklist={k: None for k in checklist_keys})


@dataclass
class LicenseLedger:
    records: Dict[str, LicenseRecord] = field(default_factory=dict)

    def add(self, rec: LicenseRecord) -> None:
        self.records[rec.subject] = rec

    def as_dict(self) -> Dict[str, Any]:
        return {"generated_at": utc_now_iso(),
                "records": {k: asdict(v) for k, v in self.records.items()}}

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    @staticmethod
    def read(path: Path) -> "LicenseLedger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        led = LicenseLedger()
        for k, v in data.get("records", {}).items():
            led.records[k] = LicenseRecord(**v)
        return led


def blank_ledger() -> LicenseLedger:
    led = LicenseLedger()
    led.add(new_license_record("ritsu_singing_db", RITSU_CHECKLIST))
    led.add(new_license_record("pjs_corpus", PJS_CHECKLIST))
    return led


def gate_license(ledger: LicenseLedger) -> GateResult:
    """G-LICENSE（§2）。未記入 = 未確認として BLOCKED を返す。"""
    problems: List[str] = []
    ev: Dict[str, Any] = {}
    for subject in ("ritsu_singing_db", "pjs_corpus"):
        rec = ledger.records.get(subject)
        if rec is None:
            problems.append(f"{subject}: 台帳に項目が無い")
            ev[subject] = "absent"
            continue
        item = {"canonical_document": rec.canonical_document,
                "document_sha256": rec.document_sha256,
                "license_name": rec.license_name,
                "answered_by": rec.answered_by,
                "unanswered": rec.unanswered()}
        ev[subject] = item
        if not rec.canonical_document or not rec.document_sha256:
            problems.append(f"{subject}: 正本規約文書が未固定")
        if not rec.license_name:
            problems.append(f"{subject}: ライセンス名が未記録")
        if rec.unanswered():
            problems.append(f"{subject}: 未確認項目 {rec.unanswered()}")
        if not rec.answered_by:
            problems.append(f"{subject}: answered_by 未記入（誰が読んだか不明）")
        if not rec.verbatim_excerpts:
            problems.append(f"{subject}: 逐語抜粋が無い（解釈の根拠を残すこと）")
        if any(v is False for v in rec.checklist.values()):
            problems.append(f"{subject}: 満たさない項目がある "
                            f"{[k for k, v in rec.checklist.items() if v is False]}")
    if problems:
        return GateResult(
            gate="G-LICENSE", status=BLOCKED, detail="; ".join(problems),
            next_action=(
                "各配布物に同梱された規約（Ritsu は歌声 DB 同梱の専用規約が本家サイト規約に"
                "優先する）を人が読み、逐語抜粋と sha256 を LICENSE_LEDGER.json へ記入すること"
            ),
            evidence=ev)
    return GateResult(gate="G-LICENSE", status=PASS,
                      detail="Ritsu / PJS ともに正本規約を固定し全項目を確認",
                      evidence=ev)


def repo_root_of(start: Path) -> Path:
    p = Path(start).resolve()
    for cand in [p] + list(p.parents):
        if (cand / ".git").exists():
            return cand
    return Path(os.sep)
