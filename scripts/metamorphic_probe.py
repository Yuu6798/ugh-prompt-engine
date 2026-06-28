"""Metamorphic probe harness — 決定論パイプラインの grip / 校正 / 直交性を自動掃引する診断計器。

このリポジトリの中心研究課題（grip=ツマミは効くか / calibration=センサーは正確か /
orthogonality=ノブは独立か）は、そのまま「メタモルフィックテスト」の問い
（入力をこう変えたら出力はこう変わるはず、という *関係* の検証）に対応する。

本ハーネスは `generate_synth_samples.render_sample`（パラメータ→決定論的合成）と
実 `extract_physical_from_file` を繋ぎ、ノブを掃引して実応答を計測する。pass/fail を
下す「裁判官」ではなく、grip 曲線・センサー盲点・校正誤差を記録する「制御盤」として働く
（K0/K1 grip ハーネスと同じ思想）。

- ライブラリ: `build_spec` / `synth_extract` / `sweep_*` / `build_report`
- CLI: `python scripts/metamorphic_probe.py [--json] [--out PATH]`
- プロパティ検証: `tests/test_metamorphic_probe.py`（Hypothesis）

設計知見の要約は `docs/metamorphic_probe.md`。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import generate_synth_samples as g  # noqa: E402
from svp_rpe.rpe.extractor import extract_physical_from_file  # noqa: E402
from svp_rpe.rpe.models import PhysicalRPE  # noqa: E402

SAMPLE_RATE: int = g.SAMPLE_RATE

# 掃引のデフォルト尺。intro 8s + outro 6s が固定なので body を確保するため >14s。
# brightness/直交性は body が短くても計測できるが、bpm 掃引は周期が乗る尺が要る。
PROBE_DURATION_SEC: float = 24.0
BPM_PROBE_DURATION_SEC: float = 36.0
DEFAULT_SEED: int = 777

# 既存の golden サンプルから (key, mode) → テンプレ spec を借りる。和音ボイシングと
# 旋律は検証済みの良品を再利用し、掃引する連続ノブ（bpm / brightness）に集中する。
KEY_TEMPLATES: dict[tuple[str, str], "g.SampleSpec"] = {
    (spec.key, spec.mode): spec for spec in g.SAMPLES
}
KEYS: list[tuple[str, str]] = sorted(KEY_TEMPLATES)


def harmonic_weights_for_level(level: float) -> tuple[float, ...]:
    """brightness_level ∈ [0,1] → 倍音重みベクトル。L が大きいほど高次倍音が増え
    spectral_centroid が上がる（= brightness ノブ）。"""
    lvl = max(0.0, min(1.0, float(level)))
    return (
        1.0,
        0.2 + 0.6 * lvl,
        0.1 + 0.5 * lvl,
        0.05 + 0.4 * lvl,
        0.02 + 0.3 * lvl,
    )


def build_spec(
    *,
    key: str = "G",
    mode: str = "major",
    bpm: float = 120.0,
    brightness_level: float = 0.5,
    duration_sec: float = PROBE_DURATION_SEC,
    seed: int = DEFAULT_SEED,
) -> "g.SampleSpec":
    """テンプレ spec をノブ値で書き換えて掃引用 SampleSpec を組む。"""
    base = KEY_TEMPLATES[(key, mode)]
    return replace(
        base,
        bpm=float(bpm),
        harmonic_weights=harmonic_weights_for_level(brightness_level),
        duration_sec=float(duration_sec),
        seed=int(seed),
    )


def synth_extract(spec: "g.SampleSpec") -> PhysicalRPE:
    """spec → 決定論合成 → 一時 WAV → 実 extract（golden path と同一経路）。"""
    audio = g.render_sample(spec)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.wav"
        sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16")
        return extract_physical_from_file(str(path))


def physical_invariants(phys: PhysicalRPE) -> list[str]:
    """常に成立すべき不変条件の違反メッセージ一覧（空 == 全て成立）。"""
    issues: list[str] = []
    sp = phys.spectral_profile
    if not 0.0 <= sp.brightness <= 1.0:
        issues.append(f"brightness out of [0,1]: {sp.brightness}")
    for name, value in (
        ("low_ratio", sp.low_ratio),
        ("mid_ratio", sp.mid_ratio),
        ("high_ratio", sp.high_ratio),
    ):
        if not 0.0 <= value <= 1.0:
            issues.append(f"{name} out of [0,1]: {value}")
    if phys.spectral_centroid <= 0.0:
        issues.append(f"spectral_centroid not positive: {phys.spectral_centroid}")
    if phys.bpm_confidence is not None and not 0.0 <= phys.bpm_confidence <= 1.0:
        issues.append(f"bpm_confidence out of [0,1]: {phys.bpm_confidence}")
    if phys.key_confidence is not None and not 0.0 <= phys.key_confidence <= 1.0:
        issues.append(f"key_confidence out of [0,1]: {phys.key_confidence}")
    return issues


def grip_summary(values: Sequence[float]) -> dict:
    """掃引応答列を要約: span（動いた量）と monotone（単調非減少か）。

    span が大きく単調 == tight grip、span ≈ 0 == dead（ツマミ死 or センサー盲）。
    """
    seq = [float(v) for v in values]
    span = max(seq) - min(seq) if seq else 0.0
    monotone = all(later >= earlier - 1e-9 for earlier, later in zip(seq, seq[1:]))
    return {"span": round(span, 4), "monotone_nondecreasing": monotone}


def sweep_brightness(
    key: str,
    mode: str,
    bpm: float,
    levels: Sequence[float],
    duration_sec: float = PROBE_DURATION_SEC,
) -> list[dict]:
    """brightness_level を掃引し centroid / brightness / 帯域比を記録。

    power 系（`high_ratio` ≥4kHz）に加え、B-3 でジャンル brightness 判別器に昇格した
    magnitude `spectral_bands.brilliance`（6-20kHz）も併記する。両センサーの grip を
    同一掃引で並べることで「power 盲（≡0）／magnitude も平坦か」を判別できる。
    """
    rows: list[dict] = []
    for lvl in levels:
        phys = synth_extract(
            build_spec(
                key=key, mode=mode, bpm=bpm, brightness_level=lvl, duration_sec=duration_sec
            )
        )
        sp = phys.spectral_profile
        sb = phys.spectral_bands
        rows.append(
            {
                "level": round(float(lvl), 3),
                "centroid": round(phys.spectral_centroid, 2),
                "brightness": round(sp.brightness, 4),
                "high_ratio": round(sp.high_ratio, 4),
                "mid_ratio": round(sp.mid_ratio, 4),
                "brilliance": round(sb.brilliance, 4) if sb is not None else None,
                "presence": round(sb.presence, 4) if sb is not None else None,
                "key": phys.key,
                "mode": phys.mode,
            }
        )
    return rows


def sweep_bpm(
    key: str,
    mode: str,
    brightness_level: float,
    bpms: Sequence[float],
    duration_sec: float = BPM_PROBE_DURATION_SEC,
) -> list[dict]:
    """入力 bpm を掃引し検出 bpm / 校正比を記録（合成器の bpm grip 計測）。"""
    rows: list[dict] = []
    for in_bpm in bpms:
        phys = synth_extract(
            build_spec(
                key=key,
                mode=mode,
                bpm=in_bpm,
                brightness_level=brightness_level,
                duration_sec=duration_sec,
            )
        )
        det = phys.bpm
        rows.append(
            {
                "in_bpm": round(float(in_bpm), 2),
                "det_bpm": round(det, 2) if det is not None else None,
                "ratio": round(det / in_bpm, 3) if det else None,
                "octave_ambiguous": phys.bpm_octave_ambiguous,
            }
        )
    return rows


def build_report(
    key: str = "G",
    mode: str = "major",
    brightness_levels: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
    bpms: Sequence[float] = (70.0, 100.0, 130.0, 160.0),
) -> dict:
    """掃引を実行し設計知見レポート（dict）を組む。"""
    bright_rows = sweep_brightness(key, mode, 120.0, brightness_levels)
    centroids = [row["centroid"] for row in bright_rows]
    bpm_rows = sweep_bpm(key, mode, 0.5, bpms)
    ratios = [row["ratio"] for row in bpm_rows if row["ratio"] is not None]

    centroid_grip = grip_summary(centroids)
    high_band_blind = all(row["high_ratio"] == 0.0 for row in bright_rows)
    # magnitude brilliance（B-3 のジャンル brightness 判別器）の grip。power high_ratio が
    # ≡0 で盲でも、magnitude が応答すれば「センサーを替えれば叩ける」ことになる。実測では
    # 非ゼロ floor（≈0.02）を持つが span は ~1e-3 で平坦＝magnitude でも合成器では dead。
    brilliances = [row["brilliance"] for row in bright_rows if row["brilliance"] is not None]
    brilliance_grip = (
        grip_summary(brilliances)
        if brilliances
        else {"span": 0.0, "monotone_nondecreasing": True}
    )
    # 平坦判定の閾値: 実測 span ~7e-4。lib 版差を吸収しつつ centroid の tight grip
    # （>5Hz 相当の比率変化）とは桁違いに小さいことを示す保守的な 0.01。
    brilliance_blind = bool(brilliances) and brilliance_grip["span"] < 0.01
    tracked = [r for r in ratios if 0.9 <= r <= 1.1]
    bpm_tracks = bool(ratios) and len(tracked) == len(ratios)
    # ratio が clean octave 窓（÷2: 0.45–0.55 / ×2: 1.8–2.2、screen_corpus と同一）に
    # 入る = オクターブ誤検出。それが octave_ambiguous で掴めているか点検（R2-2a の実効力）。
    # 窓外の gross 誤検出（例 70→172.3=2.46×）は octave でないので含めない。
    def _is_octave_ratio(ratio: float) -> bool:
        return 0.45 <= ratio <= 0.55 or 1.8 <= ratio <= 2.2

    unflagged_octave = [
        row
        for row in bpm_rows
        if row["ratio"] is not None
        and _is_octave_ratio(row["ratio"])
        and not row["octave_ambiguous"]
    ]

    findings = [
        (
            "spectral_centroid は brightness ノブに tight grip"
            if centroid_grip["span"] > 5.0 and centroid_grip["monotone_nondecreasing"]
            else "spectral_centroid の brightness grip は弱い/非単調"
        ),
        (
            "高域比 brightness センサーは合成器レンジで盲（high_ratio≡0）— ツマミ死でなくセンサー盲"
            if high_band_blind
            else "高域比 brightness センサーが応答（合成器が >4kHz を駆動）"
        ),
        (
            "magnitude brilliance（B-3 のジャンル brightness 判別器）も合成器レンジで盲"
            f"（grip span={brilliance_grip['span']}, 非ゼロ floor で平坦）— power 盲を替えても"
            "ジャンル校正は実音源(R1-audio)が必須"
            if brilliance_blind
            else f"magnitude brilliance が応答（grip span={brilliance_grip['span']}）"
        ),
        (
            f"bpm 校正は {len(tracked)}/{len(ratios)} 点が±10%内で追従"
            if bpm_tracks
            else f"bpm 校正は {len(tracked)}/{len(ratios)} 点のみ追従 — 合成器の bpm grip は不安定"
            "、bpm 校正は実音源(R1-audio)が必須"
        ),
    ]
    if unflagged_octave:
        cases = ", ".join(f"{r['in_bpm']}→{r['det_bpm']}" for r in unflagged_octave)
        findings.append(
            f"オクターブ誤検出が octave_ambiguous で未フラグ: {cases} "
            "— R2-2a 半折り検出器のカバレッジ外ケース"
        )

    return {
        "probe_key": f"{key} {mode}",
        "duration_sec": {
            "brightness": PROBE_DURATION_SEC,
            "bpm": BPM_PROBE_DURATION_SEC,
        },
        "brightness_sweep": {
            "rows": bright_rows,
            "centroid_grip": centroid_grip,
            "high_band_blind": high_band_blind,
            "brilliance_grip": brilliance_grip,
            "brilliance_blind": brilliance_blind,
        },
        "bpm_sweep": {
            "rows": bpm_rows,
            "tracks_input": bpm_tracks,
            "unflagged_octave_errors": len(unflagged_octave),
        },
        "findings": findings,
    }


def render_markdown(report: dict) -> str:
    """レポート dict を Markdown に整形（人間が読む設計知見ビュー）。"""
    lines: list[str] = []
    lines.append(f"# Metamorphic Probe Report — key={report['probe_key']}")
    lines.append("")
    lines.append("## Brightness sweep (knob: harmonic richness)")
    lines.append("")
    lines.append(
        "| level | centroid(Hz) | brightness | high_ratio | mid_ratio | brilliance(mag) | key |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in report["brightness_sweep"]["rows"]:
        lines.append(
            f"| {row['level']} | {row['centroid']} | {row['brightness']} | "
            f"{row['high_ratio']} | {row['mid_ratio']} | {row['brilliance']} | "
            f"{row['key']} {row['mode']} |"
        )
    grip = report["brightness_sweep"]["centroid_grip"]
    brilliance_grip = report["brightness_sweep"]["brilliance_grip"]
    lines.append("")
    lines.append(
        f"- centroid grip: span={grip['span']} Hz, monotone={grip['monotone_nondecreasing']}"
    )
    lines.append(f"- high-band blind (power high_ratio): {report['brightness_sweep']['high_band_blind']}")
    lines.append(
        f"- brilliance blind (magnitude): {report['brightness_sweep']['brilliance_blind']} "
        f"(grip span={brilliance_grip['span']})"
    )
    lines.append("")
    lines.append("## BPM sweep (knob: tempo)")
    lines.append("")
    lines.append("| in_bpm | det_bpm | ratio | octave_ambiguous |")
    lines.append("|---|---|---|---|")
    for row in report["bpm_sweep"]["rows"]:
        lines.append(
            f"| {row['in_bpm']} | {row['det_bpm']} | {row['ratio']} | {row['octave_ambiguous']} |"
        )
    lines.append("")
    lines.append(f"- tracks input: {report['bpm_sweep']['tracks_input']}")
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for finding in report["findings"]:
        lines.append(f"- {finding}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON でレポートを出力")
    parser.add_argument("--key", default="G", help="掃引キー (例: G)")
    parser.add_argument("--mode", default="major", help="major | minor")
    parser.add_argument("--out", type=Path, default=None, help="Markdown 出力先パス")
    args = parser.parse_args(argv)

    report = build_report(key=args.key, mode=args.mode)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        markdown = render_markdown(report)
        if args.out is not None:
            args.out.write_text(markdown, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
