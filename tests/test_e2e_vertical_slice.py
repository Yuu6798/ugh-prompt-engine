"""AR3-2 縦切り E2E (07-14 S2 items 17-18): 1 曲 x 1 編曲 x 1 生成器 x 実在 artifact で
IdentityManifest -> PreservationContract -> InputCapabilityProfile -> PerformancePackage ->
CompilationReport を実 CLI で一気通貫させる回帰テスト。

固定する主張は「hard melody anchor が honest に unsupported と記録されること」
（配送成功は一切主張しない。Design Memo D-2）と、同一 package 内で lyrics
（hard x supported = delivered）との対比が読めること（D-3）。

`examples/arrangement/midnight_signal/` 配下は #177 の EDM/Jazz byte-pin fixture
を連鎖再生成させないため、identity 付き編曲 spec (`edm.identity.arrangement.yaml`) と
IdentityManifest 一式は additive な別ファイルとして新設したものを使う（Design Memo D-1）。

`artifact_base.locator` は identity manifest ディレクトリから performance package
ディレクトリまでの相対パスであり、両者の絶対位置ではなく相対的な深さのみに依存する
(`os.path.relpath` は共通接頭辞より先の構成要素だけを見る)。committed
`expected/e2e_edm/performance_package.json` は `--output-dir` を
`examples/arrangement/midnight_signal/expected/<anything>` に置いて生成しており
（manifest ディレクトリから 2 階層下 = locator `"../.."`）、本テストの byte-pin 検証も
同じ「manifest ディレクトリから 2 階層下」の一時ディレクトリへ出力することで、
チェックアウト先の絶対パスに関わらず同一 bytes を再生成する。
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.arrange.bundle import compute_content_digest
from svp_rpe.arrange.package import (
    COMPILATION_REPORT_SCHEMA_VERSION,
    PERFORMANCE_PACKAGE_FILENAME,
    CompilationReport,
    PerformancePackage,
)
from svp_rpe.cli import app

FIXTURE_DIR = Path("examples/arrangement/midnight_signal")
BASE_SCORE = FIXTURE_DIR / "composition_score.yaml"
IDENTITY_MANIFEST = FIXTURE_DIR / "identity_manifest.yaml"
EDM_IDENTITY_SPEC = FIXTURE_DIR / "edm.identity.arrangement.yaml"
IDENTITY_ARTIFACT_DIR = FIXTURE_DIR / "identity"
SUNO_PROFILE_PATH = Path("config/capability_profiles/suno.yaml")
EXPECTED_PACKAGE_PATH = FIXTURE_DIR / "expected" / "e2e_edm" / "performance_package.json"


def _package_cli_args(
    identity_yaml: Path,
    output_dir: Path,
    *,
    score_yaml: Path = BASE_SCORE,
    arrangement_yaml: Path = EDM_IDENTITY_SPEC,
    strict: bool = False,
) -> list[str]:
    args = [
        "package",
        str(score_yaml),
        str(identity_yaml),
        str(arrangement_yaml),
        "--capability-profile",
        str(SUNO_PROFILE_PATH),
        "--output-dir",
        str(output_dir),
    ]
    if strict:
        args.append("--strict-capabilities")
    return args


@pytest.fixture
def advisory_package() -> Iterator[dict[str, Any]]:
    """Compile the advisory package into a scratch dir 2 levels below the identity
    manifest directory (same depth as `expected/e2e_edm/`), so `artifact_base.locator`
    reproduces the committed byte-pin regardless of the checkout's absolute path."""
    with tempfile.TemporaryDirectory(dir=FIXTURE_DIR / "expected") as scratch:
        output_dir = Path(scratch)
        result = CliRunner().invoke(app, _package_cli_args(IDENTITY_MANIFEST, output_dir))
        assert result.exit_code == 0, result.output
        package_bytes = (output_dir / "performance_package.json").read_bytes()
        report_bytes = (output_dir / "compilation_report.json").read_bytes()
        yield {
            "output_dir": output_dir,
            "package_bytes": package_bytes,
            "report_bytes": report_bytes,
            "package": PerformancePackage.model_validate_json(package_bytes),
            "report": CompilationReport.model_validate_json(report_bytes),
        }


# --- 1. arrange -> package 連鎖 sha 検証 -------------------------------------------


