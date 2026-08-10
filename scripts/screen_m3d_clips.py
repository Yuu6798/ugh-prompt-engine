"""screen_m3d_clips.py — M3d v2: 観測ゲート由来の素材スクリーニング（S1/S2）。

`docs/measurements/m3d_2026-08/preregistration_v2.md` §2 に従う:

- **S1**: `m2c_external_fixtures.yaml` の vocadito 40 clip 全数（pin 照合済み原音）
  へ M1 観測ゲート（route=crepe_direct）を適用する。
- **S2**: S1 で sufficient の各 clip について 4 変形（`scripts/build_m3d_pairs.py`
  と同一の librosa 決定論変形パラメータ・関数——`make_variants` / `VOCADITO_SEMITONES`
  / `VOCADITO_TIME_RATES`）を生成し、各変形にも同ゲートを適用する。
- **survivor**: 原音 + 4 変形の全 5 本が sufficient の clip。

`scripts/run_melody_comparison.py` が run phase で使う観測経路
（`observe_via_route_with_provenance` → `assess_observability`）と**同一のコード
パス・同一の M1 registry**（`tests/fixtures/melody_bench/registry.yaml`）を再利用
するが、`compare_melodies`（類似度計算）は一切呼ばない — M1 の設計原理「比較の前に
観測可否を問う」を素材選定へ適用する（prereg_v2 §2）。

1 プロセスで完走する設計（モデルロード 1 回）: setsid デタッチでの長時間実行を
想定し、全 clip×side の処理を単一の python プロセス内で行う（サブプロセス起動を
挟まない — crepe/tensorflow のモデルロードは初回呼び出しの 1 回のみで済む）。

出力（schema: m3d-screening/0.1）は atomic write する。この記録自体は
**非コミットのビルド生成物**（`build/external_m3d/`）であり、正本は
`docs/measurements/m3d_2026-08/preregistration_v2.md`。実測後、生成物は
`docs/measurements/m3d_2026-08/screening_v2.json` へコピーして commit する
（builder v2 が読む sidecar は `build/` 配下のこのファイルそのもの）。

**Codex レビュー第 4 巡対応（U1・スナップショット束縛）**: 従来 `screen()` は
各 vocadito 原音 `audio_path` を (a) `observe_via_route_with_provenance`
（S1 観測） (b) `file_sha256`（sha256 記録） (c) `librosa.load`（S2 の 4 変形
生成の親波形取得）でそれぞれ**独立に**開いていた——長時間スクリーニング run
中にファイルが差し替えられれば、この 3 つの読み取りが異なるバイト列を見る
余地があり、記録された sha256・観測結果・4 変形の親バイトが相互に一致する
保証がなかった（`scripts/build_m3d_pairs.py::generate_vocadito_variants` の
スナップショット束縛と同型の穴）。ここで `_freeze_and_verify_vocadito_
snapshot` を新設し、各 clip の原音バイトを screening 開始時（clip 処理の
先頭）に 1 回だけ `staging`（tempfile ディレクトリ）へコピーし、コピー先を
非キャッシュで pin（`m2c_external_fixtures.yaml`）と照合してから、以後の
(a)(b)(c) 全てをこの**同一スナップショットバイト**から行う——一度書いた
バイトを読み直すだけなので、以後原本が差し替わってもスナップショットの
内容には一切影響しない（`generate_vocadito_variants` の decode 直前コピー・
非キャッシュ pin 照合と同型の設計）。

使い方::

    python scripts/screen_m3d_clips.py --out build/external_m3d/m3d_screening_v2.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SRC = ROOT / "src"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import librosa  # noqa: E402
import soundfile as sf  # noqa: E402

import build_m3d_pairs as bp  # noqa: E402
import run_melody_comparison as harness  # noqa: E402
from svp_rpe.melody.extractors import observe_via_route_with_provenance  # noqa: E402
from svp_rpe.melody.observability import ObservabilityThresholds, assess_observability  # noqa: E402
from svp_rpe.utils.hashing import file_sha256  # noqa: E402

_SCHEMA = "m3d-screening/0.1"
# prereg_v2 §2: S1/S2 とも route=crepe_direct 固定（run phase の実測 route と同一）。
_ROUTE_NAME = "crepe_direct"


class ScreenM3dClipsError(RuntimeError):
    """スクリーニング固有の fail-closed エラー。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """`data` を `path` へ atomic に書く（`build_m3d_pairs._atomic_write_bytes` と同型）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_variant_wav(path: Path, y: Any, sample_rate: int) -> None:
    """変形波形を staging 領域へ書く（PCM_24 — `build_m3d_pairs._atomic_write_wav`
    と同じ subtype 規約。libsndfile の FLOAT/DOUBLE subtype は PEAK チャンクへ
    壁時計を埋めバイト決定論を壊すため避ける——builder v2 が後で全く同じ入力/
    パラメータから決定論再生成する変形と、本スクリーニングが観測した変形の
    バイトを同一にできる設計選択。本 WAV は診断専用の一時ファイルであり、
    committed artifact ではない（呼び出し側が一時ディレクトリごと破棄する）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sample_rate, subtype="PCM_24")


