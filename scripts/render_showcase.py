"""Fable showcase: 楽譜→コンパイル→決定論演奏→計測→往復診断→楽譜準拠を 1 枚の HTML に焼く。

既存計器（compose / perform / extract / roundtrip / score-adherence）を 1 コマンドで
直列に回し、試聴可能な自己完結 HTML レポートを生成するデモ。計測データ
（showcase.json）は決定論で、HTML は同データの描画＋音声抜粋の埋め込み。

Usage:
    python scripts/render_showcase.py
    python scripts/render_showcase.py --score <score.yaml> --output-dir <dir>
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import sys
from pathlib import Path
from string import Template
from typing import Any

import librosa
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from svp_rpe.compose import ExternalPromptAdapter, load_composition_score  # noqa: E402
from svp_rpe.perform import (  # noqa: E402
    FAITHFUL_TAKE,
    FIRST_TAKE,
    perform,
    sha256_bytes,
    wav_bytes,
)
from svp_rpe.perform.synth import SAMPLE_RATE  # noqa: E402
from svp_rpe.roundtrip.adherence import score_adherence  # noqa: E402
from svp_rpe.roundtrip.diagnose import diagnose_roundtrip  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.transcribe import draft_score, render_draft_score_yaml  # noqa: E402

SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "composition" / "midnight_signal" / "showcase"

# HTML へ埋め込む試聴抜粋: verse 終盤→chorus 突入を跨ぐ窓（低レートで十分暗い素材）
EXCERPT_WINDOW_SEC = (30.0, 54.0)
EXCERPT_SR = 11025

TAKE_LABELS = {
    "first_take": "first_take（初見: 楽譜の指示を弱く読む）",
    "faithful_take": "faithful_take（忠実: 楽譜の指示に寄せる）",
}


# ---------------------------------------------------------------------------
# データ収集（決定論部分）
# ---------------------------------------------------------------------------


def _waveform_points(samples: np.ndarray, *, buckets: int = 640) -> list[float]:
    """ピーク包絡（バケツごとの max |x|）を [0,1] で返す。"""

    normalized = np.abs(samples.astype(np.float64)) / 32767.0
    edges = np.linspace(0, len(normalized), buckets + 1, dtype=int)
    return [
        round(float(normalized[a:b].max()) if b > a else 0.0, 4)
        for a, b in zip(edges[:-1], edges[1:])
    ]


def _excerpt_wav_b64(samples: np.ndarray) -> tuple[str, tuple[float, float]]:
    """試聴用の低レート抜粋 WAV を base64 で返す。"""

    start_sec, end_sec = EXCERPT_WINDOW_SEC
    duration = len(samples) / SAMPLE_RATE
    start_sec = min(start_sec, max(0.0, duration - 1.0))
    end_sec = min(end_sec, duration)
    clip = samples[int(start_sec * SAMPLE_RATE) : int(end_sec * SAMPLE_RATE)]
    floats = clip.astype(np.float64) / 32767.0
    resampled = librosa.resample(floats, orig_sr=SAMPLE_RATE, target_sr=EXCERPT_SR)
    as_int16 = np.round(np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16)

    import io

    from scipy.io import wavfile

    buffer = io.BytesIO()
    wavfile.write(buffer, EXCERPT_SR, as_int16)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), (start_sec, end_sec)


def run_take(score: Any, style: Any, output_dir: Path) -> dict[str, Any]:
    """1 テイクを演奏・保存・抽出し、針の読みと試聴素材を返す。"""

    samples = perform(score, style)
    data = wav_bytes(samples)
    wav_path = output_dir / f"{style.name}.wav"
    wav_path.write_bytes(data)
    bundle = extract_rpe_from_file(str(wav_path))
    excerpt_b64, excerpt_window = _excerpt_wav_b64(samples)
    physical = bundle.physical
    return {
        "style": style.name,
        "wav_path": str(wav_path),
        "wav_sha256": sha256_bytes(data),
        "duration_sec": round(len(samples) / SAMPLE_RATE, 3),
        "needles": {
            "bpm": round(float(physical.bpm), 2) if physical.bpm is not None else None,
            "spectral_centroid": round(float(physical.spectral_centroid), 1),
            "active_rate": round(float(physical.active_rate), 4),
            "key": f"{physical.key} {physical.mode}" if physical.key else None,
        },
        "waveform": _waveform_points(samples),
        "excerpt_b64": excerpt_b64,
        "excerpt_window_sec": [round(v, 1) for v in excerpt_window],
        "bundle": bundle,
    }


def gather(score_path: Path, output_dir: Path) -> dict[str, Any]:
    """パイプライン一式を回して showcase データを組む。"""

    score = load_composition_score(str(score_path))
    prompt = ExternalPromptAdapter().render(score)

    takes = [run_take(score, style, output_dir) for style in (FIRST_TAKE, FAITHFUL_TAKE)]
    faithful = takes[-1]

    transcribed = draft_score(faithful["bundle"])
    roundtrip_report = diagnose_roundtrip(score, transcribed)
    adherence = score_adherence(score, roundtrip_report)
    semantic = faithful["bundle"].semantic

    return {
        "score": score,
        "score_path": str(score_path.relative_to(ROOT)),
        "prompt": prompt,
        "takes": takes,
        "transcribed": transcribed,
        "roundtrip": roundtrip_report,
        "adherence": adherence,
        "semantic": semantic,
    }


def as_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    """決定論の計測結果のみ（音声 base64 は除外）を JSON 化する。"""

    return {
        "schema_version": "1.0",
        "score_path": data["score_path"],
        "prompt": data["prompt"].model_dump(mode="json"),
        "takes": [
            {k: v for k, v in take.items() if k not in ("bundle", "excerpt_b64")}
            for take in data["takes"]
        ],
        "roundtrip": data["roundtrip"].model_dump(mode="json"),
        "adherence": data["adherence"].model_dump(mode="json"),
        "semantic": {
            "por_core": data["semantic"].por_core,
            "cultural_context": data["semantic"].cultural_context,
            "instrumentation_summary": data["semantic"].instrumentation_summary,
            "estimation_disclaimer": data["semantic"].estimation_disclaimer,
        },
    }


# ---------------------------------------------------------------------------
# HTML 描画
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _yaml_block(payload: Any) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip()


def _waveform_svg(points: list[float], *, width: int = 640, height: int = 72) -> str:
    mid = height / 2
    step = width / max(1, len(points))
    top = " ".join(
        f"{i * step:.1f},{mid - max(1.0, value * (mid - 2)):.1f}"
        for i, value in enumerate(points)
    )
    bottom = " ".join(
        f"{(len(points) - 1 - i) * step:.1f},{mid + max(1.0, value * (mid - 2)):.1f}"
        for i, value in enumerate(reversed(points))
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'aria-label="waveform" class="wave"><polygon points="{top} {bottom}" /></svg>'
    )


def _needle_strip(
    *,
    domain: tuple[float, float],
    bands: list[tuple[float, float, str]],
    target: tuple[float, str] | None,
    marks: list[tuple[float, str]],
    unit: str,
    width: int = 640,
) -> str:
    """1 本の水平スケールに帯（許容域）・目標線（amber）・実測点（cyan）を置く。"""

    lo, hi = domain
    span = hi - lo
    height, axis_y = 80, 42

    def x(value: float) -> float:
        return (max(lo, min(hi, value)) - lo) / span * (width - 20) + 10

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="strip" role="img" aria-label="scale">'
    ]
    for b_lo, b_hi, label in bands:
        parts.append(
            f'<rect x="{x(b_lo):.1f}" y="{axis_y - 13}" width="{x(b_hi) - x(b_lo):.1f}" '
            f'height="13" class="band" />'
            f'<text x="{x(b_lo) + 4:.1f}" y="{axis_y - 17}" class="bandlabel">{_esc(label)}</text>'
        )
    parts.append(
        f'<line x1="10" y1="{axis_y}" x2="{width - 10}" y2="{axis_y}" class="axis" />'
    )
    for tick in (lo, hi):
        parts.append(
            f'<text x="{x(tick):.1f}" y="{axis_y + 14}" class="tick" '
            f'text-anchor="middle">{tick:g}{_esc(unit)}</text>'
        )
    if target is not None:
        t_value, t_label = target
        parts.append(
            f'<line x1="{x(t_value):.1f}" y1="{axis_y - 22}" x2="{x(t_value):.1f}" '
            f'y2="{axis_y + 4}" class="target" />'
            f'<text x="{x(t_value):.1f}" y="{axis_y - 26}" class="targetlabel" '
            f'text-anchor="middle">{_esc(t_label)}</text>'
        )
    for index, (m_value, m_label) in enumerate(marks):
        label_y = axis_y + 18 + (index % 2) * 14  # 近接マークのラベル衝突を段違いで回避
        parts.append(
            f'<circle cx="{x(m_value):.1f}" cy="{axis_y}" r="5" class="mark" />'
            f'<text x="{x(m_value):.1f}" y="{label_y}" class="marklabel" '
            f'text-anchor="middle">{_esc(m_label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


_DIAGNOSIS_JA = {
    "preserved": "保存",
    "sensor_blind": "センサー盲",
    "knob_dead": "ツマミ死",
    "calibration_disagreement": "校正不一致",
}


def _diagnosis_chip(diagnosis: str) -> str:
    label = _DIAGNOSIS_JA.get(diagnosis, diagnosis)
    return f'<span class="chip chip-{_esc(diagnosis)}">{_esc(label)} <code>{_esc(diagnosis)}</code></span>'


def render_html(data: dict[str, Any]) -> str:
    score = data["score"]
    prompt = data["prompt"]
    roundtrip = data["roundtrip"]
    adherence = data["adherence"]
    semantic = data["semantic"]
    takes = data["takes"]

    # --- stage 1: 楽譜 -----------------------------------------------------
    physical_yaml = _yaml_block(score.physical.model_dump(mode="json", exclude_none=True))
    profile_rows = []
    backend_profile = (score.control_profile or {}).get(adherence.backend, {})
    for field, grip in backend_profile.items():
        grip_txt = "" if grip.grip is None else f"{grip.grip:.2f}"
        profile_rows.append(
            "<tr>"
            f"<td><code>{_esc(field)}</code></td>"
            f'<td><span class="chip chip-{_esc(grip.grip_class)}">{_esc(grip.grip_class)}</span></td>'
            f'<td class="num">{grip_txt}</td>'
            f"<td><code>{_esc(grip.sensor or '')}</code></td>"
            "</tr>"
        )

    # --- stage 2: コンパイル ------------------------------------------------
    advisories = "".join(
        f'<div class="advisory"><span class="advisory-tag">機種メモ</span>{_esc(item)}</div>'
        for item in prompt.advisories
    )
    dropped = (
        "、".join(f"<code>{_esc(item)}</code>" for item in prompt.dropped_elements)
        if prompt.dropped_elements
        else "なし（全要素がプロンプトに残った）"
    )

    # --- stage 3: 演奏 ------------------------------------------------------
    take_cards = []
    for take in takes:
        start, end = take["excerpt_window_sec"]
        take_cards.append(
            '<div class="take">'
            f'<div class="take-head"><strong>{_esc(TAKE_LABELS.get(take["style"], take["style"]))}</strong>'
            f'<span class="mono dim">sha256 {_esc(take["wav_sha256"][:16])}…</span></div>'
            f"{_waveform_svg(take['waveform'])}"
            f'<audio controls preload="metadata" '
            f'src="data:audio/wav;base64,{take["excerpt_b64"]}"></audio>'
            f'<div class="dim small">試聴は {start:g}–{end:g} 秒の抜粋（{EXCERPT_SR / 1000:g} kHz 縮約）。'
            f"全長 {take['duration_sec']:.0f} 秒・44.1 kHz の原音 WAV は決定論に再生成できる。</div>"
            "</div>"
        )

    bpm_strip = _needle_strip(
        domain=(90, 150),
        bands=[],
        target=(float(score.physical.bpm), f"楽譜 {score.physical.bpm:g}"),
        marks=[
            (take["needles"]["bpm"], f"{take['style'].split('_')[0]} {take['needles']['bpm']:g}")
            for take in takes
            if take["needles"]["bpm"] is not None
        ],
        unit="",
    )
    centroid_strip = _needle_strip(
        domain=(0, 3000),
        bands=[(0, 1200, "dark 帯 (≤1200 Hz)"), (2500, 3000, "bright 帯")],
        target=None,
        marks=[
            (
                take["needles"]["spectral_centroid"],
                f"{take['style'].split('_')[0]} {take['needles']['spectral_centroid']:.0f}",
            )
            for take in takes
        ],
        unit="Hz",
    )

    # --- stage 4: 往復診断 ---------------------------------------------------
    roundtrip_rows = []
    preserved_count = sum(1 for f in roundtrip.fields if f.diagnosis == "preserved")
    for field in roundtrip.fields:
        grip_txt = "" if field.grip is None else f"{field.grip:.3g}"
        roundtrip_rows.append(
            "<tr>"
            f"<td><code>{_esc(field.field)}</code></td>"
            f'<td class="authored">{_esc(field.source_value)}</td>'
            f'<td class="measured">{_esc(field.transcribed_value)}</td>'
            f"<td>{_diagnosis_chip(field.diagnosis)}</td>"
            f'<td class="num">{grip_txt}</td>'
            f"<td>{_esc(field.note or '')}</td>"
            "</tr>"
        )
    draft_yaml = _yaml_block(
        data["transcribed"].physical.model_dump(mode="json", exclude_none=True)
    )

    # --- stage 5: 楽譜準拠 + 意味層 -------------------------------------------
    adherence_rows = []
    for row in adherence.tight_fields:
        grip_txt = "" if row.grip is None else f"{row.grip:.2f}"
        adherence_rows.append(
            "<tr>"
            f"<td><code>{_esc(row.field)}</code></td>"
            f'<td class="num">{grip_txt}</td>'
            f"<td><code>{_esc(row.sensor or '')}</code></td>"
            f'<td>{"✓ 保持" if row.compiled_kept else "✗ drop"}</td>'
            f"<td>{_diagnosis_chip(row.roundtrip_diagnosis) if row.roundtrip_diagnosis else ''}</td>"
            f'<td>{"✓" if row.preserved else "✗"}</td>'
            "</tr>"
        )
    contexts = "、".join(semantic.cultural_context) or "—"

    template = Template(_PAGE_TEMPLATE)
    return template.substitute(
        title=_esc(score.meta.title),
        score_path=_esc(data["score_path"]),
        bpm=_esc(f"{score.physical.bpm:g}"),
        key=_esc(score.physical.key),
        time_signature=_esc(score.physical.time_signature),
        brightness=_esc(score.physical.brightness),
        stereo_width=_esc(score.physical.stereo_width),
        core=_esc(score.semantic.core),
        physical_yaml=_esc(physical_yaml),
        profile_rows="".join(profile_rows),
        backend=_esc(adherence.backend),
        prompt_text=_esc(prompt.text),
        advisories=advisories,
        dropped=dropped,
        take_cards="".join(take_cards),
        bpm_strip=bpm_strip,
        centroid_strip=centroid_strip,
        roundtrip_rows="".join(roundtrip_rows),
        preserved_count=str(preserved_count),
        field_count=str(len(roundtrip.fields)),
        draft_yaml=_esc(draft_yaml),
        adherence_rows="".join(adherence_rows),
        tight_total=str(adherence.total_tight),
        kept_count=str(adherence.compiled_kept_count),
        adh_preserved=str(adherence.preserved_count),
        por_core=_esc(semantic.por_core),
        contexts=_esc(contexts),
        instrumentation=_esc(semantic.instrumentation_summary),
        disclaimer=_esc(semantic.estimation_disclaimer),
        first_sha=_esc(takes[0]["wav_sha256"]),
        faithful_sha=_esc(takes[1]["wav_sha256"]),
    )


_PAGE_TEMPLATE = """<title>$title — 楽譜は往復する</title>
<style>
:root{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --band:rgba(32,116,158,.14);
  --code-bg:#10182A; --code-ink:#D8DFEF; --code-dim:#8B96AF;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --band:rgba(62,155,208,.16);
}}
:root[data-theme="dark"]{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --band:rgba(62,155,208,.16);
}
:root[data-theme="light"]{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --band:rgba(32,116,158,.14);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;
  font-family:"Hiragino Sans","Yu Gothic UI","Noto Sans JP","Segoe UI",system-ui,sans-serif;
  font-size:15px;line-height:1.75}
