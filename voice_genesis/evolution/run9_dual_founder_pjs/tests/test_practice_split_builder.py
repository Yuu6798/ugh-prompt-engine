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
    """PR #321 review 第5巡 Fix 6（P1・sealed-holdout 非開封）以降、
    sidecar の計測対象は training/validation のみ——`sealed_holdout` の
    song は sidecar["songs"] に一切現れない。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    before = psb.dump_practice_split_manifest_bytes(manifest)

    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)

    after = psb.dump_practice_split_manifest_bytes(manifest)
    assert before == after
    assert sidecar["schema"] == psb.SCHEMA_PRACTICE_ACOUSTIC_INVENTORY_SIDECAR
    expected_measured_ids = set(manifest["row_ids"]["training"]) | set(manifest["row_ids"]["validation"])
    assert len(sidecar["songs"]) == len(expected_measured_ids)
    assert {entry["song_id"] for entry in sidecar["songs"]} == expected_measured_ids


def test_sidecar_generation_with_corrupt_audio_still_leaves_manifest_unchanged(tmp_path: Path) -> None:
    """sidecar 側の測定失敗（ここでは音声ファイルを壊れたバイト列に差し替えて
    pyin 対象自体を壊す）でも manifest バイトは無変化のまま——sidecar の
    データフローが manifest へ逆流しないことの追加確認。

    ファイルは corpus_root に実在し続けたまま**中身だけ**壊す（事後削除では
    ない）——PR #321 review Fix 3 の corpus identity 検証
    （`_enumerate_pjs_song_ids()` 再計算 vs `manifest.expanded_corpus_
    identity_sha256`）は、識別子計算**前**に破損させて manifest 自体を
    その破損状態から構築することで通す。事後にファイルを削除/改変すると
    corpus identity が変わり Fix 3 が意図どおり fail-closed 拒否する
    （`test_sidecar_rejects_song_id_escaping_corpus_root_before_any_read`
    の脅威モデルと同型——別テストの対象）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    # 1曲分の音声を、識別子計算より前に壊れたバイト列へ差し替える
    # （ファイルは存在し続けるため corpus identity は以降不変）。
    (root / "pjs001" / "pjs001_song.wav").write_bytes(b"not a real wav, corrupt on purpose")

    _, manifest = _identity_and_manifest(root)
    before = psb.dump_practice_split_manifest_bytes(manifest)

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
    拒否されることの直接証拠。

    **PR #321 review Fix 4 追記**: `row_ids.training` を単独改変すると
    `_reject_song_ids_outside_corpus_root()`（Fix 1 の語彙的・包含検査層）
    より前段の split assignment 層（Fix 4: `row_ids` が corpus 由来の決定論
    割当と一致するか）が先に検知して拒否するようになった——検証する
    エラーメッセージも Fix 4 のものへ更新している。「read/existence 判定
    より前に、計測関数を一切呼ばず fail-closed 拒否される」という外部
    可観測な契約自体は不変（層が変わっただけで、拒否タイミング・計測
    未実行の保証は変わらない）。Fix 1 の語彙的判定ロジック自体は
    `test_song_id_is_lexically_safe_rejects_unsafe_forms()` が単体で直接
    検査済み（Fix 4 適用後は、単独 song_id 改変で Fix 1 層まで到達する
    経路が構造的に無くなったため——`row_ids` が corpus 由来の決定論割当と
    完全一致しない限り Fix 4 で先に拒否される）。"""
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

    with pytest.raises(m.Run9ValidationError, match="deterministic split assignment"):
        psb.build_acoustic_inventory_sidecar(root, malicious_manifest)


def test_sidecar_accepts_normal_corpus_derived_song_ids_regression(tmp_path: Path) -> None:
    """corpus enumeration 由来の正常な song_id（`pjsNNN`）は
    `_reject_song_ids_outside_corpus_root()` を通過し、sidecar が
    通常どおり構築できることの正常系回帰。PR #321 review 第5巡 Fix 6
    （sealed-holdout 非開封）以降、計測対象は training/validation の
    song_id のみ（`sealed_holdout` は含まれない）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    expected_measured_ids = set(manifest["row_ids"]["training"]) | set(manifest["row_ids"]["validation"])
    assert {entry["song_id"] for entry in sidecar["songs"]} == expected_measured_ids


