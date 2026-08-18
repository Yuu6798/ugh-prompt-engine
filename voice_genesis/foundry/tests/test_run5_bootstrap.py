"""test_run5_bootstrap.py — `scripts/run5_bootstrap.py` ロジック層の検証。

本開発環境には GPU・torch・rclone・runpodctl が無いため、検証対象は
ロジック層のみ（stage 計画・pin 検証・phase config 導出・milestone 検知・
wall-clock 判定・heartbeat 記帳・自動停止コマンド組み立て）。実行系
（render/binarize/train/rclone の subprocess）の初回実測は run 5 本番が
兼ねる（`run5_bootstrap.py` docstring 冒頭の正直会計と対）。
"""
from __future__ import annotations

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
    names = [p.name for p in artifacts]
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


def test_collect_salvage_artifacts_empty_when_nothing_exists(tmp_path: Path) -> None:
    assert r5b.collect_salvage_artifacts(tmp_path / "nope", tmp_path / "nope2") == []


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
