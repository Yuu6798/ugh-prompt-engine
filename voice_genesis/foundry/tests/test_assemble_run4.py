"""test_assemble_run4.py — S3 Phase D `s1_dataprep/assemble_run4.py` の検証。

`S3_RUN4_RUNBOOK.md` §3 / `DESIGN_S3_backfill.md` §2.4 の受け入れ条件
（正常 3 話者 / 名前衝突 fail / ゲート違反 fail / 決定論 2 回一致）を
高速・合成フィクスチャで検証する（`test_convert_d3.py` と同じ流儀:
実レンダ・実 voicebank には依存しない）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import Sequence

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import assemble_run4  # noqa: E402
import build_dataset  # noqa: E402

_RITSU_HEADER = ["name", "ph_seq", "ph_dur"]
_FULL_HEADER = ["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]


def _write_wav(path: Path, duration_sec: float, *, rate: int = 24000) -> None:
    """`rate` Hz mono PCM_16 の無音 wav を `duration_sec` 秒ぶん書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = round(duration_sec * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * n_frames)


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


def _make_ritsu_raw_dir(root: Path) -> Path:
    """D2 (ritsu VCV) の最小フィクスチャ: 3 列ヘッダ (note_dur 等を持たない)。"""
    out = root / "ritsu_raw"
    _write_csv(
        out / "transcriptions.csv",
        _RITSU_HEADER,
        [
            ["ritsu_A3_001", "a i", "0.5 0.5"],
            ["ritsu_A3_002", "u", "0.8"],
        ],
    )
    _write_wav(out / "wavs" / "ritsu_A3_001.wav", 1.0)
    _write_wav(out / "wavs" / "ritsu_A3_002.wav", 0.8)
    return out


def _make_d3_raw_dir(root: Path, *, extra_row: Sequence[str] | None = None) -> Path:
    """D3 の最小フィクスチャ: 6 列ヘッダ (ph_num/note_seq/note_dur を持つ)。"""
    out = root / "d3_raw"
    rows = [
        ["sakura_seed11", "a k a", "0.3 0.3 0.4", "1 2", "60 62", "0.3 0.7"],
        ["umi_seed11", "i", "0.6", "1", "64", "0.6"],
    ]
    if extra_row is not None:
        rows.append(list(extra_row))
    _write_csv(out / "transcriptions.csv", _FULL_HEADER, rows)
    _write_wav(out / "wavs" / "sakura_seed11.wav", 1.0)
    _write_wav(out / "wavs" / "umi_seed11.wav", 0.6)
    if extra_row is not None:
        _write_wav(out / "wavs" / f"{extra_row[0]}.wav", 1.0)
    return out


def _make_pjs_raw_dir(root: Path) -> Path:
    out = root / "pjs_raw"
    _write_csv(
        out / "transcriptions.csv",
        _FULL_HEADER,
        [["pjs_song1_001", "s o", "0.4 0.4", "1 1", "60 62", "0.4 0.4"]],
    )
    _write_wav(out / "wavs" / "pjs_song1_001.wav", 0.8)
    return out


def _make_user_raw_dir(root: Path, *, ph_seq: str = "a", ph_dur: str = "0.5") -> Path:
    out = root / "user_raw"
    _write_csv(
        out / "transcriptions.csv",
        _FULL_HEADER,
        [["UC-001", ph_seq, ph_dur, "1", "60", "0.5"]],
    )
    _write_wav(out / "wavs" / "UC-001.wav", 0.5)
    (out / "exclusions.json").write_text(
        json.dumps({"excluded": []}, ensure_ascii=False), encoding="utf-8"
    )
    return out


# ---------------------------------------------------------------------------
# 1. 正常 3 話者
# ---------------------------------------------------------------------------


