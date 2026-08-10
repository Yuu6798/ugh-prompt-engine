"""tests/test_build_m3d_pairs.py — `scripts/build_m3d_pairs.py`（M3d pairs manifest
builder）のテスト。

実音声・crepe 非依存: `tmp_path` に微小な合成 WAV（正弦波トーン）を書き、builder の
ロジック（clip 選定の決定論・tuning/holdout の clip 単位排他・manifest がハーネスの
ローダで読めること・pin 不一致の fail-closed・同一入力→同一 manifest のバイト一致・
pins sidecar digest 照合の fail-closed 化・アトミック公開の失敗時無傷性）だけを検証
する。crepe/tensorflow・実 vocadito 音源には依存しない。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import librosa
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


def _default_outputs(tmp_path: Path) -> Dict[str, Path]:
    return {
        "out_dir": tmp_path / "external_m3d" / "m3d_pairs",
        "manifest_out": tmp_path / "manifest.yaml",
        "pins_out": tmp_path / "pins.json",
    }


def _build(tmp_path: Path, fixtures_path: Path, vocadito_dir: Path, **overrides) -> Dict:
    outputs = _default_outputs(tmp_path)
    outputs.update(overrides)
    summary = bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    return {"summary": summary, **outputs}


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
    staging_wav_dir = tmp_path / "staging_wav"

    pairs, audio_source_lookup, build_input_sha256, fixtures_from_build = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        staging_wav_dir=staging_wav_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    assert set(build_input_sha256) == {"m2c_external_fixtures_sha256", "m3d_synth_specs_sha256"}
    assert isinstance(fixtures_from_build, dict) and fixtures_from_build
    manifest_doc = {"schema": bm._MANIFEST_SCHEMA, "pairs": pairs}

    # ハーネス自身のスキーマ検証（`_validate_manifest`）を独立に通す — pairs manifest
    # スキーマ（m3-comparison-pairs/0.1）への準拠をハーネスのコードそのもので確認する。
    validated = harness._validate_manifest(manifest_doc)
    assert len(validated) == len(pairs)

    # audio_source_lookup の全パスが staging 側に実在する（publish 前に読める）。
    for rel_path, src in audio_source_lookup.items():
        assert src.exists(), (rel_path, src)

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

    outputs = _default_outputs(tmp_path)

    with pytest.raises(bm.BuildM3dPairsError, match="vocadito_3"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=outputs["out_dir"],
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    # fail-closed: 生成（variant WAV）も manifest も pins も一切書かれていない。
    assert not outputs["out_dir"].exists()
    assert not outputs["manifest_out"].exists()
    assert not outputs["pins_out"].exists()
    # staging も残らない（tmp_path 直下に隠しディレクトリが残留していない）。
    leftover_staging = [p for p in tmp_path.iterdir() if p.name.startswith(".external_m3d")]
    assert leftover_staging == []


def test_pin_missing_file_is_fail_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    (vocadito_dir / "Audio" / "vocadito_7.wav").unlink()

    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    with pytest.raises(bm.BuildM3dPairsError, match="MISSING"):
        bm.verify_vocadito_pins(vocadito_dir, fixtures)


def test_main_cli_fails_closed_on_pin_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    doc = yaml.safe_load(fixtures_path.read_text(encoding="utf-8"))
    doc["fixtures"]["vocadito_1"]["expected_audio_sha256"] = "e" * 64
    fixtures_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    outputs = _default_outputs(tmp_path)

    argv = [
        "build_m3d_pairs.py",
        "--vocadito-dir",
        str(vocadito_dir),
        "--out-dir",
        str(outputs["out_dir"]),
        "--manifest-out",
        str(outputs["manifest_out"]),
        "--pins-out",
        str(outputs["pins_out"]),
        "--fixtures",
        str(fixtures_path),
        "--synth-specs",
        str(REAL_SYNTH_SPECS_PATH),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    rc = bm.main()

    assert rc == 1
    assert not outputs["manifest_out"].exists()
    assert not outputs["pins_out"].exists()


# --------------------------------------------------------------------------- #
# (e) 同一入力 → 同一 manifest（バイト一致）
# --------------------------------------------------------------------------- #
def test_same_input_produces_byte_identical_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"

    build1 = _build(
        tmp_path,
        fixtures_path,
        vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest1.yaml",
        pins_out=tmp_path / "pins1.json",
    )
    # 同じ out_dir へ再生成（既存 variant WAV を上書き）。librosa/soundfile は乱数を
    # 使わないため、同一入力・同一パラメータなら bit 一致するはず。
    build2 = _build(
        tmp_path,
        fixtures_path,
        vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest2.yaml",
        pins_out=tmp_path / "pins2.json",
    )

    assert build1["manifest_out"].read_bytes() == build2["manifest_out"].read_bytes()

    pins_1 = json.loads(build1["pins_out"].read_text(encoding="utf-8"))
    pins_2 = json.loads(build2["pins_out"].read_text(encoding="utf-8"))
    assert pins_1["audio_sha256"] == pins_2["audio_sha256"]
    assert pins_1["material"] == pins_2["material"]
    assert pins_1["manifest_sha256"] == pins_2["manifest_sha256"]
    assert pins_1["m2c_external_fixtures_sha256"] == pins_2["m2c_external_fixtures_sha256"]
    assert pins_1["m3d_synth_specs_sha256"] == pins_2["m3d_synth_specs_sha256"]


# --------------------------------------------------------------------------- #
# --check-only モード（R1: digest 照合 fail-closed 化）
# --------------------------------------------------------------------------- #
def test_check_only_passes_after_successful_build(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    summary = bm.check_existing(
        manifest_out=build["manifest_out"],
        pins_out=build["pins_out"],
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    assert summary["total"] == 98


def test_check_only_detects_tampered_wav(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    tampered = next(build["out_dir"].glob("vocadito_*__pitch_p3.wav"))
    tampered.write_bytes(b"tampered")

    with pytest.raises(bm.BuildM3dPairsError, match="mismatch"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_manifest_sha256_drift(tmp_path: Path, monkeypatch):
    """manifest ファイルが pins サイドカーの記録と無関係に改変された場合、
    --check-only が sha256 mismatch で fail-closed になる（R1 対応の主眼:
    従来は manifest_sha256 を記録するのみで再照合していなかった）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    # pins サイドカーは更新せず、manifest だけ末尾にコメントを追記する
    # （YAML としては引き続きパースできるが、バイト内容が sha256 記録とずれる）。
    with build["manifest_out"].open("ab") as handle:
        handle.write(b"# tampered\n")

    with pytest.raises(bm.BuildM3dPairsError, match="manifest sha256 mismatch"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_pins_schema_drift(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    pins_doc = json.loads(build["pins_out"].read_text(encoding="utf-8"))
    pins_doc["schema"] = "m3d-pairs-pins/0.1"  # 旧スキーマへ改ざん
    build["pins_out"].write_text(json.dumps(pins_doc), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="schema"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_missing_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)
    build["manifest_out"].unlink()

    with pytest.raises(bm.BuildM3dPairsError, match="manifest が存在しない"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_load_and_validate_pins_rejects_missing_required_key(tmp_path: Path):
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps(
            {
                "schema": bm._PINS_SCHEMA,
                "generated_utc": "2026-01-01T00:00:00+00:00",
                "m2c_external_fixtures_sha256": "0" * 64,
                "m3d_synth_specs_sha256": "0" * 64,
                "audio_sha256": {},
                "material": {},
                # "manifest_sha256" キーを欠落させる
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(bm.BuildM3dPairsError, match="必須キー欠落"):
        bm._load_and_validate_pins(pins_path)


# --------------------------------------------------------------------------- #
# material 区分（pair_id 命名規則で判別可能）
# --------------------------------------------------------------------------- #
def test_material_is_recoverable_from_pair_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    staging_wav_dir = tmp_path / "staging_wav"

    pairs, _, _, _ = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        staging_wav_dir=staging_wav_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    materials = {bm._material_of(p["pair_id"]) for p in pairs}
    assert materials == {"real_voice", "synthetic"}
    real_count = sum(1 for p in pairs if bm._material_of(p["pair_id"]) == "real_voice")
    synth_count = sum(1 for p in pairs if bm._material_of(p["pair_id"]) == "synthetic")
    assert real_count == 48 + 24 + 12 + 6  # vocadito positive + negative_cross
    assert synth_count == 4 + 2 + 2  # synth positive + negative_rhythm + negative_interval


# --------------------------------------------------------------------------- #
# レビュー対応 第 11 ラウンド（M2）: 両マーカー含有 pair_id の拒否
# --------------------------------------------------------------------------- #
def test_material_of_rejects_pair_id_with_both_markers():
    """`_material_of` は `_real_`/`_synth_` 両方のマーカーを含む pair_id を
    fail-closed で拒否する（M2 対応）。従来はどちらか一方が含まれれば
    （`_real_` を先に判定）判別成立としており、`pt_real_synth_x` のような id
    が real_voice に分類されてしまっていた——synthetic 診断対が real の
    margin/凍結計算へ混入する穴があった。
    """
    assert bm._material_of("pt_real_tuning_x") == "real_voice"
    assert bm._material_of("pt_synth_tuning_x") == "synthetic"
    with pytest.raises(bm.BuildM3dPairsError, match="両方のマーカーを含んでいる"):
        bm._material_of("pt_real_synth_x")
    with pytest.raises(bm.BuildM3dPairsError, match="判別できない"):
        bm._material_of("pt_unknown_x")


# --------------------------------------------------------------------------- #
# R3: アトミック公開（staging → 一括 publish、失敗時ロールバック）
# --------------------------------------------------------------------------- #
def test_publish_staged_bundle_publishes_all_entries(tmp_path: Path):
    staging = tmp_path / "staging"
    staging.mkdir()
    out_a = tmp_path / "a" / "file_a.txt"
    out_b = tmp_path / "b" / "nested" / "file_b.txt"

    staged_a = staging / "file_a.txt"
    staged_b = staging / "file_b.txt"
    staged_a.write_bytes(b"content-a")
    staged_b.write_bytes(b"content-b")

    bm._publish_staged_bundle([(staged_a, out_a), (staged_b, out_b)])

    assert out_a.read_bytes() == b"content-a"
    assert out_b.read_bytes() == b"content-b"
    # staged ファイル自体は publish 先へ move 済み（元位置には残らない）。
    assert not staged_a.exists()
    assert not staged_b.exists()


def test_publish_staged_bundle_rolls_back_on_failure_leaving_existing_files_intact(
    tmp_path: Path, monkeypatch
):
    out_a = tmp_path / "out" / "file_a.txt"
    out_b = tmp_path / "out" / "file_b.txt"
    out_c = tmp_path / "out" / "file_c.txt"  # まだ存在しない新規ファイル
    out_a.parent.mkdir(parents=True)
    out_a.write_bytes(b"old-a")
    out_b.write_bytes(b"old-b")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_a = staging / "file_a.txt"
    staged_b = staging / "file_b.txt"
    staged_c = staging / "file_c.txt"
    staged_a.write_bytes(b"new-a")
    staged_b.write_bytes(b"new-b")
    staged_c.write_bytes(b"new-c")

    real_replace = os.replace
    call_count = {"n": 0}
    triggered = {"done": False}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        # file_b.txt の publish（snapshot 後の最終 replace）で 1 回だけ意図的に
        # 失敗させる——rollback 自身の復元 replace（同じく dst=out_b）まで
        # 失敗させると rollback 機構そのものを壊してしまうため、発火は一度きり
        # に限定する（実際の障害は通常一過性であり、rollback 時点まで同一障害が
        # 継続する前提はここでは置かない）。
        if not triggered["done"] and Path(dst) == out_b:
            triggered["done"] = True
            raise RuntimeError("simulated crash during publish")
        return real_replace(src, dst)

    monkeypatch.setattr(bm.os, "replace", _flaky_replace)

    with pytest.raises(RuntimeError, match="simulated crash"):
        bm._publish_staged_bundle(
            [(staged_a, out_a), (staged_b, out_b), (staged_c, out_c)]
        )

    # 既存の公開済みセットが無傷で残る（file_a は publish 済みだったはずがロール
    # バックされて元の内容へ復元・file_b はそもそも旧内容のまま・file_c は
    # 新規発行されない）。
    assert out_a.read_bytes() == b"old-a"
    assert out_b.read_bytes() == b"old-b"
    assert not out_c.exists()
    assert call_count["n"] > 0


def test_run_and_publish_leaves_previous_build_intact_when_rebuild_fails(
    tmp_path: Path, monkeypatch
):
    """再ビルド時のアトミック公開（R3 対応の主眼）: 1 回目のビルドが成功した後、
    2 回目のビルド（同一 out_dir・同一 manifest_out）が生成過程で失敗しても、
    1 回目に公開済みのファイル一式が無傷で残る。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build1 = _build(tmp_path, fixtures_path, vocadito_dir)
    wav_before = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    manifest_before = build1["manifest_out"].read_bytes()
    pins_before = build1["pins_out"].read_bytes()

    # 2 回目は fixtures の pin を破損させ、staging 到達前（`verify_vocadito_pins`）
    # で fail-closed にする——生成過程のどの段階で失敗しても公開済みセットに
    # 触れないことを確認する。
    doc = yaml.safe_load(fixtures_path.read_text(encoding="utf-8"))
    doc["fixtures"]["vocadito_2"]["expected_audio_sha256"] = "d" * 64
    fixtures_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=build1["out_dir"],
            manifest_out=build1["manifest_out"],
            pins_out=build1["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    wav_after = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    assert wav_after == wav_before
    assert build1["manifest_out"].read_bytes() == manifest_before
    assert build1["pins_out"].read_bytes() == pins_before


# --------------------------------------------------------------------------- #
# R2R N1: build 入力 pin の完全化（TOCTOU 解消・m3d_synth_specs.yaml の pin 追加）
# --------------------------------------------------------------------------- #
def test_read_bytes_and_sha256_matches_manual_hash(tmp_path: Path):
    path = tmp_path / "sample.yaml"
    path.write_text("a: 1\nb: 2\n", encoding="utf-8")

    data, digest = bm._read_bytes_and_sha256(path)

    assert data == path.read_bytes()
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_m2c_fixtures_hash_is_computed_from_same_bytes_as_parse(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    _, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    fixtures, digest = bm.load_m2c_fixtures(fixtures_path)

    assert isinstance(fixtures, dict) and fixtures
    assert digest == hashlib.sha256(fixtures_path.read_bytes()).hexdigest()


def test_load_synth_specs_hash_is_computed_from_same_bytes_as_parse():
    specs, digest = bm._load_synth_specs(REAL_SYNTH_SPECS_PATH)

    assert isinstance(specs, dict) and specs
    assert digest == hashlib.sha256(REAL_SYNTH_SPECS_PATH.read_bytes()).hexdigest()


def test_check_only_detects_fixtures_sha256_drift(tmp_path: Path, monkeypatch):
    """m2c_external_fixtures.yaml がビルド後（pins サイドカーとは無関係に）改変
    された場合、--check-only が m2c_external_fixtures sha256 mismatch で
    fail-closed になる（R2R N1 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    with fixtures_path.open("a", encoding="utf-8") as handle:
        handle.write("# tampered\n")

    with pytest.raises(bm.BuildM3dPairsError, match="m2c_external_fixtures sha256 mismatch"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_synth_specs_sha256_drift(tmp_path: Path, monkeypatch):
    """m3d_synth_specs.yaml がビルド後に改変された場合、--check-only が
    m3d_synth_specs sha256 mismatch で fail-closed になる（R2R N1 対応・従来は
    そもそも無 pin だった）。共有の実 fixture ファイルを汚さないよう、
    `tmp_path` 内のコピーを使う。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    synth_specs_path = tmp_path / "synth_specs.yaml"
    synth_specs_path.write_bytes(REAL_SYNTH_SPECS_PATH.read_bytes())

    outputs = _default_outputs(tmp_path)
    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=synth_specs_path,
    )

    with synth_specs_path.open("a", encoding="utf-8") as handle:
        handle.write("# tampered\n")

    with pytest.raises(bm.BuildM3dPairsError, match="m3d_synth_specs sha256 mismatch"):
        bm.check_existing(
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=synth_specs_path,
        )


# --------------------------------------------------------------------------- #
# R2R N3: 公開先衝突の拒否（生成開始前・fail-closed）
# --------------------------------------------------------------------------- #
def test_reject_output_input_collisions_detects_output_output_duplicate(tmp_path: Path):
    manifest_out = tmp_path / "manifest.yaml"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=manifest_out,
            pins_out=manifest_out,  # manifest_out と同一パス
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
        )


def test_reject_output_input_collisions_detects_manifest_out_vs_fixtures_input(
    tmp_path: Path,
):
    fixtures_path = tmp_path / "fixtures.yaml"
    fixtures_path.write_text("dummy", encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=fixtures_path,  # manifest_out が入力 fixtures と同じパス
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=fixtures_path,
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
        )


def test_reject_output_input_collisions_detects_wav_vs_vocadito_input(tmp_path: Path):
    vocadito_wav = tmp_path / "vocadito" / "Audio" / "clip.wav"
    vocadito_wav.parent.mkdir(parents=True)
    vocadito_wav.write_bytes(b"x")
    out_dir = vocadito_wav.parent  # out_dir を誤って入力ディレクトリに向ける

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=out_dir,
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["clip.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[vocadito_wav],
        )


def test_reject_output_input_collisions_passes_for_disjoint_paths(tmp_path: Path):
    bm._reject_output_input_collisions(
        out_dir=tmp_path / "out",
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        expected_wav_filenames=["a.wav", "b.wav"],
        fixtures_path=tmp_path / "fixtures.yaml",
        synth_specs_path=tmp_path / "specs.yaml",
        vocadito_audio_paths=[tmp_path / "vocadito" / "Audio" / "clip.wav"],
    )  # 例外が飛ばなければ OK


def test_run_and_publish_rejects_collision_before_generation(tmp_path: Path, monkeypatch):
    """`--manifest-out`/`--pins-out` を誤って同一パスに向けた場合、生成
    （staging への物理書き込み）を一切開始せず fail-closed で拒否する
    （R2R N3 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    outputs["pins_out"] = outputs["manifest_out"]  # 意図的に衝突させる

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=outputs["out_dir"],
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    # 生成が一切開始していない（out_dir 自体が作られていない = WAV 未生成）。
    assert not outputs["out_dir"].exists()
    assert not outputs["manifest_out"].exists()
    # staging も残らない。
    leftover_staging = [p for p in tmp_path.iterdir() if p.name.startswith(".external_m3d")]
    assert leftover_staging == []


# --------------------------------------------------------------------------- #
# R2R N2: アトミック公開成功時の .prev snapshot 掃除
# --------------------------------------------------------------------------- #
def test_publish_staged_bundle_removes_prev_snapshots_after_success(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_a = out_dir / "file_a.txt"
    out_a.write_bytes(b"old-a")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_a = staging / "file_a.txt"
    staged_a.write_bytes(b"new-a")

    bm._publish_staged_bundle([(staged_a, out_a)])

    assert out_a.read_bytes() == b"new-a"
    # snapshot（`.prev`）は publish 成功後に残らない。
    assert list(out_dir.glob(".*.prev")) == []


def test_run_and_publish_leaves_no_prev_snapshots_after_successful_rebuild(
    tmp_path: Path, monkeypatch
):
    """再ビルド（既存の公開済みセットを上書きする経路）が成功した場合も、
    `_publish_staged_bundle` が作る snapshot（`.prev`）が最終的に一切残らない
    （R2R N2 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build1 = _build(tmp_path, fixtures_path, vocadito_dir)
    # 同一の出力先へ再ビルド（各出力ファイルが必ず「既存」を経由し snapshot が
    # 作られる状況を作る）。
    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=build1["out_dir"],
        manifest_out=build1["manifest_out"],
        pins_out=build1["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )

    leftover = list(build1["out_dir"].glob(".*.prev"))
    leftover += list(build1["manifest_out"].parent.glob(".*.prev"))
    leftover += list(build1["pins_out"].parent.glob(".*.prev"))
    assert leftover == []


# --------------------------------------------------------------------------- #
# R2R T2（Codex レビュー第 3 ラウンド）: vocadito バイトの公開直前再照合
# --------------------------------------------------------------------------- #
def test_run_and_publish_rejects_publish_when_vocadito_input_tampered_after_staging(
    tmp_path: Path, monkeypatch
):
    """staging 完成後（＝生成完了後・公開開始前）に vocadito 入力 WAV が改変
    された場合、T2 の公開直前再照合が検出し公開を拒否する（fail-closed）。
    既公開セット（1 回目のビルド成果物）は無傷のまま残る。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build1 = _build(tmp_path, fixtures_path, vocadito_dir)
    wav_before = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    manifest_before = build1["manifest_out"].read_bytes()
    pins_before = build1["pins_out"].read_bytes()

    real_verify = bm.verify_vocadito_pins
    call_count = {"n": 0}
    tampered_path = vocadito_dir / "Audio" / "vocadito_5.wav"

    def _verify_then_tamper_on_second_call(vocadito_dir_arg, fixtures_arg, *, use_cache=True):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # T2 の公開直前再照合（2 回目の呼び出し）の直前に入力を改変する
            # ——staging（生成）は 1 回目の呼び出し（run_build 内の pin 照合）
            # 通過後、既に完了している状態を模す。
            tampered_path.write_bytes(b"tampered-bytes-after-staging-complete")
        return real_verify(vocadito_dir_arg, fixtures_arg, use_cache=use_cache)

    monkeypatch.setattr(bm, "verify_vocadito_pins", _verify_then_tamper_on_second_call)

    with pytest.raises(bm.BuildM3dPairsError, match="vocadito_5"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=build1["out_dir"],
            manifest_out=build1["manifest_out"],
            pins_out=build1["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    # run_build 内の 1 回目（生成前）+ T2 の 2 回目（公開直前）の計 2 回呼ばれた
    # ことを確認する——本テストが検証対象の工程を実際に踏んでいることの担保。
    assert call_count["n"] == 2

    # 既公開セットが無傷のまま残る（今回の失敗した再ビルドは一切公開されない）。
    wav_after = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    assert wav_after == wav_before
    assert build1["manifest_out"].read_bytes() == manifest_before
    assert build1["pins_out"].read_bytes() == pins_before


# --------------------------------------------------------------------------- #
# R2R T3（Codex レビュー第 3 ラウンド）: clip ID / fixture ID の path traversal 拒否
# --------------------------------------------------------------------------- #
def _fixtures_doc_with_clip_id(tmp_path: Path, clip_id: str) -> Path:
    """単一 clip_id を持つ m2c_external_fixtures.yaml 互換ファイルを書き、その
    パスを返す（字句検証テスト専用）。字句検証はパース直後（音声 I/O 前）に
    走るため、音声ファイルを実際に用意する必要はない。
    """
    doc = {
        "schema_version": "m2c-external-fixtures/0.1",
        "registered_utc": "2026-01-01",
        "fixtures": {
            clip_id: {
                "expected_audio_sha256": "0" * 64,
                "expected_annotation_sha256": "0" * 64,
            }
        },
    }
    path = tmp_path / "fixtures_bad.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "bad_clip_id",
    ["../evil", "a/b", "a\\b", "/etc/passwd", "..", "a/../../b", ""],
    ids=[
        "dotdot_prefix",
        "forward_slash",
        "backslash",
        "absolute_path",
        "bare_dotdot",
        "embedded_traversal",
        "empty_string",
    ],
)
def test_load_m2c_fixtures_rejects_path_traversal_clip_id(tmp_path: Path, bad_clip_id: str):
    fixtures_path = _fixtures_doc_with_clip_id(tmp_path, bad_clip_id)

    with pytest.raises(bm.BuildM3dPairsError, match="許可文字集合"):
        bm.load_m2c_fixtures(fixtures_path)


def test_load_m2c_fixtures_accepts_all_real_vocadito_clip_ids():
    """実在の m2c_external_fixtures.yaml（40 clip）が字句検証を全件通過する
    ことを確認する（許可文字集合の決定根拠そのもの）。
    """
    fixtures, _ = bm.load_m2c_fixtures(bm.M2C_FIXTURES_PATH)
    assert len(fixtures) == 40
    for clip_id in fixtures:
        assert bm._ID_LEXICAL_PATTERN.fullmatch(clip_id), clip_id


def test_load_synth_specs_accepts_real_specs_file():
    """実在の m3d_synth_specs.yaml の全 fixture id が字句検証を通過する。"""
    specs, _ = bm._load_synth_specs(REAL_SYNTH_SPECS_PATH)
    assert set(specs["fixtures"]) >= {"m3d_synth_tuning_pos", "m3d_synth_holdout_pos"}
    for fixture_id in specs["fixtures"]:
        assert bm._ID_LEXICAL_PATTERN.fullmatch(fixture_id), fixture_id


def test_load_synth_specs_rejects_path_traversal_fixture_id(tmp_path: Path):
    doc = yaml.safe_load(REAL_SYNTH_SPECS_PATH.read_text(encoding="utf-8"))
    doc["fixtures"]["../evil"] = doc["fixtures"]["m3d_synth_tuning_pos"]
    bad_specs_path = tmp_path / "bad_specs.yaml"
    bad_specs_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="許可文字集合"):
        bm._load_synth_specs(bad_specs_path)


def test_synth_id_constants_pass_lexical_validation():
    """SYNTH_* module 定数（Python 側ハードコード id）が字句検証を通過する
    ことを確認する（import 時に既に検証済み——ここでは検証対象の集合そのものを
    再確認する）。
    """
    assert bm._SYNTH_ID_CONSTANTS
    for synth_id in bm._SYNTH_ID_CONSTANTS:
        assert bm._ID_LEXICAL_PATTERN.fullmatch(synth_id), synth_id


def test_resolve_within_rejects_path_outside_container(tmp_path: Path):
    container = tmp_path / "container"
    container.mkdir()
    outside = tmp_path / "outside" / "evil.wav"

    with pytest.raises(bm.BuildM3dPairsError, match="path traversal"):
        bm._resolve_within(outside, container=container, label="test")


def test_resolve_within_accepts_path_inside_container(tmp_path: Path):
    container = tmp_path / "container"
    container.mkdir()
    inside = container / "ok.wav"

    resolved = bm._resolve_within(inside, container=container, label="test")

    assert resolved == inside.resolve()


# --------------------------------------------------------------------------- #
# R2R F1（Codex レビュー第 4 ラウンド）: 公開直前再照合のキャッシュバイパス
# --------------------------------------------------------------------------- #
def _mtime_preserving_tamper(path: Path) -> None:
    """`path` の内容だけを（先頭 1 byte 反転で）差し替え、size・mtime は
    元の値のまま保つ（`os.utime` で mtime を復元）。`file_sha256` の
    (path, size, mtime_ns) キャッシュキーが変化しない改ざんを再現する。
    """
    original_stat = path.stat()
    original_bytes = path.read_bytes()
    tampered_bytes = bytes([original_bytes[0] ^ 0xFF]) + original_bytes[1:]
    assert len(tampered_bytes) == len(original_bytes)  # size 不変であることの前提確認
    path.write_bytes(tampered_bytes)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_verify_vocadito_pins_cached_call_misses_mtime_preserving_tamper(tmp_path: Path):
    """`file_sha256` の (path, size, mtime_ns) キャッシュの性質そのものを固定
    する negative control: 同一 size・同一 mtime のまま内容だけ差し替えた
    改ざんは、`use_cache=True`（既定）だと検出できない（stale digest が
    キャッシュから返る）。これが F1 で「窓を閉じる目的の検証」に
    `use_cache=False` を必須化した理由そのもの。`use_cache=False` なら
    同じ改ざんを正しく検出することも併せて確認する。
    """
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path, n_clips=1)
    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    target = vocadito_dir / "Audio" / "vocadito_1.wav"

    # 1 回目（既定 use_cache=True）でキャッシュへ (path, size, mtime) → digest
    # を記録させる——`run_build` 内の初回照合を模す。
    bm.verify_vocadito_pins(vocadito_dir, fixtures)  # 例外なし

    _mtime_preserving_tamper(target)

    # use_cache=True（既定）だと (path, size, mtime) キーが一致するため、
    # stale digest が返り改ざんを見逃す（脆弱性クラスの実証）。
    bm.verify_vocadito_pins(vocadito_dir, fixtures)  # 例外が飛ばない = 見逃し

    # use_cache=False（F1 対応後の「窓を閉じる目的の検証」）は実バイトを
    # 読み直すため、同じ改ざんを正しく検出する。
    with pytest.raises(bm.BuildM3dPairsError, match="sha256 mismatch"):
        bm.verify_vocadito_pins(vocadito_dir, fixtures, use_cache=False)


def test_run_and_publish_detects_mtime_preserving_tamper_at_publish_gate(
    tmp_path: Path, monkeypatch
):
    """T2 の公開直前再照合（F1 対応後）は `use_cache=False` で実バイトを読み
    直すため、size/mtime を保存したまま内容だけ差し替えた改ざん（`os.utime`
    で mtime を復元）も正しく検出し、公開を拒否する（fail-closed）。既公開
    セットは無傷のまま残る。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build1 = _build(tmp_path, fixtures_path, vocadito_dir)
    wav_before = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    manifest_before = build1["manifest_out"].read_bytes()
    pins_before = build1["pins_out"].read_bytes()

    target = vocadito_dir / "Audio" / "vocadito_4.wav"
    real_verify = bm.verify_vocadito_pins
    call_count = {"n": 0}

    def _verify_then_tamper_preserving_stat(vocadito_dir_arg, fixtures_arg, *, use_cache=True):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # T2 の公開直前再照合（2 回目の呼び出し）の直前に、size/mtime を
            # 保った改ざんを注入する——staging（生成）は既に完了している。
            _mtime_preserving_tamper(target)
        return real_verify(vocadito_dir_arg, fixtures_arg, use_cache=use_cache)

    monkeypatch.setattr(bm, "verify_vocadito_pins", _verify_then_tamper_preserving_stat)

    with pytest.raises(bm.BuildM3dPairsError, match="vocadito_4"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=build1["out_dir"],
            manifest_out=build1["manifest_out"],
            pins_out=build1["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    assert call_count["n"] == 2
    wav_after = {p.name: p.read_bytes() for p in build1["out_dir"].glob("*.wav")}
    assert wav_after == wav_before
    assert build1["manifest_out"].read_bytes() == manifest_before
    assert build1["pins_out"].read_bytes() == pins_before


def test_check_only_uses_cache_bypass_for_vocadito_pin_reverification(tmp_path: Path, monkeypatch):
    """`--check-only`（`check_existing`）の vocadito pin 再照合も
    `use_cache=False` で実バイトを読み直す（F1 のファミリー掃討対象）。
    size/mtime を保った改ざんを正しく検出することを確認する。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    # 事前にキャッシュへ (path, size, mtime) → digest を記録させておく
    # （build 内の初回照合が既定 use_cache=True で行っているため、この時点で
    # 既にキャッシュ済みのはず——念のため明示的にも一度呼んでおく）。
    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    bm.verify_vocadito_pins(vocadito_dir, fixtures)

    target = vocadito_dir / "Audio" / "vocadito_2.wav"
    _mtime_preserving_tamper(target)

    with pytest.raises(bm.BuildM3dPairsError, match="vocadito_2"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


# --------------------------------------------------------------------------- #
# R2R F2（Codex レビュー第 4 ラウンド）: rollback の未初期化 snapshot 復元防止
# --------------------------------------------------------------------------- #
def test_publish_staged_bundle_leaves_destination_intact_when_snapshot_rename_fails(
    tmp_path: Path, monkeypatch
):
    """F2 対応: snapshot 作成の rename（`final_path` → `snapshot_path`）自体が
    失敗/中断した場合、destination（`final_path`）は無傷のまま残る——rollback
    が mkstemp の空 placeholder を有効な退避物として誤って復元し、無傷の
    destination を空ファイルで上書き（切り詰め）する経路がないことを確認する。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "file.txt"
    final_path.write_bytes(b"original-content")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_path = staging / "file.txt"
    staged_path.write_bytes(b"new-content")

    real_replace = os.replace

    def _flaky_replace(src, dst):
        if Path(src) == final_path:
            # snapshot 作成の rename（final_path → snapshot_path）自体を失敗
            # させる——mkstemp による空 placeholder は既に存在するが、rename
            # は一度も成功していない状態を模す（KeyboardInterrupt 等の中断と
            # 同じ効果）。
            raise RuntimeError("simulated crash during snapshot rename")
        return real_replace(src, dst)

    monkeypatch.setattr(bm.os, "replace", _flaky_replace)

    with pytest.raises(RuntimeError, match="simulated crash"):
        bm._publish_staged_bundle([(staged_path, final_path)])

    # destination は無傷のまま（空 placeholder で上書きされていない）。
    assert final_path.read_bytes() == b"original-content"
    # mkstemp が作った空 placeholder も残らない（掃除されている）。
    assert list(out_dir.glob(".*.prev")) == []


def test_publish_staged_bundle_rollback_restores_fully_completed_entry_when_later_entry_fails(
    tmp_path: Path, monkeypatch
):
    """F2 対応・部分完了時の復元: 複数エントリのうち先行するものが snapshot+
    publish まで完全に完了した後、後続のエントリの snapshot rename 自体が
    失敗した場合——先行エントリは正しく元の内容へロールバックされ、後続
    エントリの destination は（snapshot rename 未完了のため）無傷のまま残る。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_a = out_dir / "a.txt"
    final_a.write_bytes(b"old-a")
    final_b = out_dir / "b.txt"
    final_b.write_bytes(b"old-b")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_a = staging / "a.txt"
    staged_a.write_bytes(b"new-a")
    staged_b = staging / "b.txt"
    staged_b.write_bytes(b"new-b")

    real_replace = os.replace

    def _flaky_replace(src, dst):
        if Path(src) == final_b:
            # b の snapshot rename 自体を失敗させる（a は先に完全成功させる）。
            raise RuntimeError("simulated crash during snapshot rename for b")
        return real_replace(src, dst)

    monkeypatch.setattr(bm.os, "replace", _flaky_replace)

    with pytest.raises(RuntimeError, match="simulated crash"):
        bm._publish_staged_bundle([(staged_a, final_a), (staged_b, final_b)])

    # a: 完全成功後にロールバックされ、元の内容へ復元されている
    # （新内容 "new-a" ではなく "old-a" のまま）。
    assert final_a.read_bytes() == b"old-a"
    # b: snapshot rename 自体が失敗しており、無傷のまま
    # （空 placeholder で上書きされていない）。
    assert final_b.read_bytes() == b"old-b"


# --------------------------------------------------------------------------- #
# R2R G2（Codex レビュー第 5 ラウンド）: --summary-out の衝突検査編入と保護
# --------------------------------------------------------------------------- #
def test_reject_output_input_collisions_detects_summary_out_vs_manifest_out(tmp_path: Path):
    manifest_out = tmp_path / "manifest.yaml"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=manifest_out,
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=manifest_out,  # manifest_out と同一パス
        )


def test_reject_output_input_collisions_detects_summary_out_vs_pins_out(tmp_path: Path):
    pins_out = tmp_path / "pins.json"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=pins_out,
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=pins_out,  # pins_out と同一パス
        )


def test_reject_output_input_collisions_detects_summary_out_vs_fixtures_input(tmp_path: Path):
    fixtures_path = tmp_path / "fixtures.yaml"
    fixtures_path.write_text("dummy", encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=fixtures_path,
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=fixtures_path,  # summary_out が入力 fixtures と同じパス
        )


def test_reject_output_input_collisions_detects_summary_out_vs_synth_specs_input(
    tmp_path: Path,
):
    synth_specs_path = tmp_path / "specs.yaml"
    synth_specs_path.write_text("dummy", encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=synth_specs_path,
            vocadito_audio_paths=[],
            summary_out=synth_specs_path,  # summary_out が入力 synth specs と同じパス
        )


def test_reject_output_input_collisions_detects_summary_out_vs_generated_wav(tmp_path: Path):
    out_dir = tmp_path / "out"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm._reject_output_input_collisions(
            out_dir=out_dir,
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=out_dir / "a.wav",  # 生成 WAV と同一パス
        )


def test_reject_output_input_collisions_passes_with_disjoint_summary_out(tmp_path: Path):
    bm._reject_output_input_collisions(
        out_dir=tmp_path / "out",
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        expected_wav_filenames=["a.wav", "b.wav"],
        fixtures_path=tmp_path / "fixtures.yaml",
        synth_specs_path=tmp_path / "specs.yaml",
        vocadito_audio_paths=[tmp_path / "vocadito" / "Audio" / "clip.wav"],
        summary_out=tmp_path / "summary.json",
    )  # 例外が飛ばなければ OK


# --------------------------------------------------------------------------- #
# Codex レビュー第 7 巡 X1: v2 で新たに消費する入力（screening record /
# M1 registry）を `_reject_output_input_collisions` の保護入力集合へ編入
# --------------------------------------------------------------------------- #
def test_reject_output_input_collisions_detects_screening_record_vs_manifest_out(
    tmp_path: Path,
):
    """`screening_record_path` を渡した場合、それが `manifest_out` と同じ
    解決済みパスを指すなら fail-closed で拒否する（従来から保護対象だった
    ことの回帰ガード。実在検証: v2 の `run_build` 呼び出し経路では既に
    渡されていた）。"""
    manifest_out = tmp_path / "manifest.yaml"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=manifest_out,
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            screening_record_path=manifest_out,  # manifest_out と同一パス
        )


def test_reject_output_input_collisions_detects_screening_record_vs_summary_out(
    tmp_path: Path,
):
    """X1(b) の直接再現: `--summary-out` が screening record と同じパスを
    指す場合、fail-closed で拒否する（`check_existing` 経路の統合再現は
    `test_build_m3d_pairs_v2.py::test_check_existing_rejects_summary_out_targeting_screening_record`
    を参照）。"""
    screening_record_path = tmp_path / "screening_v2.json"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=screening_record_path,  # screening record と同一パス
            screening_record_path=screening_record_path,
        )


def test_reject_output_input_collisions_detects_m1_registry_vs_pins_out(tmp_path: Path):
    """X1(a): `m1_registry_path` を渡した場合、`--pins-out` の誤設定で M1
    registry を上書きしうる衝突を fail-closed で拒否する。"""
    pins_out = tmp_path / "pins.json"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=pins_out,
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            m1_registry_path=pins_out,  # pins_out と同一パス
        )


def test_reject_output_input_collisions_detects_m1_registry_vs_summary_out(tmp_path: Path):
    """X1(a): `--summary-out` が M1 registry と同じパスを指す場合も同様に
    fail-closed で拒否する。"""
    m1_registry_path = tmp_path / "registry.yaml"

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm._reject_output_input_collisions(
            out_dir=tmp_path / "out",
            manifest_out=tmp_path / "manifest.yaml",
            pins_out=tmp_path / "pins.json",
            expected_wav_filenames=["a.wav"],
            fixtures_path=tmp_path / "fixtures.yaml",
            synth_specs_path=tmp_path / "specs.yaml",
            vocadito_audio_paths=[],
            summary_out=m1_registry_path,
            m1_registry_path=m1_registry_path,
        )


def test_reject_output_input_collisions_passes_with_disjoint_screening_and_m1_registry(
    tmp_path: Path,
):
    """`screening_record_path`/`m1_registry_path` の両方を渡しても、互いに
    disjoint なら通る（正常系の回帰ガード）。"""
    bm._reject_output_input_collisions(
        out_dir=tmp_path / "out",
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        expected_wav_filenames=["a.wav"],
        fixtures_path=tmp_path / "fixtures.yaml",
        synth_specs_path=tmp_path / "specs.yaml",
        vocadito_audio_paths=[],
        summary_out=tmp_path / "summary.json",
        screening_record_path=tmp_path / "screening_v2.json",
        m1_registry_path=tmp_path / "registry.yaml",
    )  # 例外が飛ばなければ OK


def test_run_and_publish_rejects_summary_out_collision_before_generation(
    tmp_path: Path, monkeypatch
):
    """`--summary-out` が入力 fixtures と衝突する場合、生成（staging への物理
    書き込み）を一切開始せず fail-closed で拒否する（R2R G2 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=outputs["out_dir"],
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
            summary_out=fixtures_path,  # 意図的に衝突させる
        )

    # 生成が一切開始していない（out_dir 自体が作られていない = WAV 未生成）。
    assert not outputs["out_dir"].exists()
    assert not outputs["manifest_out"].exists()
    leftover_staging = [p for p in tmp_path.iterdir() if p.name.startswith(".external_m3d")]
    assert leftover_staging == []


def test_run_and_publish_writes_summary_out_atomically_with_bundle(tmp_path: Path, monkeypatch):
    """衝突がない場合、`--summary-out` は manifest/pins/生成 WAV と同じ公開
    タイミングで正しく書かれ、内容が戻り値の crosstab summary と一致する
    （R2R G2 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    summary_out = tmp_path / "summary.json"

    summary = bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )

    assert summary_out.exists()
    on_disk = json.loads(summary_out.read_text(encoding="utf-8"))
    assert on_disk == summary
    assert summary["total"] == 98


def test_check_only_rejects_summary_out_collision_with_published_wav(tmp_path: Path, monkeypatch):
    """`--check-only --summary-out` が既公開の生成 WAV と衝突する場合、書き込み
    前に fail-closed で拒否する（R2R G2 対応: `--check-only --summary-out
    <manifest>` のような自壊シナリオのファミリー）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)
    published_wav = next(build["out_dir"].glob("*.wav"))
    before = published_wav.read_bytes()

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が入力と衝突している"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
            summary_out=published_wav,  # 既公開の生成 WAV と衝突させる
        )

    # 衝突検出時点で書き込みは一切発生しない（既公開 WAV は無傷）。
    assert published_wav.read_bytes() == before


def test_check_only_rejects_summary_out_collision_with_manifest_out(tmp_path: Path, monkeypatch):
    """`--check-only --summary-out <manifest>` は検証直後の自壊シナリオその
    ものであり、書き込み前に fail-closed で拒否する（R2R G2 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)
    manifest_before = build["manifest_out"].read_bytes()

    with pytest.raises(bm.BuildM3dPairsError, match="公開先が重複している"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
            summary_out=build["manifest_out"],  # manifest_out と同一パス
        )

    assert build["manifest_out"].read_bytes() == manifest_before


