"""reference_set.py — P5 (VG-016 + VG-018 lite): reference-set/0.1 + linkability 監査。

`proto1_design_memo.md` §P5 の縮約実装:

  - sidecar `reference-set/0.1`（{id, version, created_at, source_datasets,
    embedding_models, coverage_notes, sha256}）。
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

E1/E2 の集約方法の詳細は [UNDERSPEC-P1-10]・[UNDERSPEC-P1-11] を参照。
"""
from __future__ import annotations

import json
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

SCHEMA_VERSION = "reference-set/0.1"

GALLERY_SIZE = 8
GALLERY_SEED_BASE = 20001  # sample() の seed 域（CHANCE_SEED_BASE と重複させない）
CHANCE_SEED_BASE = 90001
CHANCE_N_PERMUTATIONS = 200

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

    def sidecar_dict(self) -> Dict[str, Any]:
        """sidecar 本体（重い embedding/gallery データを含まない、メモが規定する形）。"""
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "source_datasets": self.source_datasets,
            "embedding_models": self.embedding_models,
            "coverage_notes": self.coverage_notes,
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
    e1_band = float(np.percentile(np.array(e1_sims), 95))
    e2_band = float(np.percentile(np.array(e2_sims), 95))
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
    """スタンドイン gallery を作り、reference-set/0.1 sidecar 一式を組み立てる。"""
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


def _report_from_dict(data: Dict[str, Any]) -> LinkabilityAuditReport:
    return LinkabilityAuditReport(**data)


class LinkabilityAuditLog:
    """監査レポートの JSONL append-only ログ + stale_audit 再監査トリガー。"""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def append(self, report: LinkabilityAuditReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_report_to_dict(report), sort_keys=True, ensure_ascii=True))
            fh.write("\n")

    def load_all(self) -> List[LinkabilityAuditReport]:
        if not self.path.exists():
            return []
        reports = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                reports.append(_report_from_dict(json.loads(line)))
        return reports

    def _rewrite(self, reports: List[LinkabilityAuditReport]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for report in reports:
                fh.write(json.dumps(_report_to_dict(report), sort_keys=True, ensure_ascii=True))
                fh.write("\n")

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
