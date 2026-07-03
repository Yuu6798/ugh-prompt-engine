"""Fable showcase 終章: Suno の曲を計器が楽譜に起こし、MusicGen に弾かせ、採点する。

機種間再現デモ。ユーザー提供の Suno 楽曲から:

1. 計器が楽譜を起こす（物理=measure、意味=ルール推定、コード進行=聴き取り。
   人間の介入ゼロ。TODO 欄は転写バンドルの意味層から機械的に補完する）
2. その楽譜を musicgen backend 向けにコンパイルし、MusicGen small に N 回演奏させる
3. R3 反復ハーネス（run_repetition_batch）でフィールド別の保存率を採点し、
   「楽譜に最も忠実なテイク」を機械選出する

常識どおり完全再現は不可能である。このデモの主張は「不可能」ではなく
「失敗の内訳が数字で出る」こと。採点はすべて計器の読みで、散文はそれに従う。

Usage:
    python scripts/render_showcase_crossgen.py                 # 生成込み full 実行
    python scripts/render_showcase_crossgen.py --skip-generate # 既存テイクを再採点・再描画
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import sys
from pathlib import Path
from string import Template
from typing import Any

import librosa
import numpy as np
import yaml
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_musicgen_takes import (  # noqa: E402
    PROFILE_MEASURED_MODEL_ID,
    PROFILE_MEASURED_MODEL_REVISION,
    perform_takes,
)
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.compose.prompt_renderer import ExternalPromptAdapter  # noqa: E402
from svp_rpe.roundtrip.repetition import (  # noqa: E402
    load_takes_for_repetition,
    run_repetition_batch,
)
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.rpe.models import RPEBundle  # noqa: E402
from svp_rpe.transcribe import draft_score, render_draft_score_yaml  # noqa: E402

SOURCE_PATH = (
    ROOT
    / "examples"
    / "composition"
    / "midnight_signal"
    / "showcase_suno"
    / "source"
    / "bpm_low_1.mp3"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "examples" / "composition" / "midnight_signal" / "showcase_crossgen"
)

REPETITIONS = 5
DURATION_SEC = 12.0
EMBED_SR = 11025
SOURCE_EMBED_OFFSET_SEC = 30.0
SOURCE_EMBED_SEC = 30.0

DARK_BAND_HZ = 1200.0
BRIGHT_BAND_HZ = 2500.0

FIELD_JA = {
    "bpm": "テンポ",
    "key": "調",
    "time_signature": "拍子",
    "brightness": "明るさ",
    "active_rate_target": "音の密度",
    "valley_depth_target": "静けさの深さ",
    "stereo_width": "音の広がり",
    "chord_progression": "コード進行",
}


# ---------------------------------------------------------------------------
# 転写と楽譜のサニタイズ（すべて計器由来）
# ---------------------------------------------------------------------------


def brightness_label(centroid: float) -> str:
    if centroid <= DARK_BAND_HZ:
        return "dark"
    if centroid >= BRIGHT_BAND_HZ:
        return "bright"
    return "neutral"


def transcribe_score(bundle: RPEBundle) -> CompositionScore:
    """draft 楽譜の TODO 欄を転写バンドルの意味層で機械補完し、musicgen へ向ける。

    人間の作曲判断は入れない: core/grv/delta_e はルールベース意味層の出力、
    brightness は centroid のラベル帯、stereo_width のみラベル帯未校正のため
    中立値 "medium" を置く（ページ側で開示する）。structure の役割文は転写では
    得られないため、プロンプト priority から structure を外して drop を明示する。
    """

    score = draft_score(bundle)
    semantic = bundle.semantic
    score.semantic.core = semantic.por_core
    score.semantic.grv.primary = semantic.grv_anchor.primary
    secondary = semantic.grv_anchor.secondary or semantic.cultural_context
    score.semantic.grv.secondary = secondary[0] if secondary else "unclassified"
    score.semantic.delta_e.overall = semantic.delta_e_profile.transition_type
    score.physical.brightness = brightness_label(float(bundle.physical.spectral_centroid))
    score.physical.stereo_width = "medium"
    if score.fixity is not None:
        # TODO 欄を実値で埋めたので fixity 入場試験の整合をとる（TODO=unlocked / 実値=locked）
        score.fixity["brightness"] = "locked"
        score.fixity["stereo_width"] = "locked"
    # 構成の役割文は転写では得られない。TODO をプロンプトへ漏らさず、転写が実測した
    # 情報（セクション名・小節数）だけで埋める。
    for section in score.structure:
        section.role = section.section.lower()
        section.physical = f"{section.bars} bars"
    score.rendering.target_backend = "musicgen"
    return score


# ---------------------------------------------------------------------------
# 音声素材
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _embed_wav_b64(audio_path: Path, *, offset_sec: float = 0.0, max_sec: float) -> str:
    floats, sr = librosa.load(str(audio_path), sr=None, mono=True)
    start = int(offset_sec * sr)
    if start >= len(floats):
        start = 0
    clip = floats[start : start + int(max_sec * sr)]
    resampled = librosa.resample(clip, orig_sr=sr, target_sr=EMBED_SR)
    peak = float(np.max(np.abs(resampled))) or 1.0
    as_int16 = np.round(np.clip(resampled / peak, -1.0, 1.0) * 32767.0 * 0.9).astype(
        np.int16
    )
    buffer = io.BytesIO()
    wavfile.write(buffer, EMBED_SR, as_int16)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _waveform_points(audio_path: Path, *, buckets: int = 480) -> list[float]:
    floats, _sr = librosa.load(str(audio_path), sr=None, mono=True)
    peak = float(np.max(np.abs(floats))) or 1.0
    normalized = np.abs(floats) / peak
    edges = np.linspace(0, len(normalized), buckets + 1, dtype=int)
    return [
        round(float(normalized[a:b].max()) if b > a else 0.0, 4)
        for a, b in zip(edges[:-1], edges[1:])
    ]


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------


def gather(source_path: Path, output_dir: Path, *, skip_generate: bool) -> dict[str, Any]:
    bundle = extract_rpe_from_file(str(source_path))
    score = transcribe_score(bundle)
    score_path = output_dir / "transcribed_score.yaml"
    score_path.write_text(render_draft_score_yaml(score), encoding="utf-8")
    prompt = ExternalPromptAdapter().render(score)

    manifest_path = output_dir / "takes_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    elif skip_generate:
        raise FileNotFoundError(
            f"--skip-generate but no manifest at {manifest_path}; run without the flag first"
        )
    else:
        manifest = perform_takes(
            score_path,
            repetitions=REPETITIONS,
            output_dir=output_dir,
            duration_seconds=DURATION_SEC,
            fixture_id="crossgen",
            model_id=PROFILE_MEASURED_MODEL_ID,
            model_revision=PROFILE_MEASURED_MODEL_REVISION,
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    takes = load_takes_for_repetition(manifest, audio_dir=output_dir)
    report = run_repetition_batch(
        score,
        takes,
        generator=manifest.get("generator", "musicgen-small"),
        score_ref=str(score_path.relative_to(ROOT)),
    )

    take_media = []
    for take_id, audio_path in takes:
        take_media.append(
            {
                "take_id": take_id,
                "waveform": _waveform_points(audio_path),
                "embed_b64": _embed_wav_b64(audio_path, max_sec=DURATION_SEC + 1),
            }
        )

    physical = bundle.physical
    source = {
        "path": str(source_path.relative_to(ROOT)),
        "sha256": _sha256_file(source_path),
        "duration_sec": round(float(physical.duration_sec), 1),
        "measured": {
            "bpm": round(float(physical.bpm), 1) if physical.bpm else None,
            "key": f"{physical.key} {physical.mode}" if physical.key else None,
            "centroid": round(float(physical.spectral_centroid), 0),
        },
        "waveform": _waveform_points(source_path),
        "embed_b64": _embed_wav_b64(
            source_path, offset_sec=SOURCE_EMBED_OFFSET_SEC, max_sec=SOURCE_EMBED_SEC
        ),
    }

    return {
        "source": source,
        "bundle": bundle,
        "score": score,
        "score_yaml": render_draft_score_yaml(score),
        "prompt": prompt,
        "report": report,
        "take_media": take_media,
    }


def as_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    source = {k: v for k, v in data["source"].items() if k not in ("embed_b64", "waveform")}
    return {
        "schema_version": "1.0",
        "provenance": (
            "原曲はユーザーが Suno で生成し 2026-07-03 に提供（コミットしない・sha256 pin）。"
            "楽譜は計器の転写のみで人間の作曲判断ゼロ。"
        ),
        "source": source,
        "prompt": data["prompt"].model_dump(mode="json"),
        "repetition_report": data["report"].model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# HTML 描画
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _waveform_svg(points: list[float], *, width: int = 480, height: int = 46) -> str:
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


def _format_observed(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        # chord_progression の observed は chord dict のリスト
        if not value:
            return "（聴き取れず）"
        if isinstance(value[0], dict) and "root" in value[0]:
            names = [
                f"{c['root']}{'m' if c.get('quality') == 'minor' else ''}" for c in value[:4]
            ]
            return " ".join(names) + (" …" if len(value) > 4 else "")
    text = str(value)
    return text if len(text) <= 24 else text[:22] + "…"


def render_html(data: dict[str, Any]) -> str:
    source = data["source"]
    report = data["report"]
    score = data["score"]

    physical_yaml = yaml.safe_dump(
        score.physical.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    chords = [
        f"{c.root}{'m' if c.quality == 'minor' else ''}"
        for c in (score.events.chord_progression if score.events else [])
    ]
    chord_line = " → ".join(chords[:8]) + (" …" if len(chords) > 8 else "")
    structure_line = " / ".join(
        f"{s.section}({s.bars})" for s in score.structure
    )

    take_cards = []
    by_take = {t.take_id: t for t in report.takes}
    selected = report.selection.selected_take_id
    for media in data["take_media"]:
        take = by_take[media["take_id"]]
        badge = (
            '<span class="chip chip-pick">★ 計器が選んだ最忠実テイク</span>'
            if media["take_id"] == selected
            else ""
        )
        preserved_fields = [f.field for f in take.fields if f.preserved]
        preserved_note = (
            "、".join(FIELD_JA.get(f, f) for f in preserved_fields)
            if preserved_fields
            else "なし"
        )
        take_cards.append(
            '<div class="takerow">'
            f'<div class="takehead"><b>{_esc(media["take_id"])}</b>{badge}'
            f'<span class="dim small">保存 {take.preserved_count} 項目</span></div>'
            f"{_waveform_svg(media['waveform'])}"
            f'<audio controls preload="metadata" '
            f'src="data:audio/wav;base64,{media["embed_b64"]}"></audio>'
            f'<div class="takemeta"><span>楽譜と一致: '
            f'<span class="measured">{_esc(preserved_note)}</span></span></div>'
            "</div>"
        )

    rows = []
    carried, partial, lost = [], [], []
    for summary in report.fields:
        ja = FIELD_JA.get(summary.field, summary.field)
        rate = summary.preserved_rate
        if rate >= 0.6:
            carried.append(ja)
            klass, mark = "ok", "✓"
        elif rate > 0.0:
            partial.append(ja)
            klass, mark = "mid", "△"
        else:
            lost.append(ja)
            klass, mark = "bad", "✗"
        observed = "、".join(
            _format_observed(v) for v in summary.observed_values[:5]
        )
        expected = next(
            (
                f.expected_value
                for t in report.takes
                for f in t.fields
                if f.field == summary.field
            ),
            "",
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(ja)} <code class='dim'>{_esc(summary.field)}</code></td>"
            f'<td class="authored">{_esc(_format_observed(expected))}</td>'
            f'<td class="measured small">{_esc(observed)}</td>'
            f'<td class="num"><span class="rate rate-{klass}">{mark} '
            f"{summary.preserved_count}/{summary.n}</span></td>"
            "</tr>"
        )

    def bucket(items: list[str]) -> str:
        return "、".join(items) if items else "なし"

    # 明るさ ✗ のうち「中立帯に着地したが draft が TODO を返すため一致扱いにならない」
    # 件数（計器側の目盛り事情）をデータから数える。
    brightness_summary = next((f for f in report.fields if f.field == "brightness"), None)
    neutral_landings = (
        sum(1 for v in brightness_summary.observed_values if str(v).startswith("TODO"))
        if brightness_summary is not None
        else 0
    )
    sensor_note = (
        f"「明るさ」は {neutral_landings}/{report.n_takes} テイクが目標と同じ中立帯に"
        "着地していたが、draft 側の目盛りが中立帯を TODO と表記するため一致に数えられない。"
        if neutral_landings
        else ""
    )

    template = Template(_PAGE_TEMPLATE)
    return template.substitute(
        source_bpm=f"{source['measured']['bpm']:g}",
        source_key=_esc(source["measured"]["key"]),
        source_centroid=f"{source['measured']['centroid']:g}",
        source_duration=f"{source['duration_sec']:g}",
        source_sha=_esc(source["sha256"][:12]),
        source_wave=_waveform_svg(source["waveform"]),
        source_audio_b64=source["embed_b64"],
        physical_yaml=_esc(physical_yaml),
        chord_line=_esc(chord_line),
        structure_line=_esc(structure_line),
        prompt_text=_esc(data["prompt"].text),
        take_cards="".join(take_cards),
        score_rows="".join(rows),
        n_takes=str(report.n_takes),
        selected_take=_esc(selected),
        carried=_esc(bucket(carried)),
        partial=_esc(bucket(partial)),
        lost=_esc(bucket(lost)),
        sensor_note=_esc(sensor_note),
        model_id=_esc(PROFILE_MEASURED_MODEL_ID),
    )


_PAGE_TEMPLATE = """<title>別の機種で、同じ曲は弾けるか — 機種間再現の採点</title>
<style>
:root{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --bad:#A33D3D; --bad-bg:rgba(163,61,61,.10);
  --code-bg:#10182A; --code-ink:#D8DFEF;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --bad:#D07070; --bad-bg:rgba(208,112,112,.14);
}}
:root[data-theme="dark"]{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --bad:#D07070; --bad-bg:rgba(208,112,112,.14);
}
:root[data-theme="light"]{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --bad:#A33D3D; --bad-bg:rgba(163,61,61,.10);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;
  font-family:"Hiragino Sans","Yu Gothic UI","Noto Sans JP","Segoe UI",system-ui,sans-serif;
  font-size:15.5px;line-height:1.85}
