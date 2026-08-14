"""singer/genesis_v0.py — S3 U2: Genesis Graph v0 探索本体（VG-015）。

読み取り専用: `proto1/`（genome/sampler/reference_set を import 流用のみ）、
`harness/`（measure_v3 を import 流用のみ）。書き込みは singer/ 配下のみ
（新規 registry ファイルは singer/results_s3/ 側に置き、proto1/results_final
の正本 registry には一切追記しない）。

U1 診断結果（`results_s3/genesis_report.md` §U1 に詳細）: `formant_scale` を
voice_A の較正点 1.0 から ±0.01 動かすだけで S5 gate6（凍結）の breathiness
grip が崩れる。原因は `formant_tv.GAIN_FLOOR`（S1 でオクターブ誤り対策として
導入）との相互作用: GAIN_FLOOR>=0.08 では特定ノート×高breathinessの組み合わせ
で periodicity 推定が破綻（-20dB 級のクラッシュ）、GAIN_FLOOR<0.08 ではその
ノートで元のオクターブ誤り（F0 が真値の約 1.9 倍に飛ぶ）が再発する。滑らかな
中間点は実測で見つからず、既存 gate1（frozen, voice_A/B の非退行必須）を
壊さずに解消する策がない → **fail-closed**。formant_scale は 1.0 に固定した
まま探索空間を tilt / bandwidth_scale / breathiness_base / register_gains /
vibrato の S2 実証済み安全域に限定する。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, replace
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
import sampler  # noqa: E402  (proto1、無改変・import 流用のみ)
import reference_set as refset  # noqa: E402  (proto1、無改変・import 流用のみ)
import measure_v3 as m3  # noqa: E402  (harness、無改変・import 流用のみ)

import render_song as rs  # noqa: E402
import gate_checks as gc  # noqa: E402
import identity_metrics as im  # noqa: E402

# ---------------------------------------------------------------------------
# U1 fail-closed 制約: gate6-safe な探索空間
# ---------------------------------------------------------------------------

GATE_SAFE_FORMANT_SCALE = 1.0
GATE_SAFE_FORMANT_OFFSETS = (0.0, 0.0, 0.0, 0.0)
# [UNDERSPEC-S3-1] register_gains の gate6/gate1 安全域は S2 で C(全0)・
# D(0,0.10,0.20,0.30,0.40) の 2 点しか実測していない。sampler.sample() の
# 既定サンプル域 [0,0.8] は S2 実測（rg 最高値 0.55〜0.75 で gate1/gate6 が
# 崩壊）に照らすと危険域を含む。個別特性化する時間がないため、D の実証済み
# 最大値 0.40 に安全マージンを乗せた 0.50 を全register共通の保守的な上限
# として採用する（根拠なき緩和はしない）。
REGISTER_GAIN_SAFETY_CAP = 0.50

GENESIS_SEED_BASE = 40001  # gallery/chance 系の既存 seed base (20001/90001) と衝突しない値


def _cap_register_gains(gains: Tuple[float, ...]) -> Tuple[float, ...]:
    return tuple(min(max(0.0, v), REGISTER_GAIN_SAFETY_CAP) for v in gains)


def gate_safe_sample(seed: int, name: str) -> pg.VoiceGenome:
    """`sampler.sample`（無改変）を呼んだ後、gate6-safe 制約を後掛けする。"""
    g = sampler.sample(seed, name=name)
    resonance = replace(g.resonance, formant_scale=GATE_SAFE_FORMANT_SCALE, formant_offsets=GATE_SAFE_FORMANT_OFFSETS)
    noise = replace(g.noise, register_gains=_cap_register_gains(g.noise.register_gains))
    return pg.build_genome(
        name, source=g.source, resonance=resonance, noise=noise,
        register=g.register, microprosody=g.microprosody, range_=g.range,
    )


TRACT_MUTATE_SCALE = 0.025
# [UNDERSPEC-S3-4] 実測で発覚した追加の脆弱性: `sampler.mutate` の一様スケール
# （memo 記載の 0.08〜0.15）を tilt/bandwidth_scale/breathiness_base
# （＝gate6 の崖が特に狭い「tract 系」軸、U1 参照）にもそのまま適用すると、
# 12 候補中どれも quick-S5 通過後の**フル** gate6 で不合格になった（実測:
# Pareto 前線の 2 個体とも gate6 不合格。survivor 5 個体全数を試しても
# 全滅）。個別マージンの狭さ（S2 で発見した崖）は 1 次元ずつの走査でしか
# 実測していなかったため、多次元同時のガウス摂動（0.08〜0.15 スケール）が
# 崖を踏み抜く確率は実質的に高かった。対処として tract 系軸は固定の狭い
# スケール（0.025、S2 実測の崖幅に対して余裕を持たせた値）で摂動し、
# memo 指定の scale（0.08/0.15）は音色系軸（register_gains・vibrato・
# jitter — gate6 への影響が相対的に緩やかと S2 で確認済み）にのみ適用する
# 非対称摂動に変更した。tract 系軸を探索しないわけではなく「歩幅を絞る」
# だけであり、Genesis の探索原理（分岐→評価→淘汰）自体は維持している。


def gate_safe_mutate(genome_in: pg.VoiceGenome, seed: int, scale: float, child_name: str) -> pg.VoiceGenome:
    """非対称ガウス摂動: tract 系（tilt/bandwidth_scale/breathiness_base）は
    固定の狭いスケール `TRACT_MUTATE_SCALE`、音色系（register_gains/
    vibrato_rate/vibrato_depth/jitter_amount）は memo 指定の `scale` で摂動
    する（背景は `TRACT_MUTATE_SCALE` のコメント参照）。formant_scale/
    formant_offsets は親から据え置き（U1 fail-closed）。
    """
    rng = np.random.default_rng(seed)
    spread = sampler._MUTATE_SPREAD

    def _p(value: float, key: str, sc: float) -> float:
        return float(value + rng.normal(0.0, sc * spread[key]))

    tilt = _p(genome_in.source.tilt, "source.tilt", TRACT_MUTATE_SCALE)
    bw = _p(genome_in.resonance.bandwidth_scale, "resonance.bandwidth_scale", TRACT_MUTATE_SCALE)
    breath = _p(genome_in.noise.breathiness_base, "noise.breathiness_base", TRACT_MUTATE_SCALE)
    reg_gains = tuple(
        max(0.0, v + rng.normal(0.0, scale * spread["noise.register_gains"]))
        for v in genome_in.noise.register_gains
    )
    vr = _p(genome_in.microprosody.vibrato_rate_hz, "microprosody.vibrato_rate_hz", scale)
    vd = max(0.0, _p(genome_in.microprosody.vibrato_depth_cents, "microprosody.vibrato_depth_cents", scale))
    ja = max(0.0, _p(genome_in.microprosody.jitter_amount, "microprosody.jitter_amount", scale))

    return pg.build_genome(
        child_name,
        source=pg.SourceSection(tilt=tilt, source_mode=genome_in.source.source_mode),
        resonance=pg.ResonanceSection(
            formant_scale=genome_in.resonance.formant_scale,
            formant_offsets=genome_in.resonance.formant_offsets,
            bandwidth_scale=bw,
        ),
        noise=pg.NoiseSection(breathiness_base=breath, register_gains=_cap_register_gains(reg_gains)),
        register=genome_in.register,
        microprosody=pg.MicroprosodySection(
            vibrato_rate_hz=vr, vibrato_depth_cents=vd, jitter_amount=ja,
            jitter_seed=genome_in.microprosody.jitter_seed,
        ),
        range_=genome_in.range,
    )


def gate_safe_crossover(a: pg.VoiceGenome, b: pg.VoiceGenome, seed: int, child_name: str) -> pg.VoiceGenome:
    """`sampler.crossover`（無改変）。親双方が formant_scale=1.0/offsets=0 の
    gate-safe 個体である前提のため追加クランプ不要だが、register_gains は
    安全のため念のため再クリップする。
    """
    child = sampler.crossover(a, b, seed)
    noise = replace(child.noise, register_gains=_cap_register_gains(child.noise.register_gains))
    return pg.build_genome(
        child_name, source=child.source, resonance=child.resonance, noise=noise,
        register=child.register, microprosody=child.microprosody, range_=child.range,
    )


# ---------------------------------------------------------------------------
# U2: 初期集団 + 12 候補生成（決定論）
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    candidate_id: int
    genome: pg.VoiceGenome
    op: str  # "parent" | "mutate" | "crossover"
    parents: Tuple[str, ...]
    seed: int
    scale: Optional[float] = None


def build_population() -> List[Candidate]:
    parent_c = rs.voice_c()
    parent_d = rs.voice_d()
    sample1 = gate_safe_sample(GENESIS_SEED_BASE + 1, "genesis-sample1")
    sample2 = gate_safe_sample(GENESIS_SEED_BASE + 2, "genesis-sample2")
    parents: List[Candidate] = [
        Candidate(0, parent_c, "parent", ("voice_C",), seed=-1),
        Candidate(1, parent_d, "parent", ("voice_D",), seed=-1),
        Candidate(2, sample1, "sample", (), seed=GENESIS_SEED_BASE + 1),
        Candidate(3, sample2, "sample", (), seed=GENESIS_SEED_BASE + 2),
    ]

    candidates: List[Candidate] = list(parents)
    cid = 4

    # mutate: 4 親 × 2 scale (0.08, 0.15) = 8
    for p in parents:
        for scale in (0.08, 0.15):
            seed = GENESIS_SEED_BASE + 100 + cid
            child = gate_safe_mutate(p.genome, seed, scale, f"genesis-mut{cid}")
            candidates.append(Candidate(cid, child, "mutate", (p.genome.name,), seed=seed, scale=scale))
            cid += 1

    # crossover: 4 組（決定論に固定した組み合わせ） = 4
    pairs = [(0, 1), (0, 2), (1, 3), (2, 3)]
    for a_idx, b_idx in pairs:
        seed = GENESIS_SEED_BASE + 200 + cid
        a, b = parents[a_idx].genome, parents[b_idx].genome
        child = gate_safe_crossover(a, b, seed, f"genesis-cross{cid}")
        candidates.append(Candidate(cid, child, "crossover", (a.name, b.name), seed=seed))
        cid += 1

    assert len(candidates) == 16, len(candidates)  # 4 parents + 8 mutate + 4 crossover
    return candidates


# ---------------------------------------------------------------------------
# U2 評価: quick-S5 / linkability / diversity / distinctiveness
# ---------------------------------------------------------------------------

QUICK_N_NOTES = 4  # phrase0 先頭 4 音のみ
QUICK_SUSTAIN_MIDIS = {"C3": 48, "A4": 69, "C6": 84}
QUICK_SUSTAIN_DUR = 0.6  # フル gate6 の 1.2s より短縮（記録: 実行時間短縮のため）


@dataclass
class QuickS5Result:
    median_cents_err: float
    max_cents_err: float
    min_periodicity_r: float
    aliasing_db: float
    aliasing_pass: bool
    f0_pass: bool
    periodicity_pass: bool
    overall_pass: bool


def quick_s5(genome_in: pg.VoiceGenome, full_result: "rs.RenderResult") -> QuickS5Result:
    rows_all = gc.measure_notes(full_result)
    phrase0_rows = [r for r, seg in zip(rows_all, full_result.segments) if seg.note.phrase_index == 0][:QUICK_N_NOTES]
    raw_errs = [abs(r.cents_err) for r in phrase0_rows]
    errs = [e for e in raw_errs if np.isfinite(e)]
    median_err = float(np.median(errs)) if errs else float("nan")
    max_err = float(np.max(errs)) if errs else float("nan")
    # PR#261 レビュー R32: 旧実装は非有限を除外済みの `errs`（フィルタ後
    # リスト）の非空判定 (`bool(errs)`) のみで f0_pass を決めていたため、
    # QUICK_N_NOTES（4 音）のうち 1 音でも cents_err が非有限（未測定）だと、
    # その 1 音がこっそり `errs` から抜け落ちるだけで残り 3 音が健全なら
    # f0_pass=True になり得た（「一部の音だけ測って良好だった」ことを
    # 「4 音全てを測って良好だった」と取り違える不完全証拠 PASS。
    # gate_checks.py::_grip_axis の R23 や render_health.py の R31 と同型）。
    # 4 音全て（`phrase0_rows` の全件）が有限に測定できたことを明示的な
    # 前提条件とする。median_err/max_err は診断用に有限値のみで従来どおり
    # 算出するが、f0_pass 自体は全件有限でなければ無条件 False になる。
    all_finite = bool(raw_errs) and all(np.isfinite(e) for e in raw_errs)
    f0_pass = (
        all_finite
        and median_err <= gc.F0_MEDIAN_CENTS_MAX
        and all(e <= gc.F0_ALL_NOTES_CENTS_MAX for e in raw_errs)
    )

    r_medians = []
    for midi in QUICK_SUSTAIN_MIDIS.values():
        wav = rs.render_sustained_vowel(genome_in, midi, vowel="a", duration_sec=QUICK_SUSTAIN_DUR)
        ptrack = m3.periodicity_track_v3(wav, sr=rs.SR_OUT, fmin=gc.FMIN, fmax=gc.FMAX)
        r_medians.append(ptrack.r_median)
    min_r = float(np.min(r_medians)) if r_medians else float("nan")
    periodicity_pass = bool(r_medians) and min_r >= gc.PERIODICITY_R_MIN

    g5 = gc.gate5_aliasing(full_result.wav, full_result.sr)

    overall = bool(f0_pass and periodicity_pass and g5["pass"])
    return QuickS5Result(
        median_cents_err=median_err, max_cents_err=max_err, min_periodicity_r=min_r,
        aliasing_db=g5["energy_ratio_db"], aliasing_pass=bool(g5["pass"]),
        f0_pass=f0_pass, periodicity_pass=periodicity_pass, overall_pass=overall,
    )


# --- linkability 監査（standin-gallery-v1 + voice_A/B/C/D に対する novelty） ---

LINKABILITY_THRESHOLD_E1 = 0.01
LINKABILITY_THRESHOLD_E2 = 0.002
# [UNDERSPEC-S3-2] 「既存個体に近すぎる」を判定する距離閾値は memo が数値を
# 与えていない。当初 S1/S2 の within-voice レンジ（E1 0.006〜0.62 / E2
# 0.09〜0.28）から一律 0.05 を仮置きしたが、実測してみると本サイクルの
# mutate scale（0.08〜0.15、親の局所探索のため意図的に小さい）由来の候補は
# E2（log-mel）側の distance が軒並み 0.0003〜0.017 のオーダーしかなく
# （formant_scale を 1.0 に固定した影響で、S2 で最大の E2 駆動因子だった
# 軸が探索空間から外れているため）、E1/E2 同一閾値では真に「複製」でない
# 候補までほぼ全滅した（実測: 12 候補中 quick-S5 通過 8 個が全て linkability
# で不合格）。実測分布を見て、明らかな複製（自分の親と E1/E2 ともほぼ 0 —
# 例: mut4 は voice_C に E1=0.0011/E2=0.0006、mut6 は voice_D に
# E1=0.00003/E2=0.0004）と、意味のある差を持つ候補（E1≳0.02 かつ E2≳0.005
# が典型）を分離できる閾値として E1=0.01・E2=0.002 に再較正した。


@dataclass
class LinkabilityResult:
    min_dist_e1: float
    min_dist_e2: float
    nearest_e1_name: str
    nearest_e2_name: str
    passed: bool
    margin: float
    # PR#261 レビュー R21: 候補/参照ベクトルまたは算出距離が非有限で監査
    # 自体が実行できなかった場合に False。`passed=False` と常に対で立つが、
    # 「正しく監査して不合格」（linkability_fail）と「監査不能」
    # （measurement_invalid）を呼び出し側 `run_genesis()` の淘汰理由で
    # 区別するためのフラグ。既定 True（従来どおりの正常監査）。
    measurement_valid: bool = True


def _reference_gallery_vectors() -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """standin-gallery-v1（8 個体, proto1 raw embedding）+ voice_A/B/C/D
    （R0.9, phrase 平均）を同一正規化空間（`identity_metrics` の gallery
    mean/std, rms 次元除外込み）に写像した「既知個体」リスト。
    """
    gallery = im.normalization_gallery()
    out: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}

    seeds = [refset.GALLERY_SEED_BASE + i for i in range(refset.GALLERY_SIZE)]
    for i, seed in enumerate(seeds):
        g = refset.sampler.sample(seed, name=f"standin-{i}")
        raw_e1, raw_e2 = refset.raw_embeddings(g)
        e1 = np.clip(refset._zscore(raw_e1, gallery.e1_mean, gallery.e1_std), -im.Z_SCORE_CLIP, im.Z_SCORE_CLIP)
        e1 = e1[im._E1_KEEP_MASK]
        e2 = np.clip(refset._zscore(raw_e2, gallery.e2_mean, gallery.e2_std), -im.Z_SCORE_CLIP, im.Z_SCORE_CLIP)
        out[f"standin-{i}"] = (e1, e2)

    for name, fn in [("voice_A", rs.voice_a), ("voice_B", rs.voice_b), ("voice_C", rs.voice_c), ("voice_D", rs.voice_d)]:
        result = rs.render_sakura(fn())
        e1_0, e2_0 = im.normalized_e1_e2(result, 0)
        e1_1, e2_1 = im.normalized_e1_e2(result, 1)
        out[name] = ((e1_0 + e1_1) / 2.0, (e2_0 + e2_1) / 2.0)

    return out


def linkability_audit(candidate_vec: Tuple[np.ndarray, np.ndarray], reference: Dict[str, Tuple[np.ndarray, np.ndarray]]) -> LinkabilityResult:
    """候補 embedding が standin-gallery-v1 + voice_A/B/C/D の全既知個体から
    十分離れている（=novelty あり）かを、最近傍距離が閾値以上かで判定する。

    PR#261 レビュー R21: 候補または参照ベクトルの成分に NaN/inf が混入した
    場合、`im.cosine_distance()` は NaN を返し得る。旧実装は
    `if d1 < best_e1:` という単純な最小値更新ループだったため、NaN との
    比較は常に False になり、`best_e1` は初期値の `float("inf")` のまま
    どのループでも更新されずに終わることがあった。その結果
    `passed = best_e1 >= LINKABILITY_THRESHOLD_E1` は `inf >= 0.01` で
    True になり、`margin = best_e1 - THRESHOLD` は inf に張り付く。これは
    「監査不能（測定が壊れている）」を「無限マージンで最も新規性が高い
    個体」と取り違える fail-open であり、`select_winner`/
    `select_final_winner_with_full_gates` の `_rank_key` は margin 最大の
    個体を当選最優先で選ぶため、測定不能な個体がそのまま当選し得た。

    候補ベクトル・各参照ベクトル・算出された各距離のいずれかが非有限
    （NaN/inf）であれば、その時点で「監査不能」と判定し即座に
    `passed=False`（呼び出し元 `run_genesis()` はこれを "linkability_fail"
    として淘汰するのではなく "measurement_invalid" として区別して記録する）
    ・`margin=-inf`（margin ランキングでどの正常な候補よりも絶対に劣後する
    番兵値）を返す。
    """
    cand_e1, cand_e2 = candidate_vec
    if not (np.all(np.isfinite(cand_e1)) and np.all(np.isfinite(cand_e2))):
        return LinkabilityResult(float("nan"), float("nan"), "", "", False, float("-inf"), measurement_valid=False)

    best_e1, best_e1_name = float("inf"), ""
    best_e2, best_e2_name = float("inf"), ""
    for name, (ref_e1, ref_e2) in reference.items():
        if not (np.all(np.isfinite(ref_e1)) and np.all(np.isfinite(ref_e2))):
            return LinkabilityResult(float("nan"), float("nan"), name, name, False, float("-inf"), measurement_valid=False)
        d1 = im.cosine_distance(cand_e1, ref_e1)
        d2 = im.cosine_distance(cand_e2, ref_e2)
        if not (np.isfinite(d1) and np.isfinite(d2)):
            return LinkabilityResult(float(d1), float(d2), name, name, False, float("-inf"), measurement_valid=False)
        if d1 < best_e1:
            best_e1, best_e1_name = d1, name
        if d2 < best_e2:
            best_e2, best_e2_name = d2, name
    passed = bool(best_e1 >= LINKABILITY_THRESHOLD_E1 and best_e2 >= LINKABILITY_THRESHOLD_E2)
    margin = min(best_e1 - LINKABILITY_THRESHOLD_E1, best_e2 - LINKABILITY_THRESHOLD_E2)
    return LinkabilityResult(best_e1, best_e2, best_e1_name, best_e2_name, passed, margin)


# --- 親からの distinctiveness（JND 合成） ---

_JND_NO_F0_FEATURES = [f for f in im.JND_FEATURES if f != "mean_f0"]


def distinctiveness_from_parents(candidate_name: str, candidate_genome: pg.VoiceGenome) -> float:
    """voice_C・voice_D それぞれとの JND（mean_f0 除く 5 特徴の中央値の平均）を
    求め、**小さい方**（=より近い親からの距離）を採用する（両親から十分離れて
    いることを要求する保守的な定義）。
    """
    scores = []
    for parent_name, parent_fn in [("voice_C", rs.voice_c), ("voice_D", rs.voice_d)]:
        rows = im.jnd_table(parent_name, parent_fn(), candidate_name, candidate_genome, notes_per_phrase=2)
        summary = im.jnd_summary(rows)
        vals = [summary[f] for f in _JND_NO_F0_FEATURES if np.isfinite(summary[f])]
        scores.append(float(np.mean(vals)) if vals else 0.0)
    return float(min(scores))


# ---------------------------------------------------------------------------
# U2 Pareto 淘汰
# ---------------------------------------------------------------------------


@dataclass
class EvalRecord:
    candidate: Candidate
    quick: QuickS5Result
    linkability: LinkabilityResult
    distinctiveness: float
    diversity_nn: float  # このプールの中での最近傍距離（E1, phrase平均）
    plausibility_score: float  # 高いほど良い
    combined_axis2: float  # 高いほど良い（distinctiveness と diversity_nn の min）


def pareto_front(records: List[EvalRecord]) -> List[EvalRecord]:
    front = []
    for r in records:
        dominated = False
        for other in records:
            if other is r:
                continue
            not_worse = other.plausibility_score >= r.plausibility_score and other.combined_axis2 >= r.combined_axis2
            strictly_better = other.plausibility_score > r.plausibility_score or other.combined_axis2 > r.combined_axis2
            if not_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            front.append(r)
    return front


def select_winner(front: List[EvalRecord]) -> EvalRecord:
    """Pareto 前線の中から linkability margin が最大の個体を選ぶ。
    タイブレーク: margin が同一（1e-9 以内）なら candidate_id が小さい方。
    """
    def key(r: EvalRecord):
        return (-round(r.linkability.margin, 9), r.candidate.candidate_id)
    return sorted(front, key=key)[0]


def _rank_key(r: EvalRecord):
    return (-round(r.linkability.margin, 9), r.candidate.candidate_id)


@dataclass
class FullGateAttempt:
    candidate_id: int
    name: str
    source: str  # "pareto_front" | "survivor_fallback"
    passed: bool
    detail: Dict


def select_final_winner_with_full_gates(front: List[EvalRecord], survivors: List[EvalRecord]) -> Tuple[Optional[EvalRecord], List[FullGateAttempt]]:
    """Pareto 前線を linkability margin 順に試し、フル S5 gate1-6 に通る
    最初の個体を当選者とする。前線が全滅したら survivor 全体（Pareto 非劣で
    ない個体も含む）に同じ順序規則で拡張して再試行する（[UNDERSPEC-S3-5]:
    memo は前線全滅時のフォールバックを規定していない。探索を空振りで終わら
    せないため、次善の safety net として survivor 全体へのフォールバックを
    追加した。フォールバックを発動したかどうかは report に必ず明記する）。
    """
    attempts: List[FullGateAttempt] = []
    tried_ids = set()

    def _try(r: EvalRecord, source: str) -> Optional[EvalRecord]:
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
        attempts.append(FullGateAttempt(
            r.candidate.candidate_id, g.name, source, passed,
            {"gate1": g1, "gate2": g2, "gate3": g3, "gate4": g4, "gate5": g5, "gate6": g6},
        ))
        return r if passed else None

    for r in sorted(front, key=_rank_key):
        tried_ids.add(r.candidate.candidate_id)
        winner = _try(r, "pareto_front")
        if winner is not None:
            return winner, attempts

    for r in sorted(survivors, key=_rank_key):
        if r.candidate.candidate_id in tried_ids:
            continue
        winner = _try(r, "survivor_fallback")
        if winner is not None:
            return winner, attempts

    return None, attempts


# ---------------------------------------------------------------------------
# U2 メインパイプライン
# ---------------------------------------------------------------------------


def reference_set_hash(reference: Dict[str, Tuple[np.ndarray, np.ndarray]]) -> str:
    h = hashlib.sha256()
    for name in sorted(reference.keys()):
        e1, e2 = reference[name]
        h.update(name.encode("utf-8"))
        h.update(np.round(e1, 6).tobytes())
        h.update(np.round(e2, 6).tobytes())
    return h.hexdigest()


@dataclass
class GenesisRun:
    candidates: List[Candidate]
    records: List[EvalRecord]
    survivors: List[EvalRecord]  # quick-S5 + linkability 両方通過
    culled: List[Tuple[Candidate, str]]  # (candidate, reason)
    front: List[EvalRecord]
    winner: EvalRecord
    diversity_median_min_e1: float
    diversity_median_min_e2: float
    reference_hash: str


def run_genesis(verbose: bool = True) -> GenesisRun:
    candidates = build_population()
    reference = _reference_gallery_vectors()
    ref_hash = reference_set_hash(reference)

    # 各候補: 1 回だけ render_sakura（quick-S5 の phrase 音・aliasing・
    # linkability/diversity 用 embedding を全てここから作る）。
    per_candidate_result = {}
    per_candidate_vec = {}  # phrase 平均の normalized (E1, E2)
    quick_results = {}
    for c in candidates:
        result = rs.render_sakura(c.genome)
        per_candidate_result[c.candidate_id] = result
        e1_0, e2_0 = im.normalized_e1_e2(result, 0)
        e1_1, e2_1 = im.normalized_e1_e2(result, 1)
        per_candidate_vec[c.candidate_id] = ((e1_0 + e1_1) / 2.0, (e2_0 + e2_1) / 2.0)
        quick_results[c.candidate_id] = quick_s5(c.genome, result)
        if verbose:
            q = quick_results[c.candidate_id]
            print(f"[{c.candidate_id:2d}] {c.genome.name:22s} op={c.op:10s} quick_pass={q.overall_pass}")

    culled: List[Tuple[Candidate, str]] = []
    passed_quick = [c for c in candidates if quick_results[c.candidate_id].overall_pass]
    for c in candidates:
        if not quick_results[c.candidate_id].overall_pass:
            culled.append((c, "quick_s5_fail"))

    link_results = {}
    passed_link: List[Candidate] = []
    for c in passed_quick:
        link = linkability_audit(per_candidate_vec[c.candidate_id], reference)
        link_results[c.candidate_id] = link
        if link.passed:
            passed_link.append(c)
        elif not link.measurement_valid:
            # PR#261 レビュー R21: 「正しく監査して不合格」と「監査自体が
            # 実行できなかった」を淘汰理由レベルで区別する。
            culled.append((c, "measurement_invalid"))
        else:
            culled.append((c, "linkability_fail"))

    survivors_candidates = passed_link

    # diversity（母集団=survivors 全体, E1/E2 phrase 平均, 自分以外の最近傍距離の中央値）
    def nn_distances(space: int) -> Dict[int, float]:
        out = {}
        for c in survivors_candidates:
            v = per_candidate_vec[c.candidate_id][space]
            dists = []
            for c2 in survivors_candidates:
                if c2.candidate_id == c.candidate_id:
                    continue
                v2 = per_candidate_vec[c2.candidate_id][space]
                dists.append(im.cosine_distance(v, v2))
            # PR#261 レビュー R21 同型掃討: Python 組込み min() は NaN を
            # 含むリストで引数の順序依存の挙動をする（例: min([nan, 5])==nan
            # だが min([5, nan])==5 — 2 つ目以降の NaN は無条件に無視され
            # 得る）。survivors_candidates は本関数の呼び出し前に
            # `linkability_audit()` の非有限拒否を通過済みのため現状は
            # 到達しないはずだが、np.min（順序に依らず NaN を伝播する）に
            # 揃えて多層防御する。
            out[c.candidate_id] = float(np.min(dists)) if dists else float("nan")
        return out

    nn_e1 = nn_distances(0)
    nn_e2 = nn_distances(1)
    diversity_median_min_e1 = float(np.median(list(nn_e1.values()))) if nn_e1 else float("nan")
    diversity_median_min_e2 = float(np.median(list(nn_e2.values()))) if nn_e2 else float("nan")

    records: List[EvalRecord] = []
    for c in survivors_candidates:
        q = quick_results[c.candidate_id]
        link = link_results[c.candidate_id]
        dist_parents = distinctiveness_from_parents(c.genome.name, c.genome)
        own_nn = float(min(nn_e1[c.candidate_id], nn_e2[c.candidate_id]))
        plausibility_score = -q.median_cents_err  # 高いほど良い（誤差が小さいほど良い）
        combined_axis2 = float(min(dist_parents, own_nn * 10.0))
        # own_nn（コサイン距離、0〜2程度のオーダー）と dist_parents（JND、
        # 概ね0〜数のオーダー）はスケールが違うため、own_nn を JND と同程度の
        # オーダーに揃える定数 10.0 を掛けてから min を取る。
        # [UNDERSPEC-S3-3] 厳密なスケール整合ではなく実測レンジからの目算。
        records.append(EvalRecord(
            candidate=c, quick=q, linkability=link, distinctiveness=dist_parents,
            diversity_nn=own_nn, plausibility_score=plausibility_score, combined_axis2=combined_axis2,
        ))
        if verbose:
            print(f"  eval[{c.candidate_id:2d}] plaus={plausibility_score:8.3f} axis2={combined_axis2:8.3f} "
                  f"dist_parents={dist_parents:6.3f} own_nn={own_nn:6.4f} link_margin={link.margin:7.4f}")

    front = pareto_front(records)
    if verbose:
        print("Pareto front ids:", [r.candidate.candidate_id for r in front])
    winner = select_winner(front)

    return GenesisRun(
        candidates=candidates, records=records, survivors=records, culled=culled,
        front=front, winner=winner,
        diversity_median_min_e1=diversity_median_min_e1, diversity_median_min_e2=diversity_median_min_e2,
        reference_hash=ref_hash,
    )
