"""af_gates.py — G0〜G14 と Overall Verdict（設計書 §19 / §20）。

**判定を増やさない。** verdict は §20 の 4 つ（PASS / NOT_ESTABLISHED / BLOCKED /
FAILED）以外を作らない。各 Gate は「証拠 dict を受け取って PASS/FAIL を返す」
だけで、音響処理も測定もしない。

Gate -> Overall のマッピング（§20）:

```text
G0  SOURCE_FREE          違反 -> FAILED
G2  DETERMINISTIC_COMP   違反 -> FAILED
G14 PROVENANCE           違反 -> FAILED（partial publication / provenance 虚偽）
G1  SPEC_VALID           不成立 -> BLOCKED（判定不能）
G3  UTAU_BODY            不成立 -> BLOCKED（compiler / adapter defect）
G4  VOICEGENESIS_INGEST  不成立 -> BLOCKED（dependency 欠落・adapter defect）
G5  METER_CONTROL        不成立 -> BLOCKED（METER_NOT_CALIBRATED）
G6..G13 trait gates      不成立 -> NOT_ESTABLISHED（reason code つき）
```
"""
from __future__ import annotations

import builtins
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

#: §27 canonical generator read whitelist（拡張子・パス断片）。
ALLOWED_READ_SUFFIXES: Tuple[str, ...] = (".json", ".py", ".pyc", ".txt", ".yaml", ".yml",
                                          ".so", ".pyd", ".dll", ".dylib", ".pth", ".cfg",
                                          ".ini", ".md", ".h", ".c", ".pxd", ".typed")

#: §27 禁止 read。生成 staging の外の音声・学習済みモデルは一切読まない。
FORBIDDEN_READ_SUFFIXES: Tuple[str, ...] = (".wav", ".mp3", ".flac", ".ogg", ".m4a",
                                            ".ckpt", ".pt", ".pth.tar", ".onnx", ".npz",
                                            ".h5", ".bin", ".safetensors", ".ust", ".ust2")

#: 既存 voicebank / 人間音源が置かれうるディレクトリ名（読んだら即 FAILED）。
FORBIDDEN_PATH_FRAGMENTS: Tuple[str, ...] = (
    "s1_dataprep/data", "recording_kit", "ritsu", "pjs", "amitaro", "vocadito",
    "musdb", "voicebank", "checkpoints", "pretrained",
)

TRAIT_GATE_REASONS: Dict[str, str] = {
    "G6": "STANDARD_IDENTITY_NOT_ESTABLISHED",
    "G7": "FOUNDER_SOURCE_NOT_ESTABLISHED",
    "G8": "FOUNDER_IDENTITY_NOT_ESTABLISHED",
    "G9": "F0_NOT_ESTABLISHED",
    "G10": "DURATION_NOT_ESTABLISHED",
    "G11": "ENERGY_NOT_ESTABLISHED",
    "G12": "RELEASE_NOT_ESTABLISHED",
    "G13": "AFTERGLOW_NOT_ESTABLISHED",
}

FAILED_GATES: Tuple[str, ...] = ("G0", "G2", "G14")
BLOCKED_GATES: Tuple[str, ...] = ("G1", "G3", "G4", "G5")


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    name: str
    verdict: str            # "PASS" / "FAIL" / "SKIPPED"
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate_id, "name": self.name, "verdict": self.verdict,
                "detail": self.detail}


