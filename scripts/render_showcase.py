"""Fable showcase: 「文字が音になる。音が文字に戻る。」を耳で体験する 1 枚 HTML を焼く。

一般向けのストーリーで構成する:

1. 楽譜は 7 行のテキスト
2. 1 行変えると音が変わる（テンポ / 調 / 明るさの 3 変奏 + 計器の読み）
3. 音だけから楽譜が戻ってくる（roundtrip 診断のやさしい表示）
4. これは何の実験か

技術詳細（コンパイル・楽譜準拠・意味層・sha256）は折りたたみに退避する。
計測データ（showcase.json）は決定論で、HTML は同データの描画＋音声抜粋の埋め込み。

Usage:
    python scripts/render_showcase.py
    python scripts/render_showcase.py --score <score.yaml> --output-dir <dir>
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import sys
from pathlib import Path
from string import Template
from typing import Any, Callable

import librosa
import numpy as np
import yaml
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from svp_rpe.compose import ExternalPromptAdapter, load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.perform import FAITHFUL_TAKE, perform, sha256_bytes, wav_bytes  # noqa: E402
from svp_rpe.perform.synth import SAMPLE_RATE  # noqa: E402
from svp_rpe.roundtrip.adherence import score_adherence  # noqa: E402
from svp_rpe.roundtrip.diagnose import diagnose_roundtrip  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.transcribe import draft_score, render_draft_score_yaml  # noqa: E402

SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "composition" / "midnight_signal" / "showcase"

# 試聴抜粋: verse 終盤→chorus 突入を跨ぐよう小節基準で切る（テンポ変奏でも同じ場面になる）
EXCERPT_START_BAR = 20
EXCERPT_SEC = 22.0
EXCERPT_SR = 11025


# ---------------------------------------------------------------------------
# 変奏の定義
# ---------------------------------------------------------------------------


def _set_bpm(score: CompositionScore) -> None:
    score.physical.bpm = 80


def _set_major(score: CompositionScore) -> None:
    score.physical.key = "C major"


def _set_bright(score: CompositionScore) -> None:
    score.physical.brightness = "bright"


VARIANTS: list[dict[str, Any]] = [
    {
        "name": "slow",
        "title": "テンポを落とす",
        "diff_field": "bpm",
        "diff_after": "bpm: 80",
        "mutate": _set_bpm,
        "meter": "bpm",
    },
    {
        "name": "major",
        "title": "悲しい調を、明るい調に",
        "diff_field": "key",
        "diff_after": "key: C major",
        "mutate": _set_major,
        "meter": "key",
    },
    {
        "name": "bright",
        "title": "音色を明るくする",
        "diff_field": "brightness",
        "diff_after": "brightness: bright",
        "mutate": _set_bright,
        "meter": "centroid",
    },
]


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


def _excerpt_wav_b64(samples: np.ndarray, score: CompositionScore) -> str:
    """小節基準の試聴抜粋を低レート WAV の base64 で返す。"""

    beats_per_bar = int(score.physical.time_signature.split("/", 1)[0])
    bar_sec = beats_per_bar * 60.0 / float(score.physical.bpm)
    duration = len(samples) / SAMPLE_RATE
    start_sec = min(EXCERPT_START_BAR * bar_sec, max(0.0, duration - EXCERPT_SEC))
    end_sec = min(start_sec + EXCERPT_SEC, duration)
    clip = samples[int(start_sec * SAMPLE_RATE) : int(end_sec * SAMPLE_RATE)]
    floats = clip.astype(np.float64) / 32767.0
    resampled = librosa.resample(floats, orig_sr=SAMPLE_RATE, target_sr=EXCERPT_SR)
    as_int16 = np.round(np.clip(resampled, -1.0, 1.0) * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    wavfile.write(buffer, EXCERPT_SR, as_int16)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def render_and_measure(
    score: CompositionScore, output_dir: Path, name: str
) -> dict[str, Any]:
    """1 スコアを決定論演奏し、WAV 保存・抽出・試聴素材化する。"""

    samples = perform(score, FAITHFUL_TAKE)
    data = wav_bytes(samples)
    wav_path = output_dir / f"{name}.wav"
    wav_path.write_bytes(data)
    bundle = extract_rpe_from_file(str(wav_path))
    physical = bundle.physical
    return {
        "name": name,
        "wav_sha256": sha256_bytes(data),
        "duration_sec": round(len(samples) / SAMPLE_RATE, 3),
        "measured": {
            "bpm": round(float(physical.bpm), 1) if physical.bpm is not None else None,
            "key": f"{physical.key} {physical.mode}" if physical.key else None,
            "centroid": round(float(physical.spectral_centroid), 0),
        },
        "waveform": _waveform_points(samples),
        "excerpt_b64": _excerpt_wav_b64(samples, score),
        "bundle": bundle,
    }


def _display_path(path: Path) -> str:
    """表示専用のパス文字列を作る。

    ``--score`` は repo 外の楽譜（他所に置かれた composition_score.yaml）を
    指すのも正常系なので、``relative_to(ROOT)`` が使えない場合は
    ``os.path.relpath`` へ、それも不適切なら絶対パス文字列へフォールバックする。
    """

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        try:
            return os.path.relpath(path, ROOT)
        except ValueError:
            return str(path)


def gather(score_path: Path, output_dir: Path) -> dict[str, Any]:
    """原曲 + 3 変奏を演奏・計測し、原曲は往復診断まで回す。"""

    score = load_composition_score(str(score_path))
    prompt = ExternalPromptAdapter().render(score)

    original = render_and_measure(score, output_dir, "original")
    variants: list[dict[str, Any]] = []
    for spec in VARIANTS:
        mutated = score.model_copy(deep=True)
        mutate: Callable[[CompositionScore], None] = spec["mutate"]
        mutate(mutated)
        result = render_and_measure(mutated, output_dir, spec["name"])
        field = spec["diff_field"]
        before_label = f"{field}: {getattr(score.physical, field)}"
        result.update(
            title=spec["title"],
            diff=(before_label, spec["diff_after"]),
            meter=spec["meter"],
        )
        variants.append(result)

    transcribed = draft_score(original["bundle"])
    roundtrip_report = diagnose_roundtrip(score, transcribed)
    adherence = score_adherence(score, roundtrip_report)
    semantic = original["bundle"].semantic

    return {
        "score": score,
        "score_path": _display_path(score_path),
        "prompt": prompt,
        "original": original,
        "variants": variants,
        "transcribed": transcribed,
        "roundtrip": roundtrip_report,
        "adherence": adherence,
        "semantic": semantic,
    }


def as_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    """決定論の計測結果のみ（音声 base64 は除外）を JSON 化する。"""

    def slim(take: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v for k, v in take.items() if k not in ("bundle", "excerpt_b64", "mutate")
        }

    return {
        "schema_version": "2.0",
        "score_path": data["score_path"],
        "prompt": data["prompt"].model_dump(mode="json"),
        "original": slim(data["original"]),
        "variants": [slim(v) for v in data["variants"]],
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


def _waveform_svg(points: list[float], *, width: int = 640, height: int = 64) -> str:
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


def _audio(b64: str) -> str:
    return (
        f'<audio controls preload="metadata" src="data:audio/wav;base64,{b64}"></audio>'
    )


def _meter_row(variant: dict[str, Any], baseline: dict[str, Any]) -> str:
    """変奏カードの「計器の読み」行を作る。"""

    kind = variant["meter"]
    before = baseline["measured"]
    after = variant["measured"]
    if kind == "bpm":
        reading = (
            f'<b class="measured">{before["bpm"]:g} BPM</b> → '
            f'<b class="measured">{after["bpm"]:g} BPM</b>'
        )
        verdict = "✓ 書いた 80 に着地"
    elif kind == "key":
        reading = (
            f'<b class="measured">{_esc(before["key"])}</b> → '
            f'<b class="measured">{_esc(after["key"])}</b>'
        )
        verdict = "✓ 長調と聴き当てた"
    else:
        reading = (
            f'<b class="measured">{before["centroid"]:g} Hz</b> → '
            f'<b class="measured">{after["centroid"]:g} Hz</b>'
        )
        verdict = "✓ 針が明るい方へ動いた"
    return (
        '<div class="meter"><span class="meter-label">計器の読み</span>'
        f"{reading}<span class='meter-verdict'>{verdict}</span></div>"
    )


_ROUNDTRIP_JA = {
    "bpm": "テンポ",
    "key": "調",
    "time_signature": "拍子",
    "brightness": "明るさ",
    "active_rate_target": "音の密度",
    "valley_depth_target": "静けさの深さ",
    "stereo_width": "音の広がり",
}


def render_html(data: dict[str, Any]) -> str:
    score = data["score"]
    prompt = data["prompt"]
    roundtrip = data["roundtrip"]
    adherence = data["adherence"]
    semantic = data["semantic"]
    original = data["original"]

    physical_yaml = _yaml_block(score.physical.model_dump(mode="json", exclude_none=True))

    variant_cards = []
    for variant in data["variants"]:
        before, after = variant["diff"]
        variant_cards.append(
            '<div class="card">'
            f'<div class="card-title">{_esc(variant["title"])}</div>'
            '<div class="diff">'
            f'<span class="diff-del">{_esc(before)}</span>'
            f'<span class="diff-arrow">→</span>'
            f'<span class="diff-add">{_esc(after)}</span></div>'
            f"{_waveform_svg(variant['waveform'])}"
            f"{_audio(variant['excerpt_b64'])}"
            f"{_meter_row(variant, original)}"
            "</div>"
        )

    preserved_count = sum(1 for f in roundtrip.fields if f.diagnosis == "preserved")
    roundtrip_rows = []
    for field in roundtrip.fields:
        ok = field.diagnosis == "preserved"
        if ok:
            state = '<span class="chip chip-ok">✓ 読めた</span>'
        else:
            state = '<span class="chip chip-na">読めない（センサー未校正と自己申告）</span>'
        transcribed = field.transcribed_value
        if isinstance(transcribed, str) and transcribed.startswith("TODO"):
            transcribed = "—"
        roundtrip_rows.append(
            "<tr>"
            f"<td>{_esc(_ROUNDTRIP_JA.get(field.field, field.field))} "
            f"<code class='dim'>{_esc(field.field)}</code></td>"
            f'<td class="authored">{_esc(field.source_value)}</td>'
            f'<td class="measured">{_esc(transcribed)}</td>'
            f"<td>{state}</td>"
            "</tr>"
        )

    advisories = "".join(
        f'<div class="advisory"><span class="advisory-tag">機種メモ</span>{_esc(item)}</div>'
        for item in prompt.advisories
    )
    adherence_rows = []
    for row in adherence.tight_fields:
        grip_txt = "" if row.grip is None else f"{row.grip:.2f}"
        adherence_rows.append(
            "<tr>"
            f"<td><code>{_esc(row.field)}</code></td>"
            f'<td class="num">{grip_txt}</td>'
            f"<td><code>{_esc(row.sensor or '')}</code></td>"
            f'<td>{"✓ 保持" if row.compiled_kept else "✗ drop"}</td>'
            f"<td><code>{_esc(row.roundtrip_diagnosis or '')}</code></td>"
            f'<td>{"✓" if row.preserved else "✗"}</td>'
            "</tr>"
        )
    draft_yaml = _yaml_block(
        data["transcribed"].physical.model_dump(mode="json", exclude_none=True)
    )
    contexts = "、".join(semantic.cultural_context) or "—"

    template = Template(_PAGE_TEMPLATE)
    return template.substitute(
        title=_esc(score.meta.title),
        score_path=_esc(data["score_path"]),
        physical_yaml=_esc(physical_yaml),
        hero_audio=_audio(original["excerpt_b64"]),
        hero_wave=_waveform_svg(original["waveform"]),
        variant_cards="".join(variant_cards),
        preserved_count=str(preserved_count),
        field_count=str(len(roundtrip.fields)),
        unread_count=str(len(roundtrip.fields) - preserved_count),
        roundtrip_rows="".join(roundtrip_rows),
        prompt_text=_esc(prompt.text),
        advisories=advisories,
        adherence_rows="".join(adherence_rows),
        tight_total=str(adherence.total_tight),
        kept_count=str(adherence.compiled_kept_count),
        adh_preserved=str(adherence.preserved_count),
        por_core=_esc(semantic.por_core),
        contexts=_esc(contexts),
        instrumentation=_esc(semantic.instrumentation_summary),
        disclaimer=_esc(semantic.estimation_disclaimer),
        draft_yaml=_esc(draft_yaml),
        original_sha=_esc(original["wav_sha256"]),
        original_bpm=f"{original['measured']['bpm']:g}",
        original_key=_esc(original["measured"]["key"]),
    )


_PAGE_TEMPLATE = """<title>$title — 文字が音になる。音が文字に戻る。</title>
<style>
:root{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --del:#A33D3D;
  --code-bg:#10182A; --code-ink:#D8DFEF; --code-dim:#8B96AF;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --del:#D07070;
}}
:root[data-theme="dark"]{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --del:#D07070;
}
:root[data-theme="light"]{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --del:#A33D3D;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;
  font-family:"Hiragino Sans","Yu Gothic UI","Noto Sans JP","Segoe UI",system-ui,sans-serif;
  font-size:15.5px;line-height:1.85}
