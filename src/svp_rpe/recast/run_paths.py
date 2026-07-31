"""builds_root 配下の per-(variant, backend) 出力ディレクトリ解決 + 衝突ガード。

`recast/plan.py`（package の永続公開）と `recast/backend.py`（orders/takes の
公開）が共有する single source — ディレクトリ命名規約がこの 2 箇所で drift
しないようにする（Codex P2 review, PR3 #208 指摘 3 対応: package の公開先が
`builds_root/packages/<variant>@<backend>/` になったことで、3 種の出力
ディレクトリ（orders/takes/packages）の命名規約を 1 箇所に集約する必要が
生じた）。

`collect_protected_input_paths` もここに置く（`plan.py`/`backend.py` 双方が
呼ぶ必要があり、`backend.py` は `plan.py` を import するため `backend.py` 側に
置くと `plan.py` → `backend.py` の逆方向 import で循環する — 両者が安全に
import できる本モジュールへ集約する、PR3 #208 指摘 6/7 対応）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from pydantic import ValidationError

from svp_rpe.arrange.identity import IdentityManifest, IdentityManifestError
from svp_rpe.arrange.pathsafe import resolve_confined
from svp_rpe.recast.experimental import resolve_melody_observation_paths_for_protection
from svp_rpe.recast.loader import LoadedRecastProject
from svp_rpe.recast.models import RecastError


def run_key(variant: str, backend: str) -> str:
    return f"{variant}@{backend}"


def resolve_orders_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """manual invoker が注文書 6 ファイルを公開する先。"""
    return loaded.builds_root / "orders" / run_key(variant, backend)


def resolve_takes_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """生成済みテイクの収蔵先（manual collect / local invoke 共通）。"""
    return loaded.builds_root / "takes" / run_key(variant, backend)


def resolve_packages_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """`build_recast_plan_artifacts` が compile 済み `PerformancePackage` /
    `CompilationReport` を公開する先（PR3 #208 指摘 3: tempfile 幾何依存を
    排し、`artifact_base_locator` を checkout-stable にするための永続公開先）。"""
    return loaded.builds_root / "packages" / run_key(variant, backend)


def resolve_reports_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """PR5: `recast ingest` の observe→report 段が `recast_report.json` /
    `recast_summary.md` を公開する先（packages/orders/takes と同じ命名規約
    — この 3 種に加える 4 種目の per-(variant, backend) 出力ディレクトリ）。"""
    return loaded.builds_root / "reports" / run_key(variant, backend)


def resolve_plans_dir(loaded: LoadedRecastProject, variant: str, backend: str) -> Path:
    """`recast plan`/`run`/`ingest` が正典の `recast_plan.json` を公開する先
    （packages/orders/takes/reports と同じ per-(variant, backend) 命名規約
    — この 4 種に加える 5 種目の per-(variant, backend) 出力ディレクトリ）。

    Codex P2 review, PR #212 指摘: 従来 `recast_plan.json` は project 直下の
    単一ファイルで、別の (variant, backend) に対する `recast plan`/`run` が
    同じファイルを上書きしていた。その結果、ある run が `awaiting_generation`
    のまま別 run の plan を実行しただけで、前者の `plan_sha256` pin が
    「現在の recast_plan.json」と一致しなくなり、`recast ingest` が正当な
    外部生成 take を stale 拒否していた。plan 成果物を他の 4 種と同じ
    per-(variant, backend) ディレクトリへ公開することで、`plan_sha256` の
    記録・`recast status`/`recast ingest` の突合はこの正典ファイルへ向ける
    — project 直下の `recast_plan.json` は「最新診断の便宜コピー」として
    書き続けるが、pin・突合対象からは外す（`cli/recast_cmd.py:
    _publish_recast_plan` docstring 参照）。"""
    return loaded.builds_root / "plans" / run_key(variant, backend)


def collect_protected_input_paths(
    loaded: LoadedRecastProject, variant: str, backend: str
) -> list[Path]:
    """`(variant, backend)` run が参照する外部入力一式（project.yaml 自身 /
    score / identity manifest / manifest が参照する source・各 anchor
    artifact / arrangement spec / capability_profile / mode_overrides）を
    集める。

    recast の**全公開サイト**（orders 公開・manual collect の takes 公開・
    deterministic invoke の takes 公開・plan の packages 公開）が publish 前に
    これを渡し、出力ファイルパスがこれらのいずれとも衝突しないことを保証する
    共通規約（Codex P2 review, PR3 #208 指摘 2/6/7: 従来は orders 公開にしか
    適用されておらず、takes/packages 公開は無防備だった）。

    意図的に他ステージの出力（orders/takes/packages 自身のファイル）は含めない
    — 含めると、例えば packages 公開サイト自身がここを呼んで自分の出力先を
    チェックすると常に「自分自身と衝突している」と誤検出してしまう（自己参照
    パラドックス）。本関数が返すのはあくまで「recast の外側から来る入力」の
    集合であり、公開先ディレクトリが異なる限り（orders/takes/packages は
    `run_paths.py` の命名規約により常に別ディレクトリ）他ステージの出力との
    衝突はディレクトリ構造上そもそも起こらない。

    identity manifest 自体は無条件で対象パスに含めるが、その中身の
    parse/validate（YAML 破損・schema 不正・source/anchor artifact の解決）が
    失敗した場合は例外を送出せず、manifest が参照する source/anchor artifact
    パスの追加だけを諦めて続行する（Codex P2 review round 7, PR3 #208 指摘 13:
    本関数は `build_recast_plan_artifacts` が既に blocked_verification として
    診断・finalize 済みの入力を、publish 直前の衝突ガードとして**再 parse**
    する呼ばれ方をする — ここで例外を送出すると「blocked plan でも publish
    される」契約が崩れ、CLI が捕捉しない例外で top-level Error に落ちる。
    破損 manifest が参照する artifact パスは特定できないため衝突ガードの
    対象に含められないが、それは診断側の blocked_verification が既に
    「この manifest は信頼できない」と報告済みであり、この関数の責務ではない）。

    未知の `variant`/`backend`（Codex P2 review round 14, PR5 #210 指摘19,
    defense in depth）: `loaded.arrangement_paths[variant]`/`loaded.
    capability_profile_paths[backend]` は元々 dict 添字で、typo を含む
    未知名では生 `KeyError` を送出していた。呼び出し側（CLI）は
    `_validate_variant_backend_declared` で preflight 前に typo を拒否する
    ようになったが、本関数自身も CLI を経由しない将来の呼び出し元
    （プログラム的呼び出し等）向けに、同じ検証を actionable な
    `RecastError` として重ねて行う（メッセージは `recast/plan.py` の
    `build_recast_plan_artifacts` と同一文言）。
    """
    project = loaded.project
    if variant not in project.variants:
        raise RecastError(
            f"recast project '{project.project.id}': unknown variant {variant!r} "
            f"(declared: {sorted(project.variants)})"
        )
    if backend not in project.backends:
        raise RecastError(
            f"recast project '{project.project.id}': unknown backend {backend!r} "
            f"(declared: {sorted(project.backends)})"
        )

    paths: list[Path] = [
        loaded.path,
        loaded.score_path,
        loaded.identity_manifest_path,
        loaded.arrangement_paths[variant],
        loaded.capability_profile_paths[backend],
    ]
    mode_override_path = loaded.mode_override_paths.get(backend)
    if mode_override_path is not None:
        paths.append(mode_override_path)

    # R1-5 (Codex round1 P2 対応): `observation.melody` の resolved パス 3 種
    # （comparison_registry/m1_registry/reference_audio）を protected-input
    # set へ編入する——これらが recast 出力（`recast_state.json`/`recast_
    # plan.json`/report 等）と alias していても、従来はここに含まれず publish
    # が入力を上書きしうった。
    #
    # R6-1 (Codex round6 P2 対応・all-or-nothing 排除): 3 パスは
    # `resolve_melody_observation_paths_for_protection` で**それぞれ独立に**
    # 「封じ込め検証のみ」で解決する——旧実装は 1 回の `resolve_melody_
    # observation_paths` 呼び出しが 1 パスの封じ込め違反/未存在で
    # ``RecastError`` を送出すると、`except RecastError: pass` が 3 パス
    # 全ての保護を諦めていた（例: 有効な M3 registry が report と alias +
    # audio 欠落 → 評価は `author_input_missing` で正しく進むのに registry が
    # protected_inputs に入らず publish に上書きされ得た）。本関数は
    # non-None のパスだけを足すため、1 パスの違反が他パスの保護を道連れに
    # しない（実際の melody エラー報告は `recast/experimental.py` の評価
    # 経路が正規の位置で行う）。
    #
    # R8-1 (Codex round8 P2 対応・disabled 対称性の完成、`recast/plan.py`
    # `build_recast_plan_artifacts` の鏡像ブロックと同一対応): `observation.
    # enabled is False` のときはこのブロック自体をスキップする。観測無効時
    # melody locator は入力として読まれないため保護不要——従来は
    # `observation.enabled` を見ずに常に解決・追加していたため、dormant な
    # locator が生成物パス（例: `performance_package.json`）を alias して
    # いると、観測を一切行わない project でも publish が拒否されていた。
    melody_config = loaded.project.observation.melody
    if loaded.project.observation.enabled and melody_config is not None:
        m3_path, m1_path, reference_audio_path = resolve_melody_observation_paths_for_protection(
            project_dir=loaded.project_dir, melody_config=melody_config
        )
        if m3_path is not None:
            paths.append(m3_path)
        if m1_path is not None:
            paths.append(m1_path)
        if reference_audio_path is not None:
            paths.append(reference_audio_path)

    manifest_dir = loaded.identity_manifest_path.resolve().parent
    try:
        manifest_bytes = loaded.identity_manifest_path.read_bytes()
        manifest_data = yaml.safe_load(manifest_bytes)
        if not isinstance(manifest_data, dict):
            raise RecastError(
                f"identity manifest must be a mapping: {loaded.identity_manifest_path}"
            )
        manifest = IdentityManifest.model_validate(manifest_data)
        paths.append(resolve_confined(manifest.source.locator, manifest_dir))
        for anchor in manifest.anchors:
            paths.append(resolve_confined(anchor.artifact, manifest_dir))
    except (OSError, RecastError, IdentityManifestError, ValidationError, ValueError, yaml.YAMLError):
        return paths

    return paths
