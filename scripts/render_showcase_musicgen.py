"""Fable showcase 第二幕: 本物の生成器（MusicGen）をガチャごと計測する 1 枚 HTML を焼く。

決定論ショーケース（render_showcase.py）の続編。自前の決定論演奏者ではなく
実際の音楽生成モデル（facebook/musicgen-small・ローカル推論）に同じ楽譜を
演奏させ、次の 3 章を実測で見せる:

1. 同じ楽譜から 3 回生成 → 毎回違う曲（ガチャ＝再生成ノイズの可視化）
2. 楽譜 1 行変更 brightness dark→bright ×3 → 分布ごと動く（効くツマミ）
3. 楽譜 1 行変更 bpm 128→80 ×3 → 動いたかはばらつき次第（効かないツマミも
   計器なら分かる）

「効いた/効かない」の判定文はハードコードせず、テイク分布の重なりから
機械的に導出する（計器の読みに散文を従わせる）。生成は非決定論（DD-A:
CI では実行しない）だが、生成済み takes manifest があれば --skip-generate で
計測と描画だけを決定論に再実行できる。

Usage:
    python scripts/render_showcase_musicgen.py                # 生成から full 実行
    python scripts/render_showcase_musicgen.py --skip-generate  # 既存テイクを再描画
"""
from __future__ import annotations

import argparse
import base64
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
from svp_rpe.compose import ExternalPromptAdapter, load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.perform import sha256_bytes  # noqa: E402
from svp_rpe.rpe.extractor import extract_physical_from_file  # noqa: E402

SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"
DEFAULT_OUTPUT_DIR = (
    ROOT / "examples" / "composition" / "midnight_signal" / "showcase_musicgen"
)

REPETITIONS = 3
DURATION_SEC = 12.0
EMBED_SR = 11025

CONFIGS: list[dict[str, Any]] = [
    {
        "name": "base",
        "title": "原譜のまま",
        "diff": None,
    },
    {
        "name": "bright",
        "title": "brightness: dark → bright",
        "diff": ("brightness: dark", "brightness: bright"),
    },
    {
        "name": "slow",
        "title": "bpm: 128 → 80",
        "diff": ("bpm: 128", "bpm: 80"),
    },
]


# ---------------------------------------------------------------------------
# スコア変奏と生成
# ---------------------------------------------------------------------------


def build_score(name: str) -> CompositionScore:
    """基準スコアを musicgen backend に向け、config ごとの 1 行変更を適用する。"""

    score = load_composition_score(str(SCORE_PATH))
    score.rendering.target_backend = "musicgen"
    if name == "bright":
        score.physical.brightness = "bright"
    elif name == "slow":
        score.physical.bpm = 80
    return score


