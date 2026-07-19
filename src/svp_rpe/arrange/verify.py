"""`svprpe verify` の中核ロジック: PerformancePackage 内部整合の全数検査（V1–V4）。

PR #187 review round 16 が `observe`（`cli/observe_cmd.py`）の検証境界を
「provenance 同定まで」と線引きし、`channel_artifacts` の sha256/artifact 等
package 内部フィールドの内容整合を「将来の verify コマンドの責務」と明文化した
（`cli/observe_cmd.py` docstring）。本モジュールはその受け皿。

スコープ: 単一 PerformancePackage（+ 同居 CompilationReport + 参照
IdentityManifest）の内部整合・ファイルシステム対面整合を読み取り専用で全数検査
する。CLI（`cli/verify_cmd.py`）は本モジュールが返す `VerifyReport` の表示と
exit code のみを担当する — 検証ロジック自体はここに純関数群として置き、CLI 非
経由でもテストできるようにする（`compile_arrangement` / `build_performance_package`
と同じ層分離）。

fail-fast の粒度: 構造的ロード失敗（V1 の package 解決失敗、V3 の manifest 解決
失敗）だけが後続グループを丸ごとスキップさせる（`VerifyReport.aborted=True`）。
それ以外の検査は最初の失敗で止まらず、全件収集してから `VerifyReport.checks` に
積む — 呼び出し側は `VerifyReport.ok` を見て初めて exit code を決める。

書き込みは一切行わない（read_bytes のみ）。schema 検証が既に保証する内容
（`PerformancePackage._validate_delivery_references` 等）は再実装しない。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from svp_rpe.arrange.bundle import compute_content_digest
from svp_rpe.arrange.identity import (
    IdentityManifest,
    IdentityManifestError,
    parse_identity_manifest_with_artifacts,
)
from svp_rpe.arrange.package import (
    COMPILATION_REPORT_FILENAME,
    PERFORMANCE_PACKAGE_FILENAME,
    CompilationReport,
    PerformancePackage,
)
from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined

# CLI 表示用のグループ見出し（`cli/verify_cmd.py` が再利用する）。
GROUP_TITLES: dict[str, str] = {
    "V1": "V1: package load",
    "V2": "V2: compilation report",
    "V3": "V3: identity manifest chain",
    "V4": "V4: channel_artifacts",
}


@dataclass(frozen=True)
class VerifyCheck:
    """1 件の検査結果。`detail` は fail 時のみ意味を持つ（pass 時は None）。"""

    group: str
    label: str
    ok: bool
    detail: Optional[str] = None


@dataclass(frozen=True)
class VerifyReport:
    """`verify_package` の戻り値。全検査結果 + 構造的中断の有無。"""

    checks: tuple[VerifyCheck, ...]
    aborted: bool

    @property
    def failures(self) -> tuple[VerifyCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        return not self.failures


def _regular_file_error(path: Path, *, label: str) -> Optional[str]:
    """symlink または非 regular file を拒否する（`cli/builds_root.py` の
    `_reject_if_not_regular_file` と同型の判定。存在しないパスはここでは失敗に
    しない — 後続の `read_bytes` が `OSError` として報告する）。"""
    if path.is_symlink():
        return f"{label} is a symlink: {path}"
    if path.exists() and not path.is_file():
        return f"{label} is not a regular file: {path}"
    return None


def _eq_check(group: str, label: str, actual: object, expected: object) -> VerifyCheck:
    if actual == expected:
        return VerifyCheck(group, label, True)
    return VerifyCheck(group, label, False, f"expected {expected!r}, got {actual!r}")


def _v1_load_package(
    package_path: Path,
) -> tuple[list[VerifyCheck], Optional[PerformancePackage], Optional[str]]:
    """package を単一 read で読み、hash 計測 + schema 検証する（V1）。

    非 regular file / read 失敗 / schema 検証失敗はいずれも structural 失敗 —
    `PerformancePackage` を得られない以上、V2–V4 は成立しないため、呼び出し側
    (`verify_package`) はここで package が `None` なら全体を即座に打ち切る。
    """
    checks: list[VerifyCheck] = []
    label = f"performance package {package_path}"
    reg_err = _regular_file_error(package_path, label=label)
    if reg_err is not None:
        checks.append(
            VerifyCheck("V1", "package file is a regular, non-symlink file", False, reg_err)
        )
        return checks, None, None
    checks.append(VerifyCheck("V1", "package file is a regular, non-symlink file", True))

    try:
        package_bytes = package_path.read_bytes()
        package_sha256 = hashlib.sha256(package_bytes).hexdigest()
        package = PerformancePackage.model_validate_json(package_bytes)
    except (OSError, ValidationError) as exc:
        checks.append(
            VerifyCheck(
                "V1",
                "package file reads and validates against PerformancePackage schema",
                False,
                str(exc),
            )
        )
        return checks, None, None
    checks.append(
        VerifyCheck(
            "V1", "package file reads and validates against PerformancePackage schema", True
        )
    )
    return checks, package, package_sha256


def _v2_compilation_report(
    package_dir: Path, package: PerformancePackage, package_sha256: str
) -> list[VerifyCheck]:
    """同居 `compilation_report.json` を package と突き合わせる（V2）。

    `package` compile 経路は package/report を常に同時公開する（`cli/package_cmd.py`
    の `contents` dict）ため、欠落は整合性欠陥として扱い、warning ではなく fail に
    する。ここでの失敗は V2 内で閉じる（V3/V4 は manifest/package 側の独立した
    検査なので、report が壊れていても継続できる）。
    """
    checks: list[VerifyCheck] = []
    report_path = package_dir / COMPILATION_REPORT_FILENAME
    label = f"compilation report {report_path}"
    reg_err = _regular_file_error(report_path, label=label)
    if reg_err is not None:
        checks.append(
            VerifyCheck(
                "V2", "compilation_report.json is a regular, non-symlink file", False, reg_err
            )
        )
        return checks
    checks.append(VerifyCheck("V2", "compilation_report.json is a regular, non-symlink file", True))

    try:
        report_bytes = report_path.read_bytes()
        report = CompilationReport.model_validate_json(report_bytes)
    except (OSError, ValidationError) as exc:
        checks.append(
            VerifyCheck(
                "V2",
                "compilation_report.json reads and validates against CompilationReport schema",
                False,
                str(exc),
            )
        )
        return checks
    checks.append(
        VerifyCheck(
            "V2",
            "compilation_report.json reads and validates against CompilationReport schema",
            True,
        )
    )

    checks.append(
        _eq_check("V2", "report.work_id matches package.work_id", report.work_id, package.work_id)
    )
    checks.append(
        _eq_check(
            "V2", "report.generator matches package.generator", report.generator, package.generator
        )
    )
    checks.append(
        _eq_check(
            "V2",
            "report.generator_variant matches package.generator_variant",
            report.generator_variant,
            package.generator_variant,
        )
    )
    checks.append(
        _eq_check("V2", "report.inputs matches package.inputs", report.inputs, package.inputs)
    )
    checks.append(
        _eq_check(
            "V2",
            "report.package_sha256 matches computed package sha256",
            report.package_sha256,
            package_sha256,
        )
    )
    expected_digest = compute_content_digest({PERFORMANCE_PACKAGE_FILENAME: package_sha256})
    checks.append(
        _eq_check(
            "V2",
            "report.content_digest matches independently recomputed content_digest",
            report.content_digest,
            expected_digest,
        )
    )
    return checks


def _parse_manifest_mapping(raw_bytes: bytes, manifest_path: Path) -> dict[str, Any]:
    """`identity.py` / `bundle.py` / `package.py` それぞれが持つ private
    `_parse_yaml_mapping` と同型の最小コピー（既存の repo 慣習 — モジュールを
    跨いだ private ヘルパー共有はしない）。schema 検証だけを hash 照合から
    切り離すために本モジュール内で持つ。"""
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"identity manifest must be a mapping: {manifest_path}")
    return data


def _v3_identity_manifest(
    manifest_path: Path, package: PerformancePackage
) -> tuple[list[VerifyCheck], Optional[IdentityManifest]]:
    """`--manifest` を package の provenance 連鎖として検証する（V3）。

    structural 失敗（V4 を丸ごとスキップさせる）は「manifest が YAML として
    parse できない、または `IdentityManifest` schema を満たさない」場合のみに
    限定する。これは意図的に `parse_identity_manifest_with_artifacts`（全
    source/anchor artifact の封じ込め + hash 照合まで含む all-or-nothing 関数、
    item 15 / PR #187 review round 2）を直接の structural ゲートには使わない
    という判断で、理由は: V4 が実際に必要とするのは「schema 検証済みの
    anchor id 集合」だけであり、1 つの anchor artifact の hash が tamper され
    ていても、他の未改竄 anchor に対する V4 の全数チェックまで巻き添えで
    スキップさせるべきではない（tamper された 1 artifact は V4 自身が
    package 側の記録と照合して独立に検出する）。したがって:

    1. YAML parse + `IdentityManifest.model_validate` のみを structural
       ゲートとして使う（失敗したら `IdentityManifest=None` を返し呼び出し側
       が V4 を丸ごとスキップする）。
    2. schema 検証済みの manifest から anchor id 集合を得て、work_id / anchor
       id 集合の突き合わせを行う（非 structural）。
    3. その後にあらためて `parse_identity_manifest_with_artifacts` を呼び、
       source/anchor artifact の封じ込め + hash 照合を行う。ここでの失敗は
       V3 の 1 件の非 structural fail として収集するのみで、戻り値の
       `IdentityManifest`（step 1 で得たもの）はそのまま呼び出し側に返す —
       V4 は実行される。

    manifest ファイルの `read_bytes` は 1 回だけ（`単一 read` 規律）。step 1 と
    step 3 はその同じ bytes を再利用して 2 度パースするが、これはファイル I/O
    の重複ではなく単なるメモリ上の再パースであり、TOCTOU の懸念は生じない。
    """
    checks: list[VerifyCheck] = []
    label = f"identity manifest {manifest_path}"
    reg_err = _regular_file_error(manifest_path, label=label)
    if reg_err is not None:
        checks.append(
            VerifyCheck("V3", "identity manifest is a regular, non-symlink file", False, reg_err)
        )
        return checks, None
    checks.append(VerifyCheck("V3", "identity manifest is a regular, non-symlink file", True))

    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        checks.append(VerifyCheck("V3", "identity manifest is readable", False, str(exc)))
        return checks, None

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    checks.append(
        _eq_check(
            "V3",
            "identity manifest sha256 matches package.inputs.identity_manifest.sha256",
            manifest_sha256,
            package.inputs.identity_manifest.sha256,
        )
    )

    try:
        manifest_data = _parse_manifest_mapping(manifest_bytes, manifest_path)
        loaded_manifest = IdentityManifest.model_validate(manifest_data)
    except (ValueError, yaml.YAMLError, ValidationError) as exc:
        checks.append(
            VerifyCheck(
                "V3",
                "identity manifest parses and validates against IdentityManifest schema",
                False,
                str(exc),
            )
        )
        return checks, None
    checks.append(
        VerifyCheck(
            "V3",
            "identity manifest parses and validates against IdentityManifest schema",
            True,
        )
    )

    checks.append(
        _eq_check(
            "V3",
            "manifest.meta.work_id matches package.work_id",
            loaded_manifest.meta.work_id,
            package.work_id,
        )
    )

    manifest_anchor_ids = {anchor.id for anchor in loaded_manifest.anchors}
    package_anchor_ids = {status.anchor_id for status in package.anchor_statuses}
    if manifest_anchor_ids == package_anchor_ids:
        checks.append(
            VerifyCheck("V3", "manifest anchor id set matches package.anchor_statuses id set", True)
        )
    else:
        missing = sorted(manifest_anchor_ids - package_anchor_ids)
        extra = sorted(package_anchor_ids - manifest_anchor_ids)
        details = []
        if missing:
            details.append(f"missing from package.anchor_statuses: {missing}")
        if extra:
            details.append(f"extra in package.anchor_statuses: {extra}")
        checks.append(
            VerifyCheck(
                "V3",
                "manifest anchor id set matches package.anchor_statuses id set",
                False,
                "; ".join(details),
            )
        )

    try:
        parse_identity_manifest_with_artifacts(manifest_bytes, manifest_path, collect=None)
    except (IdentityManifestError, ValueError, ValidationError, yaml.YAMLError) as exc:
        checks.append(
            VerifyCheck(
                "V3",
                "all manifest source/anchor artifacts resolve and hash-verify",
                False,
                str(exc),
            )
        )
    else:
        checks.append(
            VerifyCheck(
                "V3",
                "all manifest source/anchor artifacts resolve and hash-verify",
                True,
            )
        )

    return checks, loaded_manifest


def _v4_channel_artifacts(
    package_dir: Path, package: PerformancePackage, manifest: IdentityManifest
) -> list[VerifyCheck]:
    """package の `channel_artifacts` を全数、package ディレクトリ基準で物理検証する
    （V4）。channel はソート順、reference はリスト順で走査し、順序を決定論的にする。

    `artifact_base.locator`（`kind="identity_manifest_directory"`）は package
    ディレクトリを起点とした相対パスだが、**package ディレクトリ配下に封じ込め
    られない** ── identity manifest は通常 package の出力先とは別の場所に置か
    れ、`compile_performance_package` は `os.path.relpath(manifest_dir, package_dir)`
    でこの locator を計算するため、`".."` になるのが実運用上の通常形（実測で確認
    済み）。`ArtifactBase._validate_relative_locator`（`package.py`）も絶対パスの
    みを拒否し `".."` を許容している。したがって V4 step 1 は
    `pathsafe.resolve_confined` の escape 拒否をここに適用せず、`package_dir` 起点の
    単純な解決 + 実在ディレクトリ確認に留める（絶対パス拒否は V1 の schema 検証が
    既に保証済み）。confinement（symlink escape を含む物理封じ込め）が実際に効くのは
    step 2 ── `reference.artifact`（この locator の値自体は schema 側 `".."` 拒否
    済みだが、symlink 経由の脱出は schema では検出できない）を、解決済みの
    `artifact_base_dir` を基点に `resolve_confined` で封じ込め解決する箇所であり、
    `arrange/identity.py` が `anchor.artifact` を `manifest_dir` 基準で封じ込める
    のと同じ構図をここでも踏襲する。
    """
    checks: list[VerifyCheck] = []
    manifest_anchor_ids = {anchor.id for anchor in manifest.anchors}
    for channel in sorted(package.channel_artifacts):
        for reference in package.channel_artifacts[channel]:
            prefix = f"channel '{channel}' anchor '{reference.anchor_id}'"
            artifact_base_dir: Optional[Path]
            artifact_path: Optional[Path]

            try:
                candidate_base_dir = (package_dir / reference.artifact_base.locator).resolve()
            except OSError as exc:
                checks.append(
                    VerifyCheck(
                        "V4",
                        f"{prefix}: artifact_base.locator resolves to an existing directory",
                        False,
                        str(exc),
                    )
                )
                artifact_base_dir = None
            else:
                if candidate_base_dir.is_dir():
                    artifact_base_dir = candidate_base_dir
                    checks.append(
                        VerifyCheck(
                            "V4",
                            f"{prefix}: artifact_base.locator resolves to an existing directory",
                            True,
                        )
                    )
                else:
                    artifact_base_dir = None
                    checks.append(
                        VerifyCheck(
                            "V4",
                            f"{prefix}: artifact_base.locator resolves to an existing directory",
                            False,
                            f"{candidate_base_dir} is not a directory",
                        )
                    )

            # The literal (unresolved) join must be symlink-checked *before*
            # `resolve_confined` ever runs — `resolve_confined` calls
            # `Path.resolve()` internally, which *follows* symlinks, so a
            # symlink check performed only on its return value would always
            # see the dereferenced target, never the symlink itself (the
            # target may well sit safely inside `artifact_base_dir` and hash-
            # match, silently defeating the "reject any symlink outright"
            # rule `cli/builds_root.py`'s `_reject_if_not_regular_file`
            # applies at every one of its own call sites). Checked here on
            # the plain `artifact_base_dir / reference.artifact` join, prior
            # to any resolution.
            artifact_path = None
            if artifact_base_dir is not None:
                literal_artifact_path = artifact_base_dir / reference.artifact
                reg_err = _regular_file_error(
                    literal_artifact_path, label=f"{prefix} artifact {literal_artifact_path}"
                )
                if reg_err is not None:
                    checks.append(
                        VerifyCheck(
                            "V4", f"{prefix}: artifact is a regular, non-symlink file", False, reg_err
                        )
                    )
                else:
                    checks.append(
                        VerifyCheck("V4", f"{prefix}: artifact is a regular, non-symlink file", True)
                    )
                    try:
                        artifact_path = resolve_confined(reference.artifact, artifact_base_dir)
                    except PathConfinementError as exc:
                        checks.append(
                            VerifyCheck(
                                "V4",
                                f"{prefix}: artifact resolves inside the artifact_base directory",
                                False,
                                str(exc),
                            )
                        )
                        artifact_path = None
                    else:
                        checks.append(
                            VerifyCheck(
                                "V4",
                                f"{prefix}: artifact resolves inside the artifact_base directory",
                                True,
                            )
                        )

            if artifact_path is not None:
                try:
                    artifact_bytes = artifact_path.read_bytes()
                except OSError as exc:
                    checks.append(
                        VerifyCheck(
                            "V4",
                            f"{prefix}: artifact sha256 matches artifact_sha256",
                            False,
                            str(exc),
                        )
                    )
                else:
                    actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
                    checks.append(
                        _eq_check(
                            "V4",
                            f"{prefix}: artifact sha256 matches artifact_sha256",
                            actual_sha256,
                            reference.artifact_sha256,
                        )
                    )

            if reference.anchor_id in manifest_anchor_ids:
                checks.append(
                    VerifyCheck("V4", f"{prefix}: anchor_id is present in the manifest anchor set", True)
                )
            else:
                checks.append(
                    VerifyCheck(
                        "V4",
                        f"{prefix}: anchor_id is present in the manifest anchor set",
                        False,
                        f"{reference.anchor_id!r} not found in manifest anchor ids",
                    )
                )
    return checks


def verify_package(package_path: Path, manifest_path: Path) -> VerifyReport:
    """PerformancePackage の内部整合を V1→V2→V3→V4 の順で全数検査する（読み取り専用）。

    V1（package ロード）または V3（manifest 解決）が structural に失敗した場合のみ、
    それに依存する後続グループをスキップして打ち切る（`VerifyReport.aborted=True`）。
    それ以外の検査は最初の失敗で止まらず全件収集する。ファイル書き込みは一切ない。
    """
    checks: list[VerifyCheck] = []

    v1_checks, package, package_sha256 = _v1_load_package(package_path)
    checks.extend(v1_checks)
    if package is None or package_sha256 is None:
        return VerifyReport(checks=tuple(checks), aborted=True)

    package_dir = package_path.parent
    checks.extend(_v2_compilation_report(package_dir, package, package_sha256))

    v3_checks, loaded_manifest = _v3_identity_manifest(manifest_path, package)
    checks.extend(v3_checks)
    if loaded_manifest is None:
        return VerifyReport(checks=tuple(checks), aborted=True)

    checks.extend(_v4_channel_artifacts(package_dir, package, loaded_manifest))
    return VerifyReport(checks=tuple(checks), aborted=False)
