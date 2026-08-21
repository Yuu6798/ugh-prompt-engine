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

import pr_census  # noqa: E402
import pr_identity  # noqa: E402
import pr_ladder  # noqa: E402
import pr_lab  # noqa: E402
import pr_match  # noqa: E402
import pr_performance as prp  # noqa: E402
import s3_spec as sp  # noqa: E402

FROZEN_MANIFEST = _FOUNDRY / "planb_real" / "results" / "ladder_manifest.json"

#: 凍結走行（S2 の `pr_run`）が使った source_id ラベル。
#: **推測ではなく表明** — これで組み直したハッシュを manifest の pin と
#: 突き合わせ、一致しなければ S3Stop で止める（§20-2）。
#: `pr_attribution._rebuild` は別ラベル（"ritsu"/"pjs"）を使うため pin を
#: 再現できない。`planb_real` は read-only なので S3 側で組み立てる（§14）。
RITSU_SOURCE_ID = "ritsu_singing_db"
PJS_SOURCE_ID = "pjs_corpus"

#: manifest の各 pair に必須のフィールド。
REQUIRED_PAIR_FIELDS = ("pair_key", "probe_kind", "ritsu_file", "pjs_file",
                        "identity_sha256", "performance_sha256")
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
    """S2 の凍結 manifest を読み、**使う前に**構造を検証する。

    検証に落ちた入力は例外を投げっぱなしにせず S3Stop（= BLOCKED、exit 3）へ
    変換する。設計書 §20 の停止は「記録を残して止まる」ことなので、
    未処理トレースバックで落ちてはならない。
    """
    if not FROZEN_MANIFEST.exists():
        raise S3Stop(
            cause=f"S2 frozen manifest が無い（{FROZEN_MANIFEST}）",
            impact="S3 は S2 の凍結 pair set を正本とするため、入力が確定できず開始できない",
            minimal_fix="planb_real の ladder を実行して results/ladder_manifest.json を用意する")
    try:
        data = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise S3Stop(
            cause=f"frozen manifest が JSON として読めない: {exc}",
            impact="凍結 pair set を確定できず S3 を開始できない",
            minimal_fix="ladder_manifest.json の破損を確認する") from exc
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise S3Stop(
            cause="frozen manifest に pairs が無い（または空）",
            impact="判定対象が 0 件になり、gene verdict を出せない",
            minimal_fix="planb_real の ladder を再実行して pair を生成する")
    seen: set = set()
    for i, pair in enumerate(pairs):
        missing = [f for f in REQUIRED_PAIR_FIELDS if not pair.get(f)]
        if missing:
            raise S3Stop(
                cause=f"pairs[{i}] に必須フィールドが無い/空: {missing}",
                impact="context 集計または pin 照合ができず、判定の前提が崩れる",
                minimal_fix="ladder_manifest.json の当該 pair を確認する")
        try:
            sp.context_id(pair)   # probe_kind の exact string を要求（§5）
        except KeyError as exc:
            raise S3Stop(
                cause=f"pairs[{i}] の probe_kind が使えない: {exc}",
                impact="context_id を確定できず、distinct context の集計が成立しない",
                minimal_fix="ladder_manifest.json の probe_kind を確認する") from exc
        key = pair["pair_key"]
        if key in seen:
            # 重複 pair は evaluable_pairs と support_ratio を二重計上する一方、
            # 記録側の pairs dict は 1 行に潰れる。表示される証拠より多い母数で
            # gene verdict が出てしまうので、集計前に止める。
            raise S3Stop(
                cause=f"frozen manifest に pair_key の重複がある: {key}",
                impact="同一観測が evaluable_pairs / support_ratio を二重計上し、"
                       "記録に表示される distinct な証拠より緩い判定になる",
                minimal_fix="ladder_manifest.json の重複 pair を取り除く")
        seen.add(key)
    return data


def manifest_sha256() -> str:
    return _sha_file(FROZEN_MANIFEST)


_CODE_STATE: Optional[Dict[str, Any]] = None


