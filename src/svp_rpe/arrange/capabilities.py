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

    schema_version: str
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
    ついて実ファイルの存在を確認する。

    `evidence_base` 省略時は `Path.cwd()`（dev/CI 実行時は repo root からの
    呼び出しを想定し、そこを基準に evidence の repo-root 相対パスを解決する）。
    呼び出し側は明示的に渡すことで cwd 非依存に検証できる。

    path が存在しない・ディレクトリである場合は `InputCapabilityError` を
    送出する（`OSError` を `path.open()` 時点でラップし、生の OS 例外を
    呼び出し側の公開契約に晒さない）。YAML が非 mapping の場合は
    `ValueError`、スキーマ違反（未知 support 値 / 未知チャネル key / 未知
    トップ key）は pydantic の `ValidationError` を送出する（他 loader との
    共通契約のため、これらはラップしない）。evidence が実在しない場合は
    `InputCapabilityError`（generator / channel / path 入りのメッセージ）を
    送出する。

    evidence は `evidence_base` 配下に閉じた相対パスでなければならない
    （identity.py の artifact 封じ込めと同じ可搬性契約）。絶対パス、および
    `../` や symlink 経由で `evidence_base` の外を指す evidence は実在
    チェックより前に fail-fast で拒否する — さもないと committed profile の
    リンク切れ検知が非可搬な evidence を見逃す。
    """
    profile_path = Path(path)
    try:
        data = _load_yaml_mapping(profile_path)
    except OSError as exc:
        raise InputCapabilityError(
            f"input capability profile unreadable at {profile_path}: {exc}"
        ) from exc
    profile = InputCapabilityProfile.model_validate(data)

    base_dir = evidence_base if evidence_base is not None else Path.cwd()
    for channel_name in INPUT_CHANNELS:
        capability: InputChannelCapability = getattr(profile.input_channels, channel_name)
        if capability.evidence is None:
            continue
        evidence_path = _resolve_confined_evidence(
            capability.evidence,
            base_dir,
            generator=profile.generator,
            channel=channel_name,
        )
        if not evidence_path.is_file():
            raise InputCapabilityError(
                f"input capability profile '{profile.generator}': channel "
                f"'{channel_name}' evidence not found at {evidence_path}"
            )

    return profile


def _resolve_confined_evidence(
    value: str, base_dir: Path, *, generator: str, channel: str
) -> Path:
    """evidence 文字列を base_dir 配下に閉じた実パスへ解決する。

    絶対パスは `base_dir / value` が base を黙って無視するため拒否する。
    `resolve()` は `../` と symlink の両方を追うため、解決後のパスが
    `base_dir` 配下にないものは evidence_base 脱出として拒否する
    （identity.py の `_resolve_confined` と同型の可搬性契約: evidence は
    常に repo-root 相対）。
    """
    if Path(value).is_absolute():
        raise InputCapabilityError(
            f"input capability profile '{generator}': channel '{channel}' evidence "
            f"{value!r} must be a relative path inside the evidence base "
            f"(absolute paths are not allowed)"
        )
    base = base_dir.resolve()
    resolved = (base / value).resolve()
    if not resolved.is_relative_to(base):
        raise InputCapabilityError(
            f"input capability profile '{generator}': channel '{channel}' evidence "
            f"{value!r} escapes the evidence base {base}: resolved to {resolved}"
        )
    return resolved


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"input capability profile must be a mapping: {path}")
    return data
