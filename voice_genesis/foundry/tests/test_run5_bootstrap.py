"""test_run5_bootstrap.py — `scripts/run5_bootstrap.py` ロジック層の検証。

本開発環境には GPU・torch・rclone・runpodctl が無いため、検証対象は
ロジック層のみ（stage 計画・pin 検証・phase config 導出・milestone 検知・
wall-clock 判定・heartbeat 記帳・自動停止コマンド組み立て）。実行系
（render/binarize/train/rclone の subprocess）の初回実測は run 5 本番が
兼ねる（`run5_bootstrap.py` docstring 冒頭の正直会計と対）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run5_bootstrap as r5b  # noqa: E402


# --- material pins（PENDING fail-closed） -----------------------------------


def _write_pins(tmp_path: Path, *, ffmpeg_sha, vocoder_sha, model_ckpt_sha="d" * 64) -> Path:
    pins = {
        "schema": "run5-material-pins/0.1",
        "materials": {
            "ritsu_voicebank_zip": {"url": "https://example/r.zip", "sha256": "a" * 64},
            "ffmpeg_static": {"url": "https://example/f.tar.xz", "sha256": ffmpeg_sha},
            "vocoder_pc_nsf_hifigan": {
                "url": "https://example/v.zip", "sha256": vocoder_sha,
                "model_ckpt_sha256": model_ckpt_sha,
                "placement_dirname": "pc_nsf_hifigan_44.1k_hop512_128bin_2025.02",
            },
            "diffsinger_repo": {"url": "https://example/ds.git", "commit": "e2307b1"},
        },
    }
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(pins, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_material_pins_rejects_pending_entries(tmp_path: Path) -> None:
    """sha256 が null（クロー報告値の未転記 = DESIGN_S4 §3.2 の起動前必須
    先行タスク未完了）の素材が 1 件でもあれば `PinPendingError` で
    fail-closed し、素材名を列挙する。"""
    path = _write_pins(tmp_path, ffmpeg_sha=None, vocoder_sha=None)
    with pytest.raises(r5b.PinPendingError) as exc_info:
        r5b.load_material_pins(path)
    assert exc_info.value.pending == ["ffmpeg_static", "vocoder_pc_nsf_hifigan"]


def test_load_material_pins_rejects_pending_sub_hash_keys(tmp_path: Path) -> None:
    """review セルフレビュー #9: トップレベル `sha256` 以外の `*_sha256`
    キー（`model_ckpt_sha256` 等）が null に戻された退行も、素材取得前の
    preflight で fail-closed する。"""
    path = _write_pins(tmp_path, ffmpeg_sha="b" * 64, vocoder_sha="c" * 64,
                       model_ckpt_sha=None)
    with pytest.raises(r5b.PinPendingError) as exc_info:
        r5b.load_material_pins(path)
    assert exc_info.value.pending == ["vocoder_pc_nsf_hifigan.model_ckpt_sha256"]


def test_load_material_pins_accepts_fully_pinned_table(tmp_path: Path) -> None:
    path = _write_pins(tmp_path, ffmpeg_sha="b" * 64, vocoder_sha="c" * 64)
    materials = r5b.load_material_pins(path)
    assert materials["ffmpeg_static"]["sha256"] == "b" * 64
    # sha256 キーを持たないエントリ（git commit pin）は PENDING 判定の対象外。
    assert materials["diffsinger_repo"]["commit"] == "e2307b1"


def test_committed_material_pins_file_is_fully_pinned() -> None:
    """コミット済みの `run5_material_pins.json` が (1) JSON として読め、
    (2) PENDING ゼロ（= 2026-08-18 の転記完了状態）であること。転記前は
    本テストが PENDING 2 件（ffmpeg_static / vocoder_nsf_hifigan_onnx）を
    期待していた — 期待値をこの「全転記済み」側へ更新したことで、以後
    null へ戻す退行を検出する。"""
    materials = r5b.load_material_pins(r5b.MATERIAL_PINS_PATH)

    ffmpeg = materials["ffmpeg_static"]
    assert ffmpeg["url"].startswith(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2024-09-30-15-36/"
    )
    assert len(ffmpeg["sha256"]) == 64
    assert len(ffmpeg["ffmpeg_bin_sha256"]) == 64
    # 来歴強度の書き分け（User 指示 2026-08-18）: ffmpeg = 当方実測（強）
    assert ffmpeg["provenance"].startswith("強")

    vocoder = materials["vocoder_pc_nsf_hifigan"]
    assert vocoder["url"].startswith("https://github.com/openvpi/vocoders/releases/download/")
    assert len(vocoder["sha256"]) == 64
    assert len(vocoder["model_ckpt_sha256"]) == 64
    # vocoder = クロー報告値（中・間接実証）
    assert vocoder["provenance"].startswith("中")
    assert "checkpoints/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02" in vocoder["placement"]
    # bootstrap の配置名照合（セルフレビュー #6）が参照する機械可読フィールド。
    # DiffSinger e2307b1 configs/acoustic.yaml:15 の既定 vocoder_ckpt ディレクトリ
    # 名と一致していること（一次ソース照合 2026-08-18）。
    assert vocoder["placement_dirname"] == "pc_nsf_hifigan_44.1k_hop512_128bin_2025.02"


def test_check_required_env_lists_missing_vars() -> None:
    assert r5b.check_required_env({}) == list(r5b.REQUIRED_ENV_VARS)
    complete = {name: "x" for name in r5b.REQUIRED_ENV_VARS}
    assert r5b.check_required_env(complete) == []
    partial = dict(complete)
    partial["RUN5_DRIVE_FOLDER_ID"] = ""  # 空文字は「無い」扱い
    assert r5b.check_required_env(partial) == ["RUN5_DRIVE_FOLDER_ID"]


def test_user_sources_url_is_optional_not_required() -> None:
    """2026-08-18 User 裁定（案 A）: user 宅録原本の既定取得経路は成果物
    フォルダ内 `user_sources/` からの rclone 取得であり、
    `RUN5_USER_SOURCES_URL` は代替経路（任意）。必須 env に含めない。"""
    assert "RUN5_USER_SOURCES_URL" not in r5b.REQUIRED_ENV_VARS
    assert r5b.REQUIRED_ENV_VARS == ("RUN5_RCLONE_CONF_B64", "RUN5_DRIVE_FOLDER_ID")


# --- dataset pin 照合 --------------------------------------------------------


def _make_dataset(tmp_path: Path, name: str, wavs: dict) -> Path:
    d = tmp_path / name
    (d / "wavs").mkdir(parents=True)
    (d / "transcriptions.csv").write_bytes(b"name,ph_seq,ph_dur\nx,a,0.5\n")
    for wav_name, content in wavs.items():
        (d / "wavs" / wav_name).write_bytes(content)
    return d


def test_verify_dataset_against_pins_passes_on_exact_match(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA", "b.wav": b"BBB"})
    pin = {
        "transcriptions_csv_sha256": r5b.sha256_file(d / "transcriptions.csv"),
        "wav_sha256": {
            "a.wav": r5b.sha256_file(d / "wavs" / "a.wav"),
            "b.wav": r5b.sha256_file(d / "wavs" / "b.wav"),
        },
    }
    assert r5b.verify_dataset_against_pins(d, pin, "d3") == []


def test_verify_dataset_against_pins_detects_byte_and_set_mismatch(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA"})
    pin = {
        "transcriptions_csv_sha256": "0" * 64,
        "wav_sha256": {"a.wav": "1" * 64, "missing.wav": "2" * 64},
    }
    diffs = r5b.verify_dataset_against_pins(d, pin, "d3")
    assert any("transcriptions.csv" in x for x in diffs)
    assert any("file set mismatch" in x for x in diffs)


def test_verify_dataset_against_pins_checks_exclusions_json_when_pinned(tmp_path: Path) -> None:
    d = _make_dataset(tmp_path, "ds", {"a.wav": b"AAA"})
    (d / "exclusions.json").write_bytes(b"{}")
    pin = {
        "transcriptions_csv_sha256": r5b.sha256_file(d / "transcriptions.csv"),
        "wav_sha256": {"a.wav": r5b.sha256_file(d / "wavs" / "a.wav")},
        "exclusions_json_sha256": r5b.sha256_file(d / "exclusions.json"),
    }
    assert r5b.verify_dataset_against_pins(d, pin, "user") == []
    (d / "exclusions.json").write_bytes(b"{tampered}")
    diffs = r5b.verify_dataset_against_pins(d, pin, "user")
    assert any("exclusions.json" in x for x in diffs)


def test_verify_assembly_against_run4_pins_maps_d3synth_to_d3_section() -> None:
    """4 話者 manifest の d3synth（run 5 の第 4 話者）は run 4 pin 表の `d3`
    セクションと、user は `user` セクションと照合される（DESIGN_S4 §1.1:
    データ内容は run 4 と同一 — 変わるのは帰属ラベルのみ）。"""
    wav_map = {"x.wav": "a" * 64}
    manifest = {
        "speakers": {
            "d3synth": {"transcriptions_csv_sha256": "d" * 64, "wav_sha256": dict(wav_map)},
            "user": {
                "transcriptions_csv_sha256": "u" * 64,
                "wav_sha256": {"y.wav": "b" * 64},
                "exclusions_json_sha256": "e" * 64,
            },
        }
    }
    pins = {
        "d3": {"transcriptions_csv_sha256": "d" * 64, "wav_sha256": dict(wav_map)},
        "user": {
            "transcriptions_csv_sha256": "u" * 64,
            "wav_sha256": {"y.wav": "b" * 64},
            "exclusions_json_sha256": "e" * 64,
        },
    }
    assert r5b.verify_assembly_against_run4_pins(manifest, pins) == []

    pins_tampered = json.loads(json.dumps(pins))
    pins_tampered["d3"]["wav_sha256"]["x.wav"] = "f" * 64
    diffs = r5b.verify_assembly_against_run4_pins(manifest, pins_tampered)
    assert diffs and any("d3synth" in x for x in diffs)


# --- phase config 導出 -------------------------------------------------------


_LIVE_CONFIG = {
    "datasets": [
        {"speaker": "ritsu", "spk_id": 0},
        {"speaker": "pjs", "spk_id": 1},
        {"speaker": "user", "spk_id": 2},
        {"speaker": "d3synth", "spk_id": 3},
    ],
    "num_spk": 4,
    "max_updates": 40000,
    "val_check_interval": 5000,
    "num_ckpt_keep": 10,
}


def test_derive_phase_a_config_is_scratch_5k_with_training_fields() -> None:
    cfg = r5b.derive_phase_config(_LIVE_CONFIG, phase="a")
    assert cfg["finetune_enabled"] is False
    assert cfg["max_updates"] == 5000
    assert "finetune_ckpt_path" not in cfg
    assert cfg["pl_trainer_precision"] == "bf16-mixed"
    assert cfg["optimizer_args"] == {"lr": 0.0002}
    assert cfg["clip_grad_norm"] == 1.0
    # live config 由来のフィールドは不変
    assert cfg["datasets"] == _LIVE_CONFIG["datasets"]
    assert cfg["num_spk"] == 4
    assert cfg["val_check_interval"] == 5000


def test_derive_phase_b_config_refinetunes_from_phase_a_5k() -> None:
    cfg = r5b.derive_phase_config(
        _LIVE_CONFIG, phase="b", finetune_ckpt_path="/ckpt/model_ckpt_steps_5000.ckpt"
    )
    assert cfg["finetune_enabled"] is True
    assert cfg["finetune_ckpt_path"] == "/ckpt/model_ckpt_steps_5000.ckpt"
    assert cfg["max_updates"] == 40000
    assert cfg["pl_trainer_precision"] == "bf16-mixed"


def test_derive_phase_b_without_ckpt_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="finetune_ckpt_path"):
        r5b.derive_phase_config(_LIVE_CONFIG, phase="b")


def test_derive_phase_config_does_not_mutate_live_config() -> None:
    before = json.loads(json.dumps(_LIVE_CONFIG))
    r5b.derive_phase_config(_LIVE_CONFIG, phase="a")
    assert _LIVE_CONFIG == before


def test_derive_phase_config_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        r5b.derive_phase_config(_LIVE_CONFIG, phase="c")


# --- milestone 検知 / wall-clock ---------------------------------------------


def test_parse_ckpt_step_parses_diffsinger_naming() -> None:
    assert r5b.parse_ckpt_step("model_ckpt_steps_5000.ckpt") == 5000
    assert r5b.parse_ckpt_step("model_ckpt_steps_40000.ckpt") == 40000
    assert r5b.parse_ckpt_step("config.yaml") is None
    assert r5b.parse_ckpt_step("model_ckpt_steps_5000.ckpt.tmp") is None


def test_find_milestone_ckpts_only_returns_milestone_steps(tmp_path: Path) -> None:
    for step in (1000, 5000, 15000, 20000):
        (tmp_path / f"model_ckpt_steps_{step}.ckpt").write_bytes(b"x")
    (tmp_path / "config.yaml").write_bytes(b"y")
    found = r5b.find_milestone_ckpts(tmp_path)
    assert sorted(found) == [5000, 20000]


def test_find_milestone_ckpts_on_missing_dir_is_empty(tmp_path: Path) -> None:
    assert r5b.find_milestone_ckpts(tmp_path / "nope") == {}


def test_new_milestones_returns_only_unseen_sorted(tmp_path: Path) -> None:
    current = {5000: tmp_path, 10000: tmp_path, 20000: tmp_path}
    assert r5b.new_milestones([5000], current) == [10000, 20000]
    assert r5b.new_milestones([5000, 10000, 20000], current) == []


def test_remaining_seconds_wall_clock_budget() -> None:
    assert r5b.remaining_seconds(0.0, 3600.0, limit=7200) == 3600.0
    assert r5b.remaining_seconds(0.0, 90000.0) <= 0  # 24h 上限超過
    assert r5b.remaining_seconds(100.0, 100.0) == r5b.WALL_CLOCK_LIMIT_SECONDS


def test_stable_milestone_candidates_requires_size_stability() -> None:
    """review セルフレビュー #2: 出現直後（サイズが前回ポーリングと不一致 =
    書き込み中の可能性）の milestone はスキャン候補にしない。前回と同サイズ
    になって初めて候補になる。"""
    # 初回観測（prev 空）: 候補なし
    assert r5b.stable_milestone_candidates([], {}, {5000: 100}) == []
    # サイズ成長中: まだ候補にしない
    assert r5b.stable_milestone_candidates([], {5000: 100}, {5000: 200}) == []
    # サイズ安定: 候補化
    assert r5b.stable_milestone_candidates([], {5000: 200}, {5000: 200}) == [5000]
    # 処理済み (seen) は再候補にしない
    assert r5b.stable_milestone_candidates([5000], {5000: 200}, {5000: 200}) == []
    # 空ファイル (size 0) は候補にしない
    assert r5b.stable_milestone_candidates([], {10000: 0}, {10000: 0}) == []
    # 複数同時安定はソート順
    assert r5b.stable_milestone_candidates(
        [], {5000: 10, 10000: 20}, {10000: 20, 5000: 10}
    ) == [5000, 10000]


def test_milestone_ckpt_sizes_measures_only_milestones(tmp_path: Path) -> None:
    (tmp_path / "model_ckpt_steps_5000.ckpt").write_bytes(b"abc")
    (tmp_path / "model_ckpt_steps_1234.ckpt").write_bytes(b"x")
    assert r5b.milestone_ckpt_sizes(tmp_path) == {5000: 3}


# --- アーカイブ形式判定（review セルフレビュー #1/#7） ------------------------


def test_detect_archive_format_sniffs_content_not_extension(tmp_path: Path) -> None:
    """拡張子の無い固定名（Drive 直リンク取得の `user_sources_archive`）でも
    magic バイトで zip / tar(gz/xz) を判定できる（旧実装は拡張子分岐のため
    materials 段が構造的に完走不能だった — セルフレビュー #1 の回帰固定）。"""
    import gzip
    import io
    import tarfile as tarfile_mod
    import zipfile as zipfile_mod

    zip_path = tmp_path / "user_sources_archive"  # 拡張子なし
    with zipfile_mod.ZipFile(zip_path, "w") as z:
        z.writestr("a.mp3", b"data")
    assert r5b.detect_archive_format(zip_path) == "zip"

    targz_path = tmp_path / "archive2"
    buf = io.BytesIO()
    with tarfile_mod.open(fileobj=buf, mode="w") as t:
        info = tarfile_mod.TarInfo("b.m4a")
        info.size = 4
        t.addfile(info, io.BytesIO(b"data"))
    targz_path.write_bytes(gzip.compress(buf.getvalue()))
    assert r5b.detect_archive_format(targz_path) == "tar"

    plain_tar = tmp_path / "archive3"
    plain_tar.write_bytes(buf.getvalue())
    assert r5b.detect_archive_format(plain_tar) == "tar"

    junk = tmp_path / "junk"
    junk.write_bytes(b"not an archive at all, definitely")
    assert r5b.detect_archive_format(junk) is None


