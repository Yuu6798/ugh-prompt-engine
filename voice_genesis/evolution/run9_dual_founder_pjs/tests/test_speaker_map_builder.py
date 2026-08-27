"""test_speaker_map_builder.py — RUN9-L0-HARNESS-3a、PR #328 Codex レビュー
第1巡指摘1（P1、採用）対応: `speaker_map_builder.py`（checkout-stable
合成 embedding 再現ビルダー）の最低テスト。

fixture は tmp_path 上に構築した固定の小 384-dim 合成ベクトル + 合成
manifest dict のみを用いる。**実 ritsu/user emb バイナリは repo へ
追加しない**（rights 制約 — `HARNESS3A_SPEAKER_MAP_RECORD.md` 参照。
実 emb に対する再現実測は session workdir 限定で別途実施済み）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402
import speaker_map_builder as smb  # noqa: E402

_DIM = 384


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_emb(path: Path, vec: np.ndarray) -> None:
    path.write_bytes(vec.tobytes())


def _synthetic_manifest_and_inputs(
    tmp_path: Path,
    *,
    ritsu_vec: np.ndarray,
    user_vec: np.ndarray,
    w_ritsu_expr: str,
    w_user_expr: str,
    founder_id: str = "R9F-01",
) -> Tuple[Dict[str, Any], Path, Path, np.ndarray]:
    """テスト用の最小合成 manifest dict（`synthesize()` が実際に参照する
    3節: `input_embeddings`/`renormalized_runtime_weights`/
    `synthesized_embedding` のみ）+ 書き出した ritsu/user emb ファイル
    パス + 独立に計算した期待合成ベクトルを返す。`synthesize()` は
    `manifest=` 明示指定時は `validate_speaker_map_manifest()` を通さない
    ため、フルスキーマを満たさない最小 dict で足りる。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    _write_emb(ritsu_path, ritsu_vec)
    _write_emb(user_path, user_vec)

    # synthesize() 自身が使う共有パーサ（m._evaluate_closed_weight_expr()）
    # で独立に重みを評価する——builder 内部実装への依存を避けつつ、
    # builder/validator が共有するパーサと同一の評価規則で期待値を作る。
    w_r = np.float32(m._evaluate_closed_weight_expr(w_ritsu_expr, field="test.w_ritsu_expr"))  # noqa: SLF001
    w_u = np.float32(m._evaluate_closed_weight_expr(w_user_expr, field="test.w_user_expr"))  # noqa: SLF001
    expected_synth = (w_r * ritsu_vec + w_u * user_vec)
    expected_sha = _sha256(expected_synth.tobytes())

    manifest = {
        "founders": {
            founder_id: {
                "input_embeddings": {
                    "ritsu_emb_sha256": _sha256(ritsu_path.read_bytes()),
                    "user_emb_sha256": _sha256(user_path.read_bytes()),
                },
                "renormalized_runtime_weights": {
                    "w_ritsu_expr": w_ritsu_expr,
                    "w_user_expr": w_user_expr,
                },
                "synthesized_embedding": {"sha256": expected_sha},
            },
        },
    }
    return manifest, ritsu_path, user_path, expected_synth


# --- synthesize(): 固定小ベクトルでの加重和の数値検証 -----------------------


