"""private_boundary.py — RUN10 の公開境界を機械強制する（DESIGN_RUN10 §2.2 / §24 / §26）。

DESIGN_RUN10 が固定した権利境界:

```yaml
research_scope: personal_private
third_party_distribution: prohibited
public_audio_release: prohibited
public_model_release: prohibited
public_synthesis_system_release: prohibited
external_listener_panel: prohibited_without_new_permission
```

さらに §2.2 は「分析表、集計値、設計文書の外部公開可否は今回の回答だけでは
確定しないため、本Runでは公開しない」と規定し、§24 は「AquesTalk由来WAV、
render、blind map、private measurementsをGitへcommitしない」と規定する。

本リポジトリ `Yuu6798/ugh-prompt-engine` は **public** であるため、ここへの
commit は公開に等しい。したがって本モジュールは

- AQUEST 由来資産・音声・モデル・blind map・測定値・集計表・設計文書本文

が RUN10 ツリーへ混入していないことを検査する。これは §32 Stop Rule 2
（private-only storage cannot be guaranteed）と 16（public upload detected）
の実行時ガードである。

注意: 本モジュールは「public リポジトリで RUN10 を運用してよい」という裁定を
与えるものではない。その裁定は §33 の User に属する（`README.md` の
「公開境界の未裁定事項」節を参照）。本モジュールが保証するのは、裁定が下りる
までの間に private カテゴリの実体が 1 バイトも commit されないことだけである。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

# RUN10 実装ツリーのリポジトリ相対パス（§24）。
RUN10_TREE = "voice_genesis/external_validation/run10_aquest_vg_phenotype_compatibility"

# 音声・モデル・特徴量など「実体」を持つ拡張子。RUN10 ツリーへ commit しない。
PRIVATE_ASSET_SUFFIXES: Tuple[str, ...] = (
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".aiff",
    ".aif",
    ".m4a",
    ".ust",
    ".frq",
    ".npy",
    ".npz",
    ".pt",
    ".pth",
    ".ckpt",
    ".onnx",
    ".safetensors",
    ".pkl",
)

# ファイル名にこれらを含むものは private カテゴリ（§19 blind map / §26 private_*）。
PRIVATE_NAME_MARKERS: Tuple[str, ...] = (
    "blind_id_map",
    "private_features",
    "private_probes",
    "private_generated",
    "human_timbre_notes",
)

# 設計文書本文は commit しない（§2.2）。参照は sha256 pin のみ。
FORBIDDEN_DOCUMENT_NAMES: Tuple[str, ...] = (
    "VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.1.md",
    "VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.2.md",
    "VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.3.md",
    "VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.4.md",
    "VoiceGenesis_RUN10_Known_Performance_Trainability_Test_v0.1.txt",
)

# `results/` 配下で commit を許す唯一のファイル（§26 は results を private bundle と規定）。
RESULTS_ALLOWLIST: Tuple[str, ...] = (".gitignore",)


class PrivateBoundaryError(RuntimeError):
    """公開境界違反。fail-closed で送出する。"""


def repo_root(start: Path | str | None = None) -> Path:
    """`.git` を持つ最も近い祖先を返す。"""
    here = Path(start) if start is not None else Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise PrivateBoundaryError(f"git リポジトリのルートが見つからない: {here}")


def git_tracked_files(root: Path, subpath: str = RUN10_TREE) -> List[str]:
    """`git ls-files` で追跡中のパス（リポジトリ相対）を返す。

    git が使えない環境では `PrivateBoundaryError` を送出する。呼び出し側
    （テスト）は skip ではなく失敗として扱ってよい — 追跡状態を確認できない
    こと自体が「private-only storage を保証できない」状態だからである。
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", subpath],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - 環境依存
        raise PrivateBoundaryError(f"git ls-files に失敗した: {exc}") from exc
    return [line for line in completed.stdout.splitlines() if line]


def classify_violation(relative_path: str) -> str | None:
    """commit してはならない理由を返す。問題なければ `None`。"""
    path = Path(relative_path)
    name = path.name

    if name in FORBIDDEN_DOCUMENT_NAMES:
        return "設計文書本文は commit しない（§2.2 — 参照は sha256 pin のみ）"

    if path.suffix.lower() in PRIVATE_ASSET_SUFFIXES:
        return f"private 実体資産の拡張子 {path.suffix}（§24 — 音声/render/モデルは commit しない）"

    lowered = name.lower()
    for marker in PRIVATE_NAME_MARKERS:
        if marker in lowered:
            return f"private カテゴリのファイル名マーカー {marker!r}（§19 / §26）"

    parts = path.parts
    if "results" in parts:
        index = parts.index("results")
        tail = parts[index + 1:]
        if tail and tail[-1] not in RESULTS_ALLOWLIST:
            return "results/ は private bundle であり .gitignore 以外を commit しない（§26）"

    return None


def scan_tracked_tree(
    root: Path | None = None,
    subpath: str = RUN10_TREE,
) -> List[Tuple[str, str]]:
    """RUN10 ツリーの git 追跡ファイルを走査し `(path, reason)` の違反一覧を返す。"""
    base = root if root is not None else repo_root()
    violations: List[Tuple[str, str]] = []
    for tracked in git_tracked_files(base, subpath):
        reason = classify_violation(tracked)
        if reason is not None:
            violations.append((tracked, reason))
    return violations


def assert_tracked_tree_clean(root: Path | None = None, subpath: str = RUN10_TREE) -> None:
    """違反があれば `PrivateBoundaryError`。"""
    violations = scan_tracked_tree(root, subpath)
    if violations:
        detail = "\n".join(f"  - {path}: {reason}" for path, reason in violations)
        raise PrivateBoundaryError(f"RUN10 公開境界違反 ({len(violations)} 件):\n{detail}")


def assert_private_staging_path(path: Path | str, staging_root: Path | str) -> Path:
    """出力先が private staging root の内側であることを保証する（§26）。

    シンボリックリンク経由の脱出も塞ぐため `resolve()` 後に比較する。
    """
    resolved_root = Path(staging_root).resolve()
    resolved = Path(path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PrivateBoundaryError(
            f"private staging 外への書き出しは禁止: {resolved} (root={resolved_root})"
        )
    return resolved


def assert_no_public_destination(destinations: Iterable[str]) -> None:
    """公開先へ成果物を送る経路を塞ぐ（§2.2 / §32 Stop Rule 16）。"""
    public_markers: Sequence[str] = (
        "http://",
        "https://",
        "s3://",
        "gs://",
        "ftp://",
    )
    offenders = [
        dest
        for dest in destinations
        if any(dest.lower().startswith(marker) for marker in public_markers)
    ]
    if offenders:
        raise PrivateBoundaryError(
            f"RUN10 成果物の外部送出先が指定された: {offenders}（§2.2 third_party_distribution）"
        )
