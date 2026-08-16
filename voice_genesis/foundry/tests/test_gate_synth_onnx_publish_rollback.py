"""test_gate_synth_onnx_publish_rollback.py — review #264 R28 P2 再現テスト
(gate_synth.py:1617 スレッド + 本文直書き)。

R27 は `run_export_acoustic()` から canonical への swap を切り離し、
diffsinger_repo の安定性検査（`_check_diffsinger_repo_stable`）成功の後に
限り `_publish_exported_acoustic()` が `onnx_gate_<step>` を canonical へ
公開するようにした。しかし R27 時点でもこの公開は、`cmd_run()` が続けて
行う phoneme/embedding discovery・model load・synthesis・公開直前の事後
ハッシュ照合（score モジュール/gate_synth.py 自身/モデル束の pin 再照合）
より**前**に発生していた。これらの後続段のいずれかが失敗すると、
`onnx_gate_<step>` は既に新世代へ差し替わっているのに、対応する
`step_<N>/gate_synth_summary.json`（別途 build_dir/synth_out_dir の swap が
完走した場合のみ公開される）は旧世代のまま残り、旧 summary が pin する
`acoustic_onnx_path` のハッシュがそのパスの実体（新世代）と一致しなくなる
——「新 ONNX + 旧 gate summary」の混成公開状態。

修正: `cmd_run()`（wrapper）が `_cmd_run_impl()` を呼び、後者が完走した
場合にのみ `swapped=True` を rollback_state へ書き込む。`onnx_gate_<step>`
を公開したのに完走しなかった run は、wrapper の `finally` 節が
`_rollback_swapped_step_dir()` で `onnx_gate_<step>` の公開を取り消す
（旧世代があれば `.old` から復元、無ければ未公開状態へ戻す）。

本テストは実際の DiffSinger exporter・ONNX Runtime 推論は使わず、フェイク
exporter（`scripts/export.py` 相当）で `onnx_gate_<step>` を公開まで進めた
上で、`gate_synth.synth_song`（`InferenceSession` を実際に呼ぶ手前の呼び出し
口）を monkeypatch して合成段で例外を送出させ、「canonical
(`onnx_gate_<step>`) が旧世代のまま/復元されていること」「summary
(`step_<N>/gate_synth_summary.json`) は公開されていないこと」を実測する。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import gate_synth as gs  # noqa: E402


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo_dir), check=True)
    (repo_dir / "README.md").write_text("placeholder\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo_dir), check=True)


_EXPORT_PY_TEMPLATE = textwrap.dedent(
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
        (out / f"{{args.exp}}.onnx").write_bytes({onnx_bytes!r})
        (out / f"{{args.exp}}.phonemes.json").write_text('{{"a": 1}}', encoding="utf-8")

    if __name__ == "__main__":
        main()
    """
)


def _make_diffsinger_export_repo(repo_dir: Path, onnx_bytes: bytes) -> None:
    _init_git_repo(repo_dir)
    # `python scripts/export.py` 自体が `__pycache__` を副生成し、
    # `run_export_acoustic()` も `checkpoints/<exp_name>/` を diffsinger_repo
    # 直下へ書き出す。これらを追跡すると、本テストの関心事（R28 の公開順序）
    # と無関係な安定性検査の偽陽性が混入する（test_gate_synth_diffsinger_
    # repo_pin.py の同型 helper と同じ対処）。
    (repo_dir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\ncheckpoints/\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add gitignore"], cwd=str(repo_dir), check=True
    )
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (repo_dir / "scripts" / "export.py").write_text(
        _EXPORT_PY_TEMPLATE.format(onnx_bytes=onnx_bytes), encoding="utf-8"
    )


def _make_ckpt_dir(base: Path, name: str, ckpt_content: bytes, config_content: bytes) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "model_ckpt_steps_5000.ckpt").write_bytes(ckpt_content)
    (d / "config.yaml").write_bytes(config_content)
    return d


def _make_canon_model_dir(base: Path) -> Path:
    d = base / "canon_model"
    (d / "dsdur").mkdir(parents=True)
    (d / "dspitch").mkdir(parents=True)
    (d / "phonemes.txt").write_text("<PAD>\na\ni\nu\ne\no\n", encoding="utf-8")
    (d / "linguistic.onnx").write_bytes(b"FAKE-LINGUISTIC-ONNX")
    (d / "dsdur" / "dur.onnx").write_bytes(b"FAKE-DUR-ONNX")
    (d / "dspitch" / "pitch.onnx").write_bytes(b"FAKE-PITCH-ONNX")
    (d / "dsconfig.yaml").write_text("fake: true\n", encoding="utf-8")
    return d


