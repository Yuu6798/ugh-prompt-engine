"""test_practice_split_builder.py — RUN9-BIRTH-PREP-1 §B:
`practice_split_builder.py`（PRACTICE_FROM_AUDIO split manifest builder +
advisory 音響 inventory sidecar）の最低テスト。

fixture は tmp_path の合成ミニコーパス（tiny wav = 数百サンプルの正弦波 +
対応する .lab）のみを用いる。**実 PJS 音源・実 sha は repo へ追加しない**。

音声処理（librosa.pyin 呼び出し）を伴う sidecar テストのみ低速の可能性が
あるが、tiny wav かつ tmp_path 単発のため slow マーカーは不要。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import List

import numpy as np
import pytest
import soundfile as sf

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import practice_split_builder as psb  # noqa: E402
import run9_schema as m  # noqa: E402

_SR = 24000
_LAB_TEXT = (
    "0 500000 pau\n"
    "500000 1000000 a\n"
    "1000000 1500000 k\n"
    "1500000 2000000 i\n"
    "2000000 2500000 pau\n"
)


def _write_song(corpus_root: Path, song_id: str, *, n_samples: int = 512, freq_hz: float = 220.0) -> None:
    """1曲分の `pjsNNN/pjsNNN.lab` + `pjsNNN/pjsNNN_song.wav`（tiny sine）を
    書き出す。"""
    song_dir = corpus_root / song_id
    song_dir.mkdir(parents=True, exist_ok=True)
    t = np.arange(n_samples, dtype=np.float64) / _SR
    y = 0.1 * np.sin(2.0 * np.pi * freq_hz * t)
    sf.write(song_dir / f"{song_id}_song.wav", y, _SR)
    (song_dir / f"{song_id}.lab").write_text(_LAB_TEXT, encoding="utf-8")


def _build_corpus(tmp_path: Path, song_ids: List[str], *, name: str = "corpus") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for song_id in song_ids:
        _write_song(root, song_id)
    return root


def _reference_score(song_id: str) -> str:
    """裁定逐語の score() を独立に再現する参照実装
    （`practice_split_builder._song_score()` を呼ばず、テスト自身で
    `hashlib.sha256` から再計算する）。"""
    return hashlib.sha256(f"{song_id}|{m.LEARNING_SEED}".encode("utf-8")).hexdigest()


def _reference_assign(song_ids: List[str]) -> dict:
    n = len(song_ids)
    n_val = n * 15 // 100
    n_holdout = n * 15 // 100
    n_train = n - n_val - n_holdout
    ranked = sorted(song_ids, key=lambda sid: (_reference_score(sid), sid))
    return {
        "training": ranked[:n_train],
        "validation": ranked[n_train : n_train + n_val],
        "sealed_holdout": ranked[n_train + n_val :],
        "row_order": ranked,
    }


# ---------------------------------------------------------------------------
# assign_split(): 純関数の件数境界・割当固定
# ---------------------------------------------------------------------------


def test_assign_split_n100_is_exactly_70_15_15() -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 101)]
    split = psb.assign_split(song_ids)
    assert len(split["training"]) == 70
    assert len(split["validation"]) == 15
    assert len(split["sealed_holdout"]) == 15
    assert len(split["row_order"]) == 100


def test_assign_split_n7_all_splits_nonempty() -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 8)]
    split = psb.assign_split(song_ids)
    assert len(split["training"]) > 0
    assert len(split["validation"]) > 0
    assert len(split["sealed_holdout"]) > 0
    assert len(split["training"]) + len(split["validation"]) + len(split["sealed_holdout"]) == 7


def test_assign_split_n6_fail_closed() -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 7)]
    with pytest.raises(m.Run9ValidationError):
        psb.assign_split(song_ids)


def test_assign_split_empty_fail_closed() -> None:
    with pytest.raises(m.Run9ValidationError):
        psb.assign_split([])


def test_assign_split_rejects_duplicate_song_ids() -> None:
    with pytest.raises(m.Run9ValidationError):
        psb.assign_split(["pjs001", "pjs002", "pjs001", "pjs003", "pjs004", "pjs005", "pjs006"])


def test_assign_split_matches_independent_reference_implementation() -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 51)]
    expected = _reference_assign(song_ids)
    actual = psb.assign_split(song_ids)
    assert actual["training"] == expected["training"]
    assert actual["validation"] == expected["validation"]
    assert actual["sealed_holdout"] == expected["sealed_holdout"]
    assert actual["row_order"] == expected["row_order"]


def test_assign_split_is_deterministic_and_order_independent_of_input_order() -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 21)]
    a = psb.assign_split(song_ids)
    b = psb.assign_split(list(reversed(song_ids)))
    assert a == b


# ---------------------------------------------------------------------------
# build_practice_split_manifest(): 決定論 + 列挙順（filesystem 順序）非依存
# ---------------------------------------------------------------------------


def _identity_and_manifest(root: Path):
    identity_hash, song_ids = psb._enumerate_pjs_song_ids(root)  # noqa: SLF001 - test-internal
    manifest = psb.build_practice_split_manifest(root, expected_corpus_identity=identity_hash)
    return identity_hash, manifest


def test_manifest_bytes_identical_across_two_generations(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    identity_hash, manifest1 = _identity_and_manifest(root)
    manifest2 = psb.build_practice_split_manifest(root, expected_corpus_identity=identity_hash)
    assert psb.dump_practice_split_manifest_bytes(manifest1) == psb.dump_practice_split_manifest_bytes(
        manifest2
    )


def test_manifest_bytes_identical_regardless_of_file_creation_order(tmp_path: Path) -> None:
    """列挙順シャッフル入力: `os.listdir` モックではなく、ファイル**作成順**を
    変えた2つの同内容 fixture を用意して比較する（filesystem 順序非依存の
    確認）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root_forward = _build_corpus(tmp_path, song_ids, name="corpus_forward")
    root_reverse = _build_corpus(tmp_path, list(reversed(song_ids)), name="corpus_reverse")

    identity_forward, manifest_forward = _identity_and_manifest(root_forward)
    identity_reverse, manifest_reverse = _identity_and_manifest(root_reverse)

    assert identity_forward == identity_reverse
    # row_ids/row_order 自体（ファイル内容由来のハッシュ群を除く）が完全一致することを確認する。
    assert manifest_forward["row_ids"] == manifest_reverse["row_ids"]
    assert manifest_forward["sample_inventory"] == manifest_reverse["sample_inventory"]
    assert manifest_forward["row_order_sha256"] == manifest_reverse["row_order_sha256"]