def test_check_only_writes_summary_out_when_no_collision(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)
    summary_out = tmp_path / "summary.json"

    # `check_existing` 自体は衝突検査のみ行い、実際の書き込みは呼び出し側
    # （`main()`）が行う契約——ここでは契約どおり呼び出し側を模して書く。
    summary = bm.check_existing(
        manifest_out=build["manifest_out"],
        pins_out=build["pins_out"],
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )
    assert not summary_out.exists()  # check_existing 自体はまだ書かない
    bm._atomic_write_bytes(summary_out, json.dumps(summary, sort_keys=True).encode("utf-8"))
    assert json.loads(summary_out.read_text(encoding="utf-8")) == summary


# --------------------------------------------------------------------------- #
# R2R G1（Codex レビュー第 5 ラウンド）: synth specs の重複キー拒否
# --------------------------------------------------------------------------- #
def test_load_synth_specs_rejects_duplicate_fixture_id(tmp_path: Path):
    path = tmp_path / "specs.yaml"
    path.write_text(
        "fixtures:\n"
        "  m3d_synth_tuning_pos:\n"
        "    kind: tone\n"
        "  m3d_synth_tuning_pos:\n"  # 重複 fixture id（値だけ差し替え）
        "    kind: chord\n",
        encoding="utf-8",
    )

    with pytest.raises(bm.BuildM3dPairsError, match="duplicate YAML mapping key"):
        bm._load_synth_specs(path)