def test_normal_three_speaker_assembly_publishes_manifest(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    # --- ritsu(=D2+D3) 合流結果 ---
    ritsu_rows = build_dataset.read_transcriptions(out_dir / "ritsu" / "transcriptions.csv")
    assert {r["name"] for r in ritsu_rows} == {
        "ritsu_A3_001", "ritsu_A3_002", "sakura_seed11", "umi_seed11",
    }
    # D2 由来行は note_dur 列を持たない (欠落 = None) ことを確認する。
    d2_row = next(r for r in ritsu_rows if r["name"] == "ritsu_A3_001")
    assert d2_row.get("note_dur") is None
    assert d2_row.get("ph_num") is None
    d3_row = next(r for r in ritsu_rows if r["name"] == "sakura_seed11")
    assert d3_row["note_dur"] == "0.3 0.7"
    for stem in ("ritsu_A3_001", "ritsu_A3_002", "sakura_seed11", "umi_seed11"):
        assert (out_dir / "ritsu" / "wavs" / f"{stem}.wav").exists()

    # --- pjs / user はそのまま複製 ---
    pjs_rows = build_dataset.read_transcriptions(out_dir / "pjs" / "transcriptions.csv")
    assert {r["name"] for r in pjs_rows} == {"pjs_song1_001"}
    assert (out_dir / "pjs" / "wavs" / "pjs_song1_001.wav").exists()

    user_rows = build_dataset.read_transcriptions(out_dir / "user" / "transcriptions.csv")
    assert {r["name"] for r in user_rows} == {"UC-001"}
    assert (out_dir / "user" / "exclusions.json").exists()

    # --- 3 話者ゲートは 0 issue ---
    for name, spk_dir, rows in (
        ("ritsu", out_dir / "ritsu", ritsu_rows),
        ("pjs", out_dir / "pjs", pjs_rows),
        ("user", out_dir / "user", user_rows),
    ):
        problems = build_dataset.validate_speaker(name, spk_dir, rows)
        problems += build_dataset.check_ph_dur_duration(name, spk_dir / "wavs", rows)
        problems += build_dataset.check_note_dur_consistency(name, rows)
        assert problems == [], f"{name}: {problems}"

    # --- 辞書統合 ---
    dict_text = (out_dir / "dict.txt").read_text(encoding="utf-8")
    symbols = {line.split("\t")[0] for line in dict_text.splitlines() if line}
    assert symbols == {"a", "i", "u", "k", "s", "o"}

    # --- manifest ---
    assert manifest["spk_id"] == {"ritsu": 0, "pjs": 1, "user": 2}
    assert manifest["speakers"]["ritsu"]["row_count"] == 4
    assert manifest["speakers"]["ritsu"]["wav_count"] == 4
    assert manifest["speakers"]["ritsu"]["components"] == ["d2", "d3"]
    assert manifest["speakers"]["pjs"]["row_count"] == 1
    assert manifest["speakers"]["pjs"]["is_fixture"] is True
    assert manifest["speakers"]["user"]["row_count"] == 1
    assert manifest["speakers"]["user"]["has_exclusions_json"] is True
    assert manifest["collision_check"]["ritsu_d3_name_collisions"] == []
    assert manifest["collision_check"]["ritsu_d3_wav_filename_collisions"] == []
    assert manifest["gate"]["problems"] == []
    assert manifest["dict"]["symbol_count"] == 6
    assert "notes" in manifest  # pjs_is_fixture=True の明記

    manifest_on_disk = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest_on_disk == manifest


# ---------------------------------------------------------------------------
# 1.5 P1 修正 (review #265 R5): assembly_manifest.json は公開した wav 実体
# そのものへのバイト束縛（{name: sha256}）を各話者ごとに持つ
# ---------------------------------------------------------------------------


def test_manifest_records_per_wav_sha256_matching_published_bytes(tmp_path: Path) -> None:
    """`assembly_manifest.json` の各話者エントリに `wav_sha256`
    （`{basename: sha256}`）が含まれ、値が実際に公開された `<out_dir>/<spk>/
    wavs/*.wav` のバイト列の実測 sha256 と一致する（手打ちでも `wav_count`/
    `transcriptions_csv_sha256` からの類推でもなく、公開後のファイルを直接
    読んで検証する）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    for spk_name in ("ritsu", "pjs", "user"):
        wav_sha256 = manifest["speakers"][spk_name]["wav_sha256"]
        wavs_dir = out_dir / spk_name / "wavs"
        actual_names = {p.name for p in wavs_dir.glob("*.wav")}
        assert set(wav_sha256) == actual_names
        for name, expected_sha in wav_sha256.items():
            actual_sha = hashlib.sha256((wavs_dir / name).read_bytes()).hexdigest()
            assert actual_sha == expected_sha, f"{spk_name}/{name}: sha256 mismatch"
        assert manifest["speakers"][spk_name]["wav_count"] == len(wav_sha256)


def test_manifest_wav_sha256_detects_tampering(tmp_path: Path) -> None:
    """`wav_sha256` の実測値は公開直後のバイト列そのものを識別するため、
    公開後に wav が（同名のまま）差し替わればもう一致しなくなることを確認
    する（`wav_count`/`transcriptions_csv_sha256` だけでは検出できなかった
    実害シナリオ）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )
    pinned_sha = manifest["speakers"]["user"]["wav_sha256"]["UC-001.wav"]

    (out_dir / "user" / "wavs" / "UC-001.wav").write_bytes(b"tampered bytes, not the real wav")
    actual_sha = hashlib.sha256((out_dir / "user" / "wavs" / "UC-001.wav").read_bytes()).hexdigest()
    assert actual_sha != pinned_sha


