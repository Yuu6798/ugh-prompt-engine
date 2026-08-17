"""test_forge_triangle.py — `s1_gate/forge_triangle.py`（S3 run 4 ゲート判定
材料④: 3 アンカー重心座標補間バッチ鍛造）のロジック層テスト。

**GPU 実測未実施**（`forge_triangle.py` 冒頭 docstring 参照）。実チェックポイント
非保有のため、以下は全て `tmp_path` に書き出した決定論的フェイク embed
（実 acoustic checkpoint 由来ではない）を入力にした「入出力契約 + ロジック層」
のテストである。

検証観点（Design Memo の要件を直接対応付け）:
- 重心座標の数学（頂点で該当アンカー再現・重み和1・負重み拒否）
- 封印会計（correspondence sha256 の再計算一致・候補ファイル名からの非漏洩）
- 台帳 shape が `test_genome_ledger_shape.py` の検証関数を通ること
  （検証ロジックを複製せず、同モジュールの関数をそのまま import して適用する）
- 決定論（同一 seed → 同一結果、異なる seed → 異なるブラインド割当）
- review #265（PR #265 Codex 第2波レビュー採用分）: P1 embed の形状・
  有限性検証（切り詰め embed 拒否・NaN/Inf embed 拒否）、P2 重心座標の
  非有限重み拒否（NaN/Inf 重み拒否）
- review #265 R3（PR #265 Codex 第3波レビュー採用分）: #8 バッチ成果物の
  原子公開（staging → swap、失敗時ロールバック・新旧混合の残留なし）、
  #9 candidate 出力が入力 embed を上書きし得る衝突の fail-closed 拒否、
  #10 `--perturb-magnitude` の非有限値・負値拒否
- review #265 R4（PR #265 Codex 第4波レビュー採用分）: #12 アンカー/prior
  embed の sha256 provenance（アンカーは公開台帳の `anchors` セクションへ、
  隠しコントロールは封印済み correspondence.json へのみ記録・ブラインド
  秘匿を維持）
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import forge_triangle as ft  # noqa: E402

_TESTS_DIR = Path(__file__).resolve().parent
_LEDGER_SHAPE_TEST_PATH = _TESTS_DIR / "test_genome_ledger_shape.py"


def _load_ledger_shape_checks():
    """`tests/test_genome_ledger_shape.py` を別名でロードし、その検証関数を
    そのまま呼べるようにする（S2 台帳の shape チェックロジックを複製せず
    再利用する。fixture 経由ではなく素の関数呼び出しなので `results_s2/
    genome_ledger.json` は一切読まれない）。"""
    spec = importlib.util.spec_from_file_location(
        "_genome_ledger_shape_checks_reused", _LEDGER_SHAPE_TEST_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- フェイク embed フィクスチャ ---------------------------------------------


def _write_fake_embed(path: Path, seed: int, dim: int = ft.EMBED_DIM) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vector = rng.standard_normal(dim).astype(np.float32)
    ft.write_embed_vector(path, vector)
    return vector


@pytest.fixture()
def fake_anchor_paths(tmp_path: Path) -> Dict[str, Path]:
    paths = {
        "ritsu": tmp_path / "s1_run4_acoustic_v1.ritsu.emb",
        "pjs": tmp_path / "s1_run4_acoustic_v1.pjs.emb",
        "user": tmp_path / "s1_run4_acoustic_v1.user.emb",
    }
    _write_fake_embed(paths["ritsu"], seed=101)
    _write_fake_embed(paths["pjs"], seed=202)
    _write_fake_embed(paths["user"], seed=303)
    return paths


@pytest.fixture()
def fake_anchors(fake_anchor_paths: Dict[str, Path]) -> Dict[str, np.ndarray]:
    return {name: ft.load_embed_vector(path) for name, path in fake_anchor_paths.items()}


# --- embed I/O 契約 -----------------------------------------------------------


def test_embed_roundtrip_preserves_bytes(tmp_path: Path) -> None:
    vector = np.random.default_rng(7).standard_normal(ft.EMBED_DIM).astype(np.float32)
    path = tmp_path / "x.emb"
    ft.write_embed_vector(path, vector)
    loaded = ft.load_embed_vector(path)
    assert np.array_equal(loaded, vector)


def test_load_embed_vector_with_sha_matches_independent_sha256(tmp_path: Path) -> None:
    import hashlib

    vector = np.random.default_rng(11).standard_normal(ft.EMBED_DIM).astype(np.float32)
    path = tmp_path / "y.emb"
    ft.write_embed_vector(path, vector)
    loaded, digest = ft.load_embed_vector_with_sha(path)
    assert np.array_equal(loaded, vector)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


# --- review #265 P1: embed の形状・有限性検証（fail-closed） ------------------


def test_load_embed_vector_rejects_truncated_float_aligned_length(tmp_path: Path) -> None:
    """383 要素分（float32 に整列した長さだが EMBED_DIM に満たない）の
    切り詰められた embed は拒否される（従来は無検証で shape (383,) のまま
    受理していた）。"""
    vector = np.random.default_rng(1).standard_normal(ft.EMBED_DIM - 1).astype(np.float32)
    path = tmp_path / "truncated.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match=f"expected exactly {ft.EMBED_DIM}"):
        ft.load_embed_vector(path)


def test_load_embed_vector_rejects_oversized_float_aligned_length(tmp_path: Path) -> None:
    vector = np.random.default_rng(2).standard_normal(ft.EMBED_DIM + 1).astype(np.float32)
    path = tmp_path / "oversized.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match=f"expected exactly {ft.EMBED_DIM}"):
        ft.load_embed_vector(path)


def test_load_embed_vector_rejects_nan_embed(tmp_path: Path) -> None:
    vector = np.random.default_rng(3).standard_normal(ft.EMBED_DIM).astype(np.float32)
    vector[5] = np.nan
    path = tmp_path / "nan.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match="non-finite"):
        ft.load_embed_vector(path)


def test_load_embed_vector_rejects_inf_embed(tmp_path: Path) -> None:
    vector = np.random.default_rng(4).standard_normal(ft.EMBED_DIM).astype(np.float32)
    vector[0] = np.inf
    path = tmp_path / "inf.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match="non-finite"):
        ft.load_embed_vector(path)


def test_load_embed_vector_with_sha_also_rejects_nan_embed(tmp_path: Path) -> None:
    """`load_embed_vector_with_sha`（アンカー読み込みの実経路）も同じ
    fail-closed 検証を通ること。"""
    vector = np.random.default_rng(5).standard_normal(ft.EMBED_DIM).astype(np.float32)
    vector[-1] = np.nan
    path = tmp_path / "nan_with_sha.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match="non-finite"):
        ft.load_embed_vector_with_sha(path)


def test_load_embed_vector_with_sha_also_rejects_truncated_embed(tmp_path: Path) -> None:
    vector = np.random.default_rng(6).standard_normal(ft.EMBED_DIM - 10).astype(np.float32)
    path = tmp_path / "truncated_with_sha.emb"
    path.write_bytes(vector.tobytes())
    with pytest.raises(ValueError, match=f"expected exactly {ft.EMBED_DIM}"):
        ft.load_embed_vector_with_sha(path)


def test_run_generate_rejects_nan_control_prior_embed(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """`--control-prior-embed` 経路（`_build_control_candidate` →
    `load_embed_vector`）でも P1 の検証が効くこと。"""
    import argparse

    bad_prior = tmp_path / "prior_nan.emb"
    prior_vector = np.random.default_rng(9).standard_normal(ft.EMBED_DIM).astype(np.float32)
    prior_vector[3] = np.nan
    bad_prior.write_bytes(prior_vector.tobytes())

    args = argparse.Namespace(
        cmd="generate",
        ritsu_embed=str(fake_anchor_paths["ritsu"]),
        pjs_embed=str(fake_anchor_paths["pjs"]),
        user_embed=str(fake_anchor_paths["user"]),
        out_dir=str(tmp_path / "out"),
        seed=42,
        batch_name="S3-batch1",
        date="2026-08-17",
        backbone_checkpoint="sha256:" + "4" * 64,
        weights=[(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)],
        voice_id_start=1,
        generation=1,
        rights_class=ft.RIGHTS_CLASS_TRIANGLE,
        control_kind="prior",
        control_anchor=None,
        control_prior_embed=str(bad_prior),
        control_prior_voice_id="VG-S2-004",
        perturb_magnitude=0.0,
        perturb_seed=None,
    )
    with pytest.raises(ValueError, match="non-finite"):
        ft.run_generate(args)


# --- 重心座標の数学 ------------------------------------------------------------


def test_barycentric_interpolate_reproduces_anchor_at_each_vertex(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    assert np.array_equal(ft.barycentric_interpolate(e_r, e_p, e_u, 1.0, 0.0, 0.0), e_r)
    assert np.array_equal(ft.barycentric_interpolate(e_r, e_p, e_u, 0.0, 1.0, 0.0), e_p)
    assert np.array_equal(ft.barycentric_interpolate(e_r, e_p, e_u, 0.0, 0.0, 1.0), e_u)


def test_barycentric_interpolate_centroid_is_mean_of_three_anchors(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    centroid = ft.barycentric_interpolate(e_r, e_p, e_u, 1 / 3, 1 / 3, 1 / 3)
    expected = (e_r + e_p + e_u) / 3.0
    assert np.allclose(centroid, expected, atol=1e-5)


@pytest.mark.parametrize(
    "w_r,w_p,w_u", [(0.5, 0.5, 0.0), (0.2, 0.3, 0.5), (0.0, 0.0, 1.0)]
)
def test_barycentric_interpolate_accepts_weights_summing_to_one(
    fake_anchors: Dict[str, np.ndarray], w_r: float, w_p: float, w_u: float
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    # 例外を投げなければ合格
    ft.barycentric_interpolate(e_r, e_p, e_u, w_r, w_p, w_u)


def test_barycentric_interpolate_rejects_weights_not_summing_to_one(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    with pytest.raises(ValueError, match="sum to 1"):
        ft.barycentric_interpolate(e_r, e_p, e_u, 0.5, 0.5, 0.5)


@pytest.mark.parametrize(
    "w_r,w_p,w_u", [(-0.1, 0.6, 0.5), (0.5, -0.5, 1.0), (1.5, -0.5, 0.0)]
)
def test_barycentric_interpolate_rejects_negative_weight(
    fake_anchors: Dict[str, np.ndarray], w_r: float, w_p: float, w_u: float
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    with pytest.raises(ValueError, match="negative weight"):
        ft.barycentric_interpolate(e_r, e_p, e_u, w_r, w_p, w_u)


# --- review #265 P2: 重心座標の非有限重み拒否（simplex 検査より先に判定） ----


@pytest.mark.parametrize(
    "w_r,w_p,w_u",
    [
        (float("nan"), 0.5, 0.5),
        (0.5, float("nan"), 0.5),
        (0.5, 0.5, float("nan")),
    ],
)
def test_barycentric_interpolate_rejects_nan_weight(
    fake_anchors: Dict[str, np.ndarray], w_r: float, w_p: float, w_u: float
) -> None:
    """`nan < -atol` も `abs(nan+...-1.0) > atol` も常に False で素通り
    していた穴の再現テスト（review #265 P2）。負重み・和=1 のどちらの
    エラーメッセージでもなく、非有限性のエラーで拒否されること。"""
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    with pytest.raises(ValueError, match="non-finite weight"):
        ft.barycentric_interpolate(e_r, e_p, e_u, w_r, w_p, w_u)


@pytest.mark.parametrize(
    "w_r,w_p,w_u",
    [
        (float("inf"), 0.5, 0.5),
        (float("-inf"), 0.5, 0.5),
        (0.5, float("inf"), 0.5),
    ],
)
def test_barycentric_interpolate_rejects_inf_weight(
    fake_anchors: Dict[str, np.ndarray], w_r: float, w_p: float, w_u: float
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    with pytest.raises(ValueError, match="non-finite weight"):
        ft.barycentric_interpolate(e_r, e_p, e_u, w_r, w_p, w_u)


def test_run_generate_rejects_nan_cli_weights(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """CLI 経路（`--weights nan,0.5,0.5` 相当）で `run_generate` へ渡した
    場合も P2 の検証で拒否されること。"""
    import argparse

    args = argparse.Namespace(
        cmd="generate",
        ritsu_embed=str(fake_anchor_paths["ritsu"]),
        pjs_embed=str(fake_anchor_paths["pjs"]),
        user_embed=str(fake_anchor_paths["user"]),
        out_dir=str(tmp_path / "out"),
        seed=42,
        batch_name="S3-batch1",
        date="2026-08-17",
        backbone_checkpoint="sha256:" + "5" * 64,
        weights=[(float("nan"), 0.5, 0.5), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)],
        voice_id_start=1,
        generation=1,
        rights_class=ft.RIGHTS_CLASS_TRIANGLE,
        control_kind="anchor",
        control_anchor="ritsu",
        control_prior_embed=None,
        control_prior_voice_id=None,
        perturb_magnitude=0.0,
        perturb_seed=None,
    )
    with pytest.raises(ValueError, match="non-finite weight"):
        ft.run_generate(args)


def test_orthogonal_perturbation_vector_is_orthogonal_to_span(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    e_r, e_p, e_u = fake_anchors["ritsu"], fake_anchors["pjs"], fake_anchors["user"]
    span = (e_p - e_r, e_u - e_r)
    direction = ft.orthogonal_perturbation_vector(span, ft.EMBED_DIM, seed=314159265)
    for s in span:
        assert abs(float(np.dot(direction.astype(np.float64), s.astype(np.float64)))) < 1e-3
    assert np.isclose(float(np.linalg.norm(direction)), 1.0, atol=1e-5)


# --- 封印会計 -----------------------------------------------------------------


def test_seal_and_verify_correspondence_round_trip(tmp_path: Path) -> None:
    correspondence = {"A": {"voice_id": "VG-S3-001"}, "B": {"voice_id": "VG-S3-002"}}
    correspondence_path, sha_path, digest = ft.seal_correspondence(correspondence, tmp_path)
    assert correspondence_path.exists()
    assert sha_path.exists()
    assert ft.verify_sealed_correspondence(correspondence_path, sha_path) is True
    assert sha_path.read_text(encoding="utf-8").strip() == digest


def test_verify_sealed_correspondence_detects_tampering(tmp_path: Path) -> None:
    correspondence = {"A": {"voice_id": "VG-S3-001"}}
    correspondence_path, sha_path, _digest = ft.seal_correspondence(correspondence, tmp_path)
    # 改変
    correspondence_path.write_text('{"A": {"voice_id": "TAMPERED"}}', encoding="utf-8")
    assert ft.verify_sealed_correspondence(correspondence_path, sha_path) is False


def test_sha_file_contains_only_the_digest_not_the_content(tmp_path: Path) -> None:
    correspondence = {"A": {"voice_id": "VG-S3-001", "method": "barycentric"}}
    _correspondence_path, sha_path, digest = ft.seal_correspondence(correspondence, tmp_path)
    content = sha_path.read_text(encoding="utf-8").strip()
    assert content == digest
    assert "voice_id" not in content
    assert "barycentric" not in content


# --- ブラインドバッチ・候補ファイル名の非漏洩 ---------------------------------


def _build_full_batch(fake_anchors: Dict[str, np.ndarray], *, seed: int = 42) -> ft.BlindBatch:
    interior = [
        ft.make_interior_candidate(
            fake_anchors, voice_id=f"VG-S3-{i:03d}", w_r=w_r, w_p=w_p, w_u=w_u, seed=seed,
        )
        for i, (w_r, w_p, w_u) in enumerate(
            [(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)], start=1
        )
    ]
    control = ft.make_hidden_control_from_anchor(
        fake_anchors, "ritsu", voice_id="VG-S3-004", seed=seed,
    )
    return ft.build_blind_batch(interior + [control], shuffle_seed=seed)


def test_build_blind_batch_requires_exactly_four_candidates(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    interior = [
        ft.make_interior_candidate(
            fake_anchors, voice_id="VG-S3-001", w_r=1.0, w_p=0.0, w_u=0.0, seed=1,
        )
    ]
    with pytest.raises(ValueError, match="exactly 4"):
        ft.build_blind_batch(interior, shuffle_seed=1)


def test_build_blind_batch_covers_all_four_labels(fake_anchors: Dict[str, np.ndarray]) -> None:
    batch = _build_full_batch(fake_anchors)
    assert set(batch.label_to_candidate.keys()) == set(ft.BLIND_LABELS)


def test_write_blind_candidate_embeds_filenames_do_not_leak_correspondence(
    tmp_path: Path, fake_anchors: Dict[str, np.ndarray]
) -> None:
    batch = _build_full_batch(fake_anchors)
    paths = ft.write_blind_candidate_embeds(batch, tmp_path)

    assert set(paths.keys()) == set(ft.BLIND_LABELS)
    for label, cand in batch.label_to_candidate.items():
        filename = paths[label].name
        assert filename == f"candidate_{label}.emb"
        # voice_id・method・重み等の識別情報がファイル名に含まれないこと
        # （開封前に対応が漏れない命名）
        assert cand.voice_id not in filename
        assert cand.method not in filename
        for other_label in ft.BLIND_LABELS:
            if other_label != label:
                assert other_label not in filename.replace(f"candidate_{label}", "")

    # 書き出したバイト列は対応する候補の embed と一致する（ブラインドでも
    # データそのものは正しい候補を指す）
    for label, cand in batch.label_to_candidate.items():
        loaded = ft.load_embed_vector(paths[label])
        assert np.array_equal(loaded, cand.vector)


def test_hidden_control_from_anchor_has_no_weights_and_is_flagged() -> None:
    fake = {name: np.zeros(ft.EMBED_DIM, dtype=np.float32) for name in ft.ANCHOR_NAMES}
    control = ft.make_hidden_control_from_anchor(fake, "pjs", voice_id="VG-S3-004", seed=1)
    assert control.is_hidden_control is True
    assert control.weights is None
    assert control.parent_ids == ("VG-S3-ANCHOR-PJS",)


def test_hidden_control_from_prior_seals_prior_voice_id_only_in_parent_ids() -> None:
    prior_vector = np.ones(ft.EMBED_DIM, dtype=np.float32)
    control = ft.make_hidden_control_from_prior(
        prior_vector, voice_id="VG-S3-004", prior_voice_id="VG-S2-004", seed=1,
    )
    assert control.is_hidden_control is True
    assert control.parent_ids == ("VG-S2-004",)
    assert control.weights is None


# --- genome 台帳 shape（test_genome_ledger_shape.py の検証関数を再利用） -----


def _build_ledger(fake_anchors: Dict[str, np.ndarray]) -> Dict[str, Any]:
    batch = _build_full_batch(fake_anchors)
    candidates = list(batch.label_to_candidate.values())
    return ft.build_genome_ledger(
        candidates, fake_anchors,
        batch="S3-batch1", date="2026-08-17",
        backbone_checkpoint="sha256:" + "0" * 64,
    )


def test_genome_ledger_passes_reused_s2_shape_checks(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    """`tests/test_genome_ledger_shape.py` の検証関数をそのまま我々の台帳へ
    適用する（S2 固有の voice_id 順序・アンカー2本前提のテストは対象外 —
    それらは「S2 の内容」を検証するものであり「台帳の形状」検証ではないため
    除外する。除外する2関数の理由は個別に明記する）。"""
    ledger = _build_ledger(fake_anchors)
    checks = _load_ledger_shape_checks()

    checks.test_ledger_is_valid_json_object(ledger)
    checks.test_top_level_required_keys_present(ledger)
    checks.test_schema_is_pinned_version(ledger)
    checks.test_genomes_is_nonempty_list(ledger)
    checks.test_every_genome_has_required_keys(ledger)
    checks.test_every_genome_has_minimal_field_types(ledger)
    checks.test_voice_id_is_unique(ledger)
    # 対象外（S2 固有の内容検証、台帳の形状とは無関係）:
    # - test_vg_s2_001_through_010_all_present_in_exact_order:
    #   VG-S2-001..010 という S2 固有の voice_id 列を厳密一致で要求する
    #   （run 4 は VG-S3-xxx 系列のため必然的に不一致になる）
    # - test_anchors_section_has_both_anchor_embeddings:
    #   anchors セクションが VG-S2-ANCHOR-RITSU/PJS の 2 本のみであることを
    #   要求する（run 4 は 3 アンカーのため必然的に不一致になる。3 アンカー
    #   版の同等チェックは下記 test_anchor_section_has_all_three_anchors）


def test_genome_ledger_is_json_serializable_round_trip(
    tmp_path: Path, fake_anchors: Dict[str, np.ndarray]
) -> None:
    ledger = _build_ledger(fake_anchors)
    path = tmp_path / "genome_ledger_run4.json"
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == ledger


def test_genome_ledger_voice_id_prefix_is_vg_s3_not_vg_s2(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    ledger = _build_ledger(fake_anchors)
    for genome in ledger["genomes"]:
        assert genome["voice_id"].startswith("VG-S3-")


def test_genome_ledger_hidden_control_entry_has_null_identity_latent_ref(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    ledger = _build_ledger(fake_anchors)
    hidden = [g for g in ledger["genomes"] if g["identity"]["method"] == "hidden_anchor_control"]
    assert len(hidden) == 1
    assert hidden[0]["identity_latent_ref"] is None


def test_genome_ledger_interior_entries_have_blob_sha256_identity_latent_ref(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    ledger = _build_ledger(fake_anchors)
    interior = [g for g in ledger["genomes"] if g["identity"]["method"] == "barycentric"]
    assert len(interior) == 3
    for g in interior:
        assert g["identity_latent_ref"].startswith("blob:sha256:")


def test_anchor_section_has_all_three_anchors(fake_anchors: Dict[str, np.ndarray]) -> None:
    ledger = _build_ledger(fake_anchors)
    anchors_section = ledger["anchors"]
    for key in ("VG-S3-ANCHOR-RITSU", "VG-S3-ANCHOR-PJS", "VG-S3-ANCHOR-USER"):
        assert key in anchors_section
        assert isinstance(anchors_section[key]["embed_l2_norm"], float)
    pairwise = anchors_section["anchor_pairwise_cosine_similarity"]
    assert set(pairwise.keys()) == {"ritsu_pjs", "ritsu_user", "pjs_user"}


# --- review #265 R4 #12: アンカー/prior embed の sha256 provenance ------------


def test_anchor_section_omits_embed_sha256_when_anchor_shas_not_provided(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    """後方互換: `anchor_shas` を渡さない既存呼び出し（`_build_ledger` 等）は
    従来どおり `embed_sha256` キー無しのまま。"""
    section = ft.build_anchor_section(fake_anchors)
    for key in ("VG-S3-ANCHOR-RITSU", "VG-S3-ANCHOR-PJS", "VG-S3-ANCHOR-USER"):
        assert "embed_sha256" not in section[key]


def test_anchor_section_includes_embed_sha256_when_anchor_shas_provided(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    anchor_shas = {name: ft.vector_sha256(vec) for name, vec in fake_anchors.items()}
    section = ft.build_anchor_section(fake_anchors, anchor_shas)
    for name, voice_id in ft.ANCHOR_VOICE_IDS.items():
        assert section[voice_id]["embed_sha256"] == anchor_shas[name]


def test_run_generate_records_anchor_embed_sha256_matching_independent_recompute(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """genome 台帳の `anchors` セクションに記録された sha256 が、入力ファイル
    の独立再計算 sha256 と完全一致すること（review #265 R4 #12 の受け入れ
    条件そのもの）。"""
    import hashlib

    args = _make_generate_args(tmp_path, fake_anchor_paths)
    result = ft.run_generate(args)

    anchors_section = result["ledger"]["anchors"]
    for name, path in fake_anchor_paths.items():
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        voice_id = ft.ANCHOR_VOICE_IDS[name]
        assert anchors_section[voice_id]["embed_sha256"] == expected


def test_run_generate_records_prior_control_source_embed_sha256_in_sealed_correspondence_only(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """prior control の入力ファイル sha256 は封印済み correspondence.json
    にのみ記録され、公開 genome 台帳（genome_entry）には一切現れないこと
    （ブラインド秘匿の維持）。"""
    import hashlib

    prior_embed = tmp_path / "prior_individual.emb"
    prior_vector = np.random.default_rng(55).standard_normal(ft.EMBED_DIM).astype(np.float32)
    ft.write_embed_vector(prior_embed, prior_vector)
    expected_sha = hashlib.sha256(prior_embed.read_bytes()).hexdigest()

    args = _make_generate_args(
        tmp_path, fake_anchor_paths,
        control_kind="prior", control_anchor=None,
        control_prior_embed=str(prior_embed), control_prior_voice_id="VG-S2-004",
    )
    result = ft.run_generate(args)

    # 公開台帳: どの genome エントリにも source_embed_sha256 が漏れていない
    for genome in result["ledger"]["genomes"]:
        assert "source_embed_sha256" not in genome
        assert "source_embed_sha256" not in genome["identity"]

    # 封印済み correspondence.json: 該当ラベルに sha256 が記録されている
    correspondence = json.loads(result["correspondence_path"].read_text(encoding="utf-8"))
    matches = [
        entry for entry in correspondence.values()
        if entry.get("is_hidden_control") is True
    ]
    assert len(matches) == 1
    assert matches[0]["source_embed_sha256"] == expected_sha


def test_run_generate_records_anchor_control_source_embed_sha256_in_sealed_correspondence_only(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    import hashlib

    args = _make_generate_args(
        tmp_path, fake_anchor_paths, control_kind="anchor", control_anchor="pjs",
    )
    result = ft.run_generate(args)

    for genome in result["ledger"]["genomes"]:
        assert "source_embed_sha256" not in genome
        assert "source_embed_sha256" not in genome["identity"]

    expected_sha = hashlib.sha256(fake_anchor_paths["pjs"].read_bytes()).hexdigest()
    correspondence = json.loads(result["correspondence_path"].read_text(encoding="utf-8"))
    matches = [
        entry for entry in correspondence.values()
        if entry.get("is_hidden_control") is True
    ]
    assert len(matches) == 1
    assert matches[0]["source_embed_sha256"] == expected_sha


def test_correspondence_interior_candidates_have_null_source_embed_sha256(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """interior/barycentric candidate は単一の「入力ファイル」概念が無いため
    `source_embed_sha256` は None のまま（3 アンカー自身の sha は
    `anchors` セクションが別途担う）。"""
    args = _make_generate_args(tmp_path, fake_anchor_paths)
    result = ft.run_generate(args)

    correspondence = json.loads(result["correspondence_path"].read_text(encoding="utf-8"))
    non_control_entries = [
        entry for entry in correspondence.values() if entry.get("is_hidden_control") is False
    ]
    assert len(non_control_entries) == 3
    for entry in non_control_entries:
        assert entry["source_embed_sha256"] is None


def test_make_hidden_control_from_anchor_without_anchor_shas_leaves_source_sha_none() -> None:
    """`anchor_shas` 省略時は従来どおり `source_embed_sha256=None`（後方互換）。"""
    fake = {name: np.zeros(ft.EMBED_DIM, dtype=np.float32) for name in ft.ANCHOR_NAMES}
    control = ft.make_hidden_control_from_anchor(fake, "ritsu", voice_id="VG-S3-004", seed=1)
    assert control.source_embed_sha256 is None


def test_genome_ledger_still_passes_reused_s2_shape_checks_with_anchor_shas(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    """`anchor_shas`/`source_embed_sha256` の追加が既存 shape 検証
    （test_genome_ledger_shape.py の必須キー集合）を壊さないこと。"""
    batch = _build_full_batch(fake_anchors)
    candidates = list(batch.label_to_candidate.values())
    anchor_shas = {name: ft.vector_sha256(vec) for name, vec in fake_anchors.items()}
    ledger = ft.build_genome_ledger(
        candidates, fake_anchors,
        batch="S3-batch1", date="2026-08-17",
        backbone_checkpoint="sha256:" + "0" * 64,
        anchor_shas=anchor_shas,
    )
    checks = _load_ledger_shape_checks()
    checks.test_top_level_required_keys_present(ledger)
    checks.test_every_genome_has_required_keys(ledger)
    checks.test_every_genome_has_minimal_field_types(ledger)
    checks.test_voice_id_is_unique(ledger)


# --- 決定論 -------------------------------------------------------------------


def test_same_seed_produces_identical_blind_batch_assignment(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    batch1 = _build_full_batch(fake_anchors, seed=7)
    batch2 = _build_full_batch(fake_anchors, seed=7)
    order1 = {label: cand.voice_id for label, cand in batch1.label_to_candidate.items()}
    order2 = {label: cand.voice_id for label, cand in batch2.label_to_candidate.items()}
    assert order1 == order2


def test_different_seeds_can_produce_different_blind_batch_assignment(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    batch1 = _build_full_batch(fake_anchors, seed=1)
    batch2 = _build_full_batch(fake_anchors, seed=2)
    order1 = {label: cand.voice_id for label, cand in batch1.label_to_candidate.items()}
    order2 = {label: cand.voice_id for label, cand in batch2.label_to_candidate.items()}
    assert order1 != order2


def test_same_seed_produces_identical_orthogonal_perturbation_vector(
    fake_anchors: Dict[str, np.ndarray]
) -> None:
    span = (fake_anchors["pjs"] - fake_anchors["ritsu"], fake_anchors["user"] - fake_anchors["ritsu"])
    v1 = ft.orthogonal_perturbation_vector(span, ft.EMBED_DIM, seed=999)
    v2 = ft.orthogonal_perturbation_vector(span, ft.EMBED_DIM, seed=999)
    assert np.array_equal(v1, v2)


def test_run_generate_is_deterministic_end_to_end(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """CLI 経路（`run_generate`）を 2 回、別ディレクトリで独立実行し、
    候補 embed バイト列・genome 台帳が完全一致することを確認する
    （S2 の「独立した2回の呼び出しで sha256 完全一致」慣行と同型の検証）。"""
    import argparse

    def make_args(out_dir: Path) -> argparse.Namespace:
        return argparse.Namespace(
            cmd="generate",
            ritsu_embed=str(fake_anchor_paths["ritsu"]),
            pjs_embed=str(fake_anchor_paths["pjs"]),
            user_embed=str(fake_anchor_paths["user"]),
            out_dir=str(out_dir),
            seed=42,
            batch_name="S3-batch1",
            date="2026-08-17",
            backbone_checkpoint="sha256:" + "1" * 64,
            weights=[(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)],
            voice_id_start=1,
            generation=1,
            rights_class=ft.RIGHTS_CLASS_TRIANGLE,
            control_kind="anchor",
            control_anchor="ritsu",
            control_prior_embed=None,
            control_prior_voice_id=None,
            perturb_magnitude=0.0,
            perturb_seed=None,
        )

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    result1 = ft.run_generate(make_args(out1))
    result2 = ft.run_generate(make_args(out2))

    assert result1["ledger"] == result2["ledger"]
    for label in ft.BLIND_LABELS:
        bytes1 = result1["embed_paths"][label].read_bytes()
        bytes2 = result2["embed_paths"][label].read_bytes()
        assert bytes1 == bytes2


def test_run_generate_writes_all_expected_artifacts(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    import argparse

    args = argparse.Namespace(
        cmd="generate",
        ritsu_embed=str(fake_anchor_paths["ritsu"]),
        pjs_embed=str(fake_anchor_paths["pjs"]),
        user_embed=str(fake_anchor_paths["user"]),
        out_dir=str(tmp_path / "out"),
        seed=42,
        batch_name="S3-batch1",
        date="2026-08-17",
        backbone_checkpoint="sha256:" + "2" * 64,
        weights=[(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)],
        voice_id_start=1,
        generation=1,
        rights_class=ft.RIGHTS_CLASS_TRIANGLE,
        control_kind="prior",
        control_anchor=None,
        control_prior_embed=str(fake_anchor_paths["pjs"]),
        control_prior_voice_id="VG-S2-004",
        perturb_magnitude=0.5,
        perturb_seed=13,
    )
    result = ft.run_generate(args)

    assert result["correspondence_path"].exists()
    assert result["sha_path"].exists()
    assert result["ledger_path"].exists()
    for label in ft.BLIND_LABELS:
        assert result["embed_paths"][label].exists()
    assert ft.verify_sealed_correspondence(result["correspondence_path"], result["sha_path"])


def test_run_generate_requires_exactly_three_weight_triples(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    import argparse

    args = argparse.Namespace(
        cmd="generate",
        ritsu_embed=str(fake_anchor_paths["ritsu"]),
        pjs_embed=str(fake_anchor_paths["pjs"]),
        user_embed=str(fake_anchor_paths["user"]),
        out_dir=str(tmp_path / "out"),
        seed=42,
        batch_name="S3-batch1",
        date="2026-08-17",
        backbone_checkpoint="sha256:" + "3" * 64,
        weights=[(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        voice_id_start=1,
        generation=1,
        rights_class=ft.RIGHTS_CLASS_TRIANGLE,
        control_kind="anchor",
        control_anchor="ritsu",
        control_prior_embed=None,
        control_prior_voice_id=None,
        perturb_magnitude=0.0,
        perturb_seed=None,
    )
    with pytest.raises(SystemExit, match="exactly 3"):
        ft.run_generate(args)


# --- review #265 R3: バッチ成果物の原子公開・入力衝突・perturb-magnitude ------


def _make_generate_args(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path], *,
    out_dir: Optional[Path] = None,
    control_kind: str = "anchor",
    control_anchor: Optional[str] = "ritsu",
    control_prior_embed: Optional[str] = None,
    control_prior_voice_id: Optional[str] = None,
    perturb_magnitude: float = 0.0,
    perturb_seed: Optional[int] = None,
    backbone_suffix: str = "6",
    seed: int = 42,
):
    import argparse

    return argparse.Namespace(
        cmd="generate",
        ritsu_embed=str(fake_anchor_paths["ritsu"]),
        pjs_embed=str(fake_anchor_paths["pjs"]),
        user_embed=str(fake_anchor_paths["user"]),
        out_dir=str(out_dir if out_dir is not None else tmp_path / "out"),
        seed=seed,
        batch_name="S3-batch1",
        date="2026-08-17",
        backbone_checkpoint="sha256:" + backbone_suffix * 64,
        weights=[(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)],
        voice_id_start=1,
        generation=1,
        rights_class=ft.RIGHTS_CLASS_TRIANGLE,
        control_kind=control_kind,
        control_anchor=control_anchor,
        control_prior_embed=control_prior_embed,
        control_prior_voice_id=control_prior_voice_id,
        perturb_magnitude=perturb_magnitude,
        perturb_seed=perturb_seed,
    )


# --- #8: 原子公開（staging → swap、失敗時ロールバック） ------------------------


def test_run_generate_writes_via_staging_directory_that_does_not_survive(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """成功時、`<out_dir>.staging-<pid>` は swap 後に消えている（rename 先の
    `out_dir` へ吸収される）こと。"""
    import os

    out_dir = tmp_path / "out"
    args = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=out_dir)
    ft.run_generate(args)

    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    assert not staging_dir.exists()
    assert out_dir.exists()
    for label in ft.BLIND_LABELS:
        assert (out_dir / f"candidate_{label}.emb").exists()
    assert (out_dir / "correspondence.json").exists()
    assert (out_dir / "genome_ledger_run4.json").exists()


def test_run_generate_rolls_back_staging_on_mid_publish_failure_and_leaves_out_dir_untouched(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """staging 構築の途中（genome 台帳書き出し）で例外を起こすと、staging
    ディレクトリは掃除され、既存の `out_dir`（マーカーファイル入り）は
    無傷のまま残ること — 新旧混合の出力ディレクトリを作らない（#8）。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    marker = out_dir / "marker_from_previous_batch.txt"
    marker.write_text("previous batch content", encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-publish failure")

    monkeypatch.setattr(ft, "build_genome_ledger", _boom)

    args = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=out_dir)
    with pytest.raises(RuntimeError, match="simulated mid-publish failure"):
        ft.run_generate(args)

    # out_dir はロールバックされず「失敗前のまま」= マーカーのみ、新規成果物は無い
    assert marker.read_text(encoding="utf-8") == "previous batch content"
    assert not (out_dir / "genome_ledger_run4.json").exists()
    assert not (out_dir / "candidate_A.emb").exists()

    # staging・.old のいずれも残留しない（掃除済み。fake_anchor_paths が
    # tmp_path 直下へ書く 3 本の anchor embed は無関係の正当な入力なので
    # 対象外にする）
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name not in ("out",) and (p.name.startswith("out.staging-") or p.name == "out.old")
    ]
    assert leftovers == [], f"unexpected leftover staging/.old paths: {leftovers}"


