"""RecastPlan: 既存 sidecar 一式を突き合わせ、決定論的な状態機械診断を組み立てる。

`build_recast_plan` は `LoadedRecastProject`（PR1 の loader が解決した参照一式）+
`variant` + `backend` から、以下を 1 パイプラインとして評価する:

0. single-read 束: 入力一式（project は loader 済み sha を再利用/score/
   identity_manifest/arrangement spec/capability profile/mode_overrides/
   device profile）を **各 1 回だけ `read_bytes()`** し、その同一 bytes から
   `inputs_digest`（`arrange.bundle.compute_content_digest` 流用）の hash 計算と
   parse/resolve/compile の両方を行う（Codex P2 sixth round #207: 別経路で
   digest 用に再読込していた旧実装は、実行中の入力差し替え A→B→A で「B で
   compile した plan を A の digest で pin」してしまう TOCTOU があった）。
   identity_manifest **自身**の bytes に加え、それが参照する source artifact /
   各 anchor artifact の宣言 sha も折り込む（`parse_identity_manifest_with_artifacts`
   が同一 read で検証済み — manifest.yaml 自体は無変更のまま参照先だけが
   書き換わる/破損するケースの検出）。device profile は
   `resolve_arrangement` の結果（`rendering.target_backend`）に依存するため、
   この束の中で score/arrangement spec を先に parse/resolve してから読む
1. variant/backend 名の存在検証
2. author field (TODO sentinel) 未解決チェック
   （semantic 層限定ではなく `model_dump()` 全体を再帰走査する fail-closed ゲート）
3. IdentityManifest の artifact hash 照合結果（束で検証済み）を判定
4. ArrangementSpec の `resolve_arrangement` 結果（束で解決済み）を判定 +
   `build_preservation_contract`
5. （4 に統合）
6. capability profile + mode_overrides（指定時）の parse 結果（束で解決済み）を判定
7. `build_performance_package`（純関数、strict= policy.capability_mode=="strict"）
8. `require_verified_package` なら一時ディレクトリで `verify_package`
9. 診断表（anchors / changed_fields）の構築。`changed_fields` に
   `mode_support=="unsupported"` が 1 件でも、または backend が
   mode_overrides を宣言している場合に限り `mode_support=="unknown"`
   （未実測）が 1 件でもあれば、anchor 配送と同じ strict/advisory 意味論を
   適用する（`_mode_gate_reasons`）: strict は `blocked_capability` へ降格、
   advisory は到達状態を維持したまま warnings へ積む（推奨 1 行も切替）

各段の**判定・報告順序**（先行ステップの失敗が後続ステップの失敗より優先して
報告される規律）は 0 の read 前倒し後も変えない — 束の構築時点では成功時
オブジェクト/失敗時例外を保持するだけに留め、実際に `blocked_*` を返す/
例外を送出する判定は元のステップ位置のまま行う。

**本関数はディスクへ副作用を持たない純粋関数** — `recast_plan.json` の publish と
`recast.state.record_state` による状態永続化は呼び出し側（CLI）の責務であり、
「plan JSON の書き込み成功後に state を記録する」順序も CLI 側が保証する
（書き込み失敗時に stale state を残さないため）。

各段の例外は診断へ変換し、最初のブロックで停止して到達状態を記録する
（`blocked_authoring` / `blocked_capability` / `blocked_verification`）。
`RecastPlan`（`recast-plan/0.1`）はタイムスタンプ・絶対パスを含まない
決定論的な JSON 互換 pydantic モデル — checkout が同じなら常に同じ bytes になる。
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import ValidationError

from svp_rpe.arrange.bundle import compute_content_digest, sha256_file
from svp_rpe.arrange.capabilities import (
    INPUT_CHANNELS,
    InputCapabilityError,
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
from svp_rpe.arrange.pathsafe import resolve_confined
from svp_rpe.arrange.observe import (
    is_harmony_sensor_anchor,
    is_lyrics_sensor_anchor,
    is_melody_sensor_anchor,
    is_structure_sensor_anchor,
)
from svp_rpe.arrange.package import (
    COMPILATION_REPORT_FILENAME,
    PERFORMANCE_PACKAGE_FILENAME,
    CompiledPerformancePackage,
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
from svp_rpe.compose.device_profile import DeviceProfile
from svp_rpe.compose.models import CompositionScore
from svp_rpe.compose.prompt_renderer import resolve_backend_descriptor
from svp_rpe.recast.loader import LoadedRecastProject
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
from svp_rpe.recast.run_paths import resolve_packages_dir
from svp_rpe.recast.state import RecastState
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.utils.config_loader import resolve_config_bytes

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
    """`build_recast_plan` の戻り値: 決定論的 `plan` + human 向けテキスト +
    state 永続化用 `inputs_digest` + `mode_gate_reasons`（呼び出し側が
    `recast.state.record_state` へ渡す。いずれも `plan` 自体には含めない —
    `recast_plan.json` の byte 互換を変えないため）。

    `mode_gate_reasons`: strict/advisory ゲート（`_mode_gate_reasons`）が
    changed_fields から導出した「届かない/未実測」診断の確定リスト
    （`blocked=None` の場合のみ意味を持つ — blocked の場合は
    `plan.blocked.reasons` が同じ情報を既に保持する）。CLI と単体テストが
    state note を組み立てる際、ゲート判定ロジックを再実装せずここから
    そのまま読む single source。

    `protected_inputs`: この `(variant, backend)` run が参照する外部入力
    パス一式（`recast/run_paths.py:collect_protected_input_paths` と同じ
    集合）を、single-read 束が既に読んだ/保持しているオブジェクトから
    副作用なく再構成したもの（Codex P2 review round 7, PR3 #208 指摘 13:
    CLI 側の publish-前ガード — `recast_plan.json`/`recast_state.json` 公開
    サイト — が独立に `collect_protected_input_paths` を呼んで identity
    manifest を**再 parse**すると、blocked_verification（manifest 破損）
    のケースで publish 前ガードそのものが例外を送出し、「blocked でも plan
    は公開される」契約を壊して CLI top-level Error に落ちる。ここで束の
    読み取り結果から再構成すれば追加の read/parse が一切発生せず、束が既に
    manifest parse 失敗を許容している以上ガードも同じ degrade（manifest が
    参照する source/anchor artifact は対象外だが、manifest ファイル自身は
    対象に含める）を継承する。"""

    plan: RecastPlan
    text: str
    inputs_digest: str
    mode_gate_reasons: List[str]
    protected_inputs: List[Path]


@dataclass(frozen=True)
class RecastPlanArtifacts:
    """`build_recast_plan_artifacts` の戻り値: 決定論的 `RecastPlanResult`（`result`）に
    加え、到達状態が `compiled`/`verified` のときのみ非 None になる compile 済み成果物
    一式（PR3 §「recast run」用）。

    PR3 の `recast/backend.py` はこれを再利用して `PreparedInvocation` を組み立てる —
    plan パイプラインを再計算せず、plan 段が既に構築した `CompiledPerformancePackage` /
    derived score / 各 sha256 pin をそのまま流用する（指示書「plan の再計算でなく
    plan 段の compile 結果を再利用できる形に plan.py を小さくリファクタしてよい」への
    対応）。`build_recast_plan`（既存公開 API）はこの `result` フィールドだけを返す
    薄いラッパーへ縮退し、返り値の型・JSON 出力は一切変更しない。
    """

    result: RecastPlanResult
    backend_ref: BackendRef
    compiled: Optional[CompiledPerformancePackage] = None
    derived_score: Optional[CompositionScore] = None
    manifest_sha256: Optional[str] = None
    contract_sha256: Optional[str] = None
    profile_sha256: Optional[str] = None
    derived_score_sha256: Optional[str] = None


def _parse_yaml_mapping(raw_bytes: bytes, label: str, path_str: str) -> Dict[str, Any]:
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping: {path_str}")
    return data


def _atomic_publish_text_bundle(
    output_dir: Path, contents: Dict[str, bytes], *, protected_inputs: List[Path]
) -> None:
    """複数ファイルを「全部揃って初めて意味を持つ 1 組」として atomic publish
    する（`recast/backend.py` の `atomic_publish_bytes_bundle` と同型 —
    `build_recast_plan_artifacts` が `resolve_packages_dir` へ
    `performance_package.json` + `compilation_report.json` を公開する際に使う。
    ロールバック契約はそちらの docstring と同一）。

    `contents` は呼び出し側が **1 回だけ** `.encode("utf-8")` した bytes を
    渡す契約（Codex P2 review round 6, PR3 #208 指摘 12: 従来は `str` を
    受け取り `Path.write_text(..., encoding="utf-8")` で書いていたため、
    Windows では既定の text-mode newline 変換で `"\n"` → `"\r\n"` に化ける。
    `package_sha256`（`arrange/package.py` が `compiled.package_json.encode
    ("utf-8")` から計算し `compilation_report.json` へ埋め込む pin）は変換
    前の bytes から計算済みのため、実際にディスクへ書かれた bytes と乖離し
    `verify_package` の V2 hash 突合が偽の `blocked_verification` を報告し
    得た。`write_bytes` は newline 変換を一切行わないため、呼び出し側が
    encode した bytes がそのままディスク上の bytes になる — pr2 abc2350 の
    `_write_recast_plan_atomically`/`_write_recast_state_atomically` bytes
    経路化と同じ原則をここにも適用する）。

    Codex P2 review, PR3 #208 指摘 8: 従来は 2 ファイルをそれぞれ独立
    `_atomic_write_text` で公開していたため、package.json の公開が成功した
    直後に report.json の書き込みが失敗/衝突すると、新 package + 古い report
    という half-updated な packages ディレクトリが残り得た。本関数は既存
    ターゲットを staging 側へ退避（snapshot）してから新ファイルを一括 rename
    し、途中失敗時は rename 済み分を削除 + snapshot を元位置へ復元すること
    で `output_dir` を呼び出し前と同じ状態に戻す — 公開は「全ファイル成功」
    か「1 つも変更なし」のいずれかにしかならない。

    `protected_inputs` は必須（デフォルト値なし — PR3 #208 指摘 7 と同じ理由）。
    衝突検査は staging へ書く**前**に全ターゲット分まとめて行う（一部だけ
    staging 済みのまま検査で弾かれる状態を避ける）。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if protected_inputs:
        resolved_inputs = {candidate.resolve() for candidate in protected_inputs}
        for filename in contents:
            target = output_dir / filename
            if target.resolve() in resolved_inputs:
                raise ValueError(f"output path collides with a protected input path: {target}")
            if target.is_dir():
                raise ValueError(f"output path is an existing directory: {target}")

    with tempfile.TemporaryDirectory(dir=output_dir) as staging:
        staging_dir = Path(staging)
        for filename, content in contents.items():
            (staging_dir / filename).write_bytes(content)

        snapshots: Dict[str, Path] = {}
        published: List[str] = []
        try:
            for filename in contents:
                target = output_dir / filename
                if target.exists():
                    previous = staging_dir / f"{filename}.prev"
                    os.replace(target, previous)
                    snapshots[filename] = previous
            for filename in contents:
                os.replace(staging_dir / filename, output_dir / filename)
                published.append(filename)
        except BaseException:
            # `except BaseException`（`except OSError` ではなく）: rename 中に
            # `KeyboardInterrupt`/`SystemExit` が飛んでも rollback を必ず通す
            # （Codex P2 review round 7, PR3 #208 指摘 14 — `backend.py:
            # atomic_publish_bytes_bundle` と同じ理由・同じ統一）。
            for filename in published:
                try:
                    os.unlink(output_dir / filename)
                except OSError:
                    pass
            for filename, previous in snapshots.items():
                try:
                    os.replace(previous, output_dir / filename)
                except OSError:
                    pass
            raise