def _freeze_and_verify_vocadito_snapshot(
    clip_id: str,
    audio_path: Path,
    expected_sha256: str,
    snapshot_dir: Path,
) -> Path:
    """`audio_path`（vocadito 原音）のバイトを一度だけ読み、
    `snapshot_dir/<clip_id>.wav` へ atomic に書いてから、**コピー先**
    （コピー元ではない）を非キャッシュで pin（`expected_sha256`）と照合し、
    そのスナップショット path を返す（fail-closed。Codex レビュー第 4 巡 U1
    対応）。

    役割: 「今回このスナップショットへ書いたバイトは承認済みか」という
    消費バイト束縛の構造的な正を作ること。呼び出し側 `screen()` は本関数が
    返す**同じ path** のみを (a) `observe_via_route_with_provenance`（S1 観測
    入力） (b) `file_sha256`（sha256 記録） (c) `librosa.load`（S2 4 変形生成
    の親波形取得）の全てへ渡す——3 用途が同一バイト列を読むことがこれで構造的
    に保証される（`scripts/build_m3d_pairs.py::generate_vocadito_variants` の
    スナップショット束縛と同型）。

    冒頭の `bp.verify_vocadito_pins(..., use_cache=False)`（`screen()` の
    生成開始前の一括照合）とは役割が異なる: あちらは「run 開始時点で原本が
    pin と一致していたか」の一括ゲート、本関数は「今回 decode/観測に実際に
    使うバイトが pin と一致しているか」を clip ごとに使用直前で束縛する
    多層防御（T2 と同じ設計判断）。
    """
    raw_bytes = Path(audio_path).read_bytes()
    snapshot_path = snapshot_dir / f"{clip_id}.wav"
    _atomic_write_bytes(snapshot_path, raw_bytes)
    actual_sha256 = file_sha256(snapshot_path, use_cache=False)
    if actual_sha256 != expected_sha256:
        raise ScreenM3dClipsError(
            f"{clip_id}: vocadito スナップショットの sha256 が pin と不一致 "
            f"(fail-closed; スナップショット={snapshot_path}; "
            f"actual={actual_sha256} expected={expected_sha256})"
        )
    return snapshot_path


