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
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


# --- load_canonical_speaker_map_manifest(): 唯一の正規経路 -----------------
# --- （PR #328 Codex レビュー第4巡指摘9、P1、採用） -------------------------
#
# 旧 `load_local_speaker_map_manifest()`（削除済み）は repo 内 manifest を
# 直接 `json.load()` し `validate_speaker_map_manifest()` による内部構造
# 検証のみを行っていた——manifest 実バイトが `RUN9_CONTRACT.yaml` の
# `expected_speaker_map_sha` pin と一致するかは確認していなかった（改変
# された manifest + 入力 + 期待値の組で偽の `reproduced: true` を印字
# できる穴——本指摘の核心）。以下は新設 `load_canonical_speaker_map_
# manifest()` の統合確認 + 指摘9 負例1系統。
#
# **2026-08-27 追記（PR #328 レビュー第6巡指摘13、P1、採用対応）**:
# 実行中 builder 自身の自己照合（旧: 本関数内で `Path(__file__).resolve()`
# を自己照合の直前に読み直していた——import 済みモジュールの実行コードと
# 自己照合が読むバイト列が別物になり得る TOCTOU）は `main()` の verified
# self-exec dispatch へ移設した。本関数はもはや builder 自己照合を行わない
# （`running_builder_path` 引数は削除済み）——`main()` 側のテストを参照。


def test_load_canonical_speaker_map_manifest_happy_path_real_repo_data() -> None:
    """repo 内の実 pin 済み manifest を、contract/domain/rights_manifest
    経由の全 cross-check を通して例外なく返す（builder CLI が実際に消費
    する経路の統合確認）。"""
    data = smb.load_canonical_speaker_map_manifest()
    assert data["schema"] == m.SCHEMA_SPEAKER_MAP
    assert "builder_provenance" in data
    assert data["builder_provenance"]["repo_relative_path"] == (
        "voice_genesis/evolution/run9_dual_founder_pjs/speaker_map_builder.py"
    )