def test_arrange_and_package_agree_on_derived_score_sha256(
    tmp_path: Path, advisory_package: dict[str, Any]
) -> None:
    arrange_output = tmp_path / "arrange_out"
    result = CliRunner().invoke(
        app,
        ["arrange", str(BASE_SCORE), str(EDM_IDENTITY_SPEC), "--output-dir", str(arrange_output)],
    )
    assert result.exit_code == 0, result.output
    derived_sha256 = hashlib.sha256(
        (arrange_output / "derived_score.yaml").read_bytes()
    ).hexdigest()

    package = advisory_package["package"]
    assert package.inputs.derived_score.sha256 == derived_sha256


# --- 2. melody 4 状態 pin + lyrics delivered 対照 -----------------------------------


def test_melody_anchor_is_honest_unsupported_and_lyrics_is_delivered(
    advisory_package: dict[str, Any],
) -> None:
    package = advisory_package["package"]
    report = advisory_package["report"]
    statuses = {status.anchor_id: status for status in package.anchor_statuses}

    melody = statuses["melody"]
    assert melody.requested_mode == "hard"
    assert melody.delivery.status == "unsupported"
    assert melody.delivery.channel is None
    assert melody.control.status == "unknown"
    assert melody.observation.status == "not_observed"
    assert "symbolic_melody" not in package.channel_artifacts

    lyrics = statuses["lyrics"]
    assert lyrics.requested_mode == "hard"
    assert lyrics.delivery.status == "delivered"
    assert lyrics.delivery.channel == "lyrics_text"
    assert lyrics.control.status == "unknown"
    assert lyrics.observation.status == "not_observed"
    lyrics_references = package.channel_artifacts["lyrics_text"]
    assert [ref.anchor_id for ref in lyrics_references] == ["lyrics"]

    assert "anchor 'melody': channel 'symbolic_melody' is unsupported" in report.warnings


# --- 3. strict 失敗で非公開 ---------------------------------------------------------


def test_strict_capabilities_fails_on_hard_melody_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "strict_out"

    result = CliRunner().invoke(
        app,
        _package_cli_args(IDENTITY_MANIFEST, output_dir, strict=True),
    )

    assert result.exit_code == 1
    assert "strict capability check failed" in result.stderr
    assert "melody" in result.stderr
    assert not output_dir.exists()


# --- 4. byte-pin ---------------------------------------------------------------


def test_advisory_package_matches_committed_byte_pin(
    advisory_package: dict[str, Any],
) -> None:
    assert advisory_package["package_bytes"] == EXPECTED_PACKAGE_PATH.read_bytes()


# --- 5. report フィールド検証 (D-4: report 自体は committed byte-pin 対象外) --------


def test_compilation_report_fields(advisory_package: dict[str, Any]) -> None:
    report = advisory_package["report"]
    package_bytes = advisory_package["package_bytes"]

    assert report.schema_version == COMPILATION_REPORT_SCHEMA_VERSION
    assert report.generator == "suno"
    assert report.generator_variant == "standard"
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    assert report.package_sha256 == package_sha256
    assert report.content_digest == compute_content_digest(
        {PERFORMANCE_PACKAGE_FILENAME: package_sha256}
    )
    # invocation_provenance.compiler is best-effort audit metadata (D-4): the field
    # must exist, but its values (package_version / git_commit) may legitimately be
    # None and are therefore never asserted against a fixed value here.
    assert report.invocation_provenance.compiler is not None


# --- 6. manifest 実在検証: anchor sha256 改竄 -> exit 1 ネガティブ ------------------


def test_tampered_anchor_sha256_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "identity").mkdir()
    (tmp_path / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    (tmp_path / "identity" / "lyrics.txt").write_bytes(
        (IDENTITY_ARTIFACT_DIR / "lyrics.txt").read_bytes()
    )
    (tmp_path / "identity" / "melody_notes.json").write_bytes(
        (IDENTITY_ARTIFACT_DIR / "melody_notes.json").read_bytes()
    )

    manifest_data = yaml.safe_load(IDENTITY_MANIFEST.read_text(encoding="utf-8"))
    tampered_ids = {anchor["id"] for anchor in manifest_data["anchors"] if anchor["id"] == "melody"}
    for anchor in manifest_data["anchors"]:
        if anchor["id"] == "melody":
            anchor["sha256"] = "0" * 64
    assert tampered_ids == {"melody"}  # guards against a silent no-op if the fixture ever drifts

    tampered_manifest = tmp_path / "identity_manifest.yaml"
    tampered_manifest.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    output_dir = tmp_path / "tamper_out"
    result = CliRunner().invoke(
        app,
        _package_cli_args(
            tampered_manifest,
            output_dir,
            score_yaml=tmp_path / "composition_score.yaml",
        ),
    )

    assert result.exit_code == 1
    assert "sha256 mismatch" in result.stderr
    assert "melody" in result.stderr
    assert not output_dir.exists()
