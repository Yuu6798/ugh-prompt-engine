from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
from svp_rpe.calibration import (
    MIN_SAMPLES_PER_GENRE,
    GenreCorpusManifest,
    GenreSample,
    load_genre_manifest,
    run_genre_calibration,
)
from svp_rpe.cli import app

ROOT = Path(__file__).resolve().parents[1]
SEED_MANIFEST = ROOT / "examples" / "calibration" / "genre" / "manifest.yaml"


def _sample(sample_id: str, genre: str, measured: dict[str, float]) -> GenreSample:
    return GenreSample(
        id=sample_id,
        genre_label=genre,
        generator="fixture",
        prompt=f"{genre} fixture",
        measured=measured,
    )


def _manifest(samples: list[GenreSample]) -> GenreCorpusManifest:
    return GenreCorpusManifest(samples=samples)


def _candidate_by_feature(report, feature: str):
    return next(item for item in report.threshold_candidates if item.feature == feature)


def test_manifest_loads_seed_and_forbids_unknown_fields() -> None:
    manifest = load_genre_manifest(SEED_MANIFEST)

    assert manifest.schema_version == "1.0"
    ids = [sample.id for sample in manifest.samples]
    # 本物アンカー stub（先頭）+ 2026-06-25 実 Suno 実測 seed（orchestral/EDM 各 5 本）。
    assert ids[:2] == ["portals", "uza"]
    assert manifest.samples[0].genre_label == "orchestral"
    by_label: dict[str, int] = {}
    for sample in manifest.samples:
        by_label[sample.genre_label] = by_label.get(sample.genre_label, 0) + 1
    assert by_label["orchestral"] == 6  # portals + orchestral_01..05
    assert by_label["electronic-dance"] == 5

    with pytest.raises(ValidationError):
        GenreSample.model_validate(
            {
                "id": "bad",
                "genre_label": "electronic",
                "generator": "fixture",
                "prompt": "fixture",
                "unexpected": True,
            }
        )


def test_feature_stats_and_threshold_candidates_for_separable_fixture() -> None:
    manifest = _manifest(
        [
            _sample(
                "orch-1",
                "orchestral",
                {"spectral_centroid": 1000, "dynamic_range_db": 18, "harmonic_ratio": 0.80},
            ),
            _sample(
                "orch-2",
                "orchestral",
                {"spectral_centroid": 1100, "dynamic_range_db": 19, "harmonic_ratio": 0.82},
            ),
            _sample(
                "orch-3",
                "orchestral",
                {"spectral_centroid": 1200, "dynamic_range_db": 20, "harmonic_ratio": 0.84},
            ),
            _sample(
                "elec-1",
                "electronic",
                {"spectral_centroid": 3000, "dynamic_range_db": 4, "harmonic_ratio": 0.60},
            ),
            _sample(
                "elec-2",
                "electronic",
                {"spectral_centroid": 3100, "dynamic_range_db": 4.5, "harmonic_ratio": 0.62},
            ),
            _sample(
                "elec-3",
                "electronic",
                {"spectral_centroid": 3200, "dynamic_range_db": 5, "harmonic_ratio": 0.64},
            ),
        ]
    )

    report = run_genre_calibration(manifest, repo_root=ROOT)

    electronic_centroid = report.genres["electronic"].features["spectral_centroid"]
    assert report.genres["electronic"].status == "sufficient"
    assert report.genres["orchestral"].sample_count == MIN_SAMPLES_PER_GENRE
    assert electronic_centroid.count == 3
    assert electronic_centroid.min == 3000
    assert electronic_centroid.max == 3200
    assert electronic_centroid.mean == pytest.approx(3100)
    assert electronic_centroid.std == pytest.approx(81.649658)

    candidate = _candidate_by_feature(report, "spectral_centroid")
    assert candidate.threshold == pytest.approx(2100)
    assert candidate.lower_genre == "orchestral"
    assert candidate.higher_genre == "electronic"
    assert candidate.direction == "electronic > orchestral"
    assert candidate.d is not None