def test_extract_archive_extracts_extensionless_zip_and_tar(tmp_path: Path) -> None:
    import zipfile as zipfile_mod

    zip_path = tmp_path / "user_sources_archive"
    with zipfile_mod.ZipFile(zip_path, "w") as z:
        z.writestr("inner/a.mp3", b"AAA")
    dest = tmp_path / "out_zip"
    r5b._extract_archive(zip_path, dest)
    assert (dest / "inner" / "a.mp3").read_bytes() == b"AAA"

    junk = tmp_path / "junk"
    junk.write_bytes(b"garbage")
    with pytest.raises(r5b.StageFailure, match="magic-byte"):
        r5b._extract_archive(junk, tmp_path / "out_junk")


# --- salvage 収集（review セルフレビュー #4） ---------------------------------


def test_collect_salvage_artifacts_gathers_from_disk_state(tmp_path: Path) -> None:
    """収集は呼び出し時点のディスク実態基準 — 学習が途中失敗しても、存在する
    phase config / manifest / milestone ckpt / log / TB がすべて拾われる。"""
    run5_raw = tmp_path / "run5_raw"
    run5_raw.mkdir()
    (run5_raw / "assembly_manifest.json").write_bytes(b"{}")
    (run5_raw / "run5_config_phase_a.yaml").write_bytes(b"a")
    # phase B config / training manifest は未生成（phase A 中の失敗を模す）

    ds_repo = tmp_path / "DiffSinger"
    ckpt_dir = ds_repo / "checkpoints" / r5b.EXP_NAME_PHASE_A
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "model_ckpt_steps_5000.ckpt").write_bytes(b"ckpt")
    (ckpt_dir / "model_ckpt_steps_1111.ckpt").write_bytes(b"not-milestone")
    (ckpt_dir / "config.yaml").write_bytes(b"cfg")
    (ckpt_dir / "train.log").write_bytes(b"log")
    tb_dir = ckpt_dir / "lightning_logs" / "version_0"
    tb_dir.mkdir(parents=True)
    (tb_dir / "events.out.tfevents.123").write_bytes(b"tb")

    artifacts = r5b.collect_salvage_artifacts(run5_raw, ds_repo)
    names = [p.name for p, _dest in artifacts]
    assert "assembly_manifest.json" in names
    assert "run5_config_phase_a.yaml" in names
    assert "model_ckpt_steps_5000.ckpt" in names
    assert "model_ckpt_steps_1111.ckpt" not in names  # 節目以外は対象外
    assert "config.yaml" in names
    assert "train.log" in names
    assert "events.out.tfevents.123" in names
    # 未生成物はスキップ（存在するものだけ・重複なし）
    assert "run5_config_phase_b.yaml" not in names
    assert len(artifacts) == len(set(artifacts))
    # dest は namespace 規則（run 単位 = 直下 / phase 別 = phase_a 配下）
    dests = {p.name: dest for p, dest in artifacts}
    assert dests["assembly_manifest.json"] == ""
    assert dests["run5_config_phase_a.yaml"] == "phase_a/config"
    assert dests["model_ckpt_steps_5000.ckpt"] == "phase_a/checkpoints"
    assert dests["config.yaml"] == "phase_a/config"
    assert dests["train.log"] == "phase_a/logs"
    assert dests["events.out.tfevents.123"] == "phase_a/logs"


