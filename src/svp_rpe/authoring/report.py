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
from svp_rpe.keys import keys_enharmonically_equal

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
    "bpm": frozenset({"preserved", "deviated"}),
    "key": frozenset({"preserved", "deviated"}),
    "brightness": frozenset({"preserved", "deviated"}),
    "structure": frozenset({"exact_match", "mismatch"}),
}

# `observed_sections` を持てる軸名のホワイトリスト（PR #246 Codex P2 review
# 13 巡目）。verdict 語彙（`_AXIS_VERDICTS`）と異なり未知軸を先回りして
# 許容しない——このフィールド自体が structure 軸の境界時刻としてスキーマ
# 設計されているため、将来軸が同フィールドを名乗るには明示的な追加が必要。
_OBSERVED_SECTIONS_AXES: frozenset[str] = frozenset({"structure"})

# brightness の信頼帯（PR #246 Codex P2 review 16 巡目）。出典 =
# `config/authoring_trusted_axes_l0.yaml` の `axes.brightness.
# band_restriction.trusted_values`（凍結信頼軸表。導出元は
# `src/svp_rpe/authoring/trusted_axes.py:_BAND_RESTRICTIONS["brightness"]`）
# ——bright 帯は演奏者の押し込み不足が正本 §5 で実測済みのため、dark 帯
# のみを信頼する。**ハードコード定数にする理由**: `report.py` が
# `trusted_axes.py`（さらにその出典計器）を import すると
# `authoring/` パッケージ内に望まない依存循環が生まれるため、値をここへ
# 複製する。**ドリフト検出**: `tests/test_trusted_axes.py::
# test_report_trusted_brightness_values_matches_derived_trusted_axes` が
# 「この定数 == `derive_trusted_axes()` の brightness `band_restriction.
# trusted_values`」の一致を enforce する（`test_trusted_axes.py` 既存の
# 「再導出 == 凍結ファイル」一致テストと同型のドリフト防止）。信頼軸表
# 側（`trusted_axes.py`/凍結 YAML）を変更したら、この定数も同期して
# 更新すること——同期を怠るとこのドリフトテストが赤くなる。
_TRUSTED_BRIGHTNESS_VALUES: frozenset[str] = frozenset({"dark"})

# 凍結信頼軸表が採用しなかった既知除外軸（PR #246 Codex P2 review 17 巡目
# B）。出典 = `svp_rpe.roundtrip.diagnose.ROUNDTRIP_FIELDS`（R0 往復診断が
# 扱う 7 フィールド）から `derive_trusted_axes()` が採用した軸集合
# （`bpm`/`key`/`brightness`）を引いた残り: K1 grip map で
# `classification == "dead"`（演奏者のつまみが死んでいる）な
# `active_rate_target`/`valley_depth_target`、恒常 `sensor_blind`
# （`stereo_width`/`time_signature`）——`trusted_axes.py` モジュール
# docstring 参照。これらは出典計器の構造上「成功 evidence への算入資格が
# ない」と判断され信頼軸表から除外された軸であり、report がこれらの軸で
# 成功側 verdict（`preserved`/`exact_match`）を主張することは band を問わず
# 拒否する（凍結表が除外した軸に対し report 側だけが独自に成功を主張できて
# しまう抜け道を塞ぐ）。失敗側 verdict（`deviated`/`mismatch`）や
# `not_observed` band での正直な報告は引き続き許容する。`bpm` は
# `runtime_gate` 付きで信頼表に**収載済み**（実験ごとの動的判定の対象で
# あり、静的な除外軸ではない）ためこのリストに含めない。**ドリフト検出**:
# `tests/test_trusted_axes.py::
# test_report_known_excluded_axes_matches_roundtrip_fields_minus_derived_axes`
# が「この定数 == `ROUNDTRIP_FIELDS` から `derive_trusted_axes()` の採用軸
# を引いた集合」の一致を enforce する（16 巡目のドリフト検出と同型）。
_KNOWN_EXCLUDED_AXES: frozenset[str] = frozenset(
    {"active_rate_target", "valley_depth_target", "stereo_width", "time_signature"}
)

# 白リスト（観測③）。新規 kind を追加する際はこの Literal とモジュール
# docstring の一覧を両方更新すること。
AuthoringNoteKind = Literal["position_match_rate"]


