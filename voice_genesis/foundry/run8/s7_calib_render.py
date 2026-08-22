"""run8/s7_calib_render.py — B-1 校正用 real-render セットのレンダ実行器。

事前登録（`results_s7/s7_b1_calibration_set.json` の `real_render_set`）が
指す 11 条件を、run7 と**同じ DiffSinger / vocoder 経路**
（`s1_gate/gate_synth.run_pipeline`）で 1 回ずつレンダし、波形と
`s7_b1_real_render_manifest.json`（境界・命令・pin）を書く。

原則:

- **本番 360 セルを 1 セルも通さない**。譜面は校正専用（C4 / 0.75・1.5・3.0 拍）。
- **正規化しない**。`gate_synth.synth_song` が行うピーク正規化（0.6）は
  条件間の振幅関係を壊し、`gain_invariance` 役の派生（×1.0 / ×0.5）が
  意味を失うため、ここでは vocoder 出力をそのまま 32-bit float WAV へ書く。
- **境界は命令から**取る（波形から推定しない）。`s7_calib_score` が命令
  フレームから算出した `note_onset_s` / `commanded_note_end_s` /
  `score_boundary_s` をそのまま manifest へ記帳する。
- **pin は事前登録と照合してから**回す（ckpt / canon zip / vocoder 容器。
  いずれも事前登録 `render_path` に sha256 がある）。不一致は停止。

出力の測定は `s7_b1_calibration.py --real-render <manifest>` が行う
（測定スタックは `ANALYSIS_STACK_PIN` 側で fail-closed 照合される）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_calib_score as cs  # noqa: E402
import s7_export_manifest as xm  # noqa: E402
import s7_io  # noqa: E402
import s7_spec  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
CALIBRATION_SET_PATH = RESULTS_DIR / "s7_b1_calibration_set.json"
PROBE_SPEC_PATH = RESULTS_DIR / "s7_0b_probe_spec.json"
GATE_SYNTH_PATH = _HERE.parent / "s1_gate" / "gate_synth.py"
MANIFEST_SCHEMA = s7_spec.REAL_RENDER_MANIFEST_SCHEMA

# 事前登録が pin している容器（`real_render_set.render_path`）。
CONTAINER_PINS = (
    ("checkpoint", ("checkpoint", "sha256")),
    ("canon_models", ("canon_models", "zip_sha256")),
    ("vocoder", ("vocoder", "container_sha256")),
)

RENDER_STACK_PACKAGES = ("numpy", "onnxruntime", "soundfile", "PyYAML")


class PinMismatch(RuntimeError):
    """事前登録の sha256 と実ファイルが食い違った（fail-closed）。"""


def load_gate_synth():
    """`s1_gate/gate_synth.py` を実体パスから読み込む（本番と同じ実装を使う）。

    `gate_synth` はロード時に自分自身を sha256 する
    （`_GATE_SYNTH_PY_LOAD_TIME_SHA256`）ので、その値を provenance として使う。
    """
    spec = importlib.util.spec_from_file_location("gate_synth", GATE_SYNTH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GATE_SYNTH_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_synth"] = module
    spec.loader.exec_module(module)
    return module


def verify_container_pins(
    real_render_set: Dict[str, Any], containers: Dict[str, Optional[Path]]
) -> Dict[str, Dict[str, str]]:
    """事前登録 `render_path` の 3 容器を sha256 照合する（渡された分だけ）。"""
    out: Dict[str, Dict[str, str]] = {}
    rp = real_render_set["render_path"]
    for name, (key, sub) in CONTAINER_PINS:
        path = containers.get(name)
        expected = str(rp[key][sub])
        if path is None:
            raise PinMismatch(
                f"container {name!r} は事前登録が pin している（{expected}）。"
                "照合対象のパスを渡すこと（fail-closed）。"
            )
        _, observed, size = s7_io.read_bytes_with_pin(Path(path))
        if observed != expected:
            raise PinMismatch(
                f"{name}: {path} sha256 {observed} != prereg {expected}"
            )
        out[name] = {"path": str(Path(path).resolve()), "sha256": observed, "bytes": str(size)}
    return out


def calibration_generation(checkpoint_sha256: str) -> str:
    """校正レンダの**世代名**を、事前登録が pin する checkpoint の sha から引く。

    `real_render_set.render_path` は checkpoint の sha は持つが世代名フィールドを
    持たない（凍結済みなので足せない）。世代名を手で書くと、それ自体が照合されない
    主張になる。`s7_0b_probe_spec.json` は世代 → `checkpoint_sha256` の対応を
    pin しているので、**そこから引く**（見つからなければ止める）。
    """
    spec, _, _ = s7_io.read_json_with_pin(PROBE_SPEC_PATH)
    hits = [
        gen for gen, info in spec["expansion"]["generations"].items()
        if str(info["checkpoint_sha256"]) == str(checkpoint_sha256)
    ]
    if len(hits) != 1:
        raise PinMismatch(
            f"checkpoint {checkpoint_sha256} に対応する世代が "
            f"{PROBE_SPEC_PATH.name} に {len(hits)} 件（1 件でなければ世代を名乗れない）"
        )
    return hits[0]


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _samples_sha256(y32: np.ndarray) -> str:
    """**標本そのもの**の sha256（決定論 pin）。

    WAV 容器の sha は再現しない: libsndfile は float WAV に PEAK チャンクを付け、
    そこに**書き出し時刻**が入る（同一音・別時刻のレンダで容器 sha が変わることを
    実測。2026-08-21）。したがって決定論の主張は標本列の sha で行い、`wav_sha256`
    は「その場のファイル実体」の照合にとどめる。
    """
    return hashlib.sha256(np.ascontiguousarray(y32, dtype=np.float32).tobytes()).hexdigest()


def render_conditions(
    gate_synth,
    renders: Sequence[cs.CalibrationRender],
    model_bytes: Dict[str, bytes],
    variance_phonemes: Dict[str, int],
    acoustic_phonemes: Dict[str, int],
    speaker_name: str,
    speaker_embed_vector: np.ndarray,
    out_dir: Path,
) -> List[Dict[str, Any]]:
    """11 条件を 1 回ずつレンダして WAV を書く（正規化しない・共通長へゼロ詰め）。"""
    entries: List[Dict[str, Any]] = []
    n_total = cs.padded_total_samples(list(renders))
    for r in renders:
        record: Dict[str, Any] = {"condition_id": r.condition_id}
        t0 = time.time()
        y = gate_synth.run_pipeline(
            list(r.notes),
            cs.beats_to_seconds,
            r.tempo_bpm,
            model_bytes,
            variance_phonemes,
            acoustic_phonemes,
            record,
            speaker_name=speaker_name,
            speaker_embed_vector=speaker_embed_vector,
            final_phone_dur_override=r.override,
        )
        elapsed = time.time() - t0
        y = np.asarray(y, dtype=np.float64)
        if not np.all(np.isfinite(y)):
            raise ValueError(f"{r.condition_id}: vocoder 出力に非有限値がある")
        n_rendered = int(y.size)
        if n_rendered > n_total:
            raise ValueError(
                f"{r.condition_id}: レンダ長 {n_rendered} が共通長 {n_total} を超えた"
            )
        y = np.concatenate([y, np.zeros(n_total - n_rendered, dtype=np.float64)])
        wav_path = out_dir / f"{r.condition_id}.wav"
        y32 = y.astype(np.float32)
        sf.write(str(wav_path), y32, cs.SAMPLE_RATE, subtype="FLOAT")
        entry = r.command_summary()
        entry.update(
            {
                "derived": False,
                "wav": wav_path.name,
                "wav_sha256": _sha256_of(wav_path),
                "samples_sha256": _samples_sha256(y32),
                "sample_rate_hz": cs.SAMPLE_RATE,
                "n_samples": int(y.size),
                "n_samples_rendered": n_rendered,
                "zero_padded_to_s": float(n_total) / cs.SAMPLE_RATE,
                "duration_s": float(y.size) / cs.SAMPLE_RATE,
                "rendered_duration_s": float(n_rendered) / cs.SAMPLE_RATE,
                "peak_raw": float(np.max(np.abs(y))) if y.size else 0.0,
                "rms_raw": float(np.sqrt(np.mean(y**2))) if y.size else 0.0,
                "normalized": False,
                "stage1_final_phone_dur_predicted": record.get(
                    "stage1_final_phone_dur_predicted"
                ),
                "stage1_final_phone_dur_applied": record.get("stage1_final_phone_dur"),
                "stage3_mode": record.get("stage3_mode"),
                "calibration_only_intervention": bool(
                    record.get("stage1_calibration_only_intervention", False)
                ),
                "render_elapsed_sec": elapsed,
            }
        )
        entries.append(entry)
        print(
            f"  {r.condition_id}: rendered {n_rendered / cs.SAMPLE_RATE:.3f}s "
            f"peak={entry['peak_raw']:.4f} sha={entry['wav_sha256'][:12]} ({elapsed:.1f}s)"
        )
    return entries


def write_zero_buffers(
    zero_buffers: Sequence[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    out_dir: Path,
) -> List[Dict[str, Any]]:
    """`zero_input_false_positive` 用の**厳密ゼロ**緩衝を書く（レンダではない）。

    2026-08-21 amendment: 要件が測るのは**測定器**の性質なので、刺激は経路に
    依存しない厳密ゼロでなければならない（SP-only render は経路が生成した
    非ゼロ波形であり、これとは別物 = `diagnostics.pipeline_silence_residual`）。
    長さ・標本化周波数・境界はレンダ条件とそろえる（測定窓の定義を共有するため）。
    """
    if not entries:
        raise ValueError("zero buffer は先にレンダ条件が要る（長さと境界を共有する）")
    ref = entries[0]
    out: List[Dict[str, Any]] = []
    for spec_entry in zero_buffers:
        zid = str(spec_entry["id"])
        n = int(ref["n_samples"])
        sr = int(ref["sample_rate_hz"])
        y = np.zeros(n, dtype=np.float32)
        wav_path = out_dir / f"{zid}.wav"
        sf.write(str(wav_path), y, sr, subtype="FLOAT")
        samples_sha = _samples_sha256(y)
        check, _ = sf.read(str(wav_path), dtype="float64", always_2d=False)
        if np.any(np.asarray(check) != 0.0):
            raise ValueError(f"{zid}: 書き出した緩衝が厳密ゼロでない")
        entry = {
            k: ref[k]
            for k in (
                "tempo_bpm",
                "beats",
                "note_target_frames",
                "frame_ms",
                "head_frames",
                "note_onset_s",
                "commanded_note_end_s",
                "score_boundary_s",
                "tail_window_ms",
                "sample_rate_hz",
            )
        }
        entry.update(
            {
                "condition_id": zid,
                "maps_to": str(spec_entry.get("kind", "synthetic_zero_buffer")),
                "kind": "synthetic_zero_buffer",
                "rendered": False,
                "derived": False,
                "wav": wav_path.name,
                "wav_sha256": _sha256_of(wav_path),
                "samples_sha256": samples_sha,
                "n_samples": n,
                "duration_s": float(n) / sr,
                "peak_raw": 0.0,
                "rms_raw": 0.0,
                "normalized": False,
                "override_kind": None,
                "calibration_only_intervention": False,
                "note": "bit-exact zeros。事前登録 real_render_set.zero_buffers に基づく",
            }
        )
        out.append(entry)
        print(f"  {zid}: zero buffer ({n} samples) sha={entry['wav_sha256'][:12]}")
    return out


def render_derived(
    derived: Sequence[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    out_dir: Path,
) -> List[Dict[str, Any]]:
    """派生条件（線形ゲイン）。**再レンダしない**（事前登録どおり）。"""
    by_id = {e["condition_id"]: e for e in entries}
    out: List[Dict[str, Any]] = []
    for cond in derived:
        cid = str(cond["id"])
        base_id = str(cond["derived_from"])
        if base_id not in by_id:
            raise KeyError(f"{cid}: derived_from {base_id!r} が未レンダ")
        base = by_id[base_id]
        gain = float(cond.get("gain", 1.0))
        base_path = out_dir / str(base["wav"])
        # 直前に自分が書いた WAV でも、記録した pin と照合してから読む
        # （PR #303 レビュー指摘: WAV を測る/派生させる全経路を s7_io へ集約）。
        y64, sr = s7_io.read_wav_with_pins(
            base_path, base["wav_sha256"], base["samples_sha256"]
        )
        y = np.asarray(y64, dtype=np.float32) * np.float32(gain)
        wav_path = out_dir / f"{cid}.wav"
        sf.write(str(wav_path), y, int(sr), subtype="FLOAT")
        samples_sha = _samples_sha256(y)
        entry = {
            k: base[k]
            for k in (
                "tempo_bpm",
                "beats",
                "pitch_midi_prereg",
                "pitch_midi_applied",
                "phones",
                "note_target_frames",
                "frame_ms",
                "head_frames",
                "note_onset_s",
                "commanded_note_end_s",
                "score_boundary_s",
                "tail_window_ms",
                "terminal_extension_ms_prereg",
                "terminal_extension_frames_applied",
                "terminal_extension_ms_applied",
                "onset_ratio_prereg",
                "override_kind",
                "sample_rate_hz",
                "stage3_mode",
                "n_samples_rendered",
                "zero_padded_to_s",
                "rendered_duration_s",
                "calibration_only_intervention",
            )
        }
        entry.update(
            {
                "condition_id": cid,
                "maps_to": str(cond["maps_to"]),
                "derived": True,
                "derived_from": base_id,
                "gain": gain,
                "wav": wav_path.name,
                "wav_sha256": _sha256_of(wav_path),
                "samples_sha256": samples_sha,
                "n_samples": int(y.size),
                "duration_s": float(y.size) / int(sr),
                "peak_raw": float(np.max(np.abs(y))) if y.size else 0.0,
                "rms_raw": float(np.sqrt(np.mean(y.astype(np.float64) ** 2))) if y.size else 0.0,
                "normalized": False,
            }
        )
        out.append(entry)
        print(f"  {cid}: derived from {base_id} × {gain} sha={entry['wav_sha256'][:12]}")
    return out


def build_manifest(
    prereg_sha: str,
    containers: Dict[str, Dict[str, str]],
    model_shas: Dict[str, str],
    aux_shas: Dict[str, Optional[str]],
    entries: List[Dict[str, Any]],
    speaker_name: str,
    out_dir: Path,
    export_binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        # レンダに使った ONNX が、事前登録 pin の checkpoint から export された
        # ものであることの照合結果（PR #303 第 2 巡 P1）
        "export_binding": export_binding,
        "authority": "User 裁定 2026-08-21 §1（裁定 1 = (b)）/ STEP 6",
        "prereg": {
            "path": str(CALIBRATION_SET_PATH.relative_to(_HERE.parents[2])),
            "sha256": prereg_sha,
            "section": "real_render_set",
        },
        "separation": {
            "production_cells_touched": 0,
            "label_inputs_used": [],
            "note": "本番 360 セル・run5/6/7 の good/bad ラベル・P-ANCHOR 結果は一切参照していない",
        },
        "render_path": {
            "harness": "voice_genesis/foundry/s1_gate/gate_synth.py::run_pipeline",
            "speaker": speaker_name,
            "normalization": "none (vocoder 出力そのまま。32-bit float WAV)",
            "containers": containers,
            "model_sha256": dict(model_shas),
            "aux_sha256": dict(aux_shas),
        },
        "render_stack": {
            pkg: importlib.metadata.version(pkg) for pkg in RENDER_STACK_PACKAGES
        }
        | {"python": platform.python_version()},
        "out_dir": str(out_dir.resolve()),
        "n_rendered": sum(
            1 for e in entries if not e["derived"] and e.get("kind") != "synthetic_zero_buffer"
        ),
        "n_derived": sum(1 for e in entries if e["derived"]),
        "n_zero_buffers": sum(1 for e in entries if e.get("kind") == "synthetic_zero_buffer"),
        "conditions": entries,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canon-model-dir", required=True)
    ap.add_argument("--vocoder-dir", required=True)
    ap.add_argument("--acoustic-onnx", required=True)
    ap.add_argument("--acoustic-dsconfig", required=True)
    ap.add_argument("--acoustic-phonemes-json", required=True)
    ap.add_argument("--canon-phonemes-txt", required=True)
    ap.add_argument("--speaker", default="ritsu")
    ap.add_argument("--speaker-emb", required=True)
    ap.add_argument("--ckpt", required=True, help="事前登録 pin 照合用（読み込みはしない）")
    ap.add_argument(
        "--export-manifest", required=True,
        help="`s7_export_manifest.py` が export 時に書いた記録。--acoustic-onnx を "
             "--ckpt へ縛る（無いと『事前登録 ckpt で測った』と名乗れない）",
    )
    ap.add_argument("--canon-zip", required=True, help="事前登録 pin 照合用")
    ap.add_argument("--vocoder-container", required=True, help="事前登録 pin 照合用（.oudep）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--manifest-out", required=True)
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest_out)
    s7_io.reject_output_collision(
        [manifest_path, out_dir],
        [
            Path(args.canon_model_dir),
            Path(args.vocoder_dir),
            CALIBRATION_SET_PATH,
            GATE_SYNTH_PATH,
            Path(args.acoustic_onnx),
            Path(args.acoustic_dsconfig),
            Path(args.acoustic_phonemes_json),
            Path(args.canon_phonemes_txt),
            Path(args.speaker_emb),
            Path(args.ckpt),
            Path(args.canon_zip),
            Path(args.vocoder_container),
            Path(args.export_manifest),
        ],
    )

    cal, prereg_sha, _ = s7_io.read_json_with_pin(CALIBRATION_SET_PATH)
    rrs = cal["real_render_set"]
    containers = verify_container_pins(
        rrs,
        {
            "checkpoint": Path(args.ckpt),
            "canon_models": Path(args.canon_zip),
            "vocoder": Path(args.vocoder_container),
        },
    )
    print("| container pins verified:", ", ".join(sorted(containers)))

    # 容器 pin だけでは「読み込む ONNX」が checkpoint と無関係のままになる
    # （PR #303 第 2 巡 P1）。export 時の記録で両者を縛ってからレンダする。
    export_binding = xm.verify_export_manifest(
        Path(args.export_manifest),
        generation=calibration_generation(containers["checkpoint"]["sha256"]),
        expected_checkpoint_sha256=containers["checkpoint"]["sha256"],
        artifacts={
            "acoustic_onnx": Path(args.acoustic_onnx),
            "acoustic_dsconfig": Path(args.acoustic_dsconfig),
            "acoustic_phonemes_json": Path(args.acoustic_phonemes_json),
            "speaker_embed": Path(args.speaker_emb),
        },
    )
    print(
        "| export binding verified:",
        f"ckpt {export_binding['source_checkpoint_sha256'][:16]}"
        f" -> {', '.join(export_binding['verified_artifacts'])}",
    )

    renders = cs.build_all_renders(rrs)
    derived = cs.derived_conditions(rrs)
    print(f"| conditions: {len(renders)} renders + {len(derived)} derived")

    gate_synth = load_gate_synth()
    model_bytes, model_shas = gate_synth.load_model_bundle_bytes(
        Path(args.canon_model_dir),
        Path(args.vocoder_dir),
        Path(args.acoustic_onnx),
        Path(args.acoustic_dsconfig),
    )
    variance_phonemes, canon_sha = gate_synth.load_canon_phonemes_with_sha(
        Path(args.canon_phonemes_txt)
    )
    acoustic_phonemes, own_sha = gate_synth.load_own_phonemes_json_with_sha(
        Path(args.acoustic_phonemes_json)
    )
    embed_vector, embed_sha = gate_synth.load_speaker_embed_vector_with_sha(
        Path(args.speaker_emb)
    )
    aux_shas: Dict[str, Optional[str]] = {
        "canon_phonemes_txt": canon_sha,
        "acoustic_phonemes_json": own_sha,
        "speaker_embed": embed_sha,
        "gate_synth_py": gate_synth._GATE_SYNTH_PY_LOAD_TIME_SHA256,
        "s7_calib_score_py": _sha256_of(_HERE / "s7_calib_score.py"),
        "s7_calib_render_py": _sha256_of(Path(__file__).resolve()),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    entries = render_conditions(
        gate_synth,
        renders,
        model_bytes,
        variance_phonemes,
        acoustic_phonemes,
        str(args.speaker),
        embed_vector,
        out_dir,
    )
    entries.extend(render_derived(derived, entries, out_dir))
    entries.extend(write_zero_buffers(rrs.get("zero_buffers", []), entries, out_dir))

    manifest = build_manifest(
        prereg_sha, containers, model_shas, aux_shas, entries, str(args.speaker), out_dir,
        export_binding=export_binding,
    )
    s7_io.assert_json_finite(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"| manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