def test_overlapping_feature_does_not_emit_threshold_candidate() -> None:
    manifest = _manifest(
        [
            _sample(f"a-{idx}", "a", {"harmonic_ratio": value})
            for idx, value in enumerate([0.1, 0.2, 0.3], start=1)
        ]
        + [
            _sample(f"b-{idx}", "b", {"harmonic_ratio": value})
            for idx, value in enumerate([0.2, 0.3, 0.4], start=1)
        ]
    )

    report = run_genre_calibration(manifest, repo_root=ROOT)

    harmonic_rows = [
        row for row in report.pair_separability if row.feature == "harmonic_ratio"
    ]
    assert harmonic_rows[0].status == "overlap"
    assert harmonic_rows[0].threshold_candidate is None
    assert report.threshold_candidates == []


def test_insufficient_genres_do_not_emit_threshold_candidates() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                _sample("orch", "orchestral", {"spectral_centroid": 1000}),
                _sample("elec", "electronic", {"spectral_centroid": 3000}),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["orchestral"].status == "insufficient"
    assert report.genres["electronic"].status == "insufficient"
    assert report.threshold_candidates == []
    assert {row.status for row in report.pair_separability} == {"insufficient"}


def test_missing_measured_features_are_excluded_from_that_feature_stats() -> None:
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)

    # orchestral seed 6 本全てに harmonic_ratio があり count=6。
    assert report.genres["orchestral"].features["harmonic_ratio"].count == 6
    # spectral_bands 未計測の portals stub は presence の count から除外される（5 本のみ）。
    assert report.genres["orchestral"].features["spectral_bands.presence"].count == 5
    # uza stub は bands 未計測なので electronic の brilliance は None のまま。
    assert report.genres["electronic"].features["spectral_bands.brilliance"].mean is None


def test_excluded_samples_are_reported_not_analyzed() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                _sample("kept", "electronic", {"spectral_centroid": 3000}),
                GenreSample(
                    id="drop",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 1000},
                    excluded=True,
                ),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["electronic"].sample_count == 1
    assert [item.id for item in report.excluded_samples] == ["drop"]


def test_unresolved_genre_labels_remain_visible_as_insufficient() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="missing-rock",
                    genre_label="rock",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator="missing.wav",
                    audio_hash="0" * 64,
                ),
                GenreSample(
                    id="excluded-rock",
                    genre_label="rock",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 2500},
                    excluded=True,
                ),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["rock"].sample_count == 0
    assert report.genres["rock"].status == "insufficient"
    assert {item.id for item in report.excluded_samples} == {
        "missing-rock",
        "excluded-rock",
    }


def test_locator_backed_audio_requires_hash_and_checkout_root(tmp_path: Path) -> None:
    root_file = tmp_path / "inside.wav"
    root_file.write_bytes(b"fixture")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.wav"
    outside_file.write_bytes(b"fixture")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="no-hash",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator=str(root_file),
                ),
                GenreSample(
                    id="outside-root",
                    genre_label="orchestral",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator=str(outside_file),
                    audio_hash="0" * 64,
                ),
            ]
        ),
        repo_root=repo_root,
    )

    assert report.genres["electronic"].sample_count == 0
    assert report.genres["orchestral"].sample_count == 0
    assert {item.reason for item in report.excluded_samples} == {"no_measured_or_audio"}


def test_verified_audio_preferred_over_cached_measured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "take.wav"
    audio.write_bytes(b"verified")
    from svp_rpe.perform import sha256_bytes

    calls: list[str] = []

    def fake_extract(path: str):
        calls.append(path)
        return SimpleNamespace(
            physical=SimpleNamespace(
                model_dump=lambda mode="json": {"spectral_centroid": 4321.0}
            )
        )

    monkeypatch.setattr(extractor, "extract_rpe_from_file", fake_extract)

    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="verified",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 1000.0},
                    audio_locator="take.wav",
                    audio_hash=sha256_bytes(b"verified"),
                )
            ]
        ),
        repo_root=tmp_path,
    )

    assert calls == [str(audio)]
    assert report.genres["electronic"].features["spectral_centroid"].mean == 4321.0