.wrap{max-width:880px;margin:0 auto;padding:56px 24px 72px}
.mono,code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
code{font-size:.88em}
.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:600}
h1{font-size:30px;line-height:1.3;margin:.35em 0 .2em;text-wrap:balance;letter-spacing:-.01em}
.lede{color:var(--muted);max-width:42em;margin:0 0 8px}
.loop{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:20px 0 0;
  font-size:13px;color:var(--muted)}
.loop b{color:var(--ink);font-weight:600;background:var(--surface);
  border:1px solid var(--line);border-radius:999px;padding:3px 12px}
.loop span{color:var(--muted)}
.stage{display:grid;grid-template-columns:44px 1fr;gap:0 18px;margin-top:44px}
.stage>div:last-child{min-width:0}
.stage-no{width:32px;height:32px;border-radius:50%;border:1.5px solid var(--line);
  background:var(--surface);display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:700;color:var(--muted)}
.stage-rail{width:1px;background:var(--line);margin:6px auto 0;flex:1}
.stage-left{display:flex;flex-direction:column;align-items:center}
h2{font-size:19px;margin:2px 0 4px;letter-spacing:-.005em}
.stage-sub{color:var(--muted);margin:0 0 14px;max-width:44em}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:10px 0}
.cmd{background:var(--code-bg);color:var(--code-dim);border-radius:8px;padding:9px 14px;
  font-size:12.5px;margin:10px 0;overflow-x:auto;white-space:nowrap}