# ---------------------------------------------------------------------------
# 1.6 P1 修正 (review #265 R7): 3 話者学習 config 生成
# （`run4_config_datasets.yaml` + `.normalized.yaml`）
# ---------------------------------------------------------------------------


def _assemble_normal_three_speaker(tmp_path: Path) -> Path:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True)
    return out_dir


def test_run4_config_has_same_structure_as_build_dataset_two_speaker_config_with_three_datasets(
    tmp_path: Path,
) -> None:
    """生成した 3 話者 config が `build_dataset.py` の 2 話者版
    (`build_config_yaml()`) と同一のトップレベル構造・同一の `datasets:`
    エントリ構造を持ち、`datasets` が 3 エントリ・`num_spk: 3` に
    なっていることを構造的に確認する（コーディネータ指定の構造テスト）。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)

    config_path = out_dir / "run4_config_datasets.yaml"
    assert config_path.exists()
    run4_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # build_dataset.py 自身の 2 話者版を同じ関数で生成し、キー集合を比較する
    # （read-only 再利用そのものの検証 — assemble_run4 が独自に config 構造を
    # 二重実装していないことの裏付け）。
    two_speaker_text = build_dataset.build_config_yaml(
        dict_path=Path("/dummy/dict.txt"),
        binary_data_dir=Path("/dummy/binary"),
        speakers=[
            ("ritsu", 0, Path("/dummy/ritsu"), ["a"]),
            ("pjs", 1, Path("/dummy/pjs"), ["b"]),
        ],
    )
    two_speaker_config = yaml.safe_load(two_speaker_text)

    assert set(run4_config.keys()) == set(two_speaker_config.keys())
    assert len(two_speaker_config["datasets"]) == 2
    assert len(run4_config["datasets"]) == 3
    assert run4_config["num_spk"] == 3
    assert two_speaker_config["num_spk"] == 2
    for entry in run4_config["datasets"]:
        assert set(entry.keys()) == set(two_speaker_config["datasets"][0].keys())

    names_and_ids = {(d["speaker"], d["spk_id"]) for d in run4_config["datasets"]}
    assert names_and_ids == {("ritsu", 0), ("pjs", 1), ("user", 2)}
    assert run4_config["use_spk_id"] is True
    assert run4_config["use_lang_id"] is False


def test_run4_config_raw_data_dir_points_to_final_out_dir_not_staging(tmp_path: Path) -> None:
    """config 内の `raw_data_dir`/`dictionaries.ja`/`binary_data_dir` は
    公開後の最終パス（`<out_dir>/...`）を指し、staging 中の一時パス
    （`.staging-<pid>`）を指さない。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    run4_config = yaml.safe_load((out_dir / "run4_config_datasets.yaml").read_text(encoding="utf-8"))

    for entry in run4_config["datasets"]:
        raw_dir = Path(entry["raw_data_dir"])
        assert raw_dir == out_dir / entry["speaker"]
        assert ".staging-" not in str(raw_dir)
    assert Path(run4_config["dictionaries"]["ja"]) == out_dir / "dict.txt"
    assert Path(run4_config["binary_data_dir"]) == out_dir / "binary"


