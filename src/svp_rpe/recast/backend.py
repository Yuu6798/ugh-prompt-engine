"""BackendInvoker 抽象: recast plan の compile 済み成果物から実行素材を組み立て、
backend の呼び出し（manual = 注文書公開 / local = 実生成）を行う。

`RecastRunContext` は `recast/plan.py` の `build_recast_plan_artifacts` が返す
compile 済み成果物（`PerformancePackage` / derived `CompositionScore` / 各
sha256 pin）を**再利用**する（plan パイプラインを再計算しない）。`build_recast_plan`
（既存公開 API・plan JSON）は一切変更しない — `build_recast_plan_artifacts` は
plan.py 側の内部エンジンをそのまま公開しただけの拡張。

`PreparedInvocation` は `BackendInvoker.prepare()` が組み立てる「この 1 回の
(variant, backend) 実行に必要な実行素材一式」（package / derived_score /
order_dir・takes_dir の解決済みパス / identity source 参照）。`invoke()` は
local invocation のみが実装し、manual invocation では `RecastError` を送出する
（逆に `collect()` は manual のみが実装し、local invocation では `RecastError`）。

具象 invoker は `recast/backends/` 以下（`manual.py` / `deterministic.py` /
`musicgen.py`）に分離する — Suno のような手動生成器固有の分岐はすべて
`ManualInvoker`（generator 非依存の汎用注文書ビルダー）に集約し、個々の
generator 名で分岐しない（PR3 指示書「Suno 例外分岐は manual backend に集約」）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Protocol

import yaml

from svp_rpe.arrange.capabilities import InputCapabilityProfile
from svp_rpe.arrange.identity import IdentityManifest
from svp_rpe.arrange.package import CompiledPerformancePackage, PerformancePackage
from svp_rpe.compose.models import CompositionScore
from svp_rpe.recast.loader import LoadedRecastProject
from svp_rpe.recast.models import BackendRef, InvocationKind, InvocationMode, RecastError
from svp_rpe.recast.plan import RecastPlanArtifacts, RecastPlanResult, build_recast_plan_artifacts


class RecastBackendUnavailable(RecastError):
    """ローカル生成器が呼び出し可能な状態にない（未配線 / 未インストール / 未取得）。"""


@dataclass(frozen=True)
class RecastRunContext:
    """`recast run` 1 回分の実行コンテキスト: 解決済みプロジェクト + plan 段の
    compile 済み成果物一式（plan の再計算はしない）。"""

    loaded: LoadedRecastProject
    variant: str
    backend: str
    backend_ref: BackendRef
    profile: InputCapabilityProfile
    plan_result: RecastPlanResult
    compiled: CompiledPerformancePackage
    derived_score: CompositionScore


def load_backend_capability_profile(
    loaded: LoadedRecastProject, backend: str
) -> InputCapabilityProfile:
    """`loaded.capability_profile_paths[backend]` から `InputCapabilityProfile` を読む。

    `recast/plan.py` の step 6 と同じ読み込み経路（evidence 検証は plan 段が
    既に行っている前提で、ここでは再検証しない — 呼び出し側は plan が
    `compiled`/`verified` に到達した後にのみこれを呼ぶ）。
    """
    profile_path = loaded.capability_profile_paths[backend]
    data = yaml.safe_load(profile_path.read_bytes())
    if not isinstance(data, dict):
        raise RecastError(f"input capability profile must be a mapping: {profile_path}")
    return InputCapabilityProfile.model_validate(data)


def run_context_from_plan_artifacts(
    loaded: LoadedRecastProject,
    *,
    variant: str,
    backend: str,
    artifacts: RecastPlanArtifacts,
    profile: InputCapabilityProfile,
) -> RecastRunContext:
    """既に計算済みの `RecastPlanArtifacts` から `RecastRunContext` を組み立てる
    （plan パイプラインの再計算をしない、`recast run` CLI の主経路）。"""
    if artifacts.compiled is None or artifacts.derived_score is None:
        raise RecastError(
            f"recast run: {variant}@{backend} did not reach a compiled performance "
            f"package (state_reached={artifacts.result.plan.state_reached!r}); resolve "
            "the blocking diagnostics reported by 'recast plan' first"
        )
    return RecastRunContext(
        loaded=loaded,
        variant=variant,
        backend=backend,
        backend_ref=artifacts.backend_ref,
        profile=profile,
        plan_result=artifacts.result,
        compiled=artifacts.compiled,
        derived_score=artifacts.derived_score,
    )


def build_recast_run_context(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> RecastRunContext:
    """`build_recast_plan_artifacts` を 1 回だけ呼び、`RecastRunContext` を組み立てる
    利便関数（テスト・非 CLI 呼び出し向け）。`recast run` CLI は plan 診断表示を
    共有するため `run_context_from_plan_artifacts` を直接使う。"""
    artifacts = build_recast_plan_artifacts(loaded, variant=variant, backend=backend)
    profile = load_backend_capability_profile(loaded, backend)
    return run_context_from_plan_artifacts(
        loaded, variant=variant, backend=backend, artifacts=artifacts, profile=profile
    )


def _run_key(variant: str, backend: str) -> str:
    return f"{variant}@{backend}"


def resolve_order_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """manual invoker が注文書 6 ファイルを公開する先: `<builds_root>/orders/<variant>@<backend>/`。"""
    return loaded.builds_root / "orders" / _run_key(variant, backend)


def resolve_takes_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """生成済みテイクの収蔵先（manual collect / local invoke 共通）:
    `<builds_root>/takes/<variant>@<backend>/`。"""
    return loaded.builds_root / "takes" / _run_key(variant, backend)


def read_identity_source(loaded: LoadedRecastProject) -> tuple[str, str]:
    """`IdentityManifest.source` の locator + sha256 を読む（軽量パースのみ —
    plan 段が既に artifact hash 照合済みのため、ここでは anchor bytes の再読み・
    再照合は行わない。cover モードの注文書に「参照音声はこれ」と示すためだけの
    情報用途）。"""
    data = yaml.safe_load(loaded.identity_manifest_path.read_bytes())
    if not isinstance(data, dict):
        raise RecastError(
            f"identity manifest must be a mapping: {loaded.identity_manifest_path}"
        )
    manifest = IdentityManifest.model_validate(data)
    return manifest.source.locator, manifest.source.sha256


def base_prepared_invocation(ctx: RecastRunContext) -> PreparedInvocation:
    """3 つの具象 invoker（manual/deterministic/musicgen）が共有する
    `PreparedInvocation` の共通フィールド組み立て（order_dir/takes_dir の解決 +
    package/derived_score/sha256 pin + identity source 参照）。"""
    order_dir = resolve_order_dir(ctx.loaded, ctx.variant, ctx.backend)
    takes_dir = resolve_takes_dir(ctx.loaded, ctx.variant, ctx.backend)
    identity_source_locator, identity_source_sha256 = read_identity_source(ctx.loaded)
    return PreparedInvocation(
        variant=ctx.variant,
        backend_name=ctx.backend,
        generator=ctx.profile.generator,
        invocation=ctx.backend_ref.invocation,
        invocation_mode=ctx.backend_ref.invocation_mode,
        package=ctx.compiled.package,
        derived_score=ctx.derived_score,
        package_sha256=ctx.compiled.report.package_sha256,
        content_digest=ctx.compiled.report.content_digest,
        order_dir=order_dir,
        takes_dir=takes_dir,
        identity_source_locator=identity_source_locator,
        identity_source_sha256=identity_source_sha256,
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """tempfile + `os.replace` による atomic publish（binary 版。
    `recast/state.py` の `_write_recast_state_atomically` と同型 — テキスト
    専用の `cli/builds_root.py:_publish_artifacts_atomically` は
    `str.encode('utf-8')` 前提のため音声 bytes には使えず、本関数を別途持つ）。"""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class PreparedInvocation:
    """1 (variant, backend) 実行分の実行素材一式（prepare 時点で確定する）。"""

    variant: str
    backend_name: str
    generator: str
    invocation: InvocationKind
    invocation_mode: InvocationMode
    package: PerformancePackage
    derived_score: CompositionScore
    package_sha256: str
    content_digest: str
    order_dir: Path
    takes_dir: Path
    identity_source_locator: str
    identity_source_sha256: str


@dataclass(frozen=True)
class GeneratedTake:
    """1 テイクの受領記録。"""

    audio_path: Path
    sha256: str
    source: Literal["local", "manual"]
    backend_name: str
    note: Optional[str] = None


class BackendInvoker(Protocol):
    """backend 呼び出しの共通インタフェース。"""

    def prepare(self, ctx: RecastRunContext) -> PreparedInvocation:
        """`ctx` から実行素材一式（`PreparedInvocation`）を組み立てる。"""
        ...

    def invoke(self, prepared: PreparedInvocation) -> GeneratedTake:
        """local invocation のみ: 実際に生成を実行する。manual では `RecastError`。"""
        ...

    def collect(self, prepared: PreparedInvocation, supplied_audio: Path) -> GeneratedTake:
        """manual invocation のみ: 外部生成された音声を受領する。local では `RecastError`。"""
        ...


def resolve_invoker(backend_ref: BackendRef, profile: InputCapabilityProfile) -> BackendInvoker:
    """`backend_ref.invocation` + `profile.generator` から `BackendInvoker` を解決する。

    `invocation == "manual"` は generator に依存しない汎用 `ManualInvoker` へ
    常に解決する（指示書: Suno 等の手動生成器固有分岐は一切ここに持ち込まない）。
    `invocation == "local"` は generator 名で具象 invoker へ分岐する。
    """
    if backend_ref.invocation == "manual":
        from svp_rpe.recast.backends.manual import ManualInvoker

        return ManualInvoker()
    if backend_ref.invocation == "local":
        if profile.generator == "deterministic":
            from svp_rpe.recast.backends.deterministic import DeterministicInvoker

            return DeterministicInvoker()
        if profile.generator == "musicgen":
            from svp_rpe.recast.backends.musicgen import MusicgenInvoker

            return MusicgenInvoker()
        raise RecastError(
            f"no local backend invoker registered for generator {profile.generator!r} "
            "(supported: 'deterministic', 'musicgen')"
        )
    raise RecastError(f"unknown invocation kind: {backend_ref.invocation!r}")
