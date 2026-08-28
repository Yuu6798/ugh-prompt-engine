"""test_run9_probe_manifest.py — RUN9-PROBE-1: DESIGN_RUN9 §15 Probe Set
(P0-P5) の実体 manifest（`evaluation/probe_manifest.json`）と
`run9_schema.validate_probe_manifest()` の最低テスト。

音声処理・実学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402

CONTRACT_PATH = _RUN_DIR / "RUN9_CONTRACT.yaml"
SCORE_PY_PATH = _RUN_DIR.parent.parent / "singer" / "score.py"


@pytest.fixture(scope="module")
def contract_raw() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> m.Run9RunContract:
    return m.load_run9_contract_from_yaml_path(CONTRACT_PATH)


@pytest.fixture(scope="module")
def manifest_data() -> Dict[str, Any]:
    return m._loads_strict_json(m.PROBE_MANIFEST_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 実体 manifest の契約照合（項目15）
# ---------------------------------------------------------------------------


def test_probe_manifest_path_conventional_location() -> None:
    assert m.PROBE_MANIFEST_PATH.name == "probe_manifest.json"
    assert m.PROBE_MANIFEST_PATH.parent == _RUN_DIR / "evaluation"


def test_probe_manifest_valid_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)  # 例外を投げないことの確認


def test_probe_manifest_sha_pinned_and_matches_real_file(
    contract_raw: Dict[str, Any], contract: m.Run9RunContract, manifest_data: Dict[str, Any]
) -> None:
    """C.12 / PR #322 第6巡指摘 Fix 13: `probe_manifest_sha` は PINNED で
    あり、`load_pinned_probe_manifest()`（実バイト sha256 照合 +
    schema 全検証を一括で行う唯一の正規取得経路）が例外を投げずに
    manifest を返す——本関数が正本、本テストは回帰確認。"""
    field = contract_raw["probe_manifest_sha"]
    assert field["status"] == "PINNED"
    loaded = m.load_pinned_probe_manifest(contract)
    assert loaded == manifest_data


def test_probe_manifest_deterministic_pretty_format() -> None:
    """項目9: 決定論 pretty 書式（ensure_ascii=False, indent=2,
    sort_keys=True + 末尾改行）— founders/*.json と同一規約。"""
    raw = m.PROBE_MANIFEST_PATH.read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    data = json.loads(raw.decode("utf-8"))
    reserialized = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    assert raw == reserialized


def test_gate_state_still_blocked_after_probe_manifest_sha_pinned(
    contract: m.Run9RunContract,
) -> None:
    """項目13: `probe_manifest_sha` を PINNED 化しても、他の pre-run 欄
    （dataset/config/learning_recipe/measurement_spec 等）が PENDING の
    ままである限り `gate_state()` は依然 BLOCKED（誤 READY 化していない
    ことの回帰確認）。"""
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# P0 転記元の逐語性（項目④「P0 転記元の確認結果」の機械確認）
# ---------------------------------------------------------------------------


def _p0_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p0,) = [p for p in data["probes"] if p["probe_id"] == "P0"]
    return p0


def test_p0_cell_source_matches_real_score_py(manifest_data: Dict[str, Any]) -> None:
    p0 = _p0_probe(manifest_data)
    (cell,) = p0["cells"]
    source = cell["source"]
    assert source["transcribed_from"] == "voice_genesis/singer/score.py"
    assert source["verbatim"] is True
    assert source["transcribed_from_sha256"] == m.compute_file_sha256(SCORE_PY_PATH), (
        "P0 cell の source.transcribed_from_sha256 が voice_genesis/singer/score.py の実バイト "
        "sha256 と一致しない — score.py は read-only 参照であり改変されていないはず"
    )


def test_p0_cell_notes_all_within_central_register(manifest_data: Dict[str, Any]) -> None:
    p0 = _p0_probe(manifest_data)
    pitches = [n["pitch_midi"] for cell in p0["cells"] for n in cell["notes"]]
    assert pitches, "P0 must have at least one note"
    assert all(57 <= p <= 72 for p in pitches), pitches


def test_p0_cell_notes_match_build_sakura_score_verbatim(manifest_data: Dict[str, Any]) -> None:
    """score.py（read-only 参照）を直接 import して build_sakura_score() を
    呼び、P0 cell の notes 列が値として完全一致することを確認する
    （逐語転記の実体照合）。"""
    score_dir = str(SCORE_PY_PATH.parent)
    inserted = score_dir not in sys.path
    if inserted:
        sys.path.insert(0, score_dir)
    try:
        import score as sakura_score  # type: ignore[import-not-found]

        expected = [
            {
                "kana": n.mora.kana,
                "pitch_midi": int(n.midi),
                "duration_beats": n.duration_beats,
                "phrase_index": n.phrase_index,
                "is_phrase_final": n.is_phrase_final,
            }
            for n in sakura_score.build_sakura_score()
        ]
    finally:
        if inserted:
            sys.path.remove(score_dir)

    p0 = _p0_probe(manifest_data)
    (cell,) = p0["cells"]
    assert cell["notes"] == expected
    assert cell["tempo_bpm"] == sakura_score.TEMPO_BPM


# ---------------------------------------------------------------------------
# P4 / P5 の実体検証（項目17）
# ---------------------------------------------------------------------------


def test_p4_heldout_independence_declared(manifest_data: Dict[str, Any]) -> None:
    (p4,) = [p for p in manifest_data["probes"] if p["probe_id"] == "P4"]
    independence = p4["heldout_independence"]
    assert independence["status"] == m.HELDOUT_INDEPENDENCE_STATUS
    assert independence["independent_of"]
    assert independence["note"].strip()
    # PR #322 第6巡指摘 Fix 14: provenance の4ブロックが揃っていること
    # （個別の内容検証は Fix 14 セクションのテスト群が担う）。
    for block in (
        "authorship",
        "environment_evidence",
        "machine_checked_separation",
        "residual_risk_declaration",
    ):
        assert block in independence, f"heldout_independence missing {block!r}"


def test_p5_notes_within_baseline_domain_and_outside_p0_register(
    manifest_data: Dict[str, Any],
) -> None:
    (p5,) = [p for p in manifest_data["probes"] if p["probe_id"] == "P5"]
    pitches = [n["pitch_midi"] for cell in p5["cells"] for n in cell["notes"]]
    assert pitches
    assert all(45 <= p <= 90 for p in pitches), pitches
    assert any(p < 57 or p > 72 for p in pitches), (
        "P5 must include at least one note outside the P0 central-register domain "
        f"[57, 72], got {pitches}"
    )


def test_p3_role_carries_diagnostic_marker(manifest_data: Dict[str, Any]) -> None:
    (p3,) = [p for p in manifest_data["probes"] if p["probe_id"] == "P3"]
    assert "diagnostic_when_trf_uncalibrated" in p3["role"]


# ---------------------------------------------------------------------------
# PR #322 第20巡指摘 Fix 32（P2, 採用）: P5 の域内制約検査は「本 manifest
# 内の他 probe（P0/P1）の使用域の外周・baseline domain 内であること」しか
# 証明せず、実際の学習分布（PJS practice/education 素材）との分離は検証
# していない。Fix 14/18 と同じ「主張を収集済み証拠へ縮小 + 再入条件の
# 事前登録」規約で `deferred_verification` ブロックを P5 へ機械強制する。
# ---------------------------------------------------------------------------


def _p5_probe_early(data: Dict[str, Any]) -> Dict[str, Any]:
    (p5,) = [p for p in data["probes"] if p["probe_id"] == "P5"]
    return p5


def test_fix32_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)


def test_fix32_deferred_verification_block_declared(manifest_data: Dict[str, Any]) -> None:
    p5 = _p5_probe_early(manifest_data)
    dv = p5["deferred_verification"]
    assert dv["status"] == m.P5_DEFERRED_VERIFICATION_STATUS
    assert set(dv["blocked_by"]) == {
        "practice_audio_split_manifest_sha", "education_technique_lesson_manifest_sha",
    }
    assert dv["verification_procedure"].strip()
    assert dv["consumption_prohibition"].strip()


def test_negative_fix32_deferred_verification_block_missing(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    del _p5_probe_early(bad)["deferred_verification"]
    with pytest.raises(m.Run9ValidationError, match="missing required key"):
        m.validate_probe_manifest(bad)


def test_negative_fix32_status_reverted_to_unconditional_claim(
    manifest_data: Dict[str, Any],
) -> None:
    """裁定済み対応4「旧・無条件 held-out 主張の残存拒否」: status を
    検証完了済みであるかのような別の literal（例:
    'TRAINING_DISTRIBUTION_SEPARATED'）へ差し替えても、凍結 status
    literal との厳密一致検証で拒否される——status literal 自体が「学習
    分布との分離は未検証」という正直な宣言を固定するメカニズムであり、
    無条件 held-out 主張への回帰を構造的に防ぐ。"""
    bad = _mutate(manifest_data)
    _p5_probe_early(bad)["deferred_verification"]["status"] = "TRAINING_DISTRIBUTION_SEPARATED"
    with pytest.raises(m.Run9ValidationError, match="must be exactly"):
        m.validate_probe_manifest(bad)


def test_negative_fix32_blocked_by_incomplete(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p5_probe_early(bad)["deferred_verification"]["blocked_by"] = [
        "practice_audio_split_manifest_sha",
    ]
    with pytest.raises(m.Run9ValidationError, match="frozen set"):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "key,replacement",
    [
        ("verification_procedure", "何もしない"),
        ("consumption_prohibition", "特に制約はない"),
    ],
)
def test_negative_fix32_prose_field_missing_marker(
    manifest_data: Dict[str, Any], key: str, replacement: str,
) -> None:
    bad = _mutate(manifest_data)
    _p5_probe_early(bad)["deferred_verification"][key] = replacement
    with pytest.raises(m.Run9ValidationError, match="must contain the marker"):
        m.validate_probe_manifest(bad)


def test_negative_fix32_direct_call_unknown_key_rejected() -> None:
    value = {
        "status": m.P5_DEFERRED_VERIFICATION_STATUS,
        "blocked_by": [
            "practice_audio_split_manifest_sha", "education_technique_lesson_manifest_sha",
        ],
        "verification_procedure": "x", "consumption_prohibition": "x",
        "unexpected_extra_key": "x",
    }
    with pytest.raises(m.Run9ValidationError, match="unknown key"):
        m._validate_p5_deferred_verification(value, field="test")


# ---------------------------------------------------------------------------
# PR #322 第20巡指摘 Fix 33（P2, 採用）: Fix 31 の構造述語（非減少 pitch
# 系列）は「減少しないこと」しか検証しておらず、テンプレートと cell を
# 協調して 60→60→60（全て同一 pitch）へ amendment すれば「上行が一切
# ない」まま diagnostic_structural_pitch_rise を名乗れてしまっていた——
# 述語へ厳密増加（終端 pitch > 先頭 pitch）を追加する。
# ---------------------------------------------------------------------------


def test_fix33_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)
    p2 = _p2_probe(manifest_data)
    cell = _cell_by_id(p2, "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    pitches = [n["pitch_midi"] for n in cell["notes"]]
    assert pitches[-1] > pitches[0]  # 厳密増加であることの回帰確認


def test_negative_fix33_all_equal_pitch_manifest_mutation(manifest_data: Dict[str, Any]) -> None:
    """本指摘の核心シナリオ: notes 全体を 60→60→60（全て同一 pitch）へ
    書き換える——非減少ではあるが上行が一切ない。テンプレート凍結
    （Fix 31）にも同時に違反するため、まず構造述語（Fix 33）で fail-closed
    になることを確認する（`_validate_p2_diagnostic_pitch_rise_cell()`
    内で構造述語がテンプレート照合より先に走る）。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    for note in cell["notes"]:
        note["pitch_midi"] = 60
    with pytest.raises(m.Run9ValidationError, match="strictly greater"):
        m.validate_probe_manifest(bad)


def test_negative_fix33_flat_pitch_direct_call_independent_of_template() -> None:
    """裁定済み対応の直接単体呼び出し版: テンプレート未登録の cell_id で
    全 note 同一 pitch にし、構造述語（厳密増加）がテンプレート凍結から
    独立に効くことを確認する。"""
    cell = {
        "cell_id": "SYNTHETIC-NOT-IN-TEMPLATE",
        "notes": [
            {
                "kana": "た", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": False,
            },
            {
                "kana": "み", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": True,
            },
        ],
    }
    with pytest.raises(m.Run9ValidationError, match="strictly greater"):
        m._validate_p2_diagnostic_pitch_rise_cell(cell, field="test")


def test_negative_fix33_distinguished_from_fix31_non_decreasing_violation(
    manifest_data: Dict[str, Any],
) -> None:
    """裁定済み対応「既存の非減少違反と区別」: 65→62→65（先頭=末尾で
    Fix 33 の厳密増加チェックにも本来違反するが、中間で減少している
    ため Fix 31 の非減少チェックがより先に発火し、そちらのメッセージで
    fail-closed になることを確認する——2つの構造述語が独立の検査であり、
    互いを覆い隠さないことの回帰確認。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    cell["notes"][0]["pitch_midi"] = 65
    cell["notes"][1]["pitch_midi"] = 62
    cell["notes"][2]["pitch_midi"] = 65
    with pytest.raises(m.Run9ValidationError, match="non-decreasing"):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# 負例（項目16）: 各 fail-closed
# ---------------------------------------------------------------------------


def _mutate(manifest_data: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(manifest_data)


def test_negative_probe_missing_5_of_6(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"] = bad["probes"][:5]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_unknown_probe_id(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["probe_id"] = "P6"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_duplicate_cell_id(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][1]["cells"][0]["cell_id"] = bad["probes"][0]["cells"][0]["cell_id"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_empty_notes(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["notes"] = []
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize("bad_value", [True, False, 64.0, "64"])
def test_negative_pitch_midi_wrong_type(manifest_data: Dict[str, Any], bad_value: Any) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["notes"][0]["pitch_midi"] = bad_value
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_performance_seed_is_learning_seed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["render_contract"]["performance_seed"] = m.LEARNING_SEED
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize("entry_name", list(m._REVISION_BRIDGE_ENTRY_NAMES))
def test_negative_revision_bridge_entry_missing(
    manifest_data: Dict[str, Any], entry_name: str
) -> None:
    bad = _mutate(manifest_data)
    del bad["revision_bridge"][entry_name]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_prohibitions_marker_missing(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["prohibitions"] = ["a placeholder statement with no required marker"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_pr333_r9_identity_axis_source_missing_rev06_supersede_marker(
    manifest_data: Dict[str, Any],
) -> None:
    """PR #333 第9巡指摘（P1、採用）の回帰: `measurement_boundary.
    identity_axis_source` が rev 0.6 の supersede 先
    （`identity_decision_protocol_v0.6.json`）へ言及しない旧文言相当の
    宣言に戻ると fail-closed で拒否される（calibration・閾値・判定規則の
    正本が identity_metric_space.json のままという stale な現在形宣言の
    再発防止）。"""
    bad = _mutate(manifest_data)
    bad["measurement_boundary"]["identity_axis_source"] = (
        "inputs/identity_metric_space.json が正本"
        "（domains/identity_domain_run9_v1.json の metric_space_sha としてpin済み）。"
        "distance/calibration/confuser_controlの式・閾値は本manifestで重複定義しない。"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_pr333_r9_identity_axis_source_missing_supersede_word(
    manifest_data: Dict[str, Any],
) -> None:
    """同上: `identity_decision_protocol_v0.6.json` への言及があっても
    supersede の語を欠く文言は依然として拒否される（両マーカーが独立に
    必須であることの確認）。"""
    bad = _mutate(manifest_data)
    bad["measurement_boundary"]["identity_axis_source"] = (
        "inputs/identity_metric_space.json が正本"
        "（metric_space_sha としてpin済み）。calibration・閾値・判定規則は "
        "inputs/identity_decision_protocol_v0.6.json も参照する。"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_pr333_r10_scope_statement_missing_rev06_supersede_marker(
    manifest_data: Dict[str, Any],
) -> None:
    """PR #333 第10巡指摘（P2、採用）の回帰: 第9巡は `identity_axis_source`
    のみを rev 0.6 supersede マーカーで守り、`measurement_boundary.
    scope_statement` は汎用文言（「何を鳴らすか」/「どう測るかは対象外」）
    のみの検査に留まっていた。scope_statement が identity_decision_
    protocol_v0.6.json への supersede に言及しない旧文言相当（第9巡是正前
    と同型）に戻ると fail-closed で拒否されることを確認する（汎用文言自体
    は保持したまま rev 0.6 言及だけを欠く最小欠陥）。"""
    bad = _mutate(manifest_data)
    bad["measurement_boundary"]["scope_statement"] = (
        "本manifestが定義するのは何を鳴らすか（score cells + render契約 + "
        "take台帳）のみ。どう測るかは対象外——identity軸は"
        "inputs/identity_metric_space.json（metric_space_sha としてpin済み）"
        "が正本、P4/P5のdevelopment/generalization軸の測定仕様は"
        "measurement_spec_sha（別欄・PENDINGのまま）が別途凍結する。"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_positive_pr333_r11_identity_axis_source_declares_exact_supersede_fragment_ref(
    manifest_data: Dict[str, Any],
) -> None:
    """PR #333 第11巡指摘2（P2、採用）: 実 manifest の
    `measurement_boundary.identity_axis_source` が、supersede 宣言の実在
    箇所（`identity_decision_protocol_v0.6.json` 側の `supersede_
    declaration.superseded_sections`）を逐語で指していること——第9巡是正
    時に誤って `identity_metric_space.json` 側を指していた欠陥（第11巡で
    是正）の回帰確認。"""
    assert (
        m._IDENTITY_AXIS_SOURCE_SUPERSEDE_FRAGMENT_REF_MARKER
        in manifest_data["measurement_boundary"]["identity_axis_source"]
    )
    assert m._IDENTITY_AXIS_SOURCE_SUPERSEDE_FRAGMENT_REF_MARKER == (
        "identity_decision_protocol_v0.6.json#supersede_declaration.superseded_sections"
    )
    # 誤 prefix（identity_metric_space.json 側）は manifest 本文には出現
    # しない（旧文言の履歴引用〔旧文言...〕ブロックは意図的に除外——旧文言
    # 自体は supersede 節への言及を欠いていたのであり、誤った prefix の
    # fragment 参照を含んではいなかった）。
    wrong_prefix_fragment = (
        "identity_metric_space.json#supersede_declaration.superseded_sections"
    )
    assert wrong_prefix_fragment not in manifest_data["measurement_boundary"][
        "identity_axis_source"
    ]


def test_negative_pr333_r11_identity_axis_source_wrong_supersede_declaration_file_prefix(
    manifest_data: Dict[str, Any],
) -> None:
    """PR #333 第11巡指摘2（P2、採用）の回帰: `identity_axis_source` の
    fragment 参照が誤って `identity_metric_space.json` 側の
    `supersede_declaration.superseded_sections` を指す（第11巡是正前と
    同型の誤 prefix）と fail-closed で拒否される——`supersede_declaration`
    節は `identity_decision_protocol_v0.6.json` 側にのみ実在し
    （`identity_metric_space.json` は本改訂で無改変のため同節を持たない）、
    第10巡の汎用マーカー検査（ファイル名・"supersede" 語の部分文字列存在
    のみ）はこの誤 prefix を捕捉できなかった——他所の正本表明文言に
    `identity_decision_protocol_v0.6.json` という文字列自体は出現するため
    汎用マーカーは素通りしていた。本テストは第11巡で追加した逐語
    fragment マーカーがこれを閉じることを確認する。"""
    bad = _mutate(manifest_data)
    bad["measurement_boundary"]["identity_axis_source"] = bad["measurement_boundary"][
        "identity_axis_source"
    ].replace(
        "identity_decision_protocol_v0.6.json#supersede_declaration.superseded_sections",
        "identity_metric_space.json#supersede_declaration.superseded_sections",
    )
    # 誤 prefix 差し替え後も汎用マーカー（ファイル名・"supersede" 語）自体は
    # 引き続き充足していること（第10巡の検査だけでは通ってしまうことの
    # 直接確認——本テストが実際に第11巡の逐語マーカーで初めて拒否される
    # ことを示すための前提確認）。
    for generic_marker in m._REV06_SUPERSEDE_DECLARATION_MARKERS:
        assert generic_marker in bad["measurement_boundary"]["identity_axis_source"]
    with pytest.raises(
        m.Run9ValidationError, match="measurement_boundary.identity_axis_source"
    ):
        m.validate_probe_manifest(bad)


def test_negative_pr333_r10_scope_statement_missing_supersede_word(
    manifest_data: Dict[str, Any],
) -> None:
    """同上: `identity_decision_protocol_v0.6.json` への言及があっても
    supersede の語を欠く scope_statement は依然として拒否される（両
    マーカーが独立に必須であることの確認、identity_axis_source 側の
    第9巡回帰テストと同型）。"""
    bad = _mutate(manifest_data)
    bad["measurement_boundary"]["scope_statement"] = (
        "本manifestが定義するのは何を鳴らすか（score cells + render契約 + "
        "take台帳）のみ。どう測るかは対象外——identity軸のfeature/distance"
        "生成定義はinputs/identity_metric_space.json（metric_space_sha と"
        "してpin済み）が正本のまま、calibration・閾値・判定規則は"
        "inputs/identity_decision_protocol_v0.6.jsonも参照する、"
        "P4/P5のdevelopment/generalization軸の測定仕様はmeasurement_spec_sha"
        "（別欄・PENDINGのまま）が別途凍結する。"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_prohibitions_missing_render_infeasible_carveout(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    bad["prohibitions"] = [
        "render後のcellの追加を禁止する。",
        "結果を見た後のprobe変更を禁止する。",
        "測定仕様の変更を本manifestで行わない。",
    ]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_p3_role_missing_diagnostic_marker(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    for p in bad["probes"]:
        if p["probe_id"] == "P3":
            p["role"] = "P3 の説明だが marker を含まない"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_p0_note_outside_central_register(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["notes"][0]["pitch_midi"] = 73
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_p5_note_outside_baseline_domain(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    for p in bad["probes"]:
        if p["probe_id"] == "P5":
            p["cells"][0]["notes"][0]["pitch_midi"] = 91
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_unknown_top_level_key(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["unexpected_extra_field"] = "not allowed"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_wrong_schema(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["schema"] = "run9-probe-manifest/9.9"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_source_sha256_tampered(manifest_data: Dict[str, Any]) -> None:
    """P0 cell の source.transcribed_from_sha256 を実 score.py の sha256
    と食い違わせると拒否される（逐語照合の fail-closed 確認）。"""
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["source"]["transcribed_from_sha256"] = "0" * 64
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第1巡指摘 Fix 1（P1, 採用）: harness_runtime_seed_policy
# ---------------------------------------------------------------------------


def test_fix1_harness_runtime_seed_policy_present_and_correct(
    manifest_data: Dict[str, Any],
) -> None:
    policy = manifest_data["render_contract"]["harness_runtime_seed_policy"]
    assert policy["harness_hardcoded_seed"] == 42
    assert "gate_synth.py:149" in policy["harness_hardcoded_seed_source"]
    assert "1213-1214" in policy["harness_hardcoded_seed_source"]
    assert "repository_commit_sha" in policy["freeze_basis"]
    assert "fail-closed" in policy["runtime_verification_condition"]
    assert "42" in policy["runtime_verification_condition"]
    assert "配線する変更は行わない" in policy["no_wiring_declaration"]
    assert "909001" in policy["no_wiring_declaration"]


def test_fix1_performance_seed_note_disambiguates_genome_policy_from_onnx_runtime(
    manifest_data: Dict[str, Any],
) -> None:
    note = manifest_data["render_contract"]["performance_seed_note"]
    assert "performance policy seed" in note
    assert "ONNX runtime の乱数 seed ではない" in note
    assert str(m.LEARNING_SEED) in note


def test_fix1_same_conditions_note_covers_both_seed_layers(
    manifest_data: Dict[str, Any],
) -> None:
    note = manifest_data["render_contract"]["same_conditions_note"]
    assert "item 13" in note and "item 18" in note and "§27" in note
    assert str(m.SHARED_PERFORMANCE_SEED) in note
    assert "42" in note


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda d: d["render_contract"].pop("harness_runtime_seed_policy"), "missing section"),
        (
            lambda d: d["render_contract"]["harness_runtime_seed_policy"].__setitem__(
                "harness_hardcoded_seed", 909001
            ),
            "wrong seed value",
        ),
        (
            lambda d: d["render_contract"]["harness_runtime_seed_policy"].__setitem__(
                "harness_hardcoded_seed", True
            ),
            "bool seed value",
        ),
        (
            lambda d: d["render_contract"]["harness_runtime_seed_policy"].__setitem__(
                "no_wiring_declaration", "no marker here"
            ),
            "no_wiring_declaration missing marker",
        ),
        (
            lambda d: d["render_contract"]["harness_runtime_seed_policy"].__setitem__(
                "runtime_verification_condition", "no marker here"
            ),
            "runtime_verification_condition missing marker",
        ),
        (
            lambda d: d["render_contract"]["harness_runtime_seed_policy"].__setitem__(
                "freeze_basis", "no marker here"
            ),
            "freeze_basis missing marker",
        ),
        (
            lambda d: d["render_contract"].__setitem__("performance_seed_note", "no markers 909002"),
            "performance_seed_note missing genome-policy markers",
        ),
        (
            lambda d: d["render_contract"].__setitem__(
                "same_conditions_note", "§27 item 13 item 18 909001 only"
            ),
            "same_conditions_note missing runtime-layer (42) marker",
        ),
    ],
)
def test_negative_fix1_harness_runtime_seed_policy(
    manifest_data: Dict[str, Any], mutate, label: str
) -> None:
    bad = _mutate(manifest_data)
    mutate(bad)
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第1巡指摘 Fix 2（P2, 採用）: factor_levels の形状 + cell 対応
# ---------------------------------------------------------------------------


def _p1_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p1,) = [p for p in data["probes"] if p["probe_id"] == "P1"]
    return p1


def test_fix2_factor_levels_axes_shape(manifest_data: Dict[str, Any]) -> None:
    for probe_id in ("P1", "P2", "P3"):
        (probe,) = [p for p in manifest_data["probes"] if p["probe_id"] == probe_id]
        axes = probe["factor_levels"]["axes"]
        assert axes, f"{probe_id} factor_levels.axes must be non-empty"
        for axis_name, levels in axes.items():
            assert levels, f"{probe_id}.{axis_name} must be non-empty"
            for level_name, value in levels.items():
                assert not isinstance(value, bool), f"{probe_id}.{axis_name}.{level_name} is bool"
                assert isinstance(value, (int, float, str))


def test_fix2_every_cell_declares_levels_referencing_axes(manifest_data: Dict[str, Any]) -> None:
    for probe_id in ("P1", "P2", "P3"):
        (probe,) = [p for p in manifest_data["probes"] if p["probe_id"] == probe_id]
        axes = probe["factor_levels"]["axes"]
        for cell in probe["cells"]:
            # Fix 11: diagnostic_role cell（levels 非保持）は操作可能軸
            # システムの対象外——スキップする。
            if "levels" not in cell:
                continue
            levels = cell["levels"]
            assert levels, f"{cell['cell_id']} must declare non-empty levels"
            for axis_name, level_name in levels.items():
                assert axis_name in axes, f"{cell['cell_id']} references unknown axis {axis_name!r}"
                assert level_name in axes[axis_name], (
                    f"{cell['cell_id']} references unknown level {level_name!r} in {axis_name!r}"
                )


def test_fix2_every_declared_level_used_by_at_least_one_cell(
    manifest_data: Dict[str, Any],
) -> None:
    for probe_id in ("P1", "P2", "P3"):
        (probe,) = [p for p in manifest_data["probes"] if p["probe_id"] == probe_id]
        axes = probe["factor_levels"]["axes"]
        used: Dict[str, set] = {axis_name: set() for axis_name in axes}
        for cell in probe["cells"]:
            for axis_name, level_name in cell.get("levels", {}).items():
                used[axis_name].add(level_name)
        for axis_name, levels in axes.items():
            assert set(levels) == used[axis_name], (
                f"{probe_id}.{axis_name}: declared {sorted(levels)} vs used {sorted(used[axis_name])}"
            )


def test_negative_fix2_factor_levels_is_empty_list(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["factor_levels"] = []
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_factor_levels_axes_empty_dict(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["factor_levels"]["axes"] = {}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_cell_references_unknown_level(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["cells"][0]["levels"] = {"register": "does-not-exist"}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_cell_references_unknown_axis(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["cells"][0]["levels"] = {"not_a_real_axis": "low"}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_unused_declared_level_rejected(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["factor_levels"]["axes"]["register"]["extreme"] = 100
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_axis_value_bool_rejected(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["factor_levels"]["axes"]["register"]["low"] = True
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_cell_missing_levels_key(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    del _p1_probe(bad)["cells"][0]["levels"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix2_cell_levels_empty_dict(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["cells"][0]["levels"] = {}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第2巡指摘 Fix 3（P2, 採用）: 軸別の意味照合
# ---------------------------------------------------------------------------


def _cell_by_id(probe: Dict[str, Any], cell_id: str) -> Dict[str, Any]:
    (cell,) = [c for c in probe["cells"] if c["cell_id"] == cell_id]
    return cell


def test_fix3_positive_manifest_notes_match_declared_levels(manifest_data: Dict[str, Any]) -> None:
    """回帰確認: Fix 3 導入後も実体 manifest（正しく宣言済み）は素通りする。"""
    m.validate_probe_manifest(manifest_data)


def test_negative_fix3_register_midi_mismatch(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")
    cell["notes"][0]["pitch_midi"] = 65  # 宣言は low=57
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix3_duration_mismatch(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")
    cell["notes"][0]["duration_beats"] = 4  # 宣言は short=1
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix3_transition_direction_mismatch(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-TRANS-LOW-TO-HIGH")
    cell["notes"][0]["pitch_midi"] = 50  # 宣言は "57->65"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix3_onset_kana_mismatch(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    (p2,) = [p for p in bad["probes"] if p["probe_id"] == "P2"]
    cell = _cell_by_id(p2, "P2-ONSET-FRICATIVE-S")
    cell["notes"][-1]["kana"] = "ぎ"  # 宣言は fricative_s だが ぎ は stop_g_voiced
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# test_negative_fix3_phrase_dynamics_structure_broken は PR #322 第5巡
# 指摘 Fix 11（P1, 採用）により削除した——`phrase_dynamics` 軸自体を
# 操作可能軸システムから除去したため、この攻撃経路（軸の構造検証破り）
# はもう存在しない。P2-PHRASE-BUILD-WEAK-TO-STRONG は `diagnostic_role`
# （levels とは独立の cell 属性）で再分類済み——対応する回帰・負例は
# 「PR #322 第5巡指摘 Fix 11」節を参照。


def test_negative_fix3_release_duration_override_mismatch(manifest_data: Dict[str, Any]) -> None:
    """PR #322 第12巡指摘 Fix 22: release_duration の照合対象は cell の
    note フィールドではなく `final_phone_dur_override` pin へ移った
    （第11巡 Fix 21 の一時的な duration_beats 照合・軸改名はいずれも
    撤回済み——4 cell の phrase-final duration_beats は現在すべて等値）。"""
    bad = _mutate(manifest_data)
    (p3,) = [p for p in bad["probes"] if p["probe_id"] == "P3"]
    cell = _cell_by_id(p3, "P3-RELEASE-LONG-VOICED")
    cell["final_phone_dur_override"]["terminal_extension_ms"] = 40.0  # 宣言は long=80.0
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix3_ending_voicing_inverted(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    (p3,) = [p for p in bad["probes"] if p["probe_id"] == "P3"]
    cell = _cell_by_id(p3, "P3-RELEASE-SHORT-VOICED")
    cell["notes"][-1]["kana"] = "す"  # 宣言は voiced だが す は unvoiced
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix3_unregistered_axis_rejected() -> None:
    """未登録 axis 名は意味照合器が存在しないため fail-closed で拒否される
    （新しい軸を追加したのに checker を追加し忘れる事故を防ぐ構造）。"""
    cell = {
        "cell_id": "X",
        "tempo_bpm": 72.0,
        "notes": [
            {
                "kana": "ら", "pitch_midi": 60, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": True,
            }
        ],
        "levels": {"not_a_real_axis": "whatever"},
    }
    with pytest.raises(m.Run9ValidationError):
        m._validate_axis_semantic_value(
            axis_name="not_a_real_axis", level_name="whatever", axis_value="whatever",
            cell=cell, field="test",
        )


# ---------------------------------------------------------------------------
# PR #322 第2巡指摘 Fix 4（P2, 採用）: 転記元不在時の fail-closed
# ---------------------------------------------------------------------------


def _valid_p0_source(sha256_hex: str) -> Dict[str, Any]:
    return {
        "transcribed_from": "voice_genesis/singer/score.py",
        "transcribed_from_sha256": sha256_hex,
        "transcription_scope": "test",
        "verbatim": True,
    }


def test_fix4_positive_real_score_py_present_and_matching() -> None:
    """回帰確認: 実 score.py（read-only 参照、無改変）は既定パスのままで
    引き続き受理される。"""
    actual_sha = m.compute_file_sha256(m.SCORE_PY_REFERENCE_PATH)
    m._validate_probe_cell_source(_valid_p0_source(actual_sha), field="test")


def test_negative_fix4_missing_source_file_fails_closed(tmp_path: Path) -> None:
    """score.py パスを一時 rename する monkeypatch ではなく、
    `score_path` 引数を存在しない tmp パスへ差し替えることで不在時の
    fail-closed 挙動を検証する（実 score.py は一切触れない）。"""
    nonexistent = tmp_path / "does_not_exist" / "score.py"
    assert not nonexistent.exists()
    source = _valid_p0_source("0" * 64)
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m._validate_probe_cell_source(source, field="test", score_path=nonexistent)


def test_negative_fix4_missing_source_file_via_full_manifest(
    manifest_data: Dict[str, Any], tmp_path: Path
) -> None:
    """`validate_probe_manifest()` 経由でも、P0 cell の source 検証に
    渡る score_path が存在しなければ拒否される（モジュール定数を直接
    monkeypatch して full-chain の挙動も確認する——`monkeypatch` fixture
    ではなく setattr/finally で明示的に復元し、実ファイルには一切触れ
    ない）。PR #322 第5巡 Fix 12 導入後は `SCORE_PY_REFERENCE_PATH` を
    `_load_score_py_module()`（Fix 12、probe 検証より前に1回だけ実行）
    も共有するため、実際に先に fail-closed する箇所は Fix 12 側のゲート
    になった——いずれにせよ full-chain が fail-closed であることに変わり
    はない（具体的な例外メッセージの発生源は問わない）。"""
    original = m.SCORE_PY_REFERENCE_PATH
    fake = tmp_path / "does_not_exist" / "score.py"
    try:
        m.SCORE_PY_REFERENCE_PATH = fake  # type: ignore[misc]
        with pytest.raises(m.Run9ValidationError):
            m.validate_probe_manifest(_mutate(manifest_data))
    finally:
        m.SCORE_PY_REFERENCE_PATH = original
    # 復元後は通常どおり通過することを確認する（後続テストへの汚染防止）。
    m.validate_probe_manifest(_mutate(manifest_data))


# ---------------------------------------------------------------------------
# PR #322 第2巡指摘 Fix 5（P2, 採用）: identity_metric_space_ref の
# dotted path 全体解決
# ---------------------------------------------------------------------------


def test_fix5_positive_all_revision_bridge_refs_resolve(manifest_data: Dict[str, Any]) -> None:
    document = m._load_identity_metric_space_document()
    for entry_name, entry in manifest_data["revision_bridge"].items():
        m._resolve_identity_metric_space_ref(
            entry["identity_metric_space_ref"], document=document, field=entry_name
        )


def test_negative_fix5_deep_segment_typo(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["revision_bridge"]["reference_render"]["identity_metric_space_ref"] = (
        "inputs/identity_metric_space.json#calibration.does_not_exist"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix5_mid_segment_typo(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["revision_bridge"]["reference_render"]["identity_metric_space_ref"] = (
        "inputs/identity_metric_space.json#calibration.freeze_threshold.does_not_exist"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix5_malformed_empty_suffix(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["revision_bridge"]["reference_render"]["identity_metric_space_ref"] = (
        "inputs/identity_metric_space.json#"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix5_missing_identity_metric_space_document(tmp_path: Path) -> None:
    """`inputs/identity_metric_space.json` の文書自体が見つからない場合
    も fail-closed（凍結・改変禁止の read-only 入力を一時ディレクトリの
    存在しないパスへ差し替えるだけで、実ファイルには触れない）。"""
    nonexistent = tmp_path / "does_not_exist" / "identity_metric_space.json"
    with pytest.raises(m.Run9ValidationError, match="実在が"):
        m._load_identity_metric_space_document(path=nonexistent)


# ---------------------------------------------------------------------------
# PR #322 第3巡指摘 Fix 6（P2, 採用）: renderer の mora 文法照合
# ---------------------------------------------------------------------------


def test_fix6_positive_all_notes_single_mora(manifest_data: Dict[str, Any]) -> None:
    """回帰確認: 実 manifest の全24 cell・全 note が phoneme_jp の mora
    文法でちょうど1モーラに分割されることを確認する（full-chain 経由）。"""
    m.validate_probe_manifest(manifest_data)
    phoneme_jp_module = m._load_phoneme_jp_module()
    for probe in manifest_data["probes"]:
        for cell in probe["cells"]:
            for note in cell["notes"]:
                m._require_single_mora_kana(
                    note["kana"], phoneme_jp_module=phoneme_jp_module, field="test"
                )


def test_negative_fix6_unsupported_character_kana(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["notes"][0]["kana"] = "abc"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix6_multi_mora_kana(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    bad["probes"][0]["cells"][0]["notes"][0]["kana"] = "さくら"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix6_applies_outside_p2_p3_class_tables(manifest_data: Dict[str, Any]) -> None:
    """P2/P3 のクラス表対象外の note（P5 の note）も Fix 6 の対象である
    ことを確認する。"""
    bad = _mutate(manifest_data)
    (p5,) = [p for p in bad["probes"] if p["probe_id"] == "P5"]
    p5["cells"][0]["notes"][0]["kana"] = "xyz"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix6_phoneme_jp_module_missing(tmp_path: Path) -> None:
    """phoneme_jp.py パスを一時 rename する monkeypatch ではなく、`path`
    引数を存在しない tmp パスへ差し替えることで不在時の fail-closed
    挙動を検証する（実 phoneme_jp.py は一切触れない）。"""
    nonexistent = tmp_path / "does_not_exist" / "phoneme_jp.py"
    with pytest.raises(m.Run9ValidationError, match="実在が"):
        m._load_phoneme_jp_module(path=nonexistent)


def test_negative_fix6_phoneme_jp_module_missing_via_full_manifest(
    manifest_data: Dict[str, Any], tmp_path: Path
) -> None:
    """`validate_probe_manifest()` 経由でも phoneme_jp.py 不在が
    fail-closed になることを、モジュール定数の一時差し替え（finally で
    復元、実ファイル無改変）で確認する。"""
    original = m.PHONEME_JP_REFERENCE_PATH
    fake = tmp_path / "does_not_exist" / "phoneme_jp.py"
    try:
        m.PHONEME_JP_REFERENCE_PATH = fake  # type: ignore[misc]
        with pytest.raises(m.Run9ValidationError, match="実在が"):
            m.validate_probe_manifest(_mutate(manifest_data))
    finally:
        m.PHONEME_JP_REFERENCE_PATH = original
    m.validate_probe_manifest(_mutate(manifest_data))  # 復元後は通常どおり通過


# ---------------------------------------------------------------------------
# PR #322 第3巡指摘 Fix 7（P2, 採用）: P2 onset cell の共通 filler 強制
# ---------------------------------------------------------------------------


def _p2_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p2,) = [p for p in data["probes"] if p["probe_id"] == "P2"]
    return p2


def test_fix7_filler_tuple_declared_and_matches_cells(manifest_data: Dict[str, Any]) -> None:
    p2 = _p2_probe(manifest_data)
    fl = p2["factor_levels"]
    assert fl["medial_filler_kana"] == "か"
    assert fl["medial_filler_beats"] == 1
    assert fl["medial_filler_pitch_midi"] == 60
    for cell in p2["cells"]:
        if "onset_consonant_class" not in cell.get("levels", {}):
            continue
        prefix = cell["notes"][:-1]
        assert len(prefix) == 1
        assert prefix[0]["kana"] == fl["medial_filler_kana"]
        assert prefix[0]["duration_beats"] == fl["medial_filler_beats"]
        assert prefix[0]["pitch_midi"] == fl["medial_filler_pitch_midi"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda note: note.__setitem__("kana", "た"),
        lambda note: note.__setitem__("pitch_midi", 65),
        lambda note: note.__setitem__("duration_beats", 2),
    ],
)
def test_negative_fix7_filler_note_mismatch(manifest_data: Dict[str, Any], mutate) -> None:
    bad = _mutate(manifest_data)
    p2 = _p2_probe(bad)
    cell = _cell_by_id(p2, "P2-ONSET-STOP-K")
    mutate(cell["notes"][0])
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix7_filler_mismatch_only_one_cell_diverges(manifest_data: Dict[str, Any]) -> None:
    """複数 onset cell のうち1つだけ filler を変えても、他 cell との
    ペアワイズ比較ではなく凍結タプルとの直接比較で検出される。"""
    bad = _mutate(manifest_data)
    p2 = _p2_probe(bad)
    cell = _cell_by_id(p2, "P2-ONSET-VOWEL-ONLY")
    cell["notes"][0]["kana"] = "の"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "missing_key", ["medial_filler_kana", "medial_filler_beats", "medial_filler_pitch_midi"]
)
def test_negative_fix7_filler_declaration_key_missing(
    manifest_data: Dict[str, Any], missing_key: str
) -> None:
    bad = _mutate(manifest_data)
    del _p2_probe(bad)["factor_levels"][missing_key]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix7_onset_cell_prefix_length_not_one() -> None:
    """onset cell の前置 note が0個/2個以上の場合も拒否される（実
    manifest では常にちょうど1個のため、private 関数への直接単体呼び出し
    で検証する——既存テスト流儀と同型）。"""
    factor_levels = {
        "medial_filler_kana": "か", "medial_filler_beats": 1, "medial_filler_pitch_midi": 60,
    }
    cell_no_prefix = {
        "cell_id": "X",
        "notes": [
            {
                "kana": "さ", "pitch_midi": 65, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": True,
            }
        ],
        "levels": {"onset_consonant_class": "fricative_s"},
    }
    with pytest.raises(m.Run9ValidationError):
        m._validate_p2_onset_filler_consistency(
            factor_levels=factor_levels, cells=[cell_no_prefix], field="test"
        )

    cell_two_prefix = {
        "cell_id": "Y",
        "notes": [
            {
                "kana": "か", "pitch_midi": 60, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": False,
            },
            {
                "kana": "か", "pitch_midi": 60, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": False,
            },
            {
                "kana": "さ", "pitch_midi": 65, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": True,
            },
        ],
        "levels": {"onset_consonant_class": "fricative_s"},
    }
    with pytest.raises(m.Run9ValidationError):
        m._validate_p2_onset_filler_consistency(
            factor_levels=factor_levels, cells=[cell_two_prefix], field="test"
        )


# ---------------------------------------------------------------------------
# PR #322 第14巡指摘 Fix 25（P2, 採用）: P2 onset cell の phrase-final
# target（pitch/duration）一貫性強制
# ---------------------------------------------------------------------------


def test_fix25_target_tuple_declared_and_matches_cells(manifest_data: Dict[str, Any]) -> None:
    p2 = _p2_probe(manifest_data)
    fl = p2["factor_levels"]
    assert fl["onset_target_pitch_midi"] == 65
    assert fl["onset_target_duration_beats"] == 2
    for cell in p2["cells"]:
        if "onset_consonant_class" not in cell.get("levels", {}):
            continue
        final = cell["notes"][-1]
        assert final["is_phrase_final"] is True
        assert final["pitch_midi"] == fl["onset_target_pitch_midi"]
        assert final["duration_beats"] == fl["onset_target_duration_beats"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda note: note.__setitem__("pitch_midi", 67),
        lambda note: note.__setitem__("duration_beats", 3),
    ],
)
def test_negative_fix25_target_note_mismatch(manifest_data: Dict[str, Any], mutate) -> None:
    """指摘の核心シナリオ: onset cell の phrase-final 検定 note の
    pitch/duration を変えても、Fix 7（前置 filler 一貫性）と
    `_check_axis_kana_class()`（kana クラスのみ）は共に検出できない
    ——Fix 25 の target 一貫性検証でのみ検出される。"""
    bad = _mutate(manifest_data)
    p2 = _p2_probe(bad)
    cell = _cell_by_id(p2, "P2-ONSET-STOP-K")
    mutate(cell["notes"][-1])
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix25_target_mismatch_only_one_cell_diverges(manifest_data: Dict[str, Any]) -> None:
    """複数 onset cell のうち1つだけ target を変えても、他 cell との
    ペアワイズ比較ではなく凍結タプルとの直接比較で検出される。"""
    bad = _mutate(manifest_data)
    p2 = _p2_probe(bad)
    cell = _cell_by_id(p2, "P2-ONSET-VOWEL-ONLY")
    cell["notes"][-1]["pitch_midi"] = 60
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "missing_key", ["onset_target_pitch_midi", "onset_target_duration_beats"]
)
def test_negative_fix25_target_declaration_key_missing(
    manifest_data: Dict[str, Any], missing_key: str
) -> None:
    bad = _mutate(manifest_data)
    del _p2_probe(bad)["factor_levels"][missing_key]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix25_direct_call_target_mismatch() -> None:
    """`_validate_p2_onset_target_consistency()` への直接単体呼び出しで、
    target タプル不一致を検出することを確認する（既存テスト流儀と同型）。"""
    factor_levels = {"onset_target_pitch_midi": 65, "onset_target_duration_beats": 2}
    cell = {
        "cell_id": "Z",
        "notes": [
            {
                "kana": "か", "pitch_midi": 60, "duration_beats": 1.0,
                "phrase_index": 0, "is_phrase_final": False,
            },
            {
                "kana": "さ", "pitch_midi": 67, "duration_beats": 2.0,
                "phrase_index": 0, "is_phrase_final": True,
            },
        ],
        "levels": {"onset_consonant_class": "fricative_s"},
    }
    with pytest.raises(m.Run9ValidationError):
        m._validate_p2_onset_target_consistency(
            factor_levels=factor_levels, cells=[cell], field="test"
        )


# ---------------------------------------------------------------------------
# PR #322 第4巡指摘 Fix 8（P2, 採用）: revision_bridge エントリ→期待 path
# の厳密対応
# ---------------------------------------------------------------------------


def test_fix8_all_entries_match_expected_paths(manifest_data: Dict[str, Any]) -> None:
    for entry_name, entry in manifest_data["revision_bridge"].items():
        assert entry["identity_metric_space_ref"] == m._REVISION_BRIDGE_EXPECTED_METRIC_REF[entry_name]


def test_negative_fix8_swap_valid_paths_between_entries(manifest_data: Dict[str, Any]) -> None:
    """reference_render と evaluated_renders は共に実在する path を持つが
    入れ替えると、実在走査（Fix 5）だけでは検出できず Fix 8 のエントリ別
    厳密対応でのみ検出される。"""
    bad = _mutate(manifest_data)
    rb = bad["revision_bridge"]
    a = rb["reference_render"]["identity_metric_space_ref"]
    b = rb["evaluated_renders"]["identity_metric_space_ref"]
    assert a != b  # 前提: 元々異なる path であることの確認
    rb["reference_render"]["identity_metric_space_ref"] = b
    rb["evaluated_renders"]["identity_metric_space_ref"] = a
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix8_entry_points_to_different_but_real_path(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    rb = bad["revision_bridge"]
    rb["pjs_reference"]["identity_metric_space_ref"] = rb["negative_reference"][
        "identity_metric_space_ref"
    ]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第18巡指摘 Fix 30（P2, 採用、Fix 8 と同族）: revision_bridge の
# render 系エントリ（reference_render/c0_replay_takes/c1_sham_takes/
# positive_reference/evaluated_renders）の cell_ref が全て
# `P0-NEUTRAL-SAKURA-FRAGMENT` と厳密一致することの機械強制。旧検証は
# 「probes[] に実在する cell_id のいずれかか」しか見ておらず、P0 以外の
# probe（P1/P4 等）の cell へ差し替えても通過していた。
# ---------------------------------------------------------------------------

_REVISION_BRIDGE_RENDER_ENTRY_NAMES: Tuple[str, ...] = (
    "reference_render", "c0_replay_takes", "c1_sham_takes", "positive_reference", "evaluated_renders",
)


def test_fix30_all_render_entries_reference_p0_cell(manifest_data: Dict[str, Any]) -> None:
    rb = manifest_data["revision_bridge"]
    for entry_name in _REVISION_BRIDGE_RENDER_ENTRY_NAMES:
        assert rb[entry_name]["cell_ref"] == "P0-NEUTRAL-SAKURA-FRAGMENT"
        assert rb[entry_name]["cell_ref"] == m._REVISION_BRIDGE_EXPECTED_CELL_REF[entry_name]
    # negative_reference/pjs_reference は新規 render 不要のため cell_ref を
    # 持たない（現行構造の確認 — 変更対象外）。
    assert "cell_ref" not in rb["negative_reference"]
    assert "cell_ref" not in rb["pjs_reference"]


@pytest.mark.parametrize("entry_name", ["evaluated_renders", "reference_render"])
def test_negative_fix30_render_entry_cell_ref_swapped_to_non_p0_cell(
    manifest_data: Dict[str, Any], entry_name: str,
) -> None:
    """本指摘の核心シナリオ: render 系エントリの cell_ref を P0 以外の
    probe の cell（P1/P4）へ振替える——`valid_cell_ids` への所属検査
    （旧実装）は probes[] 全体からの実在チェックのため通過してしまうが、
    Fix 30 のエントリ別厳密対応で検出される。"""
    bad = _mutate(manifest_data)
    other_cell_id = "P4-HELDOUT-ORIGINAL-FRAGMENT" if entry_name == "evaluated_renders" else (
        "P1-REG-LOW-DUR-SHORT"
    )
    bad["revision_bridge"][entry_name]["cell_ref"] = other_cell_id
    with pytest.raises(m.Run9ValidationError, match="must be exactly"):
        m.validate_probe_manifest(bad)


def test_negative_fix30_expected_cell_ref_table_missing_entry_direct_call() -> None:
    """`_REVISION_BRIDGE_EXPECTED_CELL_REF` に対応表エントリが存在しない
    render 系エントリ名で照合しようとすると `KeyError` になる（対応表は
    render 系5エントリの閉じた集合であり、部分的な欠落を静かに許さない
    ことの確認 — 既存 `_PROBE_EXPECTED_FACTOR_VALUES`/`_REVISION_BRIDGE_
    EXPECTED_METRIC_REF` と同じ辞書アクセス規約）。"""
    assert set(m._REVISION_BRIDGE_EXPECTED_CELL_REF.keys()) == set(
        _REVISION_BRIDGE_RENDER_ENTRY_NAMES
    )
    with pytest.raises(KeyError):
        _ = m._REVISION_BRIDGE_EXPECTED_CELL_REF["negative_reference"]


# ---------------------------------------------------------------------------
# PR #322 第4巡指摘 Fix 9（P2, 採用）: 凍結 cell_id 集合 + factorial 直積
# 被覆
# ---------------------------------------------------------------------------


def test_fix9_expected_cell_ids_match_manifest(manifest_data: Dict[str, Any]) -> None:
    for probe in manifest_data["probes"]:
        actual = {c["cell_id"] for c in probe["cells"]}
        assert actual == m._PROBE_EXPECTED_CELL_IDS[probe["probe_id"]]


def test_negative_fix9_delete_cell_whose_levels_remain_used_elsewhere(
    manifest_data: Dict[str, Any],
) -> None:
    """P1-REG-LOW-DUR-SHORT を削除しても low/short は他 cell（LOW-DUR-LONG
    / MID-DUR-SHORT 等）に残るため、旧 Fix 2/3 の水準実在チェックだけでは
    通過してしまっていた欠陥の回帰確認。"""
    bad = _mutate(manifest_data)
    p1 = _p1_probe(bad)
    p1["cells"] = [c for c in p1["cells"] if c["cell_id"] != "P1-REG-LOW-DUR-SHORT"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix9_p3_factorial_cell_deleted(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    (p3,) = [p for p in bad["probes"] if p["probe_id"] == "P3"]
    p3["cells"] = [c for c in p3["cells"] if c["cell_id"] != "P3-RELEASE-SHORT-UNVOICED"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix9_surplus_cell_id(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    (p5,) = [p for p in bad["probes"] if p["probe_id"] == "P5"]
    extra = copy.deepcopy(p5["cells"][0])
    extra["cell_id"] = "P5-EXTRA-SURPLUS-CELL"
    p5["cells"].append(extra)
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix9_factorial_coverage_gap_isolated() -> None:
    """cell_id 集合の凍結チェックとは独立に、factorial 直積被覆の欠落
    のみを検証する（private 関数への直接単体呼び出し——既存テスト流儀と
    同型。P1-REG-HIGH-DUR-LONG に相当する組合せを欠落させる）。"""
    factor_levels = {
        "axes": {
            "register": {"low": 57, "mid": 62, "high": 65},
            "duration": {"short": 1, "long": 4},
        }
    }
    cells = [
        {"cell_id": "a", "levels": {"register": "low", "duration": "short"}},
        {"cell_id": "b", "levels": {"register": "low", "duration": "long"}},
        {"cell_id": "c", "levels": {"register": "mid", "duration": "short"}},
        {"cell_id": "d", "levels": {"register": "mid", "duration": "long"}},
        {"cell_id": "e", "levels": {"register": "high", "duration": "short"}},
        # ("high", "long") を意図的に欠落させる
    ]
    with pytest.raises(m.Run9ValidationError, match="high"):
        m._validate_probe_factorial_coverage(
            expected_probe_id="P1", factor_levels=factor_levels, cells=cells, field="test"
        )


def test_fix9_factorial_coverage_full_grid_passes() -> None:
    factor_levels = {
        "axes": {
            "register": {"low": 57, "mid": 62, "high": 65},
            "duration": {"short": 1, "long": 4},
        }
    }
    cells = [
        {"cell_id": f"{r}-{d}", "levels": {"register": r, "duration": d}}
        for r in ("low", "mid", "high")
        for d in ("short", "long")
    ]
    m._validate_probe_factorial_coverage(
        expected_probe_id="P1", factor_levels=factor_levels, cells=cells, field="test"
    )  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #322 第16巡指摘 Fix 28（P2, 採用、Fix 25/26 と同族の文脈凍結）: P1 の
# register×duration グリッド cell は kana を変えても通過し、transition
# cell は先頭/末尾 note の pitch_midi 系列しか見ておらず kana/duration
# 変更や中間 note の挿入が通過していた。
# ---------------------------------------------------------------------------


def test_fix28_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)


def test_fix28_grid_cells_share_identical_non_factor_note_fields(
    manifest_data: Dict[str, Any],
) -> None:
    p1 = _p1_probe(manifest_data)
    grid_cells = [
        c for c in p1["cells"]
        if "register" in c.get("levels", {}) and "duration" in c.get("levels", {})
    ]
    assert len(grid_cells) == 6
    shapes = {
        tuple(
            (n["kana"], n["phrase_index"], n["is_phrase_final"]) for n in c["notes"]
        )
        for c in grid_cells
    }
    assert len(shapes) == 1, f"grid cells must share identical non-factor note shape, got {shapes}"


def test_negative_fix28_grid_cell_kana_swapped(manifest_data: Dict[str, Any]) -> None:
    """本指摘の核心シナリオ: grid cell の kana を差し替えても、軸 checker
    （pitch_midi/duration_beats のみ照合）は検出できない——Fix 28 の
    cell 間一貫性検証でのみ検出される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")
    cell["notes"][0]["kana"] = "り"
    with pytest.raises(m.Run9ValidationError, match="diverge"):
        m.validate_probe_manifest(bad)


def test_negative_fix28_grid_cell_note_appended(manifest_data: Dict[str, Any]) -> None:
    """grid cell へ note を追加すると note 数不一致で拒否される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")
    extra = copy.deepcopy(cell["notes"][0])
    extra["is_phrase_final"] = False
    cell["notes"].insert(0, extra)
    with pytest.raises(m.Run9ValidationError, match="note"):
        m.validate_probe_manifest(bad)


def test_negative_fix28_direct_call_grid_field_diverges() -> None:
    """`_validate_p1_grid_note_context_consistency()` への直接単体呼び出し
    で、phrase_index の相違（ホワイトリスト外のフィールド）を検出する
    ことを確認する（既存テスト流儀と同型）。"""
    cells = [
        {
            "cell_id": "A", "levels": {"register": "low", "duration": "short"},
            "notes": [
                {
                    "kana": "ら", "pitch_midi": 57, "duration_beats": 1, "phrase_index": 0,
                    "is_phrase_final": True,
                }
            ],
        },
        {
            "cell_id": "B", "levels": {"register": "low", "duration": "long"},
            "notes": [
                {
                    "kana": "ら", "pitch_midi": 57, "duration_beats": 4, "phrase_index": 1,
                    "is_phrase_final": True,
                }
            ],
        },
    ]
    with pytest.raises(m.Run9ValidationError, match="diverge"):
        m._validate_p1_grid_note_context_consistency(cells=cells, field="test")


def test_negative_fix28_transition_cell_middle_pitch_inserted(
    manifest_data: Dict[str, Any],
) -> None:
    """本指摘の核心シナリオ: transition cell の先頭/末尾 note の間に中間
    pitch を挿入する——`_check_axis_transition_direction()`（先頭/末尾のみ
    参照）は通過するが、テンプレート凍結（Fix 28）で拒否される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-TRANS-LOW-TO-HIGH")
    middle = {
        "kana": "り", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
        "is_phrase_final": False,
    }
    cell["notes"].insert(1, middle)
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m.validate_probe_manifest(bad)


def test_negative_fix28_transition_cell_kana_changed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-TRANS-LOW-TO-HIGH")
    cell["notes"][0]["kana"] = "た"
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m.validate_probe_manifest(bad)


def test_negative_fix28_transition_cell_duration_changed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-TRANS-HIGH-TO-LOW")
    cell["notes"][-1]["duration_beats"] = 2
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m.validate_probe_manifest(bad)


def test_negative_fix28_direct_call_transition_length_mismatch() -> None:
    """`_validate_p1_transition_notes_template()` への直接単体呼び出しで、
    notes 配列の長さ不一致を検出することを確認する。"""
    cells = [
        {
            "cell_id": "P1-TRANS-LOW-TO-HIGH",
            "notes": [
                {
                    "kana": "ら", "pitch_midi": 57, "duration_beats": 1, "phrase_index": 0,
                    "is_phrase_final": False,
                },
            ],
        },
    ]
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m._validate_p1_transition_notes_template(cells=cells, field="test")


def test_fix28_direct_call_transition_ignores_non_transition_cell_id() -> None:
    """テンプレートに存在しない cell_id（transition cell 以外）は本関数の
    対象外——例外を投げずに素通りすることを確認する。"""
    cells = [{"cell_id": "P1-REG-LOW-DUR-SHORT", "notes": [{"kana": "x"}]}]
    m._validate_p1_transition_notes_template(cells=cells, field="test")  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #322 第5巡指摘 Fix 10（P2, 採用）: P4 held-out 分離の機械検証
# ---------------------------------------------------------------------------


def _p4_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p4,) = [p for p in data["probes"] if p["probe_id"] == "P4"]
    return p4


def test_fix10_positive_p4_separated_from_p0_p3(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)  # 例外を投げないことの確認（回帰）


def test_negative_fix10_p4_full_copy_of_p0_cell(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    p0_notes = copy.deepcopy(_p0_probe(bad)["cells"][0]["notes"])
    _p4_probe(bad)["cells"][0]["notes"] = p0_notes
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix10_p4_contiguous_subsequence_of_p1_cell(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    p1 = _p1_probe(bad)
    source_cell = _cell_by_id(p1, "P1-TRANS-LOW-TO-HIGH")
    _p4_probe(bad)["cells"][0]["notes"] = copy.deepcopy(source_cell["notes"])
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_fix10_helper_contiguous_subsequence_detection() -> None:
    assert m._is_contiguous_subsequence((1, 2), (0, 1, 2, 3))
    assert not m._is_contiguous_subsequence((1, 3), (0, 1, 2, 3))
    assert not m._is_contiguous_subsequence((), (0, 1, 2))
    assert not m._is_contiguous_subsequence((1, 2, 3, 4), (1, 2, 3))


# ---------------------------------------------------------------------------
# PR #322 第5巡指摘 Fix 11（P1, 採用）: P2 Energy 計器能力の境界宣言
# ---------------------------------------------------------------------------


def test_fix11_phrase_dynamics_axis_removed(manifest_data: Dict[str, Any]) -> None:
    p2 = _p2_probe(manifest_data)
    assert "phrase_dynamics" not in p2["factor_levels"]["axes"]


def test_fix11_diagnostic_cell_declared(manifest_data: Dict[str, Any]) -> None:
    p2 = _p2_probe(manifest_data)
    cell = _cell_by_id(p2, "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    assert "levels" not in cell
    assert cell["diagnostic_role"]["role_id"] == m._DIAGNOSTIC_STRUCTURAL_PITCH_RISE_ROLE_ID
    note = cell["diagnostic_role"]["scope_boundary_note"]
    assert "pitch 上行構造のみ" in note
    assert "energy 効果の帰属に使わない" in note


def test_fix11_p2_role_boundary_declaration_markers(manifest_data: Dict[str, Any]) -> None:
    role = _p2_probe(manifest_data)["role"]
    for marker in m._P2_ENERGY_BOUNDARY_MARKERS:
        assert marker in role


def test_negative_fix11_p2_role_missing_boundary_marker(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p2_probe(bad)["role"] = "phrase内の弱→強・onset class差を通じてEnergy/Attack応答をprobeする。"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix11_diagnostic_role_unknown_role_id(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    cell["diagnostic_role"]["role_id"] = "not_a_registered_role"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix11_diagnostic_role_scope_note_missing_marker(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    cell["diagnostic_role"]["scope_boundary_note"] = "no markers here"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix11_cell_has_both_levels_and_diagnostic_role(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-ONSET-FRICATIVE-S")
    cell["diagnostic_role"] = {
        "role_id": m._DIAGNOSTIC_STRUCTURAL_PITCH_RISE_ROLE_ID,
        "scope_boundary_note": "pitch 上行構造のみ を操作し、energy 効果の帰属に使わない。",
    }
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix11_cell_has_neither_levels_nor_diagnostic_role(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-ONSET-FRICATIVE-S")
    del cell["levels"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix11_reintroducing_phrase_dynamics_axis_rejected(
    manifest_data: Dict[str, Any],
) -> None:
    """`phrase_dynamics` は操作可能軸システムから完全除去済み——cell の
    `levels` へ復活させても未知 axis として拒否される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    del cell["diagnostic_role"]
    cell["levels"] = {"phrase_dynamics": "weak_to_strong_build"}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第19巡指摘 Fix 31（P2, 採用、Fix 28 transition テンプレートと
# 同族）: diagnostic_structural_pitch_rise role を持つ P2 cell の notes を
# テンプレート凍結 + 構造述語（非減少 pitch 系列・phrase-final マーカーの
# 終端位置）の両方で検証する。旧実装は scope_boundary_note の文言のみを
# 検証し、その文言が主張する notes の実体は一切照合していなかった。
# ---------------------------------------------------------------------------


def test_fix31_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)


def test_fix31_diagnostic_cell_notes_match_frozen_template(manifest_data: Dict[str, Any]) -> None:
    cell = _cell_by_id(_p2_probe(manifest_data), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    template = m._P2_DIAGNOSTIC_PITCH_RISE_NOTES_TEMPLATE["P2-PHRASE-BUILD-WEAK-TO-STRONG"]
    assert len(cell["notes"]) == len(template)
    for note, expected in zip(cell["notes"], template):
        assert dict(note) == dict(expected)
    pitches = [n["pitch_midi"] for n in cell["notes"]]
    assert pitches == sorted(pitches)  # 非減少（上行）であることの回帰確認


def test_negative_fix31_leading_pitch_made_non_monotonic(manifest_data: Dict[str, Any]) -> None:
    """本指摘の核心シナリオ: 先頭 pitch を 60→66 へ変更し 66→62→65 という
    非単調系列にする——scope_boundary_note の文言はそのままのため旧実装
    では通過していたが、Fix 31 の構造述語検証で検出される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    cell["notes"][0]["pitch_midi"] = 66
    with pytest.raises(m.Run9ValidationError, match="non-decreasing"):
        m.validate_probe_manifest(bad)


def test_negative_fix31_template_note_kana_swapped(manifest_data: Dict[str, Any]) -> None:
    """pitch 系列は非減少のまま保ちつつ kana だけ差し替える——構造述語は
    満たすがテンプレート凍結（全フィールド厳密一致）で検出される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    assert cell["notes"][0]["kana"] == "さ"
    cell["notes"][0]["kana"] = "そ"  # 同じ fricative_s クラスの別 kana
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m.validate_probe_manifest(bad)


def test_negative_fix31_template_note_duration_changed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-PHRASE-BUILD-WEAK-TO-STRONG")
    cell["notes"][-1]["duration_beats"] = 3
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m.validate_probe_manifest(bad)


def test_negative_fix31_direct_call_structural_predicate_only_violation() -> None:
    """裁定済み対応 (4)「構造述語のみ破る合成ケース」: cell_id をテンプレ
    ート未登録の合成値にして template チェックを素通りさせつつ、pitch 系列
    を非単調にする——構造述語がテンプレート凍結から独立に効くことを
    直接単体呼び出しで確認する。"""
    cell = {
        "cell_id": "SYNTHETIC-NOT-IN-TEMPLATE",
        "notes": [
            {
                "kana": "さ", "pitch_midi": 65, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": False,
            },
            {
                "kana": "ぎ", "pitch_midi": 60, "duration_beats": 2, "phrase_index": 0,
                "is_phrase_final": True,
            },
        ],
    }
    with pytest.raises(m.Run9ValidationError, match="non-decreasing"):
        m._validate_p2_diagnostic_pitch_rise_cell(cell, field="test")


def test_fix31_direct_call_synthetic_cell_not_in_template_skips_template_check() -> None:
    """テンプレート未登録の cell_id は、構造述語さえ満たせばテンプレート
    照合なしに通過することの確認（`_P2_DIAGNOSTIC_PITCH_RISE_NOTES_
    TEMPLATE.get(cell_id)` が None を返す経路の回帰確認）。"""
    cell = {
        "cell_id": "SYNTHETIC-NOT-IN-TEMPLATE",
        "notes": [
            {
                "kana": "た", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": False,
            },
            {
                "kana": "み", "pitch_midi": 61, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": True,
            },
        ],
    }
    m._validate_p2_diagnostic_pitch_rise_cell(cell, field="test")  # 例外を投げないことの確認


def test_negative_fix31_direct_call_length_mismatch() -> None:
    """構造述語（非減少 + 厳密増加）をどちらも満たしつつ note 数だけ
    テンプレートと異なる合成 cell で、length-mismatch 経路が独立に
    機能することを確認する（Fix 33 の厳密増加チェックより後段）。"""
    cells_notes = [
        {
            "kana": "さ", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
            "is_phrase_final": False,
        },
        {
            "kana": "ぎ", "pitch_midi": 65, "duration_beats": 2, "phrase_index": 0,
            "is_phrase_final": True,
        },
    ]
    cell = {"cell_id": "P2-PHRASE-BUILD-WEAK-TO-STRONG", "notes": cells_notes}
    with pytest.raises(m.Run9ValidationError, match="frozen template"):
        m._validate_p2_diagnostic_pitch_rise_cell(cell, field="test")


# ---------------------------------------------------------------------------
# PR #322 第5巡指摘 Fix 12（P2, 採用）: P0 の score.py 逐語照合
# ---------------------------------------------------------------------------


def test_fix12_positive_p0_matches_build_sakura_score(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)  # 例外を投げないことの確認（回帰）
    score_py_module = m._load_score_py_module()
    cell = _p0_probe(manifest_data)["cells"][0]
    m._require_p0_matches_build_sakura_score(cell, score_py_module=score_py_module, field="test")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cell: cell["notes"].__setitem__(0, {**cell["notes"][0], "pitch_midi": 65}),
        lambda cell: cell["notes"].__setitem__(0, {**cell["notes"][0], "kana": "り"}),
        lambda cell: cell["notes"].__setitem__(0, {**cell["notes"][0], "duration_beats": 99}),
        lambda cell: cell.__setitem__("tempo_bpm", 999.0),
    ],
)
def test_negative_fix12_p0_content_altered_despite_hash_and_verbatim_claim(
    manifest_data: Dict[str, Any], mutate,
) -> None:
    """hash 一致 + verbatim:true を保ったまま notes/tempo_bpm の値だけを
    改変しても、score.py との逐語比較で検出される。"""
    bad = _mutate(manifest_data)
    cell = _p0_probe(bad)["cells"][0]
    mutate(cell)
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix12_score_py_missing(tmp_path: Path) -> None:
    """score.py パスを一時 rename する monkeypatch ではなく、`path` 引数
    を存在しない tmp パスへ差し替えることで不在時の fail-closed 挙動を
    検証する（実 score.py は一切触れない）。"""
    nonexistent = tmp_path / "does_not_exist" / "score.py"
    with pytest.raises(m.Run9ValidationError, match="実在が"):
        m._load_score_py_module(path=nonexistent)


# ---------------------------------------------------------------------------
# PR #322 第6巡指摘 Fix 13（P1, 採用）: probe manifest pin の実物照合を
# `load_pinned_probe_manifest()` へ集約（gate_state() 自体は構造述語の
# まま不変。実物照合はこの消費関数が消費時点で行う）。
# ---------------------------------------------------------------------------


def test_fix13_render_contract_declares_access_contract_markers(
    manifest_data: Dict[str, Any],
) -> None:
    access_contract = manifest_data["render_contract"]["probe_manifest_access_contract"]
    for marker in m._PROBE_MANIFEST_ACCESS_CONTRACT_MARKERS:
        assert marker in access_contract


def test_negative_fix13_render_contract_missing_access_contract_marker(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    bad["render_contract"]["probe_manifest_access_contract"] = "no markers here"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_fix13_load_pinned_probe_manifest_positive(
    contract: m.Run9RunContract, manifest_data: Dict[str, Any]
) -> None:
    loaded = m.load_pinned_probe_manifest(contract)
    assert loaded == manifest_data


def test_negative_fix13_missing_manifest_file(
    contract: m.Run9RunContract, tmp_path: Path
) -> None:
    """`manifest_path` を実在しない tmp パスへ差し替え、pod checkout での
    manifest 欠落を模した fail-closed 挙動を検証する（実 manifest は
    一切触れない）。"""
    missing = tmp_path / "does_not_exist" / "probe_manifest.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        m.load_pinned_probe_manifest(contract, manifest_path=missing)


def test_negative_fix13_byte_altered_manifest_copy(
    contract: m.Run9RunContract, tmp_path: Path
) -> None:
    """実 manifest の tmp コピーへ 1 byte 改変を加え、raw sha256 が pin 値
    と一致しなくなることによる fail-closed 挙動を検証する（実 manifest
    ファイル自体は一切書き換えない——コピーのみ操作）。"""
    tmp_manifest = tmp_path / "probe_manifest.json"
    raw = bytearray(m.PROBE_MANIFEST_PATH.read_bytes())
    raw[0] = raw[0] ^ 0xFF  # 先頭 1 byte を反転して改変する
    tmp_manifest.write_bytes(bytes(raw))
    with pytest.raises(m.Run9ValidationError, match="一致しない"):
        m.load_pinned_probe_manifest(contract, manifest_path=tmp_manifest)


def _write_synthetic_contract_yaml(raw: Dict[str, Any], tmp_path: Path) -> Path:
    """PR #322 第17巡指摘（P1, 採用）以降の合成 contract 系テストが共有する
    ヘルパ: `load_pinned_probe_manifest()` が既定でディスク上の正典
    `RUN9_CONTRACT_YAML_PATH` を都度再読込するようになったため（ディスク
    正典アンカー）、合成 raw dict をそのまま渡すだけでは「渡された
    contract がディスク正典から乖離している」検証で弾かれる——本ヘルパで
    合成 raw を tmp YAML ファイルへ書き出し、`contract_path` 注入で
    テスト対象のディスク側を差し替える（既存テストの synthetic contract
    群を contract_path 注入方式へ追随させる、裁定済み対応 (b)）。"""
    tmp_yaml = tmp_path / "synthetic_run9_contract.yaml"
    tmp_yaml.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_yaml


def test_negative_fix13_pin_value_mismatch(contract_raw: Dict[str, Any], tmp_path: Path) -> None:
    """`RUN9_CONTRACT.yaml` 側の pin 値が実ファイルの sha256 と一致しない
    場合の fail-closed 挙動を検証する——実 RUN9_CONTRACT.yaml は一切触れず、
    実 contract の deepcopy を mutate した `Run9RunContract` を渡す（Fix 27
    により `load_pinned_probe_manifest()` は `contract.raw` を
    `load_run9_contract()` で再検証するため、`pin_field()` のみを実装した
    偽オブジェクトでは通らない——schema-valid な別の64hex値へ差し替える
    ことで再検証自体は通過させる）。第17巡のディスク正典アンカーにより
    `contract_path` も同じ mutated 値へ差し替えて渡す（さもないと
    ディスク正典との乖離検証が先に発火する）ことで、意図どおり実
    `probe_manifest.json` バイトとの sha256 不一致経路を検証する。
    """
    mutated = copy.deepcopy(contract_raw)
    mutated["probe_manifest_sha"]["value"] = "0" * 64
    fake_contract = m.Run9RunContract(raw=mutated)
    tmp_contract_path = _write_synthetic_contract_yaml(mutated, tmp_path)
    with pytest.raises(m.Run9ValidationError, match="一致しない"):
        m.load_pinned_probe_manifest(fake_contract, contract_path=tmp_contract_path)


def test_negative_fix13_pin_not_pinned(contract_raw: Dict[str, Any], tmp_path: Path) -> None:
    """pin の status が PINNED でない（PENDING 等）場合、実バイト照合の
    前段で fail-closed になることを検証する（Fix 27: 実 contract の
    deepcopy を mutate——`load_run9_contract()` の再検証を通過する形式で
    status のみ変更する）。第17巡のディスク正典アンカーにより
    `contract_path` も同じ mutated 値へ差し替えて渡す。"""
    mutated = copy.deepcopy(contract_raw)
    mutated["probe_manifest_sha"] = {
        "status": "PENDING", "value": None, "source": mutated["probe_manifest_sha"]["source"],
    }
    fake_contract = m.Run9RunContract(raw=mutated)
    tmp_contract_path = _write_synthetic_contract_yaml(mutated, tmp_path)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        m.load_pinned_probe_manifest(fake_contract, contract_path=tmp_contract_path)


# ---------------------------------------------------------------------------
# PR #322 第16巡指摘 Fix 27（P1, 採用）: `Run9RunContract` は frozen
# dataclass だが `raw: Dict[str, Any]` 自体はミュータブル——load 後に
# `contract.raw["probe_manifest_sha"]["value"]` を直接書き換えれば、
# `RUN9_CONTRACT.yaml` の正典 pin に被覆されないバイトを
# `load_pinned_probe_manifest()` が受理し得た（旧実装は `contract.raw` を
# 再検証せず直読みしていた）。`gate_state()`（PR #315 Fix 4 と同型）の
# 再検証パターンを本関数へも適用する。
# ---------------------------------------------------------------------------


def test_fix27_load_pinned_probe_manifest_still_works_on_untampered_contract(
    contract_raw: Dict[str, Any],
) -> None:
    """対照実験: 改変していない contract では再検証の追加が正常系を壊して
    いないことの確認。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    m.load_pinned_probe_manifest(contract)  # 例外を投げないことの確認


def test_negative_fix27_in_process_value_tampering_after_load_still_fail_closed(
    contract_raw: Dict[str, Any],
) -> None:
    """本指摘の核心シナリオ: 正常 load 後に
    `contract.raw["probe_manifest_sha"]["value"]` を直接（schema-valid な
    別の64hex へ）書き換える——`load_run9_contract()` の再検証はこの
    書き換えを構造的には受理する（形式的には有効な64hexのため）が、
    第17巡のディスク正典アンカーにより、書き換え後の値はディスク上の
    正典 `RUN9_CONTRACT.yaml` の pin 値と一致しなくなり、契約乖離
    （tampering evidence）として fail-closed になることを確認する
    （旧実装ではこの検出は下流の実バイト sha256 照合に依存していたが、
    第17巡の変更でより早い層——ディスク正典との一致検証——で捕捉される）。
    """
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    m.load_pinned_probe_manifest(contract)  # 前提: 改変前は正常に通る
    contract.raw["probe_manifest_sha"]["value"] = "1" * 64  # in-process 改変
    with pytest.raises(m.Run9ValidationError, match="diverges from the canonical on-disk"):
        m.load_pinned_probe_manifest(contract)


def test_negative_fix27_in_process_structural_tampering_after_load_rejected(
    contract_raw: Dict[str, Any],
) -> None:
    """正常 load 後に `contract.raw` の非 pin 欄（`run_id`）を直接書き換え
    構造的に無効な contract にすると、`probe_manifest_sha` 自体は無傷でも
    再検証（`load_run9_contract()`）が先に fail-closed で拒否することを
    確認する——旧実装（`contract.pin_field()` の直読み）ではこの構造破壊は
    一切検出されなかった。"""
    fresh_raw = copy.deepcopy(contract_raw)
    contract = m.load_run9_contract(fresh_raw)
    contract.raw["run_id"] = "RUN9X"  # in-process 改変（probe_manifest_sha 自体は無傷）
    with pytest.raises(m.Run9ValidationError, match="run_id"):
        m.load_pinned_probe_manifest(contract)


# ---------------------------------------------------------------------------
# PR #322 第17巡指摘 Fix 29（P1, 採用 — Fix 27 への正当な追撃）: Fix 27 の
# `load_run9_contract(contract.raw)` 再検証は「改変後の raw の自己整合性」
# しか証明せず、ディスク上の正典 `RUN9_CONTRACT.yaml` との一致は証明しない
# ——in-process で `contract.raw` を丸ごと自己無矛盾な別内容へ差し替える
# 攻撃者に対しては Fix 27 単体では無力だった。`load_pinned_probe_manifest()`
# を `contract_path`（既定 `RUN9_CONTRACT_YAML_PATH`）が指すディスク上の
# 正典へアンカーし、渡された `contract` の pin がディスク再読込 pin と
# 厳密一致しない場合は改竄証拠として fail-closed にする。
# ---------------------------------------------------------------------------


def test_negative_fix29_self_consistent_raw_diverges_from_disk_is_rejected(
    contract_raw: Dict[str, Any], tmp_path: Path,
) -> None:
    """本指摘の核心シナリオ: manifest のコピーを 1 byte 改変し、その実
    sha256 を `contract.raw["probe_manifest_sha"]["value"]` へ書き込んで
    from-scratch で自己無矛盾な（内部的には筋が通った）contract を組み立て、
    改変済みコピーを `manifest_path` で渡す——`contract_path` は指定せず
    既定のディスク上の正典 `RUN9_CONTRACT.yaml` を使わせる。Fix 27 単体
    （`load_run9_contract(contract.raw)` の自己整合性再検証のみ）であれば、
    この攻撃は「渡された contract の pin と、渡された manifest_path の実
    バイトが一致する」ため素通りしてしまう——ディスク上の正典 pin 値とは
    異なるにもかかわらず。本 Fix のディスク正典アンカーにより、渡された
    contract の pin がディスク正典の pin と乖離しているという理由だけで
    fail-closed になることを確認する。"""
    tampered_bytes = bytearray(m.PROBE_MANIFEST_PATH.read_bytes())
    tampered_bytes[0] = tampered_bytes[0] ^ 0xFF
    tmp_manifest = tmp_path / "probe_manifest.json"
    tmp_manifest.write_bytes(bytes(tampered_bytes))
    tampered_sha = hashlib.sha256(bytes(tampered_bytes)).hexdigest()

    mutated = copy.deepcopy(contract_raw)
    mutated["probe_manifest_sha"]["value"] = tampered_sha  # 改変済みコピーと自己整合
    contract = m.Run9RunContract(raw=mutated)

    with pytest.raises(m.Run9ValidationError, match="diverges from the canonical on-disk"):
        m.load_pinned_probe_manifest(contract, manifest_path=tmp_manifest)


def test_fix29_contract_path_injection_with_matching_synthetic_disk_copy_passes(
    manifest_data: Dict[str, Any],
) -> None:
    """裁定済み対応 (b): `contract_path` を実 `RUN9_CONTRACT.yaml` のバイト
    そのままの一時コピーへ差し替えても（ディスク正典の「場所」だけを変え、
    「内容」は変えない）、正常系が通ることを確認する——本注入経路自体が
    壊れていないことの回帰確認（合成 contract 系テストが依拠する土台）。"""
    contract = m.load_run9_contract_from_yaml_path(CONTRACT_PATH)
    loaded = m.load_pinned_probe_manifest(contract, contract_path=CONTRACT_PATH)
    assert loaded == manifest_data


def test_fix29_positive_real_contract_and_manifest_pass_without_overrides(
    contract: m.Run9RunContract, manifest_data: Dict[str, Any],
) -> None:
    """対照実験(c): `contract_path`/`manifest_path` を一切指定しない既定
    経路（実運用の呼び出し形）が、本 Fix 導入後も正常に通ることの回帰
    確認。"""
    loaded = m.load_pinned_probe_manifest(contract)
    assert loaded == manifest_data


def test_fix29_run9_contract_yaml_path_constant_points_to_real_file() -> None:
    assert m.RUN9_CONTRACT_YAML_PATH.name == "RUN9_CONTRACT.yaml"
    assert m.RUN9_CONTRACT_YAML_PATH == CONTRACT_PATH
    assert m.RUN9_CONTRACT_YAML_PATH.is_file()


# ---------------------------------------------------------------------------
# PR #322 第7巡指摘 Fix 15（P1, 採用）: `load_pinned_probe_manifest()` の
# read-once 化。旧実装は `compute_file_sha256(path)`（1回目の読込）と
# `path.read_text()`（2回目の読込）でファイルを2回読んでおり、可変
# ボリューム/並行差し替え環境で「hash した版」と「parse した版」が別
# バイト列になり得た（TOCTOU）。`path.read_bytes()` で1回だけ読み、同一
# バッファから digest（`hashlib.sha256`）と parse 対象（`str.decode`）の
# 両方を導出するよう変更した——hash した版と parse した版の乖離が
# 構造的に不可能になる。
# ---------------------------------------------------------------------------


class _ReadCountingPath:
    """`Path` の薄いラッパー。`load_pinned_probe_manifest()` が呼ぶ
    `is_file()` / `read_bytes()` のみを実 Path へ委譲しつつ、
    `read_bytes()` の呼び出し回数を数える（read-once 検証の spy）。"""

    def __init__(self, real_path: Path) -> None:
        self._real = real_path
        self.read_bytes_call_count = 0

    def is_file(self) -> bool:
        return self._real.is_file()

    def read_bytes(self) -> bytes:
        self.read_bytes_call_count += 1
        return self._real.read_bytes()

    def __str__(self) -> str:  # エラーメッセージの f-string 埋め込み用
        return str(self._real)


def test_fix15_load_pinned_probe_manifest_reads_file_exactly_once(
    contract: m.Run9RunContract,
) -> None:
    spy = _ReadCountingPath(m.PROBE_MANIFEST_PATH)
    data = m.load_pinned_probe_manifest(contract, manifest_path=spy)  # type: ignore[arg-type]
    assert spy.read_bytes_call_count == 1, (
        "load_pinned_probe_manifest() はファイルをちょうど1回だけ read_bytes() する契約"
        f"（read-once。実測 {spy.read_bytes_call_count} 回）"
    )
    assert data["probes"]


def test_fix15_digest_and_parse_derive_from_identical_buffer(
    contract: m.Run9RunContract, manifest_data: Dict[str, Any]
) -> None:
    """digest（sha256）と parse 対象が同一バイト列由来であることを、実
    ファイルの `read_bytes()` から独立に導出した期待値と突き合わせて
    確認する（回帰確認——read-once 化で外部挙動が変わっていないこと）。"""
    buf = m.PROBE_MANIFEST_PATH.read_bytes()
    expected_sha = hashlib.sha256(buf).hexdigest()
    field = contract.pin_field("probe_manifest_sha")
    assert field["value"] == expected_sha
    loaded = m.load_pinned_probe_manifest(contract)
    assert loaded == manifest_data


# ---------------------------------------------------------------------------
# PR #322 第6巡指摘 Fix 14（P2, 採用）: P4 heldout_independence の
# provenance 拡張（authorship / environment_evidence /
# machine_checked_separation / residual_risk_declaration の4ブロック）。
# 絶対独立は主張せず、検証可能な範囲の証跡 + 正直な残余宣言へ縮小する。
# ---------------------------------------------------------------------------


def test_fix14_positive_p4_provenance_blocks_well_formed(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)  # 例外を投げないことの確認（回帰）
    independence = _p4_probe(manifest_data)["heldout_independence"]
    assert independence["authorship"]["author"].strip()
    assert m._HELDOUT_AUTHORED_AT_RE.match(independence["authorship"]["authored_at"])
    assert independence["authorship"]["provenance_record"].strip()
    # PR #322 第8巡指摘 Fix 18: environment_evidence は machine_checked /
    # author_record の2ブロックへ区分済み。
    env = independence["environment_evidence"]
    for marker in m._HELDOUT_ENV_EVIDENCE_MACHINE_CHECKED_CLAIM_MARKERS:
        assert marker in env["machine_checked"]["claim"]
    assert env["machine_checked"]["verification_method"].strip()
    for marker in m._HELDOUT_ENV_EVIDENCE_AUTHOR_RECORD_CLAIM_MARKERS:
        assert marker in env["author_record"]["claim"]
    assert (
        m._HELDOUT_MACHINE_CHECKED_SEPARATION_MARKER
        in independence["machine_checked_separation"]["reference"]
    )
    for marker in m._HELDOUT_RESIDUAL_RISK_MARKERS:
        assert marker in independence["residual_risk_declaration"]["note"]


@pytest.mark.parametrize(
    "block",
    ["authorship", "environment_evidence", "machine_checked_separation", "residual_risk_declaration"],
)
def test_negative_fix14_provenance_block_missing(
    manifest_data: Dict[str, Any], block: str
) -> None:
    bad = _mutate(manifest_data)
    del _p4_probe(bad)["heldout_independence"][block]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix14_authorship_bad_date_format(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["authorship"]["authored_at"] = "2026/08/25"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix14_environment_evidence_missing_marker(manifest_data: Dict[str, Any]) -> None:
    """PR #322 第8巡指摘 Fix 18 で `environment_evidence` は
    machine_checked/author_record へ分割された——ここでは machine_checked
    側のマーカー欠落を検証する（author_record 側は別テストで検証）。"""
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["environment_evidence"]["machine_checked"]["claim"] = (
        "no markers here"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_author_record_missing_marker(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["environment_evidence"]["author_record"]["claim"] = (
        "no markers here"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix14_machine_checked_separation_missing_marker(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["machine_checked_separation"]["reference"] = (
        "no reference here"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix14_residual_risk_declaration_missing_marker(
    manifest_data: Dict[str, Any],
) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["residual_risk_declaration"]["note"] = (
        "no markers here"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix14_unknown_key_in_provenance_block(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["authorship"]["unexpected_extra_key"] = "x"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_fix14_repo_has_no_pjs_audio_or_label_files() -> None:
    """`environment_evidence.machine_checked.claim`（PR #322 第8巡指摘
    Fix 18 で `machine_checked`/`author_record` へ分割済み——現在の repo
    checkout に、ファイル名ベースで pjs を含む wav/lab 実体ファイルが
    一切存在しない）を機械検証する——repo 内を glob してファイル名に
    "pjs" を含む .wav / .lab 実体ファイルの不在を確認する。メタデータ
    *.json 内の "pjs" 文字列参照（pin 値の文字列参照等）は対象外——
    音源・採譜の実体ファイルのみを検査する。"""
    repo_root = _RUN_DIR
    while not (repo_root / ".git").exists():
        if repo_root.parent == repo_root:
            pytest.fail("repository root（.git を含むディレクトリ）が見つからなかった")
        repo_root = repo_root.parent

    hits = [
        p
        for p in repo_root.rglob("*")
        if ".git" not in p.parts
        and "pjs" in p.name.lower()
        and p.suffix.lower() in (".wav", ".lab")
    ]
    assert hits == [], (
        "repo 内に PJS 音源/採譜の実体ファイルが見つかった（environment_evidence.claim と矛盾）: "
        f"{hits}"
    )


# ---------------------------------------------------------------------------
# PR #322 第8巡指摘 Fix 16（P1, 採用）: `_load_score_py_module()` の
# read-once 化（Fix 15 と同型）。hash 照合対象と実行対象は同一バイト列
# から導出し、`_validate_probe_cell_source()`（P0 source hash 照合）は
# score_py_module 経由でその digest を再利用し、独自に別読みしない。
# ---------------------------------------------------------------------------


class _ReadBytesCountingPath:
    """`_load_score_py_module()` が呼ぶ `is_file()` / `read_bytes()` のみを
    実 Path へ委譲しつつ、`read_bytes()` の呼び出し回数を数える
    （read-once 検証の spy。Fix 15 の `_ReadCountingPath` と同型）。"""

    def __init__(self, real_path: Path) -> None:
        self._real = real_path
        self.read_bytes_call_count = 0

    def is_file(self) -> bool:
        return self._real.is_file()

    def read_bytes(self) -> bytes:
        self.read_bytes_call_count += 1
        return self._real.read_bytes()

    @property
    def parent(self) -> Path:
        return self._real.parent

    def __str__(self) -> str:  # エラーメッセージの f-string 埋め込み用
        return str(self._real)


def test_fix16_load_score_py_module_reads_file_exactly_once() -> None:
    spy = _ReadBytesCountingPath(m.SCORE_PY_REFERENCE_PATH)
    module = m._load_score_py_module(path=spy)  # type: ignore[arg-type]
    assert spy.read_bytes_call_count == 1, (
        "_load_score_py_module() はファイルをちょうど1回だけ read_bytes() する契約（read-once。"
        f"実測 {spy.read_bytes_call_count} 回）"
    )
    assert hasattr(module, "build_sakura_score")


def test_fix16_digest_and_execution_derive_from_identical_buffer() -> None:
    """digest（module.__source_sha256__）と実行対象が同一バイト列由来で
    あることを、実ファイルから独立に導出した期待 digest と突き合わせて
    確認する（回帰確認——read-once 化で外部挙動が変わっていないこと）。"""
    module = m._load_score_py_module()
    expected_sha = hashlib.sha256(m.SCORE_PY_REFERENCE_PATH.read_bytes()).hexdigest()
    assert module.__source_sha256__ == expected_sha
    assert module.__source_sha256__ == m.compute_file_sha256(m.SCORE_PY_REFERENCE_PATH)
    # 実行対象（build_sakura_score）が正しく動作することも確認する。
    notes = module.build_sakura_score()
    assert notes


def test_fix16_validate_probe_cell_source_reuses_module_digest_no_reread() -> None:
    """full-chain 経路の配線を単体で確認する: `score_py_module` を渡した
    場合、`_validate_probe_cell_source()` は独自に score.py を読まない
    （score_path は使われない——存在しない score_path を同時に渡しても
    無視されて通過することで、別読みしていないことを示す）。"""
    module = m._load_score_py_module()
    actual_sha = m.compute_file_sha256(m.SCORE_PY_REFERENCE_PATH)
    source = _valid_p0_source(actual_sha)
    nonexistent = Path("/tmp/pr322_r8_fix16_does_not_exist/score.py")
    assert not nonexistent.exists()
    # score_path はダミー（存在しない）だが score_py_module を渡すため
    # 参照されない——別読みしない配線の確認。
    m._validate_probe_cell_source(
        source, field="test", score_path=nonexistent, score_py_module=module
    )


def test_negative_fix16_validate_probe_cell_source_mismatched_sha_via_module(
) -> None:
    module = m._load_score_py_module()
    bad_source = _valid_p0_source("0" * 64)
    with pytest.raises(m.Run9ValidationError, match="does not match"):
        m._validate_probe_cell_source(bad_source, field="test", score_py_module=module)


def test_fix16_standalone_fallback_still_works_without_module(
) -> None:
    """回帰確認: `score_py_module` を渡さない既存テスト・スタンドアロン
    呼び出しは従来どおり `score_path` 経由で自己完結的にファイルを読む
    （後方互換フォールバック、Fix 4/12 のテストは無変更で green）。"""
    actual_sha = m.compute_file_sha256(m.SCORE_PY_REFERENCE_PATH)
    m._validate_probe_cell_source(_valid_p0_source(actual_sha), field="test")


def test_fix16_phoneme_jp_loader_out_of_scope_has_no_hash_check() -> None:
    """`_load_phoneme_jp_module()` は Fix 16 の対象外であることの根拠
    確認: phoneme_jp.py には hash 照合が一切存在しない（mora 文法検証の
    みを行う）ため、read-once 化すべき「hash した版と実行した版の乖離」
    という契約自体が存在しない。"""
    module = m._load_phoneme_jp_module()
    assert not hasattr(module, "__source_sha256__")


# ---------------------------------------------------------------------------
# PR #322 第8巡指摘 Fix 17（P2, 採用）: P4 held-out 分離を射影別に独立して
# 比較する（結合 (kana, pitch, duration) タプル比較は kana だけ変えて
# 旋律・リズムを丸コピーした P4 を通してしまうため）。pitch_midi/kana は
# 完全一致・連続部分列包含を厳密拒否、duration_beats は低エントロピー
# 対策として最小長4以上の完全一致/連続部分列に限って拒否する。
# ---------------------------------------------------------------------------


def _set_p4_notes(data: Dict[str, Any], notes: List[Dict[str, Any]]) -> None:
    _p4_probe(data)["cells"][0]["notes"] = notes


def _p0_note_slice(data: Dict[str, Any], start: int, stop: int) -> List[Dict[str, Any]]:
    return copy.deepcopy(_p0_probe(data)["cells"][0]["notes"][start:stop])


def test_fix17_positive_real_manifest_passes_projection_checks(
    manifest_data: Dict[str, Any],
) -> None:
    """回帰確認: 実 manifest（P4 は元々分離済み）は Fix 17 の射影別検査
    でも引き続き通過する（cell の書き換えは不要だった）。"""
    m.validate_probe_manifest(manifest_data)


def test_negative_fix17_kana_replaced_but_pitch_and_duration_copied(
    manifest_data: Dict[str, Any],
) -> None:
    """kana だけ差し替えて pitch/duration を P0 の連続部分列から丸コピー
    した P4 は、結合タプル検査（Fix 10）をすり抜けても pitch_midi 射影の
    独立検査（Fix 17）で捕捉されることを確認する——本指摘の核心シナリオ。"""
    bad = _mutate(manifest_data)
    src = _p0_note_slice(bad, 13, 18)  # み,わ,た,す,か (5 notes; P4 と同じ長さ)
    for n in src:
        n["kana"] = "ぬ"  # kana だけ差し替え、pitch/duration はそのまま
    _set_p4_notes(bad, src)
    with pytest.raises(m.Run9ValidationError, match="pitch_midi"):
        m.validate_probe_manifest(bad)


def test_negative_fix17_pitch_projection_only_copy(manifest_data: Dict[str, Any]) -> None:
    """pitch 系列のみを既存 cell からコピーし、kana/duration は変えた
    場合でも pitch_midi 射影の独立検査で拒否されることを確認する。"""
    bad = _mutate(manifest_data)
    src_cell = _cell_by_id(_p1_probe(bad), "P1-TRANS-LOW-TO-HIGH")  # pitch [57, 65]
    notes = []
    for n in src_cell["notes"]:
        notes.append({
            "kana": "ぬ", "pitch_midi": n["pitch_midi"], "duration_beats": 9,
            "phrase_index": 0, "is_phrase_final": n is src_cell["notes"][-1],
        })
    _set_p4_notes(bad, notes)
    with pytest.raises(m.Run9ValidationError, match="pitch_midi"):
        m.validate_probe_manifest(bad)


def test_negative_fix17_duration_long_run_copy(manifest_data: Dict[str, Any]) -> None:
    """duration 系列の長い連続部分列（長さ5、閾値4以上）を P0 からコピー
    すると duration_beats 射影の独立検査で拒否されることを確認する
    （kana/pitch は変える）。"""
    bad = _mutate(manifest_data)
    src = _p0_note_slice(bad, 0, 5)  # duration pattern: 1,1,2,1,1
    for i, n in enumerate(src):
        n["kana"] = "ぬ"
        n["pitch_midi"] = 40  # P0/P4 の中央音域制約を外れない値は後段で
        n["is_phrase_final"] = i == len(src) - 1
    _set_p4_notes(bad, src)
    with pytest.raises(m.Run9ValidationError, match="duration_beats"):
        m.validate_probe_manifest(bad)


def test_positive_fix17_duration_short_match_not_flagged(manifest_data: Dict[str, Any]) -> None:
    """duration の短い一致（長さ3以下）は低エントロピーの誤検知として
    フラグされないことを確認する——P4 の一部 note の duration だけを
    他 cell と揃えても、長さが閾値未満なら通過する。"""
    bad = _mutate(manifest_data)
    p4_cell = _p4_probe(bad)["cells"][0]
    # 先頭2 note の duration を P1 transition cell（duration [1, 1]）と
    # 揃える——長さ2 < 閾値4 のため duration 射影検査はフラグしない
    # （kana/pitch は元のままなので他射影・結合タプル検査にも抵触しない）。
    p4_cell["notes"][0]["duration_beats"] = 1
    p4_cell["notes"][1]["duration_beats"] = 1
    m.validate_probe_manifest(bad)  # 例外を投げないことの確認


def test_fix17_helper_duration_threshold_constant() -> None:
    assert m._HELDOUT_DURATION_MIN_LEAK_LENGTH == 4


def test_fix17_manifest_reference_text_matches_actual_check_content(
    manifest_data: Dict[str, Any],
) -> None:
    """`machine_checked_separation.reference` の宣言文言が、実際の検査
    内容（射影別・duration閾値付き）と一致することを確認する（指摘:
    「manifest の分離宣言文言を実際の検査内容と一致するよう更新」）。"""
    reference = _p4_probe(manifest_data)["heldout_independence"][
        "machine_checked_separation"
    ]["reference"]
    for marker in ("pitch_midi", "kana", "duration_beats", "4"):
        assert marker in reference


# ---------------------------------------------------------------------------
# PR #322 第8巡指摘 Fix 18（P2, 採用）: `AUTHORED_INDEPENDENTLY_OF_PJS_
# CORPUS` は現 repo checkout の pjs 名 wav/lab 検査のみなのに、歴史的
# 作業環境 + 全形態（MIDI/MusicXML/歌詞テキスト・別名）にまで及ぶ主張に
# 読めた——主張を収集済み証拠へ縮小する。
# ---------------------------------------------------------------------------


def test_fix18_status_literal_renamed() -> None:
    assert m.HELDOUT_INDEPENDENCE_STATUS == "AUTHORED_WITHOUT_PJS_MATERIAL_IN_AUTHORING_ENVIRONMENT"


def test_negative_fix18_old_status_literal_rejected(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["status"] = "AUTHORED_INDEPENDENTLY_OF_PJS_CORPUS"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_old_flat_environment_evidence_shape_rejected(
    manifest_data: Dict[str, Any],
) -> None:
    """Fix 14 時点の旧形状（`claim`/`verification_method` を
    environment_evidence 直下に持つ）はもはや unknown key として拒否
    される——Fix 18 の再構造化が実際に強制されていることの確認。"""
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["environment_evidence"] = {
        "claim": "old shape", "verification_method": "old shape",
    }
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_unknown_key_in_machine_checked(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["environment_evidence"]["machine_checked"][
        "unexpected_extra_key"
    ] = "x"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_unknown_key_in_author_record(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p4_probe(bad)["heldout_independence"]["environment_evidence"]["author_record"][
        "unexpected_extra_key"
    ] = "x"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_machine_checked_missing_key(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    del _p4_probe(bad)["heldout_independence"]["environment_evidence"]["machine_checked"][
        "verification_method"
    ]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix18_author_record_missing_key(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    del _p4_probe(bad)["heldout_independence"]["environment_evidence"]["author_record"]["claim"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "marker", ["別 workspace", "別名ファイル", "MIDI", "MusicXML", "テキスト形態", "事前知識"]
)
def test_negative_fix18_residual_risk_scope_marker_missing(
    manifest_data: Dict[str, Any], marker: str,
) -> None:
    """PR #322 第8巡指摘 Fix 18 が residual_risk_declaration へ追加した
    検査対象外範囲マーカー（別 workspace・別名ファイル・MIDI/MusicXML・
    テキスト形態・モデル事前知識）が、それぞれ個別に必須であることを
    確認する。"""
    bad = _mutate(manifest_data)
    note = _p4_probe(bad)["heldout_independence"]["residual_risk_declaration"]["note"]
    _p4_probe(bad)["heldout_independence"]["residual_risk_declaration"]["note"] = note.replace(
        marker, ""
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第9巡指摘 Fix 19（P2, 採用）: factor_levels.axes の宣言値を cell
# から独立した凍結表 `_PROBE_EXPECTED_FACTOR_VALUES` と照合する。cell との
# 内部自己整合性（Fix 3）だけでは、axes 値と対応 cell の note フィールドを
# 協調して書き換える amendment（例: register.low を 57->58 に変え、
# low-register の cell も MIDI 58 へ揃える）を検出できない——本 Fix は
# それを検出する外部アンカーを追加する。
# ---------------------------------------------------------------------------


def _p3_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p3,) = [p for p in data["probes"] if p["probe_id"] == "P3"]
    return p3


def test_fix19_positive_real_manifest_passes_frozen_value_check(
    manifest_data: Dict[str, Any],
) -> None:
    """回帰確認: 実 manifest（P1/P2/P3 の axes・filler タプルは元々凍結表
    と一致していた）は Fix 19 の外部アンカー検査でも引き続き通過する
    （factor_levels の書き換えは不要だった）。"""
    m.validate_probe_manifest(manifest_data)


def test_negative_fix19_coordinated_register_edit_core_scenario(
    manifest_data: Dict[str, Any],
) -> None:
    """本指摘の核心シナリオ: P1 register.low を 57->58 に変え、
    low-register の両 cell（P1-REG-LOW-DUR-SHORT/LONG）の phrase-final
    pitch も同時に 58 へ揃える——manifest 自己整合性（Fix 3）は満たすが、
    凍結表（Fix 19）には違反する。"""
    bad = _mutate(manifest_data)
    p1 = _p1_probe(bad)
    p1["factor_levels"]["axes"]["register"]["low"] = 58
    for cell_id in ("P1-REG-LOW-DUR-SHORT", "P1-REG-LOW-DUR-LONG"):
        cell = _cell_by_id(p1, cell_id)
        for note in cell["notes"]:
            if note.get("is_phrase_final"):
                note["pitch_midi"] = 58
    with pytest.raises(m.Run9ValidationError, match="frozen expected value"):
        m.validate_probe_manifest(bad)


def test_negative_fix19_p2_filler_tuple_coordinated_edit(manifest_data: Dict[str, Any]) -> None:
    """P2 filler タプル（medial_filler_pitch_midi）を変更し、全 onset cell
    の前置 note も追随させる——Fix 7（filler 一貫性）は満たすが Fix 19
    （外部凍結値）には違反する。"""
    bad = _mutate(manifest_data)
    p2 = _p2_probe(bad)
    p2["factor_levels"]["medial_filler_pitch_midi"] = 61
    for cell in p2["cells"]:
        if "onset_consonant_class" in cell.get("levels", {}):
            cell["notes"][0]["pitch_midi"] = 61
    with pytest.raises(m.Run9ValidationError, match="frozen expected value"):
        m.validate_probe_manifest(bad)


def test_negative_fix19_p3_duration_level_coordinated_edit(manifest_data: Dict[str, Any]) -> None:
    """P3 release_duration.long（PR #322 第12巡指摘 Fix 22 で意味論を
    `final_phone_dur_override.terminal_extension_ms` へ再定義済み——
    第11巡 Fix 21 の final_note_duration 改名は撤回済み）の水準値を変更
    し、対応 cell の override も追随させる——cell との内部自己整合性
    （Fix 3/22）は満たすが、Fix 19 の外部凍結表には違反する。"""
    bad = _mutate(manifest_data)
    p3 = _p3_probe(bad)
    p3["factor_levels"]["axes"]["release_duration"]["long"] = 40.0
    for cell in p3["cells"]:
        if cell.get("levels", {}).get("release_duration") == "long":
            cell["final_phone_dur_override"]["terminal_extension_ms"] = 40.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected value"):
        m.validate_probe_manifest(bad)


def test_negative_fix19_p1_duration_axis_value_changed_alone(
    manifest_data: Dict[str, Any],
) -> None:
    """cell 側を変えずに axes 側だけを変更した単体シナリオでは、実際には
    Fix 3（cell との内部自己整合性）がパイプライン上先に検出する
    （full-chain の `validate_probe_manifest()` はこちらのメッセージで
    fail する）——Fix 19 が付加検査するのは Fix 3 が通ってしまう
    *協調*編集（本ファイル冒頭の core scenario テスト）である。ここでは
    `_validate_probe_expected_factor_values()` を直接呼び、Fix 19 の
    凍結値照合そのものが（cell 非依存で）単体でも機能することを確認する。
    """
    bad = _mutate(manifest_data)
    factor_levels = _p1_probe(bad)["factor_levels"]
    factor_levels["axes"]["duration"]["long"] = 5
    with pytest.raises(m.Run9ValidationError, match="frozen expected value"):
        m._validate_probe_expected_factor_values(
            expected_probe_id="P1", factor_levels=factor_levels, field="test"
        )
    # full-chain でも（Fix 3 経由であれ）fail-closed であることの回帰確認。
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix19_p2_onset_class_description_text_changed(
    manifest_data: Dict[str, Any],
) -> None:
    """P2 onset_consonant_class の記述文言（例: 'liquid_r' の 'ら/り'）を
    書き換えると、cell 側は変えなくても凍結表照合で拒否される。"""
    bad = _mutate(manifest_data)
    _p2_probe(bad)["factor_levels"]["axes"]["onset_consonant_class"]["liquid_r"] = "ろ"
    with pytest.raises(m.Run9ValidationError, match="frozen expected value"):
        m.validate_probe_manifest(bad)


def test_negative_fix19_unknown_axis_rejected(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p1_probe(bad)["factor_levels"]["axes"]["not_a_frozen_axis"] = {"x": 1}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_fix19_helper_frozen_value_equal_rejects_bool_int_confusion() -> None:
    """`True == 1` の Python の罠を明示的に排除していることを確認する。"""
    assert m._frozen_factor_value_equal(1, 1) is True
    assert m._frozen_factor_value_equal(True, 1) is False
    assert m._frozen_factor_value_equal(1, True) is False
    assert m._frozen_factor_value_equal("57->65", "57->65") is True


# ---------------------------------------------------------------------------
# PR #322 第10巡指摘 Fix 20（P2, 採用）: `tempo_bpm` は正値検査のみだった
# ——amendment で cell 別に tempo を変えても水準ラベル検証を保ったまま
# 通過していた（gate_synth::run_pipeline は各 cell の tempo で beats->ms
# 換算するため、tempo を変えるだけで duration 比較が黙って交絡する）。
# Fix 19 と同方式（cell 非依存の外部凍結表）で、全 probe（P0-P5）の全 cell
# の tempo_bpm を固定する。
# ---------------------------------------------------------------------------


def _p5_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    (p5,) = [p for p in data["probes"] if p["probe_id"] == "P5"]
    return p5


def test_fix20_positive_real_manifest_passes_frozen_tempo_check(
    manifest_data: Dict[str, Any],
) -> None:
    """回帰確認: 実 manifest（全 cell の tempo は元々凍結表と一致していた）
    は Fix 20 の外部アンカー検査でも引き続き通過する。"""
    m.validate_probe_manifest(manifest_data)


def test_fix20_frozen_table_covers_all_six_probes() -> None:
    assert set(m._PROBE_EXPECTED_TEMPO_BPM.keys()) == set(m.PROBE_IDS)


def test_fix20_p0_frozen_tempo_matches_score_py(manifest_data: Dict[str, Any]) -> None:
    """Fix 20 の静的凍結値は Fix 12 の動的照合（score.py TEMPO_BPM）と
    整合していることを確認する——二重防御が矛盾しない。"""
    score_py_module = m._load_score_py_module()
    assert m._PROBE_EXPECTED_TEMPO_BPM["P0"] == score_py_module.TEMPO_BPM


def test_negative_fix20_p1_short_cell_tempo_changed_core_scenario(
    manifest_data: Dict[str, Any],
) -> None:
    """本指摘の核心シナリオ: P1 の short cell だけ tempo を 18 BPM に
    変える——duration の水準ラベル（levels.duration='short'）や note の
    duration_beats=1 自体は無改変のまま、実時間換算だけが交絡する。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")
    cell["tempo_bpm"] = 18.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected tempo"):
        m.validate_probe_manifest(bad)


def test_negative_fix20_p0_tempo_changed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p0_probe(bad)["cells"][0]["tempo_bpm"] = 100.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected tempo"):
        m.validate_probe_manifest(bad)


def test_negative_fix20_p3_cell_tempo_diverges_from_probe_frozen_value(
    manifest_data: Dict[str, Any],
) -> None:
    """P3 の1 cell だけ tempo を変える——他 cell との相互不一致というより、
    probe 単位で凍結された単一の期待値からの逸脱として検出される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-SHORT-VOICED")
    cell["tempo_bpm"] = 90.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected tempo"):
        m.validate_probe_manifest(bad)


def test_negative_fix20_p4_tempo_changed(manifest_data: Dict[str, Any]) -> None:
    """P4（held-out、他 probe と異なる 80.0 BPM）も凍結対象であることを
    確認する。"""
    bad = _mutate(manifest_data)
    _p4_probe(bad)["cells"][0]["tempo_bpm"] = 72.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected tempo"):
        m.validate_probe_manifest(bad)


def test_negative_fix20_p5_tempo_changed(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    _p5_probe(bad)["cells"][0]["tempo_bpm"] = 60.0
    with pytest.raises(m.Run9ValidationError, match="frozen expected tempo"):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第11巡指摘 Fix 21（P1, 採用）は第12巡 Fix 22 で**訂正・撤回**
# した——「宣言 harness に phrase-end release の制御入力が存在しない」
# という前提が誤りだった（`final_phone_dur_override` kwarg が実在）。
# Fix 21 固有の回帰・負例（軸名 `final_note_duration` への改名・
# `_P3_RELEASE_BOUNDARY_MARKERS`）はもはや現実装と一致しないため、Fix 22
# 節へ置き換えた。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PR #322 第12巡指摘 Fix 22（P1, 採用——上限超過後だが「凍結した境界宣言
# が虚偽である可能性」= 致命的クラスの新規具体経路のため例外採用）:
# Fix 21 の訂正。`release_duration` 軸を復活させ、意味を
# `final_phone_dur_override.terminal_extension_ms`（ms）へ再定義した
# （short=0.0=override なし / long=80.0=run 8 B-1 rr_long_tail_080 実
# 使用値）。cell レベルへ `final_phone_dur_override` pin 欄を新設し
# （P3 のみ許容・P3 は必須）、P3 の 4 cell の phrase-final note の
# duration_beats を全 cell 等値へ揃えた。
# ---------------------------------------------------------------------------


def test_fix22_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    """回帰確認: 実 manifest（release_duration 軸復活・override pin 追加
    済み）は引き続き通過する。"""
    m.validate_probe_manifest(manifest_data)


def test_fix22_axis_restored_and_redefined_in_all_frozen_tables() -> None:
    assert m._PROBE_FACTORIAL_AXES["P3"] == ("release_duration", "ending_voicing")
    assert m._PROBE_EXPECTED_FACTOR_VALUES["P3"]["axes"]["release_duration"] == {
        "short": 0.0, "long": 80.0,
    }
    assert "final_note_duration" not in m._PROBE_EXPECTED_FACTOR_VALUES["P3"]["axes"]
    # release_duration は note フィールド照合（_AXIS_NUMERIC_FIELD_CHECKS）
    # ではなく専用 checker（_check_axis_release_override）へディスパッチ
    # される。
    assert "release_duration" not in m._AXIS_NUMERIC_FIELD_CHECKS
    assert "final_note_duration" not in m._AXIS_NUMERIC_FIELD_CHECKS


def test_fix22_p3_role_declares_control_markers(manifest_data: Dict[str, Any]) -> None:
    role = _p3_probe(manifest_data)["role"]
    for marker in m._P3_RELEASE_CONTROL_MARKERS:
        assert marker in role


def test_fix22_p3_cells_have_uniform_phrase_final_duration(manifest_data: Dict[str, Any]) -> None:
    """release_duration との交絡源だった終端 note 長の変動を除去し、
    全 P3 cell の phrase-final duration_beats が等値であることを確認
    する。"""
    p3 = _p3_probe(manifest_data)
    durations = set()
    for cell in p3["cells"]:
        for note in cell["notes"]:
            if note.get("is_phrase_final"):
                durations.add(note["duration_beats"])
    assert len(durations) == 1, f"P3 cells must share one phrase-final duration, got {durations}"


def test_negative_fix22_override_key_missing_on_p3_cell(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    del _cell_by_id(_p3_probe(bad), "P3-RELEASE-SHORT-VOICED")["final_phone_dur_override"]
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix22_override_key_leaks_into_non_p3_probe(manifest_data: Dict[str, Any]) -> None:
    """`final_phone_dur_override` は P3 のみ許容——他 probe の cell へ
    付与すると未知キーとして拒否される。"""
    bad = _mutate(manifest_data)
    _cell_by_id(_p1_probe(bad), "P1-REG-LOW-DUR-SHORT")["final_phone_dur_override"] = None
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix22_short_cell_with_non_null_override_rejected(
    manifest_data: Dict[str, Any],
) -> None:
    """short 水準（override なし = null）の cell に非 null override を
    与えると、宣言水準（0.0）と実 override の不一致として拒否される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-SHORT-VOICED")
    cell["final_phone_dur_override"] = {"kind": "tail_extension_ms", "terminal_extension_ms": 80.0}
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix22_long_cell_override_diverges_from_frozen_value(
    manifest_data: Dict[str, Any],
) -> None:
    """long 水準の override 値が凍結値（80.0）から逸脱すると拒否される
    （本指摘の核心の一つ: 凍結値と実 override の乖離検出）。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    cell["final_phone_dur_override"]["terminal_extension_ms"] = 40.0
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix22_override_unknown_kind_rejected(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    cell["final_phone_dur_override"]["kind"] = "not_a_supported_kind"
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


def test_negative_fix22_old_retracted_fix21_role_text_rejected(
    manifest_data: Dict[str, Any],
) -> None:
    """『旧虚偽宣言文言の残存拒否』: 第11巡 Fix 21 の（今は虚偽と判明した）
    境界宣言のみを持つ role へ差し替えると、Fix 22 の訂正マーカーが
    欠落しているため fail-closed で拒否される——訂正なしに古い宣言が
    残っているケースを検出する。"""
    bad = _mutate(manifest_data)
    p3 = _p3_probe(bad)
    p3["role"] = (
        "短いrelease・長いrelease・voiced/unvoiced endingを通じてPhrase-End応答をprobeする"
        "（v0.1 §15 P3）。TRF（Technique Response Function）が未校正の間、本probeの評価は "
        "diagnostic_when_trf_uncalibrated（診断/advisory）として扱う。"
        "\n\n境界宣言（旧 Fix 21, 撤回済みの記述をそのまま残置——本テスト専用）: 宣言harness"
        "（gate_synth.py::run_pipelineの_NoteWithMs/build_inputs()）はphrase-end releaseの制御"
        "入力を持たない——_NoteWithMsはis_phrase_finalを消費せず、末尾releaseは全cell固定の"
        "TAIL_FRAMES（定数、cell非依存）である。v0.1 §15 P3のshort/long releaseは本backboneでは"
        "操作変数として表現不能である。再入条件はrelease制御入力を消費するbackboneへの交換で"
        "ある。"
    )
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "marker", ["_NoteWithMs", "final_phone_dur_override", "run_pipeline", "TAIL_FRAMES", "義務を負う"]
)
def test_negative_fix22_control_marker_missing(
    manifest_data: Dict[str, Any], marker: str,
) -> None:
    bad = _mutate(manifest_data)
    p3 = _p3_probe(bad)
    p3["role"] = p3["role"].replace(marker, "")
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #322 第15巡指摘 Fix 26（P2, 採用、Fix 25 と同族の新規具体経路）:
# release checker（`_check_axis_release_override()`）は release ラベルと
# `final_phone_dur_override` の対応しか見ておらず、short/long cell の
# notes 配列（pitch/duration/filler/同 ending_voicing クラス内の別 kana）
# を互いに変えても通過してしまっていた——release は cell の
# `final_phone_dur_override` pin のみが駆動する設計のため、pair 間の相違
# はこの override 以外に存在してはならない。
# ---------------------------------------------------------------------------


def test_fix26_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)


def test_fix26_short_long_pairs_share_identical_notes(manifest_data: Dict[str, Any]) -> None:
    p3 = _p3_probe(manifest_data)
    pairs = {
        "voiced": ("P3-RELEASE-SHORT-VOICED", "P3-RELEASE-LONG-VOICED"),
        "unvoiced": ("P3-RELEASE-SHORT-UNVOICED", "P3-RELEASE-LONG-UNVOICED"),
    }
    for short_id, long_id in pairs.values():
        short_notes = _cell_by_id(p3, short_id)["notes"]
        long_notes = _cell_by_id(p3, long_id)["notes"]
        assert short_notes == long_notes, (short_id, long_id)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda notes: notes[-1].__setitem__("pitch_midi", 65),
        lambda notes: notes[0].__setitem__("duration_beats", 2),
    ],
)
def test_negative_fix26_long_cell_pitch_or_duration_diverges(
    manifest_data: Dict[str, Any], mutate,
) -> None:
    """本指摘の核心シナリオ: long cell の note の pitch/duration を short
    cell から変えても、release checker（override とラベルの対応のみ）は
    検出できない——Fix 26 の pair context 一致検証でのみ検出される。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    mutate(cell["notes"])
    with pytest.raises(m.Run9ValidationError, match="diverges"):
        m.validate_probe_manifest(bad)


def test_negative_fix26_long_cell_kana_swapped_within_same_voicing_class(
    manifest_data: Dict[str, Any],
) -> None:
    """long cell の phrase-final kana を同じ ending_voicing クラス内の
    別 kana（'ら'->'り'、共に liquid_r/voiced）へ差し替える——
    `_check_axis_kana_class()`（ending_voicing クラス照合）は通過するが、
    short cell との notes 完全一致（Fix 26）には違反する。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    assert cell["notes"][-1]["kana"] == "ら"
    cell["notes"][-1]["kana"] = "り"
    with pytest.raises(m.Run9ValidationError, match="diverges"):
        m.validate_probe_manifest(bad)


def test_negative_fix26_long_cell_filler_note_kana_changed(manifest_data: Dict[str, Any]) -> None:
    """long cell の前置（filler）note の kana を short cell から変えても
    通過しない——release checker は override のみを見るため検出できない。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    assert cell["notes"][0]["kana"] == "か"
    cell["notes"][0]["kana"] = "の"
    with pytest.raises(m.Run9ValidationError, match="diverges"):
        m.validate_probe_manifest(bad)


def test_negative_fix26_direct_call_length_mismatch() -> None:
    """`_validate_p3_release_pair_context()` への直接単体呼び出しで、
    notes 配列の長さ不一致を検出することを確認する（既存テスト流儀と
    同型）。"""
    factor_levels: Dict[str, Any] = {}  # 本関数は factor_levels を未使用
    short_cell = {
        "cell_id": "S",
        "notes": [
            {
                "kana": "か", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": False,
            },
            {
                "kana": "ら", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": True,
            },
        ],
        "levels": {"release_duration": "short", "ending_voicing": "voiced"},
    }
    long_cell = {
        "cell_id": "L",
        "notes": short_cell["notes"] + [
            {
                "kana": "り", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": False,
            }
        ],
        "levels": {"release_duration": "long", "ending_voicing": "voiced"},
    }
    with pytest.raises(m.Run9ValidationError, match="different length"):
        m._validate_p3_release_pair_context(
            factor_levels=factor_levels, cells=[short_cell, long_cell], field="test"
        )


def test_negative_fix26_direct_call_missing_pair_partner_is_skipped() -> None:
    """pair の片方（long）が欠落しているケースは本関数の対象外——
    `_validate_probe_factorial_coverage()`（Fix 9）が別途検出するため、
    本関数は例外を投げずに素通りすることを確認する。"""
    factor_levels: Dict[str, Any] = {}  # 本関数は factor_levels を未使用
    short_cell = {
        "cell_id": "S",
        "notes": [
            {
                "kana": "ら", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
                "is_phrase_final": True,
            },
        ],
        "levels": {"release_duration": "short", "ending_voicing": "voiced"},
    }
    m._validate_p3_release_pair_context(
        factor_levels=factor_levels, cells=[short_cell], field="test"
    )  # 例外を投げないことの確認


# ---------------------------------------------------------------------------
# PR #322 第13巡指摘 Fix 23（P2, 採用——第12巡 P3 再設計への新規具体経路）:
# `_select_phrase_final_note()`（Fix 3 の数値/kana/transition/release
# override 照合、Fix 7 の P2 filler 一貫性検証が共有する唯一の selector）
# は、is_phrase_final=true の note が cell の notes 配列内のどこにあって
# も受理していた——gate_synth は is_phrase_final を消費せず、release
# override は Python list の実際の最終要素へ効くため、マーカー後に note
# を追記すると検証と実効果の帰属先がずれる。マーカー note が notes 配列
# の最終要素であることを fail-closed 強制する。
# ---------------------------------------------------------------------------


def test_fix23_positive_real_manifest_passes(manifest_data: Dict[str, Any]) -> None:
    m.validate_probe_manifest(manifest_data)


def test_negative_fix23_note_appended_after_marker_p3_long_cell(
    manifest_data: Dict[str, Any],
) -> None:
    """本指摘の核心シナリオ: P3 の long cell の phrase-final マーカー
    note の後ろへ valid な note を追記する——release override の実効果は
    追記 note（実際の最終要素）へ作用するが、旧実装は依然としてマーカー
    note を意味照合対象として返してしまっていた。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p3_probe(bad), "P3-RELEASE-LONG-VOICED")
    appended = copy.deepcopy(cell["notes"][-1])
    appended["is_phrase_final"] = False
    cell["notes"].append(appended)
    with pytest.raises(m.Run9ValidationError, match="must be the last element of notes"):
        m.validate_probe_manifest(bad)


def test_negative_fix23_marker_at_middle_position_p2_onset_cell(
    manifest_data: Dict[str, Any],
) -> None:
    """P2 onset cell（filler note + phrase-final target note の2 note
    構造）でマーカーを先頭 note へ動かす——マーカーは依然としてちょうど
    1つだが、notes 配列の最終要素ではなくなる。"""
    bad = _mutate(manifest_data)
    cell = _cell_by_id(_p2_probe(bad), "P2-ONSET-FRICATIVE-S")
    cell["notes"][0]["is_phrase_final"] = True
    cell["notes"][-1]["is_phrase_final"] = False
    with pytest.raises(m.Run9ValidationError, match="must be the last element of notes"):
        m.validate_probe_manifest(bad)


def test_fix23_helper_select_phrase_final_note_accepts_marker_as_last_note() -> None:
    cell = {
        "cell_id": "TEST",
        "notes": [
            {"kana": "か", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
             "is_phrase_final": False},
            {"kana": "ら", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
             "is_phrase_final": True},
        ],
    }
    final = m._select_phrase_final_note(cell, field="test")
    assert final is cell["notes"][-1]


def test_negative_fix23_helper_select_phrase_final_note_rejects_marker_not_last() -> None:
    cell = {
        "cell_id": "TEST",
        "notes": [
            {"kana": "か", "pitch_midi": 60, "duration_beats": 1, "phrase_index": 0,
             "is_phrase_final": True},
            {"kana": "ら", "pitch_midi": 62, "duration_beats": 1, "phrase_index": 0,
             "is_phrase_final": False},
        ],
    }
    with pytest.raises(m.Run9ValidationError, match="must be the last element of notes"):
        m._select_phrase_final_note(cell, field="test")


# ---------------------------------------------------------------------------
# PR #322 第13巡指摘 Fix 24（P2, 採用——第12巡 P3 再設計への新規具体経路）:
# 第12巡の P3 実行レシピが `gate_synth.frames_from_ms(terminal_extension_
# ms)` という1引数呼び出しを記載していたが、実 helper のシグネチャは
# `frames_from_ms(ms, frame_ms)`——レシピどおりの pod harness は全
# long-release cell で TypeError になる。前例 run8/s7_calib_score.py の
# `make_tail_extension_override()` 実装を逐語で写した、ctx-aware な正しい
# レシピへ訂正する。
# ---------------------------------------------------------------------------


def test_fix24_positive_real_manifest_passes_with_corrected_recipe(
    manifest_data: Dict[str, Any],
) -> None:
    m.validate_probe_manifest(manifest_data)
    role = _p3_probe(manifest_data)["role"]
    assert "make_tail_extension_override" in role
    assert "frames_from_ms(terminal_extension_ms, frame_ms)" in role
    assert m._P3_RELEASE_RECIPE_FORBIDDEN_MARKER not in role


def test_fix24_gate_synth_frames_from_ms_signature_matches_recipe() -> None:
    """PR #322 第13巡指摘 Fix 24: manifest の実行レシピが前提とする
    `gate_synth.frames_from_ms(ms, frame_ms)` の実シグネチャを、
    gate_synth.py（凍結・改変禁止の read-only 参照）のソースを AST 解析
    して検証する——onnxruntime 等の重い実行時依存を要する実 import は
    避け、静的解析のみで読む（read-only 精神を保ったまま manifest->
    run_pipeline のラウンドトリップに最も近い機械検証）。"""
    assert m.GATE_SYNTH_PY_REFERENCE_PATH.is_file()
    source = m.GATE_SYNTH_PY_REFERENCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(m.GATE_SYNTH_PY_REFERENCE_PATH))
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "frames_from_ms" in functions, "gate_synth.py に frames_from_ms が定義されていない"
    frames_from_ms_args = [a.arg for a in functions["frames_from_ms"].args.args]
    assert frames_from_ms_args == ["ms", "frame_ms"], (
        f"gate_synth.frames_from_ms のシグネチャが変わっている（{frames_from_ms_args!r}）——"
        "manifest の実行レシピは frames_from_ms(ms, frame_ms) の2引数呼び出しを前提とする"
    )
    assert "run_pipeline" in functions, "gate_synth.py に run_pipeline が定義されていない"
    run_pipeline_args = [a.arg for a in functions["run_pipeline"].args.args]
    assert "final_phone_dur_override" in run_pipeline_args, (
        "gate_synth.run_pipeline が final_phone_dur_override kwarg を持たなくなっている"
    )


def test_negative_fix24_stale_single_arg_recipe_text_rejected(
    manifest_data: Dict[str, Any],
) -> None:
    """『レシピの1引数呼び出し文言の残存拒否』: 旧（誤り）の1引数呼び出し
    文言が role へ残置されていると fail-closed で拒否される。"""
    bad = _mutate(manifest_data)
    p3 = _p3_probe(bad)
    p3["role"] = p3["role"] + "\n\n" + m._P3_RELEASE_RECIPE_FORBIDDEN_MARKER + " を使う。"
    with pytest.raises(m.Run9ValidationError, match="stale single-argument recipe text"):
        m.validate_probe_manifest(bad)


@pytest.mark.parametrize(
    "marker", ["make_tail_extension_override", "frames_from_ms(terminal_extension_ms, frame_ms)"]
)
def test_negative_fix24_recipe_marker_missing(
    manifest_data: Dict[str, Any], marker: str,
) -> None:
    bad = _mutate(manifest_data)
    p3 = _p3_probe(bad)
    p3["role"] = p3["role"].replace(marker, "")
    with pytest.raises(m.Run9ValidationError):
        m.validate_probe_manifest(bad)


# ---------------------------------------------------------------------------
# PR #333 Codex bot レビュー第3巡指摘1（P1、採用）: revision_bridge の
# C0/C1/positive/negative 4エントリの `identity_metric_space_ref` が
# identity_decision_protocol_v0.6.json の supersede_declaration.
# preserved_generation_definitions に列挙されていることの閉包検査
# ---------------------------------------------------------------------------


def test_pr333_r3_positive_all_superseded_calibration_entries_are_preserved(
    manifest_data: Dict[str, Any],
) -> None:
    """回帰確認: 実 probe_manifest.json + 実 identity_decision_protocol_
    v0.6.json の組で、C0/C1/positive/negative の4エントリいずれも閉包
    検査を通過すること（full-chain 経由）。"""
    m.validate_probe_manifest(manifest_data)  # 例外なしの確認
    protocol_document = m._load_identity_decision_protocol_document()
    preserved = set(
        protocol_document["supersede_declaration"]["preserved_generation_definitions"]
    )
    for entry_name in m._REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES:
        ref = manifest_data["revision_bridge"][entry_name]["identity_metric_space_ref"]
        assert ref in preserved, f"{entry_name}: {ref!r} not in preserved_generation_definitions"


def test_negative_pr333_r3_bridge_ref_outside_preserved_generation_definitions(
    manifest_data: Dict[str, Any], tmp_path: Path,
) -> None:
    """`identity_decision_protocol_v0.6.json` の
    `supersede_declaration.preserved_generation_definitions` から
    `c0_replay_takes` が実際に参照する生成定義 path を1件取り除いた合成
    fixture を `IDENTITY_DECISION_PROTOCOL_PATH` へ差し替えると、
    `validate_probe_manifest()` full-chain が閉包検査で fail-closed 拒否
    すること（実ファイルは一切改変しない——`m.IDENTITY_DECISION_PROTOCOL_
    PATH` を差し替えて元へ復元する、`SCORE_PY_REFERENCE_PATH` と同型の
    monkeypatch 方式）。"""
    protocol_data = copy.deepcopy(m._load_identity_decision_protocol_document())
    removed_ref = "inputs/identity_metric_space.json#calibration.freeze_threshold.d_c0_population"
    items = protocol_data["supersede_declaration"]["preserved_generation_definitions"]
    assert removed_ref in items
    items.remove(removed_ref)
    tampered_path = tmp_path / "identity_decision_protocol_v0.6_tampered.json"
    tampered_path.write_text(
        json.dumps(protocol_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    original_path = m.IDENTITY_DECISION_PROTOCOL_PATH
    try:
        m.IDENTITY_DECISION_PROTOCOL_PATH = tampered_path  # type: ignore[misc]
        with pytest.raises(
            m.Run9ValidationError, match="preserved_generation_definitions"
        ):
            m.validate_probe_manifest(_mutate(manifest_data))
    finally:
        m.IDENTITY_DECISION_PROTOCOL_PATH = original_path  # type: ignore[misc]
    # 復元後は通常どおり通過することを確認する（後続テストへの汚染防止）。
    m.validate_probe_manifest(_mutate(manifest_data))


def test_negative_pr333_r3_bridge_ref_missing_preserved_generation_definitions_key(
    manifest_data: Dict[str, Any], tmp_path: Path,
) -> None:
    """`preserved_generation_definitions` キー自体が存在しない合成
    fixture でも（`.get(..., [])` フォールバックにより）closure check が
    通常どおり fail-closed で拒否すること（KeyError で落ちず
    Run9ValidationError として一貫させる）。"""
    protocol_data = copy.deepcopy(m._load_identity_decision_protocol_document())
    del protocol_data["supersede_declaration"]["preserved_generation_definitions"]
    tampered_path = tmp_path / "identity_decision_protocol_v0.6_no_key.json"
    tampered_path.write_text(
        json.dumps(protocol_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    original_path = m.IDENTITY_DECISION_PROTOCOL_PATH
    try:
        m.IDENTITY_DECISION_PROTOCOL_PATH = tampered_path  # type: ignore[misc]
        with pytest.raises(m.Run9ValidationError):
            m.validate_probe_manifest(_mutate(manifest_data))
    finally:
        m.IDENTITY_DECISION_PROTOCOL_PATH = original_path  # type: ignore[misc]
    m.validate_probe_manifest(_mutate(manifest_data))