def test_run4_config_normalized_copy_uses_relative_paths_and_is_host_independent(
    tmp_path: Path,
) -> None:
    """`.normalized.yaml` コピーは `out_dir` 基準の相対パスを使い、生
    config と異なり絶対パス（tmp_path 由来の実行環境固有プレフィクス）を
    含まない。さらに、この相対表現は `out_dir` の絶対位置に依存しないため、
    別々の `out_dir` へ同一論理内容で組み立てても正規化コピーはバイト
    一致する（生 config は絶対パスが埋め込まれるため一致しない——これは
    `build_dataset.py` 自身の設計どおりで回帰ではない）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir_1 = tmp_path / "run4_raw_cfg_1"
    out_dir_2 = tmp_path / "nested" / "run4_raw_cfg_2"  # 異なる深さの絶対位置
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_1, pjs_is_fixture=True)
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_2, pjs_is_fixture=True)

    normalized_1 = (out_dir_1 / "run4_config_datasets.yaml.normalized.yaml").read_text(encoding="utf-8")
    normalized_2 = (out_dir_2 / "run4_config_datasets.yaml.normalized.yaml").read_text(encoding="utf-8")
    assert normalized_1 == normalized_2  # host/絶対位置非依存

    normalized_config = yaml.safe_load(normalized_1)
    for entry in normalized_config["datasets"]:
        assert not Path(entry["raw_data_dir"]).is_absolute()
        assert str(tmp_path) not in entry["raw_data_dir"]
    assert not Path(normalized_config["dictionaries"]["ja"]).is_absolute()

    raw_1 = (out_dir_1 / "run4_config_datasets.yaml").read_text(encoding="utf-8")
    raw_2 = (out_dir_2 / "run4_config_datasets.yaml").read_text(encoding="utf-8")
    assert raw_1 != raw_2  # 絶対パスが異なるため一致しない（想定どおり）


def test_run4_config_cli_knobs_are_applied(tmp_path: Path) -> None:
    """`--binary-data-dir`/`--max-updates`/`--val-check-interval`/
    `--num-ckpt-keep`/`--n-test-prefixes` が生成 config へ反映される。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"
    custom_binary_dir = tmp_path / "custom_binary"

    assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True,
        binary_data_dir=custom_binary_dir, n_test_prefixes=1,
        max_updates=123, val_check_interval=45, num_ckpt_keep=6,
    )

    run4_config = yaml.safe_load((out_dir / "run4_config_datasets.yaml").read_text(encoding="utf-8"))
    assert Path(run4_config["binary_data_dir"]) == custom_binary_dir
    assert run4_config["max_updates"] == 123
    assert run4_config["val_check_interval"] == 45
    assert run4_config["num_ckpt_keep"] == 6
    for entry in run4_config["datasets"]:
        assert len(entry["test_prefixes"]) == 1


def test_run4_config_default_knobs_match_build_dataset_defaults(tmp_path: Path) -> None:
    """既定値省略時は `build_dataset.py` の `DEFAULT_MAX_UPDATES`/
    `DEFAULT_VAL_CHECK_INTERVAL`/`DEFAULT_NUM_CKPT_KEEP`（run 3 が実際に
    使った 40K steps・5K 節目という値）をそのまま流用する。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    run4_config = yaml.safe_load((out_dir / "run4_config_datasets.yaml").read_text(encoding="utf-8"))
    assert run4_config["max_updates"] == build_dataset.DEFAULT_MAX_UPDATES
    assert run4_config["val_check_interval"] == build_dataset.DEFAULT_VAL_CHECK_INTERVAL
    assert run4_config["num_ckpt_keep"] == build_dataset.DEFAULT_NUM_CKPT_KEEP


def test_run4_config_not_published_when_gate_validation_fails(tmp_path: Path) -> None:
    """ゲート違反時は他の成果物同様、config も一切公開されない（config
    生成が `_assemble_into` の他ステップと同じ原子的公開トランザクション内
    にあることの確認）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path, ph_seq="a b", ph_dur="0.5")  # 長さ不一致で壊す
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.GateValidationError):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert not out_dir.exists()
    assert not (out_dir / "run4_config_datasets.yaml").exists()