def ensure_takes(config: dict[str, Any], output_dir: Path, *, skip_generate: bool) -> dict:
    """config のテイクを生成（--skip-generate 時のみ既存 manifest を再利用）して manifest を返す。"""

    config_dir = output_dir / config["name"]
    manifest_path = config_dir / "takes_manifest.json"
    if skip_generate:
        # --skip-generate 時のみ既存 manifest を再利用する（決定論な再採点・再描画用）。
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"--skip-generate but no manifest at {manifest_path}; run without the flag first"
            )
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    # フル実行は既存 manifest の有無に関わらず takes を再生成する
    # （provenance 比較ゲートは実装しない: デモスクリプトには過剰）。
    score = build_score(config["name"])
    score_path = config_dir / "score.yaml"
    config_dir.mkdir(parents=True, exist_ok=True)
    score_path.write_text(
        yaml.safe_dump(score.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    manifest = perform_takes(
        score_path,
        repetitions=REPETITIONS,
        output_dir=config_dir,
        duration_seconds=DURATION_SEC,
        fixture_id=f"showcase_{config['name']}",
        model_id=PROFILE_MEASURED_MODEL_ID,
        model_revision=PROFILE_MEASURED_MODEL_REVISION,
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# 計測と試聴素材
# ---------------------------------------------------------------------------


def _embed_wav_b64(audio_path: Path) -> str:
    """テイク全長を低レート int16 WAV にして base64 で返す（float WAV は埋め込まない）。"""

    floats, sr = librosa.load(str(audio_path), sr=None, mono=True)
    resampled = librosa.resample(floats, orig_sr=sr, target_sr=EMBED_SR)
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


def measure_takes(manifest: dict, config_dir: Path) -> list[dict[str, Any]]:
    takes = []
    for sample in manifest["samples"]:
        audio_path = config_dir / sample["audio_path"]
        computed_sha256 = sha256_bytes(audio_path.read_bytes())
        manifest_sha256 = str(sample["audio_sha256"])
        if computed_sha256 != manifest_sha256:
            raise ValueError(
                f"sample {sample['sample_id']!r}: manifest audio_sha256 does not match "
                f"the audio file at {audio_path} "
                f"(manifest={manifest_sha256}, computed={computed_sha256})"
            )
        physical = extract_physical_from_file(str(audio_path))
        takes.append(
            {
                "sample_id": sample["sample_id"],
                "seed": sample["seed"],
                "audio_sha256": computed_sha256,
                "measured": {
                    "bpm": round(float(physical.bpm), 1) if physical.bpm else None,
                    "centroid": round(float(physical.spectral_centroid), 0),
                    "key": (
                        f"{physical.key} {physical.mode}" if physical.key else None
                    ),
                },
                "waveform": _waveform_points(audio_path),
                "embed_b64": _embed_wav_b64(audio_path),
            }
        )
    return takes


def separation(
    base_values: list[float],
    variant_values: list[float],
    *,
    expected_direction: str,
) -> dict[str, Any]:
    """2 群のテイク値の分離を記述する（判定文の材料。verdict ではない）。

    範囲非重複だけでなく期待方向も判定に含める（K 系列の grip=方向込み効果量の
    規律に合わせる）。逆方向に非重複な場合は ``separated=False`` かつ
    ``inverted=True`` とし、呼び出し側で「分離はあるが期待と逆方向」と
    正直に区別表記できるようにする。

    Args:
        expected_direction: variant 群が base 群に対して期待される方向。
            ``"higher"`` — variant の平均が base より高いことを期待
            （例: brightness ノブ）。
            ``"lower"`` — variant の平均が base より低いことを期待
            （例: bpm 80 指定 = slow）。
    """
    if expected_direction not in ("higher", "lower"):
        raise ValueError(
            f"expected_direction must be 'higher' or 'lower', got {expected_direction!r}"
        )

    lo_a, hi_a = min(base_values), max(base_values)
    lo_b, hi_b = min(variant_values), max(variant_values)
    mean_a = sum(base_values) / len(base_values)
    mean_b = sum(variant_values) / len(variant_values)
    overlap = not (hi_a < lo_b or hi_b < lo_a)
    non_overlap = not overlap
    matches_direction = (mean_b > mean_a) if expected_direction == "higher" else (mean_b < mean_a)
    return {
        "mean_base": round(mean_a, 1),
        "mean_variant": round(mean_b, 1),
        "range_base": [lo_a, hi_a],
        "range_variant": [lo_b, hi_b],
        "shift": round(mean_b - mean_a, 1),
        "separated": non_overlap and matches_direction,
        "inverted": non_overlap and not matches_direction,
    }


def gather(output_dir: Path, *, skip_generate: bool) -> dict[str, Any]:
    score = build_score("base")
    prompt = ExternalPromptAdapter().render(score)

    configs = []
    for spec in CONFIGS:
        manifest = ensure_takes(spec, output_dir, skip_generate=skip_generate)
        takes = measure_takes(manifest, output_dir / spec["name"])
        configs.append({**spec, "prompt": manifest["prompt"], "takes": takes})

    by_name = {c["name"]: c for c in configs}

    def values(name: str, field: str) -> list[float]:
        return [t["measured"][field] for t in by_name[name]["takes"]]

    stats = {
        "brightness": separation(
            values("base", "centroid"),
            values("bright", "centroid"),
            expected_direction="higher",
        ),
        "bpm": separation(
            values("base", "bpm"),
            values("slow", "bpm"),
            expected_direction="lower",
        ),
        "noise": {
            "bpm_range": [min(values("base", "bpm")), max(values("base", "bpm"))],
            "centroid_range": [
                min(values("base", "centroid")),
                max(values("base", "centroid")),
            ],
        },
    }
    return {
        "prompt": prompt,
        "configs": configs,
        "stats": stats,
        "model_id": PROFILE_MEASURED_MODEL_ID,
        "model_revision": PROFILE_MEASURED_MODEL_REVISION,
    }


def as_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "model_id": data["model_id"],
        "model_revision": data["model_revision"],
        "prompt": data["prompt"].model_dump(mode="json"),
        "configs": [
            {
                "name": c["name"],
                "title": c["title"],
                "prompt": c["prompt"],
                "takes": [
                    {k: v for k, v in t.items() if k not in ("embed_b64", "waveform")}
                    for t in c["takes"]
                ],
            }
            for c in data["configs"]
        ],
        "stats": data["stats"],
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


def _dot_strip(
    rows: list[tuple[str, list[float], str]], *, unit: str, width: int = 640
) -> str:
    """複数群のテイク値を同一スケールに点で置く比較ストリップ。"""

    all_values = [v for _, values, _ in rows for v in values]
    lo, hi = min(all_values), max(all_values)
    pad = max((hi - lo) * 0.15, 1.0)
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    row_h = 34
    height = row_h * len(rows) + 22

    def x(value: float) -> float:
        return (value - lo) / span * (width - 150) + 140

    parts = [f'<svg viewBox="0 0 {width} {height}" class="strip" role="img">']
    for index, (label, values, klass) in enumerate(rows):
        y = row_h * index + row_h / 2 + 4
        parts.append(
            f'<text x="0" y="{y + 4}" class="rowlabel">{_esc(label)}</text>'
            f'<line x1="140" y1="{y}" x2="{width - 10}" y2="{y}" class="axis" />'
        )
        for value in values:
            parts.append(
                f'<circle cx="{x(value):.1f}" cy="{y}" r="6" class="dot {klass}" />'
            )
        mean = sum(values) / len(values)
        parts.append(
            f'<text x="{x(mean):.1f}" y="{y - 12}" text-anchor="middle" '
            f'class="meanlabel {klass}">平均 {mean:.0f}{_esc(unit)}</text>'
        )
    for tick in (min(all_values), max(all_values)):
        parts.append(
            f'<text x="{x(tick):.1f}" y="{height - 2}" text-anchor="middle" '
            f'class="tick">{tick:g}{_esc(unit)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _take_row(take: dict[str, Any]) -> str:
    measured = take["measured"]
    return (
        '<div class="takerow">'
        f"{_waveform_svg(take['waveform'])}"
        f'<audio controls preload="metadata" '
        f'src="data:audio/wav;base64,{take["embed_b64"]}"></audio>'
        '<div class="takemeta">'
        f'<span>seed {take["seed"]}</span>'
        f'<span class="measured">{measured["bpm"]:g} BPM</span>'
        f'<span class="measured">{measured["centroid"]:g} Hz</span>'
        f'<span class="measured">{_esc(measured["key"])}</span>'
        "</div></div>"
    )


def render_html(data: dict[str, Any]) -> str:
    configs = {c["name"]: c for c in data["configs"]}
    stats = data["stats"]

    base_rows = "".join(_take_row(t) for t in configs["base"]["takes"])
    bright_rows = "".join(_take_row(t) for t in configs["bright"]["takes"])
    slow_rows = "".join(_take_row(t) for t in configs["slow"]["takes"])

    centroid_strip = _dot_strip(
        [
            ("dark（原譜）", [t["measured"]["centroid"] for t in configs["base"]["takes"]], "a"),
            ("bright に変更", [t["measured"]["centroid"] for t in configs["bright"]["takes"]], "b"),
        ],
        unit="Hz",
    )
    bpm_strip = _dot_strip(
        [
            ("bpm 128（原譜）", [t["measured"]["bpm"] for t in configs["base"]["takes"]], "a"),
            ("bpm 80 に変更", [t["measured"]["bpm"] for t in configs["slow"]["takes"]], "b"),
        ],
        unit="",
    )

    bri = stats["brightness"]
    bpm = stats["bpm"]
    if bri["separated"]:
        bri_verdict = (
            f"✓ 分布ごと動いた（平均 {bri['mean_base']:g} → {bri['mean_variant']:g} Hz、"
            "3 テイクの範囲が重ならない）— このツマミは効く"
        )
    elif bri["inverted"]:
        bri_verdict = (
            f"△ 分布ごと動いたが期待と逆方向（平均 {bri['mean_base']:g} → "
            f"{bri['mean_variant']:g} Hz、bright 指定なのに明るさの針が下がった）— "
            "inverted。分離はあるが向きが逆で、そのままでは使えない"
        )
    else:
        bri_verdict = (
            f"△ 平均は {bri['mean_base']:g} → {bri['mean_variant']:g} Hz と動いたが、"
            "テイクのばらつきと重なる — 効き目はノイズと区別できない"
        )
    if bpm["separated"]:
        bpm_verdict = f"✓ 分布ごと動いた（平均 {bpm['mean_base']:g} → {bpm['mean_variant']:g}）"
    elif bpm["inverted"]:
        bpm_verdict = (
            f"△ 分布ごと動いたが期待と逆方向（平均 {bpm['mean_base']:g} → "
            f"{bpm['mean_variant']:g}、bpm 80 指定なのにテンポが上がった）— "
            "inverted。分離はあるが向きが逆で、そのままでは使えない"
        )
    else:
        bpm_verdict = (
            f"✗ 平均 {bpm['mean_base']:g} → {bpm['mean_variant']:g}。テイクのばらつきに"
            "埋まっている — この機種でこのツマミは（この書き方では）効かない"
        )

    # 結論文も verdict と同じく separated/inverted の状態から機械的に組み立てる
    # （固定文だと実測と矛盾し得るため、bri/bpm の分離判定に従わせる）。
    def _word(stat: dict[str, Any]) -> str:
        if stat["separated"]:
            return "効く"
        if stat["inverted"]:
            return "逆方向"
        return "効かない"

    bri_word = _word(bri)
    bpm_word = _word(bpm)
    summary_line = f"brightness は{bri_word}、bpm は{bpm_word}"

    # CSS クラスも文言と同じ判定から導出する（視覚と文言の不一致防止）。
    # inverted は「効く」の緑ではないが「効かない」の警告とも異なるため、
    # 既存の警告色（verdict-bad）を流用して区別を促す。
    bri_verdict_class = "verdict-ok" if bri["separated"] else "verdict-bad"
    bpm_verdict_class = "verdict-ok" if bpm["separated"] else "verdict-bad"

    template = Template(_PAGE_TEMPLATE)
    return template.substitute(
        prompt_text=_esc(data["prompt"].text),
        base_rows=base_rows,
        bright_rows=bright_rows,
        slow_rows=slow_rows,
        centroid_strip=centroid_strip,
        bpm_strip=bpm_strip,
        bri_verdict=_esc(bri_verdict),
        bpm_verdict=_esc(bpm_verdict),
        bri_verdict_class=bri_verdict_class,
        bpm_verdict_class=bpm_verdict_class,
        summary_line=_esc(summary_line),
        model_id=_esc(data["model_id"]),
        model_revision=_esc(data["model_revision"][:12]),
        duration=f"{DURATION_SEC:g}",
    )


_PAGE_TEMPLATE = """<title>本物の生成器をガチャごと測る — MusicGen 実演</title>
<style>
:root{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --bad:#A33D3D;
  --code-bg:#10182A; --code-ink:#D8DFEF; --code-dim:#8B96AF;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --bad:#D07070;
}}
:root[data-theme="dark"]{
  --bg:#0D1322; --surface:#161D30; --ink:#E8ECF6; --muted:#9AA3B8; --line:#2A3350;
  --authored:#BC8425; --measured:#3E9BD0; --ok:#3E9B62; --ok-bg:rgba(62,155,98,.16);
  --warn-bg:rgba(188,132,37,.14); --bad:#D07070;
}
:root[data-theme="light"]{
  --bg:#F2F4F8; --surface:#FFFFFF; --ink:#1A2233; --muted:#5A6478; --line:#D9DEE9;
  --authored:#8A6210; --measured:#20749E; --ok:#1E7A4E; --ok-bg:rgba(30,122,78,.10);
  --warn-bg:rgba(138,98,16,.10); --bad:#A33D3D;
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
.diff-add{color:var(--ok);font-weight:700;background:var(--ok-bg);
  border-radius:6px;padding:1px 8px;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px}
.takerow{border-top:1px dashed var(--line);padding-top:10px;margin-top:10px}
.takerow:first-of-type{border-top:none;margin-top:4px;padding-top:0}
.wave{width:100%;height:46px;display:block;margin:2px 0 6px}
.wave polygon{fill:var(--measured);opacity:.7}
audio{width:100%;margin:0 0 4px}
.takemeta{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted);
  font-variant-numeric:tabular-nums}
.takemeta .measured{color:var(--measured);font-weight:700}
.strip{width:100%;display:block;margin:8px 0}
.strip .axis{stroke:var(--line);stroke-width:1}
.strip .rowlabel{font-size:12px;fill:var(--ink);font-weight:600}
.strip .tick{font-size:10.5px;fill:var(--muted);
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.strip .meanlabel{font-size:10.5px;font-weight:700;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.strip .dot{stroke:var(--surface);stroke-width:2}
.strip .dot.a,.strip .meanlabel.a{fill:var(--authored)}
.strip .dot.b,.strip .meanlabel.b{fill:var(--measured)}
.verdict{border-radius:10px;padding:12px 16px;font-weight:700;margin:10px 0}
.verdict-ok{background:var(--ok-bg);color:var(--ok)}
.verdict-bad{background:var(--warn-bg);color:var(--authored)}
.why p{margin:.6em 0}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
.small{font-size:13px}
.dim{color:var(--muted)}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">ugh-prompt-engine / 実生成器デモ（第二幕）</div>
  <h1>本物の生成器を、<br>ガチャごと測る</h1>
  <p class="lede">今度は自前の合成ではなく、実際の音楽生成モデル
  <b>MusicGen</b>（$model_id・ローカル推論）に同じ楽譜を演奏させました。
  本物の生成器は毎回違う曲を出します。それでも——<b>どのツマミが効くかは、
  計器で測れば分かる</b>。それを実測でお見せします。</p>
</header>

<section>
  <div class="chap">第 1 章</div>
  <h2>同じ楽譜から 3 回生成すると、3 つの違う曲が出る</h2>
  <p class="sub">楽譜をコンパイルした 1 つの同じプロンプトを、乱数の種だけ変えて
  3 回演奏させた結果です。これが「ガチャ」の正体で、以後の判定基準になる
  <b>再生成ノイズ</b>（同条件でもこれだけブレる）でもあります。</p>
  <pre>$prompt_text</pre>
  <div class="card">
    <div class="card-title">原譜のまま × 3 テイク</div>
    $base_rows
  </div>
</section>

<section>
  <div class="chap">第 2 章</div>
  <h2>1 行変えて 3 回 — 分布ごと動けば「効くツマミ」</h2>
  <p class="sub">楽譜の <span class="diff-add">brightness: bright</span> だけを
  書き換えて、また 3 回。1 回の比較では偶然と区別できませんが、
  <b>3 テイクの分布が帯ごと動けば</b>それはツマミが効いた証拠です。</p>
  <div class="card">
    <div class="card-title">bright に変更 × 3 テイク</div>
    $bright_rows
  </div>
  <div class="card">
    <div class="card-title">明るさの針（spectral centroid）— 6 テイクを同じ物差しに</div>
    $centroid_strip
    <div class="verdict $bri_verdict_class">$bri_verdict</div>
  </div>
</section>

<section>
  <div class="chap">第 3 章</div>
  <h2>効かないツマミも、計器なら分かる</h2>
  <p class="sub">同じことを <span class="diff-add">bpm: 80</span> でも試します。
  耳ではどうでしょう。針ではどうでしょう。</p>
  <div class="card">
    <div class="card-title">bpm 80 に変更 × 3 テイク</div>
    $slow_rows
  </div>
  <div class="card">
    <div class="card-title">テンポの針（BPM）— 6 テイクを同じ物差しに</div>
    $bpm_strip
    <div class="verdict $bpm_verdict_class">$bpm_verdict</div>
  </div>
</section>

<section class="why">
  <div class="chap">第 4 章</div>
  <h2>これが「機種の取扱説明書」になる</h2>
  <p>いま見た実測——<b>$summary_line</b>——は、そのまま
  この機種（MusicGen small）の<b>取扱説明書</b>としてリポジトリに保存されています
  （<code>device_profiles/musicgen.yaml</code>）。</p>
  <p>事前の device-profile 知見では、Suno には Suno の実測プロファイルがあり、
  同じ楽譜でも機種ごとに効くツマミが違うことが分かっています（これは今回の
  MusicGen 実測とは別に蓄積された期待値で、両者を混同しないための注記です）。</p>
  <p>楽譜をコンパイルするとき、コンパイラはこの取説を読んで
  「この機種でその指定は届かない実績があります」と<b>機種メモ</b>を添えます。
  ガチャは消せません。でも、<b>ガチャのまま科学にする</b>ことはできる——
  それがこのプロジェクトの二本目の柱です（一本目の決定論編は前のページ）。</p>
</section>

<footer class="foot">
  <p><b>再現情報</b> — $model_id @ $model_revision・${duration} 秒×各 3 テイク・
  seed は manifest に記録済み。生成は非決定論のため takes manifest（sha256 付き）が
  一次記録。計測と描画は <code>python scripts/render_showcase_musicgen.py
  --skip-generate</code> で決定論に再実行できる。モデル重みは CC-BY-NC-4.0
  （研究デモ用途）。</p>
</footer>
</div>
"""


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(output_dir: Path, *, skip_generate: bool) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore = output_dir / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    data = gather(output_dir, skip_generate=skip_generate)
    payload = as_json_payload(data)
    (output_dir / "showcase_musicgen.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "showcase_musicgen.html").write_text(render_html(data), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()
    payload = run(args.output_dir, skip_generate=args.skip_generate)
    for name in ("brightness", "bpm"):
        stat = payload["stats"][name]
        print(
            f"{name}: mean {stat['mean_base']} -> {stat['mean_variant']} "
            f"(shift {stat['shift']}, separated={stat['separated']}, "
            f"inverted={stat['inverted']})"
        )
    print(f"artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
