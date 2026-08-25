"""test_run9_probe_manifest.py — RUN9-PROBE-1: DESIGN_RUN9 §15 Probe Set
(P0-P5) の実体 manifest（`evaluation/probe_manifest.json`）と
`run9_schema.validate_probe_manifest()` の最低テスト。

音声処理・実学習を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

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


def test_probe_manifest_sha_pinned_and_matches_real_file(contract_raw: Dict[str, Any]) -> None:
    """C.12: `probe_manifest_sha` は PINNED であり、値は
    `evaluation/probe_manifest.json` の実バイト sha256 と一致する。"""
    field = contract_raw["probe_manifest_sha"]
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(m.PROBE_MANIFEST_PATH), (
        "probe_manifest_sha が PINNED を宣言しているが、"
        f"{m.PROBE_MANIFEST_PATH} の実バイト sha256 と一致しない"
    )


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


def test_negative_fix3_release_duration_mismatch(manifest_data: Dict[str, Any]) -> None:
    bad = _mutate(manifest_data)
    (p3,) = [p for p in bad["probes"] if p["probe_id"] == "P3"]
    cell = _cell_by_id(p3, "P3-RELEASE-SHORT-VOICED")
    cell["notes"][-1]["duration_beats"] = 4  # 宣言は short=1
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
