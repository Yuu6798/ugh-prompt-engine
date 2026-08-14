"""singer/genesis_v2.py — S4 W4: 新安全域ボックス（gate6-v2 実測）での多世代探索。

`genesis_v1.py`（S3b、無改変で import 流用）の多世代探索機構（mutate/
crossover・quick-S5・linkability・貪欲坂登り親選択・distinctiveness 降順
フル gate フォールバック）をそのまま再利用し、以下の 2 点のみ差し替える:

1. 安全域ボックス（`SAFE_BOX_V2`。`results_s4/safe_box_v2.md` の実測に基づく）
2. 最終当選者判定の「フル gate」を gate1-5（無改変）+ **gate6-v2**
   （score-informed QC、`gate_checks_v2.py`）に差し替える

`genesis_v1.clip_to_safe_box` はモジュールグローバルとして他関数から素の
名前で参照されるため、本ファイルは import 後に **モンキーパッチ**で
`genesis_v1.clip_to_safe_box` を新ボックス版に差し替える（`genesis_v1.py`
自体のソースは無改変のまま）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
_VT_HARNESS_DIR = _HERE.parent / "harness"
for _p in (_PROTO1_DIR, _VT_HARNESS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import genome as pg  # noqa: E402
import render_song as rs  # noqa: E402
import gate_checks_v2 as gc2  # noqa: E402
import genesis_v0 as gv  # noqa: E402
import genesis_v1 as gv1  # noqa: E402

# ---------------------------------------------------------------------------
# 新安全域ボックス（W3 実測。詳細根拠は results_s4/safe_box_v2.md）
# ---------------------------------------------------------------------------

SAFE_BOX_V2 = {
    "tilt": (-13.0, -8.0),
    "bandwidth_scale": (0.80, 1.05),
    "breathiness_base": (0.0, 0.35),
    "register_gains_each": (0.0, 0.35),
    "vibrato_rate_hz": (4.5, 7.0),
    "vibrato_depth_cents": (15.0, 40.0),
    "formant_scale": (0.99, 1.01),
}


def clip_to_safe_box_v2(g: pg.VoiceGenome) -> pg.VoiceGenome:
    tilt = min(max(g.source.tilt, SAFE_BOX_V2["tilt"][0]), SAFE_BOX_V2["tilt"][1])
    fs = min(max(g.resonance.formant_scale, SAFE_BOX_V2["formant_scale"][0]), SAFE_BOX_V2["formant_scale"][1])
    bw = min(max(g.resonance.bandwidth_scale, SAFE_BOX_V2["bandwidth_scale"][0]), SAFE_BOX_V2["bandwidth_scale"][1])
    breath = min(max(g.noise.breathiness_base, SAFE_BOX_V2["breathiness_base"][0]), SAFE_BOX_V2["breathiness_base"][1])
    lo_r, hi_r = SAFE_BOX_V2["register_gains_each"]
    reg = tuple(min(max(v, lo_r), hi_r) for v in g.noise.register_gains)
    vr = min(max(g.microprosody.vibrato_rate_hz, SAFE_BOX_V2["vibrato_rate_hz"][0]), SAFE_BOX_V2["vibrato_rate_hz"][1])
    vd = min(max(g.microprosody.vibrato_depth_cents, SAFE_BOX_V2["vibrato_depth_cents"][0]), SAFE_BOX_V2["vibrato_depth_cents"][1])
    return pg.build_genome(
        g.name,
        source=pg.SourceSection(tilt=tilt, source_mode=g.source.source_mode),
        resonance=pg.ResonanceSection(formant_scale=fs, formant_offsets=(0.0, 0.0, 0.0, 0.0), bandwidth_scale=bw),
        noise=pg.NoiseSection(breathiness_base=breath, register_gains=reg),
        register=g.register,
        microprosody=pg.MicroprosodySection(
            vibrato_rate_hz=vr, vibrato_depth_cents=vd,
            jitter_amount=g.microprosody.jitter_amount, jitter_seed=g.microprosody.jitter_seed,
        ),
        range_=g.range,
    )


# genesis_v1 内の各関数は素の名前 `clip_to_safe_box(...)` で自モジュール
# グローバルを参照するため、モンキーパッチで新ボックス版に差し替える。
gv1.clip_to_safe_box = clip_to_safe_box_v2  # type: ignore[attr-defined]
gv1.SAFE_BOX = SAFE_BOX_V2  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# フル gate（gate1-5 無改変 + gate6-v2）でのフォールバック選出
# ---------------------------------------------------------------------------


def _try_full_gates_v2_by_distinctiveness(records: List["gv1.EvalRecord"]) -> Tuple[Optional["gv1.EvalRecord"], List[dict]]:
    attempts = []
    for r in sorted(records, key=gv1._rank_key):
        g = r.candidate.genome
        full = gc2.full_gates_v2(g)
        attempts.append({
            "candidate_id": r.candidate.candidate_id, "name": g.name,
            "passed": full["pass"],
            "gate6_breathiness_grip": full["gate6"]["breathiness"]["grip_ratio"],
            "gate6_vibrato_grip": full["gate6"]["vibrato_depth"]["grip_ratio"],
            "provenance_gate6": full["provenance_gate6"],
        })
        if full["pass"]:
            return r, attempts
    return None, attempts


def run_multigen_v2(n_generations: int = 4, n_candidates: int = 8, verbose: bool = True):
    reference = gv._reference_gallery_vectors()
    ref_hash = gv.reference_set_hash(reference)
    c_result = rs.render_sakura(rs.voice_c())
    d_result = rs.render_sakura(rs.voice_d())

    gen0 = gv1.build_gen0()  # 内部で clip_to_safe_box（モンキーパッチ済み）を使用
    all_survivors: List["gv1.EvalRecord"] = []
    gen_logs = []

    def evaluate(cands):
        survivors, culled = [], []
        for c in cands:
            result = rs.render_sakura(c.genome)
            q = gv.quick_s5(c.genome, result)
            if not q.overall_pass:
                culled.append((c, "quick_s5_fail"))
                continue
            import identity_metrics as im
            e1_0, e2_0 = im.normalized_e1_e2(result, 0)
            e1_1, e2_1 = im.normalized_e1_e2(result, 1)
            vec = ((e1_0 + e1_1) / 2.0, (e2_0 + e2_1) / 2.0)
            link = gv.linkability_audit(vec, reference)
            if not link.passed:
                culled.append((c, "linkability_fail"))
                continue
            comp, dc, dd = gv1.distinctiveness_cached(result, c_result, d_result)
            survivors.append(gv1.EvalRecord(c, q, link, comp, dc, dd))
        return survivors, culled

    parents = gen0
    next_id = 4
    winner = None
    winner_attempts: List[dict] = []
    winner_meets_floor = False

    for gen_idx in range(1, n_generations + 1):
        cands, next_id = gv1.make_generation_candidates(parents, gen_idx, n_candidates, next_id)
        survivors, culled = evaluate(cands)
        all_survivors.extend(survivors)
        best_d = max((s.distinctiveness for s in survivors), default=float("-inf"))
        floor_ids = [s.candidate.candidate_id for s in survivors if s.distinctiveness >= gv1.JND_FLOOR]
        gen_logs.append(gv1.GenerationLog(gen_idx, cands, survivors, culled, best_d, floor_ids))
        if verbose:
            print(f"=== gen {gen_idx}: {len(survivors)}/{len(cands)} survived, best_dist={best_d:.3f}, floor_clear={floor_ids} ===")
            for s in sorted(survivors, key=gv1._rank_key):
                print(f"  [{s.candidate.candidate_id:3d}] {s.candidate.genome.name:20s} op={s.candidate.op:9s} dist={s.distinctiveness:6.3f} (C={s.dist_c:.3f} D={s.dist_d:.3f})")

        if winner is None:
            floor_survivors = [s for s in survivors if s.distinctiveness >= gv1.JND_FLOOR]
            w, attempts = _try_full_gates_v2_by_distinctiveness(floor_survivors)
            winner_attempts.extend(attempts)
            if w is not None:
                winner = w
                winner_meets_floor = True
        if winner is not None:
            if verbose:
                print(f"floor + full-gate-v2 winner found at generation {gen_idx}: {winner.candidate.candidate_id}")
            break

        if survivors:
            parents = [s.candidate for s in sorted(survivors, key=gv1._rank_key)[:4]]

    if winner is None:
        w, attempts = _try_full_gates_v2_by_distinctiveness(all_survivors)
        winner_attempts = attempts
        winner = w
        winner_meets_floor = False

    return {
        "generations": gen_logs, "all_survivors": all_survivors, "winner": winner,
        "winner_meets_floor": winner_meets_floor, "winner_full_gate_attempts": winner_attempts,
        "reference_hash": ref_hash,
    }