# 診断文字列に残る絶対パスの残骸を検出する（`(?<![\w./-])` は "0/4" や
# "cover/prompt_only" のような path でない "/" 区切りを誤検出しないための
# 否定先読み — 直前が英数字/ドット/スラッシュ/ハイフンなら「/」始まりの
# トークンとして扱わない）。POSIX 絶対パスに加え、Windows ドライブレター
# 形式（`C:\...` / `C:/...`）と UNC パス（`\\server\share\...`）も対象に
# する（Codex P2 thirteenth round #207: 旧実装は POSIX の `/...` トークンしか
# 認識せず、Windows 実行時の絶対パスがマスクされずそのまま漏れていた）。
_ABSOLUTE_PATH_TOKEN_RE = re.compile(
    r"(?<![\w./-])/[^\s'\"]*"
    r"|(?<![\w:\\])[A-Za-z]:[\\/][^\s'\"]*"
    r"|(?<!\\)\\\\[^\s'\"]*"
)


def _normalize_diagnostic(text: str, project_dir: Path) -> str:
    """診断文字列（例外メッセージ由来）に含まれる解決済み絶対パスを project
    相対の locator へ正規化する（Codex P2 seventh round #207: blocked plan の
    reasons/warnings に `str(exc)` をそのまま埋め込んでいたため、実行マシンの
    絶対パス（`project_dir` 配下）が `recast_plan.json` という永続化物へ
    そのまま漏れ、成果物が checkout ごとに異なる機械依存 bytes になっていた
    — `RecastPlan` の「同一 checkout なら常に同じ bytes」契約に反する）。

    `project_dir` 配下の絶対パスは相対 locator（例 `identity/lyrics.txt`）へ
    置換する。project 外を指す絶対パス（通常は出ないはずだが、封じ込め
    エラーの escaped-to パス等の将来経路に備える）は安定したプレースホルダ
    `<external-path>` へ置換し、ローカル FS レイアウトの漏洩を fail-closed に
    防ぐ（中身は判読せず一律マスクする）。

    境界判定（Codex P2 eighth round #207）: `project_dir` は文字列 prefix
    一致だけでなく、直後が path 継続文字（英数字/`.`/`-`/`_`）でないことまで
    確認してから置換する。無境界な `str.replace(project_dir_str, ...)` だと
    `demo_project_evil` のような**文字列 prefix が同じだけの兄弟ディレクトリ**
    まで剥がしてしまい、`._evil/...` のような機械依存の断片が残る。
    `project_dir` 配下として認識できない絶対パスは兄弟ディレクトリごと
    `<external-path>` へ完全にマスクされる（`_ABSOLUTE_PATH_TOKEN_RE` 側）。

    Windows パス対応（Codex P2 thirteenth round #207）: Windows 実行時は
    `project_dir` 自身がバックスラッシュ区切りの文字列になる（`str(Path)` は
    OS ネイティブの区切り文字を使う）。project_dir 配下の絶対パスを検出する
    区切り文字は `/` と `\\` の両方を受理し、relativize した結果の相対
    locator は常に POSIX 区切り（`/`）へ正規化する（`recast_plan.json` の
    「同一 checkout なら常に同じ bytes」契約は OS を跨いでも成立させたい
    ため、区切り文字の違いを出力から消す）。"""
    project_dir_pattern = re.escape(str(project_dir))

    def _relativize(match: "re.Match[str]") -> str:
        # マッチした相対 locator 部分（project_dir + 区切り文字の直後から
        # 次の空白/引用符まで）だけを POSIX 区切りへ変換する — 診断文字列の
        # 他の部分（無関係なバックスラッシュを含みうる自由文）には触れない。
        return match.group(1).replace("\\", "/")

    # 1) "project_dir/xxx" または "project_dir\xxx" 形（配下の絶対パス）を
    #    相対 locator へ。
    normalized = re.sub(
        project_dir_pattern + r"[\\/]([^\s'\"]*)",
        _relativize,
        text,
    )
    # 2) project_dir 自身への完全一致（直後が path 継続文字でない場合のみ）を
    #    "." へ。既に 1) で "project_dir/" / "project_dir\" 形は除去済みのため、
    #    ここに残る一致は「project_dir で終わる」か「project_dir の直後が
    #    区切り文字以外の非継続文字（空白・引用符・行末等）」のいずれかに
    #    限られる。
    normalized = re.sub(project_dir_pattern + r"(?![\w.-])", ".", normalized)
    return _ABSOLUTE_PATH_TOKEN_RE.sub("<external-path>", normalized)


