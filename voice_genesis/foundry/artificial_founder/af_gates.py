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

import ast
import builtins
import site
import socket
import sys
import sysconfig
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

#: §17 の metric family 完全集合。controls ファイルが部分集合でも G5 が PASS に
#: なると、難しい family を落とすだけで overall PASS を作れてしまう。
REQUIRED_CONTROL_FAMILIES: Tuple[str, ...] = (
    "hl_even_odd_ratio", "ar_alpha_center", "beta_alpha_ratio", "terminal_f0_delta",
    "duration_r_share", "energy_sustain", "release", "afterglow",
)


def repo_root() -> Path:
    """このパッケージを含むリポジトリのルート。"""
    return Path(__file__).resolve().parents[3]


def repo_relative(path: str | Path) -> str:
    """provenance へ書くパスを **checkout 非依存** の表記へ正規化する。

    絶対パスをそのまま記録すると、同一入力・同一コードの run でも checkout の
    場所が違うだけで `source_free_attestation.json` / `p0_results.json` の
    バイト列と checksum が変わり、provenance 記録を機械的に再現できない。
    """
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:  # pragma: no cover
        return p.as_posix()
    root = repo_root()
    if resolved == root or root in resolved.parents:
        return resolved.relative_to(root).as_posix() or "."
    # リポジトリ外（Python ランタイム・一時ディレクトリ）は最後の 2 階層だけ残す。
    parts = resolved.parts
    return "<external>/" + "/".join(parts[-2:]) if len(parts) >= 2 else "<external>"


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
def runtime_read_roots() -> List[Path]:
    """Python / インストール済みパッケージのランタイム根（§27 の read whitelist）。"""
    roots: List[Path] = []
    for key in ("stdlib", "platstdlib", "purelib", "platlib", "data"):
        try:
            roots.append(Path(sysconfig.get_paths()[key]))
        except KeyError:  # pragma: no cover - プラットフォーム差
            continue
    roots += [Path(sys.prefix), Path(sys.base_prefix)]
    for entry in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
        roots.append(Path(entry))
    out: List[Path] = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:  # pragma: no cover
            continue
    return sorted(set(out), key=lambda p: p.as_posix())


class SourceFreeAudit:
    """コンパイル中に開かれた**全ファイルパス**と network 使用を記録する。

    判定は **allowlist を主、denylist を従** とする（fail-closed）。denylist だけに
    頼ると、見慣れない拡張子の外部音源（`/tmp/speaker.npy` など）が素通りして
    G0 PASS になりうる。許可されるのは次のいずれかに限る。

    ```text
    1. 生成 staging 配下（自分が今作った成果物の読み直し）
    2. pinned inputs（AF0.json / criteria / controls / probes）そのもの
    3. allowed_roots 配下 かつ 拡張子が ALLOWED_READ_SUFFIXES（ソースコード）
    4. Python / パッケージのランタイム根 かつ 拡張子が ALLOWED_READ_SUFFIXES
    ```

    どれにも当たらない read は理由つきで violation にする。
    """

    def __init__(self, allowed_roots: Sequence[Path], staging_roots: Sequence[Path],
                 pinned_inputs: Sequence[Path] = (),
                 runtime_roots: Optional[Sequence[Path]] = None) -> None:
        self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        self.staging_roots = [Path(p).resolve() for p in staging_roots]
        self.pinned_inputs = {Path(p).resolve() for p in pinned_inputs}
        self.runtime_roots = ([Path(p).resolve() for p in runtime_roots]
                              if runtime_roots is not None else runtime_read_roots())
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

    def classify(self, raw: str) -> Optional[str]:
        """許可なら `None`、禁止なら理由文字列を返す。"""
        p = Path(raw)
        low = p.as_posix().lower()
        # denylist は allowlist より先に効かせる（staging 配下でも既存音源の
        # 持ち込みは許さない）。
        for frag in FORBIDDEN_PATH_FRAGMENTS:
            if frag in low:
                return f"forbidden path fragment {frag!r}"
        if self._under(p, self.staging_roots):
            return None
        if p.suffix.lower() in FORBIDDEN_READ_SUFFIXES:
            return f"forbidden suffix {p.suffix!r}"
        try:
            resolved = p.resolve()
        except OSError:  # pragma: no cover - 解決できないパス
            return "unresolvable path"
        if resolved in self.pinned_inputs:
            return None
        in_allowed = self._under(p, self.allowed_roots)
        in_runtime = self._under(p, self.runtime_roots)
        if not (in_allowed or in_runtime):
            return "outside the declared read allowlist"
        if p.suffix.lower() not in ALLOWED_READ_SUFFIXES:
            return f"suffix {p.suffix!r} is not in the declared read allowlist"
        return None

    def violations(self) -> List[Dict[str, str]]:
        """禁止 read を理由つきで列挙する。"""
        bad: Dict[str, str] = {}
        for raw in self.reads:
            reason = self.classify(raw)
            if reason is not None:
                bad.setdefault(raw, reason)
        return [{"path": k, "reason": bad[k]} for k in sorted(bad)]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n_reads": len(self.reads),
            "n_unique_reads": len(set(self.reads)),
            "allowed_roots": [repo_relative(p) for p in self.allowed_roots],
            "staging_roots": [repo_relative(p) for p in self.staging_roots],
            "n_runtime_roots": len(self.runtime_roots),
            "pinned_inputs": sorted(repo_relative(p) for p in self.pinned_inputs),
            "violations": [{"path": repo_relative(v["path"]), "reason": v["reason"]}
                           for v in self.violations()],
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
    """§17: **8 family すべて**が規定方向を弁別して初めて PASS。

    供給された行の中だけで失敗を数えると、難しい family を controls ファイルから
    落とすだけで G5 PASS を作れる（部分集合の偽 PASS）。集合そのものを検査する。
    """
    families = list(controls.get("families", []))
    names = [c.get("family") for c in families]
    missing = [f for f in REQUIRED_CONTROL_FAMILIES if f not in set(names)]
    unknown = sorted({n for n in names if n not in set(REQUIRED_CONTROL_FAMILIES)})
    duplicated = sorted({n for n in names if names.count(n) > 1})
    failed = [c["family"] for c in families if c.get("verdict") != "PASS"]
    ok = not (missing or unknown or duplicated or failed)
    detail: Dict[str, Any] = {
        "families": families, "failed_families": failed,
        "required_families": list(REQUIRED_CONTROL_FAMILIES),
        "missing_families": missing, "unknown_families": unknown,
        "duplicated_families": duplicated,
    }
    if not ok:
        detail["reason_code"] = "METER_NOT_CALIBRATED"
    return GateResult("G5", "METER_CONTROL", "PASS" if ok else "FAIL", detail)


