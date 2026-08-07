"""tests/test_build_m3d_pairs.py — `scripts/build_m3d_pairs.py`（M3d pairs manifest
builder）のテスト。

実音声・crepe 非依存: `tmp_path` に微小な合成 WAV（正弦波トーン）を書き、builder の
ロジック（clip 選定の決定論・tuning/holdout の clip 単位排他・manifest がハーネスの
ローダで読めること・pin 不一致の fail-closed・同一入力→同一 manifest のバイト一致）
だけを検証する。crepe/tensorflow・実 vocadito 音源には依存しない。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pytest
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_m3d_pairs as bm  # noqa: E402
import run_melody_comparison as harness  # noqa: E402

REAL_SYNTH_SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_synth_specs.yaml"

_SAMPLE_RATE = 22050
_TONE_DURATION_SEC = 0.35  # librosa pitch_shift/time_stretch が安定して動く最小限の短さ


def _write_tone_wav(path: Path, *, freq: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(_SAMPLE_RATE * _TONE_DURATION_SEC))
    t = np.linspace(0.0, _TONE_DURATION_SEC, n, endpoint=False)
    y = (0.2 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, y, _SAMPLE_RATE, subtype="FLOAT")


def _make_vocadito_pool(
    tmp_path: Path, n_clips: int = 18
) -> Tuple[Path, Path, Dict[str, str]]:
    """`n_clips` 件の疑似 vocadito clip（`Audio/<id>.wav`）+ 対応する
    `m2c_external_fixtures.yaml` 互換 fixture ファイルを `tmp_path` に作る。

    `n_clips=18`（既定）= `TUNING_COUNT(12) + HOLDOUT_COUNT(6)` ちょうど。全 clip が
    tuning/holdout いずれかに選定される最小プールにして、選定規則の排他性テスト
    （(b)）をこのプールでそのまま使えるようにしてある。
    """
    vocadito_dir = tmp_path / "external_m3d" / "vocadito"
    clip_ids = [f"vocadito_{i}" for i in range(1, n_clips + 1)]
    fixtures_doc: Dict[str, object] = {
        "schema_version": "m2c-external-fixtures/0.1",
        "registered_utc": "2026-01-01",
        "fixtures": {},
    }
    for idx, clip_id in enumerate(clip_ids):
        audio_path = vocadito_dir / "Audio" / f"{clip_id}.wav"
        _write_tone_wav(audio_path, freq=180.0 + idx * 7.0)
        digest = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        fixtures_doc["fixtures"][clip_id] = {  # type: ignore[index]
            "expected_audio_sha256": digest,
            "expected_annotation_sha256": "0" * 64,
        }
    fixtures_path = tmp_path / "m2c_external_fixtures.yaml"
    fixtures_path.write_text(yaml.safe_dump(fixtures_doc, sort_keys=False), encoding="utf-8")
    expected = {
        cid: entry["expected_audio_sha256"]  # type: ignore[index]
        for cid, entry in fixtures_doc["fixtures"].items()  # type: ignore[union-attr]
    }
    return vocadito_dir, fixtures_path, expected


# --------------------------------------------------------------------------- #
# (a) clip 選定の決定論
# --------------------------------------------------------------------------- #
def test_select_clips_matches_hand_computed_sha256_ranking():
    clip_ids = [f"vocadito_{i}" for i in range(1, 21)]
    fixtures = {cid: "0" * 64 for cid in clip_ids}

    tuning, holdout = bm.select_clips(fixtures)

    ranked = sorted(
        sorted(clip_ids), key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
    )
    assert tuning == ranked[:12]
    assert holdout == ranked[12:18]


def test_select_clips_is_deterministic_across_calls():
    clip_ids = [f"vocadito_{i}" for i in range(1, 25)]
    fixtures = {cid: "0" * 64 for cid in clip_ids}

    first = bm.select_clips(fixtures)
    second = bm.select_clips(fixtures)
    assert first == second


def test_select_clips_rejects_too_few_registered_clips():
    fixtures = {f"vocadito_{i}": "0" * 64 for i in range(1, 10)}
    with pytest.raises(bm.BuildM3dPairsError):
        bm.select_clips(fixtures)


def test_circular_pairs_wraps_around_and_covers_all_clips():
    ordered = ["vocadito_5", "vocadito_1", "vocadito_3"]
    pairs = bm.circular_pairs(ordered)
    assert pairs == [
        ("vocadito_1", "vocadito_3"),
        ("vocadito_3", "vocadito_5"),
        ("vocadito_5", "vocadito_1"),
    ]


# --------------------------------------------------------------------------- #
# (b) tuning/holdout の clip 単位排他
# --------------------------------------------------------------------------- #
def test_select_clips_tuning_holdout_are_disjoint_and_cover_pool():
    clip_ids = [f"vocadito_{i}" for i in range(1, 19)]  # ちょうど 18 = 12 + 6
    fixtures = {cid: "0" * 64 for cid in clip_ids}

    tuning, holdout = bm.select_clips(fixtures)

    assert len(tuning) == 12
    assert len(holdout) == 6
    assert set(tuning).isdisjoint(set(holdout))
    assert set(tuning) | set(holdout) == set(clip_ids)


# --------------------------------------------------------------------------- #
# (c) manifest がハーネスのローダで読める
# --------------------------------------------------------------------------- #
def test_generated_manifest_loads_via_harness_validator(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"

    pairs = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    manifest_doc = {"schema": bm._MANIFEST_SCHEMA, "pairs": pairs}

    # ハーネス自身のスキーマ検証（`_validate_manifest`）を独立に通す — pairs manifest
    # スキーマ（m3-comparison-pairs/0.1）への準拠をハーネスのコードそのもので確認する。
    validated = harness._validate_manifest(manifest_doc)
    assert len(validated) == len(pairs)

    # audio_a/audio_b はリポジトリルート（monkeypatch 後は tmp_path）相対パス。
    # ハーネスの path 解決関数（`_manifest_audio_paths`）でも実ファイルへ解決できる
    # ことを確認する。
    manifest_path = tmp_path / "manifest.yaml"
    bm._atomic_write_bytes(
        manifest_path,
        yaml.safe_dump(manifest_doc, sort_keys=False, allow_unicode=True).encode("utf-8"),
    )
    resolved_paths = harness._manifest_audio_paths(manifest_path)
    for path in resolved_paths:
        assert path.exists(), path

    summary = bm.crosstab(pairs)
    assert summary["total"] == 98
    assert summary["by_kind_split"]["positive_transform"] == {"tuning": 50, "holdout": 26}
    assert summary["by_kind_split"]["negative_cross"] == {"tuning": 12, "holdout": 6}
    assert summary["by_kind_split"]["negative_rhythm"] == {"tuning": 2}
    assert summary["by_kind_split"]["negative_interval"] == {"tuning": 2}


# --------------------------------------------------------------------------- #
# (d) pin 不一致で fail-closed（部分出力なし）
# --------------------------------------------------------------------------- #
def test_pin_mismatch_is_fail_closed_with_no_partial_output(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    # 1 clip の pin を破損させる。
    doc = yaml.safe_load(fixtures_path.read_text(encoding="utf-8"))
    doc["fixtures"]["vocadito_3"]["expected_audio_sha256"] = "f" * 64
    fixtures_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"

    with pytest.raises(bm.BuildM3dPairsError, match="vocadito_3"):
        bm.run_build(
            vocadito_dir=vocadito_dir,
            out_dir=out_dir,
            manifest_out=manifest_out,
            pins_out=pins_out,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    # fail-closed: 生成（variant WAV）も manifest も pins も一切書かれていない。
    assert not out_dir.exists()
    assert not manifest_out.exists()
    assert not pins_out.exists()


def test_pin_missing_file_is_fail_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    (vocadito_dir / "Audio" / "vocadito_7.wav").unlink()

    with pytest.raises(bm.BuildM3dPairsError, match="MISSING"):
        bm.verify_vocadito_pins(vocadito_dir, bm.load_m2c_fixtures(fixtures_path))


def test_main_cli_fails_closed_on_pin_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    doc = yaml.safe_load(fixtures_path.read_text(encoding="utf-8"))
    doc["fixtures"]["vocadito_1"]["expected_audio_sha256"] = "e" * 64
    fixtures_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"

    argv = [
        "build_m3d_pairs.py",
        "--vocadito-dir",
        str(vocadito_dir),
        "--out-dir",
        str(out_dir),
        "--manifest-out",
        str(manifest_out),
        "--pins-out",
        str(pins_out),
        "--fixtures",
        str(fixtures_path),
        "--synth-specs",
        str(REAL_SYNTH_SPECS_PATH),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    rc = bm.main()

    assert rc == 1
    assert not manifest_out.exists()
    assert not pins_out.exists()


# --------------------------------------------------------------------------- #
# (e) 同一入力 → 同一 manifest（バイト一致）
# --------------------------------------------------------------------------- #
def test_same_input_produces_byte_identical_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"

    pairs_1 = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest1.yaml",
        pins_out=tmp_path / "pins1.json",
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    bm.write_outputs(
        pairs_1,
        manifest_out=tmp_path / "manifest1.yaml",
        pins_out=tmp_path / "pins1.json",
        fixtures_path=fixtures_path,
    )

    # 同じ out_dir へ再生成（既存 variant WAV を上書き）。librosa/soundfile は乱数を
    # 使わないため、同一入力・同一パラメータなら bit 一致するはず。
    pairs_2 = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest2.yaml",
        pins_out=tmp_path / "pins2.json",
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    bm.write_outputs(
        pairs_2,
        manifest_out=tmp_path / "manifest2.yaml",
        pins_out=tmp_path / "pins2.json",
        fixtures_path=fixtures_path,
    )

    assert pairs_1 == pairs_2
    bytes_1 = (tmp_path / "manifest1.yaml").read_bytes()
    bytes_2 = (tmp_path / "manifest2.yaml").read_bytes()
    assert bytes_1 == bytes_2

    pins_1 = json.loads((tmp_path / "pins1.json").read_text(encoding="utf-8"))
    pins_2 = json.loads((tmp_path / "pins2.json").read_text(encoding="utf-8"))
    assert pins_1["audio_sha256"] == pins_2["audio_sha256"]
    assert pins_1["material"] == pins_2["material"]


# --------------------------------------------------------------------------- #
# --check-only モード
# --------------------------------------------------------------------------- #
def test_check_only_passes_after_successful_build(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"

    pairs = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    bm.write_outputs(
        pairs, manifest_out=manifest_out, pins_out=pins_out, fixtures_path=fixtures_path
    )

    summary = bm.check_existing(
        manifest_out=manifest_out,
        pins_out=pins_out,
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
    )
    assert summary["total"] == len(pairs)


def test_check_only_detects_tampered_wav(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"

    pairs = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    bm.write_outputs(
        pairs, manifest_out=manifest_out, pins_out=pins_out, fixtures_path=fixtures_path
    )

    # 生成済み variant WAV を 1 件改ざんする。
    tampered = next(out_dir.glob("vocadito_*__pitch_p3.wav"))
    tampered.write_bytes(b"tampered")

    with pytest.raises(bm.BuildM3dPairsError, match="mismatch"):
        bm.check_existing(
            manifest_out=manifest_out,
            pins_out=pins_out,
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
        )


# --------------------------------------------------------------------------- #
# material 区分（pair_id 命名規則で判別可能）
# --------------------------------------------------------------------------- #
def test_material_is_recoverable_from_pair_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"

    pairs = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    materials = {bm._material_of(p["pair_id"]) for p in pairs}
    assert materials == {"real_voice", "synthetic"}
    real_count = sum(1 for p in pairs if bm._material_of(p["pair_id"]) == "real_voice")
    synth_count = sum(1 for p in pairs if bm._material_of(p["pair_id"]) == "synthetic")
    assert real_count == 48 + 24 + 12 + 6  # vocadito positive + negative_cross
    assert synth_count == 4 + 2 + 2  # synth positive + negative_rhythm + negative_interval
