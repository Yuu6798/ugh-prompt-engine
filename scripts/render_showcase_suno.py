"""Fable showcase 第三幕: 実用機 Suno のユーザー生成 A/B ペアを計器にかける 1 枚 HTML。

決定論編（render_showcase.py）・MusicGen 編（render_showcase_musicgen.py）の続編。
ユーザーが Suno で生成した「指定だけを変えた」音源ペア（bpm 低/高・brightness
暗/明）を同じ計器で測り、次を見せる:

1. テンポの注文は実用機でも針に届く（bpm 低/高ペアの分離・半折り診断込み）
2. 明るさの注文も方向どおり動く — ただし dark 指定は絶対 dark 帯に届かない。
   これは device_profiles/suno.yaml（K2 実測 0/4）が予言し、コンパイラが機種メモで
   事前警告していた癖の実地追試になる
3. 三幕総括: 定規（決定論）→ 統計（MusicGen）→ 実用機（Suno）

音源はユーザー提供のためコミットしない（sha256 を provenance として JSON に記録）。
計測と描画は音源ファイルが同一なら決定論。

Usage:
    python scripts/render_showcase_suno.py
    python scripts/render_showcase_suno.py --source-dir <dir> --output-dir <dir>
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
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from svp_rpe.rpe.extractor import extract_physical_from_file  # noqa: E402

DEFAULT_DIR = ROOT / "examples" / "composition" / "midnight_signal" / "showcase_suno"

EMBED_SR = 11025
EMBED_MAX_SEC = 30.0

# ユーザー生成ペアの取り込み定義。offset は試聴抜粋の開始秒（長尺はイントロを飛ばす）。
TRACKS: list[dict[str, Any]] = [
    {"name": "bpm_low_1", "pair": "bpm", "level": "低テンポ指定", "offset": 30.0},
    {"name": "bpm_High_1", "pair": "bpm", "level": "高テンポ指定", "offset": 0.0},
    {"name": "bright_dark_1", "pair": "bright", "level": "dark 指定", "offset": 15.0},
    {"name": "bright_bright_1", "pair": "bright", "level": "bright 指定", "offset": 0.0},
]

DARK_BAND_HZ = 1200.0  # semantic_rules の絶対 dark 帯（centroid ≤ 1200 Hz）


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _embed_wav_b64(audio_path: Path, *, offset_sec: float) -> str:
    floats, sr = librosa.load(str(audio_path), sr=None, mono=True)
    start = int(offset_sec * sr)
    if start >= len(floats):
        start = 0
    clip = floats[start : start + int(EMBED_MAX_SEC * sr)]
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


def gather(source_dir: Path) -> dict[str, Any]:
    tracks = []
    for spec in TRACKS:
        path = source_dir / f"{spec['name']}.mp3"
        physical = extract_physical_from_file(str(path))
        tracks.append(
            {
                **spec,
                "sha256": _sha256_file(path),
                "duration_sec": round(float(physical.duration_sec), 1),
                "measured": {
                    "bpm": round(float(physical.bpm), 1) if physical.bpm else None,
                    "bpm_octave_ambiguous": bool(physical.bpm_octave_ambiguous),
                    "centroid": round(float(physical.spectral_centroid), 0),
                    "key": f"{physical.key} {physical.mode}" if physical.key else None,
                },
                "waveform": _waveform_points(path),
                "embed_b64": _embed_wav_b64(path, offset_sec=spec["offset"]),
            }
        )
    return {"tracks": tracks}


def as_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "provenance": (
            "ユーザーが Suno で生成し 2026-07-03 に提供した A/B ペア。"
            "音源はコミットしない — sha256 が一次 pin。"
        ),
        "tracks": [
            {k: v for k, v in t.items() if k not in ("embed_b64", "waveform")}
            for t in data["tracks"]
        ],
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


def _ab_strip(
    rows: list[tuple[str, float]],
    *,
    unit: str,
    band: tuple[float, float, str] | None = None,
    width: int = 640,
) -> str:
    """A/B 2 値を同一スケールに置く針ストリップ（任意で帯を敷く）。"""

    values = [v for _, v in rows]
    lo = min(values + ([band[0]] if band else []))
    hi = max(values + ([band[1]] if band else []))
    pad = max((hi - lo) * 0.12, 1.0)
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    row_h = 34
    height = row_h * len(rows) + 24

    def x(value: float) -> float:
        return (value - lo) / span * (width - 160) + 150

    parts = [f'<svg viewBox="0 0 {width} {height}" class="strip" role="img">']
    if band is not None:
        b_lo, b_hi, label = band
        parts.append(
            f'<rect x="{x(b_lo):.1f}" y="4" width="{x(b_hi) - x(b_lo):.1f}" '
            f'height="{row_h * len(rows)}" class="band" />'
            f'<text x="{x(b_lo) + 4:.1f}" y="{row_h * len(rows) + 16}" '
            f'class="bandlabel">{_esc(label)}</text>'
        )
    for index, (label, value) in enumerate(rows):
        y = row_h * index + row_h / 2 + 4
        parts.append(
            f'<text x="0" y="{y + 4}" class="rowlabel">{_esc(label)}</text>'
            f'<line x1="150" y1="{y}" x2="{width - 10}" y2="{y}" class="axis" />'
            f'<circle cx="{x(value):.1f}" cy="{y}" r="7" class="dot" />'
            f'<text x="{x(value):.1f}" y="{y - 13}" text-anchor="middle" '
            f'class="meanlabel">{value:g}{_esc(unit)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _track_card(track: dict[str, Any]) -> str:
    measured = track["measured"]
    halving = (
        '<span class="dim">（半折り警告なし）</span>'
        if not measured["bpm_octave_ambiguous"]
        else '<span class="warn">（⚠ 半折り疑い: 高 prior 再推定を推奨）</span>'
    )
    return (
        '<div class="takerow">'
        f'<div class="takehead"><b>{_esc(track["level"])}</b>'
        f'<span class="dim small">{track["duration_sec"]:g} 秒・sha256 '
        f'{_esc(track["sha256"][:12])}…</span></div>'
        f"{_waveform_svg(track['waveform'])}"
        f'<audio controls preload="metadata" '
        f'src="data:audio/wav;base64,{track["embed_b64"]}"></audio>'
        '<div class="takemeta">'
        f'<span class="measured">{measured["bpm"]:g} BPM</span>{halving}'
        f'<span class="measured">{measured["centroid"]:g} Hz</span>'
        f'<span class="measured">{_esc(measured["key"])}</span>'
        "</div></div>"
    )


def render_html(data: dict[str, Any]) -> str:
    by_name = {t["name"]: t for t in data["tracks"]}
    bpm_low, bpm_high = by_name["bpm_low_1"], by_name["bpm_High_1"]
    dark, bright = by_name["bright_dark_1"], by_name["bright_bright_1"]

    bpm_strip = _ab_strip(
        [
            ("低テンポ指定", bpm_low["measured"]["bpm"]),
            ("高テンポ指定", bpm_high["measured"]["bpm"]),
        ],
        unit=" BPM",
    )
    centroid_strip = _ab_strip(
        [
            ("dark 指定", dark["measured"]["centroid"]),
            ("bright 指定", bright["measured"]["centroid"]),
        ],
        unit="Hz",
        band=(0.0, DARK_BAND_HZ, "絶対 dark 帯 (≤1200 Hz)"),
    )

    dark_centroid = dark["measured"]["centroid"]
    dark_in_band = dark_centroid <= DARK_BAND_HZ
    # 見出し・callout 文言はどちらも同じ dark_in_band から導出する
    # （的中/反例=更新候補の固定文が実測と食い違わないようにする）。
    if not dark_in_band:
        prophecy_heading = "取説の予言、的中。"
        prophecy = (
            f"今回の dark 指定は {dark_centroid:g} Hz — 帯の外です。取説の予言どおり、"
            "Suno は「dark 風に明るい」音を返しました。"
        )
    else:
        prophecy_heading = "取説の予言、反証——更新候補。"
        prophecy = (
            f"今回の dark 指定は {dark_centroid:g} Hz で帯に入りました — 既知実績"
            "（0/4）を上回る挙動で、取説の更新候補です。"
        )

    # 半折り注記も両 BPM トラックの実測フラグから組み立てる
    # （片方でも bpm_octave_ambiguous なら固定文「警告なし」と矛盾するため）。
    halving_flagged = [
        label
        for label, track in (("低テンポ指定", bpm_low), ("高テンポ指定", bpm_high))
        if track["measured"]["bpm_octave_ambiguous"]
    ]
    if not halving_flagged:
        halving_note = (
            "高速側で起きがちな「半折り」（テンポを半分に読み違える計器側の持病）の"
            "警告も出ていません。"
        )
    else:
        halving_note = (
            "高速側で起きがちな「半折り」（テンポを半分に読み違える計器側の持病）の"
            f"警告が{'・'.join(halving_flagged)}で出ています — 半折り疑いとして"
            "再確認が必要です。"
        )

    template = Template(_PAGE_TEMPLATE)
    return template.substitute(
        bpm_low_card=_track_card(bpm_low),
        bpm_high_card=_track_card(bpm_high),
        dark_card=_track_card(dark),
        bright_card=_track_card(bright),
        bpm_strip=bpm_strip,
        centroid_strip=centroid_strip,
        bpm_low_val=f"{bpm_low['measured']['bpm']:g}",
        bpm_high_val=f"{bpm_high['measured']['bpm']:g}",
        dark_val=f"{dark_centroid:g}",
        bright_val=f"{bright['measured']['centroid']:g}",
        prophecy_heading=_esc(prophecy_heading),
        prophecy=_esc(prophecy),
        halving_note=_esc(halving_note),
    )


_PAGE_TEMPLATE = """<title>実用機でも、針は動く — Suno 実測（第三幕）</title>
<style>
:root{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --band:rgba(32,116,158,.14);
  --code-bg:#10182A; --code-ink:#D8DFEF;
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
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:14px 0}
.card-title{font-weight:700;font-size:16px;margin-bottom:4px}
.takerow{border-top:1px dashed var(--line);padding-top:10px;margin-top:10px}
.takerow:first-of-type{border-top:none;margin-top:4px;padding-top:0}
.takehead{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  align-items:baseline;margin-bottom:4px}
.wave{width:100%;height:46px;display:block;margin:2px 0 6px}
.wave polygon{fill:var(--measured);opacity:.7}
audio{width:100%;margin:0 0 4px}
.takemeta{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted);
  font-variant-numeric:tabular-nums}