def code_state() -> Dict[str, Any]:
    """実際に走ったコードの状態を **1 回だけ**確定して使い回す。

    `rev-parse HEAD` だけでは、worktree が dirty なとき / 実験中に HEAD が
    動いたときに「その commit には無いコード」を来歴として書いてしまう。
    そこで commit に加えて **未コミット差分のダイジェスト**も採る。
    測定の前後で別々に呼んで別の値を得ることが無いよう、初回の値を凍結する。
    """
    global _CODE_STATE
    if _CODE_STATE is not None:
        return dict(_CODE_STATE)

    def _git(*args: str) -> Optional[str]:
        try:
            proc = subprocess.run(["git", *args], cwd=str(_HERE),
                                  capture_output=True, text=True, check=True)
            return proc.stdout
        except Exception:  # noqa: BLE001
            return None

    commit = (_git("rev-parse", "HEAD") or "").strip() or "unknown"
    porcelain = _git("status", "--porcelain", "--untracked-files=all")
    diff = _git("diff", "HEAD")
    state: Dict[str, Any] = {"commit": commit, "dirty": False, "dirty_digest": None}
    if porcelain is None or diff is None:
        state["dirty"] = None          # git が使えない = 判定不能を偽らない
    elif porcelain.strip():
        h = hashlib.sha256()
        h.update(porcelain.encode("utf-8"))
        h.update(diff.encode("utf-8"))
        state["dirty"] = True
        state["dirty_digest"] = h.hexdigest()
    _CODE_STATE = state
    return dict(state)


def source_commit() -> str:
    return str(code_state()["commit"])


def build_inputs(pair: Dict[str, Any], ctx: int):
    """frozen pair から bank / probe 位置 / compose track / performance / donor 参照を作る。

    `planb_real` の既存関数を read-only に呼ぶだけで、新しい前処理は足さない。
    組み上げたあと **manifest の pin と突き合わせ**、一致しなければ止める（§20-2）。
    pin を照合しないと、参照先の lab/wav が差し替わっていても黙って別素材を
    評価し、manifest のハッシュを来歴として提示したまま PASS を出せてしまう。
    """
    pk = pair["pair_key"]
    r_lab_path = Path(pair["ritsu_file"])
    r_lab = pr_lab.read_lab(r_lab_path)
    r_idx = int(pk.split("|")[1].split("#")[1])
    r_wav = pr_census.wav_for_lab(r_lab_path)
    p_lab_path = Path(pair["pjs_file"])
    p_lab = pr_lab.read_lab(p_lab_path)
    p_idx = int(pk.split("|")[2].split("#")[1])
    p_wav = pr_census.wav_for_lab(p_lab_path)
    if r_wav is None or p_wav is None:
        raise S3Stop(
            cause=f"{pk}: lab に対応する wav が見つからない",
            impact="凍結 pair の素材を読めず、その pair を評価できない",
            minimal_fix="corpus の配置（wav と lab の stem 一致）を確認する")

    bank, pos = pr_identity.build_identity_for_probe(
        r_wav, r_lab, r_idx, source_id=RITSU_SOURCE_ID, context=ctx)
    lo = p_lab.phones[max(0, p_idx - 2)].start_s
    hi = p_lab.phones[min(len(p_lab.phones) - 1, p_idx + 1)].end_s
    an, pw, off = pr_identity.analyze_for_performance(
        p_wav, start_s=lo - pr_identity.SLICE_PAD_S, end_s=hi + pr_identity.SLICE_PAD_S)
    p_phones = pr_identity.shift_phones(p_lab.phones, off)
    perf = prp.extract_performance(
        f0=an.f0, power_db=pw, frame_period_ms=an.frame_period_ms, phones=p_phones,
        vowel_index=p_idx, source_id=PJS_SOURCE_ID, source_file=str(p_lab_path),
        probe_kind=pair["probe_kind"])
    _assert_matches_pin(pk, "identity", bank.content_sha256(), pair["identity_sha256"])
    _assert_matches_pin(pk, "performance", perf.content_sha256(), pair["performance_sha256"])

    track = pr_match.build_compose_track(bank, pos, perf)
    donor_core = pr_ladder.donor_core_envelope(
        an, p_phones, p_idx, target_sr=bank.sr, target_bins=bank.sp[pos].shape[1])
    return bank, pos, track, perf, donor_core


def _assert_matches_pin(pair_key: str, what: str, got: str, want: str) -> None:
    if got == want:
        return
    raise S3Stop(
        cause=f"{pair_key}: {what} の再構築ハッシュが凍結 pin と一致しない "
              f"(got {got[:16]}… / pin {want[:16]}…)",
        impact="S2 と別の素材を評価しながら manifest のハッシュを来歴として"
               "提示することになり、PASS の provenance が偽になる",
        minimal_fix="参照先の lab/wav が S2 走行時から変わっていないかを確認する")


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
    code_state()          # 実験の**前**に確定させる（走行中に HEAD が動いても揺れない）
    data = load_frozen()
    ctx = int(data.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES))
    runs = [run_pair(p, ctx, write_wav=write_wav) for p in data["pairs"]]
    cs = code_state()
    meta = {"context_phones": ctx,
            "identity_ap_scale": data.get("identity_ap_scale"),
            "input_manifest_sha256": manifest_sha256(),
            "source_commit": cs["commit"],
            "code_state": cs}
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