def _normalize_diagnostics(texts: List[str], project_dir: Path) -> List[str]:
    return [_normalize_diagnostic(text, project_dir) for text in texts]


def _collect_todo_sentinel_paths(value: Any, path: str, unresolved: List[str]) -> None:
    """`value`（`CompositionScore.model_dump()` の canonical dump 断片）を再帰走査し、
    `sentinels.is_todo_sentinel` に該当する文字列値の canonical path を `unresolved` へ
    追記する。dict / list を降り、それ以外はリーフとして判定する（著者欄か計測欄かは
    問わない — TODO が残る score は全面的に compile 不適格という fail-closed ゲート）。"""
    if isinstance(value, dict):
        for key, sub_value in value.items():
            sub_path = f"{path}.{key}" if path else str(key)
            _collect_todo_sentinel_paths(sub_value, sub_path, unresolved)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_todo_sentinel_paths(item, f"{path}[{index}]", unresolved)
    elif is_todo_sentinel(value):
        unresolved.append(path)


def _unresolved_author_field_paths(score: CompositionScore) -> List[str]:
    """`score` の canonical dump（`model_dump()`）全体を再帰走査し、
    `sentinels.is_todo_sentinel` に該当する全ての文字列値を canonical path 付きで
    返す — semantic 層に限らず structure[].role / structure[].physical を含む
    score 全体が対象（1 件でも残っていれば compile 不適格）。"""
    unresolved: List[str] = []
    _collect_todo_sentinel_paths(score.model_dump(), "", unresolved)
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


def unsupported_changed_field_reasons(
    changed_fields: List[ChangedFieldPlanEntry], invocation_mode: InvocationMode
) -> List[str]:
    """`changed_fields` のうち `mode_support=="unsupported"` な各 path から、
    anchor 配送の warning/reason と同じ文体の 1 行診断を作る（`_mode_gate_reasons`
    が下記の unknown（宣言時のみ）と合成する土台。「unsupported」は
    mode_overrides の declared/undeclared を問わず常にゲート対象）。"""
    return [
        f"field {c.path} は invocation_mode {invocation_mode} で unsupported"
        for c in changed_fields
        if c.mode_support == "unsupported"
    ]