.wrap{max-width:760px;margin:0 auto;padding:64px 24px 72px}
.mono,code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
code{font-size:.88em}
.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:600}
h1{font-size:clamp(30px,6vw,42px);line-height:1.25;margin:.4em 0 .3em;
  text-wrap:balance;letter-spacing:-.015em}
.lede{color:var(--muted);font-size:17px;max-width:36em;margin:0}
.hero-listen{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;margin:28px 0 0}
.hero-listen .cap{font-weight:700;margin-bottom:6px}
.hero-listen .sub{font-size:13px;color:var(--muted);margin-top:6px}
section{margin-top:64px}
.chap{font-size:12px;font-weight:700;letter-spacing:.14em;color:var(--measured);
  text-transform:uppercase}
h2{font-size:24px;line-height:1.35;margin:.3em 0 .4em;letter-spacing:-.01em;
  text-wrap:balance}
.sub{color:var(--muted);margin:0 0 18px;max-width:40em}
pre{background:var(--code-bg);color:var(--code-ink);border-radius:10px;padding:14px 16px;
  font-size:13px;line-height:1.7;overflow-x:auto;margin:10px 0;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.prewrap{white-space:pre-wrap;word-break:break-word}
.score-note{display:grid;grid-template-columns:1fr;gap:2px 14px;font-size:13.5px;
  color:var(--muted);margin-top:10px}
.score-note b{color:var(--ink)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:14px 0}
.card-title{font-weight:700;font-size:16px;margin-bottom:6px}
.diff{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px}
.diff-del{color:var(--del);text-decoration:line-through;opacity:.8}
.diff-arrow{color:var(--muted)}
.diff-add{color:var(--ok);font-weight:700;background:var(--ok-bg);
  border-radius:6px;padding:1px 8px}
.wave{width:100%;height:64px;display:block;margin:2px 0 8px}
.wave polygon{fill:var(--measured);opacity:.75}
audio{width:100%;margin:2px 0 4px}
.meter{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;margin-top:8px;
  font-size:14px;font-variant-numeric:tabular-nums}
.meter-label{font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted);
  text-transform:uppercase}
