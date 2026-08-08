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

    pairs, audio_source_lookup = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
        staging_wav_dir=staging_wav_dir,
        fixtures_path=fixtures_path,
        synth_specs_path=REAL_SYNTH_SPECS_PATH,
    )
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

    with pytest.raises(bm.BuildM3dPairsError, match="MISSING"):
        bm.verify_vocadito_pins(vocadito_dir, bm.load_m2c_fixtures(fixtures_path))


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
        )


def test_load_and_validate_pins_rejects_missing_required_key(tmp_path: Path):
    pins_path = tmp_path / "pins.json"
    pins_path.write_text(
        json.dumps(
            {
                "schema": bm._PINS_SCHEMA,
                "generated_utc": "2026-01-01T00:00:00+00:00",
                "m2c_external_fixtures_sha256": "0" * 64,
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

    pairs, _ = bm.run_build(
        vocadito_dir=vocadito_dir,
        out_dir=out_dir,
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
