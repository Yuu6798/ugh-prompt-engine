"""RecastPlan: 既存 sidecar 一式を突き合わせ、決定論的な状態機械診断を組み立てる。

`build_recast_plan` は `LoadedRecastProject`（PR1 の loader が解決した参照一式）+
`variant` + `backend` から、以下を 1 パイプラインとして評価する:

1. variant/backend 名の存在検証
2. CompositionScore ロード + author field (TODO sentinel) 未解決チェック
3. IdentityManifest ロード + artifact hash 照合
4. ArrangementSpec ロード + `resolve_arrangement`
5. `build_preservation_contract`
6. capability profile + mode_overrides（指定時）ロード
7. `build_performance_package`（純関数、strict= policy.capability_mode=="strict"）
8. `require_verified_package` なら一時ディレクトリで `verify_package`
9. 診断表（anchors / changed_fields）と推奨 1 行の構築
10. `recast.state.record_state` による状態永続化

各段の例外は診断へ変換し、最初のブロックで停止して到達状態を記録する
（`blocked_authoring` / `blocked_capability` / `blocked_verification`）。
`RecastPlan`（`recast-plan/0.1`）はタイムスタンプ・絶対パスを含まない
決定論的な JSON 互換 pydantic モデル — checkout が同じなら常に同じ bytes になる。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import ValidationError

from svp_rpe.arrange.capabilities import (
    INPUT_CHANNELS,
    InputCapabilityProfile,
    _validate_evidence_form,
)
from svp_rpe.arrange.contract import (
    PreservationContractError,
    build_preservation_contract,
)
from svp_rpe.arrange.identity import (
    AnchorDomain,
    IdentityAnchor,
    IdentityManifestError,
    parse_identity_manifest_with_artifacts,
)
from svp_rpe.arrange.models import ArrangementChange, ArrangementSpec, PreservationMode
from svp_rpe.arrange.observe import (
    is_harmony_sensor_anchor,
    is_lyrics_sensor_anchor,
    is_melody_sensor_anchor,
    is_structure_sensor_anchor,
)
from svp_rpe.arrange.package import (
    COMPILATION_REPORT_FILENAME,
    PERFORMANCE_PACKAGE_FILENAME,
    DeliveryStatus,
    InputChannel,
    PackageCompilationError,
    PerformancePackage,
    build_performance_package,
    compute_derived_score_sha256,
    compute_preservation_contract_sha256,
    detect_compiler_git_commit,
    detect_compiler_package_version,
)
from svp_rpe.arrange.resolver import (
    ArrangementConflictError,
    ArrangementPolicyError,
    resolve_arrangement,
)
from svp_rpe.arrange.verify import verify_package
from svp_rpe.compose.device_profile import load_device_profile
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.compose.models import CompositionScore
from svp_rpe.compose.prompt_renderer import resolve_backend_descriptor
from svp_rpe.recast.loader import LoadedRecastProject, load_mode_overrides
from svp_rpe.recast.models import (
    BackendRef,
    CapabilityMode,
    InvocationKind,
    InvocationMode,
    ModeOverridesConfig,
    OverrideSupport,
    RecastError,
    RecastModel,
    RecastProject,
)
from svp_rpe.recast.state import RecastState, record_state
from svp_rpe.sentinels import is_todo_sentinel

PreservationChangeMode = Literal["elastic", "free"]

RECAST_PLAN_SCHEMA_VERSION = "recast-plan/0.1"
RECAST_PLAN_FILENAME = "recast_plan.json"


class BlockedInfo(RecastModel):
    """ブロック状態に到達したときの詳細。"""

    state: RecastState
    reasons: List[str]


class AnchorPlanEntry(RecastModel):
    """1 identity anchor 分の診断（`package.anchor_statuses` + manifest から）。"""

    anchor_id: str
    domain: AnchorDomain
    policy_mode: Optional[PreservationMode] = None
    delivery: DeliveryStatus
    channel: Optional[InputChannel] = None
    sensor_available: bool


class ChangedFieldPlanEntry(RecastModel):
    """1 変更 canonical path 分の診断（`resolution.changes` + mode_overrides から）。"""

    path: str
    preservation_mode: PreservationChangeMode
    mode_support: OverrideSupport
    note: Optional[str] = None


class RecastPlan(RecastModel):
    """`svprpe recast plan` の決定論的出力（recast-plan/0.1）。

    タイムスタンプ・絶対パスを一切含まない — 同一 checkout + 同一 variant/backend
    であれば常に同じ JSON bytes になる（snapshot 比較の前提）。
    """

    schema_version: Literal["recast-plan/0.1"] = RECAST_PLAN_SCHEMA_VERSION
    project_id: str
    variant: str
    backend: str
    invocation: InvocationKind
    invocation_mode: InvocationMode
    capability_mode: CapabilityMode
    state_reached: RecastState
    blocked: Optional[BlockedInfo] = None
    anchors: List[AnchorPlanEntry]
    changed_fields: List[ChangedFieldPlanEntry]
    warnings: List[str]
    recommendation: str


@dataclass(frozen=True)
class RecastPlanResult:
    """`build_recast_plan` の戻り値: 決定論的 `plan` + human 向けテキスト。"""

    plan: RecastPlan
    text: str


def _parse_yaml_mapping(raw_bytes: bytes, label: str, path_str: str) -> Dict[str, Any]:
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping: {path_str}")
    return data


def _unresolved_author_field_paths(score: CompositionScore) -> List[str]:
    """semantic 層（core / grv / delta_e / avoid / lyrics_presence）を走査し、
    `sentinels.is_todo_sentinel` に該当する値が残っている canonical path 一覧を返す。
    """
    semantic = score.semantic
    unresolved: List[str] = []
    if is_todo_sentinel(semantic.core):
        unresolved.append("semantic.core")
    if is_todo_sentinel(semantic.grv.primary):
        unresolved.append("semantic.grv.primary")
    if is_todo_sentinel(semantic.grv.secondary):
        unresolved.append("semantic.grv.secondary")
    if is_todo_sentinel(semantic.delta_e.overall):
        unresolved.append("semantic.delta_e.overall")
    for index, item in enumerate(semantic.avoid):
        if is_todo_sentinel(item):
            unresolved.append(f"semantic.avoid[{index}]")
    if is_todo_sentinel(semantic.lyrics_presence):
        unresolved.append("semantic.lyrics_presence")
    return unresolved


def _sensor_available(anchor: IdentityAnchor) -> bool:
    """`observe.py` の sensor routing 述語を使い、実測どおりに正直な値を返す
    （推測補完しない — 述語が False を返す domain は素直に False）。"""
    return (
        is_harmony_sensor_anchor(anchor)
        or is_structure_sensor_anchor(anchor)
        or is_lyrics_sensor_anchor(anchor)
        or is_melody_sensor_anchor(anchor)
    )


def _build_anchor_entries(
    package: PerformancePackage, manifest_by_id: Dict[str, IdentityAnchor]
) -> List[AnchorPlanEntry]:
    entries: List[AnchorPlanEntry] = []
    for status in package.anchor_statuses:
        manifest_anchor = manifest_by_id[status.anchor_id]
        entries.append(
            AnchorPlanEntry(
                anchor_id=status.anchor_id,
                domain=manifest_anchor.domain,
                policy_mode=status.requested_mode,
                delivery=status.delivery.status,
                channel=status.delivery.channel,
                sensor_available=_sensor_available(manifest_anchor),
            )
        )
    return entries


def mode_support_for_path(
    path: str,
    invocation_mode: InvocationMode,
    mode_overrides: Optional[ModeOverridesConfig],
) -> OverrideSupport:
    """`mode_overrides` の `invocation_mode` 側エントリから `path` の support を引く。
    設定が無い/該当エントリが無い場合は `"unknown"`（存在するか未確認、の意味論を
    `ChannelSupport`/`OverrideSupport` の既存規約どおり保つ）。"""
    if mode_overrides is None:
        return "unknown"
    entries = mode_overrides.modes.get(invocation_mode)
    if entries is None:
        return "unknown"
    entry = entries.get(path)
    if entry is None:
        return "unknown"
    return entry.support


def _mode_override_note(
    path: str,
    invocation_mode: InvocationMode,
    mode_overrides: Optional[ModeOverridesConfig],
) -> Optional[str]:
    if mode_overrides is None:
        return None
    entries = mode_overrides.modes.get(invocation_mode)
    if entries is None:
        return None
    entry = entries.get(path)
    if entry is None:
        return None
    return entry.note


def _build_changed_field_entries(
    changes: List[ArrangementChange],
    invocation_mode: InvocationMode,
    mode_overrides: Optional[ModeOverridesConfig],
) -> List[ChangedFieldPlanEntry]:
    return [
        ChangedFieldPlanEntry(
            path=change.path,
            preservation_mode=change.preservation_mode,
            mode_support=mode_support_for_path(change.path, invocation_mode, mode_overrides),
            note=_mode_override_note(change.path, invocation_mode, mode_overrides),
        )
        for change in changes
    ]


def _build_recommendation(
    blocked: Optional[BlockedInfo],
    state_reached: RecastState,
    changed_fields: List[ChangedFieldPlanEntry],
) -> str:
    if blocked is not None:
        if blocked.state == "blocked_capability":
            return (
                "capability_mode を advisory へ切替するか、hard 宣言した anchor の "
                "要求を降格してください。"
            )
        if blocked.state == "blocked_authoring":
            return "未解決の author field / preservation 契約違反を解消してから再実行してください。"
        if blocked.state == "blocked_verification":
            return "identity manifest / package artifact の整合性を確認してから再実行してください。"
        return "ブロックを解消してから再実行してください。"

    unsupported_paths = [c.path for c in changed_fields if c.mode_support == "unsupported"]
    if unsupported_paths:
        return (
            "この invocation_mode ではこの変更は届きません: "
            f"{', '.join(unsupported_paths)}（cover/prompt_only の切替を検討してください）。"
        )
    if state_reached == "verified":
        return "run へ進行可。"
    return "次の状態（compile/verify）へ進めてください。"


def _render_text(plan: RecastPlan) -> str:
    lines = [
        f"recast plan: {plan.project_id} / {plan.variant}@{plan.backend}",
        f"invocation: {plan.invocation} ({plan.invocation_mode}) / "
        f"capability_mode: {plan.capability_mode}",
        f"state_reached: {plan.state_reached}",
    ]
    if plan.blocked is not None:
        lines.append(f"blocked: {plan.blocked.state}")
        for reason in plan.blocked.reasons:
            lines.append(f"  - {reason}")
    if plan.anchors:
        lines.append("anchors:")
        for anchor in plan.anchors:
            lines.append(
                f"  - {anchor.anchor_id} [{anchor.domain}] "
                f"policy={anchor.policy_mode or '-'} delivery={anchor.delivery} "
                f"channel={anchor.channel or '-'} sensor_available={anchor.sensor_available}"
            )
    if plan.changed_fields:
        lines.append("changed_fields:")
        for change in plan.changed_fields:
            lines.append(
                f"  - {change.path} mode={change.preservation_mode} "
                f"mode_support={change.mode_support}"
            )
    if plan.warnings:
        lines.append("warnings:")
        for warning in plan.warnings:
            lines.append(f"  - {warning}")
    lines.append(f"recommendation: {plan.recommendation}")
    return "\n".join(lines) + "\n"


def build_recast_plan(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> RecastPlanResult:
    """`loaded` の (variant, backend) 組に対する RecastPlan を構築し、state を永続化する。"""
    project: RecastProject = loaded.project

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

    backend_ref: BackendRef = project.backends[backend]

    def _finalize(
        *,
        state_reached: RecastState,
        blocked: Optional[BlockedInfo] = None,
        anchors: Optional[List[AnchorPlanEntry]] = None,
        changed_fields: Optional[List[ChangedFieldPlanEntry]] = None,
        warnings: Optional[List[str]] = None,
    ) -> RecastPlanResult:
        resolved_changed_fields = changed_fields or []
        recommendation = _build_recommendation(blocked, state_reached, resolved_changed_fields)
        plan = RecastPlan(
            project_id=project.project.id,
            variant=variant,
            backend=backend,
            invocation=backend_ref.invocation,
            invocation_mode=backend_ref.invocation_mode,
            capability_mode=project.policy.capability_mode,
            state_reached=state_reached,
            blocked=blocked,
            anchors=anchors or [],
            changed_fields=resolved_changed_fields,
            warnings=warnings or [],
            recommendation=recommendation,
        )
        note = "; ".join(blocked.reasons) if blocked is not None else None
        record_state(loaded.project_dir, variant, backend, state_reached, note)
        return RecastPlanResult(plan=plan, text=_render_text(plan))

    # --- step 2: score + author field resolution ---------------------------
    score = load_composition_score(loaded.score_path)
    if project.policy.require_author_fields_resolved:
        unresolved = _unresolved_author_field_paths(score)
        if unresolved:
            return _finalize(
                state_reached="blocked_authoring",
                blocked=BlockedInfo(
                    state="blocked_authoring",
                    reasons=[
                        f"unresolved TODO(transcribe) sentinel at {path}" for path in unresolved
                    ],
                ),
            )

    # --- step 3: identity manifest -----------------------------------------
    manifest_path = loaded.identity_manifest_path
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        return _finalize(
            state_reached="blocked_verification",
            blocked=BlockedInfo(state="blocked_verification", reasons=[str(exc)]),
        )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        manifest, _artifact_bytes = parse_identity_manifest_with_artifacts(
            manifest_bytes, manifest_path, collect=None
        )
    except (IdentityManifestError, ValueError, ValidationError, yaml.YAMLError) as exc:
        return _finalize(
            state_reached="blocked_verification",
            blocked=BlockedInfo(state="blocked_verification", reasons=[str(exc)]),
        )
    manifest_by_id = {anchor.id: anchor for anchor in manifest.anchors}

    # --- step 4+5: arrangement resolve + preservation contract -------------
    arrangement_path = loaded.arrangement_paths[variant]
    try:
        spec_bytes = arrangement_path.read_bytes()
    except OSError as exc:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(exc)]),
        )
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()
    try:
        spec_data = _parse_yaml_mapping(spec_bytes, "arrangement spec", str(arrangement_path))
        spec = ArrangementSpec.model_validate(spec_data)
        resolution = resolve_arrangement(score, spec)
        contract = build_preservation_contract(
            manifest, spec, manifest_sha256=manifest_sha256, spec_sha256=spec_sha256
        )
    except (
        ValueError,
        ValidationError,
        yaml.YAMLError,
        ArrangementConflictError,
        ArrangementPolicyError,
        PreservationContractError,
    ) as exc:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(exc)]),
        )

    # --- step 6: capability profile + mode overrides ------------------------
    profile_path = loaded.capability_profile_paths[backend]
    profile_bytes = profile_path.read_bytes()
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    profile_data = _parse_yaml_mapping(
        profile_bytes, "input capability profile", str(profile_path)
    )
    profile = InputCapabilityProfile.model_validate(profile_data)
    for channel_name in INPUT_CHANNELS:
        capability = getattr(profile.input_channels, channel_name)
        if capability.evidence is not None:
            _validate_evidence_form(
                capability.evidence, generator=profile.generator, channel=channel_name
            )

    mode_overrides_config: Optional[ModeOverridesConfig] = None
    mode_override_path = loaded.mode_override_paths.get(backend)
    if mode_override_path is not None:
        mode_overrides_config = load_mode_overrides(mode_override_path)
        if mode_overrides_config.generator != profile.generator:
            raise RecastError(
                f"recast project '{project.project.id}': backend {backend!r} mode_overrides "
                f"generator {mode_overrides_config.generator!r} does not match capability "
                f"profile generator {profile.generator!r}"
            )

    # --- step 7: build performance package ----------------------------------
    contract_sha256 = compute_preservation_contract_sha256(contract)
    derived_score_sha256 = compute_derived_score_sha256(resolution.derived_score)
    render_generator = resolve_backend_descriptor(
        resolution.derived_score.rendering.target_backend
    ).profile_key
    device_profile = load_device_profile(render_generator)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        package_dir = Path(tmp_dir_str)
        try:
            artifact_base_locator = Path(
                os.path.relpath(manifest_path.resolve().parent, package_dir.resolve())
            ).as_posix()
        except ValueError as exc:
            return _finalize(
                state_reached="blocked_capability",
                blocked=BlockedInfo(state="blocked_capability", reasons=[str(exc)]),
            )

        try:
            compiled = build_performance_package(
                manifest,
                contract,
                profile,
                resolution.derived_score,
                device_profile=device_profile,
                artifact_base_locator=artifact_base_locator,
                manifest_sha256=manifest_sha256,
                contract_sha256=contract_sha256,
                profile_sha256=profile_sha256,
                derived_score_sha256=derived_score_sha256,
                strict=(project.policy.capability_mode == "strict"),
                compiler_package_version=detect_compiler_package_version(),
                compiler_git_commit=detect_compiler_git_commit(),
            )
        except (PackageCompilationError, ValidationError) as exc:
            return _finalize(
                state_reached="blocked_capability",
                blocked=BlockedInfo(state="blocked_capability", reasons=[str(exc)]),
            )

        warnings = list(compiled.report.warnings)

        # --- step 8: optional verification ----------------------------------
        if project.policy.require_verified_package:
            (package_dir / PERFORMANCE_PACKAGE_FILENAME).write_text(
                compiled.package_json, encoding="utf-8"
            )
            (package_dir / COMPILATION_REPORT_FILENAME).write_text(
                compiled.report_json, encoding="utf-8"
            )
            verify_report = verify_package(package_dir / PERFORMANCE_PACKAGE_FILENAME, manifest_path)
            if not verify_report.ok:
                reasons = [
                    f"{check.group} {check.label}: {check.detail}"
                    for check in verify_report.failures
                ]
                return _finalize(
                    state_reached="blocked_verification",
                    blocked=BlockedInfo(state="blocked_verification", reasons=reasons),
                )
            state_reached: RecastState = "verified"
        else:
            state_reached = "compiled"

        # --- step 9: diagnostics tables ---------------------------------------
        anchors = _build_anchor_entries(compiled.package, manifest_by_id)
        changed_fields = _build_changed_field_entries(
            resolution.changes, backend_ref.invocation_mode, mode_overrides_config
        )

        return _finalize(
            state_reached=state_reached,
            blocked=None,
            anchors=anchors,
            changed_fields=changed_fields,
            warnings=warnings,
        )
