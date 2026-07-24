"""run_melody_observability.py — M1 観測成立帯域ハーネス（評価 CLI スケルトン）。

全 fixture × 全経路について `melody.observability` のゲート指標を採取し、
「どの経路がどの入力帯で `sufficient` になるか」の表を JSON で書き出す。
**比較（M3）は行わない**——観測が成立するか否かだけを測る（設計 §4）。

2 つのモード:

- 合成 fixture（既定・CI 安全）: `tests/fixtures/melody_bench/synthesis_specs.yaml`
  を決定論合成し、その `input_kind` の経路のうち**利用可能な抽出器を持つ経路だけ**
  を回す。本環境で回るのは pyin 経路（core librosa）のみ。CREPE / Melodia /
  Demucs 経路は optional 依存が未導入なら `unavailable` として表に記録する
  （fail ではない・slow-lane 隔離）。
- 外部素材（`--external <manifest.json>`）: 正解 MIDI を持たない実素材
  （Suno vocals stem 等）の観測可能性のみを測る slow/manual lane 用。

さらに `--evaluate-go-bar <report.json> [...]` は、上記 external モードで得た
report（n>=2 の繰り返し実行）に対し registry.yaml の凍結済み `m1_real_go_bar` を
機械適用し Go/No-Go を出す（抽出は行わない・独立モード）。

使い方::

    python scripts/run_melody_observability.py --out /tmp/melody_obs.json
    python scripts/run_melody_observability.py --external ext.json --out /tmp/ext_obs.json
    python scripts/run_melody_observability.py --evaluate-go-bar run1.json run2.json --out verdict.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_melody_bench import SPECS_PATH, build_signal, load_specs  # noqa: E402

from svp_rpe.melody.extractors import observe_assist_notes, observe_via_route  # noqa: E402
from svp_rpe.melody.observability import (  # noqa: E402
    ObservabilityThresholds,
    assess_observability,
    gate_reasons,
)
from svp_rpe.melody.routing import select_routes  # noqa: E402
from svp_rpe.rpe.learned import LearnedModelUnavailable  # noqa: E402

REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"

# 本ハーネスが解釈できる registry スキーマ契約。新スキーマで閾値の意味論が
# 変わった場合に v0.1 の解釈で結果を publish しないよう、registry を消費する前に
# fail-closed で検証する（Codex 指摘・AGENTS §8）。
_EXPECTED_REGISTRY_SCHEMA = "melody-bench/0.1"


def _require_registry_schema(registry: Dict[str, Any]) -> None:
    version = registry.get("schema_version")
    if version != _EXPECTED_REGISTRY_SCHEMA:
        raise ValueError(
            f"unsupported melody_bench registry schema_version {version!r}; "
            f"expected {_EXPECTED_REGISTRY_SCHEMA} (fail-closed)"
        )


def _load_registry(registry_path: "Path | None" = None) -> "tuple[Dict[str, Any], str]":
    """registry の bytes を**一度だけ**読み、hash し、同じ buffer から parse する。

    thresholds（observation_gate）と fixture metadata（fixtures / external_fixtures）
    を別々に open すると、間に registry が書き換わった場合に食い違う（TOCTOU）。
    さらに publish 済み report が registry hash / 閾値スナップショットを pin しないと、
    passing（reasons が空の sufficient）行がどのゲート値・登録で出たか後から証明
    できない。single read で consistency を保証し、`registry_sha256` を返して report に
    pin できるようにする（Codex 指摘・AGENTS §8。manifest/audio bytes 凍結と同型）。

    `registry_path` 未指定時はモジュール globals の `REGISTRY_PATH` を**呼び出し時**に
    解決する（default 引数に束縛するとテストの monkeypatch が効かない）。
    """
    if registry_path is None:
        registry_path = REGISTRY_PATH
    data = Path(registry_path).read_bytes()
    registry = yaml.safe_load(data)
    _require_registry_schema(registry)
    return registry, hashlib.sha256(data).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    """`text` を `path` へ atomic に書く（同一ディレクトリの temp file → os.replace）。

    `write_text` は最終パスを直接 truncate してから書くため、書き込み途中の中断や
    disk error で partial な report が残り、既存の完全な成果物を破壊しうる
    （後の provenance / Go-No-Go 読み手が corrupt 出力を消費する）。同一ディレクトリ
    （= 同一FS）の temp file へ全 bytes を書き切ってから os.replace で publish する
    （Codex 指摘・AGENTS §8）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _unique_id_map(entries: List[Dict[str, Any]], where: str) -> Dict[str, str]:
    """`entries` の id → input_kind マップを、重複 id を fail-closed で作る。

    dict 内包表記は重複 id を黙って last-wins で上書きするため、事前登録が
    曖昧（同一 id に別 input_kind）でも slow-lane 実行が通ってしまう。重複を
    検出して reject し、曖昧な事前登録の下に観測を publish させない（Codex 指摘）。
    """
    ids = [entry["id"] for entry in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(
            f"duplicate fixture id(s) in registry.yaml {where}: {duplicates} (fail-closed)"
        )
    return {entry["id"]: entry["input_kind"] for entry in entries}

# 抽出器名 → PyPI distribution 名（provenance の installed version 採取用）。
_EXTRACTOR_DIST = {
    "pyin": "librosa",
    "crepe": "crepe",
    "melodia": "essentia",
    "basic_pitch": "basic-pitch",
}

# 抽出器名 → source_model に含まれるべきトークン（各 extractor モジュールの
# source_model 定数と対応。librosa:pyin / crepe:predict /
# essentia:PredominantPitchMelodia / spotify:basic_pitch）。measured 行の source_model が
# 期待抽出器ファミリのトークンを（大小無視で）含まなければ、別抽出器の payload を
# 貼ったすり替えとして弾く（source_model は #17 の extractor ラベルと違い、実測に
# 使った抽出器を暴く唯一の provenance フィールド・Codex 指摘）。
_EXTRACTOR_SOURCE_MODEL_TOKEN = {
    "pyin": "pyin",
    "crepe": "crepe",
    "melodia": "melodia",
    "basic_pitch": "basic_pitch",
}


def _dist_version(dist: str) -> "str | None":
    """PyPI distribution の installed version を best-effort で返す（未導入なら None）。"""
    import importlib.metadata as _md

    try:
        return _md.version(dist)
    except Exception:
        return None


def _extractor_version(extractor: str) -> "str | None":
    """抽出器の installed package version を best-effort で返す（未導入なら None）。"""
    dist = _EXTRACTOR_DIST.get(extractor)
    return _dist_version(dist) if dist else None


def _preprocessing_provenance(route: Any) -> "Dict[str, Any] | None":
    """分離前処理（Demucs）の provenance。分離不要な経路は None。

    同一 audio_sha256 でも Demucs のパッケージ/モデル/重みが違えば vocals stem が
    変わり下流のピッチ結果も変わるため、`requires_separation` 行に分離器の
    モデル名と installed version を記録する（Codex 指摘・AGENTS §8）。
    stem hash レベルの provenance は observe_via_route が stem を露出する必要が
    あり、Demucs 不在では検証できないため本 PR では見送る。
    """
    if not getattr(route, "requires_separation", False):
        return None
    from svp_rpe.io.source_separator import DEFAULT_MODEL

    return {
        "preprocessing": route.preprocessing,
        "separation_model": DEFAULT_MODEL,
        "separation_version": _dist_version("demucs"),
    }


def load_thresholds(registry_path: Path = REGISTRY_PATH) -> ObservabilityThresholds:
    registry, _ = _load_registry(registry_path)
    return ObservabilityThresholds.from_registry(registry["observation_gate"])


def _run_routes_on_file(
    audio_path: str,
    input_kind: str,
    thresholds: ObservabilityThresholds,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for route in select_routes(input_kind):
        if not route.applies:
            # 旋律不在入力: 抽出せず not_observed へ落とす（設計 §4.2）。
            rows.append(
                {
                    "route": route.name,
                    "extractor": route.extractor,
                    "outcome": "not_observed_by_routing",
                    "report": None,
                }
            )
            continue
        preprocessing = _preprocessing_provenance(route)
        try:
            observation = observe_via_route(audio_path, route)
        except LearnedModelUnavailable as exc:
            unavailable_row: Dict[str, Any] = {
                "route": route.name,
                "extractor": route.extractor,
                "outcome": "unavailable",
                "detail": str(exc).splitlines()[0],
                "report": None,
            }
            if preprocessing is not None:
                unavailable_row["preprocessing"] = preprocessing
            rows.append(unavailable_row)
            continue
        # assist 抽出器が宣言されていれば（full_mix の basic-pitch × Melodia など）、
        # 補助抽出器を同一音声に走らせて reference notes を採り、cross_extractor_
        # agreement を実測する（設計 §4.2「一致時のみ」）。assist が未導入なら
        # agreement は null のまま（graceful・slow-lane 隔離）。
        reference_notes = None
        assist_status = None
        assist_source_model = None
        if route.assist:
            try:
                reference_notes, assist_source_model = observe_assist_notes(
                    audio_path, route, thresholds
                )
                assist_status = "measured"
            except LearnedModelUnavailable:
                assist_status = "unavailable"
        report = assess_observability(
            observation, thresholds, reference_notes=reference_notes
        )
        # provenance: 同一 audio_sha256 でも抽出器ビルド/モデル差で結果が変わりうる
        # ため、主・補助抽出器の source_model と installed version を行に記録する。
        row: Dict[str, Any] = {
            "route": route.name,
            "extractor": route.extractor,
            "outcome": report.status,
            "report": report.to_dict(),
            "source_model": observation.source_model,
            "extractor_version": _extractor_version(route.extractor),
        }
        if preprocessing is not None:
            row["preprocessing"] = preprocessing
        if route.assist:
            row["assist_extractor"] = route.assist
            row["assist_status"] = assist_status
            row["assist_source_model"] = assist_source_model
            row["assist_extractor_version"] = _extractor_version(route.assist)
        rows.append(row)
    return rows


def run_synthetic(
    thresholds: "ObservabilityThresholds | None" = None,
) -> Dict[str, Any]:
    specs = load_specs()
    # registry を single read（bytes→hash→parse）。thresholds も fixture metadata も
    # この同じ read から作り、registry_sha256 を report に pin する。
    registry, registry_sha256 = _load_registry()
    thresholds_source = "registry" if thresholds is None else "override"
    if thresholds is None:
        thresholds = ObservabilityThresholds.from_registry(registry["observation_gate"])
    fixture_kinds = _unique_id_map(registry["fixtures"], "fixtures")
    # expect_status を全 synthetic fixture に必須化（fail-closed）。`.get` で欠落を
    # 黙って None にすると、期待値を持たない fixture が Go/No-Go JSON に紛れ込み、
    # registry の typo/記入漏れが publish 前に検出されない（Codex 指摘・設計 §5）。
    _VALID_EXPECT = {"sufficient", "insufficient"}
    expect: Dict[str, str] = {}
    for fixture in registry["fixtures"]:
        status = fixture.get("expect_status")
        if status not in _VALID_EXPECT:
            raise ValueError(
                f"synthetic fixture {fixture['id']!r} has invalid/missing expect_status "
                f"{status!r}; must be one of {sorted(_VALID_EXPECT)} (fail-closed 事前登録)"
            )
        expect[fixture["id"]] = status

    # fail-closed: 全ての合成 spec id は registry に事前登録されていなければならない。
    # 未登録 id を既定の input_kind へ黙って分類すると、事前登録の期待値を持たない
    # ケースが Go/No-Go 出力へ紛れ込む（設計 §5 事前登録厳守）。推論せず reject する。
    unregistered = [fid for fid in specs["fixtures"] if fid not in fixture_kinds]
    if unregistered:
        raise ValueError(
            f"synthesis spec ids without a registry.yaml fixtures entry: {unregistered}. "
            "全ての spec id を registry へ事前登録すること（input_kind 推論は禁止）。"
        )

    # 合成波形の provenance pin（registry.provenance.waveform_sha256）。external モードの
    # audio_sha256 と対称に、synthetic Go/No-Go も「どの音を観測したか」を report へ pin
    # する。registry_sha256 だけでは synthesis_specs.yaml / build_signal が drift しても
    # 検出できず、dated な gate 行が再現・stale 検出不能になる（Codex 指摘・AGENTS §8）。
    # 各 fixture の raw float32 サンプル hash を registry pin と照合し fail-closed。
    waveform_pins: Dict[str, str] = registry.get("provenance", {}).get("waveform_sha256", {})

    results: Dict[str, Any] = {
        "mode": "synthetic",
        "registry_sha256": registry_sha256,
        "thresholds_source": thresholds_source,
        # ★実際に assess へ渡した閾値そのものを pin する。registry スナップショットを
        # そのまま載せると、caller が thresholds override を渡した場合に「載っているゲート」
        # と「判定に使ったゲート」が食い違い、report が使っていないゲートを主張する
        # （Codex 指摘・AGENTS §8）。asdict は default 値込みの解決済み全フィールドを返す
        # ため registry snapshot より厳密。thresholds_source で由来（registry/override）も明示。
        "observation_gate": asdict(thresholds),
        "fixtures": {},
    }
    with tempfile.TemporaryDirectory(prefix="melody-bench-") as tmp:
        for fid in specs["fixtures"]:
            y, sr = build_signal(fid, specs)
            # raw float32 サンプルの hash（registry pin と同一定義: soundfile の
            # コンテナエンコードに依存しない生サンプル指紋）。spec/builder drift 検出。
            waveform_sha256 = hashlib.sha256(
                np.asarray(y, dtype=np.float32).tobytes()
            ).hexdigest()
            expected_wf = waveform_pins.get(fid)
            if expected_wf is None:
                raise ValueError(
                    f"synthetic fixture {fid!r} lacks a registry provenance.waveform_sha256 "
                    "pin (fail-closed 事前登録)"
                )
            if expected_wf != waveform_sha256:
                raise ValueError(
                    f"synthetic fixture {fid!r} waveform sha256 mismatch: "
                    f"{waveform_sha256} != registry {expected_wf}. "
                    "synthesis_specs.yaml / build_signal が drift している — registry の "
                    "waveform_sha256 を更新し dated 再実測すること。"
                )
            wav_path = Path(tmp) / f"{fid}.wav"
            sf.write(wav_path, y, sr, subtype="FLOAT")
            input_kind = fixture_kinds[fid]
            rows = _run_routes_on_file(str(wav_path), input_kind, thresholds)
            results["fixtures"][fid] = {
                "input_kind": input_kind,
                "expect_status": expect.get(fid),
                "waveform_sha256": waveform_sha256,  # 観測した音の pin（external audio_sha256 と対称）
                "routes": rows,
            }
    return results


def run_external(
    manifest_path: Path, thresholds: "ObservabilityThresholds | None" = None
) -> Dict[str, Any]:
    """外部素材 manifest（[{id, path, input_kind, audio_sha256?}]）の観測可能性を測る。

    provenance（AGENTS §8）: どの bytes を観測したかを後の slow-lane 実測が証明
    できるよう、各素材の実パスと content hash（audio_sha256）を出力へ記録する。
    manifest が期待 hash を持つ場合は照合し、不一致なら fail-closed で reject
    （同一 id で別 WAV が差し替わる silent swap を防ぐ）。manifest 自体の hash も
    記録する。
    """
    # manifest の bytes を一度だけ読み、その bytes を hash し、同じ buffer から
    # JSON を parse する。別々に open すると、pin する manifest_sha256 と実際に
    # entries を供給した manifest がズレる TOCTOU が残る（Codex 指摘。audio bytes
    # の凍結と同型）。
    manifest_bytes = Path(manifest_path).read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    entries = json.loads(manifest_bytes)
    # registry を single read（bytes→hash→parse）。thresholds も external_fixtures も
    # 同じ read から作り、registry_sha256 を report に pin する。
    registry, registry_sha256 = _load_registry()
    thresholds_source = "registry" if thresholds is None else "override"
    if thresholds is None:
        thresholds = ObservabilityThresholds.from_registry(registry["observation_gate"])

    # fail-closed: 各 manifest entry の id は registry.yaml の external_fixtures に
    # 事前登録され、input_kind も登録値と一致していなければならない。typo や
    # ミスラベル（例: suno_vocals_stem を clear_lead と誤記）で誤った経路集合を
    # 走らせ、未登録/不整合な fixture の下に一見妥当な観測を publish するのを防ぐ
    # （合成側 fail-closed と対称・設計 §5）。
    registered = _unique_id_map(registry.get("external_fixtures", []), "external_fixtures")
    seen_ids: set[str] = set()

    # 相対 path は manifest の位置を基準に解決する。cwd 基準だと、可搬 manifest を
    # 別ディレクトリから起動したとき launch dir の同名ファイルを観測して一見妥当な
    # 結果を publish しうる（Codex 指摘）。解決後の正規化パスを記録する。
    manifest_dir = Path(manifest_path).resolve().parent

    results: Dict[str, Any] = {
        "mode": "external",
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "registry_sha256": registry_sha256,
        "thresholds_source": thresholds_source,
        # ★実際に assess へ渡した閾値そのものを pin（override 時も嘘をつかない）。
        # 詳細は run_synthetic の同フィールド注記を参照（Codex 指摘・AGENTS §8）。
        "observation_gate": asdict(thresholds),
        # ★この 1 回の external 実行を一意に識別する run provenance。決定論パイプラインでは
        # n>=2 の repeats が意味を持つのは**独立した実行**の揺れを見るとき（設計 §3.1
        # 「実行揺れ確認」）だけで、run1.json を run2.json へコピーすれば別パス・同一 bytes で
        # path-dedup（#6）を通過し 1 回の抽出が repeats_min=2 を満たしてしまう。各実行で
        # 新規発行する run_id を評価器が「存在必須・report 間で相互に distinct」と要求する
        # ことで、コピー由来の偽 repeats を弾く（Codex 指摘・#45）。
        "run_id": uuid.uuid4().hex,
        "fixtures": {},
    }
    with tempfile.TemporaryDirectory(prefix="melody-ext-") as tmp:
        for entry in entries:
            entry_id = entry["id"]
            if entry_id in seen_ids:
                raise ValueError(f"duplicate external fixture id {entry_id!r} in manifest")
            seen_ids.add(entry_id)
            if entry_id not in registered:
                raise ValueError(
                    f"external fixture id {entry_id!r} is not pre-registered in "
                    "registry.yaml external_fixtures (fail-closed)"
                )
            if entry["input_kind"] != registered[entry_id]:
                raise ValueError(
                    f"external fixture {entry_id!r} input_kind {entry['input_kind']!r} "
                    f"!= registered {registered[entry_id]!r}"
                )

            raw_path = Path(entry["path"])
            resolved = raw_path if raw_path.is_absolute() else (manifest_dir / raw_path)
            resolved = resolved.resolve()

            # 観測する bytes と pin する hash を一致させる: entry のバイト列を一度だけ
            # 読み、その hash を取り、**同じバイト列**を temp file へ凍結して観測する。
            # 別々に 2 回 open すると、間にファイルが再生成・差し替えられた場合に pin
            # と観測波形がズレる TOCTOU が残る（Codex 指摘）。
            data = resolved.read_bytes()
            audio_sha256 = hashlib.sha256(data).hexdigest()
            expected = entry.get("audio_sha256")
            if expected is not None and expected != audio_sha256:
                raise ValueError(
                    f"external audio {entry_id!r} sha256 mismatch: "
                    f"{audio_sha256} != manifest {expected}"
                )
            frozen = Path(tmp) / f"{entry_id}{resolved.suffix or '.wav'}"
            frozen.write_bytes(data)

            rows = _run_routes_on_file(str(frozen), entry["input_kind"], thresholds)
            results["fixtures"][entry_id] = {
                "input_kind": entry["input_kind"],
                "expect_status": None,  # 正解なし実素材（観測可能性のみ）
                "audio_path": str(resolved),  # 正規化パス
                "audio_sha256": audio_sha256,
                "routes": rows,
            }
    return results


def _fixture_route_outcomes(report: Dict[str, Any], fixture_id: str) -> Dict[str, str]:
    """`report` 中の `fixture_id` について route 名 → outcome の map を返す。"""
    info = report["fixtures"][fixture_id]
    return {row["route"]: row["outcome"] for row in info["routes"]}


def _fixture_route_rows(report: Dict[str, Any], fixture_id: str) -> Dict[str, Dict[str, Any]]:
    """`report` 中の `fixture_id` について route 名 → 行 dict 全体の map を返す。"""
    info = report["fixtures"][fixture_id]
    return {row["route"]: row for row in info["routes"]}


def _route_provenance(row: Dict[str, Any]) -> "tuple":
    """route 行の model provenance 署名（source_model / extractor_version / 分離器）を返す。

    n>=2 の repeats を「安定」と数える前に、抽出器/分離器の model stack が同一である
    ことを証明するために使う。マシン跨ぎや途中の CREPE/Demucs/Essentia アップグレードで
    provenance が変われば、同一 stack 下の再現でないので repeats と見なせない（Codex 指摘・
    設計 §2.3 provenance pin）。分離前処理は separation_model/version に加え、存在すれば
    分離器の重み hash（separation_weights_sha256）と vocals stem hash（stem_sha256）まで
    見る。同一 htdemucs_ft/version でも別 weights や再生成 stem なら別 run として弾く
    （これらの hash の **emit** は実 Demucs を要する machine-dependent な slow-lane 課題で
    現状 harness は未出力。評価器は「存在すれば比較」で forward-compat とし、emit されたら
    自動で穴が塞がる・Codex 指摘）。
    """
    preprocessing = row.get("preprocessing")
    if isinstance(preprocessing, dict):
        separation = (
            preprocessing.get("preprocessing"),
            preprocessing.get("separation_model"),
            preprocessing.get("separation_version"),
            preprocessing.get("separation_weights_sha256"),
            preprocessing.get("stem_sha256"),
        )
    else:
        separation = (preprocessing, None, None, None, None)
    return (
        row.get("source_model"),
        row.get("extractor_version"),
        row.get("assist_source_model"),
        row.get("assist_extractor_version"),
        separation,
    )


def _evaluator_code_paths() -> "List[Path]":
    """verdict を解釈するコード（評価器＋依存モジュール）の resolved パス集合。

    `_evaluator_code_sha256`（provenance digest）と `--out` 衝突保護の双方が同じ集合を
    共有し、hash した直後にそのコードを --out で上書き破壊するのを防ぐ。
    """
    import svp_rpe.melody.observability as _obs
    import svp_rpe.melody.routing as _routing

    return [
        Path(__file__).resolve(),
        Path(_obs.__file__).resolve(),
        Path(_routing.__file__).resolve(),
    ]


def _evaluator_code_sha256() -> str:
    """verdict を解釈するコード（評価器＋依存モジュール）の digest。

    verdict は入力 report（report_pins）と registry を pin するが、それらを解釈する
    **コード**（`evaluate_m1_real_go_bar` / `select_routes` の route matrix / `gate_reasons`
    の閾値ロジック）が変われば、同じ pin から別 verdict が再導出されうる。この digest を
    verdict に載せることで、別 checkout で候補経路や scoring が変わったのに同じ report_pins/
    registry_sha256 で別結果になる stale を検出可能にする（Codex 指摘・AGENTS §8）。
    """
    digest = hashlib.sha256()
    for path in sorted(_evaluator_code_paths(), key=lambda p: p.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _is_stably_sufficient(reports: List[Dict[str, Any]], fixture_id: str, route: str) -> bool:
    """全 report でその route が観測され、かつ全て outcome=='sufficient' なら True。

    route が一部の report に存在しない、または unavailable 等の非観測 outcome を
    1 回でも含む場合は False（実行揺れ・環境差を素通りさせない）。
    """
    for report in reports:
        outcomes = _fixture_route_outcomes(report, fixture_id)
        if outcomes.get(route) != "sufficient":
            return False
    return True


def _is_ever_sufficient(reports: List[Dict[str, Any]], fixture_id: str, route: str) -> bool:
    """いずれか 1 つの report で outcome=='sufficient' なら True（保守的な偽陽性判定）。"""
    return any(
        _fixture_route_outcomes(report, fixture_id).get(route) == "sufficient"
        for report in reports
    )


# ゲート判定が下りた outcome（観測が成立し sufficient/insufficient を返した）。
# unavailable / not_observed_by_routing / not_applicable は「未観測」で、これらは
# その経路でゲートが実行されていないことを意味する。
_GATE_OUTCOMES = frozenset({"sufficient", "insufficient"})


def _is_measured(reports: List[Dict[str, Any]], fixture_id: str, route: str) -> bool:
    """全 report でその route が gate outcome（sufficient/insufficient）で観測されたか。

    route が一部の report で欠落・未観測（unavailable 等）なら False。負の対照で
    「その経路を一度も測っていない」状態を「偽陽性なし」と誤認しないための判定に使う。
    """
    for report in reports:
        if _fixture_route_outcomes(report, fixture_id).get(route) not in _GATE_OUTCOMES:
            return False
    return True


def evaluate_m1_real_go_bar(
    reports: List[Dict[str, Any]],
    registry: Dict[str, Any],
    registry_sha256: "str | None" = None,
) -> Dict[str, Any]:
    """凍結済み `registry["m1_real_go_bar"]` を `reports`（external モード n>=2 回）へ機械適用する。

    本関数は事前登録済みバー（≥3/4 sufficient・偽陽性 0）を**そのまま機械算出するだけ**
    で、閾値を緩めたり実測データを見て調整したりしない（`one_way_rule` の延長・設計 §5）。

    `registry_sha256` を渡した場合（CLI 経路では `_load_registry()` が返す、いま
    バーを load した registry の hash）、それを authoritative reference として全
    report の pin と照合する。これがないと、古い registry で測った stale な report
    同士が互いに一致しさえすれば今日の `m1_real_go_bar` で評価され、verdict が測定
    時と別の凍結バーに紐づいてしまう（Codex 指摘・バーの焼き込み保証）。None の
    場合（survivor ロジックの純粋単体テスト等）は report 間の相互一致で代替する。

    fail-closed 条件（誤った Go 判定を出さないための拒否）:

    - `reports` が空
    - いずれかの report の `mode` が ``"external"`` でない（synthetic report を
      混ぜて判定できない）
    - `len(reports) < m1_real_go_bar.repeats_min`（凍結ルールは n>=2 を要求する。
      CLI の ``nargs="+"`` で単一 report が渡っても 1 回実行の verdict を publish
      しない・Codex 指摘）
    - いずれかの report が非空 str の `run_id` を欠く、または report 間で `run_id` が
      重複（決定論パイプラインでは n>=2 が意味を持つのは独立実行のときだけで、run1.json を
      別パスへコピーすれば path-dedup を通過し 1 回の抽出で repeats_min を満たせてしまう。
      run_external が実行ごとに発行する run_id の存在＋相互 distinct を要求してコピー由来の
      偽 repeats を弾く・#45・Codex 指摘）
    - `registry_sha256` 指定時、いずれかの report の pin がその hash と不一致
      （渡されない場合は複数 report 間の `registry_sha256` 相互一致を要求）
    - 複数 report 間で `observation_gate` が食い違う（異なる閾値下で測定した
      report を混ぜて判定するのは無効）
    - `m1_real_go_bar` の positive_ids / negative_ids のいずれかが、**いずれかの**
      report の fixtures に欠けている（全 pre-registered fixture を n>=2 で測定
      してからでないと verdict を主張できない）
    - 同一 fixture id の `audio_sha256` が report 間で欠落・不一致（manifest は
      ``audio_sha256: null`` を許すが、同一 id で別素材を使った 2 run を「同一素材の
      n>=2 repeats」と見なせない・Codex 指摘）
    - いずれかの report の `thresholds_source` が ``"registry"`` でない、または
      `observation_gate` が凍結 registry の解決ゲートと不一致（override（緩い）ゲートで
      測定した report では凍結バーの判定を publish しない・Codex 指摘）
    - いずれかの fixture の report 自己申告 `input_kind` が registry の凍結 kind と
      不一致（易しい matrix へのすり替えを防ぐ・Codex 指摘）
    - いずれかの fixture が、**registry の凍結 input_kind** で回すべき全経路
      （`select_routes`）を report に持たない（stale/truncated report で一部経路が
      丸ごと欠けると、残った経路だけで go が出うる・docs §6.3 の完全 matrix を要求・
      Codex 指摘）
    - 別素材であるべき go-bar fixture が同一 `audio_sha256` を共有（fixture 間で素材が
      重複すると「4 別素材の ≥3/4」が実質 1 素材で満たされる・Codex 指摘）
    - `m1_real_go_bar` の positive_ids / negative_ids が非一意、両者が重複、
      positive 本数が `total_positive` と不一致、negative_ids が空、`repeats_min < 2`、
      `min_positive_sufficient` が [1, total_positive] 外、または
      `max_negative_false_positive` が [0, len(negatives)) 外（凍結バー自身の typo で
      二重計上・素材数不足・偽陽性ガード欠落/空洞化・n<2 の verdict を許さない。具体的
      凍結値 min=3/max=0 は事前登録テストで pin・#26 参照・Codex 指摘）
    - いずれかの fixture の routes に同名 route 行が重複（last-wins で失敗/未観測行を
      隠蔽させない・Codex 指摘）
    - measured（gate outcome）な route 行が provenance フィールド（source_model /
      extractor_version、分離経路は separation_model/version）を欠く、または同一
      fixture×route の provenance が repeats 間で不一致（欠落を「一致」と扱わず、別
      model stack の run を n>=2 と誤認しない・設計 §2.3・Codex 指摘）
    - measured な route 行の `extractor` / preprocessing が凍結 route 定義
      （`select_routes`）と不一致、または `source_model` が期待抽出器ファミリのトークンを
      含まない（経路ラベル/provenance と実測抽出器のすり替えを許さない・Codex 指摘）
    - payload の gate metric が非有限（NaN/Infinity）または範囲外（率/被覆は [0,1]、
      ノート数は非負 int でない）（`NaN < min` を利用した passing すり抜けを許さない・Codex 指摘）
    - measured な route 行が根拠 report payload / gate metrics を欠く、`report.status`
      が `outcome` と不一致、payload metrics から凍結ゲートで**厳密に**再導出した status が
      `outcome` と**両方向いずれかで**不一致（`to_dict` が metrics を丸めず厳密値を保存する
      ため丸め起因の曖昧さは無く、outcome=sufficient の水増しも outcome=insufficient で隠した
      実 sufficient＝特に negative の偽陽性隠蔽も曖昧さなく弾く）、または payload 自身の
      `route` が行の route と不一致（`gate_reasons` を assess_observability と共有・Codex 指摘）

    候補経路は positive fixture の registry 凍結 kind が規定する matrix に限定し、
    report に混入した非登録経路は verdict に効かせない（Codex 指摘）。

    ゲート outcome は ``"sufficient"`` / ``"insufficient"`` のみを指す
    （``"unavailable"`` / ``"not_observed_by_routing"`` / ``"not_applicable"`` は
    非観測として扱い、stably/ever のいずれの sufficient にも数えない）。survive には
    さらに **全 positive・全 negative でその経路が実測（gate outcome）済み**である
    ことを要求する（未観測の positive/negative がある経路は matrix 未完 or 偽陽性
    未証明で survive させない・``positive_unmeasured_ids`` / ``negative_unmeasured_ids``
    に記録・Codex 指摘）。なお `--evaluate-go-bar` CLI は同一 report ファイルの二重
    指定を resolve 済みパスの重複として弾き、1 回の実行で repeats_min を満たせない
    （`_resolve_unique_report_paths`）。

    各候補 route（positive fixture 群で観測された route 名の和集合）について:

    - ``stably_sufficient``: 全 report でその route が観測され、かつ全て
      outcome=='sufficient'
    - ``ever_sufficient``: いずれか 1 回でも outcome=='sufficient'
      （偽陽性判定は保守的に「1 回でも旋律を幻視したら失格」とする）
    - ``pos_sufficient`` = positive_ids のうち stably_sufficient な数
    - ``neg_false_positive`` = negative_ids のうち ever_sufficient な数
    - ``surviving`` = pos_sufficient >= min_positive_sufficient かつ
      neg_false_positive <= max_negative_false_positive

    verdict は 3 値: surviving な route が 1 本でもあれば ``"go"``。無い場合、**全**
    候補経路が全 fixture×route で実測（gate outcome）済みなら ``"no_go"``（強化版
    No-Go・設計 §4.4「全経路」）、1 つでも未測定の候補経路があれば ``"inconclusive"``
    （その経路がバーを満たす可能性を排除できておらず「生き残りなし」を証明していない・
    optional 依存欠如の unavailable 等）。"partial" 帯の解釈は人間の記録判断に委ねる
    （判定に必要な per-route データを返り値にすべて含める）。
    """
    if not reports:
        raise ValueError("evaluate_m1_real_go_bar: reports is empty (fail-closed)")

    for idx, report in enumerate(reports):
        mode = report.get("mode")
        if mode != "external":
            raise ValueError(
                f"evaluate_m1_real_go_bar: reports[{idx}] has mode {mode!r}, "
                "expected 'external' (fail-closed; M1-real Go bar は external 実測のみを対象とする)"
            )

    bar = registry["m1_real_go_bar"]
    positive_ids: List[str] = list(bar["positive_ids"])
    negative_ids: List[str] = list(bar["negative_ids"])
    min_positive_sufficient = bar["min_positive_sufficient"]
    max_negative_false_positive = bar["max_negative_false_positive"]
    repeats_min = bar["repeats_min"]
    # 凍結バー自身の整合性: positive_ids / negative_ids は互いに重複なし・disjoint で
    # なければならない。registry の typo で positive_ids に同一 id が 2 回入ると、
    # fixture_audio_hash は id で 1 素材に畳まれる一方で pos_sufficient はリストを
    # そのまま数え、1 素材が二重計上されて 4 未満の別素材で ≥3/4 を満たしうる
    # （Codex 指摘・凍結バーの well-formed 性を守る config 整合チェック）。
    if len(set(positive_ids)) != len(positive_ids):
        raise ValueError(
            f"evaluate_m1_real_go_bar: m1_real_go_bar.positive_ids に重複 {positive_ids}; "
            "1 素材の二重計上を防ぐため一意でなければならない (fail-closed)"
        )
    if len(set(negative_ids)) != len(negative_ids):
        raise ValueError(
            f"evaluate_m1_real_go_bar: m1_real_go_bar.negative_ids に重複 {negative_ids} (fail-closed)"
        )
    overlap = sorted(set(positive_ids) & set(negative_ids))
    if overlap:
        raise ValueError(
            f"evaluate_m1_real_go_bar: positive_ids と negative_ids が重複 {overlap}; "
            "同一素材を positive と negative の両方に使えない (fail-closed)"
        )
    # 凍結バーの cardinality 整合。positive_ids の本数は total_positive と一致し、
    # negative_ids は非空でなければならない。registry の typo で positive を 1 本落とすと
    # 「3/3 で go」、negative_ids が空だと偽陽性ガードなしで go が通る（total_positive は
    # 出力に echo されるだけで scoring には効かない）ため fail-closed（Codex 指摘・
    # docs §6.1 の 4 素材 + negative バーを守る config 整合）。
    total_positive = bar["total_positive"]
    if len(positive_ids) != total_positive:
        raise ValueError(
            f"evaluate_m1_real_go_bar: positive_ids 数 {len(positive_ids)} != "
            f"total_positive {total_positive}; 凍結バーの素材数が食い違う (fail-closed)"
        )
    if not negative_ids:
        raise ValueError(
            "evaluate_m1_real_go_bar: negative_ids が空; 偽陽性ガードなしで verdict を "
            "出せない (fail-closed)"
        )
    # repeats_min の下限。M1-real 仕様は n>=2 の繰返しを要求するので、registry の typo で
    # repeats_min が 2 未満に下がっても単一 report の verdict を通さない（凍結バーの
    # cardinality 整合と同型の config 整合・Codex 指摘）。
    if repeats_min < 2:
        raise ValueError(
            f"evaluate_m1_real_go_bar: m1_real_go_bar.repeats_min {repeats_min} < 2; "
            "M1-real 仕様は n>=2 を要求する (fail-closed)"
        )
    # pass/fail 閾値の semantic 自己整合。具体的凍結値（≥3/4 → min 3・偽陽性0 → max 0）は
    # 事前登録テスト（test_registry_has_m1_real_go_bar_preregistered が ==3/==0 を assert）が
    # registry SSOT を guard するので、ここでは値の pin（ランタイム二重 SSOT・#26 参照）は
    # せず、どの bar でも成り立つべき普遍不変条件だけを fail-closed で守る:
    #   - min_positive_sufficient は 1..total_positive（0 は「0 本 sufficient で go」、
    #     total 超過は go 不能の無意味設定）
    #   - max_negative_false_positive は 0..len(negatives)-1（len(neg) 以上だと全 negative で
    #     偽陽性を出しても pass し、偽陽性ガードが空洞化する）（Codex 指摘）。
    if not (1 <= min_positive_sufficient <= total_positive):
        raise ValueError(
            f"evaluate_m1_real_go_bar: min_positive_sufficient {min_positive_sufficient} は "
            f"[1, total_positive={total_positive}] の範囲外 (fail-closed)"
        )
    if not (0 <= max_negative_false_positive < len(negative_ids)):
        raise ValueError(
            f"evaluate_m1_real_go_bar: max_negative_false_positive {max_negative_false_positive} は "
            f"[0, len(negative_ids)={len(negative_ids)}) の範囲外; 偽陽性ガードが空洞化する "
            "(fail-closed)"
        )
    # registry の external_fixtures が凍結した id → input_kind。report 自己申告の kind を
    # 信用せず、これを真として matrix を評価する（Codex 指摘）。
    registered_kinds = _unique_id_map(
        registry.get("external_fixtures", []), "external_fixtures"
    )
    # id → 登録エントリ全体。expected_audio_sha256（存在すれば）で素材同一性を照合する。
    registered_external = {e["id"]: e for e in registry.get("external_fixtures", [])}

    # 凍結ルールが要求する繰返し回数（n>=2）未満の report で verdict を出さない。
    # CLI は nargs="+" で単一 report も受けるため、ここで弾かないと 1 回実行の観測が
    # go verdict を publish しうる（Codex 指摘・事前登録厳守）。
    if len(reports) < repeats_min:
        raise ValueError(
            f"evaluate_m1_real_go_bar: got {len(reports)} report(s) but the frozen "
            f"m1_real_go_bar requires repeats_min={repeats_min} (n>=2); "
            "1 回の実行だけで verdict を publish しない (fail-closed)"
        )

    # run provenance の存在 + 相互 distinct 性。決定論パイプラインでは同一素材の抽出は
    # bit 単位で同一結果を返すため、n>=2 が意味を持つのは**独立した実行**の揺れを見るとき
    # だけ（設計 §3.1）。run_external は実行ごとに新規 run_id を発行するので、report 間で
    # run_id が欠落・重複していたら、それは 1 回の実行結果を別パスへコピーして n>=2 を
    # 偽装した証拠（別パス・同一 bytes は path-dedup #6 を通過する）として弾く（Codex 指摘・
    # #45）。audio_sha256 の一致（同一素材）とは直交する軸で、素材は同一・実行は独立を要求。
    run_ids: List[str] = []
    for idx, report in enumerate(reports):
        run_id = report.get("run_id")
        if not run_id or not isinstance(run_id, str):
            raise ValueError(
                f"evaluate_m1_real_go_bar: reports[{idx}] lacks a non-empty string run_id; "
                "各 external 実行の独立性を pin できず、コピー由来の偽 repeats を排除できない "
                "(fail-closed)"
            )
        run_ids.append(run_id)
    duplicate_run_ids = sorted({r for r in run_ids if run_ids.count(r) > 1})
    if duplicate_run_ids:
        raise ValueError(
            f"evaluate_m1_real_go_bar: reports share run_id(s) {duplicate_run_ids}; "
            "同一実行の複製を独立した n>=2 repeats と見なせない (fail-closed)"
        )

    # 凍結バーを load した registry の hash（渡された場合）を authoritative reference と
    # する。渡されない場合は report 間の相互一致で代替する。いずれの report の pin も
    # reference と一致しなければ fail-closed（測定時と別の凍結バーで判定しない）。
    ref_registry_sha256 = (
        registry_sha256 if registry_sha256 is not None else reports[0]["registry_sha256"]
    )
    _ref_label = "loaded registry" if registry_sha256 is not None else "reports[0]"
    # 凍結 registry が解決する observation_gate。report の gate はこれと完全一致し、かつ
    # override 由来でない（thresholds_source == "registry"）ことを要求する。相互一致だけ
    # では、両 report が run_external(..., thresholds=override) で同じ緩いゲートを使った
    # 場合に registry_sha256 が一致したまま通過し、凍結ゲート外の測定で verdict を publish
    # できてしまう（Codex 指摘）。
    frozen_thresholds = ObservabilityThresholds.from_registry(registry["observation_gate"])
    frozen_gate = asdict(frozen_thresholds)
    for idx, report in enumerate(reports):
        if report["registry_sha256"] != ref_registry_sha256:
            raise ValueError(
                f"evaluate_m1_real_go_bar: reports[{idx}] registry_sha256 "
                f"{report['registry_sha256']!r} != {_ref_label} {ref_registry_sha256!r}; "
                "測定時と別の凍結バー/registry の下で判定できない (fail-closed)"
            )
        if report.get("thresholds_source") != "registry":
            raise ValueError(
                f"evaluate_m1_real_go_bar: reports[{idx}] thresholds_source="
                f"{report.get('thresholds_source')!r} != 'registry'; override ゲートで測定した "
                "report では凍結バーの判定を publish できない (fail-closed)"
            )
        if report["observation_gate"] != frozen_gate:
            raise ValueError(
                f"evaluate_m1_real_go_bar: reports[{idx}] observation_gate が凍結 registry の "
                "解決ゲートと一致しない; 凍結ゲート外で測定した report では判定できない (fail-closed)"
            )

    fixture_audio_hash: Dict[str, str] = {}
    for fixture_id in positive_ids + negative_ids:
        missing_in = [
            idx for idx, report in enumerate(reports) if fixture_id not in report.get("fixtures", {})
        ]
        if missing_in:
            raise ValueError(
                f"evaluate_m1_real_go_bar: required fixture {fixture_id!r} missing from "
                f"reports{missing_in}; 全事前登録 fixture を n>=2 で測定してからでないと "
                "verdict を主張できない (fail-closed)"
            )
        # route 名の重複を fail-closed。route 名 → outcome/row の map は dict 内包で
        # last-wins になるため、同一 fixture に同名 route 行が 2 つあると（後勝ちで）
        # 失敗/未観測行を後続の passing 行が隠蔽しうる。harness（_run_routes_on_file）は
        # select_routes を各 1 回しか回さず重複を出さないので、重複＝stale/手書き report
        # の証拠として弾く（Codex 指摘）。
        for idx, report in enumerate(reports):
            route_names = [row["route"] for row in report["fixtures"][fixture_id]["routes"]]
            duplicate_routes = sorted({r for r in route_names if route_names.count(r) > 1})
            if duplicate_routes:
                raise ValueError(
                    f"evaluate_m1_real_go_bar: fixture {fixture_id!r} in reports[{idx}] has "
                    f"duplicate route row(s) {duplicate_routes}; last-wins で失敗/未観測行を "
                    "隠蔽させない (fail-closed)"
                )
        # 各 fixture の n>=2 repeats が**同一素材**（同一 audio bytes）であることを
        # audio_sha256 の一致で保証する。manifest は audio_sha256: null を許すが
        # harness は run ごとに実測 hash を記録するため、同一 id で別 audio を使った
        # 2 run を「同一素材の n>=2 repeats」と誤認しないよう fail-closed（Codex 指摘）。
        audio_hashes = []
        for idx, report in enumerate(reports):
            audio_sha256 = report["fixtures"][fixture_id].get("audio_sha256")
            if not audio_sha256:
                raise ValueError(
                    f"evaluate_m1_real_go_bar: fixture {fixture_id!r} in reports[{idx}] lacks "
                    "audio_sha256; 素材の同一性を pin できず verdict を出せない (fail-closed)"
                )
            audio_hashes.append(audio_sha256)
        if len(set(audio_hashes)) > 1:
            raise ValueError(
                f"evaluate_m1_real_go_bar: fixture {fixture_id!r} has differing audio_sha256 "
                f"across reports {sorted(set(audio_hashes))}; 別素材を同一 id の n>=2 "
                "repeats と見なせない (fail-closed)"
            )
        fixture_audio_hash[fixture_id] = audio_hashes[0]
        # 素材同一性の pin（forward-compat）。frozen real_vocal_* は自作 Suno 曲で
        # 非 commit（設計 §6.5・波形は repo に置かない）、その expected audio hash は
        # slow-lane 生成時に決まる dated pin（§2.3・deliverable 2/3）で PR 時に repo へ
        # 固定できない。registry の external_fixtures エントリに `expected_audio_sha256`
        # が**存在すれば**（＝slow-lane 実測後に operator が記録したら）report の
        # audio_sha256 と一致することを要求し、manifest typo/差替で別素材に verdict を
        # 出すのを弾く。未記録なら no-op（emit は machine-dependent・Codex 指摘）。
        expected_audio = registered_external.get(fixture_id, {}).get("expected_audio_sha256")
        if expected_audio is not None and fixture_audio_hash[fixture_id] != expected_audio:
            raise ValueError(
                f"evaluate_m1_real_go_bar: fixture {fixture_id!r} audio_sha256 "
                f"{fixture_audio_hash[fixture_id]} != registry expected_audio_sha256 "
                f"{expected_audio}; 事前登録素材と別の audio で測定している (fail-closed)"
            )
        # registry が凍結した input_kind（report 自己申告でなく）で回すべき経路集合。
        registry_kind = registered_kinds.get(fixture_id)
        if registry_kind is None:
            raise ValueError(
                f"evaluate_m1_real_go_bar: fixture {fixture_id!r} は registry.yaml の "
                "external_fixtures に未登録 (fail-closed)"
            )
        expected_route_objs = select_routes(registry_kind)
        expected_routes = {r.name for r in expected_route_objs}
        expected_route_by_name = {r.name: r for r in expected_route_objs}
        separation_routes = {r.name for r in expected_route_objs if r.requires_separation}
        # model provenance の完全性 + 一致確認。各 report に共通して存在する route に
        # ついて、(a) measured（gate outcome）な行は provenance フィールドが実在する
        # ことを要求し（欠落した古い/手書き report が全 None 署名で一致扱いになる穴を
        # 塞ぐ）、(b) provenance 署名（source_model / version / separation_*）が repeats
        # 間で一致することを要求する（別 model stack の run を n>=2 と誤認しない）。
        # 設計 §2.3 の Demucs/CREPE/Melodia バージョン pin 要件（Codex 指摘）。
        common_routes = set(_fixture_route_rows(reports[0], fixture_id))
        for report in reports[1:]:
            common_routes &= set(_fixture_route_rows(report, fixture_id))
        for route in sorted(common_routes):
            # 凍結 matrix 外の混入経路は verdict に効かない（candidate_routes は凍結
            # matrix 限定）ので provenance/報告 payload 検証の対象外。
            if route not in expected_routes:
                continue
            rows = [_fixture_route_rows(report, fixture_id)[route] for report in reports]
            for idx, row in enumerate(rows):
                # 未観測（unavailable/not_applicable 等）は抽出器を回していないので
                # provenance を要求しない。measured な行のみ pin を必須化する。
                if row.get("outcome") not in _GATE_OUTCOMES:
                    continue
                missing_fields = []
                if not row.get("source_model"):
                    missing_fields.append("source_model")
                if not row.get("extractor_version"):
                    missing_fields.append("extractor_version")
                if route in separation_routes:
                    preprocessing = row.get("preprocessing")
                    if (
                        not isinstance(preprocessing, dict)
                        or not preprocessing.get("separation_model")
                        or not preprocessing.get("separation_version")
                    ):
                        missing_fields.append("preprocessing.separation_model/version")
                if missing_fields:
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] is measured but lacks provenance {missing_fields}; "
                        "provenance を pin しない report では同一 model stack の n>=2 を証明できない "
                        "(fail-closed)"
                    )
                # gate outcome は assess_observability(...).status 由来。measured 行は
                # その根拠 report payload を持ち、report.status が outcome と一致しなければ
                # ならない。outcome だけ insufficient→sufficient に改竄しても metrics は
                # 失敗のまま、という食い違いを弾く（Codex 指摘・手書き report 対策）。
                report_payload = row.get("report")
                if not isinstance(report_payload, dict) or report_payload.get("status") != row["outcome"]:
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] outcome {row['outcome']!r} lacks a matching report "
                        "payload (report.status); outcome と metrics の食い違いを許さない "
                        "(fail-closed)"
                    )
                # report payload 自身の route が行の route と一致することを要求。別経路の
                # passing payload（例 pyin_direct）を demucs_vocals_then_crepe 行に貼って
                # その経路で測ったように計上させるすり替えを弾く（Codex 指摘・harness は
                # observation.route=route.name で書くので実 report では常に一致）。
                if report_payload.get("route") != row["route"]:
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] report payload route {report_payload.get('route')!r} != "
                        f"row route {row['route']!r}; 別経路の観測 payload のすり替えを許さない "
                        "(fail-closed)"
                    )
                # status と payload metrics の整合を凍結ゲートで revalidate する。
                # outcome/report.status を改竄しつつ metrics を残す手書き report を弾く。
                _METRIC_KEYS = (
                    "voiced_coverage", "note_count", "phrase_count",
                    "confidence_mean", "low_confidence_rate", "octave_jump_rate",
                )
                missing_metrics = [k for k in _METRIC_KEYS if k not in report_payload]
                if missing_metrics:
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] report payload lacks gate metrics {missing_metrics} "
                        "(fail-closed)"
                    )
                # 数値の健全性検証。json.loads は NaN/Infinity を受理し、`NaN < min` は
                # False になるため、非有限や範囲外の metric を混ぜても sufficient を再導出
                # しうる。判定の前に、率/被覆は [0,1] の有限値、ノート数は非負 int である
                # ことを要求する（Codex 指摘・手書き report 対策）。
                for _key in ("voiced_coverage", "confidence_mean", "low_confidence_rate", "octave_jump_rate"):
                    _val = report_payload[_key]
                    if (
                        isinstance(_val, bool)
                        or not isinstance(_val, (int, float))
                        or not math.isfinite(_val)
                        or not (0.0 <= _val <= 1.0)
                    ):
                        raise ValueError(
                            f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                            f"reports[{idx}] metric {_key}={_val!r} は [0,1] の有限値でない "
                            "(fail-closed)"
                        )
                for _key in ("note_count", "phrase_count"):
                    _val = report_payload[_key]
                    if isinstance(_val, bool) or not isinstance(_val, int) or _val < 0:
                        raise ValueError(
                            f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                            f"reports[{idx}] metric {_key}={_val!r} は非負整数でない (fail-closed)"
                        )
                _agreement = report_payload.get("cross_extractor_agreement")
                if _agreement is not None and (
                    isinstance(_agreement, bool)
                    or not isinstance(_agreement, (int, float))
                    or not math.isfinite(_agreement)
                    or not (0.0 <= _agreement <= 1.0)
                ):
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] cross_extractor_agreement={_agreement!r} は "
                        "None か [0,1] の有限値でない (fail-closed)"
                    )
                # 凍結ゲートに対し payload metrics から status を**厳密に**再導出して
                # outcome と照合する（両方向）。report.to_dict() が metrics を丸めなくなった
                # ため（厳密値保存）、gate_reasons による再導出は原判定と厳密一致し、丸め起因
                # の曖昧さ・false-positive が消える。確定した不整合を両方向とも弾く:
                #   - outcome=sufficient なのに再導出 insufficient → go への水増し
                #   - outcome=insufficient なのに再導出 sufficient → 特に negative で「実は
                #     偽陽性」を outcome 文字列で隠蔽（neg_false_positive 対策）
                # （Codex 指摘・gate_reasons を assess_observability と共有）。
                revalidated_reasons = gate_reasons(
                    frozen_thresholds,
                    voiced_coverage=report_payload["voiced_coverage"],
                    note_count=report_payload["note_count"],
                    phrase_count=report_payload["phrase_count"],
                    confidence_mean=report_payload["confidence_mean"],
                    low_confidence_rate=report_payload["low_confidence_rate"],
                    octave_jump_rate=report_payload["octave_jump_rate"],
                    cross_extractor_agreement=report_payload.get("cross_extractor_agreement"),
                )
                revalidated_status = "sufficient" if not revalidated_reasons else "insufficient"
                if revalidated_status != row["outcome"]:
                    raise ValueError(
                        f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                        f"reports[{idx}] outcome {row['outcome']!r} だが payload metrics は凍結"
                        f"ゲートで {revalidated_status!r} を示す {revalidated_reasons}; "
                        "status と metrics の食い違いを許さない (fail-closed)"
                    )
                # 行が凍結 route 定義（select_routes）の抽出器/前処理と一致することを要求。
                # 経路名だけ `demucs_vocals_then_crepe` を名乗り実際は pyin/librosa で測った
                # 行が CREPE 経路として計上されるすり替えを防ぐ（Codex 指摘・手書き report
                # 対策）。expected 集合外の経路（candidate に効かない混入行）は検査しない。
                expected_route = expected_route_by_name.get(route)
                if expected_route is not None:
                    if row.get("extractor") != expected_route.extractor:
                        raise ValueError(
                            f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                            f"reports[{idx}] measured with extractor {row.get('extractor')!r} != "
                            f"frozen {expected_route.extractor!r}; 経路ラベルと実測抽出器の"
                            "すり替えを許さない (fail-closed)"
                        )
                    # source_model が期待抽出器ファミリと矛盾しないことを要求。extractor
                    # ラベルを crepe に偽装しても source_model=librosa:pyin のままなら、
                    # 別抽出器の payload を貼ったすり替えとして弾く（Codex 指摘）。
                    expected_token = _EXTRACTOR_SOURCE_MODEL_TOKEN.get(expected_route.extractor)
                    source_model = row.get("source_model") or ""
                    if expected_token is not None and expected_token not in source_model.lower():
                        raise ValueError(
                            f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                            f"reports[{idx}] source_model {row.get('source_model')!r} は期待抽出器 "
                            f"{expected_route.extractor!r}（token {expected_token!r}）と矛盾する; "
                            "別抽出器の payload のすり替えを許さない (fail-closed)"
                        )
                    if expected_route.requires_separation:
                        pp = row.get("preprocessing")
                        pp_label = pp.get("preprocessing") if isinstance(pp, dict) else None
                        if pp_label != expected_route.preprocessing:
                            raise ValueError(
                                f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                                f"reports[{idx}] preprocessing {pp_label!r} != frozen "
                                f"{expected_route.preprocessing!r} (fail-closed)"
                            )
                    elif row.get("preprocessing"):
                        # 分離不要な直接経路（例 pyin_direct）が preprocessing を持つのは、
                        # 生の直接経路を測らずに分離後の観測を direct baseline として計上
                        # させるすり替え。harness は非分離経路に preprocessing を書かない
                        # ので、存在＝stale/手書き report として弾く（Codex 指摘・対称）。
                        raise ValueError(
                            f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} in "
                            f"reports[{idx}] は分離不要（frozen preprocessing={expected_route.preprocessing!r}）"
                            "なのに preprocessing を持つ (fail-closed)"
                        )
            if len({_route_provenance(row) for row in rows}) > 1:
                raise ValueError(
                    f"evaluate_m1_real_go_bar: fixture {fixture_id!r} route {route!r} has "
                    "differing model provenance (source_model/extractor_version/separation) "
                    "across reports; 別 model stack の run を n>=2 repeats と見なせない (fail-closed)"
                )
        # 完全 route matrix の存在確認。candidate_routes は「report に既に存在する経路」
        # からしか作らないため、stale/truncated report である経路が全 positive から
        # 丸ごと欠けても未観測として弾かれず baseline 経路単独で go が出うる。**registry が
        # 凍結した input_kind**（report 自己申告でなく）で回すべき全経路（select_routes）
        # と突き合わせ、欠落を fail-closed にする（Codex 指摘・docs §6.3 の vocal_track
        # 4 経路 matrix を全 fixture × n>=2）。
        for idx, report in enumerate(reports):
            report_kind = report["fixtures"][fixture_id]["input_kind"]
            if report_kind != registry_kind:
                raise ValueError(
                    f"evaluate_m1_real_go_bar: fixture {fixture_id!r} in reports[{idx}] declares "
                    f"input_kind {report_kind!r} != registry frozen {registry_kind!r}; "
                    "report 自己申告の kind で易しい matrix にすり替えさせない (fail-closed)"
                )
            present_routes = set(_fixture_route_outcomes(report, fixture_id))
            missing_routes = sorted(expected_routes - present_routes)
            if missing_routes:
                raise ValueError(
                    f"evaluate_m1_real_go_bar: fixture {fixture_id!r} in reports[{idx}] is "
                    f"missing route(s) {missing_routes} of the pre-registered {registry_kind} "
                    f"matrix {sorted(expected_routes)}; truncated/stale report では全 fixture が "
                    "全経路を測っていないと verdict を主張できない (fail-closed)"
                )

    # go-bar の全 fixture が**互いに別素材**であることを保証する。per-fixture の
    # audio_sha256 一致（上）は同一 id の repeats 同一性を見るだけで、異なる id が
    # 同じ WAV を指しても弾かない。3 つの positive が同じ易しい WAV なら「4 別素材」
    # でなく実質 1 素材で ≥3/4 を満たしてしまう（凍結バーは 4 別素材を前提）。
    # fixture 間で audio_sha256 の重複を fail-closed（Codex 指摘）。
    hash_to_fixtures: Dict[str, List[str]] = {}
    for fixture_id, audio_sha256 in fixture_audio_hash.items():
        hash_to_fixtures.setdefault(audio_sha256, []).append(fixture_id)
    collisions = {h: fids for h, fids in hash_to_fixtures.items() if len(fids) > 1}
    if collisions:
        collided = sorted(fid for fids in collisions.values() for fid in fids)
        raise ValueError(
            f"evaluate_m1_real_go_bar: distinct go-bar fixtures share the same audio_sha256 "
            f"{collided}; 別素材であるべき fixture が同一 WAV を指している (fail-closed)"
        )

    # 候補経路は positive fixture の**registry 凍結 kind**が規定する matrix に限定する。
    # report 内容の union から作ると、stale/手書き report が正規 4 経路に加えて非登録の
    # 経路（例 clear_lead 用 crepe_direct）を混入させたとき、凍結 §6.3 経路が 1 本も
    # survive していなくてもその混入経路で go が出うる。凍結 matrix だけを候補にする
    # ことで、混入経路は verdict に一切効かない（Codex 指摘）。
    candidate_routes: set[str] = set()
    for fixture_id in positive_ids:
        candidate_routes.update(
            r.name for r in select_routes(registered_kinds[fixture_id])
        )

    routes_out: Dict[str, Dict[str, Any]] = {}
    surviving_routes: List[str] = []
    for route in sorted(candidate_routes):
        pos_sufficient = sum(
            1 for fid in positive_ids if _is_stably_sufficient(reports, fid, route)
        )
        neg_false_positive = sum(
            1 for fid in negative_ids if _is_ever_sufficient(reports, fid, route)
        )
        unstable_positive_ids = sorted(
            fid
            for fid in positive_ids
            if _is_ever_sufficient(reports, fid, route)
            and not _is_stably_sufficient(reports, fid, route)
        )
        # 「≥3/4 sufficient」は 4 本すべてを実測した上での 3 本以上を意味する。
        # ある positive がその経路で未観測（欠落/unavailable 等）なら、matrix は
        # 未完（全 5 fixture × n>=2 を回していない）であり、残りの sufficient が
        # 偶々バーを満たしても go を publish しない（未観測を「測って落ちた」と
        # 混同しない・Codex 指摘。negative 側と対称）。
        positive_unmeasured_ids = sorted(
            fid for fid in positive_ids if not _is_measured(reports, fid, route)
        )
        # 偽陽性ゼロは「negative でその経路を実際に測って sufficient が出なかった」で
        # 初めて certify できる。全 report で未観測（欠落/unavailable 等）の negative が
        # あれば、その経路は偽陽性なしを証明できていないので survive させない
        # （neg_false_positive==0 を「測っていない」と「幻視しなかった」で混同しない・
        # Codex 指摘）。
        negative_unmeasured_ids = sorted(
            fid for fid in negative_ids if not _is_measured(reports, fid, route)
        )
        surviving = (
            pos_sufficient >= min_positive_sufficient
            and neg_false_positive <= max_negative_false_positive
            and not positive_unmeasured_ids
            and not negative_unmeasured_ids
        )
        routes_out[route] = {
            "pos_sufficient": pos_sufficient,
            "neg_false_positive": neg_false_positive,
            "unstable_positive_ids": unstable_positive_ids,
            "positive_unmeasured_ids": positive_unmeasured_ids,
            "negative_unmeasured_ids": negative_unmeasured_ids,
        }
        if surviving:
            surviving_routes.append(route)

    surviving_routes.sort()
    # 確定的な no_go（強化版 No-Go・設計 §4.4）は「**全**候補経路を全 fixture×route で
    # 実測（gate outcome）した上でどれも生き残らなかった」ときだけ出せる。ある候補経路が
    # 未測定（optional 依存欠如で unavailable 等）なら、その経路が凍結バーを満たす可能性を
    # 排除できていないので「生き残りなし＝no_go」を証明したことにならない。未測定を測定
    # 未達と偽らず inconclusive を返す（Codex 指摘）。
    fully_measured_routes = [
        route
        for route, info in routes_out.items()
        if not info["positive_unmeasured_ids"] and not info["negative_unmeasured_ids"]
    ]
    all_candidates_measured = len(fully_measured_routes) == len(routes_out)
    if surviving_routes:
        verdict = "go"
    elif all_candidates_measured:
        verdict = "no_go"
    else:
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "fully_measured_routes": sorted(fully_measured_routes),
        "registry_sha256": ref_registry_sha256,
        # verdict を解釈したコードの digest。別 checkout で route matrix / scoring / ゲート
        # 論理が変われば同じ report_pins/registry_sha256 でも別 verdict になりうる stale を
        # 検出可能にする（Codex 指摘・AGENTS §8）。
        "evaluator_code_sha256": _evaluator_code_sha256(),
        "n_reports": len(reports),
        # 各 report の run provenance を verdict へ転記（独立実行だった証跡・#45）。
        "run_ids": sorted(run_ids),
        "surviving_routes": surviving_routes,
        "bar": {
            "min_positive_sufficient": min_positive_sufficient,
            "max_negative_false_positive": max_negative_false_positive,
            "total_positive": bar["total_positive"],
            "repeats_min": bar["repeats_min"],
        },
        "routes": dict(sorted(routes_out.items())),
        "positive_ids": positive_ids,
        "negative_ids": negative_ids,
    }


