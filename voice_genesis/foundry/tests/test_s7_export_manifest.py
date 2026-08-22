"""test_s7_export_manifest.py — ONNX を export 元 checkpoint へ縛る（PR #303 第 2 巡 P1）。

レンダ側は checkpoint を pin 照合するだけで、**実際に読み込む ONNX** とは何も
結び付いていなかった。正しい `--ckpt` と別物の ONNX を渡せば「事前登録 checkpoint で
測った」と名乗る spec を凍結できた。`verify_export_manifest` はそれを止める。

ONNX は checkpoint から決定論的に再生成できない（再 export でバイト列が変わる）ので、
事前登録に ONNX の sha を書く方式は取れない。代わりに **export 時点の対応**を記録し、
レンダ前に照合する。外し方 1 つにつき 1 テストで固定する。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_export_manifest.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_export_manifest as xm  # noqa: E402
import s7_io  # noqa: E402

GEN = "run7"


@pytest.fixture()
def bundle(tmp_path: Path):
    """checkpoint 1 個 + 生成物 3 個の最小束（中身は本物である必要が無い）。"""
    ckpt = tmp_path / "model_ckpt_steps_40000.ckpt"
    ckpt.write_bytes(b"checkpoint-bytes")
    artifacts = {}
    for name, content in (
        ("acoustic_onnx", b"onnx-bytes"),
        ("acoustic_dsconfig", b"dsconfig-bytes"),
        ("acoustic_phonemes_json", b"phonemes-bytes"),
    ):
        f = tmp_path / name
        f.write_bytes(content)
        artifacts[name] = f
    manifest = tmp_path / "export_manifest.json"
    doc = xm.build_manifest(GEN, ckpt, artifacts)
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return manifest, ckpt, artifacts


def _ckpt_sha(ckpt: Path) -> str:
    return s7_io.sha256_bytes(ckpt.read_bytes())


def test_a_manifest_written_at_export_time_binds_the_artifacts(bundle):
    manifest, ckpt, artifacts = bundle
    out = xm.verify_export_manifest(manifest, GEN, _ckpt_sha(ckpt), artifacts)
    assert out["generation"] == GEN
    assert out["source_checkpoint_sha256"] == _ckpt_sha(ckpt)
    assert out["verified_artifacts"] == sorted(artifacts)
    assert out["exporter"]["revision"] == xm.DEFAULT_EXPORTER["revision"]


def test_an_onnx_from_another_export_is_rejected(bundle):
    """**これが本命**: ckpt は事前登録どおりなのに ONNX だけ別物、という取り違え。"""
    manifest, ckpt, artifacts = bundle
    artifacts["acoustic_onnx"].write_bytes(b"onnx-bytes-from-another-export")
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, _ckpt_sha(ckpt), artifacts)


def test_a_manifest_from_another_checkpoint_is_rejected(bundle):
    manifest, ckpt, artifacts = bundle
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, "0" * 64, artifacts)


def test_a_manifest_relabeled_to_another_generation_is_rejected(bundle):
    manifest, ckpt, artifacts = bundle
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, "run5", _ckpt_sha(ckpt), artifacts)


def test_an_artifact_absent_from_the_manifest_is_rejected(bundle, tmp_path):
    """記録に無いファイルを読み込もうとしたら、その由来は言えない。"""
    manifest, ckpt, artifacts = bundle
    extra = tmp_path / "speaker_embed"
    extra.write_bytes(b"emb-bytes")
    artifacts["speaker_embed"] = extra
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, _ckpt_sha(ckpt), artifacts)


def test_a_foreign_schema_is_rejected(bundle):
    manifest, ckpt, artifacts = bundle
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["schema"] = "something-else/0.1"
    manifest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(xm.ExportManifestError):
        xm.verify_export_manifest(manifest, GEN, _ckpt_sha(ckpt), artifacts)


def test_the_calibration_generation_is_derived_from_the_pinned_checkpoint():
    """校正レンダの世代名は**手書きしない** — 事前登録の ckpt sha から引く。"""
    import s7_calib_render as cr

    cal, _, _ = s7_io.read_json_with_pin(cr.CALIBRATION_SET_PATH)
    pinned = str(cal["real_render_set"]["render_path"]["checkpoint"]["sha256"])
    assert cr.calibration_generation(pinned) == "run7"
    with pytest.raises(cr.PinMismatch):
        cr.calibration_generation("0" * 64)


def test_both_render_entry_points_require_an_export_manifest():
    """`--export-manifest` を省いた呼び出しは**引数解析の時点で**落ちる。"""
    import s7_calib_render as cr

    with pytest.raises(SystemExit):
        cr.main([
            "--canon-model-dir", "x", "--vocoder-dir", "x", "--acoustic-onnx", "x",
            "--acoustic-dsconfig", "x", "--acoustic-phonemes-json", "x",
            "--canon-phonemes-txt", "x", "--speaker-emb", "x", "--ckpt", "x",
            "--canon-zip", "x", "--vocoder-container", "x",
            "--out-dir", "x", "--manifest-out", "x",
        ])
    assert "--export-manifest" in (_RUN8 / "s7_0b_probe.py").read_text(encoding="utf-8")
