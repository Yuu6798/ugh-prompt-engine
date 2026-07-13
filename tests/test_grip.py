"""K0/K1 grip harness tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from scripts.measure_grip import (
    _equal_means_static_null,
    _exact_match_score,
    _key_match_score,
    analyze_fixture,
    load_fixture,
    main,
    render_markdown,
)
from svp_rpe.control import (
    GRIP_SATURATED,
    MATCH_TIGHT_MIN,
    classify_grip,
    classify_match_grip,
    grip_effect_size,
    match_rate,
)

FIXTURE_PATH = Path("examples/control/k0/musicgen_rpe_fixture.json")
EXPECTED_PATH = Path("examples/control/k0/expected_grip.json")
K1_FIXTURE_PATH = Path("examples/control/k1/synth_performer_rpe_fixture.json")
K1_EXPECTED_PATH = Path("examples/control/k1/expected_grip.json")
K2_FIXTURE_PATH = Path("examples/control/k2/suno_rpe_fixture.json")
K2_EXPECTED_PATH = Path("examples/control/k2/expected_grip.json")


def test_grip_effect_size_uses_pooled_sd() -> None:
    assert grip_effect_size([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(3.0)
    assert grip_effect_size([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 3.0, 4.0, 5.0, 6.0]) == pytest.approx(
        0.632455532
    )


def test_grip_effect_size_zero_variance_rules_are_finite() -> None:
    assert grip_effect_size([1.0, 1.0], [1.0, 1.0]) == 0.0
    assert grip_effect_size([1.0, 1.0], [2.0, 2.0]) == GRIP_SATURATED
    assert grip_effect_size([2.0, 2.0], [1.0, 1.0]) == -GRIP_SATURATED

    for value in (
        grip_effect_size([1.0, 1.0], [1.0, 1.0]),
        grip_effect_size([1.0, 1.0], [2.0, 2.0]),
        grip_effect_size([2.0, 2.0], [1.0, 1.0]),
    ):
        assert math.isfinite(value)


def test_classify_grip_thresholds_and_sign() -> None:
    assert classify_grip(0.8, expected_sign=1) == "tight"
    assert classify_grip(0.2, expected_sign=1) == "loose"
    assert classify_grip(0.199999, expected_sign=1) == "dead"
    assert classify_grip(-0.3, expected_sign=1) == "dead"
    assert classify_grip(-GRIP_SATURATED, expected_sign=-1) == "tight"
    assert classify_grip(GRIP_SATURATED, expected_sign=-1) == "dead"


def test_classify_grip_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        classify_grip(1.0, expected_sign=0)
    with pytest.raises(ValueError):
        classify_grip(float("nan"), expected_sign=1)


def test_k0_fixture_snapshot() -> None:
    report = analyze_fixture(load_fixture(FIXTURE_PATH))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["bpm"]["grip"] > 0.8
    assert by_knob["brightness"]["classification"] == "dead"


def test_measure_grip_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def test_measure_grip_cli_knob_filter(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json", "--knob", "bpm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [result["knob"] for result in payload["results"]] == ["bpm"]
    assert payload["summary"] == {"tight": 1, "loose": 0, "dead": 0}


def test_measure_grip_cli_markdown_shows_gated_downgrade(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既定 CLI（markdown）でもヌルゲート格下げが可視化される（#174 第 4R）。

    gated 値が JSON のみだと markdown は stock classification を表示し続け
    dead 格下げが隠れる。M2 fixture の time_signature 行に gate=fired /
    gated class=dead が現れ、summary_gated も stock summary と並記されること。
    continuous 行はゲート対象外の "—" 表示。"""
    m2_fixture = Path("examples/control/musicgen_m2_knobs/m2_results_fixture.json")
    exit_code = main(["--fixture", str(m2_fixture)])

    assert exit_code == 0
    markdown = capsys.readouterr().out
    assert "| gate | gated class |" in markdown

    lines = markdown.splitlines()
    ts_row = next(line for line in lines if line.startswith("| time_signature "))
    assert "| loose " in ts_row  # stock 分類は生値のまま温存される
    assert "| fired " in ts_row
    assert ts_row.rstrip().endswith("| dead |")

    continuous_row = next(line for line in lines if line.startswith("| active_rate_target "))
    assert "| — | — |" in continuous_row

    assert "- summary (stock): tight 0 / loose 3 / dead 0" in markdown
    assert "- summary_gated: tight 0 / loose 2 / dead 1" in markdown

    # render_markdown 単体でも同一内容（CLI 経路と関数経路の同値性）。
    report = analyze_fixture(load_fixture(m2_fixture))
    assert render_markdown(report) == markdown


def test_match_rate_bounds_and_validation() -> None:
    assert match_rate([1.0, 0.0]) == pytest.approx(0.5)
    assert match_rate([1.0, 1.0, 1.0]) == 1.0
    with pytest.raises(ValueError):
        match_rate([1.2])
    with pytest.raises(ValueError):
        match_rate([])


def test_classify_match_grip_thresholds() -> None:
    assert classify_match_grip(0.7) == "tight"
    assert classify_match_grip(0.699999) == "loose"
    assert classify_match_grip(0.3) == "loose"
    assert classify_match_grip(0.299999) == "dead"
    with pytest.raises(ValueError):
        classify_match_grip(1.5)


def test_key_match_score_known_relations() -> None:
    assert _key_match_score("C major", "C major") == 1.0
    # mir_eval weighted score: relative minor = 0.3、無関係キーは 0.0
    assert _key_match_score("C major", "A minor") == pytest.approx(0.3)
    assert _key_match_score("C major", "F# minor") == 0.0


def test_exact_match_score_is_literal_and_normalized() -> None:
    """非 key categorical センサー用の汎用一致スコア: casefold + 空白正規化の完全一致のみ。"""
    assert _exact_match_score("4/4", "4/4") == 1.0
    assert _exact_match_score("4/4", "3/4") == 0.0
    assert _exact_match_score("3/4", " 3/4 ") == 1.0
    assert _exact_match_score("C Major", "c  major") == 1.0
    # key ファジーマッチなら部分点 0.3 が付く近縁調も、汎用スコアでは文字列不一致 = 0.0
    assert _key_match_score("C major", "A minor") == pytest.approx(0.3)
    assert _exact_match_score("C major", "A minor") == 0.0


