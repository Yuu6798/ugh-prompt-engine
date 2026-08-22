"""debt/d4/d4_runner.py — VG-DEBT-004 (D4) run5-7 の TRF 1.2 再測定ランナー。

**machine-independent 部分**（計器の配線 + 事前登録 spec との pin 照合）のみを
本 PR の範囲とする。実行（レンダ・実測そのもの）は checkpoint / 音響モデル
実体に依存する machine-dependent 作業であり、本モジュールは`--help` 相当の
形状・fail-closed ガードだけを CI で検査する（`tests/test_d4_remeasure.py`）。

**凍結物は一切変更しない。** 本モジュールは以下を**読み取り専用で import 再利用**
するだけで、書き換えない:

- `run8/s7_0b_probe.py`（`verify_spec` / `load_gate_synth` / `run_group` を
  そのまま呼ぶ — render の契約は 8-0b probe と同一にする）
- `run8/s7_b1_v12.py`（`measure_candidate_12` を経由して 1.2 の測定ロジック
  = `analyse_shape` / `voiced_mask_12` を呼ぶ）
- `run8/s7_b1_calibration.py`（`Stimulus` / `measure_voicing_axes` /
  `verify_analysis_stack`）
- `run8/s7_io.py` / `s7_export_manifest.py` / `s7_trf.py` の pin ガード群

サブコマンド 2 つ:

- `render` — `s7_0b_probe.run_group` を直接呼び、1 群（話者 x 世代）ぶんの
  WAV + `input_meta`（測定窓）を書く。**測定は 1.0 のまま**（`run_group` の
  契約をそのまま使うため）で構わない — D4 が使うのは WAV と `input_meta` の
  みであり、1.0 測定値は消費しない（`d4_remeasure_spec_sha256` を束縛して
  レンダ出力に付す）。
- `measure` — `render` が書いた群 JSON（または同一 schema の 8-0b probe 群
  JSON）を読み、WAV を pin 照合してから `s7_b1_v12` の 1.2 選定候補で
  voicing 3 軸を測り直し、`d4_results.json` を書く。

fail-closed 起動ガード: 両サブコマンドとも、実行の**前**に本 spec
(`d4_remeasure_spec.json`) 自身と、その `pins` が指す 3 ファイル + セル定義
source の sha256 を実ファイルと照合する。1 つでも不一致なら `D4SpecMismatch`
で abort する（本番値を見て仕様を書き換える経路を構造的に閉じる）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent                 # .../debt/d4
_DEBT_DIR = _HERE.parent                                 # .../debt
_FOUNDRY_DIR = _DEBT_DIR.parent                           # .../foundry
_RUN8_DIR = _FOUNDRY_DIR / "run8"
_RESULTS_S7_DIR = _FOUNDRY_DIR / "results_s7"
_S1_GATE_DIR = _FOUNDRY_DIR / "s1_gate"
_REPO_ROOT = _FOUNDRY_DIR.parents[1]

if str(_RUN8_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN8_DIR))

# run8/ のモジュール群。numpy + librosa + soundfile はいずれも本体の必須依存
# なので、ここまでは常時 import してよい（onnxruntime を要するのは
# `s7_0b_probe.load_gate_synth()` 内だけで、これはレンダ実行時にしか呼ばない）。
import s7_b1_calibration as b1  # noqa: E402
import s7_b1_v12 as v12  # noqa: E402
import s7_export_manifest as xm  # noqa: E402
import s7_io  # noqa: E402
import s7_trf as trf  # noqa: E402
import s7_0b_probe as probe0b  # noqa: E402

SPEC_SCHEMA = "vg-d4-remeasure-spec/0.1"
RENDER_SCHEMA = "vg-d4-render-group-result/0.1"
RESULTS_SCHEMA = "vg-d4-remeasure-results/0.1"
DEBT_REF = "VG-DEBT-004"

SPEC_PATH = _HERE / "d4_remeasure_spec.json"

#: D4 が測る 3 軸（voicing のみ）。`terminal_mel_persistence` は closeout §2-2
#: により対象外（spec の `axes_out_of_scope` に明記）。
D4_AXES: Tuple[str, ...] = (
    "excess_tail_voiced_ms",
    "release_after_score_boundary_ms",
    "tail_f0_persistence",
)


class D4SpecMismatch(RuntimeError):
    """D4 事前登録 spec 自身、または pin 対象ファイルが期待 sha256 と食い違った
    （fail-closed）。"""


# --- fail-closed: 事前登録 spec の pin を実ファイルと照合 -------------------


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_and_verify_d4_spec(spec_path: Path = SPEC_PATH) -> Tuple[Dict[str, Any], str]:
    """`d4_remeasure_spec.json` を読み、schema・軸集合・pins 全件を実ファイルと
    照合してから返す。**測定より前に必ず呼ぶ**（両サブコマンドの入口)。"""
    spec, spec_sha, _ = s7_io.read_json_with_pin(spec_path)
    if spec.get("schema") != SPEC_SCHEMA:
        raise D4SpecMismatch(f"schema {spec.get('schema')!r} != {SPEC_SCHEMA!r}")
    if spec.get("debt_ref") != DEBT_REF:
        raise D4SpecMismatch(f"debt_ref {spec.get('debt_ref')!r} != {DEBT_REF!r}")

    axes = spec.get("axes", {})
    if set(axes) != set(D4_AXES):
        raise D4SpecMismatch(f"axes {sorted(axes)} != {sorted(D4_AXES)}（voicing 3軸のみ）")

    pins = spec.get("pins", {})
    sources = pins.get("sources", {})
    for key, rel_path in sources.items():
        want = pins.get(key)
        if not isinstance(want, str) or not want:
            raise D4SpecMismatch(f"pins.{key} が欠けている")
        got = _sha_file(_REPO_ROOT / rel_path)
        if got != want:
            raise D4SpecMismatch(
                f"{rel_path}: sha256 {got} が spec pin {key}={want} と違う"
                "（凍結物が pin 後に変わったか、pin 自体が陳腐化している）"
            )

    cds = pins.get("cell_definition_source", {})
    cds_path = cds.get("path")
    cds_sha = cds.get("sha256")
    if not cds_path or not cds_sha:
        raise D4SpecMismatch("pins.cell_definition_source に path/sha256 が欠けている")
    got_cds = _sha_file(_REPO_ROOT / cds_path)
    if got_cds != cds_sha:
        raise D4SpecMismatch(
            f"{cds_path}: sha256 {got_cds} が spec pin cell_definition_source と違う"
        )

    return spec, spec_sha


def _axis_candidates(spec: Dict[str, Any]) -> Dict[str, "v12.Cand12"]:
    return {axis: parse_voicing_candidate_id(str(cfg["selected_candidate"]))
            for axis, cfg in spec["axes"].items()}


# --- 1.2 候補 ID の再構成 ----------------------------------------------------

#: `s7_b1_v12.enumerate_candidates_12` の生成規則
#: (`f"{fam}|thr{thr:g}|win{win:g}|hop{hop:g}"`) の逆変換。
_CAND_ID_RE = re.compile(
    r"^(?P<family>[^|]+)\|thr(?P<thr>[^|]+)\|win(?P<win>[^|]+)\|hop(?P<hop>[^|]+)$"
)


def parse_voicing_candidate_id(candidate_id: str) -> "v12.Cand12":
    """凍結済み 1.2 spec が pin する `selected_candidate` 文字列から、
    `s7_b1_v12.voiced_mask_12` / `measure_candidate_12` に渡せる `Cand12` を
    再構成する（voicing 候補専用。1.2 の 3 軸はすべて voicing 候補）。"""
    m = _CAND_ID_RE.match(candidate_id)
    if not m:
        raise D4SpecMismatch(f"未知の voicing candidate_id 形式: {candidate_id!r}")
    family = m.group("family")
    if family not in ("S_melshape_core_distance", "P_mel_peakiness"):
        raise D4SpecMismatch(f"未知の voicing family: {family!r}")
    return v12.Cand12(
        candidate_id=candidate_id, kind="voicing", family=family,
        threshold=float(m.group("thr")), window_ms=float(m.group("win")),
        hop_ms=float(m.group("hop")),
    )


# --- 出力ガード（D0 器具と同型: 衝突拒否 + atomic write） -------------------


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    """`path` と同一ディレクトリの一時ファイルへ書いてから `os.replace` で
    atomic に置き換える。voice_genesis は src/ の `utils/atomic_io.py` を
    import しない契約（CLAUDE.md）のため、`scripts/check_checkpoint_finite.py`
    の `_atomic_write_text` と同型でローカルに最小実装する。"""
    s7_io.assert_json_finite(doc)
    content = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --- render: s7_0b_probe と同じ契約でセルをレンダ ---------------------------


def cmd_render(args: argparse.Namespace) -> int:
    d4_spec, d4_spec_sha = load_and_verify_d4_spec()
    group_ids = {g["group_id"] for g in d4_spec["groups"]}
    group_id = f"{args.generation}_{args.speaker}"
    if group_id not in group_ids:
        raise D4SpecMismatch(
            f"{group_id!r} は D4 spec の groups に無い（有効: {sorted(group_ids)}）"
        )

    # 8-0b probe と**同じ**事前登録 + verify（凍結物は読むだけ）。
    probe_spec, probe_spec_sha, _ = s7_io.read_json_with_pin(probe0b.SPEC_PATH)
    probe0b.verify_spec(probe_spec)
    if args.speaker not in probe_spec["expansion"]["generations"][args.generation]["speakers"]:
        raise probe0b.ProbeSpecMismatch(
            f"{args.generation} に話者 {args.speaker} は事前登録されていない"
        )

    acoustic_dir, stem = Path(args.acoustic_dir), args.acoustic_stem
    export_binding = xm.verify_export_manifest(
        Path(args.export_manifest),
        generation=str(args.generation),
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

    # `run_group` の測定は凍結済み 1.0 spec のまま呼ぶ（render の契約を 8-0b
    # probe と完全に一致させるため）。D4 が消費するのは WAV / `input_meta` の
    # みで、ここで出た 1.0 測定値は `measure` サブコマンドでは使わない。
    frozen = trf.load_frozen_measurement()
    gate_synth = probe0b.load_gate_synth()

    out_path = Path(args.result_out)
    s7_io.reject_output_collision(
        [out_path, Path(args.out_dir)],
        [
            SPEC_PATH, probe0b.SPEC_PATH, trf.TRF_SPEC_PATH, Path(args.export_manifest),
            xm.INPUT_PINS_PATH, probe0b.GATE_SYNTH_PATH,
            acoustic_dir / f"{stem}.onnx", acoustic_dir / "dsconfig.yaml",
            acoustic_dir / f"{stem}.phonemes.json", acoustic_dir / f"{stem}.{args.speaker}.emb",
            Path(args.canon_model_dir), Path(args.vocoder_dir), Path(args.canon_phonemes_txt),
        ],
    )
    doc = probe0b.run_group(
        gate_synth, probe_spec, frozen, probe_spec_sha, args.generation, args.speaker,
        acoustic_dir, stem, Path(args.canon_model_dir), Path(args.vocoder_dir),
        Path(args.canon_phonemes_txt), Path(args.out_dir),
    )
    doc["export_binding"] = export_binding
    doc["d4_schema"] = RENDER_SCHEMA
    doc["d4_remeasure_spec_sha256"] = d4_spec_sha
    doc["d4_remeasure_spec_path"] = str(SPEC_PATH.relative_to(_REPO_ROOT))
    _atomic_write_json(out_path, doc)
    print(
        f"| {args.generation}/{args.speaker}: {doc['n_rendered']} rendered / "
        f"{doc['n_dropped']} dropped -> {out_path}"
    )
    return 0


# --- measure: WAV 群へ 1.2 の voicing 3 軸を適用 -----------------------------


def _measure_cell_axes(
    stim: "b1.Stimulus", axis_candidates: Dict[str, "v12.Cand12"],
    cache: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for axis, cand in axis_candidates.items():
        if cand.candidate_id not in cache:
            cache[cand.candidate_id] = v12.measure_candidate_12(cand, stim)
        out[axis] = cache[cand.candidate_id][axis]
    return out


def _measure_group(
    render_doc_path: Path, d4_spec_sha: str, axis_candidates: Dict[str, "v12.Cand12"],
) -> Dict[str, Any]:
    doc, doc_sha, _ = s7_io.read_json_with_pin(render_doc_path)
    bound_sha = doc.get("d4_remeasure_spec_sha256")
    if bound_sha is not None and bound_sha != d4_spec_sha:
        raise D4SpecMismatch(
            f"{render_doc_path}: レンダ時の D4 spec sha {bound_sha} が"
            f"現在の {d4_spec_sha} と違う"
        )
    generation, speaker = str(doc["generation"]), str(doc["speaker"])
    out_dir = Path(str(doc["out_dir"]))

    cells_out: Dict[str, Any] = {}
    n_measured = n_missing = n_error = 0
    cache: Dict[str, Dict[str, float]] = {}
    for cell in doc["cells"]:
        cid = str(cell["cell_id"])
        if cell.get("outcome") != "rendered":
            cells_out[cid] = {
                "outcome": "missing",
                "reason": cell.get("status", "not_rendered"),
                "error": cell.get("error"),
            }
            n_missing += 1
            continue
        try:
            wav_path = s7_io.child_path(out_dir, str(cell["wav"]))
            y, sr = s7_io.read_wav_with_pins(
                wav_path, cell.get("wav_sha256"), cell.get("samples_sha256")
            )
            meta = cell["input_meta"]
            stim = b1.Stimulus(
                stim_id=cid, family=str(cell.get("probe", "")), samples=y, sr=sr,
                note_onset_s=float(meta["note_onset_s"]),
                commanded_note_end_s=float(meta["commanded_note_end_s"]),
                score_boundary_s=float(meta["score_boundary_s"]),
                tail_window_ms=float(meta["tail_window_ms"]),
            )
            axes = _measure_cell_axes(stim, axis_candidates, cache)
        except (s7_io.WavPinMismatch, s7_io.PathEscapeError, KeyError, ValueError) as exc:
            cells_out[cid] = {"outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
            n_error += 1
            continue
        cells_out[cid] = {
            "outcome": "measured",
            "wav_sha256": cell["wav_sha256"],
            "samples_sha256": cell["samples_sha256"],
            "axes": axes,
        }
        n_measured += 1

    return {
        "generation": generation, "speaker": speaker,
        "render_doc_path": str(render_doc_path),
        "render_doc_sha256": doc_sha,
        "n_cells": len(doc["cells"]),
        "n_measured": n_measured, "n_missing": n_missing, "n_error": n_error,
        "materials_sha256": {
            "model_sha256": doc.get("model_sha256"),
            "aux_sha256": doc.get("aux_sha256"),
            "export_binding": doc.get("export_binding"),
        },
        "cells": cells_out,
    }


def cmd_measure(args: argparse.Namespace) -> int:
    d4_spec, d4_spec_sha = load_and_verify_d4_spec()
    axis_candidates = _axis_candidates(d4_spec)
    analysis_stack = b1.verify_analysis_stack(b1.load_prereg())

    out_path = Path(args.out)
    render_docs = [Path(p) for p in args.render_doc]
    s7_io.reject_output_collision([out_path], [SPEC_PATH, *render_docs])

    groups: Dict[str, Any] = {}
    for render_doc_path in render_docs:
        group_doc = _measure_group(render_doc_path, d4_spec_sha, axis_candidates)
        group_id = f"{group_doc['generation']}_{group_doc['speaker']}"
        if group_id in groups:
            raise D4SpecMismatch(f"群 {group_id!r} が複数の --render-doc に現れている")
        groups[group_id] = group_doc

    n_total_cells = sum(g["n_cells"] for g in groups.values())
    n_total_measured = sum(g["n_measured"] for g in groups.values())
    doc = {
        "schema": RESULTS_SCHEMA,
        "debt_ref": DEBT_REF,
        "generated_by": "voice_genesis/foundry/debt/d4/d4_runner.py",
        "d4_remeasure_spec_sha256": d4_spec_sha,
        "d4_remeasure_spec_path": str(SPEC_PATH.relative_to(_REPO_ROOT)),
        "trf_measurement_spec_1_2_sha256": d4_spec["pins"]["trf_measurement_spec_1_2_sha256"],
        "instrument_sha256": d4_spec["pins"]["instrument_sha256"],
        "candidate_ids": {axis: cand.candidate_id for axis, cand in axis_candidates.items()},
        "analysis_stack": analysis_stack,
        "n_groups": len(groups),
        "n_total_cells": n_total_cells,
        "n_total_measured": n_total_measured,
        "groups": groups,
    }
    _atomic_write_json(out_path, doc)
    print(
        f"| wrote {out_path} ({len(groups)} groups, "
        f"{n_total_measured}/{n_total_cells} cells measured)"
    )
    return 0


# --- CLI ---------------------------------------------------------------


def _add_render_parser(sub: "argparse._SubParsersAction") -> None:
    ap = sub.add_parser(
        "render", help="s7_0b_probe.py と同じ契約で 1 群（話者 x 世代）をレンダする"
    )
    ap.add_argument("--generation", required=True, choices=["run5", "run6", "run7"])
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--acoustic-dir", required=True)
    ap.add_argument("--acoustic-stem", required=True)
    ap.add_argument("--export-manifest", required=True)
    ap.add_argument("--canon-model-dir", required=True)
    ap.add_argument("--vocoder-dir", required=True)
    ap.add_argument("--canon-phonemes-txt", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--result-out", required=True)
    ap.set_defaults(func=cmd_render)


def _add_measure_parser(sub: "argparse._SubParsersAction") -> None:
    ap = sub.add_parser(
        "measure", help="render 済み群 JSON へ 1.2 の voicing 3 軸を適用する"
    )
    ap.add_argument(
        "--render-doc", action="append", required=True, dest="render_doc",
        help="render サブコマンド（または s7_0b_probe.py）が書いた群 JSON。複数指定可",
    )
    ap.add_argument("--out", required=True, help="d4_results.json の出力先")
    ap.set_defaults(func=cmd_measure)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    _add_render_parser(sub)
    _add_measure_parser(sub)
    args = ap.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
