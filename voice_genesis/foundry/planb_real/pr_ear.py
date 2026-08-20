"""planb_real/pr_ear.py — G-ear（聴感判定）の材料と回答様式を用意する。

instruction §10 G-ear / §0-10「人間の聴感と機械評価を上書き統合しない」に従い、
機械の判定とは独立に人が答えられる形にする。

用意するもの（probe ペアごと）:

    ANCHOR_ritsu  Ritsu 原音の当該区間（Identity の基準。合成していない）
    R0 … R4       ラダー出力
    P0_pjs        PJS 原音の当該区間（Performance ドナー。Q4 の対照）

`--blind` を付けると R0/R4 をランダムでない決定論的な順で A/B へ伏せ、
対応表を別ファイル（`ear_blind_key.json`）へ出す。Q3 は改善の向きを
知っていると引きずられるため、伏せて聴いてから開ける運用ができる。

**出力音声はリポジトリへ収載しない**（歌声データベース利用規約 第3条 +
LICENSE_LEDGER の非公開方針）。`results/.gitignore` が機械的に除外する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "planb"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pr_census  # noqa: E402
import pr_gates  # noqa: E402
import pr_identity  # noqa: E402
import pr_lab  # noqa: E402

LADDER_MANIFEST = _HERE / "results" / "ladder_manifest.json"
EAR_DIR = _HERE / "results" / "ear_set"
BLIND_KEY = _HERE / "results" / "ear_blind_key.json"
TEMPLATE = _HERE / "results" / "ear_answers.template.json"

PAD_S = 0.6


def _context_phones() -> int:
    """原音の切り出しは、合成側と同じ文脈長にそろえる（比較の土俵を合わせる）。"""
    try:
        data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
        return int(data.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES))
    except Exception:  # noqa: BLE001
        return pr_identity.DEFAULT_CONTEXT_PHONES


def _write_pcm16(path: Path, x: np.ndarray, sr: int) -> None:
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x))) or 1.0
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, (x / peak * 0.9 * 32767.0).astype(np.int16), sr, subtype="PCM_16")


def _slice_original(lab_path: Path, phone_index: int) -> Optional[tuple]:
    """原音の probe 周辺を切り出す（合成せず、収録音そのもの）。"""
    wav = pr_census.wav_for_lab(lab_path)
    if wav is None:
        return None
    lab = pr_lab.read_lab(lab_path)
    idx, _flags, _pos = pr_identity.probe_unit_indices(
        lab.phones, phone_index, context=_context_phones())
    lo = min(lab.phones[i].start_s for i in idx)
    hi = max(lab.phones[i].end_s for i in idx)
    x, sr, _off = pr_identity.load_wav_mono(wav, start_s=lo - PAD_S, end_s=hi + PAD_S)
    return x, sr


def build(blind: bool = False, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
    manifest: Dict[str, Any] = {"pairs": {}}
    key: Dict[str, Any] = {}
    for p in data["pairs"]:
        kind = p["probe_kind"]
        if kinds and kind not in kinds:
            continue
        pair_key = p["pair_key"]
        tag = pair_key.replace("|", "__").replace("#", "-")
        out = EAR_DIR / tag
        files: Dict[str, str] = {}

        r_lab = Path(p["ritsu_file"])
        r_idx = int(pair_key.split("|")[1].split("#")[1])
        got = _slice_original(r_lab, r_idx)
        if got is not None:
            _write_pcm16(out / "ANCHOR_ritsu.wav", got[0], got[1])
            files["ANCHOR_ritsu"] = str(out / "ANCHOR_ritsu.wav")

        p_lab = Path(p["pjs_file"])
        p_idx = int(pair_key.split("|")[2].split("#")[1])
        got = _slice_original(p_lab, p_idx)
        if got is not None:
            _write_pcm16(out / "P0_pjs.wav", got[0], got[1])
            files["P0_pjs"] = str(out / "P0_pjs.wav")

        for rung, m in p["rungs"].items():
            src = Path(m["wav_path"])
            if not src.exists():
                continue
            x, sr = sf.read(str(src))
            _write_pcm16(out / f"{rung}.wav", x, sr)
            files[rung] = str(out / f"{rung}.wav")

        if blind and "R0" in files and "R4" in files:
            # 決定論的な伏せ方: pair_key の sha256 の最下位ビットで A/B を割り当てる
            bit = int(hashlib.sha256(pair_key.encode("utf-8")).hexdigest(), 16) & 1
            a_src, b_src = ("R0", "R4") if bit == 0 else ("R4", "R0")
            for label, rung in (("A", a_src), ("B", b_src)):
                x, sr = sf.read(files[rung])
                _write_pcm16(out / f"BLIND_{label}.wav", x, sr)
            key[pair_key] = {"A": a_src, "B": b_src}
            files["BLIND_A"] = str(out / "BLIND_A.wav")
            files["BLIND_B"] = str(out / "BLIND_B.wav")

        manifest["pairs"][pair_key] = {"probe_kind": kind, "files": files}

    if blind:
        BLIND_KEY.parent.mkdir(parents=True, exist_ok=True)
        BLIND_KEY.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def write_template() -> None:
    """回答様式。`results/ear_answers.json` へコピーして埋めると G-ear が閉じる。"""
    tmpl = {
        "listener": "<回答者名>",
        "listened_at": "<YYYY-MM-DD>",
        "blinded": False,
        "material": "results/ear_set/<pair>/{ANCHOR_ritsu,R0..R4,P0_pjs}.wav",
        "machine_result_seen_before_listening": True,
        **{q: {"verdict": "<yes|partial|no|unclear>", "note": "<自由記述>",
               "question": text}
           for q, text in pr_gates.EAR_QUESTIONS},
        "per_pair": {
            "<pair_key>": {"Q3_verdict": "<yes|partial|no|unclear>", "note": ""}
        },
    }
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="G-ear の材料と回答様式を用意する")
    ap.add_argument("--blind", action="store_true", help="R0/R4 を A/B へ伏せる")
    ap.add_argument("--kinds", nargs="*", default=None, help="probe 種を絞る")
    args = ap.parse_args(argv)
    man = build(blind=args.blind, kinds=args.kinds)
    write_template()
    for pk, v in man["pairs"].items():
        print(f"{pk}\n   {sorted(v['files'])}")
    print(f"\ntemplate -> {TEMPLATE}")
    if args.blind:
        print(f"blind key -> {BLIND_KEY}（聴き終わるまで開かないこと）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
