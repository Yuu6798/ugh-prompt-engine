"""test_private_output_boundary.py — RUN10 公開境界の機械強制（DESIGN_RUN10 §2.2 / §24 / §26）。

§28 最低テストのうち本ファイルが担当する項目:

```text
7  public/shared output path rejected
8  AquesTalk-derived WAV cannot enter Git staging
9  AquesTalk-derived render cannot enter public artifact bundle
```

本リポジトリは public であるため、これらは「設計上の望ましさ」ではなく
実行時の必須ガードである。§32 Stop Rule 2 / 16 に直結する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import private_boundary as pb  # noqa: E402


def test_tracked_run10_tree_has_no_private_assets() -> None:
    """§28-8 / §28-9: RUN10 ツリーの git 追跡ファイルに private 実体が無い。

    これが本 PR の中心的な安全性主張である。違反があれば即座に失敗する。
    """
    pb.assert_tracked_tree_clean()


def test_repo_root_is_this_repository() -> None:
    """ガードが対象にしているのが本リポジトリであることを確認する。"""
    root = pb.repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / pb.RUN10_TREE).is_dir()


@pytest.mark.parametrize(
    "path",
    [
        f"{pb.RUN10_TREE}/corpus/a0/a.wav",
        f"{pb.RUN10_TREE}/corpus/a1_render/ka.WAV",
        f"{pb.RUN10_TREE}/results/private_features/f1.npy",
        f"{pb.RUN10_TREE}/inputs/neutral.ust",
        f"{pb.RUN10_TREE}/models/adapter.onnx",
    ],
)
def test_private_asset_suffixes_are_rejected(path: str) -> None:
    """§28-8: 音声・モデル・特徴量の実体は commit させない。"""
    assert pb.classify_violation(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        f"{pb.RUN10_TREE}/inputs/blind_id_map.json",
        f"{pb.RUN10_TREE}/results/private_probes/x.json",
        f"{pb.RUN10_TREE}/results/human_timbre_notes.json",
    ],
)
def test_private_name_markers_are_rejected(path: str) -> None:
    """§19 / §26: blind map と private_* 成果物は commit させない。"""
    assert pb.classify_violation(path) is not None


@pytest.mark.parametrize("name", pb.FORBIDDEN_DOCUMENT_NAMES)
def test_design_documents_are_rejected(name: str) -> None:
    """§2.2: 設計文書本文（全 revision）と旧 RUN10 案は commit させない。"""
    reason = pb.classify_violation(f"{pb.RUN10_TREE}/{name}")
    assert reason is not None
    assert "設計文書" in reason


def test_results_directory_allows_only_gitignore() -> None:
    """§28-9 / §26: results/ は private bundle であり .gitignore 以外を置かない。"""
    assert pb.classify_violation(f"{pb.RUN10_TREE}/results/.gitignore") is None
    assert pb.classify_violation(f"{pb.RUN10_TREE}/results/run10_results.json") is not None
    assert pb.classify_violation(f"{pb.RUN10_TREE}/results/compatibility_matrix.json") is not None


def test_implementation_files_are_allowed() -> None:
    """ガードが実装コードまで塞いでいないこと（偽陽性で運用不能にしない）。"""
    for path in (
        f"{pb.RUN10_TREE}/run10_schema.py",
        f"{pb.RUN10_TREE}/RUN10_CONTRACT.yaml",
        f"{pb.RUN10_TREE}/inputs/af01_payload_sha256sums.txt",
        f"{pb.RUN10_TREE}/inputs/rights_manifest.json",
    ):
        assert pb.classify_violation(path) is None


def test_results_gitignore_is_present_and_total() -> None:
    """§26: results/.gitignore が全ファイルを無視している。"""
    text = (_RUN_DIR / "results" / ".gitignore").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "*"
    assert "!.gitignore" in text


# --- §28-7: public/shared output path rejected -----------------------------


def test_private_staging_path_guard(tmp_path: Path) -> None:
    """§26: staging root の外へは書き出せない。"""
    staging = tmp_path / "private"
    staging.mkdir()
    assert pb.assert_private_staging_path(staging / "run10_results.json", staging)
    with pytest.raises(pb.PrivateBoundaryError, match="private staging 外"):
        pb.assert_private_staging_path(tmp_path / "public" / "out.json", staging)


def test_private_staging_path_guard_blocks_symlink_escape(tmp_path: Path) -> None:
    """symlink 経由で staging 外へ抜ける経路も塞ぐ。"""
    staging = tmp_path / "private"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (staging / "escape").symlink_to(outside)
    with pytest.raises(pb.PrivateBoundaryError, match="private staging 外"):
        pb.assert_private_staging_path(staging / "escape" / "out.json", staging)


@pytest.mark.parametrize(
    "destination",
    ["https://example.invalid/upload", "s3://bucket/run10", "gs://bucket/run10"],
)
def test_public_destinations_are_rejected(destination: str) -> None:
    """§28-7 / §32-16: 外部送出先の指定を拒否する。"""
    with pytest.raises(pb.PrivateBoundaryError, match="外部送出先"):
        pb.assert_no_public_destination([destination])


def test_local_destinations_are_allowed() -> None:
    """ローカル private パスは通す。"""
    pb.assert_no_public_destination(["/mnt/private/run10", "./results/RUN10_PRIVATE"])


# --- 閉世界 allowlist（PR #330 Codex 第 1 巡 P1） --------------------------


@pytest.mark.parametrize(
    "path",
    [
        "measurement/compatibility_matrix.json",
        "measurement/features_a0.json",
        "calibration/external_calibration_results.json",
        "evaluation/difference_map.json",
        "evaluation/statistical_report.json",
        "synthesis_validation/generative_compatibility_matrix.json",
        "corpus/manifests/a1_render_manifest.json",
        "novel_trait_candidates.json",
        "summary.csv",
        "aggregate_table.tsv",
    ],
)
def test_future_output_directories_are_denied_by_allowlist(path: str) -> None:
    """§24 の未作成ディレクトリが追加されても測定値・集計表は公開されない。

    拒否リスト方式では拡張子もファイル名マーカーも `results/` にも当たらず
    素通りしていた経路を、閉世界 allowlist が塞ぐ。
    """
    reason = pb.classify_violation(f"{pb.RUN10_TREE}/{path}")
    assert reason is not None
    assert "allowlist" in reason


@pytest.mark.parametrize(
    "path",
    [
        "measurement/extract_features.py",
        "calibration/validate_meters.py",
        "evaluation/adjudicate_run10.py",
        "calibration/measurement_decision_spec.yaml",
        "synthesis_validation/phase_b_entry_spec.yaml",
        "corpus/README_PRIVATE_ASSET_BOUNDARY.md",
    ],
)
def test_implementation_files_in_future_directories_are_allowed(path: str) -> None:
    """コード・契約・文書は将来ディレクトリでも通す（偽陽性で運用不能にしない）。"""
    assert pb.classify_violation(f"{pb.RUN10_TREE}/{path}") is None


def test_every_tracked_data_file_is_explicitly_listed() -> None:
    """公開中の JSON / TXT が allowlist に明示登録されている（暗黙許可を作らない）。"""
    tracked = pb.git_tracked_files(pb.repo_root())
    data = [
        pb._tree_relative(t)
        for t in tracked
        if Path(t).suffix.lower() in (".json", ".txt", ".csv", ".tsv")
    ]
    assert data, "追跡中の構造 manifest が 1 件も無いのは想定外"
    assert set(data) <= set(pb.PUBLISHABLE_DATA_FILES)