def test_cli_text_and_json_smoke_on_seed_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    text_result = runner.invoke(app, ["genre-calibrate", str(SEED_MANIFEST)])
    assert text_result.exit_code == 0
    assert "Genre Calibration" in text_result.output
    assert "insufficient" in text_result.output

    out = tmp_path / "genre_report.json"
    json_result = runner.invoke(
        app,
        ["genre-calibrate", str(SEED_MANIFEST), "--format", "json", "-o", str(out)],
    )
    assert json_result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    # 実 Suno seed で orchestral/electronic-dance は sufficient、uza stub の
    # electronic は n=1 で insufficient。
    assert payload["genres"]["orchestral"]["status"] == "sufficient"
    assert payload["genres"]["electronic"]["status"] == "insufficient"
    assert payload["threshold_candidates"] != []


def _pair_row(report, feature: str, genre_a: str, genre_b: str):
    for row in report.pair_separability:
        if row.feature == feature and {row.genre_a, row.genre_b} == {genre_a, genre_b}:
            return row
    raise AssertionError(f"no pair row for {feature} {genre_a}/{genre_b}")


def test_low_mid_power_bands_stay_power_q1_5_ph2() -> None:
    """Q1-5 Ph2: `low_ratio` ゲートは power 据え置き closeout、`mid_ratio` は seed 未発火で繰越。

    高域 brightness（power high_ratio）は #91 で power が defective と分かり B-3 で
    magnitude `brilliance` へ移行した。残る low/mid を seed corpus（orchestral/rock/
    electronic-dance）で測ると、両者は性質が異なる:

    1. power `low_ratio` は 3 低域厚ジャンルを全て admit（min>0.4）＝**判別器でなくゲート**。
       ジャンル間は全ペア overlap で discriminate しない。is sound として power 据え置き。
    2. magnitude 低域は **3 ペア全てを分離する単独バンドが無い**: `bass` は全ペア overlap、
       `sub_bass` は rock↔EDM のみ、`low_mid` は orchestral を rock/EDM から分けるが
       rock↔EDM は overlap、と**いずれも部分的**（捨てずに Phase C 補助軸候補として残す）。
    3. 一方 `spectral_bands.brilliance` は全ペア candidate（分離可、d>3）＝**全ペア判別軸は
       既に B-3 で magnitude 化済み**。
    4. `mid_ratio` は **本 seed では評価対象に乗らない**（評価不能、closeout に含めない）。
       低域厚 seed の mid_ratio は production の mid-focused 閾値（`mid_ratio_min:0.45`/
       `mid_ratio_gt:0.5`）に届かず（全ジャンル max<0.45）mid-focused パスが一度も発火しない。
       よって mid_ratio の power/magnitude 是非は測れず、mid-focused/general アンカー待ちで繰越。

    結論: closeout できるのは `low_ratio` ゲート（sound・境界は general アンカー待ち）のみ。
    `mid_ratio` は据え置くが「評価不能のため繰越」（migration の是非は未判断）。本テストは
    この区別（low ゲート据え置き / 低域 magnitude の**部分的**分離 / mid-focused ルール未発火）を
    回帰固定し、mid を closeout 済みに見せる過剰主張と低域の全 overlap 過剰主張を防ぐ。
    """
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)
    low_heavy = ("orchestral", "rock", "electronic-dance")
    pairs = (
        ("orchestral", "electronic-dance"),
        ("rock", "electronic-dance"),
        ("orchestral", "rock"),
    )

    # (1) power low_ratio ゲートは 3 低域厚ジャンルを全て admit（>0.4）
    for genre in low_heavy:
        low_stats = report.genres[genre].features["low_ratio"]
        assert low_stats.min is not None and low_stats.min > 0.4, genre

    # (4) `mid_ratio` は本 seed では評価不能（mid-focused ルール未発火・Codex #108 P2-2）。
    # production の `perc.mid_focused`(mid_ratio_min:0.45) / `instr.mid_focused`(mid_ratio_gt:0.5)
    # に対し、低域厚 seed の mid_ratio は最大でも 0.45 未満で一度も発火しない＝この seed で
    # mid_ratio の power/magnitude 是非は測れない（closeout は low_ratio ゲートのみ）。
    for genre in low_heavy:
        mid_stats = report.genres[genre].features["mid_ratio"]
        assert mid_stats.max is not None and mid_stats.max < 0.45, genre

    # (1)(2) power low/mid_ratio と magnitude 低域 bass は判別器にならない（全ペア overlap）
    for feature in ("low_ratio", "mid_ratio", "spectral_bands.bass"):
        for genre_a, genre_b in pairs:
            assert _pair_row(report, feature, genre_a, genre_b).status == "overlap", (
                feature,
                genre_a,
                genre_b,
            )

    # (2) low_mid/sub_bass は **部分的**分離（全 overlap ではない: Codex #108 P2 の修正）。
    # low_mid: orchestral を rock/EDM から分けるが rock↔EDM は overlap。
    assert _pair_row(report, "spectral_bands.low_mid", "orchestral", "rock").status == "candidate"
    assert (
        _pair_row(report, "spectral_bands.low_mid", "orchestral", "electronic-dance").status
        == "candidate"
    )
    assert (
        _pair_row(report, "spectral_bands.low_mid", "rock", "electronic-dance").status == "overlap"
    )
    # sub_bass: rock↔EDM のみ分離、orchestral は両者と overlap。
    assert (
        _pair_row(report, "spectral_bands.sub_bass", "rock", "electronic-dance").status
        == "candidate"
    )
    assert _pair_row(report, "spectral_bands.sub_bass", "orchestral", "rock").status == "overlap"

    # (3) 全ペアを単独で分離するのは magnitude brilliance のみ＝高域のみ magnitude 化が必要だった
    for genre_a, genre_b in pairs:
        row = _pair_row(report, "spectral_bands.brilliance", genre_a, genre_b)
        assert row.status == "candidate", (genre_a, genre_b)
        assert row.d is not None and row.d > 3.0, (genre_a, genre_b)

    # (5) 実アンカー grounding の区別（Codex #108 P2-5）。唯一の実アンカー portals は
    # scalar `low_ratio` のみ持ち mid_ratio/spectral_bands を欠く（非 null のみ比較）ため、
    # `low_ratio` 所見だけが実アンカー裏打ち（orchestral n=6）で、mid_ratio と magnitude 判別軸は
    # Suno のみ（n=5）＝generator bias 未検証。この実効件数差を回帰固定する（過大評価防止）。
    orch = report.genres["orchestral"].features
    assert orch["low_ratio"].count == 6  # portals（実アンカー）含む
    assert orch["mid_ratio"].count == 5  # Suno のみ
    assert orch["spectral_bands.brilliance"].count == 5  # Suno のみ
    assert orch["spectral_bands.low_mid"].count == 5  # Suno のみ