# ---------------------------------------------------------------------------
# G0 SOURCE_FREE — read-set tripwire（§19「宣言だけでなく read-set tripwire で検証」）
# ---------------------------------------------------------------------------
class SourceFreeAudit:
    """コンパイル中に開かれた**全ファイルパス**と network 使用を記録する。"""

    def __init__(self, allowed_roots: Sequence[Path], staging_roots: Sequence[Path]) -> None:
        self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        self.staging_roots = [Path(p).resolve() for p in staging_roots]
        self.reads: List[str] = []
        self.network_attempts: List[str] = []

    def record(self, path: Any) -> None:
        try:
            p = Path(path)
        except TypeError:
            return
        if isinstance(path, int):
            return
        self.reads.append(str(p))

    def _under(self, p: Path, roots: Sequence[Path]) -> bool:
        try:
            rp = p.resolve()
        except OSError:  # pragma: no cover - 解決できないパスは外扱い
            return False
        return any(rp == r or r in rp.parents for r in roots)

    def violations(self) -> List[str]:
        """禁止 read を列挙する（生成 staging 配下の read は許可）。"""
        bad: List[str] = []
        for raw in self.reads:
            p = Path(raw)
            if self._under(p, self.staging_roots):
                continue
            low = p.as_posix().lower()
            if any(frag in low for frag in FORBIDDEN_PATH_FRAGMENTS):
                bad.append(raw)
                continue
            if p.suffix.lower() in FORBIDDEN_READ_SUFFIXES:
                bad.append(raw)
        return sorted(set(bad))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_reads": len(self.reads),
            "n_unique_reads": len(set(self.reads)),
            "violations": self.violations(),
            "network_attempts": self.network_attempts,
        }