# ---------------------------------------------------------------------------
# G6..G13 trait gates（Body と re-expression の両方が通って初めて PASS）
# ---------------------------------------------------------------------------
def _trait_gate(gate_id: str, name: str, body: Mapping[str, Any],
                reexp: Optional[Mapping[str, Any]], keys: Sequence[str],
                body_only_keys: Sequence[str] = ()) -> GateResult:
    """1 形質 Gate。`keys` は §18.1 と §18.2 の両方に行がある比較。

    `body_only_keys` は **Body にしか行が無い**比較（`energy_attack` /
    `terminal_zero`）。§18.2 に対応行が無いからといって Gate から外すと、
    §18.1 の必須契約が落ちても G11 / G13 が PASS し、全 Gate 通過の overall PASS
    まで通ってしまう。Body 側だけ必須にして Gate へ配線する。
    """
    detail: Dict[str, Any] = {"body": {}, "reexpression": {}}
    ok = True
    for key in list(keys) + list(body_only_keys):
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
        _trait_gate("G11", "ENERGY", body, reexp, ("energy_sustain",),
                    body_only_keys=("energy_attack",)),
        _trait_gate("G12", "RELEASE", body, reexp, ("release",)),
        _trait_gate("G13", "FOUNDER_EXPRESSION_AG", body, reexp, ("afterglow",),
                    body_only_keys=("terminal_zero",)),
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
#: §20 PASS は「G0–G14 all PASS」。この集合に**過不足があれば判定しない**。
ALL_GATE_IDS: Tuple[str, ...] = tuple(f"G{i}" for i in range(15))


