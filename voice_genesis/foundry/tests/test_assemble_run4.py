"""test_assemble_run4.py — `s1_dataprep/assemble_run4.py`（spk_id map v2）の検証。

`DESIGN_S4_run5.md` §1–§2 の受け入れ条件（正常 4 話者・全話者バイト単位
コピー / ゲート違反 fail / 決定論 2 回一致 / `refresh-config-pin` の 4 話者
生リスト形状検査）を高速・合成フィクスチャで検証する（`test_convert_d3.py`
と同じ流儀: 実レンダ・実 voicebank には依存しない）。run 4 までの D2/D3
マージサブシステム検証（名前衝突 fail 等）はマージ撤去（DESIGN_S4 §2-2）に
伴い削除し、代わりに「入力 raw dir とのバイト一致」を固定する。
"""
from __future__ import annotations

import copy
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
# 1. 正常 4 話者（spk_id map v2・全話者バイト単位コピー）
# ---------------------------------------------------------------------------


def test_normal_four_speaker_assembly_publishes_manifest(tmp_path: Path) -> None:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run5_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    # --- ritsu は D2 のみ（D3 はもうマージされない） ---
    ritsu_rows = build_dataset.read_transcriptions(out_dir / "ritsu" / "transcriptions.csv")
    assert {r["name"] for r in ritsu_rows} == {"ritsu_A3_001", "ritsu_A3_002"}
    # D2 の 3 列 CSV はそのまま複製され、note_dur 列を持たない (欠落 = None)。
    d2_row = next(r for r in ritsu_rows if r["name"] == "ritsu_A3_001")
    assert d2_row.get("note_dur") is None
    assert d2_row.get("ph_num") is None
    for stem in ("ritsu_A3_001", "ritsu_A3_002"):
        assert (out_dir / "ritsu" / "wavs" / f"{stem}.wav").exists()

    # --- d3synth は第 4 話者としてそのまま複製 ---
    d3synth_rows = build_dataset.read_transcriptions(
        out_dir / "d3synth" / "transcriptions.csv"
    )
    assert {r["name"] for r in d3synth_rows} == {"sakura_seed11", "umi_seed11"}
    d3_row = next(r for r in d3synth_rows if r["name"] == "sakura_seed11")
    assert d3_row["note_dur"] == "0.3 0.7"
    for stem in ("sakura_seed11", "umi_seed11"):
        assert (out_dir / "d3synth" / "wavs" / f"{stem}.wav").exists()
        assert not (out_dir / "ritsu" / "wavs" / f"{stem}.wav").exists()

    # --- pjs / user はそのまま複製 ---
    pjs_rows = build_dataset.read_transcriptions(out_dir / "pjs" / "transcriptions.csv")
    assert {r["name"] for r in pjs_rows} == {"pjs_song1_001"}
    assert (out_dir / "pjs" / "wavs" / "pjs_song1_001.wav").exists()

    user_rows = build_dataset.read_transcriptions(out_dir / "user" / "transcriptions.csv")
    assert {r["name"] for r in user_rows} == {"UC-001"}
    assert (out_dir / "user" / "exclusions.json").exists()

    # --- 4 話者ゲートは 0 issue ---
    for name, spk_dir, rows in (
        ("ritsu", out_dir / "ritsu", ritsu_rows),
        ("pjs", out_dir / "pjs", pjs_rows),
        ("user", out_dir / "user", user_rows),
        ("d3synth", out_dir / "d3synth", d3synth_rows),
    ):
        problems = build_dataset.validate_speaker(name, spk_dir, rows)
        problems += build_dataset.check_ph_dur_duration(name, spk_dir / "wavs", rows)
        problems += build_dataset.check_note_dur_consistency(name, rows)
        assert problems == [], f"{name}: {problems}"

    # --- 辞書統合（4 話者分のシンボル集合） ---
    dict_text = (out_dir / "dict.txt").read_text(encoding="utf-8")
    symbols = {line.split("\t")[0] for line in dict_text.splitlines() if line}
    assert symbols == {"a", "i", "u", "k", "s", "o"}

    # --- manifest ---
    assert manifest["spk_id"] == {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}
    assert manifest["speakers"]["ritsu"]["row_count"] == 2
    assert manifest["speakers"]["ritsu"]["wav_count"] == 2
    assert manifest["speakers"]["d3synth"]["row_count"] == 2
    assert manifest["speakers"]["d3synth"]["wav_count"] == 2
    assert manifest["speakers"]["d3synth"]["spk_id"] == 3
    assert manifest["speakers"]["pjs"]["row_count"] == 1
    assert manifest["speakers"]["pjs"]["is_fixture"] is True
    assert manifest["speakers"]["user"]["row_count"] == 1
    assert manifest["speakers"]["user"]["has_exclusions_json"] is True
    # D2/D3 マージ専用だった collision_check / components 系フィールドは
    # schema 0.4 で撤去済み。
    assert "collision_check" not in manifest
    assert "components" not in manifest["speakers"]["ritsu"]
    assert manifest["gate"]["problems"] == []
    assert manifest["dict"]["symbol_count"] == 6
    assert "notes" in manifest  # pjs_is_fixture=True の明記

    manifest_on_disk = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest_on_disk == manifest


