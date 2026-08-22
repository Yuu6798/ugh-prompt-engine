"""t0_sidecar.py — Trait Sidecar の生成と Body 束縛（設計書 §8 / §9 / §27）。

Sidecar は「WORLD carrier が運びきれない形質を別チャネルで運ぶ」ための正典値。
**評価 metric の最適化結果ではない**（§15）。したがってここで作る値は
すべて「Body 側で確定している事実」だけから決まり、re-expression 側の測定値を
一切参照しない。参照してしまうと until-pass loop と区別がつかなくなる。

§9 Truth Source:

```text
Duration          Founder Genome / unit_truth        （設計値そのもの）
Energy            Body blind measurement             （AF0.json を post-hoc 強制しない）
AG-alpha          Genome parameter + Body realization（final target は Body 実現値）
```

§27 Sidecar Integrity: `source_body_sha256` で Body に束縛する。Body を差し替えて
sidecar を使い回した場合は `SIDECAR_BODY_MISMATCH` で FAILED。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

_AF = Path(__file__).resolve().parent.parent
if str(_AF) not in sys.path:
    sys.path.insert(0, str(_AF))

from af_spec import AliasUnit, FounderGenome, sha256_file, timeline_for  # noqa: E402

from t0_schema import SIDECAR_SCHEMA, canonical_digest, validate_sidecar  # noqa: E402

#: operator の版。sidecar の意味論を変えたら上げる（§27 の必須項目）。
OPERATOR_VERSION = "t0-transport/1.0"


class SidecarBodyMismatch(RuntimeError):
    """§27。sidecar が束縛している Body と、渡された Body が違う。"""


def build_sidecar(genome: FounderGenome, unit: AliasUnit, body_wav: str | Path,
                  body_measurement: Mapping[str, Any]) -> Dict[str, Any]:
    """1 unit の Trait Sidecar を作る（§8 のスキーマ）。

    AF0 専用の hardcode は置かない（§8 末尾）。値はすべて `genome` と
    `body_measurement` から引くので、fixture の patch 済み genome でも
    そのまま通る。
    """
    tl = timeline_for(genome, unit)
    onset_ms = tl.ms(tl.onset_len)
    vowel_ms = tl.ms(tl.vowel_len)

    sustain = body_measurement.get("sustain_dbfs")
    attack = body_measurement.get("attack_ms")
    ag_realized = body_measurement.get("afterglow_extra_ms")

    sidecar: Dict[str, Any] = {
        "schema": SIDECAR_SCHEMA,
        "unit_id": f"{genome.founder_id}/{genome.pitch_dir}/{unit.stem}",
        "source_body_sha256": sha256_file(body_wav),
        "operator_version": OPERATOR_VERSION,
        "duration": {
            # §9: Founder Genome / unit_truth。測定値ではなく設計値。
            "source": "founder_body_truth",
            "onset_ms": round(float(onset_ms), 6),
            "vowel_ms": round(float(vowel_ms), 6),
            "total_articulation_ms": round(float(onset_ms + vowel_ms), 6),
            "lead_zero_ms": round(float(tl.ms(tl.lead)), 6),
            "main_taper_ms": round(float(genome.main_taper_ms), 6),
        },
        "energy": {
            # §9: Body blind measurement。AF0.json の -12 dBFS を直接入れない。
            "source": "body_measurement",
            "sustain_dbfs": _opt_round(sustain),
            "attack_ms": _opt_round(attack),
        },
        "founder_expression": {
            "id": "AG-alpha",
            # §9: Genome parameter + Body realization。final target は Body 実現値。
            "source": "genome_parameter+body_realization",
            "terminal_f0_delta_cents": round(float(genome.terminal_f0_delta_cents), 6),
            "ar_alpha_afterglow_extra_ms": round(
                float(genome.ar_alpha_afterglow_extra_ms), 6),
            "body_realized_afterglow_ms": _opt_round(ag_realized),
            "ar_alpha_center_hz": round(float(genome.ar_alpha_center_hz), 6),
            "ar_alpha_bandwidth_hz": round(float(genome.ar_alpha_bandwidth_hz), 6),
            # §10 A2 の入力。Body の AR-alpha 帯包絡を afterglow 区間で切り出し、
            # 開始値で正規化した **形状ベクトル**。A1 が使う 3 スカラーには
            # 含まれない情報で、A2 だけがこれを使う。
            "ar_alpha_band_envelope_shape": body_ar_alpha_envelope_shape(
                genome, unit, body_wav),
        },
    }
    sidecar["sidecar_sha256"] = canonical_digest(
        {k: v for k, v in sidecar.items() if k != "sidecar_sha256"})
    return sidecar


#: A2 が運ぶ形状ベクトルの点数（事前登録の定数）。
BAND_ENVELOPE_SHAPE_POINTS = 32


def body_ar_alpha_envelope_shape(genome: FounderGenome, unit: AliasUnit,
                                 body_wav: str | Path) -> Optional[List[float]]:
    """Body の AR-alpha 帯包絡（afterglow 区間）の正規化形状。

    Body 側の事実だけから作る（再表現側は見ない = §15）。afterglow 区間が
    取れない unit では `None`。
    """
    import soundfile as sf

    from t0_afterglow import ar_alpha_band_envelope

    x, sr = sf.read(str(body_wav), dtype="float64", always_2d=False)
    if getattr(x, "ndim", 1) > 1:  # pragma: no cover - Body は mono
        x = x.mean(axis=1)
    tl = timeline_for(genome, unit)
    if tl.ag_end <= tl.main_end or tl.main_end >= x.size:
        return None
    env = ar_alpha_band_envelope(
        x, int(sr), float(genome.ar_alpha_center_hz),
        float(genome.ar_alpha_bandwidth_hz))
    seg = env[tl.main_end:min(tl.ag_end, env.size)]
    if seg.size < 2:
        return None
    head = float(seg[0])
    if head <= 0.0:
        return None
    import numpy as np
    idx = np.linspace(0, seg.size - 1, BAND_ENVELOPE_SHAPE_POINTS)
    shape = np.interp(idx, np.arange(seg.size, dtype=float), seg) / head
    return [round(float(v), 6) for v in np.clip(shape, 0.0, 4.0)]


def _opt_round(v: Any, ndigits: int = 6) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), ndigits)


def build_sidecars(genome: FounderGenome, voicebank_root: str | Path,
                   body_measurements: Mapping[str, Mapping[str, Any]]
                   ) -> Dict[str, Dict[str, Any]]:
    """Body 全 unit 分の sidecar。**stem 昇順で決定論的に作る**（§21 T0-G8）。"""
    root = Path(voicebank_root) / genome.pitch_dir
    out: Dict[str, Dict[str, Any]] = {}
    for unit in sorted(genome.units, key=lambda u: u.stem):
        meas = body_measurements.get(unit.stem) or {}
        out[unit.stem] = build_sidecar(genome, unit, root / unit.wav_filename, meas)
    return out


def require_sidecar_matches_body(sidecar: Mapping[str, Any],
                                 body_wav: str | Path) -> None:
    """§27。Body 差し替えで sidecar を使い回していないか。"""
    errors = validate_sidecar(sidecar)
    if errors:
        raise SidecarBodyMismatch(
            f"sidecar is not well-formed: {'; '.join(errors)}")
    want = sidecar.get("source_body_sha256")
    got = sha256_file(body_wav)
    if want != got:
        raise SidecarBodyMismatch(
            f"SIDECAR_BODY_MISMATCH: sidecar {sidecar.get('unit_id')!r} is bound to "
            f"body sha256 {want}, but the supplied body hashes to {got}")


def sidecar_digest(sidecars: Mapping[str, Mapping[str, Any]]) -> str:
    """sidecar 群全体の正準ダイジェスト（T0-G8 determinism の対象）。"""
    return canonical_digest({k: sidecars[k] for k in sorted(sidecars)})


def uses_reexpression_measurement(sidecar: Mapping[str, Any],
                                  forbidden_keys: Sequence[str] = ()) -> bool:
    """sidecar に re-expression 由来の値が混じっていないかの構造検査（§15）。

    sidecar は Body 側の事実だけで決まる。再表現側の測定値が入っていたら、
    それは「評価対象を見てから補正量を決めた」ということ。
    """
    banned = set(forbidden_keys) | {"reexpressed", "reexpression", "measured_after_world",
                                    "target_error", "residual_error"}
    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            if banned & set(node):
                return True
            return any(_walk(v) for v in node.values())
        if isinstance(node, list):
            return any(_walk(v) for v in node)
        return False
    return _walk(sidecar)


__all__ = ["OPERATOR_VERSION", "SidecarBodyMismatch", "build_sidecar", "build_sidecars",
           "require_sidecar_matches_body", "sidecar_digest",
           "uses_reexpression_measurement"]
