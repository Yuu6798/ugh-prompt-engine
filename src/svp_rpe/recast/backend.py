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
from typing import Dict, Literal, Optional, Protocol

import yaml

from svp_rpe.arrange.capabilities import InputCapabilityProfile
from svp_rpe.arrange.package import CompiledPerformancePackage, PerformancePackage
from svp_rpe.compose.models import CompositionScore
from svp_rpe.recast.loader import LoadedRecastProject
from svp_rpe.recast.models import BackendRef, InvocationKind, InvocationMode, RecastError
from svp_rpe.recast.plan import RecastPlanArtifacts, RecastPlanResult, build_recast_plan_artifacts
from svp_rpe.recast.run_paths import resolve_orders_dir, resolve_takes_dir


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
    # `lyrics_text`/`section_map` anchor の hash 照合済み bytes（anchor_id
    # キー）。plan 段（`build_recast_plan_artifacts`）が single-read で 1 回
    # だけ読んだものをそのまま引き回す — `ManualInvoker.prepare()` の注文書
    # 描画（`lyrics.txt`/`section_tags.txt`）がこれを使い、artifact ファイルを
    # 再 read しない（Codex P2 review round 11, PR3 #208 指摘22: 従来は
    # 注文書描画時に `resolve_confined(...).read_text()` で disk から再 read
    # しており、plan 段の hash 検証とファイル内容が乖離し得る TOCTOU があった）。
    channel_artifact_bytes: Dict[str, bytes]
    # `IdentityManifest.source` の locator/sha256（plan 段の single-read 束が
    # 既に hash 照合込みで parse 済みの値をそのまま引き回す）。`ManualInvoker`
    # の cover モード注文書がこれを使い、`identity.yaml` を再 read/再 parse
    # しない（Codex P2 review round 12, PR3 #208 指摘26）。
    identity_source_locator: str
    identity_source_sha256: str


