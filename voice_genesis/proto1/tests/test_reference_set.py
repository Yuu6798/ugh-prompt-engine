"""test_reference_set.py — P5 (VG-016 + VG-018 lite) の受け入れテスト。

pytest の日常反復用に `n_permutations` は小さい値（20）で回す（本ファイルの
目的はロジックの正しさの検証であり、チャンス帯推定の統計的精度そのものは
`results_p1/scaffold_test_report.md` 生成時に memo 規定の 200 回で実測する）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
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
    for key in (
        "schema_version",
        "id",
        "version",
        "created_at",
        "source_datasets",
        "embedding_models",
        "coverage_notes",
        "chance_band_procedure",
        "sha256",
    ):
        assert key in d
    assert d["schema_version"] == "reference-set/0.2"
    assert len(d["sha256"]) == 64  # sha256 hexdigest


def test_sidecar_dict_chance_band_procedure_has_expected_fields(small_gallery):
    """PR#261 レビュー R4: チャンス帯手続きパラメータを sidecar で可視化する。"""
    procedure = small_gallery.sidecar_dict()["chance_band_procedure"]
    assert procedure == {
        "chance_seed_base": rset.CHANCE_SEED_BASE,
        "n_permutations": _SMALL_N_PERMUTATIONS,
        "percentile": rset.CHANCE_BAND_PERCENTILE,
    }


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


# --- PR#261 レビュー R4: チャンス帯手続きパラメータの hash 被覆 --------------


def test_reference_set_hash_changes_with_different_n_permutations():
    """n_permutations を変えるとチャンス帯閾値（e1_pass/e2_pass を左右する）が
    変わり得るため、reference_set_hash も変わらなければならない（R4 の主眼）。"""
    g1 = rset.build_reference_set(n_permutations=1)
    g2 = rset.build_reference_set(n_permutations=2)
    assert g1.sha256 != g2.sha256


def test_reference_set_hash_changes_with_different_chance_seed_base():
    g1 = rset.build_reference_set(n_permutations=1, chance_seed_base=rset.CHANCE_SEED_BASE)
    g2 = rset.build_reference_set(n_permutations=1, chance_seed_base=rset.CHANCE_SEED_BASE + 5000)
    assert g1.sha256 != g2.sha256


def test_reference_set_hash_stable_for_identical_chance_band_procedure_params():
    """gallery 本体・チャンス帯手続きパラメータが全て同一なら、2 回実行しても
    reference_set_hash は安定する（決定論の非退行確認）。"""
    g1 = rset.build_reference_set(
        n_permutations=3, gallery_seed_base=rset.GALLERY_SEED_BASE, chance_seed_base=rset.CHANCE_SEED_BASE
    )
    g2 = rset.build_reference_set(
        n_permutations=3, gallery_seed_base=rset.GALLERY_SEED_BASE, chance_seed_base=rset.CHANCE_SEED_BASE
    )
    assert g1.sha256 == g2.sha256


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


# --- PR#261 レビュー R9: load 時の pass/fail 再計算検証 ----------------------