.wrap{max-width:760px;margin:0 auto;padding:64px 24px 72px}
code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:.88em}
.eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:600}
h1{font-size:clamp(28px,5.5vw,40px);line-height:1.28;margin:.4em 0 .3em;
  text-wrap:balance;letter-spacing:-.015em}
.lede{color:var(--muted);font-size:17px;max-width:37em;margin:0}
section{margin-top:60px}
.chap{font-size:12px;font-weight:700;letter-spacing:.14em;color:var(--measured);
  text-transform:uppercase}
h2{font-size:23px;line-height:1.4;margin:.3em 0 .4em;letter-spacing:-.01em;
  text-wrap:balance}
.sub{color:var(--muted);margin:0 0 16px;max-width:40em}
pre{background:var(--code-bg);color:var(--code-ink);border-radius:10px;padding:14px 16px;
  font-size:12.5px;line-height:1.7;overflow-x:auto;margin:10px 0;
  white-space:pre-wrap;word-break:break-word;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:14px 0}
.card-title{font-weight:700;font-size:16px;margin-bottom:4px}
.takerow{border-top:1px dashed var(--line);padding-top:10px;margin-top:10px}
.takerow:first-of-type{border-top:none;margin-top:4px;padding-top:0}
.takehead{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;margin-bottom:4px}
.wave{width:100%;height:46px;display:block;margin:2px 0 6px}
.wave polygon{fill:var(--measured);opacity:.7}
audio{width:100%;margin:0 0 4px}
.takemeta{font-size:13px;color:var(--muted)}
.takemeta .measured{color:var(--measured);font-weight:600}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 4px}
.fact{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:5px 12px;font-size:13px}
.fact b{font-variant-numeric:tabular-nums}
.fact .dim{margin-right:.5em}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{text-align:right;white-space:nowrap}
td.authored{color:var(--authored);font-weight:700}
td.measured{color:var(--measured)}
.rate{font-weight:700;border-radius:999px;padding:1px 10px;white-space:nowrap}
.rate-ok{background:var(--ok-bg);color:var(--ok)}
.rate-mid{background:var(--warn-bg);color:var(--authored)}
.rate-bad{background:var(--bad-bg);color:var(--bad)}
.chip{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12px;
  font-weight:700;border:1px solid var(--line);white-space:nowrap}
