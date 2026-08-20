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

#: 展開後に実際に消費する拡張子（この集合のバイトを pin し、gate で照合する）
CONSUMED_EXT = (".lab", ".wav", ".flac")


def aggregate_extracted_sha256(root: Path) -> tuple:
    """展開ディレクトリのうち **実際に消費するファイル**の集約ハッシュ。

    archive の sha256 だけでは、展開後に 1 ファイル差し替えられても検出できない
    （レビュー指摘 r3825358558）。相対パスと各ファイルの sha256 を並べて
    集約する（順序は相対パスのソート順で決定論）。
    """
    root = Path(root)
    h = hashlib.sha256()
    n = 0
    for f in sorted(p for p in root.rglob("*")
                    if p.is_file() and p.suffix.lower() in CONSUMED_EXT):
        rel = f.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(sha256_file(f).encode("ascii"))
        h.update(b"\n")
        n += 1
    return h.hexdigest(), n


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
    extracted_sha256: Optional[str] = None   # 実際に消費する展開物の集約ハッシュ
    extracted_file_count: Optional[int] = None
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
    ex_sha, ex_n = (aggregate_extracted_sha256(Path(extracted_path))
                    if extracted_path else (None, None))
    return SourceEntry(
        source=source, version=version, download_origin=download_origin,
        archive_sha256=sha256_file(archive_path),
        size_bytes=archive_path.stat().st_size,
        acquired_at=utc_now_iso(),
        license_document_sha256=sha256_file(license_document_path),
        local_material_path=str(archive_path.resolve()),
        redistribution=False,
        extracted_path=str(Path(extracted_path).resolve()) if extracted_path else None,
        extracted_sha256=ex_sha, extracted_file_count=ex_n,
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
        # 展開後の主要ファイル存在 + repo 外 + 消費バイトの一致
        ex = Path(entry.extracted_path) if entry.extracted_path else None
        item["extracted_present"] = bool(ex and ex.exists() and any(ex.rglob("*")))
        if not item["extracted_present"]:
            problems.append(f"{src}: 展開後ディレクトリが空または未指定")
        elif _is_inside(ex, repo_root):
            problems.append(f"{src}: **展開後コーパスがリポジトリ内にある**（raw corpus 非収載違反）")
            item["extracted_outside_repo"] = False
        else:
            item["extracted_outside_repo"] = True
            if not entry.extracted_sha256:
                problems.append(f"{src}: 展開物の集約ハッシュが未記録")
            else:
                cur_sha, cur_n = aggregate_extracted_sha256(ex)
                item["extracted_sha_matches"] = cur_sha == entry.extracted_sha256
                item["extracted_file_count"] = cur_n
                if not item["extracted_sha_matches"]:
                    problems.append(
                        f"{src}: **展開物が取得時から変化している**"
                        f"（記録 {entry.extracted_sha256[:12]}… / 現在 {cur_sha[:12]}…、"
                        f"{cur_n} ファイル）")
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


REQUIRED_CHECKLIST = {"ritsu_singing_db": RITSU_CHECKLIST, "pjs_corpus": PJS_CHECKLIST}


def gate_license(ledger: LicenseLedger,
                 manifest: Optional["SourceManifest"] = None) -> GateResult:
    """G-LICENSE（§2）。未記入 = 未確認として BLOCKED を返す。

    2 つの強化（レビュー指摘）:

    - 正本規約は**実体を開いて sha256 を取り直し**、記録値および acquire 時の
      `license_document_sha256` と一致することを確かめる（r3825325979）
    - checklist は**必須キー集合と突き合わせ**、値が厳密に `True` であることを
      要求する。キー欠落や `"yes"` のような文字列を通さない（r3825592731）
    """
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
        required = set(REQUIRED_CHECKLIST[subject])
        got = set(rec.checklist)
        missing_keys = sorted(required - got)
        extra_keys = sorted(got - required)
        bad_values = sorted(k for k, v in rec.checklist.items() if v is not True and v is not None)
        item["missing_keys"] = missing_keys
        item["extra_keys"] = extra_keys
        item["non_true_values"] = bad_values
        if missing_keys:
            problems.append(f"{subject}: 確認項目が欠落 {missing_keys}")
        if extra_keys:
            problems.append(f"{subject}: 未定義の確認項目 {extra_keys}")
        if bad_values:
            problems.append(f"{subject}: 値が True でない項目 {bad_values}")
        if rec.unanswered():
            problems.append(f"{subject}: 未確認項目 {rec.unanswered()}")
        # 正本規約の実体を開いて照合する
        if rec.canonical_document:
            doc = Path(rec.canonical_document)
            if not doc.exists():
                problems.append(f"{subject}: 正本規約の実体が存在しない（{doc}）")
                item["document_exists"] = False
            else:
                actual = sha256_file(doc)
                item["document_exists"] = True
                item["document_sha_matches"] = actual == rec.document_sha256
                if actual != rec.document_sha256:
                    problems.append(
                        f"{subject}: 規約文書の sha256 が実体と不一致（実体 {actual[:12]}…）")
                if manifest is not None:
                    e = manifest.entries.get(subject)
                    if e and e.license_document_sha256 and e.license_document_sha256 != actual:
                        problems.append(
                            f"{subject}: acquire 時の規約 sha256 と現在の実体が不一致")
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
