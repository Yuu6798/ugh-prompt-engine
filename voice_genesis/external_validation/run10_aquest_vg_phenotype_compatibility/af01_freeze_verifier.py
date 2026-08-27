"""af01_freeze_verifier.py — AF01 v1.0 凍結検証（DESIGN_RUN10 §29 手順 6/7、§21 R10-G2）。

§29 実行順:

```text
6 verify AF01 v1.0 payload ledger / spec / generator / manifest / canonical C4 Body
7 execute deterministic AF01 payload replay and freeze verification report
```

§21 R10-G2 は「AF01 payload ledger、spec、generator、manifest、canonical Body
のいずれかが不一致なら BLOCKED / reason = AF01_INPUT_DRIFT」と規定する。

本モジュールは 3 段の検証を提供する。

1. `verify_ledger_bytes()` — 台帳ファイル自身の実バイト sha256 が凍結値
   `af01_payload_ledger_sha256` と一致するか。**bundle 実体なしで実行できる**。
2. `verify_bundle()` — 台帳の全エントリが実在し、実バイト sha256 が一致し、
   §7.3 の構造量（75 unit WAV = 25 alias × 3 pitch、9 E0 fixture、6 aggregate
   probe）を満たし、canonical 4 点（spec / generator / manifest / C4 Body）が
   凍結値と一致するか。
3. `verify_deterministic_replay()` — 同梱 generator を **実行前に認証してから**
   再実行し、再生成 payload が凍結台帳と同一かどうか（§29 手順 7）。
   CLI の `--replay` は `--bundle-root` を必須とし、手順 6（bundle 実体照合）を
   通過した場合にのみ手順 7 へ進む。

AF01 は AQUEST 由来資産を一切含まない VoiceGenesis 側の source-free 素体で
あるため（§7.3）、本モジュールが扱うのは公開境界の外側ではない。ただし
bundle 実体（音声）はリポジトリへ置かない — 参照するのは台帳とハッシュだけ。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from run10_schema import (  # noqa: E402  (sibling import 流儀 — run9_schema.py と同型)
    AF01_AGGREGATE_PROBE_COUNT,
    AF01_ALIAS_COUNT,
    AF01_E0_CALIBRATION_CASES,
    AF01_FROZEN_HASHES,
    AF01_PITCHES,
    AF01_UNIT_FILE_COUNT,
    compute_file_sha256,
)

# 本リポジトリへ同梱した凍結台帳（AF01 bundle の PAYLOAD_SHA256SUMS.txt と同一バイト）。
PINNED_LEDGER_PATH = _THIS_DIR / "inputs" / "af01_payload_sha256sums.txt"

# 台帳の対象外だが bundle には存在する登録ファイル（§7.3）。
LEDGER_EXCLUDED_NAMES: Tuple[str, ...] = (
    "PAYLOAD_SHA256SUMS.txt",
    "SHA256SUMS.txt",
    "FREEZE_REGISTRATION.json",
    "AF01_FROZEN_REGISTRATION_v1.0.md",
)

# replay で再生成対象から外す台帳エントリ（閉世界）。generator 自身のソースは
# payload に載っているが定義上 replay の**入力**であり、生成器が自分のソースを
# 出力することは要求できない。これ以外を黙って除外してはならない — 生成器が
# 出力しない payload が他にあると分かった場合は、実測の裏付けとともにここへ
# 明示登録する（黙って除外すると「再生成できていないのに PASS」になる）。
REPLAY_INPUT_ONLY_ENTRIES: Tuple[str, ...] = ("generator_AF01_SF1.py",)

# canonical 4 点（§21 R10-G2 が不一致で AF01_INPUT_DRIFT を出す対象）。
CANONICAL_ENTRIES: Dict[str, str] = {
    "AF01.json": "af01_spec_sha256",
    "generator_AF01_SF1.py": "af01_generator_sha256",
    "founder_manifest.json": "af01_manifest_sha256",
    "AF01_all25_units_C4.wav": "af01_canonical_c4_sha256",
}

VERDICT_PASS = "PASS"
VERDICT_DRIFT = "AF01_INPUT_DRIFT"
VERDICT_UNAVAILABLE = "AF01_BUNDLE_UNAVAILABLE"


class Af01LedgerError(ValueError):
    """台帳の構文・構造の不正。"""


@dataclass(frozen=True)
class Af01VerificationReport:
    """`verify_*` の結果。判定は `verdict` のみが正本。"""

    verdict: str
    checks: Dict[str, str] = field(default_factory=dict)
    mismatches: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    unexpected: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def to_json(self) -> Dict[str, object]:
        return {
            "verdict": self.verdict,
            "checks": dict(self.checks),
            "mismatches": list(self.mismatches),
            "missing": list(self.missing),
            "unexpected": list(self.unexpected),
        }


def parse_payload_ledger(text: str) -> Dict[str, str]:
    """`sha256␠␠path` 形式の台帳を `{path: sha256}` にする。

    sha256sum(1) の出力形式に合わせ、区切りは半角空白 2 個。順序は
    LC_ALL=C ソート（= Python の str 比較）であることも検証する。
    """
    entries: Dict[str, str] = {}
    order: List[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        if "  " not in raw:
            raise Af01LedgerError(f"{lineno} 行目: 'sha256  path' 形式でない: {raw!r}")
        digest, path = raw.split("  ", 1)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Af01LedgerError(f"{lineno} 行目: sha256 が不正: {digest!r}")
        if path in entries:
            raise Af01LedgerError(f"{lineno} 行目: パスが重複している: {path!r}")
        entries[path] = digest
        order.append(path)
    if not entries:
        raise Af01LedgerError("台帳が空である")
    if order != sorted(order):
        raise Af01LedgerError("台帳のパス順序が LC_ALL=C ソートでない")
    return entries


def render_payload_ledger(entries: Mapping[str, str]) -> bytes:
    """`parse_payload_ledger()` の逆写像（決定論的再構成）。"""
    lines = [f"{entries[path]}  {path}" for path in sorted(entries)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_pinned_ledger(path: Path | str | None = None) -> Dict[str, str]:
    """同梱の凍結台帳を読み、自身の sha256 も検証してから返す。"""
    ledger_path = Path(path) if path is not None else PINNED_LEDGER_PATH
    report = verify_ledger_bytes(ledger_path)
    if not report.passed:
        raise Af01LedgerError(f"凍結台帳の sha256 が一致しない: {report.to_json()}")
    return parse_payload_ledger(ledger_path.read_text(encoding="utf-8"))


def verify_ledger_bytes(ledger_path: Path | str | None = None) -> Af01VerificationReport:
    """台帳ファイル自身の実バイト sha256 を凍結値と照合する（bundle 不要）。"""
    path = Path(ledger_path) if ledger_path is not None else PINNED_LEDGER_PATH
    expected = AF01_FROZEN_HASHES["af01_payload_ledger_sha256"]
    if not path.is_file():
        return Af01VerificationReport(
            verdict=VERDICT_UNAVAILABLE,
            checks={"payload_ledger_present": "FAIL"},
            missing=[str(path)],
        )
    actual = compute_file_sha256(path)
    if actual != expected:
        return Af01VerificationReport(
            verdict=VERDICT_DRIFT,
            checks={"payload_ledger_sha256": "FAIL"},
            mismatches=[f"payload_ledger_sha256: expected={expected} actual={actual}"],
        )
    return Af01VerificationReport(
        verdict=VERDICT_PASS,
        checks={"payload_ledger_sha256": "PASS"},
    )


def check_ledger_structure(entries: Mapping[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """§7.3 の構造量を台帳だけで検査する（bundle 不要）。"""
    checks: Dict[str, str] = {}
    problems: List[str] = []

    unit_wavs = [p for p in entries if p.split("/")[0] in AF01_PITCHES and p.endswith(".wav")]
    checks["unit_wav_count"] = "PASS" if len(unit_wavs) == AF01_UNIT_FILE_COUNT else "FAIL"
    if checks["unit_wav_count"] == "FAIL":
        problems.append(f"unit WAV 数: expected={AF01_UNIT_FILE_COUNT} actual={len(unit_wavs)}")

    for pitch in AF01_PITCHES:
        aliases = [p for p in unit_wavs if p.startswith(f"{pitch}/")]
        key = f"alias_count_{pitch}"
        checks[key] = "PASS" if len(aliases) == AF01_ALIAS_COUNT else "FAIL"
        if checks[key] == "FAIL":
            problems.append(f"{pitch} alias 数: expected={AF01_ALIAS_COUNT} actual={len(aliases)}")
        oto = f"{pitch}/oto.ini"
        checks[f"oto_ini_{pitch}"] = "PASS" if oto in entries else "FAIL"
        if oto not in entries:
            problems.append(f"{oto} が台帳に無い")

    e0_wavs = [
        p for p in entries if p.startswith("E0_calibration/") and p.endswith(".wav")
    ]
    checks["e0_fixture_count"] = "PASS" if len(e0_wavs) == AF01_E0_CALIBRATION_CASES else "FAIL"
    if checks["e0_fixture_count"] == "FAIL":
        problems.append(
            f"E0 fixture 数: expected={AF01_E0_CALIBRATION_CASES} actual={len(e0_wavs)}"
        )

    truth = "E0_calibration/E0_calibration_truth.json"
    checks["e0_truth_manifest"] = "PASS" if truth in entries else "FAIL"
    if truth not in entries:
        problems.append(f"{truth} が台帳に無い")

    probes = [p for p in entries if "/" not in p and p.endswith(".wav")]
    checks["aggregate_probe_count"] = (
        "PASS" if len(probes) == AF01_AGGREGATE_PROBE_COUNT else "FAIL"
    )
    if checks["aggregate_probe_count"] == "FAIL":
        problems.append(
            f"aggregate probe 数: expected={AF01_AGGREGATE_PROBE_COUNT} actual={len(probes)}"
        )

    for name, pin_key in CANONICAL_ENTRIES.items():
        expected = AF01_FROZEN_HASHES[pin_key]
        actual = entries.get(name)
        ok = actual == expected
        checks[f"canonical_{pin_key}"] = "PASS" if ok else "FAIL"
        if not ok:
            problems.append(f"{name}: expected={expected} actual={actual}")

    return checks, problems


def verify_bundle(
    bundle_root: Path | str,
    ledger_path: Path | str | None = None,
) -> Af01VerificationReport:
    """bundle 実体を台帳と突き合わせる（§29 手順 6 の完全版）。"""
    root = Path(bundle_root)
    if not root.is_dir():
        return Af01VerificationReport(
            verdict=VERDICT_UNAVAILABLE,
            checks={"bundle_present": "FAIL"},
            missing=[str(root)],
        )

    source = Path(ledger_path) if ledger_path is not None else (root / "PAYLOAD_SHA256SUMS.txt")
    ledger_report = verify_ledger_bytes(source)
    if not ledger_report.passed:
        return ledger_report

    entries = parse_payload_ledger(source.read_text(encoding="utf-8"))
    checks, problems = check_ledger_structure(entries)
    checks["payload_ledger_sha256"] = "PASS"

    missing: List[str] = []
    mismatches: List[str] = []
    escaping: List[str] = []
    resolved_root = root.resolve()
    for relative, expected in sorted(entries.items()):
        target = root / relative
        # bundle の外を指す symlink を辿って hash すると、外部ファイルへの
        # symlink を並べた薄いディレクトリが「完全な AF01 bundle」として
        # 通ってしまう（PR #330 Codex 第 3 巡 P2）。payload バイトが bundle に
        # 含まれていることを、字句上と解決後の両方で要求する。
        if ".." in Path(relative).parts or Path(relative).is_absolute():
            escaping.append(f"{relative}: 台帳のパスが bundle 外を指す字句を含む")
            continue
        if not target.is_file():
            missing.append(relative)
            continue
        resolved = target.resolve()
        if resolved_root not in resolved.parents:
            escaping.append(f"{relative}: 解決後 {resolved} が bundle root の外")
            continue
        actual = compute_file_sha256(target)
        if actual != expected:
            mismatches.append(f"{relative}: expected={expected} actual={actual}")
    checks["payload_files_present"] = "PASS" if not missing else "FAIL"
    checks["payload_files_sha256"] = "PASS" if not mismatches else "FAIL"
    checks["payload_contained_in_bundle"] = "PASS" if not escaping else "FAIL"
    mismatches.extend(escaping)

    known = set(entries) | set(LEDGER_EXCLUDED_NAMES)
    unexpected = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and str(p.relative_to(root)) not in known
    )
    checks["no_unledgered_payload"] = "PASS" if not unexpected else "FAIL"

    failed = problems or missing or mismatches or unexpected or escaping
    return Af01VerificationReport(
        verdict=VERDICT_DRIFT if failed else VERDICT_PASS,
        checks=checks,
        mismatches=mismatches + problems,
        missing=missing,
        unexpected=unexpected,
    )


def verify_deterministic_replay(
    bundle_root: Path | str,
    python_executable: Optional[str] = None,
    timeout_s: int = 1800,
) -> Af01VerificationReport:
    """同梱 generator を再実行し、再生成 payload の台帳が凍結台帳と一致するか（§29 手順 7）。

    generator は standalone（VoiceGenesis / AQUEST を import しない — §7.3）で
    あるため、一時ディレクトリで実行して出力だけを比較する。generator が
    出力先引数を受け付けない場合は cwd 出力とみなす。
    """
    root = Path(bundle_root)
    generator = root / "generator_AF01_SF1.py"
    if not generator.is_file():
        return Af01VerificationReport(
            verdict=VERDICT_UNAVAILABLE,
            checks={"generator_present": "FAIL"},
            missing=[str(generator)],
        )

    ledger_source = root / "PAYLOAD_SHA256SUMS.txt"
    ledger_report = verify_ledger_bytes(ledger_source)
    if not ledger_report.passed:
        return ledger_report
    frozen = parse_payload_ledger(ledger_source.read_text(encoding="utf-8"))

    # generator を **起動する前に** 実バイトを認証する。台帳だけ検証して
    # bundle 側の generator をそのまま実行すると、drift を検出するはずの
    # 検証器が「drift した任意の Python を実行する」経路になる
    # （PR #330 Codex 第 1 巡 P1 / AGENTS.md「hash した bytes と実行された
    # bytes の間に cache の窓が無いか」）。
    expected_generator = frozen.get("generator_AF01_SF1.py")
    if expected_generator is None:
        return Af01VerificationReport(
            verdict=VERDICT_DRIFT,
            checks={"generator_in_ledger": "FAIL"},
            mismatches=["generator_AF01_SF1.py が台帳に無い"],
        )
    if expected_generator != AF01_FROZEN_HASHES["af01_generator_sha256"]:
        return Af01VerificationReport(
            verdict=VERDICT_DRIFT,
            checks={"generator_ledger_matches_frozen_pin": "FAIL"},
            mismatches=[
                f"台帳の generator sha が凍結 pin と不一致:"
                f" ledger={expected_generator}"
                f" pin={AF01_FROZEN_HASHES['af01_generator_sha256']}"
            ],
        )
    generator_sha_before = compute_file_sha256(generator)
    if generator_sha_before != expected_generator:
        return Af01VerificationReport(
            verdict=VERDICT_DRIFT,
            checks={"generator_authenticated_before_run": "FAIL"},
            mismatches=[
                f"generator_AF01_SF1.py: expected={expected_generator}"
                f" actual={generator_sha_before}（実行しない）"
            ],
        )

    with tempfile.TemporaryDirectory(prefix="af01_replay_") as tmp:
        workdir = Path(tmp)
        try:
            completed = subprocess.run(
                [python_executable or sys.executable, str(generator)],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return Af01VerificationReport(
                verdict=VERDICT_UNAVAILABLE,
                checks={"generator_run": "TIMEOUT"},
            )
        if completed.returncode != 0:
            return Af01VerificationReport(
                verdict=VERDICT_UNAVAILABLE,
                checks={"generator_run": "FAIL"},
                mismatches=[completed.stderr.strip()[-2000:]],
            )

        rebuilt: Dict[str, str] = {}
        for produced in sorted(workdir.rglob("*")):
            if not produced.is_file():
                continue
            relative = str(produced.relative_to(workdir))
            if relative in LEDGER_EXCLUDED_NAMES:
                continue
            rebuilt[relative] = compute_file_sha256(produced)

    # 実行後に再 hash して mutation の窓を閉じる（実行中に自身を書き換える
    # generator が「認証済みの bytes を実行した」と主張できないようにする）。
    generator_sha_after = compute_file_sha256(generator)
    if generator_sha_after != generator_sha_before:
        return Af01VerificationReport(
            verdict=VERDICT_DRIFT,
            checks={
                "generator_authenticated_before_run": "PASS",
                "generator_unchanged_after_run": "FAIL",
            },
            mismatches=[
                f"generator_AF01_SF1.py が実行中に変化した:"
                f" before={generator_sha_before} after={generator_sha_after}"
            ],
        )

    required = {k: v for k, v in frozen.items() if k not in REPLAY_INPUT_ONLY_ENTRIES}
    missing = sorted(set(required) - set(rebuilt))
    unexpected = sorted(set(rebuilt) - set(frozen))
    mismatches = [
        f"{path}: frozen={frozen[path]} rebuilt={rebuilt[path]}"
        for path in sorted(set(required) & set(rebuilt))
        if frozen[path] != rebuilt[path]
    ]
    identical = not (missing or unexpected or mismatches)
    return Af01VerificationReport(
        verdict=VERDICT_PASS if identical else VERDICT_DRIFT,
        checks={
            "generator_authenticated_before_run": "PASS",
            "generator_unchanged_after_run": "PASS",
            "generator_run": "PASS",
            "deterministic_payload_replay": "PASS" if identical else "FAIL",
        },
        mismatches=mismatches,
        missing=missing,
        unexpected=unexpected,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AF01 v1.0 凍結検証（DESIGN_RUN10 §29 手順 6/7）")
    parser.add_argument(
        "--bundle-root",
        default=None,
        help="AF01 bundle 展開先。省略時は同梱台帳の自己検証と構造検査のみ実行する。",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="§29 手順 7 の決定論的 payload replay も実行する（--bundle-root 必須）。",
    )
    parser.add_argument("--json-out", default=None, help="検証レポートの書き出し先。")
    args = parser.parse_args(argv)

    if args.replay and args.bundle_root is None:
        # `--replay` を bundle なしで受理して exit 0 を返すと、自動化が
        # §29 手順 7 を「成功」として記録できてしまう（PR #330 Codex 第 2 巡 P2）。
        parser.error("--replay には --bundle-root が必要（bundle なしで手順 7 は成立しない）")

    if args.bundle_root is None:
        report = verify_ledger_bytes()
        if report.passed:
            entries = parse_payload_ledger(PINNED_LEDGER_PATH.read_text(encoding="utf-8"))
            checks, problems = check_ledger_structure(entries)
            checks["payload_ledger_sha256"] = "PASS"
            report = Af01VerificationReport(
                verdict=VERDICT_PASS if not problems else VERDICT_DRIFT,
                checks=checks,
                mismatches=problems,
            )
    elif args.replay:
        # replay は bundle 実体照合の**後**に走らせる（手順 6 → 手順 7 の順序）。
        report = verify_bundle(args.bundle_root)
        if report.passed:
            replay = verify_deterministic_replay(args.bundle_root)
            report = Af01VerificationReport(
                verdict=replay.verdict,
                checks={**report.checks, **replay.checks},
                mismatches=report.mismatches + replay.mismatches,
                missing=report.missing + replay.missing,
                unexpected=report.unexpected + replay.unexpected,
            )
    else:
        report = verify_bundle(args.bundle_root)

    payload = json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
