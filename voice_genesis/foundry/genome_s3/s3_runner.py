"""genome_s3/s3_runner.py — B0/F/D/E/R の生成と実測（設計書 §15）。

責務は 5 つだけ。研究判断は行わない。

1. `planb_real` の frozen source / pair manifest を読む
2. B0/F/D/E/R を生成する
3. 既存の compose path を呼ぶ
4. output SHA を保存する
5. output realization metrics を計測する

`planb/` と `planb_real/` は **read-only import**（§14）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pb_compose as pc  # noqa: E402
import pb_gates as pbg  # noqa: E402

import pr_attribution as pa  # noqa: E402  (planb_real、read-only)
import pr_census  # noqa: E402
import pr_identity  # noqa: E402
import pr_ladder  # noqa: E402
import pr_lab  # noqa: E402
import pr_match  # noqa: E402
import pr_performance as prp  # noqa: E402
import s3_spec as sp  # noqa: E402

FROZEN_MANIFEST = _FOUNDRY / "planb_real" / "results" / "ladder_manifest.json"
RESULTS = _HERE / "results"
WAV_DIR = RESULTS / "wav"


class S3Stop(Exception):
    """設計書 §20 の停止条件。原因・影響・最小修正案だけを持つ。"""

    def __init__(self, cause: str, impact: str, minimal_fix: str) -> None:
        super().__init__(cause)
        self.cause, self.impact, self.minimal_fix = cause, impact, minimal_fix

    def as_dict(self) -> Dict[str, str]:
        return {"status": "BLOCKED", "cause": self.cause,
                "impact": self.impact, "minimal_fix": self.minimal_fix}


@dataclass
class ConditionOutput:
    condition: str
    toggles: str
    sample_sha256: str
    wav_sha256: str
    wav_path: str
    metrics: Dict[str, Optional[float]]
    tripwire_status: str = ""
    tripwire_accessed: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"condition": self.condition, "toggles": self.toggles,
                "sample_sha256": self.sample_sha256, "wav_sha256": self.wav_sha256,
                "wav_path": self.wav_path, "metrics": self.metrics,
                "tripwire_status": self.tripwire_status,
                "tripwire_accessed": list(self.tripwire_accessed)}


@dataclass
class PairRun:
    pair_key: str
    context_id: str
    identity_sha256: str
    performance_sha256: str
    performance_payload_1d: bool = True
    conditions: Dict[str, ConditionOutput] = field(default_factory=dict)
    intervention: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    repeat_sample_sha256: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"pair_key": self.pair_key, "context_id": self.context_id,
                "identity_sha256": self.identity_sha256,
                "performance_sha256": self.performance_sha256,
                "performance_payload_1d": self.performance_payload_1d,
                "conditions": {k: v.as_dict() for k, v in self.conditions.items()},
                "intervention": self.intervention,
                "repeat_sample_sha256": self.repeat_sample_sha256}


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_frozen() -> Dict[str, Any]:
    if not FROZEN_MANIFEST.exists():
        raise S3Stop(
            cause=f"S2 frozen manifest が無い（{FROZEN_MANIFEST}）",
            impact="S3 は S2 の凍結 pair set を正本とするため、入力が確定できず開始できない",
            minimal_fix="planb_real の ladder を実行して results/ladder_manifest.json を用意する")
    data = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    for pair in data.get("pairs", []):
        sp.context_id(pair)   # probe_kind 欠落は即 BLOCKED（§5）
    return data


def manifest_sha256() -> str:
    return _sha_file(FROZEN_MANIFEST)


def source_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(_HERE),
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_inputs(pair: Dict[str, Any], ctx: int):
    """frozen pair から bank / probe 位置 / compose track / performance / donor 参照を作る。

    すべて `planb_real` の既存関数を read-only に呼ぶだけ（新しい前処理を足さない）。
    """
    bank, pos, track, perf = pa._rebuild(pair, ctx, want_performance=True)
    p_lab_path = Path(pair["pjs_file"])
    p_lab = pr_lab.read_lab(p_lab_path)
    p_idx = int(pair["pair_key"].split("|")[2].split("#")[1])
    p_wav = pr_census.wav_for_lab(p_lab_path)
    lo = p_lab.phones[max(0, p_idx - 2)].start_s
    hi = p_lab.phones[min(len(p_lab.phones) - 1, p_idx + 1)].end_s
    an, _pw, off = pr_identity.analyze_for_performance(
        p_wav, start_s=lo - pr_identity.SLICE_PAD_S, end_s=hi + pr_identity.SLICE_PAD_S)
    donor_core = pr_ladder.donor_core_envelope(
        an, pr_identity.shift_phones(p_lab.phones, off), p_idx,
        target_sr=bank.sr, target_bins=bank.sp[pos].shape[1])
    return bank, pos, track, perf, donor_core


def _clean(v: float) -> Optional[float]:
    return None if not np.isfinite(v) else round(float(v), 6)


def measure(condition: str, result: pc.ComposeResult, wav_path: Path, bank, pos,
            perf, donor_core, track) -> Dict[str, Optional[float]]:
    m = pr_ladder.measure_rung(condition, result, wav_path, bank, pos, perf,
                               donor_core, track)
    return {"f0_dev_rmse_cents": _clean(m.f0_dev_rmse_cents),
            "note_split_mae_ms": _clean(m.note_split_mae_ms),
            "energy_corr": _clean(m.energy_corr),
            "taper_rmse_db": _clean(m.taper_rmse_db)}


def intervention_amounts(bank, pos, track, perf) -> Dict[str, Dict[str, Any]]:
    """P2 用: 各 gene の**移植量**が baseline と非同一かを測る（§8 P2）。

    出力側ではなく **control 側**の量。ゼロなら NOT_EVALUABLE。
    """
    lo, hi = pr_match.note_unit_span(bank, pos)
    native = np.array([u.duration_s for u in bank.units], dtype=np.float64)
    transplanted = np.asarray(track.unit_durations_s, dtype=np.float64)
    probe_mask = track.f0_dev_unit_index == pos
    e_mask = track.energy_unit_index == pos
    taper = np.asarray(track.release.taper_db, dtype=np.float64)
    terminal_units = [i for i, u in enumerate(bank.units) if u.is_terminal]
    return {
        sp.Gene.F0.value: {
            "amount": float(np.max(np.abs(track.f0_dev_cents[probe_mask]))) if np.any(probe_mask) else 0.0,
            "unit": "cent", "nonzero": bool(np.any(np.abs(track.f0_dev_cents) > 0.0))},
        sp.Gene.DURATION.value: {
            "amount": float(np.max(np.abs(transplanted - native)) * 1000.0),
            "unit": "ms",
            "nonzero": bool(np.max(np.abs(transplanted - native)) > 1e-9),
            "structural_noop": bool(lo == hi)},
        sp.Gene.ENERGY.value: {
            "amount": float(np.max(np.abs(track.energy_db[e_mask]))) if np.any(e_mask) else 0.0,
            "unit": "dB", "nonzero": bool(np.any(np.abs(track.energy_db) > 0.0))},
        sp.Gene.RELEASE.value: {
            "amount": float(np.max(np.abs(taper))) if taper.size else 0.0,
            "unit": "dB",
            "nonzero": bool(taper.size and np.max(np.abs(taper)) > 0.0 and bool(terminal_units)),
            "structural_noop": not bool(terminal_units)},
    }


def run_pair(pair: Dict[str, Any], ctx: int, *, write_wav: bool = True) -> PairRun:
    bank, pos, track, perf, donor_core = build_inputs(pair, ctx)
    run = PairRun(pair_key=pair["pair_key"], context_id=sp.context_id(pair),
                  identity_sha256=bank.content_sha256(),
                  performance_sha256=perf.content_sha256())
    run.intervention = intervention_amounts(bank, pos, track, perf)
    out_dir = WAV_DIR / run.pair_key.replace("|", "__").replace("#", "-")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        prp.assert_no_spectral_payload(perf)
        run.performance_payload_1d = True
    except ValueError:
        run.performance_payload_1d = False
    for cond, toggles in sp.CONDITIONS.items():
        res = pc.compose(bank, track, toggles)
        wav_path = out_dir / f"{cond}.wav"
        if write_wav:
            sf.write(wav_path, res.wav.astype(np.float32), res.sr, subtype="FLOAT")
            wav_sha = _sha_file(wav_path)
        else:
            wav_sha = ""
        # 構造的 tripwire は **条件ごと**に張る（§8 P1）。
        tw = pbg.gate_tripwire(pc.compose, bank, track, toggles)
        run.conditions[cond] = ConditionOutput(
            condition=cond, toggles=toggles.label, sample_sha256=res.sha256(),
            wav_sha256=wav_sha, wav_path=str(wav_path),
            metrics=measure(cond, res, wav_path, bank, pos, perf, donor_core, track)
            if write_wav else {},
            tripwire_status=tw.status,
            tripwire_accessed=list(tw.evidence.get("accessed", [])))
        # 同一プロセス内の反復（§8 P4）
        run.repeat_sample_sha256[cond] = pc.compose(bank, track, toggles).sha256()
    return run


def recompute_sample_shas(ctx: int) -> Dict[str, Dict[str, str]]:
    """別プロセス用: pin 済み入力から全条件のサンプル列 sha256 を作り直す。"""
    data = load_frozen()
    out: Dict[str, Dict[str, str]] = {}
    for pair in data["pairs"]:
        bank, pos, track, _perf, _dc = build_inputs(pair, ctx)
        out[pair["pair_key"]] = {c: pc.compose(bank, track, tg).sha256()
                                 for c, tg in sp.CONDITIONS.items()}
    return out


def cross_process_shas() -> Dict[str, Dict[str, str]]:
    """別プロセスで同じ入力から sample sha256 を作り直す（§8 P4）。

    サブプロセスが落ちた場合は**空を返さない**。空を返すと全 pair が
    「別プロセス不一致」に化けて、決定論違反と実行失敗が区別できなくなる。
    """
    proc = subprocess.run([sys.executable, str(_HERE / "s3_runner.py"), "--recompute"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise S3Stop(
            cause=f"別プロセス再計算が異常終了した（rc={proc.returncode}）: "
                  f"{proc.stderr.strip()[-500:]}",
            impact="P4 の cross-process 決定論が検査できず、gene verdict を出せない",
            minimal_fix="`python s3_runner.py --recompute` を単体で実行して原因を特定する")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        raise S3Stop(
            cause=f"別プロセス再計算の出力が JSON として読めない: {exc}",
            impact="P4 の cross-process 決定論が検査できず、gene verdict を出せない",
            minimal_fix="`python s3_runner.py --recompute` の標準出力を確認する") from exc


def run_all(*, write_wav: bool = True) -> Tuple[List[PairRun], Dict[str, Any]]:
    data = load_frozen()
    ctx = int(data.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES))
    runs = [run_pair(p, ctx, write_wav=write_wav) for p in data["pairs"]]
    meta = {"context_phones": ctx,
            "identity_ap_scale": data.get("identity_ap_scale"),
            "input_manifest_sha256": manifest_sha256(),
            "source_commit": source_commit()}
    return runs, meta


if __name__ == "__main__":
    if "--recompute" in sys.argv:
        d = load_frozen()
        print(json.dumps(recompute_sample_shas(
            int(d.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES)))))
    else:
        rs, meta = run_all()
        print(json.dumps({"meta": meta, "pairs": [r.as_dict() for r in rs]},
                         ensure_ascii=False, indent=2)[:2000])