def test_load_all_rejects_tampered_e1_pass(tmp_path, small_gallery):
    """e1_max_similarity > e1_chance_band_p95（本来 fail）なのに e1_pass=True
    と偽装した改ざん行を、load 時の再計算検証で拒否する。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    # gallery 自身のメンバーを監査すると確実に e1_pass=False になる（既存テスト
    # test_audit_gallery_member_against_itself_fails_both_systems で確認済み）。
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    assert report.e1_pass is False
    log.append(report)

    text = log.path.read_text(encoding="utf-8")
    tampered = text.replace('"e1_pass": false', '"e1_pass": true')
    assert tampered != text  # 前提確認: 置換が実際に発生した
    log.path.write_text(tampered, encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


def test_load_all_rejects_tampered_overall_pass(tmp_path, small_gallery):
    """e1_pass/e2_pass の宣言値自体は正しくても、overall_pass だけ偽装した
    行も拒否する。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    assert report.overall_pass is False
    log.append(report)

    text = log.path.read_text(encoding="utf-8")
    tampered = text.replace('"overall_pass": false', '"overall_pass": true')
    assert tampered != text
    log.path.write_text(tampered, encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


def test_load_all_accepts_untampered_report(tmp_path, small_gallery):
    """非退行確認: 改ざんされていない正しい監査ログは従来どおり読み込める。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    loaded = log.load_all()
    assert len(loaded) == 1
    assert loaded[0].e1_pass == report.e1_pass
    assert loaded[0].overall_pass == report.overall_pass


# --- PR#261 レビュー R13: pass/fail 再計算前の実測値の定義域検証 ------------


def test_load_all_rejects_similarity_above_cosine_domain(tmp_path, small_gallery):
    """コサイン類似度は理論上 [-1.0, 1.0] に収まる。1.0 を超える宣言値は、
    R9 の再計算式そのものを無意味にする域外注入として拒否する（R13）。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    data = json.loads(log.path.read_text(encoding="utf-8").strip())
    data["e1_max_similarity"] = 1.5  # コサイン類似度の定義域外（R13 が R9 より先に検出するはず）
    log.path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


def test_load_all_rejects_non_finite_similarity(tmp_path, small_gallery):
    """e1_max_similarity が NaN の場合、`NaN <= x` は常に False になるため
    R9 単体の再計算一致検証だけでは検出できない構成になり得る。R13 の
    定義域検証（R9 より先に走る）で拒否する。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    data = json.loads(log.path.read_text(encoding="utf-8").strip())
    data["e1_max_similarity"] = float("nan")
    log.path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


# --- PR#261 レビュー R19: report_id の内容アドレス再計算検証 -----------------


def test_load_all_rejects_tampered_report_id(tmp_path, small_gallery):
    """report_id を genome_id/reference_set_hash からの再計算と食い違う値へ
    改ざんした行を拒否する（[UNDERSTEC-F-2] の内容アドレス規約の読み込み側検証）。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    data = json.loads(log.path.read_text(encoding="utf-8").strip())
    data["report_id"] = "audit-000000000000"  # genome_id/reference_set_hash とは無関係な値
    assert data["report_id"] != report.report_id  # 前提確認
    log.path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


def test_load_all_accepts_report_id_matching_recomputation(tmp_path, small_gallery):
    """非退行確認: 正しく計算された report_id を持つ行は従来どおり通過する。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    loaded = log.load_all()
    assert loaded[0].report_id == report.report_id


# --- PR#261 レビュー R17: mark_stale() の書き戻しをアトミック置換にする -----


def test_rewrite_interrupted_before_replace_leaves_old_log_intact(tmp_path, small_gallery, monkeypatch):
    """旧実装は truncate-then-write だったため、書き込み中の中断（プロセス kill・
    ディスク満杯等）で旧ログが全損し得た。staging + `os.replace()` のアトミック
    置換であれば、置換（rename）前に中断しても旧ログはそのまま残ることを確認する。
    """
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    original_text = log.path.read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("simulated interruption before atomic replace")

    monkeypatch.setattr(rset.os, "replace", _boom)

    with pytest.raises(OSError):
        log.mark_stale("some-different-hash-because-reference-set-was-rebuilt")

    # 旧ログは無傷（staging ファイルへの書き込み自体は完了していても、本ファイルを
    # 指す os.replace が呼ばれる前に中断したため本ファイルは一切変化しない）。
    assert log.path.read_text(encoding="utf-8") == original_text
    loaded = log.load_all()
    assert len(loaded) == 1
    assert loaded[0].stale_audit is False  # mark_stale が失敗したので反映されていない

    # staging ファイルも例外ハンドラで掃除されている（残置しない）。
    leftover = list(tmp_path.glob(f"{log.path.name}.*.tmp"))
    assert leftover == []


def test_mark_stale_still_succeeds_without_interruption(tmp_path, small_gallery):
    """非退行確認: 中断がなければ staging+os.replace 化後も mark_stale は従来どおり動く。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    changed = log.mark_stale("some-different-hash")
    assert changed == 1
    loaded = log.load_all()
    assert loaded[0].stale_audit is True

    leftover = list(tmp_path.glob(f"{log.path.name}.*.tmp"))
    assert leftover == []


# --- PR#261 レビュー R30: append() の書き込み前ラウンドトリップ検証 ---------


def test_append_rejects_report_with_e1_pass_contradicting_measurement(tmp_path, small_gallery):
    """append() は `audit_linkability()` が構築したレポートのみを受理する設計
    だが、呼び出し元が `LinkabilityAuditReport(...)` を直接構築して
    `e1_pass` を実測値（e1_max_similarity vs e1_chance_band_p95）と矛盾させた
    レポートを渡すと、旧実装では append() 自体は無検証で成功してしまい
    「書けるが load_all()/mark_stale() では読めない」非対称なログ行を
    作れてしまっていた（registry.py の R28 と同型の穴）。書き込み前の
    ラウンドトリップ検証（_report_from_dict() を通す）でこれを拒否する。
    """
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    tampered = replace(report, e1_pass=not report.e1_pass)  # 実測値と矛盾させる

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.append(tampered)

    # 拒否されたレポートは一切書き込まれていない。
    assert not log.path.exists() or log.path.read_text(encoding="utf-8") == ""


def test_append_accepts_valid_report_non_regression(tmp_path, small_gallery):
    """非退行確認: `audit_linkability()` が構築した正常なレポート（宣言値が
    実測値と整合している）は書き込み前ラウンドトリップ検証を問題なく通過し、
    従来どおり append・load_all() できる。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)

    log.append(report)

    loaded = log.load_all()
    assert len(loaded) == 1
    assert loaded[0].report_id == report.report_id


# --- PR#261 レビュー R33: report_id の重複検出（load_all() / append()） ----


def test_load_all_rejects_duplicate_report_id(tmp_path, small_gallery):
    """report_id は内容アドレス（R19: {genome_id, reference_set_hash} から
    一意に決まる）である以上、同一 report_id の複数行は append-only ログの
    意味論と矛盾する（例: 片方だけ stale_audit が異なる「同じ監査を指す
    2 通りの主張」）。手編集・インポートされた JSONL に紛れ込んだ重複を
    load_all() で拒否する。
    """
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    # append() 自体は重複を拒否するため、ファイルへ直接同一行を複製する
    # （手編集・インポートされたログを模す）。
    original_line = log.path.read_text(encoding="utf-8")
    log.path.write_text(original_line + original_line, encoding="utf-8")

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.load_all()


def test_append_rejects_duplicate_report_id(tmp_path, small_gallery):
    """append() は書き込み前に既存ログとの report_id 重複を軽量チェックで
    拒否する（ラウンドトリップ検証より先に走る fail-fast パス）。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    with pytest.raises(rset.LinkabilityAuditValidationError):
        log.append(report)  # 同一 report（= 同一 report_id）を再 append

    # 拒否された 2 回目は書き込まれておらず、ログは 1 行のまま。
    loaded = log.load_all()
    assert len(loaded) == 1


def test_append_accepts_distinct_report_ids_non_regression(tmp_path, small_gallery):
    """非退行確認: 異なる候補（= 異なる report_id）の複数 append は従来どおり
    成功する。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report_a = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    report_b = rset.audit_linkability(small_gallery.members[1].genome, small_gallery)
    assert report_a.report_id != report_b.report_id  # 前提確認

    log.append(report_a)
    log.append(report_b)

    loaded = log.load_all()
    assert {r.report_id for r in loaded} == {report_a.report_id, report_b.report_id}


def test_mark_stale_rewrite_path_does_not_trigger_duplicate_detection(tmp_path, small_gallery):
    """mark_stale() の書き戻し（`_rewrite()`）は `load_all()` で読み込んだ
    既存レポート集合の `stale_audit` フィールドだけを書き換えて丸ごと
    再書き込みする経路であり、`append()` を経由しない（report_id は不変の
    まま）。R33 の重複検出が誤って自己干渉しないことを確認する（複数件
    ある状態で mark_stale() が問題なく完走し、件数・report_id 集合が
    保たれること）。
    """
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report_a = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    report_b = rset.audit_linkability(small_gallery.members[1].genome, small_gallery)
    log.append(report_a)
    log.append(report_b)

    changed = log.mark_stale("some-different-hash-because-reference-set-was-rebuilt")
    assert changed == 2

    loaded = log.load_all()  # 重複検出込みで問題なく読める
    assert len(loaded) == 2
    assert {r.report_id for r in loaded} == {report_a.report_id, report_b.report_id}
    assert all(r.stale_audit for r in loaded)


# --- PR#261 レビュー R35: append() の書き込みを staging + os.replace アトミック
# 置換にする（R17 と同型。`_rewrite()` を append() から再利用する形で実装） -----


def test_append_interrupted_before_replace_leaves_old_log_intact(tmp_path, small_gallery, monkeypatch):
    """旧実装は `open(path, "a")` での write() だったため、書き込み途中の中断
    （プロセス kill・ディスク満杯等）で正本 JSONL の末尾に破損した部分行が
    残り得た。append() が `_rewrite()`（staging + `os.replace()` のアトミック
    置換）へ委譲するようになった後も、置換（rename）前に中断すれば旧ログは
    そのまま残ることを確認する。
    """
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report_a = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report_a)

    original_text = log.path.read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("simulated interruption before atomic replace")

    monkeypatch.setattr(rset.os, "replace", _boom)

    report_b = rset.audit_linkability(small_gallery.members[1].genome, small_gallery)
    with pytest.raises(OSError):
        log.append(report_b)

    # 旧ログは無傷（staging ファイルへの書き込み自体は完了していても、本ファイルを
    # 指す os.replace が呼ばれる前に中断したため本ファイルは一切変化しない）。
    assert log.path.read_text(encoding="utf-8") == original_text
    loaded = log.load_all()
    assert len(loaded) == 1  # report_b は反映されていない

    # staging ファイルも例外ハンドラで掃除されている（残置しない）。
    leftover = list(tmp_path.glob(f"{log.path.name}.*.tmp"))
    assert leftover == []


def test_append_still_succeeds_without_interruption(tmp_path, small_gallery):
    """非退行確認: 中断がなければ append() の staging+os.replace 化後も
    従来どおり動く。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report_a = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    report_b = rset.audit_linkability(small_gallery.members[1].genome, small_gallery)
    log.append(report_a)
    log.append(report_b)

    loaded = log.load_all()
    assert {r.report_id for r in loaded} == {report_a.report_id, report_b.report_id}

    leftover = list(tmp_path.glob(f"{log.path.name}.*.tmp"))
    assert leftover == []


# --- PR#261 レビュー R37: append()/mark_stale() の read-modify-replace 全区間を
# ロックで直列化 ------------------------------------------------------------


def test_append_rejects_when_lock_already_held(tmp_path, small_gallery):
    """ロック保持中の並行 append を模擬する: `<正本>.lock` が既に存在する状態で
    append() を呼ぶと、検証・書き込みのいずれも行わず即座に `FileLockError`
    で拒否されることを確認する（PR#261 R37）。
    """
    from filelock import FileLockError

    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    lock_path = log._lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("12345", encoding="utf-8")  # 別プロセスが保持中を模す

    try:
        report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
        with pytest.raises(FileLockError):
            log.append(report)

        # 拒否されたので何も書き込まれていない(ログファイル自体が未作成のまま)。
        assert not log.path.exists()
    finally:
        lock_path.unlink()


def test_mark_stale_rejects_when_lock_already_held(tmp_path, small_gallery):
    """mark_stale() 側も append() と同じロックで排他されることを確認する
    （PR#261 R37）。"""
    from filelock import FileLockError

    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)

    lock_path = log._lock_path()
    lock_path.write_text("12345", encoding="utf-8")  # 別プロセスが保持中を模す

    try:
        with pytest.raises(FileLockError):
            log.mark_stale("some-different-hash")

        # 拒否されたので stale_audit は変化していない。
        loaded = log.load_all()
        assert loaded[0].stale_audit is False
    finally:
        lock_path.unlink()


def test_append_and_mark_stale_still_succeed_after_lock_released_non_regression(tmp_path, small_gallery):
    """非退行確認: ロックファイルが存在しなければ append()/mark_stale() は
    従来どおり成功し、ロックファイルはブロック終了時に必ず削除される。"""
    log = rset.LinkabilityAuditLog(tmp_path / "audit_log.jsonl")
    report = rset.audit_linkability(small_gallery.members[0].genome, small_gallery)
    log.append(report)
    assert not log._lock_path().exists()

    changed = log.mark_stale("some-different-hash")
    assert changed == 1
    assert not log._lock_path().exists()