def test_build_practice_split_manifest_passes_validator(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 12)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    m.validate_practice_split_manifest(manifest)  # 例外を投げないことの確認


def test_build_practice_split_manifest_row_ids_nonempty_and_disjoint(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 12)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    row_ids = manifest["row_ids"]
    training, validation, sealed_holdout = (
        set(row_ids["training"]),
        set(row_ids["validation"]),
        set(row_ids["sealed_holdout"]),
    )
    assert training and validation and sealed_holdout
    assert not (training & validation)
    assert not (training & sealed_holdout)
    assert not (validation & sealed_holdout)
    assert training | validation | sealed_holdout == set(song_ids)


def test_build_practice_split_manifest_excludes_speech_and_background_wav(tmp_path: Path) -> None:
    """`_song.wav` 以外の付随音声（speech/background）が song_id 列挙・
    corpus identity へ混入しないことを確認する。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    # 被覆対象外の付随ファイルを追加する。
    (root / "pjs001" / "pjs001_speech.wav").write_bytes(b"not a real wav, must not be enumerated")
    (root / "pjs001" / "pjs001_background.wav").write_bytes(b"not a real wav, must not be enumerated")

    root_clean = tmp_path / "corpus_clean"
    root_clean.mkdir()
    for song_id in song_ids:
        _write_song(root_clean, song_id)

    identity_with_extras, song_ids_with_extras = psb._enumerate_pjs_song_ids(root)  # noqa: SLF001
    identity_clean, song_ids_clean = psb._enumerate_pjs_song_ids(root_clean)  # noqa: SLF001

    assert identity_with_extras == identity_clean
    assert song_ids_with_extras == song_ids_clean
    assert set(song_ids_with_extras) == set(song_ids)


def test_build_practice_split_manifest_rejects_corpus_identity_mismatch(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    with pytest.raises(m.Run9ValidationError, match="corpus identity mismatch"):
        psb.build_practice_split_manifest(root, expected_corpus_identity="a" * 64)


def test_build_practice_split_manifest_requires_expected_corpus_identity(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    with pytest.raises(TypeError):
        psb.build_practice_split_manifest(root)  # type: ignore[call-arg]
    with pytest.raises(m.Run9ValidationError):
        psb.build_practice_split_manifest(root, expected_corpus_identity="")


def test_build_practice_split_manifest_fails_closed_on_too_few_songs(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 7)]  # N=6
    root = _build_corpus(tmp_path, song_ids)
    identity_hash, _ = psb._enumerate_pjs_song_ids(root)  # noqa: SLF001
    with pytest.raises(m.Run9ValidationError):
        psb.build_practice_split_manifest(root, expected_corpus_identity=identity_hash)


def test_expanded_corpus_identity_matches_pinned_contract_value() -> None:
    """モジュール定数 `EXPANDED_CORPUS_IDENTITY_SHA256` が
    `RUN9_CONTRACT.yaml`/`inputs/rights_manifest.json` の既 pin 値
    （9905cec0...）と一致する（転記ミス防止の対照確認）。"""
    assert (
        psb.EXPANDED_CORPUS_IDENTITY_SHA256
        == "9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996"
    )
    assert (
        psb.PJS_SOURCE_ARCHIVE_SHA256
        == "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca"
    )


# ---------------------------------------------------------------------------
# sidecar 不干渉: sidecar 生成の有無・内容差で manifest バイト不変
# ---------------------------------------------------------------------------


def test_sidecar_generation_does_not_change_manifest_bytes(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    before = psb.dump_practice_split_manifest_bytes(manifest)

    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)

    after = psb.dump_practice_split_manifest_bytes(manifest)
    assert before == after
    assert sidecar["schema"] == psb.SCHEMA_PRACTICE_ACOUSTIC_INVENTORY_SIDECAR
    assert len(sidecar["songs"]) == len(song_ids)


def test_sidecar_generation_with_missing_audio_still_leaves_manifest_unchanged(tmp_path: Path) -> None:
    """sidecar 側の測定失敗（ここでは音声ファイルを事後削除して pyin 対象
    自体を消す）でも manifest バイトは無変化のまま——sidecar のデータフローが
    manifest へ逆流しないことの追加確認。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    before = psb.dump_practice_split_manifest_bytes(manifest)

    # 1曲分の音声を消し、sidecar 測定を意図的に失敗させる。
    (root / "pjs001" / "pjs001_song.wav").unlink()

    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    after = psb.dump_practice_split_manifest_bytes(manifest)
    assert before == after
    entry = next(s for s in sidecar["songs"] if s["song_id"] == "pjs001")
    assert entry["pitch_range_hz"] is None


