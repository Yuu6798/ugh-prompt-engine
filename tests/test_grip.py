"""K0/K1 grip harness tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from scripts.measure_grip import (
    _exact_match_score,
    _key_match_score,
    analyze_fixture,
    load_fixture,
    main,
)
from svp_rpe.control import (
    GRIP_SATURATED,
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
    """per-cell ヌル格下げ規則（high セル match_rate <= low セル match_rate なら
    分類によらず dead）が発火し、combined 機械分類 loose にもかかわらず
    primary_verdict は dead で確定することを pin する（§7.6 の 0.5 combined 誤読
    前例の再発防止・m2_plan.yaml §3 honesty 注記）。"""
    expected = _load_m2_expected_grip()
    verdict = expected["verdicts"]["time_signature"]

    assert verdict["match_rate_low_cell"] == pytest.approx(1.0)
    assert verdict["match_rate_high_cell"] == pytest.approx(0.125)
    assert verdict["combined_match_rate"] == pytest.approx(0.5625, abs=1e-6)
    assert verdict["machine_classification_from_combined_match_rate"] == "loose"

    null_gate_fired = verdict["match_rate_high_cell"] <= verdict["match_rate_low_cell"]
    assert null_gate_fired is True
    assert verdict["null_gate_fired"] is True
    assert verdict["preregistered_rule_outcome"] == "dead"
    assert verdict["primary_verdict"] == "dead"
    assert verdict["verdict_canonical"] is True
    assert "1/8" in verdict["descriptive_evidence"]
    assert "0/8" in verdict["descriptive_evidence"]


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