def test_load_synth_specs_rejects_duplicate_nested_key(tmp_path: Path):
    path = tmp_path / "specs.yaml"
    path.write_text(
        "fixtures:\n"
        "  m3d_synth_tuning_pos:\n"
        "    kind: tone\n"
        "    kind: chord\n",  # 同一 fixture 定義内の重複ネストキー
        encoding="utf-8",
    )

    with pytest.raises(bm.BuildM3dPairsError, match="duplicate YAML mapping key"):
        bm._load_synth_specs(path)


def test_load_synth_specs_still_loads_real_spec_file():
    specs, digest = bm._load_synth_specs(REAL_SYNTH_SPECS_PATH)

    assert isinstance(specs, dict) and specs.get("fixtures")
    assert digest == hashlib.sha256(REAL_SYNTH_SPECS_PATH.read_bytes()).hexdigest()


def test_parse_m2c_fixtures_rejects_duplicate_clip_id(tmp_path: Path):
    """ファミリー掃討の確認: `m2c_external_fixtures.yaml` 側は既に
    `_yaml_load_no_dup_keys` を使っており（R2R G1 では無改造）、重複 clip_id は
    引き続き fail-closed で拒否される。
    """
    data = (
        "schema_version: m2c-external-fixtures/0.1\n"
        "fixtures:\n"
        "  vocadito_1:\n"
        "    expected_audio_sha256: '" + "0" * 64 + "'\n"
        "  vocadito_1:\n"  # 重複 clip_id
        "    expected_audio_sha256: '" + "1" * 64 + "'\n"
    ).encode("utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="duplicate YAML mapping key"):
        bm._parse_m2c_fixtures(data, source="dummy.yaml")


# --------------------------------------------------------------------------- #
# R2R H1（Codex レビュー第 6 ラウンド）: material map の内容検証
# --------------------------------------------------------------------------- #
def test_expected_material_map_matches_material_of_for_all_pairs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    out_dir = tmp_path / "external_m3d" / "m3d_pairs"
    staging_wav_dir = tmp_path / "staging_wav"

    pairs, _, _, _ = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        manifest_out=tmp_path / "manifest.yaml",
        pins_out=tmp_path / "pins.json",
        staging_wav_dir=staging_wav_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )

    expected = bm._expected_material_map(pairs)
    # 全 audio path が漏れなく含まれ、各値が pair_id から独立に判別した
    # material と一致する。
    assert set(expected) == set(bm.all_audio_paths(pairs))
    for pair in pairs:
        pair_material = bm._material_of(pair["pair_id"])
        assert expected[pair["audio_a"]] == pair_material
        assert expected[pair["audio_b"]] == pair_material


