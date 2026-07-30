"""M2c の commit 済み測定記録（verdict / report）の整合を CI で守る。

`docs/measurements/m2c_2026-07/` の verdict は run report を hash で pin するが、
通常の評価器テストは一時 report しか使わないため、commit 済み report が後から
再生成・編集されても CI は沈黙する（`test_m1real_committed_record.py` /
`test_m2b_committed_record.py` と同型の問題・#220 由来）。ここで pin ↔ 実ファイルの
sha256 と凍結 bars/external fixtures の digest を突き合わせ、「go/fail 判定が
存在しない bytes を指す」状態を機械検出する。

M2b との差分:

- ファイル名に `m2c_` prefix を付けない。`verdict.json` の
  `report_pins[].path_name` が実行時点の出力ファイル名 `run1.json` /
  `run2.json` をそのまま指しており、リネームすると pin の指す先が消える
  （README「命名が M2b と異なる理由」参照）。
- M2c のカテゴリ V_direct は外部素材（vocadito・実声・分離なし）を扱うため、
  M2b の合成 fixture 由来 `reference_frame_counts` の代わりに `clip_ids` /
  `external_fixtures_sha256` / `external_manifest_sha256` を持つ。凍結入力 pin も
  `m2_accuracy_bars.yaml` に加え `m2c_external_fixtures.yaml`（40 clip の
  audio/annotation sha256 事前登録）を対象に含める。

sha256 pin は report の bytes 自体の改変は検出するが、verdict 側の
metrics/run_ids 等が pin と無関係に独自に編集された場合は（pin 対象の
report が無傷である限り）沈黙する。そのため verdict が pinned reports から
verbatim 継承する値（run_ids・categories.metrics・clip_ids・outcomes・
external_manifest_sha256）は pinned reports への読み戻し照合で別途検証する。
mir_eval/スコアラーの再実行による数値の再導出はしない（commit 済み JSON 同士の
値比較のみで CI に環境依存 float を持ち込まない）。

意図的な帰結: bars/external fixtures を後から編集すると `bars_sha256` /
`external_fixtures_sha256` が食い違いこのテストが赤くなる。それは「fixture を
変えるなら再実測して verdict を作り直す」という運用コストを CI が可視化する
挙動であり、テストを緩めて通してはならない。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "docs" / "measurements" / "m2c_2026-07"
VERDICT = RECORD_DIR / "verdict.json"
BARS = REPO / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_bars.yaml"
SPECS = REPO / "tests" / "fixtures" / "melody_bench" / "m2_accuracy_specs.yaml"
EXTERNAL_FIXTURES = REPO / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"

# M2c-2 確定実測（vocadito 40 clip・V_direct）の verdict bytes の凍結 digest。
# dated 凍結記録なので、これが変わる正当な事象は「新しい dated 記録の作成」のみ
# （一方向規則）。
VERDICT_SHA256 = "806556d57feb34daadc234575e7b883e6a07f6bd4c55feb46c24e60aabc01255"

# m2c_external_fixtures.yaml の事前登録時点（commit 1cbd448）の凍結 digest。
# verdict/report が pin する値と一致するはず（下記テストで相互照合する）。
EXTERNAL_FIXTURES_SHA256 = "91b08852dabe3584de289c5ad5d9aafd7a40c8d3c2e14b2dbd8f599acc03b92f"


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
        "verdict.json の bytes が凍結 digest と不一致。dated 記録の編集は"
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
        # M1-real/M2b と異なり path_relative は null（未使用）。commit 済みコピーは
        # verdict と同じディレクトリに path_name と同名で置く規約（M2c は
        # run 実行時のファイル名をそのまま維持する — README 参照）。
        target = RECORD_DIR / pin["path_name"]
        assert target.is_file(), f"pinned report が存在しない: {target}"
        assert _sha256(target) == pin["sha256"], (
            f"{target.name} の bytes が verdict の pin と不一致。report を再生成した"
            "場合は verdict も作り直すこと（pin だけ残して中身を差し替えない）"
        )


def test_verdict_bars_and_external_fixtures_pins_match_frozen_fixtures() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert _sha256(BARS) == verdict["bars_sha256"], (
        "m2_accuracy_bars.yaml が verdict 生成時から変更されている。bars を編集した"
        "場合は再実測して verdict を作り直すこと（dated 判定を旧 bars の名で残さない）"
    )
    assert _sha256(EXTERNAL_FIXTURES) == EXTERNAL_FIXTURES_SHA256, (
        "m2c_external_fixtures.yaml の bytes が事前登録時点の凍結 digest と不一致"
        "（commit 1cbd448 由来の事前登録 pin が編集されている）"
    )
    assert verdict["external_fixtures_sha256"] == EXTERNAL_FIXTURES_SHA256, (
        "verdict.external_fixtures_sha256 が事前登録済み m2c_external_fixtures.yaml"
        "の凍結 digest と不一致"
    )
    assert verdict["categories"]["V_direct"]["external_fixtures_sha256"] == (
        EXTERNAL_FIXTURES_SHA256
    ), "verdict.categories.V_direct.external_fixtures_sha256 が凍結 digest と不一致"


def test_run_reports_pin_frozen_bars_specs_and_external_fixtures() -> None:
    verdict = json.loads(VERDICT.read_text())
    pins = verdict["report_pins"]
    # dated 記録の固定集合の凍結: report を増減するなら新しい dated 記録 + 新 verdict を作る運用。
    assert {pin["path_name"] for pin in pins} == {"run1.json", "run2.json"}

    bars_sha256 = _sha256(BARS)
    specs_sha256 = _sha256(SPECS)
    for report_path in _pinned_report_paths(verdict):
        report = json.loads(report_path.read_text())
        assert report["bars_sha256"] == bars_sha256, (
            f"{report_path.name} の bars_sha256 が凍結 fixture と不一致"
        )
        # specs_sha256 は M2c でも harness が発行する（外部素材経路でも参照される）。
        assert report["specs_sha256"] == specs_sha256, (
            f"{report_path.name} の specs_sha256 が凍結 fixture と不一致"
        )
        v_direct = report["categories"]["V_direct"]
        assert v_direct["external_fixtures_sha256"] == EXTERNAL_FIXTURES_SHA256, (
            f"{report_path.name} の categories.V_direct.external_fixtures_sha256 が"
            "凍結 m2c_external_fixtures.yaml と不一致"
        )


def test_verdict_derived_fields_match_pinned_reports() -> None:
    """verdict が pinned reports から verbatim 継承した値を読み戻しで照合する。

    sha256 pin は report ファイルの bytes 改変は検出するが、verdict 側の
    metrics/run_ids/clip_ids 等が pin と無関係に独自編集された場合は（pinned
    report 自体が無傷なら）他のテストは沈黙する。ここでは pin 順に実際の
    report を読み込み、verdict の該当フィールドと突き合わせる。再導出はせず、
    commit 済み JSON 同士の値比較のみを行う。
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
    assert set(verdict_categories) == {"V_direct"}, "M2c-2 は V_direct 単一カテゴリのはず"

    for category, verdict_cat in verdict_categories.items():
        report_cats = [report["categories"][category] for report in reports]
        report_metrics = [report_cat["metrics"] for report_cat in report_cats]
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

        # clip_ids: 外部素材経路の弁別単位。pinned reports 間で一致し、verdict とも一致する。
        report_clip_id_sets = [
            sorted(clip["clip_id"] for clip in report_cat["clips"]) for report_cat in report_cats
        ]
        assert all(ids == report_clip_id_sets[0] for ids in report_clip_id_sets), (
            f"{category}: reports 間で clip_ids が食い違っている"
        )
        assert verdict_cat["clip_ids"] == report_clip_id_sets[0], (
            f"{category}: verdict.clip_ids が pinned reports の clip_ids と不一致"
        )
        assert len(verdict_cat["clip_ids"]) == 40, (
            f"{category}: clip_ids の件数が想定の 40 件と不一致"
        )

        assert sorted(verdict_cat["outcomes"]) == sorted(
            {report_cat["outcome"] for report_cat in report_cats}
        ), f"{category}: verdict.outcomes が pinned reports の outcome 集合と不一致"
        assert verdict_cat["n_rows"] == len(reports), (
            f"{category}: verdict.n_rows が pinned report 数と不一致"
        )

        # external 特有: verdict/report の external_manifest_sha256 の相互一致。
        report_manifest_shas = {report_cat["external_manifest_sha256"] for report_cat in report_cats}
        assert len(report_manifest_shas) == 1, (
            f"{category}: reports 間で external_manifest_sha256 が食い違っている"
        )
        assert verdict_cat["external_manifest_sha256"] == next(iter(report_manifest_shas)), (
            f"{category}: verdict.external_manifest_sha256 が pinned reports と不一致"
        )


def test_verdict_is_the_committed_pass() -> None:
    verdict = json.loads(VERDICT.read_text())
    assert verdict["schema_version"] == "m2-accuracy-verdict/0.1"

    v_direct = verdict["categories"]["V_direct"]
    assert v_direct["status"] == "pass"
    assert v_direct["failures"] == []
    assert v_direct["repeats_bit_identical"] is True

    assert verdict["n_reports"] >= verdict["repeats_min"]
