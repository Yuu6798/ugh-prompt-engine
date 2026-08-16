"""S1 データ工場: リツ (D2) + PJS (D1) の 2 話者統合 + 辞書生成 + 検証。

出力は `openvpi/DiffSinger` の `scripts/binarize.py` にそのまま渡せる多話者
acoustic config（`datasets:` に 2 エントリ・`spk_id`/`num_spk` 付き）と、統合
音素辞書（恒等写像）。

**実装決定**（`s1b_dataset_record.md` §5.1 の判断を再現）: 単一マージ CSV では
なく openvpi ネイティブの複数 `datasets:` エントリ方式を採用する。理由:
`AcousticBinarizer` は dataset 単位で `spk_map`/`test_prefixes` を解決する
設計であり、単一マージ CSV 方式は openvpi のネイティブな読み込み経路と整合
しないため。したがって本スクリプトは `convert_ritsu.py`/`convert_pjs.py` の
出力（各話者の `raw_data_dir`）をそのまま参照する config を書き出すのみで、
wav やラベルを新たにコピー・結合することはしない。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

# AP/SP は DiffSinger の `PhonemeDictionary` が自動登録する特殊トークンであり、
# 辞書ファイルへは含めない（`s1a_conversion_record.md` §3 / `s1b_dataset_record.md`
# §5.1 と同じ扱い）。
SPECIAL_TOKENS: Set[str] = {"AP", "SP"}

# review #263 R13 P1: 学習規模フィールド（`max_updates`/`val_check_interval`/
# `num_ckpt_keep`）の既定値。`S1_GPU_RUNBOOK.md` §3 が binarize 前に手動追記
# していた値と同一（早期ゲート節目 5K/10K/20K に checkpoint を確実に乗せる
# ための現行 runbook 値）。CLI 引数で上書きしない限りこの値が config へ
# 書き込まれる。
DEFAULT_MAX_UPDATES = 40000
DEFAULT_VAL_CHECK_INTERVAL = 5000
DEFAULT_NUM_CKPT_KEEP = 10


def read_transcriptions(csv_path: Path) -> List[Dict[str, str]]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def collect_phoneme_symbols(rows: Sequence[Dict[str, str]]) -> Set[str]:
    symbols: Set[str] = set()
    for row in rows:
        symbols.update(row["ph_seq"].split())
    return symbols


def select_test_prefixes(rows: Sequence[Dict[str, str]], n: int = 5) -> List[str]:
    """決定論的な検証用サブセット選択。

    `name` 昇順に並べ、区間を `n` 等分した位置（先頭・末尾を含む）から選ぶ。
    件数が `n` 以下ならそのまま全件を返す。乱数は使わない。
    """
    names = sorted(row["name"] for row in rows)
    if len(names) <= n:
        return names
    if n <= 1:
        return names[:1]
    picked: List[str] = []
    seen: Set[str] = set()
    for i in range(n):
        idx = round(i * (len(names) - 1) / (n - 1))
        name = names[idx]
        if name not in seen:
            seen.add(name)
            picked.append(name)
    # 等分位置が丸めで重複した分は末尾側から埋め直す（常に n 件を確保する）。
    i = len(names) - 1
    while len(picked) < n and i >= 0:
        if names[i] not in seen:
            seen.add(names[i])
            picked.append(names[i])
        i -= 1
    return sorted(picked)


def build_merged_dict(symbol_sets: Sequence[Set[str]]) -> List[Tuple[str, str]]:
    """全話者の phoneme symbol 和集合（AP/SP 除く）を恒等写像辞書として返す。"""
    union: Set[str] = set()
    for s in symbol_sets:
        union.update(s)
    union -= SPECIAL_TOKENS
    return [(sym, sym) for sym in sorted(union)]


def render_dict_text(pairs: Sequence[Tuple[str, str]]) -> str:
    """`write_dict` が書き出す辞書ファイルの内容をテキストとして組み立てる
    （atomic 公開のため、書き込み前に全文をメモリ上で確定させる）。"""
    return "".join(f"{sym}\t{mapped}\n" for sym, mapped in pairs)


def write_dict(path: Path, pairs: Sequence[Tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_dict_text(pairs))


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """staging tempfile へ書き込み、成功後にのみ `os.replace` で `path` へ
    atomic 公開する（`adapter/donor_bank.py _atomic_stage_and_replace` /
    `adapter/voice_spec.py save_voice_spec` と同じ流儀。AGENTS.md Persistent
    Artifact Safety Gate 項目6「全構築後公開」準拠）。

    [P2 修正] `problems` が非空でも辞書/config/report を書いてから失敗
    return していたため、失敗した再実行が既存の有効な成果物を壊し得た。
    呼び出し側 (`main`) で全構築・全検証を終えてから、成功時のみこの関数で
    公開する構造へ是正。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R4): `--out-dict`/`--out-config`/`--report` が
    互いに衝突する、または保護入力（Ritsu/PJS 両 raw dir 配下の
    transcriptions.csv・wavs/・参照ファイル）と衝突する場合に送出する
    （fail-closed。書き込み前に検出する。`adapter/render.py`/
    `scripts/measure_bands.py` の `OutputCollisionError` と対称の設計）。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """公開対象の全出力パス（resolve 後）を相互および保護入力ルートと照合し、
    衝突があれば公開前に fail-closed で拒否する。

    Codex 再現手順（`--out-dict` に Ritsu の入力 raw dir 配下の CSV を渡す）
    では、検証成功後の `_publish_outputs` がその CSV を辞書内容で上書きし、
    入力そのものを破壊してしまう。resolved 比較（symlink 解決後の完全一致・
    包含）で判定する（AGENTS.md Persistent Artifact Safety Gate 項目2）。

    - 出力同士: `--out-dict`/`--out-config`/`--report` が同一パスを指す場合
      （最後に書いたものが他方を無言で上書きする）。R10 で祖先/子孫関係
      （例: `--out-dict=/tmp/artifact`, `--out-config=/tmp/artifact/config.yaml`）
      も対象に追加（`_preflight_writable` の `mkdir(parents=True)` が祖先側
      出力パスをディレクトリとして先に作ってしまい、後続の祖先側公開が
      「ファイルでディレクトリを置換」しようとして失敗し、子孫側だけ公開
      された混合世代を招くため）。
    - 出力と保護入力: `--ritsu-raw-dir`/`--pjs-raw-dir` 配下（`transcriptions.csv`・
      `wavs/`・その他参照ファイルを含むルートごと）を出力先に指定した場合。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )
            # [P2 修正] (review #263 R10) 等価判定のみでは、出力パス同士が
            # 祖先/子孫関係にある場合（例: --out-dict=/tmp/artifact,
            # --out-config=/tmp/artifact/config.yaml）を見逃す。
            # `_preflight_writable`/`_publish_outputs` は各出力の親ディレクトリ
            # を `mkdir(parents=True, exist_ok=True)` するため、子孫側の
            # preflight が祖先側の出力パスをディレクトリとして先に作って
            # しまい、後続の祖先側公開 (`os.replace`) が「ファイルで
            # ディレクトリを置換」しようとして失敗し、子孫側だけ公開された
            # 混合世代を招く（Codex 再現手順: 上記 2 パス）。祖先/子孫関係も
            # 公開前に fail-closed で拒否する（`convert_pjs.py`
            # `_reject_output_collision` の保護 root 双方向判定と同型ロジック）。
            try:
                r_j.relative_to(r_i)
            except ValueError:
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p_j} is inside output path {p_i}（fail-closed で拒否）"
                )
            try:
                r_i.relative_to(r_j)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"output path {p_i} is inside output path {p_j}（fail-closed で拒否）"
            )

    for root in protected_roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p, r in resolved_outs:
            if r == root_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input root {root}（fail-closed で拒否）"
                )
            try:
                r.relative_to(root_resolved)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
            )


def _preflight_writable(paths: Sequence[Path]) -> None:
    """公開対象の全パスについて、書込可能性を事前検証する（実際の書き込みを
    始める前に全件をチェックし、途中失敗による「混合世代」— 辞書は新・config は
    旧のような公開状態 — を未然に防ぐ）。

    - 既存ディレクトリをそのまま出力先に指定した場合（`--out-config` に既存
      ディレクトリを渡す等。Codex 再現手順）は、`os.replace` が
      `IsADirectoryError` を投げて他の成果物だけ公開済みという事故になる前に
      ここで検出する。
    - 親ディレクトリの書込可否も、実際に probe ファイルを作成して確認する
      （`os.access` は root では常に True を返す等の既知の弱点があるため）。
    """
    for path in paths:
        if path.is_dir():
            raise IsADirectoryError(
                f"out path is an existing directory, not a file: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        probe_fd, probe_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".writetest.tmp"
        )
        try:
            os.close(probe_fd)
        finally:
            os.unlink(probe_name)


def _publish_outputs(items: Sequence[Tuple[Path, str]]) -> None:
    """複数出力（辞書/config/report）を検証してから一括で atomic 公開する
    （遷移性: 途中失敗時に混合世代の成果物が残らないようにする）。

    手順: (1) 全パスの書込可能性を事前検証（`_preflight_writable`） →
    (2) 新内容を staging tempfile へ書き込み → (3) 既存ファイルをバックアップ
    へ退避 → (4) staging を `os.replace` で一括公開。(3)/(4) の途中で失敗
    したら、公開済み分をバックアップから巻き戻し、staging/バックアップの
    残骸を片付けてから再送出する。
    """
    paths = [p for p, _ in items]
    _preflight_writable(paths)

    # Phase 1: 新内容を staging tempfile へ書き込む（実パスにはまだ触れない）。
    staged: List[Tuple[Path, str]] = []
    try:
        for path, content in items:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
            except BaseException:
                os.unlink(tmp_name)
                raise
            staged.append((path, tmp_name))
    except BaseException:
        for _path, tmp_name in staged:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise

    # Phase 2: 既存ファイルをバックアップへ退避する（無ければ None を記録）。
    backups: List[Tuple[Path, Optional[str]]] = []
    try:
        for path, _tmp_name in staged:
            if path.exists():
                bak_fd, bak_name = tempfile.mkstemp(
                    dir=path.parent, prefix=f"{path.name}.", suffix=".prepublish-bak"
                )
                os.close(bak_fd)
                os.unlink(bak_name)
                os.replace(path, bak_name)
                backups.append((path, bak_name))
            else:
                backups.append((path, None))
    except BaseException:
        # 退避中の失敗: ここまでの退避分を復元し、staging を破棄する。
        for path, bak_name in backups:
            if bak_name is not None:
                os.replace(bak_name, path)
        for _path, tmp_name in staged:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise

    # Phase 3: staging を実パスへ一括公開する。
    published = 0
    try:
        for path, tmp_name in staged:
            os.replace(tmp_name, path)
            published += 1
    except BaseException:
        # 途中失敗: 公開済み分をバックアップから巻き戻し、未公開分の
        # staging/バックアップを片付ける（旧成果物を確実に復元する）。
        for i, (path, tmp_name) in enumerate(staged):
            bak_name = backups[i][1]
            if i < published:
                if bak_name is not None:
                    os.replace(bak_name, path)
                else:
                    os.unlink(path)
            else:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                if bak_name is not None:
                    os.replace(bak_name, path)
        raise

    # 全件公開に成功: バックアップを削除する。
    for _path, bak_name in backups:
        if bak_name is not None:
            os.unlink(bak_name)


# fix (2026-08-16, s1_poison_scan 捜査結果を受けての導入): PJS 4 セグメントで
# 申告 ph_dur 合計が実音声長の 1.37〜1.89 倍という構造不良が見つかった
# （末尾 SP の申告時間が数秒単位あるにもかかわらず実 wav がそれより短く、
# `get_mel2ph_torch` の length クランプで末尾フォノームが 0 フレームへ完全
# 消滅する）。整合性の低い教師信号が binarize 済みデータへ無警告で混入する
# のを防ぐため、ph_dur 合計と実 wav 長の整合を検査する。フレーム量子化
# （timestep 11.61ms 相当）を考慮し、相対 5% or 絶対 0.1s の大きい方を許容
# 誤差とする。ただし現行 PJS 素材で 5 件が既知違反のため、既定は fail-closed
# にせず warn のみに留め、`--strict-duration` 指定時のみ problems へ昇格させて
# fail させる 2 段階仕様にする（`convert_pjs.py` の `DURATION_REL_TOLERANCE`/
# `DURATION_ABS_TOLERANCE_SEC` と同一閾値。両ファイルとも既存の record スクリプト
# 群の慣例に倣い、共有モジュール新設ではなく値を直接コピペする）。
DURATION_REL_TOLERANCE = 0.05
DURATION_ABS_TOLERANCE_SEC = 0.1


def _is_safe_wav_name(name: str, wav_dir: Path, resolved_wav_dir: Path) -> bool:
    """`name` が `wav_dir` 配下の `<name>.wav` を安全に指すかを判定する
    （字句検査 + resolve 封じ込め検査）。

    fix (2026-08-16, review #264 R4 P2): `check_ph_dur_duration` が
    `validate_speaker` の安全 name 判定より前に `wav_dir / f"{name}.wav"` を
    無検査で `wave.open` していたため、`../../outside` のような `name` や
    wav_dir 外へ逃げる symlink 経由で `wav_dir` 外の任意ファイルを読み取れて
    しまう穴があった。`validate_speaker` の安全集合判定（字句検査: 絶対パス・
    `..` セグメント・パス区切り文字を拒否 + resolve 後の実パスが `wav_dir`
    配下に収まることを確認する多層防御）と同一ロジックをここへ切り出し、
    両関数で共有する（open 前に必ずこの関数を通す）。
    """
    if not name or os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return False
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    candidate = wav_dir / f"{name}.wav"
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_wav_dir and resolved_wav_dir not in resolved_candidate.parents:
        return False
    return True


def wav_duration_seconds(path: Path) -> Optional[float]:
    """`path` の WAV 実長を秒で返す。読み取れない（非存在・非 PCM・破損）場合は
    `None`（この検査をスキップする合図。存在チェック自体は `validate_speaker`
    の既存ロジックが別途担当する）。"""
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            if rate <= 0:
                return None
            return w.getnframes() / rate
    except (wave.Error, OSError, EOFError):
        return None


def check_ph_dur_duration(
    speaker_name: str, wav_dir: Path, rows: Sequence[Dict[str, str]]
) -> List[str]:
    """`ph_dur` 合計と実 wav 長の乖離を検出する。乖離があっても例外にせず
    violation メッセージのリストとして全件収集して返す（`validate_speaker` と
    同じ「全収集してから呼び出し側が判定する」設計。既定では戻り値は warning
    として扱われ、`--strict-duration` 時のみ呼び出し側が problems へ昇格させる）。

    fix (2026-08-16, review #264 R4 P2): `row["name"]` を字句検査 + resolve
    封じ込め検査（`_is_safe_wav_name`、`validate_speaker` の安全集合判定と
    同一ロジック）を通してから初めて `wav_duration_seconds`（= WAV open）を
    呼ぶ。安全でない name（`../../outside` 等のパストラバーサル・wav_dir 外へ
    逃げる symlink 経由）は WAV を open せずスキップする（不安全 name の
    problems 昇格は `validate_speaker` 側が別途担当するため、ここでは黙って
    スキップしてよい）。
    """
    resolved_wav_dir = wav_dir.resolve()
    violations: List[str] = []
    for row in rows:
        try:
            ph_dur = [float(x) for x in row["ph_dur"].split()]
        except ValueError:
            continue  # 数値でない ph_dur は validate_speaker が別途検出する
        if not ph_dur or not all(math.isfinite(d) for d in ph_dur):
            continue  # 空/非有限は validate_speaker が別途検出する
        name = row["name"]
        if not _is_safe_wav_name(name, wav_dir, resolved_wav_dir):
            continue  # 不安全な name は open せずスキップ（validate_speaker が別途検出する）
        wav_dur = wav_duration_seconds(wav_dir / f"{name}.wav")
        if wav_dur is None or wav_dur <= 0:
            continue
        total = math.fsum(ph_dur)
        tol = max(wav_dur * DURATION_REL_TOLERANCE, DURATION_ABS_TOLERANCE_SEC)
        diff = abs(total - wav_dur)
        if diff > tol:
            ratio = total / wav_dur
            violations.append(
                f"{speaker_name}: {row['name']} ph_dur total {total:.4f}s vs wav "
                f"duration {wav_dur:.4f}s (ratio {ratio:.3f}x, diff {diff:.4f}s > "
                f"tolerance {tol:.4f}s)"
            )
    return violations


def check_note_dur_consistency(
    speaker_name: str, rows: Sequence[Dict[str, str]]
) -> List[str]:
    """`ph_dur` と `note_dur` のクロスフィールド不変量を検査する。

    fix (2026-08-16, review #264 R3): `convert_pjs.py` の EOF 末尾切り詰め/
    削除処理（`_drop_dead_phonemes`）は、EOF がノートの途中を切る場合
    （ノート自体は音素を1個以上残して生存するが末尾側の一部音素だけが
    truncate/drop される場合）に生存ノートの `note_dur` を旧値のまま残す
    実装バグを含んでいた。修正後もこの不変量を readback 時に enforce する
    ことで、将来の同型回帰（本関数がある限り上流の実装がどうであれ）を
    検出する。

    fix (2026-08-16, review #264 R8 P2 x2):
    1. `note_dur` の非数値・非有限（nan/inf）・非正値は、以前は他の既存
       チェックが別途検出する想定で黙って skip していたが、実際には
       `note_dur` を独立に検証する箇所が他に存在せず、壊れた `note_dur` が
       無検査のまま公開されていた（`ph_dur` は `validate_speaker` 本体が
       別途 non-numeric/non-finite/non-positive を検出するため、ここでは
       skip のままでよい）。`note_dur` はここが唯一の検査箇所のため、
       同じ3種の不正を violation として拒否する（fail-closed）。
    2. 合計同士の比較だけでは、ノート間で duration が移動しつつ合計が
       一致する破損（例: `ph_num="1 2"` / `ph_dur="1 1 1"` /
       `note_dur="2 1"`）を見逃す。`ph_num`（ノートごとの音素数、
       `sum(ph_num) == len(ph_dur)` かつ `len(ph_num) == len(note_dur)`）
       が有効な形で存在する場合は `ph_dur` を `ph_num` でノート単位へ
       グループ化し、各ノートの `ph_dur` 小計 ↔ 対応する `note_dur` を
       個別に比較する（閾値は `check_ph_dur_duration` と同一定数、ノート
       ごとの `ph_dur` 小計を基準にする）。`ph_num` が存在しない・不正な
       形（数値化不可・要素数不一致・`sum(ph_num) != len(ph_dur)`・非正の
       count を含む）の場合は、ノート単位の対応付けができないため合計
       同士の比較（旧来の挙動）へフォールバックする（`note_dur` を持たない
       speaker（例: Ritsu）由来の行では `ph_num` も存在しないため、この
       フォールバック経路を通ることはない）。

    `note_dur` を持たない行（例: Ritsu 側が `note_dur` を書き出さない場合）
    はスキップする。数値化できない・非有限な `ph_dur` は他の既存チェック
    （ph_seq/ph_dur 長さ不一致・非有限 ph_dur 等）が別途検出するため、
    ここではスキップする（全収集してから呼び出し側が判定する設計は
    `check_ph_dur_duration` と同じ）。
    """
    violations: List[str] = []
    for row in rows:
        note_dur_raw = row.get("note_dur")
        if not note_dur_raw:
            continue
        try:
            ph_dur = [float(x) for x in row["ph_dur"].split()]
        except ValueError:
            continue  # 数値化できない ph_dur は validate_speaker が別途検出する
        if not ph_dur or not all(math.isfinite(d) for d in ph_dur):
            continue  # 空/非有限な ph_dur は validate_speaker が別途検出する

        try:
            note_dur = [float(x) for x in note_dur_raw.split()]
        except ValueError:
            violations.append(f"{speaker_name}: {row['name']} has non-numeric note_dur")
            continue
        if not note_dur:
            violations.append(f"{speaker_name}: {row['name']} has empty note_dur")
            continue
        if not all(math.isfinite(d) for d in note_dur):
            violations.append(f"{speaker_name}: {row['name']} has non-finite note_dur")
            continue
        if any(d <= 0 for d in note_dur):
            violations.append(f"{speaker_name}: {row['name']} has non-positive note_dur")
            continue

        per_note_ph_totals: Optional[List[float]] = None
        ph_num_raw = row.get("ph_num")
        if ph_num_raw:
            try:
                ph_num = [int(x) for x in ph_num_raw.split()]
            except ValueError:
                ph_num = []
            if (
                ph_num
                and all(n > 0 for n in ph_num)
                and len(ph_num) == len(note_dur)
                and sum(ph_num) == len(ph_dur)
            ):
                per_note_ph_totals = []
                idx = 0
                for count in ph_num:
                    per_note_ph_totals.append(math.fsum(ph_dur[idx:idx + count]))
                    idx += count

        if per_note_ph_totals is not None:
            for note_idx, (ph_note_total, note_val) in enumerate(
                zip(per_note_ph_totals, note_dur)
            ):
                tol = max(ph_note_total * DURATION_REL_TOLERANCE, DURATION_ABS_TOLERANCE_SEC)
                diff = abs(ph_note_total - note_val)
                if diff > tol:
                    violations.append(
                        f"{speaker_name}: {row['name']} note[{note_idx}] ph_dur subtotal "
                        f"{ph_note_total:.4f}s vs note_dur {note_val:.4f}s "
                        f"(diff {diff:.4f}s > tolerance {tol:.4f}s)"
                    )
        else:
            ph_total = math.fsum(ph_dur)
            note_total = math.fsum(note_dur)
            tol = max(ph_total * DURATION_REL_TOLERANCE, DURATION_ABS_TOLERANCE_SEC)
            diff = abs(ph_total - note_total)
            if diff > tol:
                violations.append(
                    f"{speaker_name}: {row['name']} ph_dur total {ph_total:.4f}s vs "
                    f"note_dur total {note_total:.4f}s (diff {diff:.4f}s > tolerance {tol:.4f}s)"
                )
    return violations


def validate_speaker(
    speaker_name: str, raw_dir: Path, rows: Sequence[Dict[str, str]]
) -> List[str]:
    """1 話者分の `transcriptions.csv` + `wavs/` の整合性を検査する。

    問題があっても例外にせず、問題メッセージのリストとして返す（呼び出し側が
    全話者分を集約してから最終的に fail-closed で止める）。
    """
    problems: List[str] = []
    wav_dir = raw_dir / "wavs"
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        dup = sorted({n for n in names if names.count(n) > 1})
        problems.append(f"{speaker_name}: duplicate name(s) in transcriptions.csv: {dup[:5]}")

    # [P1 修正] (review #263 R16) transcriptions.csv の `name` は外部生成物
    # （convert_ritsu.py/convert_pjs.py の出力）であり、`wav_dir / f"{n}.wav"`
    # に無検査で連結すると絶対パスや `..` を含む `name` によって wav_dir の
    # 外を指すパスを組み立てられてしまう（パストラバーサル）。字句検査
    # （絶対パス・`..` セグメント・パス区切り文字を拒否）に加え、resolve 後の
    # 実パスが wav_dir 配下に収まることも検証する（シンボリックリンク等の
    # 迂回にも対応する多層防御）。違反は全件収集し、既存の fail-closed
    # 集約（呼び出し側で他の問題と合流して最終的に停止）に乗せる。判定ロジック
    # 本体は `_is_safe_wav_name`（review #264 R4 P2 で `check_ph_dur_duration`
    # と共有化）。
    resolved_wav_dir = wav_dir.resolve()
    unsafe: List[str] = []
    safe_names: List[str] = []
    for n in sorted(set(names)):
        if _is_safe_wav_name(n, wav_dir, resolved_wav_dir):
            safe_names.append(n)
        else:
            unsafe.append(n)
    if unsafe:
        problems.append(
            f"{speaker_name}: {len(unsafe)} unsafe name(s) in transcriptions.csv "
            f"(absolute path / '..' / path separator rejected), e.g. {sorted(unsafe)[:3]}"
        )

    missing = [n for n in safe_names if not (wav_dir / f"{n}.wav").exists()]
    if missing:
        problems.append(f"{speaker_name}: {len(missing)} wav missing under {wav_dir}, e.g. {missing[:3]}")

    for row in rows:
        ph_seq = row["ph_seq"].split()
        try:
            ph_dur = [float(x) for x in row["ph_dur"].split()]
        except ValueError:
            problems.append(f"{speaker_name}: {row['name']} has non-numeric ph_dur")
            continue
        if len(ph_seq) != len(ph_dur):
            problems.append(
                f"{speaker_name}: {row['name']} ph_seq/ph_dur length mismatch "
                f"({len(ph_seq)} vs {len(ph_dur)})"
            )
        if any(d <= 0 for d in ph_dur):
            problems.append(f"{speaker_name}: {row['name']} has non-positive ph_dur")
        # [P2 修正] (review #263 R15) `d <= 0` は nan（比較が常に False）・
        # inf（`0 < inf` なので通過）を検出できない。上流 `convert_ritsu.py`/
        # `convert_pjs.py` が非有限値を fail-closed で拒否するようになった
        # 後も、本関数は独立した下流ゲートとして自前でも非有限値を検出する
        # （多話者統合コーパスへ非有限 ph_dur が混入したまま公開されるのを
        # 防ぐ最終防衛線）。
        if any(not math.isfinite(d) for d in ph_dur):
            problems.append(f"{speaker_name}: {row['name']} has non-finite ph_dur")

    # fix (2026-08-16, review #264 R3): ph_dur 合計と note_dur 合計のクロス
    # フィールド不変量は構造的整合性（`check_ph_dur_duration` の「現行 PJS
    # 素材に既知5件の乖離あり」という経緯とは異なる）であり、常に fail-closed
    # で扱う（`--strict-duration` の分岐対象にしない）。
    problems += check_note_dur_consistency(speaker_name, rows)
    return problems


def build_config_yaml(
    dict_path: Path,
    binary_data_dir: Path,
    speakers: Sequence[Tuple[str, int, Path, Sequence[str]]],
    path_fmt: "Callable[[Path], str]" = str,
    *,
    max_updates: int = DEFAULT_MAX_UPDATES,
    val_check_interval: int = DEFAULT_VAL_CHECK_INTERVAL,
    num_ckpt_keep: int = DEFAULT_NUM_CKPT_KEEP,
) -> str:
    """`scripts/binarize.py --config <this>` にそのまま渡せる acoustic config
    を組み立てる（`s1b_multispeaker_acoustic_config.yaml` と同一構造）。
    `speakers` は `(speaker_name, spk_id, raw_data_dir, test_prefixes)` の列。

    `path_fmt`（review #263 R12 P2 新設）: `dict_path`/`raw_data_dir`/
    `binary_data_dir` の 3 パスフィールドをどう文字列化するかを差し替える
    フック。既定 `str` は従来どおり絶対パスをそのまま書く（実行時 config は
    走行中の学習・binarize が参照するため無変更を維持する）。ハッシュ用の
    正規化コピー（`build_normalized_config_yaml`）はこれを実行者 home 非依存の
    表現へ差し替えるために使う。

    `max_updates`/`val_check_interval`/`num_ckpt_keep`（review #263 R13 P1
    昇格）: 学習規模に関わる 3 フィールドを config 末尾へ書き込む。従来は
    `S1_GPU_RUNBOOK.md` §3 が binarize 実行前に手動追記していたが、正規化
    コピー（`.normalized.yaml`）はビルド時点で既に書き出し済みのため
    手動追記の対象外となり、実効 config と `§3.1` が pin する manifest 対象が
    食い違っていた（R13 指摘）。ビルダー自身が最終形（live config + 正規化
    コピーの両方）を出力することでこの食い違いを構造的に無くす。
    """
    lines: List[str] = []
    lines.append("base_config:")
    lines.append("  - configs/acoustic.yaml")
    lines.append("")
    lines.append("dictionaries:")
    lines.append(f"  ja: {path_fmt(dict_path)}")
    lines.append("extra_phonemes: []")
    lines.append("merged_phoneme_groups: []")
    lines.append("")
    lines.append("datasets:")
    for name, spk_id, raw_dir, prefixes in speakers:
        lines.append(f"  - raw_data_dir: {path_fmt(raw_dir)}")
        lines.append(f"    speaker: {name}")
        lines.append(f"    spk_id: {spk_id}")
        lines.append("    language: ja")
        lines.append("    test_prefixes:")
        for p in prefixes:
            lines.append(f"      - {p}")
    lines.append("")
    lines.append(f"binary_data_dir: {path_fmt(binary_data_dir)}")
    lines.append("")
    lines.append("# CPU-only, checkpoint-free feature extraction")
    lines.append("# (S1a/S1b と同じ理由: hnsep 既定 'vr' は checkpoints/vr/model.pt を")
    lines.append("# 無条件ロードするが CPU オフライン環境では未取得のため 'world' へ上書き)")
    lines.append("pe: parselmouth")
    lines.append("hnsep: world")
    lines.append("")
    lines.append("use_lang_id: false")
    lines.append("num_lang: 1")
    lines.append("use_spk_id: true")
    lines.append(f"num_spk: {len(speakers)}")
    lines.append("")
    lines.append("binarization_args:")
    lines.append("  shuffle: true")
    lines.append("  num_workers: 0")
    lines.append(f"max_updates: {max_updates}")
    lines.append(f"val_check_interval: {val_check_interval}")
    lines.append(f"num_ckpt_keep: {num_ckpt_keep}")
    lines.append("")
    return "\n".join(lines)


def normalize_path_field(path: Path, config_dir: Path) -> str:
    """review #263 R12 P2: config のパスフィールド（dictionary/raw_data_dir/
    binary_data_dir）を、ハッシュ対象として実行者 home に依存しない形へ
    正規化する。

    `config_dir`（正規化コピー自身の所在）の子孫であれば `..` を含まない
    相対パスへ変換する（`$OUT` 配下に config・辞書・raw dir・binary dir が
    すべて収まる runbook §3 の標準レイアウトでは、`$OUT` の絶対位置に関わらず
    常に同一の相対パスになる）。子孫でない（`..` で始まる = config の外へ
    出る）場合は、実行者ごとに `..` の段数が変わり得て digest がやはり
    executor 依存になるため、basename のみを埋め込んだ固定プレースホルダへ
    落とす。
    """
    resolved_path = path.resolve()
    resolved_config_dir = config_dir.resolve()
    try:
        rel = os.path.relpath(resolved_path, start=resolved_config_dir)
    except ValueError:
        # 異なるドライブ (Windows) 等、relpath 自体が計算不能な場合。
        return f"<EXTERNAL_PATH:{resolved_path.name}>"
    if rel.split(os.sep, 1)[0] == os.pardir:
        return f"<EXTERNAL_PATH:{resolved_path.name}>"
    return rel.replace(os.sep, "/")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ritsu-raw-dir", type=Path, required=True,
        help="convert_ritsu.py の --out-dir (transcriptions.csv + wavs/ を含む)",
    )
    parser.add_argument(
        "--pjs-raw-dir", type=Path, required=True,
        help="convert_pjs.py が生成する '<staging-dir>/diffsinger_db/'",
    )
    parser.add_argument("--out-dict", type=Path, required=True, help="統合辞書の出力先")
    parser.add_argument("--out-config", type=Path, required=True, help="多話者 acoustic config の出力先")
    parser.add_argument(
        "--binary-data-dir", type=Path, required=True,
        help="config の binary_data_dir に書く binarize 出力先 (未生成のパスでよい)",
    )
    parser.add_argument("--n-test-prefixes", type=int, default=5, help="話者ごとの検証用セグメント数")
    parser.add_argument("--report", type=Path, default=None, help="検証レポート JSON の出力先 (省略可)")
    # review #263 R13 P1: 学習規模フィールドを CLI 引数へ昇格（既定値は
    # S1_GPU_RUNBOOK.md 現行値と同一。build_config_yaml docstring 参照）。
    parser.add_argument(
        "--max-updates", type=int, default=DEFAULT_MAX_UPDATES,
        help=f"config へ書き込む max_updates (既定: {DEFAULT_MAX_UPDATES})",
    )
    parser.add_argument(
        "--val-check-interval", type=int, default=DEFAULT_VAL_CHECK_INTERVAL,
        help=f"config へ書き込む val_check_interval (既定: {DEFAULT_VAL_CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--num-ckpt-keep", type=int, default=DEFAULT_NUM_CKPT_KEEP,
        help=f"config へ書き込む num_ckpt_keep (既定: {DEFAULT_NUM_CKPT_KEEP})",
    )
    parser.add_argument(
        "--strict-duration", action="store_true",
        help="ph_dur 合計と実 wav 長の乖離（相対5%% or 絶対0.1sの大きい方を超過）を"
             "warning ではなく problem として扱い、検証を fail させる。"
             "既定 (指定なし) は warning のみで公開は継続する "
             "(現行 PJS 素材で 5 件が既知違反のため)。",
    )
    args = parser.parse_args(argv)

    ritsu_csv = args.ritsu_raw_dir / "transcriptions.csv"
    pjs_csv = args.pjs_raw_dir / "transcriptions.csv"
    if not ritsu_csv.exists():
        print(f"error: not found: {ritsu_csv}", file=sys.stderr)
        return 1
    if not pjs_csv.exists():
        print(f"error: not found: {pjs_csv}", file=sys.stderr)
        return 1

    ritsu_rows = read_transcriptions(ritsu_csv)
    pjs_rows = read_transcriptions(pjs_csv)

    problems: List[str] = []
    problems += validate_speaker("ritsu", args.ritsu_raw_dir, ritsu_rows)
    problems += validate_speaker("pjs", args.pjs_raw_dir, pjs_rows)

    duration_warnings: List[str] = []
    duration_warnings += check_ph_dur_duration(
        "ritsu", args.ritsu_raw_dir / "wavs", ritsu_rows
    )
    duration_warnings += check_ph_dur_duration(
        "pjs", args.pjs_raw_dir / "wavs", pjs_rows
    )
    for w in duration_warnings:
        print(f"{'problem' if args.strict_duration else 'warning'}: {w}", file=sys.stderr)
    if args.strict_duration:
        problems += duration_warnings

    ritsu_symbols = collect_phoneme_symbols(ritsu_rows)
    pjs_symbols = collect_phoneme_symbols(pjs_rows)
    merged_pairs = build_merged_dict([ritsu_symbols, pjs_symbols])
    dict_text = render_dict_text(merged_pairs)

    ritsu_prefixes = select_test_prefixes(ritsu_rows, args.n_test_prefixes)
    pjs_prefixes = select_test_prefixes(pjs_rows, args.n_test_prefixes)

    speakers = [
        ("ritsu", 0, args.ritsu_raw_dir, ritsu_prefixes),
        ("pjs", 1, args.pjs_raw_dir, pjs_prefixes),
    ]
    config_text = build_config_yaml(
        dict_path=args.out_dict,
        binary_data_dir=args.binary_data_dir,
        speakers=speakers,
        max_updates=args.max_updates,
        val_check_interval=args.val_check_interval,
        num_ckpt_keep=args.num_ckpt_keep,
    )

    # review #263 R12 P2: 実行時 config (`args.out_config` へ公開する上記
    # `config_text`) は絶対パス（dictionary/raw_data_dir/binary_data_dir）を
    # 含んだまま無変更で維持する（走行中の学習・binarize が実際に参照する
    # ファイルのため、パス形式を変えると互換性を壊す）。ハッシュ用にのみ、
    # パスフィールドを config 自身の所在からの相対 / 固定プレースホルダへ
    # 正規化した別コピー `<out-config>.normalized.yaml` を追加で書き出す
    # （§3.1 の manifest はこちらを pin する — 実効 config の内容は同一だが、
    # 実行者の home ディレクトリ配置に digest が左右されなくなる）。
    normalized_config_path = args.out_config.with_name(args.out_config.name + ".normalized.yaml")
    config_dir = args.out_config.parent
    normalized_config_text = build_config_yaml(
        dict_path=args.out_dict,
        binary_data_dir=args.binary_data_dir,
        speakers=speakers,
        path_fmt=lambda p: normalize_path_field(p, config_dir),
        max_updates=args.max_updates,
        val_check_interval=args.val_check_interval,
        num_ckpt_keep=args.num_ckpt_keep,
    )

    report = {
        "ritsu_segments": len(ritsu_rows),
        "pjs_segments": len(pjs_rows),
        "total_segments": len(ritsu_rows) + len(pjs_rows),
        "merged_dict_symbols": len(merged_pairs),
        "ritsu_only_symbols": sorted(ritsu_symbols - pjs_symbols - SPECIAL_TOKENS),
        "pjs_only_symbols": sorted(pjs_symbols - ritsu_symbols - SPECIAL_TOKENS),
        "shared_symbols": sorted((ritsu_symbols & pjs_symbols) - SPECIAL_TOKENS),
        "ritsu_test_prefixes": ritsu_prefixes,
        "pjs_test_prefixes": pjs_prefixes,
        "problems": problems,
        "duration_warnings": duration_warnings,
        "strict_duration": args.strict_duration,
    }
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(report_text)

    if problems:
        # [P2 修正] 検証失敗時は辞書/config/report のいずれも公開しない
        # （全構築・全検証をここまでで終え、失敗が確定したら成果物には
        # 触れずに return する。既存の有効な成果物を壊さない）。
        print(f"validation FAILED: {len(problems)} problem(s)", file=sys.stderr)
        return 1

    # 全構築・全検証が終わり、成功が確定してからのみ、辞書/config/report を
    # 遷移的に（全て公開できるか事前検証してから一括で）公開する。
    outputs: List[Tuple[Path, str]] = [
        (args.out_dict, dict_text),
        (args.out_config, config_text),
        (normalized_config_path, normalized_config_text),
    ]
    if args.report is not None:
        outputs.append((args.report, report_text + "\n"))

    # [P1 修正] (review #263 R4) 全出力パスを相互および両 raw dir（入力の
    # transcriptions.csv/wavs/参照ファイル）と照合し、衝突があれば公開前に
    # fail-closed で拒否する。
    _reject_output_collision(
        [p for p, _ in outputs],
        protected_roots=[args.ritsu_raw_dir, args.pjs_raw_dir],
    )

    try:
        _publish_outputs(outputs)
    except OSError as exc:
        # 公開失敗（既存ディレクトリを出力先に指定 / 書込不可等）。
        # `_publish_outputs` が事前検証・巻き戻しで混合世代を防いだ上での
        # 失敗であり、既存の有効な成果物は無事なはず（呼び出し側で再実行可能）。
        print(f"error: failed to publish outputs: {exc}", file=sys.stderr)
        return 1

    print("validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
