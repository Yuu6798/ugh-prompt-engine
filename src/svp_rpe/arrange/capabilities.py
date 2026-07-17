"""InputCapabilityProfile: 生成器が実際に受け取れる入力チャネルの自己記述。

既存 `control_profile`（`config/device_profiles/*.yaml` = プロンプト欄の grip
自己記述）とは**別モデル**として、生成器が受け取れる入力チャネル（style prompt /
lyrics / section tags / reference audio / symbolic melody / MIDI）の対応状況を
記述する。「入力チャネルとして存在するか」と「値をどれだけ精度良く保持するか
（grip）」は独立した問いであり、本モジュールは前者のみを扱う。

`support="supported"` は「そのチャネルが入力として存在する」ことのみを意味し、
保持精度（grip / adherence）を保証しない。grip は引き続き `control_profile`
（`config/device_profiles/`）の責務であり、本モジュールはそれを変更しない。

チャネルがファイルに記載されていない場合は `support="unknown"`（未計測・
未確認）にフォールバックする。`unsupported`（チャネルが存在しないと判明
済み）と `unknown`（存在するかどうか未確認）を混同しないこと — 前者は明示
的な否定判断、後者は判断保留であり、記載漏れを "supported" はもちろん
"unsupported" とも推測しない。

PerformancePackage への配線（AR3-2）はスコープ外。本モジュールは `compose`
を import しない。

`InputCapabilityError` は `resolver.py` の `ArrangementError` 階層に連なる
（既存 arrange 系エラー階層との整合。この継承のために `arrange.resolver` を
import するが、`compose` 側の型を参照することはない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.arrange.pathsafe import (
    PathConfinementError,
    resolve_confined,
    validate_relative_locator,
)
from svp_rpe.arrange.resolver import ArrangementError

ChannelSupport = Literal["supported", "experimental", "unsupported", "unknown"]

INPUT_CHANNELS: tuple[str, ...] = (
    "style_prompt",
    "lyrics_text",
    "section_tags",
    "reference_audio",
    "symbolic_melody",
    "midi",
)


class InputChannelCapability(BaseModel):
    """1 入力チャネルの対応状況。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")

    support: ChannelSupport
    evidence: Optional[str] = None
    note: Optional[str] = None


def _unknown_capability() -> InputChannelCapability:
    return InputChannelCapability(support="unknown")


class InputChannels(BaseModel):
    """`INPUT_CHANNELS` の各チャネルを個別 field で持つ。

    省略されたチャネルは `InputChannelCapability(support="unknown")` に
    フォールバックする（ファイルに無いチャネルを supported と推測しない）。
    未知 key を拒否する。
    """

    model_config = ConfigDict(extra="forbid")

    style_prompt: InputChannelCapability = Field(default_factory=_unknown_capability)
    lyrics_text: InputChannelCapability = Field(default_factory=_unknown_capability)
    section_tags: InputChannelCapability = Field(default_factory=_unknown_capability)
    reference_audio: InputChannelCapability = Field(default_factory=_unknown_capability)
    symbolic_melody: InputChannelCapability = Field(default_factory=_unknown_capability)
    midi: InputChannelCapability = Field(default_factory=_unknown_capability)