def test_run_generate_publish_failure_does_not_create_out_dir_when_absent(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`out_dir` が未作成の状態で公開に失敗した場合、`out_dir` 自体が
    作られてしまわないこと（部分成果物の漏れ出しが無い）。"""
    out_dir = tmp_path / "never_created"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure before swap")

    monkeypatch.setattr(ft, "write_blind_candidate_embeds", _boom)

    args = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=out_dir)
    with pytest.raises(RuntimeError, match="simulated failure before swap"):
        ft.run_generate(args)

    assert not out_dir.exists()


def test_run_generate_second_successful_run_fully_replaces_first_no_mixed_residue(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """同一 `out_dir` へ 2 回連続で成功公開すると、`out_dir` は 2 回目の
    成果物のみを持ち（1 回目の成果物と混在しない）、1 回目の内容は
    `<out_dir>.old` へ丸ごと退避されること。"""
    out_dir = tmp_path / "out"

    args1 = _make_generate_args(
        tmp_path, fake_anchor_paths, out_dir=out_dir, seed=1, backbone_suffix="1",
    )
    result1 = ft.run_generate(args1)
    first_ledger = json.loads(result1["ledger_path"].read_text(encoding="utf-8"))

    args2 = _make_generate_args(
        tmp_path, fake_anchor_paths, out_dir=out_dir, seed=2, backbone_suffix="2",
    )
    result2 = ft.run_generate(args2)
    second_ledger = json.loads(result2["ledger_path"].read_text(encoding="utf-8"))

    assert first_ledger != second_ledger  # 異なる seed → 異なるブラインド割当（実測で確認）
    # out_dir の genome ledger は 2 回目の内容のみ（1 回目と混在しない）
    current_ledger = json.loads((out_dir / "genome_ledger_run4.json").read_text(encoding="utf-8"))
    assert current_ledger == second_ledger

    old_dir = out_dir.parent / f"{out_dir.name}.old"
    assert old_dir.exists()
    old_ledger = json.loads((old_dir / "genome_ledger_run4.json").read_text(encoding="utf-8"))
    assert old_ledger == first_ledger


# --- #9: candidate 出力による入力 embed の上書き衝突（fail-closed） ------------


def test_reject_embed_input_collisions_raises_when_input_exactly_inside_managed_dir(
    tmp_path: Path
) -> None:
    managed_dir = tmp_path / "out"
    managed_dir.mkdir()
    leaked_input = managed_dir / "candidate_A.emb"
    leaked_input.write_bytes(b"\x00" * (ft.EMBED_DIM * 4))

    with pytest.raises(ft.convert_d3.OutputCollisionError, match="inside"):
        ft._reject_embed_input_collisions(managed_dir, [leaked_input])


def test_reject_embed_input_collisions_raises_when_input_nested_deeper_inside(
    tmp_path: Path
) -> None:
    managed_dir = tmp_path / "out"
    nested = managed_dir / "some" / "nested" / "dir"
    nested.mkdir(parents=True)
    leaked_input = nested / "prior_backup.emb"
    leaked_input.write_bytes(b"\x00" * (ft.EMBED_DIM * 4))

    with pytest.raises(ft.convert_d3.OutputCollisionError, match="inside"):
        ft._reject_embed_input_collisions(managed_dir, [leaked_input])


def test_reject_embed_input_collisions_raises_when_input_equals_managed_dir(
    tmp_path: Path
) -> None:
    # managed_dir と入力パスが同一パスというありえない構成でも拒否する
    managed_dir = tmp_path / "out"
    managed_dir.mkdir()
    with pytest.raises(ft.convert_d3.OutputCollisionError):
        ft._reject_embed_input_collisions(managed_dir, [managed_dir])


def test_reject_embed_input_collisions_allows_sibling_input_outside_managed_dir(
    tmp_path: Path
) -> None:
    managed_dir = tmp_path / "out"
    managed_dir.mkdir()
    sibling_input = tmp_path / "anchors" / "ritsu.emb"
    sibling_input.parent.mkdir(parents=True)
    sibling_input.write_bytes(b"\x00" * (ft.EMBED_DIM * 4))

    ft._reject_embed_input_collisions(managed_dir, [sibling_input])  # 例外なし = 合格


def test_reject_embed_input_collisions_skips_none_and_nonexistent_entries(
    tmp_path: Path
) -> None:
    managed_dir = tmp_path / "out"
    managed_dir.mkdir()
    ft._reject_embed_input_collisions(managed_dir, [None, tmp_path / "does_not_exist.emb"])


def test_reject_embed_input_collisions_noop_when_managed_dir_does_not_exist_yet(
    tmp_path: Path
) -> None:
    managed_dir = tmp_path / "not_yet_created"
    some_input = tmp_path / "x.emb"
    some_input.write_bytes(b"\x00" * (ft.EMBED_DIM * 4))
    ft._reject_embed_input_collisions(managed_dir, [some_input])  # 例外なし = 合格


def test_run_generate_rejects_control_prior_embed_that_is_a_leftover_candidate_in_same_out_dir(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """review #265 R3 #9 の実害例そのもの: 前回バッチの `candidate_A.emb`
    を次回バッチの `--control-prior-embed` として同一 `--out-dir` で渡すと
    fail-closed で拒否され、何も書き出されないこと。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    leftover_candidate = out_dir / "candidate_A.emb"
    leftover_vector = np.random.default_rng(77).standard_normal(ft.EMBED_DIM).astype(np.float32)
    ft.write_embed_vector(leftover_candidate, leftover_vector)

    args = _make_generate_args(
        tmp_path, fake_anchor_paths, out_dir=out_dir,
        control_kind="prior", control_anchor=None,
        control_prior_embed=str(leftover_candidate), control_prior_voice_id="VG-S2-004",
    )
    with pytest.raises(ft.convert_d3.OutputCollisionError, match="inside"):
        ft.run_generate(args)

    # 拒否前の状態のまま（新規成果物は一切書き出されない）
    assert not (out_dir / "genome_ledger_run4.json").exists()
    assert leftover_candidate.read_bytes() == leftover_vector.tobytes()


def test_run_generate_rejects_ritsu_embed_placed_inside_out_dir(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ritsu_inside = out_dir / "anchor_ritsu_copy.emb"
    ritsu_inside.write_bytes(fake_anchor_paths["ritsu"].read_bytes())

    args = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=out_dir)
    args.ritsu_embed = str(ritsu_inside)
    with pytest.raises(ft.convert_d3.OutputCollisionError, match="inside"):
        ft.run_generate(args)


# --- #10: --perturb-magnitude の非有限値・負値拒否 -----------------------------


@pytest.mark.parametrize("bad_magnitude", [float("nan"), float("inf"), float("-inf")])
def test_run_generate_rejects_non_finite_perturb_magnitude(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path], bad_magnitude: float
) -> None:
    args = _make_generate_args(
        tmp_path, fake_anchor_paths, perturb_magnitude=bad_magnitude, perturb_seed=1,
    )
    with pytest.raises(SystemExit, match="finite"):
        ft.run_generate(args)