def test_collect_salvage_artifacts_empty_when_nothing_exists(tmp_path: Path) -> None:
    assert r5b.collect_salvage_artifacts(tmp_path / "nope", tmp_path / "nope2") == []


def test_salvage_namespaces_same_basename_across_phases(tmp_path: Path) -> None:
    """2026-08-19 外部レビュー P1 の回帰: phase A/B に同じ basename
    （model_ckpt_steps_5000.ckpt / config.yaml / train.log）が存在しても、
    両方が独立した dest で保存される（run 5 実走の同名後勝ち上書き =
    s4_record §5.6 の根治。手動 copyto 保全を不要にする）。"""
    run5_raw = tmp_path / "run5_raw"
    run5_raw.mkdir()
    ds_repo = tmp_path / "DiffSinger"
    for exp_name in (r5b.EXP_NAME_PHASE_A, r5b.EXP_NAME_PHASE_B):
        ckpt_dir = ds_repo / "checkpoints" / exp_name
        ckpt_dir.mkdir(parents=True)
        (ckpt_dir / "model_ckpt_steps_5000.ckpt").write_bytes(b"ckpt-" + exp_name.encode())
        (ckpt_dir / "config.yaml").write_bytes(b"cfg-" + exp_name.encode())
        (ckpt_dir / "train.log").write_bytes(b"log-" + exp_name.encode())

    artifacts = r5b.collect_salvage_artifacts(run5_raw, ds_repo)
    by_basename: dict = {}
    for path, dest in artifacts:
        by_basename.setdefault(path.name, []).append(dest)
    assert sorted(by_basename["model_ckpt_steps_5000.ckpt"]) == [
        "phase_a/checkpoints", "phase_b/checkpoints"]
    assert sorted(by_basename["config.yaml"]) == ["phase_a/config", "phase_b/config"]
    assert sorted(by_basename["train.log"]) == ["phase_a/logs", "phase_b/logs"]
    # (path, dest) 単位で全件ユニーク = 上書き衝突ゼロ
    assert len(artifacts) == len(set(artifacts))


def test_rclone_argv_copies_into_namespace_dest(tmp_path: Path) -> None:
    """dest 指定時はリモート側サブディレクトリへ、無指定はフォルダ直下へ。"""
    argv = r5b._rclone_argv(tmp_path / "rc.conf", "FOLDER", tmp_path / "x.ckpt",
                            "phase_b/checkpoints")
    assert "run5drive:phase_b/checkpoints" in argv
    argv_root = r5b._rclone_argv(tmp_path / "rc.conf", "FOLDER", tmp_path / "x.json")
    assert "run5drive:" in argv_root
    assert "--drive-root-folder-id" in argv_root


# --- user 原本の sha256 索引（ファイル名非依存の特定） -------------------------


def test_index_files_by_sha256_is_filename_independent(tmp_path: Path) -> None:
    """台帳 `source_filename`（intake 正規化名）と Drive 表示名（日本語日付名 +
    「〜 のコピー」）は一致しないため、原本の特定は中身の sha256 で行う。
    リネーム不要・重複コピー耐性の両方を固定する。"""
    import hashlib

    root = tmp_path / "src"
    (root / "nested").mkdir(parents=True)
    (root / "8月17日（午前0-18）.m4a のコピー").write_bytes(b"CONTENT-A")
    (root / "nested" / "適当な別名.m4a").write_bytes(b"CONTENT-B")
    # 同内容の重複コピー（Drive で 2 回コピーした状況）
    (root / "8月17日（午前0-18）.m4a のコピー(1)").write_bytes(b"CONTENT-A")

    index = r5b.index_files_by_sha256(root)

    sha_a = hashlib.sha256(b"CONTENT-A").hexdigest()
    sha_b = hashlib.sha256(b"CONTENT-B").hexdigest()
    assert set(index) == {sha_a, sha_b}
    # 重複は全パス保持（余剰カウントを正確にするため）。使うのは先頭（等価）。
    assert len(index[sha_a]) == 2
    assert index[sha_a][0].read_bytes() == b"CONTENT-A"
    assert [p.name for p in index[sha_b]] == ["適当な別名.m4a"]


def test_match_user_sources_resolves_by_content_and_counts_extras() -> None:
    """review PR#270 セルフレビュー #3: 余剰は distinct sha 数ではなく実ファイル
    数で数える（重複コピーの過小報告を防ぐ）。card_id → path の解決も確認。"""
    entries = [
        {"card_id": "UC-001", "source_filename": "UC-001_x.mp3", "source_sha256": "a" * 64},
        {"card_id": "UC-002", "source_filename": "UC-002_y.m4a", "source_sha256": "b" * 64},
    ]
    p_needed = Path("/drive/UC1 のコピー.m4a")
    p_needed2 = Path("/drive/UC1 のコピー(1).m4a")  # 同 sha の重複
    p_needed_b = Path("/drive/UC2.m4a")
    p_junk1 = Path("/drive/junk.m4a")
    p_junk2 = Path("/drive/junk のコピー.m4a")  # 同 sha の無関係重複 3 本
    p_junk3 = Path("/drive/junk のコピー(1).m4a")
    files_by_sha = {
        "a" * 64: [p_needed, p_needed2],
        "b" * 64: [p_needed_b],
        "c" * 64: [p_junk1, p_junk2, p_junk3],
    }

    source_paths, diffs, extras = r5b.match_user_sources(files_by_sha, entries)

    assert diffs == []
    assert source_paths == {"UC-001": p_needed, "UC-002": p_needed_b}
    # 無関係 sha の実ファイルは 3 本 — distinct sha 数(1)ではなく 3 と数える
    assert extras == 3


