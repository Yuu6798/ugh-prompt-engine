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

sha256 pin は report の bytes 自体の改変は検出するが、verdict 側の
metrics/run_ids 等が pin と無関係に独自に編集された場合は（pin 対象の
report が無傷である限り）沈黙する。そのため verdict が pinned reports から
verbatim 継承する値（run_ids・categories.metrics・reference_frame_counts・
outcomes）は pinned reports への読み戻し照合で別途検証する。mir_eval/
スコアラーの再実行による数値の再導出はしない（commit 済み JSON 同士の
値比較のみで CI に環境依存 float を持ち込まない）。

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

# 確定実測 run7 の verdict bytes の凍結 digest。dated 凍結記録なので、これが
# 変わる正当な事象は「新しい dated 記録の作成」のみ（一方向規則）。
VERDICT_SHA256 = "7f0e314df07a2f14e0cb8c6deb059134ba3ce8bd35aaeaf02dffb06a55bf5c51"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pinned_report_paths(verdict: dict) -> list[Path]:
    return [RECORD_DIR / pin["path_name"] for pin in verdict["report_pins"]]


def test_verdict_bytes_are_frozen() -> None:
    """verdict ファイル全体を bytes 単位で凍結する。

    フィールド単位の読み戻し照合（下記テスト群）は「どこが壊れたか」の診断
    粒度を与えるが、verdict 側の任意フィールドの事後編集（provenance・
    scorer pin・数値・構成の別なく）を漏れなく検出する終端はこの 1 本が担う。
    evaluator のコピー意味論をテスト側へ複製する field-by-field 照合の
    際限ない拡張はしない。
    """
    assert _sha256(VERDICT) == VERDICT_SHA256, (
        "m2b_verdict.json の bytes が凍結 digest と不一致。dated 記録の編集は"
        "禁止 — 正当な再実測なら新しい dated 記録ディレクトリを作ること"
    )


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
    verdict = json.loads(VERDICT.read_text())
    pins = verdict["report_pins"]
    # dated 記録の固定集合の凍結: report を増減するなら新しい dated 記録 + 新 verdict を作る運用。
    assert {pin["path_name"] for pin in pins} == {"m2b_run1.json", "m2b_run2.json"}

    bars_sha256 = _sha256(BARS)
    specs_sha256 = _sha256(SPECS)
    for report_path in _pinned_report_paths(verdict):
        report = json.loads(report_path.read_text())
        assert report["bars_sha256"] == bars_sha256, (
            f"{report_path.name} の bars_sha256 が凍結 fixture と不一致"
        )
        assert report["specs_sha256"] == specs_sha256, (
            f"{report_path.name} の specs_sha256 が凍結 fixture と不一致"
        )


def test_verdict_derived_fields_match_pinned_reports() -> None:
    """verdict が pinned reports から verbatim 継承した値を読み戻しで照合する。

    sha256 pin は report ファイルの bytes 改変は検出するが、verdict 側の
    metrics/run_ids/reference_frame_counts 等が pin と無関係に独自編集された
    場合は（pinned report 自体が無傷なら）他のテストは沈黙する。ここでは
    pin 順に実際の report を読み込み、verdict の該当フィールドと突き合わせる。
    再導出はせず、commit 済み JSON 同士の値比較のみを行う。
    """
    verdict = json.loads(VERDICT.read_text())
    reports = [json.loads(path.read_text()) for path in _pinned_report_paths(verdict)]

    assert verdict["run_ids"] == [report["run_id"] for report in reports], (
        "verdict.run_ids が pin 順の report.run_id と不一致（pin 順序と run_ids の対応が崩れている）"
    )

    verdict_categories = verdict["categories"]
    report_category_sets = [set(report["categories"]) for report in reports]
    assert all(cats == report_category_sets[0] for cats in report_category_sets), (
        "reports 間でカテゴリ集合が食い違っている"
    )
    assert set(verdict_categories) == report_category_sets[0], (
        "verdict のカテゴリ集合が report のカテゴリ集合と不一致"
        "（片方だけにカテゴリが増減している）"
    )

    for category, verdict_cat in verdict_categories.items():
        report_metrics = [report["categories"][category]["metrics"] for report in reports]
        assert verdict_cat["metrics"] == report_metrics, (
            f"{category}: verdict.metrics が pinned reports の metrics と不一致"
        )
        assert len(verdict_cat["metrics"]) == len(reports), (
            f"{category}: verdict.metrics の長さが n_reports と不一致"
        )
        assert report_metrics, f"{category}: pinned reports に metrics がない"
        assert verdict_cat["repeats_bit_identical"] is all(
            metrics == report_metrics[0] for metrics in report_metrics
        ), (
            f"{category}: repeats_bit_identical フラグが実際の repeat 間一致/不一致と矛盾"
        )

        ref_frame_counts = verdict_cat["reference_frame_counts"]
        for report in reports:
            report_cat = report["categories"][category]
            assert ref_frame_counts["ref_frame_count"] == report_cat["ref_frame_count"], (
                f"{category}: verdict.reference_frame_counts.ref_frame_count が "
                f"{report['run_id']} の ref_frame_count と不一致"
            )
            assert ref_frame_counts["ref_voiced_frame_count"] == report_cat["ref_voiced_frame_count"], (
                f"{category}: verdict.reference_frame_counts.ref_voiced_frame_count が "
                f"{report['run_id']} の ref_voiced_frame_count と不一致"
            )

        assert sorted(verdict_cat["outcomes"]) == sorted(
            {report["categories"][category]["outcome"] for report in reports}
        ), f"{category}: verdict.outcomes が pinned reports の outcome 集合と不一致"
        assert verdict_cat["n_rows"] == len(reports), (
            f"{category}: verdict.n_rows が pinned report 数と不一致"
        )


def test_verdict_is_the_committed_fail_and_diagnostic() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert verdict["schema_version"] == "m2-accuracy-verdict/0.1"

    s_direct = verdict["categories"]["S_direct"]
    assert s_direct["status"] == "fail"
    # dated 凍結記録なので期待値は verbatim 定数で凍結する。metrics+bars からの
    # 再導出（evaluator のバー適用ロジックの複製）はしない。
    assert s_direct["failures"] == [
        "repeat[0] voicing_false_alarm 0.2588 > max_vfa 0.15",
        "repeat[1] voicing_false_alarm 0.2588 > max_vfa 0.15",
    ]
    assert s_direct["repeats_bit_identical"] is True

    s_fullstack = verdict["categories"]["S_fullstack"]
    assert s_fullstack["status"] == "diagnostic_only"
    assert s_fullstack["repeats_bit_identical"] is True

    assert verdict["n_reports"] >= verdict["repeats_min"]
