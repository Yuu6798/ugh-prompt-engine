"""reference_set.py — P5 (VG-016 + VG-018 lite): reference-set/0.2 + linkability 監査。

`proto1_design_memo.md` §P5 の縮約実装:

  - sidecar `reference-set/0.2`（{id, version, created_at, source_datasets,
    embedding_models, coverage_notes, chance_band_procedure, sha256}）。
  - スタンドイン gallery: 8 個の固定 seed Genome を sustain+phrase probe で
    レンダリングし登録（実在歌手ではない。§7.5 の要求どおり provenance に
    instrument-validity caveat を明記する）。
  - 2 系統スタンドイン embedding:
      E1 = measure_v3 特徴ベクトル（probe 横断の頻度正規化済み集約）
      E2 = log-mel 帯域エネルギー平均ベクトル（librosa, 64 帯域）
  - チャンスレベル帯 = permutation（200 回）による最近傍類似分布の 95 パーセン
    タイル。厳密な「gallery ラベルシャッフル」は有限固定集合で縮退するため、
    無関係な合成候補 200 個をサンプルして代替する
    （underspec_log_p1.md [UNDERSPEC-P1-9] に詳細と根拠）。
  - 監査は E1・E2 の両系統で最近傍類似がチャンス帯以下なら PASS。
  - reference_set_hash が変わったら過去エントリに stale_audit を立てる
    再監査トリガー（`LinkabilityAuditLog.mark_stale`）。

  `reference-set/0.2`（PR#261 レビュー R4）: チャンス帯手続きパラメータ
  （`chance_seed_base` / `n_permutations` / パーセンタイル）を sidecar へ
  可視化し、`reference_set_hash`（sha256）の被覆にも含める。旧版
  （0.1）はこれらのパラメータを変えても hash が変わらず、`e1_pass`/
  `e2_pass` を直接左右する判定手続きが異なるのに同一 ID を共有し得た
  （chance-band 手続きの版管理が抜けていた欠陥。詳細は
  `voice_genesis/README.md` の「reference-set の版と stale_audit」節）。
  フィールド追加は版管理された sidecar の規律に従い schema_version を
  bump する（0.1→0.2）。0.1 時代に生成済みの registry/lineage 記録の
  `audit.reference_set_hash` は書き換えない（歴史的記録として保存し、
  0.2 の新規 gallery に対しては意図的に stale_audit 扱いとなる。
  `mark_stale()` の既定の挙動どおり）。

E1/E2 の集約方法の詳細は [UNDERSPEC-P1-10]・[UNDERSPEC-P1-11] を参照。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

import bridge  # sys.path に harness を追加する副作用込み
import probes
import sampler
from genome import VoiceGenome, to_dict
from hashing import sha256_of_canonical_json
from registry import genome_content_hash

import measure_v3 as mv3  # harness、無改変 import 流用
import librosa

SCHEMA_VERSION = "reference-set/0.2"

GALLERY_SIZE = 8
GALLERY_SEED_BASE = 20001  # sample() の seed 域（CHANCE_SEED_BASE と重複させない）
CHANCE_SEED_BASE = 90001
CHANCE_N_PERMUTATIONS = 200
# estimate_chance_band() の np.percentile に渡すパーセンタイル（PR#261 レビュー R4:
# チャンス帯手続きの凍結定数として named constant 化し、sidecar/hash 双方から
# 参照する単一の真実源にする）。
CHANCE_BAND_PERCENTILE: float = 95.0

# P5 memo: 「sustain+phrase probe」
EMBEDDING_PROBES: Tuple[str, ...] = ("sustain", "phrase")

N_MEL_BANDS = 64
EPS = 1e-12

PROVENANCE_CAVEAT = "measured (instrument-validity caveat: stand-in embeddings)"

COVERAGE_NOTES = (
    "合成スタンドイン。実在歌手 embedding は machine_dependent で未実装。"
    "チャンス帯は gallery ラベルシャッフルの字義通りの実装では有限固定集合ゆえに"
    "縮退するため、gallery と無関係な合成候補 200 個の最近傍類似分布で代替した"
    "（underspec_log_p1.md [UNDERSPEC-P1-9]）。E1/E2 いずれも実在人間声で"
    "訓練された識別器ではなく、`" + PROVENANCE_CAVEAT + "` の意味での「測定値」である。"
)


# ---------------------------------------------------------------------------
# 埋め込み計算（E1 / E2）
# ---------------------------------------------------------------------------


def _e1_vector_for_waveform(sig: np.ndarray, sr: int) -> np.ndarray:
    feat = mv3.extract_all_features_v3(sig, sr=sr)
    return np.array([mv3.transformed_value(feat, name) for name in mv3.FEATURE_NAMES_V3], dtype=np.float64)


def _e2_vector_for_waveform(sig: np.ndarray, sr: int) -> np.ndarray:
    mel = librosa.feature.melspectrogram(y=sig.astype(np.float32), sr=sr, n_mels=N_MEL_BANDS)
    log_mel = np.log10(mel + EPS)
    return log_mel.mean(axis=1).astype(np.float64)


def _aggregate_probe_vectors(genome: VoiceGenome, vector_fn) -> np.ndarray:
    """probe 横断の頻度正規化済み集約（[UNDERSPEC-P1-10]/[UNDERSPEC-P1-11] の 2 段平均）。

    (a) 同一 probe 内の各音のベクトルを probe 内で平均する。
    (b) EMBEDDING_PROBES に含まれる probe 群を等重みで平均する。

    [UNDERSPEC-P1-10 追記] E1 の vibrato_depth 次元は、phrase probe の 0.5s
    ノートのように観測窓が短いと `measure_v3.vibrato_depth_robust_v3` が
    NaN を返すことがある（accepted フレーム数不足）。この NaN が伝播すると
    z-score/コサイン類似度が総崩れになるため、(a)(b) いずれの平均も
    `np.nanmean` で NaN を除外して集約し、それでも全 probe が NaN だった
    次元だけ 0.0 にフォールバックする（「ロングトーンでは測れたが短音では
    測れなかった」という実測の欠測を、値の捏造ではなく明示的な既定値で
    埋める設計）。
    """
    probe_vectors = []
    for probe_name in EMBEDDING_PROBES:
        result = probes.render_probe(genome, probe_name)
        note_vectors = np.stack([vector_fn(wave, bridge.SR) for wave in result.waveforms], axis=0)
        with np.errstate(all="ignore"):
            probe_vectors.append(np.nanmean(note_vectors, axis=0))
    with np.errstate(all="ignore"):
        agg = np.nanmean(np.stack(probe_vectors, axis=0), axis=0)
    return np.where(np.isnan(agg), 0.0, agg)


def raw_embeddings(genome: VoiceGenome) -> Tuple[np.ndarray, np.ndarray]:
    """(E1_raw, E2_raw) の正規化前ベクトルを返す。"""
    e1 = _aggregate_probe_vectors(genome, _e1_vector_for_waveform)
    e2 = _aggregate_probe_vectors(genome, _e2_vector_for_waveform)
    return e1, e2


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + EPS
    return float(np.dot(a, b) / denom)


def _zscore(raw: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (raw - mean) / std


# ---------------------------------------------------------------------------
# gallery / reference set 構築
# ---------------------------------------------------------------------------


@dataclass
class GalleryMember:
    genome: VoiceGenome
    seed: int
    e1_raw: np.ndarray
    e2_raw: np.ndarray


@dataclass
class ReferenceSetGallery:
    schema_version: str
    id: str
    version: int
    created_at: str
    source_datasets: List[str]
    embedding_models: List[Dict[str, str]]
    coverage_notes: str
    sha256: str  # reference_set_hash
    members: List[GalleryMember]
    e1_mean: np.ndarray
    e1_std: np.ndarray
    e2_mean: np.ndarray
    e2_std: np.ndarray
    e1_normalized: np.ndarray  # (n_gallery, d1)
    e2_normalized: np.ndarray  # (n_gallery, d2)
    e1_chance_band_p95: float
    e2_chance_band_p95: float
    chance_n_permutations: int
    chance_seed_base: int

    def sidecar_dict(self) -> Dict[str, Any]:
        """sidecar 本体（重い embedding/gallery データを含まない、メモが規定する形）。

        `chance_band_procedure`（PR#261 レビュー R4, schema 0.2 で追加）:
        チャンス帯推定の再現に必要な手続きパラメータを可視化する。これらの
        値は `sha256` の被覆にも含まれるため、`chance_seed_base` や
        `n_permutations` を変えて別の判定手続きで作った gallery は
        自動的に別の `reference_set_hash` になる。
        """
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "source_datasets": self.source_datasets,
            "embedding_models": self.embedding_models,
            "coverage_notes": self.coverage_notes,
            "chance_band_procedure": {
                "chance_seed_base": self.chance_seed_base,
                "n_permutations": self.chance_n_permutations,
                "percentile": CHANCE_BAND_PERCENTILE,
            },
            "sha256": self.sha256,
        }


def _embedding_models_metadata() -> List[Dict[str, str]]:
    return [
        {
            "id": "E1-measure_v3-agg",
            "kind": "handcrafted-acoustic-features",
            "provenance": PROVENANCE_CAVEAT,
            "description": (
                "measure_v3 6 特徴 (mean_f0/formant_centroid/source_tilt/periodicity/"
                "rms/vibrato_depth) の probe 横断頻度正規化済み集約（sustain+phrase）"
            ),
        },
        {
            "id": "E2-logmel64-agg",
            "kind": "log-mel-band-energy",
            "provenance": PROVENANCE_CAVEAT,
            "description": (
                f"librosa log-mel {N_MEL_BANDS} 帯域の probe 横断頻度正規化済み平均（sustain+phrase）"
            ),
        },
    ]


def estimate_chance_band(
    e1_mean: np.ndarray,
    e1_std: np.ndarray,
    e1_normalized: np.ndarray,
    e2_mean: np.ndarray,
    e2_std: np.ndarray,
    e2_normalized: np.ndarray,
    chance_seed_base: int = CHANCE_SEED_BASE,
    n_permutations: int = CHANCE_N_PERMUTATIONS,
) -> Tuple[float, float]:
    """gallery と無関係な合成候補 n_permutations 個の最近傍類似分布から 95 パーセンタイルを推定する。

    字義通りの「gallery ラベルシャッフル」が有限固定集合で縮退する理由と、
    本関数が代わりに実装する具体的手続きは underspec_log_p1.md
    [UNDERSPEC-P1-9] を参照。
    """
    e1_sims: List[float] = []
    e2_sims: List[float] = []
    for i in range(n_permutations):
        seed = chance_seed_base + i
        candidate = sampler.sample(seed, name=f"chance-{seed}")
        raw_e1, raw_e2 = raw_embeddings(candidate)
        norm_e1 = _zscore(raw_e1, e1_mean, e1_std)
        norm_e2 = _zscore(raw_e2, e2_mean, e2_std)
        e1_sims.append(max(_cosine_similarity(norm_e1, row) for row in e1_normalized))
        e2_sims.append(max(_cosine_similarity(norm_e2, row) for row in e2_normalized))
    e1_band = float(np.percentile(np.array(e1_sims), CHANCE_BAND_PERCENTILE))
    e2_band = float(np.percentile(np.array(e2_sims), CHANCE_BAND_PERCENTILE))
    return e1_band, e2_band


def build_reference_set(
    *,
    gallery_seed_base: int = GALLERY_SEED_BASE,
    n_gallery: int = GALLERY_SIZE,
    chance_seed_base: int = CHANCE_SEED_BASE,
    n_permutations: int = CHANCE_N_PERMUTATIONS,
    reference_set_id: str = "standin-gallery-v1",
    version: int = 1,
    now: Optional[datetime] = None,
) -> ReferenceSetGallery:
    """スタンドイン gallery を作り、reference-set/0.2 sidecar 一式を組み立てる。"""
    seeds = [gallery_seed_base + i for i in range(n_gallery)]
    genomes = [sampler.sample(seed, name=f"standin-{i}") for i, seed in enumerate(seeds)]

    raw_pairs = [raw_embeddings(g) for g in genomes]
    e1_raw_matrix = np.stack([p[0] for p in raw_pairs], axis=0)
    e2_raw_matrix = np.stack([p[1] for p in raw_pairs], axis=0)

    e1_mean = e1_raw_matrix.mean(axis=0)
    e1_std = np.maximum(e1_raw_matrix.std(axis=0, ddof=0), 1e-9)
    e2_mean = e2_raw_matrix.mean(axis=0)
    e2_std = np.maximum(e2_raw_matrix.std(axis=0, ddof=0), 1e-9)

    e1_normalized = _zscore(e1_raw_matrix, e1_mean, e1_std)
    e2_normalized = _zscore(e2_raw_matrix, e2_mean, e2_std)

    members = [
        GalleryMember(genome=g, seed=seed, e1_raw=raw[0], e2_raw=raw[1])
        for g, seed, raw in zip(genomes, seeds, raw_pairs)
    ]

    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

    content_for_hash = {
        "reference_set_id": reference_set_id,
        "version": version,
        "gallery_genomes": [to_dict(g) for g in genomes],
        "gallery_seeds": seeds,
        "e1_normalized": np.round(e1_normalized, 8).tolist(),
        "e2_normalized": np.round(e2_normalized, 8).tolist(),
        "e1_mean": np.round(e1_mean, 8).tolist(),
        "e1_std": np.round(e1_std, 8).tolist(),
        "e2_mean": np.round(e2_mean, 8).tolist(),
        "e2_std": np.round(e2_std, 8).tolist(),
        # PR#261 レビュー R4: チャンス帯手続きパラメータを hash 被覆に含める。
        # これが無いと chance_seed_base/n_permutations を変えて e1_pass/e2_pass
        # を直接左右するチャンス帯閾値が変わっても reference_set_hash が同一の
        # ままになり、異なる判定手続きが同一 ID を共有してしまう。
        "chance_seed_base": chance_seed_base,
        "n_permutations": n_permutations,
        "chance_band_percentile": CHANCE_BAND_PERCENTILE,
    }
    reference_set_hash = sha256_of_canonical_json(content_for_hash)

    e1_band, e2_band = estimate_chance_band(
        e1_mean,
        e1_std,
        e1_normalized,
        e2_mean,
        e2_std,
        e2_normalized,
        chance_seed_base=chance_seed_base,
        n_permutations=n_permutations,
    )

    return ReferenceSetGallery(
        schema_version=SCHEMA_VERSION,
        id=reference_set_id,
        version=version,
        created_at=created_at,
        source_datasets=["standin-synthetic-gallery-v1 (proto1 sampler.sample, no real singer audio)"],
        embedding_models=_embedding_models_metadata(),
        coverage_notes=COVERAGE_NOTES,
        sha256=reference_set_hash,
        members=members,
        e1_mean=e1_mean,
        e1_std=e1_std,
        e2_mean=e2_mean,
        e2_std=e2_std,
        e1_normalized=e1_normalized,
        e2_normalized=e2_normalized,
        e1_chance_band_p95=e1_band,
        e2_chance_band_p95=e2_band,
        chance_n_permutations=n_permutations,
        chance_seed_base=chance_seed_base,
    )


# ---------------------------------------------------------------------------
# linkability 監査
# ---------------------------------------------------------------------------


@dataclass
class LinkabilityAuditReport:
    report_id: str
    genome_id: str
    reference_set_hash: str
    created_at: str
    e1_nearest_gallery_index: int
    e1_max_similarity: float
    e1_chance_band_p95: float
    e1_pass: bool
    e2_nearest_gallery_index: int
    e2_max_similarity: float
    e2_chance_band_p95: float
    e2_pass: bool
    overall_pass: bool
    provenance: str
    stale_audit: bool = False


def audit_linkability(
    candidate: VoiceGenome, gallery: ReferenceSetGallery, now: Optional[datetime] = None
) -> LinkabilityAuditReport:
    """候補 Genome を gallery に対して監査する。E1・E2 の両方が chance band 以下なら PASS。"""
    raw_e1, raw_e2 = raw_embeddings(candidate)
    norm_e1 = _zscore(raw_e1, gallery.e1_mean, gallery.e1_std)
    norm_e2 = _zscore(raw_e2, gallery.e2_mean, gallery.e2_std)

    e1_sims = [_cosine_similarity(norm_e1, row) for row in gallery.e1_normalized]
    e2_sims = [_cosine_similarity(norm_e2, row) for row in gallery.e2_normalized]
    e1_idx = int(np.argmax(e1_sims))
    e2_idx = int(np.argmax(e2_sims))
    e1_max = float(e1_sims[e1_idx])
    e2_max = float(e2_sims[e2_idx])

    e1_pass = e1_max <= gallery.e1_chance_band_p95
    e2_pass = e2_max <= gallery.e2_chance_band_p95

    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    genome_id = genome_content_hash(candidate)
    # report_id は内容アドレス（genome_id × reference_set_hash）のみで決める。created_at は
    # 意図的に含めない: 「いつ実行したか」の記録は created_at フィールドに残すが、report_id
    # 自体を wall-clock に依存させると同一候補×同一 reference set の監査を 2 回実行した際に
    # 毎回別 ID になってしまい、「版管理された手続き」としての内容再現性が壊れる
    # （final_assembly_memo.md F1-6 の 2 回実行決定論照合で顕在化。underspec_log_final.md 参照）。
    report_id = "audit-" + sha256_of_canonical_json({"genome_id": genome_id, "reference_set_hash": gallery.sha256})[
        :12
    ]

    return LinkabilityAuditReport(
        report_id=report_id,
        genome_id=genome_id,
        reference_set_hash=gallery.sha256,
        created_at=created_at,
        e1_nearest_gallery_index=e1_idx,
        e1_max_similarity=e1_max,
        e1_chance_band_p95=gallery.e1_chance_band_p95,
        e1_pass=e1_pass,
        e2_nearest_gallery_index=e2_idx,
        e2_max_similarity=e2_max,
        e2_chance_band_p95=gallery.e2_chance_band_p95,
        e2_pass=e2_pass,
        overall_pass=bool(e1_pass and e2_pass),
        provenance=PROVENANCE_CAVEAT,
        stale_audit=False,
    )


# ---------------------------------------------------------------------------
# 監査ログ（JSONL）+ stale_audit 再監査トリガー
# ---------------------------------------------------------------------------


def _report_to_dict(report: LinkabilityAuditReport) -> Dict[str, Any]:
    return {
        "report_id": report.report_id,
        "genome_id": report.genome_id,
        "reference_set_hash": report.reference_set_hash,
        "created_at": report.created_at,
        "e1_nearest_gallery_index": report.e1_nearest_gallery_index,
        "e1_max_similarity": report.e1_max_similarity,
        "e1_chance_band_p95": report.e1_chance_band_p95,
        "e1_pass": report.e1_pass,
        "e2_nearest_gallery_index": report.e2_nearest_gallery_index,
        "e2_max_similarity": report.e2_max_similarity,
        "e2_chance_band_p95": report.e2_chance_band_p95,
        "e2_pass": report.e2_pass,
        "overall_pass": report.overall_pass,
        "provenance": report.provenance,
        "stale_audit": report.stale_audit,
    }


def audit_report_to_dict(report: LinkabilityAuditReport) -> Dict[str, Any]:
    """`_report_to_dict` の公開エイリアス（proto1_demo.py 等、外部モジュールから
    監査結果を JSON 化するための公開 API）。"""
    return _report_to_dict(report)


class LinkabilityAuditValidationError(ValueError):
    """監査ログの pass/fail 宣言値が実測値からの再計算と不一致（PR#261 レビュー R9）。"""