def test_all_four_speakers_are_byte_identical_copies_of_inputs(tmp_path: Path) -> None:
    """マージ撤去後の中核契約: 4 話者すべての `transcriptions.csv`（+ wav 実体）
    が入力 raw dir とバイト一致する（加工・行連結・ヘッダ変換が一切介在
    しない）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run5_raw"

    assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    for src_dir, spk_name in (
        (ritsu_raw, "ritsu"), (d3_raw, "d3synth"), (pjs_raw, "pjs"), (user_raw, "user"),
    ):
        assert (
            (out_dir / spk_name / "transcriptions.csv").read_bytes()
            == (src_dir / "transcriptions.csv").read_bytes()
        ), f"{spk_name}: transcriptions.csv not byte-identical"
        for src_wav in sorted((src_dir / "wavs").glob("*.wav")):
            assert (
                (out_dir / spk_name / "wavs" / src_wav.name).read_bytes()
                == src_wav.read_bytes()
            ), f"{spk_name}/{src_wav.name}: wav not byte-identical"
    assert (
        (out_dir / "user" / "exclusions.json").read_bytes()
        == (user_raw / "exclusions.json").read_bytes()
    )


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

    for spk_name in ("ritsu", "pjs", "user", "d3synth"):
        wav_sha256 = manifest["speakers"][spk_name]["wav_sha256"]
        wavs_dir = out_dir / spk_name / "wavs"
        actual_names = {p.name for p in wavs_dir.glob("*.wav")}
        assert set(wav_sha256) == actual_names
        for name, expected_sha in wav_sha256.items():
            actual_sha = hashlib.sha256((wavs_dir / name).read_bytes()).hexdigest()
            assert actual_sha == expected_sha, f"{spk_name}/{name}: sha256 mismatch"
        assert manifest["speakers"][spk_name]["wav_count"] == len(wav_sha256)


def test_manifest_records_exclusions_json_sha256_matching_published_bytes(tmp_path: Path) -> None:
    """P2 修正 (review #265 R11): user の `exclusions.json` を run-4 バンドルへ
    コピーする際、`assembly_manifest.json` の `speakers.user.exclusions_json_sha256`
    へ、staged 出力バイトから実測した sha256 を記帳する（旧実装は
    `has_exclusions_json` の真偽値のみで実体へのバイト束縛が無かった）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    published_exclusions = out_dir / "user" / "exclusions.json"
    assert published_exclusions.exists()
    expected_sha = hashlib.sha256(published_exclusions.read_bytes()).hexdigest()
    assert manifest["speakers"]["user"]["exclusions_json_sha256"] == expected_sha

    manifest_on_disk = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest_on_disk["speakers"]["user"]["exclusions_json_sha256"] == expected_sha


def test_manifest_exclusions_json_sha256_is_none_when_absent(tmp_path: Path) -> None:
    """`exclusions.json` を持たない user raw dir では `exclusions_json_sha256`
    は `None`（`has_exclusions_json` が `False` の場合との整合）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    (user_raw / "exclusions.json").unlink()
    out_dir = tmp_path / "run4_raw"

    manifest = assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir)

    assert manifest["speakers"]["user"]["has_exclusions_json"] is False
    assert manifest["speakers"]["user"]["exclusions_json_sha256"] is None
    assert not (out_dir / "user" / "exclusions.json").exists()


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


def _assemble_normal_four_speaker(tmp_path: Path) -> Path:
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(tmp_path)
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run4_raw"
    assemble_run4.assemble(ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True)
    return out_dir


def test_run4_config_has_same_structure_as_build_dataset_two_speaker_config_with_four_datasets(
    tmp_path: Path,
) -> None:
    """生成した 4 話者 config が `build_dataset.py` の 2 話者版
    (`build_config_yaml()`) と同一のトップレベル構造・同一の `datasets:`
    エントリ構造を持ち、`datasets` が 4 エントリ・`num_spk: 4` に
    なっていることを構造的に確認する（コーディネータ指定の構造テスト）。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)

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
    assert len(run4_config["datasets"]) == 4
    assert run4_config["num_spk"] == 4
    assert two_speaker_config["num_spk"] == 2
    for entry in run4_config["datasets"]:
        assert set(entry.keys()) == set(two_speaker_config["datasets"][0].keys())

    names_and_ids = [(d["speaker"], d["spk_id"]) for d in run4_config["datasets"]]
    # 既存話者の spk_id 不変 + d3synth は末尾追加（DESIGN_S4 §1.1）。
    assert names_and_ids == [("ritsu", 0), ("pjs", 1), ("user", 2), ("d3synth", 3)]
    assert run4_config["use_spk_id"] is True
    assert run4_config["use_lang_id"] is False