class InputCapabilityProfile(BaseModel):
    """generator 単位の InputCapabilityProfile。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["input-capability/0.1"]
    generator: str
    profile_version: str
    input_channels: InputChannels


class InputCapabilityError(ArrangementError):
    """InputCapabilityProfile のロード・evidence 検証に関するエラー。"""


def load_input_capability_profile(
    path: Path | str, *, evidence_base: Path | None = None
) -> InputCapabilityProfile:
    """InputCapabilityProfile YAML をロードし、evidence の実在を検証する。

    他 loader（`identity.py` 等）と同型: YAML mapping をロードし、
    `model_validate` でスキーマ検証した後、`evidence` が非 None の各チャネルに
    ついて検証を行う。

    evidence のファイルシステム検証は**明示 base 限定**:

    - `evidence_base=None`（既定）: evidence の実在チェックを行わない。
      evidence は repo-root 相対の provenance 文字列として schema 保持のみ。
      wheel 配布では profile YAML は package resource として同梱されるが
      evidence の指す `docs/*.md` は同梱されないため、インストール環境
      （repo checkout 外）では実在検証が必ず偽陽性で失敗する。evidence の
      実在は repo checkout の場所を知る呼び出し側だけが検証できるので、
      既定 cwd への暗黙依存は廃止した。ただし文字列としての形式検証
      （絶対パス拒否 + 字面の遡上拒否 = 可搬性契約）は base 不要で判定
      できるため常に行う
    - `evidence_base` 明示時: 上記の形式検証に加えて resolve() ベースの
      封じ込め（symlink 脱出など実ファイルシステム上の脱出の拒否 —
      identity.py の artifact 封じ込めと同じ可搬性契約）+ 実在チェックを
      行い、evidence が実在しない場合は `InputCapabilityError`
      （generator / channel / path 入りのメッセージ）を送出する（CI での
      リンク切れ検知は repo root を明示して呼ぶ）

    path が存在しない・ディレクトリである場合は `InputCapabilityError` を
    送出する（`OSError` を `path.open()` 時点でラップし、生の OS 例外を
    呼び出し側の公開契約に晒さない）。YAML が非 mapping の場合は
    `ValueError`、スキーマ違反（未知 support 値 / 未知チャネル key / 未知
    トップ key）は pydantic の `ValidationError` を送出する（他 loader との
    共通契約のため、これらはラップしない）。
    """
    profile_path = Path(path)
    try:
        data = _load_yaml_mapping(profile_path)
    except OSError as exc:
        raise InputCapabilityError(
            f"input capability profile unreadable at {profile_path}: {exc}"
        ) from exc
    profile = InputCapabilityProfile.model_validate(data)

    for channel_name in INPUT_CHANNELS:
        capability: InputChannelCapability = getattr(profile.input_channels, channel_name)
        if capability.evidence is None:
            continue
        _validate_evidence_form(
            capability.evidence, generator=profile.generator, channel=channel_name
        )
        if evidence_base is None:
            continue
        evidence_path = _resolve_confined_evidence(
            capability.evidence,
            evidence_base,
            generator=profile.generator,
            channel=channel_name,
        )
        if not evidence_path.is_file():
            raise InputCapabilityError(
                f"input capability profile '{profile.generator}': channel "
                f"'{channel_name}' evidence not found at {evidence_path}"
            )

    return profile


def _validate_evidence_form(value: str, *, generator: str, channel: str) -> None:
    """evidence 文字列の形式検証（base 非依存・字面のみ）。

    可搬性契約（evidence は repo-root 相対の provenance 文字列）は base が
    無くても文字列だけで判定できるため、`evidence_base=None` でも常に行う:

    - 絶対パス拒否
    - 字面（lexical）の遡上拒否: `..` セグメントで深さカウンタを減らし、
      0 未満になったら evidence root より上への遡上として拒否する。
      `a/../b` のような内部相殺は 0 未満にならないので通る

    symlink 経由の脱出は字面では判定できないため、`evidence_base` 明示時の
    `_resolve_confined_evidence`（resolve() ベース封じ込め）が捕捉する。

    確認は共通 utility `arrange.pathsafe.validate_relative_locator` に委譲する
    （item 15: path 検証共通化）。ここで捕捉した `PathConfinementError` を
    既存の `InputCapabilityError` 型・既存メッセージ書式へ再送出するだけで、
    挙動は移行前と完全に同一。
    """
    try:
        validate_relative_locator(value)
    except PathConfinementError as exc:
        if exc.reason == "absolute":
            raise InputCapabilityError(
                f"input capability profile '{generator}': channel '{channel}' evidence "
                f"{value!r} must be a relative path inside the evidence base "
                f"(absolute paths are not allowed)"
            ) from exc
        raise InputCapabilityError(
            f"input capability profile '{generator}': channel '{channel}' evidence "
            f"{value!r} must not traverse above the evidence root"
        ) from exc


def _resolve_confined_evidence(
    value: str, base_dir: Path, *, generator: str, channel: str
) -> Path:
    """evidence 文字列を base_dir 配下に閉じた実パスへ解決する。

    絶対パスは `base_dir / value` が base を黙って無視するため拒否する。
    `resolve()` は `../` と symlink の両方を追うため、解決後のパスが
    `base_dir` 配下にないものは evidence_base 脱出として拒否する
    （identity.py の `_resolve_confined` と同型の可搬性契約: evidence は
    常に repo-root 相対）。

    確認は共通 utility `arrange.pathsafe.resolve_confined` に委譲する
    （item 15: path 検証共通化）。挙動・メッセージは移行前と完全に同一。
    """
    try:
        return resolve_confined(value, base_dir)
    except PathConfinementError as exc:
        if exc.reason == "absolute":
            raise InputCapabilityError(
                f"input capability profile '{generator}': channel '{channel}' evidence "
                f"{value!r} must be a relative path inside the evidence base "
                f"(absolute paths are not allowed)"
            ) from exc
        raise InputCapabilityError(
            f"input capability profile '{generator}': channel '{channel}' evidence "
            f"{value!r} escapes the evidence base {exc.base}: resolved to {exc.resolved}"
        ) from exc


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"input capability profile must be a mapping: {path}")
    return data