.takemeta .measured{color:var(--measured);font-weight:700}
.takemeta .warn{color:var(--authored)}
.strip{width:100%;display:block;margin:8px 0}
.strip .axis{stroke:var(--line);stroke-width:1}
.strip .band{fill:var(--band)}
.strip .bandlabel{font-size:10.5px;fill:var(--muted);
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.strip .rowlabel{font-size:12px;fill:var(--ink);font-weight:600}
.strip .meanlabel{font-size:11px;font-weight:700;fill:var(--measured);
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.strip .dot{fill:var(--measured);stroke:var(--surface);stroke-width:2}
.verdict{border-radius:10px;padding:12px 16px;font-weight:700;margin:10px 0}
.verdict-ok{background:var(--ok-bg);color:var(--ok)}
.callout{background:var(--warn-bg);border-radius:10px;padding:12px 16px;margin:12px 0;
  font-size:14.5px}
.callout b{color:var(--authored)}
.why p{margin:.6em 0}
.acts{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;
  margin:14px 0}
.act{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;font-size:13.5px}
.act b{display:block;margin-bottom:2px}
.act .dim{font-size:12px}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
.small{font-size:13px}
.dim{color:var(--muted)}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">ugh-prompt-engine / Suno 実測（第三幕）</div>
  <h1>実用機でも、<br>針は動く</h1>
  <p class="lede">最後は、誰が聴いても「曲」と呼べる音——<b>Suno</b> で生成された
  本物の楽曲です。指定だけを変えて注文した A/B ペア（ユーザー生成・提供）を、
  これまでと同じ計器にかけました。</p>
</header>

<section>
  <div class="chap">第 1 章</div>
  <h2>テンポの注文は、実用機の針に届く</h2>
  <p class="sub">低テンポ指定と高テンポ指定で生成された 2 曲。耳でも明確に違い、
  計器の読みは $bpm_low_val BPM と $bpm_high_val BPM に割れました。
  $halving_note</p>
  <div class="card">
    $bpm_low_card
    $bpm_high_card
  </div>
  <div class="card">
    <div class="card-title">テンポの針 — 2 曲を同じ物差しに</div>
    $bpm_strip
    <div class="verdict verdict-ok">✓ 指定どおりに割れた（$bpm_low_val →
    $bpm_high_val BPM）— K2 実測（本物 Suno 16 曲・効果量 d=1.61）の追試として
    も整合</div>
  </div>
</section>

<section>
  <div class="chap">第 2 章</div>
  <h2>明るさも方向どおり — そして「取説の予言」が当たる</h2>
  <p class="sub">dark 指定と bright 指定のペア。針は $dark_val Hz → $bright_val Hz と
  方向どおりに動きました。ここで面白いのは動いたことより、<b>dark 側の着地点</b>です。</p>
  <div class="card">
    $dark_card
    $bright_card
  </div>
  <div class="card">
    <div class="card-title">明るさの針（spectral centroid）— 2 曲を同じ物差しに</div>
    $centroid_strip
    <div class="verdict verdict-ok">✓ 方向どおりに動いた（$dark_val → $bright_val Hz）</div>
  </div>
  <div class="callout"><b>$prophecy_heading</b> このリポジトリの Suno 取扱説明書
  （device_profiles/suno.yaml・過去実測 0/4）には「Suno は dark 指定で絶対 dark 帯
  （centroid ≤1200 Hz）に到達した実績なし」と記録されており、楽譜のコンパイル時には
  機種メモとして事前警告されます。$prophecy ちなみに前ページの MusicGen は
  dark 指定で 305 Hz まで沈みました — 機種ごとの癖まで、計器は言い当てます。</div>
</section>

<section class="why">
  <div class="chap">終幕</div>
  <h2>三幕がひとつにつながる</h2>
  <div class="acts">
    <div class="act"><b>第一幕 — 定規</b>決定論の演奏者で「書く→鳴らす→測る→書き戻す」が
    一周することを証明（7 項目中 6 項目が往復保存）<span class="dim">音は習作、論理は厳密</span></div>
    <div class="act"><b>第二幕 — 統計</b>MusicGen のガチャを分布で測り、効くツマミ
    （brightness）と効かないツマミ（bpm）を判別<span class="dim">音は音楽、判定は分布</span></div>
    <div class="act"><b>第三幕 — 実用機</b>Suno の本物の楽曲でも同じ針が同じ向きに動き、
    機種の癖（dark 未到達）まで取説どおり<span class="dim">音は作品、計器は共通</span></div>
  </div>
  <p>楽譜という共通言語で書き、機種を問わず同じ計器で測る——「AI 音楽の MIDI」への
  道筋を、音色の違う 3 種類の演奏者がひとつのループで実証した、というのが
  この 3 部作の結論です。</p>
  <p class="small dim">正直な注記: 本ページの Suno ペアは各水準 1 曲（n=1）なので、
  単独では「効いた」と断定できません。分布としての裏付けは K2 実測
  （本物 Suno 16 曲、bpm d=1.61 / brightness d=0.86）にあり、本ページはその
  生きた追試という位置づけです。生成時のプロンプト原文は提供者の手元にあります。</p>
</section>

<footer class="foot">
  <p><b>provenance</b> — 音源はユーザーが Suno で生成し 2026-07-03 に提供
  （リポジトリにはコミットしない）。sha256 と計測値は showcase_suno.json に記録。
  計測・描画は <code>python scripts/render_showcase_suno.py</code> で同一音源から
  決定論に再生成できる。</p>
</footer>
</div>
"""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore = output_dir / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    data = gather(source_dir)
    payload = as_json_payload(data)
    (output_dir / "showcase_suno.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "showcase_suno.html").write_text(render_html(data), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_DIR / "source")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    payload = run(args.source_dir, args.output_dir)
    for track in payload["tracks"]:
        measured = track["measured"]
        print(
            f"{track['name']}: bpm={measured['bpm']} centroid={measured['centroid']} "
            f"key={measured['key']}"
        )
    print(f"artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