def test_load_canonical_speaker_map_manifest_tampered_manifest_contract_pin_mismatch_rejected(
    tmp_path: Path,
) -> None:
    """指摘9 負例(a): manifest の内部整合は保ったまま実バイトだけを改変
    すると（末尾改行1バイト追加——JSON構造上は無害）、改変後の実バイト
    sha256 が `RUN9_CONTRACT.yaml` の `expected_speaker_map_sha` pin と
    食い違うため canonical loader が fail-closed で拒否する（manifest の
    内部構造だけを見ていた旧経路の穴の直接反証）。"""
    real_bytes = m.SPEAKER_MAP_MANIFEST_PATH.read_bytes()
    tampered_path = tmp_path / "speaker_map_manifest.json"
    tampered_path.write_bytes(real_bytes + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        smb.load_canonical_speaker_map_manifest(manifest_path=tampered_path)


def test_load_canonical_speaker_map_manifest_missing_rights_manifest_rejected(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist_rights.json"
    with pytest.raises(m.Run9ValidationError, match="rights manifest source"):
        smb.load_canonical_speaker_map_manifest(rights_manifest_path=missing_path)


def test_synthesize_default_manifest_none_uses_canonical_loader_real_repo_data() -> None:
    """`synthesize()` の `manifest` 省略（CLI 既定経路）が
    `load_canonical_speaker_map_manifest()` を実際に経由することの統合
    確認——未知 founder_id を渡し、canonical 経路が正常に manifest を
    取得した**後**の `synthesize()` 内バリデーションで拒否されることを
    もって、canonical loader が正常に通過したことの間接証拠とする（emb
    ファイル自体は rights 制約により repo 非同梱のため、正常合成までは
    本テストの範囲外——`load_local_speaker_map_manifest()` 由来の弱い
    経路ではなく `load_canonical_speaker_map_manifest()` が実際に呼ばれて
    いることの回帰防止が目的）。"""
    with pytest.raises(m.Run9ValidationError, match="unknown founder_id"):
        smb.synthesize("R9F-99", Path("/dev/null"), Path("/dev/null"))


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


# --- _execute_cli(): main() のオーケストレーション本体（PR #328 レビュー ---
# --- 第6巡指摘13、P1、採用対応で main() から切り出し） --------------------
#
# `main()` は verified self-exec dispatch（下記「main() verified
# self-exec dispatch」節）で得た隔離名前空間の関数群のみを実処理に使う
# ため、production コード（`smb.synthesize` 等のモジュール属性）を
# monkeypatch しても `main()` の実処理には一切影響しない——これは本対応の
# 意図した挙動そのものである（下記 TOCTOU 負例テスト参照）。よって
# alias-guard/atomic-write の end-to-end 挙動は、`main()` が実際に呼ぶ
# のと同じ共通オーケストレーション関数 `_execute_cli()` へ fake 関数群を
# 直接注入して検証する（実 founder embedding データ非依存のまま従来と
# 同等のカバレッジを維持する）。


def test_execute_cli_rejects_out_aliasing_ritsu_emb_and_does_not_corrupt_input(
    tmp_path: Path,
) -> None:
    """`_execute_cli()` の統合確認: fake `synthesize_fn` を注入し、
    `--out` == `--ritsu-emb`（同一パス）指定時に atomic write が一切
    実行されず、非ゼロ終了し、入力ファイルの実バイトが無傷のまま残る
    ことを直接確認する（fail-closed 契約の end-to-end 確認、実 emb を
    repo に追加せずに検証する）。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    original_ritsu_bytes = b"\x01\x02\x03\x04" * 96  # 384 bytes、無意味な固定値
    ritsu_path.write_bytes(original_ritsu_bytes)
    user_path.write_bytes(b"\x00" * 384)

    fake_synth = np.zeros(smb.EMB_DIM, dtype=np.float32)
    fake_report: Dict[str, Any] = {"founder_id": "R9F-01", "reproduced": True}

    def _fake_synthesize(founder_id: str, ritsu_emb_path: Path, user_emb_path: Path) -> Any:
        return fake_synth, dict(fake_report)

    args = smb._build_argument_parser().parse_args([  # noqa: SLF001
        "--founder", "R9F-01",
        "--ritsu-emb", str(ritsu_path),
        "--user-emb", str(user_path),
        "--out", str(ritsu_path),
    ])
    rc = smb._execute_cli(  # noqa: SLF001
        args,
        synthesize_fn=_fake_synthesize,
        check_alias_fn=smb._check_out_does_not_alias_inputs,  # noqa: SLF001
        atomic_write_fn=smb._atomic_write_bytes,  # noqa: SLF001
    )
    assert rc == 1
    # 入力 ritsu emb の実バイトが無傷のまま残っていること（破壊されていない）。
    assert ritsu_path.read_bytes() == original_ritsu_bytes


# --- _atomic_write_bytes(): atomic write（PR #328 Codex レビュー第3巡 -------
# --- 指摘7、P2、採用） -------------------------------------------------------


class _ForwardingModuleProxy:
    """テスト用の薄いプロキシ: 委譲先モジュール（real `os`/`tempfile`）の
    属性を素通しし、`overrides` に指定した属性だけ差し替える。

    `monkeypatch.setattr(smb.os, "replace", ...)` のようにプロセス全体で
    共有される real `os`/`tempfile` モジュールのオブジェクトを直接書き換え
    ると、テスト境界を越えてプロセス内の他コード（pytest 自身の内部処理
    含む）にまで影響し得る——`monkeypatch.setattr(smb, "os", proxy)` で
    `speaker_map_builder` モジュール**内の名前束縛だけ**を差し替え、real
    モジュールは無傷のまま保つ（monkeypatch がテスト終了時に自動で
    元へ戻す）。"""

    def __init__(self, delegate: Any, **overrides: Any) -> None:
        self._delegate = delegate
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._delegate, name)


def test_atomic_write_bytes_writes_full_content_and_no_staging_leftover(
    tmp_path: Path,
) -> None:
    """正常系: 書き込んだ実バイトが目的ファイルと完全一致し、staging
    ファイル（`.<name>.*.tmp`）が書き込み先ディレクトリに残っていないこと
    （atomic 置換後の bytes 一致 + staging cleanup の直接証拠）。"""
    out_path = tmp_path / "out.emb"
    payload = b"\x01\x02\x03\x04" * 96
    smb._atomic_write_bytes(out_path, payload)  # noqa: SLF001
    assert out_path.read_bytes() == payload
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(f".{out_path.name}.")]
    assert leftovers == []


def test_atomic_write_bytes_replaces_existing_file(tmp_path: Path) -> None:
    """既存の正当な出力ファイルがある状態でも、atomic 置換後は新しい
    バイト列に完全に置き換わること。"""
    out_path = tmp_path / "out.emb"
    out_path.write_bytes(b"\x00" * 4)
    payload = b"\xff" * 8
    smb._atomic_write_bytes(out_path, payload)  # noqa: SLF001
    assert out_path.read_bytes() == payload


def test_atomic_write_bytes_creates_parent_dir(tmp_path: Path) -> None:
    """`--out` の親ディレクトリが存在しない場合でも作成した上で書き込む
    （旧実装の `main()` が呼んでいた `mkdir(parents=True, exist_ok=True)`
    と同じ挙動を `_atomic_write_bytes()` 自身が担う）。"""
    out_path = tmp_path / "nested" / "dir" / "out.emb"
    payload = b"\x09" * 16
    smb._atomic_write_bytes(out_path, payload)  # noqa: SLF001
    assert out_path.read_bytes() == payload


def test_atomic_write_bytes_failure_injection_leaves_old_bytes_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-injection: `os.replace()` 実行前（staging 書き込み完了後）
    に例外を注入すると、書き込み途中の中断を模擬しつつ、既存の旧出力
    ファイルの実バイトが無傷のまま残ることを確認する（PR #328 Codex
    レビュー第3巡指摘7、P2、採用対応 — 非 atomic write の穴の直接反証）。
    """
    out_path = tmp_path / "out.emb"
    original_bytes = b"\x11\x22\x33\x44" * 96  # 既存の正当な出力（384 bytes）。
    out_path.write_bytes(original_bytes)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected failure before os.replace()")

    monkeypatch.setattr(smb, "os", _ForwardingModuleProxy(os, replace=_boom))

    with pytest.raises(RuntimeError, match="injected failure"):
        smb._atomic_write_bytes(out_path, b"\x99" * 384)  # noqa: SLF001

    # 旧出力の実バイトが無傷のまま残っていること（truncate/partial 出力なし）。
    assert out_path.read_bytes() == original_bytes
    # staging ファイルは best-effort で削除され、ディレクトリに残らないこと。
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(f".{out_path.name}.")]
    assert leftovers == []


def test_atomic_write_bytes_failure_injection_during_write_leaves_old_bytes_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-injection（書き込み処理そのものへの注入）: staging
    ファイルへの書き込み中に例外を注入しても、`os.replace()` に到達しない
    ため既存の旧出力ファイルは無傷のまま残る。"""
    out_path = tmp_path / "out.emb"
    original_bytes = b"\xaa\xbb\xcc\xdd" * 96
    out_path.write_bytes(original_bytes)

    class _BoomFile:
        def write(self, _data: bytes) -> int:
            raise RuntimeError("injected failure during staging write")

        def __enter__(self) -> "_BoomFile":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

    def _fake_fdopen(fd: int, mode: str) -> Any:
        os.close(fd)
        return _BoomFile()

    monkeypatch.setattr(smb, "os", _ForwardingModuleProxy(os, fdopen=_fake_fdopen))

    with pytest.raises(RuntimeError, match="injected failure during staging write"):
        smb._atomic_write_bytes(out_path, b"\x77" * 384)  # noqa: SLF001

    assert out_path.read_bytes() == original_bytes


def test_atomic_write_bytes_staging_does_not_alias_input_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """staging ファイルのパスが `--ritsu-emb`/`--user-emb` のいずれとも
    異なること（alias 拒否チェックが staging 書き込みでは迂回されない
    ことの直接証拠——`_check_out_does_not_alias_inputs()` は `--out` と
    入力2つの3パスしか比較しないため、staging ファイル自体が偶然どちらか
    と同一パスになれば alias ガードを迂回し得る、という懸念に対する
    反証）。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    ritsu_path.write_bytes(b"\x00" * 4)
    user_path.write_bytes(b"\x00" * 4)
    out_path = tmp_path / "out.emb"

    seen_staging_names: List[str] = []

    def _tracking_mkstemp(*args: Any, **kwargs: Any) -> Any:
        fd, name = tempfile.mkstemp(*args, **kwargs)
        seen_staging_names.append(name)
        return fd, name

    monkeypatch.setattr(
        smb, "tempfile", _ForwardingModuleProxy(tempfile, mkstemp=_tracking_mkstemp),
    )
    smb._atomic_write_bytes(out_path, b"\x42" * 384)  # noqa: SLF001

    assert len(seen_staging_names) == 1
    staging_path = Path(seen_staging_names[0])
    assert staging_path.resolve() != ritsu_path.resolve()
    assert staging_path.resolve() != user_path.resolve()


def test_execute_cli_writes_atomically_via_out_flag(tmp_path: Path) -> None:
    """`_execute_cli()` の統合確認: `--out` 指定時に注入した
    `atomic_write_fn`（本物の `_atomic_write_bytes()`）が実際に呼ばれ、
    期待バイト列がそのまま書き出されること。"""
    ritsu_path = tmp_path / "ritsu.emb"
    user_path = tmp_path / "user.emb"
    out_path = tmp_path / "out.emb"
    ritsu_path.write_bytes(b"\x00" * 384)
    user_path.write_bytes(b"\x00" * 384)

    fake_synth = np.full(smb.EMB_DIM, 7.0, dtype=np.float32)
    fake_report: Dict[str, Any] = {"founder_id": "R9F-01", "reproduced": True}

    def _fake_synthesize(founder_id: str, ritsu_emb_path: Path, user_emb_path: Path) -> Any:
        return fake_synth, dict(fake_report)

    args = smb._build_argument_parser().parse_args([  # noqa: SLF001
        "--founder", "R9F-01",
        "--ritsu-emb", str(ritsu_path),
        "--user-emb", str(user_path),
        "--out", str(out_path),
    ])
    rc = smb._execute_cli(  # noqa: SLF001
        args,
        synthesize_fn=_fake_synthesize,
        check_alias_fn=smb._check_out_does_not_alias_inputs,  # noqa: SLF001
        atomic_write_fn=smb._atomic_write_bytes,  # noqa: SLF001
    )
    assert rc == 0
    assert out_path.read_bytes() == fake_synth.tobytes()


# --- main(): verified self-exec dispatch（PR #328 Codex レビュー第6巡 ------
# --- 指摘13、P1、採用・Fable 確定対応方針） ---------------------------------
#
# 旧実装（`load_canonical_speaker_map_manifest()` 内の自己照合）は
# `Path(__file__).resolve()` を自己照合の**直前に読み直して**いたため、
# 「hash 対象のバイト列」と「実際に実行されるコード（import 済みモジュール
# が束縛する、pin 照合時点とは別バイト列を保持しているかもしれない旧
# コード）」が別オブジェクトになり得る TOCTOU を構造的に抱えていた。
# 新実装は `main()` が読んだ `source_bytes` を pin と照合した**同一の
# オブジェクト**を `compile()`→`exec()` して得た隔離名前空間の関数群のみを
# 実処理に用いる。


def test_main_verified_self_exec_dispatch_real_repo_data_unknown_founder_rejected() -> None:
    """正常系 end-to-end（既存テスト追随）: `main()` の verified self-exec
    dispatch が実際に canonical manifest ロード + `source_bytes` 照合 +
    compile/exec を経由して隔離名前空間の `synthesize()` を呼び出すことの
    統合確認（実 founder embedding データ非依存）: `--ritsu-emb`/
    `--user-emb` に空ファイル（`/dev/null`）を渡すと、実 sha256 が
    manifest pin と食い違うため隔離名前空間の `synthesize()` 内バリデー
    ションで拒否される（`--founder` は argparse `choices` 制約があるため
    未知 founder_id では argparse 自体が `SystemExit` を送出してしまい
    `main()` の実処理まで到達しない——本テストは有効な founder_id で
    `synthesize()` 側の fail-closed 分岐を直接検証する）。"""
    argv = ["--founder", "R9F-01", "--ritsu-emb", "/dev/null", "--user-emb", "/dev/null"]
    assert smb.main(argv) == 1


def test_main_verified_self_exec_dispatch_source_bytes_mismatch_rejected(
    tmp_path: Path,
) -> None:
    """実行対象 builder バイト改変（実ファイルのコピーへ1byte追加）→
    `running_builder_path` override 経由で渡すと、manifest/contract 側は
    本物のまま（cross-check (j) は repo 内の本物の `speaker_map_
    builder.py` と照合するため通過する）にもかかわらず、`main()` の
    verified self-exec dispatch が `source_bytes` の sha256 と
    `builder_provenance.builder_sha256` pin の不一致を fail-closed で
    拒否する（cross-check (j) と verified self-exec dispatch が独立した
    防御であることの直接証拠——鶏卵性: builder バイト自体を変える対応で
    builder_sha256 が変わるため、テストは実行中ファイルの改変ではなく
    `running_builder_path` override で「実行対象が pin と乖離した」状態を
    模擬する）。"""
    real_builder_path = _RUN_DIR / "speaker_map_builder.py"
    tampered_copy = tmp_path / "speaker_map_builder_tampered_copy.py"
    tampered_copy.write_bytes(real_builder_path.read_bytes() + b"\n# tampered\n")
    argv = ["--founder", "R9F-01", "--ritsu-emb", "/dev/null", "--user-emb", "/dev/null"]
    rc = smb.main(argv, running_builder_path=tampered_copy)
    assert rc == 1


def test_main_verified_self_exec_dispatch_missing_source_rejected(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist_builder.py"
    argv = ["--founder", "R9F-01", "--ritsu-emb", "/dev/null", "--user-emb", "/dev/null"]
    rc = smb.main(argv, running_builder_path=missing_path)
    assert rc == 1


def test_main_verified_self_exec_dispatch_bypasses_monkeypatched_module_attribute(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """TOCTOU シミュレーション負例（PR #328 レビュー第6巡指摘13、P1、
    採用対応、必須テスト(a)）: import 済みモジュール属性 `smb.synthesize`
    を「常に成功を偽装する」実装へ改変する（TOCTOU の比喩: 旧実装が
    hash 対象を再度ディスクから読み直す間に、実行中コードが既に改変
    されている状況の直接模擬）。

    旧実装（自己照合の直前に `Path(__file__).resolve()` を読み直すだけで、
    その後の実処理自体は import 済みの——ここでは改変済みの——
    `synthesize` をそのまま使っていた）なら、この改変された
    `smb.synthesize` がそのまま使われ、空ファイル入力（emb sha256
    不一致）のような fail-closed 分岐すら偽装成功で素通りしてしまう。
    新実装は `main()` が読んだ `source_bytes` を compile→exec した隔離
    名前空間の `synthesize` のみを使うため、`smb.synthesize`（モジュール
    属性）をどれだけ改変しても `main()` の実処理には一切影響しない——
    改変バイトが exec されないことの直接証拠。"""
    def _malicious_always_reproduced(
        founder_id: str, ritsu_emb_path: Path, user_emb_path: Path, *, manifest: Any = None,
    ) -> Any:
        # 実際には何も検証せず「成功した」と偽装する——import 済みモジュール
        # 属性が改変された状況の直接模擬。
        fake = np.zeros(smb.EMB_DIM, dtype=np.float32)
        return fake, {"reproduced": True, "founder_id": founder_id, "forged": True}

    monkeypatch.setattr(smb, "synthesize", _malicious_always_reproduced)

    argv = ["--founder", "R9F-01", "--ritsu-emb", "/dev/null", "--user-emb", "/dev/null"]
    rc = smb.main(argv)
    # 改変された smb.synthesize は使われず、隔離名前空間の本物の
    # synthesize() が使われ、空ファイル入力の emb sha256 不一致で
    # fail-closed 拒否されることを rc != 0 で確認する（forged な
    # reproduced: true は出力に一切現れない）。
    assert rc == 1
    captured = capsys.readouterr()
    assert "forged" not in captured.out
    assert '"reproduced": true' not in captured.out.lower()


def test_compile_and_exec_verified_source_does_not_trigger_main_guard() -> None:
    """exec ガードの単体（PR #328 レビュー第6巡指摘13、P1、採用対応、
    必須テスト(c)）: `_compile_and_exec_verified_source()` は隔離名前空間の
    `__name__` を `"__main__"` 以外の sentinel に設定するため、exec 中に
    モジュール末尾の `if __name__ == "__main__": raise SystemExit(main())`
    が発火せず、`main()` の再帰起動が起きないことを直接確認する（発火して
    いれば、引数なしの argparse が実プロセスの `sys.argv`（pytest 自身の
    引数）を解釈しようとして `SystemExit` を送出するため、この非発火は
    `SystemExit` が上がらないことで検出できる）。"""
    real_builder_path = _RUN_DIR / "speaker_map_builder.py"
    source_bytes = real_builder_path.read_bytes()
    namespace = smb._compile_and_exec_verified_source(source_bytes, real_builder_path)  # noqa: SLF001
    assert namespace["__name__"] == smb._VERIFIED_NAMESPACE_NAME  # noqa: SLF001
    assert namespace["__name__"] != "__main__"
    # namespace 内に主要関数が定義されていること（exec が完遂したことの
    # 直接証拠）。
    assert callable(namespace["synthesize"])
    assert callable(namespace["main"])
    assert callable(namespace["_check_out_does_not_alias_inputs"])
    assert callable(namespace["_atomic_write_bytes"])
    assert callable(namespace["_execute_cli"])
