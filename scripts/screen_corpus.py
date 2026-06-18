"""R1 corpus screening harness — 実音源を「指示値 vs 検出値」の保存率テーブルにする計器。

実生成音源（Suno 等）を一括抽出し、生成プロンプトの指示値（ground truth）と
抽出器の検出値を突き合わせて *保存性* を計測する。母数を増やして base rate
（bpm 保存率・key 保存率・アトラクタ再発・brightness 分布）を読むための breadth ツール。

metamorphic_probe（合成音で配線テスト）に対し、本ツールは実音源で「指示が生成器を
通って保存されるか」を測る往復保存(目的2)のスクリーナ。pass/fail でなく計測。

各曲を既定 prior(~120) に対して高 prior(180) / 低 prior(50) でも再推定し、「既定 prior で
崩壊したが別 prior で stated を回復する」行を prior recovery として分離する
（roundtrip_corpus_screen.md「prior 診断」のコード化）。これにより BPM 非保存を
*抽出器の prior 偏向（真テンポは音源に在る）* と *生成器の不忠実* に弁別する。

入力 ground-truth YAML:

    songs:
      - id: shiden
        audio: /path/to/shiden.mp3
        bpm: 168
        key: D minor          # "D minor" or split key:/mode:
        time_signature: 4/4   # optional
        audio_sha256: <hex>   # optional provenance pin; mismatch -> untrusted

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

from svp_rpe.io.audio_loader import load_audio  # noqa: E402
from svp_rpe.perform import sha256_bytes  # noqa: E402
from svp_rpe.rpe.extractor import extract_physical_from_file  # noqa: E402
from svp_rpe.rpe.physical_features import compute_bpm  # noqa: E402

_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
    "BB": 10, "B": 11, "CB": 11,
}

# bpm 保存判定の許容（±%）。これ以内なら preserved。
BPM_TOLERANCE = 0.04

# 高 prior 回復チェックの tempo prior。既定 prior(~120) は高速曲の真テンポを
# 89/117.45 のグリッド点へ降格させる（roundtrip_corpus_screen.md「高 prior 診断」）。
# 実 Suno 6 テイクで start_bpm=180 が全件 172.3 を回復した実測値を採用。
HIGH_PRIOR_START_BPM = 180.0

# 低 prior 回復チェックの tempo prior。既定 prior(~120) が遅い真テンポを倍速側へ
# 押し上げる reported-too-fast / doubling ケースを、stated 真値を持つ screener で診断する。
LOW_PRIOR_START_BPM = 50.0


def prior_recovery(default_status: str, alternate_prior_status: str) -> str:
    """既定 prior と別 prior の bpm 保存判定から「崩壊が抽出器 prior 由来か」を分類。

    - "n/a": 既定 prior で既に preserved（回復の余地がない）。
    - "recovered": 既定は非保存だが別 prior で preserved に転じる
      → 崩壊は抽出器の prior 偏向で、真テンポは音源に在る。
    - "not_recovered": 別 prior でも非保存 → prior 偏向では説明できない
      （生成器の不忠実、または別の誤差源）。
    """
    if default_status == "preserved":
        return "n/a"
    if alternate_prior_status == "preserved":
        return "recovered"
    return "not_recovered"


def classify_prior_recovery(
    stated_bpm: float, raw_default_bpm: float | None, high_prior_bpm: float | None
) -> str:
    """stated に対する *生の* 既定 prior 推定と高 prior 推定から回復を分類。

    **default 側は補正前の生 `compute_bpm`（start_bpm=120）を渡すこと。**
    R2-2c/2d 以降 `extract_physical_from_file` は崩壊を検出すると `phys.bpm` を
    回復テンポへ補正して返すため、補正後の値を default 側に使うと崩壊が preserved に
    見え、まさに検出器が捕まえた halving が "n/a" となって recovered 母数から漏れる
    （Codex P2, PR #85）。生の二択推定（同一コードパス・prior のみ差）で比較する。
    """
    default_status = bpm_relation(stated_bpm, raw_default_bpm)["status"]
    high_status = bpm_relation(stated_bpm, high_prior_bpm)["status"]
    return prior_recovery(default_status, high_status)


def classify_doubling_recovery(
    stated_bpm: float, raw_default_bpm: float | None, low_prior_bpm: float | None
) -> str:
    """stated に対する *生の* 既定 prior 推定と低 prior 推定から回復を分類。

    reported-too-fast / doubling（真テンポは低いが既定 prior が速い側を選ぶ）を、低 prior
    再推定で stated に戻るかで診断する。回復分類は direction-agnostic な `prior_recovery`
    を使いつつ、doubling 診断なので既定生 BPM が stated より速い側にあることだけを要求する。
    """
    default_status = bpm_relation(stated_bpm, raw_default_bpm)["status"]
    low_status = bpm_relation(stated_bpm, low_prior_bpm)["status"]
    recovery = prior_recovery(default_status, low_status)
    if recovery != "recovered":
        return recovery
    if raw_default_bpm is None or raw_default_bpm <= stated_bpm * (1.0 + BPM_TOLERANCE):
        return "not_recovered"
    return recovery


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


def audio_hash_ok(path: Path, expected: str | None) -> bool:
    """provenance pin の検証。`expected` が無ければ常に True（pin 任意）。
    pin がある場合はファイルが実在し sha256 が一致したときのみ True。"""
    if not expected:
        return True
    if not path.is_file():
        return False
    return sha256_bytes(path.read_bytes()) == expected


def _untrusted_row(song: dict[str, Any], reason: str) -> dict[str, Any]:
    """provenance 不一致の行。抽出せず保存率からも除外される（untrusted）。"""
    stated_root, stated_mode = parse_key(song.get("key"), song.get("mode"))
    return {
        "id": song.get("id", Path(str(song["audio"])).stem),
        "provenance": reason,
        "stated": {
            "bpm": song.get("bpm"),
            "key": f"{stated_root} {stated_mode}" if stated_root else None,
            "time_signature": song.get("time_signature"),
        },
        "detected": None,
        "bpm_relation": {"status": "untrusted"},
        "bpm_prior_recovery": "n/a",
        "bpm_doubling_prior_recovery": "n/a",
        "key_relation": "untrusted",
    }


def screen_song(song: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """1曲を抽出し指示値と突き合わせる。相対 audio パスは `base_dir` 基準で解決。
    `audio_sha256` pin がある行はバイト一致を検証し、不一致なら抽出せず untrusted。"""
    audio_path = resolve_audio(str(song["audio"]), base_dir)
    if not audio_hash_ok(audio_path, song.get("audio_sha256")):
        return _untrusted_row(song, reason="sha256_mismatch")
    phys = extract_physical_from_file(str(audio_path))
    stated_root, stated_mode = parse_key(song.get("key"), song.get("mode"))
    bpm = bpm_relation(float(song["bpm"]), phys.bpm) if song.get("bpm") else {"status": "no_intent"}
    key = key_relation(stated_root, stated_mode, phys.key, phys.mode)

    # 高 prior 回復チェック: 同じ音源を既定 prior(生) と高 prior で *補正前* の生値で
    # 二択推定し、既定 prior の崩壊が「抽出器 prior 由来（halving）」か「生成器の
    # 不忠実」かを切り分ける。default 側は phys.bpm（R2-2c/2d 補正済み）でなく生の
    # compute_bpm を使う（補正後だと崩壊が preserved に見え recovered が漏れる）。
    # 音源は extract が内部で読むため二重ロードになるが、breadth 計器なので可読性優先。
    audio = load_audio(str(audio_path))
    bpm_default_raw, _ = compute_bpm(audio.y_mono, audio.sr)
    bpm_hp, _ = compute_bpm(audio.y_mono, audio.sr, start_bpm=HIGH_PRIOR_START_BPM)
    bpm_lp, _ = compute_bpm(audio.y_mono, audio.sr, start_bpm=LOW_PRIOR_START_BPM)
    recovery = (
        classify_prior_recovery(float(song["bpm"]), bpm_default_raw, bpm_hp)
        if song.get("bpm")
        else "n/a"
    )
    recovery_doubling = (
        classify_doubling_recovery(float(song["bpm"]), bpm_default_raw, bpm_lp)
        if song.get("bpm")
        else "n/a"
    )

    return {
        "id": song.get("id", Path(str(song["audio"])).stem),
        "stated": {
            "bpm": song.get("bpm"),
            "key": f"{stated_root} {stated_mode}" if stated_root else None,
            "time_signature": song.get("time_signature"),
        },
        "detected": {
            "bpm": phys.bpm,
            "bpm_default_raw": bpm_default_raw,
            "bpm_high_prior": bpm_hp,
            "bpm_low_prior": bpm_lp,
            "bpm_octave_ambiguous": phys.bpm_octave_ambiguous,
            "key": f"{phys.key} {phys.mode}" if phys.key else None,
            "time_signature": phys.time_signature,
            "centroid": round(phys.spectral_centroid, 1),
            "high_ratio": round(phys.spectral_profile.high_ratio, 4),
        },
        "bpm_relation": bpm,
        "bpm_prior_recovery": recovery,
        "bpm_doubling_prior_recovery": recovery_doubling,
        "key_relation": key,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """保存率の base rate を集計。"""
    bpm_status = [r["bpm_relation"]["status"] for r in rows if "ratio" in r["bpm_relation"]]
    key_status = [r["key_relation"] for r in rows if r["key_relation"] not in {"unknown", "untrusted"}]
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
    # 既定 prior で崩壊したが高 prior(180) で stated を回復した行＝抽出器 halving。
    # 真テンポは音源に在り、生成器の不忠実ではないと診断できる母数（roundtrip_
    # corpus_screen.md「高 prior 診断」のコード化）。
    prior_recoverable = [r["id"] for r in rows if r.get("bpm_prior_recovery") == "recovered"]
    # 既定 prior で倍速側へ崩壊したが低 prior(50) で stated を回復した行＝抽出器 doubling。
    doubling_prior_recoverable = [
        r["id"] for r in rows if r.get("bpm_doubling_prior_recovery") == "recovered"
    ]
    untrusted = [r["id"] for r in rows if r.get("provenance")]
    return {
        "n_songs": len(rows),
        "untrusted": untrusted,
        "bpm_preservation_rate": round(bpm_status.count("preserved") / n_bpm, 3),
        "bpm_status_counts": {s: bpm_status.count(s) for s in sorted(set(bpm_status))},
        "key_preservation_rate": round(key_status.count("preserved") / n_key, 3),
        "key_status_counts": {s: key_status.count(s) for s in sorted(set(key_status))},
        "bpm_errors_unflagged": unflagged_errors,
        "bpm_halving_prior_recoverable": prior_recoverable,
        "bpm_doubling_prior_recoverable": doubling_prior_recoverable,
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
    lines.append(
        "| id | stated bpm | det bpm | raw bpm | hi-prior bpm | lp bpm | bpm | recovery | "
        "dbl-recov | stated key | det key | key | high_ratio |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in report["rows"]:
        s, d = r["stated"], r["detected"]
        if d is None:  # untrusted (provenance mismatch): 抽出していない
            lines.append(
                f"| {r['id']} | {s['bpm']} | — | — | — | — | {r['bpm_relation']['status']} | "
                f"— | — | {s['key']} | — | {r['key_relation']} | — |"
            )
            continue
        lines.append(
            f"| {r['id']} | {s['bpm']} | {d['bpm']} | {d['bpm_default_raw']} | "
            f"{d['bpm_high_prior']} | {d['bpm_low_prior']} | {r['bpm_relation']['status']} | "
            f"{r['bpm_prior_recovery']} | {r['bpm_doubling_prior_recovery']} | "
            f"{s['key']} | {d['key']} | {r['key_relation']} | {d['high_ratio']} |"
        )
    sm = report["summary"]
    lines += [
        "",
        "## Base rates",
        "",
        f"- songs: {sm['n_songs']}  (untrusted: {sm['untrusted']})",
        f"- bpm preservation: {sm['bpm_preservation_rate']}  {sm['bpm_status_counts']}",
        f"- key preservation: {sm['key_preservation_rate']}  {sm['key_status_counts']}",
        f"- bpm errors unflagged (octave_ambiguous=False): {sm['bpm_errors_unflagged']}",
        f"- bpm halving prior-recoverable (start_bpm={HIGH_PRIOR_START_BPM:g}): "
        f"{sm['bpm_halving_prior_recoverable']}",
        f"- bpm doubling prior-recoverable (start_bpm={LOW_PRIOR_START_BPM:g}): "
        f"{sm['bpm_doubling_prior_recoverable']}",
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
