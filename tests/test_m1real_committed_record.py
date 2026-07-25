"""M1-real の commit 済み測定記録（verdict / report）の整合を CI で守る。

`docs/measurements/m1real_2026-07/` の verdict は report を hash で pin するが、
通常の評価器テストは一時 report しか使わないため、commit 済み report が後から
再生成・編集されても CI は沈黙する（Codex 指摘・#220）。ここで pin ↔ 実ファイルの
sha256 と registry pin を突き合わせ、「go artifact が存在しない bytes を指す」状態を
機械検出する。

意図的な帰結: registry.yaml を後から編集する（例: crepe / demucs の code pin 転記）と
verdict の `registry_sha256` が食い違いこのテストが赤くなる。それは「pin を足すなら
再実測して verdict を作り直す」という運用コストを CI が可視化する挙動であり、
テストを緩めて通してはならない。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "docs" / "measurements" / "m1real_2026-07"
VERDICT = RECORD_DIR / "m1real_verdict.json"
REGISTRY = REPO / "tests" / "fixtures" / "melody_bench" / "registry.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verdict_report_pins_match_committed_reports() -> None:
    verdict = json.loads(VERDICT.read_text())
    pins = verdict["report_pins"]
    # pin 集合の完全性: n_reports / run_ids と件数が一致し、path・digest とも重複なし。
    # 片方の pin を消す / 複製する編集で「n=2 の repeats に見える単一 report」を作らせない。
    assert len(pins) == verdict["n_reports"] == len(verdict["run_ids"]), (
        f"pin 数 {len(pins)} が n_reports={verdict['n_reports']} / "
        f"run_ids={len(verdict['run_ids'])} と一致しない"
    )
    assert len({Path(p["path"]).name for p in pins}) == len(pins), "pin の path が重複"
    assert len({p["sha256"] for p in pins}) == len(pins), "pin の digest が重複"
    for pin in pins:
        # 生成時の path は basename（生成場所相対）。commit 済みコピーは verdict と
        # 同じディレクトリに同名で置く規約。
        target = RECORD_DIR / Path(pin["path"]).name
        assert target.is_file(), f"pinned report が存在しない: {target}"
        assert _sha256(target) == pin["sha256"], (
            f"{target.name} の bytes が verdict の pin と不一致。report を再生成した"
            "場合は verdict も作り直すこと（pin だけ残して中身を差し替えない）"
        )


def test_verdict_registry_pin_matches_frozen_registry() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert _sha256(REGISTRY) == verdict["registry_sha256"], (
        "registry.yaml が verdict 生成時から変更されている。registry を編集した場合は"
        "再実測して verdict を作り直すこと（dated go を旧 registry の名で残さない）"
    )


def test_verdict_is_the_committed_go() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert verdict["verdict"] == "go"
    assert verdict["surviving_routes"] == ["demucs_vocals_then_crepe"]
