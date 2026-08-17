"""adapter/render.py — VG-F1 パイプライン: score -> f0/units/joins/warp -> WORLD -> WAV。

設計書 §2 render.py に対応する。

CLI:
    python -m adapter.render --score sakura --voice presets/neutral.json \
        --wav <vocadito_2.wav> --notes-csv <vocadito_2_notesA1.csv> --out x.wav

同一 spec(JSON) + seed -> 同一バイト列（決定論契約。乱数は perf_genes 側のみで
`np.random.default_rng(seed)` を使用し、他に非決定要素（wall-clock 等）を
含まない）。
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pyworld as pw
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_SINGER_DIR = _HERE.parent.parent / "singer"
for _p in (_SINGER_DIR, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import score as sc  # noqa: E402  (singer、read-only import)
import score_umi as sc_umi  # noqa: E402  (singer、read-only import)
import score_d3_sustain as sc_d3_sustain  # noqa: E402  (singer、read-only import。S3 Phase B1)
import score_d3_kana as sc_d3_kana  # noqa: E402  (singer、read-only import。S3 Phase B1)
import performance as perf  # noqa: E402  (singer、read-only import)

import consonants as cons  # noqa: E402  (追補 F1.1-B)
import donor_bank as db  # noqa: E402
import donor_bank_lab as dbl  # noqa: E402  (追補 F1.2-C)
import donor_bank_utau as dbu  # noqa: E402  (追補 F1.2-B)
import joins as jn  # noqa: E402
import perf_genes as pg  # noqa: E402
import units as un  # noqa: E402
import voice_spec as vs  # noqa: E402
import vowel_class as vc  # noqa: E402  (追補 F1.1-A)

SR = 24000
FRAME_PERIOD_MS = 5.0
PEAK_NORM = 0.6

DONOR_CHOICES = ("vocadito", "ritsu", "pjs")
CONSONANT_SOURCE_CHOICES = ("recorded", "synthetic", "none")


class DonorProvenanceError(ValueError):
    """P1 修正 (review #262 R2): `spec.donor` の宣言が実際の描画入力
    （--donor/--wav/--voicebank-root）と一致しない（fail-closed）。"""


class OutputCollisionError(ValueError):
    """P1 修正 (review #262 R2): `--out` が保護入力（--wav/--voice/--notes-csv/
    --voicebank-root 配下）と衝突する（fail-closed。書き込み前に検出する）。"""


def _validate_spec_donor(
    spec: "vs.FoundryVoiceSpec",
    donor: str,
    wav_path: Optional[str | Path],
    voicebank_root: Optional[str | Path],
) -> dict:
    """P1 修正 (review #262 R2): `spec.donor` の宣言を実際の描画入力の実体
    ハッシュと fail-closed で照合する。

    従来は `spec.donor`（dataset/sha256）が読み込まれるだけで一度も検証され
    ず、実際のドナーは `--donor`/`--wav`/`--voicebank-root` から独立に決まる
    ため、コミット済み neutral/warped spec の vocadito sha256 を宣言したまま
    任意の wav や ritsu/pjs バンクを描画でき、provenance が嘘をつけた
    （review #262 R2 P1 指摘）。

    `donor` dict のスキーマ拡張（foundry-voice/0.1・破壊的変更ではなく
    dataset 別の許容キーを追加するのみ。既存 vocadito 形は不変）:

    - `dataset="vocadito"`: 既存どおり `{"dataset":"vocadito","clip":int,
      "sha256":str}`。`sha256` は `--wav` の内容 sha256 と一致必須。
    - `dataset="ritsu"`: `{"dataset":"ritsu","voicebank_sha256":str}`。
      `voicebank_sha256` は `--voicebank-root` 配下の全 pitch dir oto.ini を
      `donor_bank_utau.voicebank_identity_hash` で集約した値と一致必須
      （oto.ini は unit セグメンテーションの権威情報 = 意味のある identity pin。
      wav 全バイトを毎回ハッシュするより軽量で WORLD 分析より前に検証できる）。
    - `dataset="pjs"`: `{"dataset":"pjs","corpus_sha256":str}`。同様に
      `donor_bank_lab.corpus_identity_hash`（全 pjsNNN/pjsNNN.lab の集約）
      と一致必須。

    `dataset` が `--donor` と不一致、宣言ハッシュ欠落/型不正、または実体
    ハッシュ不一致のいずれかで即座に `DonorProvenanceError` を送出する
    （AGENTS.md L465-468 参照。実データ分析より前段で検証するため、無関係な
    入力での無駄な WORLD 分析も防ぐ）。一致時は record 用の照合結果 dict を返す。

    冪等（同一ファイル状態なら何度呼んでも同じ結果）なため、`render()` は本
    関数を 2 回呼ぶ（review #262 R6 修正）: ①WORLD 分析より前の fail-fast、
    ②bank 構築完了後・成果物公開前の post-build revalidation（この 2 回目が
    ①と bank 構築本体の read との間の TOCTOU 窓を閉じる。`voicebank_identity_hash`/
    `corpus_identity_hash`/`sha256_of` はいずれもキャッシュを介さず毎回
    ディスクを直接再走査するため、bank 構築が cache hit だった場合でも
    2 回目の呼び出しが独立した鮮度検証になる）。
    """
    declared = dict(spec.donor)
    declared_dataset = declared.get("dataset")
    if declared_dataset != donor:
        raise DonorProvenanceError(
            f"spec.donor.dataset={declared_dataset!r} は --donor={donor!r} と不一致です"
            "（provenance 検証: fail-closed）"
        )

    if donor == "vocadito":
        if wav_path is None:
            raise DonorProvenanceError("donor='vocadito' の provenance 検証には --wav が必須です")
        declared_sha = declared.get("sha256")
        actual_sha = db.sha256_of(wav_path)
        if not isinstance(declared_sha, str) or declared_sha != actual_sha:
            raise DonorProvenanceError(
                f"spec.donor.sha256={declared_sha!r} は --wav の実体 sha256={actual_sha!r} と"
                "不一致です（provenance 検証: fail-closed）"
            )
        return dict(dataset="vocadito", declared_sha256=declared_sha, actual_sha256=actual_sha)

    if donor == "ritsu":
        if voicebank_root is None:
            raise DonorProvenanceError("donor='ritsu' の provenance 検証には --voicebank-root が必須です")
        declared_sha = declared.get("voicebank_sha256")
        actual_sha, pitch_dirs = dbu.voicebank_identity_hash(voicebank_root)
        if not isinstance(declared_sha, str) or declared_sha != actual_sha:
            raise DonorProvenanceError(
                f"spec.donor.voicebank_sha256={declared_sha!r} は --voicebank-root の実体ハッシュ="
                f"{actual_sha!r}（pitch_dirs={pitch_dirs}）と不一致です（provenance 検証: fail-closed）"
            )
        return dict(dataset="ritsu", declared_sha256=declared_sha, actual_sha256=actual_sha)

    # donor == "pjs"
    if voicebank_root is None:
        raise DonorProvenanceError("donor='pjs' の provenance 検証には --voicebank-root が必須です")
    declared_sha = declared.get("corpus_sha256")
    actual_sha = dbl.corpus_identity_hash(voicebank_root)
    if not isinstance(declared_sha, str) or declared_sha != actual_sha:
        raise DonorProvenanceError(
            f"spec.donor.corpus_sha256={declared_sha!r} は --voicebank-root の実体ハッシュ="
            f"{actual_sha!r} と不一致です（provenance 検証: fail-closed）"
        )
    return dict(dataset="pjs", declared_sha256=declared_sha, actual_sha256=actual_sha)


def _reject_output_collision(
    out_path: str | Path,
    protected_files: "List[Optional[str | Path]]",
    protected_roots: "List[Optional[str | Path]]",
) -> None:
    """P1 修正 (review #262 R2): `--out` が保護入力と衝突する場合は書き込み前に
    fail-closed で拒否する（AGENTS.md 永続成果物の「公開サイト」設計チェック
    リスト項目2「衝突ガードは必須引数」準拠）。

    resolved containment（symlink 解決後の一致・`is_relative_to` 相当）で判定
    する（AGENTS.md Persistent Artifact Safety Gate 項目2）。字句検証
    （`../` 遡上判定）は本関数では行わない —— `out_path`/保護パスはいずれも
    CLI から渡される独立した絶対/相対パスであり、path-join で組み立てる
    値ではないため、字句検証が防ぐ「base の外への脱出」は該当しない
    （resolved 比較のみで十分）。

    `protected_files`（単一ファイルとの完全一致）と `protected_roots`
    （ディレクトリ配下への包含）の両方を検査する。`None` 要素はスキップする
    （donor 種別ごとに未使用の引数があるため）。
    """
    out_resolved = Path(out_path).resolve()
    for f in protected_files:
        if f is None:
            continue
        f_path = Path(f)
        if not f_path.exists():
            continue  # 未使用/未指定パス（vocadito の notes_csv 省略時等）
        if out_resolved == f_path.resolve():
            raise OutputCollisionError(
                f"--out ({out_path}) が保護入力 ({f}) と衝突しています（fail-closed で拒否）"
            )
    for root in protected_roots:
        if root is None:
            continue
        root_path = Path(root)
        if not root_path.exists():
            continue
        root_resolved = root_path.resolve()
        try:
            out_resolved.relative_to(root_resolved)
        except ValueError:
            continue
        raise OutputCollisionError(
            f"--out ({out_path}) が保護ディレクトリ ({root}) 配下です（fail-closed で拒否）"
        )


def _atomic_write_wav(y: np.ndarray, sr: int, out_path: str | Path) -> str:
    """P2 修正 (review #262 R2): staging + `os.replace` で成果物 WAV を atomic
    公開する（AGENTS.md Persistent Artifact Safety Gate 項目6「全構築後公開」
    準拠。`src/svp_rpe/utils/atomic_io.atomic_write_bytes` と同じ流儀だが、
    `src/` は変更禁止のため adapter 内に独立実装する）。

    `sf.write` の失敗（プロセス kill・ディスク満杯等）やインタラプトが
    途中で起きても、`out_path` に既存の有効な出力があればそれを破壊せず、
    staging tempfile を best-effort で削除してから re-raise する
    （`except BaseException` — `KeyboardInterrupt`/`SystemExit` も含む）。

    P2 修正 (review #262 R8・`r3789486148`): `bank.wav_sha256` はドナー入力の
    ハッシュであり、生成された成果物 WAV のバイト列を識別しない
    （AGENTS.md「入力と出力の両ハッシュ記録」要件を満たさない）。ここで
    `os.replace` により実際に公開される staging ファイルのバイト列そのものを
    read して sha256 を計算し、呼び出し元へ返す（公開後の別読みだと
    replace 後の実ファイルとの間に別の TOCTOU 窓が生じるため、公開直前の
    staging バイト列を正本にする）。
    """
    out_path = Path(out_path)
    tmp_name, output_sha256 = _stage_wav_bytes(y, sr, out_path)
    try:
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return output_sha256


def _stage_wav_bytes(y: np.ndarray, sr: int, out_path: Path) -> Tuple[str, str]:
    """P1 修正 (review #265): `_atomic_write_wav`/`_atomic_write_wav_and_timing`
    共通の staging 処理を切り出したもの。`out_path` と同じディレクトリへ WAV を
    書くが、まだ `out_path` への置換は行わない（戻り値 `(tmp_path, sha256)`。
    最終的な `os.replace` は呼び出し側が担う）。失敗時は staging tempfile を
    best-effort で削除してから re-raise する（`_atomic_write_wav` 従来実装と
    同一の失敗時挙動）。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, prefix=f"{out_path.name}.", suffix=".tmp")
    os.close(fd)  # sf.write はパス経由で自前ハンドルを開くため fd は即閉じる
    try:
        # staging 名の拡張子は `.tmp`（拡張子から format を推測できない）ため
        # `format="WAV"` を明示する。
        sf.write(tmp_name, y, sr, subtype="PCM_16", format="WAV")
        output_sha256 = hashlib.sha256(Path(tmp_name).read_bytes()).hexdigest()
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return tmp_name, output_sha256


def _stage_timing_csv_bytes(rows: List[dict], timing_out_path: Path) -> str:
    """P1 修正 (review #265): timing CSV の staging 版。`write_timing_csv` と
    同じ列/内容を書くが、まだ `timing_out_path` への置換は行わない（戻り値は
    staging tempfile のパス）。失敗時は staging tempfile を best-effort で
    削除してから re-raise する。
    """
    timing_out_path = Path(timing_out_path)
    timing_out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=timing_out_path.parent, prefix=f"{timing_out_path.name}.", suffix=".tmp"
    )
    os.close(fd)
    try:
        with open(tmp_name, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TIMING_CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return tmp_name


def _atomic_write_wav_and_timing(
    y: np.ndarray, sr: int, out_path: str | Path,
    timing_rows: List[dict], timing_out_path: str | Path,
) -> str:
    """P1 修正 (review #265): WAV + timing CSV を両方 staging してから両方
    公開する。旧実装は `render()` が WAV を atomic 公開した後、CLI 側
    (`main()`) が別途 `write_timing_csv` を（非 atomic に）呼んでいたため、
    CSV 書き込みが後から失敗すると WAV だけが公開済みで残る非対称があった
    （review #265 P1 指摘）。ここでは両ファイルとも staging tempfile を
    書き終えるまで最終パス（`out_path`/`timing_out_path`）には一切触れず、
    両方の staging が成功して初めて順に `os.replace` する。戻り値は WAV の
    `output_sha256`（`_atomic_write_wav` と同じ契約）。

    P2 修正 (review #265 R3): 2 つの `os.replace` の間の窓で 1 個目
    （WAV）が成功し 2 個目（timing CSV）が失敗すると、旧実装は WAV だけが
    新世代へ差し替わった不整合な公開状態を残していた（`--out`/`--timing-out`
    は「同じ合成結果を指す対」であるべきで、片方だけ新しいのは
    `_build_timing_rows` docstring の「タイミング export は成果物 WAV の
    合成結果を読むだけ」という契約と矛盾する）。ここでは公開の直前に
    既存の `out_path`/`timing_out_path`（あれば）を**同一ディレクトリへ
    退避 rename**（バイトコピーしない — WAV は大きくなり得るため）してから
    両方を `os.replace` し、どちらか一方でも失敗したら新たに公開された側を
    削除し、退避しておいた旧世代を両方とも復元する（`recording_kit/intake.py`
    `run()` の公開フェーズ巻き戻し・`gate_synth.py` `_swap_step_dir_into_place`
    の `.old` 退避と同型パターン。復元判定はファイルシステム状態
    （退避有無・新パスの実在）で行い、Python 変数の記帳には頼らない）。
    """
    out_path = Path(out_path)
    timing_out_path = Path(timing_out_path)
    wav_tmp, output_sha256 = _stage_wav_bytes(y, sr, out_path)
    try:
        csv_tmp = _stage_timing_csv_bytes(timing_rows, timing_out_path)
    except BaseException:
        try:
            os.unlink(wav_tmp)
        except OSError:
            pass
        raise

    # 公開直前に既存宛先（あれば）を同一ディレクトリの退避名へ rename する
    # （バイトコピーしない）。`os.getpid()` サフィックスは同一プロセス内での
    # 再入・並行呼び出しでの退避名衝突を避けるための実務上の目安であり、
    # 本関数自体の並行実行に対する排他は呼び出し側の責務（他の swap 系
    # ヘルパーと同様）。
    wav_backup = out_path.parent / f"{out_path.name}.prev-{os.getpid()}.bak"
    csv_backup = timing_out_path.parent / f"{timing_out_path.name}.prev-{os.getpid()}.bak"
    for stale in (wav_backup, csv_backup):
        if stale.exists():
            os.unlink(stale)
    wav_had_previous = out_path.exists()
    csv_had_previous = timing_out_path.exists()
    if wav_had_previous:
        os.rename(out_path, wav_backup)
    if csv_had_previous:
        os.rename(timing_out_path, csv_backup)

    try:
        os.replace(wav_tmp, out_path)
        os.replace(csv_tmp, timing_out_path)
    except BaseException:
        # 巻き戻し: 各宛先について「自分がこの呼び出しで新世代へ差し替えた
        # か」を宛先の実在（`new_path.exists()`）で判定する（`os.replace` は
        # 原子的なので、失敗した置換対象は「未着手のまま」——既に退避済みで
        # 存在しない——のいずれかであり、部分バイト列が残ることはない）。
        for new_path, backup_path, had_previous in (
            (out_path, wav_backup, wav_had_previous),
            (timing_out_path, csv_backup, csv_had_previous),
        ):
            if new_path.exists():
                os.unlink(new_path)
            if had_previous:
                os.rename(backup_path, new_path)
        for tmp in (wav_tmp, csv_tmp):
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        raise

    # 公開成功: 退避しておいた旧世代はもう不要。
    for backup, had_previous in ((wav_backup, wav_had_previous), (csv_backup, csv_had_previous)):
        if had_previous:
            try:
                os.unlink(backup)
            except OSError:
                pass
    return output_sha256


def _vowel_distribution_from_labels(labels: dict) -> dict:
    """unit.index -> ラベル(str) の辞書から母音別件数分布を作る（3 ドナー共通）。"""
    dist: Dict[str, int] = {}
    for lbl in labels.values():
        dist[lbl] = dist.get(lbl, 0) + 1
    return dist


def _vcv_required_contexts(segments: list) -> List[Tuple[Optional[str], str]]:
    """追補 F1.4-A: レンダリング対象スコアが実際に必要とする VCV 文脈キーの
    重複無し決定論順リスト（`donor_bank_utau.build_donor_bank_utau_vcv` の
    `required_contexts` へそのまま渡す・キャッシュキー材料にも使う）。
    """
    seen: List[Tuple[Optional[str], str]] = []
    seen_set = set()
    for ctx in un.vcv_context_sequence(segments):
        if ctx not in seen_set:
            seen_set.add(ctx)
            seen.append(ctx)
    seen.sort(key=lambda c: (c[0] or "", c[1]))
    return seen


def prepend_recorded_consonant(
    resolved: "un.ResolvedSegment", clip: "dbu.ConsonantClip", note_index: int
) -> Tuple["un.ResolvedSegment", "cons.ConsonantEvent"]:
    """追補 F1.2-D: 録音済み子音クリップ（自然長・非伸縮）を resolved 母音核
    frame 列の先頭へ前置する。総 frame 数はクリップ分だけ増える
    （joins.assemble の 30ms クロスフェードが後続の接続を担う。決定論・純関数）。
    """
    new_sp = np.concatenate([clip.sp, resolved.sp], axis=0)
    new_ap = np.concatenate([clip.ap, resolved.ap], axis=0)
    new_resolved = dataclasses.replace(resolved, sp=new_sp, ap=new_ap, n_frames=new_sp.shape[0])
    event = cons.ConsonantEvent(
        note_index=note_index, onset=clip.onset, consonant_class="recorded", n_frames_processed=clip.n_frames,
    )
    return new_resolved, event


def _build_vcv_placements(
    segments: list, selections: List["un.VCVUnitSelection"], bank: "db.DonorBank",
) -> Tuple[List["jn.NotePlacement"], List["un.ResolvedVCVSegment"], dict]:
    """追補 F1.4-B（F1.4 R3 で絶対グリッド配置へ全面改訂・PR #262 R3）: VCV unit
    の配置。

    R3 改訂の背景・設計判定（record 参照）: R2 までは「フレーズ先頭ノートのみ
    cursor を preutterance 分前へずらし、mid-phrase ノートは
    `joins.assemble_v2` 側の追加トリム（`_resolve_extra_trim`）で
    preutterance を消費する」方式だった。この方式は run 内部の join のたびに
    直前アキュムレータ末尾を実際にトリムする（総尺を縮める）ため、複数 run
    から成る render 全体では各 run の縮みが後続 run へ伝播し、総尺が score
    総尺から累積的に縮んでいた（sakura で 19% 相当の圧縮）。

    R3 は全ノート（フレーズ先頭・mid-phrase 問わず）に対して統一的に
    「拍位置（score 由来の絶対タイムライン上の cursor 位置）− lead
    （= 選択された unit の `preutterance_frames`。真のアタック点までの
    オフセット）」の絶対座標へ配置する（`start_frame`/`end_frame` の**両方**
    が同じだけシフトする。R2 までの「end_frame は shift 非依存」は撤廃 ——
    絶対グリッド上では unit 自身の長さ `resolved.n_frames`（=
    `target_n_frames`。shift 非依存で不変）が保たれるため、
    `end_frame = start_frame + resolved.n_frames` が自然に成り立つ）。

    フレーズ先頭ノートのみ、shift をブレスギャップ長にクリップする（ブレス
    無音区間より前へは食い込ませない・既存挙動維持）。mid-phrase ノートは
    クリップしない（直前 note が既に配置済みの音域へ食い込むことを許す ——
    実際に重なるか・重なり幅は `joins.assemble_absolute` が両ノートの絶対
    座標の交差から自動的に決め、重なった区間のみ log-sp/ap クロスフェードで
    ブレンドする。トリムはしない）。

    `resolved.n_frames` は shift の影響を受けないため、shift 分だけ unit の
    audible span 全体が前方へ動く。後続ノートで shift が埋め合わされない
    末尾（例: フレーズ最終ノートの末尾 `lead` フレーム分）に生じる空白は、
    `joins.assemble_absolute` の末尾 carry-forward（前方 hold）で埋める
    （既存の無声ギャップ充填規約と同一 = 追加実装なし・[実装決定・record
    記録]）。
    """
    placements: List[jn.NotePlacement] = []
    resolved_list: List[un.ResolvedVCVSegment] = []
    prev_end_sample = 0
    n_shift_applied = 0
    n_shift_clipped = 0
    shift_frames: List[int] = []
    shift_per_note: List[int] = []
    head_clipped_note_indices: List[int] = []

    for note_index, (seg, sel) in enumerate(zip(segments, selections)):
        gap_samples = seg.start_sample - prev_end_sample
        gap_frames = frames_for_samples(gap_samples, SR) if gap_samples > 0 else 0
        # P2 修正 (review #262 R14・`r3789636058`): 名目開始フレームは「丸めた
        # duration の累積（旧 cursor）」ではなく `seg.start_sample` から毎ノート
        # 独立に絶対換算する。累積方式は各ノートの `frames_for_samples()` 丸め
        # 誤差（最大 ±0.5 frame）が後続ノートへそのまま伝播し、score が非整数
        # フレーム長のビートを含む場合（例: umi 楽曲の 1 拍 = 16,364 サンプル、
        # 136.36... frame）に累積ドリフトを生む（record 実測: umi 終盤で
        # −3 frame(15ms)・末尾に補償パディングが残る）。絶対換算は各ノートが
        # 独立に score グリッドへ re-snap されるため誤差が伝播しない。
        nominal_frame = frames_for_samples(seg.start_sample, SR)
        note_dur_frames = max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1)
        resolved = un.resolve_vcv_unit_to_note(
            bank, sel.unit, note_dur_frames, note_index=note_index, frame_period_ms=FRAME_PERIOD_MS
        )
        if resolved.head_clipped:
            head_clipped_note_indices.append(note_index)
        resolved_list.append(resolved)

        preutt = sel.unit.preutterance_frames or 0
        lead = min(preutt, gap_frames) if seg.is_phrase_first else preutt
        shift = min(lead, nominal_frame)  # バッファ先頭（0）より前へは配置しない
        start_frame = nominal_frame - shift
        shift_per_note.append(shift)
        if shift > 0:
            n_shift_applied += 1
            shift_frames.append(shift)
            if shift < preutt:
                n_shift_clipped += 1
        end_frame = start_frame + resolved.n_frames

        placements.append(
            jn.NotePlacement(
                start_frame=start_frame, end_frame=end_frame, sp=resolved.sp, ap=resolved.ap,
                has_join_to_prev=not seg.is_phrase_first, overlap_frames=sel.unit.overlap_frames,
                # R3: assemble_absolute はこのフィールドを読まない（絶対座標
                # の交差だけで重なりを決める）。record/後方互換のため保持する。
                preutterance_frames=sel.unit.preutterance_frames,
            )
        )
        prev_end_sample = seg.end_sample

    stats = dict(
        n_preutterance_applied=n_shift_applied,
        n_preutterance_clipped=n_shift_clipped,
        preutterance_shift_frames=shift_frames,
        preutterance_shift_frames_mean=(
            float(np.mean(shift_frames)) if shift_frames else 0.0
        ),
        n_head_clipped=len(head_clipped_note_indices),
        head_clipped_note_indices=head_clipped_note_indices,
        # P1 修正 (review #262 R4・`r3789341843`): note ごとの実効配置シフト
        # （ブレスギャップ/バッファ先頭でクリップ済みの `shift`）を placements
        # と同じ並びで公開する。`_note_frame_track` の `origin_shift_frames`
        # へそのまま渡すことで、f0/振幅トラックのサンプリング原点を sp/ap の
        # 絶対配置（`start_frame = cursor - shift`）と一致させる。
        shift_per_note=shift_per_note,
    )
    return placements, resolved_list, stats


def _frame_period_s() -> float:
    return FRAME_PERIOD_MS / 1000.0


def frames_for_samples(n_samples: int, sr: int) -> int:
    return max(int(round(n_samples / sr / _frame_period_s())), 0)


def _note_frame_track(
    per_sample: np.ndarray, seg, note_dur_frames: int, n_frames: int, sr: int, frame_period_ms: float,
    origin_shift_frames: int = 0,
) -> np.ndarray:
    """P1 修正 (review #262): 1 note の resolve 後フレーム列（`n_frames`）に
    対応する perf_genes per-sample トラック（`base_f0_persample`/`amp_env`。
    圧縮前の生スコアサンプル時間軸で構築済み）の値を、**このノート自身の
    圧縮前・実スパン内**で決定論的に抽出する（他ノートの overlap 圧縮とは
    無関係。圧縮後タイムラインへの整列は `joins.assemble_control_tracks_v2`
    が sp/ap と同じ run/overlap 機構で後段に行う）。

    `n_frames` は録音済み子音の前置（`prepend_recorded_consonant`）で
    `note_dur_frames`（= seg の生スコア尺から素朴に求めたフレーム数）より
    長くなることがある。その場合、末尾側の `note_dur_frames` フレームが
    seg の実スパン [start_sample, end_sample) に対応するよう原点をずらす
    （先頭の余剰フレームは子音が鳴る時間帯 = seg 開始より前へ自然に延びる。
    0 側でクランプ）。

    `origin_shift_frames`（P1 修正・review #262 R4・`r3789341843`）:
    `donor="ritsu"`（VCV 経路）専用。`render.py` `_build_vcv_placements` は
    unit を「拍位置 − lead（preutterance 相当・ブレスギャップでクリップ済み）」
    の絶対座標へ配置するが、本関数は従来 `seg.start_sample`（= score 上の拍
    位置そのもの）を原点にサンプリングしていたため、f0/振幅トラックの**内容**
    が sp/ap の絶対配置より `lead` フレーム分だけ「早取り」されていた
    （sp/ap 自身のフレーム数・配置位置は正しいままだったため、旧実装では
    join を経ても致命的な破綻は生じないが、拍位置での f0/振幅の値自体が
    ズレる——`_build_vcv_placements` が計算した実効 shift（`lead` をブレス
    ギャップ/バッファ先頭でクリップした後の値）を渡すことで、サンプリング
    原点を配置と同じだけ前へずらし、拍位置に着地するフレームが score 上の
    拍位置ちょうどの値を持つよう補正する（`origin_shift_frames=0` は従来
    どおり無補正 = vocadito/pjs 経路は完全不変）。
    """
    if n_frames <= 0:
        return np.zeros(0, dtype=np.float64)
    if per_sample.shape[0] == 0:
        return np.zeros(n_frames, dtype=np.float64)
    samples_per_frame = sr * frame_period_ms / 1000.0
    extra_head = max(0, n_frames - note_dur_frames)
    k = np.arange(n_frames) - extra_head - origin_shift_frames
    sample_idx = np.round(seg.start_sample + k * samples_per_frame).astype(np.int64)
    sample_idx = np.clip(sample_idx, 0, per_sample.shape[0] - 1)
    return per_sample[sample_idx]


def _phrase_head_preutterance_shifts(
    segments: list, note_origin_shift_frames: List[int], sr: int, frame_period_ms: float,
) -> List[Tuple[object, int, int]]:
    """P2/P3 修正 (review #262 R14/R19) 共通の対象ノート判定。

    origin-shift（`_build_vcv_placements` が計算した実効 preutterance シフト）
    を受けるフレーズ先頭 seg を列挙し、`(seg, ext_start, ar)` を返す
    （`ext_start` = 延長区間の開始サンプル、`ar` = `NOTE_ATTACK_RELEASE_MS`
    由来のアタック長サンプル数・セグメント長でクランプ済み）。
    `_extend_phrase_head_amp_for_preutterance`（振幅）と
    `_extend_phrase_head_f0_for_preutterance`（F0・R19 で追加）が本関数を
    共有し、分岐条件の二重管理を避ける。
    """
    samples_per_frame = sr * frame_period_ms / 1000.0
    attack_release = int(sr * perf.NOTE_ATTACK_RELEASE_MS / 1000.0)
    out: List[Tuple[object, int, int]] = []
    for seg, shift_frames in zip(segments, note_origin_shift_frames):
        if not seg.is_phrase_first or shift_frames <= 0:
            continue
        shift_samples = int(round(shift_frames * samples_per_frame))
        ext_start = max(0, seg.start_sample - shift_samples)
        if ext_start >= seg.start_sample:
            continue
        n = seg.end_sample - seg.start_sample
        ar = min(attack_release, n // 4) if n >= 4 else 0
        out.append((seg, ext_start, ar))
    return out


def _extend_phrase_head_amp_for_preutterance(
    amp_env: np.ndarray, segments: list, note_origin_shift_frames: List[int], sr: int, frame_period_ms: float,
) -> np.ndarray:
    """P2 修正 (review #262 R14・`r3789636053`、R19 でフェード二重化を解消):
    origin-shift 済みフレーズ頭 preutterance の消音を防ぐ。

    `_note_frame_track` は `origin_shift_frames`（`_build_vcv_placements` が
    計算した実効 shift）分だけサンプリング原点を `seg.start_sample` より前へ
    ずらす。しかし `perf.build_amplitude_envelope` はフレーズ先頭より前
    （ブレス無音区間）を常にゼロ初期化したままのため、ずらしたサンプリング
    原点が拾うのはこのゼロ区間で、preutterance に置いた録音済み子音/遷移
    （典型 100ms）がゼロ乗算で消音されていた（review #262 R13・
    `r3789636053`）。

    フレーズアーチ自体（`amp[phrase_start:phrase_end]` の cosine half-arch）
    の形は変更しない。R14 時点では拡張区間 `[ext_start, seg.start_sample)`
    のみを埋めていたため、`build_amplitude_envelope` が書いた元の attack
    フェード `[seg.start_sample, seg.start_sample+ar)` が手つかずのまま残り、
    「外挿区間で立ち上がった直後に seg.start_sample でゼロ近傍へ落ち、
    そこから再度フェードインする」ノッチが生じていた（review #262 R19
    指摘 1）。本修正はフェードそのものを延長端へ移設し、単一の立ち上がりに
    する: `[ext_start, ext_start+ar)` を 0→`connect_level` へフェードイン
    （`ar` = 元のアタック時間そのまま）、続く `[ext_start+ar,
    seg.start_sample+ar)` を `connect_level` で定数充填する（旧フェード区間
    `[seg.start_sample, seg.start_sample+ar)` もこの充填で上書きされ、
    二重フェードが解消する）。`connect_level` は既存 `amp_env` の
    `seg.start_sample+ar` 時点の値（フェード完了直後の定常値）をそのまま
    読む——`build_amplitude_envelope` のアーチ/フェード実装を再現せず、
    マジックナンバーの二重実装を避けつつ、`seg.start_sample+ar` 以降
    （不変）との段差をゼロにする。
    """
    amp = amp_env.copy()
    if amp.shape[0] == 0:
        return amp
    for seg, ext_start, ar in _phrase_head_preutterance_shifts(
        segments, note_origin_shift_frames, sr, frame_period_ms
    ):
        connect_idx = min(seg.start_sample + ar, amp.shape[0] - 1)
        connect_level = float(amp[connect_idx])
        fill_end = min(seg.start_sample + ar, amp.shape[0])
        if fill_end > ext_start:
            amp[ext_start:fill_end] = connect_level
        if ar > 0:
            fade_end = min(ext_start + ar, amp.shape[0])
            fade_len = fade_end - ext_start
            if fade_len > 0:
                amp[ext_start:fade_end] = connect_level * np.linspace(0.0, 1.0, fade_len)
    return amp


def _extend_phrase_head_f0_for_preutterance(
    f0_persample: np.ndarray, segments: list, note_origin_shift_frames: List[int], sr: int, frame_period_ms: float,
) -> np.ndarray:
    """P3 修正 (review #262 R19 指摘 3): origin-shift 済みフレーズ頭
    preutterance の F0 無声化 (0Hz) を防ぐ、振幅側と対になる F0 後方延長。

    `perf.build_f0_contour` はフレーズ先頭より前（ブレス無音区間）を常に
    ゼロ初期化したままのため、`_note_frame_track` がシフト分だけ前へずらして
    サンプリングすると preutterance 区間の F0 が 0 のまま拾われる
    （`_extend_phrase_head_amp_for_preutterance` の R14 修正により当該区間は
    もはや無音でなくなっており、F0=0 は無声ノイズとして可聴になりうる）。

    対象ノート判定は `_extend_phrase_head_amp_for_preutterance` と同一
    （`_phrase_head_preutterance_shifts` を共有）。延長区間 `[ext_start,
    seg.start_sample)` を、ノート開始時点の F0（`seg.start_sample` の輪郭値。
    それ自体がまだ 0 の場合は同ノート内で最初に現れる有声値）で定数外挿する。
    ビブラート等の時間変化は延長区間には適用しない（定数で足りる・仕様上
    preutterance は「直前のノート開始ピッチへ滑らかに繋がる」だけで十分）。
    """
    f0 = f0_persample.copy()
    if f0.shape[0] == 0:
        return f0
    for seg, ext_start, _ar in _phrase_head_preutterance_shifts(
        segments, note_origin_shift_frames, sr, frame_period_ms
    ):
        start_idx = min(seg.start_sample, f0.shape[0] - 1)
        onset_f0 = float(f0[start_idx])
        if onset_f0 <= 0.0:
            tail = f0[start_idx:]
            voiced = tail[tail > 0.0]
            onset_f0 = float(voiced[0]) if voiced.size > 0 else 0.0
        if onset_f0 > 0.0:
            f0[ext_start:seg.start_sample] = onset_f0
    return f0


def _midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


# S3 Phase B1: score registry 化（DESIGN_S3_backfill.md §2.3 B1）。
# `--score` の choices ハードコード（旧: `["sakura", "umi"]` 直書き分岐）を
# score 名 -> ノート列ビルダーの対応表へ置き換え、新規スコアモジュールを
# 追加登録できる構造にする。AC = 既存 sakura/umi の同一 spec+seed 出力が
# 本環境で変更前後バイト同一（f1_4 record の 3 回一致プロトコル準拠）。
# 各ビルダーは `() -> Tuple[List[ScoreNote], float]`（ノート列, tempo_bpm）を
# 返す（既存 `build_score` の戻り値契約を維持）。
SCORE_REGISTRY: Dict[str, Callable[[], Tuple[list, float]]] = {
    "sakura": lambda: (sc.build_sakura_score(), sc.TEMPO_BPM),
    "umi": lambda: (sc_umi.build_umi_score(), sc_umi.TEMPO_BPM),
    # S3 Phase B1 D3: サステイン譜（5 母音×低中高 3 段ロングトーン）+
    # かな短句譜（VCV 被覆内・拗音/撥音を含む短句。§ score_d3_kana.py docstring
    # に促音非対応の設計逸脱を記録）。
    "d3_sustain": lambda: (sc_d3_sustain.build_d3_sustain_score(), sc_d3_sustain.TEMPO_BPM),
    "d3_kana": lambda: (sc_d3_kana.build_d3_kana_score(), sc_d3_kana.TEMPO_BPM),
}


def register_score(name: str, builder: Callable[[], Tuple[list, float]]) -> None:
    """新規スコアを registry へ追加登録する（テスト・将来スコア用のフック）。

    既存キーへの再登録は明示的なミスを検知するため拒否する
    （上書きしたい場合は `SCORE_REGISTRY[name] = builder` を直接使うこと）。
    """
    if name in SCORE_REGISTRY:
        raise ValueError(f"score {name!r} is already registered (use SCORE_REGISTRY[name] = ... to overwrite)")
    SCORE_REGISTRY[name] = builder


def build_score(name: str) -> Tuple[list, float]:
    try:
        builder = SCORE_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown score: {name!r} (expected one of {sorted(SCORE_REGISTRY)})"
        ) from None
    return builder()


# ---------------------------------------------------------------------------
# S3 Phase B1: タイミング export（合成に実際に使った内部タイミングを CSV へ）
# ---------------------------------------------------------------------------

TIMING_CSV_FIELDS = [
    "row_index", "row_kind", "note_index", "phrase_index", "is_phrase_first", "is_phrase_last",
    "mora_kana", "onset", "vowel", "midi", "note_dur_frames", "note_dur_sec",
    "ph_seq", "ph_dur_frames", "ph_dur_sec", "ph_num",
]


def _consonant_frames_by_note(consonant_events: List["cons.ConsonantEvent"]) -> Dict[int, int]:
    """note_index -> 適用された子音イベントの `n_frames_processed`（先勝ち。
    1 note に付く consonant event は高々 1 件という既存契約に従う）。"""
    out: Dict[int, int] = {}
    for ev in consonant_events:
        out.setdefault(ev.note_index, ev.n_frames_processed)
    return out


def _build_timing_rows(
    segments: list,
    resolved_list: list,
    note_dur_frames_list: List[int],
    consonant_events: List["cons.ConsonantEvent"],
    is_vcv: bool,
    sr: int,
    frame_period_ms: float,
) -> List[dict]:
    """S3 Phase B1: 合成に実際に使った内部タイミング（音素列/各音素の実時間長・
    ノート列/ノート長）を、レンダー本体が使う内部構造（`segments`・
    `resolved_list`・`note_dur_frames_list`・`consonant_events`）だけから導出
    する（B3 `s1_dataprep/convert_d3.py` 側でのタイミング再計算の二重実装を
    避けるための橋 = `DESIGN_S3_backfill.md` §2.3 B1「render と converter で
    タイミング計算を二重実装しない」）。

    行の種類（`row_kind`）:

    - **"note"**: 各ノート 1 行。`ph_seq`/`ph_dur_frames`/`ph_dur_sec` はこの
      ノート内の音素分解（半角スペース区切り、`ph_num` 個の値が並ぶ）:
        - VCV（`donor="ritsu"`）: `ResolvedVCVSegment.head_frames`（録音済み
          調音遷移・伸縮なし）と `n_frames - head_frames`（母音定常部）の
          2 音素。onset の無い（母音のみ/撥音/長音）モーラは遷移区間を母音
          音素へ吸収し 1 音素として報告する（このモデルに独立した子音
          ラベルが存在しないため・[実装決定]）。
        - 非 VCV（vocadito/pjs）: `consonant_events`（note_index キー）に
          対応イベントがあれば `n_frames_processed` を子音区間、残りを母音
          区間とする 2 音素。無ければ 1 音素（母音のみ、resolved 全長）。
    - **"rest"**: フレーズ間ブレスに対応する行（`performance.build_timeline`
      が挿入する無音区間・`BREATH_DURATION_SEC`）。`segments[i].start_sample -
      segments[i-1].end_sample` から検出する（`build_timeline` 自身が置いた
      ギャップをそのまま読むだけで、ブレス長を再計算しない・[実装決定]）。
      `ph_seq="SP"`。

    `note_dur_frames`/`note_dur_sec` は score 由来の**ノート長**
    （`note_dur_frames_list`。VCV の絶対配置シフトや非 VCV の overlap 圧縮
    より前の、各ノートに割り当てられた名目フレーム数——DiffSinger
    `transcriptions.csv` の `note_dur` に対応する）。一方 `ph_dur_frames`
    の合計は resolved unit の実フレーム数（`resolved.n_frames`。VCV は常に
    `note_dur_frames` と一致・非 VCV は録音子音前置で `note_dur_frames` を
    上回りうる）——`ph_dur` に対応する値（実際に合成へ使われた音素長）。
    両者が異なりうることは DiffSinger のノート/音素の別レイヤー性
    （`ph_num>=1` の音素が 1 note へ紐づく）と整合する。

    レンダー結果 bit には一切影響しない（読み取りのみ・副作用なし）ため、
    `render()` は `--timing-out` の指定有無に関わらず常に計算し
    `result["timing_rows"]` へ格納する（CLI 側が要求時のみファイルへ書く）。
    """
    consonant_frames = _consonant_frames_by_note(consonant_events)
    rows: List[dict] = []
    row_index = 0
    prev_end_sample: Optional[int] = None
    for i, seg in enumerate(segments):
        if prev_end_sample is not None:
            gap_samples = seg.start_sample - prev_end_sample
            if gap_samples > 0:
                gap_frames = frames_for_samples(gap_samples, sr)
                gap_sec = gap_frames * frame_period_ms / 1000.0
                rows.append(dict(
                    row_index=row_index, row_kind="rest", note_index="", phrase_index="",
                    is_phrase_first="", is_phrase_last="", mora_kana="", onset="", vowel="",
                    midi="", note_dur_frames=gap_frames, note_dur_sec=round(gap_sec, 6),
                    ph_seq="SP", ph_dur_frames=str(gap_frames), ph_dur_sec=f"{gap_sec:.6f}",
                    ph_num=1,
                ))
                row_index += 1

        mora = seg.note.mora
        resolved = resolved_list[i]
        note_dur_frames = note_dur_frames_list[i]
        n_frames = resolved.n_frames

        if is_vcv:
            head_frames = min(getattr(resolved, "head_frames", 0), n_frames)
        else:
            head_frames = min(consonant_frames.get(i, 0), n_frames)

        if mora.onset is not None and head_frames > 0:
            core_frames = max(n_frames - head_frames, 0)
            ph_seq = [mora.onset, mora.vowel]
            ph_dur_frames = [head_frames, core_frames]
        else:
            ph_seq = [mora.vowel]
            ph_dur_frames = [n_frames]

        rows.append(dict(
            row_index=row_index, row_kind="note", note_index=i, phrase_index=seg.note.phrase_index,
            is_phrase_first=seg.is_phrase_first, is_phrase_last=seg.is_phrase_last,
            mora_kana=mora.kana, onset=(mora.onset or ""), vowel=mora.vowel, midi=seg.note.midi,
            note_dur_frames=note_dur_frames, note_dur_sec=round(note_dur_frames * frame_period_ms / 1000.0, 6),
            ph_seq=" ".join(ph_seq),
            ph_dur_frames=" ".join(str(f) for f in ph_dur_frames),
            ph_dur_sec=" ".join(f"{f * frame_period_ms / 1000.0:.6f}" for f in ph_dur_frames),
            ph_num=len(ph_seq),
        ))
        row_index += 1
        prev_end_sample = seg.end_sample
    return rows


def write_timing_csv(path: str | Path, rows: List[dict]) -> None:
    """`_build_timing_rows` の行を CSV（`TIMING_CSV_FIELDS` 列）へ非 atomic に
    直接書く（staging を経由しない単純書き込み）。

    P1 修正 (review #265): CLI (`main()`) 経由の `--timing-out` 公開は、
    現在は `render(..., timing_out_path=...)` 内の
    `_atomic_write_wav_and_timing`（WAV と両方 staging → 両方 rename）が
    担う。本関数は後方互換・プログラム的な単体利用（`render()` を介さず
    `timing_rows` を直接ファイル化したい呼び出し元）向けに残す非 atomic な
    素朴実装であり、`main()` からはもう呼ばれない。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TIMING_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def render(
    score_name: str,
    voice_spec_path: str | Path,
    wav_path: Optional[str | Path] = None,
    notes_csv_path: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
    out_path: Optional[str | Path] = None,
    w_p: float = un.DEFAULT_W_P,
    w_d: float = un.DEFAULT_W_D,
    w_c: float = un.DEFAULT_W_C,
    w_v: float = un.DEFAULT_W_V,
    apply_consonants: bool = True,
    donor: str = "vocadito",
    consonant_source: Optional[str] = None,
    voicebank_root: Optional[str | Path] = None,
    timing_out_path: Optional[str | Path] = None,
) -> dict:
    """VG-F1 / 追補 F1.1 / 追補 F1.2 パイプライン本体。

    `donor`: "vocadito"（既定・従来どおり）| "ritsu"（UTAU oto バンク・
    追補 F1.2-B）| "pjs"（PJS lab バンク・追補 F1.2-C）。既存 CLI 挙動は
    `donor="vocadito"` の場合に完全不変（追補 F1.2 Acceptance 「既存 CLI
    挙動は不変」）。

    `consonant_source`: "recorded"（bank 提供の録音済み子音を前置。無ければ
    synthetic へフォールバック・件数記録）| "synthetic"（従来の
    `consonants.apply_consonant_onset` のみ）| "none"（子音加工なし）。
    省略時は donor に応じた既定（vocadito -> "synthetic", ritsu/pjs ->
    "recorded"）。`apply_consonants=False` は "none" と等価（後方互換の
    `--no-consonants` フラグ用）。

    `timing_out_path`: P1 修正 (review #265) `--timing-out` の公開パス。
    指定時は `--out` と同じ保護入力衝突検査（`_reject_output_collision`）を
    合成（`build_score`/WORLD 分析）より前に通す（重い処理を始める前の
    fail-closed）。かつ WAV とタイミング CSV を両方 staging してから両方
    公開する（`_atomic_write_wav_and_timing`）ため、CSV 書き込みが後から
    失敗して WAV だけが残る非対称を避ける。省略時（`None`）は WAV 単独の
    従来経路（`_atomic_write_wav`）のままで、出力バイト列は完全不変。
    """
    if consonant_source is None:
        consonant_source = "synthetic" if donor == "vocadito" else "recorded"
    if not apply_consonants:
        consonant_source = "none"
    if consonant_source not in CONSONANT_SOURCE_CHOICES:
        raise ValueError(f"unknown consonant_source: {consonant_source!r}")
    if donor not in DONOR_CHOICES:
        raise ValueError(f"unknown donor: {donor!r} (expected one of {DONOR_CHOICES})")
    # P2 修正 (review #262 R9・`r3789495255`): donor="ritsu" は F1.4 で VCV unit
    # 経路（子音は録音済み調音遷移として unit 内に不可分に録り込み済み・§1.8/1.3）
    # へ全面移行済みのため、`consonant_source="none"/"synthetic"`（および
    # `--no-consonants` の "none" 翻訳）は録音済み VCV 遷移を後から取り除く/
    # 差し替えることができず意味を成さない。従来はここを黙って "vcv" へ上書きして
    # いた（=指定を無視）が、重い bank 分析（WORLD 解析・oto.ini 全数走査）より
    # 前の fail-closed な明示エラーへ変更する。donor="ritsu" で対応する唯一の値は
    # "recorded"（既定）のみ。
    if donor == "ritsu" and consonant_source != "recorded":
        raise ValueError(
            "donor='ritsu' は VCV 録音遷移が不可分のため consonant_source は "
            "'vcv' 固定（内部的には 'recorded' 既定のみ対応）です。"
            "'none'/'synthetic' の指定や --no-consonants は非対応です。"
        )

    spec = vs.load_voice_spec(voice_spec_path)
    # P1 修正 (review #262 R2): 重い WORLD 分析より前に spec.donor の provenance
    # を fail-closed で検証する（§ _validate_spec_donor docstring 参照）。
    # R6 修正: bank 構築完了後にもう一度 `_validate_spec_donor` を呼ぶ
    # （post-build revalidation・§ bank 構築直後のコメント参照）ため、
    # ここでの呼び出しは「無関係な入力での無駄な WORLD 分析を防ぐ fail-fast」
    # の役割に限定される。
    donor_provenance = _validate_spec_donor(spec, donor, wav_path, voicebank_root)
    # P1 修正 (review #265): `--timing-out` の衝突検査を、`--out` の衝突検査
    # （書き込み直前・§ 後段）とは独立に、重い合成（build_score 以降の
    # perf/WORLD パイプライン）より前へ前倒しする。`--timing-out` が `--out`・
    # 保護入力（--wav/--voice/--notes-csv/--voicebank-root/--cache-dir）の
    # いずれかと一致する呼び出しを、無駄な重い処理を始める前に拒否する。
    if timing_out_path is not None:
        if out_path is not None and Path(timing_out_path).resolve() == Path(out_path).resolve():
            # `_reject_output_collision` の `protected_files` は「既存の入力
            # ファイル」を前提に `.exists()` でスキップする（--notes-csv 省略時
            # 等）ため、まだ書かれていない `--out`（通常のケース）との一致判定
            # には使えない。out/timing-out は互いに「これから書く 2 つの出力」
            # であり、存在有無に関わらず厳密一致のみで衝突と判定する。
            raise OutputCollisionError(
                f"--timing-out ({timing_out_path}) が --out ({out_path}) と衝突しています"
                "（fail-closed で拒否）"
            )
        _reject_output_collision(
            timing_out_path,
            protected_files=[wav_path, voice_spec_path, notes_csv_path],
            protected_roots=[voicebank_root, cache_dir],
        )
    notes, tempo_bpm = build_score(score_name)
    segments, total_samples = perf.build_timeline(notes, sr=SR, tempo_bpm=tempo_bpm)
    # F1.4 R3: score 由来の絶対総尺（frame 数）。donor="ritsu"（VCV/preutterance
    # 経路）の `joins.assemble_absolute` はこの固定長バッファへ trim せずに
    # 配置するため、総尺は常に score 準拠（±1 frame の丸め誤差のみ）になる。
    score_total_frames = frames_for_samples(total_samples, SR)
    # P1 修正 (review #262): f0/振幅の per-sample トラックは segments/total_samples
    # だけに依存する（donor 選択より前に決められる）。旧実装はこれらを
    # assemble_v2 呼び出しの後段で構築し、圧縮後フレーム数でナイーブに再インデックス
    # していたためタイムラインがズレていた（`_note_frame_track` + `jn.assemble_control_tracks_v2`
    # 経由で圧縮後タイムラインへ正しく整列させる。後段の placements 構築後に使う）。
    note_dur_frames_list = [
        max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1) for seg in segments
    ]
    portamento_ms = float(spec.perf.get("portamento_ms", 55.0))
    base_f0_persample = pg.build_perf_f0(
        segments, total_samples, SR, spec.perf, seed=spec.seed, portamento_ms=portamento_ms
    )
    amp_env = perf.build_amplitude_envelope(segments, total_samples, SR)

    consonant_clips: Dict[str, list] = {}
    donor_extra_stats: dict = {}
    vcv_selection_stats: dict = {}
    vcv_placement_stats: dict = {}
    is_vcv = donor == "ritsu"  # 追補 F1.4: ritsu は VCV unit 経路（他は v0/F1.2 のまま）

    if donor == "vocadito":
        if wav_path is None:
            raise ValueError("donor='vocadito' には --wav（ドナー wav パス）が必須です")
        bank = db.build_donor_bank(wav_path, notes_csv_path=notes_csv_path, cache_dir=cache_dir)
        # 追補 F1.1-A: donor unit を母音分類し、選択コストへ母音制約を効かせる。
        # select_units は unit.index -> ラベル文字列を期待するため、VowelClassResult
        # から label のみ抜き出す（分布・F1/F2 の record 用には unit_vowel_results を残す）。
        unit_vowel_results = vc.classify_donor_units(bank)
        unit_vowel_labels = {idx: r.label for idx, r in unit_vowel_results.items()}
    elif donor == "ritsu":
        if voicebank_root is None:
            raise ValueError("donor='ritsu' には --voicebank-root（UTAU 音源ルート）が必須です")
        # 追補 F1.4-A: VCV unit 化（donor_bank_utau v2）。旧「母音核 unit + 録音子音
        # クリップ挿入」経路（v1 `build_donor_bank_utau`）は F1.3 までの互換・単体
        # テストのため関数として残置するが render.py からはもう呼ばない。
        required_contexts = _vcv_required_contexts(segments)
        bank, unit_contexts, donor_extra_stats = dbu.build_donor_bank_utau_vcv(
            voicebank_root, cache_dir=cache_dir, required_contexts=required_contexts,
        )
        # record/CLI 表示用の母音分布（選択には使わない・文脈キー一致が代替）。
        unit_vowel_labels = {}
        for idx, ctx in unit_contexts.items():
            _onset, _v, _status = dbu.normalize_mora_kana(ctx.mora)
            if _status == "ok" and _v:
                unit_vowel_labels[idx] = _v
    else:  # donor == "pjs"
        if voicebank_root is None:
            raise ValueError("donor='pjs' には --voicebank-root（PJS コーパスルート）が必須です")
        target_median_hz = float(np.median([_midi_to_hz(seg.note.midi) for seg in segments]))
        # P2 修正 (review #262 R12): coverage は選択時の約束ではなく実際に
        # 構築できた unit/クリップから導出する（終端修正）。sakura/umi の
        # 必須子音オンセット + 5 母音（`_select_lab_files` が従来から貪欲
        # 被覆の target にしている固定集合と同一）を明示的に必須要件として
        # 渡し、realized coverage がこれを満たさなければ fail-closed で
        # 拒否させる（§ build_donor_bank_utau_vcv の required_contexts と
        # 対称。近傍/global フォールバックへの黙った縮退を禁止）。
        bank, unit_vowel_labels, consonant_clips, donor_extra_stats = dbl.build_donor_bank_lab(
            voicebank_root, target_median_hz=target_median_hz, cache_dir=cache_dir,
            required_onsets=dbu.REQUIRED_ONSETS, required_vowels=dbl.VOWELS_5,
        )

    # P2 修正 (review #262 R6・`r3789428504`): bank 構築完了後・成果物公開前に
    # provenance を再照合する（post-build revalidation）。`_validate_spec_donor`
    # 冒頭の検証（重い WORLD 分析より前の fail-fast）と builder 内部の実際の
    # read（bank 構築）は別読みであるため、その間に入力ファイルが書き換われば
    # 「spec 一致の provenance を返しつつ別バイトを分析」しうる（TOCTOU）。
    # ここで同じ検証をもう一度実行し、bank 構築時点までの間に対象ファイルが
    # 変化していないことを確認する（identity hash が変化 = 不一致で
    # `DonorProvenanceError`・fail-closed）。cache-hit 経路でも
    # `voicebank_identity_hash`/`corpus_identity_hash`（vocadito は
    # `sha256_of`）はキャッシュに依存せず毎回ディスクを直接再走査するため、
    # bank 構築が pickle 復元だった場合でも同じ強度で検証できる。以降で使う
    # `donor_provenance` はこの再検証後の値（分析直後の状態を反映）で上書きする。
    donor_provenance = _validate_spec_donor(spec, donor, wav_path, voicebank_root)

    # 追補 F1.3-B item1: unit の収録時レベルを除去する（母音核区間の平均パワーで
    # sp を正規化）。ドナー種別を問わず一律に適用（DonorBank は 3 ドナー共通の
    # スキーマ）。concat cost が正規化後の実レンダー値と整合するよう
    # head/tail_log_bands も再計算済みの bank を以降で使う。
    bank, energy_norm_stats = db.normalize_unit_energy(bank)

    n_bins = bank.sp.shape[1]
    consonant_events: List[cons.ConsonantEvent] = []
    n_recorded_consonants_used = 0
    n_recorded_consonants_fallback_synthetic = 0

    if is_vcv:
        # 追補 F1.4-C: 文脈キー完全一致必須の VCV 選択（w_c/w_v は使わない
        # ——文脈フィルタが母音一致を自動充足するため）。
        vcv_targets = [
            un.VCVTargetNote(
                pitch_hz=_midi_to_hz(seg.note.midi),
                duration_sec=(seg.end_sample - seg.start_sample) / SR,
                label=seg.note.mora.kana, prev_vowel=ctx[0], mora=ctx[1],
            )
            for seg, ctx in zip(segments, un.vcv_context_sequence(segments))
        ]
        ctx_map = {idx: (c.prev_vowel, c.mora) for idx, c in unit_contexts.items()}
        selections, sel_stats = un.select_vcv_units(vcv_targets, bank.units, ctx_map, w_p=w_p, w_d=w_d)
        vcv_selection_stats = sel_stats
        placements, resolved_list, vcv_placement_stats = _build_vcv_placements(segments, selections, bank)
        # 追補 F1.4-B item3: consonants.py 合成 / recorded クリップ挿入経路は
        # VCV 経路では不使用（子音は unit に録り込み済み）。
        consonant_source = "vcv"
    else:
        targets = [
            un.TargetNote(
                pitch_hz=_midi_to_hz(seg.note.midi),
                duration_sec=(seg.end_sample - seg.start_sample) / SR,
                label=seg.note.mora.kana,
                vowel_target=un.mora_to_vowel_target(seg.note.mora),
            )
            for seg in segments
        ]
        selections, sel_stats = un.select_units(
            targets, bank.units, w_p=w_p, w_d=w_d, w_c=w_c, unit_vowels=unit_vowel_labels, w_v=w_v
        )

        placements = []
        resolved_list = []
        cursor = 0
        prev_end_sample = 0
        for note_index, (seg, sel) in enumerate(zip(segments, selections)):
            gap_samples = seg.start_sample - prev_end_sample
            gap_frames = frames_for_samples(gap_samples, SR) if gap_samples > 0 else 0
            cursor += gap_frames
            note_dur_frames = max(frames_for_samples(seg.end_sample - seg.start_sample, SR), 1)
            resolved = un.resolve_unit_to_note(
                bank, sel.unit, note_dur_frames, note_index=note_index, frame_period_ms=FRAME_PERIOD_MS
            )
            onset = seg.note.mora.onset
            if consonant_source != "none" and onset is not None:
                # 追補 F1.2-D: consonant_source="recorded" は bank 提供の録音済み
                # 子音クリップを unit 先頭へ preutterance 相当分（クリップの実長）
                # だけ前置する（joins.assemble の 30ms クロスフェードが接続を担う）。
                # クリップが無ければ synthetic（追補 F1.1-B）へフォールバックする。
                used_recorded = False
                if consonant_source == "recorded":
                    clips = consonant_clips.get(onset)
                    if clips:
                        clip = clips[0]  # 決定論選択（bank 側で句頭優先・名前昇順ソート済み）
                        resolved, event = prepend_recorded_consonant(resolved, clip, note_index)
                        consonant_events.append(event)
                        n_recorded_consonants_used += 1
                        used_recorded = True
                    else:
                        n_recorded_consonants_fallback_synthetic += 1
                if not used_recorded:
                    # 追補 F1.1-B: 母音制約選択 -> 子音オンセット加工 -> (この後の) joins/perf_genes/warp。
                    proc_sp, proc_ap, event = cons.apply_consonant_onset(
                        resolved.sp, resolved.ap, onset, SR, frame_period_ms=FRAME_PERIOD_MS
                    )
                    if event is not None:
                        consonant_events.append(dataclasses.replace(event, note_index=note_index))
                    resolved = dataclasses.replace(resolved, sp=proc_sp, ap=proc_ap)
            resolved_list.append(resolved)
            start_frame = cursor
            end_frame = start_frame + resolved.n_frames
            placements.append(
                jn.NotePlacement(
                    start_frame=start_frame, end_frame=end_frame, sp=resolved.sp, ap=resolved.ap,
                    has_join_to_prev=not seg.is_phrase_first,
                    # 追補 F1.3-A: 選択された donor unit の oto overlap（フレーム）を
                    # そのまま接合長として使う。録音子音を前置した場合も vowel unit
                    # 自身の overlap 値を流用する（子音クリップ自体は oto overlap を
                    # 持たないための近似・[実装決定・record 記録]）。None = 情報なし
                    # (vocadito/pjs) -> assemble_v2 が DEFAULT_OVERLAP_MS へフォールバック。
                    overlap_frames=sel.unit.overlap_frames,
                )
            )
            cursor = end_frame
            prev_end_sample = seg.end_sample

    if is_vcv:
        # F1.4 R3: 絶対グリッド配置（総尺は score 準拠・trim しない）。
        sp_seq, ap_seq, join_stats = jn.assemble_absolute(score_total_frames, n_bins, placements)
    else:
        # 追補 F1.3-A: v1 の単側ブレンド（jn.assemble）ではなく true overlap-add
        # （jn.assemble_v2）のみを使う。n_total_frames は overlap 分だけ短くなった
        # 実際の圧縮後タイムライン長を join_stats から受け取る（vocadito/pjs は
        # 無変更・回帰リスクを避けるため R3 のスコープ外）。
        sp_seq, ap_seq, join_stats = jn.assemble_v2(n_bins, placements, frame_period_ms=FRAME_PERIOD_MS)
    n_total_frames = join_stats["n_total_frames"]

    sp_seq, ap_seq = vs.apply_warp(sp_seq, ap_seq, SR, spec.warp)

    # P1 修正 (review #262): f0/振幅トラックを sp/ap と同じ圧縮後タイムライン上へ
    # ノート単位で再構築する。placements[i] の実スパン（consonant 前置で
    # note_dur_frames_list[i] より長いことがある）に対して perf_genes の
    # per-sample トラックを抽出し（`_note_frame_track`）、sp/ap と全く同じ
    # run/overlap 圧縮（`jn.assemble_control_tracks_v2`）へ通すことで、
    # join を経るたびに累積していたズレ（旧: 圧縮後フレーム数から作った
    # 素朴な等間隔インデックスで圧縮前の生タイムラインを読み出していたバグ）
    # を解消する。overlap 区間は f0 を log domain（sp と同じ）・振幅を
    # linear domain（ap と同じ）でブレンドする。
    # P1 修正 (review #262 R4・`r3789341843`): donor="ritsu"（VCV 経路）は
    # `_build_vcv_placements` が計算した note ごとの実効配置シフト
    # （`vcv_placement_stats["shift_per_note"]`）をサンプリング原点へも
    # 適用する（sp/ap の絶対配置と同じだけ f0/振幅の取り出し位置を前へ
    # ずらす。vocadito/pjs 経路は shift=0 のリストで従来どおり無補正）。
    note_origin_shift_frames: List[int] = (
        list(vcv_placement_stats["shift_per_note"]) if is_vcv else [0] * len(placements)
    )
    # P2 修正 (review #262 R14・`r3789636053`): origin-shift されたフレーズ頭
    # preutterance が `amp_env` のゼロ初期化ブレス区間を拾って消音される問題
    # を、amp トラック抽出専用にフレーズ頭のみ前方へ定数外挿した複製で回避
    # する（vocadito/pjs 経路は shift 全 0 のため no-op）。
    # P3 修正 (review #262 R19 指摘 3): 同じ理由で `base_f0_persample` も
    # 無声区間 (F0=0) を拾ってしまうため、対になる F0 後方延長を追加する
    # （`_extend_phrase_head_f0_for_preutterance` docstring 参照。対象ノート
    # 判定は amp 側と共有・vocadito/pjs 経路は no-op）。
    amp_env_for_extraction = (
        _extend_phrase_head_amp_for_preutterance(
            amp_env, segments, note_origin_shift_frames, SR, FRAME_PERIOD_MS
        )
        if is_vcv
        else amp_env
    )
    base_f0_for_extraction = (
        _extend_phrase_head_f0_for_preutterance(
            base_f0_persample, segments, note_origin_shift_frames, SR, FRAME_PERIOD_MS
        )
        if is_vcv
        else base_f0_persample
    )
    note_f0_tracks = [
        _note_frame_track(
            base_f0_for_extraction, segments[i], note_dur_frames_list[i], p.sp.shape[0], SR, FRAME_PERIOD_MS,
            origin_shift_frames=note_origin_shift_frames[i],
        )
        for i, p in enumerate(placements)
    ]
    note_amp_tracks = [
        _note_frame_track(
            amp_env_for_extraction, segments[i], note_dur_frames_list[i], p.sp.shape[0], SR, FRAME_PERIOD_MS,
            origin_shift_frames=note_origin_shift_frames[i],
        )
        for i, p in enumerate(placements)
    ]
    if is_vcv:
        f0_seq, amp_seq, control_track_stats = jn.assemble_control_tracks_absolute(
            score_total_frames, placements, note_f0_tracks, note_amp_tracks
        )
    else:
        f0_seq, amp_seq, control_track_stats = jn.assemble_control_tracks_v2(
            placements, note_f0_tracks, note_amp_tracks, frame_period_ms=FRAME_PERIOD_MS
        )
    assert f0_seq.shape[0] == n_total_frames, (
        f"f0_seq/sp_seq フレーム数不一致: {f0_seq.shape[0]} != {n_total_frames}"
    )
    assert amp_seq.shape[0] == n_total_frames, (
        f"amp_seq/sp_seq フレーム数不一致: {amp_seq.shape[0]} != {n_total_frames}"
    )

    # S3 Phase B1: タイミング export 用の内部行を構築する（読み取りのみ・
    # 既存の合成パス（f0_seq/sp_seq/ap_seq/y）には一切影響しない。§ 関数
    # docstring 参照）。
    timing_rows = _build_timing_rows(
        segments, resolved_list, note_dur_frames_list, consonant_events, is_vcv, SR, FRAME_PERIOD_MS,
    )

    y = pw.synthesize(
        np.ascontiguousarray(f0_seq), np.ascontiguousarray(sp_seq), np.ascontiguousarray(ap_seq),
        SR, FRAME_PERIOD_MS,
    )
    y = np.asarray(y, dtype=np.float64)

    # 追補 F1.3-B item2: 振幅の唯一の権威 = performance.build_amplitude_envelope
    # （フレーズアーチ）。unit 由来の振幅は F1.3-B item1 の正規化で spectral 形状
    # のみに縮退済みなので、ここで乗算するフレーズアーチ + articulation
    # エンベロープが最終波形の音量ダイナミクスを決める唯一の経路になる
    # （singer/render_song.py の amp_render 適用パターンと同じ乗算方式）。
    # P1 修正: amp_seq は sp_seq/ap_seq と同じ**フレーム**単位（圧縮後タイムライン
    # 上で正しく整列済み）のため、y（サンプル単位）へ適用する際はフレーム→サンプル
    # の素朴な等分割で読み出す（旧実装のような圧縮前タイムラインとの取り違えはない）。
    if len(y) > 0 and len(amp_seq) > 0:
        samples_per_frame = SR * FRAME_PERIOD_MS / 1000.0
        frame_idx_for_sample = np.minimum(
            (np.arange(len(y)) / samples_per_frame).astype(np.int64), len(amp_seq) - 1
        )
        y = y * amp_seq[frame_idx_for_sample]

    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * PEAK_NORM

    output_sha256: Optional[str] = None
    if out_path is not None:
        # P1 修正 (review #262 R2): --out が保護入力（--wav/--voice/--notes-csv/
        # --voicebank-root 配下）と衝突していないか書き込み前に fail-closed で
        # 拒否してから、staging + os.replace で atomic 公開する。
        # P2 修正 (review #262 R11・`r3789543157`): --out が donor bank キャッシュ
        # （--cache-dir 配下に生成される donor_bank_*.npz/utau_bank_*.pkl/
        # lab_bank_*.pkl）と衝突する場合、cache-hit render はまずそのキャッシュを
        # 読み、その後同じパスを WAV バイト列で atomic 置換してしまう
        # （次回同一 render はそのキャッシュを読んでデコード失敗する）。
        # cache_dir を protected_roots へ追加し、同じ resolved containment 検査で
        # 拒否する。
        _reject_output_collision(
            out_path,
            protected_files=[wav_path, voice_spec_path, notes_csv_path],
            protected_roots=[voicebank_root, cache_dir],
        )
        if timing_out_path is not None:
            # P1 修正 (review #265): WAV + timing CSV を両方 staging してから
            # 両方公開する（§ `_atomic_write_wav_and_timing` docstring 参照。
            # 衝突検査自体は合成前の早期チェック（§ 冒頭）で既に完了済み）。
            output_sha256 = _atomic_write_wav_and_timing(
                y, SR, out_path, timing_rows, timing_out_path
            )
        else:
            output_sha256 = _atomic_write_wav(y, SR, out_path)

    cap_modes = [r.cap_mode for r in resolved_list]
    return dict(
        y=y, sr=SR, n_total_frames=n_total_frames, total_samples_timeline=total_samples,
        selections=selections, selection_stats=sel_stats, join_stats=join_stats,
        control_track_stats=control_track_stats,
        donor_bank_stats=bank.stats, donor_bank_source=bank.source, wav_sha256=bank.wav_sha256,
        output_sha256=output_sha256,
        donor=donor, consonant_source=consonant_source, donor_extra_stats=donor_extra_stats,
        donor_provenance=donor_provenance,
        n_stretch_extended_looped=cap_modes.count("extended_looped"),
        n_stretch_compressed_truncated=cap_modes.count("compressed_truncated"),
        n_stretch_none=cap_modes.count("none"),
        unit_vowels=unit_vowel_labels, vowel_distribution=_vowel_distribution_from_labels(unit_vowel_labels),
        consonant_events=consonant_events,
        consonant_class_counts=dict(Counter(e.consonant_class for e in consonant_events)),
        n_recorded_consonants_used=n_recorded_consonants_used,
        n_recorded_consonants_fallback_synthetic=n_recorded_consonants_fallback_synthetic,
        energy_norm_stats=energy_norm_stats,
        is_vcv=is_vcv, vcv_selection_stats=vcv_selection_stats, vcv_placement_stats=vcv_placement_stats,
        timing_rows=timing_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="VG-F1 Foundry Adapter render CLI")
    parser.add_argument("--score", required=True, choices=sorted(SCORE_REGISTRY))
    parser.add_argument("--voice", required=True, help="voice spec JSON path")
    parser.add_argument("--out", required=True, help="output WAV path")
    parser.add_argument("--donor", default="vocadito", choices=list(DONOR_CHOICES), help="追補 F1.2-D: ドナー選択")
    parser.add_argument("--wav", default=None, help="donor WAV path（donor=vocadito で必須）")
    parser.add_argument("--notes-csv", default=None, help="donor notes annotation CSV（vocadito・省略時 fallback）")
    parser.add_argument(
        "--voicebank-root", default=None,
        help="UTAU 音源ルート（donor=ritsu）または PJS コーパスルート（donor=pjs）",
    )
    parser.add_argument("--cache-dir", default=None, help="donor bank npz/pkl キャッシュディレクトリ")
    parser.add_argument(
        "--consonant-source", default=None, choices=list(CONSONANT_SOURCE_CHOICES),
        help=(
            "追補 F1.2-D: 子音供給元（省略時 donor に応じた既定）。"
            "donor=ritsu は VCV 録音遷移が不可分のため 'recorded'（既定）のみ対応・"
            "'none'/'synthetic' はエラー（review #262 R9）"
        ),
    )
    parser.add_argument(
        "--no-consonants", action="store_true",
        help=(
            "子音オンセット加工を無効化する（--consonant-source none と等価。旧 F1.1-B 互換フラグ）。"
            "donor=ritsu では非対応（エラー・review #262 R9）"
        ),
    )
    parser.add_argument(
        "--timing-out", default=None,
        help=(
            "S3 Phase B1: 合成に実際に使った内部タイミング（音素列/各音素の実時間長・"
            "ノート列/ノート長）を CSV へ export するパス（省略時は export しない。"
            "省略時の挙動・WAV 出力バイト列は完全不変 = DESIGN_S3_backfill.md §2.3 B1 AC）。"
            "列仕様は `TIMING_CSV_FIELDS`/`_build_timing_rows` docstring 参照。"
        ),
    )
    args = parser.parse_args()

    result = render(
        args.score, args.voice, args.wav, notes_csv_path=args.notes_csv,
        cache_dir=args.cache_dir, out_path=args.out, apply_consonants=not args.no_consonants,
        donor=args.donor, consonant_source=args.consonant_source, voicebank_root=args.voicebank_root,
        timing_out_path=args.timing_out,
    )
    print(f"wrote {args.out}: {len(result['y'])} samples ({len(result['y']) / result['sr']:.3f}s)")
    print(f"donor={result['donor']} consonant_source={result['consonant_source']}")
    print(f"donor_provenance={result['donor_provenance']}")
    print(f"donor_bank_source={result['donor_bank_source']} wav_sha256={result['wav_sha256']}")
    print(f"output_sha256={result['output_sha256']}")
    print(f"donor_bank_stats={result['donor_bank_stats']}")
    print(f"selection_stats={result['selection_stats']}")
    print(f"vowel_distribution={result['vowel_distribution']}")
    print(f"consonant_class_counts={result['consonant_class_counts']}")
    print(
        f"recorded_consonants_used={result['n_recorded_consonants_used']} "
        f"fallback_synthetic={result['n_recorded_consonants_fallback_synthetic']}"
    )
    print(f"join_stats={result['join_stats']}")
    print(f"energy_norm_stats={result['energy_norm_stats']}")
    print(
        f"stretch: none={result['n_stretch_none']} "
        f"extended_looped={result['n_stretch_extended_looped']} "
        f"compressed_truncated={result['n_stretch_compressed_truncated']}"
    )
    if result["is_vcv"]:
        print(f"vcv_selection_stats={result['vcv_selection_stats']}")
        print(f"vcv_placement_stats={result['vcv_placement_stats']}")
    if args.timing_out is not None:
        # P1 修正 (review #265): timing CSV は `render(..., timing_out_path=...)`
        # 内で WAV と両方 staging → 両方 rename 済み（`_atomic_write_wav_and_timing`）
        # のため、ここで別途 `write_timing_csv` を呼ばない（非対称公開の回避）。
        print(f"wrote timing csv {args.timing_out}: {len(result['timing_rows'])} rows")


if __name__ == "__main__":
    main()