def _mode_gate_reasons(
    changed_fields: List[ChangedFieldPlanEntry],
    invocation_mode: InvocationMode,
    *,
    mode_overrides_declared: bool,
) -> List[str]:
    """anchor 配送と同じ strict/advisory ゲート対象の changed_field 診断一式を作る
    （`build_recast_plan` の strict/advisory ゲート・`RecastPlanResult.mode_gate_reasons`
    経由の CLI state note、両方が共有する single source）。

    `mode_support=="unsupported"` は常に対象（`unsupported_changed_field_reasons`）。
    `mode_support=="unknown"`（mode_overrides ファイルに当該 path のエントリが
    無い＝未実測）は **backend が mode_overrides を宣言している場合のみ**
    対象に含める（Codex P2 fifth round #207）。

    線引きの根拠（opt-in 計器）: mode_overrides は backend ごとの opt-in 計器
    — 宣言していない backend は invocation_mode 軸そのものを未計測であり、
    全 changed_field が unknown になるのは仕様どおりで異常ではない（従来
    挙動を変えない）。一方、宣言した backend にとっての unknown は「この
    path を計測対象にしたが実測データがまだ無い」を意味し、届くか不明な
    まま生成へ進めるのは fail-closed の趣旨に反する。これは
    `arrange/package.py` の strict 検査が hard 宣言 anchor の
    delivery=="unknown" を strict failure として扱う（`requested.mode ==
    "hard" and delivery_status in ("unsupported", "unknown")`）既存規律と
    同型 — 「宣言した契約について unknown を許容しない」という一貫した規律。"""
    reasons = unsupported_changed_field_reasons(changed_fields, invocation_mode)
    if mode_overrides_declared:
        reasons = reasons + [
            f"field {c.path} は invocation_mode {invocation_mode} で unknown（未実測）"
            for c in changed_fields
            if c.mode_support == "unknown"
        ]
    return reasons


