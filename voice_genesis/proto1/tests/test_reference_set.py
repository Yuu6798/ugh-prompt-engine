"""test_reference_set.py — P5 (VG-016 + VG-018 lite) の受け入れテスト。

pytest の日常反復用に `n_permutations` は小さい値（20）で回す（本ファイルの
目的はロジックの正しさの検証であり、チャンス帯推定の統計的精度そのものは
`results_p1/scaffold_test_report.md` 生成時に memo 規定の 200 回で実測する）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

import reference_set as rset
import sampler

_SMALL_N_PERMUTATIONS = 20


@pytest.fixture(scope="module")
def small_gallery():
    return rset.build_reference_set(n_permutations=_SMALL_N_PERMUTATIONS)


def test_gallery_has_expected_size(small_gallery):
    assert len(small_gallery.members) == rset.GALLERY_SIZE == 8


def test_sidecar_dict_has_required_fields(small_gallery):
    d = small_gallery.sidecar_dict()
    for key in ("schema_version", "id", "version", "created_at", "source_datasets", "embedding_models", "coverage_notes", "sha256"):
        assert key in d
    assert d["schema_version"] == "reference-set/0.1"
    assert len(d["sha256"]) == 64  # sha256 hexdigest


def test_coverage_notes_include_instrument_validity_caveat(small_gallery):
    assert "instrument-validity caveat" in small_gallery.coverage_notes
    assert "合成スタンドイン" in small_gallery.coverage_notes


def test_embedding_models_include_provenance_caveat(small_gallery):
    assert len(small_gallery.embedding_models) == 2
    for model in small_gallery.embedding_models:
        assert model["provenance"] == rset.PROVENANCE_CAVEAT


def test_embedding_vectors_have_no_nan(small_gallery):
    assert not np.isnan(small_gallery.e1_normalized).any()
    assert not np.isnan(small_gallery.e2_normalized).any()


def test_chance_bands_are_finite_probabilities_like_values(small_gallery):
    assert np.isfinite(small_gallery.e1_chance_band_p95)
    assert np.isfinite(small_gallery.e2_chance_band_p95)
    # コサイン類似度なので理論上 [-1, 1] に収まる
    assert -1.0 <= small_gallery.e1_chance_band_p95 <= 1.0
    assert -1.0 <= small_gallery.e2_chance_band_p95 <= 1.0


def test_reference_set_hash_is_deterministic():
    g1 = rset.build_reference_set(n_permutations=1)
    g2 = rset.build_reference_set(n_permutations=1)
    assert g1.sha256 == g2.sha256  # gallery 本体（genome+embedding）は permutation 回数に非依存


def test_reference_set_hash_changes_with_different_gallery_seed():
    g1 = rset.build_reference_set(n_permutations=1, gallery_seed_base=rset.GALLERY_SEED_BASE)
    g2 = rset.build_reference_set(n_permutations=1, gallery_seed_base=rset.GALLERY_SEED_BASE + 1000)
    assert g1.sha256 != g2.sha256


def test_audit_gallery_member_against_itself_fails_both_systems(small_gallery):
    """gallery 自身のメンバーを監査すると、当然 E1・E2 とも最近傍=自分自身で類似度最大
    （chance band を超える）になり、linkability 監査は正しく検出できるはず。"""
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    assert report.e1_max_similarity > report.e1_chance_band_p95
    assert report.e1_pass is False
    assert report.e2_max_similarity > report.e2_chance_band_p95
    assert report.e2_pass is False
    assert report.overall_pass is False
    assert report.provenance == rset.PROVENANCE_CAVEAT


def test_audit_report_pairs_reference_set_hash(small_gallery):
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    assert report.reference_set_hash == small_gallery.sha256


def test_audit_is_deterministic(small_gallery):
    candidate = sampler.sample(31337)
    r1 = rset.audit_linkability(candidate, small_gallery, now=None)
    r2 = rset.audit_linkability(candidate, small_gallery, now=None)
    assert r1.e1_max_similarity == r2.e1_max_similarity
    assert r1.e2_max_similarity == r2.e2_max_similarity
    assert r1.overall_pass == r2.overall_pass


# --- LinkabilityAuditLog: append / stale_audit 再監査トリガー ---------------------


def test_audit_log_append_and_load(tmp_path, small_gallery):
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    loaded = log.load_all()
    assert len(loaded) == 1
    assert loaded[0].report_id == report.report_id
    assert loaded[0].stale_audit is False


def test_mark_stale_flags_entries_with_old_reference_set_hash(tmp_path, small_gallery):
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    changed = log.mark_stale("some-different-hash-because-reference-set-was-rebuilt")
    assert changed == 1

    loaded = log.load_all()
    assert loaded[0].stale_audit is True


def test_mark_stale_does_not_flag_current_hash(tmp_path, small_gallery):
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    changed = log.mark_stale(small_gallery.sha256)  # 現行ハッシュと同じ = stale ではない
    assert changed == 0
    loaded = log.load_all()
    assert loaded[0].stale_audit is False


def test_mark_stale_is_idempotent(tmp_path, small_gallery):
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    first = log.mark_stale("new-hash")
    second = log.mark_stale("new-hash")
    assert first == 1
    assert second == 0  # 既に stale 済みのものは二重カウントしない
