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

    def _verify_then_tamper_on_second_call(vocadito_dir_arg, fixtures_arg):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # T2 の公開直前再照合（2 回目の呼び出し）の直前に入力を改変する
            # ——staging（生成）は 1 回目の呼び出し（run_build 内の pin 照合）
            # 通過後、既に完了している状態を模す。
            tampered_path.write_bytes(b"tampered-bytes-after-staging-complete")
        return real_verify(vocadito_dir_arg, fixtures_arg)

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