@contextmanager
def source_free_tripwire(audit: SourceFreeAudit) -> Iterator[SourceFreeAudit]:
    """`open` / `Path.read_*` / `socket` を差し替えて read-set を採取する。

    network は**遮断**する（§19 G0 `network access = 0`）。宣言を信用せず、
    実際に開けないようにしてから走らせる。
    """
    real_open = builtins.open
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    real_socket = socket.socket

    def guarded_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if "r" in mode or "+" in mode:
            audit.record(file)
        return real_open(file, mode, *args, **kwargs)

    def guarded_read_bytes(self):  # type: ignore[no-untyped-def]
        audit.record(self)
        return real_read_bytes(self)

    def guarded_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        audit.record(self)
        return real_read_text(self, *args, **kwargs)

    class BlockedSocket(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            audit.network_attempts.append("socket.socket")
            raise RuntimeError("network access is forbidden during AF0 compilation (§19 G0)")

    builtins.open = guarded_open  # type: ignore[assignment]
    Path.read_bytes = guarded_read_bytes  # type: ignore[assignment]
    Path.read_text = guarded_read_text  # type: ignore[assignment]
    socket.socket = BlockedSocket  # type: ignore[assignment]
    try:
        yield audit
    finally:
        builtins.open = real_open  # type: ignore[assignment]
        Path.read_bytes = real_read_bytes  # type: ignore[assignment]
        Path.read_text = real_read_text  # type: ignore[assignment]
        socket.socket = real_socket  # type: ignore[assignment]


def gate_source_free(origin: Mapping[str, Any], audit: Mapping[str, Any]) -> GateResult:
    declared_ok = (origin.get("human_audio_used") is False
                   and origin.get("speaker_specific_parameters_used") is False
                   and origin.get("pretrained_voice_model_used") is False
                   and origin.get("external_voicebank_used") is False)
    violations = list(audit.get("violations", []))
    net = list(audit.get("network_attempts", []))
    detail = {"declared_source_free": declared_ok, "read_violations": violations,
              "network_attempts": net, "n_reads": audit.get("n_reads")}
    ok = declared_ok and not violations and not net
    return GateResult("G0", "SOURCE_FREE", "PASS" if ok else "FAIL", detail)


# ---------------------------------------------------------------------------
# G1..G5 構造・決定論・取り込み・計器
# ---------------------------------------------------------------------------
def gate_spec_valid(errors: Sequence[str]) -> GateResult:
    return GateResult("G1", "SPEC_VALID", "PASS" if not errors else "FAIL",
                      {"errors": list(errors)})


def gate_determinism(same_process: Mapping[str, Any],
                     cross_process: Mapping[str, Any]) -> GateResult:
    ok = bool(same_process.get("match")) and bool(cross_process.get("match"))
    return GateResult("G2", "DETERMINISTIC_COMPILATION", "PASS" if ok else "FAIL",
                      {"same_process": dict(same_process), "cross_process": dict(cross_process)})


def gate_utau_body(structure: Mapping[str, Any], sums: Mapping[str, Any]) -> GateResult:
    ok = structure.get("verdict") == "PASS" and sums.get("verdict") == "PASS"
    return GateResult("G3", "UTAU_BODY", "PASS" if ok else "FAIL",
                      {"structure": dict(structure), "sha256sums": dict(sums)})


def gate_ingestion(health: Mapping[str, Any], join: Mapping[str, Any],
                   reexpression: Mapping[str, Any], dataset: Mapping[str, Any]) -> GateResult:
    ok = (health.get("verdict") == "PASS" and join.get("verdict") == "PASS"
          and bool(reexpression.get("all_finite")) and dataset.get("verdict") == "PASS")
    return GateResult("G4", "VOICEGENESIS_INGESTION", "PASS" if ok else "FAIL",
                      {"donor_bank": dict(health), "join_smoke": dict(join),
                       "reexpression_finite": bool(reexpression.get("all_finite")),
                       "dataset": dict(dataset)})


def gate_meter_control(controls: Mapping[str, Any]) -> GateResult:
    failed = [c["family"] for c in controls.get("families", []) if c["verdict"] != "PASS"]
    ok = bool(controls.get("families")) and not failed
    detail = {"families": controls.get("families", []), "failed_families": failed}
    if not ok:
        detail["reason_code"] = "METER_NOT_CALIBRATED"
    return GateResult("G5", "METER_CONTROL", "PASS" if ok else "FAIL", detail)


# ---------------------------------------------------------------------------
# G6..G13 trait gates（Body と re-expression の両方が通って初めて PASS）
# ---------------------------------------------------------------------------
def _trait_gate(gate_id: str, name: str, body: Mapping[str, Any],
                reexp: Optional[Mapping[str, Any]], keys: Sequence[str]) -> GateResult:
    detail: Dict[str, Any] = {"body": {}, "reexpression": {}}
    ok = True
    for key in keys:
        b = body.get(key, {})
        detail["body"][key] = b.get("verdict", "MISSING")
        ok = ok and b.get("verdict") == "PASS"
    if reexp is None:
        detail["reexpression"] = "SKIPPED"
        ok = False
    else:
        for key in keys:
            r = reexp.get(key, {})
            detail["reexpression"][key] = r.get("verdict", "MISSING")
            ok = ok and r.get("verdict") == "PASS"
    if not ok:
        detail["reason_code"] = TRAIT_GATE_REASONS[gate_id]
    return GateResult(gate_id, name, "PASS" if ok else "FAIL", detail)


def trait_gates(body: Mapping[str, Any],
                reexp: Optional[Mapping[str, Any]]) -> List[GateResult]:
    """G6〜G13。§18.1 と §18.2 の両方を満たしたときだけ PASS。"""
    reexp_identity_keys = ("spectral_identity",)
    return [
        _trait_gate("G6", "STANDARD_IDENTITY", body, reexp, ("identity",))
        if reexp is None else
        GateResult("G6", "STANDARD_IDENTITY",
                   "PASS" if (body.get("identity", {}).get("verdict") == "PASS"
                              and all(reexp.get(k, {}).get("verdict") == "PASS"
                                      for k in reexp_identity_keys)) else "FAIL",
                   {"body": {"identity": body.get("identity", {}).get("verdict", "MISSING")},
                    "reexpression": {k: reexp.get(k, {}).get("verdict", "MISSING")
                                     for k in reexp_identity_keys},
                    **({} if (body.get("identity", {}).get("verdict") == "PASS"
                              and all(reexp.get(k, {}).get("verdict") == "PASS"
                                      for k in reexp_identity_keys))
                       else {"reason_code": TRAIT_GATE_REASONS["G6"]})}),
        _trait_gate("G7", "FOUNDER_SOURCE_HL", body, reexp, ("hl_alpha",)),
        _trait_gate("G8", "FOUNDER_IDENTITY_AR", body, reexp, ("ar_alpha", "ar_beta")),
        _trait_gate("G9", "F0", body, reexp, ("f0_core", "terminal_f0")),
        _trait_gate("G10", "DURATION", body, reexp, ("duration_onset", "duration_share")),
        _trait_gate("G11", "ENERGY", body, reexp, ("energy_sustain",)),
        _trait_gate("G12", "RELEASE", body, reexp, ("release",)),
        _trait_gate("G13", "FOUNDER_EXPRESSION_AG", body, reexp, ("afterglow",)),
    ]


def gate_provenance(pins: Mapping[str, Any], publication: Mapping[str, Any]) -> GateResult:
    """G14: pins / hash / atomic publish / rollback。"""
    required_pins = ("spec_sha256", "criteria_sha256", "controls_sha256", "probes_sha256",
                     "code_closure_sha256", "body_identity_digest")
    missing = [k for k in required_pins if not pins.get(k)]
    ok = (not missing and publication.get("published")
          and publication.get("bundle_verified") is True
          and publication.get("partial_artifacts") == [])
    detail = {"missing_pins": missing, "publication": dict(publication)}
    if not ok:
        detail["reason_code"] = "PROVENANCE_OR_PUBLICATION_FAILED"
    return GateResult("G14", "PROVENANCE_AND_PUBLICATION", "PASS" if ok else "FAIL", detail)


# ---------------------------------------------------------------------------
# §20 Overall Verdict
# ---------------------------------------------------------------------------
def overall_verdict(gates: Sequence[GateResult]) -> Dict[str, Any]:
    by_id = {g.gate_id: g for g in gates}
    failed = [g.gate_id for g in gates if g.verdict != "PASS"]
    reasons: List[str] = []

    for gid in FAILED_GATES:
        g = by_id.get(gid)
        if g is not None and g.verdict != "PASS":
            reasons.append(g.detail.get("reason_code", f"{g.name}_VIOLATION"))
            return {"verdict": "FAILED", "failed_gates": failed, "reason_codes": reasons}

    for gid in BLOCKED_GATES:
        g = by_id.get(gid)
        if g is not None and g.verdict != "PASS":
            reasons.append(g.detail.get("reason_code", f"{g.name}_NOT_EVALUABLE"))
            return {"verdict": "BLOCKED", "failed_gates": failed, "reason_codes": reasons}

    for gid, reason in TRAIT_GATE_REASONS.items():
        g = by_id.get(gid)
        if g is not None and g.verdict != "PASS":
            reasons.append(g.detail.get("reason_code", reason))
    if reasons:
        return {"verdict": "NOT_ESTABLISHED", "failed_gates": failed, "reason_codes": reasons}

    if failed:  # pragma: no cover - 上の分類で拾い切れない Gate は保守的に BLOCKED
        return {"verdict": "BLOCKED", "failed_gates": failed,
                "reason_codes": ["UNCLASSIFIED_GATE_FAILURE"]}
    return {"verdict": "PASS", "failed_gates": [], "reason_codes": []}


def code_closure_digest(package_dir: str | Path) -> Tuple[str, List[Tuple[str, str]]]:
    """本パッケージ + 利用する adapter モジュールの import 閉包ハッシュ（§25 code_closure）。"""
    from af_spec import aggregate_digest, sha256_file

    pkg = Path(package_dir)
    adapter = pkg.parent / "adapter"
    rows: List[Tuple[str, str]] = []
    for root, label in ((pkg, "artificial_founder"), (adapter, "adapter")):
        for p in sorted(root.glob("*.py")):
            rows.append((f"{label}/{p.name}", sha256_file(p)))
    return aggregate_digest(rows), rows