def load_backend_capability_profile(
    loaded: LoadedRecastProject, backend: str
) -> InputCapabilityProfile:
    """`loaded.capability_profile_paths[backend]` から `InputCapabilityProfile` を読む。

    スタンドアロンの読み込みヘルパー（`recast/plan.py` の step 6 と同じ読み込み
    経路）— `recast run`/`recast ingest` CLI はもはやこれを呼ばない（Codex P2
    review round 12, PR3 #208 指摘24: `run_context_from_plan_artifacts` は
    plan 段が single-read 束で既に parse・validate 済みの `RecastPlanArtifacts.
    profile` を使う。本関数は plan 束を経由しないスタンドアロン用途
    （単体テストや、plan を経由しない診断ツール等）向けに残す）。
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
) -> RecastRunContext:
    """既に計算済みの `RecastPlanArtifacts` から `RecastRunContext` を組み立てる
    （plan パイプラインの再計算をしない、`recast run` CLI の主経路）。

    `profile`/`identity_source_locator`/`identity_source_sha256` は
    `artifacts`（plan 段の single-read 束）からそのまま複製する — 呼び出し側が
    別途 `load_backend_capability_profile`/identity manifest 読み込みを行って
    独自の値を渡す経路はもう存在しない（Codex P2 review round 12, PR3 #208
    指摘24/26: 再 read すると plan 段の診断と実際に invoke/注文書へ使われる
    値が実行中の入力変化で乖離し得た）。
    """
    if artifacts.compiled is None or artifacts.derived_score is None:
        raise RecastError(
            f"recast run: {variant}@{backend} did not reach a compiled performance "
            f"package (state_reached={artifacts.result.plan.state_reached!r}); resolve "
            "the blocking diagnostics reported by 'recast plan' first"
        )
    # `compiled`/`derived_score` が非 None ならば、plan.py の同一 finalize 呼び出し
    # （step 6 の profile 解決 → step 7 の compile → 最終 success return）が
    # 必ずこれらも設定済み（plan.py 側の不変条件 — 別途 None チェックはしない）。
    assert artifacts.profile is not None
    assert artifacts.identity_source_locator is not None
    assert artifacts.identity_source_sha256 is not None
    return RecastRunContext(
        loaded=loaded,
        variant=variant,
        backend=backend,
        backend_ref=artifacts.backend_ref,
        profile=artifacts.profile,
        plan_result=artifacts.result,
        compiled=artifacts.compiled,
        derived_score=artifacts.derived_score,
        channel_artifact_bytes=artifacts.channel_artifact_bytes,
        identity_source_locator=artifacts.identity_source_locator,
        identity_source_sha256=artifacts.identity_source_sha256,
    )


def build_recast_run_context(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> RecastRunContext:
    """`build_recast_plan_artifacts` を 1 回だけ呼び、`RecastRunContext` を組み立てる
    利便関数（テスト・非 CLI 呼び出し向け）。`recast run` CLI は plan 診断表示を
    共有するため `run_context_from_plan_artifacts` を直接使う。`publish=True`
    で呼ぶ — CLI の `recast run` と同じく package/report を builds_root へ
    永続公開する経路を再現する（PR3 #208 指摘19: `build_recast_plan_
    artifacts` は `publish` が必須引数になった）。"""
    artifacts = build_recast_plan_artifacts(
        loaded, variant=variant, backend=backend, publish=True
    )
    return run_context_from_plan_artifacts(
        loaded, variant=variant, backend=backend, artifacts=artifacts
    )


# `resolve_order_dir`/`resolve_takes_dir` は `recast/run_paths.py`（PR3 #208
# 指摘 3: packages/ の永続公開先追加に伴い single source を切り出した）へ委譲する
# 薄いエイリアス。既存呼び出し元（本モジュール内・テスト）の名前は変えない。
resolve_order_dir = resolve_orders_dir


def base_prepared_invocation(ctx: RecastRunContext) -> PreparedInvocation:
    """3 つの具象 invoker（manual/deterministic/musicgen）が共有する
    `PreparedInvocation` の共通フィールド組み立て（order_dir/takes_dir の解決 +
    package/derived_score/sha256 pin + identity source 参照 +
    `protected_input_paths`）。

    identity source 参照（`identity_source_locator`/`sha256`）と
    `protected_input_paths` はどちらも `ctx`（`RecastPlanArtifacts` 由来の
    `RecastRunContext`）からそのまま複製する — `identity.yaml` の再 read/
    再 parse は一切しない（Codex P2 review round 12, PR3 #208 指摘26: 従来は
    `read_identity_source(ctx.loaded)` と `collect_protected_input_paths
    (ctx.loaded, ...)` がそれぞれ独立に identity manifest を再 read/再 parse
    しており、plan 段が読んだ内容（`recast_plan.json` の診断が前提とする値）
    と実行中に乖離し得た。`ctx.plan_result.protected_inputs` は plan.py の
    single-read 束から副作用なく再構成済みの同じ集合 — `RecastPlanResult.
    protected_inputs` docstring 参照）。
    """
    order_dir = resolve_order_dir(ctx.loaded, ctx.variant, ctx.backend)
    takes_dir = resolve_takes_dir(ctx.loaded, ctx.variant, ctx.backend)
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
        identity_source_locator=ctx.identity_source_locator,
        identity_source_sha256=ctx.identity_source_sha256,
        protected_input_paths=ctx.plan_result.protected_inputs,
        channel_artifact_bytes=ctx.channel_artifact_bytes,
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """tempfile + `os.replace` による atomic publish（binary 版、単一ファイル）。
    `recast/state.py` の `_write_recast_state_atomically` と同型 — テキスト
    専用の `cli/builds_root.py:_publish_artifacts_atomically` は
    `str.encode('utf-8')` 前提のため音声 bytes には使えず、本関数を別途持つ。

    複数ファイルを「全部揃って初めて意味を持つ 1 組」として publish する場合
    （take 音声 + `take.json` provenance 等）は本関数を個別に 2 回呼ばず、
    `atomic_publish_bytes_bundle` を使うこと（Codex P2 review, PR3 #208
    指摘 1: 個別 publish だと 1 本目成功・2 本目失敗で provenance を伴わない
    半端な成果物が残り得る）。

    薄いラッパー — 実体は `svp_rpe.utils.atomic_io.atomic_write_bytes` へ集約済み。
    """
    from svp_rpe.utils import atomic_io

    atomic_io.atomic_write_bytes(path, data)


def atomic_publish_bytes_bundle(
    output_dir: Path,
    contents: dict[str, bytes],
    *,
    protected_inputs: list[Path],
    stale_filenames: tuple[str, ...] = (),
) -> None:
    """複数バイナリファイルを「全部揃って初めて意味を持つ 1 組」として atomic
    publish する（binary 版 `cli/builds_root.py:_publish_artifacts_atomically`
    — recast->cli の層依存を避けるため import せずここへ複製・binary 化した
    もの。ロールバック契約はそちらの docstring と同一）。

    `protected_inputs` は必須（デフォルト値なし — Codex P2 review, PR3 #208
    指摘 6/7: 呼び出し側が渡し忘れると衝突ガードが黙って無効になっていた。
    シグネチャで必須化することで渡し忘れを型/実行時に検出できるようにする）。
    `output_dir / filename` のいずれかがそれらのいずれかと一致する場合は
    何も書かずに `ValueError` を送出する（fail-closed — `_publish_artifacts_atomically`
    の同名パラメータと同じ契約。空リストを渡せば実質的にガード無効も明示的に
    選べる — が本モジュール内の全呼び出し元は非空リストを渡す）。

    Codex P2 review（PR3 #208 指摘 1）対応: `take-01.wav` を単独で最終名へ
    publish してから `take.json` を書くと、後者が失敗した時に provenance の
    無い音声だけが `takes_dir` に残ってしまう。`ManualInvoker.collect` /
    `DeterministicInvoker.invoke` は本関数で音声 + `take.json` を 1 組として
    publish し、途中失敗時は（ステージング中の失敗はもちろん、`os.replace`
    による最終 publish 中の失敗も）ロールバックして `output_dir` を呼び出し前
    と同じ状態に戻す。

    `stale_filenames`（Codex P2 review, PR #212 指摘）: `contents` には含め
    ないが、`output_dir` に存在すれば bundle publish の一部として除去する
    ファイル名の集合（`contents` と重複するキーは無視 — 上書き publish の
    通常経路に任せる）。既存の snapshot/rollback 機構をそのまま再利用する:
    存在すれば snapshot（`output_dir` から staging 側の `.prev` へ
    `os.replace`）するだけで新しい bytes を書き戻さない — 成功時は
    `TemporaryDirectory` のクリーンアップで snapshot ごと消え、失敗時は
    既存の rollback ループ（`snapshots` の全エントリを元位置へ復元）が
    `contents` 由来かどうかを区別せず一律に復元するため、追加のロールバック
    分岐は不要（`ManualInvoker.collect` が受理拡張子（wav/mp3）を跨いで
    take を再収蔵する際、旧拡張子の `take-01.<旧ext>` を新 `take-01.<新ext>`
    と同じ atomic 操作で除去する用途 — 除去前に `takes_dir` が「新
    take-01.<ext> + take.json のみ」の整合状態になることを、record_state
    より前に保証する）。

    薄いラッパー — 実体は `svp_rpe.utils.atomic_io.atomic_publish_bundle`
    へ集約済み（`except BaseException` での rollback・`protected_inputs` が
    truthy のときのみ検査する意味論はそちらのデフォルト挙動のまま）。
    """
    from svp_rpe.utils.atomic_io import atomic_publish_bundle

    atomic_publish_bundle(
        output_dir,
        contents,
        protected_inputs=protected_inputs,
        stale_filenames=stale_filenames,
    )


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
    protected_input_paths: list[Path]
    # `RecastRunContext.channel_artifact_bytes` の docstring 参照（Codex P2
    # review round 11, PR3 #208 指摘22）: `ManualInvoker.prepare()` の注文書
    # 描画がこの pin 済み bytes を使い、artifact ファイルを再 read しない。
    channel_artifact_bytes: Dict[str, bytes]


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
