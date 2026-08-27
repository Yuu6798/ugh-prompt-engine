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

裁定: R10-PUB-1 は User 裁定 2026-08-27 `APPROVED_CODE_ONLY_PUBLIC`
（実装コードのみ public で継続）で決着している。本モジュールはその常設方針を
機械強制するものであり、方針そのものを与えるものではない（裁定は §33 の
User に属する — `inputs/private_storage_policy.json` が正本）。

判定は**閉世界 allowlist**である。公開してよいものを列挙し、それ以外は
すべて拒否する。将来 `measurement/` `calibration/` `evaluation/` が
追加されたときに測定値・集計表が拒否リストの隙間から公開されることを防ぐ
（AGENTS.md「回収・検収系の成功条件は閉世界契約で書く」）。
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

# --- 閉世界 allowlist ------------------------------------------------------
#
# 拡張子・ファイル名マーカーによる**拒否リスト**だけでは、将来 §24 の
# `measurement/` `calibration/` `evaluation/` が追加されたとき
# `measurement/compatibility_matrix.json` のような測定値・集計表が
# どの拒否条件にも当たらず public リポジトリへ入る（PR #330 Codex 第 1 巡 P1）。
# AGENTS.md「回収・検収系の成功条件は閉世界契約で書く」に従い、
# **公開してよいものを列挙し、それ以外はすべて拒否する**方式へ反転する。

# どこに置いても公開してよい拡張子（実装コード・契約・文書）。
PUBLISHABLE_SUFFIXES: Tuple[str, ...] = (
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
)

# 拡張子を持たない、または特殊なファイル名で公開してよいもの。
PUBLISHABLE_NAMES: Tuple[str, ...] = (
    ".gitignore",
    ".gitkeep",
)

# JSON / TXT は「測定値・集計表」になり得るため、置ける場所と名前を閉世界で限定する。
# 新しい構造 manifest を公開したくなったら、ここへ明示的に足すこと（fail-closed）。
PUBLISHABLE_DATA_FILES: Tuple[str, ...] = (
    "inputs/af01_payload_sha256sums.txt",
    "inputs/rights_manifest.json",
    "inputs/private_storage_policy.json",
    "inputs/dependency_pins.json",
    "inputs/aquest_voicebank_manifest.json",
    "inputs/vg_reference_manifest.json",
    "inputs/neutral_carrier_manifest.json",
    "pre_run/inventory.json",
    "pre_run/aquest_pitch_inventory.json",
    "pre_run/vg_reference_inventory.json",
    "pre_run/dependency_presence_report.json",
)


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


def _tree_relative(relative_path: str) -> str:
    """RUN10 ツリー相対パスへ正規化する（ツリー外はそのまま返す）。"""
    prefix = RUN10_TREE + "/"
    return relative_path[len(prefix):] if relative_path.startswith(prefix) else relative_path


def classify_violation(relative_path: str) -> str | None:
    """commit してはならない理由を返す。問題なければ `None`。

    判定順は「明示的な拒否理由 → 閉世界 allowlist」。前段は違反の理由を
    具体的に述べるためにあり、最終的な可否は後段の allowlist が決める。
    """
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

    # --- 閉世界 allowlist（ここを通らないものはすべて拒否する）---
    inside = _tree_relative(relative_path)
    if name in PUBLISHABLE_NAMES:
        return None
    if path.suffix.lower() in PUBLISHABLE_SUFFIXES:
        return None
    if inside in PUBLISHABLE_DATA_FILES:
        return None
    return (
        f"公開 allowlist に無い（§2.2 測定値・集計表の非公開）。"
        f" 構造 manifest として公開が必要なら private_boundary.PUBLISHABLE_DATA_FILES"
        f" へ明示登録すること: {inside}"
    )


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
