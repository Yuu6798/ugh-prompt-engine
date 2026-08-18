"""test_models.py — VG-E0 schema 4種（`models.py`）のテスト。

DESIGN_VG_E0.md §7 AC「schema4種のloader/validator（fail-closed・未知
フィールド拒否・genome_id 再計算一致検証）」を直接検証する。
"""
from __future__ import annotations

import math
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


# --- 符号付きゼロの正規化（PR #267 Codex R8 指摘） --------------------------


def test_genome_id_negative_zero_coord_matches_positive_zero() -> None:
    """-0.0 と 0.0 は数値的に同一の重心座標だが、修正前は
    `_canonicalize_for_hash` の6桁固定表記が `"-0.000000"` / `"0.000000"`
    に分岐し、別 genome_id になっていた（`-0.0 < 0.0` は False・
    `round(-0.0, 6) != -0.0` も False のため coords 検証の既存チェックを
    両方すり抜けていた）。"""
    a = models.compute_genome_id(
        coords=models.Coords(-0.0, 0.0, 1.0), seed=0, lineage="L-U", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    b = models.compute_genome_id(
        coords=models.Coords(0.0, 0.0, 1.0), seed=0, lineage="L-U", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    assert a == b


def test_build_genome_normalizes_negative_zero_coord() -> None:
    """build_genome()（書込経路）は座標の -0.0 を正準 +0.0 へ正規化してから
    genome_id 計算・格納に使う: `Coords(-0.0, 0.0, 1.0)` と
    `Coords(0.0, 0.0, 1.0)` は同一 genome_id・同一格納値になる。"""
    a = models.build_genome(
        coords=models.Coords(-0.0, 0.0, 1.0), seed=0, lineage="L-U", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    b = models.build_genome(
        coords=models.Coords(0.0, 0.0, 1.0), seed=0, lineage="L-U", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    assert a.genome_id == b.genome_id
    assert a.coords == b.coords
    assert math.copysign(1.0, a.coords.ritsu) > 0.0
    assert math.copysign(1.0, a.coords.pjs) > 0.0


def test_genome_from_dict_rejects_negative_zero_coord() -> None:
    """デシリアライズ経路（genome_from_dict）は operator_params の
    `_require_bounded_float(normalize=False)` と対称に、座標の -0.0 を
    非正規形として fail-closed で拒否する（台帳へ非正規形の値が紛れ込むのを
    構造的に防ぐ）。"""
    d = models.genome_to_dict(_base_genome())
    d["coords"] = {"ritsu": -0.0, "pjs": 0.5, "user": 0.5}
    with pytest.raises(models.GenomeValidationError, match="negative zero"):
        models.genome_from_dict(d)


def test_build_genome_normalizes_negative_zero_operator_param_float() -> None:
    """operator_params.step が丸め後に -0.0 になっても、build_genome() は
    正準 +0.0 へ正規化する（coords と同じ理由）。"""
    a = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16,), operator="drift",
        operator_params={"rng_seed": 1, "step": -0.0},
    )
    b = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16,), operator="drift",
        operator_params={"rng_seed": 1, "step": 0.0},
    )
    assert a.genome_id == b.genome_id
    assert a.operator_params == {"rng_seed": 1, "step": 0.0}
    assert math.copysign(1.0, a.operator_params["step"]) > 0.0


def test_genome_from_dict_rejects_negative_zero_operator_param_float() -> None:
    """デシリアライズ経路は operator_params の -0.0 も coords と同様に
    fail-closed で拒否する。"""
    d = models.genome_to_dict(_base_genome())
    d["operator_params"]["step"] = -0.0
    with pytest.raises(models.GenomeValidationError, match="negative zero"):
        models.genome_from_dict(d)