def test_song_id_is_lexically_safe_rejects_unsafe_forms() -> None:
    assert psb._song_id_is_lexically_safe("pjs001") is True  # noqa: SLF001
    for unsafe in ("/tmp/evil", "../escape", "nested/song", "nested\\song", "", ".", ".."):
        assert psb._song_id_is_lexically_safe(unsafe) is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# build_acoustic_inventory_sidecar(): corpus_root 自体を manifest の
# expanded_corpus_identity_sha256 pin と照合する（PR #321 review 第2巡
# 指摘・P2 修正 Fix 3: corpus A 用の valid manifest に、同名 song_id を
# 含むが中身の異なる別 corpus_root B を渡すと、Fix 1 のガード（song_id の
# 形式検査のみ）は素通りし、B を計測して A の inventory として返して
# しまう——sidecar 自体に検証済み corpus digest も載らないため provenance
# が黙って失われる）
# ---------------------------------------------------------------------------


def test_sidecar_rejects_mismatched_corpus_root_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """corpus A 用の manifest に、同名 song_id を持つが中身の異なる
    corpus_root B を渡すと、song を読む前に corpus identity 再計算
    （`_enumerate_pjs_song_ids()`）で `Run9ValidationError` になること。
    計測関数（`_measure_phoneme_classes`/`_measure_pitch_range_hz`）が
    一切呼ばれないことをスパイで直接検証する（既存 Fix 1 テストと同じ
    流儀——B が実際に計測されて A の inventory として返らないことの
    直接証拠）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root_a = _build_corpus(tmp_path, song_ids, name="corpus_a")
    _, manifest_a = _identity_and_manifest(root_a)

    # 同名 song_id を持つが中身が異なる別コーパス B（周波数を変えて
    # バイト内容を相違させる——song_id 集合自体は A と同一)。
    root_b = tmp_path / "corpus_b"
    root_b.mkdir()
    for song_id in song_ids:
        _write_song(root_b, song_id, freq_hz=440.0)

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("measurement function must not be called for mismatched corpus_root")

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _must_not_be_called)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _must_not_be_called)

    with pytest.raises(m.Run9ValidationError, match="corpus identity mismatch"):
        psb.build_acoustic_inventory_sidecar(root_b, manifest_a)


def test_sidecar_records_verified_corpus_identity_on_matching_root(tmp_path: Path) -> None:
    """`corpus_root` が manifest の由来コーパスと一致する正常系では、
    sidecar 出力に `verified_expanded_corpus_identity_sha256`
    （再計算・照合済みの値）が manifest の `expanded_corpus_identity_sha256`
    と同一値で記録されること。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    identity_hash, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    assert sidecar["verified_expanded_corpus_identity_sha256"] == identity_hash
    assert sidecar["verified_expanded_corpus_identity_sha256"] == manifest["expanded_corpus_identity_sha256"]


