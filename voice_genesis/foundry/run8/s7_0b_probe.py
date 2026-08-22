"""run8/s7_0b_probe.py — Run 8-0b の probe 実行器（36 セル × 話者 × 世代）。

事前登録 `s7_0b_probe_spec.json` を **pin 照合してから**回し、1 群
（同一話者 × 同一世代）ぶんの結果 JSON を書く。**1 target event = 1 render**
（§4-0）。P-ANCHOR は実曲フル譜面を**同一バッチで再レンダ**して該当区間を
切り出す（§4-2。既存 wav は流用しない）。

測定は `s7_trf.py`（凍結済み 1.0 spec の候補のみ）。正規化は一切しない（§5-2）。
達成条件は「全セルがレンダされたこと」ではなく **「全セルの帰結が記帳された
こと」**（§11 AC）なので、失敗も `status` 付きで必ず 1 行残す。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_export_manifest as xm  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402
import s7_trf as trf  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_PATH = RESULTS_DIR / "s7_0b_probe_spec.json"
GATE_SYNTH_PATH = _HERE.parent / "s1_gate" / "gate_synth.py"
SINGER_DIR = _HERE.parents[1] / "singer"
GROUP_SCHEMA = sp.PROBE_GROUP_SCHEMA


class ProbeSpecMismatch(RuntimeError):
    """事前登録と実体が食い違った（fail-closed）。"""


def load_gate_synth():
    spec = importlib.util.spec_from_file_location("gate_synth", GATE_SYNTH_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_synth"] = module
    spec.loader.exec_module(module)
    return module


def verify_spec(spec: Dict[str, Any]) -> None:
    """譜面モジュールと測定仕様の pin が実体と一致することを確かめる。"""
    if spec.get("schema") != "s7-0b-probe-spec/0.1":
        raise ProbeSpecMismatch(f"schema {spec.get('schema')!r}")
    for name, sha in spec["score_modules"].items():
        got = hashlib.sha256((SINGER_DIR / name).read_bytes()).hexdigest()
        if got != sha:
            raise ProbeSpecMismatch(f"{name}: sha256 {got} != pin {sha}")
    m = spec["measurement"]["trf_measurement_spec"]
    got = hashlib.sha256(trf.TRF_SPEC_PATH.read_bytes()).hexdigest()
    if got != m["sha256"]:
        raise ProbeSpecMismatch(f"trf_measurement_spec: {got} != pin {m['sha256']}")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_wav(path: Path, y: np.ndarray, sr: int) -> Dict[str, Any]:
    """正規化せず 32-bit float で書く。決定論 pin は**標本列**の sha で行う。"""
    y32 = np.asarray(y, dtype=np.float32)
    sf.write(str(path), y32, sr, subtype="FLOAT")
    return {
        "wav": path.name,
        "wav_sha256": _sha_bytes(path.read_bytes()),
        "samples_sha256": _sha_bytes(np.ascontiguousarray(y32).tobytes()),
        "n_samples": int(y32.size),
        "duration_s": float(y32.size) / sr,
        "peak_raw": float(np.max(np.abs(y32))) if y32.size else 0.0,
        "normalized": False,
    }


def _measure_cell(
    frozen: trf.FrozenMeasurement,
    cell: Dict[str, Any],
    measurement: Dict[str, Any],
    target_note_index: int,
    target_beats: float,
    tempo_bpm: float,
) -> Dict[str, Any]:
    win = trf.windows_from_command(
        measurement, target_note_index, target_beats, tempo_bpm, frozen.tail_window_ms
    )
    y = np.asarray(measurement["raw_waveform"], dtype=np.float64)
    out: Dict[str, Any] = {
        "input_meta": {
            "input_tail_sp_frames": int(measurement["tail_frames"]),
            "commanded_note_frames": int(sum(win.target_phone_frames)),
            "note_target_frames": list(measurement["note_target_frames"]),
            "final_phone_dur": list(measurement["final_phone_dur"]),
            "real_phones": list(measurement["real_phones"]),
            "frame_ms": win.frame_ms,
            "note_onset_s": win.note_onset_s,
            "commanded_note_end_s": win.commanded_note_end_s,
            "score_boundary_s": win.score_boundary_s,
            "tail_window_ms": win.tail_window_ms,
        },
        "primary": trf.measure_primary(frozen, y, win, str(cell["cell_id"])),
        "tail_voiced_frames": trf.tail_voiced_frames(frozen, y, win, str(cell["cell_id"])),
        "duration": trf.measure_duration_axes(win),
        "acoustic": dict(trf.measure_hnr(y, win)),
        "waveform": {"energy_decay_slope": trf.measure_energy_decay_slope(y, win)},
    }
    mel = measurement.get("mel")
    if mel is not None:
        out["acoustic"]["vowel_drift_l1"] = trf.measure_vowel_drift(mel, win)
        out["terminal_mel"] = [
            float(v) for v in trf.terminal_mel_vector(mel, win, measurement)
        ]
    return out


def run_group(
    gate_synth,
    spec: Dict[str, Any],
    frozen: trf.FrozenMeasurement,
    spec_sha256: str,
    generation: str,
    speaker: str,
    acoustic_dir: Path,
    acoustic_stem: str,
    canon_model_dir: Path,
    vocoder_dir: Path,
    canon_phonemes_txt: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    """1 群（話者 × 世代）= 診断 33 セル + フル譜面 2 本（アンカー 3 セル）。

    `spec_sha256` は**起動時に読んだバイト列**の sha を受け取る。ここで
    `SPEC_PATH` を読み直すと、レンダ中に事前登録が再生成 / 編集された場合に
    「古い spec で測った値」を「新しい spec の sha」で名乗る（PR #303 第 6 巡 P1）。
    集計側の `validate_group_document` はこの digest を突き合わせるので、
    ずれた digest は誤った受理を生む。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    acoustic_onnx = acoustic_dir / f"{acoustic_stem}.onnx"
    dsconfig = acoustic_dir / "dsconfig.yaml"
    phonemes_json = acoustic_dir / f"{acoustic_stem}.phonemes.json"
    emb = acoustic_dir / f"{acoustic_stem}.{speaker}.emb"

    model_bytes, model_shas = gate_synth.load_model_bundle_bytes(
        canon_model_dir, vocoder_dir, acoustic_onnx, dsconfig
    )
    variance_phonemes, canon_sha = gate_synth.load_canon_phonemes_with_sha(canon_phonemes_txt)
    acoustic_phonemes, own_sha = gate_synth.load_own_phonemes_json_with_sha(phonemes_json)
    embed_vector, embed_sha = gate_synth.load_speaker_embed_vector_with_sha(emb)

    cells_by_id = {str(c["cell_id"]): c for c in spec["cells"]}
    results: List[Dict[str, Any]] = []

    # --- 診断 33 セル（1 セル = 1 レンダ） ---
    for cell in spec["cells"]:
        if cell.get("source") != "diagnostic_score":
            continue
        cid = str(cell["cell_id"])
        entry: Dict[str, Any] = {
            "cell_id": cid,
            "generation": generation,
            "speaker": speaker,
            "probe": cell["probe"],
            "pitch_key": cell["pitch_key"],
            "duration_key": cell["duration_key"],
            "position": cell["position"],
            "expected_terminal": cell["expected_terminal"],
            "outcome": "rendered",
        }
        try:
            song = gate_synth.DIAGNOSTIC_SONG_PREFIX + cid
            build_fn, b2s, tempo, path, module_shas = gate_synth.load_song_module(song, SINGER_DIR)
            notes = build_fn()
            record: Dict[str, Any] = {}
            measurement: Dict[str, Any] = {}
            t0 = time.time()
            y = gate_synth.run_pipeline(
                notes, b2s, tempo, model_bytes, variance_phonemes, acoustic_phonemes,
                record, speaker_name=speaker, speaker_embed_vector=embed_vector,
                measurement_out=measurement,
            )
            entry["render_elapsed_sec"] = time.time() - t0
            entry.update(_write_wav(out_dir / f"{cid.replace('|', '_')}.wav", y, int(measurement["sample_rate"])))
            entry.update(
                _measure_cell(frozen, cell, measurement, 0, float(cell["duration_beats"]), tempo)
            )
            entry["score_module_sha256"] = {Path(v[0]).name: v[1] for v in module_shas.values()}
        except Exception as exc:  # fail-closed: 帰結は必ず記帳する
            entry["outcome"] = "dropped"
            entry["status"] = "render_failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)

    # --- P-ANCHOR: 実曲フル譜面を同一バッチで再レンダして切り出す ---
    anchors = [c for c in spec["cells"] if c.get("source") == "full_song_render_excerpt"]
    for song_name in sorted({str(a["song"]) for a in anchors}):
        song_anchors = [a for a in anchors if a["song"] == song_name]
        try:
            build_fn, b2s, tempo, path, module_shas = gate_synth.load_song_module(
                song_name, SINGER_DIR
            )
            notes = build_fn()
            record, measurement = {}, {}
            t0 = time.time()
            y = gate_synth.run_pipeline(
                notes, b2s, tempo, model_bytes, variance_phonemes, acoustic_phonemes,
                record, speaker_name=speaker, speaker_embed_vector=embed_vector,
                measurement_out=measurement,
            )
            elapsed = time.time() - t0
            wav_meta = _write_wav(
                out_dir / f"anchor_{song_name}.wav", y, int(measurement["sample_rate"])
            )
        except Exception as exc:
            for a in song_anchors:
                results.append(
                    {
                        "cell_id": a["cell_id"], "generation": generation, "speaker": speaker,
                        "probe": "P-ANCHOR", "outcome": "dropped", "status": "render_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            continue
        for a in song_anchors:
            idx = [
                i for i, n in enumerate(notes) if n.phrase_index == int(a["phrase_index"])
            ]
            entry = {
                "cell_id": a["cell_id"], "generation": generation, "speaker": speaker,
                "probe": "P-ANCHOR", "pitch_key": a["pitch_key"],
                "duration_key": a["duration_key"], "position": a["position"],
                "expected_terminal": a["expected_terminal"], "outcome": "rendered",
                "song": song_name, "render_elapsed_sec": elapsed,
                "score_module_sha256": {Path(v[0]).name: v[1] for v in module_shas.values()},
            }
            entry.update(wav_meta)
            try:
                note_index = idx[int(a["target_mora_index_in_phrase"])]
                note = notes[note_index]
                if abs(float(note.midi) - float(a["pitch_midi"])) > 1e-9 or abs(
                    float(note.duration_beats) - float(a["duration_beats"])
                ) > 1e-9:
                    raise ValueError(
                        f"anchor note mismatch: midi {note.midi} vs {a['pitch_midi']}, "
                        f"beats {note.duration_beats} vs {a['duration_beats']}"
                    )
                entry.update(
                    _measure_cell(frozen, a, measurement, note_index, float(note.duration_beats), tempo)
                )
            except Exception as exc:
                entry["outcome"] = "dropped"
                entry["status"] = "anchor_excerpt_failed"
                entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)

    trf.add_contrast_axes(results)
    ringing = trf.apply_ringing_correction(results)

    # `terminal_mel`（128 次元）は 3 対照距離を作るための**中間生成物**で、その結果
    # （i_reference_distance / N_reference_distance / N_similarity_delta）は各セルに
    # 残っている。群 JSON は git に入れる成果物なので、消費済みの中間ベクトルは
    # 落とす（1 群 220 KB のうち 90 KB = 40% がこれだった）。§5-1 の 3 対照は
    # **話者内 = 群内**で閉じているため、群を跨いで再計算する用途は無い。
    for cell in results:
        cell.pop("terminal_mel", None)
    # score モジュールの sha は全セルで同一なので群レベルへ畳む（36 重複を 1 つに）。
    module_shas_all = {}
    for cell in results:
        module_shas_all.update(cell.pop("score_module_sha256", {}) or {})

    missing = set(cells_by_id) - {r["cell_id"] for r in results}
    if missing:
        raise ProbeSpecMismatch(f"帰結が記帳されていないセルがある: {sorted(missing)}")

    return {
        "schema": GROUP_SCHEMA,
        "generation": generation,
        "speaker": speaker,
        "n_cells": len(results),
        "n_rendered": sum(1 for r in results if r["outcome"] == "rendered"),
        "n_dropped": sum(1 for r in results if r["outcome"] == "dropped"),
        "ringing": ringing,
        "measurement_spec": {
            "sha256": frozen.spec_sha256,
            "spec_version": frozen.spec_version,
            "selected_candidates": {
                axis: frozen.axis_candidate_id(axis) for axis in sp.PRIMARY_AXES
            },
            "epsilon": dict(frozen.epsilons),
            "axes_without_machine_measure": list(trf.AXES_WITHOUT_MACHINE_MEASURE),
        },
        "probe_spec_sha256": spec_sha256,
        "score_module_sha256": module_shas_all,
        "dropped_from_cells": {
            "terminal_mel": (
                "3 対照距離を作る中間生成物。結果（i_reference_distance / "
                "N_reference_distance / N_similarity_delta）は各セルに残っている"
            ),
            "score_module_sha256": "全セル同一なので群レベルへ畳んだ（上のキー）",
        },
        "model_sha256": dict(model_shas),
        "aux_sha256": {
            "canon_phonemes_txt": canon_sha,
            "acoustic_phonemes_json": own_sha,
            "speaker_embed": embed_sha,
            "gate_synth_py": gate_synth._GATE_SYNTH_PY_LOAD_TIME_SHA256,
            "s7_trf_py": hashlib.sha256((_HERE / "s7_trf.py").read_bytes()).hexdigest(),
            "s7_0b_probe_py": hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest(),
        },
        "out_dir": str(out_dir.resolve()),
        "cells": results,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generation", required=True, choices=["run5", "run6", "run7"])
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--acoustic-dir", required=True)
    ap.add_argument("--acoustic-stem", required=True)
    ap.add_argument(
        "--export-manifest", required=True,
        help="`s7_export_manifest.py` が export 時に書いた記録。--acoustic-dir の "
             "生成物を、事前登録が世代ごとに pin する checkpoint へ縛る",
    )
    ap.add_argument("--canon-model-dir", required=True)
    ap.add_argument("--vocoder-dir", required=True)
    ap.add_argument("--canon-phonemes-txt", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--result-out", required=True)
    args = ap.parse_args(list(argv) if argv is not None else None)

    spec, spec_sha, _ = s7_io.read_json_with_pin(SPEC_PATH)
    verify_spec(spec)
    if args.speaker not in spec["expansion"]["generations"][args.generation]["speakers"]:
        raise ProbeSpecMismatch(
            f"{args.generation} に話者 {args.speaker} は事前登録されていない"
        )
    # 従来は「任意の acoustic ディレクトリ + stem」に `--generation` ラベルを
    # **貼るだけ**だった（PR #303 第 2 巡 P1）。事前登録は世代ごとに
    # `checkpoint_sha256` を pin しているので、export 時の記録で ONNX をそこへ縛る。
    acoustic_dir, stem = Path(args.acoustic_dir), args.acoustic_stem
    export_binding = xm.verify_export_manifest(
        Path(args.export_manifest),
        generation=str(args.generation),
        expected_checkpoint_sha256=str(
            spec["expansion"]["generations"][args.generation]["checkpoint_sha256"]
        ),
        artifacts={
            "acoustic_onnx": acoustic_dir / f"{stem}.onnx",
            "acoustic_dsconfig": acoustic_dir / "dsconfig.yaml",
            "acoustic_phonemes_json": acoustic_dir / f"{stem}.phonemes.json",
            "speaker_embed": acoustic_dir / f"{stem}.{args.speaker}.emb",
        },
    )
    print(
        f"| export binding verified: {args.generation} <- ckpt "
        f"{export_binding['source_checkpoint_sha256'][:16]}"
    )

    frozen = trf.load_frozen_measurement()
    gate_synth = load_gate_synth()

    out_path = Path(args.result_out)
    s7_io.reject_output_collision(
        [out_path], [SPEC_PATH, trf.TRF_SPEC_PATH, Path(args.export_manifest)]
    )
    doc = run_group(
        gate_synth, spec, frozen, spec_sha, args.generation, args.speaker,
        Path(args.acoustic_dir), args.acoustic_stem, Path(args.canon_model_dir),
        Path(args.vocoder_dir), Path(args.canon_phonemes_txt), Path(args.out_dir),
    )
    # 検証した束縛を成果物へ残す（検証したが記録しない、では後から言えない）
    doc["export_binding"] = export_binding
    s7_io.assert_json_finite(doc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"| {args.generation}/{args.speaker}: {doc['n_rendered']} rendered / "
        f"{doc['n_dropped']} dropped, ringing={doc['ringing']['group_status']} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