def test_categorical_non_key_sensor_uses_exact_match_not_key_fuzzy() -> None:
    """sensor != "key" の categorical ノブは mir_eval key 経路を通らず完全一致率で採点。

    K2-seg time_signature ノブの経路: "4/4"/"3/4" を `_key_match_score` に流すと
    音楽 key として解釈されて意味を持たない。observed に「key として読めば部分点が
    付く」文字列（A minor vs C major = 0.3）を混ぜ、exact match の 0.0 として
    数えられることまで確認する。
    """
    fixture = {
        "fixture_id": "categorical_dispatch_probe",
        "repetitions": 2,
        "knobs": [
            {
                "name": "time_signature",
                "sensor": "time_signature",
                "kind": "categorical",
                "low_level": "4/4",
                "high_level": "3/4",
                "expected_sign": 0,
            }
        ],
        "samples": [
            {
                "knob": "time_signature",
                "level": "4/4",
                "features": {"time_signature": "4/4"},
            },
            {
                "knob": "time_signature",
                "level": "4/4",
                "features": {"time_signature": "3/4"},
            },
            {
                "knob": "time_signature",
                "level": "3/4",
                "features": {"time_signature": "3/4"},
            },
            {
                "knob": "time_signature",
                "level": "3/4",
                "features": {"time_signature": "4/4"},
            },
        ],
    }

    report = analyze_fixture(fixture)

    assert len(report["results"]) == 1
    result = report["results"][0]
    assert result["kind"] == "categorical"
    assert result["low_values"] == [1.0, 0.0]
    assert result["high_values"] == [1.0, 0.0]
    assert result["low_mean"] == 0.5
    assert result["high_mean"] == 0.5
    assert result["grip"] == 0.5
    assert result["classification"] == "loose"

    # key として読めば部分点 0.3 の近縁調（C major vs A minor）が、非 key センサー
    # では 0.0 になる = mir_eval key ファジーが混入していないことの直接証明
    fuzzy_probe = {
        "fixture_id": "categorical_dispatch_probe_2",
        "repetitions": 1,
        "knobs": [
            {
                "name": "mode_label",
                "sensor": "mode_label",
                "kind": "categorical",
                "low_level": "C major",
                "high_level": "A minor",
                "expected_sign": 0,
            }
        ],
        "samples": [
            {"knob": "mode_label", "level": "C major", "features": {"mode_label": "A minor"}},
            {"knob": "mode_label", "level": "A minor", "features": {"mode_label": "A minor"}},
        ],
    }
    fuzzy_report = analyze_fixture(fuzzy_probe)
    fuzzy_result = fuzzy_report["results"][0]
    assert fuzzy_result["low_values"] == [0.0]  # key ファジーなら 0.3 になるところ
    assert fuzzy_result["high_values"] == [1.0]
    assert fuzzy_result["grip"] == 0.5


# ---------------------------------------------------------------------------
# 事前登録ヌルゲートの計器 encode（PR #173 Codex P2、2026-07-13）: categorical
# 経路のみに additive フィールド（`null_gate_fired` / `gated_classification` /
# トップレベル `summary_gated`）として実装する。既存の `classification`（combined
# match_rate ベースの stock 分類）は温存し、continuous（effect_size）経路は
# 変更しない。m2_plan.yaml §3 の事前登録規約を、手動転記ではなく計器へ機械的に
# encode する。
#
# ゲート条件（#174 Codex P2 第 2・第 3・第 5・第 6 ラウンド採用の合成規則）:
#   null_gate_fired = (high < low and high < MATCH_TIGHT_MIN)
#                     or (high == low and 等号ヌル条件)
#   等号ヌル条件 = multiset(low_observed) == multiset(high_observed)
#                = (low + high) <= 1.0 + cross_score   # 観測リスト欠損時のみ
# 非改善（high < low）は high セル単独が tight 水準（>= MATCH_TIGHT_MIN = 0.7）
# 未達の場合のみ発火 — ゲートの目的は low/デフォルトセルが combined を押し上げる
# 誤読の防止であり、high 処方が単独で実現済み（例 1.0/0.875）なら誤読は発生
# しないため免除（第 3 ラウンド）。等号 means 時は per-cell 観測 multiset の
# 同一性 = 処方非依存（静的）出力の最強の観測的シグネチャで発火を判定する
# （第 6 ラウンド。和境界 1+s（第 5 ラウンド）は静的性の必要条件にすぎず、
# 応答して分布がシフトしても等号 means になり得る — key 五度シフト例
# low [C,G] / high [G,D] の 0.75/0.75 は multiset 相違につき免除）。排他一致
# では multiset 一致 + 等号 means → 和 <= 1 が数学的に必然のため、排他一致の
# 従来挙動（0.5/0.5 発火・0.6/0.6 / 0.9/0.9 / 1.0/1.0 非発火）は完全に保存
# される。structure 計器の 0.667/0.667 dead 前例は非排他マッチの別計器であり
# 本規則の対象外。
# ---------------------------------------------------------------------------


def _categorical_probe_fixture(low_observed: list[str], high_observed: list[str]) -> dict:
    """`low_level`/`high_level` に一致する observed のみを 1.0、それ以外を 0.0 として
    採点させる最小 categorical fixture（`_exact_match_score` 経路）。"""
    return {
        "fixture_id": "null_gate_probe",
        "repetitions": len(low_observed),
        "knobs": [
            {
                "name": "probe_knob",
                "sensor": "probe_sensor",
                "kind": "categorical",
                "low_level": "low",
                "high_level": "high",
                "expected_sign": 0,
            }
        ],
        "samples": (
            [
                {"knob": "probe_knob", "level": "low", "features": {"probe_sensor": observed}}
                for observed in low_observed
            ]
            + [
                {"knob": "probe_knob", "level": "high", "features": {"probe_sensor": observed}}
                for observed in high_observed
            ]
        ),
    }


def _key_probe_fixture(low_observed: list[str], high_observed: list[str]) -> dict:
    """sensor="key"（`_key_match_score` の fuzzy 経路）で C major / G major を
    セルレベルに持つ最小 categorical fixture。五度 C↔G の相互スコアは 0.5。"""
    return {
        "fixture_id": "key_null_gate_probe",
        "repetitions": len(low_observed),
        "knobs": [
            {
                "name": "key_knob",
                "sensor": "key",
                "kind": "categorical",
                "low_level": "C major",
                "high_level": "G major",
                "expected_sign": 0,
            }
        ],
        "samples": (
            [
                {"knob": "key_knob", "level": "C major", "features": {"key": observed}}
                for observed in low_observed
            ]
            + [
                {"knob": "key_knob", "level": "G major", "features": {"key": observed}}
                for observed in high_observed
            ]
        ),
    }