def test_sidecar_generation_does_not_change_manifest_bytes_with_corpus_check(tmp_path: Path) -> None:
    """Fix 3 の corpus identity 検証を追加した後も、sidecar 生成が manifest
    バイトへ逆流しないという不干渉契約が維持されていることの直接回帰
    （既存 `test_sidecar_generation_does_not_change_manifest_bytes` と同じ
    確認を、`verified_expanded_corpus_identity_sha256` フィールド追加後の
    sidecar 形状に対しても行う）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    before = psb.dump_practice_split_manifest_bytes(manifest)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    after = psb.dump_practice_split_manifest_bytes(manifest)
    assert before == after
    assert "verified_expanded_corpus_identity_sha256" in sidecar


# ---------------------------------------------------------------------------
# build_acoustic_inventory_sidecar(): row_ids を corpus 由来の決定論割当と
# 照合する（PR #321 review 第3巡指摘・P2 修正 Fix 4: row_ids を改変（例:
# training と sealed_holdout の ID 入れ替え）しても宣言済み6 hash が形式
# 検証しか受けないため `validate_practice_split_manifest()` を通過してしま
# い、sidecar が偽造 split ラベル下で計測を記録して provenance を汚す）
# ---------------------------------------------------------------------------


def _manifest_with_swapped_training_and_sealed_holdout(base_manifest: dict) -> dict:
    """`row_ids.training[0]` と `row_ids.sealed_holdout[0]` を入れ替えた
    manifest を返す（宣言済み6 hash は据え置き——PR #321 review 第3巡
    Fix 4 が再現する脅威モデルそのもの: `validate_practice_split_manifest()`
    は hash 形式・非空・split 間排他しか見ないため、この入れ替えだけでは
    validate は通過する）。"""
    manifest = dict(base_manifest)
    row_ids = {k: list(v) for k, v in manifest["row_ids"].items()}
    row_ids["training"][0], row_ids["sealed_holdout"][0] = (
        row_ids["sealed_holdout"][0],
        row_ids["training"][0],
    )
    manifest["row_ids"] = row_ids
    return manifest


def _manifest_with_swapped_rows_and_reforged_hashes(base_manifest: dict) -> dict:
    """上記の row 入れ替えに加え、`training_split_sha256`/
    `validation_split_sha256`/`sealed_holdout_sha256`/`row_order_sha256`
    を偽造後の `row_ids` に対して**再計算**し、manifest 自身を自己整合
    させたもの（row_ids と6 hash を両方偽造して自己整合させるケース——
    真の決定論割当（corpus 由来の `assign_split()` 再計算）とは一致しない
    ため、hash 単独の再計算・照合では見抜けず、真の割当との比較でのみ
    検知できる）。"""
    manifest = _manifest_with_swapped_training_and_sealed_holdout(base_manifest)
    row_ids = manifest["row_ids"]
    manifest["training_split_sha256"] = psb._canonical_song_list_sha256(row_ids["training"])  # noqa: SLF001
    manifest["validation_split_sha256"] = psb._canonical_song_list_sha256(  # noqa: SLF001
        row_ids["validation"]
    )
    manifest["sealed_holdout_sha256"] = psb._canonical_song_list_sha256(  # noqa: SLF001
        row_ids["sealed_holdout"]
    )
    row_order = row_ids["training"] + row_ids["validation"] + row_ids["sealed_holdout"]
    manifest["row_order_sha256"] = psb._canonical_song_list_sha256(row_order)  # noqa: SLF001
    return manifest


def test_sidecar_rejects_swapped_row_ids_with_hashes_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """training↔sealed_holdout の ID 入れ替え（宣言済み6 hash は据え置き）
    は、計測前に fail-closed 拒否されること。計測関数が一切呼ばれない
    ことをスパイで直接検証する。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    forged = _manifest_with_swapped_training_and_sealed_holdout(manifest)
    m.validate_practice_split_manifest(forged)  # 前提: schema は素通りする

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("measurement function must not be called for swapped row_ids")

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _must_not_be_called)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _must_not_be_called)

    with pytest.raises(m.Run9ValidationError, match="deterministic split assignment"):
        psb.build_acoustic_inventory_sidecar(root, forged)


def test_sidecar_rejects_self_consistent_forged_split_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """row_ids と宣言済み6 hash の両方を偽造後の値へ揃えて自己整合させた
    manifest（真の決定論割当とは不一致）も、計測前に fail-closed 拒否
    されること。計測関数が一切呼ばれないことをスパイで直接検証する——
    hash 単独の自己整合性では防げず、corpus 由来の真の割当との比較が
    必須であることの直接証拠。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    forged = _manifest_with_swapped_rows_and_reforged_hashes(manifest)
    m.validate_practice_split_manifest(forged)  # 前提: schema は素通りする（自己整合済み）

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "measurement function must not be called for self-consistent forged manifest"
        )

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _must_not_be_called)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _must_not_be_called)

    with pytest.raises(m.Run9ValidationError):
        psb.build_acoustic_inventory_sidecar(root, forged)


def test_sidecar_accepts_unmodified_manifest_regression_fix4(tmp_path: Path) -> None:
    """未改変の正常 manifest は Fix 4 の split assignment / hash pin 検証を
    通過し、sidecar が通常どおり構築できることの正常系回帰。PR #321 review
    第5巡 Fix 6（sealed-holdout 非開封）以降、計測対象は training/
    validation の song_id のみ。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    expected_measured_ids = set(manifest["row_ids"]["training"]) | set(manifest["row_ids"]["validation"])
    assert {entry["song_id"] for entry in sidecar["songs"]} == expected_measured_ids


