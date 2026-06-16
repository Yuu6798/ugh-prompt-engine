"""R1 corpus screening harness — 実音源を「指示値 vs 検出値」の保存率テーブルにする計器。

実生成音源（Suno 等）を一括抽出し、生成プロンプトの指示値（ground truth）と
抽出器の検出値を突き合わせて *保存性* を計測する。母数を増やして base rate
（bpm 保存率・key 保存率・アトラクタ再発・brightness 分布）を読むための breadth ツール。

metamorphic_probe（合成音で配線テスト）に対し、本ツールは実音源で「指示が生成器を
通って保存されるか」を測る往復保存(目的2)のスクリーナ。pass/fail でなく計測。

入力 ground-truth YAML:

    songs:
      - id: shiden
        audio: /path/to/shiden.mp3
        bpm: 168
        key: D minor          # "D minor" or split key:/mode:
        time_signature: 4/4   # optional

使い方:

    python scripts/screen_corpus.py ground_truth.yaml            # Markdown
    python scripts/screen_corpus.py ground_truth.yaml --json
    python scripts/screen_corpus.py ground_truth.yaml --out report.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from svp_rpe.rpe.extractor import extract_physical_from_file  # noqa: E402

_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
    "BB": 10, "B": 11, "CB": 11,
}

# bpm 保存判定の許容（±%）。これ以内なら preserved。
BPM_TOLERANCE = 0.04


def pitch_class(root: str) -> int | None:
    """音名 → ピッチクラス [0,11]。未知は None。"""
    return _PITCH_CLASS.get(root.strip().upper())


def parse_key(stated: str | None, mode: str | None = None) -> tuple[str | None, str | None]:
    """'D minor' / 'D' + mode → (root, mode) に正規化。"""
    if stated is None:
        return None, None
    text = str(stated).strip()
    if mode is None and " " in text:
        root, _, m = text.partition(" ")
        return root.strip(), m.strip().lower()
    return text, (mode.strip().lower() if mode else None)


def bpm_relation(stated: float, detected: float | None) -> dict[str, Any]:
    """指示 bpm と検出 bpm の関係を分類。

    preserved / octave_half / octave_double / off の4値。off は
    「オクターブですらない誤検出」（例 175→136）を捕まえる。
    """
    if detected is None or stated <= 0:
        return {"status": "no_detection", "ratio": None, "error_pct": None}
    ratio = detected / stated
    error_pct = round(abs(detected - stated) / stated * 100, 1)
    if abs(ratio - 1.0) <= BPM_TOLERANCE:
        status = "preserved"
    elif 0.45 <= ratio <= 0.55:
        status = "octave_half"
    elif 1.8 <= ratio <= 2.2:
        status = "octave_double"
    else:
        status = "off"
    return {"status": status, "ratio": round(ratio, 3), "error_pct": error_pct}


def key_relation(
    stated_root: str | None,
    stated_mode: str | None,
    det_root: str | None,
    det_mode: str | None,
) -> str:
    """指示 key と検出 key の関係: preserved / relative / parallel / off / unknown。"""
    sp = pitch_class(stated_root) if stated_root else None
    dp = pitch_class(det_root) if det_root else None
    if sp is None or dp is None:
        return "unknown"
    if sp == dp and stated_mode == det_mode:
        return "preserved"
    if sp == dp and stated_mode != det_mode:
        return "parallel"
    # relative: major root +9 == relative minor root
    if stated_mode == "major" and det_mode == "minor" and (sp + 9) % 12 == dp:
        return "relative"
    if stated_mode == "minor" and det_mode == "major" and (sp + 3) % 12 == dp:
        return "relative"
    return "off"


def resolve_audio(raw: str, base_dir: Path | None) -> Path:
    """manifest の audio パスを解決する。相対パスは ground-truth YAML の置かれた
    `base_dir` を基準に解決し、manifest を可搬にする（cwd 依存を避ける）。
    絶対パスはそのまま返す。"""
    path = Path(raw)
    if base_dir is not None and not path.is_absolute():
        return base_dir / path
    return path


def screen_song(song: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """1曲を抽出し指示値と突き合わせる。相対 audio パスは `base_dir` 基準で解決。"""
    phys = extract_physical_from_file(str(resolve_audio(str(song["audio"]), base_dir)))
    stated_root, stated_mode = parse_key(song.get("key"), song.get("mode"))
    bpm = bpm_relation(float(song["bpm"]), phys.bpm) if song.get("bpm") else {"status": "no_intent"}
    key = key_relation(stated_root, stated_mode, phys.key, phys.mode)
    return {
        "id": song.get("id", Path(str(song["audio"])).stem),
        "stated": {
            "bpm": song.get("bpm"),
            "key": f"{stated_root} {stated_mode}" if stated_root else None,
            "time_signature": song.get("time_signature"),
        },
        "detected": {
            "bpm": phys.bpm,
            "bpm_octave_ambiguous": phys.bpm_octave_ambiguous,
            "key": f"{phys.key} {phys.mode}" if phys.key else None,
            "time_signature": phys.time_signature,
            "centroid": round(phys.spectral_centroid, 1),
            "high_ratio": round(phys.spectral_profile.high_ratio, 4),
        },
        "bpm_relation": bpm,
        "key_relation": key,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """保存率の base rate を集計。"""
    bpm_status = [r["bpm_relation"]["status"] for r in rows if "ratio" in r["bpm_relation"]]
    key_status = [r["key_relation"] for r in rows if r["key_relation"] != "unknown"]
    n_bpm = len(bpm_status) or 1
    n_key = len(key_status) or 1
    # 非保存 bpm 誤差（off/octave_half/octave_double）のうち octave_ambiguous フラグが
    # 立っていないもの全般。R2-2a の ×2 契約外の "off"（例 172→117.45=0.68× の subharmonic
    # collapse）も含むため「octave detector のミス」とは名乗らず「未フラグ誤差」と総称する
    # （これらを掴むのが R2-2b の動機）。
    unflagged_errors = [
        r["id"]
        for r in rows
        if r["bpm_relation"].get("status") in {"off", "octave_half", "octave_double"}
        and not r["detected"]["bpm_octave_ambiguous"]
    ]
    return {
        "n_songs": len(rows),
        "bpm_preservation_rate": round(bpm_status.count("preserved") / n_bpm, 3),
        "bpm_status_counts": {s: bpm_status.count(s) for s in sorted(set(bpm_status))},
        "key_preservation_rate": round(key_status.count("preserved") / n_key, 3),
        "key_status_counts": {s: key_status.count(s) for s in sorted(set(key_status))},
        "bpm_errors_unflagged": unflagged_errors,
    }


def build_report(ground_truth_path: str | Path) -> dict[str, Any]:
    gt_path = Path(ground_truth_path)
    data = yaml.safe_load(gt_path.read_text(encoding="utf-8"))
    songs = data["songs"] if isinstance(data, dict) else data
    base_dir = gt_path.resolve().parent
    rows = [screen_song(song, base_dir=base_dir) for song in songs]
    return {"rows": rows, "summary": aggregate(rows)}


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# R1 Corpus Screen", ""]
    lines.append("| id | stated bpm | det bpm | bpm | stated key | det key | key | high_ratio |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report["rows"]:
        s, d = r["stated"], r["detected"]
        lines.append(
            f"| {r['id']} | {s['bpm']} | {d['bpm']} | {r['bpm_relation']['status']} | "
            f"{s['key']} | {d['key']} | {r['key_relation']} | {d['high_ratio']} |"
        )
    sm = report["summary"]
    lines += [
        "",
        "## Base rates",
        "",
        f"- songs: {sm['n_songs']}",
        f"- bpm preservation: {sm['bpm_preservation_rate']}  {sm['bpm_status_counts']}",
        f"- key preservation: {sm['key_preservation_rate']}  {sm['key_status_counts']}",
        f"- bpm errors unflagged (octave_ambiguous=False): {sm['bpm_errors_unflagged']}",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ground_truth", type=Path, help="ground-truth YAML")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build_report(args.ground_truth)
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
