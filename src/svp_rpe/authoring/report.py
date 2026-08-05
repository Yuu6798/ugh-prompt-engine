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


class ObservedSection(BaseModel):
    """structure 軸の 1 観測セクションの境界秒（観測①）。

    境界宣言: このモデルはスキーマとしてのみ定義され、このリポジトリの
    どの生産器（`measure_round.py` 相当・`svprpe observe`）もまだ
    populate しない——配線は L0b の仕事。

    `start_seconds`/`end_seconds` の制約（PR #246 Codex P2 review 7 巡目
    B）: 両方 `>= 0`（`Field(ge=0)`）、かつ `end_seconds > start_seconds`
    （ゼロ長・逆転区間を拒否）。境界秒は観測器が返す実測値であり、負値や
    逆転区間は計器・生産器側のバグを表す——著者に見せる報告として意味を
    なさない壊れた区間を、スキーマの時点で構成不能にする。"""

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
    報告のまま許容する）。"""

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


def dump_json_bytes(model: BaseModel) -> bytes:
    """`model` をバイト決定論 JSON へ直列化する。

    `examples/l0s_spike/scripts/validate_score.py`/`measure_round.py` と
    同じ規約: `sort_keys=True` + 末尾改行 + UTF-8 encode の結果を
    `write_bytes` へそのまま渡せる形で返す（`write_text` のプラットフォーム
    依存改行変換を避ける）。`exclude_none=True` で歴史的 `report.json`
    （`symbolic_validation.errors`/`AxisReport.observed_sections` を省略した
    形）とバイト互換の欠落キー省略を保つ。
    """

    payload = model.model_dump(mode="json", exclude_none=True)
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
