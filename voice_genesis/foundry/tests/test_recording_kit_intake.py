"""test_recording_kit_intake.py — review #264 R10/R11 再現テスト。

`recording_kit/intake.py` への 3 件の指摘を再現・検証する:

- R10 P1: `UC-001.wav` と `UC-001.m4a` のように拡張子違いで stem が一致する
  入力が同じ `{stem}.norm24k.wav` に解決され、2 回目の ffmpeg -y が 1 回目を
  上書きしてしまう衝突（`assign_normalized_filenames` のテイク連番一意化で解消）
- R10 P2: バッチ途中の変換・測定失敗後も、既に成功した正規化 wav だけが
  `out_dir` に残り、台帳は未記帳のまま部分バッチが残留する（staging 経由の
  一括公開 + 失敗時ロールバックで解消）
- R11 P2: `UC-0010.m4a` のような非有界マッチが `UC-001` に誤帰属する
  （3 桁直後の境界チェックで解消）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator, List

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "recording_kit"))

import intake  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_intake_module_sys_path() -> Iterator[None]:
    """他テストへの `sys.path` 汚染防止（gate_synth 系テストと同じ作法）。"""
    original_sys_path = list(sys.path)
    yield
    sys.path[:] = original_sys_path


def _write_fake_source(path: Path, seed: int, sample_rate: int = 24000) -> None:
    """ffmpeg に依存しない偽の音声ソースを書き出す（内容は seed で決定論的に変える）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0.0, 0.05, int(sample_rate * 0.05), endpoint=False)
    data = 0.1 * np.sin(2.0 * np.pi * (220.0 + seed * 37.0) * t)
    sf.write(str(path), data.astype(np.float32), sample_rate)


def _fake_normalize_to_wav(monkeypatch: pytest.MonkeyPatch, *, fail_for: set[str] | None = None) -> None:
    """`normalize_to_wav`（ffmpeg 呼び出し）を、ソースファイル名から決定論的に
    内容が変わる偽 wav 書き出しへ差し替える。`fail_for` に含まれるソース名は
    例外を送出する（R10 P2 のバッチ途中失敗を再現するため）。
    """
    fail_names = fail_for or set()

    def _fake(src: Path, dst: Path) -> None:
        if src.name in fail_names:
            raise intake.subprocess.CalledProcessError(1, ["ffmpeg"], b"", b"boom")
        seed = sum(src.name.encode("utf-8"))
        _write_fake_source(dst, seed=seed, sample_rate=intake.TARGET_SAMPLE_RATE)

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)


# ---------------------------------------------------------------------------
# R11 P2: card ID 抽出の境界チェック
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "UC-0010.m4a",
        "UC-0010_take1.wav",
        "UC-001oops.wav",
        "UC-0011.mp3",
    ],
)
def test_extract_card_id_rejects_unbounded_suffix(filename: str) -> None:
    """4 桁目以降が続く非有界マッチは `card_id: null`（別カードへの誤帰属禁止）。"""
    assert intake.extract_card_id(filename) is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("UC-001.wav", "UC-001"),
        ("UC-001.m4a", "UC-001"),
        ("UC-001_あ_2026-08-20.m4a", "UC-001"),
        ("UC-001 take2.wav", "UC-001"),
        ("UC-001-b.wav", "UC-001"),
        ("UC-001.take2.wav", "UC-001"),
        ("uc-002.wav", "UC-002"),
    ],
)
def test_extract_card_id_accepts_documented_delimiters(filename: str, expected: str) -> None:
    """stem 終端・`_`/空白/`.`/`-` 区切りのいずれかが続く場合は正しく抽出する。"""
    assert intake.extract_card_id(filename) == expected


# ---------------------------------------------------------------------------
# R10 P1: 正規化後ファイル名の事前衝突検査 + テイク連番一意化
# ---------------------------------------------------------------------------