def _is_str_list(value: Any) -> bool:
    """`value` が「全要素 `str` の `list`」かどうか（PR #246 Codex P2
    review 18 巡目 C）。`bool` は除外しない仕様——`structure` 軸の
    `requirement`/`observed` はラベル列（`["intro", "chorus", ...]`）
    であり `bool` 要素はそもそも意味を持たないため、他所の
    `bool` 除外パターン（`_validate_position_match_rate` 等の数値系）を
    ここに合わせて複製する必要はない。"""

    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_finite_numeric(value: Any) -> bool:
    """`value` が「有限数値」（`int` または有限 `float`）かどうか
    （PR #246 Codex P2 review 20 巡目）。`bool` は `int` のサブクラスだが
    除外する（`True`/`False` は `bpm` の型不正——他所の `bool` 除外
    パターン、`_validate_position_match_rate` 等と同じ理由）。`float` は
    `math.isfinite` で `NaN`/`inf`/`-inf` を拒否する。"""

    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


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

    bpm の既知軸化 + 成功側の型形状強制（PR #246 Codex P2 review 20 巡目）:
    `bpm` は凍結信頼軸表（`config/authoring_trusted_axes_l0.yaml`）に
    `runtime_gate` 付きで収載済みの既知軸だが、`_AXIS_VERDICTS` に載って
    いなかったため上記の境界宣言に落ちて未知軸扱いになり、
    `verdict: "exact_match"`（`structure` 専用のはずの語彙）や任意の型の
    `requirement`/`observed` を伴う成功が正規形として受理されてしまって
    いた。`_AXIS_VERDICTS["bpm"] = frozenset({"preserved", "deviated"})`
    を追加し、`key`/`brightness` と同じ物理軸の verdict 語彙に固定した
    （`exact_match`/`mismatch` は `structure` 専用のまま拒否される）。
    さらに `verdict` が成功側（`preserved`）のとき、`requirement`/
    `observed` は**両方「有限数値」（`int` または有限 `float`、`bool`
    除外、`math.isfinite`）でなければならない**（19 巡目のテンプレート
    ——成功側は型形状を強制、失敗側は `Any` のまま）。**数値の等価判定は
    このスキーマに入れない**——`bpm` の一致判定は許容誤差・丸め・
    octave/halving 帯域を含む計器定義（`svp_rpe.roundtrip` の
    `values_match`/`runtime_gate`）に属する関心事であり、離散ラベル軸
    （`key` の異名同音等価・`brightness` の帯域所属・`structure` の列
    完全一致）とは性質が異なる——report スキーマ側が独自に「何 BPM 差まで
    一致とみなすか」を再定義すべきではない、という線引き（下記の値×
    verdict 整合ファミリー終端宣言の但し書きを参照）。**失敗側 verdict
    （`deviated`）は型不正な値でも引き続き受理する**（`Any` 設計を維持
    ——19 巡目と同じ一貫方針）。**ドリフト検出との整合**: `bpm` は
    `derive_trusted_axes()` の採用軸（`_KNOWN_EXCLUDED_AXES` には含まれ
    ない）であり、17 巡目 B のドリフトテストの前提と矛盾しない。

    brightness の信頼帯強制（PR #246 Codex P2 review 16 巡目 + 19 巡目、
    8 巡目 B の verdict×band 整合と同族——凍結信頼軸表
    `config/authoring_trusted_axes_l0.yaml` の `axes.brightness.
    band_restriction`（`trusted_values: [dark]`）が report スキーマ側で
    従来強制されておらず、`brightness` 軸で `"bright"`（帯外）×
    `verdict: "preserved"`（成功）× `band: "measured"` の組が正規形として
    受理されてしまっていた）: `brightness` 軸の `verdict` が成功側
    （`preserved`——7 巡目 A の `_AXIS_VERDICTS["brightness"]` により
    `brightness` の成功語彙は `preserved` のみ）のとき、`requirement`/
    `observed` は**両方 `str` でなければならない**（19 巡目: 型形状自体を
    強制——旧実装は非 `str` を「判定対象外」として素通ししていたため
    `requirement=3.5` のような型不正な値でも成功を主張できた）、かつ
    `_TRUSTED_BRIGHTNESS_VALUES`（`{"dark"}`）に含まれることを強制する。
    成功 verdict は 8 巡目 B により既に `band == "measured"` が必須のため、
    本ガードは実質「`measured` × `preserved` × （型不正または帯外）」の
    遮断になる。**失敗側 verdict は型不正・帯外でも受理したまま**
    （`deviated` で「帯外だから保持されていないと正直に報告する」経路
    ——8 巡目 B の失敗側許容方針と同じ理由で塞がない。`AxisReport` の
    `Any` 設計を失敗側では維持する）。

    key の値×verdict 整合（PR #246 Codex P2 review 17 巡目 A + 19 巡目、
    16 巡目の brightness 帯強制と同じ「成功側 verdict のみ制約する」
    一貫方針）: `key` 軸の `verdict` が成功側（`preserved`）のとき、
    `requirement`/`observed` は**両方 `str` でなければならない**（19 巡目:
    型形状自体を強制——旧実装は非 `str` を「判定対象外」として素通しして
    いたため `requirement=1`/`observed=2` のような型不正な値でも成功を
    主張できた）、かつ `svp_rpe.keys.keys_enharmonically_equal` による
    異名同音等価判定を要求する——従来は verdict 語彙とデータ型のみを検証し
    値そのものの整合を見ていなかったため、`requirement="D minor"` /
    `observed="E minor"` × `verdict="preserved"` のような矛盾した組が正規形
    として受理されてしまっていた。`keys_enharmonically_equal` はこの
    リポジトリの roundtrip 診断が「往復で調が保存されたか」の二値判定に
    使う決定論実装をそのまま再利用する（`C#`/`Db` のような異名同音は
    等価、パース不能値は casefold 完全一致へフォールバック——他の目的
    （grip 効果量の加重スコア）向けの `weighted_key_score` とは意味が
    異なるため混同しない、`svp_rpe/keys.py` モジュール docstring 参照）。
    **失敗側 verdict（`deviated`）は型不正・値の不一致いずれも制約しない**
    （16 巡目と同じ「成功側のみ強制」方針——「一致しないと正直に報告する」
    経路を塞がない。`AxisReport` の `Any` 設計を失敗側では維持する）。

    既知除外軸への成功宣言の禁止（PR #246 Codex P2 review 17 巡目 B）:
    凍結信頼軸表が採用しなかった軸（`_KNOWN_EXCLUDED_AXES` — K1 grip map
    で `dead` な `active_rate_target`/`valley_depth_target`、恒常
    `sensor_blind` な `stereo_width`/`time_signature`。詳細は同定数の
    コメント参照）で成功側 verdict（`preserved`/`exact_match`）を主張する
    報告は、`band` の値を問わず一律拒否する——出典計器の構造上これらの軸は
    そもそも成功 evidence への算入資格がないため、`band: "measured"` を
    自称してもその主張自体が凍結表と矛盾する。失敗側 verdict や
    `not_observed` band での正直な報告（「この軸は計測しなかった/観測でき
    なかった」）は引き続き許容する。`bpm` は `runtime_gate` 付きで信頼表に
    収載済み（実験ごとの動的判定の対象）のためこのリストに含めない——
    静的に「常に除外」とは言えない軸を誤って denylist しないための区別。

    structure の値×verdict 整合（PR #246 Codex P2 review 18 巡目 C + 19 巡目、
    17 巡目 A の key 整合と同じ「成功側 verdict のみ制約する」一貫方針）:
    `structure` 軸の `verdict` が成功側（`exact_match` — `structure` の
    成功語彙は `exact_match` のみ）のとき、`requirement`/`observed` は
    **両方「全要素 `str` の `list`」でなければならない**（`_is_str_list`。
    19 巡目: 型形状自体を強制——旧実装は非該当の型を「判定対象外」として
    素通ししていたため `requirement="intro,chorus"`（list ではなく文字列
    そのもの）のような型不正な値でも成功を主張できた）、かつ casefold
    逐要素の列完全一致（長さの不一致も拒否）を要求する——
    `['intro', 'chorus']` vs `['intro', 'outro']` のような矛盾した組や、
    要素は前方一致するが長さが異なる組が `verdict='exact_match'` として
    正規形受理されてしまっていた欠陥を閉じる。**report が格納する値は
    観測器（`svp_rpe.arrange.observe` の structure domain）の正規化済み
    ラベル列であるという契約**に依拠する——観測器自身の正規化ロジック
    （大小文字・空白の扱い等）はここで再実装しない。**失敗側 verdict
    （`mismatch`）は型不正・列の不一致いずれも制約しない**（17 巡目 A・
    16 巡目 brightness と同じ方針——「一致しないと正直に報告する」経路を
    塞がない。`AxisReport` の `Any` 設計を失敗側では維持する）。

    **値×verdict 整合ファミリーの終端宣言（PR #246 Codex P2 review 16
    （brightness）→ 17 巡目 A（key）→ 18 巡目 C（structure）→ 19 巡目
    （型形状）→ 20 巡目（bpm・数値軸の但し書き））**: この一連のガードは
    一貫して「成功側 verdict のみ値の整合を強制し、失敗側 verdict は
    `AxisReport` の `Any` 設計のまま無制約に保つ」という方針を貫く。19
    巡目でこの方針を「値そのものの整合（帯域・異名同音・列一致）」から
    「値の**型形状**」まで踏み込んで完結させた——成功側 verdict は型不正な
    値（`requirement=1` のような非 `str`、`requirement="intro,chorus"`
    のような非 `list`）を経由して整合チェックを回避できなくなった。既知
    4軸（`bpm`/`brightness`/`key`/`structure`）の成功側整合はこれで型・
    値の両面から閉じている。**但し書き（20 巡目、`bpm` の非対称性）**:
    `bpm` は他3軸と異なり**値そのものの等価判定をスキーマに持たない**
    ——型形状（有限数値であること）のみを強制し、`requirement`/`observed`
    の数値としての一致・不一致は判定しない。これは見落としではなく設計
    判断: `key`（異名同音）・`brightness`（帯域所属）・`structure`（列
    完全一致）はいずれも**離散ラベル**の等価性であり、その等価規則は
    report スキーマがそのまま採用してよい安定した定義を持つ。一方 `bpm`
    は連続値であり、一致判定には許容誤差・丸め・octave/halving 帯域と
    いった**計器固有の判定規則**（`svp_rpe.roundtrip` の
    `values_match`/`runtime_gate`）が絡む——これを report スキーマ側で
    再定義すると、計器側の規則変更のたびにスキーマも追従させる必要が
    生じ、二重管理になる。したがって `bpm` の数値一致は生産器
    （`svp_rpe.roundtrip`）の責務のまま残し、report スキーマは「型として
    有限数値か」という形式的な健全性のみを見る。将来 L0b が新しい既知軸を
    追加する場合は、その軸が離散ラベル（この3件と同じ「成功側は型形状+
    値整合の両方を強制」テンプレート）か連続値・計器定義の一致規則を持つ
    軸（`bpm` と同じ「成功側は型形状のみ強制、値の等価はスキーマ外」）
    かをまず判定してから、対応するテンプレートに従うこと。

    `observed_sections` の軸限定（PR #246 Codex P2 review 13 巡目 + 14 巡目、
    7 巡目 A と同族の軸不整合）: `AxisReport.observed_sections` は
    「structure 軸の境界時刻」として文書化・設計されたフィールド
    （`ObservedSection` docstring 参照）だが、`AxisReport` 単体は軸名を
    知らないため、`axes["key"].observed_sections` のような軸不整合な
    組み合わせを構成できてしまう。`AuthoringDiffReport` 側で軸名が
    `"structure"` の場合のみこのフィールドの非 `None` 値を許容し、
    `"structure"` 以外（`key`/`brightness`、および 7 巡目の verdict 語彙
    とは異なり**未知軸も含む**）では拒否する。判定は `is not None` の
    厳密比較（14 巡目で truthy 判定 `if axis_report.observed_sections` から
    修正——5 巡目の `SymbolicValidationResult` の `errors: []` 素通り
    バグと同型のパターン: 明示的な空リスト `observed_sections: []` は
    falsy だが `None` ではなく、truthy 判定では構造軸以外でも素通り・
    `exclude_none` の再 dump でそのまま出力されてしまう。「正規形ガードは
    truthy 判定でなく `is None`/`is not None` の厳密比較を使う」という
    教訓は本モジュール内で複数回踏んでおり、今後このパターンで新規ガードを
    追加する際は truthy 判定を避けること）。**7 巡目との線引き**:
    `_AXIS_VERDICTS` の未知軸許容は「verdict という汎用語彙は将来軸でも
    意味を持ちうる」という判断だが、`observed_sections` は verdict のような
    汎用語彙ではなく `structure` 軸の境界時刻という**スキーマ自体が構造軸
    固有**のフィールドであり、未知軸がこれを名乗る根拠がない——将来 L0b が
    境界時刻を持つ新しい軸を追加する場合は、このホワイトリスト
    （`_OBSERVED_SECTIONS_AXES`）へ
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
    def _bpm_success_requires_finite_numeric_value(self) -> Self:
        axis_report = self.axes.get("bpm")
        if axis_report is None or axis_report.verdict != "preserved":
            return self
        requirement = axis_report.requirement
        observed = axis_report.observed
        if not _is_finite_numeric(requirement) or not _is_finite_numeric(observed):
            raise ValueError(
                f"axes['bpm'].verdict='preserved' but requirement={requirement!r} and "
                f"observed={observed!r} must both be a finite numeric value (int or float, "
                "bool excluded, NaN/inf rejected) — a success verdict requires a well-formed "
                "value, not just an unchecked one (19 巡目 type-shape template). Numeric "
                "equality itself is intentionally not enforced here — see class docstring."
            )
        return self

    @model_validator(mode="after")
    def _brightness_success_requires_trusted_band(self) -> Self:
        axis_report = self.axes.get("brightness")
        if axis_report is None or axis_report.verdict not in _SUCCESS_VERDICTS:
            return self
        errors = []
        for field_name, value in (("requirement", axis_report.requirement), ("observed", axis_report.observed)):
            if not isinstance(value, str):
                # PR #246 Codex P2 review 19 巡目: 成功側 verdict では型形状
                # 自体を強制する（旧実装は非 str をここで判定スキップして
                # いたため、requirement=1/observed=2 のような型不正な値でも
                # 「str でないので帯域判定対象外」として素通りしていた）。
                errors.append(
                    f"axes['brightness'].{field_name}={value!r} must be str when "
                    "verdict is a success verdict — a success claim requires a "
                    "well-formed value, not just an in-band one"
                )
            elif value not in _TRUSTED_BRIGHTNESS_VALUES:
                errors.append(
                    f"axes['brightness'].{field_name}={value!r} is outside the trusted band "
                    f"{sorted(_TRUSTED_BRIGHTNESS_VALUES)!r} (config/authoring_trusted_axes_l0."
                    "yaml axes.brightness.band_restriction) — a success verdict cannot be "
                    "claimed for an untrusted value per D5's band-annotation discipline"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _key_success_requires_enharmonic_match(self) -> Self:
        axis_report = self.axes.get("key")
        if axis_report is None or axis_report.verdict != "preserved":
            return self
        requirement = axis_report.requirement
        observed = axis_report.observed
        if not isinstance(requirement, str) or not isinstance(observed, str):
            # PR #246 Codex P2 review 19 巡目: 成功側 verdict では型形状を
            # 強制する（旧実装は非 str をここで判定スキップしていたため、
            # requirement=1/observed=2 のような型不正な値が「str でないので
            # 判定対象外」として preserved のまま素通りしていた）。
            raise ValueError(
                f"axes['key'].verdict='preserved' but requirement={requirement!r} and "
                f"observed={observed!r} must both be str — a success verdict requires a "
                "well-formed value, not just an unchecked one"
            )
        if not keys_enharmonically_equal(requirement, observed):
            raise ValueError(
                f"axes['key'].verdict='preserved' but requirement={requirement!r} and "
                f"observed={observed!r} are not enharmonically equal per "
                "svp_rpe.keys.keys_enharmonically_equal — a success verdict cannot be "
                "claimed for mismatched key values"
            )
        return self

    @model_validator(mode="after")
    def _known_excluded_axes_reject_success_verdicts(self) -> Self:
        errors = []
        for axis_name, axis_report in self.axes.items():
            if axis_name in _KNOWN_EXCLUDED_AXES and axis_report.verdict in _SUCCESS_VERDICTS:
                errors.append(
                    f"axes[{axis_name!r}].verdict={axis_report.verdict!r} is a success verdict "
                    f"but {axis_name!r} is excluded from the frozen trusted-axis table "
                    "(config/authoring_trusted_axes_l0.yaml) — this axis has no source "
                    "instrument backing a success claim regardless of band"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _structure_success_requires_matching_sequence(self) -> Self:
        axis_report = self.axes.get("structure")
        if axis_report is None or axis_report.verdict != "exact_match":
            return self
        requirement = axis_report.requirement
        observed = axis_report.observed
        if not _is_str_list(requirement) or not _is_str_list(observed):
            # PR #246 Codex P2 review 19 巡目: 成功側 verdict では型形状を
            # 強制する（旧実装は「全要素 str の list」でない値をここで
            # 判定スキップしていたため、`"intro,chorus"`（文字列そのもの）
            # のような型不正な値が「list でないので判定対象外」として
            # exact_match のまま素通りしていた）。
            raise ValueError(
                f"axes['structure'].verdict='exact_match' but requirement={requirement!r} and "
                f"observed={observed!r} must both be a list of str — a success verdict "
                "requires a well-formed label sequence, not just an unchecked value"
            )
        if [item.casefold() for item in requirement] != [item.casefold() for item in observed]:
            raise ValueError(
                f"axes['structure'].verdict='exact_match' but requirement={requirement!r} and "
                f"observed={observed!r} are not a matching label sequence (casefold "
                "element-wise, length included) — a success verdict cannot be claimed for "
                "mismatched structure sequences"
            )
        return self

    @model_validator(mode="after")
    def _observed_sections_are_structure_only(self) -> Self:
        errors = []
        for axis_name, axis_report in self.axes.items():
            if axis_report.observed_sections is not None and axis_name not in _OBSERVED_SECTIONS_AXES:
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

    直列化境界での再検証（PR #246 Codex P2 review 18 巡目 A、`frozen=True`
    の浅さ対策）: pydantic の `frozen=True` はフィールド**属性の再代入**
    （`model.axes = {...}`）のみを防ぎ、フィールドが保持する**可変
    コンテナの中身**（例: `dict[str, AxisReport]` である `axes` への
    `report.axes["brightness"] = 別の AxisReport インスタンス`、あるいは
    `AxisReport.requirement` が保持する `list` への `append`）を構築後に
    書き換える経路は素通しする——`dict.__setitem__`/`list.append` は
    pydantic の `__setattr__` オーバーライドを経由しないため。この経路で
    書き換えられた値は構築時に一度だけ走った `model_validator` 群
    （brightness 帯・key 整合・既知除外軸 denylist 等）を一切通っておらず、
    汚染された report が publish されうる。**対策**: 直列化前に
    `type(model).model_validate(model.model_dump(mode="python"))` で
    モデルを再構築し、全 `model_validator` を強制的に再実行してから
    payload を作る——publish 直前の最終防衛線として、構築後 mutation で
    不変条件をすり抜けたモデルを fail-closed に拒否する
    （`ValidationError`）。正常なモデルはバイト同一の結果を返す
    （`model_dump(mode="python")` → 再構築 → `model_dump(mode="json")` は
    決定論的な往復であり、既存の byte-determinism を変えない）。

    `allow_nan=False`（PR #246 Codex P2 review 9 巡目 B）: 既定の
    `json.dumps` は `NaN`/`Infinity`/`-Infinity` を（RFC 8259 準拠でない）
    非標準リテラルとして書き出してしまう——`AxisReport`/`ObservedSection`
    の構築時 fail-fast（`_reject_non_finite_float`）で通常はここまで
    到達しないが、直列化そのものにも防御として `allow_nan=False` を掛け、
    万一非有限値を積んだモデルが構築できてしまった場合でも不正 JSON を
    出力する前に `ValueError` で fail-closed にする（二重の安全網。上記の
    再検証と役割は重複しない——再検証は「mutation ですり抜けた不変条件」、
    `allow_nan=False` は「それでも尚すり抜けた場合の直列化そのものの
    防御」という別レイヤー）。
    """

    revalidated = type(model).model_validate(model.model_dump(mode="python"))
    payload = revalidated.model_dump(mode="json", exclude_none=True)
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
