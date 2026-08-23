"""test_gate_synth_export_provenance.py — review #264 R20 再現テスト。

`run_export_acoustic()`（`s1_gate/gate_synth.py`）の非 `--skip-export` 経路に
対する 2 件の指摘を再現・固定する。

1. (P1) stale ONNX candidates: 同じ `--out-dir`/`--step` を使い回す再実行で、
   export 先ディレクトリに前回実行の残置ファイルが混在していても、今回の
   exporter invocation が何も生成しなかった場合に stale な候補を新 alias
   として誤って採用してはならない（fail-closed）。
2. (P2) checkpoint TOCTOU: summary に pin する ckpt/config の sha256 は、
   exporter に実際に消費させたバイト列（コピー前に read したスナップショット）
   のハッシュでなければならない。コピー〜exporter 実行の間に元ファイルが
   差し替えられても、記録ハッシュは exporter が消費したバイト列を指し続ける
   こと。

いずれも実際の DiffSinger exporter は使わず、`scripts/export.py` の位置に
軽量なフェイクスクリプトを配置して `subprocess.run` を実運用する（GPU/実重み
非依存）。
"""
from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))
sys.path.insert(0, str(_TESTS_DIR.parent / "s1_gate"))

from _optional_runtime_stubs import stub_onnxruntime_if_missing  # noqa: E402

with stub_onnxruntime_if_missing():
    import gate_synth as gs  # noqa: E402
sys.modules.pop("gate_synth", None)

_FAKE_EXPORT_PY = textwrap.dedent(
    """\
    import argparse
    from pathlib import Path

    def main():
        p = argparse.ArgumentParser()
        p.add_argument("mode")
        p.add_argument("--exp", required=True)
        p.add_argument("--ckpt", required=True)
        p.add_argument("--out", required=True)
        args = p.parse_args()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        ckpt_path = (
            Path(__file__).resolve().parent.parent
            / "checkpoints" / args.exp / f"model_ckpt_steps_{args.ckpt}.ckpt"
        )
        # exporter が実際に読んだ checkpoint バイトをそのまま "onnx" として
        # 書き出す（テストは onnx の中身 == exporter が消費したバイト列、を
        # 検証することで snapshot-consumption を確認する）。
        (out / f"{args.exp}.onnx").write_bytes(ckpt_path.read_bytes())

    if __name__ == "__main__":
        main()
    """
)

_FAKE_EXPORT_PY_NOOP = textwrap.dedent(
    """\
    # 何も生成しない exporter（exit 0 だが期待物を書かない）。
    """
)

_FAKE_EXPORT_PY_AMBIGUOUS = textwrap.dedent(
    """\
    import argparse
    from pathlib import Path

    def main():
        p = argparse.ArgumentParser()
        p.add_argument("mode")
        p.add_argument("--exp", required=True)
        p.add_argument("--ckpt", required=True)
        p.add_argument("--out", required=True)
        args = p.parse_args()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "foo.onnx").write_bytes(b"foo")
        (out / "bar.onnx").write_bytes(b"bar")

    if __name__ == "__main__":
        main()
    """
)


def _make_diffsinger_repo(base: Path, export_py_src: str) -> Path:
    repo = base / "diffsinger_repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "export.py").write_text(export_py_src, encoding="utf-8")
    return repo


def _make_ckpt_dir(base: Path, name: str, ckpt_content: bytes, config_content: bytes) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "model_ckpt_steps_5000.ckpt").write_bytes(ckpt_content)
    (d / "config.yaml").write_bytes(config_content)
    return d


