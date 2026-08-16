"""test_recording_kit_intake.py — review #264 R10/R11/R12 再現テスト。

`recording_kit/intake.py` への指摘を再現・検証する:

- R10 P1: `UC-001.wav` と `UC-001.m4a` のように拡張子違いで stem が一致する
  入力が同じ `{stem}.norm24k.wav` に解決され、2 回目の ffmpeg -y が 1 回目を
  上書きしてしまう衝突（`assign_normalized_filenames` のテイク連番一意化で解消）
- R10 P2: バッチ途中の変換・測定失敗後も、既に成功した正規化 wav だけが
  `out_dir` に残り、台帳は未記帳のまま部分バッチが残留する（staging 経由の
  一括公開 + 失敗時ロールバックで解消）
- R11 P2: `UC-0010.m4a` のような非有界マッチが `UC-001` に誤帰属する
  （3 桁直後の境界チェックで解消）
- R12 P1: `--ledger` が incoming 元ファイル・導出出力（staging 内/最終正規化
  wav）・staging ディレクトリ自体と衝突する場合、`save_ledger()` がドナー
  原本や正規化済み wav を JSON で上書きしてしまう（`_check_ledger_path_
  collisions` の処理開始前 preflight で解消）
- R12 P2: 公開フェーズ（`out_dir` への移動ループ + `save_ledger`）の途中で
  移動または台帳保存が失敗すると、既に公開済みの wav が巻き戻されず
  `out_dir` に部分公開が残留する（移動済みファイルを staging へ戻す
  `BaseException` 巻き戻しで解消）
- R12 P2: 台帳エントリが正規化後 wav の sha256 のみを記録し、incoming 原本の
  provenance を追跡できない（`source_sha256`/`source_size_bytes` の追記で解消）
- R13 P2 (gate_synth.py:119 の自己ハッシュ束縛は本ファイル対象外。境界宣言は
  PR #264 レビュースレッド参照): `process_one` が `source_sha256`/
  `source_size_bytes` を計算する read と `ffmpeg` が実際に変換する read が
  別タイミングだった（`src` を staging 内スナップショットへ一度だけ read し、
  ハッシュと変換入力の両方をそのスナップショットから確定させることで解消）
- R13 P2: 公開フェーズ（`out_dir` への移動 + `save_ledger`）の巻き戻しが
  WAV のみで台帳を復元していなかった（`save_ledger()` 成功直後・関数が
  返る前の中断シナリオを含めて、公開フェーズ開始前の台帳バイト列
  スナップショットへ復元することで解消）
- R13 P2: `load_ledger` が `schema` の不一致を無視して `entries` があれば
  そのまま受理していた（`schema == LEDGER_SCHEMA` の完全一致と `entries`
  がリストであることを検証し、不一致は `LedgerSchemaError` で fail-closed
  拒否することで解消）
- R14 P1 (intake.py:270): `save_ledger`/`_restore_ledger` が使う
  決定論的な `<ledger>.tmp` パスに、無関係な既存ファイルが偶然存在すると
  黙って truncate ＋ 消失させてしまう（`tempfile.mkstemp` による排他生成
  一意 tmp パスへ切り替えて解消）
- R14 P1 (intake.py:387): `__src_snapshot__{name}` プレフィクス方式の
  スナップショットパスが、入力の組み合わせ次第で staged 出力名と衝突し
  得た（`staging_dir/src_snapshots/{元ファイル名}` という専用サブ
  ディレクトリへ分離することで、名前空間を構造的に非交差にして解消）
- R16 P1 (intake.py:173): `assign_normalized_filenames` の出力名予約が
  `p.is_file()` でファイルのみを対象にしており、`out_dir` に同名の
  ディレクトリ・symlink ディレクトリが既に存在してもその名前を予約しない。
  公開フェーズの `shutil.move` は移動先が既存ディレクトリだと"その中へ"
  移動する挙動を持つため、同名ディレクトリがあると wav がその中に置かれ
  台帳は誤ってディレクトリ自体を `normalized_path` として記録し、symlink
  ディレクトリなら `out_dir` の外側へ書き込まれる。予約対象を `out_dir`
  の全エントリへ拡張し、さらに公開直前に `_check_publish_path` で
  最終防御（既存エントリなら拒否・親ディレクトリが `out_dir` と一致する
  ことを検証）することで解消
- R17 P2 (intake.py:567): incoming をクリアせずに再実行した場合や、同じ
  収録が別ファイル名で 2 度届いた場合、旧実装は無条件に台帳へ追記して
  おり、同一 `source_sha256` の take が二重計上され得た（「本物の再録」＝
  バイト列が異なる場合との区別を失う）。今回バッチ各ファイルの
  `source_sha256` を既存台帳の全エントリ・同一バッチ内の他ファイルの
  両方と突き合わせ、重複があれば staging → `out_dir` 一括公開の直前で
  fail-closed 拒否する（`_check_duplicate_sources`）ことで解消。バイト列が
  異なる再録は影響を受けない
- R21 P1 (intake.py:765): 同一 `out_dir`/`--ledger` を対象に 2 つの intake
  プロセスが並行実行されると、両方が同じ旧台帳を読み込み、それぞれ別の
  wav を公開した上で `save_ledger()` を呼び、後発の save が先発の追記済み
  エントリを丸ごと上書きしていた（先発の wav は `out_dir` に存在するのに
  台帳には記録されないデータ損失。ロールバック機構をすり抜ける正常終了
  パスで起きる）。`run()` 全体（preflight〜公開〜台帳 save〜ロールバック）
  を `<ledger>.lock` への `fcntl.flock(LOCK_EX | LOCK_NB)` で直列化し、
  取得できない場合は `LedgerLockError` で即座に fail-closed 拒否すること
  で解消
- R21 P2 (intake.py:667): ヘッダのみの WAV（フレーム数 0）や、ffmpeg が
  exit 0 でもフレームを一切書き出さなかった場合、`measure_loudness()` が
  `duration_sec == 0.0` を返し、旧実装はこれをそのまま有効な intake として
  台帳へ記録・公開していた。`process_one` が台帳エントリ構築前に
  `duration_sec` の非正・非有限を `NonPositiveDurationError` で fail-closed
  拒否することで解消（`convert_pjs.py`/`build_dataset.py` の「非正
  duration は無条件で不正」という意味論と揃える）
"""
from __future__ import annotations