.chip-pick{background:var(--ok-bg);color:var(--ok);border-color:transparent}
.buckets{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;
  margin:14px 0}
.bucket{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;font-size:13.5px}
.bucket b{display:block;margin-bottom:2px}
.bucket.ok b{color:var(--ok)} .bucket.mid b{color:var(--authored)}
.bucket.bad b{color:var(--bad)}
.why p{margin:.6em 0}
.small{font-size:13px}
.dim{color:var(--muted)}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">ugh-prompt-engine / 機種間再現（終章）</div>
  <h1>別の機種で、<br>同じ曲は弾けるか</h1>
  <p class="lede">Suno で作られた曲を、<b>計器が楽譜に起こし</b>、その楽譜を
  <b>別の機種（MusicGen）</b>に弾かせて、どこまで運べたかを採点しました。
  常識的には再現は不可能に近い——そのとおりです。このページの主張は
  「できた」ではなく、<b>どこができて、どこができないかが数字で出る</b>こと。</p>
</header>

<section>
  <div class="chap">第 1 章</div>
  <h2>原曲 — Suno が弾いた「正解」</h2>
  <div class="card">
    $source_wave
    <audio controls preload="metadata"
      src="data:audio/wav;base64,$source_audio_b64"></audio>
    <div class="facts">
      <div class="fact"><span class="dim">テンポ</span> <b>$source_bpm BPM</b></div>
      <div class="fact"><span class="dim">調</span> <b>$source_key</b></div>
      <div class="fact"><span class="dim">明るさ</span> <b>$source_centroid Hz</b></div>
      <div class="fact"><span class="dim">全長</span> <b>$source_duration 秒</b></div>
    </div>
    <p class="small dim">ユーザー生成・提供（sha256 $source_sha…）。試聴は 30 秒抜粋。</p>
  </div>