def _gate_one(audio_path: Path, thresholds: ObservabilityThresholds, route: Any) -> Dict[str, Any]:
    """1 音声ファイルへ観測ゲートを適用して結果 dict を返す。

    **比較（`compare_melodies`）は一切呼ばない** — `observe_via_route_with_provenance`
    （run phase の既定 route_runner と同一関数）で観測し、`assess_observability`
    （M1 のゲート判定 single source of truth）で sufficient/insufficient を判定する
    だけである。

    `audio_path`（U1 対応）: 呼び出し側は原本ではなく `_freeze_and_verify_
    vocadito_snapshot` が返したスナップショット path を渡す——ここで計算する
    `audio_sha256` が観測入力と同一バイトであることの構造的保証はこの引数の
    選び方に依存する（本関数自体は path の由来を区別しない）。
    """
    observation, provenance = observe_via_route_with_provenance(str(audio_path), route)
    report = assess_observability(observation, thresholds)
    audio_sha256 = file_sha256(audio_path, use_cache=False)
    return {
        "status": report.status,
        "reasons": list(report.reasons),
        "audio_sha256": audio_sha256,
        "route_provenance": provenance,
        "gate_metrics": report.to_dict(),
    }


def _reject_output_input_collisions(
    *,
    out_path: Path,
    fixtures_path: Path,
    m1_registry_path: Path,
    vocadito_audio_paths: "list[Path]",
) -> None:
    """resolve() 済みの `--out` を全保護入力（fixtures yaml・m1 registry・
    vocadito 全 WAV）と突き合わせ、衝突があれば screening 開始前に
    fail-closed で拒否する（`build_m3d_pairs._reject_output_input_collisions`
    と同型・Codex レビュー第 2 巡 N1 対応）。

    `--out` は誤設定（例: `--fixtures`/`--m1-registry`/vocadito 音声そのものを
    誤って指定）で入力を指した場合、S1/S2 のスキャン自体は成功裏に読み終えて
    しまい、その**後**の `_atomic_write_bytes` が入力をスクリーニング JSON で
    上書き破壊する——生成コスト（crepe/tensorflow モデルロード込みの全 40
    clip×5 本のゲート適用）を払った後に気付く事故を防ぐため、スキャン開始前
    （関数冒頭）に検査する。resolve() 済み絶対パスの完全一致で判定するため、
    同一ファイルを指す別表記（相対パス・symlink 越し等）も検出する。

    S2 変形の一時 WAV（`_write_variant_wav` が書く）と U1 の原音スナップショット
    （`_freeze_and_verify_vocadito_snapshot` が書く）はいずれも本 preflight の
    対象としない: `tempfile.TemporaryDirectory` が呼び出しごとに OS 側で
    ランダムに採番するディレクトリで、`--out` から独立に決まり、screening
    開始前に存在する決定論的な resolve() 済みパスを持たない（事前に衝突判定
    できる固定パスがない）。実際に `--out` と一致する確率は実務上ゼロであり、
    仮に一致しても一時ディレクトリは `with` ブロック終了時に破棄される診断
    専用の生成物であって、上書き破壊されたら実測系列が壊れる build 入力
    （fixtures/m1-registry/vocadito 原音）とは性質が異なる。
    """
    resolved_out = Path(out_path).resolve()
    inputs: Dict[Path, str] = {
        Path(fixtures_path).resolve(): f"fixtures_path ({fixtures_path})",
        Path(m1_registry_path).resolve(): f"m1_registry_path ({m1_registry_path})",
    }
    for audio_path in vocadito_audio_paths:
        inputs.setdefault(Path(audio_path).resolve(), f"vocadito WAV ({audio_path})")

    if resolved_out in inputs:
        raise ScreenM3dClipsError(
            f"--out が build 入力と衝突している (fail-closed): "
            f"{resolved_out} は {inputs[resolved_out]!r} と同じ解決済みパスを指す"
        )


