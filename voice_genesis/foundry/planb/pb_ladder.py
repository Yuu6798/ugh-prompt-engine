"""planb/pb_ladder.py — Plan B PoC ラダー（R0–R4 + P0）の実行器。

プラン §3 の 6 サンプルを 1 コマンドで走らせ、軸別計測 + 受け入れゲートを
JSON record へ落とす。事前登録の手順を実装で強制する:

    R0 を走らせる -> R0 だけから TRF ゲート閾値を凍結 -> R1–R4 を走らせる -> 判定

閾値の凍結は R1–R4 の実測値に触れない位置で行われる（コード上の順序で保証）。
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import dataclasses
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pb_compose as pc  # noqa: E402
import pb_extract as px  # noqa: E402
import pb_gates as pg  # noqa: E402
import pb_metrics as pm  # noqa: E402
import pb_substitute as ps  # noqa: E402
import pb_world as pw  # noqa: E402
from pb_tracks import (  # noqa: E402
    LADDER, LADDER_SUPPLEMENT, PerformanceToggles, ReleaseSpec,
)


@dataclass(frozen=True)
class RunConfig:
    identity_genome: str = "voice_A"
    performance_genome: str = "voice_B"
    identity_tempo_bpm: float = ps.IDENTITY_TEMPO_BPM
    performance_tempo_bpm: float = ps.PERFORMANCE_TEMPO_BPM
    fault: bool = True
    fault_window_frac: float = 0.35
    fault_depth: float = 0.85
    primary_probe_kana: str = "り"
    holdout_probe_kana: Tuple[str, ...] = ("す",)
    medial_control: bool = True
    write_wav: bool = True


def build_assets(cfg: RunConfig):
    """代替 Identity / Performance ドナーを構築する（決定論）。"""
    wi, sr_i, units_i = ps.render_source(
        genome_name=cfg.identity_genome, tempo_bpm=cfg.identity_tempo_bpm, rubato=False)
    ai = pw.analyze(wi, sr_i)
    bank = px.build_identity_bank(ai, units_i, source_id=cfg.identity_genome)

    wp, sr_p, units_p = ps.render_source(
        genome_name=cfg.performance_genome, tempo_bpm=cfg.performance_tempo_bpm, rubato=True)
    ap = pw.analyze(wp, sr_p)
    donor_bank = px.build_identity_bank(ap, units_p, source_id=cfg.performance_genome)
    perf = px.build_performance_track(ap, units_p, source_id=cfg.performance_genome)

    if cfg.fault:
        bank = ps.inject_terminal_nasal_drift(
            bank, window_frac=cfg.fault_window_frac, depth=cfg.fault_depth)
    return bank, perf, donor_bank, (wp, sr_p, ap, units_p)


def _probe_indices(units, kana_list) -> List[int]:
    out = []
    for kana in kana_list:
        out.extend(ps.probe_unit_indices(units, kana, terminal_only=True))
    return sorted(set(out))


def compose_all(bank, perf, *, supplement: bool = False) -> Dict[str, pc.ComposeResult]:
    rungs = LADDER + (LADDER_SUPPLEMENT if supplement else ())
    return {name: pc.compose(bank, perf, tg) for name, tg in rungs}


def _subprocess_shas(cfg: RunConfig) -> List[str]:
    """別プロセスで同じ合成を回し sha256 列を得る（決定論の跨プロセス検査）。"""
    cmd = [sys.executable, str(_HERE / "pb_ladder.py"), "--sha-only",
           "--identity-genome", cfg.identity_genome,
           "--performance-genome", cfg.performance_genome,
           "--fault-depth", str(cfg.fault_depth),
           "--fault-window-frac", str(cfg.fault_window_frac),
           "--identity-tempo", str(cfg.identity_tempo_bpm),
           "--performance-tempo", str(cfg.performance_tempo_bpm)]
    if not cfg.fault:
        cmd.append("--no-fault")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def run(cfg: RunConfig, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    bank, perf, donor_bank, (wp, sr_p, ap, units_p) = build_assets(cfg)

    primary_idx = _probe_indices(bank.units, [cfg.primary_probe_kana])
    holdout_idx = _probe_indices(bank.units, cfg.holdout_probe_kana)
    medial_idx: List[int] = []
    if cfg.medial_control and primary_idx:
        j = primary_idx[0] - 1
        while j >= 0 and (bank.units[j].vowel == "sil" or bank.units[j].is_terminal):
            j -= 1
        if j >= 0:
            medial_idx = [j]
    probe_idx = sorted(set(primary_idx + holdout_idx + medial_idx))
    if not primary_idx:
        raise RuntimeError(f"primary probe /{cfg.primary_probe_kana}/ が素材に存在しない")

    donor_cores = pm.donor_core_envelopes(ap, units_p)

    # --- 合成（2 回: 同一プロセス内の再現性検査） ---
    results = compose_all(bank, perf)
    results_again = compose_all(bank, perf)

    # --- R0 の計測 -> ここでゲートを凍結（R1–R4 は未計測） ---
    m0 = pm.measure_rung("R0", results["R0"], bank, perf, probe_idx, donor_cores)
    primary_key = f"{bank.units[primary_idx[0]].label}@{primary_idx[0]}"
    holdout_keys = [f"{bank.units[i].label}@{i}" for i in holdout_idx]
    frozen = pg.freeze_trf_gate(m0, primary_key, holdout_keys)
    (out_dir / "trf_gate_frozen.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- R1–R4 の計測（凍結後） ---
    metrics = {"R0": m0}
    for name, _tg in LADDER:
        if name == "R0":
            continue
        metrics[name] = pm.measure_rung(name, results[name], bank, perf, probe_idx, donor_cores)

    # --- 補助段 S1–S3（成分単独。ゲート対象外・帰属の読み取り専用） ---
    supplement = {}
    for name, tg in LADDER_SUPPLEMENT:
        res = pc.compose(bank, perf, tg)
        supplement[name] = pm.measure_rung(name, res, bank, perf, probe_idx, donor_cores)
        if cfg.write_wav:
            sf.write(out_dir / f"{name}_{tg.label}.wav", res.wav.astype(np.float32),
                     res.sr, subtype="FLOAT")

    # --- S4: release を「taper のみ」に落とした対照（hold_core=False）。
    #     release skill の内訳（コア保持 vs 利得カーブ）を分離する。
    perf_taper_only = dataclasses.replace(
        perf, release=ReleaseSpec(window_frac=perf.release.window_frac,
                                  taper_db=perf.release.taper_db, hold_core=False))
    res_s4 = pc.compose(bank, perf_taper_only, PerformanceToggles(release=True))
    supplement["S4"] = pm.measure_rung("S4", res_s4, bank, perf, probe_idx, donor_cores)
    if cfg.write_wav:
        sf.write(out_dir / "S4_release_taper_only.wav", res_s4.wav.astype(np.float32),
                 res_s4.sr, subtype="FLOAT")

    # --- P0: Performance 参照そのもの（identity は PJS 側 = 比較用） ---
    p0_trf = pm.measure_trf(
        ap, [ (int(u.start_s / (ap.frame_period_ms / 1000.0)),
               int(u.end_s / (ap.frame_period_ms / 1000.0))) for u in units_p ],
        probe_idx, [u.label for u in units_p], donor_bank.nasal_envelope)

    # --- ゲート ---
    gates: List[pg.GateResult] = []
    gates.append(pg.gate_structural(perf))
    gates.append(pg.gate_tripwire(pc.compose, bank, perf,
                                  PerformanceToggles(True, True, True, True)))
    sha_a = [results[n].sha256() for n, _ in LADDER]
    sha_b = [results_again[n].sha256() for n, _ in LADDER]
    sub = _subprocess_shas(cfg)
    gates.append(pg.gate_determinism(sha_a, sha_b, sub))
    gates.append(pg.gate_donor_texture(metrics))
    gates.append(pg.gate_attribution(metrics))
    gates.append(pg.gate_identity_preservation(metrics))
    medial_keys = [f"{bank.units[i].label}@{i}" for i in medial_idx]
    gates.append(pg.gate_medial_regression(metrics, medial_keys))
    prim, hold = pg.gate_trf(frozen, metrics["R0"], metrics["R4"])
    gates.extend([prim, hold])
    gates.append(pg.gate_ear_blocked(
        "聴感判定（リツらしさ / 実 TRF の耳確認）は実資産 + 人間の耳が要る。"
        "本セッションでは実行不能のため成功に数えない"))

    # --- wav 出力 ---
    if cfg.write_wav:
        for name, _ in LADDER:
            sf.write(out_dir / f"{name}.wav", results[name].wav.astype(np.float32),
                     results[name].sr, subtype="FLOAT")
        sf.write(out_dir / "P0_performance_reference.wav", wp.astype(np.float32), sr_p,
                 subtype="FLOAT")
        # 耳判定用の抜粋（primary probe を含む最終フレーズのみ・16bit PCM）。
        # 全長 float wav はサイズが大きく再生成可能なので commit しない（.gitignore）。
        _write_excerpts(out_dir, results, primary_idx[0], cfg)

    record = {
        "config": {**asdict(cfg), "holdout_probe_kana": list(cfg.holdout_probe_kana)},
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pyworld": _pyworld_version(),
            "platform": platform.platform(),
        },
        "assets": {
            "mode": "substitute(procedural R0.9 singer)",
            "identity_source": bank.source_id,
            "identity_sha256": bank.content_sha256(),
            "performance_source": perf.source_id,
            "nasal_envelope_source": bank.nasal_envelope_source,
            "n_units": bank.n_units,
            "fault_injected": cfg.fault,
        },
        "probe_coverage": {
            k: [{"index": i, "label": bank.units[i].label} for i in v]
            for k, v in ps.probe_coverage(bank.units).items()
        },
        "probes": {
            "primary": primary_key,
            "holdout": holdout_keys,
            "medial_control": medial_keys,
        },
        "trf_gate_frozen": frozen,
        "rungs": {name: metrics[name].as_dict() for name, _ in LADDER},
        "supplement_rungs": {name: m.as_dict() for name, m in supplement.items()},
        "P0_reference_trf": [t.as_dict() for t in p0_trf],
        "gates": [g.as_dict() for g in gates],
    }
    (out_dir / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _write_excerpts(out_dir: Path, results: Dict[str, pc.ComposeResult],
                    primary_index: int, cfg: RunConfig) -> None:
    """primary probe を含む最終フレーズを 16bit PCM で切り出す（耳判定用）。"""
    tag = "fault" if cfg.fault else "clean"
    for name, res in results.items():
        start_unit = primary_index
        while start_unit > 0 and res.unit_frames[start_unit - 1][0] > 0:
            # 直前の無音 unit（フレーズ境界）まで遡る
            prev = start_unit - 1
            if res.unit_frames[prev][1] - res.unit_frames[prev][0] < 2:
                break
            start_unit = prev
            if start_unit <= primary_index - 3:
                break
        f0_lo = res.unit_frames[start_unit][0]
        s0 = int(f0_lo * res.frame_period_ms / 1000.0 * res.sr)
        seg = res.wav[max(0, s0):]
        peak = float(np.max(np.abs(seg))) if seg.size else 1.0
        seg = seg / max(peak, 1e-9) * 0.9
        sf.write(out_dir / f"excerpt_{tag}_{name}.wav",
                 (seg * 32767.0).astype(np.int16), res.sr, subtype="PCM_16")


def _pyworld_version() -> str:
    try:
        import pyworld
        return getattr(pyworld, "__version__", "unknown")
    except Exception:  # pragma: no cover
        return "unavailable"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Plan B PoC ラダー (R0–R4 + P0)")
    ap.add_argument("--out", default=str(_HERE / "results_pb0" / "run"), help="出力ディレクトリ")
    ap.add_argument("--identity-genome", default="voice_A")
    ap.add_argument("--performance-genome", default="voice_B")
    ap.add_argument("--fault-window-frac", type=float, default=0.35)
    ap.add_argument("--identity-tempo", type=float, default=ps.IDENTITY_TEMPO_BPM)
    ap.add_argument("--performance-tempo", type=float, default=ps.PERFORMANCE_TEMPO_BPM)
    ap.add_argument("--fault-depth", type=float, default=0.85)
    ap.add_argument("--no-fault", action="store_true",
                    help="合成故障を注入しない（陰性対照）")
    ap.add_argument("--no-wav", action="store_true")
    ap.add_argument("--sha-only", action="store_true",
                    help="ラダーの wav sha256 のみを JSON で出力（跨プロセス決定論検査用）")
    args = ap.parse_args(argv)

    cfg = RunConfig(
        identity_genome=args.identity_genome,
        performance_genome=args.performance_genome,
        identity_tempo_bpm=args.identity_tempo,
        performance_tempo_bpm=args.performance_tempo,
        fault=not args.no_fault,
        fault_window_frac=args.fault_window_frac,
        fault_depth=args.fault_depth,
        write_wav=not args.no_wav,
    )
    if args.sha_only:
        bank, perf, _donor, _ = build_assets(cfg)
        results = compose_all(bank, perf)
        print(json.dumps([results[n].sha256() for n, _ in LADDER]))
        return 0

    record = run(cfg, Path(args.out))
    for g in record["gates"]:
        print(f"[{g['status'].upper():14s}] {g['gate']:11s} {g['criterion']:26s} {g['detail']}")
    # ゲートが fail を返したら非ゼロで返す。表示だけして exit 0 だと、
    # スクリプトや CI が却下された実験を成功と誤読する。
    # blocked / not_evaluable は「設計どおり判定できない」状態なので成功扱いのまま。
    failed = [g["gate"] for g in record["gates"] if g["status"] == "fail"]
    if failed:
        print(f"\n[FAIL] 機械ゲート不通過: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
