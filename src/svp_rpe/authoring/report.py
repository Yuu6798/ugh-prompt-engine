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
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.authoring.validate import SymbolicValidationResult

SCHEMA_VERSION = "authoring-diff-report/1.0"

Band = Literal["measured", "out_of_band", "not_observed"]

# 白リスト（観測③）。新規 kind を追加する際はこの Literal とモジュール
# docstring の一覧を両方更新すること。
AuthoringNoteKind = Literal["position_match_rate"]


class ObservedSection(BaseModel):
    """structure 軸の 1 観測セクションの境界秒（観測①）。

    境界宣言: このモデルはスキーマとしてのみ定義され、このリポジトリの
    どの生産器（`measure_round.py` 相当・`svprpe observe`）もまだ
    populate しない——配線は L0b の仕事。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    start_seconds: float
    end_seconds: float


class AxisReport(BaseModel):
    """1 軸（key/brightness/structure 等）の差分報告。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement: Any
    observed: Any
    verdict: str
    band: Band
    observed_sections: Optional[list[ObservedSection]] = None


class AuthoringNote(BaseModel):
    """構造化された参考値 1 件（観測③。自由文の notes は禁止）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AuthoringNoteKind
    value: Any


class AuthoringDiffReport(BaseModel):
    """L0a 差分報告の正規形（`report.json`）。

    `axes` はキーが軸名（`key`/`brightness`/`structure` 等、事前登録課題が
    宣言する軸集合に従う——固定の軸名 Literal ではない、契約側
    `docs/l0a_authoring_contract.md` (e) の信頼軸表がどの軸を許可するかを
    決める）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["authoring-diff-report/1.0"] = SCHEMA_VERSION
    round: int
    symbolic_validation: SymbolicValidationResult
    axes: dict[str, AxisReport] = Field(default_factory=dict)
    notes: list[AuthoringNote] = Field(default_factory=list)


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
