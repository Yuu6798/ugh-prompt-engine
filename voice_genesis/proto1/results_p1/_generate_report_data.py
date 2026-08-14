"""results_p1/_generate_report_data.py — scaffold_test_report.md 用の実測データ生成。

P6 (aliasing / register transition / formant sweep) の実測値と、P5 linkability
監査の memo 規定どおり n_permutations=200 でのサンプル実行結果を JSON に落とす。
フォアグラウンド実行・決定論（固定 seed のみ）。数分規模で完走する想定。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import genome as g
import registry as r
import render_health as rh
import reference_set as rset
import sampler

OUT_DIR = Path(__file__).resolve().parent
FIXED_TIME = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def main() -> None:
    report: dict = {}
    t_start = time.time()

    # --- P6: レンダラ健全性 --------------------------------------------------
    default_genome = g.build_genome("scaffold-default")

    aliasing = rh.check_aliasing(default_genome)
    aliasing_c3 = rh.check_aliasing(default_genome, midi=48.0)
    aliasing_c6 = rh.check_aliasing(default_genome, midi=84.0)
    report["p6_aliasing"] = {
        "A4": aliasing.__dict__,
        "C3": aliasing_c3.__dict__,
        "C6": aliasing_c6.__dict__,
    }

    register = rh.register_transition_report(default_genome)
    report["p6_register_transition"] = {
        "max_db_jump": register.max_db_jump,
        "threshold_db": register.threshold_db,
        "passed": register.passed,
        "n_notes": len(register.note_rms_db),
        "note_rms_db_head5": register.note_rms_db[:5],
        "note_rms_db_tail5": register.note_rms_db[-5:],
    }

    formant = rh.formant_sweep_report(default_genome)
    report["p6_formant_sweep"] = {
        "formant_scales": formant.formant_scales,
        "formant_centroid_log2hz": formant.formant_centroid_log2hz,
        "direction_consistency": formant.direction_consistency,
        "net_direction_positive": formant.net_direction_positive,
        "threshold": rh.DIRECTION_CONSISTENCY_THRESHOLD,
        "passed": formant.passed,
    }

    # 追加サンプル (10 seed) でのロバスト性サマリ
    sampled_summaries = []
    for seed in range(10):
        gen = sampler.sample(seed)
        a = rh.check_aliasing(gen)
        rt = rh.register_transition_report(gen)
        fs = rh.formant_sweep_report(gen)
        sampled_summaries.append(
            {
                "seed": seed,
                "aliasing_db": a.high_band_energy_ratio_db,
                "aliasing_passed": a.passed,
                "register_max_db_jump": rt.max_db_jump,
                "register_passed": rt.passed,
                "formant_direction_consistency": fs.direction_consistency,
                "formant_passed": fs.passed,
            }
        )
    report["p6_sampled_seeds_0_9"] = sampled_summaries

    t_p6 = time.time()
    report["_timing_p6_sec"] = t_p6 - t_start

    # --- P4: registry + lineage の実測サンプル -------------------------------
    registry_path = OUT_DIR / "_scratch_registry.jsonl"
    if registry_path.exists():
        registry_path.unlink()
    reg = r.GenomeRegistry(registry_path)
    g1 = sampler.sample(1)
    e1 = reg.append(g1, op="sample", seed=1, now=FIXED_TIME)
    g2 = sampler.mutate(g1, seed=2, scale=0.1)
    e2 = reg.append(g2, op="mutate", seed=2, parents=[e1.genome_id], now=FIXED_TIME)
    g3 = sampler.sample(3)
    e3 = reg.append(g3, op="sample", seed=3, now=FIXED_TIME)
    g4 = sampler.crossover(g2, g3, seed=4)
    e4 = reg.append(g4, op="crossover", seed=4, parents=[e2.genome_id, e3.genome_id], now=FIXED_TIME)
    lineage = reg.lineage(e4.genome_id)
    report["p4_registry_sample"] = {
        "entries_written": 4,
        "genome_ids": [e1.genome_id, e2.genome_id, e3.genome_id, e4.genome_id],
        "lineage_of_e4": [{"genome_id": e.genome_id, "op": e.op, "parents": e.parents} for e in lineage],
    }
    registry_path.unlink()

    # --- P5: reference set + linkability 監査 (memo 規定どおり n_permutations=200) --
    t_p5_start = time.time()
    gallery = rset.build_reference_set(n_permutations=rset.CHANCE_N_PERMUTATIONS, now=FIXED_TIME)
    t_p5_gallery = time.time()

    report["p5_reference_set_sidecar"] = gallery.sidecar_dict()
    report["p5_chance_bands"] = {
        "e1_chance_band_p95": gallery.e1_chance_band_p95,
        "e2_chance_band_p95": gallery.e2_chance_band_p95,
        "n_permutations": gallery.chance_n_permutations,
    }

    # 監査サンプル 1: gallery メンバー自身（linkable であるべき = FAIL 期待）
    self_report = rset.audit_linkability(gallery.members[0].genome, gallery, now=FIXED_TIME)
    # 監査サンプル 2〜4: gallery と無関係な新規候補（PASS することが多いはず）
    unrelated_reports = []
    for seed in (777001, 777002, 777003):
        cand = sampler.sample(seed, name=f"audit-candidate-{seed}")
        rep = rset.audit_linkability(cand, gallery, now=FIXED_TIME)
        unrelated_reports.append(rep)

    def _report_summary(rep: rset.LinkabilityAuditReport) -> dict:
        return {
            "report_id": rep.report_id,
            "genome_id": rep.genome_id,
            "reference_set_hash": rep.reference_set_hash,
            "e1_max_similarity": rep.e1_max_similarity,
            "e1_chance_band_p95": rep.e1_chance_band_p95,
            "e1_pass": rep.e1_pass,
            "e2_max_similarity": rep.e2_max_similarity,
            "e2_chance_band_p95": rep.e2_chance_band_p95,
            "e2_pass": rep.e2_pass,
            "overall_pass": rep.overall_pass,
            "provenance": rep.provenance,
        }

    report["p5_audit_self_match_sample"] = _report_summary(self_report)
    report["p5_audit_unrelated_candidate_samples"] = [_report_summary(r_) for r_ in unrelated_reports]

    # stale_audit 再監査トリガーの実測
    audit_log_path = OUT_DIR / "_scratch_audit_log.jsonl"
    if audit_log_path.exists():
        audit_log_path.unlink()
    log = rset.LinkabilityAuditLog(audit_log_path)
    log.append(self_report)
    for rep in unrelated_reports:
        log.append(rep)
    changed_same_hash = log.mark_stale(gallery.sha256)
    changed_new_hash = log.mark_stale("simulated-rebuilt-reference-set-hash")
    report["p5_stale_audit_trigger"] = {
        "entries_logged": 1 + len(unrelated_reports),
        "changed_when_marking_current_hash": changed_same_hash,
        "changed_when_marking_different_hash": changed_new_hash,
    }
    audit_log_path.unlink()

    t_p5_end = time.time()
    report["_timing_p5_sec"] = t_p5_end - t_p5_start
    report["_timing_p5_gallery_only_sec"] = t_p5_gallery - t_p5_start

    report["_timing_total_sec"] = time.time() - t_start

    out_path = OUT_DIR / "report_data.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path} ({time.time() - t_start:.1f}s total)")


if __name__ == "__main__":
    main()