def test_genome_to_json_never_emits_negative_zero() -> None:
    """coords/operator_params のどのフィールドが -0.0 を経由しても、
    genome_to_json() の出力テキストに `"-0.0"`（`"-0.000000"` を含む）が
    現れないことを走査で担保する（全経路の回帰テスト）。"""
    genomes = [
        models.build_genome(
            coords=models.Coords(-0.0, 0.0, 1.0), seed=0, lineage="L-U", generation=0,
            parents=(), operator="founder", operator_params={},
        ),
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="drift",
            operator_params={"rng_seed": 1, "step": -0.0},
        ),
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16, "b" * 16), operator="vertex_pull",
            operator_params={"weight": -0.0, "vertex": "ritsu", "pull": -0.0},
        ),
    ]
    for g in genomes:
        text = models.genome_to_json(g)
        assert "-0.0" not in text


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
    — DESIGN_VG_E0.md §3.1）。operator=novelty_jump は build_genome() 経由でも
    lineage=NOVELTY を必須とする（Codex 指摘A, PR #267 R4）ため、まず正しい
    NOVELTY 個体を構築してから coords だけを「NOVELTY でなければ L-R になる
    はずの」値へ書き換え、genome_id を再計算する — これで「座標によらず
    許容される」ことをローダー側で直接確認できる。"""
    base = models.build_genome(
        coords=models.Coords(ritsu=0.5, pjs=0.2, user=0.3), seed=11, lineage="NOVELTY", generation=1,
        parents=("ffc44fd26d70e89d",), operator="novelty_jump", operator_params={"rng_seed": 7},
    )
    d = models.genome_to_dict(base)
    d["coords"] = {"ritsu": 0.9, "pjs": 0.05, "user": 0.05}  # 座標由来なら L-R になるはずの値
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


# --- PR #267 Codex R5 指摘3（P2）: reseed の seed 束縛 ----------------------


def test_build_genome_rejects_reseed_new_seed_mismatching_top_level_seed() -> None:
    with pytest.raises(models.GenomeValidationError, match="new_seed"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=7, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="reseed", operator_params={"new_seed": 8},
        )


def test_genome_from_dict_rejects_reseed_new_seed_mismatching_top_level_seed() -> None:
    d = models.genome_to_dict(models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=7, lineage="L-C", generation=1,
        parents=("a" * 16,), operator="reseed", operator_params={"new_seed": 7},
    ))
    d["seed"] = 9  # genome_id はもう再計算値と一致しなくなるが、seed 束縛
    # チェックが genome_id チェックより先に走ることを確かめるため match で
    # "new_seed" を要求する。
    with pytest.raises(models.GenomeValidationError, match="new_seed"):
        models.genome_from_dict(d)


# --- PR #267 Codex R5 指摘4（P2）: founder の generation=0 強制 -------------


def test_build_genome_rejects_founder_with_nonzero_generation() -> None:
    with pytest.raises(models.GenomeValidationError, match="generation"):
        models.build_genome(
            coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=9,
            parents=(), operator="founder", operator_params={},
        )


def test_genome_from_dict_rejects_founder_with_nonzero_generation() -> None:
    d = models.genome_to_dict(models.build_genome(
        coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    ))
    d["generation"] = 9
    with pytest.raises(models.GenomeValidationError, match="generation"):
        models.genome_from_dict(d)


def test_build_genome_rejects_non_founder_with_generation_zero() -> None:
    with pytest.raises(models.GenomeValidationError, match="generation"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=0,
            parents=("a" * 16,), operator="drift", operator_params={"rng_seed": 1, "step": 0.01},
        )


def test_genome_from_dict_rejects_non_founder_with_generation_zero() -> None:
    d = models.genome_to_dict(models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16,), operator="drift", operator_params={"rng_seed": 1, "step": 0.01},
    ))
    d["generation"] = 0
    with pytest.raises(models.GenomeValidationError, match="generation"):
        models.genome_from_dict(d)


# --- operator_params float 正規化（Codex 指摘B） --------------------------


def test_build_genome_normalizes_operator_params_float_to_six_decimals() -> None:
    """weight=0.5 と weight=0.5000001 は build_genome() で同一の6桁丸め値へ
    正規化され、同一 genome_id・同一シリアライズペイロードになる（丸め前は
    genome_id ハッシュ計算だけが独自に6桁丸めしていたため、ハッシュは一致
    するのに格納ペイロードが食い違い、台帳の排他 create が「同一IDの宣言
    差」として衝突していた）。"""
    a = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16, "b" * 16), operator="vertex_pull",
        operator_params={"weight": 0.5, "vertex": "ritsu", "pull": 0.1},
    )
    b = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16, "b" * 16), operator="vertex_pull",
        operator_params={"weight": 0.5000001, "vertex": "ritsu", "pull": 0.1},
    )
    assert a.genome_id == b.genome_id
    assert a.operator_params == {"weight": 0.5, "vertex": "ritsu", "pull": 0.1}
    assert b.operator_params == {"weight": 0.5, "vertex": "ritsu", "pull": 0.1}
    assert models.genome_to_dict(a) == models.genome_to_dict(b)


def test_build_genome_rounds_before_bound_check_at_boundary() -> None:
    """丸めは上限値検査より先に行う（順序の直接確認）: DRIFT_STEP_MAX(0.08)
    より 3e-7 だけ大きい生値は丸め前チェックなら拒否されるはずだが、6桁
    丸め後はちょうど 0.08 になり境界内として受理される。丸めても境界を
    明確に超える値は引き続き拒否される。"""
    just_over_max_pre_round = models.DRIFT_STEP_MAX + 3e-7
    assert round(just_over_max_pre_round, 6) == models.DRIFT_STEP_MAX  # 丸めで境界に一致する前提の確認
    g = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
        parents=("a" * 16,), operator="drift",
        operator_params={"rng_seed": 1, "step": just_over_max_pre_round},
    )
    assert g.operator_params["step"] == models.DRIFT_STEP_MAX

    with pytest.raises(models.GenomeValidationError, match="step"):
        models.build_genome(
            coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=1,
            parents=("a" * 16,), operator="drift",
            operator_params={"rng_seed": 1, "step": models.DRIFT_STEP_MAX + 0.01},
        )


def test_genome_from_dict_rejects_non_normalized_operator_param_float() -> None:
    """デシリアライズ経路は build_genome() と対称に、既に6桁丸め済みでない
    operator_params の float を fail-closed で拒否する（Codex 指摘B）。"""
    d = models.genome_to_dict(_base_genome())
    d["operator_params"]["step"] = 0.0500001  # 7桁目が非ゼロ = 正規化されていない
    with pytest.raises(models.GenomeValidationError, match="6 decimal"):
        models.genome_from_dict(d)


# --- NOVELTY / operator 整合（Codex 指摘C） --------------------------------


def test_genome_from_dict_rejects_novelty_with_drift_operator() -> None:
    """NOVELTY は operator ∈ {novelty_jump, vertex_pull} でしか宣言できない
    （Codex 指摘C）。drift + NOVELTY は fail-closed で拒否する。"""
    base = models.build_genome(
        coords=models.Coords(0.5, 0.2, 0.3), seed=11, lineage="L-C", generation=1,
        parents=("ffc44fd26d70e89d",), operator="drift", operator_params={"rng_seed": 7, "step": 0.05},
    )
    d = models.genome_to_dict(base)
    d["lineage"] = "NOVELTY"
    d["genome_id"] = models.compute_genome_id(
        coords=models.Coords(**d["coords"]), seed=d["seed"], lineage="NOVELTY",
        generation=d["generation"], parents=d["parents"], operator=d["operator"],
        operator_params=d["operator_params"],
    )
    with pytest.raises(models.GenomeValidationError, match="NOVELTY"):
        models.genome_from_dict(d)


def test_genome_from_dict_rejects_novelty_jump_with_coordinate_lineage() -> None:
    """逆方向: operator=novelty_jump は lineage=NOVELTY を必須とする
    （Codex 指摘C）。座標由来 lineage（NOVELTY でない）を宣言した
    novelty_jump ドキュメントは拒否する。build_genome() 自体が同じ組合せを
    拒否するようになった（Codex 指摘A, PR #267 R4）ため、ここでは一旦正しい
    NOVELTY 個体を構築してから lineage だけを座標由来値へ書き換え、
    genome_id を再計算してローダー単体の拒否を確認する（builder 側の同型
    拒否は `test_build_genome_rejects_novelty_jump_with_coordinate_lineage`
    が担当する）。"""
    base = models.build_genome(
        coords=models.Coords(0.5, 0.2, 0.3), seed=11, lineage="NOVELTY", generation=1,
        parents=("ffc44fd26d70e89d",), operator="novelty_jump", operator_params={"rng_seed": 7},
    )
    d = models.genome_to_dict(base)
    d["lineage"] = "L-C"  # (0.5, 0.2, 0.3) の座標由来 lineage と一致する値
    d["genome_id"] = models.compute_genome_id(
        coords=models.Coords(**d["coords"]), seed=d["seed"], lineage="L-C",
        generation=d["generation"], parents=d["parents"], operator=d["operator"],
        operator_params=d["operator_params"],
    )
    with pytest.raises(models.GenomeValidationError, match="novelty_jump"):
        models.genome_from_dict(d)


def test_genome_from_dict_accepts_novelty_with_vertex_pull_operator() -> None:
    """vertex_pull は系統間交配時に NOVELTY を宣言できる（両親 lineage が
    実際に異なるかは本関数単体では検証不能 — 台帳参照が必要なため
    VG-E1 送り。ここでは operator=vertex_pull であれば受理されることのみ
    確認する）。"""
    base = models.build_genome(
        coords=models.Coords(0.5, 0.2, 0.3), seed=11, lineage="L-C", generation=1,
        parents=("ffc44fd26d70e89d", "0123456789abcdef"), operator="vertex_pull",
        operator_params={"weight": 0.5, "vertex": "ritsu", "pull": 0.1},
    )
    d = models.genome_to_dict(base)
    d["lineage"] = "NOVELTY"
    d["genome_id"] = models.compute_genome_id(
        coords=models.Coords(**d["coords"]), seed=d["seed"], lineage="NOVELTY",
        generation=d["generation"], parents=d["parents"], operator=d["operator"],
        operator_params=d["operator_params"],
    )
    genome = models.genome_from_dict(d)
    assert genome.lineage == "NOVELTY"


# --- build_genome() の lineage 検証（Codex 指摘A, PR #267 R4） -------------
#
# 従来 build_genome() は宣言 lineage を素通ししていた（Archive.submit が
# builder 出力を round-trip なしで消費する経路がある以上、書込経路そのもの
# での強制が必要）。genome_from_dict() と共有の `_validate_lineage_for_genome()`
# を build_genome() 経由でも直接検証する。


def test_build_genome_rejects_lineage_coords_mismatch() -> None:
    """coords=(1.0, 0.0, 0.0) は座標由来 lineage が L-R になるはずだが、
    build_genome() へ改竄した lineage="L-C" を渡すと fail-closed で拒否する
    （genome_from_dict() 側の同型検証と対称）。"""
    with pytest.raises(models.GenomeValidationError, match="does not match coords-derived lineage"):
        models.build_genome(
            coords=models.Coords(1.0, 0.0, 0.0), seed=0, lineage="L-C", generation=0,
            parents=(), operator="founder", operator_params={},
        )


def test_build_genome_rejects_drift_with_novelty_lineage() -> None:
    """NOVELTY は operator ∈ {novelty_jump, vertex_pull} でしか宣言できない
    （Codex 指摘C）。build_genome() 経由で drift + NOVELTY を渡すと拒否する。"""
    with pytest.raises(models.GenomeValidationError, match="NOVELTY"):
        models.build_genome(
            coords=models.Coords(0.5, 0.2, 0.3), seed=11, lineage="NOVELTY", generation=1,
            parents=("ffc44fd26d70e89d",), operator="drift", operator_params={"rng_seed": 7, "step": 0.05},
        )


def test_build_genome_rejects_novelty_jump_with_coordinate_lineage() -> None:
    """逆方向: operator=novelty_jump は build_genome() 経由でも lineage=NOVELTY
    を必須とする（Codex 指摘C）。coords=(0.5, 0.2, 0.3) は座標由来 lineage が
    L-C と一致する値のため、ここで拒否されるのは coords 不一致チェックでは
    なく novelty_jump 専用の逆方向チェックであることを担保する。"""
    with pytest.raises(models.GenomeValidationError, match="novelty_jump"):
        models.build_genome(
            coords=models.Coords(0.5, 0.2, 0.3), seed=11, lineage="L-C", generation=1,
            parents=("ffc44fd26d70e89d",), operator="novelty_jump", operator_params={"rng_seed": 7},
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


def test_build_evaluation_record_rejects_empty_string_axis_key() -> None:
    with pytest.raises(models.GenomeValidationError, match="axes key must be a non-empty string"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={"": 0.5},
        )


def test_evaluation_record_from_dict_rejects_empty_string_axis_key() -> None:
    """Codex 指摘B: build_evaluation_record() は空文字 axis 名を拒否するが、
    従来 evaluation_record_from_dict() はこの検証を欠き素通ししていた。
    builder と同じ非空文字列検証を loader 側にも適用する（共有ヘルパー
    `_validate_axes()`）。"""
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["axes"] = {"": 0.5}
    with pytest.raises(models.GenomeValidationError, match="axes key must be a non-empty string"):
        models.evaluation_record_from_dict(d)


# --- PR #267 Codex R10 指摘A（P2）: 空白のみ axis 名の fail-closed 拒否 ------


@pytest.mark.parametrize(
    "whitespace_only_name",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_evaluation_record_rejects_whitespace_only_axis_key(
    whitespace_only_name: str,
) -> None:
    """従来の非空チェックは strip 前の文字列に対してで、空白のみキー
    （半角スペース・タブ・全角スペース等）が `not name` を素通りしていた
    （予約名チェックのみ strip 済み比較で非対称だった）。非空判定を
    `name.strip()` に適用し fail-closed 拒否する（格納キー自体の trim は
    行わない — 拒否のみ）。"""
    with pytest.raises(models.GenomeValidationError, match="axes key must be a non-empty string"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={whitespace_only_name: 0.5},
        )


@pytest.mark.parametrize(
    "whitespace_only_name",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_evaluation_record_from_dict_rejects_whitespace_only_axis_key(
    whitespace_only_name: str,
) -> None:
    """loader 側（`evaluation_record_from_dict()`）も builder と同じ
    `_validate_axes()` 共有実装を通るため、空白のみキーは同様に拒否される。"""
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["axes"] = {whitespace_only_name: 0.5}
    with pytest.raises(models.GenomeValidationError, match="axes key must be a non-empty string"):
        models.evaluation_record_from_dict(d)


# --- PR #267 Codex R9 指摘2（P2）: 総合1点スコア名の予約ブロックリスト -----


@pytest.mark.parametrize(
    "reserved_name",
    ["overall", "total", "score", "aggregate", "composite", "summary"],
)
def test_build_evaluation_record_rejects_reserved_axis_name(reserved_name: str) -> None:
    """DESIGN_VG_E0.md §5「総合1点スコアのフィールドを作らないことを schema
    レベルで強制」— axes キーに予約された総合点相当の名前を使うと builder
    側で fail-closed 拒否される。"""
    with pytest.raises(models.GenomeValidationError, match="reserved single-score name"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={reserved_name: 0.95},
        )


@pytest.mark.parametrize(
    "reserved_variant",
    ["Overall", "TOTAL", " score ", "Aggregate\t", "COMPOSITE", "  Summary"],
)
def test_build_evaluation_record_rejects_reserved_axis_name_case_and_whitespace_variants(
    reserved_variant: str,
) -> None:
    """予約名判定は大文字小文字非区別・前後空白 trim 後に比較する。"""
    with pytest.raises(models.GenomeValidationError, match="reserved single-score name"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={reserved_variant: 0.5},
        )


def test_evaluation_record_from_dict_rejects_reserved_axis_name() -> None:
    """loader 側（`evaluation_record_from_dict()`）も builder と同じ
    `_validate_axes()` 共有実装を通るため、予約名は同様に拒否される。"""
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["axes"] = {"overall": 0.95}
    with pytest.raises(models.GenomeValidationError, match="reserved single-score name"):
        models.evaluation_record_from_dict(d)


def test_build_evaluation_record_accepts_ordinary_axis_names_unchanged() -> None:
    """通常の per-dimension 軸名（予約名を含まない）の受理は不変（回帰確認）。"""
    record = models.build_evaluation_record(
        genome_id="a" * 16, probe_set="d3-probe/0.1",
        evaluator=models.Evaluator(kind="training", version="v0"),
        axes={"naturalness": 0.8, "pitch_accuracy": 0.7, "scoreboard_novelty": 0.3},
    )
    assert record.axes == {"naturalness": 0.8, "pitch_accuracy": 0.7, "scoreboard_novelty": 0.3}


# --- blind_batch × evaluator.kind 束縛（Codex 指摘A） ----------------------


@pytest.mark.parametrize("kind", ["training", "hidden"])
def test_build_evaluation_record_rejects_blind_batch_for_non_human_kind(kind: str) -> None:
    """kind が human 以外（training/hidden）の場合、blind_batch は null 必須
    — 非 null は fail-closed で拒否する。"""
    with pytest.raises(models.GenomeValidationError, match="blind_batch"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind=kind, version="v0"),
            axes={"naturalness": 0.8}, blind_batch="batch-1",
        )


def test_build_evaluation_record_rejects_empty_blind_batch_for_human_kind() -> None:
    """kind=human で blind_batch を与える場合は非空文字列を要求する
    （空文字は拒否）。"""
    with pytest.raises(models.GenomeValidationError, match="blind_batch"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="human", version="v0"),
            axes={"naturalness": 0.8}, blind_batch="",
        )


def test_build_evaluation_record_accepts_nonempty_blind_batch_for_human_kind() -> None:
    r = models.build_evaluation_record(
        genome_id="a" * 16, probe_set="d3-probe/0.1",
        evaluator=models.Evaluator(kind="human", version="v0"),
        axes={"naturalness": 0.8}, blind_batch="batch-1",
    )
    assert r.blind_batch == "batch-1"


def test_build_evaluation_record_allows_null_blind_batch_for_human_kind() -> None:
    """human でも blind_batch 自体は任意（null は許容、与える場合のみ非空
    文字列を要求する）。"""
    r = models.build_evaluation_record(
        genome_id="a" * 16, probe_set="d3-probe/0.1",
        evaluator=models.Evaluator(kind="human", version="v0"),
        axes={"naturalness": 0.8}, blind_batch=None,
    )
    assert r.blind_batch is None


@pytest.mark.parametrize("kind", ["training", "hidden"])
def test_evaluation_record_from_dict_rejects_blind_batch_for_non_human_kind(kind: str) -> None:
    """デシリアライズ経路は build_evaluation_record() と対称に、非 human
    kind へ non-null blind_batch が紛れ込むのを拒否する。"""
    d = models.evaluation_record_to_dict(
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="human", version="v0"),
            axes={"naturalness": 0.8}, blind_batch="batch-1",
        )
    )
    d["evaluator"]["kind"] = kind
    with pytest.raises(models.GenomeValidationError, match="blind_batch"):
        models.evaluation_record_from_dict(d)


def test_evaluation_record_from_dict_rejects_empty_blind_batch_for_human_kind() -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["evaluator"]["kind"] = "human"
    d["blind_batch"] = ""
    with pytest.raises(models.GenomeValidationError, match="blind_batch"):
        models.evaluation_record_from_dict(d)


# --- PR #267 Codex R13 指摘2（P2）: 空白のみ参照文字列の fail-closed 拒否 ---
# probe_set / evaluator.version / blind_batch（human 時）は従来 truthiness
# 判定（`not value`）のみで、strip 後に空になる空白のみ文字列（`"   "` 等）
# を素通りしていた。`_require_nonblank_str()` 共有実装で builder/loader
# 双方に strip 後非空検証を適用する（axes キー・HackRecord の同種フィールド
# と同じ「参照文字列ファミリー」）。


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_evaluation_record_rejects_whitespace_only_probe_set(whitespace_only: str) -> None:
    with pytest.raises(models.GenomeValidationError, match="probe_set must be a non-empty string"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set=whitespace_only,
            evaluator=models.Evaluator(kind="training", version="v0"),
            axes={"naturalness": 0.8},
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_evaluation_record_from_dict_rejects_whitespace_only_probe_set(whitespace_only: str) -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["probe_set"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="probe_set must be a non-empty string"):
        models.evaluation_record_from_dict(d)


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_evaluation_record_rejects_whitespace_only_evaluator_version(whitespace_only: str) -> None:
    with pytest.raises(models.GenomeValidationError, match="evaluator.version must be a non-empty string"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="training", version=whitespace_only),
            axes={"naturalness": 0.8},
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_evaluation_record_from_dict_rejects_whitespace_only_evaluator_version(whitespace_only: str) -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["evaluator"]["version"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="evaluator.version must be a non-empty string"):
        models.evaluation_record_from_dict(d)


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_evaluation_record_rejects_whitespace_only_blind_batch_for_human_kind(
    whitespace_only: str,
) -> None:
    with pytest.raises(models.GenomeValidationError, match="blind_batch must be a non-empty string"):
        models.build_evaluation_record(
            genome_id="a" * 16, probe_set="d3-probe/0.1",
            evaluator=models.Evaluator(kind="human", version="v0"),
            axes={"naturalness": 0.8}, blind_batch=whitespace_only,
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_evaluation_record_from_dict_rejects_whitespace_only_blind_batch_for_human_kind(
    whitespace_only: str,
) -> None:
    d = models.evaluation_record_to_dict(_base_eval_record())
    d["evaluator"]["kind"] = "human"
    d["blind_batch"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="blind_batch must be a non-empty string"):
        models.evaluation_record_from_dict(d)


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


# --- PR #267 Codex R13 指摘2（P2）: HackRecord の空白のみ参照文字列 ---------
# symptom / evaluator_version / discovered_by も EvaluationRecord の
# probe_set/evaluator.version/blind_batch と同じ参照文字列ファミリーとして
# `_require_nonblank_str()` を共有する。


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_hack_record_rejects_whitespace_only_symptom(whitespace_only: str) -> None:
    with pytest.raises(models.GenomeValidationError, match="symptom must be a non-empty string"):
        models.build_hack_record(
            genome_id="a" * 16, symptom=whitespace_only, evaluator_version="v0",
            discovered_by="blind_batch_3",
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_hack_record_from_dict_rejects_whitespace_only_symptom(whitespace_only: str) -> None:
    d = models.hack_record_to_dict(_base_hack_record())
    d["symptom"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="symptom must be a non-empty string"):
        models.hack_record_from_dict(d)


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_hack_record_rejects_whitespace_only_evaluator_version(whitespace_only: str) -> None:
    with pytest.raises(models.GenomeValidationError, match="evaluator_version must be a non-empty string"):
        models.build_hack_record(
            genome_id="a" * 16, symptom="spectral spike exploit", evaluator_version=whitespace_only,
            discovered_by="blind_batch_3",
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_hack_record_from_dict_rejects_whitespace_only_evaluator_version(whitespace_only: str) -> None:
    d = models.hack_record_to_dict(_base_hack_record())
    d["evaluator_version"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="evaluator_version must be a non-empty string"):
        models.hack_record_from_dict(d)


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_build_hack_record_rejects_whitespace_only_discovered_by(whitespace_only: str) -> None:
    with pytest.raises(models.GenomeValidationError, match="discovered_by must be a non-empty string"):
        models.build_hack_record(
            genome_id="a" * 16, symptom="spectral spike exploit", evaluator_version="v0",
            discovered_by=whitespace_only,
        )


@pytest.mark.parametrize(
    "whitespace_only",
    [" ", "   ", "\t", "\n", "　", " \t　 "],
    ids=["single-space", "spaces", "tab", "newline", "fullwidth-space", "mixed"],
)
def test_hack_record_from_dict_rejects_whitespace_only_discovered_by(whitespace_only: str) -> None:
    d = models.hack_record_to_dict(_base_hack_record())
    d["discovered_by"] = whitespace_only
    with pytest.raises(models.GenomeValidationError, match="discovered_by must be a non-empty string"):
        models.hack_record_from_dict(d)
