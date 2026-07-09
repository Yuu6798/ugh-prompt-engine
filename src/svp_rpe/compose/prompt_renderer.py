"""Deterministic prompt rendering for Composition Score documents.

PR1.5 で `control_profile` 駆動のフィールド粒度コンパイルに刷新:
- 粗い `physical.optional` 束を PhysicalLayer フィールド粒度トークンへ分解
  （`brightness` / `stereo_width` / `active_rate_target` / `valley_depth_target`）。
- `control_profile` が覆う tight フィールドを「芯」として先頭へ昇格し drop で最後まで残す。
  loose/dead は真っ先の削減候補へ格下げ。control_profile 外のセグメントは
  `rendering.priority`（エイリアス正規化済み）を fallback／tie-breaker に使う。
- backend 固有の制約を薄い `BackendDescriptor` に隔離し、`target_backend` →
  control_profile キーを決定論的に解決（`external`→`suno`）。

SEM-1 で `semantic.lyrics_presence`（歌詞の有無）を意味層の制御チャネルとして追加。
物理フィールドと同じ `control_profile` grip_class ランキングに乗るが、fixity 対象外
（docs/control_profile.md 参照）。

PR3 後半で generator device profile（`compose/device_profile.py`）を 2 経路で配線:
`control_profile` のフィールド欠落を `control_defaults` で補完（score 宣言が常に勝つ）、
score の値が既知の癖（`knob_quirks`）に当たったら `GeneratedPrompt.advisories` へ警告を
出す（プロンプト本文 / tags は一切変えない＝自動補正しない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from svp_rpe.compose.device_profile import DeviceProfile, KnobQuirk, load_device_profile
from svp_rpe.compose.models import (
    SEMANTIC_CONTROL_FIELDS,
    CompositionScore,
    ControlGrip,
    GeneratedPrompt,
    PhysicalLayer,
)

# 旧セグメントトークン → フィールド粒度トークンのエイリアス／移行マップ。
# 既存 `rendering.priority`（旧トークンで記述）の drop 順位契約を分割後も保つ。
# SEM-1: 意味層制御フィールドのドット表記（`semantic.lyrics_presence`）も
# フィールド粒度トークン（`lyrics_presence`）へ正規化する（bare 表記と等価に扱う）。
_PRIORITY_ALIAS: dict[str, list[str]] = {
    "physical.bpm": ["bpm"],
    "physical.key": ["key"],
    "physical.time_signature": ["time_signature"],
    "physical.optional": [
        "brightness",
        "stereo_width",
        "active_rate_target",
        "valley_depth_target",
    ],
    "semantic.lyrics_presence": ["lyrics_presence"],
}

# control_profile が覆う物理フィールドの描画ティア（小さいほど先頭・drop で後）。
_TIER_TIGHT = 0  # 保証チャネル: 芯として先頭描画・最後まで残す
_TIER_FALLBACK = 1  # 未プロファイル: rendering.priority 順
_TIER_ADVISORY = 2  # loose/dead: 助言・真っ先の削減候補


@dataclass(frozen=True)
class _PromptSegment:
    # 物理フィールドは PhysicalLayer.model_fields 正式名、意味層制御フィールドは
    # SEMANTIC_CONTROL_FIELDS のフィールド名そのまま（例 "lyrics_presence"）、他は
    # semantic.*/structure
    token: str
    text: str
    order: int


@dataclass(frozen=True)
class BackendDescriptor:
    """生成器固有の機械的制約を隔離する薄い記述子（seam を名前で引く）。

    `ExternalPromptAdapter.backend = "external"` が暗黙に Suno 化するのを防ぐ。
    """

    profile_key: str  # control_profile のキー（"suno" / "musicgen" / ...）
    negative_channel: str = "exclude_styles"  # 否定指定を流す欄（Suno=Exclude Styles）
    # K2-seg（#152, #162）実測: musicgen / suno はともに本文 "Avoid: X" が X の attractor
    # になる（musicgen: bright 系 Avoid で centroid d=+1.10・#152 / suno: K2-seg Suno
    # 転移バッチ1で d=+4.03・事前登録規約の attractor 確定閾値 d>=+0.8 該当・#162）。
    # True の backend は `semantic.avoid` 本文セグメントの送出を止める（negative_tags
    # は従来どおり保持）。実測なき横展開はしない — 効果が実測された backend のみ
    # True にする（docs/musicgen_backend.md §7.6 / docs/control_profile.md 参照）。
    # `external` は Suno ルートのエイリアスだが実測は Suno 生成そのものに対するもの
    # であり汎用 external への横展開はしない（#153 と同じ規律）ため不変（False）。
    omit_body_negative: bool = False


# target_backend → BackendDescriptor。`external` は Suno ルートのエイリアス。
_BACKEND_DESCRIPTORS: dict[str, BackendDescriptor] = {
    "external": BackendDescriptor(profile_key="suno"),
    "suno": BackendDescriptor(profile_key="suno", omit_body_negative=True),
    "musicgen": BackendDescriptor(
        profile_key="musicgen",
        negative_channel="negative_prompt",
        omit_body_negative=True,
    ),
}


def resolve_backend_descriptor(target_backend: str) -> BackendDescriptor:
    """`target_backend` を control_profile キー付き descriptor へ決定論的に解決。

    未知 backend は profile_key=backend 名の素の descriptor へフォールバック
    （未プロファイルの render として正当に通る）。`external` は明示的に `suno` を引く
    ため「未プロファイルの external render」へ黙って落ちない。
    """

    return _BACKEND_DESCRIPTORS.get(
        target_backend, BackendDescriptor(profile_key=target_backend)
    )


class ExternalPromptAdapter:
    """Render a CompositionScore for external text-to-music generators."""

    backend = "external"

    def render(self, score: CompositionScore, max_chars: int | None = None) -> GeneratedPrompt:
        limit = max(0, score.rendering.prompt_max_chars if max_chars is None else max_chars)
        descriptor = resolve_backend_descriptor(score.rendering.target_backend)
        device = load_device_profile(descriptor.profile_key)
        score_profile = (score.control_profile or {}).get(descriptor.profile_key)
        # device の control_defaults を score 宣言で上書き merge（score が常に勝つ）。
        # score 側が未宣言の backend / フィールドでも device defaults だけで成立する。
        effective_profile: dict[str, ControlGrip] = (
            dict(device.control_defaults) if device is not None else {}
        )
        if score_profile:
            effective_profile.update(score_profile)
        rank = _rank_key_factory(score.rendering.priority, effective_profile)

        segments = sorted(
            _segments_for(score, omit_avoid_segment=descriptor.omit_body_negative), key=rank
        )
        kept = list(segments)
        dropped_elements: list[str] = []

        while _join_segments(kept) and len(_join_segments(kept)) > limit:
            # ランク最大（advisory → 低優先 fallback → tight の順）を先に落とす。
            segment = max(kept, key=rank)
            kept.remove(segment)
            dropped_elements.append(segment.token)

        return GeneratedPrompt(
            backend=_generated_backend(score.rendering.target_backend),
            text=_join_segments(kept),
            tags=_tags_for(score),
            negative_tags=list(score.semantic.avoid),
            dropped_elements=dropped_elements,
            advisories=_advisories_for(score, device),
        )


def _field_value_for_quirk(score: CompositionScore, field: str) -> Any:
    """`KnobQuirk.field` に対応するスコア上の現在値を取得する（無ければ None）。

    `SEMANTIC_CONTROL_FIELDS` はドット付き表記（`semantic.avoid` / `semantic.core`）と
    bare 表記（`lyrics_presence`）が混在するが、`score.semantic` の実属性名は常に
    bare（`avoid` / `core` / `lyrics_presence`）。ドット付きはプレフィクスを剥がして
    解決する（さもないと `getattr(score.semantic, "semantic.avoid", None)` は属性名に
    `.` を含むため常に存在せず None フォールバックに落ちる＝quirk が発火しない）。
    """

    if field in SEMANTIC_CONTROL_FIELDS:
        attr = field.rsplit(".", 1)[-1] if field.startswith("semantic.") else field
        return getattr(score.semantic, attr, None)
    if field in PhysicalLayer.model_fields:
        return getattr(score.physical, field)
    return None


def _quirk_matches(quirk: KnobQuirk, value: Any) -> bool:
    """発火条件を満たすか判定する。

    3 通りの発火経路:
    - `applies_to_values`: 値の完全一致（str() 比較）
    - `applies_below` / `applies_above`: 数値閾値（int のみ。TODO(transcribe) センチネル
      文字列は isinstance(value, int) が False になるため自然にスキップされる）
    - 制約なし（`applies_to_values` 空 かつ 閾値未設定）× advisory 非 null: 値が
      「使われている」（None でない、list/str なら非空）だけで発火する
      （例: musicgen `semantic.avoid` — 一致すべき固定値も閾値も存在しない癖）。
    """

    if value is None:
        return False
    if quirk.applies_to_values and str(value) in quirk.applies_to_values:
        return True
    has_threshold = quirk.applies_below is not None or quirk.applies_above is not None
    if has_threshold:
        if isinstance(value, int) and not isinstance(value, bool):
            if quirk.applies_below is not None and value < quirk.applies_below:
                return True
            if quirk.applies_above is not None and value > quirk.applies_above:
                return True
        return False
    if not quirk.applies_to_values and quirk.advisory is not None:
        if isinstance(value, (list, str)):
            return bool(value)
        return True
    return False


def _advisories_for(score: CompositionScore, device: Optional[DeviceProfile]) -> list[str]:
    """デバイスプロファイルの knob_quirks を走査し、発火した advisory 文言を集める。

    プロンプト text / tags / negative_tags / dropped_elements には一切影響しない
    （自動補正しない・quirk 定義順で決定論）。
    """

    if device is None:
        return []
    advisories: list[str] = []
    for quirk in device.knob_quirks:
        if quirk.advisory is None:
            continue
        value = _field_value_for_quirk(score, quirk.field)
        if _quirk_matches(quirk, value):
            advisories.append(quirk.advisory)
    return advisories


def _segments_for(
    score: CompositionScore, *, omit_avoid_segment: bool = False
) -> list[_PromptSegment]:
    segments: list[_PromptSegment] = []

    def add(token: str, text: str) -> None:
        if text:
            segments.append(_PromptSegment(token=token, text=text, order=len(segments)))

    grv_values = [
        value for value in [score.semantic.grv.primary, score.semantic.grv.secondary] if value
    ]
    if grv_values:
        add("semantic.grv", f"{' / '.join(grv_values)} track.")

    # brightness は専用フィールドトークンが単独所有する（独立 keep/drop を成立させるため、
    # semantic.core の形容詞重複は除去。PR1.5 の duplication 整理）。
    # casing は先頭1文字のみ大文字化し、acronym/固有名（例 "AI EDM"）の意図的な casing を保つ。
    add("semantic.core", f"{_sentence_case(score.semantic.core)} atmosphere.")
    add("bpm", _bpm_text(score.physical.bpm))
    add("key", f"{score.physical.key}.")
    # time_signature も control_profile が tight 宣言しうる保証チャネルなのでフィールド粒度で描画
    # （宣言したのに描画/保持されない穴を塞ぐ）。
    add("time_signature", f"{score.physical.time_signature} time.")

    for section in score.structure:
        add(
            "structure",
            f"{section.section}: {section.physical}; role={section.role}.",
        )

    # 旧 physical.optional 束をフィールド粒度トークンへ分解（各々独立に keep/drop 可能）。
    add("brightness", f"Brightness {score.physical.brightness}.")
    add("stereo_width", f"{_sentence_case(score.physical.stereo_width)} stereo.")
    add("active_rate_target", f"Active rate {score.physical.active_rate_target}.")
    add("valley_depth_target", f"Valley depth {score.physical.valley_depth_target}.")

    # SEM-1: 歌詞の有無（意味層の制御チャネル）。未指定なら描画しない。
    if score.semantic.lyrics_presence == "present":
        add("lyrics_presence", "With vocals.")
    elif score.semantic.lyrics_presence == "absent":
        add("lyrics_presence", "Instrumental, no vocals.")

    # K2-seg（#152 musicgen, #162 suno）実測: musicgen / suno は本文 Avoid が attractor
    # 化するため、当該 backend の descriptor（`omit_body_negative=True`）は本文
    # セグメントの送出自体を止める。
    # `negative_tags`（GeneratedPrompt 側）は render() 側で従来どおり保持するため、
    # 楽譜の意図（避けたい要素の記録）は失われない。「字数超過 drop」とは区別し、
    # ここで候補にすら入れないことで `dropped_elements` にも現れない
    # （drop accounting との混同を避ける — Design Memo 判断 4）。
    if score.semantic.avoid and not omit_avoid_segment:
        add("semantic.avoid", f"Avoid: {'; '.join(score.semantic.avoid)}.")

    return segments


def _normalize_priority(priority: list[str]) -> list[str]:
    """旧セグメントトークンをフィールド粒度トークンへ展開（順序保存）。"""

    normalized: list[str] = []
    for token in priority:
        normalized.extend(_PRIORITY_ALIAS.get(token, [token]))
    return normalized


_PHYSICAL_FIELDS = frozenset(PhysicalLayer.model_fields)
# control_profile がグリップを引ける全トークン（物理フィールド + 意味層制御フィールド）。
_CONTROL_PROFILE_FIELDS = _PHYSICAL_FIELDS | SEMANTIC_CONTROL_FIELDS


def _rank_key_factory(
    priority: list[str],
    profile: dict[str, ControlGrip] | None,
):
    """セグメント → (tier, priority_index, order) のランクキーを返すクロージャ。

    昇順ソート = 描画順、最大 = 最初に落とす drop 対象。tight=先頭/drop最後、
    loose/dead=末尾/drop最初、未プロファイルは正規化 priority 順。
    """

    normalized = _normalize_priority(priority)
    priority_index = {token: index for index, token in enumerate(normalized)}
    unlisted_index = len(normalized)

    def rank(segment: _PromptSegment) -> tuple[int, int, int]:
        grip = (
            profile.get(segment.token)
            if profile and segment.token in _CONTROL_PROFILE_FIELDS
            else None
        )
        if grip is None:
            tier = _TIER_FALLBACK
        elif grip.grip_class == "tight":
            tier = _TIER_TIGHT
        else:  # loose / dead
            tier = _TIER_ADVISORY
        return (tier, priority_index.get(segment.token, unlisted_index), segment.order)

    return rank


# GeneratedPrompt.backend は出力チャネルの種別（Literal）。target_backend が
# musicgen/midi ならそれを反映し、external/suno 等の外部テキスト系は "external" に正規化する
# （非 external render を external と誤ラベルしない）。
_NON_EXTERNAL_GENERATED_BACKENDS = frozenset({"musicgen", "midi"})


def _generated_backend(target_backend: str) -> str:
    if target_backend in _NON_EXTERNAL_GENERATED_BACKENDS:
        return target_backend
    return "external"


def _sentence_case(text: str) -> str:
    """先頭1文字だけ大文字化する（`str.capitalize` と違い 2 文字目以降の意図的な
    casing＝acronym/固有名/ジャンル表記 "AI EDM" 等を保つ）。"""

    return text[:1].upper() + text[1:]


def _join_segments(segments: list[_PromptSegment]) -> str:
    return " ".join(segment.text for segment in segments)


def _bpm_text(bpm: int | str) -> str:
    if isinstance(bpm, int) and not isinstance(bpm, bool):
        return f"{bpm} BPM."
    return f"{bpm}."


def _tags_for(score: CompositionScore) -> list[str]:
    tags = [
        score.semantic.grv.primary,
        score.semantic.grv.secondary,
        score.physical.brightness,
        f"{score.physical.stereo_width}_stereo",
    ]
    if score.semantic.lyrics_presence == "absent":
        tags.append("instrumental")
    return tags
