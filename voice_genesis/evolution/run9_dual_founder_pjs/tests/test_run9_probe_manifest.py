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