import hashlib
import json
import sys
import wave
from pathlib import Path
from typing import Dict, Iterator, List

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

    R13 P2 対応で `normalize_to_wav` へ渡される `src` は `process_one` が
    staging 内へ作るスナップショット（R14 P1 以降は
    `src_snapshots/{元ファイル名}`、ファイル名自体は元ファイル名のまま）に
    変わったため、元ファイル名との一致は完全一致でなく部分一致で判定する
    （プレフィクス方式だった旧実装からの互換のため部分一致のまま残す）。
    """
    fail_names = fail_for or set()

    def _fake(src: Path, dst: Path) -> None:
        if any(name in src.name for name in fail_names):
            raise intake.subprocess.CalledProcessError(1, ["ffmpeg"], b"", b"boom")
        seed = sum(src.name.encode("utf-8"))
        _write_fake_source(dst, seed=seed, sample_rate=intake.TARGET_SAMPLE_RATE)

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)


def _valid_ledger_entry_dict(**overrides: object) -> Dict[str, object]:
    """`LedgerEntry` の全必須フィールドを満たす台帳エントリ dict を返す
    （R19 P2: `load_ledger` がエントリ単位で必須フィールド・型を検証する
    ようになったため、手書きの台帳 fixture もこの形状を満たす必要がある）。
    """
    entry: Dict[str, object] = {
        "card_id": "UC-001",
        "source_filename": "UC-001.wav",
        "source_sha256": hashlib.sha256(b"placeholder source bytes").hexdigest(),
        "source_size_bytes": 25,
        "normalized_path": "out/UC-001.norm24k.wav",
        "sha256": hashlib.sha256(b"placeholder normalized bytes").hexdigest(),
        "received_at": "2026-01-01T00:00:00Z",
        "duration_sec": 0.05,
        "sample_rate": intake.TARGET_SAMPLE_RATE,
        "rms_dbfs": -20.0,
        "peak_dbfs": -6.0,
        "alignment_status": "not_started",
    }
    entry.update(overrides)
    return entry


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


def test_assign_normalized_filenames_avoids_collision_with_existing_directory(
    tmp_path: Path,
) -> None:
    """R16 P1 の再現: `out_dir` に同名の**ディレクトリ**が既に存在する場合、
    旧実装（`p.is_file()` のみ予約）はその名前を予約しないため、テイク番号を
    振らずそのまま衝突する候補名を返していた。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "UC-001.norm24k.wav").mkdir()  # ディレクトリとして既存

    inputs = [tmp_path / "UC-001.wav"]
    assigned = intake.assign_normalized_filenames(inputs, out_dir)

    assert assigned[inputs[0]] == "UC-001.take2.norm24k.wav"


def test_assign_normalized_filenames_avoids_collision_with_existing_symlink(
    tmp_path: Path,
) -> None:
    """R16 P1 の再現: `out_dir` に同名の symlink（外部ディレクトリを指す）が
    既に存在する場合も同様に予約されなければならない。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    external = tmp_path / "external_secret"
    external.mkdir()
    (out_dir / "UC-001.norm24k.wav").symlink_to(external, target_is_directory=True)

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
# R12 P1: --ledger のパス衝突 preflight 拒否
# ---------------------------------------------------------------------------


def test_check_ledger_path_collisions_rejects_incoming_source(tmp_path: Path) -> None:
    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-001.norm24k.wav"}

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(src, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_rejects_final_normalized_wav(tmp_path: Path) -> None:
    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-001.norm24k.wav"}
    ledger_path = out_dir / "UC-001.norm24k.wav"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_rejects_staged_output(tmp_path: Path) -> None:
    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-001.norm24k.wav"}
    ledger_path = staging_dir / "UC-001.norm24k.wav"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_rejects_staging_dir_itself(tmp_path: Path) -> None:
    """`--ledger` が staging ディレクトリ内部を指す場合、バッチ終了時の
    `rmtree(staging_dir)` で保存直後の台帳ごと消える事故を防ぐ。
    """
    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-001.norm24k.wav"}
    ledger_path = staging_dir / "user_donor_ledger.json"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_rejects_existing_out_dir_file(tmp_path: Path) -> None:
    """今回バッチの導出出力でなくても、`out_dir` に既に公開済みの他バッチの
    ファイルと衝突する場合も拒否する。
    """
    src = tmp_path / "UC-002.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "UC-001.norm24k.wav"
    existing.write_bytes(b"already published")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-002.norm24k.wav"}

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(existing, [src], filenames, out_dir, staging_dir)


# ---------------------------------------------------------------------------
# R19 P2 (intake.py:409): out_dir 内台帳の衝突誤検知の解消
# ---------------------------------------------------------------------------


def test_check_ledger_path_collisions_allows_existing_valid_ledger_at_ledger_path(
    tmp_path: Path,
) -> None:
    """`--ledger` が `out_dir` 内にある正当な配置（append ワークフロー）では、
    2 回目以降のバッチの preflight で `--ledger` 自身が『out_dir に公開済みの
    既存ファイル』として見つかる。中身が現行スキーマの台帳として読み込める
    場合は、これを衝突として拒否してはならない（R19 P2 の再現）。
    """
    src = tmp_path / "UC-002.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ledger_path = out_dir / "user_donor_ledger.json"
    intake.save_ledger(
        ledger_path, {"schema": intake.LEDGER_SCHEMA, "entries": [_valid_ledger_entry_dict()]}
    )
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-002.norm24k.wav"}

    intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_still_rejects_non_ledger_file_at_ledger_path(
    tmp_path: Path,
) -> None:
    """`--ledger` が `out_dir` 内の既存ファイルを指していても、中身が台帳と
    して読み込めない（例: 正規化 wav 等の無関係なファイル）場合は、従来通り
    衝突として fail-closed 拒否する（`_is_existing_ledger_file` の除外条件が
    『中身が台帳として読める』場合のみに限定されていることの回帰）。
    """
    src = tmp_path / "UC-002.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "UC-001.norm24k.wav"
    existing.write_bytes(b"already published")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-002.norm24k.wav"}

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(existing, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_allows_normal_ledger_path(tmp_path: Path) -> None:
    """通常の（衝突しない）`--ledger` パスは preflight を素通りする。"""
    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"a")
    out_dir = tmp_path / "out"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    filenames = {src: "UC-001.norm24k.wav"}
    ledger_path = tmp_path / "user_donor_ledger.json"

    intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_run_rejects_ledger_colliding_with_incoming_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12 P1 の再現: `--ledger` が incoming の元ファイルを指す場合、旧実装は
    変換・公開後の `save_ledger()` がその元ファイルを JSON で上書きしドナー
    原本を破壊していた。修正後は変換開始前に fail-closed 拒否し、原本は
    一切変更されない。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    donor_original = incoming_dir / "UC-001.wav"
    donor_original.write_bytes(b"precious original bytes")

    out_dir = tmp_path / "out"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake.run(incoming_dir, out_dir, donor_original)

    assert donor_original.read_bytes() == b"precious original bytes", (
        "preflight 拒否後も incoming 原本が一切変更されてはならない"
    )
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_run_rejects_ledger_colliding_with_derived_normalized_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12 P1 の再現: `--ledger` が最終正規化 wav のパスと一致する場合、旧
    実装は公開後の `save_ledger()` がその wav を JSON で上書きしていた
    （台帳が記録する音声 hash の実体が消える）。修正後は変換開始前に
    fail-closed 拒否する。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")

    out_dir = tmp_path / "out"
    colliding_ledger = out_dir / "UC-001.norm24k.wav"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake.run(incoming_dir, out_dir, colliding_ledger)

    assert not colliding_ledger.exists(), "衝突する導出出力パスへは何も書かれてはならない"


def test_run_appends_second_batch_when_ledger_lives_inside_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R19 P2 の再現（E2E）: `--ledger` を `--out-dir` 内に置く正当な配置で、
    1 回目の intake が成功し台帳・wav が `out_dir` に公開された後、2 回目の
    バッチ（追記）も `_check_ledger_path_collisions` の誤検知で拒否されずに
    成功しなければならない。
    """
    _fake_normalize_to_wav(monkeypatch)

    out_dir = tmp_path / "out"
    ledger_path = out_dir / "user_donor_ledger.json"

    first_incoming = tmp_path / "incoming1"
    first_incoming.mkdir()
    (first_incoming / "UC-001.wav").write_bytes(b"first donor bytes")

    first_entries = intake.run(first_incoming, out_dir, ledger_path)
    assert len(first_entries) == 1

    second_incoming = tmp_path / "incoming2"
    second_incoming.mkdir()
    (second_incoming / "UC-002.wav").write_bytes(b"second donor bytes")

    second_entries = intake.run(second_incoming, out_dir, ledger_path)
    assert len(second_entries) == 1

    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 2
    source_filenames = {e["source_filename"] for e in ledger["entries"]}
    assert source_filenames == {"UC-001.wav", "UC-002.wav"}