def test_check_only_detects_material_value_rewritten(tmp_path: Path, monkeypatch):
    """sidecar の `material` マップの値だけが real_voice→synthetic 等へ書き換え
    られた場合（manifest 自体は無改変）、`--check-only` が fail-closed で検出
    する（H1 対応の主眼: 従来は `material` の中身を一切見ていなかった）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    pins_doc = json.loads(build["pins_out"].read_text(encoding="utf-8"))
    real_key = next(k for k, v in pins_doc["material"].items() if v == "real_voice")
    pins_doc["material"][real_key] = "synthetic"  # 書換
    build["pins_out"].write_text(json.dumps(pins_doc), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="material マップ不一致"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_material_entry_deleted(tmp_path: Path, monkeypatch):
    """sidecar の `material` マップからエントリが削除された場合（manifest は
    無改変）、`--check-only` が欠落として fail-closed で検出する（H1 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    pins_doc = json.loads(build["pins_out"].read_text(encoding="utf-8"))
    deleted_key = next(iter(pins_doc["material"]))
    del pins_doc["material"][deleted_key]
    build["pins_out"].write_text(json.dumps(pins_doc), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="material マップに欠落エントリ"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_material_stale_entry_added(tmp_path: Path, monkeypatch):
    """sidecar の `material` マップへ manifest に無い余剰（stale）エントリが
    追加された場合、`--check-only` が fail-closed で検出する（H1 対応）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    pins_doc = json.loads(build["pins_out"].read_text(encoding="utf-8"))
    pins_doc["material"]["nonexistent/stale/path.wav"] = "real_voice"
    build["pins_out"].write_text(json.dumps(pins_doc), encoding="utf-8")

    with pytest.raises(bm.BuildM3dPairsError, match="material マップに余剰エントリ"):
        bm.check_existing(
            manifest_out=build["manifest_out"],
            pins_out=build["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_passes_with_untouched_material_map(tmp_path: Path, monkeypatch):
    """正常パス: material マップが無改変であれば `--check-only` は引き続き
    通る（H1 の検査が偽陽性を出さないことの確認）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)

    summary = bm.check_existing(
        manifest_out=build["manifest_out"],
        pins_out=build["pins_out"],
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    assert summary["total"] == 98


# --------------------------------------------------------------------------- #
# R2R H2（Codex レビュー第 6 ラウンド）: summary の pin 化
# --------------------------------------------------------------------------- #
def test_run_and_publish_records_summary_pin_in_pins_doc(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    summary_out = tmp_path / "summary.json"

    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )

    pins_doc = json.loads(outputs["pins_out"].read_text(encoding="utf-8"))
    assert pins_doc["schema"] == bm._PINS_SCHEMA  # H2 はスキーマ version を bump しない
    assert pins_doc["summary_path"] == bm._repo_rel(summary_out)
    assert pins_doc["summary_sha256"] == hashlib.sha256(summary_out.read_bytes()).hexdigest()


def test_run_and_publish_omits_summary_pin_when_summary_out_not_given(
    tmp_path: Path, monkeypatch
):
    """summary 非使用ビルド（従来どおり）は summary_path/summary_sha256 の
    いずれも記録しない——既存 0.3 sidecar との後方互換性の確認。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)

    build = _build(tmp_path, fixtures_path, vocadito_dir)  # summary_out 未指定

    pins_doc = json.loads(build["pins_out"].read_text(encoding="utf-8"))
    assert "summary_path" not in pins_doc
    assert "summary_sha256" not in pins_doc
    # 既存 sidecar は --check-only を通る（後方互換）。
    summary = bm.check_existing(
        manifest_out=build["manifest_out"],
        pins_out=build["pins_out"],
        vocadito_dir=vocadito_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
    assert summary["total"] == 98


def test_check_only_detects_summary_tampered_after_publish(tmp_path: Path, monkeypatch):
    """公開済み summary ファイルが事後改変された場合、`--check-only` が
    summary sha256 mismatch で fail-closed になる（H2 対応の主眼）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    summary_out = tmp_path / "summary.json"

    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )

    with summary_out.open("a", encoding="utf-8") as handle:
        handle.write("\n// tampered\n")

    with pytest.raises(bm.BuildM3dPairsError, match="summary sha256 mismatch"):
        bm.check_existing(
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_detects_summary_missing_when_pinned(tmp_path: Path, monkeypatch):
    """pins に summary の記録があるのに現物が削除された場合も fail-closed
    （H2 要件: 「記録があるのに現物欠落も fail」）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    summary_out = tmp_path / "summary.json"

    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )
    summary_out.unlink()

    with pytest.raises(bm.BuildM3dPairsError, match="summary ファイルが存在しない"):
        bm.check_existing(
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )


def test_check_only_verifies_summary_pin_independent_of_this_calls_summary_out_arg(
    tmp_path: Path, monkeypatch
):
    """summary pin の照合は sidecar に記録された path 基準で行われ、今回の
    呼び出しに `summary_out` を渡すかどうかとは独立している（H2 docstring の
    設計判定どおり）——今回 `summary_out=None` で呼んでも、過去に記録された
    summary の tamper は検出される。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    summary_out = tmp_path / "summary.json"

    bm.run_and_publish(
        vocadito_dir=vocadito_dir,
        out_dir=outputs["out_dir"],
        manifest_out=outputs["manifest_out"],
        pins_out=outputs["pins_out"],
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
        summary_out=summary_out,
    )
    summary_out.write_bytes(b"{}")  # tamper

    with pytest.raises(bm.BuildM3dPairsError, match="summary sha256 mismatch"):
        bm.check_existing(
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            vocadito_dir=vocadito_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
            summary_out=None,  # 今回の呼び出しでは --summary-out 未指定
        )


def test_load_and_validate_pins_rejects_summary_path_without_sha(tmp_path: Path):
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps(
            {
                "schema": bm._PINS_SCHEMA,
                "generated_utc": "2026-01-01T00:00:00+00:00",
                "m2c_external_fixtures_sha256": "0" * 64,
                "m3d_synth_specs_sha256": "0" * 64,
                "manifest_sha256": "0" * 64,
                "audio_sha256": {},
                "material": {},
                "summary_path": "summary.json",  # summary_sha256 を欠落させる
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(bm.BuildM3dPairsError, match="両方存在するか両方欠落"):
        bm._load_and_validate_pins(pins_path)


def test_load_and_validate_pins_accepts_pins_without_summary_fields(tmp_path: Path):
    """H2 追加前の既存 0.3 sidecar（summary フィールド無し）は引き続き有効
    （後方互換の確認）。
    """
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps(
            {
                "schema": bm._PINS_SCHEMA,
                "generated_utc": "2026-01-01T00:00:00+00:00",
                "m2c_external_fixtures_sha256": "0" * 64,
                "m3d_synth_specs_sha256": "0" * 64,
                "manifest_sha256": "0" * 64,
                "audio_sha256": {},
                "material": {},
            }
        ),
        encoding="utf-8",
    )
    doc = bm._load_and_validate_pins(pins_path)
    assert "summary_path" not in doc


# --------------------------------------------------------------------------- #
# レビュー対応 第 11 ラウンド（M3）: pins sidecar の重複キー拒否
# --------------------------------------------------------------------------- #
def _valid_pins_doc_json_prefix() -> str:
    return (
        '{"schema": "' + bm._PINS_SCHEMA + '", '
        '"generated_utc": "2026-01-01T00:00:00+00:00", '
        '"m2c_external_fixtures_sha256": "' + "0" * 64 + '", '
        '"m3d_synth_specs_sha256": "' + "0" * 64 + '", '
    )


def test_load_and_validate_pins_rejects_duplicate_top_level_key(tmp_path: Path):
    """pins sidecar の JSON に重複したトップレベルキー（`manifest_sha256` を
    2 回書く）があれば fail-closed で拒否する（M3 対応の主眼: 素の
    `json.loads` は last-wins で通してしまう）。
    """
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        _valid_pins_doc_json_prefix()
        + '"manifest_sha256": "' + "1" * 64 + '", '
        + '"manifest_sha256": "' + "2" * 64 + '", '
        + '"audio_sha256": {}, "material": {}}',
        encoding="utf-8",
    )

    with pytest.raises(bm.BuildM3dPairsError, match="duplicate JSON object key"):
        bm._load_and_validate_pins(pins_path)


def test_load_and_validate_pins_rejects_duplicate_nested_key(tmp_path: Path):
    """`material`（ネストした object）内の重複キーも同様に拒否する
    （`object_pairs_hook` が全ネスト層に再帰適用されることの確認）。
    """
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        _valid_pins_doc_json_prefix()
        + '"manifest_sha256": "' + "1" * 64 + '", "audio_sha256": {}, '
        + '"material": {"a.wav": "real_voice", "a.wav": "synthetic"}}',
        encoding="utf-8",
    )

    with pytest.raises(bm.BuildM3dPairsError, match="duplicate JSON object key"):
        bm._load_and_validate_pins(pins_path)