def test_match_user_sources_reports_missing_with_diagnostic() -> None:
    """該当 sha のファイルが無いエントリは fail-closed 用の diff に載り、
    メッセージに『未コピー or 中身相違』の切り分けを含む（sha-only 照合で
    診断信号が痩せる問題への言葉の補い — セルフレビュー #4）。"""
    entries = [
        {"card_id": "UC-001", "source_filename": "UC-001_x.mp3", "source_sha256": "a" * 64},
    ]
    source_paths, diffs, extras = r5b.match_user_sources({}, entries)
    assert source_paths == {}
    assert len(diffs) == 1
    assert "UC-001_x.mp3" in diffs[0]
    assert "未コピー" in diffs[0] or "中身" in diffs[0]


def test_guard_user_sources_size_passes_normal_and_blocks_runaway(tmp_path: Path) -> None:
    """review PR#270 セルフレビュー #1: 台帳規模（~2 MiB・17 本）は通し、
    ファイル数がランナウェイ上限を超えたら hash 前に fail-closed する。"""
    normal = tmp_path / "normal"
    normal.mkdir()
    for i in range(17):
        (normal / f"f{i}.m4a").write_bytes(b"x" * 1000)
    count, total = r5b.guard_user_sources_size(normal)
    assert count == 17 and total == 17000

    runaway = tmp_path / "runaway"
    runaway.mkdir()
    for i in range(r5b.USER_SOURCES_MAX_FILES + 1):
        (runaway / f"f{i}.bin").write_bytes(b"x")
    with pytest.raises(r5b.StageFailure, match="runaway guard"):
        r5b.guard_user_sources_size(runaway)


# --- stage 計画 / heartbeat / self-stop --------------------------------------


def test_stage_plan_matches_design_s4_ordering() -> None:
    """DESIGN_S4 §3.1 の段階（ゲート → 素材照合 → 再生成 → pin 照合 →
    学習 → 退避 → 自動停止）の順序が保存されていること。"""
    plan = r5b.build_stage_plan()
    assert plan == (
        "preflight", "gates", "materials", "datasets", "assemble",
        "binarize", "train_phase_a", "train_phase_b", "salvage", "self_stop",
    )
    assert plan.index("gates") < plan.index("materials") < plan.index("datasets")
    assert plan.index("assemble") < plan.index("binarize") < plan.index("train_phase_a")
    assert plan.index("train_phase_b") < plan.index("salvage") < plan.index("self_stop")


def test_heartbeat_marks_stage_and_pushes_marker(tmp_path: Path) -> None:
    pushed = []
    hb = r5b.Heartbeat(tmp_path / "hb", pushed.append)
    marker = hb.mark("gates", "ok", detail="4/4 passed")
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["stage"] == "gates"
    assert data["status"] == "ok"
    assert data["detail"] == "4/4 passed"
    assert data["utc"].endswith("Z")
    assert pushed == [marker]


def test_self_stop_command_stops_not_removes() -> None:
    """DESIGN_S4 §3.3 裁定 (c): 停止のみで Pod ディスクは保険として残置する
    — remove ではなく stop であること。"""
    argv = r5b.self_stop_command("abc123")
    assert argv == ["runpodctl", "stop", "pod", "abc123"]
    assert "remove" not in argv


def test_training_fields_match_runbook_section4_values() -> None:
    """runbook §4 の 4 項目のうち自動付与分（bf16-mixed / lr 0.0002 /
    clip 1.0）が凍結値のまま保たれていること（finetune 系 2 キーは phase
    導出側が付与する）。"""
    assert r5b.TRAINING_FIELDS == {
        "pl_trainer_precision": "bf16-mixed",
        "optimizer_args": {"lr": 0.0002},
        "clip_grad_norm": 1.0,
    }
    assert r5b.PHASE_A_MAX_UPDATES == 5000
    assert r5b.PHASE_B_MAX_UPDATES == 40000
    assert r5b.WALL_CLOCK_LIMIT_SECONDS == 24 * 3600


def test_plan_cli_prints_stages_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert r5b.main(["--plan"]) == 0
    out = capsys.readouterr().out
    for stage in r5b.build_stage_plan():
        assert stage in out


def test_main_without_env_fails_closed_before_any_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """必須環境変数が無ければ素材取得どころか pin 読みにも進まず exit 1。"""
    for name in r5b.REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert r5b.main(["--work-dir", str(tmp_path / "w")]) == 1
    err = capsys.readouterr().err
    assert "missing required env var" in err


def test_main_with_env_but_pending_pins_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """env が揃っていても pin 表に PENDING が残っていれば preflight で
    fail-closed する（起動前必須の先行タスクの実行時表現。コミット済み
    pin 表は転記完了済みのため、PENDING 状態は一時 pin 表で再現する）。"""
    for name in r5b.REQUIRED_ENV_VARS:
        monkeypatch.setenv(name, "dummy")
    pending_pins = _write_pins(tmp_path, ffmpeg_sha=None, vocoder_sha=None)
    monkeypatch.setattr(r5b, "MATERIAL_PINS_PATH", pending_pins)
    assert r5b.main(["--work-dir", str(tmp_path / "w")]) == 1
    err = capsys.readouterr().err
    assert "PENDING" in err


# --- ffmpeg libavformat 版パース（run 5 初回起動の fail-closed 原因） ---------

# 実測 fixture（2026-08-18・BtbN n6.1.2 static を展開して `ffmpeg -version` を
# 実行した出力から、**banner 行と lib 行のみを逐語で抜粋**したもの。長大な
# `configuration:` 行（2KB 級）と `built with` 行は本テストの対象外につき省略）。
# ffmpeg は `%2d.%3d.%3d` で桁揃えするため 16 が " 16"、単桁 major は " 9" になる。
_REAL_FFMPEG_VERSION_OUTPUT = """ffmpeg version n6.1.2-8-gf00f71f590-20240930 Copyright (c) 2000-2024 the FFmpeg developers
libavutil      58. 29.100 / 58. 29.100
libavcodec     60. 31.102 / 60. 31.102
libavformat    60. 16.100 / 60. 16.100
libavdevice    60.  3.100 / 60.  3.100
libavfilter     9. 12.100 /  9. 12.100
libswscale      7.  5.100 /  7.  5.100
libswresample   4. 12.100 /  4. 12.100
libpostproc    57.  3.100 / 57.  3.100
"""


def test_parse_libavformat_version_on_real_ffmpeg_output() -> None:
    """run 5 初回起動はここで fail-closed した: 実出力は `libavformat    60. 16.100`
    と**ドットの後に空白が入る**ため、素朴な `"60.16.100" in out` は構造的に
    成立しない（pin もバイナリも正しかった）。実測 fixture で回帰を固定する。"""
    assert r5b.parse_libavformat_version(_REAL_FFMPEG_VERSION_OUTPUT) == (60, 16, 100)
    assert r5b.parse_libavformat_version(_REAL_FFMPEG_VERSION_OUTPUT) == r5b.FFMPEG_LIBAVFORMAT_PIN
    # 旧実装が使っていた素朴な部分文字列は実出力に**存在しない**ことも固定する
    # （この事実こそが初回停止の原因 — 再導入を防ぐ）。
    assert "60.16.100" not in _REAL_FFMPEG_VERSION_OUTPUT


def test_parse_libavformat_version_handles_unpadded_and_missing() -> None:
    # 桁揃えが不要な値（空白なし）でも同じ結果になる
    assert r5b.parse_libavformat_version("  libavformat    60.116.100 / 60.116.100\n") == (60, 116, 100)
    # 別バージョンは pin と不一致として検出できる（7.x 系）
    other = r5b.parse_libavformat_version("  libavformat    61. 7.100 / 61. 7.100\n")
    assert other == (61, 7, 100)
    assert other != r5b.FFMPEG_LIBAVFORMAT_PIN
    # 該当行が無ければ None（= pin 不一致として fail-closed される）
    assert r5b.parse_libavformat_version("ffmpeg version 4.4.2\n libavcodec 58.\n") is None
    assert r5b.parse_libavformat_version("") is None


def test_parse_libavformat_version_tolerates_single_digit_major_padding() -> None:
    """`%2d` により単桁 major は " 9" と空白詰めされる（実測 fixture の
    libavfilter 行が実例）。libavformat 以外の行に引っ張られないことも確認。"""
    assert r5b.parse_libavformat_version("libavfilter     9. 12.100 /  9. 12.100\n") is None
    assert r5b.parse_libavformat_version(
        "libavformat     9. 12.100 /  9. 12.100\n"
    ) == (9, 12, 100)