def _resolve_unique_report_paths(paths: List[Path]) -> List[Path]:
    """`--evaluate-go-bar` の report パスを正規化し、同一ファイルの二重指定を fail-closed。

    同じ ``run1.json`` を 2 回渡すと、1 回の実 slow-lane run だけで
    ``repeats_min=2`` を満たせてしまう（Codex 指摘）。resolve 後の絶対パスで重複を
    検出して弾く。内容ではなくパス同一性で判定する点が重要——決定論パイプラインの
    正当な n>=2 繰返しは別パス（run1/run2）に置かれ内容が一致するのが正常であり、
    内容重複で弾くと真の繰返しを誤って拒否してしまうため。
    """
    resolved = [Path(p).resolve() for p in paths]
    seen: set[Path] = set()
    duplicates: List[str] = []
    for path in resolved:
        if path in seen:
            duplicates.append(str(path))
        seen.add(path)
    if duplicates:
        raise ValueError(
            f"--evaluate-go-bar received duplicate report path(s): {sorted(set(duplicates))}; "
            "同一 report を複数回渡して repeats_min を満たすことはできない (fail-closed)"
        )
    return resolved


def summarize(results: Dict[str, Any]) -> List[str]:
    lines = [f"# melody observability ({results['mode']} mode)"]
    for fid, info in results["fixtures"].items():
        lines.append(f"\n## {fid}  (input_kind={info['input_kind']}, expect={info['expect_status']})")
        for row in info["routes"]:
            report = row.get("report")
            detail = ""
            if report and report.get("reasons"):
                detail = "  reasons=" + "; ".join(report["reasons"])
            lines.append(f"  - {row['route']:<28} [{row['extractor']:<11}] -> {row['outcome']}{detail}")
    return lines