def _report_from_dict(data: Dict[str, Any]) -> LinkabilityAuditReport:
    report = LinkabilityAuditReport(**data)

    # PR#261 レビュー R19: report_id は {genome_id, reference_set_hash} のみに
    # 基づく内容アドレス（audit_linkability() の生成ロジック・上部コメント
    # 参照。created_at は意図的に除外）である。宣言された report_id を
    # そのまま信頼せず、保存済みの genome_id/reference_set_hash から
    # 同一手続きで再計算し、不一致なら拒否する（[UNDERSTEC-F-2] の内容
    # アドレス規約は書き込み側だけでなく読み込み側でも検証しないと、
    # 「別の genome_id/reference_set_hash に手編集で差し替えつつ report_id
    # だけ古い値を残す」改ざんを通してしまう）。
    recomputed_report_id = (
        "audit-"
        + sha256_of_canonical_json(
            {"genome_id": report.genome_id, "reference_set_hash": report.reference_set_hash}
        )[:12]
    )
    if report.report_id != recomputed_report_id:
        raise LinkabilityAuditValidationError(
            "監査ログの report_id が genome_id/reference_set_hash からの再計算と不一致（宣言値: "
            f"{report.report_id!r} / 再計算値: {recomputed_report_id!r}）"
        )

    # PR#261 レビュー R13: R9 の pass/fail 再計算より前に、その入力となる
    # 実測値（e1/e2 の最近傍類似度・チャンス帯 p95）自体がコサイン類似度の
    # 定義域（有限かつ [-1.0, 1.0]）内であることを検証する。R9 の再計算は
    # `<=` 比較のみに依拠するため、非有限値（NaN）や定義域外の値（>1.0 等）を
    # 混入されると、比較結果自体が無意味になったまま「宣言値と一致」して
    # R9 を素通りし得る（例: NaN <= NaN は常に False。宣言側も False に
    # 揃えておけば R9 の一致検証だけでは検出できない）。
    for field_name in ("e1_max_similarity", "e1_chance_band_p95", "e2_max_similarity", "e2_chance_band_p95"):
        value = getattr(report, field_name)
        if not np.isfinite(value) or not (-1.0 <= value <= 1.0):
            raise LinkabilityAuditValidationError(
                f"監査ログの {field_name} がコサイン類似度の定義域外または非有限"
                f"（実際: {value!r}。期待範囲: 有限かつ [-1.0, 1.0]）"
            )

    # PR#261 レビュー R9: 保存済みの実測値（e1/e2 の最近傍類似度・チャンス帯
    # p95）から e1_pass/e2_pass/overall_pass を audit_linkability() と同一の
    # 比較則（`<=`）で再計算し、宣言値と不一致なら拒否する。監査ログは append-only
    # JSONL で手編集され得るため、pass/fail の宣言だけを実測値と無関係に
    # 書き換えられると「不合格の候補が合格扱いになる」改ざんを検証なしで
    # 通してしまう。
    recomputed_e1_pass = report.e1_max_similarity <= report.e1_chance_band_p95
    recomputed_e2_pass = report.e2_max_similarity <= report.e2_chance_band_p95
    recomputed_overall_pass = bool(recomputed_e1_pass and recomputed_e2_pass)
    if (
        report.e1_pass != recomputed_e1_pass
        or report.e2_pass != recomputed_e2_pass
        or report.overall_pass != recomputed_overall_pass
    ):
        raise LinkabilityAuditValidationError(
            "監査ログの pass/fail 判定が実測値からの再計算と不一致（宣言値: "
            f"e1_pass={report.e1_pass}, e2_pass={report.e2_pass}, overall_pass={report.overall_pass} "
            f"/ 再計算値: e1_pass={recomputed_e1_pass}, e2_pass={recomputed_e2_pass}, "
            f"overall_pass={recomputed_overall_pass}）"
        )
    return report