def test_sidecar_note_declares_advisory_and_no_calibration_claim(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    assert "advisory" in sidecar["note"].lower()


def test_sidecar_rejects_manifest_that_fails_validation(tmp_path: Path) -> None:
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    broken = dict(manifest)
    del broken["row_ids"]
    with pytest.raises(m.Run9ValidationError):
        psb.build_acoustic_inventory_sidecar(root, broken)


# ---------------------------------------------------------------------------
# build_acoustic_inventory_sidecar(): row_ids の song_id を corpus_root 配下
# へ拘束する（PR #321 review 指摘・P2 修正: `manifest` は
# `validate_practice_split_manifest()` を通過する任意の同形 dict を受理
# する外部入力であり、`row_ids` の song_id 形式は schema 側で保証されない）
# ---------------------------------------------------------------------------


def _manifest_with_training_song_id(base_manifest: dict, malicious_song_id: str) -> dict:
    """`base_manifest`（`validate_practice_split_manifest()` を通過する
    正常 manifest）の `row_ids.training` 先頭 1 件を `malicious_song_id`
    に差し替えた manifest を返す。差し替え後も `row_ids` は非空文字列
    リスト・split 間排他という schema 制約を満たし続けるため
    `validate_practice_split_manifest()` はそのまま通過する——本テストが
    再現する脅威モデルそのもの（song_id の *形式* は schema 側で検証
    されない）。"""
    manifest = dict(base_manifest)
    row_ids = {k: list(v) for k, v in manifest["row_ids"].items()}
    row_ids["training"] = [malicious_song_id, *row_ids["training"][1:]]
    manifest["row_ids"] = row_ids
    return manifest


@pytest.mark.parametrize(
    "malicious_song_id_factory",
    [
        pytest.param(lambda outside_dir: str(outside_dir / "evil"), id="absolute_path"),
        pytest.param(lambda outside_dir: "../escape", id="parent_traversal"),
        pytest.param(lambda outside_dir: "nested/song", id="path_separator"),
    ],
)
def test_sidecar_rejects_song_id_escaping_corpus_root_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malicious_song_id_factory
) -> None:
    """絶対パス・`../` 親ディレクトリ脱出・パス区切りを含む `song_id` は
    read/existence 判定より前に fail-closed で拒否される（レビュー指摘の
    `song_id='/tmp/evil'` → `/tmp/evil.lab` と同型の脱出）。corpus_root 外
    に実在する fixture（本物の .lab/.wav）を置いた上で、計測関数
    （`_measure_phoneme_classes`/`_measure_pitch_range_hz`）が一切呼ばれ
    ないことをスパイで直接検証する——ファイルが実在しても読まれずに
    拒否されることの直接証拠。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)

    # corpus_root 外に、脱出が成功すれば読まれてしまう本物の fixture を置く。
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    _write_song(outside_dir, "evil")
    # "../escape" 系の脱出先（root の親 = tmp_path 直下）にも fixture を置く。
    _write_song(tmp_path, "escape")

    malicious_song_id = malicious_song_id_factory(outside_dir)
    malicious_manifest = _manifest_with_training_song_id(manifest, malicious_song_id)
    m.validate_practice_split_manifest(malicious_manifest)  # 前提: schema は素通りする

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            f"measurement function must not be called for escaping song_id={malicious_song_id!r}"
        )

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _must_not_be_called)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _must_not_be_called)

    with pytest.raises(m.Run9ValidationError, match="escape corpus_root"):
        psb.build_acoustic_inventory_sidecar(root, malicious_manifest)


def test_sidecar_accepts_normal_corpus_derived_song_ids_regression(tmp_path: Path) -> None:
    """corpus enumeration 由来の正常な song_id（`pjsNNN`）は
    `_reject_song_ids_outside_corpus_root()` を通過し、sidecar が
    通常どおり構築できることの正常系回帰。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    assert {entry["song_id"] for entry in sidecar["songs"]} == set(song_ids)


def test_song_id_is_lexically_safe_rejects_unsafe_forms() -> None:
    assert psb._song_id_is_lexically_safe("pjs001") is True  # noqa: SLF001
    for unsafe in ("/tmp/evil", "../escape", "nested/song", "nested\\song", "", ".", ".."):
        assert psb._song_id_is_lexically_safe(unsafe) is False  # noqa: SLF001