def _make_vocoder_dir(base: Path) -> Path:
    d = base / "vocoder"
    d.mkdir(parents=True)
    (d / "nsf_hifigan.onnx").write_bytes(b"FAKE-VOCODER-ONNX")
    return d


_SCORE_PY_SRC = textwrap.dedent(
    """\
    \"\"\"fake score.py — test_gate_synth_onnx_publish_rollback.py 用。\"\"\"
    from __future__ import annotations
    from dataclasses import dataclass
    from typing import List

    TEMPO_BPM = 120.0

    @dataclass
    class ScoreNote:
        midi: int

    def beats_to_seconds(beats: float, tempo_bpm: float) -> float:
        return beats * 60.0 / tempo_bpm

    def build_sakura_score() -> "List[ScoreNote]":
        return []
    """
)

_PHONEME_JP_SRC = '"""fake phoneme_jp.py — test_gate_synth_onnx_publish_rollback.py 用。"""\n'


def _make_singer_dir(base: Path) -> Path:
    d = base / "singer"
    d.mkdir(parents=True)
    (d / "phoneme_jp.py").write_text(_PHONEME_JP_SRC, encoding="utf-8")
    (d / "score.py").write_text(_SCORE_PY_SRC, encoding="utf-8")
    return d


def _make_run_args(
    *,
    diffsinger_repo: Path,
    ckpt_dir: Path,
    canon_model_dir: Path,
    vocoder_dir: Path,
    out_dir: Path,
    singer_dir: Path,
    step: int = 5000,
) -> argparse.Namespace:
    return argparse.Namespace(
        diffsinger_repo=str(diffsinger_repo),
        ckpt_dir=str(ckpt_dir),
        exp_name="s1_gate",
        step=step,
        skip_export=False,
        acoustic_dir=None,
        canon_model_dir=str(canon_model_dir),
        vocoder_dir=str(vocoder_dir),
        out_dir=str(out_dir),
        song="sakura",
        notes_limit=None,
        tokens="own",
        singer_dir=str(singer_dir),
        speaker=None,
    )


@pytest.fixture(autouse=True)
def _isolate_score_module_cache():
    """`load_song_module` が `sys.modules["score"]`/`"phoneme_jp"` へ書き込む
    ため、他テストへの汚染を防ぐ（`test_gate_synth_score_module_cache.py` と
    同じ後始末作法）。"""
    yield
    for name in ("score", "score_umi", "phoneme_jp"):
        sys.modules.pop(name, None)


