"""singer/genesis_v1.py — S3b: 多世代 Genesis Graph 探索（VG-015 追補）。

S3（`genesis_v0.py`, `results_s3/genesis_report.md`）は 1 世代の探索で
当選者 genesis1 を選出したが、tract 系軸の摂動幅を安全のため
`TRACT_MUTATE_SCALE=0.025` に絞ったため親 voice_C からの複合距離が
<1 JND にとどまった。S3b は同じ歩幅規約のまま **世代を重ねて** 安全域内を
遠くまで歩く（貪欲坂登り）ことで、識別床（両親から JND 複合距離 >=2.0）を
満たす個体を探す。

読み取り専用: `proto1/`, `vt_harness/`。書き込みは singer/ 配下のみ。
S3 の `genesis_v0.py` を import 流用する（無改変）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
_VT_HARNESS_DIR = _HERE.parent / "harness"
for _p in (_PROTO1_DIR, _VT_HARNESS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import genome as pg  # noqa: E402  (proto1、無改変・import 流用のみ)
import measure_v3 as m3  # noqa: E402  (vt_harness、無改変・import 流用のみ)

import render_song as rs  # noqa: E402
import gate_checks as gc  # noqa: E402
import identity_metrics as im  # noqa: E402
import genesis_v0 as gv  # noqa: E402  (S3、無改変・import 流用のみ)

# ---------------------------------------------------------------------------
# S3b 安全域ボックス（仕様 §2、S2/S3 実測済みの安全域）
# ---------------------------------------------------------------------------

SAFE_BOX = {
    "tilt": (-17.0, -7.0),
    "bandwidth_scale": (0.80, 1.30),
    "breathiness_base": (0.0, 0.40),
    "register_gains_each": (0.0, 0.50),
    "vibrato_rate_hz": (4.5, 7.0),
    "vibrato_depth_cents": (20.0, 60.0),
}

JND_FLOOR = 2.0
S3B_SEED_BASE = 60001
N_GENERATIONS = 4
N_CANDIDATES_PER_GEN = 8  # [UNDERSPEC-S3B-1] 仕様 §6: 15分規模の余裕を見て
# 世代内候補数を当初から 12 ではなく 8 に設定した（多世代 x 再現性照合の
# 2 回実行で時間超過するリスクを避けるための事前判断。理由は underspec_log
# に記録）。


def clip_to_safe_box(g: pg.VoiceGenome) -> pg.VoiceGenome:
    tilt = min(max(g.source.tilt, SAFE_BOX["tilt"][0]), SAFE_BOX["tilt"][1])
    bw = min(max(g.resonance.bandwidth_scale, SAFE_BOX["bandwidth_scale"][0]), SAFE_BOX["bandwidth_scale"][1])
    breath = min(max(g.noise.breathiness_base, SAFE_BOX["breathiness_base"][0]), SAFE_BOX["breathiness_base"][1])
    lo_r, hi_r = SAFE_BOX["register_gains_each"]
    reg = tuple(min(max(v, lo_r), hi_r) for v in g.noise.register_gains)
    vr = min(max(g.microprosody.vibrato_rate_hz, SAFE_BOX["vibrato_rate_hz"][0]), SAFE_BOX["vibrato_rate_hz"][1])
    vd = min(max(g.microprosody.vibrato_depth_cents, SAFE_BOX["vibrato_depth_cents"][0]), SAFE_BOX["vibrato_depth_cents"][1])
    return pg.build_genome(
        g.name,
        source=pg.SourceSection(tilt=tilt, source_mode=g.source.source_mode),
        resonance=pg.ResonanceSection(formant_scale=1.0, formant_offsets=(0.0, 0.0, 0.0, 0.0), bandwidth_scale=bw),
        noise=pg.NoiseSection(breathiness_base=breath, register_gains=reg),
        register=g.register,
        microprosody=pg.MicroprosodySection(
            vibrato_rate_hz=vr, vibrato_depth_cents=vd,
            jitter_amount=g.microprosody.jitter_amount, jitter_seed=g.microprosody.jitter_seed,
        ),
        range_=g.range,
    )


# ---------------------------------------------------------------------------
# 親からの JND 複合距離（S3 と同一定義。cache 済み RenderResult で高速化）
# ---------------------------------------------------------------------------

_JND_NO_F0 = [f for f in im.JND_FEATURES if f != "mean_f0"]


def _jnd_rows_from_results(result_a: "rs.RenderResult", result_b: "rs.RenderResult", notes_per_phrase: int = 2) -> List[Dict[str, float]]:
    rows = []
    phrase_indices = sorted(set(s.note.phrase_index for s in result_a.segments))
    for phrase_idx in phrase_indices:
        segs_a = [s for s in result_a.segments if s.note.phrase_index == phrase_idx]
        segs_b = [s for s in result_b.segments if s.note.phrase_index == phrase_idx]
        n = min(notes_per_phrase, len(segs_a), len(segs_b))
        for i in range(n):
            core_a = im._vowel_core(result_a, segs_a[i])
            core_b = im._vowel_core(result_b, segs_b[i])
            feat_a = m3.extract_all_features_v3(core_a, sr=result_a.sr, fmin=im.FMIN, fmax=im.FMAX)
            feat_b = m3.extract_all_features_v3(core_b, sr=result_b.sr, fmin=im.FMIN, fmax=im.FMAX)
            row = {}
            for feat_name in im.JND_FEATURES:
                val_a = m3.transformed_value(feat_a, feat_name)
                val_b = m3.transformed_value(feat_b, feat_name)
                ref = m3.ref_scale_for(feat_a, feat_name)
                row[feat_name] = abs(val_a - val_b) / ref if (np.isfinite(val_a) and np.isfinite(val_b) and ref > 0) else float("nan")
            rows.append(row)
    return rows


def _jnd_composite_from_rows(rows: List[Dict[str, float]]) -> float:
    vals = []
    for f in _JND_NO_F0:
        fv = [r[f] for r in rows if np.isfinite(r[f])]
        if fv:
            vals.append(float(np.median(fv)))
    return float(np.mean(vals)) if vals else 0.0


def distinctiveness_cached(candidate_result: "rs.RenderResult", c_result: "rs.RenderResult", d_result: "rs.RenderResult") -> Tuple[float, float, float]:
    """S3 の `distinctiveness_from_parents` と同一定義（mean_f0 除く5特徴の
    中央値の平均、両親のうち小さい方）を、親の render を使い回して高速に計算する。
    戻り値: (composite = min(val_c, val_d), val_c, val_d)
    """
    rows_c = _jnd_rows_from_results(c_result, candidate_result, notes_per_phrase=2)
    rows_d = _jnd_rows_from_results(d_result, candidate_result, notes_per_phrase=2)
    val_c = _jnd_composite_from_rows(rows_c)
    val_d = _jnd_composite_from_rows(rows_d)
    return float(min(val_c, val_d)), val_c, val_d


# ---------------------------------------------------------------------------
# 個体・世代
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    candidate_id: int
    genome: pg.VoiceGenome
    op: str
    parents: Tuple[str, ...]
    seed: int
    scale: Optional[float]
    generation: int


@dataclass
class EvalRecord:
    candidate: Candidate
    quick: "gv.QuickS5Result"
    linkability: "gv.LinkabilityResult"
    distinctiveness: float
    dist_c: float
    dist_d: float


def build_gen0() -> List[Candidate]:
    # voice_C/voice_D は S2/S3 で既に安全域内（tilt=-17/-10, bw=0.80/1.30,
    # breathiness=0.0/0.40 など）に確定済みのため clip は事実上 no-op だが、
    # SAFE_BOX の定義と食い違いがないことを明示的に保証するために通す。
    parent_c = clip_to_safe_box(rs.voice_c())
    parent_d = clip_to_safe_box(rs.voice_d())
    sample1 = clip_to_safe_box(gv.gate_safe_sample(S3B_SEED_BASE + 1, "s3b-sample1"))
    sample2 = clip_to_safe_box(gv.gate_safe_sample(S3B_SEED_BASE + 2, "s3b-sample2"))
    return [
        Candidate(0, parent_c, "parent", ("voice_C",), -1, None, 0),
        Candidate(1, parent_d, "parent", ("voice_D",), -1, None, 0),
        Candidate(2, sample1, "sample", (), S3B_SEED_BASE + 1, None, 0),
        Candidate(3, sample2, "sample", (), S3B_SEED_BASE + 2, None, 0),
    ]


def make_generation_candidates(parents: List[Candidate], generation_idx: int, n_candidates: int, next_id: int) -> Tuple[List[Candidate], int]:
    seed_base = S3B_SEED_BASE + generation_idx * 10000
    out: List[Candidate] = []
    cid = next_id
    n_mutate = (n_candidates + 1) // 2
    n_cross = n_candidates - n_mutate

    for k in range(n_mutate):
        p = parents[k % len(parents)]
        scale = 0.08 if k % 2 == 0 else 0.15
        seed = seed_base + 100 + k
        child = clip_to_safe_box(gv.gate_safe_mutate(p.genome, seed, scale, f"g{generation_idx}-{cid}-mut"))
        out.append(Candidate(cid, child, "mutate", (p.genome.name,), seed, scale, generation_idx))
        cid += 1

    if len(parents) >= 2:
        pairs = [(a, b) for a in range(len(parents)) for b in range(a + 1, len(parents))]
        for k in range(n_cross):
            a_idx, b_idx = pairs[k % len(pairs)]
            seed = seed_base + 200 + k
            a, b = parents[a_idx].genome, parents[b_idx].genome
            child = clip_to_safe_box(gv.gate_safe_crossover(a, b, seed, f"g{generation_idx}-{cid}-cross"))
            out.append(Candidate(cid, child, "crossover", (a.name, b.name), seed, None, generation_idx))
            cid += 1
    else:
        for k in range(n_cross):
            p = parents[0]
            seed = seed_base + 300 + k
            child = clip_to_safe_box(gv.gate_safe_mutate(p.genome, seed, 0.10, f"g{generation_idx}-{cid}-mut2"))
            out.append(Candidate(cid, child, "mutate", (p.genome.name,), seed, 0.10, generation_idx))
            cid += 1

    return out, cid


def _rank_key(r: EvalRecord):
    return (-round(r.distinctiveness, 9), r.candidate.candidate_id)


def _try_full_gates_by_distinctiveness(records: List[EvalRecord]) -> Tuple[Optional[EvalRecord], List["gv.FullGateAttempt"]]:
    """distinctiveness 降順・candidate_id 昇順で順にフル S5 gate1-6 を試し、
    最初に全通過した個体を返す（`gv.select_final_winner_with_full_gates` は
    linkability margin 順にランクするため、distinctiveness 順が要件の
    fail-closed フォールバックでは使えない。同じ判定ロジックを距離順で
    ラップし直したもの）。
    """
    attempts: List["gv.FullGateAttempt"] = []
    for r in sorted(records, key=_rank_key):
        g = r.candidate.genome
        result = rs.render_sakura(g)
        rows = gc.measure_notes(result)
        g1 = gc.gate1_f0_tracking(rows)
        g2 = gc.gate2_plausibility(rows)
        g3 = gc.gate3_consonant_existence(result)
        g4 = gc.gate4_determinism(g)
        g5 = gc.gate5_aliasing(result.wav, result.sr)
        g6 = gc.gate6_grip_quick_check(g)
        passed = bool(g1["pass"] and g2["pass"] and g3["pass"] and g4["pass"] and g5["pass"] and g6["pass"])
        attempts.append(gv.FullGateAttempt(
            r.candidate.candidate_id, g.name, "distinctiveness_rank", passed,
            {"gate1": g1, "gate2": g2, "gate3": g3, "gate4": g4, "gate5": g5, "gate6": g6},
        ))
        if passed:
            return r, attempts
    return None, attempts


@dataclass
class GenerationLog:
    generation: int
    candidates: List[Candidate]
    survivors: List[EvalRecord]
    culled: List[Tuple[Candidate, str]]
    best_distinctiveness: float
    floor_cleared_ids: List[int]


@dataclass
class MultiGenRun:
    generations: List[GenerationLog]
    all_survivors: List[EvalRecord]
    winner: Optional[EvalRecord]
    winner_meets_floor: bool
    winner_full_gate_attempts: List["gv.FullGateAttempt"]
    reference_hash: str


def run_multigen(n_generations: int = N_GENERATIONS, n_candidates: int = N_CANDIDATES_PER_GEN, verbose: bool = True) -> MultiGenRun:
    reference = gv._reference_gallery_vectors()
    ref_hash = gv.reference_set_hash(reference)
    c_result = rs.render_sakura(rs.voice_c())
    d_result = rs.render_sakura(rs.voice_d())

    gen0 = build_gen0()
    all_survivors: List[EvalRecord] = []
    gen_logs: List[GenerationLog] = []

    def evaluate(cands: List[Candidate]) -> Tuple[List[EvalRecord], List[Tuple[Candidate, str]]]:
        survivors, culled = [], []
        for c in cands:
            result = rs.render_sakura(c.genome)
            q = gv.quick_s5(c.genome, result)
            if not q.overall_pass:
                culled.append((c, "quick_s5_fail"))
                continue
            e1_0, e2_0 = im.normalized_e1_e2(result, 0)
            e1_1, e2_1 = im.normalized_e1_e2(result, 1)
            vec = ((e1_0 + e1_1) / 2.0, (e2_0 + e2_1) / 2.0)
            link = gv.linkability_audit(vec, reference)
            if not link.passed:
                culled.append((c, "linkability_fail"))
                continue
            comp, dc, dd = distinctiveness_cached(result, c_result, d_result)
            survivors.append(EvalRecord(c, q, link, comp, dc, dd))
        return survivors, culled

    # gen0 は評価のみ（親候補として使う。voice_C/voice_D 自身は linkability
    # で必ず不合格になるが、次世代の mutate/crossover の「親」としては使う）。
    parents = gen0
    next_id = 4

    winner: Optional[EvalRecord] = None
    winner_attempts: List["gv.FullGateAttempt"] = []
    winner_meets_floor = False

    for gen_idx in range(1, n_generations + 1):
        cands, next_id = make_generation_candidates(parents, gen_idx, n_candidates, next_id)
        survivors, culled = evaluate(cands)
        all_survivors.extend(survivors)
        best_d = max((s.distinctiveness for s in survivors), default=float("-inf"))
        floor_ids = [s.candidate.candidate_id for s in survivors if s.distinctiveness >= JND_FLOOR]
        gen_logs.append(GenerationLog(gen_idx, cands, survivors, culled, best_d, floor_ids))
        if verbose:
            print(f"=== gen {gen_idx}: {len(survivors)}/{len(cands)} survived, best_dist={best_d:.3f}, floor_clear={floor_ids} ===")
            for s in sorted(survivors, key=_rank_key):
                print(f"  [{s.candidate.candidate_id:3d}] {s.candidate.genome.name:20s} op={s.candidate.op:9s} dist={s.distinctiveness:6.3f} (C={s.dist_c:.3f} D={s.dist_d:.3f}) link_margin={s.linkability.margin:7.4f}")

        # floor をクリアした survivor をフル gate に通す（distinctiveness 降順）
        if winner is None:
            floor_survivors = [s for s in survivors if s.distinctiveness >= JND_FLOOR]
            w, attempts = _try_full_gates_by_distinctiveness(floor_survivors)
            winner_attempts.extend(attempts)
            if w is not None:
                winner = w
                winner_meets_floor = True
        if winner is not None:
            if verbose:
                print(f"floor + full-gate winner found at generation {gen_idx}: {winner.candidate.candidate_id}")
            break

        # 次世代の親: 生存者から distinctiveness 降順・id 昇順で上位 min(4,len) を選ぶ
        if survivors:
            parents = [s.candidate for s in sorted(survivors, key=_rank_key)[:4]]
        # survivors が空なら前世代の親をそのまま維持（停滞を明示的に許容）

    if winner is None:
        # fail-closed: 全 survivor の中から distinctiveness 降順でフル gate を試し、
        # 最初に通った個体を「床未達の最良個体」として採用する。
        w, attempts = _try_full_gates_by_distinctiveness(all_survivors)
        winner_attempts = attempts
        winner = w
        winner_meets_floor = False

    return MultiGenRun(
        generations=gen_logs, all_survivors=all_survivors, winner=winner,
        winner_meets_floor=winner_meets_floor, winner_full_gate_attempts=winner_attempts,
        reference_hash=ref_hash,
    )