def test_summarize_ffmpeg_version_keeps_diagnosis_drops_configuration() -> None:
    """review PR#271 セルフレビュー #1: 版不一致で無人 Pod が停止したとき、
    heartbeat に「何が出ていたか」を残す。banner + lib 行は残し、2KB 級の
    `configuration:` 行は落とす。"""
    raw = (
        "ffmpeg version 4.4.2-0ubuntu0 Copyright (c) 2000-2021\n"
        "  built with gcc 11\n"
        "  configuration: " + "--enable-x " * 500 + "\n"
        "  libavformat    58. 76.100 / 58. 76.100\n"
    )
    out = r5b.summarize_ffmpeg_version(raw)
    assert "ffmpeg version 4.4.2" in out
    assert "libavformat    58. 76.100" in out
    assert "--enable-x" not in out
    assert len(out) <= 800


# --- コマンド出力ログ / 失敗時の証跡（2026-08-18 gdown 停止の教訓） ---------


def test_sanitize_label_makes_safe_log_filenames() -> None:
    assert r5b.sanitize_label("datasets/convert-d3") == "datasets_convert-d3"
    assert r5b.sanitize_label("materials/pjs-zip") == "materials_pjs-zip"
    assert r5b.sanitize_label("") == "run"
    assert "/" not in r5b.sanitize_label("a/b/c")


def test_tail_text_keeps_end_and_marks_truncation() -> None:
    assert r5b.tail_text("short") == "short"
    long = "x" * 100 + "TAIL_MARKER"
    out = r5b.tail_text(long, max_chars=20)
    assert out.endswith("TAIL_MARKER")
    assert "省略" in out
    assert len(out) <= 20 + 20  # 印の分だけ超える


def test_run_failure_includes_command_output_tail(tmp_path: Path) -> None:
    """外部コマンドが失敗したら、終了コードだけでなく**出力の末尾**を
    StageFailure に載せる（旧実装はコマンド行と exit code のみで、gdown の
    Drive Quota exceeded を Pod 外の再現調査でしか特定できなかった）。"""
    r5b.set_log_dir(tmp_path / "cmdlogs")
    try:
        with pytest.raises(r5b.StageFailure) as exc_info:
            r5b._run(
                [sys.executable, "-c",
                 "import sys; print('DIAGNOSTIC_NEEDLE'); sys.exit(3)"],
                label="test/failing",
            )
        msg = str(exc_info.value)
        assert "exit 3" in msg
        assert "DIAGNOSTIC_NEEDLE" in msg
        # ログ本体も残る（salvage で Drive へ退避される）
        log = tmp_path / "cmdlogs" / "test_failing.log"
        assert log.exists()
        assert "DIAGNOSTIC_NEEDLE" in log.read_text(encoding="utf-8")
    finally:
        r5b._LOG_DIR = None


def test_run_success_writes_log_without_raising(tmp_path: Path) -> None:
    r5b.set_log_dir(tmp_path / "cmdlogs")
    try:
        r5b._run([sys.executable, "-c", "print('OK_LINE')"], label="test/ok")
        log = tmp_path / "cmdlogs" / "test_ok.log"
        assert "OK_LINE" in log.read_text(encoding="utf-8")
    finally:
        r5b._LOG_DIR = None


def test_collect_salvage_artifacts_includes_command_logs(tmp_path: Path) -> None:
    """コマンドログは失敗診断の一次証跡なので salvage 対象に含める。"""
    r5b.set_log_dir(tmp_path / "cmdlogs")
    try:
        (tmp_path / "cmdlogs" / "materials_pjs-zip.log").write_text("boom", encoding="utf-8")
        run5_raw = tmp_path / "run5_raw"
        run5_raw.mkdir()
        (run5_raw / "assembly_manifest.json").write_bytes(b"{}")
        artifacts = r5b.collect_salvage_artifacts(run5_raw, tmp_path / "DiffSinger")
        assert any(p.name == "materials_pjs-zip.log" and dest == "cmdlogs"
                   for p, dest in artifacts)
    finally:
        r5b._LOG_DIR = None


def test_pjs_pin_uses_drive_file_id_not_gdown() -> None:
    """PJS は認証済み Drive API（rclone backend copyid）で取得する — 匿名 DL の
    per-file 上限（Quota exceeded）で無人走行が止まる経路を撤去した記録。"""
    materials = r5b.load_material_pins(r5b.MATERIAL_PINS_PATH)
    pjs = materials["pjs_corpus_zip"]
    assert "gdown_id" not in pjs
    assert pjs["drive_file_id"] == "1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_"
    assert pjs["size_bytes"] == 275179158
    assert "Quota exceeded" in pjs["fetch_note"]


# --- PR#272 セルフレビュー対応の回帰 ------------------------------------------


def test_read_tail_reads_only_the_end_of_large_log(tmp_path: Path) -> None:
    """review #2: 成功時も全文を読み込んでいた（progress 出力でログは巨大に
    なり得る）。末尾のみをシークして読むこと・切り詰め印が付くことを固定。"""
    log = tmp_path / "big.log"
    log.write_text("A" * 200_000 + "END_MARKER", encoding="utf-8")
    out = r5b.read_tail(log)
    assert out.endswith("END_MARKER")
    assert len(out) < 2_000
    assert "省略" in out
    # 読めないパスでも例外を投げない（診断の付随処理で二次障害を起こさない）
    assert r5b.read_tail(tmp_path / "missing.log") == ""


def test_collect_salvage_artifacts_puts_command_logs_last(tmp_path: Path) -> None:
    """review #4: 予算/wall-clock 枯渇時に先に押し出すべきは checkpoint。
    コマンドログは末尾に積む。"""
    r5b.set_log_dir(tmp_path / "cmdlogs")
    try:
        for i in range(5):
            (tmp_path / "cmdlogs" / f"stage{i}.log").write_text("x", encoding="utf-8")
        run5_raw = tmp_path / "run5_raw"
        run5_raw.mkdir()
        (run5_raw / "assembly_manifest.json").write_bytes(b"{}")
        ckpt_dir = tmp_path / "DiffSinger" / "checkpoints" / r5b.EXP_NAME_PHASE_B
        ckpt_dir.mkdir(parents=True)
        (ckpt_dir / "model_ckpt_steps_40000.ckpt").write_bytes(b"ckpt")

        artifacts = r5b.collect_salvage_artifacts(run5_raw, tmp_path / "DiffSinger")
        names = [p.name for p, _dest in artifacts]
        ckpt_idx = names.index("model_ckpt_steps_40000.ckpt")
        first_log_idx = min(i for i, n in enumerate(names) if n.endswith(".log"))
        assert ckpt_idx < first_log_idx, f"checkpoint はログより先に push する: {names}"
    finally:
        r5b._LOG_DIR = None


# --- Drive の同名重複対策（ID 指定 fetch・2026-08-18 run 5 三度目の停止） -----


_LSJSON_WITH_DUPLICATE_NAMES = json.dumps([
    {"ID": "1bbb", "Name": "8月17日（午前0-29）.m4a のコピー", "Size": 100},
    {"ID": "1aaa", "Name": "8月17日（午前0-29）.m4a のコピー", "Size": 101},
    {"ID": "1ccc", "Name": "8月17日（午前0-18）.m4a のコピー", "Size": 102},
])


def test_plan_drive_id_fetch_gives_unique_local_names_for_duplicate_drive_names() -> None:
    """Drive は同一フォルダに同名ファイルを複数持てる。名前ベースの
    `rclone copy` は 2 本目以降を "Duplicate object found in source - ignoring"
    で黙って落とし、run 5 三度目の起動は 17 本中 10 本しか届かず fail-closed
    した。ID 指定なら全件に一意なローカル名が割り当たることを固定する。"""
    plan = r5b.plan_drive_id_fetch(_LSJSON_WITH_DUPLICATE_NAMES)
    assert len(plan) == 3
    ids = [file_id for file_id, _ in plan]
    names = [local for _, local in plan]
    assert ids == ["1aaa", "1bbb", "1ccc"]  # ID 昇順で決定論
    assert len(set(names)) == 3, f"ローカル名が衝突している: {names}"
    assert all(n.startswith(f"{i:03d}_") for i, n in enumerate(names))


def test_plan_drive_id_fetch_sanitizes_and_handles_edge_names() -> None:
    listing = json.dumps([
        {"ID": "z", "Name": "a/b\\c:*?.m4a"},
        {"ID": "y", "Name": ""},
        {"ID": "x", "Name": "x" * 200 + "_TAILNAME.m4a"},
    ])
    plan = r5b.plan_drive_id_fetch(listing)
    names = [n for _, n in plan]
    assert all("/" not in n and "\\" not in n for n in names)
    assert any(n.endswith("file") for n in names)      # 空名のフォールバック
    assert all(len(n) <= 4 + 60 for n in names)        # 連番 + 60 字上限
    assert any("TAILNAME" in n for n in names)         # 末尾側を残す