def test_load_and_validate_pins_accepts_clean_json_without_dup_keys(tmp_path: Path):
    """重複キーの無い正常な pins sidecar は従来どおり読める（偽陽性を出さない
    ことの確認）。
    """
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps(
            {
                "schema": bm._PINS_SCHEMA,
                "generated_utc": "2026-01-01T00:00:00+00:00",
                "m2c_external_fixtures_sha256": "0" * 64,
                "m3d_synth_specs_sha256": "0" * 64,
                "manifest_sha256": "1" * 64,
                "audio_sha256": {"a.wav": "2" * 64},
                "material": {"a.wav": "real_voice"},
            }
        ),
        encoding="utf-8",
    )

    doc = bm._load_and_validate_pins(pins_path)
    assert doc["material"] == {"a.wav": "real_voice"}


# --------------------------------------------------------------------------- #
# レビュー対応 第 10 ラウンド（L3）: cross-device 構成の事前拒否
# --------------------------------------------------------------------------- #
class _FakeStatResult:
    def __init__(self, st_dev: int) -> None:
        self.st_dev = st_dev


def test_reject_cross_device_publish_targets_passes_for_same_device(tmp_path: Path):
    """全公開先が staging と同一ファイルシステム上（テストでは自明に
    `tmp_path` 配下）であれば例外は飛ばない（偽陽性を出さないことの確認）。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"
    summary_out = tmp_path / "summary.json"

    bm._reject_cross_device_publish_targets(
        staging_parent=tmp_path,
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        summary_out=summary_out,
    )  # 例外が飛ばなければ OK


def test_reject_cross_device_publish_targets_detects_pins_out_mismatch(
    tmp_path: Path, monkeypatch
):
    """`--pins-out` の親ディレクトリが staging と異なる `st_dev` を返す場合、
    fail-closed で拒否する（monkeypatch で st_dev 不一致を模す。L3 対応の
    主眼）。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest_out = tmp_path / "manifest.yaml"
    pins_dir = tmp_path / "pins_fs"
    pins_dir.mkdir()
    pins_out = pins_dir / "pins.json"

    real_stat = os.stat
    mismatched = pins_dir.resolve()

    def _fake_stat(path, *args, **kwargs):
        # 注意: ここで `.resolve()`/`.exists()` 等の再 stat を伴う操作をしては
        # ならない——`os.stat` はグローバルに monkeypatch 済みのため、それ自体が
        # 本関数を再帰的に呼び出し無限再帰になる。呼び出し元
        # （`_nearest_existing_ancestor`）は既に resolve 済みの絶対パスを渡す
        # ため、単純な等価比較で十分。
        if isinstance(path, (str, Path)) and Path(path) == mismatched:
            return _FakeStatResult(st_dev=999999)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(bm.os, "stat", _fake_stat)

    with pytest.raises(bm.BuildM3dPairsError, match="staging と異なるファイルシステム"):
        bm._reject_cross_device_publish_targets(
            staging_parent=tmp_path,
            out_dir=out_dir,
            manifest_out=manifest_out,
            pins_out=pins_out,
        )


