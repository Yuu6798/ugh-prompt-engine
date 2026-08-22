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
  = `analyse_shape` / `voiced_mask_12` を呼ぶ。内部で `run8/s7_b1_v11.py`
  を transitively import する — 1.1 の候補・測定基盤を再利用しているため）
- `run8/s7_b1_calibration.py`（`Stimulus` / `measure_voicing_axes` /
  `verify_analysis_stack`）
- `run8/s7_io.py` / `s7_export_manifest.py` / `s7_trf.py` の pin ガード群

**pin 閉包（v0.2・PR #306 レビュー第 1 巡 P1-3）**: `d4_remeasure_spec.json`
の `pins.sources` は上記のうち実際に import 再利用する実装モジュール本体
（`s7_b1_v11.py` / `s7_b1_calibration.py` / `s7_0b_probe.py`）と、このモジュール
自身（`d4_runner.py`。起動時に自分自身のバイト列を読み、spec の pin と照合する
自己参照）を照合対象へ含む。凍結済み 1.2 spec・render ハーネス（gate_synth.py）・
1.2 候補選定本体（s7_b1_v12.py）と合わせて計 7 点。

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
で abort する（本番値を見て仕様を書き換える経路を構造的に閉じる）。加えて
`pins` のキー集合そのもの（欠落・余剰）と、D4 spec の `axes[].selected_candidate`
が凍結済み `trf_measurement_spec_1_2.json` の同軸 `selected_candidate` と逐語
一致することも検査する。

`--spec-sha256`（両サブコマンド必須）: operator が渡す「コミット済みのはずの
spec sha256」の期待値。実ファイルから計算した sha256 と一致しなければ abort
する — スクリプトの sha256 自己計算だけでは「operator が意図した版」までは
束縛できない（例えばコミット前の作業コピーを誤って指す事故）ため、期待値を
呼び出し側から明示的に渡させる。

`measure` は追加で、渡された render 群 JSON が D4 spec の登録内容（群 ID・
36 セルという規定数・cell_id 集合の欠落/重複なし）と一致することを検査する。
これは `d4_remeasure_spec_sha256` が None（= D4 render を経由しない生の 8-0b
probe 群 JSON）でも必ず通す。セル単位の測定失敗は `outcome: "error"` として
隔離し、他セルの測定は継続する（fail-closed = 全滅させない代わりに、1 件でも
エラーがあれば `d4_results.json` を書き切った上で非ゼロ終了する）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as _md
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Sequence, Tuple

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

SPEC_SCHEMA = "vg-d4-remeasure-spec/0.2"
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


#: `pins.sources` が持つべきキー集合（厳密一致。欠落・余剰とも abort）。
#: v0.2（PR #306 レビュー指摘 #3・二層証明方式）で、D4 が実行時に読む実装
#: モジュール本体（測定ロジック / 計器 / render ハーネス / runner 自身）を
#: 閉包へ追加した。`d4_runner_sha256` は**自己参照**（このモジュールが起動時に
#: 自分自身のバイト列を読み、spec の pin と照合する）— 循環にはならない:
#: spec 側は「runner のあるべき sha」を主張するだけで runner の内容そのものを
#: 内包しないため、runner が spec を読んで自分の sha を確認する経路は素直に
#: 成立する。
EXPECTED_PIN_SOURCE_KEYS = frozenset({
    "trf_measurement_spec_1_2_sha256", "instrument_sha256", "render_harness_sha256",
    "s7_b1_v11_sha256", "s7_b1_calibration_sha256", "s7_0b_probe_sha256", "d4_runner_sha256",
})
#: `pins` トップレベルが持つべきキー集合（上記 7 キー + cell_definition_source +
#: sources 自身。厳密一致）。
EXPECTED_PIN_KEYS = EXPECTED_PIN_SOURCE_KEYS | {"cell_definition_source", "sources"}


