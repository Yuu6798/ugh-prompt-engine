"""singer/identity_metrics.py — S2 T1: identity 分離度の定量化 + JND 会計。

`proto1/reference_set.py`（無改変・import 流用）の E1/E2 スタンドイン
embedding 定義（E1 = measure_v3 6 特徴集約、E2 = log-mel 64 帯域集約）を
**R0.9 で実際にレンダリングした「さくらさくら」フレーズ音声**に適用する。
reference_set.py 自体の gallery 構築（`build_reference_set`）は R0.1
（proto1/bridge.py 経由）でレンダリングした 8 個のスタンドイン Genome を
使うが、これは「E1/E2 各次元の典型的なばらつき（z-score 正規化の基準）」
を得るためだけに使う——between/within の実測対象は常に R0.9 の実音声。

between（歌手違い・内容同一）: voice_X vs voice_Y の同一フレーズ間距離。
within（歌手同一・内容違い）: 同一 voice のフレーズ0 vs フレーズ1 間距離。
分離判定: between > within が E1/E2 両系統・両フレーズで成立するか。

JND 会計: 各フレーズ先頭 3 ノート（不足フレーズは全ノート）の母音核で、
`measure_v3`（無改変）の 6 特徴を v0.3 REF_SCALE で割った効果量として
A/B 間差を算出する。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
_VT_HARNESS_DIR = _HERE.parent / "harness"
for _p in (_PROTO1_DIR, _VT_HARNESS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import reference_set as refset  # noqa: E402  (proto1、無改変・import 流用のみ)
import measure_v3 as m3  # noqa: E402  (vt_harness、無改変・import 流用のみ)

import render_song as rs  # noqa: E402

FMIN, FMAX = 55.0, 2200.0


# ---------------------------------------------------------------------------
# フレーズ音声抽出 + E1/E2 embedding
# ---------------------------------------------------------------------------


def extract_phrase_audio(result: "rs.RenderResult", phrase_index: int) -> np.ndarray:
    segs = [s for s in result.segments if s.note.phrase_index == phrase_index]
    if not segs:
        raise ValueError(f"phrase_index={phrase_index} に該当するノートがありません")
    start = segs[0].start_sample
    end = segs[-1].end_sample
    return result.wav[start:end]


def e1_e2_for_phrase(result: "rs.RenderResult", phrase_index: int) -> Tuple[np.ndarray, np.ndarray]:
    audio = extract_phrase_audio(result, phrase_index)
    e1 = refset._e1_vector_for_waveform(audio, result.sr)
    e2 = refset._e2_vector_for_waveform(audio, result.sr)
    return e1, e2


_NORM_GALLERY = None


@dataclass
class _LightGallery:
    e1_mean: np.ndarray
    e1_std: np.ndarray
    e2_mean: np.ndarray
    e2_std: np.ndarray


def normalization_gallery():
    """proto1 スタンドイン gallery（R0.1・8個体）由来の E1/E2 各次元の平均・標準偏差。

    z-score 正規化の基準にのみ使う（between/within の実測対象自体は R0.9）。
    `reference_set.build_reference_set()` はチャンス帯（200 permutation、
    各回 R0.1 で追加レンダ）まで計算するため T1 の目的（平均・標準偏差だけ）
    には過剰に重い（実測: 2分超）。gallery 8 個体の raw embedding 平均・
    標準偏差だけを計算する軽量版で代替する（`estimate_chance_band` を
    呼ばない以外は `build_reference_set` と同じ gallery 生成ロジックを
    無改変のまま流用）。1 プロセス内でキャッシュする。
    """
    global _NORM_GALLERY
    if _NORM_GALLERY is None:
        seeds = [refset.GALLERY_SEED_BASE + i for i in range(refset.GALLERY_SIZE)]
        genomes = [refset.sampler.sample(seed, name=f"standin-{i}") for i, seed in enumerate(seeds)]
        raw_pairs = [refset.raw_embeddings(g) for g in genomes]
        e1_matrix = np.stack([p[0] for p in raw_pairs], axis=0)
        e2_matrix = np.stack([p[1] for p in raw_pairs], axis=0)
        _NORM_GALLERY = _LightGallery(
            e1_mean=e1_matrix.mean(axis=0), e1_std=np.maximum(e1_matrix.std(axis=0, ddof=0), 1e-9),
            e2_mean=e2_matrix.mean(axis=0), e2_std=np.maximum(e2_matrix.std(axis=0, ddof=0), 1e-9),
        )
    return _NORM_GALLERY


Z_SCORE_CLIP = 5.0
# [UNDERSPEC-S2-2] T1 実測で発見した重大な計器欠陥: reference_set.py の
# gallery（R0.1・8 個体）の E1 の rms 次元は標準偏差が極端に小さい
# （実測: std≈0.072、他次元は 0.5〜760 のオーダー）。R0.1 が各ノートの
# 出力を一定ピーク値へ正規化する設計のため、gallery 内の genome 間で
# rms がほとんど変動しないことが原因と考えられる。この結果、rms 次元の
# z-score が他次元より 1〜2 桁大きくなり、コサイン類似度が事実上
# 「rms 次元の符号が一致するか」だけで決まってしまう（実測: 同一声の
# 2 フレーズ間で偶然 rms z-score の符号・大きさが揃うと within 距離が
# ほぼ 0 になり、tract 系次元の実質的な差が埋没する）。
# `reference_set.py` は無改変（変更禁止）のため、本ファイル側で z-score
# を ±`Z_SCORE_CLIP` にクリップしてから距離計算する頑健化を導入した
# （1 次元の病的なスケールが距離全体を支配することを防ぐ）。
# スタンドイン計器である旨の instrument-validity caveat はこの頑健化後も
# 引き続き有効（真の識別器ではない）。

E1_DEGENERATE_DIMS = frozenset({"rms"})
# [UNDERSPEC-S2-3] Z_SCORE_CLIP だけでは不十分と実測判明: rms 次元の
# gallery std が極端に小さいため、クリップ後も raw 値のわずかな揺らぎで
# z-score の**符号**が gallery 平均を挟んで反転する（実測: voice_C の
# phrase0 rms z=+5.00 / phrase1 rms z=-5.00 — 同一声・同一 register 帯の
# 2 フレーズ間でだけこの符号反転が起こり、within 距離を人為的に押し上げて
# between>within 判定を覆す。voice_D では両フレーズとも z=+5.00 で反転せず、
# 対照的にこちらは between 距離を人為的に圧縮した）。この符号は tract/
# identity 情報を運んでおらず、gallery 平均近傍のノイズが増幅されている
# だけと判断できる（実測値: raw rms は C phrase0=-18.21, phrase1=-20.25,
# D phrase0=-16.24, phrase1=-17.82 と全て同オーダーで、符号反転を正当化する
# ほどの実質差はない）。よってコサイン距離計算では rms 次元を除外する
# （JND 会計表は per-feature 個別集計のため rms も従来通り記載し続ける—
# 除外は集約 embedding 距離のみ）。

_E1_KEEP_MASK = np.array([name not in E1_DEGENERATE_DIMS for name in m3.FEATURE_NAMES_V3])


def normalized_e1_e2(result: "rs.RenderResult", phrase_index: int) -> Tuple[np.ndarray, np.ndarray]:
    gallery = normalization_gallery()
    raw_e1, raw_e2 = e1_e2_for_phrase(result, phrase_index)
    norm_e1 = np.clip(refset._zscore(raw_e1, gallery.e1_mean, gallery.e1_std), -Z_SCORE_CLIP, Z_SCORE_CLIP)
    norm_e1 = norm_e1[_E1_KEEP_MASK]
    norm_e2 = np.clip(refset._zscore(raw_e2, gallery.e2_mean, gallery.e2_std), -Z_SCORE_CLIP, Z_SCORE_CLIP)
    return norm_e1, norm_e2


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - refset._cosine_similarity(a, b)


# ---------------------------------------------------------------------------
# T1-1: between vs within 分離判定
# ---------------------------------------------------------------------------


@dataclass
class SeparationReport:
    voice_x: str
    voice_y: str
    between_e1: Dict[int, float]  # phrase_index -> distance
    between_e2: Dict[int, float]
    within_x_e1: float  # phrase0 vs phrase1、voice_x
    within_x_e2: float
    within_y_e1: float
    within_y_e2: float
    separation_e1_phrase0: bool
    separation_e1_phrase1: bool
    separation_e2_phrase0: bool
    separation_e2_phrase1: bool
    all_separated: bool
    margins: Dict[str, float]


def measure_separation(voice_x_name: str, genome_x, voice_y_name: str, genome_y) -> SeparationReport:
    result_x = rs.render_sakura(genome_x)
    result_y = rs.render_sakura(genome_y)

    e1_x0, e2_x0 = normalized_e1_e2(result_x, 0)
    e1_x1, e2_x1 = normalized_e1_e2(result_x, 1)
    e1_y0, e2_y0 = normalized_e1_e2(result_y, 0)
    e1_y1, e2_y1 = normalized_e1_e2(result_y, 1)

    between_e1 = {0: cosine_distance(e1_x0, e1_y0), 1: cosine_distance(e1_x1, e1_y1)}
    between_e2 = {0: cosine_distance(e2_x0, e2_y0), 1: cosine_distance(e2_x1, e2_y1)}
    within_x_e1 = cosine_distance(e1_x0, e1_x1)
    within_x_e2 = cosine_distance(e2_x0, e2_x1)
    within_y_e1 = cosine_distance(e1_y0, e1_y1)
    within_y_e2 = cosine_distance(e2_y0, e2_y1)

    # 分離判定: between（両フレーズ）が within（両声の中でより大きい方）を上回るか
    # [UNDERSPEC-S2-1] memo は within をどちらの voice のを使うか明記していない。
    # 両者のうち大きい方（=より厳しい基準）を採用する。
    within_e1_max = max(within_x_e1, within_y_e1)
    within_e2_max = max(within_x_e2, within_y_e2)

    sep_e1_p0 = between_e1[0] > within_e1_max
    sep_e1_p1 = between_e1[1] > within_e1_max
    sep_e2_p0 = between_e2[0] > within_e2_max
    sep_e2_p1 = between_e2[1] > within_e2_max

    return SeparationReport(
        voice_x=voice_x_name, voice_y=voice_y_name,
        between_e1=between_e1, between_e2=between_e2,
        within_x_e1=within_x_e1, within_x_e2=within_x_e2,
        within_y_e1=within_y_e1, within_y_e2=within_y_e2,
        separation_e1_phrase0=sep_e1_p0, separation_e1_phrase1=sep_e1_p1,
        separation_e2_phrase0=sep_e2_p0, separation_e2_phrase1=sep_e2_p1,
        all_separated=bool(sep_e1_p0 and sep_e1_p1 and sep_e2_p0 and sep_e2_p1),
        margins={
            "e1_phrase0": between_e1[0] - within_e1_max,
            "e1_phrase1": between_e1[1] - within_e1_max,
            "e2_phrase0": between_e2[0] - within_e2_max,
            "e2_phrase1": between_e2[1] - within_e2_max,
        },
    )


# ---------------------------------------------------------------------------
# T1-2: JND 会計表
# ---------------------------------------------------------------------------

JND_FEATURES = m3.FEATURE_NAMES_V3  # ["mean_f0","formant_centroid","source_tilt","periodicity","rms","vibrato_depth"]


@dataclass
class JndRow:
    phrase_index: int
    note_in_phrase: int
    kana: str
    midi: float
    jnd_by_feature: Dict[str, float]
    raw_a: Dict[str, float]
    raw_b: Dict[str, float]


def _vowel_core(result: "rs.RenderResult", seg) -> np.ndarray:
    n = seg.end_sample - seg.start_sample
    lo = seg.start_sample + int(n * 0.25)
    hi = seg.start_sample + int(n * 0.75)
    core = result.wav[lo:hi]
    if len(core) < 256:
        core = result.wav[seg.start_sample:seg.end_sample]
    return core


def jnd_table(name_a: str, genome_a, name_b: str, genome_b, notes_per_phrase: int = 3) -> List[JndRow]:
    result_a = rs.render_sakura(genome_a)
    result_b = rs.render_sakura(genome_b)

    rows: List[JndRow] = []
    phrase_indices = sorted(set(s.note.phrase_index for s in result_a.segments))
    for phrase_idx in phrase_indices:
        segs = [s for s in result_a.segments if s.note.phrase_index == phrase_idx]
        segs_b = [s for s in result_b.segments if s.note.phrase_index == phrase_idx]
        for i in range(min(notes_per_phrase, len(segs))):
            seg_a = segs[i]
            seg_b = segs_b[i]
            core_a = _vowel_core(result_a, seg_a)
            core_b = _vowel_core(result_b, seg_b)
            feat_a = m3.extract_all_features_v3(core_a, sr=result_a.sr, fmin=FMIN, fmax=FMAX)
            feat_b = m3.extract_all_features_v3(core_b, sr=result_b.sr, fmin=FMIN, fmax=FMAX)

            jnd = {}
            raw_a_d = {}
            raw_b_d = {}
            for feat_name in JND_FEATURES:
                val_a = m3.transformed_value(feat_a, feat_name)
                val_b = m3.transformed_value(feat_b, feat_name)
                ref = m3.ref_scale_for(feat_a, feat_name)
                raw_a_d[feat_name] = val_a
                raw_b_d[feat_name] = val_b
                if np.isfinite(val_a) and np.isfinite(val_b) and ref > 0:
                    jnd[feat_name] = abs(val_a - val_b) / ref
                else:
                    jnd[feat_name] = float("nan")

            rows.append(
                JndRow(
                    phrase_index=phrase_idx, note_in_phrase=i, kana=seg_a.note.mora.kana, midi=seg_a.note.midi,
                    jnd_by_feature=jnd, raw_a=raw_a_d, raw_b=raw_b_d,
                )
            )
    return rows


def jnd_summary(rows: List[JndRow]) -> Dict[str, float]:
    """特徴ごとの中央値 JND（欠測は無視）。"""
    out = {}
    for feat_name in JND_FEATURES:
        vals = [r.jnd_by_feature[feat_name] for r in rows if np.isfinite(r.jnd_by_feature[feat_name])]
        out[feat_name] = float(np.median(vals)) if vals else float("nan")
    return out
