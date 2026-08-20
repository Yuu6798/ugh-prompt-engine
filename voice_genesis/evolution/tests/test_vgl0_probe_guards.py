"""VG-L0 probe / checker の **破壊的書き込みガード**を実測なしで検査する。

PR #289 の 7 巡目レビュー（chatgpt-codex-connector）が指摘した 2 件を閉じた
ことを、モデル資産なしで固定するためのテスト:

1. **checker の派生 acoustic 入力が保護されていない** — `--acoustic-onnx` が
   `--acoustic-dir` の外にあると、そこから派生する `*.phonemes.json` /
   `*.<spk>.emb` は checker 側の保護リストに載らず、`--result-json` がそれを
   指すと全 probe 完走後にモデル入力を上書きする
2. **所有外の checker result ファイルを unlink する** — `--work-dir` を既存の
   無関係なディレクトリにすると、`order_forward.json` のような固定名の
   ファイルが所有検査なしに消える

どちらも「合成が終わってから入力を壊す」型なので、**起動前に fail-closed で
弾けているか**だけを見る。ONNX 資産も onnxruntime も要らない。

`gate_synth` が module import 時に `onnxruntime` を要求するため、import だけ
スタブで通す（実行経路には一切入らない — 本テストはどれも合成の手前で
`SystemExit` になることを確認する）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBES = REPO_ROOT / "voice_genesis" / "evolution" / "probes"


def _import_checker():
    """onnxruntime をスタブして checker を import する。

    スタブは import の間だけ差し込み、後片付けする（他テストが本物の
    onnxruntime の有無で分岐するのを壊さないため）。
    """
    if "vgl0_reproducibility_check" in sys.modules:
        return sys.modules["vgl0_reproducibility_check"]
    stub = types.ModuleType("onnxruntime")
    stub.InferenceSession = object
    stub.get_available_providers = lambda: ["CPUExecutionProvider"]
    stub.__version__ = "0.0.0-stub"
    had = "onnxruntime" in sys.modules
    sys.modules.setdefault("onnxruntime", stub)
    sys.path.insert(0, str(PROBES))
    try:
        import vgl0_reproducibility_check as chk  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # numpy / soundfile / yaml が無い環境
        pytest.skip(f"probe の依存が無い環境: {exc}")
    finally:
        sys.path.remove(str(PROBES))
        if not had and sys.modules.get("onnxruntime") is stub:
            del sys.modules["onnxruntime"]
    return chk


@pytest.fixture(scope="module")
def chk():
    return _import_checker()


def _model_args(tmp_path: Path) -> list[str]:
    """`--acoustic-onnx` を `--acoustic-dir` の **外**に置いた引数一式。

    この配置が指摘 1 の前提そのもの（派生ファイルは onnx のパスから作られる
    ので、acoustic-dir だけを保護しても覆えない）。
    """
    acoustic_dir = tmp_path / "export"
    onnx_dir = tmp_path / "elsewhere"
    acoustic_dir.mkdir(exist_ok=True)
    onnx_dir.mkdir(exist_ok=True)
    return [
        "--canon-dir", str(tmp_path / "canon"),
        "--vocoder-dir", str(tmp_path / "vocoder"),
        "--acoustic-dir", str(acoustic_dir),
        "--acoustic-onnx", str(onnx_dir / "acoustic_v1.onnx"),
        "--speaker", "ritsu",
    ]


def _derived(tmp_path: Path, suffix: str) -> Path:
    return tmp_path / "elsewhere" / f"acoustic_v1{suffix}"


@pytest.mark.parametrize(
    "suffix", [".phonemes.json", ".ritsu.emb", ".onnx"])
def test_checker_refuses_result_json_pointing_at_a_derived_model_input(
    chk, tmp_path: Path, suffix: str,
) -> None:
    """派生入力（phonemes.json / 話者 embed）と本体 onnx を、checker が
    `--result-json` の書き込み先として拒否すること。

    保護リストを checker 側で書き写していた頃は、`--acoustic-dir` の外に
    ある onnx から派生する 2 ファイルが抜けていた（レビュー指摘 P2）。
    いまは probe の `ProbeConfig.protected_inputs()` を流用しているので、
    probe が知っている入力は自動で覆われる。
    """
    target = _derived(tmp_path, suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"model-bytes")
    with pytest.raises(SystemExit):
        chk.main([*_model_args(tmp_path),
                  "--work-dir", str(tmp_path / "work"),
                  "--result-json", str(target)])
    # 弾かれた = 1 バイトも触っていないこと
    assert target.read_bytes() == b"model-bytes"


def test_checker_refuses_result_json_inside_the_singer_tree(
    chk, tmp_path: Path,
) -> None:
    """既定 singer ディレクトリの中身（楽譜）も書き込み先にできないこと。"""
    import vgl0_control_axis_probe as probe_mod  # noqa: PLC0415

    score = probe_mod.DEFAULT_SINGER_DIR / "score.py"
    with pytest.raises(SystemExit):
        chk.main([*_model_args(tmp_path),
                  "--work-dir", str(tmp_path / "work"),
                  "--result-json", str(score)])


def test_claim_work_dir_marks_a_fresh_directory(chk, tmp_path: Path) -> None:
    work = chk.claim_work_dir(tmp_path / "work")
    assert (work / chk.WORK_OWNER_MARKER).exists()
    # 2 度目は所有済みなので通る（再実行を妨げない）
    chk.claim_work_dir(work)


def test_claim_work_dir_refuses_an_unowned_non_empty_directory(
    chk, tmp_path: Path,
) -> None:
    """既存の無関係なディレクトリを work に使わせない。

    ここを通すと、その直下の `order_forward.json` 等の固定名ファイルが
    起動ごとに unlink される（レビュー指摘 P2）。
    """
    work = tmp_path / "someone_elses"
    work.mkdir()
    victim = work / "order_forward.json"
    victim.write_text("大事なデータ", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.claim_work_dir(work)
    assert victim.read_text(encoding="utf-8") == "大事なデータ"


def test_claim_work_dir_accepts_an_existing_empty_directory(
    chk, tmp_path: Path,
) -> None:
    work = tmp_path / "empty"
    work.mkdir()
    chk.claim_work_dir(work)
    assert (work / chk.WORK_OWNER_MARKER).exists()


def test_run_probe_refuses_to_unlink_outside_the_owned_work_dir(
    chk, tmp_path: Path,
) -> None:
    """結果ファイルが所有 work ディレクトリの直下にないなら、
    サブプロセスを起動する前に止まること（unlink の手前）。"""
    work = chk.claim_work_dir(tmp_path / "work")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        chk.run_probe("/nonexistent/python", [], work / "cond", outside,
                      owned_work_dir=work)
    assert outside.exists()


def test_run_probe_requires_the_owned_work_dir_argument(chk) -> None:
    """`owned_work_dir` は **キーワード必須**（渡し忘れを呼び出し時に落とす）。"""
    with pytest.raises(TypeError):
        chk.run_probe("python", [], Path("/tmp/out"), Path("/tmp/r.json"))