def test_cmd_run_rolls_back_onnx_publish_when_synthesis_stage_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """review #264 R28 P2 再現本体: 1回目の正常 run で `onnx_gate_5000`/
    `step_5000/gate_synth_summary.json` の旧世代を作った後、2回目の run で
    export 自体は成功して `onnx_gate_5000` が新世代へ公開された**後**、
    `synth_song`（合成段）が例外を送出するよう monkeypatch する。

    修正前: `onnx_gate_5000` は新世代のまま残り、`step_5000/gate_synth_
    summary.json` は旧世代のまま——旧 summary が pin する acoustic_onnx の
    ハッシュが実体（新世代）と食い違う混成公開状態になっていた。

    修正後: `cmd_run` の rollback wrapper が `onnx_gate_5000` を旧世代へ
    （`.old` から）復元し、`step_5000/gate_synth_summary.json` は公開されない
    （build_dir 自体が finally で削除される既存挙動のまま）。
    """
    diffsinger_repo = tmp_path / "diffsinger_repo"
    _make_diffsinger_export_repo(diffsinger_repo, b"ONNX-GEN-1")
    ckpt_dir_1 = _make_ckpt_dir(tmp_path, "ckpt_1", b"CKPT-1", b"CONFIG-1")
    canon_model_dir = _make_canon_model_dir(tmp_path)
    vocoder_dir = _make_vocoder_dir(tmp_path)
    out_dir = tmp_path / "out"
    singer_dir = _make_singer_dir(tmp_path)

    # --- 1回目: 完走させて旧世代を作る（合成本体は monkeypatch でスキップ）。
    fake_record = {
        "wav_path": "fake.wav", "wav_sha256": "0" * 64, "wav_duration_sec": 0.1,
        "wav_rms": 0.0, "wav_peak": 0.0,
        "score": {"n_phonemes": 0},
        "stage3_dual_encoding_diverged": False, "stage3_mode": "fake",
        "stage3_depth": None, "stage3_steps": None, "stage3_speaker": None,
    }
    monkeypatch.setattr(gs, "synth_song", lambda *a, **k: dict(fake_record))

    args1 = _make_run_args(
        diffsinger_repo=diffsinger_repo, ckpt_dir=ckpt_dir_1,
        canon_model_dir=canon_model_dir, vocoder_dir=vocoder_dir,
        out_dir=out_dir, singer_dir=singer_dir,
    )
    gs.cmd_run(args1)

    onnx_out_path = out_dir / "onnx_gate_5000"
    summary_path = out_dir / "step_5000" / "gate_synth_summary.json"
    assert onnx_out_path.exists()
    assert summary_path.exists()
    old_generation_onnx_bytes = (onnx_out_path / "acoustic.onnx").read_bytes()
    assert old_generation_onnx_bytes == b"ONNX-GEN-1"
    old_generation_summary_bytes = summary_path.read_bytes()

    # --- 2回目: export は成功する（別バイト列の新世代）が、synthesis 段で例外。
    (diffsinger_repo / "scripts" / "export.py").write_text(
        _EXPORT_PY_TEMPLATE.format(onnx_bytes=b"ONNX-GEN-2-SHOULD-BE-ROLLED-BACK"),
        encoding="utf-8",
    )
    ckpt_dir_2 = _make_ckpt_dir(tmp_path, "ckpt_2", b"CKPT-2", b"CONFIG-2")

    def _raise_during_synthesis(*_a, **_k):
        raise RuntimeError("synthesis stage failure (test-injected)")

    monkeypatch.setattr(gs, "synth_song", _raise_during_synthesis)

    args2 = _make_run_args(
        diffsinger_repo=diffsinger_repo, ckpt_dir=ckpt_dir_2,
        canon_model_dir=canon_model_dir, vocoder_dir=vocoder_dir,
        out_dir=out_dir, singer_dir=singer_dir,
    )
    with pytest.raises(RuntimeError, match="synthesis stage failure"):
        gs.cmd_run(args2)

    # canonical (onnx_gate_5000) は旧世代のまま/復元されている——新世代
    # (ONNX-GEN-2-SHOULD-BE-ROLLED-BACK) が公開されたまま残っていない。
    assert onnx_out_path.exists()
    assert (onnx_out_path / "acoustic.onnx").read_bytes() == old_generation_onnx_bytes
    # summary も旧世代のまま（新規 build_dir は未公開のまま finally で掃除される）。
    assert summary_path.exists()
    assert summary_path.read_bytes() == old_generation_summary_bytes
    # ロールバックで .old 自体は消費され（out_dir へ rename）、build_dir も
    # 残置しない。
    assert not (out_dir / "onnx_gate_5000.old").exists()
    leftover_build_dirs = [
        p for p in out_dir.iterdir()
        if p.name.startswith(".onnx_gate_5000.build-")
        or p.name.startswith("step_5000.build-")
    ]
    assert leftover_build_dirs == []


def test_cmd_run_publishes_onnx_when_run_completes_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常系の回帰ガード: 完走した run では、`onnx_gate_<step>` も
    `gate_synth_summary.json` も新世代へ公開されること（rollback wrapper が
    正常完走時には一切介入しないことの確認）。"""
    diffsinger_repo = tmp_path / "diffsinger_repo"
    _make_diffsinger_export_repo(diffsinger_repo, b"ONNX-GEN-OK")
    ckpt_dir = _make_ckpt_dir(tmp_path, "ckpt", b"CKPT", b"CONFIG")
    canon_model_dir = _make_canon_model_dir(tmp_path)
    vocoder_dir = _make_vocoder_dir(tmp_path)
    out_dir = tmp_path / "out"
    singer_dir = _make_singer_dir(tmp_path)

    fake_record = {
        "wav_path": "fake.wav", "wav_sha256": "0" * 64, "wav_duration_sec": 0.1,
        "wav_rms": 0.0, "wav_peak": 0.0,
        "score": {"n_phonemes": 0},
        "stage3_dual_encoding_diverged": False, "stage3_mode": "fake",
        "stage3_depth": None, "stage3_steps": None, "stage3_speaker": None,
    }
    monkeypatch.setattr(gs, "synth_song", lambda *a, **k: dict(fake_record))

    args = _make_run_args(
        diffsinger_repo=diffsinger_repo, ckpt_dir=ckpt_dir,
        canon_model_dir=canon_model_dir, vocoder_dir=vocoder_dir,
        out_dir=out_dir, singer_dir=singer_dir,
    )
    gs.cmd_run(args)

    onnx_out_path = out_dir / "onnx_gate_5000"
    summary_path = out_dir / "step_5000" / "gate_synth_summary.json"
    assert (onnx_out_path / "acoustic.onnx").read_bytes() == b"ONNX-GEN-OK"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["acoustic_onnx_path"] == str(onnx_out_path / "acoustic.onnx")