.cmd b{color:var(--code-ink);font-weight:500}
pre{background:var(--code-bg);color:var(--code-ink);border-radius:10px;padding:14px 16px;
  font-size:12.5px;line-height:1.65;overflow-x:auto;margin:10px 0;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
table{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{text-align:right}
td.authored{color:var(--authored);font-weight:600}
td.measured{color:var(--measured);font-weight:600}
.chip{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12px;
  font-weight:600;border:1px solid var(--line);white-space:nowrap}
.chip code{font-size:.85em;opacity:.75}
.chip-preserved{background:var(--ok-bg);color:var(--ok);border-color:transparent}
.chip-sensor_blind{color:var(--muted);border-style:dashed}
.chip-knob_dead,.chip-calibration_disagreement{background:var(--warn-bg);
  color:var(--authored);border-color:transparent}
.chip-tight{background:var(--ok-bg);color:var(--ok);border-color:transparent}
.chip-loose{color:var(--muted);border-style:dashed}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.fact{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:6px 12px;font-size:13px}
.fact b{font-variant-numeric:tabular-nums}
.fact .dim{margin-right:.5em}
.advisory{background:var(--warn-bg);border-radius:8px;padding:10px 14px;margin:10px 0;
  font-size:13.5px}
.advisory-tag{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--authored);
  margin-right:10px;text-transform:uppercase}
.take{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;margin:12px 0}
.take-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  align-items:baseline;margin-bottom:8px;font-size:14px}
.wave{width:100%;height:72px;display:block;margin:4px 0 10px}
.wave polygon{fill:var(--measured);opacity:.75}
audio{width:100%;margin:2px 0 6px}
.strip{width:100%;height:80px;display:block;margin:2px 0 8px}
.prewrap{white-space:pre-wrap;word-break:break-word}
.strip .axis{stroke:var(--line);stroke-width:1}
.strip .band{fill:var(--band)}
.strip .bandlabel,.strip .tick,.strip .marklabel,.strip .targetlabel{
  font-size:10.5px;fill:var(--muted);
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.strip .targetlabel{fill:var(--authored);font-weight:700}
.strip .marklabel{fill:var(--measured)}
.strip .target{stroke:var(--authored);stroke-width:2}
.strip .mark{fill:var(--measured);stroke:var(--surface);stroke-width:2}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;
  margin:12px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px}
