"""S4 run 5 — RunPod API 無人ブートストラップ（DESIGN_S4_run5.md §3）。

Pod 作成時の起動コマンド（`scripts/run5_pod_entry.sh` 参照）から呼ばれ、
SSH 対話なしで clone → 4 ゲート → 素材照合 → 再生成 → pin 照合 → 学習 →
Google Drive 退避 → 自動停止を単一実行で完走する（クロー非経由 =
2026-08-17 User 決定事項。稼働中の介入手段は持たない前提で fail-closed:
異常時は成果物・ログを Drive へ退避してから自己停止する）。

**GPU/Pod 実測未実施** — 本ファイルは `gate_synth_run4.py` と同じ正直会計を
とる: 本開発環境には GPU・torch・rclone・runpodctl が無いため、ロジック層
（stage 計画・pin 検証・phase config 導出・milestone 検知・wall-clock 判定・
heartbeat 記帳・コマンド組み立て）のみ `tests/test_run5_bootstrap.py` で
検証済み。実行系（subprocess で叩く render/binarize/train/rclone）の初回
実測は run 5 本番が兼ねる。

## ステージ（DESIGN_S4 §3.1 の段階に一致。`--plan` で一覧を印字する）

1.  `preflight`      : env 検査（§ 環境変数）+ `run5_material_pins.json` の
                       PENDING 検査（sha256 が null の素材が 1 件でもあれば
                       素材取得に入らず fail-closed。ffmpeg/vocoder の 2 件は
                       2026-08-18 に転記完了 — pins 表の provenance 欄参照）
2.  `gates`          : runbook §2.2 の 4 ゲート（数値スタック版 pin / SIMD
                       受け入れ X86_V3 / silent no-op 検査 / cache 来歴）。
                       ゲート 3 は「設定が効いたかは必ずゲート 2 の
                       show_config 実測で確認する」という規律そのもので
                       あり、ゲート 2 の実測成功が充足を兼ねる。ゲート 4 は
                       新規 Pod + 新規 `--out-dir`（既存 out-dir があれば
                       fail-closed）で cache 来歴を構造的に保証する
3.  `materials`      : 素材取得 + sha256 全数照合（pins 表と 1 件でも
                       不一致なら停止）。user 宅録原本は既定で成果物
                       フォルダ内 `user_sources/` から rclone 取得
                       （`RUN5_USER_SOURCES_URL` 指定時のみ直リンク経路）し、
                       `user_donor_ledger.json` の `source_sha256` と 17/17
                       照合する
4.  `datasets`       : D3 再生成（`run_d3_cells.py`・tripwire 込み）→
                       `convert_d3`（出力は d3synth 専用 raw dir）→ user
                       replay 正規化 → `convert_user` → それぞれ
                       `run4_dataset_pins.json` と全数照合（データ内容は
                       run 4 と同一 = DESIGN_S4 §1.1 のため同じ pin）→
                       D2/pjs 変換
5.  `assemble`       : `assemble_run4.py`（4 話者・spk_id map v2）→
                       `refresh-config-pin`（生リスト形状 v2 検証を兼ねる）→
                       assembly_manifest の d3synth/user セクションを
                       `run4_dataset_pins.json` と照合
6.  `binarize`       : DiffSinger `scripts/binarize.py`
7.  `train_phase_a`  : スクラッチ 0→5K（run 3 レシピ再現の前段。
                       runbook §8.3 裁定: スクラッチ開始）
8.  `train_phase_b`  : finetune 機構再適用（optimizer 新品・
                       `finetune_ckpt_path` = phase A の 5K checkpoint）で
                       0→40K。5K 節目毎に milestone watcher が NaN スキャン +
                       Drive push（NaN 検知 = 即退避・停止）
9.  `salvage`        : 節目 checkpoint / config / 辞書 / manifest / log / TB
                       の Drive push（成功・失敗どちらの経路でも必ず通る）
10. `self_stop`      : `runpodctl stop pod $RUNPOD_POD_ID`（Pod 放置課金の
                       構造的排除。ディスク残置 = 保険経路 (c) はそのまま）

## 学習 2 フェーズについて（run 3 レシピの機械化）

runbook §4/§8.3: run 3 は「スクラッチ開始 + 5K 節目で optimizer 新品の
finetune 機構再適用」。無人化では live config（`run4_config_datasets.yaml`
— assemble が生成し assembly_manifest に pin 済み）へは手を加えず、
そこから 2 つの phase config を**導出**する:

- phase A: `finetune_enabled: false`・`max_updates: 5000` + 学習 4 項目
  （bf16-mixed / lr 0.0002 / clip_grad_norm 1.0 — s1_record/s3_record の
  実測記述に基づく。runbook §4 の「キー名は s1_record の文章記述からの
  引用であり実 YAML との一次照合はできていない」という限界も継承する）
- phase B: `finetune_enabled: true`・`finetune_ckpt_path: <phase A の 5K
  checkpoint>`・`max_updates: 40000` + 同 4 項目

導出した 2 config の sha256 は `run5_training_manifest.json` として記帳し
Drive へ push する（「実際に実行された学習の実 config を証明する pin」の
無人版 — runbook §4 の手動編集→refresh フローの代替。live config 自体は
無編集のため assembly_manifest の記帳と常に一致する）。

## 環境変数（Pod 作成時に注入。リポジトリへはコミットしない）

- `RUN5_RCLONE_CONF_B64`  : rclone.conf の base64（成果物専用フォルダに
                            権限を限定したスコープ — Drive 全域トークン
                            不可 = DESIGN_S4 §3.3。リモート名 `run5drive`）
- `RUN5_DRIVE_FOLDER_ID`  : 成果物フォルダ ID（退避先。**user 宅録原本の
                            入力元も兼ねる** — 下記）
- `RUN5_USER_SOURCES_URL` : （任意）user 宅録原本アーカイブの直リンク。
                            **省略時が既定経路（2026-08-18 User 裁定・
                            案 A）**: 成果物フォルダ内の `user_sources/`
                            サブフォルダ（原本 17 本を User がコピーして
                            おく）から同じ rclone トークンで取得する —
                            追加の共有設定・直リンク発行が不要で、原本を
                            リンク公開せずに済む
- `RUNPOD_POD_ID`         : RunPod が注入する Pod 自身の ID（self-stop 用）

## 予算・停止条件（DESIGN_S4 §3.4）

- wall-clock 上限 24h（cap $8 に対し $0.22/h × 24h ≈ $5.3 — スクリプト内
  タイムアウト。スクリプト自体が死ぬ場合の露出は Q8 として設計受容済み）
- NaN 検知（5K 節目の state_dict 非有限値スキャン）= 即退避・停止
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import yaml

_THIS_DIR = Path(__file__).resolve().parent
FOUNDRY_DIR = _THIS_DIR.parent
REPO_ROOT = FOUNDRY_DIR.parent.parent

MATERIAL_PINS_PATH = FOUNDRY_DIR / "results_s3" / "run5_material_pins.json"
DATASET_PINS_PATH = FOUNDRY_DIR / "results_s3" / "run4_dataset_pins.json"
USER_DONOR_LEDGER_PATH = FOUNDRY_DIR / "recording_kit" / "user_donor_ledger.json"

WALL_CLOCK_LIMIT_SECONDS = 24 * 3600  # DESIGN_S4 §3.4
MILESTONE_STEPS = (5000, 10000, 20000, 40000)
POLL_INTERVAL_SECONDS = 60  # milestone watcher のポーリング周期
# torch.load 失敗（書き込み途中/破損）の再試行上限。超過は「読めない
# checkpoint」として停止する — NaN 判定とは区別する（review セルフレビュー #2）。
NAN_SCAN_MAX_RETRIES = 5
PHASE_A_MAX_UPDATES = 5000
PHASE_B_MAX_UPDATES = 40000
EXP_NAME_PHASE_A = "s4_run5_acoustic_scratch"
EXP_NAME_PHASE_B = "s4_run5_acoustic_v1"

# Drive 退避の run 別 prefix（run 5 = フォルダ直下 ""・run 6 以降 = "run6" 等。
# _rclone_argv が dest と結合する — 同一成果物フォルダ内で run が混ざらない）。
REMOTE_PREFIX = ""

# --- run プロファイル（DESIGN_S5_run6.md §2-3: run 依存定数のパラメータ化）。
# 環境変数 RUN_PROFILE（既定 "run5"）で選択し、apply_run_profile() が
# モジュール定数を差し替える。run5 プロファイルの**定数値**は run 5 実走時と
# 同一（ただし gates 段の ANALYSIS_STACK_PIN 強制は全プロファイル共通の
# 前進措置 — run 5 実走時は未 pin で numba 0.66 系を解決していたと推定
# 〔freeze 未捕獲〕であり、歴史的環境の完全再現ではない）。convert_user の正規化目標
# （normalize_loudness_lufs）は dataset pins ファイルの
# user.normalization_target_lufs から読む（pin と実行値の単一ソース化）。
RUN_PROFILES: Dict[str, Dict[str, object]] = {
    "run5": {
        "run_id": "s4_run5",
        "exp_a": "s4_run5_acoustic_scratch",
        "exp_b": "s4_run5_acoustic_v1",
        "dataset_pins": "run4_dataset_pins.json",
        "remote_prefix": "",
        "assemble_profile": "run5",
        "expected_spk_map": {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3},
        # preflight の前回 manifest 探索順（自 run の resume → 前 run の置き場）。
        "prev_manifest_prefixes": ("",),
    },
    "run6": {
        "run_id": "s5_run6",
        "exp_a": "s5_run6_acoustic_scratch",
        "exp_b": "s5_run6_acoustic_v1",
        "dataset_pins": "run6_dataset_pins.json",
        "remote_prefix": "run6",
        "assemble_profile": "run5",
        "expected_spk_map": {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3},
        # run 6 の比較相手 = run 5（フォルダ直下）— DESIGN_S5 §3 の正常系。
        "prev_manifest_prefixes": ("run6", ""),
    },
    # run 7（DESIGN_S6_run7.md）: 教師交代 = d3synth 引退（spk_id 3 恒久欠番）
    # → amitaro（spk_id 4・num_spk 5）。教師データの生成/検証分岐は
    # dataset pins のセクション構成（"d3" or "amitaro"）が単一ソース。
    "run7": {
        "run_id": "s6_run7",
        "exp_a": "s6_run7_acoustic_scratch",
        "exp_b": "s6_run7_acoustic_v1",
        "dataset_pins": "run7_dataset_pins.json",
        "remote_prefix": "run7",
        "assemble_profile": "run7",
        "expected_spk_map": {"ritsu": 0, "pjs": 1, "user": 2, "amitaro": 4},
        # run 7 の比較相手 = run 6（DESIGN_S6 §4）。フォルダ直下（run 5）へは
        # フォールバックしない — 誤ベースライン比較を黙って記帳するより
        # 「no previous manifest」の方が正直（セルフレビュー #2）。
        "prev_manifest_prefixes": ("run7", "run6"),
    },
}


def apply_run_profile(name: str) -> Dict[str, object]:
    """RUN_PROFILES[name] をモジュール定数へ反映する（main() 冒頭と
    テストから呼ぶ。未知名は fail-closed）。戻り値はプロファイル辞書。"""
    global RUN_ID, EXP_NAME_PHASE_A, EXP_NAME_PHASE_B, PHASE_REMOTE_DIRS
    global DATASET_PINS_PATH, REMOTE_PREFIX, ASSEMBLE_PROFILE, EXPECTED_SPK_MAP
    global PREV_MANIFEST_PREFIXES
    if name not in RUN_PROFILES:
        raise ValueError(
            f"unknown RUN_PROFILE {name!r} (expected one of {sorted(RUN_PROFILES)})"
        )
    profile = RUN_PROFILES[name]
    RUN_ID = str(profile["run_id"])
    EXP_NAME_PHASE_A = str(profile["exp_a"])
    EXP_NAME_PHASE_B = str(profile["exp_b"])
    PHASE_REMOTE_DIRS = {EXP_NAME_PHASE_A: "phase_a", EXP_NAME_PHASE_B: "phase_b"}
    DATASET_PINS_PATH = FOUNDRY_DIR / "results_s3" / str(profile["dataset_pins"])
    REMOTE_PREFIX = str(profile["remote_prefix"])
    ASSEMBLE_PROFILE = str(profile["assemble_profile"])
    EXPECTED_SPK_MAP = dict(profile["expected_spk_map"])  # type: ignore[arg-type]
    PREV_MANIFEST_PREFIXES = tuple(profile["prev_manifest_prefixes"])  # type: ignore[arg-type]
    return profile

# phase 別の Drive 退避 namespace（2026-08-19 外部レビュー P1: run 5 実走で
# phase A/B の `model_ckpt_steps_5000.ckpt` と `config.yaml` が同名 push で
# 後勝ち上書きされ、phase A 5K は監視側の手動 copyto 保全を要した —
# s4_record §5.6。milestone push と salvage の両方がこの規則を使い、
# 同 basename でも独立に保存される）。
PHASE_REMOTE_DIRS: Dict[str, str] = {
    EXP_NAME_PHASE_A: "phase_a",
    EXP_NAME_PHASE_B: "phase_b",
}

# 機械可読な実行正本（2026-08-19 外部レビュー P2: Markdown record とは別に、
# 次回 run の preflight が前回成功走行と機械比較できる 1 ファイルを残す）。
EXEC_MANIFEST_SCHEMA = "run-execution-manifest/0.1"
EXEC_MANIFEST_NAME = "run_execution_manifest.json"
RUN_ID = "s4_run5"

# assemble のプロファイルと binarize 後 spk_map.json の期待マップ（run 7・
# DESIGN_S6_run7.md §0-2: DiffSinger build_spk_map は spk_id 未指定エントリへ
# 最小空き番号を自動採番するため、恒久欠番 3 が黙って埋まる事故を
# verify_spk_map で fail-closed に検出する）。apply_run_profile が差し替える。
ASSEMBLE_PROFILE = "run5"
EXPECTED_SPK_MAP: Dict[str, int] = {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}
PREV_MANIFEST_PREFIXES: Tuple[str, ...] = ("",)

# runbook §4 の「LR/finetune/精度/勾配クリップ」4 項目（s1_record の文章
# 記述 + s3_record の実測行〔bf16 / clip 1.0 / lr 2e-4〕からの引用。実 YAML
# との一次照合未達という限界は runbook §4 に明記済み — 本スクリプトも
# その限界ごと継承する）。finetune 系 2 キーは phase 導出側で付与する。
TRAINING_FIELDS: Dict[str, object] = {
    "pl_trainer_precision": "bf16-mixed",
    "optimizer_args": {"lr": 0.0002},
    "clip_grad_norm": 1.0,
}

STAGE_PLAN: Tuple[str, ...] = (
    "preflight", "gates", "materials", "datasets", "assemble",
    "binarize", "train_phase_a", "train_phase_b", "salvage", "self_stop",
)

# RUN5_USER_SOURCES_URL は必須から外れた（2026-08-18 User 裁定・案 A: 既定は
# 成果物フォルダ内 user_sources/ からの rclone 取得。URL は代替経路）。
REQUIRED_ENV_VARS = (
    "RUN5_RCLONE_CONF_B64", "RUN5_DRIVE_FOLDER_ID",
)

# runbook §2.2 ゲート 1/2 の判定ワンライナー（明示分岐 + sys.exit 符号化 —
# assert は PYTHONOPTIMIZE で剥がされるため使わない。runbook から逐語）。
GATE1_SNIPPET = (
    "import sys, numpy, scipy, pyworld, soundfile; "
    "expected = {'numpy': '2.4.6', 'scipy': '1.17.1', 'pyworld': '0.3.5', "
    "'soundfile': '0.14.0'}; "
    "actual = {'numpy': numpy.__version__, 'scipy': scipy.__version__, "
    "'pyworld': pyworld.__version__, 'soundfile': soundfile.__version__}; "
    "ok = actual == expected; "
    "print(('numeric stack pin OK' if ok else 'numeric stack pin NG:'), actual); "
    "sys.exit(0 if ok else 1)"
)
GATE2_SNIPPET = (
    "import sys, numpy; "
    "f = numpy.show_config('dicts')['SIMD Extensions']['found']; "
    "ok = 'X86_V3' in f and 'X86_V4' not in f; "
    "print(('SIMD gate OK:' if ok else 'SIMD gate NG:'), f); "
    "sys.exit(0 if ok else 1)"
)
NUMERIC_STACK_PIN = (
    "numpy==2.4.6", "scipy==1.17.1", "pyworld==0.3.5", "soundfile==0.14.0",
)

# 解析系 pin（2026-08-19 実測: numba 0.67.0 × numpy 2.4.6 は librosa.pyin
# が SIGSEGV。0.66.0 で解消。未 pin 依存のドリフトが数日で走行を壊した実例 —
# requirements_run5_pod.lock と同期・gates 段で NUMERIC_STACK_PIN と同時に
# 強制インストールする。gate1 の版検査対象には含めない〔検査契約は不変〕。
# pyloudnorm は run 6 の正規化数値そのものを決める依存（run6 pin は
# 0.2.0 で生成）— 未 pin だと同じドリフト類型で pin 照合が割れるため同梱）。
ANALYSIS_STACK_PIN = (
    "numba==0.66.0", "librosa==0.11.0", "pyloudnorm==0.2.0",
)

# ffmpeg 環境契約 1（s3_record 2026-08-17）: 44.1kHz 変換バイトは libavformat
# 60.16.100（6.1.x）に依存する。
FFMPEG_LIBAVFORMAT_PIN = (60, 16, 100)
# `ffmpeg -version` はライブラリ版を `%2d.%3d.%3d` で桁揃えして印字するため、
# 実出力は `libavformat    60. 16.100 / 60. 16.100` のように **ドットの後に
# 空白が入る**（2026-08-18 実測）。素朴な部分文字列照合（`"60.16.100" in out`）は
# 構造的に成立せず、**run 5 初回起動はこれで fail-closed 停止した** — pin も
# バイナリ（bin/ffmpeg sha256 照合は通過）も正しく、検査文字列だけが誤りだった。
_LIBAVFORMAT_RE = re.compile(r"libavformat\s+(\d+)\.\s*(\d+)\.\s*(\d+)")


def parse_libavformat_version(version_output: str) -> Optional[Tuple[int, int, int]]:
    """`ffmpeg -version` の出力から libavformat の `(major, minor, micro)` を
    返す（該当行が無ければ None）。空白揺れに耐える正規表現で読む
    （§ `_LIBAVFORMAT_RE` のコメント参照）。"""
    m = _LIBAVFORMAT_RE.search(version_output)
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def summarize_ffmpeg_version(version_output: str, max_chars: int = 800) -> str:
    """`ffmpeg -version` 出力から診断に要る行だけを抜き出す（先頭の
    banner 行 + `lib*` の版一覧）。`configuration:` 行は 2KB 級で
    heartbeat detail を埋め尽くすため落とす。

    review（PR#271 セルフレビュー #1）: 版不一致で停止すると無人 Pod は
    salvage → self-stop まで進んで人が入れない。エラーに parsed 値だけを
    載せると「何が出ていたのか」が失われ、本コミットが直したのと同じ
    証跡ゼロの停止になる — 実出力の要約を必ず同梱する。"""
    lines = version_output.splitlines()
    kept = [ln for ln in lines[:1]] + [ln for ln in lines if ln.lstrip().startswith("lib")]
    text = "\n".join(kept) if kept else version_output
    return text[:max_chars]


class PinPendingError(RuntimeError):
    """`run5_material_pins.json` に sha256 未転記（null）の素材が残っている
    場合に送出する（fail-closed。素材取得を一切始めない）。"""

    def __init__(self, pending: Sequence[str]) -> None:
        self.pending = list(pending)
        super().__init__(
            "material pin(s) still PENDING (fail-closed, nothing downloaded): "
            f"{self.pending} — run5_material_pins.json の当該エントリへ"
            "クロー報告値（URL + sha256）を転記するまで run 5 は起動できない"
            "（DESIGN_S4_run5.md §3.2）"
        )


class PinMismatchError(RuntimeError):
    """取得素材・再生成データセットの sha256 が pin と一致しない場合に
    送出する（照合不一致はそこで停止 — S3_RUN4_RUNBOOK.md §2 の規律）。"""

    def __init__(self, diffs: Sequence[str]) -> None:
        self.diffs = list(diffs)
        super().__init__(
            f"{len(self.diffs)} pin mismatch(es) (fail-closed): {self.diffs[:10]}"
        )


class StageFailure(RuntimeError):
    """ステージ実行が非 0 exit・検証不一致で失敗した場合の包み例外。
    `main()` はこれを捕捉して salvage → self_stop へ必ず進む。"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_material_pins(path: Optional[Path] = None) -> Dict[str, dict]:
    """pin 表を読み、PENDING（`sha256` で終わるキーを持つのに null のままの
    素材）があれば `PinPendingError` で fail-closed する。戻り値は materials
    dict。`path` 省略時はモジュール属性 `MATERIAL_PINS_PATH` を**呼び出し時**
    に解決する（テストが monkeypatch で差し替えられるように — def 時束縛の
    既定引数にしない）。

    review（run 5 実装 PR セルフレビュー #9）: 検査対象はトップレベル
    `sha256` だけでなく `*_sha256` 全キー（`ffmpeg_bin_sha256`/
    `model_ckpt_sha256` 等）。どれか 1 つでも null に戻す退行は素材取得前に
    ここで止まる（「未転記 hash はダウンロード前に fail-closed」という
    pins 表の契約と一致させる）。"""
    if path is None:
        path = MATERIAL_PINS_PATH
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    materials = data["materials"]
    pending = sorted(
        f"{name}.{key}" if key != "sha256" else name
        for name, entry in materials.items()
        for key, value in entry.items()
        if (key == "sha256" or key.endswith("_sha256")) and value is None
    )
    if pending:
        raise PinPendingError(pending)
    return materials