# --- fail-closed: 事前登録 spec の pin を実ファイルと照合 -------------------


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_pins_key_shape(pins: Dict[str, Any]) -> None:
    """`pins` / `pins.sources` のキー集合を先に厳密検査する（値の照合より前）。
    欠落・余剰のどちらも spec の陳腐化・改竄の兆候として abort する。"""
    got_top = set(pins.keys())
    if got_top != EXPECTED_PIN_KEYS:
        raise D4SpecMismatch(
            f"pins のキー集合が期待と違う: missing={sorted(EXPECTED_PIN_KEYS - got_top)} "
            f"extra={sorted(got_top - EXPECTED_PIN_KEYS)}"
        )
    got_sources = set(pins.get("sources", {}).keys())
    if got_sources != EXPECTED_PIN_SOURCE_KEYS:
        raise D4SpecMismatch(
            f"pins.sources のキー集合が期待と違う: "
            f"missing={sorted(EXPECTED_PIN_SOURCE_KEYS - got_sources)} "
            f"extra={sorted(got_sources - EXPECTED_PIN_SOURCE_KEYS)}"
        )


def load_and_verify_d4_spec(
    spec_path: Path = SPEC_PATH, *, expected_sha256: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """`d4_remeasure_spec.json` を読み、schema・軸集合・pins 全件を実ファイルと
    照合してから返す。**測定より前に必ず呼ぶ**（両サブコマンドの入口)。

    `expected_sha256` を渡すと、operator が明示した期待 spec sha256（CLI の
    `--spec-sha256`）と実ファイルの sha256 を照合する（不一致は abort）。
    省略時（テスト等）はこの照合をスキップする。
    """
    spec, spec_sha, _ = s7_io.read_json_with_pin(spec_path)
    if expected_sha256 is not None and spec_sha != expected_sha256:
        raise D4SpecMismatch(
            f"--spec-sha256 {expected_sha256!r} が {spec_path} の実 sha256 "
            f"{spec_sha!r} と違う（operator が束縛した期待版と現物が食い違う）"
        )
    if spec.get("schema") != SPEC_SCHEMA:
        raise D4SpecMismatch(f"schema {spec.get('schema')!r} != {SPEC_SCHEMA!r}")
    if spec.get("debt_ref") != DEBT_REF:
        raise D4SpecMismatch(f"debt_ref {spec.get('debt_ref')!r} != {DEBT_REF!r}")

    axes = spec.get("axes", {})
    if set(axes) != set(D4_AXES):
        raise D4SpecMismatch(f"axes {sorted(axes)} != {sorted(D4_AXES)}（voicing 3軸のみ）")

    pins = spec.get("pins", {})
    _verify_pins_key_shape(pins)
    sources = pins["sources"]
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

    _verify_axes_match_frozen_spec_1_2(axes, _REPO_ROOT / sources["trf_measurement_spec_1_2_sha256"])

    return spec, spec_sha


def _verify_axes_match_frozen_spec_1_2(axes: Dict[str, Any], trf12_path: Path) -> None:
    """D4 spec の `axes[].selected_candidate` が、凍結済み
    `trf_measurement_spec_1_2.json` の同軸 `selected_candidate` と逐語一致する
    ことを検査する（不一致 = D4 spec 側の typo として abort。凍結物は読むだけ）。
    """
    trf12, _, _ = s7_io.read_json_with_pin(trf12_path)
    for axis, cfg in axes.items():
        want = cfg.get("selected_candidate")
        got = trf12.get("axes", {}).get(axis, {}).get("selected_candidate")
        if got != want:
            raise D4SpecMismatch(
                f"axis {axis!r}: D4 spec の selected_candidate {want!r} が "
                f"凍結済み trf_measurement_spec_1_2.json の {got!r} と違う"
                "（D4 spec 側の typo の可能性）"
            )


def _load_cell_definition(spec: Dict[str, Any]) -> Dict[str, Any]:
    """D4 spec が pin する `cell_definition_source`（`s7_0b_probe_spec.json`）
    を読む。sha256 は `load_and_verify_d4_spec` が既に照合済みだが、呼び出し
    経路が増えても壊れないよう独立にも確認する。"""
    cds = spec["pins"]["cell_definition_source"]
    path = _REPO_ROOT / cds["path"]
    doc, sha, _ = s7_io.read_json_with_pin(path)
    if sha != cds["sha256"]:
        raise D4SpecMismatch(f"{path}: sha256 {sha} が spec pin と違う")
    return doc


def _resolve_axis_candidates(spec: Dict[str, Any]) -> Dict[str, "v12.Cand12"]:
    """D4 spec の `axes[].selected_candidate`（文字列）を、`s7_b1_v12` が実際に
    列挙する候補（`enumerate_candidates_12`）から candidate_id 一致で解決する
    （`s7_0b_remeasure_12.load_winners` と同じ方式。文字列から `Cand12` を
    独自に再構成しない — 候補空間の定義は常に `s7_b1_v12` 側が正）。"""
    cs, _cal, _rule, _pins = v12.load_prereg_12()
    by_id = {c.candidate_id: c for c in v12.enumerate_candidates_12(cs)}
    out: Dict[str, "v12.Cand12"] = {}
    for axis, cfg in spec["axes"].items():
        cid = str(cfg["selected_candidate"])
        if cid not in by_id:
            raise D4SpecMismatch(
                f"axis {axis!r}: candidate_id {cid!r} が s7_b1_v12 の 1.2 候補"
                "空間（enumerate_candidates_12）に無い"
            )
        out[axis] = by_id[cid]
    return out


# --- runtime_stack（PR #306 レビュー指摘: render stack の実測を結果へ埋め込む） --


#: `runtime_stack` に記録するパッケージ。`s7_b1_real_render_manifest.json` の
#: `render_stack`（numpy/onnxruntime/soundfile/PyYAML/python）と同じ語彙にする
#: （2026-08-22 の D4 実測はこの記録先が無く、`d4_exec_report_2026-08-22.md`
#: 側にしか残せなかった。以後の実行分は結果 JSON 自身に機械可読で残す）。
_RUNTIME_STACK_PACKAGES: Tuple[str, ...] = ("numpy", "onnxruntime", "soundfile", "PyYAML")


def _runtime_stack() -> Dict[str, Optional[str]]:
    """実行時のパッケージ版 + python 版を返す。`onnxruntime` は render 時にしか
    要らない任意依存のため、未導入でも例外にせず `None` を記録する（measure 側
    には元々不要な依存だが、render/measure 双方で同一関数を使い語彙を揃える）。
    `PyYAML` はディストリビューション名（import 名は `yaml`）。"""
    out: Dict[str, Optional[str]] = {"python": platform.python_version()}
    for pkg in _RUNTIME_STACK_PACKAGES:
        try:
            out[pkg] = _md.version(pkg)
        except _md.PackageNotFoundError:
            out[pkg] = None
    return out


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
    d4_spec, d4_spec_sha = load_and_verify_d4_spec(expected_sha256=args.spec_sha256)
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
    doc["runtime_stack"] = _runtime_stack()
    _atomic_write_json(out_path, doc)
    print(
        f"| {args.generation}/{args.speaker}: {doc['n_rendered']} rendered / "
        f"{doc['n_dropped']} dropped -> {out_path}"
    )
    return 0


# --- measure: WAV 群へ 1.2 の voicing 3 軸を適用 -----------------------------


def _measure_cell_axes(
    stim: "b1.Stimulus", axis_candidates: Dict[str, "v12.Cand12"],
) -> Dict[str, float]:
    """1 セルぶんの 3 軸を測る。`cache` は**このセル専用**（呼び出し側が毎セル
    新しい dict を渡す）— 目的は同一セル内で複数軸が同じ candidate_id（例:
    excess_tail_voiced_ms と tail_f0_persistence が同じ hop10 候補を使う）を
    共有するときの重複計算を避けることだけであり、セルをまたいで使い回すと
    2 セル目以降が 1 セル目の軸値を再利用する致命バグになる（セルフレビュー
    #1・実音源で再現: 300ms voiced tail のセル A の値が 0.9s カットのセル B に
    そのまま記帳された）。"""
    cache: Dict[str, Dict[str, float]] = {}
    out: Dict[str, float] = {}
    for axis, cand in axis_candidates.items():
        if cand.candidate_id not in cache:
            cache[cand.candidate_id] = v12.measure_candidate_12(cand, stim)
        out[axis] = cache[cand.candidate_id][axis]
    return out


def _verify_group_doc_registration(
    doc: Dict[str, Any], render_doc_path: Path,
    valid_group_ids: FrozenSet[str], expected_cell_ids: FrozenSet[str],
) -> None:
    """render 群 JSON が D4 spec の事前登録内容と一致することを検査する。

    `d4_remeasure_spec_sha256` が None（= D4 render を経由しない生の 8-0b
    probe 群 JSON をそのまま `--render-doc` に渡した場合）でも**必ず**この
    検査を通す — spec 束縛の有無で緩めると、由来不明の群 JSON がすり抜ける。
    """
    generation, speaker = str(doc.get("generation")), str(doc.get("speaker"))
    group_id = f"{generation}_{speaker}"
    if group_id not in valid_group_ids:
        raise D4SpecMismatch(
            f"{render_doc_path}: 群 {group_id!r} は D4 spec の groups に無い"
            f"（有効: {sorted(valid_group_ids)}）"
        )
    cells = doc.get("cells", [])
    ids = [str(c["cell_id"]) for c in cells]
    if len(ids) != len(expected_cell_ids):
        raise D4SpecMismatch(
            f"{render_doc_path}: セル数 {len(ids)} が事前登録の規定 "
            f"{len(expected_cell_ids)} と違う"
        )
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise D4SpecMismatch(f"{render_doc_path}: 重複した cell_id がある: {dupes}")
    got = set(ids)
    if got != expected_cell_ids:
        raise D4SpecMismatch(
            f"{render_doc_path}: cell_id 集合が事前登録と違う "
            f"(missing={sorted(expected_cell_ids - got)}, extra={sorted(got - expected_cell_ids)})"
        )


def _measure_group(
    render_doc_path: Path, d4_spec_sha: str, axis_candidates: Dict[str, "v12.Cand12"],
    valid_group_ids: FrozenSet[str], expected_cell_ids: FrozenSet[str],
) -> Dict[str, Any]:
    doc, doc_sha, _ = s7_io.read_json_with_pin(render_doc_path)
    _verify_group_doc_registration(doc, render_doc_path, valid_group_ids, expected_cell_ids)
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
            axes = _measure_cell_axes(stim, axis_candidates)
        except Exception as exc:  # noqa: BLE001 — fail-closed: 隔離して全セル記帳を続ける
            # KeyboardInterrupt / SystemExit は Exception のサブクラスではない
            # ため、ここでは捕まらず素通りする（意図どおり）。
            cells_out[cid] = {
                "outcome": "error", "status": "error",
                "reason": type(exc).__name__,
                "error": f"{type(exc).__name__}: {exc}",
            }
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
    d4_spec, d4_spec_sha = load_and_verify_d4_spec(expected_sha256=args.spec_sha256)
    axis_candidates = _resolve_axis_candidates(d4_spec)

    valid_group_ids = frozenset(g["group_id"] for g in d4_spec["groups"])
    cell_def = _load_cell_definition(d4_spec)
    expected_cell_ids = frozenset(str(c["cell_id"]) for c in cell_def["cells"])

    out_path = Path(args.out)
    render_docs = [Path(p) for p in args.render_doc]
    # 出力衝突検査は analysis stack 検証より**前**に行う（PR #306 レビュー指摘:
    # 衝突だけを検査したい呼び出しが、無関係な ANALYSIS_STACK_PIN 不一致
    # （実行環境のパッケージ版）で先に落ちていた。凍結物破壊の防止は最優先の
    # fail-closed であり、環境のパッケージ版検査より先に評価するのが正しい
    # 順序 — analysis stack 検証自体は緩めない・そのまま維持する）。
    s7_io.reject_output_collision([out_path], [SPEC_PATH, *render_docs])

    analysis_stack = b1.verify_analysis_stack(b1.load_prereg())

    groups: Dict[str, Any] = {}
    for render_doc_path in render_docs:
        group_doc = _measure_group(
            render_doc_path, d4_spec_sha, axis_candidates, valid_group_ids, expected_cell_ids
        )
        group_id = f"{group_doc['generation']}_{group_doc['speaker']}"
        if group_id in groups:
            raise D4SpecMismatch(f"群 {group_id!r} が複数の --render-doc に現れている")
        groups[group_id] = group_doc

    n_total_cells = sum(g["n_cells"] for g in groups.values())
    n_total_measured = sum(g["n_measured"] for g in groups.values())
    n_total_error = sum(g["n_error"] for g in groups.values())

    # 完全性検査（PR #306 レビュー指摘 #4）: 事前登録された 10 群すべてが
    # `--render-doc` に揃っており、かつ全群が「規定セル数ぶん measured
    # （missing=0・error=0）」であることを既定の「完了」条件にする。満たさない
    # 場合も結果は書き切る（failure_policy の「隔離して全セル記帳する」は
    # 維持する）が、`complete: false` を明記して exit を非ゼロにし、部分実行が
    # 「静かな成功」に見えることを防ぐ。`--allow-partial` は abort/書き込み拒否
    # を解除する意味は持たない（既定でも書き切る）— 意図的な部分実行であることを
    # `partial: true` で記帳するためのものであり、exit は不完全なら常に非ゼロ。
    groups_complete = frozenset(groups) == valid_group_ids
    cells_complete = all(
        g["n_measured"] == g["n_cells"] and g["n_missing"] == 0 and g["n_error"] == 0
        for g in groups.values()
    )
    complete = groups_complete and cells_complete
    allow_partial = bool(getattr(args, "allow_partial", False))

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
        "runtime_stack": _runtime_stack(),
        "n_groups": len(groups),
        "n_total_cells": n_total_cells,
        "n_total_measured": n_total_measured,
        "n_total_error": n_total_error,
        "complete": complete,
        "groups": groups,
    }
    if allow_partial:
        doc["partial"] = not complete
    # failure_policy「落ちたセルは隔離して全セル記帳する」の後半 = 結果は必ず
    # 書き切る。ただしエラーが 1 件でも、群/セルが事前登録の規定数に満たなくても
    # 「静かな成功」を騙らせないため exit を非ゼロにする（呼び出し側 / CI が
    # エラー混入・部分実行を見落とさない）。
    _atomic_write_json(out_path, doc)
    status = "complete" if complete else ("partial (--allow-partial)" if allow_partial else "INCOMPLETE")
    print(
        f"| wrote {out_path} ({len(groups)} groups, "
        f"{n_total_measured}/{n_total_cells} cells measured, "
        f"{n_total_error} errored, {status})"
    )
    return 0 if complete else 1


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
    ap.add_argument(
        "--spec-sha256", required=True,
        help="operator が束縛する d4_remeasure_spec.json の期待 sha256（コミット済みの"
             "はずの版）。実ファイルの sha256 と一致しなければ abort する",
    )
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
    ap.add_argument(
        "--spec-sha256", required=True,
        help="operator が束縛する d4_remeasure_spec.json の期待 sha256（コミット済みの"
             "はずの版）。実ファイルの sha256 と一致しなければ abort する",
    )
    ap.add_argument(
        "--allow-partial", action="store_true", dest="allow_partial",
        help="事前登録の10群・規定セル数に満たない部分実行を明示的に許可する意図を"
             "結果へ記帳する（`partial: true`）。既定でも部分実行は abort せず結果を"
             "書き切るため exit の非ゼロ化は変わらない — 意図的な部分実行であることの"
             "記帳だけが違う",
    )
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