# ---------------------------------------------------------------------------
# R12 P2: 公開フェーズ途中失敗のロールバック
# ---------------------------------------------------------------------------


def test_run_rolls_back_published_wavs_when_a_later_move_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12 P2 の再現: 公開フェーズ（`out_dir` への移動ループ）で 2 件目の
    移動が失敗した場合、旧実装は 1 件目を `out_dir` へ公開したまま巻き戻さず
    例外送出していた（台帳は未記帳のまま部分公開が残留）。修正後は公開済み
    だった 1 件目も staging へ戻され、`out_dir` に痕跡が残らない。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")
    (incoming_dir / "UC-002.wav").write_bytes(b"b")
    (incoming_dir / "UC-003.wav").write_bytes(b"c")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    real_move = intake.shutil.move
    move_calls = {"n": 0}

    def _flaky_move(src, dst):
        move_calls["n"] += 1
        if move_calls["n"] == 2:
            raise OSError("simulated failure during publish move")
        return real_move(src, dst)

    monkeypatch.setattr(intake.shutil, "move", _flaky_move)

    with pytest.raises(OSError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists(), "公開フェーズ失敗時は台帳へ一切記帳されてはならない"
    assert not out_dir.exists() or list(out_dir.iterdir()) == [], (
        "巻き戻し後は out_dir に公開済み wav が残ってはならない（部分公開の禁止）"
    )
    leftover_staging = [
        p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith(".intake-staging-")
    ]
    assert leftover_staging == []


def test_run_rolls_back_all_published_wavs_when_ledger_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R12 P2 の再現（指摘の本丸）: 全 wav の移動が完了した直後に台帳保存が
    失敗した場合、旧実装は `out_dir` に全 wav を公開したまま台帳だけ未更新の
    状態で例外送出していた（部分公開）。修正後は移動済みの wav 全てを
    staging へ巻き戻す。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")
    (incoming_dir / "UC-002.wav").write_bytes(b"b")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    def _boom_save_ledger(path: Path, ledger: dict) -> None:
        raise OSError("simulated ledger write failure")

    monkeypatch.setattr(intake, "save_ledger", _boom_save_ledger)

    with pytest.raises(OSError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists()
    assert not out_dir.exists() or list(out_dir.iterdir()) == [], (
        "台帳保存失敗時は、直前に移動済みだった全 wav が out_dir から巻き戻されて"
        "いなければならない"
    )
    leftover_staging = [
        p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith(".intake-staging-")
    ]
    assert leftover_staging == []


# ---------------------------------------------------------------------------
# R12 P2: 元 incoming ファイルの sha256/サイズ記帳
# ---------------------------------------------------------------------------


def test_process_one_records_source_sha256_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_normalize_to_wav(monkeypatch)

    src = tmp_path / "UC-001.wav"
    src_bytes = b"incoming donor bytes for hashing"
    src.write_bytes(src_bytes)

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    publish_dir = tmp_path / "out"

    entry = intake.process_one(src, staging_dir, "UC-001.norm24k.wav", publish_dir)

    assert entry.source_sha256 == hashlib.sha256(src_bytes).hexdigest()
    assert entry.source_size_bytes == len(src_bytes)
    # 正規化後 wav の sha256（`sha256`）とは別物であること（原本と正規化後で
    # 内容が異なる以上、両ハッシュも一致しないはず）。
    assert entry.sha256 != entry.source_sha256


def test_run_records_source_sha256_for_each_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"donor one")
    (incoming_dir / "UC-002.wav").write_bytes(b"donor two")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    by_source = {e.source_filename: e for e in entries}
    assert by_source["UC-001.wav"].source_sha256 == hashlib.sha256(b"donor one").hexdigest()
    assert by_source["UC-001.wav"].source_size_bytes == len(b"donor one")
    assert by_source["UC-002.wav"].source_sha256 == hashlib.sha256(b"donor two").hexdigest()
    assert by_source["UC-002.wav"].source_size_bytes == len(b"donor two")

    ledger = intake.load_ledger(ledger_path)
    for raw_entry in ledger["entries"]:
        assert "source_sha256" in raw_entry
        assert "source_size_bytes" in raw_entry


# ---------------------------------------------------------------------------
# R13 P2: ハッシュ対象バイトと変換入力の一本化
# ---------------------------------------------------------------------------


def test_process_one_converts_a_staging_snapshot_not_the_original_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`normalize_to_wav` に渡される `src` は incoming の原本パスではなく、
    staging 内のスナップショットでなければならない（単一 read の証跡）。
    """
    seen_srcs: List[Path] = []

    def _fake(src: Path, dst: Path) -> None:
        seen_srcs.append(src)
        _write_fake_source(dst, seed=1, sample_rate=intake.TARGET_SAMPLE_RATE)

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)

    src = tmp_path / "UC-001.wav"
    src.write_bytes(b"original donor bytes")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    publish_dir = tmp_path / "out"

    intake.process_one(src, staging_dir, "UC-001.norm24k.wav", publish_dir)

    assert len(seen_srcs) == 1
    converted_src = seen_srcs[0]
    assert converted_src != src, "ffmpeg 入力は原本ではなく staging 内スナップショットであること"
    assert converted_src.parent == staging_dir / "src_snapshots", (
        "スナップショットは専用サブディレクトリ配下でなければならない（R14 P1 対応）"
    )
    assert converted_src.read_bytes() == b"original donor bytes"
    # 原本は一切書き換えられていない。
    assert src.read_bytes() == b"original donor bytes"


def test_process_one_hash_matches_the_bytes_actually_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`source_sha256`/`source_size_bytes` は変換入力（スナップショット）と
    同一バイト列から確定していること。原本が読み込み直後に差し替わっても、
    台帳に記録されるのはスナップショット取得時点のバイト列である。
    """
    captured: dict = {}

    def _fake(src: Path, dst: Path) -> None:
        captured["snapshot_bytes"] = src.read_bytes()
        _write_fake_source(dst, seed=2, sample_rate=intake.TARGET_SAMPLE_RATE)

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)

    src = tmp_path / "UC-001.wav"
    original_bytes = b"bytes present at snapshot time"
    src.write_bytes(original_bytes)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    publish_dir = tmp_path / "out"

    entry = intake.process_one(src, staging_dir, "UC-001.norm24k.wav", publish_dir)

    assert captured["snapshot_bytes"] == original_bytes
    assert entry.source_sha256 == hashlib.sha256(original_bytes).hexdigest()
    assert entry.source_size_bytes == len(original_bytes)