def test_reject_cross_device_publish_targets_detects_out_dir_mismatch(
    tmp_path: Path, monkeypatch
):
    out_dir = tmp_path / "out_fs"
    out_dir.mkdir()
    manifest_out = tmp_path / "manifest.yaml"
    pins_out = tmp_path / "pins.json"

    real_stat = os.stat
    mismatched = out_dir.resolve()

    def _fake_stat(path, *args, **kwargs):
        # 注意: ここで `.resolve()`/`.exists()` 等の再 stat を伴う操作をしては
        # ならない——`os.stat` はグローバルに monkeypatch 済みのため、それ自体が
        # 本関数を再帰的に呼び出し無限再帰になる。呼び出し元
        # （`_nearest_existing_ancestor`）は既に resolve 済みの絶対パスを渡す
        # ため、単純な等価比較で十分。
        if isinstance(path, (str, Path)) and Path(path) == mismatched:
            return _FakeStatResult(st_dev=999999)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(bm.os, "stat", _fake_stat)

    with pytest.raises(bm.BuildM3dPairsError, match="staging と異なるファイルシステム"):
        bm._reject_cross_device_publish_targets(
            staging_parent=tmp_path,
            out_dir=out_dir,
            manifest_out=manifest_out,
            pins_out=pins_out,
        )