# ---------------------------------------------------------------------------
# build_acoustic_inventory_sidecar(): 最終消費ファイル（`root/song_id/
# song_id.lab` / `song_id_song.wav`）それぞれを resolve + corpus_root 配下
# 包含検査する（PR #321 review 第3巡指摘・P2 修正 Fix 5: Fix 1 は
# `root / song_id`（ディレクトリ）の resolve しかしておらず、最終消費
# ファイル自体が corpus_root 外を指す symlink である経路が残っていた——
# symlink 参照先がコーパス内の実体とバイト同一なら corpus identity
# （Fix 3）・split assignment/hash（Fix 4）はいずれも変化せず通過する）
# ---------------------------------------------------------------------------


def test_sidecar_rejects_final_path_symlink_escape_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enumeration 由来の正規 song_id（Fix 1/3/4 の全層を通過する）でも、
    その最終消費ファイル（`pjs001_song.wav`）自体が corpus_root 外を指す
    symlink であれば、read/existence 判定より前に resolve + 包含検査で
    fail-closed 拒否されること。symlink 参照先はコーパス内の実体と
    バイト同一のコピー——corpus identity・split assignment/hash はいずれも
    song_id 集合とファイル内容のみを見るため変化せず通過し、本 Fix 5 の
    最終パス層のみが検知することの直接証拠。計測関数が一切呼ばれないこと
    をスパイで直接検証する。

    このリポジトリの sandbox では `Path.symlink_to()` が動作することを
    事前確認済み（symlink 不可の環境では明示的な `OSError`/
    `NotImplementedError` として素通しで失敗させる方針——`pytest.skip` で
    黙って握り潰さない。PR #321 review 第3巡 Fix 5 裁定の指示どおり）。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)

    # symlink 対象を corpus_root 外に用意する（pjs001_song.wav とバイト
    # 同一のコピー——corpus identity に影響を与えないため）。
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()
    original_wav = root / "pjs001" / "pjs001_song.wav"
    outside_wav = outside_dir / "pjs001_song.wav"
    outside_wav.write_bytes(original_wav.read_bytes())

    # pjs001_song.wav 自体を、外部実体を指す symlink へ差し替える
    # （親ディレクトリ root/pjs001 は symlink 化しない — Fix 1/3 が既に
    # 塞いでいる経路とは別の、最終ファイル単体の symlink 経路を再現する）。
    original_wav.unlink()
    original_wav.symlink_to(outside_wav)

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("measurement function must not be called for symlinked final path")

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _must_not_be_called)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _must_not_be_called)

    with pytest.raises(m.Run9ValidationError, match="escape corpus_root"):
        psb.build_acoustic_inventory_sidecar(root, manifest)


def test_sidecar_accepts_non_symlinked_files_regression_fix5(tmp_path: Path) -> None:
    """symlink を伴わない通常の corpus では、Fix 5 の最終パス検査を通過し
    sidecar が通常どおり構築できることの正常系回帰。PR #321 review 第5巡
    Fix 6（sealed-holdout 非開封）以降、計測対象は training/validation の
    song のみ。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    expected_measured_ids = set(manifest["row_ids"]["training"]) | set(manifest["row_ids"]["validation"])
    assert len(sidecar["songs"]) == len(expected_measured_ids)
    for entry in sidecar["songs"]:
        assert entry["pitch_range_hz"] is not None


# ---------------------------------------------------------------------------
# build_acoustic_inventory_sidecar(): sealed_holdout を計測対象・最終パス
# read 対象から構造的に除外する（PR #321 review 第5巡指摘・P1 修正 Fix 6:
# DESIGN_RUN9_REVISION_0.3.md の学習中使用禁止規定・
# DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md §22 step 14
# （開封＝両 adapter 凍結後）に反し、通常の（学習前）sidecar 生成が
# sealed_holdout の `.lab`/WAV を読んで pitch/phrase/phoneme 観測値を
# 出力し、実験設計を汚染していた）
# ---------------------------------------------------------------------------


def test_sidecar_excludes_sealed_holdout_songs_and_marks_flag(tmp_path: Path) -> None:
    """sidecar 出力に `sealed_holdout` の song entry が1件も含まれず、
    `sealed_holdout_excluded` が `true` であること。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)

    holdout_ids = set(manifest["row_ids"]["sealed_holdout"])
    assert holdout_ids  # 前提: N=9 では sealed_holdout は非空（n_holdout=1）

    measured_ids = {entry["song_id"] for entry in sidecar["songs"]}
    assert not (measured_ids & holdout_ids)
    assert all(entry["split"] != "sealed_holdout" for entry in sidecar["songs"])
    assert sidecar["sealed_holdout_excluded"] is True
    assert "sealed_holdout" in sidecar["note"]


