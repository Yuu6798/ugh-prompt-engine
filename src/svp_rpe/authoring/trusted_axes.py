"""authoring/trusted_axes.py — L0a 信頼軸表の機械導出 (D-L0a-3).

正本 = `docs/llm_adapter_planning.md` §5「正式な信頼軸表は L0a の契約凍結時に、
軸ごとに明示した出典計器から機械的に導出して凍結する（手書きリストの陳腐化を
防ぐ）」。本モジュールは出典計器から純関数で信頼軸表を導出する。凍結先 =
`config/authoring_trusted_axes_l0.yaml`（同梱コピー同期）——
`tests/test_trusted_axes.py` が「再導出 == 凍結ファイル」を enforce する
（ドリフト検出、M3 のレジストリ凍結一致テストと同型）。

導出規則:

- **物理軸**: 出典 = `svp_rpe.roundtrip.diagnose.ROUNDTRIP_FIELDS`（R0
  往復診断が扱う 7 フィールド）。以下 2 種類を除外する:
    1. K1 grip map（`roundtrip/data/expected_grip.json`）で
       `classification == "dead"` な軸（`active_rate_target`/
       `valley_depth_target` — 演奏者のつまみが死んでいる = knob_dead）。
    2. `diagnose.py` の `_is_sensor_blind` が値に関係なく恒常的に blind と
       判定する軸（`stereo_width` — T1 のステレオ帯が未較正で恒久 blind）、
       および `docs/roundtrip_preservation.md` が全スナップショットで
       実測確認した `time_signature`（T1 が常に TODO センチネルを返すため
       実質的に恒常 blind——`_is_sensor_blind` 自体は値依存だが、この
       フィールドが `sensor_blind` 以外の診断を得た記録が存在しない）。
  残る軸（本 spec 時点で `bpm`/`key`/`brightness`）に `source_instrument`
  を付与する。`brightness` はさらに `band_restriction` を持つ: bright 帯は
  演奏者の押し込み不足が正本 §5 で実測済みのため dark 帯のみ信頼する。
- **構造軸**: R0 の診断対象外のため、AR4 の観測計器
  （`svp_rpe.arrange.observe` の structure domain、`svprpe observe` で
  実配線済み）を出典として `structure` を追加する。
- 出典を示せない軸は表に載せない（正本 §5「暗黙の例外を作らない」）。

境界宣言 + `runtime_gate`（Fable レビュー是正、band_restriction との非対称
解消）: この導出は R0/K1/AR4 という**出典計器の構造**からメカニカルに
決まる軸集合であり、`bpm` の実験時信頼判定（抽出器の octave/halving
誤検出——正本 §5 「事前登録課題の BPM が信頼帯内と確認できた場合のみ算入」）
は別の関心事——L0b が各実験の事前登録時に D5 の `out_of_band` 規則で個別に
決める runtime 判定であり、この静的な出典表が確定させる値ではない。ただし
`brightness` の `band_restriction`（表内に明記）と非対称にならないよう、
`bpm` エントリには `runtime_gate` フィールドで同じ境界宣言を**表内**にも
1 行要約する——表だけを読む監査者が bpm を無条件信頼と誤読しないように
docstring 側の説明を表側へも複製する（決定論な固定文字列、`_RUNTIME_GATES`
参照）。
"""
from __future__ import annotations

from typing import Any

from svp_rpe.roundtrip.diagnose import GRIP_KEYS, ROUNDTRIP_FIELDS, load_grip_map

SCHEMA_VERSION = "authoring-trusted-axes/1.0"

# `diagnose.py` の恒常 sensor_blind 軸（モジュール docstring 参照）。値依存の
# 一時的 blind（None・TODO センチネル出現時のみ blind になる軸)はここでは
# 扱わない——恒常的にセンサーが機能しない軸のみを出典計器の構造として除外する。
_CONSTANT_SENSOR_BLIND_FIELDS = frozenset({"stereo_width", "time_signature"})

_PHYSICAL_SOURCE_INSTRUMENT = (
    "svp_rpe.roundtrip.diagnose.ROUNDTRIP_FIELDS (R0 roundtrip diagnosis)"
)
_STRUCTURE_SOURCE_INSTRUMENT = (
    "svp_rpe.arrange.observe structure domain (AR4, svprpe observe --manifest)"
)

# brightness の bright 帯は演奏者の押し込み不足が正本 §5 で実測済み（dark 帯
# のみ信頼）。他の軸は帯域制限を持たない。
_BAND_RESTRICTIONS: dict[str, dict[str, Any]] = {
    "brightness": {"trusted_values": ["dark"]},
}

# bpm は出典計器の構造上この表に載るが、正本 §5 は抽出器の octave/halving
# 誤検出（実測済み: roundtrip_preservation.md / R1 corpus screen）を理由に
# 「事前登録課題の BPM が信頼帯内と確認できた場合のみ算入・それ以外は
# out_of_band」という実験ごとの動的判定を要求する。`brightness` の
# `band_restriction` と表記の非対称が生まれないよう、この境界宣言を表内
# フィールドとしても 1 行要約する（決定論な固定文字列——実験ごとに変わる
# 値ではなく、判定規則そのものの参照）。
_RUNTIME_GATES: dict[str, str] = {
    "bpm": (
        "conditionally trusted only (docs/llm_adapter_planning.md §5): "
        "the BPM extractor's octave/halving misdetection is empirically "
        "confirmed (roundtrip_preservation.md / R1 corpus screen) — an "
        "experiment may count this axis only after confirming its "
        "pre-registered task's BPM value falls within the extractor's "
        "trusted band; otherwise treat as out_of_band per D5. This is a "
        "per-experiment runtime determination made by L0b, not a static "
        "fact this table can freeze."
    ),
}


def derive_trusted_axes() -> dict[str, Any]:
    """出典計器から信頼軸表を導出する（純関数・副作用なし）。"""

    grip_map = load_grip_map()
    axes: dict[str, Any] = {}
    for field in ROUNDTRIP_FIELDS:
        if field in _CONSTANT_SENSOR_BLIND_FIELDS:
            continue
        grip_record = grip_map.get(GRIP_KEYS.get(field, field))
        if grip_record is not None and grip_record.classification == "dead":
            continue
        axis: dict[str, Any] = {"source_instrument": _PHYSICAL_SOURCE_INSTRUMENT}
        if field in _BAND_RESTRICTIONS:
            axis["band_restriction"] = _BAND_RESTRICTIONS[field]
        if field in _RUNTIME_GATES:
            axis["runtime_gate"] = _RUNTIME_GATES[field]
        axes[field] = axis

    axes["structure"] = {"source_instrument": _STRUCTURE_SOURCE_INSTRUMENT}

    return {"schema_version": SCHEMA_VERSION, "axes": axes}
