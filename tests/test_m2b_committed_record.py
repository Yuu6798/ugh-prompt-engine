"""M2b の commit 済み測定記録（verdict / report）の整合を CI で守る。

`docs/measurements/m2b_2026-07/` の verdict は run report を hash で pin するが、
通常の評価器テストは一時 report しか使わないため、commit 済み report が後から
再生成・編集されても CI は沈黙する（`test_m1real_committed_record.py` と同型の
問題・#220 由来）。ここで pin ↔ 実ファイルの sha256 と凍結 bars/specs fixture の
digest を突き合わせ、「go/fail 判定が存在しない bytes を指す」状態を機械検出する。

M1-real との差分: report_pins のキーは `path_name`（`path_relative` は null で
未使用）。また M2b は verdict に registry pin を持たず、代わりに凍結
`m2_accuracy_bars.yaml` / `m2_accuracy_specs.yaml` の sha256 を run report・
verdict 双方が pin する。

意図的な帰結: bars/specs fixture を後から編集すると `bars_sha256` /
`specs_sha256` が食い違いこのテストが赤くなる。それは「fixture を変えるなら
再実測して verdict を作り直す」という運用コストを CI が可視化する挙動であり、
テストを緩めて通してはならない。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "docs" / "measurements" / "m2b_2026-07"
VERDICT = RECORD_DIR / "m2b_verdict.json"
BARS = REPO / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"
SPECS = REPO / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"
RUN_REPORTS = [RECORD_DIR / "m2b_run1.json", RECORD_DIR / "m2b_run2.json"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verdict_report_pins_match_committed_reports() -> None:
    verdict = json.loads(VERDICT.read_text())
    pins = verdict["report_pins"]
    # pin 集合の完全性: n_reports / run_ids と件数が一致し、path_name・digest とも
    # 重複なし。片方の pin を消す / 複製する編集で「n=2 の repeats に見える単一
    # report」を作らせない。
    assert len(pins) == verdict["n_reports"] == len(verdict["run_ids"]), (
        f"pin 数 {len(pins)} が n_reports={verdict['n_reports']} / "
        f"run_ids={len(verdict['run_ids'])} と一致しない"
    )
    assert len({pin["path_name"] for pin in pins}) == len(pins), "pin の path_name が重複"
    assert len({pin["sha256"] for pin in pins}) == len(pins), "pin の digest が重複"
    assert len(set(verdict["run_ids"])) == len(verdict["run_ids"]), "run_ids が重複"
    for pin in pins:
        # M1-real と異なり path_relative は null（未使用）。commit 済みコピーは
        # verdict と同じディレクトリに path_name と同名で置く規約。
        target = RECORD_DIR / pin["path_name"]
        assert target.is_file(), f"pinned report が存在しない: {target}"
        assert _sha256(target) == pin["sha256"], (
            f"{target.name} の bytes が verdict の pin と不一致。report を再生成した"
            "場合は verdict も作り直すこと（pin だけ残して中身を差し替えない）"
        )


def test_verdict_bars_pin_matches_frozen_bars() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert _sha256(BARS) == verdict["bars_sha256"], (
        "m2_accuracy_bars.yaml が verdict 生成時から変更されている。bars を編集した"
        "場合は再実測して verdict を作り直すこと（dated 判定を旧 bars の名で残さない）"
    )


def test_run_reports_pin_frozen_bars_and_specs() -> None:
    bars_sha256 = _sha256(BARS)
    specs_sha256 = _sha256(SPECS)
    for report_path in RUN_REPORTS:
        report = json.loads(report_path.read_text())
        assert report["bars_sha256"] == bars_sha256, (
            f"{report_path.name} の bars_sha256 が凍結 fixture と不一致"
        )
        assert report["specs_sha256"] == specs_sha256, (
            f"{report_path.name} の specs_sha256 が凍結 fixture と不一致"
        )


def test_verdict_is_the_committed_fail_and_diagnostic() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert verdict["schema_version"] == "m2-accuracy-verdict/0.1"

    s_direct = verdict["categories"]["S_direct"]
    assert s_direct["status"] == "fail"
    assert s_direct["failures"], "S_direct の failures が空"
    assert all("voicing_false_alarm" in failure for failure in s_direct["failures"]), (
        "S_direct の fail 因子が voicing 単独でない（帰属の固定が崩れている）"
    )
    assert s_direct["repeats_bit_identical"] is True

    s_fullstack = verdict["categories"]["S_fullstack"]
    assert s_fullstack["status"] == "diagnostic_only"
    assert s_fullstack["repeats_bit_identical"] is True

    assert verdict["n_reports"] >= verdict["repeats_min"]