def test_real_jpop_anchors_below_suno_edm_brightness() -> None:
    """Phase C 着手: 実 J-POP 3 本（repo 初の実 grounding spectral_bands）で generator bias を固定。

    ユーザー提供の実録音（安室/SPEED/湘南乃風、`generator: real`・`j-pop`）を seed に登録した。
    所見を回帰固定する:
    1. `low_ratio>0.4` ゲートは本物でも通用（3 本とも >0.4）＝#108 のゲート closeout を裏付け。
    2. 本物 dance-pop の brilliance は Suno rock 帯（0.117–0.204）に着地し、**Suno EDM 帯
       （≥0.2119）に届かない**（j-pop max < Suno EDM min、重なりゼロ）＝Suno が EDM を本物の
       ダンス曲より明るく描く over-brightening の実証。現行ルールは本物 dance-pop を rock に誤分類。
    """
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)
    jpop = report.genres["j-pop"]
    assert jpop.status == "sufficient" and jpop.sample_count == 3

    # (1) ゲートは本物でも通用
    jpop_low = jpop.features["low_ratio"]
    assert jpop_low.min is not None and jpop_low.min > 0.4

    # (2) 本物 j-pop の brilliance は Suno rock 帯に入り、Suno EDM 帯（≥0.2119）に届かない
    jpop_bril = jpop.features["spectral_bands.brilliance"]
    edm_bril = report.genres["electronic-dance"].features["spectral_bands.brilliance"]
    assert jpop_bril.min is not None and jpop_bril.max is not None
    assert edm_bril.min is not None
    assert 0.117 <= jpop_bril.min and jpop_bril.max < 0.204  # Suno rock 帯
    assert jpop_bril.max < edm_bril.min  # 本物 dance-pop は Suno EDM より厳密に暗い（bias）