class LinkabilityAuditLog:
    """監査レポートの JSONL append-only ログ + stale_audit 再監査トリガー。"""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def append(self, report: LinkabilityAuditReport) -> None:
        # PR#261 レビュー R33: report_id は内容アドレス（R19: {genome_id,
        # reference_set_hash} から一意に決まる）である以上、同一 report_id
        # の複数行は append-only ログとして矛盾する（load_all() 側で新設
        # した重複検出（下記）が拒否する）。ラウンドトリップ検証（新規
        # エントリの直列化・再解析・全意味論再検証を伴い相対的に重い）に
        # 先立ち、既存ログとの ID 重複だけを先に確認する軽量チェックとして
        # 実施する（fail fast）。
        #
        # PR#261 レビュー R35: 以前はここで `self.load_all()` を呼んだ結果を
        # 重複検出にのみ使い捨てていたが、書き込みも全件アトミック置換
        # （下記）に切り替えたため、同じ結果を書き込みにも再利用する
        # （二重の I/O を避ける）。
        existing_reports = self.load_all()
        existing_ids = {r.report_id for r in existing_reports}
        if report.report_id in existing_ids:
            raise LinkabilityAuditValidationError(
                f"監査ログに既に存在する report_id への追記を拒否する（実際: {report.report_id!r}）"
            )

        # PR#261 レビュー R30: registry.py::GenomeRegistry.append() の R28 と
        # 同型の書読対称性保証。append() はここまで常に `audit_linkability()`
        # が内部で構築した `LinkabilityAuditReport` のみを受理する設計だが、
        # 呼び出し元が `LinkabilityAuditReport(...)` を直接構築して e1_pass
        # 等の宣言値を実測値と矛盾させたレポート（本来 `_report_from_dict()`
        # の R9/R13/R19 検証が拒否するはずの内容）を渡すと、append() 自体は
        # 無検証で成功してしまい「書けるが load_all()/mark_stale() では
        # 読めない」非対称なログ行を作れてしまっていた。書き込み前に
        # 直列化 → `_report_from_dict()` のラウンドトリップを試し、
        # load_all() が将来拒否するであろう行は書き込み時点で拒否する。
        line = json.dumps(_report_to_dict(report), sort_keys=True, ensure_ascii=True)
        try:
            _report_from_dict(json.loads(line))
        except LinkabilityAuditValidationError as exc:
            raise LinkabilityAuditValidationError(
                "append() が構築したレポートは load_all() 側の検証を通過しない"
                f"（書読対称性違反。report_id={report.report_id!r}）: {exc}"
            ) from exc

        # PR#261 レビュー R35: 追記の書き込みを `self.path.open("a")` での
        # write() から、既存全件 + 新規レポートを `_rewrite()`（R17 で導入した
        # staging + `os.replace()` アトミック置換）へ委譲する形へ変更する。
        # `a` モードの write() は書き込み途中（プロセス kill・ディスク満杯等）
        # の中断で正本 JSONL の末尾に破損した部分行が恒久的に残り得た。
        # load_all() はそのような行で必ず `json.loads()` 例外を投げるため、
        # 1 回の中断がログ全体を読み込み不能にしてしまう。`_rewrite()` は
        # 既に mark_stale() 用に同じアトミック置換を実装済みのため、それを
        # 再利用する（新規 staging ロジックの重複実装を避ける）。
        self._rewrite(existing_reports + [report])

    def load_all(self) -> List[LinkabilityAuditReport]:
        if not self.path.exists():
            return []
        reports = []
        seen_report_ids: set = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                report = _report_from_dict(json.loads(line))
                # PR#261 レビュー R33: report_id は内容アドレス（R19）のため
                # 同一 report_id の複数行は append-only ログの意味論と
                # 矛盾する（例: 片方だけ stale_audit フラグが異なる「同じ
                # 監査を指す 2 通りの主張」が同居し得る）。registry.py の
                # R5（genome_id 重複検出）と同型の規律を監査ログにも敷く。
                if report.report_id in seen_report_ids:
                    raise LinkabilityAuditValidationError(
                        f"監査ログに重複 report_id を検出（実際: {report.report_id!r}）"
                    )
                seen_report_ids.add(report.report_id)
                reports.append(report)
        return reports

    def _rewrite(self, reports: List[LinkabilityAuditReport]) -> None:
        """全件を書き戻す（`mark_stale()` のみが使う全置換パス）。

        PR#261 レビュー R17: 旧実装は `self.path.open("w", ...)` の
        truncate-then-write だったため、書き込み中に中断（プロセス kill・
        ディスク満杯等）すると、真ん中で切れた不完全な内容どころか
        「truncate は完了したが 1 バイトも書けていない」状態、すなわち
        旧ログの全損があり得た（append-only ログという設計と矛盾する
        破壊的失敗モード）。同一ディレクトリに staging ファイルを作って
        全件書き込みを完了させてから `os.replace()` で置換する: POSIX の
        `rename(2)` はアトミックなため、置換前に中断すれば旧ログがそのまま
        残り、置換後に中断する余地は（rename 自体が単一のシステムコールで
        あるため）存在しない。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for report in reports:
                    fh.write(json.dumps(_report_to_dict(report), sort_keys=True, ensure_ascii=True))
                    fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            # staging ファイルが残っていれば掃除する（置換前に例外が起きた場合のみ
            # 到達する。os.replace 後の例外は原理上あり得ない）。
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def mark_stale(self, current_reference_set_hash: str) -> int:
        """current_reference_set_hash と異なる reference_set_hash を持つ全エントリの
        stale_audit を True にして書き戻す。新たにフラグを立てた件数を返す。"""
        reports = self.load_all()
        changed = 0
        for report in reports:
            if report.reference_set_hash != current_reference_set_hash and not report.stale_audit:
                report.stale_audit = True
                changed += 1
        self._rewrite(reports)
        return changed