</section>

<section>
  <div class="chap">第 2 章</div>
  <h2>計器が楽譜を起こす — 人間の介入ゼロ</h2>
  <p class="sub">音声だけから、物理層は計測で、意味層はルール推定で、
  楽譜を自動で書き起こしました。コード進行まで聴き取っています。</p>
  <pre>$physical_yaml</pre>
  <div class="card">
    <div class="card-title">聴き取ったコード進行（冒頭）</div>
    <p style="margin:.2em 0"><code>$chord_line</code></p>
    <div class="card-title" style="margin-top:10px">聴き取った曲の構成</div>
    <p style="margin:.2em 0" class="small">$structure_line</p>
  </div>
  <p class="small dim">正直な注記: stereo_width はラベル帯未校正のため中立値。
  構成の役割文は転写では得られないため、実測できたセクション名と小節数のみを
  プロンプトへ渡す。この楽譜を MusicGen 向けにコンパイルしたプロンプトが下記 —
  機種の取説により brightness（tight）が文頭に昇格しています。</p>
  <pre>$prompt_text</pre>
</section>

<section>
  <div class="chap">第 3 章</div>
  <h2>MusicGen が $n_takes 回弾く</h2>
  <p class="sub">同じ楽譜から $n_takes テイク。ガチャなので毎回違います。
  各テイクの下に「楽譜と一致した項目」を計器の読みで添えました。
  <b>★ は計器が機械的に選んだ、楽譜に最も忠実なテイク</b>です
  （rejection sampling — ガチャを引き直して良い個体を拾う、の自動化）。</p>
  <div class="card">
    $take_cards
  </div>