def test_plan_drive_id_fetch_empty_listing() -> None:
    assert r5b.plan_drive_id_fetch("[]") == []


# --- PR#274 セルフレビュー対応の回帰 ------------------------------------------


def test_plan_drive_id_fetch_rejects_entries_without_id() -> None:
    """review #5: drive backend の lsjson は ID を出す前提 — 欠落は素の
    KeyError ではなく、当該エントリを名指しする StageFailure で fail-closed。"""
    listing = json.dumps([
        {"ID": "ok1", "Name": "a.m4a"},
        {"Name": "no-id.m4a"},
    ])
    with pytest.raises(r5b.StageFailure, match="without ID"):
        r5b.plan_drive_id_fetch(listing)


def test_run_capture_returns_stdout_logs_and_attaches_tail_on_failure(
    tmp_path: Path,
) -> None:
    """review #2: 出力を parse に使う外部コマンド（lsjson）も cmdlog + 失敗時
    tail 同梱の規約に乗せる（素の subprocess.run では gdown 事件で塞いだ
    「証跡ゼロの停止」を再現してしまう）。"""
    r5b.set_log_dir(tmp_path / "cmdlogs")
    try:
        out = r5b._run_capture(
            [sys.executable, "-c", "print('CAPTURED_STDOUT')"],
            label="test/capture-ok",
        )
        assert "CAPTURED_STDOUT" in out
        log = tmp_path / "cmdlogs" / "test_capture-ok.log"
        assert "CAPTURED_STDOUT" in log.read_text(encoding="utf-8")

        with pytest.raises(r5b.StageFailure) as exc_info:
            r5b._run_capture(
                [sys.executable, "-c",
                 "import sys; print('ERR_NEEDLE', file=sys.stderr); sys.exit(4)"],
                label="test/capture-fail",
            )
        assert "exit 4" in str(exc_info.value)
        assert "ERR_NEEDLE" in str(exc_info.value)
        fail_log = tmp_path / "cmdlogs" / "test_capture-fail.log"
        assert "ERR_NEEDLE" in fail_log.read_text(encoding="utf-8")
    finally:
        r5b._LOG_DIR = None


# --- 2026-08-19 外部レビュー対応（実行 manifest / lock 同期 / stage 記帳） ---


def test_new_execution_manifest_has_review_required_fields() -> None:
    """外部レビュー P2 の必須フィールドが初期形で全て存在すること。"""
    m = r5b.new_execution_manifest(
        pod_id="pod1", repo_commit="abc", container_image="img", gpu="RTX 3090")
    for key in (
        "schema_version", "run_id", "pod_id", "repo_commit", "container_image",
        "gpu", "start_time", "end_time", "stage_status", "environment_versions",
        "material_hashes", "dataset_hashes", "checkpoint_hashes",
        "tensorboard_hashes", "artifact_count", "salvage_status",
        "self_stop_status", "failure_history",
    ):
        assert key in m, key
    assert m["schema_version"] == "run-execution-manifest/0.1"
    assert m["failure_history"] == []
    assert m["start_time"].endswith("Z")


def test_compare_execution_manifests_detects_environment_drift() -> None:
    """preflight の前回比較: 環境・入力キーの差分だけを報告し、走行毎に変わる
    キー（stage_status 等）は比較しない。"""
    prev = r5b.new_execution_manifest(
        pod_id="p1", repo_commit="abc", container_image="img", gpu="g")
    curr = r5b.new_execution_manifest(
        pod_id="p2", repo_commit="abc", container_image="img", gpu="g")
    curr["stage_status"]["gates"] = {"status": "ok"}  # 比較対象外
    assert r5b.compare_execution_manifests(prev, curr) == []
    # 前回側は走行完了時の最終形（実測版が追記済み）、今回側は preflight の
    # 初期形（python のみ）— 共有キーが一致する限り差分にしない（セルフ
    # レビュー #1: 等値比較だと構造的に毎回 environment_versions が差分になり
    # 「match with previous run」が死文化する）。
    prev["environment_versions"]["binarize_numeric_stack"] = "numpy 1.26.4"
    assert r5b.compare_execution_manifests(prev, curr) == []
    # 共有キー（python）の実差分は検出する
    prev["environment_versions"]["python"] = "0.0.0"
    assert r5b.compare_execution_manifests(prev, curr) == ["environment_versions"]
    prev["environment_versions"]["python"] = curr["environment_versions"]["python"]
    curr["repo_commit"] = "def"
    curr["material_hashes"] = {"ffmpeg": {"sha256": "x"}}
    diffs = r5b.compare_execution_manifests(prev, curr)
    assert "repo_commit" in diffs and "material_hashes" in diffs
    assert "stage_status" not in diffs
    assert "environment_versions" not in diffs


def test_write_execution_manifest_lands_in_run5_raw_and_is_salvaged(
    tmp_path: Path,
) -> None:
    """manifest は run5_raw に書かれ、salvage 収集にフォルダ直下 dest で載る。"""
    run5_raw = tmp_path / "run5_raw"
    m = r5b.new_execution_manifest(
        pod_id="p", repo_commit="c", container_image=None, gpu=None)
    path = r5b.write_execution_manifest(m, run5_raw)
    assert path == run5_raw / "run_execution_manifest.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "run-execution-manifest/0.1"
    artifacts = r5b.collect_salvage_artifacts(run5_raw, tmp_path / "DiffSinger")
    assert (path, "") in artifacts


def test_heartbeat_records_stage_status_into_manifest_record(tmp_path: Path) -> None:
    """Heartbeat.mark は実行 manifest の stage_status へ同時記帳する（detail は
    traceback 肥大を避けて切り詰め）。"""
    record: dict = {}
    hb = r5b.Heartbeat(tmp_path / "hb", lambda *_a, **_k: None, record=record)
    hb.mark("gates", "ok")
    hb.mark("failure", "failed", detail="X" * 5000)
    assert record["gates"]["status"] == "ok"
    assert record["gates"]["utc"].endswith("Z")
    assert record["failure"]["status"] == "failed"
    assert len(record["failure"]["detail"]) < 1000


def test_summarize_material_hashes_keeps_only_sha256_keys() -> None:
    materials = {
        "ffmpeg": {"url": "http://x", "sha256": "aa", "bin_sha256": "bb",
                   "size_bytes": 1},
    }
    summary = r5b.summarize_material_hashes(materials)
    assert summary == {"ffmpeg": {"sha256": "aa", "bin_sha256": "bb"}}


