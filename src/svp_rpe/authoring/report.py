"""authoring/report.py — L0a 差分報告 (`report.json`) 正規形の凍結 (D-L0a-4).

`docs/l0s_spike_record.md` §3.3 の観測 3 点を報告スキーマへ編入する:

1. structure 軸の境界時刻（`observed_sections`）——観測 ①。**スキーマとして
   定義するのみ**で、この PR ではどの生産器も populate しない（L0b の境界
   宣言。`svprpe observe` の structure sensor が返す `measurements` に境界
   秒はまだ無く、配線は L0b の仕事）。
2. 計器の分解能・可行域の開示——観測 ②。`docs/l0a_authoring_contract.md`
   （文書側）に編入、このモジュールはスキーマ側の変更なし。
3. `notes` の使用規約——観測 ③。`kind` を白リスト `Literal` に制限する
   （`AuthoringNoteKind`）。新しい kind を追加するときはこの `Literal` と
   下の docstring を両方更新すること（M3d の狙い撃ち negative と同型の
   ドリフト防止）。現在許可されている kind:
     - `"position_match_rate"`: structure 軸の正規化ラベル列の位置一致率
       （`svp_rpe.arrange.observe` の `measurements["position_match_rate"]`
       と同じ定義）を参考値として運ぶ。

`AuthoringDiffReport` は既存 `examples/l0s_spike/rounds/round{1..5}/report.json`
（歴史的成果物、凍結・不変更）と後方互換——`tests/test_authoring_report.py` が
5 件全件のバイトをこのスキーマで parse できることを確認する。
"""
from __future__ import annotations

import json
import math
from typing import Any, Literal, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.authoring.validate import SymbolicValidationResult

SCHEMA_VERSION = "authoring-diff-report/1.0"

Band = Literal["measured", "out_of_band", "not_observed"]
Verdict = Literal["preserved", "deviated", "exact_match", "mismatch"]

# 既知軸ごとの許容 verdict 語彙（PR #246 Codex P2 review 7 巡目 A）。ここに
# 無い軸名は `Verdict` の全語彙を許容する（将来軸の拡張余地——正式な軸集合は
# `docs/l0a_authoring_contract.md` (e) の信頼軸表が定義し、report スキーマ
# 自体は先回りして固定軸 Literal にしない、という既存方針の踏襲。境界宣言:
# ここでの制約は「軸名が分かっているときの verdict 語彙の妥当性」のみで、
# 軸集合そのものを固定しない）。
_AXIS_VERDICTS: dict[str, frozenset[str]] = {
    "key": frozenset({"preserved", "deviated"}),
    "brightness": frozenset({"preserved", "deviated"}),
    "structure": frozenset({"exact_match", "mismatch"}),
}

# `observed_sections` を持てる軸名のホワイトリスト（PR #246 Codex P2 review
# 13 巡目）。verdict 語彙（`_AXIS_VERDICTS`）と異なり未知軸を先回りして
# 許容しない——このフィールド自体が structure 軸の境界時刻としてスキーマ
# 設計されているため、将来軸が同フィールドを名乗るには明示的な追加が必要。
_OBSERVED_SECTIONS_AXES: frozenset[str] = frozenset({"structure"})

# 白リスト（観測③）。新規 kind を追加する際はこの Literal とモジュール
# docstring の一覧を両方更新すること。
AuthoringNoteKind = Literal["position_match_rate"]


def _validate_position_match_rate(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"position_match_rate value must be a number (bool excluded), got {value!r}"
        )
    if not math.isfinite(value):
        raise ValueError(f"position_match_rate value must be finite (NaN/inf rejected): {value!r}")
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"position_match_rate value must be within [0.0, 1.0], got {value!r}")


# kind ごとの値検証（PR #246 Codex P2 review 8 巡目 A）。`AuthoringNoteKind`
# へ新規 kind を追加する際は、この辞書へも対応する検証関数を**同時に**追加
# すること——欠けている kind は `AuthoringNote` 構築時に `ValueError`
# （spec/report ロードの `ValidationError`）で fail-closed になる
# （白リストに kind だけ追加して値検証を忘れる、という片手落ちを防ぐ）。
_NOTE_VALUE_VALIDATORS: dict[str, Any] = {
    "position_match_rate": _validate_position_match_rate,
}