# ---------------------------------------------------------------------------
# R13 P2: 台帳スキーマの fail-fast 検証
# ---------------------------------------------------------------------------


def test_load_ledger_rejects_mismatched_schema(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        '{"schema": "user-donor-ledger/0.9-typo", "entries": []}', encoding="utf-8"
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_rejects_missing_schema_field(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text('{"entries": []}', encoding="utf-8")

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_rejects_non_list_entries(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        f'{{"schema": "{intake.LEDGER_SCHEMA}", "entries": {{"not": "a list"}}}}',
        encoding="utf-8",
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_accepts_matching_schema_and_list_entries(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    valid_entry = _valid_ledger_entry_dict()
    ledger_path.write_text(
        json.dumps({"schema": intake.LEDGER_SCHEMA, "entries": [valid_entry]}),
        encoding="utf-8",
    )

    ledger = intake.load_ledger(ledger_path)
    assert ledger["schema"] == intake.LEDGER_SCHEMA
    assert ledger["entries"] == [valid_entry]


# ---------------------------------------------------------------------------
# R19 P2 (intake.py:289): 台帳エントリ単位の fail-closed 検証
# ---------------------------------------------------------------------------


def test_load_ledger_rejects_entry_missing_source_sha256(tmp_path: Path) -> None:
    """R19 P2 の再現: `source_sha256` を欠くエントリ（schema バージョンは
    一致するだけの破損/旧世代台帳）を、旧実装は `entries` がリストである
    ことしか検証しないため無自覚に受理していた。修正後はエントリ単位の
    必須フィールド検証で fail-closed 拒否する。
    """
    ledger_path = tmp_path / "user_donor_ledger.json"
    broken_entry = _valid_ledger_entry_dict()
    del broken_entry["source_sha256"]
    ledger_path.write_text(
        json.dumps({"schema": intake.LEDGER_SCHEMA, "entries": [broken_entry]}),
        encoding="utf-8",
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


@pytest.mark.parametrize(
    "field",
    [
        "card_id",
        "source_filename",
        "source_sha256",
        "source_size_bytes",
        "normalized_path",
        "sha256",
        "received_at",
        "duration_sec",
        "sample_rate",
        "rms_dbfs",
        "peak_dbfs",
        "alignment_status",
    ],
)
def test_load_ledger_rejects_entry_missing_any_required_field(
    tmp_path: Path, field: str
) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    broken_entry = _valid_ledger_entry_dict()
    del broken_entry[field]
    ledger_path.write_text(
        json.dumps({"schema": intake.LEDGER_SCHEMA, "entries": [broken_entry]}),
        encoding="utf-8",
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_rejects_entry_with_wrong_field_type(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    broken_entry = _valid_ledger_entry_dict(source_sha256=12345)
    ledger_path.write_text(
        json.dumps({"schema": intake.LEDGER_SCHEMA, "entries": [broken_entry]}),
        encoding="utf-8",
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_rejects_non_dict_entry(tmp_path: Path) -> None:
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text(
        f'{{"schema": "{intake.LEDGER_SCHEMA}", "entries": ["not-a-dict"]}}',
        encoding="utf-8",
    )

    with pytest.raises(intake.LedgerSchemaError):
        intake.load_ledger(ledger_path)


def test_load_ledger_accepts_entry_with_null_card_id_and_dbfs(tmp_path: Path) -> None:
    """`card_id`/`rms_dbfs`/`peak_dbfs` は `Optional` なので `None` を許容する。"""
    ledger_path = tmp_path / "user_donor_ledger.json"
    entry = _valid_ledger_entry_dict(card_id=None, rms_dbfs=None, peak_dbfs=None)
    ledger_path.write_text(
        json.dumps({"schema": intake.LEDGER_SCHEMA, "entries": [entry]}),
        encoding="utf-8",
    )

    ledger = intake.load_ledger(ledger_path)
    assert ledger["entries"] == [entry]


def test_run_rejects_broken_schema_ledger_before_processing_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """壊れた/未知スキーマの台帳が既存の場合、`run()` は変換・公開のいずれも
    開始せずに `LedgerSchemaError` で fail-closed 拒否する。
    """
    normalize_calls: List[Path] = []

    def _fake(src: Path, dst: Path) -> None:
        normalize_calls.append(src)
        _write_fake_source(dst, seed=3, sample_rate=intake.TARGET_SAMPLE_RATE)

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"
    ledger_path.write_text('{"schema": "unknown/0.0", "entries": []}', encoding="utf-8")

    with pytest.raises(intake.LedgerSchemaError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert normalize_calls == [], "スキーマ不一致の場合は変換を一切開始してはならない"
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
    # 既存の壊れた台帳自体は書き換えられていない。
    assert ledger_path.read_text(encoding="utf-8") == '{"schema": "unknown/0.0", "entries": []}'


# ---------------------------------------------------------------------------
# R13 P2: 公開フェーズ中断時の台帳復元
# ---------------------------------------------------------------------------


def test_run_restores_previous_ledger_when_interrupted_right_after_save_ledger_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`save_ledger()` が正常にディスクへ台帳を replace した直後・呼び出し元へ
    制御が返る前に `KeyboardInterrupt`/`SystemExit` が届くケースを再現する。
    旧実装は WAV だけを staging へ巻き戻し、台帳は新バッチのエントリを含んだ
    まま publicly visible に残った（存在しない wav パスを指す壊れた台帳）。
    修正後は台帳も公開フェーズ開始前の内容へ復元される。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-002.wav").write_bytes(b"new donor bytes")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    previous_ledger = {
        "schema": intake.LEDGER_SCHEMA,
        "entries": [_valid_ledger_entry_dict()],
    }
    intake.save_ledger(ledger_path, previous_ledger)
    previous_ledger_bytes = ledger_path.read_bytes()

    real_save_ledger = intake.save_ledger

    def _save_then_interrupt(path: Path, ledger: dict) -> None:
        real_save_ledger(path, ledger)
        raise KeyboardInterrupt

    monkeypatch.setattr(intake, "save_ledger", _save_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert ledger_path.read_bytes() == previous_ledger_bytes, (
        "save_ledger() 成功直後の中断でも、台帳は公開フェーズ開始前の内容へ"
        "復元されなければならない"
    )
    assert not out_dir.exists() or list(out_dir.iterdir()) == [], (
        "台帳と同様、公開済み wav も巻き戻されていなければならない"
    )


def test_run_deletes_new_ledger_when_interrupted_and_no_ledger_existed_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開フェーズ開始前に台帳が存在しなかった場合、`save_ledger()` 成功直後
    の中断でも、このバッチが新規生成した台帳ファイルは削除される（「無し」の
    状態への復元）。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"
    assert not ledger_path.exists()

    real_save_ledger = intake.save_ledger

    def _save_then_interrupt(path: Path, ledger: dict) -> None:
        real_save_ledger(path, ledger)
        raise SystemExit(1)

    monkeypatch.setattr(intake, "save_ledger", _save_then_interrupt)

    with pytest.raises(SystemExit):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists(), (
        "台帳が元々存在しなかった場合は、新規生成された台帳が削除されなければならない"
    )
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# R14 P1: 台帳 tmp パスの排他化（無関係な既存ファイルを truncate しない）
# ---------------------------------------------------------------------------


def test_save_ledger_does_not_clobber_unrelated_preexisting_tmp_file(tmp_path: Path) -> None:
    """R14 P1 の再現: `<ledger>.tmp` という決定論的パスに無関係な既存
    ファイル（例: `out/user_donor_ledger.json.tmp`）が既にあると、旧実装は
    それを `open("w")` で黙って truncate し、`replace()` でそのファイル名
    ごと消してしまっていた。修正後は `tempfile.mkstemp` の排他生成一意
    パスを使うため、この無関係ファイルは一切変更されない。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ledger_path = out_dir / "user_donor_ledger.json"
    unrelated_tmp = out_dir / "user_donor_ledger.json.tmp"
    unrelated_bytes = b"precious unrelated data, not a ledger tmp file"
    unrelated_tmp.write_bytes(unrelated_bytes)

    intake.save_ledger(ledger_path, {"schema": intake.LEDGER_SCHEMA, "entries": []})

    assert unrelated_tmp.exists(), "無関係な既存ファイルが消えてはならない"
    assert unrelated_tmp.read_bytes() == unrelated_bytes
    assert intake.load_ledger(ledger_path)["entries"] == []


def test_restore_ledger_does_not_clobber_unrelated_preexisting_tmp_file(tmp_path: Path) -> None:
    """`_restore_ledger` も `save_ledger` と同じ `_atomic_write` を使うため、
    同種の無関係 tmp ファイルを保護する。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ledger_path = out_dir / "user_donor_ledger.json"
    ledger_path.write_bytes(b'{"schema": "user-donor-ledger/0.1", "entries": []}')
    previous_bytes = ledger_path.read_bytes()

    unrelated_tmp = out_dir / "user_donor_ledger.json.tmp"
    unrelated_bytes = b"precious unrelated data"
    unrelated_tmp.write_bytes(unrelated_bytes)

    intake._restore_ledger(ledger_path, previous_bytes)

    assert unrelated_tmp.read_bytes() == unrelated_bytes
    assert ledger_path.read_bytes() == previous_bytes


def test_atomic_write_removes_tmp_file_on_writer_failure(tmp_path: Path) -> None:
    """書き込み中に失敗した場合、tmp ファイルを残さない（fail-closed のクリーン
    アップ）。"""
    target = tmp_path / "target.json"

    def _boom(tmp_write_path: Path) -> None:
        tmp_write_path.write_text("partial", encoding="utf-8")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        intake._atomic_write(target, _boom)

    assert not target.exists()
    leftover = list(tmp_path.iterdir())
    assert leftover == [], "書き込み失敗時は tmp ファイルを残してはならない"


def test_run_does_not_clobber_unrelated_ledger_tmp_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14 P1 の再現（レビュー指摘の実例そのもの）: `out/user_donor_ledger.json`
    の隣に無関係な `out/user_donor_ledger.json.tmp` が既に存在するバッチを
    公開しても、その無関係ファイルは無傷のまま残る。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"a")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ledger_path = out_dir / "user_donor_ledger.json"
    unrelated_tmp = out_dir / "user_donor_ledger.json.tmp"
    unrelated_bytes = b"unrelated pre-existing file"
    unrelated_tmp.write_bytes(unrelated_bytes)

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    assert len(entries) == 1
    assert unrelated_tmp.read_bytes() == unrelated_bytes
    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 1


# ---------------------------------------------------------------------------
# R14 P1: スナップショットの専用サブディレクトリ分離
# ---------------------------------------------------------------------------


def test_run_resolves_snapshot_and_staged_output_namespace_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14 P1 の再現（レビュー指摘の再現ケースそのもの）: 入力
    `__src_snapshot__z.wav`（1件目、name 順で先に処理される）の派生出力名は
    `__src_snapshot__z.norm24k.wav`。旧実装（`staging_dir/__src_snapshot__
    {元ファイル名}` プレフィクス方式）ではこれが 2 件目 `z.norm24k.wav` の
    スナップショットパスと一致し、2 件目のスナップショット書き込み
    （`write_bytes()`）が 1 件目の正規化済み出力を ledger hash 計算後に
    上書きしてしまっていた（公開されるバイト列と台帳の sha256 が食い違う
    ミスラベル公開）。修正後はスナップショットが `src_snapshots/` サブ
    ディレクトリへ分離されるため、この衝突は構造的に起こらない。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "__src_snapshot__z.wav").write_bytes(b"first donor bytes")
    (incoming_dir / "z.norm24k.wav").write_bytes(b"second donor bytes")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    entries = intake.run(incoming_dir, out_dir, ledger_path)

    assert len(entries) == 2
    published_files = sorted(out_dir.iterdir())
    assert len(published_files) == 2, "2 件とも別々のファイルとして公開されていること"

    # 公開されたファイルの実バイト列が台帳の sha256 と食い違っていないこと
    # （旧実装ではここで 1 件目の公開ファイルが破損値になり不一致が起きていた）。
    for entry in entries:
        published = Path(entry.normalized_path)
        assert published.exists()
        assert intake.sha256_of(published) == entry.sha256, (
            f"{published} の実バイト列が台帳の sha256 と食い違っている"
            "（スナップショット/staged 出力の名前空間衝突によるミスラベル公開）"
        )


# ---------------------------------------------------------------------------
# R16 P1: 公開直前の出力パス最終検証（ディレクトリ/symlink 経由の事故防止）
# ---------------------------------------------------------------------------


def test_check_publish_path_rejects_existing_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "UC-001.norm24k.wav"
    final_path.mkdir()

    with pytest.raises(intake.OutputPathCollisionError):
        intake._check_publish_path(final_path, out_dir)


def test_check_publish_path_rejects_existing_symlink_to_external_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    external = tmp_path / "external_secret"
    external.mkdir()
    final_path = out_dir / "UC-001.norm24k.wav"
    final_path.symlink_to(external, target_is_directory=True)

    with pytest.raises(intake.OutputPathCollisionError):
        intake._check_publish_path(final_path, out_dir)


def test_check_publish_path_rejects_parent_outside_out_dir(tmp_path: Path) -> None:
    """`final_path.parent` が `out_dir` の resolve 済みパスと一致しない場合も
    拒否する（`out_dir` 自体が symlink 経由になっているケース等への備え）。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    final_path = other_dir / "UC-001.norm24k.wav"

    with pytest.raises(intake.OutputPathCollisionError):
        intake._check_publish_path(final_path, out_dir)


def test_check_publish_path_allows_new_file(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "UC-001.norm24k.wav"

    intake._check_publish_path(final_path, out_dir)  # 例外を送出しないこと


def test_run_rejects_publish_when_out_dir_has_colliding_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R16 P1 の再現（レビュー指摘の再現ケース ①）: `out_dir` に既に同名の
    ディレクトリが存在する状態で公開しようとするケースの全体挙動を検証する。
    `assign_normalized_filenames`（予約段階の修正）を意図的にバイパスし、
    公開直前の最終防御（`_check_publish_path`）単独でも事故を防げることを
    確認する（予約漏れ・TOCTOU に対する多層防御の検証）。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    donor_original = incoming_dir / "UC-001.wav"
    donor_original.write_bytes(b"precious original bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    colliding_dir = out_dir / "UC-001.norm24k.wav"
    colliding_dir.mkdir()
    (colliding_dir / "sentinel.txt").write_bytes(b"pre-existing directory contents")

    def _assign_without_reservation_fix(inputs: List[Path], out_dir_arg: Path) -> Dict[Path, str]:
        return {src: f"{src.stem}.norm24k.wav" for src in inputs}

    monkeypatch.setattr(intake, "assign_normalized_filenames", _assign_without_reservation_fix)

    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(intake.OutputPathCollisionError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert donor_original.read_bytes() == b"precious original bytes", (
        "原本 incoming ファイルは一切変更されてはならない"
    )
    assert colliding_dir.is_dir()
    assert [p.name for p in colliding_dir.iterdir()] == ["sentinel.txt"], (
        "既存ディレクトリの中身が変わってはならない（wav がその中へ移動されない）"
    )
    assert not ledger_path.exists()


def test_run_rejects_publish_when_out_dir_has_colliding_symlink_to_external_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R16 P1 の再現（レビュー指摘の再現ケース ②）: `out_dir` に同名の
    symlink ディレクトリ（外部を指す）が存在する場合、`shutil.move` は
    その中へファイルを移動し `out_dir` の外側へ書き込んでしまう。最終防御が
    これを拒否し、外部ディレクトリには何も書き込まれないことを検証する。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    donor_original = incoming_dir / "UC-001.wav"
    donor_original.write_bytes(b"precious original bytes")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    external_dir = tmp_path / "external_outside_out_dir"
    external_dir.mkdir()
    colliding_symlink = out_dir / "UC-001.norm24k.wav"
    colliding_symlink.symlink_to(external_dir, target_is_directory=True)

    def _assign_without_reservation_fix(inputs: List[Path], out_dir_arg: Path) -> Dict[Path, str]:
        return {src: f"{src.stem}.norm24k.wav" for src in inputs}

    monkeypatch.setattr(intake, "assign_normalized_filenames", _assign_without_reservation_fix)

    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(intake.OutputPathCollisionError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert donor_original.read_bytes() == b"precious original bytes", (
        "原本 incoming ファイルは一切変更されてはならない"
    )
    assert list(external_dir.iterdir()) == [], (
        "out_dir の外側（symlink の指す先）へは何も書き込まれてはならない"
    )
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


# ---------------------------------------------------------------------------
# R17 P2 (intake.py:567): source_sha256 重複の preflight fail-closed 拒否
# ---------------------------------------------------------------------------


def test_run_rejects_duplicate_against_existing_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存台帳に同一 `source_sha256` のエントリが既にある状態で、同じバイト
    列を別ファイル名で再度取り込もうとすると fail-closed 拒否する
    （incoming をクリアせずに再実行した場合や、同じ収録が別名で 2 度届く
    ケースの二重計上を防止）。
    """
    _fake_normalize_to_wav(monkeypatch)

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    first_incoming = tmp_path / "incoming1"
    first_incoming.mkdir()
    (first_incoming / "UC-001.wav").write_bytes(b"identical donor bytes")
    intake.run(first_incoming, out_dir, ledger_path)

    ledger_before = ledger_path.read_bytes()
    published_before = sorted(p.name for p in out_dir.iterdir())

    second_incoming = tmp_path / "incoming2"
    second_incoming.mkdir()
    # 別ファイル名だが第1バッチと完全に同じバイト列（重複送付を再現）。
    (second_incoming / "UC-001_resend.wav").write_bytes(b"identical donor bytes")

    with pytest.raises(intake.DuplicateSourceError):
        intake.run(second_incoming, out_dir, ledger_path)

    # 部分公開はしない: 台帳・公開済みファイル一覧は第2バッチ実行前後で不変。
    assert ledger_path.read_bytes() == ledger_before
    assert sorted(p.name for p in out_dir.iterdir()) == published_before


def test_run_rejects_duplicate_within_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一バッチ内に完全に同じバイト列のファイルが2件届いた場合も
    fail-closed 拒否する（既存台帳側に重複が無くても検出する）。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"same bytes twice")
    (incoming_dir / "UC-002.wav").write_bytes(b"same bytes twice")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(intake.DuplicateSourceError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists()
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_run_allows_genuine_retake_with_different_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じカードの再録（バイト列が異なる = 別テイク）は `source_sha256` が
    一致しないため、重複拒否の影響を受けず正常に公開される（積み立て運用の
    「同カードの再録は正常系」を壊さないことの回帰ガード）。
    """
    _fake_normalize_to_wav(monkeypatch)

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    first_incoming = tmp_path / "incoming1"
    first_incoming.mkdir()
    (first_incoming / "UC-001.wav").write_bytes(b"take one bytes")
    intake.run(first_incoming, out_dir, ledger_path)

    second_incoming = tmp_path / "incoming2"
    second_incoming.mkdir()
    (second_incoming / "UC-001_take2.wav").write_bytes(b"take two bytes, genuinely different")

    entries = intake.run(second_incoming, out_dir, ledger_path)

    assert len(entries) == 1
    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 2
    source_hashes = {e["source_sha256"] for e in ledger["entries"]}
    assert len(source_hashes) == 2


# ---------------------------------------------------------------------------
# R21 P1: 並行 intake の直列化（`<ledger>.lock` への flock）
# ---------------------------------------------------------------------------


def test_run_rejects_when_ledger_lock_already_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`<ledger>.lock` を別プロセス相当（先行 `flock` 取得）が保持している
    状態で `run()` を呼ぶと、待ち合わせず `LedgerLockError` で即座に
    fail-closed 拒否する（R21 P1 の再現）。拒否時は台帳・out_dir のいずれ
    にも痕跡を残さない。ロック解放後は同じ入力で正常に成功する。
    """
    _fake_normalize_to_wav(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"lock contention donor bytes")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    lock_path = intake._ledger_lock_path(ledger_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 実際の OS レベル flock を先行取得することで「別プロセスが実行中」を
    # 再現する（flock は open file description 単位の排他制御のため、同一
    # プロセス内の別 open() でも競合が成立する）。
    held_lock_file = open(lock_path, "a+", encoding="utf-8")
    intake.fcntl.flock(held_lock_file, intake.fcntl.LOCK_EX | intake.fcntl.LOCK_NB)
    try:
        with pytest.raises(intake.LedgerLockError):
            intake.run(incoming_dir, out_dir, ledger_path)

        assert not ledger_path.exists()
        assert not out_dir.exists() or list(out_dir.iterdir()) == []
    finally:
        intake.fcntl.flock(held_lock_file, intake.fcntl.LOCK_UN)
        held_lock_file.close()

    # ロック解放後は待ち合わせなしで正常に成功する。
    entries = intake.run(incoming_dir, out_dir, ledger_path)
    assert len(entries) == 1
    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 1


def test_run_leaves_lock_file_in_place_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功後も `<ledger>.lock` は削除されず空ファイルとして残置される
    （削除すると unlink 直後・別プロセスの open+flock 直後という窓で
    二重ロックが成立し得るための設計。R21 P1）。残置したロックファイルが
    2 回目のバッチ実行（同一 out_dir/ledger 配置）を妨げないことも確認する。
    """
    _fake_normalize_to_wav(monkeypatch)

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    first_incoming = tmp_path / "incoming1"
    first_incoming.mkdir()
    (first_incoming / "UC-001.wav").write_bytes(b"first lock persistence bytes")
    intake.run(first_incoming, out_dir, ledger_path)

    lock_path = intake._ledger_lock_path(ledger_path)
    assert lock_path.exists()
    assert lock_path.stat().st_size == 0

    second_incoming = tmp_path / "incoming2"
    second_incoming.mkdir()
    (second_incoming / "UC-002.wav").write_bytes(b"second lock persistence bytes")
    entries = intake.run(second_incoming, out_dir, ledger_path)

    assert len(entries) == 1
    assert lock_path.exists()
    ledger = intake.load_ledger(ledger_path)
    assert len(ledger["entries"]) == 2


def test_check_ledger_path_collisions_ignores_own_reserved_lock_file(
    tmp_path: Path,
) -> None:
    """append ワークフロー（`--ledger` が `out_dir` 内）で残置された
    `<ledger>.lock` が、`out_dir` 内の「公開済み既存ファイル」走査に
    引っかからないことを直接検証する（R21 P1）。`lock_path` を渡さない
    （旧来の呼び出し形）場合の後方互換も併せて確認する。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ledger_path = out_dir / "user_donor_ledger.json"
    intake.save_ledger(ledger_path, {"schema": intake.LEDGER_SCHEMA, "entries": []})
    lock_path = intake._ledger_lock_path(ledger_path)
    lock_path.write_bytes(b"")  # R21 P1: run() が残置する空ファイルを模擬

    src = tmp_path / "incoming" / "UC-001.wav"
    src.parent.mkdir()
    _write_fake_source(src, seed=1)
    filenames = {src: "UC-001.norm24k.wav"}
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    # lock_path を渡すと、残置ロックファイルは予約済み扱いで除外される。
    intake._check_ledger_path_collisions(
        ledger_path, [src], filenames, out_dir, staging_dir, lock_path=lock_path
    )

    # lock_path 省略時（旧来の呼び出し）でも、ロックファイルはただの
    # `out_dir` 内既存ファイルとして扱われるが、resolved_ledger との比較は
    # ledger_path 自身にしか一致しないため、この場合も衝突しない
    # （ロックファイル名 != 台帳ファイル名）。
    intake._check_ledger_path_collisions(ledger_path, [src], filenames, out_dir, staging_dir)


def test_check_ledger_path_collisions_rejects_lock_path_matching_incoming_source(
    tmp_path: Path,
) -> None:
    """`lock_path` が incoming の元音源ファイルと衝突する場合も、`--ledger`
    自身と同様に fail-closed 拒否する（R21 P1）。
    """
    out_dir = tmp_path / "out"
    src = tmp_path / "incoming" / "UC-001.wav"
    src.parent.mkdir()
    _write_fake_source(src, seed=1)
    filenames = {src: "UC-001.norm24k.wav"}
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(intake.LedgerPathCollisionError):
        intake._check_ledger_path_collisions(
            ledger_path, [src], filenames, out_dir, staging_dir, lock_path=src
        )


# ---------------------------------------------------------------------------
# R21 P2: 非正 duration（ヘッダのみ WAV 等）の拒否
# ---------------------------------------------------------------------------


def _fake_normalize_to_wav_header_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """`normalize_to_wav` を、入力に関わらずヘッダのみ（フレーム数 0）の
    wav を書き出す偽実装へ差し替える（ffmpeg が exit 0 でもフレームを
    一切書き出さなかったケースの再現。R21 P2）。
    """

    def _fake(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dst), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(intake.TARGET_SAMPLE_RATE)
            f.writeframes(b"")

    monkeypatch.setattr(intake, "normalize_to_wav", _fake)


def test_measure_loudness_returns_zero_duration_for_header_only_wav(tmp_path: Path) -> None:
    """`measure_loudness()` 単体の回帰ガード: ヘッダのみ wav は
    `duration_sec == 0.0` を返す（`process_one` の fail-closed 拒否の前提）。
    """
    wav_path = tmp_path / "header_only.wav"
    with wave.open(str(wav_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(intake.TARGET_SAMPLE_RATE)
        f.writeframes(b"")

    duration_sec, rms_dbfs, peak_dbfs = intake.measure_loudness(wav_path)

    assert duration_sec == 0.0
    assert rms_dbfs is None
    assert peak_dbfs is None


def test_process_one_rejects_header_only_normalized_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`process_one()` 単体: 正規化後 wav がヘッダのみ（フレーム数 0）の
    場合、台帳エントリを構築せず `NonPositiveDurationError` で fail-closed
    拒否する（R21 P2 の再現）。
    """
    _fake_normalize_to_wav_header_only(monkeypatch)

    src = tmp_path / "incoming" / "UC-001.wav"
    src.parent.mkdir()
    _write_fake_source(src, seed=1)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    out_dir = tmp_path / "out"

    with pytest.raises(intake.NonPositiveDurationError):
        intake.process_one(src, staging_dir, "UC-001.norm24k.wav", out_dir)


def test_run_rejects_header_only_normalized_wav_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run()` E2E: ヘッダのみ wav を生む incoming ファイルはバッチ全体を
    fail-closed 拒否し、台帳・out_dir のいずれにも記録・公開されない
    （R21 P2 の再現）。
    """
    _fake_normalize_to_wav_header_only(monkeypatch)

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "UC-001.wav").write_bytes(b"donor bytes yielding zero-frame output")

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "user_donor_ledger.json"

    with pytest.raises(intake.NonPositiveDurationError):
        intake.run(incoming_dir, out_dir, ledger_path)

    assert not ledger_path.exists()
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