def test_run_and_publish_rejects_cross_device_before_generation(tmp_path: Path, monkeypatch):
    """`run_and_publish` は cross-device 不一致を生成開始前に検出し、staging
    すら作らずに fail-closed で拒否する（既公開セット同様「部分出力なし」）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path)
    outputs = _default_outputs(tmp_path)
    outputs["pins_out"].parent.mkdir(parents=True, exist_ok=True)
    mismatched = outputs["pins_out"].parent.resolve()

    real_stat = os.stat

    def _fake_stat(path, *args, **kwargs):
        # 注意: ここで `.resolve()`/`.exists()` 等の再 stat を伴う操作をしては
        # ならない——`os.stat` はグローバルに monkeypatch 済みのため、それ自体が
        # 本関数を再帰的に呼び出し無限再帰になる。呼び出し元
        # （`_nearest_existing_ancestor`）は既に resolve 済みの絶対パスを渡す
        # ため、単純な等価比較で十分。
        if isinstance(path, (str, Path)) and Path(path) == mismatched:
            return _FakeStatResult(st_dev=999999)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(bm.os, "stat", _fake_stat)

    with pytest.raises(bm.BuildM3dPairsError, match="staging と異なるファイルシステム"):
        bm.run_and_publish(
            vocadito_dir=vocadito_dir,
            out_dir=outputs["out_dir"],
            manifest_out=outputs["manifest_out"],
            pins_out=outputs["pins_out"],
            fixtures_path=fixtures_path,
            synth_specs_path=REAL_SYNTH_SPECS_PATH,
        )

    # 生成が一切開始していない（out_dir 自体が作られていない = WAV 未生成）。
    assert not outputs["out_dir"].exists()
    assert not outputs["manifest_out"].exists()
    leftover_staging = [p for p in tmp_path.iterdir() if p.name.startswith(".external_m3d")]
    assert leftover_staging == []


# --------------------------------------------------------------------------- #
# レビュー対応 第 12 ラウンド: vocadito スナップショット束縛
# （消費バイト = 照合バイトの構造保証）
# --------------------------------------------------------------------------- #
def test_generate_vocadito_variants_immune_to_original_swap_during_decode(
    tmp_path: Path, monkeypatch
):
    """decode（`librosa.load`）呼び出しのタイミングで「原本」だけを一時的に
    差し替える攻撃（公開直前再照合の前に元へ戻す updater 相当）を模す。

    スナップショット方式（第 12 ラウンド対応）なら、`librosa.load` は原本
    ではなく `write_dir/_vocadito_snapshot/<clip_id>.wav`（decode 開始前に
    既にコピー・pin 照合済み）から読むため、decode 呼び出し時点で原本を
    差し替えても生成結果には一切影響しない——「decode が実際に消費した
    バイト」は常に承認済み（pin 一致確認済み）のスナップショットのバイトで
    あることを、`make_variants` へ渡された `y` 配列を直接捕捉して確認する
    （WAV 書き出しの量子化を経由しない厳密比較）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path, n_clips=1)
    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    resolved_audio = bm.verify_vocadito_pins(vocadito_dir, fixtures)
    clip_id = next(iter(fixtures))
    original_path = resolved_audio[clip_id]
    original_bytes = original_path.read_bytes()

    # クリーンな経路で先に「あるべき」decode 結果を計算しておく（比較対象）。
    y_clean, sr_clean = librosa.load(original_path, sr=None, mono=True)

    real_load = librosa.load
    triggered = {"done": False}

    def _load_with_original_swap(path, *args, **kwargs):
        if not triggered["done"]:
            triggered["done"] = True
            # 「decode 呼び出しのこの瞬間だけ」原本を破壊する——公開直前
            # 再照合の前に元へ戻す想定の攻撃タイミングを模す。
            original_path.write_bytes(b"malicious-tampered-bytes-not-approved")
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(bm.librosa, "load", _load_with_original_swap)

    captured: Dict[str, Any] = {}
    real_make_variants = bm.make_variants

    def _capturing_make_variants(y, sr, **kwargs):
        captured["y"] = y
        captured["sr"] = sr
        return real_make_variants(y, sr, **kwargs)

    monkeypatch.setattr(bm, "make_variants", _capturing_make_variants)

    out_dir = tmp_path / "out"
    write_dir = tmp_path / "staging_wav"
    write_dir.mkdir(parents=True)

    result = bm.generate_vocadito_variants([clip_id], resolved_audio, out_dir, write_dir, fixtures)

    assert triggered["done"] is True
    # sanity: 原本は実際に破壊されている（攻撃が発火したことの確認）。
    assert original_path.read_bytes() != original_bytes

    # スナップショットは改変前（承認済み）のバイトのまま——decode が実際に
    # 消費したのはこちら。
    snapshot_path = write_dir / "_vocadito_snapshot" / f"{clip_id}.wav"
    assert snapshot_path.read_bytes() == original_bytes

    # make_variants へ渡された y はクリーンな decode 結果と厳密一致
    # （改変後の原本の影響を一切受けていない）。
    np.testing.assert_array_equal(captured["y"], y_clean)
    assert captured["sr"] == sr_clean
    assert set(result[clip_id]) == set(bm.VOCADITO_VARIANT_ORDER)