def test_run_export_acoustic_normal_generation_adopts_alias(tmp_path: Path) -> None:
    """正常系: fresh staging へ export し、単一 *.onnx を acoustic.onnx へ
    alias 化する。review #264 R27 P2: `run_export_acoustic()` 自体はもう
    canonical (`out_path`) へ swap しない — staging に留め置いたまま
    `(staging_dir, out_path, ...)` を返す。呼び出し側が明示的に
    `_swap_step_dir_into_place` を呼ぶまで `out_path` は未生成のまま。"""
    repo = _make_diffsinger_repo(tmp_path, _FAKE_EXPORT_PY)
    ckpt_content = b"CKPT-CONTENT-A"
    config_content = b"CONFIG-CONTENT-A"
    ckpt_dir = _make_ckpt_dir(tmp_path, "ckpt_a", ckpt_content, config_content)
    out_dir = tmp_path / "out"

    staging_dir, out_path, ckpt_sha, train_config_sha = gs.run_export_acoustic(
        repo, ckpt_dir, "s1_gate", 5000, out_dir
    )

    assert out_path == out_dir / "onnx_gate_5000"
    # canonical はまだ swap されていない — staging に留め置かれたまま。
    assert not out_path.exists()
    staged_alias = staging_dir / "acoustic.onnx"
    assert staged_alias.exists()
    assert staged_alias.read_bytes() == ckpt_content
    assert ckpt_sha == hashlib.sha256(ckpt_content).hexdigest()
    assert train_config_sha == hashlib.sha256(config_content).hexdigest()

    gs._swap_step_dir_into_place(staging_dir, out_path)

    alias = out_path / "acoustic.onnx"
    assert alias.exists()
    assert alias.read_bytes() == ckpt_content
    # staging ディレクトリ自体は swap で消費され、out_dir 直下に残らない。
    leftovers = [p for p in out_dir.iterdir() if p.name.startswith(".onnx_gate_")]
    assert leftovers == []


def test_run_export_acoustic_does_not_adopt_stale_onnx_when_exporter_produces_nothing(
    tmp_path: Path,
) -> None:
    """P1 再現: 同じ out_dir/step へ 2 回目の export を行い、2 回目の
    exporter が何も生成しなかった場合、1 回目の生成物（stale）を新 alias
    として誤って採用せず、fail-closed で停止すること。out_path（公開先）は
    1 回目の内容のまま無変更であること。"""
    ckpt_dir_a = _make_ckpt_dir(tmp_path, "ckpt_a", b"CKPT-A", b"CONFIG-A")
    out_dir = tmp_path / "out"

    repo_ok = _make_diffsinger_repo(tmp_path / "repo_ok", _FAKE_EXPORT_PY)
    staging_dir_a, out_path, _, _ = gs.run_export_acoustic(
        repo_ok, ckpt_dir_a, "s1_gate", 5000, out_dir
    )
    gs._swap_step_dir_into_place(staging_dir_a, out_path)
    first_gen_bytes = (out_path / "acoustic.onnx").read_bytes()
    assert first_gen_bytes == b"CKPT-A"

    ckpt_dir_b = _make_ckpt_dir(tmp_path, "ckpt_b", b"CKPT-B", b"CONFIG-B")
    repo_noop = _make_diffsinger_repo(tmp_path / "repo_noop", _FAKE_EXPORT_PY_NOOP)
    with pytest.raises(RuntimeError, match="did not produce"):
        gs.run_export_acoustic(repo_noop, ckpt_dir_b, "s1_gate", 5000, out_dir)

    # fail-closed: out_path (公開先) は 1 回目の生成物のまま無変更。
    # stale な候補を拾って acoustic.onnx を差し替えてはいない。
    assert (out_path / "acoustic.onnx").read_bytes() == first_gen_bytes
    # staging は掃除され、リークしていない。
    leftovers = [
        p for p in out_dir.iterdir()
        if p.name.startswith(".onnx_gate_") or p.name.startswith("onnx_gate_5000.build-")
    ]
    assert leftovers == []