def test_run4_config_assembly_manifest_bytes_unchanged_by_config_generation(tmp_path: Path) -> None:
    """config 生成の追加は `assembly_manifest.json` の内容へは一切影響しない
    （新規ファイルのみの差分であることの直接確認 — R5 検証方針の踏襲）。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    manifest = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert "config" not in manifest
    assert set(manifest.keys()) == {
        "schema", "spk_id", "speakers", "collision_check", "dict", "gate", "notes",
    }


# ---------------------------------------------------------------------------
# 1.6 P1 修正 (review #265 R9): `refresh-config-pin` — 手動編集後の
# `.normalized.yaml` pin 副本再生成。「編集 → refresh → 等価検証 pass /
# 意味的差分で fail」の両方向をコーディネータが明示要求。
# ---------------------------------------------------------------------------


def test_refresh_config_pin_after_manual_edit_regenerates_normalized_copy(tmp_path: Path) -> None:
    """runbook §4 が指示する手動編集（LR/finetune/precision/勾配クリップの
    追記）を live config へ加えた後 `refresh_config_pin()` を呼ぶと、
    (1) 例外を送出せず成功し、(2) 再生成された `.normalized.yaml` が
    手動追記されたキーをそのまま含み、(3) パス系フィールドは引き続き
    `out_dir` 基準の相対パスへ正規化されていることを確認する
    （「編集 → refresh → 等価検証 pass」方向）。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    stale_normalized_text_before_edit = normalized_path.read_text(encoding="utf-8")

    # runbook §4 の手動移植を模す: LR/finetune/precision/clip を live config
    # のみへ追記する（正規化コピーは追随しないため、この時点で pin は stale）。
    with open(config_path, "a", encoding="utf-8") as f:
        f.write("lr: 0.0001\n")
        f.write("finetune_ckpt_path: /some/finetune/ckpt.ckpt\n")
        f.write("precision: 16-mixed\n")
        f.write("clip_grad_norm: 1.0\n")

    result_path = assemble_run4.refresh_config_pin(config_path)

    assert result_path == normalized_path
    refreshed = yaml.safe_load(normalized_path.read_text(encoding="utf-8"))
    assert refreshed["lr"] == 0.0001
    assert refreshed["finetune_ckpt_path"] == "/some/finetune/ckpt.ckpt"
    assert refreshed["precision"] == "16-mixed"
    assert refreshed["clip_grad_norm"] == 1.0
    # パス系フィールドは引き続き相対パス（正規化維持）
    assert not Path(refreshed["dictionaries"]["ja"]).is_absolute()
    for entry in refreshed["datasets"]:
        assert not Path(entry["raw_data_dir"]).is_absolute()
    # 手動追記前の pin から中身が変わっている（refresh が実際に再生成した証拠）
    assert normalized_path.read_text(encoding="utf-8") != stale_normalized_text_before_edit