def test_run4_config_raw_data_dir_points_to_final_out_dir_not_staging(tmp_path: Path) -> None:
    """config 内の `raw_data_dir`/`dictionaries.ja`/`binary_data_dir` は
    公開後の最終パス（`<out_dir>/...`）を指し、staging 中の一時パス
    （`.staging-<pid>`）を指さない。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
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
    out_dir = _assemble_normal_four_speaker(tmp_path)
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


def test_run4_config_assembly_manifest_records_config_sha256(tmp_path: Path) -> None:
    """P1 修正 (review #265 R11): config 生成は `assembly_manifest.json` の
    `config` セクションへ live/normalized 両方の sha256 を記帳する（旧実装は
    `_write_run4_config()` の戻り値〔sha256 2 値〕を握り潰し、manifest には
    一切記録されなかった）。schema が 4 話者版 `0.4` へ上がっていることを
    確認する。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    manifest = json.loads((out_dir / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "run4-assembly-manifest/0.4"
    assert set(manifest.keys()) == {
        "schema", "spk_id", "speakers", "dict", "config", "gate", "notes",
    }

    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    assert manifest["config"]["config_path"] == config_path.name
    assert manifest["config"]["normalized_config_path"] == normalized_path.name
    assert manifest["config"]["config_sha256"] == hashlib.sha256(
        config_path.read_bytes()
    ).hexdigest()
    assert manifest["config"]["normalized_config_sha256"] == hashlib.sha256(
        normalized_path.read_bytes()
    ).hexdigest()


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
    out_dir = _assemble_normal_four_speaker(tmp_path)
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
    out_dir = _assemble_normal_four_speaker(tmp_path)
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
    out_dir = _assemble_normal_four_speaker(tmp_path)
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


def test_refresh_config_pin_rejects_duplicate_dataset_row_folded_to_matching_map(
    tmp_path: Path,
) -> None:
    """P1 修正 (review #265 R11): `datasets` に同一 speaker の重複エントリ
    （例: ritsu が誤って 2 行、計 4 エントリ）が混入していても、旧実装の
    `{speaker: spk_id}` dict 内包による比較では畳まれて `SPK_IDS` と一致して
    しまい PASS していた。本テストは重複行があっても畳んだマップが偶然
    `SPK_IDS` と一致するケース（重複行が既存行と全く同一内容）を再現し、
    それでも `ConfigPinMismatchError` で fail-closed することを確認する。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    normalized_text_before = normalized_path.read_text(encoding="utf-8")

    live_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    ritsu_entry = next(e for e in live_config["datasets"] if e["speaker"] == "ritsu")
    # 重複行を追加する — 内容は既存 ritsu 行と完全に同一なので、
    # {speaker: spk_id} へ畳めば依然として SPK_IDS と一致してしまう
    # （旧実装が見逃していた fail-open 経路そのもの）。
    live_config["datasets"].append(copy.deepcopy(ritsu_entry))
    assert len(live_config["datasets"]) == 5
    config_path.write_text(
        yaml.safe_dump(live_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(assemble_run4.ConfigPinMismatchError) as exc_info:
        assemble_run4.refresh_config_pin(config_path)
    assert any("5" in d or "duplicate" in d for d in exc_info.value.diffs)

    # fail-closed: 正規化コピーは一切変更されない
    assert normalized_path.read_text(encoding="utf-8") == normalized_text_before
    assert list(config_path.parent.glob("*.tmp-*")) == []


def test_refresh_config_pin_rejects_datasets_out_of_order(tmp_path: Path) -> None:
    """`datasets` のエントリ数・speaker/spk_id の集合は正しくても、順序が
    既定（ritsu, pjs, user, d3synth）からずれていれば fail-closed する
    （リスト形状そのものを検査するチェックの「順序」側）。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"

    live_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    live_config["datasets"] = list(reversed(live_config["datasets"]))
    config_path.write_text(
        yaml.safe_dump(live_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(assemble_run4.ConfigPinMismatchError):
        assemble_run4.refresh_config_pin(config_path)


def test_refresh_config_pin_missing_config_fails_closed(tmp_path: Path) -> None:
    """live config が存在しない場合は `RefreshConfigPinError` で fail-closed
    する。"""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(assemble_run4.RefreshConfigPinError):
        assemble_run4.refresh_config_pin(missing)


def test_validate_live_datasets_shape_accepts_canonical_entries() -> None:
    datasets = [
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "user", "spk_id": 2},
        {"speaker": "d3synth", "spk_id": 3},
    ]
    assert assemble_run4._validate_live_datasets_shape(datasets) == []


def test_validate_live_datasets_shape_rejects_duplicate_row_count(tmp_path: Path) -> None:
    """5 エントリ（ritsu が重複）は、畳んだ `{speaker: spk_id}` マップが
    `SPK_IDS` と一致していても、リスト形状（エントリ数 4 期待）で拒否する。"""
    datasets = [
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "user", "spk_id": 2},
        {"speaker": "d3synth", "spk_id": 3},
    ]
    diffs = assemble_run4._validate_live_datasets_shape(datasets)
    assert diffs
    assert any("4" in d for d in diffs)


def test_validate_live_datasets_shape_rejects_missing_d3synth_row() -> None:
    """v1（3 話者）形状の config は run 5 ではもう有効ではない — d3synth 行を
    欠く 3 エントリはエントリ数検査で拒否する（spk_id map v2 への追随漏れの
    検出）。"""
    datasets = [
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "user", "spk_id": 2},
    ]
    diffs = assemble_run4._validate_live_datasets_shape(datasets)
    assert diffs
    assert any("4" in d for d in diffs)


def test_validate_live_datasets_shape_rejects_not_a_list() -> None:
    diffs = assemble_run4._validate_live_datasets_shape("not-a-list")
    assert diffs
    assert any("not a list" in d for d in diffs)


def test_validate_live_datasets_shape_rejects_wrong_order() -> None:
    datasets = [
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "user", "spk_id": 2},
        {"speaker": "d3synth", "spk_id": 3},
    ]
    diffs = assemble_run4._validate_live_datasets_shape(datasets)
    assert diffs
    assert any("order" in d for d in diffs)


def test_refresh_config_pin_updates_manifest_config_sha256(tmp_path: Path) -> None:
    """P1 修正 (review #265 R11): `refresh_config_pin()` 実行後、
    `assembly_manifest.json` の `config.config_sha256`/
    `config.normalized_config_sha256` が実測値へ更新される（手動編集で
    live config のバイト列が変わっているため、`assemble()` 実行時に記帳
    した値は refresh 前は stale になっている）。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    manifest_path = out_dir / "assembly_manifest.json"

    manifest_before = json.loads(manifest_path.read_text(encoding="utf-8"))
    stale_config_sha = manifest_before["config"]["config_sha256"]

    with open(config_path, "a", encoding="utf-8") as f:
        f.write("lr: 0.0001\n")
    # live config のバイト列は変わったが、記帳値はまだ編集前のまま stale。
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() != stale_config_sha

    assemble_run4.refresh_config_pin(config_path)

    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    expected_normalized_sha = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
    assert manifest_after["config"]["config_sha256"] == expected_config_sha
    assert manifest_after["config"]["normalized_config_sha256"] == expected_normalized_sha
    assert manifest_after["config"]["config_sha256"] != stale_config_sha
    # config セクション以外は変更されない
    manifest_before["config"] = manifest_after["config"]
    assert manifest_before == manifest_after


def test_refresh_config_pin_reads_live_config_bytes_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 修正 (review #14): 旧実装は live config を parse 用と
    `config.config_sha256` 記帳用とで別々に読んでおり、2 読の間にファイルが
    書き換わると「旧バイト由来の normalized コピー + 新バイトの
    config_sha256」という意味的に不整合な pin 束が成立し得た。本テストは
    1 回目の `read_bytes()` が返った直後にディスク上の `config_path` を
    別内容へ差し替えるフックを注入し、(1) `config_path` への
    `Path.read_bytes` 呼び出しが厳密に 1 回であること、(2) 公開された
    `.normalized.yaml`（parse 結果由来）と manifest の `config_sha256` が
    いずれも「parse に使われた旧バイト列」基準で一致し、差し替え後の新
    バイト列の sha256 には汚染されないことを確認する。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    manifest_path = out_dir / "assembly_manifest.json"

    with open(config_path, "a", encoding="utf-8") as f:
        f.write("lr: 0.0001\n")
    stale_bytes = config_path.read_bytes()  # parse に使われるべきバイト列

    swapped_bytes = stale_bytes.replace(b"lr: 0.0001", b"lr: 0.9999")
    assert swapped_bytes != stale_bytes

    real_read_bytes = Path.read_bytes
    read_call_count = {"n": 0}

    def _tracking_read_bytes(self: Path) -> bytes:
        if self == config_path:
            read_call_count["n"] += 1
            data = real_read_bytes(self)
            if read_call_count["n"] == 1:
                # 1 回目の read が返った直後、ディスク上のファイルを
                # 別内容へ差し替える（parse 用・sha256 用に 2 回読む実装
                # であれば、2 回目の read はこの新バイト列を拾ってしまう）。
                config_path.write_bytes(swapped_bytes)
            return data
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _tracking_read_bytes)

    assemble_run4.refresh_config_pin(config_path)

    assert read_call_count["n"] == 1

    expected_sha256 = hashlib.sha256(stale_bytes).hexdigest()
    swapped_sha256 = hashlib.sha256(swapped_bytes).hexdigest()
    assert expected_sha256 != swapped_sha256

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["config"]["config_sha256"] == expected_sha256
    assert manifest["config"]["config_sha256"] != swapped_sha256

    # parse に使われたのも旧バイト列であること（normalized コピーへ反映
    # された値が「差し替え後」ではなく「差し替え前」の lr であることで
    # 検証する）。
    refreshed = yaml.safe_load(normalized_path.read_text(encoding="utf-8"))
    assert refreshed["lr"] == 0.0001


def test_publish_config_pin_transaction_rolls_back_both_files_on_mid_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 修正 (review #265 R12) 単体テスト: `.normalized.yaml` と
    `assembly_manifest.json` の 2 ファイル公開の**途中**（2 個目の
    `os.replace`）で失敗が注入されても、両方とも旧バイト列のまま無傷で
    残る（1 個目だけ新しい「新 config + 旧/破損 manifest」の不整合バンドル
    にならない）。staging/backup の残骸も残らない。"""
    normalized_path = tmp_path / "run4_config_datasets.yaml.normalized.yaml"
    manifest_path = tmp_path / "assembly_manifest.json"

    old_normalized_bytes = b"old-normalized-content\n"
    old_manifest_bytes = b'{"config": {"config_sha256": "old"}}\n'
    normalized_path.write_bytes(old_normalized_bytes)
    manifest_path.write_bytes(old_manifest_bytes)

    real_os_replace = assemble_run4.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated I/O failure during manifest publish")
        return real_os_replace(src, dst)

    monkeypatch.setattr(assemble_run4.os, "replace", _flaky_replace)

    with pytest.raises(OSError, match="simulated I/O failure"):
        assemble_run4._publish_config_pin_transaction(
            normalized_path, "new-normalized-content\n",
            manifest_path, '{"config": {"config_sha256": "new"}}\n',
        )

    assert normalized_path.read_bytes() == old_normalized_bytes
    assert manifest_path.read_bytes() == old_manifest_bytes
    leftovers = {p.name for p in tmp_path.iterdir()}
    assert leftovers == {normalized_path.name, manifest_path.name}


def test_publish_config_pin_transaction_rolls_back_when_first_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 個目（normalized）の `os.replace` そのものが失敗した場合も、両方の
    宛先が旧バイト列のまま無傷で残る（manifest 側は退避 rename すら発生し
    ない設計だが、normalized 側の退避も正しく巻き戻ることを確認する）。"""
    normalized_path = tmp_path / "run4_config_datasets.yaml.normalized.yaml"
    manifest_path = tmp_path / "assembly_manifest.json"

    old_normalized_bytes = b"old-normalized-content\n"
    old_manifest_bytes = b'{"config": {"config_sha256": "old"}}\n'
    normalized_path.write_bytes(old_normalized_bytes)
    manifest_path.write_bytes(old_manifest_bytes)

    real_os_replace = assemble_run4.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated I/O failure during normalized publish")
        return real_os_replace(src, dst)

    monkeypatch.setattr(assemble_run4.os, "replace", _flaky_replace)

    with pytest.raises(OSError, match="simulated I/O failure"):
        assemble_run4._publish_config_pin_transaction(
            normalized_path, "new-normalized-content\n",
            manifest_path, '{"config": {"config_sha256": "new"}}\n',
        )

    assert normalized_path.read_bytes() == old_normalized_bytes
    assert manifest_path.read_bytes() == old_manifest_bytes
    leftovers = {p.name for p in tmp_path.iterdir()}
    assert leftovers == {normalized_path.name, manifest_path.name}


def test_refresh_config_pin_leaves_both_files_untouched_on_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指摘 3 の受け入れ条件（統合）: `refresh_config_pin()` の公開フェーズで
    中断（manifest 側の `os.replace` が失敗）が起きても、`.normalized.yaml`
    と `assembly_manifest.json` はどちらも編集前のバイト列のまま残る
    （「新 config + 旧/破損 manifest」の不整合バンドルが発生しない）。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
    config_path = out_dir / "run4_config_datasets.yaml"
    normalized_path = out_dir / "run4_config_datasets.yaml.normalized.yaml"
    manifest_path = out_dir / "assembly_manifest.json"

    normalized_before = normalized_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    with open(config_path, "a", encoding="utf-8") as f:
        f.write("lr: 0.0003\n")

    real_os_replace = assemble_run4.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated publish failure")
        return real_os_replace(src, dst)

    monkeypatch.setattr(assemble_run4.os, "replace", _flaky_replace)

    with pytest.raises(OSError, match="simulated publish failure"):
        assemble_run4.refresh_config_pin(config_path)

    assert normalized_path.read_bytes() == normalized_before
    assert manifest_path.read_bytes() == manifest_before


def test_refresh_config_pin_without_sibling_manifest_is_a_noop_for_manifest(
    tmp_path: Path,
) -> None:
    """`assembly_manifest.json` が config と同じディレクトリに存在しない
    （`assemble()` を経由しない直接テスト等）場合、`refresh_config_pin()` は
    manifest 更新をスキップして通常どおり成功する（fail-closed の理由には
    しない）。"""
    out_dir = tmp_path / "standalone"
    out_dir.mkdir()
    config_path = out_dir / "run4_config_datasets.yaml"
    config_text = build_dataset.build_config_yaml(
        dict_path=out_dir / "dict.txt",
        binary_data_dir=out_dir / "binary",
        speakers=[
            ("ritsu", 0, out_dir / "ritsu", ["a"]),
            ("pjs", 1, out_dir / "pjs", ["b"]),
            ("user", 2, out_dir / "user", ["c"]),
            ("d3synth", 3, out_dir / "d3synth", ["d"]),
        ],
    )
    config_path.write_text(config_text, encoding="utf-8")

    result_path = assemble_run4.refresh_config_pin(config_path)

    assert result_path.exists()
    assert not (out_dir / "assembly_manifest.json").exists()


def test_main_refresh_config_pin_subcommand_end_to_end(tmp_path: Path, capsys) -> None:
    """`assemble_run4.py refresh-config-pin --config <path>` の CLI 経路が
    `refresh_config_pin()` と同じ結果を公開し、既存の（サブコマンド無指定）
    `main()` 呼び出し形式は従来どおり `_main_assemble` へ委譲されることを
    確認する（後方互換性）。"""
    out_dir = _assemble_normal_four_speaker(tmp_path)
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
# 2. 話者間の同名行はもう衝突ではない（マージ撤去の帰結）
# ---------------------------------------------------------------------------


def test_same_name_across_ritsu_and_d3synth_is_not_a_collision(tmp_path: Path) -> None:
    """run 4 まではマージ先 (ritsu) 内での name/wav 衝突として fail-closed
    していたが、run 5 では ritsu と d3synth は別話者ディレクトリに分離される
    ため、同名行が存在しても正常に組み立てられる（話者ディレクトリが分ける
    ので衝突自体が構造的に発生しない）。"""
    ritsu_raw = _make_ritsu_raw_dir(tmp_path)
    d3_raw = _make_d3_raw_dir(
        tmp_path, extra_row=["ritsu_A3_001", "a", "1.0", "1", "60", "1.0"]
    )
    pjs_raw = _make_pjs_raw_dir(tmp_path)
    user_raw = _make_user_raw_dir(tmp_path)
    out_dir = tmp_path / "run5_raw"

    manifest = assemble_run4.assemble(
        ritsu_raw, d3_raw, pjs_raw, user_raw, out_dir, pjs_is_fixture=True
    )

    assert (out_dir / "ritsu" / "wavs" / "ritsu_A3_001.wav").exists()
    assert (out_dir / "d3synth" / "wavs" / "ritsu_A3_001.wav").exists()
    # 双方とも入力バイトのまま（互いを上書きしない）。
    assert (
        (out_dir / "ritsu" / "wavs" / "ritsu_A3_001.wav").read_bytes()
        == (ritsu_raw / "wavs" / "ritsu_A3_001.wav").read_bytes()
    )
    assert (
        (out_dir / "d3synth" / "wavs" / "ritsu_A3_001.wav").read_bytes()
        == (d3_raw / "wavs" / "ritsu_A3_001.wav").read_bytes()
    )
    assert manifest["speakers"]["d3synth"]["row_count"] == 3


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
        Path("d3synth/transcriptions.csv"),
        Path("d3synth/wavs/sakura_seed11.wav"),
        Path("d3synth/wavs/umi_seed11.wav"),
        Path("pjs/transcriptions.csv"),
        Path("pjs/wavs/pjs_song1_001.wav"),
        Path("user/transcriptions.csv"),
        Path("user/wavs/UC-001.wav"),
        Path("user/exclusions.json"),
        Path("dict.txt"),
    ]
    for rel in rel_paths:
        b1 = (out_dir_1 / rel).read_bytes()
        b2 = (out_dir_2 / rel).read_bytes()
        assert b1 == b2, f"mismatch: {rel}"

    # `assembly_manifest.json` は review #265 R11 P1 以降、live config の
    # sha256（`config.config_sha256`）を記帳する。live config は
    # `raw_data_dir`/`dict_path`/`binary_data_dir` に `out_dir` の絶対パスを
    # 埋め込むため（`test_run4_config_normalized_copy_uses_relative_paths_and_is_host_independent`
    # が確認する既存の設計どおり）、`out_dir` の絶対位置が異なれば
    # `config.config_sha256` も異なるのが正しい——manifest 全体のバイト
    # 一致ではなく、「config セクション以外は一致」+「`normalized_config_sha256`
    # は host 非依存で一致」を検証する。
    manifest_1 = json.loads((out_dir_1 / "assembly_manifest.json").read_text(encoding="utf-8"))
    manifest_2 = json.loads((out_dir_2 / "assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest_1["config"]["config_sha256"] != manifest_2["config"]["config_sha256"]
    assert (
        manifest_1["config"]["normalized_config_sha256"]
        == manifest_2["config"]["normalized_config_sha256"]
    )
    manifest_1_without_config = {k: v for k, v in manifest_1.items() if k != "config"}
    manifest_2_without_config = {k: v for k, v in manifest_2.items() if k != "config"}
    assert manifest_1_without_config == manifest_2_without_config
    assert (
        manifest_1["config"]["config_path"] == manifest_2["config"]["config_path"]
    )
    assert (
        manifest_1["config"]["normalized_config_path"]
        == manifest_2["config"]["normalized_config_path"]
    )