def test_assign_normalized_filenames_dedupes_same_stem_different_extensions(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    inputs: List[Path] = sorted([tmp_path / "UC-001.m4a", tmp_path / "UC-001.wav"])

    assigned = intake.assign_normalized_filenames(inputs, out_dir)

    names = [assigned[p] for p in inputs]
    assert len(set(names)) == 2, "同じ stem の 2 入力が同じ出力名に解決されてはならない"
    assert names[0] == "UC-001.norm24k.wav"
    assert names[1] == "UC-001.take2.norm24k.wav"


def test_assign_normalized_filenames_take_numbers_increment_for_three_or_more(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    inputs = sorted(
        [tmp_path / "UC-002.m4a", tmp_path / "UC-002.mp3", tmp_path / "UC-002.wav"]
    )

    assigned = intake.assign_normalized_filenames(inputs, out_dir)

    names = [assigned[p] for p in inputs]
    assert names == [
        "UC-002.norm24k.wav",
        "UC-002.take2.norm24k.wav",
        "UC-002.take3.norm24k.wav",
    ]


def test_assign_normalized_filenames_avoids_collision_with_existing_out_dir(
    tmp_path: Path,
) -> None:
    """別バッチで既に公開済みの `out_dir` 内ファイルとも衝突させない。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "UC-001.norm24k.wav").write_bytes(b"already published")

    inputs = [tmp_path / "UC-001.wav"]
    assigned = intake.assign_normalized_filenames(inputs, out_dir)

    assert assigned[inputs[0]] == "UC-001.take2.norm24k.wav"


# ---------------------------------------------------------------------------
# R10 P1 + P2: run() end-to-end（ffmpeg 非依存の偽変換で再現）
# ---------------------------------------------------------------------------


def test_run_resolves_stem_collision_with_distinct_hashes_and_take_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R10 P1 の再現: 2 回目の ffmpeg -y が 1 回目を上書きしていた旧実装では、
    ここで `out_dir` に wav が 1 本しか残らず、台帳の一方の sha256 が
    ファイル実体と食い違う（hash 不整合）。修正後は 2 本とも残り、
    それぞれの sha256 が実ファイルと一致する。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.m4a").write_bytes(b"fake m4a bytes")
    (incoming_dir / "UC-001.wav").write_bytes(b"fake wav bytes")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    assert len(entries) == 2
    normalized_paths = {Path(e.normalized_path) for e in entries}
    assert len(normalized_paths) == 2, "正規化後パスが衝突してはならない"
    assert {p.name for p in normalized_paths} == {
        "UC-001.norm24k.wav",
        "UC-001.take2.norm24k.wav",
    }

    # 実ファイルが両方とも out_dir に存在し、台帳の sha256 と一致する。
    published_files = sorted(out_dir.iterdir())
    assert len(published_files) == 2
    for entry in entries:
        published = Path(entry.normalized_path)
        assert published.exists()
        assert intake.sha256_of(published) == entry.sha256

    hashes = {e.sha256 for e in entries}
    assert len(hashes) == 2, "衝突していた旧実装では 2 エントリの hash が同一値に潰れる"

    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 2


def test_run_rolls_back_whole_batch_on_mid_batch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R10 P2 の再現: 3 件中 2 件目の変換が失敗した場合、旧実装は 1 件目の
    正規化 wav だけを `out_dir` へ公開したまま台帳を更新せず例外送出していた
    （部分バッチが残留）。修正後は staging から公開する前に失敗するため、
    `out_dir`/台帳のどちらにも痕跡が残らない。
    """
    _fake_normalize_to_wav(monkeypatch, fail_for={"UC-002.wav"})

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")
    (incoming_dir / "UC-002.wav").write_bytes(b"b")
    (incoming_dir / "UC-003.wav").write_bytes(b"c")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(Exception):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists(), "失敗したバッチは台帳へ一切記帳されてはならない"
    assert not out_dir.exists() or list(out_dir.iterdir()) == [], (
        "失敗したバッチの正規化 wav が out_dir に残ってはならない（部分公開の禁止）"
    )
    # staging 用の一時ディレクトリも後片付けされていること。
    leftover_staging = [
        p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith(".intake-staging-")
    ]
    assert leftover_staging == []


def test_run_no_inputs_leaves_out_dir_and_ledger_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    assert entries == []
    assert not out_dir.exists()
    assert not ledger_path.exists()


# ---------------------------------------------------------------------------
# 実 ffmpeg 変換の動作確認（ffmpeg が無い環境では skip）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(intake.FFMPEG_PATH is None, reason="ffmpeg が見つからない環境ではスキップ")
def test_run_with_real_ffmpeg_resolves_stem_collision(tmp_path: Path) -> None:
    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    _write_fake_source(incoming_dir / "UC-001.wav", seed=1, sample_rate=44100)

    # soundfile（libsndfile）は m4a を直接書けないため、同 stem 衝突の
    # 2 本目は実 ffmpeg で wav → m4a へ変換して用意する（本テスト自体が
    # ffmpeg 存在時のみ実行されるため、fixture 生成に使っても矛盾しない）。
    seed_wav = tmp_path / "_seed_for_m4a.wav"
    _write_fake_source(seed_wav, seed=2, sample_rate=44100)
    assert intake.FFMPEG_PATH is not None
    intake.subprocess.run(
        [intake.FFMPEG_PATH, "-y", "-i", str(seed_wav), str(incoming_dir / "UC-001.m4a")],
        check=True,
        capture_output=True,
    )

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    assert len(entries) == 2
    published = sorted(out_dir.iterdir())
    assert len(published) == 2
    for entry in entries:
        published_path = Path(entry.normalized_path)
        assert published_path.exists()
        assert intake.sha256_of(published_path) == entry.sha256
        data, sample_rate = sf.read(str(published_path), always_2d=False)
        assert sample_rate == intake.TARGET_SAMPLE_RATE
        if data.ndim > 1:
            assert data.shape[1] == 1
    hashes = {e.sha256 for e in entries}
    assert len(hashes) == 2