def overall_verdict(gates: Sequence[GateResult]) -> Dict[str, Any]:
    """§20 の Overall Verdict。

    **まず Gate 集合の完全性を検査する。** 以前は欠けた Gate を各ループが黙って
    読み飛ばし、`overall_verdict([])` すら PASS を返していた（部分集合でも
    「全 Gate 通過」に見える偽の成功）。§20 の PASS は G0–G14 all PASS の意味
    なので、ID の過不足・重複はそれ自体が判定不能（BLOCKED）である。

    `SKIPPED` は PASS ではない。停止（例: G5 不成立で Phase 2 以降を実行しない）
    のときに未評価の Gate が SKIPPED で並ぶが、その場合は先に落ちた Gate が
    verdict を決める。
    """
    ids = [g.gate_id for g in gates]
    missing = [gid for gid in ALL_GATE_IDS if gid not in set(ids)]
    unknown = sorted(set(ids) - set(ALL_GATE_IDS))
    duplicated = sorted({gid for gid in ids if ids.count(gid) > 1})
    if missing or unknown or duplicated:
        return {"verdict": "BLOCKED", "failed_gates": sorted(set(ids)) or [],
                "reason_codes": ["INCOMPLETE_GATE_SET"],
                "gate_set_error": {"missing": missing, "unknown": unknown,
                                   "duplicated": duplicated}}

    by_id = {g.gate_id: g for g in gates}
    failed = [g.gate_id for g in gates if g.verdict != "PASS"]
    reasons: List[str] = []

    for gid in FAILED_GATES:
        g = by_id[gid]
        if g.verdict == "FAIL":
            reasons.append(g.detail.get("reason_code", f"{g.name}_VIOLATION"))
            return {"verdict": "FAILED", "failed_gates": failed, "reason_codes": reasons}

    # 実際に落ちた Gate を、単に未評価（SKIPPED）の Gate より先に報告する。
    # 停止した run では後続 Gate が軒並み SKIPPED になるため、順番に見ると
    # 「NOT_EVALUATED」だけが理由として出て、真の原因（例 METER_NOT_CALIBRATED）
    # が埋もれる。
    blocked_failed = [gid for gid in BLOCKED_GATES if by_id[gid].verdict == "FAIL"]
    blocked_skipped = [gid for gid in BLOCKED_GATES if by_id[gid].verdict == "SKIPPED"]
    if blocked_failed or blocked_skipped:
        for gid in blocked_failed + blocked_skipped:
            g = by_id[gid]
            reasons.append(g.detail.get("reason_code", f"{g.name}_NOT_EVALUABLE"))
        return {"verdict": "BLOCKED", "failed_gates": failed, "reason_codes": reasons}

    for gid, reason in TRAIT_GATE_REASONS.items():
        g = by_id[gid]
        if g.verdict != "PASS":
            reasons.append(g.detail.get("reason_code", reason))
    if reasons:
        return {"verdict": "NOT_ESTABLISHED", "failed_gates": failed, "reason_codes": reasons}

    if failed:
        # FAILED 系 Gate が SKIPPED（公開差し止め等）で残った場合。保守的に BLOCKED。
        return {"verdict": "BLOCKED", "failed_gates": failed,
                "reason_codes": ["UNCLASSIFIED_GATE_NOT_PASSED"]}
    return {"verdict": "PASS", "failed_gates": [], "reason_codes": []}


def _module_search_roots(package_dir: Path) -> List[Tuple[str, Path]]:
    """flat module 名を解決する探索根（実行時の `sys.path` 挿入と同じ並び）。

    `adapter/donor_bank_utau.py` は `voice_genesis/singer` を `sys.path` へ入れて
    `phoneme_jp` を import する。取り込み経路が実際に実行するのだから、閉包は
    そこまで辿らないと pin が実体を代表しない。
    """
    foundry = package_dir.parent
    voice_genesis = foundry.parent
    return [
        ("artificial_founder", package_dir),
        ("adapter", foundry / "adapter"),
        ("singer", voice_genesis / "singer"),
        ("planb", foundry / "planb"),
        ("s1_dataprep", foundry / "s1_dataprep"),
    ]


def _imported_module_names(path: Path) -> List[str]:
    """1 ファイルが import する **トップレベル名** を静的に収集する。

    実行順に依存しないよう `sys.modules` ではなく `ast` で静的に辿る（同じ
    ソースツリーからは常に同じ閉包が出る = §27 決定論）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):  # pragma: no cover - 読めないファイルは辿らない
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module.split(".")[0])
    return names


def code_closure_digest(package_dir: str | Path) -> Tuple[str, List[Tuple[str, str]]]:
    """import 閉包（推移的）のハッシュ（§25 code_closure）。

    本パッケージの全 `*.py` を起点に、リポジトリ内で解決できる import を再帰的に
    辿る。`adapter/*.py` を丸ごと入れるだけでは、そこから実行される
    `singer/phoneme_jp.py`（AF0 の alias 正規化を実際に行う）が pin から漏れ、
    それを変えても `code_closure_sha256` が動かないまま G14 が PASS になる。
    """
    from af_spec import aggregate_digest, sha256_file

    pkg = Path(package_dir).resolve()
    roots = [(label, root.resolve()) for label, root in _module_search_roots(pkg)]
    seen: Dict[str, Path] = {}
    queue: List[Path] = sorted(pkg.glob("*.py"))

    def _label_for(path: Path) -> Optional[str]:
        for label, root in roots:
            if path.parent == root:
                return f"{label}/{path.name}"
        return None

    while queue:
        path = queue.pop(0)
        label = _label_for(path)
        if label is None or label in seen:
            continue
        seen[label] = path
        for name in _imported_module_names(path):
            for _, root in roots:
                candidate = root / f"{name}.py"
                if candidate.exists():
                    queue.append(candidate)
                    break

    rows = [(label, sha256_file(seen[label])) for label in sorted(seen)]
    return aggregate_digest(rows), rows