.meter .measured{color:var(--measured)}
.meter-verdict{color:var(--ok);font-weight:700}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{text-align:right}
td.authored{color:var(--authored);font-weight:700}
td.measured{color:var(--measured);font-weight:700}
.chip{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12.5px;
  font-weight:600;border:1px solid var(--line);white-space:nowrap}
.chip-ok{background:var(--ok-bg);color:var(--ok);border-color:transparent}
.chip-na{color:var(--muted);border-style:dashed}
.bigstat{display:flex;align-items:baseline;gap:14px;margin:16px 0 6px}
.bigstat .value{font-size:56px;font-weight:800;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums;line-height:1}
.bigstat .label{color:var(--muted);max-width:22em}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:12px 0}
.advisory{background:var(--warn-bg);border-radius:8px;padding:10px 14px;margin:10px 0;
  font-size:13.5px}
.advisory-tag{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--authored);
  margin-right:10px;text-transform:uppercase}
.why p{margin:.6em 0}
.why b{color:var(--ink)}
.legend-inline{color:var(--muted);font-size:13px}
.legend-inline i{display:inline-block;width:10px;height:10px;border-radius:2px;
  margin:0 5px 0 12px;vertical-align:baseline}
details{margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
details summary{cursor:pointer;font-weight:700;color:var(--muted)}
details .inner{margin-top:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;
  margin:12px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px}
.tile .value{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.tile .label{font-size:12px;color:var(--muted)}
.cmd{background:var(--code-bg);color:var(--code-dim);border-radius:8px;padding:9px 14px;
  font-size:12.5px;margin:10px 0;overflow-x:auto;white-space:nowrap}
.cmd b{color:var(--code-ink);font-weight:500}
.dim{color:var(--muted)}
.small{font-size:13px}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
.foot code{word-break:break-all}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">ugh-prompt-engine / 決定論デモ</div>
  <h1>文字が音になる。<br>音が文字に戻る。</h1>
  <p class="lede">AI に「いい感じの曲にして」とお願いする代わりに、<b>数字で楽譜を書く</b>実験です。
  コンピュータは楽譜どおりに演奏し、できた音を計器が測って、書いた楽譜を言い当てます。
  すべて手元のコードだけで動き、何度やっても同じ結果になります。</p>
  <div class="hero-listen">
    <div class="cap">▶ まず 20 秒だけ聴いてください</div>
    $hero_wave
    $hero_audio
    <div class="sub">この曲は、AI 生成サービスではなく、下の 7 行のテキストから
    乱数なしで合成されました（習作レベルの音色なのは仕様です — 主役は音色ではなく仕組み）。</div>
  </div>
</header>

<section>
  <div class="chap">第 1 章</div>
  <h2>楽譜は、たった 7 行のテキスト</h2>
  <p class="sub">「お願い文」との違いはひとつ。<b>あとから機械が答え合わせできる</b>ことです。
  7 行それぞれが、測定可能な命令になっています。</p>
  <pre>$physical_yaml</pre>
  <div class="score-note">
    <span><b>bpm</b> — テンポ（1 分あたりの拍数）</span>
    <span><b>key</b> — 調。C minor は「ハ短調」＝物悲しい響き</span>
    <span><b>brightness</b> — 音色の明るさ</span>
    <span><b>active_rate / valley_depth</b> — 音の密度と、静かな谷の深さ</span>
  </div>
</section>

<section>
  <div class="chap">第 2 章</div>
  <h2>1 行変えると、音が変わる。<br>そして計器が、それを言い当てる</h2>
  <p class="sub">上の楽譜から <b>1 行だけ</b>書き換えて演奏し直し、できた音を計器に読ませました。
  耳で聴こえる変化を、機械も数字で確認できています。
  <span class="legend-inline"><i style="background:var(--authored)"></i>書いた値
  <i style="background:var(--measured)"></i>音から測った値</span></p>
  $variant_cards
  <p class="small dim">3 枚目の補足: 針は確かに明るい方へ動きますが、この演奏者の音色では
  「bright」の絶対基準（2500 Hz 以上）には届きません。計器は動いた事実と届かない事実を
  両方そのまま報告します — 忖度しないのが、この計器の設計です。</p>
</section>

<section>
  <div class="chap">第 3 章</div>
  <h2>今度は逆再生 — 音だけから、楽譜が戻ってくる</h2>
  <p class="sub">原曲の音声ファイル<b>だけ</b>を計器に渡し、楽譜を復元させました。
  楽譜の 7 項目のうち——</p>
  <div class="bigstat">
    <div class="value">$preserved_count<span style="font-size:.5em;color:var(--muted)">/$field_count</span></div>
    <div class="label">が、音だけから正しく読み戻せた。読めなかった $unread_count 項目は
    「読めない」と自己申告する。</div>
  </div>
  <div class="panel"><div class="tablewrap"><table>
    <tr><th>項目</th><th>書いた値</th><th>音から読めた値</th><th>結果</th></tr>
    $roundtrip_rows
  </table></div></div>
  <p class="small dim">「書く→鳴らす→測る→書き戻す」が一周して、ほぼ同じ楽譜に戻る——
  これがこのプロジェクトの心臓部です（往復保存性と呼んでいます）。</p>
</section>

<section class="why">
  <div class="chap">第 4 章</div>
  <h2>これは、何の実験なのか</h2>
  <p>いまの AI 音楽生成は<b>ガチャ</b>です。同じお願い文でも毎回違う曲が出て、
  意図が通ったかを確かめる方法がありません。作った曲は保存も再現も編集もできない、
  一回きりの偶然です。</p>
  <p>このリポジトリは、その状況に<b>楽譜と計器</b>を持ち込みます。
  数字で書ける楽譜（SVP）と、音を数字に戻す計器（RPE）が揃うと、
  いま聴いた「書く→鳴らす→測る→書き戻す」の一周が閉じます。</p>
  <p>ゴールは、この楽譜を <b>AI 音楽にとっての MIDI</b>（共通言語）にすること。
  そうなれば AI の曲は、ガチャの結果ではなく、保存・再現・編集できる<b>作品</b>になります。
  実際の生成サービス（Suno / MusicGen）相手にも、どのツマミが効くかを同じ計器で
  測定済みです。</p>
</section>

<details>
  <summary>技術者向けの詳細（生成プロンプト・楽譜準拠・意味層・再現手順）</summary>
  <div class="inner">
    <p class="small dim">楽譜: <code>$score_path</code> / 原曲の計器の読み:
    $original_bpm BPM・$original_key</p>

    <div class="eyebrow" style="margin:14px 0 4px">外部生成器向けコンパイル
    （control_profile-aware・tight フィールド先頭昇格）</div>
    <div class="cmd">$$ <b>svprpe compose $score_path</b></div>
    <pre class="prewrap">$prompt_text</pre>
    $advisories

    <div class="eyebrow" style="margin:18px 0 4px">楽譜準拠（score-adherence）</div>
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

    <div class="eyebrow" style="margin:18px 0 4px">意味層の読み（ルールベース推定）</div>
    <div class="panel">
      <p style="margin:.2em 0">core: <b>$por_core</b></p>
      <p style="margin:.2em 0">文脈: $contexts / 楽器感: $instrumentation</p>
      <p class="small dim" style="margin:.6em 0 0">意味層は「演奏の音色」を読む。決定論演奏者は
      正弦波スケッチなので、意図（deep_house）ではなく実際に鳴っている音の性格が返る —
      センサーは意図に忖度しない。$disclaimer</p>
    </div>

    <div class="eyebrow" style="margin:18px 0 4px">音から戻した draft 楽譜（physical 層）</div>
    <pre>$draft_yaml</pre>
  </div>
</details>

<footer class="foot">
  <p><b>決定論の証跡</b> — 原曲 WAV sha256 <code>$original_sha</code>。
  同じリポジトリで <code>python scripts/render_showcase.py</code> を実行すれば、
  同じ WAV・同じ数値・同じページが再生成される。API キー・LLM・ネットワーク不要。</p>
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
    score_path = args.score.resolve()
    output_dir = args.output_dir.resolve()
    payload = run(score_path, output_dir)
    roundtrip = payload["roundtrip"]
    preserved = sum(1 for f in roundtrip["fields"] if f["diagnosis"] == "preserved")
    print(f"roundtrip: {preserved}/{len(roundtrip['fields'])} preserved")
    for variant in payload["variants"]:
        measured = variant["measured"]
        print(
            f"variant {variant['name']}: bpm={measured['bpm']} key={measured['key']} "
            f"centroid={measured['centroid']}"
        )
    print(f"artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