def test_key_null_gate_exempts_responsive_fifth_shift_with_equal_means() -> None:
    """応答的な五度シフト（low [C,G] / high [G,D]・means 0.75/0.75）は免除
    （#174 第 6R）。

    和境界 1+s（第 5R）は静的性の必要条件にすぎず、このケース（和 1.5 =
    1+cross_score(C,G)=1.5）を dead に誤格下げしていた反例。分布は処方に追従して
    [C,G]→[G,D] へシフトしており応答的 — per-cell 観測 multiset の相違により
    非発火・stock tight 温存。"""
    fixture = _key_probe_fixture(
        low_observed=["C major", "G major"], high_observed=["G major", "D major"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.75
    assert result["high_mean"] == 0.75
    assert result["classification"] == "tight"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == "tight"
    assert report["summary_gated"] == report["summary"]


def test_key_null_gate_fires_on_identical_multiset_static_mixture() -> None:
    """fuzzy key スコアラーの静的混合（処方非依存の同一分布）は発火（#174 第 5R/6R）。

    ケース 1: 文字どおりの C/G 静的 50/50 混合 — mir_eval の五度部分点は方向付き
    （score(C 参照, G 観測)=0.5 / 逆方向 0.0）のため means は 0.75/0.5 となり
    strict 分岐（high < low かつ high < 0.7）で発火。
    ケース 2: 両セルとも観測 [C, G, D] の静的混合 — means 0.5/0.5 の等号かつ
    per-cell 観測 multiset が同一 = 処方非依存出力のシグネチャにつき等号分岐で
    発火・gated dead（stock は combined 0.5 = loose 温存）。"""
    # ケース 1: strict 分岐で発火。
    static_mixture = _key_probe_fixture(
        low_observed=["C major", "G major"], high_observed=["C major", "G major"]
    )
    result = analyze_fixture(static_mixture)["results"][0]
    assert result["low_mean"] == 0.75
    assert result["high_mean"] == 0.5
    assert result["null_gate_fired"] is True
    assert result["gated_classification"] == "dead"

    # ケース 2: 等号 means + multiset 同一 — 等号分岐で発火。
    fixture = _key_probe_fixture(
        low_observed=["C major", "G major", "D major"],
        high_observed=["D major", "C major", "G major"],
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.5
    assert result["high_mean"] == 0.5
    assert result["classification"] == "loose"  # stock 分類は生値のまま温存される
    assert result["null_gate_fired"] is True
    assert result["gated_classification"] == "dead"
    assert report["summary"] == {"tight": 0, "loose": 1, "dead": 0}
    assert report["summary_gated"] == {"tight": 0, "loose": 0, "dead": 1}


def test_key_null_gate_exempts_full_match_equal_means() -> None:
    """key の天井等号（1.0/1.0 — K1 key 相当）は免除継続。

    両セル完全一致（low [C,C] / high [G,G]）は per-cell 観測 multiset が相違
    （分布が処方どおり移動）＝応答性の実証につき非発火（第 6R。第 5R の和境界
    でも和 2.0 > 1.5 で免除だった — 判定機構が変わっても結論は不変）。"""
    fixture = _key_probe_fixture(
        low_observed=["C major", "C major"], high_observed=["G major", "G major"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 1.0
    assert result["high_mean"] == 1.0
    assert result["classification"] == "tight"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == "tight"
    assert report["summary_gated"] == report["summary"]


def test_equal_means_static_null_fallback_without_observed_lists() -> None:
    """観測リストが得られない場合のフォールバック（1 + cross_score 和境界、
    necessary-only の近似）の単体検証（#174 第 6R）。

    計器経路（analyze_fixture）は観測リストを常に持つため multiset 本則で判定
    される — フォールバックは committed 結果の再検算等、観測リストを欠く入力
    のための経路であり、ここで直接 pin する。"""
    # 和 <= 1 + s: 静的で説明可能につき発火side（排他一致 s=0 の 0.5/0.5 相当）。
    assert _equal_means_static_null(None, None, 0.5, 0.5, 0.0) is True
    # 和 > 1 + s: 静的で説明不能につき免除side（K1 型 1.0/1.0、s=0）。
    assert _equal_means_static_null(None, None, 1.0, 1.0, 0.0) is False
    # fuzzy 境界 1+s=1.5: 0.75/0.75 は和 1.5 <= 1.5 で発火side（第 5R の挙動を保存）。
    assert _equal_means_static_null(None, None, 0.75, 0.75, 0.5) is True
    # 観測リストがあれば multiset 本則が優先（同 means でも相違なら免除）。
    assert _equal_means_static_null(["C", "G"], ["G", "D"], 0.75, 0.75, 0.5) is False
    assert _equal_means_static_null(["C", "G"], ["G", "C"], 0.5, 0.5, 0.0) is True


def test_categorical_null_gate_fires_when_high_mean_below_low_mean() -> None:
    """high セル一致率 < low セル一致率（本例: high 0.0 < low 1.0）なら、combined
    match_rate の機械分類（0.5 = loose 帯）によらず `gated_classification` は
    "dead" へ additive に格下げる。既存の `classification`（stock）は不変。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low", "low"], high_observed=["low", "low"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 1.0
    assert result["high_mean"] == 0.0
    assert result["classification"] == "loose"  # stock 分類は生値のまま温存される
    assert result["null_gate_fired"] is True
    assert result["gated_classification"] == "dead"
    assert report["summary"] == {"tight": 0, "loose": 1, "dead": 0}
    assert report["summary_gated"] == {"tight": 0, "loose": 0, "dead": 1}


def test_categorical_null_gate_does_not_fire_when_high_mean_exceeds_low_mean() -> None:
    """high セル一致率が low セルを上回る（本例: high 1.0 > low 0.0）場合、ヌルゲートは
    発火せず `gated_classification` は stock `classification` と同値のまま。"""
    fixture = _categorical_probe_fixture(
        low_observed=["high", "high"], high_observed=["high", "high"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.0
    assert result["high_mean"] == 1.0
    assert result["classification"] == "loose"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == result["classification"]
    assert report["summary"] == {"tight": 0, "loose": 1, "dead": 0}
    assert report["summary_gated"] == {"tight": 0, "loose": 1, "dead": 0}


def test_categorical_null_gate_does_not_fire_on_equal_full_match_means() -> None:
    """天井等号（low 1.0 / high 1.0 — K1 key ノブが実例）はヌルゲート非発火。

    和 2.0 > 1 は排他的完全一致の静的出力では説明不能（静的出力なら
    match_low + match_high <= 1 が必然）＝両セルとも処方に応答した実証であり、
    dead へ格下げてはならない。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low", "low"], high_observed=["high", "high"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 1.0
    assert result["high_mean"] == 1.0
    assert result["classification"] == "tight"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == "tight"
    assert report["summary"] == {"tight": 1, "loose": 0, "dead": 0}
    assert report["summary_gated"] == report["summary"]


def test_categorical_null_gate_fires_on_equal_means_explainable_by_static_output() -> None:
    """静的説明可能な等号（low 0.5 / high 0.5、和 1.0 <= 1）はヌルゲート発火。

    combined 0.5 の機械分類は loose 帯だが、両セル 0.5/0.5 は「常に同じ出力を
    返す静的コイン」でも達成できる値であり、処方への応答（改善）の証拠がない。
    #174 Codex P2 指摘: strict `<` のみだとこのケースが combined loose 誤読として
    再導入されるため、和 <= 1 の等号は発火に変更（第 2R）。第 6R では両セルの
    観測 multiset が同一（{low, high}）= 静的シグネチャにつき multiset 本則で
    発火する（挙動不変）。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low", "high"], high_observed=["high", "low"]
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.5
    assert result["high_mean"] == 0.5
    assert result["classification"] == "loose"  # stock 分類は生値のまま温存される
    assert result["null_gate_fired"] is True
    assert result["gated_classification"] == "dead"
    assert report["summary"] == {"tight": 0, "loose": 1, "dead": 0}
    assert report["summary_gated"] == {"tight": 0, "loose": 0, "dead": 1}


def test_categorical_null_gate_does_not_fire_on_equal_means_above_static_bound() -> None:
    """和が 1 を超える等号（low 0.6 / high 0.6、和 1.2）はヌルゲート非発火。

    排他的完全一致では静的出力の match_low + match_high は 1 を超えられない
    ため、和 1.2 は両セルが処方に部分応答した実証。改善（high > low）ではない
    ものの、静的コインで説明できない以上 dead 格下げの根拠はない（第 6R では
    観測 multiset の相違（low×3 vs high×3）として同じ結論に到達する — 挙動不変）。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low", "low", "low", "x", "x"],
        high_observed=["high", "high", "high", "x", "x"],
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.6
    assert result["high_mean"] == 0.6
    assert result["classification"] == "loose"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == result["classification"]
    assert report["summary_gated"] == report["summary"]


def test_categorical_null_gate_does_not_fire_on_high_equal_means() -> None:
    """高位等号（low 0.9 / high 0.9、和 1.8 > 1）はヌルゲート非発火。

    天井 1.0/1.0 と同じ原理: 静的出力で説明不能な高一致率は応答性の実証。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low"] * 9 + ["x"],
        high_observed=["high"] * 9 + ["x"],
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 0.9
    assert result["high_mean"] == 0.9
    assert result["classification"] == "tight"
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == "tight"
    assert report["summary_gated"] == report["summary"]


def test_categorical_null_gate_exempts_non_improving_high_cell_at_tight_level() -> None:
    """非改善（high 0.875 < low 1.0）でも high セル単独が tight 水準
    （>= MATCH_TIGHT_MIN = 0.7）に達していればヌルゲート非発火（#174 第 3R）。

    ゲートの目的は low/デフォルトセルが combined を押し上げる誤読の防止であり、
    high 処方が 7/8 実現されている強い非対称ノブ（combined 0.9375 = tight）を
    dead に誤殺してはならない。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low"] * 8,
        high_observed=["high"] * 7 + ["x"],
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 1.0
    assert result["high_mean"] == 0.875
    assert result["classification"] == "tight"  # combined 0.9375
    assert result["null_gate_fired"] is False
    assert result["gated_classification"] == "tight"
    assert report["summary_gated"] == report["summary"]


def test_categorical_null_gate_fires_on_non_improving_high_cell_below_tight_level() -> None:
    """非改善かつ high セル単独が tight 水準未達（high 0.65 < 0.7）はヌルゲート発火。

    low 1.0 が combined（0.825 = tight 帯）を押し上げる誤読の典型形 — 第 3R の
    免除は high >= MATCH_TIGHT_MIN の場合のみで、未達なら従来どおり dead へ
    格下げる。"""
    fixture = _categorical_probe_fixture(
        low_observed=["low"] * 20,
        high_observed=["high"] * 13 + ["x"] * 7,
    )
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["low_mean"] == 1.0
    assert result["high_mean"] == 0.65
    assert result["classification"] == "tight"  # stock 分類は生値のまま温存される
    assert result["null_gate_fired"] is True
    assert result["gated_classification"] == "dead"
    assert report["summary"] == {"tight": 1, "loose": 0, "dead": 0}
    assert report["summary_gated"] == {"tight": 0, "loose": 0, "dead": 1}


def test_continuous_knob_is_unaffected_by_null_gate() -> None:
    """continuous（effect_size）経路は additive フィールド自体を持たない
    （`null_gate_fired` / `gated_classification` キーが存在しない）。`summary_gated`
    は categorical 結果が皆無なら `summary` と完全一致する（no-op であることの確認）。
    符号不一致 dead（既存の `classify_grip` 規約）もヌルゲートとは無関係に温存される。"""
    fixture = {
        "fixture_id": "continuous_probe",
        "repetitions": 2,
        "knobs": [
            {
                "name": "sign_mismatch_knob",
                "sensor": "probe_sensor",
                "expected_sign": 1,
                "low_level": "0",
                "high_level": "1",
            }
        ],
        "samples": [
            {"knob": "sign_mismatch_knob", "level": "0", "features": {"probe_sensor": 5.0}},
            {"knob": "sign_mismatch_knob", "level": "0", "features": {"probe_sensor": 5.0}},
            {"knob": "sign_mismatch_knob", "level": "1", "features": {"probe_sensor": 1.0}},
            {"knob": "sign_mismatch_knob", "level": "1", "features": {"probe_sensor": 1.0}},
        ],
    }
    report = analyze_fixture(fixture)
    result = report["results"][0]

    assert result["classification"] == "dead"  # 符号不一致 dead（既存 classify_grip 規約）
    assert "null_gate_fired" not in result
    assert "gated_classification" not in result
    assert report["summary"] == {"tight": 0, "loose": 0, "dead": 1}
    assert report["summary_gated"] == report["summary"]


def test_all_categorical_null_gate_fixtures_have_dead_verdict_when_gate_fires() -> None:
    """将来バッチが `measure_grip.py` の categorical ヌルゲート発火を primary_verdict
    へ手動反映し忘れるリスクの CI 化（Codex レビュー指摘・PR #173 Thread 3）。

    `examples/control/**/*.json` のうち analyze_fixture 形式の `results` と、
    手動裁定 `verdicts`（`primary_verdict` 付き knob 別 dict）を両方持つ fixture
    （現状 m2_expected_grip.json のみ）について、categorical 結果で計器のゲート
    条件（合成規則: high < low かつ high < MATCH_TIGHT_MIN、または等号かつ
    和 <= 1）が成立するのに対応する `verdicts[knob]["primary_verdict"]` が
    "dead" でない場合に fail する。ゲート条件は per-cell 平均から直接再計算し、
    committed `null_gate_fired` フラグとの整合も合わせて検査する（転記ずれの
    検出。計器と同じ合成規則で再計算しないと、1.0/0.875 型の tight 免除 fixture
    投入時に人間裁定を override して CI が誤って赤くなる）。"""
    control_root = Path("examples/control")
    checked_any = False
    for path in sorted(control_root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        results = data.get("results")
        verdicts = data.get("verdicts")
        if not isinstance(results, list) or not isinstance(verdicts, dict):
            continue
        for result in results:
            if not isinstance(result, dict) or result.get("kind") != "categorical":
                continue
            high_mean = float(result["high_mean"])
            low_mean = float(result["low_mean"])
            # 計器と同じ等号ヌル判定を再計算して同期する（第 6R: 観測 multiset の
            # 同一性が本則。committed 結果に観測リストがない場合は計器と同じ
            # 1 + cross_score 和境界フォールバックに落ちる）。
            score_fn = (
                _key_match_score if result["sensor"] == "key" else _exact_match_score
            )
            cross_score = max(
                score_fn(result["low_level"], result["high_level"]),
                score_fn(result["high_level"], result["low_level"]),
            )
            low_observed = result.get("low_observed")
            high_observed = result.get("high_observed")
            gate_condition = (
                (high_mean < low_mean and high_mean < MATCH_TIGHT_MIN)
                or (
                    high_mean == low_mean
                    and _equal_means_static_null(
                        low_observed, high_observed, low_mean, high_mean, cross_score
                    )
                )
            )
            if "null_gate_fired" in result:
                assert result["null_gate_fired"] == gate_condition, (
                    f"{path}: {result['knob']!r} committed null_gate_fired="
                    f"{result['null_gate_fired']} does not match the composite gate "
                    f"condition (high < low and high < MATCH_TIGHT_MIN, or equal "
                    f"means with the static-null multiset check / 1+cross_score="
                    f"{1.0 + cross_score} fallback) for "
                    f"high_mean={high_mean} low_mean={low_mean} (is {gate_condition})"
                )
            if not gate_condition:
                continue
            checked_any = True
            knob_name = result["knob"]
            assert knob_name in verdicts, (
                f"{path}: categorical knob {knob_name!r} fired the null gate "
                "but has no corresponding verdicts entry"
            )
            assert verdicts[knob_name]["primary_verdict"] == "dead", (
                f"{path}: {knob_name!r} fires the null gate "
                f"(high_mean={result['high_mean']}, low_mean={result['low_mean']}) "
                f"but verdicts[{knob_name!r}]['primary_verdict'] != 'dead' "
                "(future batch forgot to reflect the preregistered null gate)"
            )

    # 本テストが実際に何か検査したことを確認する（fixture 構成変化で silently
    # no-op 化するのを防ぐ — 現状 m2_expected_grip.json の time_signature が該当）。
    assert checked_any


def test_k1_fixture_snapshot_spans_tight_and_dead() -> None:
    """K1 代表マップ: 決定論的演奏者に対する 5 ツマミ + 補助センサーの grip 固定。"""
    report = analyze_fixture(load_fixture(K1_FIXTURE_PATH))
    expected = json.loads(K1_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["key"]["classification"] == "tight"
    assert by_knob["key"]["kind"] == "categorical"
    # 正規センサー（spectral_centroid）では tight。legacy の帯域比センサーは
    # HF の乏しい素材で盲目になり dead に見える — 「ツマミ死」と「センサー盲」の判別例
    assert by_knob["brightness"]["classification"] == "tight"
    assert by_knob["brightness"]["sensor"] == "spectral_centroid"
    assert by_knob["brightness_band_ratio"]["classification"] == "dead"
    # 演奏者が読まないフィールド = 繋がっていないツマミは dead と検出される
    assert by_knob["active_rate_target"]["classification"] == "dead"
    assert by_knob["valley_depth_target"]["classification"] == "dead"


def test_k2_suno_fixture_snapshot_bpm_and_brightness_transfer() -> None:
    """K2 転移検証: K1 で tight だった bpm/brightness が本物 Suno でも tight に転移。

    fixture は Suno 生成 16 曲（bpm/brightness × 2 水準 × 4 反復）の抽出特徴量。
    bpm は素朴な製品センサー（既定 prior 120）でも tight（d≈1.61、真テンポでは
    さらに大きいが prior アトラクタが分離を圧縮 — docs §5.2）。brightness は
    spectral_centroid で borderline tight（d≈0.86、Suno は「明」は守るが「暗」は
    絶対 dark 帯まで落ちない非対称）。
    """
    report = analyze_fixture(load_fixture(K2_FIXTURE_PATH))
    expected = json.loads(K2_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["bpm"]["grip"] > 0.8
    assert by_knob["brightness"]["sensor"] == "spectral_centroid"
    assert by_knob["brightness"]["classification"] == "tight"


K2_MUSICGEN_FIXTURE_PATH = Path("examples/control/k2_musicgen/fixture.json")
K2_MUSICGEN_EXPECTED_PATH = Path("examples/control/k2_musicgen/expected_grip.json")


def test_k2_musicgen_fixture_snapshot_brightness_tight_bpm_loose() -> None:
    """K2 第二機種（MusicGen PR B, 2026-07-03 実測）: fixture→grip の決定論スナップショット。

    fixture は facebook/musicgen-small ローカル生成 32 本（bpm 90/170・brightness
    dark/bright × R=8）の抽出特徴量。brightness は Suno（0.86）より強い tight
    （d≈2.25、絶対 dark 帯 ≤1200Hz へも 3/8 到達＝Suno 0/4 と対照的）。bpm は素朴
    センサーで loose（d≈0.21）だが、高 prior 再推定（start_bpm=180）で high 側
    7/8 が 172.27 に回復＝R2 の抽出器 halving が第二生成器でも再現（knob_dead では
    ない — docs/musicgen_backend.md PR B 実測）。
    """
    report = analyze_fixture(load_fixture(K2_MUSICGEN_FIXTURE_PATH))
    expected = json.loads(K2_MUSICGEN_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "loose"
    assert by_knob["brightness"]["classification"] == "tight"
    assert by_knob["brightness"]["grip"] > 2.0


K2_MUSICGEN_SEGMENTS_FIXTURE_PATH = Path("examples/control/k2_musicgen_segments/fixture.json")
K2_MUSICGEN_SEGMENTS_EXPECTED_PATH = Path("examples/control/k2_musicgen_segments/expected_grip.json")


def test_k2_seg_musicgen_segments_fixture_snapshot() -> None:
    """K2-seg（2026-07-05）: compose が送出する未計測プロンプト欄 5 本
    （active rate / valley depth / Avoid / semantic.core / time signature）の
    fixture→grip 決定論スナップショット。tight 0 / loose 2 / dead 3
    （docs/musicgen_backend.md §7.6・config/device_profiles/musicgen.yaml 実測欄）。
    """
    report = analyze_fixture(load_fixture(K2_MUSICGEN_SEGMENTS_FIXTURE_PATH))
    expected = json.loads(K2_MUSICGEN_SEGMENTS_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["active_rate_target"]["classification"] == "loose"
    assert by_knob["valley_depth_target"]["classification"] == "dead"
    # semantic_avoid: expected_sign=-1（Avoid が効くなら centroid 低下）だが実測は
    # d=+1.10（符号逆・意図と逆方向の attractor）で dead。
    assert by_knob["semantic_avoid"]["classification"] == "dead"
    assert by_knob["semantic_avoid"]["grip"] > 0
    assert by_knob["semantic_core"]["classification"] == "dead"
    assert by_knob["time_signature"]["kind"] == "categorical"
    assert report["summary"] == {"tight": 0, "loose": 2, "dead": 3}


K2_SUNO_SEGMENTS_FIXTURE_PATH = Path("examples/control/k2_suno_segments/suno_rpe_fixture.json")
K2_SUNO_SEGMENTS_EXPECTED_PATH = Path("examples/control/k2_suno_segments/expected_grip.json")


def test_k2_seg_suno_segments_fixture_snapshot() -> None:
    """K2-seg Suno 転移バッチ 1（2026-07-09）: MusicGen スクリーン（§7.6）で裁定価値が
    最も高かった 2 欄（本文 `Avoid:` / `semantic.core`）を実測 Suno 12 曲へ転移した
    fixture→grip 決定論スナップショット（`examples/control/k2_suno_segments/README.md`）。

    - `semantic_avoid`: expected_sign=-1（Avoid が効くなら centroid 低下）だが実測は
      d=+4.03（符号逆・MusicGen の d=+1.10 より約 3.7 倍強い attractor）で
      `classify_grip` は機械的に dead 判定。事前登録の attractor 専用ルーブリック
      （発注書 verbatim、README 参照）では d>=+0.8 は「attractor 確定」で別扱い。
    - `semantic_core`（物理センサー `onset_density`）: d=+0.23 loose・正方向。
      MusicGen の同ノブ（d=-0.70・dead/物理センサー盲）と対照的に Suno では
      物理センサーも弱く生存している。
    """
    report = analyze_fixture(load_fixture(K2_SUNO_SEGMENTS_FIXTURE_PATH))
    expected = json.loads(K2_SUNO_SEGMENTS_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["semantic_avoid"]["grip"] == pytest.approx(4.029548, abs=1e-4)
    assert by_knob["semantic_avoid"]["classification"] == "dead"
    assert by_knob["semantic_core"]["grip"] == pytest.approx(0.230909, abs=1e-4)
    assert by_knob["semantic_core"]["classification"] == "loose"
    assert report["summary"] == {"tight": 0, "loose": 1, "dead": 1}


def test_k2_seg_suno_segments_clap_energy_axis_pins_semantic_core_grip() -> None:
    """K2-seg Suno バッチ 1: `semantic_core` CLAP 第二センサー（energy 軸）の grip を
    pin する。canonical 経路（`scripts/measure_grip.py`）は物理センサー専用のため、
    fixture 直下の `clap_semantic_axes` 節から直接 `grip_effect_size` を呼ぶ
    （物理と同一の pooled-SD 式、svp_rpe.control）。MusicGen の同軸（d=+1.90 tight、
    docs/musicgen_backend.md §7.6）よりさらに強い tight 域（d=+2.45）。
    """
    fixture = load_fixture(K2_SUNO_SEGMENTS_FIXTURE_PATH)
    clap = fixture["clap_semantic_axes"]
    assert clap["axis"] == "energy"
    assert clap["provenance"]["checkpoint_sha256"] == (
        "fae3e9c087f2909c28a09dc31c8dfcdacbc42ba44c70e972b58c1bd1caf6dedd"
    )

    by_level: dict[str, list[float]] = {}
    for sample in clap["samples"]:
        by_level.setdefault(str(sample["level"]), []).append(float(sample["contrast_fit"]))
    assert set(by_level) == {"calm", "euph"}
    assert all(len(values) == 4 for values in by_level.values())

    d = grip_effect_size(by_level["calm"], by_level["euph"])
    assert d == pytest.approx(2.446820, abs=1e-4)
    assert classify_grip(d, 1) == "tight"


K2_SUNO_SEGMENTS_EXCL_FIXTURE_PATH = Path("examples/control/k2_suno_segments/excl_rpe_fixture.json")
K2_SUNO_SEGMENTS_EXCL_EXPECTED_PATH = Path(
    "examples/control/k2_suno_segments/excl_expected_grip.json"
)


def test_k2_seg_suno_segments_excl_fixture_snapshot() -> None:
    """K2-seg Suno Exclude 欄併用追試（2026-07-09・バッチ 1 増補セル `calm_avoid_excl`）:
    fixture->grip 決定論スナップショット。ユーザー再アップロードの実音源 4 本を
    再抽出し、前セッションの事前登録比較 2 本の判読値と完全一致を確認した後の
    fixture 収載（`examples/control/k2_suno_segments/README.md` 追試節）。

    - `exclude_channel_grip`（low=`calm_avoid`, high=`calm_avoid_excl`）:
      d=-1.656645、tight・負方向=期待どおりの機械的分類。ただし本比較は excl セル
      （モデル/生成フロー未確認のブラウザ生成）と `calm_avoid`（バッチ 1・
      user-custom 流用）を跨ぐ cross-batch 交絡であり、Exclude-channel 単独 grip
      の確定エビデンスではない（confounded・未確定。詳細は
      `examples/control/k2_suno_segments/excl_plan.yaml` の
      `exclude_channel_grip.decision_rule` / `confound_honesty` 参照）。
    - `exclude_net_effect`（low=`calm`, high=`calm_avoid_excl`）: d=+1.642929。
      事前登録の問いは「Exclude 併用で本文 Avoid の attractor（#162: d=+4.03）を
      打ち消し、calm より暗くできるか」であり、成功なら負方向 —
      `expected_sign=-1` を保持する（Codex P2 レビュー指摘、バッチ 1
      `semantic_avoid` と同型の規約）。観測は正方向で符号反転しており、
      `classify_grip` は expected_sign と逆符号かつ |d|>=GRIP_LOOSE_MIN のケースを
      "dead" と分類するため、機械的分類は「非 tight（dead）」として記録される。
      ただしこの比較も `calm`（バッチ 1・user-custom 流用）と excl セル（モデル/
      生成フロー未確認のブラウザ生成）を跨ぐ cross-batch 交絡であり、「正味では
      attractor を打ち消せなかった」という解釈自体は isolated な結論ではなく
      confounded・未確定（詳細は `excl_plan.yaml` の `exclude_net_effect.decision_rule`
      / `confound_honesty` 参照）。omit_body_negative（#163）の妥当性はこの比較の
      確定を待たず、本文 Avoid=attractor のバッチ 1 内実測（d=+4.03、同一モデル）に立つ。
    - 両 d 値は `scratchpad/excl_extract/summary.json` の事前算出値
      （-1.6566449476718548 / 1.642929272618472）と一致する（抽出を伴わない純
      fixture 解析なので slow マーカー不要）。
    """
    report = analyze_fixture(load_fixture(K2_SUNO_SEGMENTS_EXCL_FIXTURE_PATH))
    expected = json.loads(K2_SUNO_SEGMENTS_EXCL_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["exclude_channel_grip"]["grip"] == pytest.approx(-1.656645, abs=1e-4)
    assert by_knob["exclude_channel_grip"]["classification"] == "tight"
    assert by_knob["exclude_net_effect"]["grip"] == pytest.approx(1.642929, abs=1e-4)
    assert by_knob["exclude_net_effect"]["classification"] == "dead"
    assert report["summary"] == {"tight": 1, "loose": 0, "dead": 1}


# ---------------------------------------------------------------------------
# バッチ M2 — MusicGen 3 ノブ（active_rate_target / valley_depth_target /
# time_signature）grip 再計測（2026-07-13）。§7.6（K2-seg・12 秒手組みプロンプト
# 計測）の既知交絡 2 件を M1 規律（30.6 秒・compose 実出力 verbatim・事前登録
# canonical、AGENTS.md §8「ローカル決定論バッチの canonical 条件」#172 適用第二号）
# で再計測し解消した。fixture→grip の決定論スナップショット（分析ロジックは
# K2-seg と同一の `scripts/measure_grip.py::analyze_fixture`）を
# `m2_expected_grip.json`（判定 verdict を含む拡張スキーマ）と
# `m2_measure_raw_2026-07-13.yaml`（`analyze_fixture` の生出力・再計算元）の
# 双方に対して pin する。判定（loose/loose/dead）はここでは再解釈しない —
# 事前登録規約（m2_plan.yaml §3）の機械適用結果を fixture 記録として固定する。
# ---------------------------------------------------------------------------

M2_DIR = Path("examples/control/musicgen_m2_knobs")
M2_RESULTS_FIXTURE_PATH = M2_DIR / "m2_results_fixture.json"
M2_EXPECTED_GRIP_PATH = M2_DIR / "m2_expected_grip.json"
M2_MEASURE_RAW_PATH = M2_DIR / "m2_measure_raw_2026-07-13.yaml"
M2_MANIFEST_PATH = M2_DIR / "m2_takes_manifest.json"
M2_DETERMINISM_SPOT_CHECK_PATH = M2_DIR / "m2_determinism_spot_check.yaml"


def _load_m2_results_fixture() -> dict:
    return json.loads(M2_RESULTS_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_m2_expected_grip() -> dict:
    return json.loads(M2_EXPECTED_GRIP_PATH.read_text(encoding="utf-8"))


def _load_m2_measure_raw() -> dict:
    return yaml.safe_load(M2_MEASURE_RAW_PATH.read_text(encoding="utf-8"))


def test_m2_analyze_fixture_matches_measure_raw_and_expected_grip() -> None:
    """`analyze_fixture(m2_results_fixture.json)` の決定論出力が、委譲実行の生出力
    （`m2_measure_raw_2026-07-13.yaml`）および設計反映済み fixture
    （`m2_expected_grip.json`）の `results`/`summary` と一致することを pin する
    （転記のみで再計算していないことの機械検算）。"""
    report = analyze_fixture(load_fixture(M2_RESULTS_FIXTURE_PATH))
    raw = _load_m2_measure_raw()
    expected = _load_m2_expected_grip()

    assert report["results"] == raw["results"]
    assert report["summary"] == raw["summary"]
    assert report["results"] == expected["results"]
    assert report["summary"] == expected["summary"]
    # 機械分類は 3 ノブとも loose（time_signature はヌルゲート適用前の combined
    # match_rate 分類 — verdict は下記テストで per-cell 込みで別途 pin する）。
    assert report["summary"] == {"tight": 0, "loose": 3, "dead": 0}

    # additive フィールド（PR #173 Codex P2）: categorical 経路のヌルゲートが
    # `null_gate_fired` / `gated_classification` を per-result に、`summary_gated`
    # をトップレベルに additive で併記する。continuous 2 ノブは無変更（キー自体が
    # 現れない）、time_signature のみ null_gate_fired=True で dead へ格下げる。
    assert report["summary_gated"] == raw["summary_gated"]
    assert report["summary_gated"] == expected["summary_gated"]
    assert report["summary_gated"] == {"tight": 0, "loose": 2, "dead": 1}

    by_knob = {result["knob"]: result for result in report["results"]}
    assert "null_gate_fired" not in by_knob["active_rate_target"]
    assert "gated_classification" not in by_knob["active_rate_target"]
    assert "null_gate_fired" not in by_knob["valley_depth_target"]
    assert "gated_classification" not in by_knob["valley_depth_target"]
    assert by_knob["time_signature"]["null_gate_fired"] is True
    assert by_knob["time_signature"]["gated_classification"] == "dead"


def test_m2_active_rate_target_recomputes_and_supersedes_ceiling_confound() -> None:
    """§7.6 の active_rate_target 天井交絡（low '0.55' で headroom 0.033）を、low 処方
    '0.30' 拡大（headroom 0.192）で解消したことを pin する。grip は §7.6 の
    0.394025 とほぼ同値（0.414395）— 天井アーティファクトでないことの確定証拠。"""
    fixture = _load_m2_results_fixture()
    expected = _load_m2_expected_grip()

    low_values = [
        s["features"]["active_rate"]
        for s in fixture["samples"]
        if s["knob"] == "active_rate_target" and s["level"] == "0.30"
    ]
    high_values = [
        s["features"]["active_rate"]
        for s in fixture["samples"]
        if s["knob"] == "active_rate_target" and s["level"] == "0.92"
    ]
    assert len(low_values) == 8
    assert len(high_values) == 8

    d = grip_effect_size(low_values, high_values)
    assert d == pytest.approx(0.414395, abs=1e-4)
    assert classify_grip(d, 1) == "loose"

    verdict = expected["verdicts"]["active_rate_target"]
    assert verdict["primary_verdict"] == "loose"
    assert verdict["verdict_canonical"] is True
    assert verdict["grip"] == pytest.approx(d, abs=1e-6)
    assert "§7.6" in verdict["supersedes"]
    assert "天井" in verdict["supersedes"]

    # §7.6 旧計測（K2-seg・12s 素材）との比較: 旧 grip 0.394025 とほぼ同値。
    old_expected = json.loads(
        Path("examples/control/k2_musicgen_segments/expected_grip.json").read_text(
            encoding="utf-8"
        )
    )
    old_result = next(
        r for r in old_expected["results"] if r["knob"] == "active_rate_target"
    )
    assert old_result["grip"] == pytest.approx(0.394025, abs=1e-4)
    assert old_result["classification"] == "loose"
    assert abs(d - old_result["grip"]) < 0.03


def test_m2_valley_depth_target_recomputes_and_supersedes_dead_verdict() -> None:
    """§7.6 の valley_depth_target dead 判定（12s 定常ビート素材の valley 床 0.078）
    を、セル処方値据え置き・素材長のみ 12s→30.6s へ伸ばした M2 が supersede し
    loose へ反転したことを pin する。"""
    fixture = _load_m2_results_fixture()
    expected = _load_m2_expected_grip()

    low_values = [
        s["features"]["valley_depth"]
        for s in fixture["samples"]
        if s["knob"] == "valley_depth_target" and s["level"] == "0.15"
    ]
    high_values = [
        s["features"]["valley_depth"]
        for s in fixture["samples"]
        if s["knob"] == "valley_depth_target" and s["level"] == "0.70"
    ]
    assert len(low_values) == 8
    assert len(high_values) == 8

    d = grip_effect_size(low_values, high_values)
    assert d == pytest.approx(0.3518, abs=1e-4)
    assert classify_grip(d, 1) == "loose"

    verdict = expected["verdicts"]["valley_depth_target"]
    assert verdict["primary_verdict"] == "loose"
    assert verdict["verdict_canonical"] is True
    assert verdict["grip"] == pytest.approx(d, abs=1e-6)
    assert "supersede" in verdict["supersedes"]
    assert "dead" in verdict["supersedes"]

    # §7.6 旧計測（K2-seg・12s 素材）は dead（0.152499）だった — 本バッチが反転させる。
    old_expected = json.loads(
        Path("examples/control/k2_musicgen_segments/expected_grip.json").read_text(
            encoding="utf-8"
        )
    )
    old_result = next(
        r for r in old_expected["results"] if r["knob"] == "valley_depth_target"
    )
    assert old_result["grip"] == pytest.approx(0.152499, abs=1e-4)
    assert old_result["classification"] == "dead"
    assert old_result["classification"] != verdict["primary_verdict"]


def test_m2_time_signature_combined_match_rate_is_loose_before_null_gate() -> None:
    """combined match_rate（低/高セル全 16 サンプル平均）の機械分類は 0.3-0.7 帯で
    loose だが、これは per-cell 値を隠す honesty リスクがある（下記ヌルゲート
    テストで dead へ格下げることを別途 pin する）。"""
    fixture = _load_m2_results_fixture()

    low_scores = [
        1.0 if s["features"]["time_signature"] == "4/4" else 0.0
        for s in fixture["samples"]
        if s["knob"] == "time_signature" and s["level"] == "4/4"
    ]
    high_scores = [
        1.0 if s["features"]["time_signature"] == "3/4" else 0.0
        for s in fixture["samples"]
        if s["knob"] == "time_signature" and s["level"] == "3/4"
    ]
    assert len(low_scores) == 8
    assert len(high_scores) == 8
    assert sum(low_scores) == 8.0
    assert sum(high_scores) == 1.0  # 3/4 の初達成 1/8（time_signature_high_06）

    combined = match_rate(low_scores + high_scores)
    assert combined == pytest.approx(0.5625, abs=1e-6)
    assert classify_match_grip(combined) == "loose"


def test_m2_time_signature_null_gate_fires_and_downgrades_to_dead() -> None:
    """per-cell ヌル格下げ規則（事前登録は ≤ 表記・計器 encode は strict `<`。
    実データ 0.125 < 1.0 はどちらでも発火し裁定不変）が発火し、combined 機械分類
    loose にもかかわらず primary_verdict は dead で確定することを pin する
    （§7.6 の 0.5 combined 誤読前例の再発防止・m2_plan.yaml §3 honesty 注記）。

    PR #173 Codex P2 対応: 手動記録の verdict（下記）と、計器
    （`measure_grip.py` categorical 経路の `null_gate_fired` / `gated_classification`
    additive フィールド）が一致することを合わせて pin する — ゲート規則が
    m2_plan.yaml §3 の事前登録どおり計器へ encode され、手動転記と計器出力が
    ずれていないことの機械検算。"""
    expected = _load_m2_expected_grip()
    verdict = expected["verdicts"]["time_signature"]

    assert verdict["match_rate_low_cell"] == pytest.approx(1.0)
    assert verdict["match_rate_high_cell"] == pytest.approx(0.125)
    assert verdict["combined_match_rate"] == pytest.approx(0.5625, abs=1e-6)
    assert verdict["machine_classification_from_combined_match_rate"] == "loose"

    # strict `<`（計器のゲート条件）で再計算 — 0.125 < 1.0 で発火する。
    null_gate_fired = verdict["match_rate_high_cell"] < verdict["match_rate_low_cell"]
    assert null_gate_fired is True
    assert verdict["null_gate_fired"] is True
    assert verdict["preregistered_rule_outcome"] == "dead"
    assert verdict["primary_verdict"] == "dead"
    assert verdict["verdict_canonical"] is True
    assert "1/8" in verdict["descriptive_evidence"]
    assert "0/8" in verdict["descriptive_evidence"]

    # 計器出力（additive フィールド）との整合: `analyze_fixture` が
    # `m2_results_fixture.json` から機械算出する null_gate_fired /
    # gated_classification が、上記の手動記録 verdict と一致する。
    report = analyze_fixture(load_fixture(M2_RESULTS_FIXTURE_PATH))
    instrument_result = next(r for r in report["results"] if r["knob"] == "time_signature")
    assert instrument_result["low_mean"] == pytest.approx(verdict["match_rate_low_cell"])
    assert instrument_result["high_mean"] == pytest.approx(verdict["match_rate_high_cell"])
    assert instrument_result["null_gate_fired"] == verdict["null_gate_fired"]
    assert instrument_result["gated_classification"] == verdict["primary_verdict"]


def test_m2_takes_manifest_sha256_matches_results_fixture() -> None:
    """results fixture の per-sample audio_sha256 は takes manifest と一致する
    （WAV 非コミット・sha256 provenance の内部整合、M1 の同型テストを踏襲）。"""
    fixture = _load_m2_results_fixture()
    manifest = json.loads(M2_MANIFEST_PATH.read_text(encoding="utf-8"))
    sha_by_id = {s["sample_id"]: s["audio_sha256"] for s in manifest["samples"]}

    assert len(manifest["samples"]) == 48
    for sample in fixture["samples"]:
        assert sample["audio_sha256"] == sha_by_id[sample["sample_id"]], sample["sample_id"]

    plan = manifest["plan"]
    assert plan["fixture_id"] == "musicgen_m2_knobs"
    assert plan["duration_seconds"] == 30.6
    assert plan["repetitions"] == 8
    assert plan["model_id"] == "facebook/musicgen-small"
    assert all(
        s["model_revision"] == "4c8334b02c6ec4e8664a91979669a501ec497792"
        for s in manifest["samples"]
    )

    # seed の決定論式（m2_plan.yaml §6）: knob_index*100 + level_index*50 + repeat。
    seeds_by_knob = {
        "active_rate_target": list(range(1000, 1008)) + list(range(1050, 1058)),
        "valley_depth_target": list(range(1100, 1108)) + list(range(1150, 1158)),
        "time_signature": list(range(1200, 1208)) + list(range(1250, 1258)),
    }
    for knob_name, expected_seeds in seeds_by_knob.items():
        observed_seeds = [
            s["seed"] for s in manifest["samples"] if s["knob"] == knob_name
        ]
        assert observed_seeds == expected_seeds


def test_m2_determinism_spot_check_records_two_byte_matches() -> None:
    """canonical 免除根拠（出力の壁時計順序非依存）の実測証拠（バッチ最初/最後の
    クリップ再生成 sha256 一致）を pin する。"""
    spot_check = yaml.safe_load(M2_DETERMINISM_SPOT_CHECK_PATH.read_text(encoding="utf-8"))
    expected = _load_m2_expected_grip()

    assert spot_check["result"] == "2/2 byte 一致（sha256 完全一致）。"
    assert len(spot_check["samples"]) == 2
    for sample in spot_check["samples"]:
        assert sample["match"] is True
        assert sample["pinned_sha256"] == sample["regenerated_sha256"]

    # expected_grip.json 側にも同内容が転記されていること（provenance の単一情報源が
    # 2 箇所に重複せずずれていないかの内部整合）。
    embedded = expected["determinism_spot_check"]
    assert embedded["result"] == spot_check["result"]
    assert len(embedded["samples"]) == 2
    for sample in embedded["samples"]:
        assert sample["match"] is True
        assert sample["pinned_sha256"] == sample["regenerated_sha256"]


def test_m2_canonical_conditions_follow_agents_md_local_batch_rule() -> None:
    """AGENTS.md §8「ローカル決定論バッチの canonical 条件」（#172）どおり、
    ABBA / 均衡ゲートは非適用（applicable=false）、補充ゼロ / タイムスタンプ記録は
    充足（applicable=true・satisfied=true）であることを pin する（M1 と同型の
    condition 集合）。"""
    expected = _load_m2_expected_grip()
    conditions = {c["id"]: c for c in expected["canonical_conditions"]["conditions"]}

    assert set(conditions) == {
        "abba_order",
        "zero_replenishment",
        "timestamps_recorded",
        "balance_gate",
    }
    for cid in ("abba_order", "balance_gate"):
        assert conditions[cid]["applicable"] is False
        assert conditions[cid]["satisfied"] is None
    for cid in ("zero_replenishment", "timestamps_recorded"):
        assert conditions[cid]["applicable"] is True
        assert conditions[cid]["satisfied"] is True

    # 3 ノブ全ての verdict が canonical であることも合わせて pin する。
    for knob_name in ("active_rate_target", "valley_depth_target", "time_signature"):
        assert expected["verdicts"][knob_name]["verdict_canonical"] is True
    assert expected["config_reflected"] is True


def test_m2_config_control_defaults_match_expected_grip() -> None:
    """`config/device_profiles/musicgen.yaml` の control_defaults 3 欄が
    `m2_expected_grip.json` の verdict と一致することを pin する（設計反映の
    config_reflected=true が実際に config に配線されていることの検算）。"""
    from svp_rpe.compose.device_profile import load_device_profile

    profile = load_device_profile("musicgen")
    assert profile is not None
    expected = _load_m2_expected_grip()

    active_rate = profile.control_defaults["active_rate_target"]
    assert active_rate.grip_class == expected["verdicts"]["active_rate_target"]["primary_verdict"]
    assert active_rate.grip == pytest.approx(
        expected["verdicts"]["active_rate_target"]["grip"], abs=1e-6
    )
    assert active_rate.evidence == "examples/control/musicgen_m2_knobs/m2_expected_grip.json"

    valley_depth = profile.control_defaults["valley_depth_target"]
    assert valley_depth.grip_class == expected["verdicts"]["valley_depth_target"]["primary_verdict"]
    assert valley_depth.grip == pytest.approx(
        expected["verdicts"]["valley_depth_target"]["grip"], abs=1e-6
    )
    assert valley_depth.evidence == "examples/control/musicgen_m2_knobs/m2_expected_grip.json"

    time_signature = profile.control_defaults["time_signature"]
    # time_signature は honesty ルール（categorical dead は grip キーに数値を持たない）
    # を M1/K2-seg から踏襲する。
    assert time_signature.grip_class == expected["verdicts"]["time_signature"]["primary_verdict"]
    assert time_signature.grip is None
    assert time_signature.evidence == "examples/control/musicgen_m2_knobs/m2_expected_grip.json"