def _build_recommendation(
    blocked: Optional[BlockedInfo],
    state_reached: RecastState,
    changed_fields: List[ChangedFieldPlanEntry],
    *,
    mode_overrides_declared: bool,
) -> str:
    if blocked is not None:
        if blocked.state == "blocked_capability":
            mode_gated = any(c.mode_support == "unsupported" for c in changed_fields) or (
                mode_overrides_declared
                and any(c.mode_support == "unknown" for c in changed_fields)
            )
            if mode_gated:
                return (
                    "この invocation_mode では届くか未確認な変更が含まれています。"
                    "capability_mode を advisory へ切替するか、届く invocation_mode"
                    "（cover/prompt_only の切替）を検討してください。"
                )
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
    unknown_paths = (
        [c.path for c in changed_fields if c.mode_support == "unknown"]
        if mode_overrides_declared
        else []
    )
    if unsupported_paths or unknown_paths:
        clauses = []
        if unsupported_paths:
            clauses.append(f"届きません: {', '.join(unsupported_paths)}")
        if unknown_paths:
            clauses.append(f"未実測です: {', '.join(unknown_paths)}")
        return (
            "この invocation_mode ではこの変更は "
            + " / ".join(clauses)
            + "（cover/prompt_only の切替を検討してください）。"
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


def _identity_reference_digest_components(identity_manifest_path: Path) -> Dict[str, str]:
    """identity manifest **自身**の bytes だけでなく、それが参照する source
    artifact / 各 anchor artifact の内容も digest へ折り込む（Codex P2 third
    round #207: `identity_manifest` component だけでは、manifest.yaml 自体は
    無変更のまま参照先の artifact（例 `lyrics.txt`）だけが書き換わる/破損する
    ケースを検出できない）。

    `parse_identity_manifest_with_artifacts` は source → 各 anchor の順で
    「宣言 sha256 == 実 bytes の sha256」を照合してから正常 return するため、
    これを呼ぶだけで参照先の drift（書き換え・削除・封じ込め違反・不正 YAML 等）
    を一括検出できる — 呼び出しが成功した時点で `manifest.source.sha256` /
    `anchor.sha256` は実 bytes と一致済みであり、component にはそれらの宣言値を
    そのまま使う（`collect=None` — bytes 自体は破棄済みで再ハッシュ不要）。

    失敗時（`IdentityManifestError` / 読み取り不能 `OSError` / 不正 YAML /
    schema 不一致）は例外を送出せず、正常系とは構造的に異なる 1 component
    （例外の型+メッセージ）を返す fail-closed 戦略を取る — `recast status` は
    これにより「digest 不一致」を経由して自動的に stale 表示へ落ちる（design
    point 2: 例外を握って stale 表示に倒す）。"""
    try:
        manifest_bytes = identity_manifest_path.read_bytes()
        manifest, _artifact_bytes = parse_identity_manifest_with_artifacts(
            manifest_bytes, identity_manifest_path, collect=None
        )
    except (IdentityManifestError, OSError, ValidationError, yaml.YAMLError, ValueError) as exc:
        return {"identity_reference_error": f"{type(exc).__name__}: {exc}"}

    components: Dict[str, str] = {"identity_source": manifest.source.sha256}
    for anchor in manifest.anchors:
        components[f"identity_artifact:{anchor.id}"] = anchor.sha256
    return components


def _device_profile_digest_component(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> Dict[str, str]:
    """実際に使われる device profile（`config/device_profiles/<generator>.yaml`）の
    raw bytes sha256 を digest component として返す（Codex P2 fourth round #207:
    `config/device_profiles/suno.yaml` を編集しても、それまでの digest は
    device profile の中身を一切見ていなかったため `recast status` が変化を
    検出できなかった）。

    使う device profile は derived score の `rendering.target_backend`
    （score 自体の宣言、または arrangement の override）に依存する
    （`resolve_backend_descriptor(...).profile_key`）ため、ここで score +
    arrangement spec を独立にロードし `resolve_arrangement` を呼んで決定する
    — `build_recast_plan` 本体のステップ 2/4/7 と同じ計算を、digest 専用に
    副作用なく再現する。解決パスは `load_device_profile` が実際に読む
    local→packaged フォールバック（`utils.config_loader.resolve_config_bytes`）
    と同一にする。

    score/spec のロード・resolve のいずれかが失敗した場合は例外を送出せず、
    正常系とは構造的に異なる 1 component を返す fail-closed 戦略を取る
    （`_identity_reference_digest_components` と同型）。profile 自体が
    見つからない場合は省略ではなく pinned センチネル `"not_found"` を使う
    （後からファイルが現れたケースも実 sha256 との差分で検出できるように）。"""
    try:
        score_bytes = loaded.score_path.read_bytes()
        score_data = _parse_yaml_mapping(score_bytes, "composition score", str(loaded.score_path))
        score = CompositionScore.model_validate(score_data)

        spec_path = loaded.arrangement_paths[variant]
        spec_bytes = spec_path.read_bytes()
        spec_data = _parse_yaml_mapping(spec_bytes, "arrangement spec", str(spec_path))
        spec = ArrangementSpec.model_validate(spec_data)

        resolution = resolve_arrangement(score, spec)
        render_generator = resolve_backend_descriptor(
            resolution.derived_score.rendering.target_backend
        ).profile_key
    except (
        OSError,
        ValueError,
        ValidationError,
        yaml.YAMLError,
        ArrangementConflictError,
        ArrangementPolicyError,
    ) as exc:
        return {"device_profile_resolution_error": f"{type(exc).__name__}: {exc}"}

    profile_bytes = resolve_config_bytes(f"device_profiles/{render_generator}")
    if profile_bytes is None:
        return {"device_profile": "not_found"}
    return {"device_profile": hashlib.sha256(profile_bytes).hexdigest()}


def compute_recast_inputs_digest(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> str:
    """`(variant, backend)` run が参照する入力一式の raw bytes sha256 を
    canonical digest へ合成する（`arrange.bundle.compute_content_digest` 流用）。

    `loaded` はロード時点で score/identity_manifest/arrangement/capability_profile
    の実在を検証済みのため、これらは読み取りをガードしない（`build_recast_plan` の
    既存ステップ 3/6 の raw read と同じ規約）。identity manifest が参照する
    source/anchor artifact は `_identity_reference_digest_components` が、
    実際に使われる device profile は `_device_profile_digest_component` が
    それぞれ fail-closed に折り込む（読めない/破損していても例外を送出しない）。
    `recast plan` はこの digest を state へ永続化し、`recast status` は現在の
    入力から再計算した digest と突き合わせて stale run（永続化後に入力または
    参照先 artifact が変更された run）を検出する。"""
    components: Dict[str, str] = {
        "project": loaded.sha256,
        "score": sha256_file(loaded.score_path),
        "identity_manifest": sha256_file(loaded.identity_manifest_path),
        "arrangement_spec": sha256_file(loaded.arrangement_paths[variant]),
        "capability_profile": sha256_file(loaded.capability_profile_paths[backend]),
    }
    components.update(_identity_reference_digest_components(loaded.identity_manifest_path))
    components.update(
        _device_profile_digest_component(loaded, variant=variant, backend=backend)
    )
    mode_override_path = loaded.mode_override_paths.get(backend)
    if mode_override_path is not None:
        components["mode_overrides"] = sha256_file(mode_override_path)
    return compute_content_digest(components)


def build_recast_plan(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> RecastPlanResult:
    """`loaded` の (variant, backend) 組に対する RecastPlan を構築する（純粋関数、
    ディスクへの副作用なし — `recast_plan.json` の publish と
    `recast.state.record_state` は呼び出し側 CLI の責務、PR2 P2 対応）。

    既存公開 API（PR2 由来）: 返り値の型・JSON 出力は不変。内部的には
    `build_recast_plan_artifacts` の `result` フィールドを返すだけの薄いラッパー
    （PR3 でのリファクタ — compile 済み成果物の再利用は `recast/backend.py` が
    `build_recast_plan_artifacts` を直接呼ぶことで行う）。
    """
    return build_recast_plan_artifacts(loaded, variant=variant, backend=backend).result


def build_recast_plan_artifacts(
    loaded: LoadedRecastProject, *, variant: str, backend: str
) -> RecastPlanArtifacts:
    """`build_recast_plan` の完全版: `RecastPlanResult`（`inputs_digest` 込み）に
    加え、到達状態が `compiled`/`verified` のときの compile 済み成果物一式
    （`RecastPlanArtifacts`）を返す。**純粋関数**（ディスクへの副作用なし —
    `recast_plan.json` の publish・`record_state` はいずれも呼び出し側の責務、
    PR2 P2 対応）。診断構築のロジックは `build_recast_plan` と完全に同一。

    single-read 束（Codex P2 sixth round #207）: score / identity manifest /
    arrangement spec / capability profile / mode_overrides / device profile の
    各ファイルはこの関数内で `read_bytes()` を 1 回ずつしか呼ばない —
    `inputs_digest` の hash 計算も、実際の parse/resolve/compile も同じ bytes
    から行う。以前は `compute_recast_inputs_digest` を関数冒頭で別途呼び、
    実パイプラインの読み取りと独立に再読込していたため、実行中の入力差し替え
    A→B→A で「B で compile した plan を A の digest で pin」してしまう TOCTOU
    があった（`compute_recast_inputs_digest` 自体は `recast status` が独立の
    時点で鮮度チェックする際の標準経路として引き続き公開する — そちらは
    意図的な別時点での再読込であり stale 検出の本質そのもの）。

    各ファイルの read はここで前倒しするが、失敗の**報告順序**（どの段の
    エラーが優先して `blocked_*` になるか）は変えない — 読み取り/parse の
    成功時オブジェクトまたは失敗時例外を一旦保持するだけに留め、実際に
    `_finalize` を呼ぶ/例外を送出する判定は元のステップ位置のまま行う。
    """
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

    # ======================================================================
    # single-read bundle: 全入力ファイルをここで 1 回ずつ read_bytes する。
    # ======================================================================
    digest_components: Dict[str, str] = {"project": loaded.sha256}

    # --- score ---------------------------------------------------------------
    score_bytes = loaded.score_path.read_bytes()
    digest_components["score"] = hashlib.sha256(score_bytes).hexdigest()
    score: Optional[CompositionScore] = None
    score_parse_error: Optional[Exception] = None
    try:
        score = CompositionScore.model_validate(
            _parse_yaml_mapping(score_bytes, "composition score", str(loaded.score_path))
        )
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        score_parse_error = exc

    # --- identity manifest -----------------------------------------------------
    manifest_path = loaded.identity_manifest_path
    manifest_read_error: Optional[OSError] = None
    manifest_bytes: Optional[bytes] = None
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        manifest_read_error = exc

    manifest = None
    manifest_parse_error: Optional[Exception] = None
    manifest_sha256 = ""
    if manifest_bytes is not None:
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        digest_components["identity_manifest"] = manifest_sha256
        try:
            manifest, _manifest_artifact_bytes = parse_identity_manifest_with_artifacts(
                manifest_bytes, manifest_path, collect=None
            )
        except (IdentityManifestError, ValueError, ValidationError, yaml.YAMLError) as exc:
            manifest_parse_error = exc

    if manifest is not None:
        digest_components["identity_source"] = manifest.source.sha256
        for anchor in manifest.anchors:
            digest_components[f"identity_artifact:{anchor.id}"] = anchor.sha256
    else:
        identity_error = manifest_read_error or manifest_parse_error
        digest_components["identity_reference_error"] = (
            f"{type(identity_error).__name__}: {identity_error}"
        )

    # --- arrangement spec + resolve ---------------------------------------------
    arrangement_path = loaded.arrangement_paths[variant]
    spec_read_error: Optional[OSError] = None
    spec_bytes: Optional[bytes] = None
    try:
        spec_bytes = arrangement_path.read_bytes()
    except OSError as exc:
        spec_read_error = exc

    spec: Optional[ArrangementSpec] = None
    resolution = None
    resolve_error: Optional[Exception] = None
    spec_sha256 = ""
    if spec_bytes is not None:
        spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()
        digest_components["arrangement_spec"] = spec_sha256
        try:
            spec = ArrangementSpec.model_validate(
                _parse_yaml_mapping(spec_bytes, "arrangement spec", str(arrangement_path))
            )
            # score の parse/validate が失敗している場合は resolve_arrangement を
            # 呼ばない（score_parse_error は step 2 で最優先報告され、この関数は
            # そこで既に return 済みのはず — ここに到達するのは score が有効な
            # 場合のみだが、念のため None ガードで TypeError 化を防ぐ）。
            if score is not None:
                resolution = resolve_arrangement(score, spec)
        except (
            ValueError,
            ValidationError,
            yaml.YAMLError,
            ArrangementConflictError,
            ArrangementPolicyError,
        ) as exc:
            resolve_error = exc

    # --- capability profile ------------------------------------------------------
    profile_path = loaded.capability_profile_paths[backend]
    profile_bytes = profile_path.read_bytes()
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    digest_components["capability_profile"] = profile_sha256
    profile: Optional[InputCapabilityProfile] = None
    profile_error: Optional[Exception] = None
    try:
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
    except (ValueError, ValidationError, InputCapabilityError) as exc:
        profile_error = exc

    # --- mode overrides ------------------------------------------------------------
    mode_overrides_config: Optional[ModeOverridesConfig] = None
    mode_overrides_error: Optional[Exception] = None
    mode_override_path = loaded.mode_override_paths.get(backend)
    if mode_override_path is not None:
        mode_overrides_bytes = mode_override_path.read_bytes()
        digest_components["mode_overrides"] = hashlib.sha256(mode_overrides_bytes).hexdigest()
        try:
            mode_overrides_data = _parse_yaml_mapping(
                mode_overrides_bytes, "mode overrides", str(mode_override_path)
            )
            mode_overrides_config = ModeOverridesConfig.model_validate(mode_overrides_data)
        except (ValueError, ValidationError, yaml.YAMLError) as exc:
            mode_overrides_error = exc
    # mode_overrides を宣言している backend のみ、changed_fields の unknown を
    # strict/advisory ゲート対象に含める（opt-in 計器 — `_mode_gate_reasons` 参照）。
    mode_overrides_declared = mode_overrides_config is not None

    # --- device profile ---------------------------------------------------------
    # 使う device profile は derived score の rendering.target_backend に依存する
    # ため、resolve_arrangement が成功した場合のみ決定できる。
    device_profile: Optional[DeviceProfile] = None
    device_profile_error: Optional[Exception] = None
    if resolution is not None:
        render_generator = resolve_backend_descriptor(
            resolution.derived_score.rendering.target_backend
        ).profile_key
        device_profile_bytes = resolve_config_bytes(f"device_profiles/{render_generator}")
        if device_profile_bytes is None:
            digest_components["device_profile"] = "not_found"
        else:
            digest_components["device_profile"] = hashlib.sha256(
                device_profile_bytes
            ).hexdigest()
            try:
                device_profile = DeviceProfile.model_validate(
                    _parse_yaml_mapping(
                        device_profile_bytes,
                        "device profile",
                        f"device_profiles/{render_generator}",
                    )
                )
            except (ValueError, ValidationError, yaml.YAMLError) as exc:
                device_profile_error = exc
    else:
        # resolution が None になる原因は score_parse_error / spec_read_error /
        # resolve_error の 3 通りあり、優先順位は該当する step チェックの評価順
        # （score → spec 読み取り → spec parse・resolve_arrangement）と一致させる
        # （Codex P2 thirteenth round #207: 従来は常に resolve_error だけを参照
        # しており、score 破損時は resolve_error が未設定 (None) のまま
        # "NoneType: None" という無意味な固定文字列を digest へ焼き込んでいた。
        # `recast status` が使う独立の `_device_profile_digest_component` は
        # 同じ入力を再パースして実際の例外を捕捉するため型/メッセージが
        # 一致し、plan 発行直後の同一入力に対して digest が食い違い、
        # blocked_authoring な run を偽 stale と誤判定させていた）。
        resolution_error: Optional[Exception] = score_parse_error or spec_read_error or resolve_error
        digest_components["device_profile_resolution_error"] = (
            f"{type(resolution_error).__name__}: {resolution_error}"
        )

    inputs_digest = compute_content_digest(digest_components)
    # step 9 で確定する（strict/advisory ゲート対象の changed_field 診断一式）。
    mode_gate_reasons: List[str] = []

    # `collect_protected_input_paths` と同じ集合を、束が既に読んだ/保持している
    # オブジェクトから副作用なく再構成する（Codex P2 review round 7, PR3 #208
    # 指摘 13: `RecastPlanResult.protected_inputs` の docstring 参照 — 再 read/
    # re-parse を一切行わない。manifest parse が失敗している場合は
    # source/anchor artifact の解決を諦める degrade を束の失敗許容と合わせる）。
    protected_inputs: List[Path] = [
        loaded.path,
        loaded.score_path,
        manifest_path,
        arrangement_path,
        profile_path,
    ]
    if mode_override_path is not None:
        protected_inputs.append(mode_override_path)
    if manifest is not None:
        manifest_dir = manifest_path.resolve().parent
        try:
            protected_inputs.append(resolve_confined(manifest.source.locator, manifest_dir))
            for anchor in manifest.anchors:
                protected_inputs.append(resolve_confined(anchor.artifact, manifest_dir))
        except ValueError:
            pass

    def _finalize(
        *,
        state_reached: RecastState,
        blocked: Optional[BlockedInfo] = None,
        anchors: Optional[List[AnchorPlanEntry]] = None,
        changed_fields: Optional[List[ChangedFieldPlanEntry]] = None,
        warnings: Optional[List[str]] = None,
        compiled: Optional[CompiledPerformancePackage] = None,
        derived_score: Optional[CompositionScore] = None,
        manifest_sha256: Optional[str] = None,
        contract_sha256: Optional[str] = None,
        profile_sha256: Optional[str] = None,
        derived_score_sha256: Optional[str] = None,
    ) -> RecastPlanArtifacts:
        # 診断文字列（例外メッセージ由来の blocked.reasons / warnings）を
        # project 相対へ正規化してから plan へ載せる（Codex P2 seventh round
        # #207: 全生成点を個別に潰すのではなく、plan/state へ入る直前の
        # 単一の絞り口でまとめて正規化する — 将来の新規例外経路も自動的に
        # 対象になる fail-closed 設計）。
        if blocked is not None:
            blocked = BlockedInfo(
                state=blocked.state,
                reasons=_normalize_diagnostics(blocked.reasons, loaded.project_dir),
            )
        normalized_warnings = (
            _normalize_diagnostics(warnings, loaded.project_dir) if warnings else warnings
        )

        resolved_changed_fields = changed_fields or []
        recommendation = _build_recommendation(
            blocked,
            state_reached,
            resolved_changed_fields,
            mode_overrides_declared=mode_overrides_declared,
        )
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
            warnings=normalized_warnings or [],
            recommendation=recommendation,
        )
        result = RecastPlanResult(
            plan=plan,
            text=_render_text(plan),
            inputs_digest=inputs_digest,
            mode_gate_reasons=mode_gate_reasons,
            protected_inputs=protected_inputs,
        )
        return RecastPlanArtifacts(
            result=result,
            backend_ref=backend_ref,
            compiled=compiled,
            derived_score=derived_score,
            manifest_sha256=manifest_sha256,
            contract_sha256=contract_sha256,
            profile_sha256=profile_sha256,
            derived_score_sha256=derived_score_sha256,
        )

    # --- step 2a: score の YAML 破損・schema 不正チェック --------------------
    # score は著者成果物 (author field / preservation 契約と同じ層) のため、
    # parse/validate 失敗は identity manifest / capability profile と違う
    # blocked_authoring として finalize する（Codex P2 eleventh round #207:
    # 以前は score parse を bundle 先頭で無条件に呼んでおり、失敗時は捕捉
    # されない例外として関数外へ伝播し CLI が top-level Error で落ちていた
    # ため recast_plan.json も state も残らなかった）。他の bundle 読み取り
    # より先に発生していた元の失敗順序を保つため、step 2 のチェックより先に
    # 判定する。
    if score_parse_error is not None:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(score_parse_error)]),
        )
    assert score is not None

    # --- step 2: author field (TODO sentinel) 未解決チェック -----------------
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
    if manifest_read_error is not None:
        return _finalize(
            state_reached="blocked_verification",
            blocked=BlockedInfo(state="blocked_verification", reasons=[str(manifest_read_error)]),
        )
    if manifest_parse_error is not None:
        return _finalize(
            state_reached="blocked_verification",
            blocked=BlockedInfo(
                state="blocked_verification", reasons=[str(manifest_parse_error)]
            ),
        )
    assert manifest is not None  # 上の 2 ガードで None のケースは既に return 済み
    manifest_by_id = {anchor.id: anchor for anchor in manifest.anchors}

    # --- step 4+5: arrangement resolve + preservation contract -------------
    if spec_read_error is not None:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(spec_read_error)]),
        )
    if resolve_error is not None:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(resolve_error)]),
        )
    assert spec is not None and resolution is not None  # 上の 2 ガードで既に return 済み
    try:
        contract = build_preservation_contract(
            manifest, spec, manifest_sha256=manifest_sha256, spec_sha256=spec_sha256
        )
    except PreservationContractError as exc:
        return _finalize(
            state_reached="blocked_authoring",
            blocked=BlockedInfo(state="blocked_authoring", reasons=[str(exc)]),
        )

    # --- step 6: capability profile + mode overrides ------------------------
    # capability_profile / mode_overrides の YAML 破損・schema 不正は、
    # identity manifest / arrangement spec の同種の失敗（blocked_verification /
    # blocked_authoring）と一貫させ blocked_capability として finalize する
    # （Codex P2 eighth round #207: 以前は保存済み例外を re-raise していたため
    # CLI が top-level Error で落ち、recast_plan.json も state も残らなかった
    # — 他の parse 失敗系と非一貫だった）。
    if profile_error is not None:
        return _finalize(
            state_reached="blocked_capability",
            blocked=BlockedInfo(state="blocked_capability", reasons=[str(profile_error)]),
        )
    assert profile is not None
    if mode_overrides_error is not None:
        return _finalize(
            state_reached="blocked_capability",
            blocked=BlockedInfo(state="blocked_capability", reasons=[str(mode_overrides_error)]),
        )
    # mode_overrides.generator と capability profile.generator の不一致も、
    # 他の capability 層の不整合と同じ blocked_capability として finalize する
    # （Codex P2 twelfth round #207: eighth round で一度スコープ外にした箇所の
    # 再指摘 — 以前は raise していたため CLI が top-level Error で落ち、
    # recast_plan.json も state も残らなかった）。reasons に両 generator 名と
    # 是正の示唆（mode_overrides を差し替えるか宣言を外す）を含める。
    if mode_overrides_config is not None and mode_overrides_config.generator != profile.generator:
        return _finalize(
            state_reached="blocked_capability",
            blocked=BlockedInfo(
                state="blocked_capability",
                reasons=[
                    f"backend {backend!r} mode_overrides generator "
                    f"{mode_overrides_config.generator!r} does not match capability profile "
                    f"generator {profile.generator!r} — mode_overrides を "
                    f"{profile.generator!r} 用に差し替えるか、backend の mode_overrides 宣言"
                    "を外してください"
                ],
            ),
        )

    # --- step 7: build performance package ----------------------------------
    # device profile の YAML 破損・schema 不正も capability_profile/mode_overrides
    # と同じ blocked_capability として finalize する（Codex P2 tenth round #207:
    # 以前は保存済み例外を re-raise していたため CLI が top-level Error で落ち、
    # recast_plan.json も state も残らなかった — eighth round で対応した
    # capability_profile/mode_overrides と同クラスの非一貫だった）。
    if device_profile_error is not None:
        return _finalize(
            state_reached="blocked_capability",
            blocked=BlockedInfo(state="blocked_capability", reasons=[str(device_profile_error)]),
        )
    contract_sha256 = compute_preservation_contract_sha256(contract)
    derived_score_sha256 = compute_derived_score_sha256(resolution.derived_score)

    # package_dir は `<builds_root>/packages/<variant>@<backend>/` の永続公開先
    # （PR3 #208 指摘 3）: tempfile.TemporaryDirectory だと `artifact_base_locator`
    # （manifest ディレクトリからの相対 locator）が呼び出し環境のテンポラリ
    # パス深さに依存し package_sha256/content_digest が非決定になっていた。
    # builds_root 配下の固定パスへ変えることで locator が checkout-stable になる
    # （builds_root 自体が project_dir から相対解決される既存契約 — loader.py
    # `_resolve_builds_root`）。この dir は per-(variant, backend) の mutable
    # 最新スナップショット（`recast_plan.json`/`recast_state.json` と同じ規約
    # — content-addressed な `arrange`/`package` の builds-root 免疫契約とは別物
    # で、再実行のたびに上書きしてよい）。
    #
    # pr2 側の同種指摘（Codex P2 twelfth round #207 指摘 19: 検証ステージングが
    # system temp に作られると project files と別ドライブになりうる Windows で
    # `os.path.relpath` が ValueError を送出しうる）は、PR3 のこの
    # `resolve_packages_dir`（`builds_root` 配下、常に project files と同一
    # ドライブ）採用により構造的に該当しない — `_atomic_publish_text_bundle`
    # 内部の staging（`tempfile.TemporaryDirectory(dir=output_dir)`）も
    # `package_dir` 配下なので同様に同一ドライブが保証される。
    #
    # pr2 thirteenth round #207 指摘 21（`build_recast_plan` が builds_root を
    # mkdir しており「ディスクへ副作用を持たない純粋関数」契約に反していた —
    # staging 先を project_dir へ変更し mkdir を撤去）も PR3 では事情が異なる:
    # `build_recast_plan_artifacts` は元々 compiled/verified 到達時に package/
    # report を builds_root 配下へ永続公開する副作用を意図的に持つ関数
    # （本 docstring 上部に明記済み — 「plan JSON の publish/state 記録」だけが
    # 呼び出し側 CLI の責務という契約で、package 公開はこの関数自身の責務）。
    # `resolve_packages_dir` は `mkdir` 自体を `_atomic_publish_text_bundle` の
    # 内部（`output_dir.mkdir(parents=True, exist_ok=True)`）へ委譲しており、
    # builds_root が project.yaml で未実在パスとして宣言されていても、実際に
    # package を公開する経路（compiled/verified 到達時のみ）でのみ mkdir が
    # 起きる — blocked_* 到達時は package_dir 自体に触れないため builds_root
    # も作られない（pr2 の懸念する「副作用が意図せず広がる」経路は無い）。
    package_dir = resolve_packages_dir(loaded, variant, backend)
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

    # package/report を永続公開する（require_verified_package の有無に関わらず
    # 常に — "compiled" 到達時も最新の compile 結果を builds_root へ残す）。
    # 束の読み取り結果から再構成済みの `protected_inputs`（closure 変数、上記
    # 参照）を全公開サイト共通の衝突ガードとして渡す（Codex P2 review, PR3
    # #208 指摘 7: 従来 packages 公開は一切ガードなしで、capability_profile/
    # mode_overrides/manifest anchor artifact 等が偶然 `packages/<variant>@
    # <backend>/` 配下と衝突する project 構成では入力を無警告で上書き破壊し
    # 得た。指摘 13 対応でここでの再計算は廃止 — この時点は manifest parse
    # が既に成功しているケースのみ到達するため、束から再構成した完全な集合を
    # そのまま使い回せる）。
    # encode は 1 回だけ行い、書き込みも（`arrange/package.py` が既に計算
    # 済みの）`package_sha256` の hash 計算元もこの同一 bytes に揃える
    # （Codex P2 review round 6, PR3 #208 指摘 12 — `_atomic_publish_text_bundle`
    # 側の docstring参照）。
    try:
        _atomic_publish_text_bundle(
            package_dir,
            {
                PERFORMANCE_PACKAGE_FILENAME: compiled.package_json.encode("utf-8"),
                COMPILATION_REPORT_FILENAME: compiled.report_json.encode("utf-8"),
            },
            protected_inputs=protected_inputs,
        )
    except ValueError as exc:
        return _finalize(
            state_reached="blocked_capability",
            blocked=BlockedInfo(state="blocked_capability", reasons=[str(exc)]),
        )

    # --- step 8: optional verification --------------------------------------
    if project.policy.require_verified_package:
        verify_report = verify_package(package_dir / PERFORMANCE_PACKAGE_FILENAME, manifest_path)
        if not verify_report.ok:
            reasons = [
                f"{check.group} {check.label}: {check.detail}" for check in verify_report.failures
            ]
            return _finalize(
                state_reached="blocked_verification",
                blocked=BlockedInfo(state="blocked_verification", reasons=reasons),
            )
        state_reached: RecastState = "verified"
    else:
        state_reached = "compiled"

    # --- step 9: diagnostics tables -------------------------------------------
    anchors = _build_anchor_entries(compiled.package, manifest_by_id)
    changed_fields = _build_changed_field_entries(
        resolution.changes, backend_ref.invocation_mode, mode_overrides_config
    )

    # anchor 配送と同じ strict/advisory 意味論を changed_fields にも適用する:
    # mode_overrides が「この invocation_mode では届かない」（unsupported）と
    # 実測している変更、および backend が mode_overrides を宣言している場合の
    # 「未実測」（unknown）な変更が 1 件でもあれば、strict は blocked_capability
    # へ降格（生成しても届くか不明な変更を verified/exit 0 で推奨しない）、
    # advisory は到達状態を維持しつつ warnings へ積む（`_mode_gate_reasons`
    # docstring に opt-in 計器としての線引きの根拠を記載）。
    mode_gate_reasons = _mode_gate_reasons(
        changed_fields,
        backend_ref.invocation_mode,
        mode_overrides_declared=mode_overrides_declared,
    )
    if mode_gate_reasons:
        if project.policy.capability_mode == "strict":
            return _finalize(
                state_reached="blocked_capability",
                blocked=BlockedInfo(state="blocked_capability", reasons=mode_gate_reasons),
                anchors=anchors,
                changed_fields=changed_fields,
                warnings=warnings,
            )
        warnings = warnings + mode_gate_reasons

    return _finalize(
        state_reached=state_reached,
        blocked=None,
        anchors=anchors,
        changed_fields=changed_fields,
        warnings=warnings,
        compiled=compiled,
        derived_score=resolution.derived_score,
        manifest_sha256=manifest_sha256,
        contract_sha256=contract_sha256,
        profile_sha256=profile_sha256,
        derived_score_sha256=derived_score_sha256,
    )