def _reject_non_finite_float(value: Any, *, field_name: str) -> None:
    """PR #246 Codex P2 review 9 巡目 B（直値）+ 11 巡目（ネスト、この関数の
    再帰拡張）で非有限値ファミリーを全数被覆する: `requirement`/`observed`
    は `Any` 型のまま（str/list/dict 等の任意の要求・実測値を運ぶ必要が
    あるため）だが、値が `float`（直値、または JSON 様コンテナ `list`/
    `dict` の値としてネストした位置）のときだけ有限性（`math.isfinite`）を
    fail-fast で強制する——`bool`（`int` のサブクラスだが `float` では
    ない）や `int`/`str`/`None` はここでの対象外、そのまま通す（文字列
    `"NaN"` は文字列としてそのまま通る——`float` 型でなければ検査対象外）。
    `dict` はキーではなく値のみ再帰する（JSON のキーは常に文字列であり
    `float` になり得ないため）。再帰は素朴な深さ優先で十分——この
    report スキーマの `requirement`/`observed` が運ぶ構造（軸のラベル列・
    単純な入れ子）は実用上浅く、決定論的に全要素を訪問できれば足りる。

    構築時にここで弾いておくことで、`dump_json_bytes` の `allow_nan=False`
    が事後的に `ValueError` として間接的に検出するのではなく、生成の時点で
    意図が明確な `ValidationError` として拒否できる——`allow_nan=False` は
    この構築時ガードをすり抜けた場合（例: `model_construct` でガード自体を
    迂回した裸のモデル）に備えた最終防衛線として残す。"""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite (NaN/inf rejected): {value!r}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite_float(item, field_name=f"{field_name}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_float(item, field_name=f"{field_name}[{key!r}]")
        return


class ObservedSection(BaseModel):
    """structure 軸の 1 観測セクションの境界秒（観測①）。

    境界宣言: このモデルはスキーマとしてのみ定義され、このリポジトリの
    どの生産器（`measure_round.py` 相当・`svprpe observe`）もまだ
    populate しない——配線は L0b の仕事。

    `start_seconds`/`end_seconds` の制約（PR #246 Codex P2 review 7 巡目
    B）: 両方 `>= 0`（`Field(ge=0)`）、かつ `end_seconds > start_seconds`
    （ゼロ長・逆転区間を拒否）。境界秒は観測器が返す実測値であり、負値や
    逆転区間は計器・生産器側のバグを表す——著者に見せる報告として意味を
    なさない壊れた区間を、スキーマの時点で構成不能にする。`inf` は
    `Field(ge=0)` を素通りしてしまう（`inf >= 0` は真）ため、9 巡目 B で
    別途有限性チェックを追加した。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end_seconds <= self.start_seconds:
            raise ValueError(
                f"end_seconds ({self.end_seconds}) must be greater than "
                f"start_seconds ({self.start_seconds}) — zero-length and reversed "
                "intervals are rejected"
            )
        return self

    @model_validator(mode="after")
    def _boundaries_are_finite(self) -> Self:
        _reject_non_finite_float(self.start_seconds, field_name="start_seconds")
        _reject_non_finite_float(self.end_seconds, field_name="end_seconds")
        return self


_SUCCESS_VERDICTS = frozenset({"preserved", "exact_match"})


class AxisReport(BaseModel):
    """1 軸（key/brightness/structure 等）の差分報告。

    `verdict` は `Verdict` 語彙に固定する（PR #246 Codex P2 review 7 巡目
    A）——凍結語彙外の任意文字列を受理していた欠陥を閉じる。軸名と verdict
    の対応（`key`/`brightness` は `preserved`/`deviated`、`structure` は
    `exact_match`/`mismatch`）は `AuthoringDiffReport` 側の
    `model_validator` が強制する（`AxisReport` 単体は軸名を知らないため
    ここでは語彙全体のみを検証する）。

    `verdict`×`band` の整合（PR #246 Codex P2 review 8 巡目 B、D5 の
    成功会計除外規則のスキーマ側強制）: 成功側 verdict（`preserved`/
    `exact_match`）は `band == "measured"` のときのみ許容する——
    `out_of_band`/`not_observed` の数値・ラベルは修正の根拠に使っては
    ならないという D5 の規則（正本 §5 の帯域注釈規律）を、成功宣言にも
    適用する。失敗側 verdict（`deviated`/`mismatch`）は非 `measured`
    band でも許容する（帯域外・未観測を理由に「preserved と偽装した
    失敗」を防ぐのが目的で、「非 measured band では常に fail」を強制する
    ものではない——`not_observed`/`out_of_band` な `deviated`/`mismatch`
    は「確認できていないが preserved を主張していない」という正直な
    報告のまま許容する）。

    `requirement`/`observed` は `Any`（str/list/dict 等の任意の要求・実測値
    を運ぶ）のままだが、`float` が来た場合のみ有限性を強制する（PR #246
    Codex P2 review 9 巡目 B の直値検証 + 11 巡目のネスト（`list`/`dict`
    内の `float`）再帰へ拡張、`_reject_non_finite_float`——非有限値
    ファミリーはこれで全数被覆・`dump_json_bytes` の `allow_nan=False` は
    最終防衛線）。文字列（`"NaN"` 含む）や整数・真偽値・`None` はそのまま
    通す。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement: Any
    observed: Any
    verdict: Verdict
    band: Band
    observed_sections: Optional[list[ObservedSection]] = None

    @model_validator(mode="after")
    def _success_verdict_requires_measured_band(self) -> Self:
        if self.verdict in _SUCCESS_VERDICTS and self.band != "measured":
            raise ValueError(
                f"verdict={self.verdict!r} requires band='measured' "
                f"(got band={self.band!r}) — a success verdict outside the measured "
                "band would smuggle an untrusted number/label past D5's band-annotation "
                "discipline"
            )
        return self

    @model_validator(mode="after")
    def _requirement_and_observed_floats_are_finite(self) -> Self:
        _reject_non_finite_float(self.requirement, field_name="requirement")
        _reject_non_finite_float(self.observed, field_name="observed")
        return self


class AuthoringNote(BaseModel):
    """構造化された参考値 1 件（観測③。自由文の notes は禁止）。

    `value` は `kind` に応じて `_NOTE_VALUE_VALIDATORS` で検証する
    （PR #246 Codex P2 review 8 巡目 A——従来 `value: Any` で無検証だった）。
    `position_match_rate` は有限数（`bool` 除外、`NaN`/`inf` 拒否）かつ
    `0.0 <= value <= 1.0`（`svp_rpe.arrange.observe` の
    `position_match_rate` が正規化ラベル列の一致率という比率のため）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AuthoringNoteKind
    value: Any

    @model_validator(mode="after")
    def _value_matches_kind(self) -> Self:
        validator = _NOTE_VALUE_VALIDATORS.get(self.kind)
        if validator is None:  # pragma: no cover - guarded by AuthoringNoteKind Literal
            raise ValueError(
                f"no value validator registered for AuthoringNoteKind {self.kind!r} — "
                "add one to _NOTE_VALUE_VALIDATORS alongside the Literal entry"
            )
        validator(self.value)
        return self


class AuthoringDiffReport(BaseModel):
    """L0a 差分報告の正規形（`report.json`）。

    `axes` はキーが軸名（`key`/`brightness`/`structure` 等、事前登録課題が
    宣言する軸集合に従う——固定の軸名 Literal ではない、契約側
    `docs/l0a_authoring_contract.md` (e) の信頼軸表がどの軸を許可するかを
    決める）。

    既知軸の verdict 整合（PR #246 Codex P2 review 7 巡目 A）: `_AXIS_VERDICTS`
    に載る軸名（`key`/`brightness`/`structure`）は、その軸専用の verdict
    部分集合に一致することを spec ロード時に強制する（例: `key` 軸に
    `exact_match`/`mismatch` を書くと拒否——`Verdict` 全体としては合法でも
    その軸には意味論的に不整合）。**境界宣言**: `_AXIS_VERDICTS` に無い軸名
    （将来 L0b が追加する軸）は `Verdict` の全語彙をそのまま許容する——この
    report スキーマは軸集合そのものを固定しないという既存方針
    （docstring 上段）を維持するため。

    `observed_sections` の軸限定（PR #246 Codex P2 review 13 巡目、7 巡目 A
    と同族の軸不整合）: `AxisReport.observed_sections` は「structure 軸の
    境界時刻」として文書化・設計されたフィールド（`ObservedSection`
    docstring 参照）だが、`AxisReport` 単体は軸名を知らないため、
    `axes["key"].observed_sections` のような軸不整合な組み合わせを構成
    できてしまう。`AuthoringDiffReport` 側で軸名が `"structure"` の場合の
    みこのフィールドの非 None/非空値を許容し、`"structure"` 以外（`key`/
    `brightness`、および 7 巡目の verdict 語彙とは異なり**未知軸も含む**）
    では拒否する。**7 巡目との線引き**: `_AXIS_VERDICTS` の未知軸許容は
    「verdict という汎用語彙は将来軸でも意味を持ちうる」という判断だが、
    `observed_sections` は verdict のような汎用語彙ではなく `structure`
    軸の境界時刻という**スキーマ自体が構造軸固有**のフィールドであり、
    未知軸がこれを名乗る根拠がない——将来 L0b が境界時刻を持つ新しい軸を
    追加する場合は、このホワイトリスト（`_OBSERVED_SECTIONS_AXES`）へ
    軸名を明示的に追加すること。

    `symbolic_validation.status`×`axes`×`notes` の provenance 整合（PR #246
    Codex P2 review 10 巡目 + 12 巡目、4〜5 巡目の `SymbolicValidationResult`
    内 `status`×`errors` 整合と同族）: `status == "fail"` の報告は `axes`
    と `notes` の両方が空でなければならない。正本 §3 のフロー（`[2] 記号
    検証ゲート` を通過した Score だけが `[3] 実行と計測` へ進む）上、記号
    検証に落ちた Score は決して計測されない——`axes` に測定済みらしき値が
    乗っている、または `notes` に測定由来の参考値（現行の白リストは
    `position_match_rate` のみで、これは構造観測器由来の実測値）が乗って
    いる `symbolic_validation.status: fail` の報告は、いずれもフローの
    因果関係と矛盾する provenance であり構成不能にする（12 巡目: `axes`
    のみのガードでは `notes` 経由で同じ矛盾が抜けていた同族の残り穴）。
    `status == "pass"` では `axes`/`notes` の空/非空いずれも許容する
    （合格直後でまだ軸別計測を報告していない中間状態などを排除しない）。

    **終端宣言**: この一律ガードは `AuthoringNoteKind` の現行白リスト
    （`_NOTE_VALUE_VALIDATORS` 参照）が全 kind 観測器由来の測定 provenance
    である前提に依存する。将来、著者の意図表明など**非測定系**の kind を
    白リストへ追加する場合は、この検証を「全 notes 空必須」から「kind ごと
    に測定系/非測定系を区別する」形へ再設計する必要がある——現時点でその
    区別を先回りして作らないのは、まだ非測定系 kind が存在しないため
    （YAGNI）。新規 kind を追加する開発者はこの docstring と検証本体を
    合わせて見直すこと。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["authoring-diff-report/1.0"] = SCHEMA_VERSION
    round: int
    symbolic_validation: SymbolicValidationResult
    axes: dict[str, AxisReport] = Field(default_factory=dict)
    notes: list[AuthoringNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_axis_verdicts_are_consistent(self) -> Self:
        errors = []
        for axis_name, axis_report in self.axes.items():
            allowed = _AXIS_VERDICTS.get(axis_name)
            if allowed is not None and axis_report.verdict not in allowed:
                errors.append(
                    f"axes[{axis_name!r}].verdict={axis_report.verdict!r} is not valid for "
                    f"axis {axis_name!r} (allowed: {sorted(allowed)!r})"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _observed_sections_are_structure_only(self) -> Self:
        errors = []
        for axis_name, axis_report in self.axes.items():
            if axis_report.observed_sections and axis_name not in _OBSERVED_SECTIONS_AXES:
                errors.append(
                    f"axes[{axis_name!r}].observed_sections is not valid for axis "
                    f"{axis_name!r} — observed_sections is structure-only "
                    f"(allowed axes: {sorted(_OBSERVED_SECTIONS_AXES)!r})"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _failed_symbolic_validation_has_no_axes_or_notes(self) -> Self:
        if self.symbolic_validation.status != "fail":
            return self
        errors = []
        if self.axes:
            errors.append(
                "symbolic_validation.status='fail' must not carry axes "
                f"(got {sorted(self.axes)!r})"
            )
        if self.notes:
            errors.append(
                "symbolic_validation.status='fail' must not carry notes "
                f"(got {len(self.notes)} note(s)) — the current AuthoringNoteKind allowlist "
                "is entirely observer-derived measurement provenance"
            )
        if errors:
            raise ValueError(
                "; ".join(errors) + " — a Score that fails the symbolic gate never reaches "
                "measurement (正本 §3: [2] gate -> [3] measure only on pass), so "
                "measured-looking axes/notes on a failed report is contradictory provenance"
            )
        return self


def dump_json_bytes(model: BaseModel) -> bytes:
    """`model` をバイト決定論 JSON へ直列化する。

    `examples/l0s_spike/scripts/validate_score.py`/`measure_round.py` と
    同じ規約: `sort_keys=True` + 末尾改行 + UTF-8 encode の結果を
    `write_bytes` へそのまま渡せる形で返す（`write_text` のプラットフォーム
    依存改行変換を避ける）。`exclude_none=True` で歴史的 `report.json`
    （`symbolic_validation.errors`/`AxisReport.observed_sections` を省略した
    形）とバイト互換の欠落キー省略を保つ。

    `allow_nan=False`（PR #246 Codex P2 review 9 巡目 B）: 既定の
    `json.dumps` は `NaN`/`Infinity`/`-Infinity` を（RFC 8259 準拠でない）
    非標準リテラルとして書き出してしまう——`AxisReport`/`ObservedSection`
    の構築時 fail-fast（`_reject_non_finite_float`）で通常はここまで
    到達しないが、直列化そのものにも防御として `allow_nan=False` を掛け、
    万一非有限値を積んだモデルが構築できてしまった場合でも不正 JSON を
    出力する前に `ValueError` で fail-closed にする（二重の安全網）。
    """

    payload = model.model_dump(mode="json", exclude_none=True)
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