def test_run_generate_rejects_negative_perturb_magnitude(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    args = _make_generate_args(
        tmp_path, fake_anchor_paths, perturb_magnitude=-0.5, perturb_seed=1,
    )
    with pytest.raises(SystemExit, match="non-negative"):
        ft.run_generate(args)


def test_run_generate_accepts_zero_perturb_magnitude_without_perturb_seed(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    args = _make_generate_args(tmp_path, fake_anchor_paths, perturb_magnitude=0.0, perturb_seed=None)
    result = ft.run_generate(args)  # 例外なし = 合格
    assert result["ledger_path"].exists()


def test_run_generate_accepts_finite_positive_perturb_magnitude_with_seed(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    args = _make_generate_args(
        tmp_path, fake_anchor_paths, perturb_magnitude=0.25, perturb_seed=13,
    )
    result = ft.run_generate(args)  # 例外なし = 合格
    assert result["ledger_path"].exists()


@pytest.mark.parametrize("bad_magnitude", [float("nan"), float("inf")])
def test_apply_perturbation_rejects_non_finite_magnitude_at_the_point_of_use(
    bad_magnitude: float,
) -> None:
    base = np.zeros(ft.EMBED_DIM, dtype=np.float32)
    direction = np.ones(ft.EMBED_DIM, dtype=np.float32) / math.sqrt(ft.EMBED_DIM)
    with pytest.raises(ValueError, match="non-finite"):
        ft.apply_perturbation(base, direction, bad_magnitude)


def test_apply_perturbation_rejects_negative_magnitude_at_the_point_of_use() -> None:
    base = np.zeros(ft.EMBED_DIM, dtype=np.float32)
    direction = np.ones(ft.EMBED_DIM, dtype=np.float32) / math.sqrt(ft.EMBED_DIM)
    with pytest.raises(ValueError, match="negative"):
        ft.apply_perturbation(base, direction, -1.0)


# --- review #265 R6 #15: float32 表現可能性 + 出力境界検証 ---------------------


def test_run_generate_rejects_perturb_magnitude_that_is_finite_float64_but_overflows_float32(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """`math.isfinite(1e300)` は True（float64 として有限）だが、
    `np.float32(1e300)` は inf 化する（spk_embed は float32）。入口の早期
    チェックがこれを拒否すること（主たる防御は出力境界検証 — 下記
    `test_write_blind_candidate_embeds_*` 参照）。"""
    assert math.isfinite(1e300)  # 前提の再確認（float64 としては有限）
    assert not np.isfinite(np.float32(1e300))  # float32 では inf 化する

    args = _make_generate_args(
        tmp_path, fake_anchor_paths, perturb_magnitude=1e300, perturb_seed=1,
    )
    with pytest.raises(SystemExit, match="float32"):
        ft.run_generate(args)


def test_write_blind_candidate_embeds_rejects_non_finite_candidate_vector_at_output_boundary(
    tmp_path: Path,
) -> None:
    """review #265 R6 #15 の核心テスト:「出力境界検証により非有限 .emb の
    公開が構造的に不可能になった」ことの直接証明。入口検証を全て素通りした
    と仮定して（`GenomeCandidate` を直接構築し non-finite ベクトルを注入 —
    実際の CLI 経路が生成しうるかどうかに依存しない）、`write_blind_
    candidate_embeds` 自身が publish 直前に拒否することを確認する。
    """
    good_vector = np.zeros(ft.EMBED_DIM, dtype=np.float32)
    bad_vector = np.zeros(ft.EMBED_DIM, dtype=np.float32)
    bad_vector[0] = np.inf  # 何らかの経路（将来の新しい摂動方式・演算バグ等）で紛れ込んだと仮定

    def _cand(voice_id: str, vector: np.ndarray) -> ft.GenomeCandidate:
        return ft.GenomeCandidate(
            voice_id=voice_id, vector=vector, method="test", weights=None,
            mutation_ops=["TEST"], seed=1, rights_class="TEST", note="",
            parent_ids=("VG-S3-ANCHOR-RITSU",),
        )

    candidates = [
        _cand("VG-S3-901", good_vector), _cand("VG-S3-902", good_vector),
        _cand("VG-S3-903", bad_vector), _cand("VG-S3-904", good_vector),
    ]
    batch = ft.build_blind_batch(candidates, shuffle_seed=1)

    with pytest.raises(ValueError, match="non-finite"):
        ft.write_blind_candidate_embeds(batch, tmp_path)


def test_write_blind_candidate_embeds_rejects_wrong_shape_candidate_vector_at_output_boundary(
    tmp_path: Path,
) -> None:
    """shape 崩れ（384 以外の次元）も同じ出力境界検証で拒否されること。"""
    wrong_shape_vector = np.zeros(ft.EMBED_DIM - 1, dtype=np.float32)

    def _cand(voice_id: str, vector: np.ndarray) -> ft.GenomeCandidate:
        return ft.GenomeCandidate(
            voice_id=voice_id, vector=vector, method="test", weights=None,
            mutation_ops=["TEST"], seed=1, rights_class="TEST", note="",
            parent_ids=("VG-S3-ANCHOR-RITSU",),
        )

    good_vector = np.zeros(ft.EMBED_DIM, dtype=np.float32)
    candidates = [
        _cand("VG-S3-905", wrong_shape_vector), _cand("VG-S3-906", good_vector),
        _cand("VG-S3-907", good_vector), _cand("VG-S3-908", good_vector),
    ]
    batch = ft.build_blind_batch(candidates, shuffle_seed=2)

    with pytest.raises(ValueError, match="expected exactly"):
        ft.write_blind_candidate_embeds(batch, tmp_path)


def test_run_generate_end_to_end_never_publishes_a_non_finite_candidate_embed(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """`run_generate` を極端な（だが float32 上限未満の）摂動 magnitude で
    実行しても、書き出された全 `.emb` は有限のまま — 出力境界検証が
    `run_generate` の実運用経路でも常に効いていることを確認する。"""
    args = _make_generate_args(
        tmp_path, fake_anchor_paths,
        perturb_magnitude=1e30, perturb_seed=1,  # 大きいが float32 上限(~3.4e38)未満
    )
    result = ft.run_generate(args)
    for label in ft.BLIND_LABELS:
        vector = ft.load_embed_vector(result["embed_paths"][label])
        assert np.all(np.isfinite(vector))


# --- 決定論（staging 化後の再確認） ---------------------------------------------


def test_run_generate_is_deterministic_across_two_full_atomic_publishes_to_distinct_dirs(
    tmp_path: Path, fake_anchor_paths: Dict[str, Path]
) -> None:
    """staging → swap を経由しても、同一 seed の 2 回独立実行（別 out_dir）は
    候補 embed バイト列・genome 台帳ともに完全一致する。"""
    args1 = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=tmp_path / "run1", seed=99)
    args2 = _make_generate_args(tmp_path, fake_anchor_paths, out_dir=tmp_path / "run2", seed=99)
    result1 = ft.run_generate(args1)
    result2 = ft.run_generate(args2)

    assert result1["ledger"] == result2["ledger"]
    for label in ft.BLIND_LABELS:
        assert result1["embed_paths"][label].read_bytes() == result2["embed_paths"][label].read_bytes()
    assert result1["sha_path"].read_text(encoding="utf-8") == result2["sha_path"].read_text(encoding="utf-8")


# --- CLI 引数パース -----------------------------------------------------------


def test_cli_parses_weights_triple() -> None:
    parser = ft.build_arg_parser()
    args = parser.parse_args(
        [
            "generate",
            "--ritsu-embed", "r.emb", "--pjs-embed", "p.emb", "--user-embed", "u.emb",
            "--out-dir", "out", "--seed", "42", "--batch-name", "S3-batch1",
            "--date", "2026-08-17", "--backbone-checkpoint", "sha256:aa",
            "--weights", "0.5,0.3,0.2", "--weights", "0.2,0.5,0.3", "--weights", "0.3,0.2,0.5",
            "--voice-id-start", "1", "--control-kind", "anchor", "--control-anchor", "ritsu",
        ]
    )
    assert args.weights == [(0.5, 0.3, 0.2), (0.2, 0.5, 0.3), (0.3, 0.2, 0.5)]


def test_cli_rejects_malformed_weights_triple() -> None:
    parser = ft.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "generate",
                "--ritsu-embed", "r.emb", "--pjs-embed", "p.emb", "--user-embed", "u.emb",
                "--out-dir", "out", "--seed", "42", "--batch-name", "S3-batch1",
                "--date", "2026-08-17", "--backbone-checkpoint", "sha256:aa",
                "--weights", "not,a,triple,too,many", "--voice-id-start", "1",
                "--control-kind", "anchor", "--control-anchor", "ritsu",
            ]
        )
