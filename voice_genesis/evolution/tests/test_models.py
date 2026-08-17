"""test_models.py — VG-E0 schema 4種（`models.py`）のテスト。

DESIGN_VG_E0.md §7 AC「schema4種のloader/validator（fail-closed・未知
フィールド拒否・genome_id 再計算一致検証）」を直接検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402


def _base_genome() -> models.VoiceGenome:
    return models.build_genome(
        coords=models.Coords(ritsu=0.5, pjs=0.2, user=0.3),
        seed=11,
        lineage="L-C",
        generation=1,
        parents=("ffc44fd26d70e89d",),
        operator="drift",
        operator_params={"rng_seed": 7, "step": 0.05},
    )


# --- genome_id 決定論 ---------------------------------------------------


def test_genome_id_is_16_lowercase_hex() -> None:
    g = _base_genome()
    assert len(g.genome_id) == 16
    assert g.genome_id == g.genome_id.lower()
    int(g.genome_id, 16)  # ValueError if not hex


def test_genome_id_deterministic_same_input() -> None:
    a = _base_genome()
    b = _base_genome()
    assert a.genome_id == b.genome_id


def test_genome_id_excludes_notes_and_anchors_provenance() -> None:
    """genome_id は coords/seed/lineage/generation/parents/operator/
    operator_params の6フィールドのみから導出される（DESIGN_VG_E0.md §1）。
    notes / anchors_provenance を変えても genome_id は不変。
    """
    a = models.build_genome(
        coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={}, notes="note A",
    )
    b = models.build_genome(
        coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={}, notes="note B",
        anchors_provenance={"checkpoint_sha256": "a" * 64, "embed_sha256": {n: "b" * 64 for n in models.ANCHOR_NAMES}},
    )
    assert a.genome_id == b.genome_id


def test_genome_id_changes_with_coords() -> None:
    a = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    b = models.build_genome(
        coords=models.Coords(0.5, 0.2, 0.3), seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    assert a.genome_id != b.genome_id


def test_genome_id_fixed_decimal_representation_matters() -> None:
    """小数6桁固定表記: 数学的に等しい値でも float 表現が違えば同じ丸め後
    値に正規化されるため genome_id は一致するはず（0.1 と 0.100000 は同じ
    round(...,6) 値）。"""
    a = models.compute_genome_id(
        coords=models.Coords(0.1, 0.4, 0.5), seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    b = models.compute_genome_id(
        coords=models.Coords(0.100000, 0.400000, 0.500000), seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    assert a == b


# --- roundtrip / from_dict ------------------------------------------------


def test_genome_roundtrip_to_dict_from_dict() -> None:
    g = _base_genome()
    d = models.genome_to_dict(g)
    g2 = models.genome_from_dict(d)
    assert g2 == g


def test_genome_roundtrip_to_json_from_json() -> None:
    g = _base_genome()
    text = models.genome_to_json(g)
    g2 = models.genome_from_json(text)
    assert g2 == g


def test_genome_from_dict_rejects_unknown_top_level_key() -> None:
    d = models.genome_to_dict(_base_genome())
    d["bogus"] = 1
    with pytest.raises(models.GenomeValidationError, match="unknown key"):
        models.genome_from_dict(d)


@pytest.mark.parametrize("key", sorted(models._GENOME_TOP_LEVEL_KEYS))
def test_genome_from_dict_rejects_missing_key(key: str) -> None:
    d = models.genome_to_dict(_base_genome())
    del d[key]
    with pytest.raises(models.GenomeValidationError, match="missing required key"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_wrong_schema() -> None:
    d = models.genome_to_dict(_base_genome())
    d["schema"] = "voice-genome/9.9"
    with pytest.raises(models.GenomeValidationError, match="schema"):
        models.genome_from_dict(d)


def test_genome_from_dict_detects_genome_id_tamper() -> None:
    d = models.genome_to_dict(_base_genome())
    d["seed"] = d["seed"] + 1  # coords 以外を書き換えても genome_id とずれる
    with pytest.raises(models.GenomeValidationError, match="genome_id mismatch"):
        models.genome_from_dict(d)


def test_genome_from_dict_detects_coords_tamper() -> None:
    d = models.genome_to_dict(_base_genome())
    d["coords"]["ritsu"] = round(d["coords"]["ritsu"] + 0.01, 6)
    d["coords"]["pjs"] = round(d["coords"]["pjs"] - 0.01, 6)
    with pytest.raises(models.GenomeValidationError, match="genome_id mismatch"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_bad_lineage() -> None:
    d = models.genome_to_dict(_base_genome())
    d["lineage"] = "L-X"
    with pytest.raises(models.GenomeValidationError, match="lineage"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_bad_operator() -> None:
    d = models.genome_to_dict(_base_genome())
    d["operator"] = "mutate_unknown"
    with pytest.raises(models.GenomeValidationError, match="operator"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_coords_not_summing_to_one() -> None:
    d = models.genome_to_dict(_base_genome())
    d["coords"] = {"ritsu": 0.4, "pjs": 0.4, "user": 0.4}
    with pytest.raises(models.GenomeValidationError):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_lineage_coords_mismatch() -> None:
    """Codex 指摘3: `_base_genome()` の coords (0.5/0.2/0.3) はどの成分も
    0.55 未満のため座標由来 lineage は L-C（元々 lineage="L-C" で一致
    している）。これを手書きで L-R に書き換えると、ローダーが座標由来の
    再計算値との不一致を fail-closed で拒否する。"""
    d = models.genome_to_dict(_base_genome())
    d["lineage"] = "L-R"
    with pytest.raises(models.GenomeValidationError, match="does not match coords-derived lineage"):
        models.genome_from_dict(d)


def test_genome_from_dict_allows_novelty_regardless_of_coords() -> None:
    """NOVELTY は座標によらず許容される（novelty_jump 由来の1世代限定隔離
    — DESIGN_VG_E0.md §3.1）。lineage を NOVELTY にすると genome_id も
    （lineage が6フィールドの1つのため）変わるので併せて再計算する。"""
    d = models.genome_to_dict(_base_genome())
    d["lineage"] = "NOVELTY"
    d["genome_id"] = models.compute_genome_id(
        coords=models.Coords(**d["coords"]), seed=d["seed"], lineage="NOVELTY",
        generation=d["generation"], parents=d["parents"], operator=d["operator"],
        operator_params=d["operator_params"],
    )
    genome = models.genome_from_dict(d)
    assert genome.lineage == "NOVELTY"


def test_genome_from_dict_rejects_non_hex_checkpoint_sha256() -> None:
    """Codex 指摘4: checkpoint_sha256 は正確に64文字の小文字16進を要求する
    （非空文字列であれば何でも通っていた従来動作を締める）。"""
    d = models.genome_to_dict(_base_genome())
    d["anchors_provenance"] = {
        "checkpoint_sha256": "z" * 64,  # 'z' は16進ではない
        "embed_sha256": {n: "a" * 64 for n in models.ANCHOR_NAMES},
    }
    with pytest.raises(models.GenomeValidationError, match="checkpoint_sha256"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_uppercase_sha256() -> None:
    d = models.genome_to_dict(_base_genome())
    d["anchors_provenance"] = {
        "checkpoint_sha256": "A" * 64,  # 大文字は拒否（小文字限定契約）
        "embed_sha256": {n: "a" * 64 for n in models.ANCHOR_NAMES},
    }
    with pytest.raises(models.GenomeValidationError, match="checkpoint_sha256"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_wrong_length_embed_sha256() -> None:
    d = models.genome_to_dict(_base_genome())
    d["anchors_provenance"] = {
        "checkpoint_sha256": "a" * 64,
        "embed_sha256": {n: "a" * 63 for n in models.ANCHOR_NAMES},  # 63 文字（桁不足）
    }
    with pytest.raises(models.GenomeValidationError, match="embed_sha256"):
        models.genome_from_dict(d)


def test_genome_from_dict_accepts_valid_sha256() -> None:
    d = models.genome_to_dict(_base_genome())
    d["anchors_provenance"] = {
        "checkpoint_sha256": "0123456789abcdef" * 4,
        "embed_sha256": {n: "fedcba9876543210" * 4 for n in models.ANCHOR_NAMES},
    }
    genome = models.genome_from_dict(d)
    assert genome.anchors_provenance["checkpoint_sha256"] == "0123456789abcdef" * 4


def test_genome_from_dict_rejects_negative_coord() -> None:
    d = models.genome_to_dict(_base_genome())
    d["coords"] = {"ritsu": -0.1, "pjs": 0.6, "user": 0.5}
    with pytest.raises(models.GenomeValidationError):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_nan_coord() -> None:
    d = models.genome_to_dict(_base_genome())
    d["coords"] = {"ritsu": float("nan"), "pjs": 0.5, "user": 0.5}
    with pytest.raises(models.GenomeValidationError):
        models.genome_from_dict(d)


@pytest.mark.parametrize(
    "operator,expected",
    sorted(models.EXPECTED_PARENT_COUNT.items()),
)
def test_expected_parent_count_enforced_at_construction(operator: str, expected: int) -> None:
    bogus_parents = tuple(f"{'a' * 15}{i}" for i in range(expected + 1))
    params = {
        "founder": {},
        "drift": {"rng_seed": 1, "step": 0.01},
        "vertex_pull": {"weight": 0.5, "vertex": "ritsu", "pull": 0.1},
        "reseed": {"new_seed": 1},
        "edge_walk": {"rng_seed": 1, "edge": ["ritsu", "pjs"], "step": 0.01},
        "novelty_jump": {"rng_seed": 1},
    }[operator]
    with pytest.raises(models.GenomeValidationError, match="parent"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=bogus_parents, operator=operator, operator_params=params,
        )


# --- operator_params 閉じた語彙 + 数値上限 --------------------------------


def test_drift_step_above_max_rejected() -> None:
    with pytest.raises(models.GenomeValidationError, match="step"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="drift",
            operator_params={"rng_seed": 1, "step": models.DRIFT_STEP_MAX + 0.001},
        )


def test_vertex_pull_pull_above_max_rejected() -> None:
    with pytest.raises(models.GenomeValidationError, match="pull"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16, "b" * 16), operator="vertex_pull",
            operator_params={"weight": 0.5, "vertex": "ritsu", "pull": models.VERTEX_PULL_PULL_MAX + 0.001},
        )


def test_edge_walk_step_above_max_rejected() -> None:
    with pytest.raises(models.GenomeValidationError, match="step"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="edge_walk",
            operator_params={"rng_seed": 1, "edge": ["ritsu", "pjs"], "step": models.EDGE_WALK_STEP_MAX + 0.001},
        )


def test_edge_walk_requires_distinct_anchors() -> None:
    with pytest.raises(models.GenomeValidationError, match="distinct"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="edge_walk",
            operator_params={"rng_seed": 1, "edge": ["ritsu", "ritsu"], "step": 0.01},
        )


def test_founder_requires_empty_operator_params() -> None:
    with pytest.raises(models.GenomeValidationError, match="founder"):
        models.build_genome(
            coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=0,
            parents=(), operator="founder", operator_params={"stray": 1},
        )


# --- EvaluationRecord ------------------------------------------------------


def _base_eval_record() -> models.EvaluationRecord:
    return models.build_evaluation_record(
        genome_id="a" * 16,
        probe_set="d3-probe/0.1",
        evaluator=models.Evaluator(kind="training", version="v0"),
        axes={"naturalness": 0.8},
        blind_batch=None,
        verdict=None,
    )


def test_evaluation_record_roundtrip() -> None:
    r = _base_eval_record()
    d = models.evaluation_record_to_dict(r)
    r2 = models.evaluation_record_from_dict(d)
    assert r2 == r


def test_evaluation_record_has_no_overall_score_field() -> None:
    """DESIGN_VG_E0.md §5「総合1点スコアのフィールドを作らない ことを
    schema レベルで強制」— 固定トップレベルキー集合に "score" 等の総合点
    フィールドが存在しないことを直接固定する。"""
    assert "score" not in models._EVAL_TOP_LEVEL_KEYS
    assert "total" not in models._EVAL_TOP_LEVEL_KEYS
    assert "overall" not in models._EVAL_TOP_LEVEL_KEYS
    assert models._EVAL_TOP_LEVEL_KEYS == {
        "schema", "genome_id", "probe_set", "evaluator", "axes", "blind_batch", "verdict",
    }


def test_evaluation_record_rejects_unknown_top_level_key() -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["score"] = 0.9
    with pytest.raises(models.GenomeValidationError, match="unknown key"):
        models.evaluation_record_from_dict(d)


def test_evaluation_record_rejects_bad_evaluator_kind() -> None:
    with pytest.raises(models.GenomeValidationError, match="evaluator.kind"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="robot", version="v0"), axes={},
        )


def test_evaluation_record_rejects_bad_verdict() -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["verdict"] = "maybe"
    with pytest.raises(models.GenomeValidationError, match="verdict"):
        models.evaluation_record_from_dict(d)


def test_evaluation_record_rejects_nonfinite_axis() -> None:
    with pytest.raises(models.GenomeValidationError):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={"naturalness": float("nan")},
        )


# --- HackRecord -------------------------------------------------------------


def _base_hack_record() -> models.HackRecord:
    return models.build_hack_record(
        genome_id="a" * 16, symptom="spectral spike exploit", evaluator_version="v0",
        discovered_by="blind_batch_3",
    )


def test_hack_record_roundtrip() -> None:
    r = _base_hack_record()
    d = models.hack_record_to_dict(r)
    r2 = models.hack_record_from_dict(d)
    assert r2 == r


def test_hack_record_default_disposition_retained() -> None:
    assert _base_hack_record().disposition == "retained"


def test_hack_record_rejects_unknown_disposition() -> None:
    d = models.hack_record_to_dict(_base_hack_record())
    d["disposition"] = "deleted"
    with pytest.raises(models.GenomeValidationError, match="disposition"):
        models.hack_record_from_dict(d)


def test_hack_record_rejects_unknown_top_level_key() -> None:
    d = models.hack_record_to_dict(_base_hack_record())
    d["extra"] = 1
    with pytest.raises(models.GenomeValidationError, match="unknown key"):
        models.hack_record_from_dict(d)
