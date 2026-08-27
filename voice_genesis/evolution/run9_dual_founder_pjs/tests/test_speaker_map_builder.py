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

    w_r = np.float32(eval(w_ritsu_expr, {"__builtins__": {}}))  # noqa: S307
    w_u = np.float32(eval(w_user_expr, {"__builtins__": {}}))  # noqa: S307
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