def test_refresh_config_pin_rejects_semantic_mismatch_from_datasets_tampering(tmp_path: Path) -> None:
    """`datasets[].speaker`/`spk_id` の対応が既定マッピング（`SPK_IDS`）から
    ずれている（例: 手動編集の際に誤って spk_id を書き換えた）場合、
    `refresh_config_pin()` は `ConfigPinMismatchError` を送出し、
    `.normalized.yaml` を一切書き換えない（「意味的差分で fail」方向）。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    normalized_text_before = normalized_path.read_text(encoding="utf-8")

    live_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for entry in live_config["datasets"]:
        if entry["speaker"] == "user":
            entry["spk_id"] = 99  # 誤編集: SPK_IDS["user"] == 2 からずれる
    config_path.write_text(yaml.safe_dump(live_config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(assemble_run4.ConfigPinMismatchError):
        assemble_run4.refresh_config_pin(config_path)

    # fail-closed: 正規化コピーは一切変更されない
    assert normalized_path.read_text(encoding="utf-8") == normalized_text_before
    assert list(config_path.parent.glob("*.tmp-*")) == []


def test_refresh_config_pin_rejects_unrelated_field_drift_between_live_and_reread(
    tmp_path: Path, monkeypatch
) -> None:
    """path フィールド以外で live config と再読込した正規化コピーとの間に
    意味的な差分が生じた場合（YAML シリアライズ往復の破損や意図しない
    フィールド変異を模す）も `ConfigPinMismatchError` で fail-closed する
    ことを、`_normalize_config_dict` を monkeypatch して確認する。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    normalized_text_before = normalized_path.read_text(encoding="utf-8")

    real_normalize = assemble_run4._normalize_config_dict

    def _corrupting_normalize(live_config, config_dir):
        normalized = real_normalize(live_config, config_dir)
        normalized["max_updates"] = -1  # path フィールド以外を意図せず変異させる
        return normalized

    monkeypatch.setattr(assemble_run4, "_normalize_config_dict", _corrupting_normalize)

    with pytest.raises(assemble_run4.ConfigPinMismatchError):
        assemble_run4.refresh_config_pin(config_path)

    assert normalized_path.read_text(encoding="utf-8") == normalized_text_before
    assert list(config_path.parent.glob("*.tmp-*")) == []


def test_refresh_config_pin_missing_config_fails_closed(tmp_path: Path) -> None:
    """live config が存在しない場合は `RefreshConfigPinError` で fail-closed
    する。"""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(assemble_run4.RefreshConfigPinError):
        assemble_run4.refresh_config_pin(missing)