def test_suno_jpop_overbrightens_vs_real_matched_pair() -> None:
    """Phase C matched-pair: 本物 J-POP × Suno J-POP（同ジャンル/キー/BPM）で生成器バイアス固定。

    #109 の本物 J-POP vs Suno EDM はジャンル交絡があった。本物 3 本に同ジャンル・同キー・
    同 BPM を狙った Suno を対で生成（`j-pop-suno`）し、残差を純粋な real→Suno 指紋として測る。
    所見を回帰固定:
    1. brilliance: 本物 j-pop（rock 帯, mean≈0.162）< Suno j-pop（EDM 帯, mean≈0.250）で
       `genre-calibrate` が candidate 分離（重なりゼロ）＝over-brightening が genre-controlled で確定。
    2. mid_ratio: Suno が一貫して中域薄い（本物 > Suno）＝スマイリー EQ 指紋の一部。
    """
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)
    real = report.genres["j-pop"]
    suno = report.genres["j-pop-suno"]
    assert real.status == "sufficient" and real.sample_count == 3
    assert suno.status == "sufficient" and suno.sample_count == 3

    # (1) brilliance: 本物 < Suno で重なりゼロ分離（over-brightening）
    real_bril = real.features["spectral_bands.brilliance"]
    suno_bril = suno.features["spectral_bands.brilliance"]
    assert real_bril.max is not None and suno_bril.min is not None
    assert real_bril.max < suno_bril.min  # 重なりゼロ（本物 max < Suno min）
    pair = _pair_row(report, "spectral_bands.brilliance", "j-pop", "j-pop-suno")
    assert pair.status == "candidate"
    assert pair.d is not None and pair.d > 3.0

    # (2) mid_ratio: Suno が中域薄い（本物 mean > Suno mean）＝スマイリー EQ 指紋
    assert real.features["mid_ratio"].mean is not None
    assert suno.features["mid_ratio"].mean is not None
    assert real.features["mid_ratio"].mean > suno.features["mid_ratio"].mean