def test_run_export_acoustic_ambiguous_candidates_fail_closed_leaves_prior_generation(
    tmp_path: Path,
) -> None:
    """1 回の invocation 内で非 alias *.onnx が複数存在する場合は曖昧なため
    fail-closed で停止し、既存の公開世代（あれば）を汚さないこと。"""
    ckpt_dir_a = _make_ckpt_dir(tmp_path, "ckpt_a", b"CKPT-A", b"CONFIG-A")
    out_dir = tmp_path / "out"
    repo_ok = _make_diffsinger_repo(tmp_path / "repo_ok", _FAKE_EXPORT_PY)
    staging_dir_a, out_path, _, _ = gs.run_export_acoustic(
        repo_ok, ckpt_dir_a, "s1_gate", 5000, out_dir
    )
    gs._swap_step_dir_into_place(staging_dir_a, out_path)
    first_gen_bytes = (out_path / "acoustic.onnx").read_bytes()

    ckpt_dir_b = _make_ckpt_dir(tmp_path, "ckpt_b", b"CKPT-B", b"CONFIG-B")
    repo_ambiguous = _make_diffsinger_repo(tmp_path / "repo_ambiguous", _FAKE_EXPORT_PY_AMBIGUOUS)
    with pytest.raises(RuntimeError, match="ambiguous export output"):
        gs.run_export_acoustic(repo_ambiguous, ckpt_dir_b, "s1_gate", 5000, out_dir)

    assert (out_path / "acoustic.onnx").read_bytes() == first_gen_bytes


def test_run_export_acoustic_ckpt_sha_pins_snapshot_not_post_race_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2 (checkpoint TOCTOU) 再現テスト。

    `run_export_acoustic` がチェックポイント/config を「コピー(=snapshot)
    してから subprocess を起動する」構成になっているため、subprocess 起動
    直前に元ファイルを差し替えても、(a) exporter が実際に消費したバイト列
    (= 生成された onnx の中身) と (b) 返り値の ckpt_sha は、どちらも
    差し替え前（snapshot）のバイト列を指し続けること。
    """
    repo = _make_diffsinger_repo(tmp_path, _FAKE_EXPORT_PY)
    original_ckpt = b"CKPT-ORIGINAL"
    original_config = b"CONFIG-ORIGINAL"
    ckpt_dir = _make_ckpt_dir(tmp_path, "ckpt_race", original_ckpt, original_config)
    out_dir = tmp_path / "out"

    ckpt_file = ckpt_dir / "model_ckpt_steps_5000.ckpt"
    real_run = gs.subprocess.run
    call_count = {"n": 0}

    def _run_with_race(cmd, **kwargs):
        # exporter subprocess が起動する直前（= このスクリプトはすでに
        # snapshot 済みの ckpt_target を読むだけなので、元ファイルの
        # 差し替えは以後の exec/hash に一切影響しないはず）に元ファイルを
        # 差し替える。
        call_count["n"] += 1
        ckpt_file.write_bytes(b"CKPT-SWAPPED-AFTER-SNAPSHOT")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(gs.subprocess, "run", _run_with_race)

    staging_dir, out_path, ckpt_sha, train_config_sha = gs.run_export_acoustic(
        repo, ckpt_dir, "s1_gate", 5000, out_dir
    )

    assert call_count["n"] == 1
    # 記録された sha256 はスナップショット（差し替え前）のバイト列。
    assert ckpt_sha == hashlib.sha256(original_ckpt).hexdigest()
    assert train_config_sha == hashlib.sha256(original_config).hexdigest()
    # exporter が実際に消費した（= 生成物へ書き出した）バイト列も同じく
    # 差し替え前の内容 — 記録ハッシュ = exporter が消費したバイト列
    # （まだ swap 前の staging 側で確認する）。
    assert (staging_dir / "acoustic.onnx").read_bytes() == original_ckpt
    # ディスク上（ckpt_dir 側）は既に差し替わっている（レースが「成功」した
    # 状態）ことの確認 — この後段の drift は別の安全網（事後照合）の役割。
    assert ckpt_file.read_bytes() == b"CKPT-SWAPPED-AFTER-SNAPSHOT"