def summarize_go_bar(verdict: Dict[str, Any]) -> List[str]:
    lines = [f"# M1-real Go bar evaluation: {verdict['verdict'].upper()}"]
    lines.append(
        f"registry_sha256={verdict['registry_sha256']}  n_reports={verdict['n_reports']}"
    )
    lines.append(f"bar: {verdict['bar']}")
    lines.append(f"surviving_routes: {verdict['surviving_routes']}")
    lines.append("routes:")
    for name, info in verdict["routes"].items():
        lines.append(
            f"  - {name:<28} pos_sufficient={info['pos_sufficient']} "
            f"neg_false_positive={info['neg_false_positive']} "
            f"unstable_positive_ids={info['unstable_positive_ids']}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="観測表 JSON の出力先")
    parser.add_argument(
        "--external", type=Path, help="外部素材 manifest（正解なし実素材の観測可能性）"
    )
    parser.add_argument(
        "--evaluate-go-bar",
        type=Path,
        nargs="+",
        metavar="REPORT_JSON",
        help=(
            "凍結済み M1-real Go bar (registry.yaml の m1_real_go_bar) を external "
            "report JSON（n>=2 の繰り返し実行）から機械評価する。抽出は行わず、"
            "--external/synthetic 実行とは独立したモード（同時指定時はこちらのみ実行）"
        ),
    )
    args = parser.parse_args()

    if args.evaluate_go_bar is not None:
        registry, registry_sha256 = _load_registry()
        report_paths = _resolve_unique_report_paths(args.evaluate_go_bar)
        # 消費する report の raw bytes を hash し、その同じ bytes を parse する。verdict に
        # 「どの report が判定を生んだか」を content hash で pin しないと、公開後に
        # run1/run2.json が差し替え・編集されても verdict 単体から検出できない
        # （verdict は n_reports/registry_sha256 しか持たない・Codex 指摘・AGENTS §8）。
        # 衝突/重複検出には resolve 済みパスを使うが、report_pins には**可搬な**パスを
        # 記録する: repo 内なら repo 相対、repo 外（/tmp scratchpad 等の transient な
        # slow-lane artifact）なら basename のみ。渡された生 arg を verbatim に載せると、
        # 絶対/scratchpad パス（本リポジトリの scratchpad 慣行は /tmp）で verdict.json が
        # machine-local になり、別 checkout の検証者が sha256 一致でも手動パス変換なしに
        # replay できない。sha256 が content-addressed の真の anchor で、path は可搬 hint
        # に徹する（Codex 指摘・AGENTS §8）。
        reports = []
        report_pins = []
        for resolved in report_paths:
            data = resolved.read_bytes()
            reports.append(json.loads(data))
            try:
                pin_path = str(resolved.relative_to(ROOT))
            except ValueError:
                pin_path = resolved.name  # repo 外 = 可搬 hint として basename のみ
            report_pins.append(
                {"path": pin_path, "sha256": hashlib.sha256(data).hexdigest()}
            )
        # --out が保護対象パスに解決されると verdict の書き込みが重要 artifact を破壊する
        # ため、書き込み前にパス衝突を fail-closed（Codex 指摘）。保護対象:
        #   - 入力 report（pin 済み report を上書きすると report_pins の hash が実体と
        #     不一致になり slow-lane repeat を破壊する）
        #   - 凍結 registry（`--out .../registry.yaml` の typo で、verdict 生成直後に
        #     凍結 Go-bar registry を上書き破壊するのを防ぐ）
        #   - 評価器コード（evaluator_code_sha256 で hash 済み＝provenance 入力。--out で
        #     上書きすると verdict が pin したコード artifact を破壊し replay 不能になる）
        # report_paths / REGISTRY_PATH / 評価器コードは resolve 済み集合で比較する。
        protected_paths = (
            set(report_paths)
            | {REGISTRY_PATH.resolve()}
            | set(_evaluator_code_paths())
        )
        if args.out is not None and Path(args.out).resolve() in protected_paths:
            raise ValueError(
                f"--out {args.out} は保護対象パス（入力 report / 凍結 registry）と衝突する; "
                "verdict の書き込みが重要 artifact を破壊するため拒否する (fail-closed)"
            )
        verdict = evaluate_m1_real_go_bar(
            reports, registry, registry_sha256=registry_sha256
        )
        verdict["report_pins"] = report_pins
        for line in summarize_go_bar(verdict):
            print(line)
        if args.out is not None:
            _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
            print(f"\nwrote {args.out}")
        return 0

    # thresholds は run_synthetic / run_external が registry の single read から
    # 構築する（別途 load_thresholds を呼ぶと registry を二重 read してしまう）。
    if args.external is not None:
        results = run_external(args.external)
    else:
        results = run_synthetic()

    for line in summarize(results):
        print(line)
    if args.out is not None:
        # evaluate-go-bar 側と対称に、--out が入力/重要 artifact を上書き破壊するのを
        # 書き込み前に fail-closed。保護対象: 凍結 registry（両モード）、manifest 本体、
        # 各 source audio（external モードで results に resolved audio_path が載る）。
        # 例: `--external manifest.json --out manifest.json` は manifest を、
        # `--out <source>.wav` は再現に必要な source audio を破壊する（Codex 指摘）。
        protected_paths = {REGISTRY_PATH.resolve()}
        if args.external is not None:
            protected_paths.add(Path(args.external).resolve())
        else:
            # synthetic モードは synthesis_specs.yaml を load_specs() で読み、
            # build_melody_bench.py（builder コード）で波形を生成する committed 入力なので
            # 両方保護する（`--out .../synthesis_specs.yaml` や `--out .../build_melody_bench.py`
            # で generator を上書き破壊させない・#38/#43 と対称）。
            import build_melody_bench as _bench

            protected_paths.add(SPECS_PATH.resolve())
            protected_paths.add(Path(_bench.__file__).resolve())
        for info in results.get("fixtures", {}).values():
            audio_path = info.get("audio_path")
            if audio_path:
                protected_paths.add(Path(audio_path).resolve())
        if Path(args.out).resolve() in protected_paths:
            raise ValueError(
                f"--out {args.out} は保護対象パス（registry / manifest / source audio）と "
                "衝突する; 書き込みが再現に必要な入力を破壊するため拒否する (fail-closed)"
            )
        _atomic_write_text(
            args.out, json.dumps(results, indent=2, sort_keys=True)
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