def test_synthesize_weighted_sum_matches_hand_computed_value(tmp_path: Path) -> None:
    """ritsu_vec を全要素 2.0、user_vec を全要素 4.0 の固定ベクトルとし、
    w_ritsu=0.75/w_user=0.25 のとき、全要素が
    `0.75*2.0 + 0.25*4.0 == 2.5` となることを独立に手計算した期待値で
    検証する（`synthesize()` 内部実装への依存を避けた直接の数値証拠）。"""
    ritsu_vec = np.full(_DIM, 2.0, dtype=np.float32)
    user_vec = np.full(_DIM, 4.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    synth, report = smb.synthesize("R9F-01", ritsu_path, user_path, manifest=manifest)
    assert synth.dtype == np.float32
    assert synth.shape == (_DIM,)
    assert np.allclose(synth, 2.5, atol=0.0, rtol=0.0)
    assert report["reproduced"] is True
    assert report["out_sha256"] == report["expected_sha256_per_manifest"]


def test_synthesize_weighted_sum_matches_hand_computed_value_thirds(tmp_path: Path) -> None:
    """R9F-02 の重み（1/3, 2/3）でも同様に検証する: ritsu_vec 全要素 3.0、
    user_vec 全要素 6.0 のとき `(1/3)*3.0 + (2/3)*6.0 == 5.0`。float32 の
    丸め誤差を許容するため `np.isclose` の既定 rtol で比較する。"""
    ritsu_vec = np.full(_DIM, 3.0, dtype=np.float32)
    user_vec = np.full(_DIM, 6.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="1.0/3.0", w_user_expr="2.0/3.0", founder_id="R9F-02",
    )
    synth, report = smb.synthesize("R9F-02", ritsu_path, user_path, manifest=manifest)
    assert synth.dtype == np.float32
    assert np.allclose(synth, 5.0)
    assert report["reproduced"] is True


def test_synthesize_random_fixed_vectors_round_trips(tmp_path: Path) -> None:
    """決定論的な乱数シードで生成した非一様ベクトルでも、独立に計算した
    期待合成ベクトルと byte 完全一致すること（L2正規化・摂動が混入して
    いないことの間接証拠 — 混入していれば byte 一致は崩れる）。"""
    rng = np.random.default_rng(12345)
    ritsu_vec = rng.standard_normal(_DIM).astype(np.float32)
    user_vec = rng.standard_normal(_DIM).astype(np.float32)
    manifest, ritsu_path, user_path, expected_synth = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    synth, report = smb.synthesize("R9F-01", ritsu_path, user_path, manifest=manifest)
    assert synth.tobytes() == expected_synth.tobytes()
    assert report["reproduced"] is True


# --- synthesize(): fail-closed 全分岐 ----------------------------------------


def test_synthesize_ritsu_emb_sha_mismatch_rejected(tmp_path: Path) -> None:
    """入力 ritsu emb の実バイトが manifest pin と食い違うと fail-closed
    で拒否される（部分出力なし）。"""
    ritsu_vec = np.full(_DIM, 1.0, dtype=np.float32)
    user_vec = np.full(_DIM, 1.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    # ritsu emb を差し替える（別内容 = 別 sha256）。
    tampered_ritsu = tmp_path / "tampered_ritsu.emb"
    _write_emb(tampered_ritsu, np.full(_DIM, 9.0, dtype=np.float32))
    with pytest.raises(m.Run9ValidationError, match="ritsu_emb_sha256"):
        smb.synthesize("R9F-01", tampered_ritsu, user_path, manifest=manifest)


def test_synthesize_user_emb_sha_mismatch_rejected(tmp_path: Path) -> None:
    """入力 user emb の実バイトが manifest pin と食い違うと fail-closed で
    拒否される。"""
    ritsu_vec = np.full(_DIM, 1.0, dtype=np.float32)
    user_vec = np.full(_DIM, 1.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    tampered_user = tmp_path / "tampered_user.emb"
    _write_emb(tampered_user, np.full(_DIM, 9.0, dtype=np.float32))
    with pytest.raises(m.Run9ValidationError, match="user_emb_sha256"):
        smb.synthesize("R9F-01", ritsu_path, tampered_user, manifest=manifest)


def test_synthesize_unknown_founder_id_rejected(tmp_path: Path) -> None:
    ritsu_vec = np.full(_DIM, 1.0, dtype=np.float32)
    user_vec = np.full(_DIM, 1.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    with pytest.raises(m.Run9ValidationError, match="unknown founder_id"):
        smb.synthesize("R9F-99", ritsu_path, user_path, manifest=manifest)


def test_synthesize_wrong_shape_rejected(tmp_path: Path) -> None:
    """emb ファイルが 384-dim float32 でない（短い）場合は shape 検証で
    fail-closed 拒否される。"""
    short_vec = np.full(32, 1.0, dtype=np.float32)
    ritsu_path = tmp_path / "short_ritsu.emb"
    user_path = tmp_path / "user.emb"
    _write_emb(ritsu_path, short_vec)
    _write_emb(user_path, np.full(_DIM, 1.0, dtype=np.float32))
    manifest = {
        "founders": {
            "R9F-01": {
                "input_embeddings": {
                    "ritsu_emb_sha256": _sha256(ritsu_path.read_bytes()),
                    "user_emb_sha256": _sha256(user_path.read_bytes()),
                },
                "renormalized_runtime_weights": {
                    "w_ritsu_expr": "0.75", "w_user_expr": "0.25",
                },
                "synthesized_embedding": {"sha256": "0" * 64},
            },
        },
    }
    with pytest.raises(m.Run9ValidationError, match="ritsu_vec shape"):
        smb.synthesize("R9F-01", ritsu_path, user_path, manifest=manifest)


def test_synthesize_output_sha_mismatch_rejected(tmp_path: Path) -> None:
    """入力 sha は pin と一致するが、manifest の
    `synthesized_embedding.sha256` を意図的に偽の値へ差し替えると、再現
    した合成結果との不一致が fail-closed で拒否される（builder の
    再現契約そのものの直接検証）。"""
    ritsu_vec = np.full(_DIM, 2.0, dtype=np.float32)
    user_vec = np.full(_DIM, 4.0, dtype=np.float32)
    manifest, ritsu_path, user_path, _expected = _synthetic_manifest_and_inputs(
        tmp_path, ritsu_vec=ritsu_vec, user_vec=user_vec,
        w_ritsu_expr="0.75", w_user_expr="0.25",
    )
    manifest["founders"]["R9F-01"]["synthesized_embedding"]["sha256"] = "f" * 64
    with pytest.raises(m.Run9ValidationError, match="再現失敗"):
        smb.synthesize("R9F-01", ritsu_path, user_path, manifest=manifest)


# --- load_local_speaker_map_manifest(): 実 repo manifest との統合 -----------


def test_load_local_speaker_map_manifest_validates_real_repo_manifest() -> None:
    """`load_local_speaker_map_manifest()` は repo 内の実 pin 済み
    manifest を読み、`validate_speaker_map_manifest()` を自己適用して
    例外なく返す（builder が実際に消費する経路の統合確認）。"""
    data = smb.load_local_speaker_map_manifest()
    assert data["schema"] == m.SCHEMA_SPEAKER_MAP
    assert "builder_provenance" in data
    assert data["builder_provenance"]["repo_relative_path"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/speaker_map_builder.py"
    )


# --- _check_out_does_not_alias_inputs(): --out 入力破壊ガード -----------------
# --- (PR #328 Codex レビュー第2巡指摘4、P1、採用) -----------------------------


def test_check_out_alias_same_path_as_ritsu_emb_rejected(tmp_path: Path) -> None:
    """`--out` に `--ritsu-emb` と全く同一のパスを渡すと（symlink 不使用の
    最も単純な alias）拒否理由の文字列が返る。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    error = smb._check_out_does_not_alias_inputs(ritsu_path, ritsu_path, user_path)  # noqa: SLF001
    assert error is not None
    assert "--ritsu-emb" in error


def test_check_out_alias_same_path_as_user_emb_rejected(tmp_path: Path) -> None:
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    error = smb._check_out_does_not_alias_inputs(user_path, ritsu_path, user_path)  # noqa: SLF001
    assert error is not None
    assert "--user-emb" in error


def test_check_out_symlink_alias_to_ritsu_emb_rejected(tmp_path: Path) -> None:
    """`--out` が `--ritsu-emb` と異なるパス文字列でも、symlink 経由で
    同一実体を指していれば `Path.resolve()` により alias と検出される。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    out_symlink = tmp_path / "out_alias.emb"
    out_symlink.symlink_to(ritsu_path)
    error = smb._check_out_does_not_alias_inputs(out_symlink, ritsu_path, user_path)  # noqa: SLF001
    assert error is not None
    assert "--ritsu-emb" in error


def test_check_out_symlink_alias_to_user_emb_rejected(tmp_path: Path) -> None:
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    out_symlink = tmp_path / "out_alias.emb"
    out_symlink.symlink_to(user_path)
    error = smb._check_out_does_not_alias_inputs(out_symlink, ritsu_path, user_path)  # noqa: SLF001
    assert error is not None
    assert "--user-emb" in error


def test_check_out_distinct_path_not_flagged(tmp_path: Path) -> None:
    """`--out` が入力2つのいずれとも異なる実体であれば `None`（alias なし）
    を返す——正常系がガードで誤って拒否されないことの確認。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    out_path = tmp_path / "out.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    assert smb._check_out_does_not_alias_inputs(out_path, ritsu_path, user_path) is None  # noqa: SLF001


def test_main_rejects_out_aliasing_ritsu_emb_and_does_not_corrupt_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()` 経由の統合確認: `synthesize()` をモックして本物の manifest
    照合を経由せずに、`--out` == `--ritsu-emb`（同一パス）指定時に
    write_bytes() が一切実行されず、非ゼロ終了し、入力ファイルの実バイトが
    無傷のまま残ることを直接確認する（fail-closed 契約の end-to-end 確認、
    実 emb を repo に追加せずに検証する）。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    original_ritsu_bytes = b"\x01\x02\x03\x04" * 96  # 384 bytes、無意味な固定値
    ritsu_path.write_bytes(original_ritsu_bytes)
    user_path.write_bytes(b"\x00" * 384)

    fake_synth = np.zeros(smb.EMB_DIM, dtype=np.float32)
    fake_report: Dict[str, Any] = {"founder_id": "R9F-01", "reproduced": True}

    def _fake_synthesize(founder_id: str, ritsu_emb_path: Path, user_emb_path: Path) -> Any:
        return fake_synth, dict(fake_report)

    monkeypatch.setattr(smb, "synthesize", _fake_synthesize)

    argv = [
        "--founder", "R9F-01",
        "--ritsu-emb", str(ritsu_path),
        "--user-emb", str(user_path),
        "--out", str(ritsu_path),
    ]
    rc = smb.main(argv)
    assert rc == 1
    # 入力 ritsu emb の実バイトが無傷のまま残っていること（破壊されていない）。
    assert ritsu_path.read_bytes() == original_ritsu_bytes