def test_sidecar_never_calls_measurement_functions_for_sealed_holdout_song(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """holdout の song_id に対して計測関数（`_measure_phoneme_classes`/
    `_measure_pitch_range_hz`）が一切呼ばれないことをスパイで直接検証する
    （既存流儀）。training/validation の計測は実関数へ委譲し従来どおり
    成功させることで、スパイが単なる「常に失敗させる」モックではなく
    「実際に呼ばれた song を記録する」ものであることを担保する。

    ファイルの物理的破損による「read 非発生」の直接証明（Fix 1/3/5 で
    採ってきた既存流儀）は、`sealed_holdout` についてはそのままでは
    適用できない: corpus identity 検証（層1, Fix 3・`_enumerate_pjs_
    song_ids()`）は corpus 全体（sealed_holdout を含む）の `.lab`/WAV
    バイトを sha256 化して provenance を確定するため、holdout ファイルを
    破損させると本 Fix の層（計測対象からの除外）ではなく層1（corpus
    identity 不一致）で拒否されてしまい、「計測のために読まれていない」
    ことの独立証拠にならない。層1 のバイト読み取りは一方向 hash 化のみで
    意味のある観測値を一切生成・出力しない（docstring 前提条件節に明記
    済み）——本 Fix が禁止するのは「pitch/phrase/phoneme 等、意味のある
    観測値の生成・sidecar への記録」であり、corpus-wide の provenance
    hash 化はその対象外。そのため本テストはスパイのみを直接証拠とする。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    holdout_ids = set(manifest["row_ids"]["sealed_holdout"])
    assert holdout_ids

    real_measure_phoneme_classes = psb._measure_phoneme_classes  # noqa: SLF001
    real_measure_pitch_range_hz = psb._measure_pitch_range_hz  # noqa: SLF001
    called_lab_paths: List[Path] = []
    called_wav_paths: List[Path] = []

    def _spy_measure_phoneme_classes(lab_path: Path):
        called_lab_paths.append(lab_path)
        return real_measure_phoneme_classes(lab_path)

    def _spy_measure_pitch_range_hz(wav_path: Path):
        called_wav_paths.append(wav_path)
        return real_measure_pitch_range_hz(wav_path)

    monkeypatch.setattr(psb, "_measure_phoneme_classes", _spy_measure_phoneme_classes)
    monkeypatch.setattr(psb, "_measure_pitch_range_hz", _spy_measure_pitch_range_hz)

    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    assert sidecar["songs"]  # training/validation は実測されていることの前提確認

    called_song_ids_from_lab = {p.stem for p in called_lab_paths}
    called_song_ids_from_wav = {p.parent.name for p in called_wav_paths}
    assert not (called_song_ids_from_lab & holdout_ids)
    assert not (called_song_ids_from_wav & holdout_ids)


def test_sidecar_still_measures_training_and_validation_regression_fix6(tmp_path: Path) -> None:
    """sealed_holdout 除外後も、training/validation の song は従来どおり
    計測されることの正常系回帰。"""
    song_ids = [f"pjs{i:03d}" for i in range(1, 10)]
    root = _build_corpus(tmp_path, song_ids)
    _, manifest = _identity_and_manifest(root)
    sidecar = psb.build_acoustic_inventory_sidecar(root, manifest)
    expected_measured_ids = set(manifest["row_ids"]["training"]) | set(manifest["row_ids"]["validation"])
    assert expected_measured_ids
    for entry in sidecar["songs"]:
        assert entry["split"] in ("training", "validation")
        assert entry["pitch_range_hz"] is not None
        assert entry["phrase_count"] is not None