def test_cross_genre_suno_fingerprint_not_constant() -> None:
    """Phase C cross-genre: 本物 orchestral/rock/EDM 各 n=3 vs 純 Suno コホートで指紋の一定性を検定。

    「Suno 指紋＝一定オフセット → 単一補正係数」仮説を検証する。3 ジャンル real n=3 の分布。
    1. brilliance bias は一定でない（暗化の量がジャンルで異なる）: 全ジャンルで real 平均 ≤ Suno
       平均（real は暗いか平坦）だが、量は orchestral=Suno 帯内（≈平坦・暗化最小）に対し
       edm=Suno 帯下限を下回る（暗化最大）と割れる→単一 brilliance 補正係数は不可。
       注意: 検定対象は **平均シフト**であり band 分離ではない。real/Suno の分布は overlap し
       個別 anchor は Suno 帯に残りうる（例 edm_real_01_onemoretime=0.2343 ∈ EDM 帯 0.2119–0.2508）
       ため、mean を min と比べる非対称検定で「分離」を主張しない（#114 Codex P2）。
       （#111 の n=1 では「edm は帯内」だったが n=3 で edm 平均は real↓ へ収束。）
    2. 一方、全ジャンルで方向一定の指紋が 2 つ（real n=3 で確定）:
       - mid_ratio: 本物 > Suno（Suno は中域を一貫して削る）
       - harmonic_ratio: 本物 < Suno（Suno は一貫してトーナル/脱パンチ）
    3. ゲートの一般化限界: 本物 orchestral の low_ratio < 0.4 で `low_ratio>0.4` ゲートを通らない
       （Suno orchestral の人工的低域厚に依存していた）。
    """
    manifest = load_genre_manifest(SEED_MANIFEST)
    report = run_genre_calibration(manifest, repo_root=ROOT)
    pairs = {  # real label -> suno cohort label
        "orchestral-real": "orchestral",
        "rock-real": "rock",
        "edm-real": "electronic-dance",
    }

    def real_mean(label: str, feat: str) -> float:
        v = report.genres[label].features[feat].mean
        assert v is not None, (label, feat)
        return v

    def _feat_value(measured: dict, feat: str):
        if "." in feat:
            _, _, band = feat.partition(".")
            return (measured.get("spectral_bands") or {}).get(band)
        return measured.get(feat)

    def suno_cohort(label: str, feat: str) -> list[float]:
        """純 Suno baseline（`generator == "suno"` のみ）。`orchestral` には real stub
        `portals` が混ざるため、report の genre stats でなく manifest を generator で
        フィルタして混合平均を避ける（#111 Codex P2）。"""
        vals = [
            float(_feat_value(s.measured, feat))
            for s in manifest.samples
            if s.genre_label == label
            and s.generator == "suno"
            and s.measured
            and isinstance(_feat_value(s.measured, feat), (int, float))
        ]
        assert len(vals) >= MIN_SAMPLES_PER_GENRE, (label, feat, len(vals))
        return vals

    # (1) brilliance bias は一定でない（暗化の量がジャンルで異なる）。検定は平均シフトであり
    #     band 分離ではない: 分布は overlap し個別 anchor は Suno 帯に残りうる
    #     （例 edm_real_01_onemoretime=0.2343 ∈ EDM 帯）。#114 Codex P2。
    # (1a) 全ジャンル: real 平均 ≤ Suno 平均（real は暗いか平坦）
    for real_lab, suno_lab in pairs.items():
        sb = suno_cohort(suno_lab, "spectral_bands.brilliance")
        assert real_mean(real_lab, "spectral_bands.brilliance") <= sum(sb) / len(sb), real_lab
    # (1b) 量はジャンル一定でない: orchestral 平均は Suno 帯内（≈平坦・暗化最小）だが
    #      edm 平均は Suno 帯下限を下回る（暗化最大）＝挙動が割れる→単一補正係数不可
    orch_b = real_mean("orchestral-real", "spectral_bands.brilliance")
    orch_suno_b = suno_cohort("orchestral", "spectral_bands.brilliance")
    assert min(orch_suno_b) <= orch_b <= max(orch_suno_b)  # orchestral は帯内（≈平坦）
    edm_suno_b = suno_cohort("electronic-dance", "spectral_bands.brilliance")
    assert real_mean("edm-real", "spectral_bands.brilliance") < min(edm_suno_b)  # edm 平均は帯下限未満

    # (2) 方向一定の指紋: 全ジャンルで mid_ratio 本物>純Suno かつ harmonic_ratio 本物<純Suno
    for real_lab, suno_lab in pairs.items():
        suno_mid = suno_cohort(suno_lab, "mid_ratio")
        suno_harm = suno_cohort(suno_lab, "harmonic_ratio")
        assert real_mean(real_lab, "mid_ratio") > sum(suno_mid) / len(suno_mid), real_lab
        assert real_mean(real_lab, "harmonic_ratio") < sum(suno_harm) / len(suno_harm), real_lab

    # (3) ゲートの一般化限界: 本物 orchestral は low_ratio<0.4 でゲートを通らない
    assert real_mean("orchestral-real", "low_ratio") < 0.4


def test_manifest_yaml_round_trip_shape(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "samples": [
                    {
                        "id": "fixture",
                        "genre_label": "electronic",
                        "generator": "fixture",
                        "prompt": "prompt",
                        "measured": {"spectral_bands": {"presence": 0.2}},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    manifest = load_genre_manifest(path)
    report = run_genre_calibration(manifest, repo_root=ROOT)

    assert report.genres["electronic"].features["spectral_bands.presence"].mean == 0.2