def test_generate_vocadito_variants_snapshot_rejects_original_tampered_before_snapshot(
    tmp_path: Path, monkeypatch
):
    """snapshot コピーの**前**（decode 開始前）に原本が改変されていた場合、
    スナップショット自身の pin 再照合が fail-closed で検出する——生成開始前
    の一括照合（`verify_vocadito_pins`）と decode 実行の間に窓があっても、
    本関数独自の照合がその窓を閉じることの確認。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path, n_clips=1)
    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    resolved_audio = bm.verify_vocadito_pins(vocadito_dir, fixtures)
    clip_id = next(iter(fixtures))
    original_path = resolved_audio[clip_id]

    # 一括照合の後・generate_vocadito_variants 呼び出しの前に原本を改変する。
    original_path.write_bytes(b"tampered-before-snapshot-copy")

    out_dir = tmp_path / "out"
    write_dir = tmp_path / "staging_wav"
    write_dir.mkdir(parents=True)

    with pytest.raises(
        bm.BuildM3dPairsError, match="スナップショットの sha256 が pin と不一致"
    ):
        bm.generate_vocadito_variants([clip_id], resolved_audio, out_dir, write_dir, fixtures)

    # 生成が一切進んでいない（write_dir に WAV が書かれていない）。
    assert list(write_dir.glob("*.wav")) == []


def test_generate_vocadito_variants_snapshot_matches_direct_decode_on_clean_input(
    tmp_path: Path, monkeypatch
):
    """クリーン（無改変）な入力では、スナップショット経由の decode 結果が
    原本を直接 decode した場合と厳密一致する（バイト同一性回帰: 第 12
    ラウンドの変更が通常経路の出力を一切変えないことの確認）。
    """
    monkeypatch.setattr(bm, "ROOT", tmp_path)
    vocadito_dir, fixtures_path, _ = _make_vocadito_pool(tmp_path, n_clips=2)
    fixtures, _ = bm.load_m2c_fixtures(fixtures_path)
    resolved_audio = bm.verify_vocadito_pins(vocadito_dir, fixtures)
    clip_ids = sorted(fixtures)

    expected_variants_by_clip = {}
    for clip_id in clip_ids:
        y, sr = librosa.load(resolved_audio[clip_id], sr=None, mono=True)
        expected_variants_by_clip[clip_id] = bm.make_variants(
            y, sr, semitones=list(bm.VOCADITO_SEMITONES), time_rates=list(bm.VOCADITO_TIME_RATES)
        )

    out_dir = tmp_path / "out"
    write_dir = tmp_path / "staging_wav"
    write_dir.mkdir(parents=True)
    bm.generate_vocadito_variants(clip_ids, resolved_audio, out_dir, write_dir, fixtures)

    for clip_id in clip_ids:
        for variant_key in bm.VOCADITO_VARIANT_ORDER:
            filename = bm._vocadito_variant_filename(clip_id, variant_key)
            produced, sr_produced = sf.read(str(write_dir / filename))
            # PCM_24 書き出しの量子化誤差の範囲内で一致することを確認する
            # （`_atomic_write_wav` の docstring 参照 — 意図的に整数 PCM を
            # 使うため float 完全一致ではなく高分解能量子化誤差を許容する）。
            expected = expected_variants_by_clip[clip_id][variant_key]
            np.testing.assert_allclose(produced, expected, atol=2.0 / (2**23))