def test_lock_file_render_pins_match_runtime_gate() -> None:
    """外部レビュー P3: lock + runtime gate の二重保証 — lock ファイルの
    確定 pin 行に NUMERIC_STACK_PIN が全て含まれること（drift 検出）。
    部分集合検査にするのは、run 6 の freeze 捕獲後に lock へ追加 pin
    （praat-parselmouth 等）が入る計画のため（等値だと lock 完全化 PR が
    このテスト自身と矛盾する — セルフレビュー #6）。"""
    lock_path = (
        Path(r5b.__file__).resolve().parent / "requirements_run5_pod.lock")
    pinned = {
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert set(r5b.NUMERIC_STACK_PIN) <= pinned
    # f0 解析系 pin（numba/librosa — 2026-08-19 の SIGSEGV 実測で確定）も
    # lock と同期していること
    assert set(r5b.ANALYSIS_STACK_PIN) <= pinned
    # 逆方向の drift（lock 側の数値スタック 4 パッケージだけ別版で残る）も検出
    gate_pkgs = {p.split("==")[0]: p
                 for p in (*r5b.NUMERIC_STACK_PIN, *r5b.ANALYSIS_STACK_PIN)}
    for line in pinned:
        pkg = line.split("==")[0]
        if pkg in gate_pkgs:
            assert line == gate_pkgs[pkg], (
                f"lock と NUMERIC_STACK_PIN の版が食い違う: {line}")


def test_phase_remote_dirs_cover_both_phases_and_are_distinct() -> None:
    """P1 回帰の土台: 両 phase に namespace が定義され、互いに異なること。"""
    dirs = r5b.PHASE_REMOTE_DIRS
    assert set(dirs) == {r5b.EXP_NAME_PHASE_A, r5b.EXP_NAME_PHASE_B}
    assert dirs[r5b.EXP_NAME_PHASE_A] != dirs[r5b.EXP_NAME_PHASE_B]


# --- run 6 対応（DESIGN_S5_run6.md §2: プロファイル・prefix・正規化 pin・salvage (c)） ---


def test_apply_run_profile_run6_switches_constants_and_run5_restores() -> None:
    try:
        profile = r5b.apply_run_profile("run6")
        assert r5b.RUN_ID == "s5_run6"
        assert r5b.EXP_NAME_PHASE_A == "s5_run6_acoustic_scratch"
        assert r5b.EXP_NAME_PHASE_B == "s5_run6_acoustic_v1"
        assert set(r5b.PHASE_REMOTE_DIRS) == {
            "s5_run6_acoustic_scratch", "s5_run6_acoustic_v1"}
        assert r5b.DATASET_PINS_PATH.name == "run6_dataset_pins.json"
        assert r5b.REMOTE_PREFIX == "run6"
        assert profile["remote_prefix"] == "run6"
    finally:
        r5b.apply_run_profile("run5")
    # run5 プロファイル = 従来値そのもの（無指定の挙動が run 5 実走と同一）
    assert r5b.RUN_ID == "s4_run5"
    assert r5b.EXP_NAME_PHASE_A == "s4_run5_acoustic_scratch"
    assert r5b.DATASET_PINS_PATH.name == "run4_dataset_pins.json"
    assert r5b.REMOTE_PREFIX == ""


def test_apply_run_profile_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown RUN_PROFILE"):
        r5b.apply_run_profile("run99")


def test_rclone_argv_composes_remote_prefix(tmp_path: Path) -> None:
    """run 6 では全 push が run6/ prefix 配下（run 5 成果物と混ざらない）。"""
    try:
        r5b.apply_run_profile("run6")
        argv = r5b._rclone_argv(tmp_path / "rc.conf", "FOLDER", tmp_path / "x.ckpt",
                                "phase_b/checkpoints")
        assert "run5drive:run6/phase_b/checkpoints" in argv
        argv_root = r5b._rclone_argv(tmp_path / "rc.conf", "FOLDER", tmp_path / "hb.json")
        assert "run5drive:run6" in argv_root
    finally:
        r5b.apply_run_profile("run5")
    argv_run5 = r5b._rclone_argv(tmp_path / "rc.conf", "FOLDER", tmp_path / "x.ckpt",
                                 "phase_a/checkpoints")
    assert "run5drive:phase_a/checkpoints" in argv_run5


def test_verify_dataset_checks_loudness_report_pin(tmp_path: Path) -> None:
    """run 6 pin に loudness_normalization_json_sha256 がある場合、会計ファイル
    の欠落・不一致を fail-closed で検出する。"""
    ds = tmp_path / "ds"
    (ds / "wavs").mkdir(parents=True)
    (ds / "transcriptions.csv").write_bytes(b"csv")
    (ds / "wavs" / "UC-001.wav").write_bytes(b"wav")
    import hashlib

    def sha(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    pin = {
        "transcriptions_csv_sha256": sha(b"csv"),
        "wav_sha256": {"UC-001.wav": sha(b"wav")},
        "loudness_normalization_json_sha256": sha(b"report"),
    }
    diffs = r5b.verify_dataset_against_pins(ds, pin, "user")
    assert any("loudness_normalization.json: missing" in d for d in diffs)
    (ds / "loudness_normalization.json").write_bytes(b"report")
    assert r5b.verify_dataset_against_pins(ds, pin, "user") == []
    (ds / "loudness_normalization.json").write_bytes(b"tampered")
    diffs = r5b.verify_dataset_against_pins(ds, pin, "user")
    assert any("loudness_normalization.json:" in d and "!= pin" in d for d in diffs)


def test_salvage_collects_export_required_maps_and_dictionary(tmp_path: Path) -> None:
    """salvage 追加 (c): spk_map.json / lang_map.json / dictionary-*.txt が
    phase config 配下へ収集される（run 5 の export 復旧を再発させない）。"""
    run5_raw = tmp_path / "run5_raw"
    run5_raw.mkdir()
    ckpt_dir = tmp_path / "DiffSinger" / "checkpoints" / r5b.EXP_NAME_PHASE_B
    ckpt_dir.mkdir(parents=True)
    for name in ("spk_map.json", "lang_map.json", "dictionary-ja.txt"):
        (ckpt_dir / name).write_bytes(b"x")
    artifacts = r5b.collect_salvage_artifacts(run5_raw, tmp_path / "DiffSinger")
    dests = {p.name: dest for p, dest in artifacts}
    assert dests["spk_map.json"] == "phase_b/config"
    assert dests["lang_map.json"] == "phase_b/config"
    assert dests["dictionary-ja.txt"] == "phase_b/config"


# --- run 7 プロファイル（DESIGN_S6_run7.md・教師交代） ------------------------


def test_apply_run_profile_run7_switches_constants_and_run5_restores() -> None:
    try:
        profile = r5b.apply_run_profile("run7")
        assert profile["run_id"] == "s6_run7"
        assert r5b.RUN_ID == "s6_run7"
        assert r5b.EXP_NAME_PHASE_A == "s6_run7_acoustic_scratch"
        assert r5b.EXP_NAME_PHASE_B == "s6_run7_acoustic_v1"
        assert r5b.DATASET_PINS_PATH.name == "run7_dataset_pins.json"
        assert r5b.REMOTE_PREFIX == "run7"
        assert r5b.ASSEMBLE_PROFILE == "run7"
        assert r5b.EXPECTED_SPK_MAP == {"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 4}
        # 恒久欠番: id 3 は期待マップに存在しない（DESIGN_S6 §0-2）。
        assert 3 not in r5b.EXPECTED_SPK_MAP.values()
        assert r5b._remote_path("amitaro_sources") == "run7/amitaro_sources"
    finally:
        r5b.apply_run_profile("run5")
    assert r5b.ASSEMBLE_PROFILE == "run5"
    assert r5b.EXPECTED_SPK_MAP == {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}


def test_verify_spk_map_accepts_exact_match(tmp_path: Path) -> None:
    expected = {"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 4}
    path = tmp_path / "spk_map.json"
    path.write_text(json.dumps(expected), encoding="utf-8")
    assert r5b.verify_spk_map(path, expected) == []


def test_verify_spk_map_detects_vacancy_autofill(tmp_path: Path) -> None:
    """DiffSinger build_spk_map の自動採番で恒久欠番 3 が埋まった事故の検出
    （DESIGN_S6 §0-2 の想定事故シナリオそのもの）。"""
    expected = {"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 4}
    path = tmp_path / "spk_map.json"
    path.write_text(
        json.dumps({"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 3}), encoding="utf-8"
    )
    diffs = r5b.verify_spk_map(path, expected)
    assert diffs and any("欠番" in d for d in diffs)


def test_verify_spk_map_missing_file_fails_closed(tmp_path: Path) -> None:
    diffs = r5b.verify_spk_map(tmp_path / "spk_map.json", {"ritsu": 0})
    assert diffs and "missing" in diffs[0]


def test_verify_dataset_against_pins_checks_selection_json(tmp_path: Path) -> None:
    """run 7 amitaro: selection.json（選定来歴）も pin 照合対象。"""
    ds = tmp_path / "amitaro_dataset"
    (ds / "wavs").mkdir(parents=True)
    (ds / "transcriptions.csv").write_text("name\n", encoding="utf-8")
    (ds / "selection.json").write_text("{}\n", encoding="utf-8")
    csv_sha = r5b.sha256_file(ds / "transcriptions.csv")
    good_sha = r5b.sha256_file(ds / "selection.json")
    pin = {
        "transcriptions_csv_sha256": csv_sha,
        "wav_sha256": {},
        "selection_json_sha256": good_sha,
    }
    assert r5b.verify_dataset_against_pins(ds, pin, "amitaro") == []
    pin_bad = dict(pin, selection_json_sha256="0" * 64)
    diffs = r5b.verify_dataset_against_pins(ds, pin_bad, "amitaro")
    assert diffs and "selection.json" in diffs[0]


def test_verify_assembly_pairs_follow_pins_sections() -> None:
    """照合ペアは pins のセクション構成が単一ソース: amitaro pins（run 7）は
    amitaro を、d3 pins（run 5/6）は d3synth を照合する。"""
    speaker_entry = {"transcriptions_csv_sha256": "x" * 64, "wav_sha256": {"a.wav": "y" * 64}}
    manifest = {
        "speakers": {
            "user": dict(speaker_entry, exclusions_json_sha256="z" * 64),
            "amitaro": dict(speaker_entry),
        }
    }
    pins = {
        "user": dict(speaker_entry, exclusions_json_sha256="z" * 64),
        "amitaro": dict(speaker_entry),
    }
    assert r5b.verify_assembly_against_run4_pins(manifest, pins) == []
    pins_bad = {
        "user": dict(speaker_entry, exclusions_json_sha256="z" * 64),
        "amitaro": dict(speaker_entry, wav_sha256={"a.wav": "0" * 64}),
    }
    diffs = r5b.verify_assembly_against_run4_pins(manifest, pins_bad)
    assert diffs and diffs[0].startswith("amitaro:")


def test_expected_spk_maps_match_assemble_profiles() -> None:
    """spk_id マップの三重定義ドリフト検出（セルフレビュー #4: bootstrap の
    RUN_PROFILES.expected_spk_map と assemble_run4 の SPK_IDS/SPK_IDS_RUN7 が
    手書き重複 — 片方だけの改訂は Pod 上の verify_spk_map まで発覚しない）。"""
    import importlib.util
    path = (Path(__file__).resolve().parent.parent / "s1_dataprep" / "assemble_run4.py")
    spec = importlib.util.spec_from_file_location("assemble_run4_for_parity", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert r5b.RUN_PROFILES["run5"]["expected_spk_map"] == mod.SPK_IDS
    assert r5b.RUN_PROFILES["run6"]["expected_spk_map"] == mod.SPK_IDS
    assert r5b.RUN_PROFILES["run7"]["expected_spk_map"] == mod.SPK_IDS_RUN7
    # 恒久欠番の番人: run7 マップに 3 が現れたら設計違反（DESIGN_S6 §0-2）。
    assert 3 not in r5b.RUN_PROFILES["run7"]["expected_spk_map"].values()


def test_prev_manifest_prefixes_follow_profile() -> None:
    """run 7 の前回 manifest 探索は run6/ まで（直下 run 5 へ落ちない —
    誤ベースライン比較の黙認防止・DESIGN_S6 §4）。"""
    try:
        r5b.apply_run_profile("run7")
        assert r5b.PREV_MANIFEST_PREFIXES == ("run7", "run6")
        r5b.apply_run_profile("run6")
        assert r5b.PREV_MANIFEST_PREFIXES == ("run6", "")
    finally:
        r5b.apply_run_profile("run5")
    assert r5b.PREV_MANIFEST_PREFIXES == ("",)


def test_verify_assembly_fails_closed_without_teacher_section() -> None:
    speaker_entry = {"transcriptions_csv_sha256": "x" * 64, "wav_sha256": {}}
    manifest = {"speakers": {"user": dict(speaker_entry)}}
    pins = {"user": dict(speaker_entry)}
    diffs = r5b.verify_assembly_against_run4_pins(manifest, pins)
    assert diffs and "教師セクション" in diffs[0]


# --- run 7 初回起動の fail-closed 回帰（2026-08-20・素材取得の命名規則） ------


def test_place_staged_sources_resolves_prefixed_names_by_content(tmp_path: Path) -> None:
    """plan_drive_id_fetch のローカル名は `{i:03d}_{元名}` の連番前置なので、
    **名前の一致では pin と突き合わせられない**（run 7 初回起動はこれで
    file set mismatch 停止した）。中身 sha で期待名へ解決できること。"""
    files_by_sha = {
        "a" * 64: [tmp_path / "000_recitation148.wav"],
        "b" * 64: [tmp_path / "001_recitation082.wav"],
    }
    pins = {"recitation148.wav": "a" * 64, "recitation082.wav": "b" * 64}
    resolved, diffs, extras = r5b.place_staged_sources_by_sha256(files_by_sha, pins)
    assert diffs == [] and extras == 0
    assert resolved["recitation148.wav"].name == "000_recitation148.wav"
    assert resolved["recitation082.wav"].name == "001_recitation082.wav"


def test_place_staged_sources_reports_missing_and_extras(tmp_path: Path) -> None:
    files_by_sha = {
        "a" * 64: [tmp_path / "000_x.wav"],
        "c" * 64: [tmp_path / "001_unexpected.wav"],
    }
    pins = {"recitation001.wav": "a" * 64, "recitation002.wav": "b" * 64}
    resolved, diffs, extras = r5b.place_staged_sources_by_sha256(files_by_sha, pins)
    assert set(resolved) == {"recitation001.wav"}
    assert len(diffs) == 1 and "recitation002.wav" in diffs[0]
    assert extras == 1


def _write_staged(root: Path, name: str, payload: bytes) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_place_staged_sources_diff_reports_what_actually_arrived(tmp_path: Path) -> None:
    """欠落時の diff は **実際に届いていたファイル名**も含むこと。
    run 7 初回 fail-closed の原因特定を可能にしたのは旧実装の
    `missing=…/extra=…` であり、sha 照合化でその情報が消えては後退になる。"""
    files_by_sha = {"c" * 64: [tmp_path / "000_recitation148.wav"]}
    pins = {"recitation001.wav": "a" * 64, "recitation002.wav": "b" * 64}
    resolved, diffs, extras = r5b.place_staged_sources_by_sha256(files_by_sha, pins)
    assert resolved == {} and extras == 1
    assert len(diffs) == 1
    msg = diffs[0]
    assert "recitation001.wav" in msg          # 何が足りないか
    assert "000_recitation148.wav" in msg      # 何が届いていたか
    assert "2/2" in msg


def test_place_staged_sources_is_deterministic_on_duplicate_content(tmp_path: Path) -> None:
    """同一 sha の重複コピー（Drive 同名重複の実体）でも解決先は決定論。
    依存の実体は `index_files_by_sha256`（`_iter_files` の sorted）なので、
    **実ファイルを置いて索引経由で**検証する（純関数を 2 回呼ぶだけでは
    ソート順の担保を何も検証していない）。重複は余剰に数えない
    （同一 sha は同内容 = `match_user_sources` と同じ設計）。"""
    src = tmp_path / "src"
    payload = b"RIFFdup"
    for name in ("002_a.wav", "000_a.wav", "001_a.wav"):
        _write_staged(src, name, payload)
    sha = hashlib.sha256(payload).hexdigest()
    pins = {"recitation001.wav": sha}
    resolved, diffs, extras = r5b.place_staged_sources_by_sha256(
        r5b.index_files_by_sha256(src), pins)
    assert diffs == []
    # `_iter_files` の sorted によりソート順先頭（= 000_a.wav）が選ばれる
    assert resolved["recitation001.wav"].name == "000_a.wav"
    # 同一 sha の 3 本は「どれを使っても等価」なので全数 matched 扱い＝余剰 0
    assert extras == 0


# --- 配線そのものの回帰（materialize_staged_sources）------------------------


def test_materialize_places_prefixed_files_under_pinned_names(tmp_path: Path) -> None:
    """実際に壊れた配線 = 「連番前置名で取得 → 期待名で配置」を検証する。
    純関数だけのテストでは、この配線が退行しても気づけない。"""
    src = tmp_path / "src"
    sha1 = _write_staged(src, "000_recitation148.wav", b"one")
    sha2 = _write_staged(src, "001_recitation082.wav", b"two")
    pins = {"recitation148.wav": sha1, "recitation082.wav": sha2}
    dest = tmp_path / "named"
    placed, extras = r5b.materialize_staged_sources(src, pins, dest)
    assert sorted(p.name for p in dest.glob("*.wav")) == [
        "recitation082.wav", "recitation148.wav"]
    assert (dest / "recitation148.wav").read_bytes() == b"one"
    assert placed["recitation082.wav"] == dest / "recitation082.wav"
    assert extras == 0


def test_materialize_fails_closed_on_missing_content(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write_staged(src, "000_x.wav", b"one")
    pins = {"recitation001.wav": "d" * 64}
    with pytest.raises(r5b.PinMismatchError):
        r5b.materialize_staged_sources(src, pins, tmp_path / "named")
    assert not (tmp_path / "named").exists()  # 失敗時は何も公開しない


def test_materialize_refuses_existing_dest(tmp_path: Path) -> None:
    """前走行の残骸を黙って消さない（gate 4 = d3_render_out と同流儀）。"""
    src = tmp_path / "src"
    sha = _write_staged(src, "000_x.wav", b"one")
    dest = tmp_path / "named"
    dest.mkdir()
    with pytest.raises(r5b.StageFailure, match="既に存在"):
        r5b.materialize_staged_sources(src, {"a.wav": sha}, dest)


def test_materialize_rejects_path_traversal_in_pin_key(tmp_path: Path) -> None:
    src = tmp_path / "src"
    sha = _write_staged(src, "000_x.wav", b"one")
    for bad in ("../escape.wav", "sub/dir.wav", ".."):
        with pytest.raises(r5b.StageFailure, match="bare filename"):
            r5b.materialize_staged_sources(src, {bad: sha}, tmp_path / f"n_{abs(hash(bad))}")