def check_required_env(environ: Dict[str, str]) -> List[str]:
    """不足している必須環境変数名のリストを返す（空 = OK）。"""
    return [name for name in REQUIRED_ENV_VARS if not environ.get(name)]


def verify_file_sha256(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise PinMismatchError([f"{label}: sha256 {actual} != pinned {expected}"])


def verify_dataset_against_pins(
    dataset_dir: Path, pin_section: dict, label: str
) -> List[str]:
    """再生成データセット（transcriptions.csv + wavs/ + 任意 exclusions.json）
    を `run4_dataset_pins.json` の 1 セクションと全数照合し、不一致の説明
    リストを返す（空 = 全一致）。"""
    diffs: List[str] = []
    csv_path = dataset_dir / "transcriptions.csv"
    actual_csv = sha256_file(csv_path)
    if actual_csv != pin_section["transcriptions_csv_sha256"]:
        diffs.append(f"{label}/transcriptions.csv: {actual_csv} != pin")
    pinned_wavs: Dict[str, str] = pin_section["wav_sha256"]
    actual_names = {p.name for p in (dataset_dir / "wavs").glob("*.wav")}
    if actual_names != set(pinned_wavs):
        diffs.append(
            f"{label}/wavs: file set mismatch (missing={sorted(set(pinned_wavs) - actual_names)[:5]}, "
            f"extra={sorted(actual_names - set(pinned_wavs))[:5]})"
        )
    for name in sorted(set(pinned_wavs) & actual_names):
        actual = sha256_file(dataset_dir / "wavs" / name)
        if actual != pinned_wavs[name]:
            diffs.append(f"{label}/wavs/{name}: {actual} != pin")
    if "exclusions_json_sha256" in pin_section:
        excl = dataset_dir / "exclusions.json"
        if not excl.exists():
            diffs.append(f"{label}/exclusions.json: missing")
        else:
            actual = sha256_file(excl)
            if actual != pin_section["exclusions_json_sha256"]:
                diffs.append(f"{label}/exclusions.json: {actual} != pin")
    if "loudness_normalization_json_sha256" in pin_section:
        # run 6: 正規化会計も pin 対象（会計が変わる = 正規化実装/目標値が
        # 変わったということ — 黙って通さない）。
        rep = dataset_dir / "loudness_normalization.json"
        if not rep.exists():
            diffs.append(f"{label}/loudness_normalization.json: missing")
        else:
            actual = sha256_file(rep)
            if actual != pin_section["loudness_normalization_json_sha256"]:
                diffs.append(f"{label}/loudness_normalization.json: {actual} != pin")
    if "selection_json_sha256" in pin_section:
        # run 7 amitaro: 選定来歴（dosage・staged 入力 sha・被覆検査）も
        # pin 対象（DESIGN_S6_run7.md §2-3 — 選定が変わる = 教師データの
        # 実体が変わったということ）。
        rep = dataset_dir / "selection.json"
        if not rep.exists():
            diffs.append(f"{label}/selection.json: missing")
        else:
            actual = sha256_file(rep)
            if actual != pin_section["selection_json_sha256"]:
                diffs.append(f"{label}/selection.json: {actual} != pin")
    return diffs


def verify_assembly_against_run4_pins(
    assembly_manifest: dict, dataset_pins: dict
) -> List[str]:
    """assembly_manifest の教師/user セクションが dataset pins と一致する
    ことを照合する（assemble はバイト単位コピーなので、話者ディレクトリの
    実測記帳が pin と一致するはず）。照合ペアは pins のセクション構成が
    単一ソース: "d3" を持つ pins（run 5/6）は d3synth を、"amitaro" を持つ
    pins（run 7・DESIGN_S6_run7.md）は amitaro を照合する。"""
    diffs: List[str] = []
    pairs: List[Tuple[str, str]] = [("user", "user")]
    if "d3" in dataset_pins:
        pairs.append(("d3synth", "d3"))
    if "amitaro" in dataset_pins:
        pairs.append(("amitaro", "amitaro"))
    if len(pairs) == 1:
        # 教師セクションを欠く pins は照合対象の脱落（セルフレビュー #3:
        # user だけ照合して clean を返す fail-open を塞ぐ）。
        diffs.append(
            "dataset pins に教師セクション（'d3' or 'amitaro'）が無い — "
            "教師話者が未照合のまま通過するため fail-closed"
        )
    for speaker_name, pin_name in pairs:
        speaker = assembly_manifest["speakers"][speaker_name]
        pin = dataset_pins[pin_name]
        if speaker["transcriptions_csv_sha256"] != pin["transcriptions_csv_sha256"]:
            diffs.append(f"{speaker_name}: transcriptions_csv_sha256 != {pin_name} pin")
        if speaker["wav_sha256"] != pin["wav_sha256"]:
            diffs.append(f"{speaker_name}: wav_sha256 map != {pin_name} pin")
    user_pin = dataset_pins["user"]
    if "exclusions_json_sha256" in user_pin:
        if (
            assembly_manifest["speakers"]["user"].get("exclusions_json_sha256")
            != user_pin["exclusions_json_sha256"]
        ):
            diffs.append("user: exclusions_json_sha256 != pin")
    return diffs


def verify_spk_map(spk_map_path: Path, expected: Dict[str, int]) -> List[str]:
    """binarize 出力の `spk_map.json` を期待マップと**完全一致**で照合する
    （DESIGN_S6_run7.md §0-2: DiffSinger `build_spk_map` は `spk_id` 未指定の
    データセットへ**最小の空き番号を自動採番**する — 設定ミスで amitaro の
    明示 spk_id が落ちると、新教師が黙って id 3〔歴史上の合成教師〕へ埋まり、
    DiffSinger 側の検査（max < num_spk）は素通しになる。完全一致照合なら
    自動採番の混入・欠番の充填・話者名の取り違えを全て検出する）。
    戻り値は不一致の説明リスト（空 = 一致）。"""
    if not spk_map_path.exists():
        return [f"spk_map.json missing: {spk_map_path} (binarize 出力レイアウト異常)"]
    actual = json.loads(spk_map_path.read_text(encoding="utf-8"))
    diffs: List[str] = []
    if actual != expected:
        diffs.append(
            f"spk_map.json mismatch: actual={actual!r} != expected={expected!r}"
        )
        vacant = sorted(set(actual.values()) - set(expected.values()))
        if vacant:
            diffs.append(
                f"spk_map.json: unexpected spk_id value(s) {vacant} — 恒久欠番"
                "（d3synth=3 引退枠等）が自動採番で埋まった疑い（fail-closed）"
            )
    return diffs


def derive_phase_config(
    live_config: Dict[str, object],
    *,
    phase: str,
    finetune_ckpt_path: Optional[str] = None,
) -> Dict[str, object]:
    """live config（assemble 生成・無編集）から phase A/B の学習 config を
    導出する（モジュール docstring「学習 2 フェーズについて」参照）。

    - phase "a": scratch 0→5K（`finetune_enabled: false`・
      `max_updates: 5000`）
    - phase "b": finetune 機構再適用（`finetune_enabled: true`・
      `finetune_ckpt_path` = phase A の 5K checkpoint・`max_updates: 40000`）

    いずれも `TRAINING_FIELDS`（bf16-mixed / lr 0.0002 / clip 1.0）を付与
    する。live config 由来のキーはそれ以外一切変更しない。"""
    if phase not in ("a", "b"):
        raise ValueError(f"phase must be 'a' or 'b', got {phase!r}")
    derived = dict(live_config)
    for key, value in TRAINING_FIELDS.items():
        derived[key] = dict(value) if isinstance(value, dict) else value
    if phase == "a":
        derived["finetune_enabled"] = False
        derived["max_updates"] = PHASE_A_MAX_UPDATES
    else:
        if not finetune_ckpt_path:
            raise ValueError("phase 'b' requires finetune_ckpt_path (phase A 5K checkpoint)")
        derived["finetune_enabled"] = True
        derived["finetune_ckpt_path"] = finetune_ckpt_path
        derived["max_updates"] = PHASE_B_MAX_UPDATES
    return derived


_CKPT_RE = re.compile(r"^model_ckpt_steps_(\d+)\.ckpt$")


def parse_ckpt_step(filename: str) -> Optional[int]:
    m = _CKPT_RE.match(filename)
    return int(m.group(1)) if m else None


def find_milestone_ckpts(ckpt_dir: Path) -> Dict[int, Path]:
    """checkpoint ディレクトリから節目 step（5K/10K/20K/40K）の ckpt を
    列挙する。"""
    found: Dict[int, Path] = {}
    if not ckpt_dir.is_dir():
        return found
    for p in ckpt_dir.iterdir():
        step = parse_ckpt_step(p.name)
        if step in MILESTONE_STEPS:
            found[step] = p
    return found


def new_milestones(
    previously_seen: Sequence[int], current: Dict[int, Path]
) -> List[int]:
    return sorted(set(current) - set(previously_seen))


def remaining_seconds(start_monotonic: float, now_monotonic: float,
                      limit: int = WALL_CLOCK_LIMIT_SECONDS) -> float:
    return limit - (now_monotonic - start_monotonic)


def stable_milestone_candidates(
    seen: Sequence[int],
    prev_sizes: Dict[int, int],
    now_sizes: Dict[int, int],
) -> List[int]:
    """未処理 milestone のうち、直前ポーリングとファイルサイズが一致した
    （= 書き込みが完了したとみなせる）step だけを NaN スキャン候補として
    返す（review セルフレビュー #2: train.py の checkpoint 書き込みは
    原子的 rename を保証しないため、出現直後のファイルを即 torch.load すると
    truncated 読みで偽 NaN 判定→学習 kill になり得る。最低 1 ポーリング
    周期のサイズ安定を書き込み完了の近似として要求する）。"""
    return sorted(
        step for step, size in now_sizes.items()
        if step not in seen and size > 0 and prev_sizes.get(step) == size
    )


def milestone_ckpt_sizes(ckpt_dir: Path) -> Dict[int, int]:
    """`find_milestone_ckpts` の各 ckpt の現在サイズを実測する。"""
    return {
        step: path.stat().st_size
        for step, path in find_milestone_ckpts(ckpt_dir).items()
        if path.exists()
    }


# user_sources ランナウェイガード（review PR#270 セルフレビュー #1）: rclone
# 経路は user_sources/ 配下を無差別コピーするため、想定外に巨大／大量の
# フォルダを掴むと課金 Pod のディスク・帯域を浪費する。台帳原本は 17 本・
# 実測 ~2 MiB のため、桁違いの余裕（500 MiB / 200 files）を上限に置き、超過は
# hash 前に fail-closed する（正当な「〜 のコピー」余剰は通す・runaway だけ止める）。
USER_SOURCES_MAX_BYTES = 500 * 1024 * 1024
USER_SOURCES_MAX_FILES = 200


def _iter_files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def guard_user_sources_size(root: Path) -> Tuple[int, int]:
    """`root` 配下の総ファイル数・総バイト数を測り、ランナウェイ上限
    （`USER_SOURCES_MAX_*`）を超えていれば `StageFailure` で fail-closed する。
    戻り値は (file_count, total_bytes)。hash を回す前に呼ぶ（大量ファイルの
    全 hash 自体が浪費になるため）。"""
    files = _iter_files(root)
    total = sum(p.stat().st_size for p in files)
    if len(files) > USER_SOURCES_MAX_FILES or total > USER_SOURCES_MAX_BYTES:
        raise StageFailure(
            f"user_sources runaway guard: {len(files)} file(s) / {total} bytes "
            f"exceeds cap ({USER_SOURCES_MAX_FILES} files / {USER_SOURCES_MAX_BYTES} "
            "bytes) — 想定は台帳原本 17 本・~2 MiB。巨大／無関係フォルダを掴んで"
            "いないか user_sources/ を確認（fail-closed・hash 前に停止）"
        )
    return len(files), total


def plan_drive_id_fetch(listing_json: str) -> List[Tuple[str, str]]:
    """`rclone lsjson --files-only` の出力から `[(file_id, local_name), ...]` を
    決定論的に組み立てる（ID 昇順）。

    **なぜ ID 指定が要るか**（2026-08-18 実地で確定）: Google Drive は同一
    フォルダ内に**同じ表示名のファイルを複数持てる**（ID 管理のため）。録音
    ファイル名が重複していると `rclone copy`（名前ベース）はローカル FS の
    名前衝突を避けるため
    `NOTICE: <name>: Duplicate object found in source - ignoring` として
    2 本目以降を**黙って落とす** — run 5 三度目の起動は 17 本中 10 本しか
    Pod に届かず、7 本が「中身が一致しない」として fail-closed した。
    サーバ側 hash 一覧（`rclone hashsum`）では 17/17 見えるため、コピー経路を
    通さない検証では発見できない穴だった。

    ローカル名は衝突しないよう連番を前置する（照合は中身の sha256 で行うため
    名前自体に意味は無い — `index_files_by_sha256` 参照）。"""
    entries = json.loads(listing_json)
    missing_id = [e for e in entries if not e.get("ID")]
    if missing_id:
        raise StageFailure(
            f"lsjson entries without ID ({len(missing_id)} 件) — drive backend の "
            f"lsjson は ID を出すはずで、欠落は想定外のリモート/形式変更を示す "
            f"(fail-closed): {missing_id[:3]!r}"
        )
    plan: List[Tuple[str, str]] = []
    for i, entry in enumerate(sorted(entries, key=lambda e: e["ID"])):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", entry.get("Name", ""))[-60:] or "file"
        plan.append((entry["ID"], f"{i:03d}_{safe}"))
    return plan


def index_files_by_sha256(root: Path) -> Dict[str, List[Path]]:
    """`root` 配下（再帰）の全ファイルを `{sha256: [paths]}` で索引する。
    user 宅録原本の特定に使う（ファイル名非依存 — 台帳 `source_filename` は
    intake 正規化名で Drive 表示名と一致せず、Drive コピーは「〜 のコピー」を
    付けるため、名前照合は構造的に成立しない。中身 hash なら重複コピーにも
    頑健）。同一 sha の重複は全パスを保持する（余剰カウントを正確にするため —
    review PR#270 セルフレビュー #3。使う 1 本はソート順先頭で等価）。"""
    index: Dict[str, List[Path]] = {}
    for path in _iter_files(root):
        index.setdefault(sha256_file(path), []).append(path)
    return index


def match_user_sources(
    files_by_sha: Dict[str, List[Path]], ledger_entries: Sequence[dict]
) -> Tuple[Dict[str, Path], List[str], int]:
    """台帳エントリを sha256 で `files_by_sha` に突き合わせる（純関数）。
    戻り値は (card_id -> path, 欠落 diff リスト, 余剰ファイル数)。

    review PR#270 セルフレビュー #3/#4: 余剰は「distinct sha 数」ではなく
    **実ファイル数**で数える（重複コピーの過小報告を防ぐ）。欠落メッセージは
    「該当 sha のファイルが user_sources に無い＝未コピー or 別内容」である
    ことを明示する（sha-only 照合では『名前は合うが中身違う』が『欠落』側に
    出るため、診断の手掛かりを言葉で補う）。"""
    source_paths: Dict[str, Path] = {}
    diffs: List[str] = []
    matched_shas: set = set()
    for entry in ledger_entries:
        paths = files_by_sha.get(entry["source_sha256"])
        if not paths:
            diffs.append(
                f"user source not found by content: {entry['source_filename']} "
                f"(sha256 {entry['source_sha256'][:12]}…) — user_sources/ に未コピー、"
                "または該当ファイルの中身が台帳と異なる"
            )
            continue
        source_paths[entry["card_id"]] = paths[0]
        matched_shas.add(entry["source_sha256"])
    matched_file_count = sum(len(files_by_sha[s]) for s in matched_shas)
    total_files = sum(len(v) for v in files_by_sha.values())
    extras = total_files - matched_file_count
    return source_paths, diffs, extras


@dataclass(frozen=True)
class StagedSourceResolution:
    """staged 素材の照合結果（異常の**内容**を失わない構造化戻り値）。

    int の `extras` だけを返す設計では呼び出し側が処理を忘れうる（実際
    run 7 の初回修正で余剰の扱いが漏れ、外部レビューで指摘された）。
    異常は種類ごとに保持し、`problems()` が診断メッセージへ落とす。"""

    resolved: Dict[str, Path] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    unexpected: List[Path] = field(default_factory=list)
    duplicate_pin_sha: Dict[str, List[str]] = field(default_factory=dict)
    n_pinned: int = 0
    n_staged_files: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems()

    def problems(self) -> List[str]:
        """fail-closed すべき理由の説明リスト（空 = 完全一致）。

        契約（外部レビュー 2026-08-20 で明文化）: **pin の期待集合と、今回
        取得した実体集合が 1 対 1 で完全一致した場合にのみ通す**。
        「名前ではなく中身で特定する」ことと「集合検証を緩める」ことは
        別問題であり、sha 照合化しても旧実装の集合 fail-closed 性を保つ。"""
        diffs: List[str] = []
        if self.duplicate_pin_sha:
            diffs.append(
                "duplicate staged-source sha256 in pin: "
                + repr({sha[:12]: sorted(names)
                        for sha, names in sorted(self.duplicate_pin_sha.items())})
                + " — 同一実体から複数の意味付きファイル名を生成することに"
                  "なるため fail-closed（amitaro はファイル名 = 文番号が意味を"
                  "持つ。user_sources の『同一 sha は同一物』判断を無条件に"
                  "適用しない）"
            )
        if self.missing:
            unexpected_names = sorted(p.name for p in self.unexpected)
            diffs.append(
                f"staged sources not found by content: "
                f"{len(self.missing)}/{self.n_pinned} 件が未解決 "
                f"(missing={self.missing[:5]}…) — 実際に届いていた "
                f"{self.n_staged_files} 本のうち未照合 "
                f"{len(unexpected_names)} 本 (unmatched={unexpected_names[:5]}…)。"
                "未コピー、または中身が pin と異なる"
            )
        if self.unexpected and not self.missing:
            diffs.append(
                f"unexpected staged file(s): {len(self.unexpected)} 本が pin に"
                f"無い (extra={sorted(p.name for p in self.unexpected)[:5]}…) — "
                "取得集合が pin と一致しないため fail-closed"
            )
        return diffs


def place_staged_sources_by_sha256(
    files_by_sha: Dict[str, List[Path]], pinned_sha: Dict[str, str]
) -> StagedSourceResolution:
    """pin の `{期待ファイル名: sha256}` を `files_by_sha`（**今回の取得のみ**
    から作った索引）へ突き合わせ、`StagedSourceResolution` を返す純関数。

    **なぜ必要か**（run 7 初回起動の fail-closed で確定・2026-08-20）:
    `plan_drive_id_fetch` は Drive の同名重複に強くするため、ローカル名を
    `{i:03d}_{元名}` の連番前置にする（照合は中身の sha256 で行う前提の設計 —
    user_sources はまさにそれ）。ところが amitaro 素材は
    **`recitationNNN.wav` という名前自体が意味を持つ**（`convert_amitaro` が
    文番号昇順で走査する）ため、取得したままの連番名では
    「file set mismatch」で停止した。名前ベース取得へ戻すと Drive 同名重複の
    穴が復活するので、**ID 指定取得は維持したまま、取得後に中身 sha で
    期待名へ解決する**。

    ただし user_sources 由来の緩さ（余剰許容・同一 sha を同一物扱い）は
    そのまま持ち込まない（外部レビュー 2026-08-20）:
    - **余剰は fail-closed**（pin に無い実体が 1 本でもあれば集合不一致）
    - **pin 側 sha の重複も fail-closed**（1 実体から複数の意味名を作れて
      しまう。amitaro は名前が文番号という意味を担う）
    """
    sha_to_names: Dict[str, List[str]] = {}
    for name, sha in pinned_sha.items():
        sha_to_names.setdefault(sha, []).append(name)
    duplicate_pin_sha = {
        sha: names for sha, names in sha_to_names.items() if len(names) > 1
    }
    all_paths = [p for paths in files_by_sha.values() for p in paths]
    if duplicate_pin_sha:
        # 多重度が曖昧な pin では解決を試みない（部分解決を返して呼び出し側が
        # 使ってしまう余地を残さない）。
        return StagedSourceResolution(
            duplicate_pin_sha=duplicate_pin_sha,
            n_pinned=len(pinned_sha), n_staged_files=len(all_paths),
        )

    resolved: Dict[str, Path] = {}
    missing: List[str] = []
    matched_shas: set = set()
    for name in sorted(pinned_sha):
        sha = pinned_sha[name]
        paths = files_by_sha.get(sha)
        if not paths:
            missing.append(name)
            continue
        resolved[name] = paths[0]
        matched_shas.add(sha)
    unexpected = sorted(
        (p for sha, paths in files_by_sha.items() if sha not in matched_shas
         for p in paths),
        key=lambda p: p.name,
    )
    return StagedSourceResolution(
        resolved=resolved, missing=missing, unexpected=unexpected,
        n_pinned=len(pinned_sha), n_staged_files=len(all_paths),
    )


def materialize_staged_sources(
    src_dir: Path, pinned_sha: Dict[str, str], dest_dir: Path
) -> StagedSourceResolution:
    """`src_dir`（**今回の取得のみ**が入った clean staging = 連番前置名）を
    中身 sha で索引し、`pinned_sha` の**期待名で `dest_dir` へ配置**する。

    `StagedSourceResolution.problems()` が非空なら `PinMismatchError` で
    fail-closed し、**何も配置しない**（欠落・余剰・pin 側 sha 重複のいずれも
    通さない = 期待集合と取得集合の 1 対 1 完全一致が通過条件）。
    `dest_dir` が既存の場合は gate 4（`d3_render_out`）と同じ流儀で
    fail-closed する — 前走行の残骸を黙って消して素材来歴を曖昧にしない。
    pin キーがベアなファイル名でない場合も拒否する（`dest_dir` 外への
    書き出し防止）。"""
    for name in sorted(pinned_sha):
        if Path(name).name != name or name in ("", ".", ".."):
            raise StageFailure(
                f"staged pin key is not a bare filename: {name!r} "
                "(fail-closed — 配置先ディレクトリ外への書き出しを拒否)"
            )
    if dest_dir.exists():
        raise StageFailure(
            f"staged 配置先が既に存在する: {dest_dir} — 前走行の残骸を黙って"
            "上書きしない（fail-closed・gate 4 と同流儀）"
        )
    resolution = place_staged_sources_by_sha256(
        index_files_by_sha256(src_dir), pinned_sha
    )
    problems = resolution.problems()
    if problems:
        raise PinMismatchError(problems)
    dest_dir.mkdir(parents=True)
    for name in sorted(resolution.resolved):
        shutil.copy2(resolution.resolved[name], dest_dir / name)
    return resolution


def prepare_clean_staging_dir(path: Path) -> Path:
    """取得用 staging を**必ず空**にしてから返す（外部レビュー P1・2026-08-20）。

    `mkdir(exist_ok=True)` のままだと、同一 work-dir を再利用した際に前回 run
    の取得物が残り、`index_files_by_sha256` がそれを索引してしまう。すると
    **今回の取得が欠落していても古いローカル実体で補完でき**、
    「今回 Drive から取得した実体だけで pinned source set を再構成できた」
    という fail-closed 契約が破れる。"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def collect_salvage_artifacts(run5_raw: Path,
                              ds_repo: Path) -> List[Tuple[Path, str]]:
    """salvage 段で push すべき成果物を**その時点のディスク実態から**収集する
    （review セルフレビュー #4: 旧実装は学習 2 フェーズ完走後にのみ
    salvage_paths へ積んでいたため、途中失敗時に log/TB/phase config が
    一切 push されず「成功・失敗どちらでも退避」の契約に反していた。
    パスは決定論的に導出できるため、失敗地点に依らず存在するものを全部
    拾う）。戻り値は `(ローカルパス, Drive 側 dest)` の列（存在するもののみ・
    重複なし・順序決定論）。

    dest は 2026-08-19 外部レビュー P1 の namespace 規則: run 単位の pin 類は
    フォルダ直下（""）、phase 別成果物は `phase_a/` / `phase_b/` 配下の
    checkpoints / config / logs へ分離する（milestone push と同一規則 —
    run 5 実走の同名後勝ち上書き〔s4_record §5.6〕の根治で、手動 copyto
    保全を不要にする）。コマンドログは `cmdlogs/` へ。"""
    candidates: List[Tuple[Path, str]] = [
        (run5_raw / "assembly_manifest.json", ""),
        (run5_raw / "run4_config_datasets.yaml.normalized.yaml", ""),
        (run5_raw / "dict.txt", ""),
        (run5_raw / "run5_training_manifest.json", ""),
        (run5_raw / EXEC_MANIFEST_NAME, ""),
        (run5_raw / "run5_config_phase_a.yaml", "phase_a/config"),
        (run5_raw / "run5_config_phase_b.yaml", "phase_b/config"),
    ]
    for exp_name in (EXP_NAME_PHASE_A, EXP_NAME_PHASE_B):
        phase_dest = PHASE_REMOTE_DIRS[exp_name]
        ckpt_dir = ds_repo / "checkpoints" / exp_name
        for _step, ckpt in sorted(find_milestone_ckpts(ckpt_dir).items()):
            candidates.append((ckpt, f"{phase_dest}/checkpoints"))
        candidates += [(p, f"{phase_dest}/config")
                       for p in sorted(ckpt_dir.glob("config.yaml"))]
        # salvage 追加 (c)（DESIGN_S5 §2-3・s4_record §5.4 export 復旧記録）:
        # ONNX export の必須入力 3 種は学習時に exp dir へ生成される —
        # run 5 では未退避で、判定材料生成時に一次記録からの再作成を要した。
        for pattern in ("spk_map.json", "lang_map.json", "dictionary-*.txt"):
            candidates += [(p, f"{phase_dest}/config")
                           for p in sorted(ckpt_dir.glob(pattern))]
        for pattern in ("*.log", "**/events.out.tfevents.*"):
            candidates += [(p, f"{phase_dest}/logs")
                           for p in sorted(ckpt_dir.glob(pattern))]
    # コマンドログは**最後**に積む（review #4: 予算/wall-clock 枯渇時に
    # 先に押し出されるべきは checkpoint。ログ 40 本超が先頭を占めると
    # 本命の退避が間に合わない経路が生まれる）。
    log_dir = _LOG_DIR
    if log_dir is not None:
        candidates += [(p, "cmdlogs") for p in sorted(log_dir.glob("*.log"))]
    unique: List[Tuple[Path, str]] = []
    for item in candidates:
        if item[0].exists() and item not in unique:
            unique.append(item)
    return unique


def new_execution_manifest(*, pod_id: str, repo_commit: str,
                           container_image: Optional[str],
                           gpu: Optional[str]) -> Dict[str, object]:
    """機械可読な実行正本の初期形（2026-08-19 外部レビュー P2）。
    Markdown record（s4_record 等）の人間向け記帳とは別に、次回 run の
    preflight が前回成功走行と比較できる構造化データを 1 ファイルに残す。
    走行中に stage_status / 各 hash / salvage 会計が埋まり、salvage 段で
    Drive フォルダ直下へ push される。"""
    return {
        "schema_version": EXEC_MANIFEST_SCHEMA,
        "run_id": RUN_ID,
        "pod_id": pod_id,
        "repo_commit": repo_commit,
        "container_image": container_image,
        "gpu": gpu,
        "start_time": utc_now_iso(),
        "end_time": None,
        "stage_status": {},
        "environment_versions": {"python": sys.version.split()[0]},
        "material_hashes": {},
        "dataset_hashes": {},
        "checkpoint_hashes": {},
        "tensorboard_hashes": {},
        "artifact_count": None,
        "salvage_status": None,
        "self_stop_status": "pending",
        "failure_history": [],
    }


def summarize_material_hashes(
    materials: Dict[str, Dict[str, object]]
) -> Dict[str, Dict[str, object]]:
    """素材 pin 表から sha256 系キーだけを抜いた要約（manifest の
    material_hashes 欄）。pin 表そのものは repo が正本なので複製しない。"""
    return {
        name: {k: v for k, v in entry.items() if k.endswith("sha256")}
        for name, entry in materials.items()
    }


# manifest 比較の対象キー（preflight での前回比較）。stage_status や費用系は
# 走行毎に変わって当然なので比較しない — 環境・入力の同一性だけを見る。
EXEC_MANIFEST_COMPARE_KEYS = (
    "schema_version", "repo_commit", "container_image",
    "material_hashes", "environment_versions",
)


def compare_execution_manifests(previous: Dict[str, object],
                                current: Dict[str, object]) -> List[str]:
    """前回 manifest と今回の初期 manifest の差分キーを返す（空 = 一致）。
    fail-closed にはしない — 意図した差分（pin 更新・コミット前進）が正常系に
    普通に存在するため、比較結果は heartbeat の info として記録し、判断は
    record / 人間側に残す。

    environment_versions は**両方に存在するキーのみ**比較する（セルフ
    レビュー #1: 前回側は走行完了時の最終形〔render/binarize の実測版が
    追記済み〕、今回側は preflight 時点の初期形〔python のみ〕なので、
    全体の等値比較だと構造的に常に差分になり、比較機能が死ぬ）。"""
    diffs: List[str] = []
    for key in EXEC_MANIFEST_COMPARE_KEYS:
        prev_val = previous.get(key)
        curr_val = current.get(key)
        if key == "environment_versions" and isinstance(prev_val, dict) \
                and isinstance(curr_val, dict):
            shared = set(prev_val) & set(curr_val)
            if any(prev_val[k] != curr_val[k] for k in shared):
                diffs.append(key)
            continue
        if prev_val != curr_val:
            diffs.append(key)
    return diffs


# import 時に既定プロファイルを適用し、モジュール定数の正本を
# RUN_PROFILES["run5"] に一本化する（セルフレビュー #4: 二重定義 drift 封じ。
# main() は RUN_PROFILE env で上書きする）。
apply_run_profile("run5")


def probe_gpu_name() -> Optional[str]:
    """nvidia-smi から GPU 名を取る（無ければ None — manifest 用の観測値で
    あってゲートではない）。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip().splitlines()
    return name[0].strip() if name else None


def probe_repo_commit(repo_root: Path) -> str:
    """実行中リポジトリの HEAD SHA（取れなければ RUN5_PIN_COMMIT env に
    フォールバック。両方欠けたら "unknown" — manifest 用の観測値）。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return os.environ.get("RUN5_PIN_COMMIT", "unknown")


def write_execution_manifest(manifest: Dict[str, object], run5_raw: Path) -> Path:
    run5_raw.mkdir(parents=True, exist_ok=True)
    path = run5_raw / EXEC_MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def build_stage_plan() -> Tuple[str, ...]:
    return STAGE_PLAN


def self_stop_command(pod_id: str) -> List[str]:
    """自動停止コマンド（DESIGN_S4 §3.3 裁定 (c): stop で止め、ディスクは
    保険として残置する — remove はしない）。"""
    return ["runpodctl", "stop", "pod", pod_id]


class Heartbeat:
    """ステージ毎のマーカー + exit code ファイルを heartbeat dir に書き、
    都度 Drive へ push する（DESIGN_S4 §3.1: 「進捗の正はステージ毎の
    マーカー…を Drive へ heartbeat push したもの」— 報告文でなく成果物で
    完了判定する）。push は runner（テストではフェイク）経由。"""

    def __init__(self, heartbeat_dir: Path, pusher: Callable[..., None],
                 record: Optional[Dict[str, object]] = None) -> None:
        self.heartbeat_dir = Path(heartbeat_dir)
        self.heartbeat_dir.mkdir(parents=True, exist_ok=True)
        self._pusher = pusher
        # 実行 manifest の stage_status へも同時記帳する（渡された dict を
        # in-place 更新。detail は traceback 等で肥大しうるため切り詰める）。
        self._record = record

    def mark(self, stage: str, status: str, detail: str = "") -> Path:
        if self._record is not None:
            self._record[stage] = {
                "status": status,
                "utc": utc_now_iso(),
                "detail": tail_text(detail, 300) if detail else "",
            }
        marker = self.heartbeat_dir / f"{stage}.status.json"
        marker.write_text(
            json.dumps(
                {
                    "stage": stage,
                    "status": status,
                    "detail": detail,
                    "utc": utc_now_iso(),
                },
                ensure_ascii=False, indent=2, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        self._pusher(marker)
        return marker


# ---------------------------------------------------------------------------
# 実行系（subprocess）。ここから下は本開発環境では実測できない（モジュール
# docstring 冒頭の正直会計）— ロジックは薄く保ち、判定はすべて上の純関数へ
# 寄せる。
# ---------------------------------------------------------------------------


# `_run` のコマンド出力ログ置き場（`main` が起動直後に `set_log_dir` で設定する）。
# None のままなら従来どおり出力を素通しする（--plan 等、ログ不要の経路）。
_LOG_DIR: Optional[Path] = None


def set_log_dir(path: Path) -> Path:
    """`_run` の出力ログ置き場を設定して返す。"""
    global _LOG_DIR
    _LOG_DIR = Path(path)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def sanitize_label(label: str) -> str:
    """ステージラベルをログのファイル名へ落とす（`datasets/convert-d3` →
    `datasets_convert-d3`）。空なら "run"。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", label) or "run"


def tail_text(text: str, max_chars: int = 1500) -> str:
    """末尾 `max_chars` 文字を返す（切り詰めたら先頭に印を付ける）。"""
    if len(text) <= max_chars:
        return text
    return "…(先頭を省略)…\n" + text[-max_chars:]


def read_tail(path: Path, max_bytes: int = 8192, max_chars: int = 1500) -> str:
    """`path` の末尾だけを読んで文字列で返す（全文を読み込まない — review #2:
    binarize / render 等は progress 出力でログが巨大になり得る）。読めなければ
    空文字（診断の付随処理でさらに例外を起こさない）。"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read()
    except OSError:
        return ""
    return tail_text(raw.decode("utf-8", errors="replace"), max_chars)


def _run(argv: Sequence[str], *, cwd: Optional[Path] = None,
         env: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
         label: str = "") -> None:
    """外部コマンドを実行し、失敗したら `StageFailure` を送出する。

    出力は `_LOG_DIR/<label>.log` へ落とし、**失敗時はその末尾をエラーへ
    同梱する**（2026-08-18 実地: gdown が exit 1 で落ちた際、旧実装は
    コマンド行と終了コードしか残さず、原因〔Drive の Quota exceeded〕の
    特定に Pod 外からの再現調査を要した。無人 Pod は salvage → self-stop まで
    進んで人が入れないため、証跡はその場で残すしかない）。ログ本体は
    salvage で Drive へ退避される。"""
    printable = " ".join(shlex.quote(str(a)) for a in argv)
    print(f"| run5_bootstrap: [{label}] {printable}", flush=True)
    if _LOG_DIR is None:
        result = subprocess.run(
            [str(a) for a in argv], cwd=str(cwd) if cwd else None,
            env=env, timeout=timeout,
        )
        captured = ""
    else:
        log_path = _LOG_DIR / f"{sanitize_label(label)}.log"
        with open(log_path, "ab") as f:
            f.write(f"\n=== {printable} ===\n".encode())
            f.flush()
            result = subprocess.run(
                [str(a) for a in argv], cwd=str(cwd) if cwd else None,
                env=env, timeout=timeout, stdout=f, stderr=subprocess.STDOUT,
            )
        captured = None  # 失敗時のみ末尾を読む（review #2: 成功時も全文を
        # メモリへ載せていた。progress bar 系のステージはログが巨大になる）
    if result.returncode != 0:
        detail = f"[{label}] exit {result.returncode}: {printable}"
        if _LOG_DIR is not None:
            captured = read_tail(log_path)
        if captured:
            detail += f"\n--- command output (tail) ---\n{captured}"
            # review #3（部分採用）: 出力をファイルへ落とすと Pod コンソールから
            # 消えるため、失敗時の末尾だけは stdout にも流す（RunPod の
            # コンテナログから人が読める経路を残す）。ライブ追従は heartbeat が
            # 正であるという設計（DESIGN_S4 §3.1）どおり犠牲にする — ログ本体は
            # ディスクに残り、salvage で Drive へ退避される。
            print(f"| run5_bootstrap: [{label}] FAILED — output tail:\n{captured}",
                  file=sys.stderr, flush=True)
        raise StageFailure(detail)


def _run_capture(argv: Sequence[str], *, timeout: Optional[float] = None,
                 label: str = "") -> str:
    """`_run` の「stdout を parse に使いたい」変種。stdout を返しつつ、
    stdout+stderr を `_LOG_DIR/<label>.log` にも書き、失敗時は末尾を
    StageFailure に同梱する（review PR#274 #2: lsjson を素の subprocess.run
    で呼ぶと、gdown 事件で塞いだ「証跡ゼロの停止」を新しいコードで再現して
    しまう — 出力を要する呼び出しはこの関数を通す）。"""
    printable = " ".join(shlex.quote(str(a)) for a in argv)
    print(f"| run5_bootstrap: [{label}] {printable}", flush=True)
    result = subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, timeout=timeout,
    )
    if _LOG_DIR is not None:
        log_path = _LOG_DIR / f"{sanitize_label(label)}.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {printable} ===\n")
            f.write(result.stdout)
            if result.stderr:
                f.write("\n--- stderr ---\n" + result.stderr)
    if result.returncode != 0:
        tail = tail_text((result.stdout + "\n" + result.stderr).strip())
        detail = f"[{label}] exit {result.returncode}: {printable}"
        if tail:
            detail += f"\n--- command output (tail) ---\n{tail}"
            print(f"| run5_bootstrap: [{label}] FAILED — output tail:\n{tail}",
                  file=sys.stderr, flush=True)
        raise StageFailure(detail)
    return result.stdout


def _remote_path(dest: str = "") -> str:
    """成果物フォルダ起点の相対リモートパス（REMOTE_PREFIX と dest の合成 —
    push/pull の両方がこの 1 箇所を通る。セルフレビュー #5）。"""
    return "/".join(part for part in (REMOTE_PREFIX, dest) if part)


def _rclone_argv(rclone_conf: Path, drive_folder_id: str, path: Path,
                 dest: str = "") -> List[str]:
    """`dest` は成果物フォルダ内のサブディレクトリ（例 "phase_a/checkpoints"）。
    空文字 = フォルダ直下（heartbeat / run 単位の pin 類）。phase 別成果物は
    必ず `PHASE_REMOTE_DIRS` 由来の dest を渡す — 2026-08-19 外部レビュー P1
    （同名後勝ち上書きの根治。rclone は存在しないリモートディレクトリを
    copy 時に自動作成する）。"""
    return [
        "rclone", "--config", str(rclone_conf), "copy", str(path),
        f"run5drive:{_remote_path(dest)}", "--drive-root-folder-id", drive_folder_id,
    ]


def _rclone_pusher(rclone_conf: Path,
                   drive_folder_id: str) -> Callable[..., None]:
    """heartbeat / 走行中 milestone 用の**非致命** pusher。失敗は進捗可視性の
    劣化であって成果物の毀損ではないため run を落とさない（最終的な成果物
    保全は salvage 段の `_rclone_push_strict` が担い、そちらの失敗は
    exit code / heartbeat status に表面化する — review セルフレビュー #5）。"""
    def push(path: Path, dest: str = "") -> None:
        try:
            subprocess.run(
                _rclone_argv(rclone_conf, drive_folder_id, path, dest),
                check=True, timeout=1800,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"| run5_bootstrap: heartbeat push failed (non-fatal here): {exc}",
                  flush=True)
    return push


def _rclone_push_strict(rclone_conf: Path, drive_folder_id: str, path: Path,
                        dest: str = "") -> bool:
    """salvage 段用の**致命側** push（review セルフレビュー #5: 旧実装は
    salvage も swallow-all pusher を使い回しており、最終 checkpoint の push
    失敗が exit code にも heartbeat にも現れないまま self-stop していた）。
    成否を返す（タイムアウトは大型 checkpoint を考慮して 1 時間）。"""
    try:
        subprocess.run(
            _rclone_argv(rclone_conf, drive_folder_id, path, dest),
            check=True, timeout=3600,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"| run5_bootstrap: SALVAGE push FAILED for {path}: {exc}",
              file=sys.stderr, flush=True)
        return False


def _scan_ckpt_for_nonfinite(ckpt: Path) -> int:
    """checkpoint の state_dict を別プロセスで走査し、0 = 全有限 /
    1 = 非有限値あり（NaN 検知）/ 2 = torch.load 失敗（書き込み途中・破損 —
    NaN 判定ではない）を返す（review セルフレビュー #2: 「読めない」を
    「NaN」と誤断して学習を kill しない）。"""
    code = (
        "import sys, torch\n"
        f"path = {str(ckpt)!r}\n"
        "try:\n"
        "    sd = torch.load(path, map_location='cpu')['state_dict']\n"
        "except Exception as exc:\n"
        "    print('ckpt load failed (not a NaN verdict):', exc)\n"
        "    sys.exit(2)\n"
        "bad = [k for k, v in sd.items() if not torch.isfinite(v).all()]\n"
        "print(('NaN scan OK' if not bad else 'NaN scan NG:'), bad[:5])\n"
        "sys.exit(1 if bad else 0)\n"
    )
    return subprocess.run([sys.executable, "-c", code]).returncode


def detect_archive_format(path: Path) -> Optional[str]:
    """先頭バイト（+ tar の ustar マジック）でアーカイブ形式を判定する
    （"zip" / "tar" / None）。review（run 5 実装 PR セルフレビュー #1）:
    旧実装はファイル名拡張子だけで分岐しており、拡張子の無い固定名
    `user_sources_archive`（Drive 直リンクは URL からも拡張子を取れない）が
    無条件で StageFailure になり materials 段が構造的に完走不能だった。
    内容そのもので判定する（tar は gzip/xz 圧縮込みで tarfile "r:*" が
    透過的に開けるため 1 種に畳む）。"""
    with open(path, "rb") as f:
        head = f.read(6)
        f.seek(257)
        ustar = f.read(5)
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b" or head[:6] == b"\xfd7zXZ\x00" or ustar == b"ustar":
        return "tar"
    return None


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    fmt = detect_archive_format(archive)
    if fmt == "zip":
        # review セルフレビュー #7（実 zip で cp437 化けを実測確認）: 波音リツ
        # voicebank zip は UTF-8 フラグ無し・cp932 格納のため、素の extractall
        # ではトップディレクトリ名『波音リツ強連続音Ver1.5.1』が化けて後段の
        # glob が壊れる。metadata_encoding は「UTF-8 フラグの無いエントリ名」
        # にのみ適用される（フラグ付きは従来どおり UTF-8）ので、cp932 指定は
        # ASCII 名のみの他 zip にも無害（Python 3.11+ — Pod イメージは 3.11。
        # それ未満では TypeError で fail-closed）。
        with zipfile.ZipFile(archive, metadata_encoding="cp932") as z:
            z.extractall(dest)
    elif fmt == "tar":
        with tarfile.open(archive, "r:*") as t:
            t.extractall(dest)
    else:
        raise StageFailure(
            f"unsupported archive format (magic-byte sniff failed): {archive}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true",
                        help="ステージ計画を印字して終了する（実行しない）")
    parser.add_argument("--work-dir", type=Path,
                        default=Path(os.environ.get("RUN5_WORK_DIR", "~/s4work")).expanduser())
    parser.add_argument("--skip-self-stop", action="store_true",
                        help="ローカルデバッグ用: 最後の runpodctl stop を呼ばない")
    args = parser.parse_args(argv)

    if args.plan:
        for i, stage in enumerate(build_stage_plan(), 1):
            print(f"{i:2d}. {stage}")
        return 0

    profile_name = os.environ.get("RUN_PROFILE", "run5")
    apply_run_profile(profile_name)
    print(f"| run5_bootstrap: RUN_PROFILE={profile_name} "
          f"(run_id={RUN_ID}, dataset_pins={DATASET_PINS_PATH.name}, "
          f"remote_prefix={REMOTE_PREFIX!r})", flush=True)


    start = time.monotonic()
    work: Path = args.work_dir
    work.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    heartbeat: Optional[Heartbeat] = None
    # salvage 段が失敗地点に依存せず成果物を拾えるよう、決定論的な出力パスは
    # 冒頭で確定させておく（review セルフレビュー #4）。
    out = work / "s4_data"
    run5_raw = out / "run5_raw"
    ds_repo = work / "DiffSinger"

    try:
        # --- stage 1: preflight ---------------------------------------------
        # review #5: mkdir 系は必ず try の内側で行う（finally の self-stop を
        # 飛ばすと Pod が課金され続ける — セルフレビュー #3 で塞いだ露出）。
        set_log_dir(work / "cmdlogs")
        missing = check_required_env(dict(os.environ))
        if missing:
            print(f"error: missing required env var(s): {missing}", file=sys.stderr)
            return 1  # finally 節が self-stop を担う（review セルフレビュー #3）
        try:
            materials = load_material_pins()
            rclone_conf = work / "rclone.conf"
            rclone_conf.write_bytes(
                base64.b64decode(os.environ["RUN5_RCLONE_CONF_B64"], validate=True)
            )
            dataset_pins = json.loads(DATASET_PINS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            # review セルフレビュー #3: 旧実装では rclone conf の base64 破損や
            # dataset_pins の読み込み失敗が try の外にあり、self-stop へ到達
            # しないまま例外で落ちて Pod が課金され続けた。ここで捕捉して
            # finally の self-stop へ必ず流す（まだ何も生成していないため
            # salvage は不要）。
            print(f"error: preflight failed: {exc}", file=sys.stderr)
            return 1
        drive_folder_id = os.environ["RUN5_DRIVE_FOLDER_ID"]
        pusher = _rclone_pusher(rclone_conf, drive_folder_id)
        # 機械可読な実行正本（外部レビュー P2）。stage_status は Heartbeat が
        # 同時記帳する。
        exec_manifest = new_execution_manifest(
            pod_id=os.environ.get("RUNPOD_POD_ID", ""),
            repo_commit=probe_repo_commit(REPO_ROOT),
            container_image=os.environ.get("RUNPOD_POD_IMAGE")
            or os.environ.get("RUNPOD_IMAGE_NAME"),
            gpu=probe_gpu_name(),
        )
        exec_manifest["material_hashes"] = summarize_material_hashes(materials)
        heartbeat = Heartbeat(work / "heartbeat", pusher,
                              record=exec_manifest["stage_status"])
        # review #1: PJS 取得（backend copyid）と salvage が rclone 依存に
        # なったため、**preflight で binary の実在・版・認証・フォルダ書き込みを
        # まとめて実証する**（materials 段まで持ち越さない）。heartbeat の
        # 通常 push は非致命なので、ここだけは strict push で確かめる。
        _run(["rclone", "--config", rclone_conf, "version"],
             label="preflight/rclone-version")
        preflight_marker = heartbeat.mark("preflight", "ok")
        if not _rclone_push_strict(rclone_conf, drive_folder_id, preflight_marker):
            raise StageFailure(
                "preflight: rclone push to the artifacts folder failed — "
                "トークン/フォルダ ID/権限のいずれかが無効。監視も退避も"
                "成立しないため起動を中止する（fail-closed）"
            )
        # 前回走行の manifest と比較（外部レビュー P2: 「次回 run の preflight
        # で前回成功 manifest と比較可能にする」）。前回が無い・読めないは
        # 正常系（初回）。差分は info として記帳し fail-closed にはしない —
        # 意図した差分（pin 更新・コミット前進）が普通に存在する。
        prev_manifest_path = work / "previous_run_execution_manifest.json"
        # 探索順はプロファイルの prev_manifest_prefixes（自 run の resume →
        # 前 run の置き場。run 7 は run6/ のみで直下 run 5 へは落ちない —
        # DESIGN_S6 §4 の比較相手契約・セルフレビュー #2）。
        _prefix_candidates = [
            f"{pfx}/{EXEC_MANIFEST_NAME}" if pfx else EXEC_MANIFEST_NAME
            for pfx in PREV_MANIFEST_PREFIXES
        ]
        for candidate in dict.fromkeys(_prefix_candidates):
            try:
                subprocess.run(
                    ["rclone", "--config", str(rclone_conf), "copyto",
                     f"run5drive:{candidate}", str(prev_manifest_path),
                     "--drive-root-folder-id", drive_folder_id],
                    timeout=300,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                # 情報目的の取得は run を落とさない（TimeoutExpired だけが
                # 素通りして preflight 全体を殺していた既知経路の再発防止）。
                print(f"| run5_bootstrap: previous manifest fetch failed "
                      f"(non-fatal): {exc}", flush=True)
            if prev_manifest_path.exists():
                break
        if prev_manifest_path.exists():
            try:
                prev_manifest = json.loads(
                    prev_manifest_path.read_text(encoding="utf-8"))
                diff_keys = compare_execution_manifests(prev_manifest, exec_manifest)
                heartbeat.mark(
                    "preflight_manifest_diff", "info",
                    detail=("match with previous run" if not diff_keys
                            else "differs from previous run: " + ", ".join(diff_keys)),
                )
            except (json.JSONDecodeError, OSError) as exc:
                heartbeat.mark("preflight_manifest_diff", "info",
                               detail=f"previous manifest unreadable: {exc}")
        else:
            heartbeat.mark("preflight_manifest_diff", "info",
                           detail="no previous manifest (first instrumented run)")

        try:
            # --- stage 2: gates（runbook §2.2） -----------------------------
            _run([sys.executable, "-m", "pip", "install", "--no-cache-dir",
                  *NUMERIC_STACK_PIN, *ANALYSIS_STACK_PIN],
                 label="gates/pin-install")
            # 実行時解決された全依存の実測 freeze を証跡化（外部レビュー P3:
            # lock + runtime gate の二重保証の材料。次 run の lock 生成元）。
            _run([sys.executable, "-m", "pip", "freeze"], label="gates/pip-freeze")
            exec_manifest["environment_versions"]["render_numeric_stack_pin"] = (
                list(NUMERIC_STACK_PIN))
            _run([sys.executable, "-c", GATE1_SNIPPET], label="gates/gate1")
            gate_env = dict(os.environ)
            gate_env["NPY_DISABLE_CPU_FEATURES"] = "X86_V4"
            _run([sys.executable, "-c", GATE2_SNIPPET], env=gate_env, label="gates/gate2")
            os.environ["NPY_DISABLE_CPU_FEATURES"] = "X86_V4"  # 以降の全 render 工程へ
            d3_render_out = work / "d3_render_out"
            if d3_render_out.exists():
                raise StageFailure(
                    f"gate 4 (cache provenance): {d3_render_out} already exists — "
                    "本ゲート 1–3 を通過済みの現在の環境で生成した確証が無い cache は"
                    "使わない（新規 Pod では常に新規 out-dir で render する）"
                )
            heartbeat.mark("gates", "ok")

            # --- stage 3: materials ----------------------------------------
            dl = work / "materials"
            dl.mkdir(exist_ok=True)
            ritsu_zip = dl / "r73_strong_ren0151.zip"
            _run(["curl", "-L", "--fail", "-o", ritsu_zip,
                  materials["ritsu_voicebank_zip"]["url"]], label="materials/ritsu-zip")
            verify_file_sha256(ritsu_zip, materials["ritsu_voicebank_zip"]["sha256"],
                               "ritsu_voicebank_zip")

            # PJS corpus は公開 Drive ファイル。匿名 DL は Google 側の per-file
            # 上限（"Quota exceeded"）に達すると HTML の警告/拒否ページを返し、
            # gdown は exit 1 で落ちる（2026-08-18 実地: run 5 二度目の起動が
            # これで停止。1 度目は上限到達前で成功していた）。**認証済み Drive
            # API 経由なら同一バイト（sha256 一致を実測）を取得できる**ため、
            # 既に注入済みの rclone トークンで `backend copyid`（ファイル ID
            # 直指定）を使う。gdown 依存はこれで撤去。
            pjs_zip = dl / "PJS_corpus_ver1.1.zip"
            _run(["rclone", "--config", rclone_conf, "backend", "copyid",
                  "run5drive:", materials["pjs_corpus_zip"]["drive_file_id"], pjs_zip],
                 label="materials/pjs-zip")
            verify_file_sha256(pjs_zip, materials["pjs_corpus_zip"]["sha256"],
                               "pjs_corpus_zip")

            namine_zip = dl / "NamineRitsu_DiffSinger.zip"
            _run(["curl", "-L", "--fail", "-o", namine_zip,
                  materials["namine_ritsu_diffsinger_zip"]["url"]],
                 label="materials/namine-zip")
            verify_file_sha256(namine_zip, materials["namine_ritsu_diffsinger_zip"]["sha256"],
                               "namine_ritsu_diffsinger_zip")

            ffmpeg_tar = dl / "ffmpeg_static.tar.xz"
            _run(["curl", "-L", "--fail", "-o", ffmpeg_tar,
                  materials["ffmpeg_static"]["url"]], label="materials/ffmpeg-static")
            verify_file_sha256(ffmpeg_tar, materials["ffmpeg_static"]["sha256"],
                               "ffmpeg_static")

            # 学習側 vocoder（pc_nsf_hifigan zip + model.ckpt。run 4 実績 pin —
            # pins 表 `vocoder_pc_nsf_hifigan` の provenance 欄参照）。zip と
            # 内容物 model.ckpt の両方を照合し、配置は binarize 段（DiffSinger
            # clone 後）で run 4 実績の checkpoints/ 直下パスへ行う。
            vocoder_pin = materials["vocoder_pc_nsf_hifigan"]
            vocoder_zip = dl / "pc_nsf_hifigan.zip"
            _run(["curl", "-L", "--fail", "-o", vocoder_zip, vocoder_pin["url"]],
                 label="materials/vocoder")
            verify_file_sha256(vocoder_zip, vocoder_pin["sha256"],
                               "vocoder_pc_nsf_hifigan(zip)")
            vocoder_extract_dir = work / "vocoder_extracted"
            _extract_archive(vocoder_zip, vocoder_extract_dir)
            vocoder_ckpts = sorted(vocoder_extract_dir.glob("**/model.ckpt"))
            if len(vocoder_ckpts) != 1:
                raise StageFailure(
                    "vocoder zip must contain exactly one model.ckpt, "
                    f"found {len(vocoder_ckpts)}"
                )
            verify_file_sha256(vocoder_ckpts[0], vocoder_pin["model_ckpt_sha256"],
                               "vocoder_pc_nsf_hifigan(model.ckpt)")
            vocoder_dir = vocoder_ckpts[0].parent
            # review セルフレビュー #6: 配置先ディレクトリ名は zip 内部
            # レイアウト任せにせず pin（`placement_dirname`）と照合する。
            # 一次ソース照合（DiffSinger e2307b1 configs/acoustic.yaml:15）で
            # `vocoder_ckpt: checkpoints/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02/model.ckpt`
            # が **base config の既定値そのもの**と確認済み — この名前から
            # ずれると生成 config（vocoder_ckpt キーを持たず既定値に依存）が
            # vocoder を見つけられず、学習数時間後ではなくここで止める。
            if vocoder_dir.name != vocoder_pin["placement_dirname"]:
                raise StageFailure(
                    f"vocoder zip layout mismatch: model.ckpt sits under "
                    f"{vocoder_dir.name!r}, pinned placement_dirname is "
                    f"{vocoder_pin['placement_dirname']!r} (fail-closed)"
                )

            # user 宅録原本 17 本の取得（2026-08-18 User 裁定・案 A）:
            # 既定は成果物フォルダ内 `user_sources/` サブフォルダから、退避と
            # 同じ folder 限定 rclone トークンで取得する（追加の共有設定・
            # 直リンク発行が不要 — 原本をリンク公開しない）。
            # RUN5_USER_SOURCES_URL 指定時のみ直リンク + アーカイブ経路。
            user_src_dir = work / "user_sources"
            user_sources_url = os.environ.get("RUN5_USER_SOURCES_URL", "")
            if user_sources_url:
                user_archive = dl / "user_sources_archive"
                _run(["curl", "-L", "--fail", "-o", user_archive, user_sources_url],
                     label="materials/user-sources-archive")
                _extract_archive(user_archive, user_src_dir)
            else:
                # 名前ベースの `rclone copy` は Drive の同名重複を黙って落とす
                # （§ `plan_drive_id_fetch` docstring）。lsjson で ID を列挙し、
                # **1 本ずつ ID 指定で**取得する。
                # --recursive: 旧経路（rclone copy）は再帰コピーだったため、
                # サブフォルダ整理された原本も従来どおり拾う（review PR#274 #1。
                # 取得は ID 指定なのでローカル名はフラットのままで衝突しない）。
                listing = _run_capture(
                    ["rclone", "--config", rclone_conf, "lsjson", "--recursive",
                     "--files-only", "run5drive:user_sources",
                     "--drive-root-folder-id", drive_folder_id],
                    timeout=300, label="materials/user-sources-lsjson",
                )
                fetch_plan = plan_drive_id_fetch(listing)
                print(f"| run5_bootstrap: user_sources = {len(fetch_plan)} file(s) "
                      "by Drive ID (名前重複に非依存)", flush=True)
                user_src_dir.mkdir(parents=True, exist_ok=True)
                # 1 本ずつの呼び出しは意図的（review PR#274 #4 の裁定）:
                # copyid は複数 ID/path 対を 1 回で受けられるが、cmdlog 上の
                # 失敗帰属をファイル単位にする方を取る（17 本 × 数 MB では
                # プロセス起動オーバーヘッドは課金上無視できる）。timeout は
                # 1 本のハングで finally の self-stop まで到達不能になるのを
                # 防ぐ（review PR#274 #3 — TimeoutExpired は上位の except で
                # salvage → self-stop に流れる）。
                for file_id, local_name in fetch_plan:
                    _run(["rclone", "--config", rclone_conf, "backend", "copyid",
                          "run5drive:", file_id, user_src_dir / local_name],
                         timeout=600, label="materials/user-sources-rclone")

            # 展開 + ffmpeg PATH 先頭化 + libavformat 60.16.100 実測
            _extract_archive(ritsu_zip, work / "ritsu_extracted")
            _extract_archive(pjs_zip, work / "pjs_extracted")
            _extract_archive(namine_zip, work / "ritsu_diffsinger_extracted")
            _extract_archive(ffmpeg_tar, work / "ffmpeg_extracted")
            ffmpeg_bins = sorted((work / "ffmpeg_extracted").glob("**/bin/ffmpeg"))
            if not ffmpeg_bins:
                raise StageFailure("ffmpeg static tarball did not contain bin/ffmpeg")
            # tarball sha に加えて展開後バイナリ実体も pin 照合する（pins 表
            # `ffmpeg_bin_sha256` — 展開時破損・想定外レイアウトの検出）。
            verify_file_sha256(ffmpeg_bins[0],
                               materials["ffmpeg_static"]["ffmpeg_bin_sha256"],
                               "ffmpeg_static(bin/ffmpeg)")
            os.environ["PATH"] = f"{ffmpeg_bins[0].parent}:{os.environ['PATH']}"
            version_out = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, check=True
            ).stdout
            actual_libavformat = parse_libavformat_version(version_out)
            if actual_libavformat != FFMPEG_LIBAVFORMAT_PIN:
                expected = ".".join(str(x) for x in FFMPEG_LIBAVFORMAT_PIN)
                raise StageFailure(
                    f"ffmpeg static build does not report libavformat {expected} "
                    f"(parsed={actual_libavformat}, 環境契約 1・s3_record 2026-08-17)"
                    f"\n--- ffmpeg -version (抜粋) ---\n"
                    f"{summarize_ffmpeg_version(version_out)}"
                )

            # user 原本 17/17 照合（source_sha256・台帳は改変しない。取得は
            # 上の分岐で user_src_dir へ済んでいる — 欠落・取り違えはここで
            # fail-closed になる）。
            # ファイル特定は **sha256（中身）で行い、ファイル名には依存しない**
            # （2026-08-18 是正）: 台帳の `source_filename` は intake 時の正規化名
            # （`UC-001_80716cf0.mp3` 等）だが、Drive 上の原本は録音時の表示名
            # （日本語日付名）のままで、さらに Drive のコピー操作は「〜 のコピー」
            # を付けるため、名前照合は構造的に成立しない。中身の hash が台帳と
            # 一致するファイルを探す方式なら、リネーム不要・重複コピーにも
            # 頑健（同一 sha の重複は同内容なのでどれを使っても等価）。
            ledger = json.loads(USER_DONOR_LEDGER_PATH.read_text(encoding="utf-8"))
            ledger_entries = ledger["entries"]
            guard_user_sources_size(user_src_dir)  # hash 前にランナウェイ停止
            files_by_sha = index_files_by_sha256(user_src_dir)
            source_paths, diffs, extras = match_user_sources(files_by_sha, ledger_entries)
            if diffs:
                raise PinMismatchError(diffs)
            n = len(ledger_entries)
            print(f"| run5_bootstrap: user sources matched {len(source_paths)}/{n} "
                  f"by sha256 ({extras} extra file(s) ignored)", flush=True)
            heartbeat.mark("materials", "ok")

            # --- stage 4: datasets -----------------------------------------
            out.mkdir(exist_ok=True)

            voicebank_roots = sorted((work / "ritsu_extracted").glob("*波音リツ*"))
            voicebank_root = (
                voicebank_roots[0] if voicebank_roots else (work / "ritsu_extracted")
            )

            # dsdict はこの段の複数変換（教師/amitaro・user）が使うため先頭で
            # 解決する（run 5/6 でも同一実体 — 位置の前倒しは挙動非依存）。
            dsdict_candidates = sorted(
                (work / "ritsu_diffsinger_extracted").glob("**/dsdur/dsdict.yaml")
            )
            if not dsdict_candidates:
                raise StageFailure("dsdict.yaml not found in NamineRitsu_DiffSinger zip")
            dsdict = dsdict_candidates[0]

            # 教師データの生成/検証（分岐の単一ソース = dataset pins の
            # セクション構成。run 5/6 = "d3"〔d3synth 再生成〕・run 7 =
            # "amitaro"〔実録音教師 intake・DESIGN_S6_run7.md §2〕）。
            teacher_dataset: Path
            if "d3" in dataset_pins:
                # D3 再生成（tripwire + 全数照合は run_d3_cells.py 自身が行う）
                _run([sys.executable, FOUNDRY_DIR / "scripts" / "run_d3_cells.py",
                      "--voicebank-root", voicebank_root,
                      "--out-dir", d3_render_out], label="datasets/run-d3-cells")
                d3_dataset = out / "d3synth_dataset"
                _run([sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_d3.py",
                      "--render-dir", d3_render_out / "render",
                      "--out-dir", d3_dataset], label="datasets/convert-d3")
                d3_diffs = verify_dataset_against_pins(d3_dataset, dataset_pins["d3"], "d3")
                if d3_diffs:
                    raise PinMismatchError(d3_diffs)
                teacher_dataset = d3_dataset
            elif "amitaro" in dataset_pins:
                amitaro_pin = dataset_pins["amitaro"]
                # staged 48kHz raw を Drive（<prefix>/amitaro_sources/）から
                # 取得し、pin の staged_source_sha256 と**集合ごと**全数照合
                # してから変換する（user_sources と同じ「素材は sha で特定」
                # の流儀。欠落・取り違え・余剰はここで fail-closed）。
                # **今回の取得専用 clean staging**（外部レビュー P1）:
                # 残骸が sha 索引へ混ざると今回の欠落を補完できてしまう。
                amitaro_src_dir = prepare_clean_staging_dir(work / "amitaro_sources")
                # user_sources と同じ ID 指定取得（名前ベース copy は Drive の
                # 同名重複を黙って落とす/取り違える — PR#274 の教訓を継承。
                # セルフレビュー #1）。
                amitaro_listing = _run_capture(
                    ["rclone", "--config", rclone_conf, "lsjson", "--recursive",
                     "--files-only",
                     f"run5drive:{_remote_path('amitaro_sources')}",
                     "--drive-root-folder-id", drive_folder_id],
                    timeout=300, label="datasets/amitaro-sources-lsjson",
                )
                amitaro_plan = plan_drive_id_fetch(amitaro_listing)
                print(f"| run5_bootstrap: amitaro_sources = {len(amitaro_plan)} "
                      "file(s) by Drive ID", flush=True)
                for file_id, local_name in amitaro_plan:
                    _run(["rclone", "--config", rclone_conf, "backend", "copyid",
                          "run5drive:", file_id, amitaro_src_dir / local_name],
                         timeout=600, label="datasets/amitaro-sources-rclone")
                # 取得ローカル名は連番前置（plan_drive_id_fetch の設計）。
                # convert_amitaro は `recitationNNN.wav` の名前で走査するため、
                # **中身 sha で pin の期待名へ解決してから**別ディレクトリへ
                # 期待名で配置する（§ materialize_staged_sources）。
                # hash 前にランナウェイ guard（user_sources と同じ露出 —
                # rclone は取得先の中身を制御できない）。
                guard_user_sources_size(amitaro_src_dir)
                staged_pins: Dict[str, str] = amitaro_pin["staged_source_sha256"]
                amitaro_named_dir = work / "amitaro_named"
                resolution = materialize_staged_sources(
                    amitaro_src_dir, staged_pins, amitaro_named_dir
                )
                print(f"| run5_bootstrap: amitaro_sources resolved "
                      f"{len(resolution.resolved)}/{len(staged_pins)} by sha256 "
                      f"→ {amitaro_named_dir.name}/ へ期待名で配置"
                      f"（期待集合と完全一致）", flush=True)
                amitaro_dataset = out / "amitaro_dataset"
                _run([sys.executable,
                      FOUNDRY_DIR / "s1_dataprep" / "convert_amitaro.py",
                      "--staged-dir", amitaro_named_dir,
                      "--transcript", FOUNDRY_DIR / "s1_dataprep" / "data" /
                      "ita_recitation_transcript_utf8.txt",
                      "--dsdict", dsdict,
                      "--out-dir", amitaro_dataset,
                      # 正規化目標・dosage 帯は pins が単一ソース（run 6 user の
                      # LUFS と同じ構造保証 — セルフレビュー #5）
                      "--normalize-loudness-lufs",
                      str(amitaro_pin["normalization_target_lufs"]),
                      "--dosage-target-s", str(amitaro_pin["dosage"]["target_s"]),
                      "--dosage-tolerance", str(amitaro_pin["dosage"]["tolerance"])],
                     label="datasets/convert-amitaro")
                amitaro_diffs = verify_dataset_against_pins(
                    amitaro_dataset, amitaro_pin, "amitaro"
                )
                if amitaro_diffs:
                    raise PinMismatchError(amitaro_diffs)
                teacher_dataset = amitaro_dataset
            else:
                raise StageFailure(
                    "dataset pins に教師セクション（'d3' or 'amitaro'）が無い "
                    "(fail-closed — pins ファイルの取り違え疑い)"
                )

            # user replay 正規化（runbook §2.3: intake.py は再実行しない —
            # ffmpeg 直接変換で台帳 normalized_path のファイル名へ再生成し、
            # 台帳 sha256 と照合する）
            user_norm_dir = work / "user_normalized"
            user_norm_dir.mkdir(exist_ok=True)
            norm_diffs: List[str] = []
            for entry in ledger_entries:
                norm_name = Path(entry["normalized_path"]).name
                norm_path = user_norm_dir / norm_name
                _run(["ffmpeg", "-y", "-i", source_paths[entry["card_id"]],
                      "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", norm_path],
                     label=f"datasets/user-normalize/{norm_name}")
                actual = sha256_file(norm_path)
                if actual != entry["sha256"]:
                    norm_diffs.append(
                        f"user normalized {norm_name}: {actual} != ledger sha256"
                    )
            if norm_diffs:
                raise PinMismatchError(norm_diffs)

            user_dataset = out / "user_dataset"
            convert_user_cmd = [
                sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_user.py",
                "--normalized-dir", user_norm_dir,
                "--ledger", USER_DONOR_LEDGER_PATH,
                "--dsdict", dsdict,
                "--out-dir", user_dataset,
            ]
            # run 6（DESIGN_S5 §1.1/§2-2）: 正規化目標は dataset pins が単一
            # ソース（pin 生成時と同じ値で実行されることを構造的に保証）。
            norm_target = dataset_pins["user"].get("normalization_target_lufs")
            if norm_target is not None:
                convert_user_cmd += ["--normalize-loudness-lufs", str(norm_target)]
            _run(convert_user_cmd, label="datasets/convert-user")
            user_diffs = verify_dataset_against_pins(
                user_dataset, dataset_pins["user"], "user"
            )
            if user_diffs:
                raise PinMismatchError(user_diffs)

            # D2 / pjs 変換（S1_GPU_RUNBOOK §3 と同一呼び出し）
            _run([sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_ritsu.py",
                  "--voicebank-root", voicebank_root,
                  "--dsdict", dsdict,
                  "--out-dir", out / "ritsu_diffsinger_db"], label="datasets/convert-ritsu")
            converter_dir = work / "nnsvs-db-converter"
            _run(["git", "clone", materials["nnsvs_db_converter"]["url"], converter_dir],
                 label="datasets/clone-nnsvs-db-converter")
            _run(["git", "-C", converter_dir, "checkout",
                  materials["nnsvs_db_converter"]["commit"]],
                 label="datasets/pin-nnsvs-db-converter")
            pjs_roots = sorted((work / "pjs_extracted").glob("*PJS*"))
            _run([sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_pjs.py",
                  "--pjs-root", pjs_roots[0] if pjs_roots else work / "pjs_extracted",
                  "--converter-dir", converter_dir,
                  "--staging-dir", out / "pjs_staging"], label="datasets/convert-pjs")
            heartbeat.mark("datasets", "ok")

            # --- stage 5: assemble（教師スロットはプロファイル解決） --------
            assemble_script = FOUNDRY_DIR / "s1_dataprep" / "assemble_run4.py"
            teacher_flag = (
                "--amitaro-raw-dir" if ASSEMBLE_PROFILE == "run7" else "--d3-raw-dir"
            )
            _run([sys.executable, assemble_script,
                  "--profile", ASSEMBLE_PROFILE,
                  "--ritsu-raw-dir", out / "ritsu_diffsinger_db",
                  teacher_flag, teacher_dataset,
                  "--pjs-raw-dir", out / "pjs_staging" / "diffsinger_db",
                  "--user-raw-dir", user_dataset,
                  "--out-dir", run5_raw], label="assemble/assemble")
            live_config = run5_raw / "run4_config_datasets.yaml"
            _run([sys.executable, assemble_script, "refresh-config-pin",
                  "--profile", ASSEMBLE_PROFILE,
                  "--config", live_config], label="assemble/refresh-config-pin")
            assembly_manifest = json.loads(
                (run5_raw / "assembly_manifest.json").read_text(encoding="utf-8")
            )
            asm_diffs = verify_assembly_against_run4_pins(assembly_manifest, dataset_pins)
            if asm_diffs:
                raise PinMismatchError(asm_diffs)
            dict_txt = run5_raw / "dict.txt"
            exec_manifest["dataset_hashes"] = {
                "live_config": sha256_file(live_config),
                "assembly_manifest": sha256_file(run5_raw / "assembly_manifest.json"),
                "dict_txt": sha256_file(dict_txt) if dict_txt.exists() else None,
            }
            heartbeat.mark("assemble", "ok")

            # --- stage 6: binarize -----------------------------------------
            _run(["git", "clone", materials["diffsinger_repo"]["url"], ds_repo],
                 label="binarize/clone-diffsinger")
            _run(["git", "-C", ds_repo, "checkout", materials["diffsinger_repo"]["commit"]],
                 label="binarize/pin-diffsinger")
            # vocoder 配置（run 4 実績 = e2307b1 configs/acoustic.yaml の既定
            # `vocoder_ckpt` パスそのもの。materials 段で placement_dirname
            # 照合済み）。
            vocoder_dest = ds_repo / "checkpoints" / vocoder_dir.name
            vocoder_dest.parent.mkdir(parents=True, exist_ok=True)
            _run(["cp", "-r", vocoder_dir, vocoder_dest], label="binarize/place-vocoder")
            _run([sys.executable, "-m", "pip", "install", "-r",
                  ds_repo / "requirements.txt"], label="binarize/pip-requirements")
            # review セルフレビュー #8（部分採用）: requirements.txt が数値
            # スタック版を動かした場合に備え、事後の実版数を heartbeat に記録
            # する（fail-closed にはしない — 4 ゲートの決定論契約の射程は
            # 「D3 render / user 正規化」までで、その両方はこの時点で pin
            # 照合済み。binarize/学習の出力はバイト pin されておらず、run 4 も
            # DiffSinger requirements 環境で binarize/学習した）。
            stack_report = subprocess.run(
                [sys.executable, "-c",
                 "import numpy, scipy; print('numpy', numpy.__version__, "
                 "'scipy', scipy.__version__)"],
                capture_output=True, text=True,
            ).stdout.strip()
            heartbeat.mark("binarize_numeric_stack", "info", detail=stack_report)
            exec_manifest["environment_versions"]["binarize_numeric_stack"] = (
                stack_report)
            # 学習側 venv の実測 freeze（外部レビュー P3 — DiffSinger
            # requirements は上流ファイル任せのため、実際に解決された版を
            # 次 run の lock 生成元として残す）。
            _run([sys.executable, "-m", "pip", "freeze"],
                 label="binarize/pip-freeze")
            _run([sys.executable, ds_repo / "scripts" / "binarize.py",
                  "--config", live_config],
                 cwd=ds_repo, label="binarize/binarize")
            # binarize 後の spk_map.json 完全一致検査（DESIGN_S6_run7.md
            # §0-2/§3-4: DiffSinger 自動採番による欠番充填・話者取り違えを
            # 学習前に fail-closed で止める。全プロファイル共通の前進措置 —
            # run 5/6 でも期待マップは既知につき無害）。binary_data_dir は
            # assemble が config へ書く既定 = <out-dir>/binary。
            spk_map_diffs = verify_spk_map(
                run5_raw / "binary" / "spk_map.json", EXPECTED_SPK_MAP
            )
            if spk_map_diffs:
                raise PinMismatchError(spk_map_diffs)
            heartbeat.mark(
                "binarize_spk_map", "ok",
                detail=json.dumps(EXPECTED_SPK_MAP, ensure_ascii=False, sort_keys=True),
            )
            heartbeat.mark("binarize", "ok")

            # --- stage 7/8: 学習 2 フェーズ ---------------------------------
            live = yaml.safe_load(live_config.read_text(encoding="utf-8"))
            phase_a_cfg_path = run5_raw / "run5_config_phase_a.yaml"
            phase_a_cfg_path.write_text(
                yaml.safe_dump(derive_phase_config(live, phase="a"),
                               allow_unicode=True, sort_keys=False), encoding="utf-8")

            training_manifest: Dict[str, object] = {
                "schema": "run5-training-manifest/0.1",
                "live_config_sha256": sha256_file(live_config),
                "phase_a_config_sha256": sha256_file(phase_a_cfg_path),
            }

            def train_phase(config_path: Path, exp_name: str, stage: str) -> Path:
                """1 フェーズ学習を実行し、milestone watcher で 5K 節目毎の
                NaN スキャン + Drive push を行う。戻り値は checkpoint dir。

                review セルフレビュー #2: checkpoint は train.py が書き終える
                前に最終名で現れうる（原子的 rename 非保証）ため、(a) サイズが
                1 ポーリング周期安定するまでスキャンしない、(b) torch.load
                失敗（exit 2）は NaN 判定と区別して再試行し、NAN_SCAN_MAX_RETRIES
                回連続で読めない場合のみ「読めない checkpoint」として停止する
                （NaN と誤記しない）。push はスキャン成功後のみ。

                2026-08-19 外部レビュー P1/P2: milestone push は
                `PHASE_REMOTE_DIRS` の namespace 配下へ行い（phase A/B の同名
                後勝ち上書きの根治）、train.py の stdout/stderr は
                `<ckpt_dir>/train.log` へ記録する（run 5 実走ではコンソール
                直結で学習生ログが残らなかった — s4_record §2。salvage の
                `*.log` glob が phase logs へ回収し、失敗時は末尾を
                StageFailure 経由で failure heartbeat の detail に添付する）。"""
                ckpt_dir = ds_repo / "checkpoints" / exp_name
                phase_dest = PHASE_REMOTE_DIRS[exp_name]
                ckpt_dir.mkdir(parents=True, exist_ok=True)
                train_log = ckpt_dir / "train.log"

                def train_log_tail() -> str:
                    tail = read_tail(train_log)
                    return f"\n--- train.log (tail) ---\n{tail}" if tail else ""

                # -u: 子プロセスの stdout をアンバッファ化（セルフレビュー #3:
                # ファイル向きの stdout はブロックバッファになり、NaN 検知等の
                # SIGKILL 経路では直近出力が flush されず train.log 末尾の診断が
                # 失敗直前を写さない）。
                train_log_f = open(train_log, "ab")
                proc = subprocess.Popen(
                    [sys.executable, "-u", str(ds_repo / "scripts" / "train.py"),
                     "--config", str(config_path), "--exp_name", exp_name],
                    cwd=str(ds_repo),
                    stdout=train_log_f, stderr=subprocess.STDOUT,
                )
                seen: List[int] = []
                prev_sizes: Dict[int, int] = {}
                scan_retries: Dict[int, int] = {}

                def scan_and_handle(step: int) -> None:
                    ckpt = ckpt_dir / f"model_ckpt_steps_{step}.ckpt"
                    rc = _scan_ckpt_for_nonfinite(ckpt)
                    if rc == 2:
                        scan_retries[step] = scan_retries.get(step, 0) + 1
                        if scan_retries[step] >= NAN_SCAN_MAX_RETRIES:
                            proc.kill()
                            raise StageFailure(
                                f"[{stage}] checkpoint at step {step} unreadable after "
                                f"{NAN_SCAN_MAX_RETRIES} attempts (torch.load failure — "
                                "NOT a NaN verdict; treating as corrupt checkpoint)"
                                + train_log_tail()
                            )
                        return  # seen に入れず次ポーリングで再試行
                    seen.append(step)
                    if rc == 0:
                        pusher(ckpt, f"{phase_dest}/checkpoints")
                        heartbeat.mark(f"{stage}_step_{step}", "ok")
                    else:
                        heartbeat.mark(f"{stage}_step_{step}", "nan")
                        proc.kill()
                        raise StageFailure(
                            f"[{stage}] NaN detected at step {step} — fail-closed "
                            "(run 4 と同じ 5K 節目 NaN スキャン規律)"
                            + train_log_tail()
                        )

                try:
                    while True:
                        if remaining_seconds(start, time.monotonic()) <= 0:
                            proc.kill()
                            raise StageFailure(
                                f"[{stage}] wall-clock limit "
                                f"({WALL_CLOCK_LIMIT_SECONDS}s) reached — killing "
                                "training and salvaging latest checkpoints "
                                "(DESIGN_S4 §3.4)" + train_log_tail()
                            )
                        now_sizes = milestone_ckpt_sizes(ckpt_dir)
                        for step in stable_milestone_candidates(seen, prev_sizes, now_sizes):
                            scan_and_handle(step)
                        prev_sizes = now_sizes
                        rc_proc = proc.poll()
                        if rc_proc is not None:
                            # プロセス終了後はもう書き込み中ではない — 残りの
                            # milestone をサイズ安定待ちなしで最終スキャンする
                            # （終了直前に書かれた 40K ckpt を取りこぼさない）。
                            for step in sorted(milestone_ckpt_sizes(ckpt_dir)):
                                if step not in seen:
                                    scan_retries[step] = NAN_SCAN_MAX_RETRIES - 1
                                    scan_and_handle(step)
                            if rc_proc != 0:
                                raise StageFailure(
                                    f"[{stage}] train.py exit {rc_proc}"
                                    + train_log_tail()
                                )
                            return ckpt_dir
                        time.sleep(POLL_INTERVAL_SECONDS)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    train_log_f.close()

            ckpt_dir_a = train_phase(phase_a_cfg_path, EXP_NAME_PHASE_A, "train_phase_a")
            phase_a_5k = ckpt_dir_a / f"model_ckpt_steps_{PHASE_A_MAX_UPDATES}.ckpt"
            if not phase_a_5k.exists():
                raise StageFailure(f"phase A finished without {phase_a_5k}")
            heartbeat.mark("train_phase_a", "ok")

            phase_b_cfg_path = run5_raw / "run5_config_phase_b.yaml"
            phase_b_cfg_path.write_text(
                yaml.safe_dump(
                    derive_phase_config(live, phase="b",
                                        finetune_ckpt_path=str(phase_a_5k)),
                    allow_unicode=True, sort_keys=False), encoding="utf-8")
            training_manifest["phase_b_config_sha256"] = sha256_file(phase_b_cfg_path)
            training_manifest["phase_a_5k_ckpt_sha256"] = sha256_file(phase_a_5k)
            (run5_raw / "run5_training_manifest.json").write_text(
                json.dumps(training_manifest, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n",
                encoding="utf-8")

            train_phase(phase_b_cfg_path, EXP_NAME_PHASE_B, "train_phase_b")
            heartbeat.mark("train_phase_b", "ok")

        except Exception as exc:
            # review セルフレビュー #3: 予見可能例外（KeyError/ValueError/
            # yaml.YAMLError 等）の列挙漏れで salvage/self-stop を飛ばさない
            # よう Exception で受ける（無人 Pod に「上へ investigate させる」
            # 相手はいない — 診断材料は traceback ごと heartbeat へ退避する）。
            print(f"error: {exc}", file=sys.stderr)
            traceback.print_exc()
            heartbeat.mark("failure", "failed",
                           detail=f"{exc}\n{traceback.format_exc()}")
            exec_manifest["failure_history"].append(
                {"utc": utc_now_iso(), "error": tail_text(str(exc), 2000)})
            exit_code = 1

        # --- stage 9: salvage（成功・失敗どちらでも必ず通る） -----------------
        # review セルフレビュー #4/#5: 成果物はこの時点のディスク実態から収集
        # し（失敗地点非依存）、push は致命側（失敗を exit code / heartbeat に
        # 表面化させる）で行う。**push が先・hash/manifest は後**（今回セルフ
        # レビュー #5: wall-clock/予算枯渇の salvage で checkpoint の脱出前に
        # 数 GB の sha256 計算を挟まない — 「checkpoint を先に押し出す」順序
        # 規律は hash 会計にも適用する）。
        artifacts = collect_salvage_artifacts(run5_raw, ds_repo)
        salvage_failures = 0
        for path, dest in artifacts:
            if not _rclone_push_strict(rclone_conf, drive_folder_id, path, dest):
                salvage_failures += 1
        if salvage_failures:
            salvage_status = "failed"
            exit_code = 1
        elif exit_code == 0:
            salvage_status = "ok"
        else:
            salvage_status = "partial"
        heartbeat.mark(
            "salvage", salvage_status,
            detail=f"pushed {len(artifacts) - salvage_failures}/{len(artifacts)} artifact(s)",
        )
        # 実行 manifest の会計を確定して書き出し・push（外部レビュー P2）。
        # この push の失敗も exit code / heartbeat に表面化させる（今回セルフ
        # レビュー #4: 戻り値無視は「正本の会計欠落」が静かに通る）。
        exec_manifest["end_time"] = utc_now_iso()
        exec_manifest["self_stop_status"] = (
            "skipped (--skip-self-stop)" if args.skip_self_stop else "scheduled")
        for path, dest in artifacts:
            if dest.endswith("/checkpoints"):
                exec_manifest["checkpoint_hashes"][f"{dest}/{path.name}"] = (
                    sha256_file(path))
            elif path.name.startswith("events.out.tfevents."):
                exec_manifest["tensorboard_hashes"][f"{dest}/{path.name}"] = (
                    sha256_file(path))
        exec_manifest["artifact_count"] = len(artifacts)
        exec_manifest["salvage_status"] = salvage_status
        manifest_path = write_execution_manifest(exec_manifest, run5_raw)
        if not _rclone_push_strict(rclone_conf, drive_folder_id, manifest_path):
            exit_code = 1
            heartbeat.mark("exec_manifest_push", "failed",
                           detail="run_execution_manifest.json push failed")
        return exit_code

    finally:
        # --- stage 10: self_stop（どの経路でも必ず通る — review #3。Pod 放置
        # 課金の構造的排除。ディスク残置 = 保険経路 (c) はそのまま） -----------
        if not args.skip_self_stop:
            pod_id = os.environ.get("RUNPOD_POD_ID", "")
            if pod_id:
                subprocess.run(self_stop_command(pod_id))
            else:
                print("| run5_bootstrap: RUNPOD_POD_ID unset — self-stop skipped "
                      "(ローカル実行?)", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