</section>

<section>
  <div class="chap">第 4 章</div>
  <h2>採点表 — どこが運べて、どこが落ちたか</h2>
  <div class="card"><div class="tablewrap"><table>
    <tr><th>項目</th><th>楽譜（原曲から）</th><th>$n_takes テイクの読み</th><th>保存率</th></tr>
    $score_rows
  </table></div></div>
  <div class="buckets">
    <div class="bucket ok"><b>✓ 運べた</b>$carried</div>
    <div class="bucket mid"><b>△ ときどき運べた</b>$partial</div>
    <div class="bucket bad"><b>✗ 運べなかった</b>$lost</div>
  </div>
  <p class="small dim">脚注: ✗ の一部は生成器でなく計器側の目盛り事情を含む。$sensor_note
  「音の広がり」はセンサー未校正（第一幕から一貫して自己申告）。つまり ✗ には
  「機種が運べない」と「計器がまだ読めない」の両方が混ざり、その区別まで含めて
  次の改善対象の座標になる。</p>
</section>

<section class="why">
  <div class="chap">結論</div>
  <h2>「不可能」が、地図になった</h2>
  <p>Suno の曲を MusicGen で完全再現することは、予想どおりできません。
  しかしこの 1 ページで、<b>何が楽譜で機種を越えられて、何が越えられないか</b>が
  フィールド単位の数字になりました。落ちた項目は「無理」ではなく
  「楽譜と機種の次の改善対象」という座標を持ちます。</p>
  <p>MIDI が「どのシンセでも同じ曲」を可能にしたように、この楽譜と計器は
  「どの生成 AI でも<b>測れる程度に</b>同じ曲」への道具です。いまの到達点と
  限界を、隠さずこの採点表が示しています。</p>
</section>

<footer class="foot">
  <p><b>再現情報</b> — 原曲はユーザー提供（コミットせず sha256 pin）。転写・コンパイル・
  採点は決定論、生成（$model_id）のみ非決定論で takes manifest が一次記録。
  <code>python scripts/render_showcase_crossgen.py --skip-generate</code> で
  採点と描画を決定論に再実行できる。</p>
</footer>
</div>
"""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(source_path: Path, output_dir: Path, *, skip_generate: bool) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore = output_dir / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    data = gather(source_path, output_dir, skip_generate=skip_generate)
    payload = as_json_payload(data)
    (output_dir / "showcase_crossgen.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "showcase_crossgen.html").write_text(
        render_html(data), encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()
    payload = run(args.source, args.output_dir, skip_generate=args.skip_generate)
    report = payload["repetition_report"]
    for field in report["fields"]:
        print(
            f"{field['field']}: {field['preserved_count']}/{field['n']} "
            f"({field['preserved_rate']:.2f})"
        )
    print(f"selected take: {report['selection']['selected_take_id']}")
    print(f"artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