def test_main_refresh_config_pin_subcommand_end_to_end(tmp_path: Path, capsys) -> None:
    """`assemble_run4.py refresh-config-pin --config <path>` の CLI 経路が
    `refresh_config_pin()` と同じ結果を公開し、既存の（サブコマンド無指定）
    `main()` 呼び出し形式は従来どおり `_main_assemble` へ委譲されることを
    確認する（後方互換性）。"""
    out_dir = _assemble_normal_three_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    with open(config_path, "a", encoding="utf-8") as f:
        f.write("lr: 0.0002\n")

    exit_code = assemble_run4.main(["refresh-config-pin", "--config", str(config_path)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "wrote" in captured.out

    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    refreshed = yaml.safe_load(normalized_path.read_text(encoding="utf-8"))
    assert refreshed["lr"] == 0.0002


def _make_empty_pjs_raw_dir(root: Path) -> Path:
    """pjs の空データセット（ヘッダのみ・データ行 0 件）フィクスチャ。"""
    out = root / "pjs_raw_empty"
    _write_csv(out / "transcriptions.csv", _FULL_HEADER, [])
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# 1.5 P1 修正 (review #265): 空話者コーパス（transcriptions.csv 行 0 件）は
# fail-closed（`validate_speaker`等が空リストに no-op で通過する false-success
# 経路を明示的に閉じる）
# ---------------------------------------------------------------------------


def test_empty_speaker_corpus_fails_closed(tmp_path: Path) -> None:
    """pjs の raw dir がヘッダのみ・データ行 0 件の場合、
    `validate_speaker`/`check_ph_dur_duration`/`check_note_dur_consistency`
    はいずれも空リストに対して no-op（`problems=[]`）で通過してしまうため、
    ゲート回付の前に明示的に検出して fail-closed する（既存出力の false
    -success な置換を防止）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_empty_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.GateValidationError) as exc_info:
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert any("zero row" in p and "pjs" in p for p in exc_info.value.problems)
    assert not out_dir.exists()


def test_empty_speaker_corpus_does_not_clobber_existing_out_dir(tmp_path: Path) -> None:
    """既存の `out_dir`（前回の正常な組み立て結果）がある状態で、空の pjs
    コーパスから再度 `assemble()` を呼んでも既存の出力は破壊されない。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True)
    existing_manifest_bytes = (out_dir / "assembly_manifest.json").read_bytes()

    empty_pjs_raw = _make_empty_pjs_raw_dir(tmp_path)
    with pytest.raises(assemble_run4.GateValidationError):
        assemble_run4.assemble(ritsu_raw, d3_raw, empty_pjs_raw, user_raw, out_dir)

    assert (out_dir / "assembly_manifest.json").read_bytes() == existing_manifest_bytes


# ---------------------------------------------------------------------------
# 1.6 P1 修正 (review #265): 衝突検査を assemble() 自身が行う（CLI 経由でなくても
# fail-closed）
# ---------------------------------------------------------------------------


def test_assemble_rejects_out_dir_colliding_with_raw_dir_without_cli(tmp_path: Path) -> None:
    """CLI `main()` を経由しない直接呼び出しでも `--out-dir` が 4 つの raw dir
    のいずれかと衝突していれば `assemble()` 自身が fail-closed で拒否する
    （review #265 P1: 旧実装は CLI のみが preflight していたため、非 CLI
    経路はこの検査を素通りしていた）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)

    with pytest.raises(assemble_run4.convert_d3.OutputCollisionError):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, user_raw)


def test_assemble_rejects_out_dir_inside_raw_dir_without_cli(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = d3_raw / "nested_out"

    with pytest.raises(assemble_run4.convert_d3.OutputCollisionError):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)


# ---------------------------------------------------------------------------
# 2. 名前衝突 fail-closed
# ---------------------------------------------------------------------------


def test_name_collision_between_ritsu_and_d3_fails_closed(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    # D3 側に ritsu と同名の行を混入させる（row/wav 双方衝突）。
    d3_raw = _make_d3_raw_dir(
        tmp_path, extra_row=["ritsu_A3_001", "a", "1.0", "1", "60", "1.0"]
    )
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.NameCollisionError, match="ritsu_A3_001"):
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 3. ゲート違反 fail-closed
# ---------------------------------------------------------------------------


def test_gate_violation_fails_closed(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    # user 行を ph_seq/ph_dur 長さ不一致で壊す（validate_speaker が検出する）。
    user_raw = _make_user_raw_dir(tmp_path, ph_seq="a b", ph_dur="0.5")
    out_dir = tmp_path / "run4_raw"

    with pytest.raises(assemble_run4.GateValidationError) as exc_info:
        assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)
    assert exc_info.value.problems  # 非空
    assert any("length mismatch" in p for p in exc_info.value.problems)
    assert not out_dir.exists()


# ---------------------------------------------------------------------------
# 4. 決定論: 同一入力 -> 同一出力バイト列
# ---------------------------------------------------------------------------


def test_assembly_is_deterministic_across_two_runs(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir_1 = tmp_path / "run4_raw_1"
    out_dir_2 = tmp_path / "run4_raw_2"

    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_1, pjs_is_fixture=True)
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir_2, pjs_is_fixture=True)

    rel_paths = [
        Path("ritsu/transcriptions.csv"),
        Path("ritsu/wavs/ritsu_A3_001.wav"),
        Path("ritsu/wavs/ritsu_A3_002.wav"),
        Path("ritsu/wavs/sakura_seed11.wav"),
        Path("ritsu/wavs/umi_seed11.wav"),
        Path("pjs/transcriptions.csv"),
        Path("pjs/wavs/pjs_song1_001.wav"),
        Path("user/transcriptions.csv"),
        Path("user/wavs/UC-001.wav"),
        Path("user/exclusions.json"),
        Path("dict.txt"),
        Path("assembly_manifest.json"),
    ]
    for rel in rel_paths:
        b1 = (out_dir_1 / rel).read_bytes()
        b2 = (out_dir_2 / rel).read_bytes()
        assert b1 == b2, f"mismatch: {rel}"