def screen(
    *,
    vocadito_dir: Path,
    fixtures_path: Path,
    m1_registry_path: Path,
) -> Dict[str, Any]:
    """S1/S2 を実行し、screening record（schema: m3d-screening/0.1）を返す。"""
    started_utc = _utc_now()

    fixtures, fixtures_sha256 = bp.load_m2c_fixtures(fixtures_path)
    # 全数 pin 照合（fail-closed）。`use_cache=False`: 事前登録前の最終照合と
    # 同じ「改変検出そのものが目的」の再照合のため実バイトを読み直す。この
    # 一括照合は run 開始時点のゲート——clip ごとの使用直前束縛は下の
    # `_freeze_and_verify_vocadito_snapshot`（U1）が別途行う（多層防御）。
    resolved_audio = bp.verify_vocadito_pins(vocadito_dir, fixtures, use_cache=False)

    m1_mapping, m1_registry_sha256 = harness._load_m1_registry(m1_registry_path)
    thresholds = ObservabilityThresholds.from_registry(m1_mapping["observation_gate"])
    route = harness._resolve_route(_ROUTE_NAME)

    clips: Dict[str, Any] = {}
    s1_reason_histogram: Dict[str, int] = {}
    s2_variant_dropout: Dict[str, int] = {}

    # U1（Codex レビュー第 4 巡）: 全 clip の原音スナップショットを束ねる単一の
    # staging ディレクトリ。run 全体で 1 つ（clip ごとに使い捨てにしない ——
    # `build_m3d_pairs.generate_vocadito_variants` の `_vocadito_snapshot` と
    # 同じ寿命規約）。`with` を抜けると全スナップショットが破棄される。
    with tempfile.TemporaryDirectory(prefix="m3d_screen_snapshot_") as snapshot_root:
        snapshot_dir = Path(snapshot_root)
        for clip_id in sorted(fixtures):
            audio_path = resolved_audio[clip_id]
            # U1: 原音バイトを 1 回だけスナップショットへコピーし、コピー先を
            # pin と照合してから、以後の (a) 観測 (b) sha256 記録 (c) 変形生成
            # の全てをこの同一スナップショット path から行う。
            snapshot_path = _freeze_and_verify_vocadito_snapshot(
                clip_id, audio_path, fixtures[clip_id], snapshot_dir
            )
            original_result = _gate_one(snapshot_path, thresholds, route)
            clip_entry: Dict[str, Any] = {"original": original_result}

            s1_sufficient = original_result["status"] == "sufficient"
            clip_entry["s1_sufficient"] = s1_sufficient
            if not s1_sufficient:
                for reason in original_result["reasons"]:
                    key = reason.split(" ", 1)[0]
                    s1_reason_histogram[key] = s1_reason_histogram.get(key, 0) + 1
                clip_entry["survivor"] = False
                clips[clip_id] = clip_entry
                continue

            # S2: S1 sufficient の clip のみ 4 変形を生成しゲートを適用する
            # （builder と同一の決定論変形パラメータ・関数を再利用 — 新値を
            # 発明しない）。U1: 親波形は原本ではなく同じスナップショットから
            # decode する — 観測が読んだのと同一バイトが 4 変形の親になる。
            y, sr = librosa.load(str(snapshot_path), sr=None, mono=True)
            variants = bp.make_variants(
                y, sr, semitones=list(bp.VOCADITO_SEMITONES), time_rates=list(bp.VOCADITO_TIME_RATES)
            )
            all_sufficient = True
            with tempfile.TemporaryDirectory(prefix="m3d_screen_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                for variant_key in bp.VOCADITO_VARIANT_ORDER:
                    variant_wav = tmp_path / (
                        f"{clip_id}__{bp.VOCADITO_VARIANT_LABELS[variant_key]}.wav"
                    )
                    _write_variant_wav(variant_wav, variants[variant_key], sr)
                    variant_result = _gate_one(variant_wav, thresholds, route)
                    clip_entry[variant_key] = variant_result
                    if variant_result["status"] != "sufficient":
                        all_sufficient = False
                        s2_variant_dropout[variant_key] = (
                            s2_variant_dropout.get(variant_key, 0) + 1
                        )

            clip_entry["survivor"] = all_sufficient
            clips[clip_id] = clip_entry

    survivor_clip_ids = sorted(cid for cid, entry in clips.items() if entry["survivor"])
    survivor_clip_ids_sha256_sorted = sorted(
        survivor_clip_ids, key=lambda cid: hashlib.sha256(cid.encode("utf-8")).hexdigest()
    )

    recorded_utc = _utc_now()
    doc: Dict[str, Any] = {
        "schema": _SCHEMA,
        "started_utc": started_utc,
        "recorded_utc": recorded_utc,
        "route": _ROUTE_NAME,
        "m1_registry_sha256": m1_registry_sha256,
        "m2c_external_fixtures_sha256": fixtures_sha256,
        "gate_parameters": {
            "min_voiced_coverage": thresholds.min_voiced_coverage,
            "min_note_count": thresholds.min_note_count,
            "min_phrase_count": thresholds.min_phrase_count,
            "min_confidence_mean": thresholds.min_confidence_mean,
            "max_low_confidence_rate": thresholds.max_low_confidence_rate,
            "max_octave_jump_rate": thresholds.max_octave_jump_rate,
            "voiced_confidence_floor": thresholds.voiced_confidence_floor,
            "low_confidence_floor": thresholds.low_confidence_floor,
            "min_cross_extractor_agreement": thresholds.min_cross_extractor_agreement,
            "note_min_run_frames": thresholds.note_min_run_frames,
            "median_filter_frames": thresholds.median_filter_frames,
            "phrase_gap_sec": thresholds.phrase_gap_sec,
            "octave_jump_semitones": thresholds.octave_jump_semitones,
        },
        "transform_parameters": {
            "semitones": list(bp.VOCADITO_SEMITONES),
            "time_rates": list(bp.VOCADITO_TIME_RATES),
        },
        "clips": clips,
        "s1_summary": {
            "clip_count": len(fixtures),
            "sufficient_count": sum(1 for e in clips.values() if e["s1_sufficient"]),
            "insufficient_count": sum(1 for e in clips.values() if not e["s1_sufficient"]),
            "insufficient_reason_histogram": dict(sorted(s1_reason_histogram.items())),
        },
        "s2_variant_dropout_count": dict(sorted(s2_variant_dropout.items())),
        "survivor_clip_ids": survivor_clip_ids,
        "survivor_clip_ids_sha256_sorted": survivor_clip_ids_sha256_sorted,
        "survivor_count": len(survivor_clip_ids),
    }
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--vocadito-dir", type=Path, default=bp.DEFAULT_VOCADITO_DIR)
    parser.add_argument("--fixtures", type=Path, default=bp.M2C_FIXTURES_PATH)
    parser.add_argument("--m1-registry", type=Path, default=harness.M1_REGISTRY_PATH)
    args = parser.parse_args()

    try:
        # N1（Codex レビュー第 2 巡）: 高価な S1/S2 スキャンを始める前に、`--out`
        # が全保護入力（fixtures yaml・m1 registry・vocadito 全 WAV）のいずれとも
        # 衝突しないことを確認する。fixtures はここで一度読み、preflight の
        # vocadito WAV パス解決に使う（`screen()` 側は従来どおり独立に読み直す
        # ——読み取りのみで破壊的操作ではないため TOCTOU 上の追加リスクはない）。
        fixtures, _ = bp.load_m2c_fixtures(args.fixtures)
        vocadito_audio_paths = [
            bp.vocadito_audio_path(args.vocadito_dir, clip_id) for clip_id in fixtures
        ]
        _reject_output_input_collisions(
            out_path=args.out,
            fixtures_path=args.fixtures,
            m1_registry_path=args.m1_registry,
            vocadito_audio_paths=vocadito_audio_paths,
        )

        doc = screen(
            vocadito_dir=args.vocadito_dir,
            fixtures_path=args.fixtures,
            m1_registry_path=args.m1_registry,
        )
    except (bp.BuildM3dPairsError, ScreenM3dClipsError) as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 1

    _atomic_write_bytes(args.out, json.dumps(doc, indent=2, sort_keys=True).encode("utf-8"))
    print(
        f"wrote screening record to {args.out} "
        f"(s1_sufficient={doc['s1_summary']['sufficient_count']}/{doc['s1_summary']['clip_count']}, "
        f"survivor_count={doc['survivor_count']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
