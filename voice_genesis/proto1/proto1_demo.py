"""proto1_demo.py — F1 (final_assembly_memo.md): 試作品 1 号 E2E デモ。

1 コマンドで以下を決定論実行し `results_final/e2e_run.json` に全記録する:

  1. 創生: sample(101), sample(202) → mutate(101系, seed=303) →
     crossover(101系mut, 202系, seed=404) の計 4 Genome（系譜の実演）
  2. レンダ: 各 Genome で probe suite 全 5 種（P2）をレンダし波形 hash 記録
  3. 検査（Genome ごと）: plausibility（periodicity r_median>=0.35, harness
     VT-1 と同一床）+ linkability 監査（E1/E2, reference-set standin-gallery-v1）。
     reference_set_hash / linkability_report_id を Genome の audit フィールドへ記録
  4. grip の参照記録: registry エントリの eval.grip_ref に
     voice_genesis/harness/results_v6/grip_report_v6.json への**参照**（値のコピーではない）
     + gate 意味論バージョン "grip-v2/band-v4/frozen-v6" を記録
  5. 登録: 4 Genome を registry へ lineage 付きで登録
  6. 決定論検証: デモ全体を 2 回実行し、genome_id・全波形 hash・監査判定が
     一致すること（created_at のみ差異許容）を機械照合
  7. 成果 WAV: 監査 PASS した個体 1 つの phrase probe レンダと cross_range
     ペアレンダを `results_final/` に保存

制約: フォアグラウンド実行、決定論（seed 固定。wall-clock 使用は
registry/監査レポートの created_at 記録のみ）。harness/ は読み取りのみ。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import soundfile as sf

import bridge
import genome as g
import plausibility as pl
import probes
import reference_set as rset
import registry as r
import sampler

PROTO_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PROTO_DIR / "results_final"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
# voice_genesis/proto1 -> voice_genesis -> リポジトリルート。
REPO_ROOT = PROTO_DIR.parent.parent

# underspec_log_final.md 参照: final_assembly_memo.md は mutate の scale を
# 指定していない。proto1_design_memo.md の他テスト・デモで慣例的に使われて
# きた 0.1（「小さな変異」の代表値）を踏襲する。
MUTATE_SCALE = 0.1

GRIP_REF_PATH = "voice_genesis/harness/results_v6/grip_report_v6.json"
GRIP_GATE_SEMANTICS_VERSION = "grip-v2/band-v4/frozen-v6"

GALLERY_N_PERMUTATIONS = rset.CHANCE_N_PERMUTATIONS  # 200（memo 規定どおり）


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _strip_created_at(obj: Any) -> Any:
    """dict/list を再帰的に走査し、キー "created_at" を除いたコピーを返す。

    決定論検証（F1-6）で「created_at のみ差異許容」を機械的に実現するための
    正規化。report_id は audit_linkability() 側で既に created_at 非依存に
    なっているため（underspec_log_final.md 参照）、created_at フィールド
    そのものを取り除くだけで比較可能になる。
    """
    if isinstance(obj, dict):
        return {k: _strip_created_at(v) for k, v in obj.items() if k != "created_at"}
    if isinstance(obj, list):
        return [_strip_created_at(v) for v in obj]
    return obj


def _diff_paths(a: Any, b: Any, path: str = "$") -> List[str]:
    """2 つの JSON 互換オブジェクトの最初の相違点を列挙する（デバッグ用）。"""
    diffs: List[str] = []
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        diffs.append(f"{path}: type mismatch {type(a).__name__} vs {type(b).__name__}")
        return diffs
    if isinstance(a, dict):
        keys = set(a.keys()) | set(b.keys())
        for k in sorted(keys):
            if k not in a:
                diffs.append(f"{path}.{k}: missing in run_1")
            elif k not in b:
                diffs.append(f"{path}.{k}: missing in run_2")
            else:
                diffs.extend(_diff_paths(a[k], b[k], f"{path}.{k}"))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: length mismatch {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs.extend(_diff_paths(x, y, f"{path}[{i}]"))
    else:
        if a != b:
            diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs[:50]  # 冗長になりすぎないよう先頭 50 件に制限


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------


def run_pipeline(gallery: rset.ReferenceSetGallery, registry_path: Path, mutate_scale: float = MUTATE_SCALE) -> Dict[str, Any]:
    """F1-1〜F1-5 の 1 回分の実行。呼び出しごとに独立した registry_path を使う
    （F1-6 の決定論検証で 2 回分を別ファイルへ書き分けるため）。
    """
    reg = r.GenomeRegistry(registry_path)

    # --- F1-1: 創生 -----------------------------------------------------
    g_a = sampler.sample(101, name="demo-a-sample101")
    g_b = sampler.sample(202, name="demo-b-sample202")
    g_c = sampler.mutate(g_a, seed=303, scale=mutate_scale)
    g_d = sampler.crossover(g_c, g_b, seed=404)

    genome_specs: List[Tuple[str, g.VoiceGenome, str, Optional[int], List[str]]] = [
        ("a", g_a, "sample", 101, []),
        ("b", g_b, "sample", 202, []),
        ("c", g_c, "mutate", 303, ["a"]),  # 親キーは genome_specs 内の key。後で実 genome_id に解決する
        ("d", g_d, "crossover", 404, ["c", "b"]),
    ]

    genome_records: Dict[str, Dict[str, Any]] = {}
    registered_id_by_key: Dict[str, str] = {}

    for key, raw_genome, op, seed, parent_keys in genome_specs:
        # --- F1-2: probe suite 全 5 種レンダ + 波形 hash -----------------
        manifest = probes.full_manifest(raw_genome)

        # --- F1-3a: plausibility (periodicity r_median >= 0.35) ----------
        plaus = pl.plausibility_report(raw_genome)
        plaus_dict = {
            "gate": "periodicity_r_median>=threshold (harness VT-1 v3 と同一床)",
            "threshold": plaus.threshold,
            "probe_names": list(plaus.probe_names),
            "n_notes": plaus.n_notes,
            "n_violations": plaus.n_violations,
            "min_r_median": plaus.min_r_median,
            "passed": plaus.passed,
            "per_note": [
                {"probe_name": n.probe_name, "midi": n.midi, "r_median": n.r_median, "passed": n.passed}
                for n in plaus.notes
            ],
        }

        # --- F1-3b: linkability 監査（novelty） ---------------------------
        audit_report = rset.audit_linkability(raw_genome, gallery, now=None)
        audit_dict = rset.audit_report_to_dict(audit_report)

        # audit 結果を Genome の audit フィールドへ書き戻す
        audited_genome = g.with_audit(
            raw_genome,
            g.AuditSection(
                reference_set_hash=gallery.sha256,
                linkability_report_id=audit_report.report_id,
                residual_gate_passed=None,  # R3 未実装 = not_applicable（memo F2 正直会計）
            ),
        )

        # --- F1-4/F1-5: registry 登録（grip_ref は参照のみ）--------------
        parents = [registered_id_by_key[pk] for pk in parent_keys]
        eval_scores = {
            "plausibility": {
                "passed": plaus.passed,
                "min_r_median": plaus.min_r_median,
                "threshold": plaus.threshold,
            },
            "grip_ref": {
                "ref": GRIP_REF_PATH,
                "gate_semantics_version": GRIP_GATE_SEMANTICS_VERSION,
            },
            "novelty": {
                "linkability_report_id": audit_report.report_id,
                "reference_set_hash": gallery.sha256,
                "overall_pass": audit_report.overall_pass,
            },
        }
        entry = reg.append(
            audited_genome,
            op=op,
            seed=seed,
            parents=parents,
            eval_scores=eval_scores,
            now=None,  # 実 wall-clock（F1-6 の created_at 差異許容の対象）
        )
        registered_id_by_key[key] = entry.genome_id

        genome_records[key] = {
            "op": op,
            "seed": seed,
            "parent_keys": parent_keys,
            "parent_genome_ids": parents,
            "pre_audit_genome_id": r.genome_content_hash(raw_genome),
            "registered_genome_id": entry.genome_id,
            "genome": g.to_dict(audited_genome),
            "probe_manifest": manifest,
            "plausibility": plaus_dict,
            "linkability_audit": audit_dict,
            "registry_entry": r.entry_to_dict(entry),
        }

    lineage_of_d = reg.lineage(registered_id_by_key["d"])
    lineage_dict = [
        {"genome_id": e.genome_id, "op": e.op, "seed": e.seed, "parents": e.parents} for e in lineage_of_d
    ]

    return {
        "genomes": genome_records,
        "lineage_of_d": lineage_dict,
        "registry_path": _repo_relative_path(registry_path),
    }


def _save_wav(sig: np.ndarray, path: Path, sr: int) -> None:
    sf.write(str(path), sig.astype(np.float32), sr)


def _repo_relative_path(path: Path) -> str:
    """path を repo root からの相対パス文字列（POSIX 区切り）で返す（PR#261 レビュー R2）。

    e2e_run.json はリポジトリへコミットされる成果物である。実行環境依存の
    絶対パス（CI/scratchpad 上の一時ディレクトリ等）をそのまま記録すると、
    別環境で読んだ第三者には意味を持たない・再現性検証の妨げになる。
    registry_path は常にこのヘルパーを通し `voice_genesis/proto1/results_final/...`
    形式の repo-relative 文字列として記録する。
    """
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _publish_outputs(replacements: List[Tuple[Path, Path]]) -> None:
    """staging パスから正本パスへ、順に `os.replace` で一括公開する（publish フェーズ）。

    呼び出し側の契約（PR#261 レビュー R1）: 本関数を呼ぶ時点で、registry 構築・
    linkability 監査・WAV レンダ・e2e_record 組み立てが**全て** staging パスへ
    完了していること。実行時間の大半を占めるこれらの工程のどこで例外が起きても
    本関数自体は一度も呼ばれないため、正本一式（registry / WAV / e2e_run.json）は
    完全に旧状態のまま残る。

    本関数の内部（`os.replace` の連続呼び出し自体）は、個々の呼び出しは
    ファイル単位で atomic だが、複数ファイルをまたぐトランザクションではない。
    途中の 1 件で例外が起きた場合、それより前の項目は既に新しい内容へ置換済み、
    それ以降の項目は正本が旧内容のまま残る（「新しいが不完全な正本一式」ではなく
    「一部だけ新しい、個々には完結した正本」に倒れる設計。真の全ファイル
    トランザクションには results_final/ ディレクトリ全体の atomic swap が必要だが、
    同ディレクトリには本デモ以外が書く既存の committed 記録も同居するため、
    本 PR の範囲では見送る）。
    """
    for staging_path, final_path in replacements:
        os.replace(staging_path, final_path)


def main() -> None:
    t_start = time.time()
    print("[proto1_demo] building reference set (n_permutations="
          f"{GALLERY_N_PERMUTATIONS}, matches P5 memo spec)...")
    gallery = rset.build_reference_set(n_permutations=GALLERY_N_PERMUTATIONS)
    t_gallery = time.time()
    print(f"[proto1_demo] reference set built in {t_gallery - t_start:.1f}s "
          f"(sha256={gallery.sha256[:12]}...)")

    tmp_registry_path = RESULTS_DIR / "_scratch_registry_run1.jsonl"
    final_registry_path = RESULTS_DIR / "genome_registry.jsonl"
    # run_2 の正本 registry は staging パスへ書く。正本への置換
    # （os.replace）は WAV・e2e_run.json も含めた publish フェーズ
    # （下記 _publish_outputs 呼び出し）でまとめて行う（PR#261 レビュー
    # C2/R1: 旧実装は final_registry_path を run_2 開始前に unlink し、
    # さらに registry だけを他成果物より早く公開していたため、run_2 完了後
    # 〜 WAV/e2e_run.json 構築完了前のどこで例外が起きても正本一式が
    # 「registry だけ新しい・他は古い/欠落」という半端な状態になり得た）。
    staging_registry_path = RESULTS_DIR / "_staging_registry_run2.jsonl"
    if tmp_registry_path.exists():
        tmp_registry_path.unlink()
    if staging_registry_path.exists():
        staging_registry_path.unlink()

    print("[proto1_demo] running pipeline (run 1/2, scratch registry)...")
    run_1 = run_pipeline(gallery, tmp_registry_path)
    t_run1 = time.time()
    print(f"[proto1_demo] run 1 done in {t_run1 - t_gallery:.1f}s")

    print("[proto1_demo] running pipeline (run 2/2, staging registry)...")
    run_2 = run_pipeline(gallery, staging_registry_path)
    t_run2 = time.time()
    print(f"[proto1_demo] run 2 done in {t_run2 - t_run1:.1f}s")

    # run_2 の registry は公開（os.replace）せず、まだ staging に置いたまま
    # 進める（PR#261 レビュー R1）。e2e_run.json に埋め込む registry_path
    # 文字列だけは公開後に実際に置かれる正本パスへ先に揃えておく（ファイルの
    # 公開自体は WAV レンダ・e2e_record 組み立てが全て成功した後の publish
    # フェーズへ遅延させる）。
    run_2["registry_path"] = _repo_relative_path(final_registry_path)

    # --- F1-6: 決定論検証（created_at のみ差異許容） -------------------------
    comparable_1 = _strip_created_at(run_1)
    comparable_2 = _strip_created_at(run_2)
    # registry_path は run_1(scratch)/run_2(正本) で異なるファイル名を指す
    # 設計であり repo-relative 化しても値は一致しないため比較対象から除く
    comparable_1.pop("registry_path", None)
    comparable_2.pop("registry_path", None)
    determinism_passed = comparable_1 == comparable_2
    differing_paths = [] if determinism_passed else _diff_paths(comparable_1, comparable_2)

    print(f"[proto1_demo] determinism check: {'PASS' if determinism_passed else 'FAIL'}")
    if not determinism_passed:
        print(f"[proto1_demo] differing paths (first {len(differing_paths)}): {differing_paths}")

    tmp_registry_path.unlink()  # スクラッチ用の 1 回目 registry は正本ではないため即時破棄してよい

    # --- F1-7: 成果 WAV（監査 PASS 個体を 1 つ選ぶ。staging へレンダ）--------
    selected_key = None
    for key in ("a", "b", "c", "d"):
        if run_2["genomes"][key]["linkability_audit"]["overall_pass"]:
            selected_key = key
            break

    wav_paths: Dict[str, str] = {}
    # (staging_path, final_path) の公開待ちリスト。publish フェーズでまとめて
    # os.replace する（PR#261 レビュー R1）。
    wav_publish: List[Tuple[Path, Path]] = []
    if selected_key is not None:
        selected_genome_dict = run_2["genomes"][selected_key]["genome"]
        selected_genome = g.from_dict(selected_genome_dict)
        genome_id = run_2["genomes"][selected_key]["registered_genome_id"]

        phrase_result = probes.render_probe(selected_genome, "phrase")
        phrase_sig = np.concatenate(phrase_result.waveforms)
        phrase_final = RESULTS_DIR / f"prototype1_phrase_{genome_id}.wav"
        phrase_staging = RESULTS_DIR / f"_staging_prototype1_phrase_{genome_id}.wav"
        _save_wav(phrase_sig, phrase_staging, bridge.SR)
        wav_publish.append((phrase_staging, phrase_final))
        wav_paths["phrase"] = str(phrase_final.relative_to(PROTO_DIR))

        cross_result = probes.render_probe(selected_genome, "cross_range")
        gap = np.zeros(int(0.15 * bridge.SR), dtype=np.float64)
        cross_sig = np.concatenate([cross_result.waveforms[0], gap, cross_result.waveforms[1]])
        cross_final = RESULTS_DIR / f"prototype1_cross_range_pair_{genome_id}.wav"
        cross_staging = RESULTS_DIR / f"_staging_prototype1_cross_range_pair_{genome_id}.wav"
        _save_wav(cross_sig, cross_staging, bridge.SR)
        wav_publish.append((cross_staging, cross_final))
        wav_paths["cross_range_pair"] = str(cross_final.relative_to(PROTO_DIR))

        print(f"[proto1_demo] selected genome '{selected_key}' ({genome_id}) for WAV export "
              f"(linkability overall_pass=True)")
    else:
        print("[proto1_demo] WARNING: no genome passed linkability audit in run_2; no WAV exported")

    # --- e2e_run.json 組み立て（staging へ書く。公開はまだしない） -----------
    report_generated_at = datetime.now(timezone.utc).isoformat()
    e2e_record = {
        "schema": "proto1-e2e-demo/1",
        "report_generated_at": report_generated_at,
        "mutate_scale": MUTATE_SCALE,
        "reference_set": {
            "sidecar": gallery.sidecar_dict(),
            "e1_chance_band_p95": gallery.e1_chance_band_p95,
            "e2_chance_band_p95": gallery.e2_chance_band_p95,
            "n_permutations": gallery.chance_n_permutations,
        },
        "grip_reference": {
            "ref": GRIP_REF_PATH,
            "gate_semantics_version": GRIP_GATE_SEMANTICS_VERSION,
            "note": "値のコピーではなく参照。実測値は voice_genesis/harness/results_v6/ を直接参照すること（読み取りのみ）。",
        },
        "run_1": run_1,
        "run_2": run_2,
        "determinism_check": {
            "compared": "run_1 vs run_2, all fields except excluded_fields",
            "excluded_fields": {
                "created_at": (
                    "監査/registry レポートの wall-clock 記録。実行時刻が異なるのは"
                    "設計上必然（§CLAUDE.md: wall-clock 依存は created_at 記録のみ許可）。"
                ),
                "registry_path": (
                    "PR#261 R2 で repo-relative 化した後も run_1(scratch)/run_2(正本) は"
                    "異なるファイル名（_scratch_registry_run1.jsonl / genome_registry.jsonl）"
                    "を指す設計であり、値そのものが run 間で一致しない。実行環境依存の"
                    "絶対パス漏えい問題は repo-relative 化で解消したが、この意図的な"
                    "ファイル名差異自体は撤廃できないため比較対象から除外を継続する"
                    "（underspec_log_final.md [UNDERSPEC-F-8]）。"
                ),
            },
            "passed": determinism_passed,
            "differing_paths": differing_paths,
        },
        "selected_pass_genome_for_wav": {
            "genome_key": selected_key,
            "genome_id": run_2["genomes"][selected_key]["registered_genome_id"] if selected_key else None,
            "wav_paths": wav_paths,
        },
        "timing_sec": {
            "reference_set_build": t_gallery - t_start,
            "run_1": t_run1 - t_gallery,
            "run_2": t_run2 - t_run1,
            "total": time.time() - t_start,
        },
    }

    out_path = RESULTS_DIR / "e2e_run.json"
    staging_out_path = RESULTS_DIR / "_staging_e2e_run.json"
    staging_out_path.write_text(
        json.dumps(e2e_record, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )

    # --- Publish フェーズ: registry・WAV・e2e_run.json の全てが staging に
    # 揃った、ここまでの工程が例外なく完走した場合にのみ、正本へ一括公開する
    # （PR#261 レビュー R1）。それより前段（run_2/監査/WAV レンダ/e2e_record
    # 組み立て）のどこかで例外が起きた場合、_publish_outputs は一度も呼ばれず
    # 正本一式（registry / WAV / e2e_run.json）は完全に旧状態のまま残る。
    print("[proto1_demo] publishing staged outputs...")
    _publish_outputs(
        [(staging_registry_path, final_registry_path)]
        + wav_publish
        + [(staging_out_path, out_path)]
    )
    print(f"[proto1_demo] wrote {out_path} (total {time.time() - t_start:.1f}s)")

    if not determinism_passed:
        raise SystemExit("determinism check FAILED — see e2e_run.json.determinism_check.differing_paths")


if __name__ == "__main__":
    main()