.tile .value{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em}
.tile .label{font-size:12px;color:var(--muted)}
.legend{display:flex;gap:16px;font-size:12.5px;color:var(--muted);margin:6px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.dim{color:var(--muted)}
.small{font-size:12.5px}
.foot{margin-top:52px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
.foot code{word-break:break-all}
@media (max-width:640px){.stage{grid-template-columns:32px 1fr;gap:0 12px}
  .stage-no{width:26px;height:26px;font-size:12px}}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">svp-rpe deterministic showcase</div>
  <h1>$title — 楽譜は往復する</h1>
  <p class="lede">YAML の楽譜 1 枚が、プロンプトにコンパイルされ、決定論の演奏者に音にされ、
  計器に測り返され、楽譜として戻ってくる。API キーなし・LLM なし・同一入力→同一出力。
  このページの数値はすべて 1 コマンドの実測から生成されている。</p>
  <div class="loop"><b>楽譜</b><span>→</span><b>コンパイル</b><span>→</span><b>演奏</b>
  <span>→</span><b>計測</b><span>→</span><b>往復診断</b><span>→</span><b>楽譜′</b></div>
</header>

<section class="stage">
  <div class="stage-left"><div class="stage-no">1</div><div class="stage-rail"></div></div>
  <div>
    <h2>楽譜 — 作曲の意図を 1 枚の YAML に書く</h2>
    <p class="stage-sub"><code>$score_path</code>。物理層（測れる指示）と意味層（聴感の指示）を
    分けて書く。楽譜は自分の <code>control_profile</code> を持ち、
    「この生成器ではどのツマミが効くか」を自己記述する。</p>
    <div class="facts">
      <div class="fact"><span class="dim">bpm</span> <b>$bpm</b></div>
      <div class="fact"><span class="dim">key</span> <b>$key</b></div>
      <div class="fact"><span class="dim">拍子</span> <b>$time_signature</b></div>
      <div class="fact"><span class="dim">明るさ</span> <b>$brightness</b></div>
      <div class="fact"><span class="dim">ステレオ</span> <b>$stereo_width</b></div>
      <div class="fact"><span class="dim">core</span> <b>$core</b></div>
    </div>
    <pre>$physical_yaml</pre>
    <div class="panel">
      <div class="eyebrow" style="margin-bottom:8px">control_profile（backend: $backend）
      — 実測 grip の自己申告</div>
      <div class="tablewrap"><table>
        <tr><th>field</th><th>grip_class</th><th>grip</th><th>sensor</th></tr>
        $profile_rows
      </table></div>
    </div>
  </div>
</section>

<section class="stage">
  <div class="stage-left"><div class="stage-no">2</div><div class="stage-rail"></div></div>
  <div>
    <h2>コンパイル — tight なツマミを先頭に昇格させる</h2>
    <p class="stage-sub">control_profile-aware compile。grip が実証済みのフィールド
    （bpm・brightness）が文頭へ昇格し、字数超過時も tight は最後まで drop されない。
    機種の既知の癖はプロンプト本文を汚さず「機種メモ」として添えられる。</p>
    <div class="cmd">$$ <b>svprpe compose $score_path</b></div>
    <pre class="prewrap">$prompt_text</pre>
    $advisories
    <p class="small dim">drop された要素: $dropped</p>
  </div>
</section>

<section class="stage">
  <div class="stage-left"><div class="stage-no">3</div><div class="stage-rail"></div></div>
  <div>
    <h2>演奏 — 決定論の演奏者が 2 通りに弾く</h2>
    <p class="stage-sub">同じ楽譜を 2 つの演奏スタイルで音にする。乱数は固定シード、
    WAV の sha256 が恒久 ID。「楽譜が同じでも演奏は違う」を耳でも針でも確認できる。</p>
    $take_cards
    <div class="panel">
      <div class="eyebrow" style="margin-bottom:6px">針の読み — BPM</div>
      $bpm_strip
      <div class="eyebrow" style="margin:14px 0 6px">針の読み — 明るさ (spectral centroid)</div>
      $centroid_strip
      <div class="legend">
        <span><i style="background:var(--authored)"></i>楽譜の指示</span>
        <span><i style="background:var(--measured)"></i>計器の実測</span>
        <span><i style="background:var(--band)"></i>ラベル帯（dark ≤1200 Hz / bright ≥2500 Hz）</span>
      </div>
    </div>
  </div>
</section>

<section class="stage">
  <div class="stage-left"><div class="stage-no">4</div><div class="stage-rail"></div></div>
  <div>
    <h2>往復診断 — 音から楽譜を測り返す</h2>
    <p class="stage-sub">faithful_take の音を extract し、draft 楽譜へ転写して原譜と突き合わせる。
    診断は 4 値（保存 / ツマミ死 / センサー盲 / 校正不一致）で、合否ではなく計器の読みとして出る。
    <b>$preserved_count/$field_count フィールドが保存</b>、stereo はセンサー未校正を
    正直に申告する。</p>
    <div class="cmd">$$ <b>svprpe roundtrip $score_path</b></div>
    <div class="panel"><div class="tablewrap"><table>
      <tr><th>field</th><th>楽譜（原譜）</th><th>転写（楽譜′）</th><th>診断</th>
      <th>grip</th><th>note</th></tr>
      $roundtrip_rows
    </table></div></div>
    <details>
      <summary class="dim small" style="cursor:pointer">戻ってきた楽譜′（draft physical 層）を開く</summary>
      <pre>$draft_yaml</pre>
    </details>
  </div>
</section>

<section class="stage">
  <div class="stage-left"><div class="stage-no">5</div></div>
  <div>
    <h2>楽譜準拠 — tight 宣言は守られたか</h2>
    <p class="stage-sub">楽譜が「効く」と宣言したフィールドだけを、コンパイル保持と往復保存の
    両面から判定する。これも verdict ではなく、フィールド単位の計器の読み。</p>
    <div class="cmd">$$ <b>svprpe score-adherence $score_path</b></div>
    <div class="tiles">
      <div class="tile"><div class="value">$tight_total</div>
        <div class="label">tight 宣言フィールド</div></div>
      <div class="tile"><div class="value">$kept_count/$tight_total</div>
        <div class="label">コンパイルで保持</div></div>
      <div class="tile"><div class="value">$adh_preserved/$tight_total</div>
        <div class="label">往復で保存</div></div>
    </div>
    <div class="panel"><div class="tablewrap"><table>
      <tr><th>field</th><th>grip</th><th>sensor</th><th>compile</th>
      <th>roundtrip</th><th>preserved</th></tr>
      $adherence_rows
    </table></div></div>
    <div class="panel">
      <div class="eyebrow" style="margin-bottom:8px">意味層の読み（ルールベース推定）</div>
      <p style="margin:.2em 0">core: <b>$por_core</b></p>
      <p style="margin:.2em 0">文脈: $contexts</p>
      <p style="margin:.2em 0">楽器感: $instrumentation</p>
      <p class="small dim" style="margin:.6em 0 0">意味層は「演奏の音色」を読む。決定論演奏者は
      正弦波スケッチなので、意図（deep_house）ではなく実際に鳴っている音の性格が返る —
      センサーは意図に忖度しない。$disclaimer</p>
    </div>
  </div>
</section>

<footer class="foot">
  <p><b>決定論の証跡</b> — first_take <code>$first_sha</code> / faithful_take
  <code>$faithful_sha</code>。同じリポジトリで
  <code>python scripts/render_showcase.py</code> を実行すれば、同じ WAV・同じ数値・
  同じページが再生成される。</p>
</footer>
</div>
"""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(score_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore = output_dir / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    data = gather(score_path, output_dir)
    payload = as_json_payload(data)
    (output_dir / "showcase.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "draft_score.yaml").write_text(
        render_draft_score_yaml(data["transcribed"]), encoding="utf-8"
    )
    (output_dir / "showcase.html").write_text(render_html(data), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=SCORE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    payload = run(args.score, args.output_dir)
    roundtrip = payload["roundtrip"]
    preserved = sum(1 for f in roundtrip["fields"] if f["diagnosis"] == "preserved")
    print(f"roundtrip: {preserved}/{len(roundtrip['fields'])} preserved")
    adherence = payload["adherence"]
    print(
        "adherence: "
        f"tight={adherence['total_tight']} "
        f"kept={adherence['compiled_kept_count']}/{adherence['total_tight']} "
        f"preserved={adherence['preserved_count']}/{adherence['total_tight']}"
    )
    print(f"artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
