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
import subprocess
import sys
import tarfile
import time
import traceback
import zipfile
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
    return diffs


def verify_assembly_against_run4_pins(
    assembly_manifest: dict, dataset_pins: dict
) -> List[str]:
    """assembly_manifest（4 話者・schema 0.4）の d3synth/user セクションが
    `run4_dataset_pins.json` の d3/user pin と一致することを照合する
    （データ内容は run 4 と同一 = DESIGN_S4 §1.1。assemble はバイト単位
    コピーなので、話者ディレクトリの実測記帳が pin と一致するはず）。"""
    diffs: List[str] = []
    pairs = (("d3synth", "d3"), ("user", "user"))
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


def collect_salvage_artifacts(run5_raw: Path, ds_repo: Path) -> List[Path]:
    """salvage 段で push すべき成果物を**その時点のディスク実態から**収集する
    （review セルフレビュー #4: 旧実装は学習 2 フェーズ完走後にのみ
    salvage_paths へ積んでいたため、途中失敗時に log/TB/phase config が
    一切 push されず「成功・失敗どちらでも退避」の契約に反していた。
    パスは決定論的に導出できるため、失敗地点に依らず存在するものを全部
    拾う）。戻り値は存在するファイルのみ（重複なし・順序決定論）。"""
    candidates: List[Path] = [
        run5_raw / "assembly_manifest.json",
        run5_raw / "run4_config_datasets.yaml.normalized.yaml",
        run5_raw / "dict.txt",
        run5_raw / "run5_config_phase_a.yaml",
        run5_raw / "run5_config_phase_b.yaml",
        run5_raw / "run5_training_manifest.json",
    ]
    for exp_name in (EXP_NAME_PHASE_A, EXP_NAME_PHASE_B):
        ckpt_dir = ds_repo / "checkpoints" / exp_name
        for _step, ckpt in sorted(find_milestone_ckpts(ckpt_dir).items()):
            candidates.append(ckpt)
        for pattern in ("config.yaml", "*.log", "**/events.out.tfevents.*"):
            candidates += sorted(ckpt_dir.glob(pattern))
    unique: List[Path] = []
    for path in candidates:
        if path.exists() and path not in unique:
            unique.append(path)
    return unique


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

    def __init__(self, heartbeat_dir: Path, pusher: Callable[[Path], None]) -> None:
        self.heartbeat_dir = Path(heartbeat_dir)
        self.heartbeat_dir.mkdir(parents=True, exist_ok=True)
        self._pusher = pusher

    def mark(self, stage: str, status: str, detail: str = "") -> Path:
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


def _run(argv: Sequence[str], *, cwd: Optional[Path] = None,
         env: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
         label: str = "") -> None:
    printable = " ".join(shlex.quote(str(a)) for a in argv)
    print(f"| run5_bootstrap: [{label}] {printable}", flush=True)
    result = subprocess.run(
        [str(a) for a in argv], cwd=str(cwd) if cwd else None,
        env=env, timeout=timeout,
    )
    if result.returncode != 0:
        raise StageFailure(f"[{label}] exit {result.returncode}: {printable}")


def _rclone_argv(rclone_conf: Path, drive_folder_id: str, path: Path) -> List[str]:
    return [
        "rclone", "--config", str(rclone_conf), "copy", str(path),
        "run5drive:", "--drive-root-folder-id", drive_folder_id,
    ]


def _rclone_pusher(rclone_conf: Path, drive_folder_id: str) -> Callable[[Path], None]:
    """heartbeat / 走行中 milestone 用の**非致命** pusher。失敗は進捗可視性の
    劣化であって成果物の毀損ではないため run を落とさない（最終的な成果物
    保全は salvage 段の `_rclone_push_strict` が担い、そちらの失敗は
    exit code / heartbeat status に表面化する — review セルフレビュー #5）。"""
    def push(path: Path) -> None:
        try:
            subprocess.run(
                _rclone_argv(rclone_conf, drive_folder_id, path),
                check=True, timeout=1800,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"| run5_bootstrap: heartbeat push failed (non-fatal here): {exc}",
                  flush=True)
    return push


def _rclone_push_strict(rclone_conf: Path, drive_folder_id: str, path: Path) -> bool:
    """salvage 段用の**致命側** push（review セルフレビュー #5: 旧実装は
    salvage も swallow-all pusher を使い回しており、最終 checkpoint の push
    失敗が exit code にも heartbeat にも現れないまま self-stop していた）。
    成否を返す（タイムアウトは大型 checkpoint を考慮して 1 時間）。"""
    try:
        subprocess.run(
            _rclone_argv(rclone_conf, drive_folder_id, path),
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
        heartbeat = Heartbeat(work / "heartbeat", pusher)
        heartbeat.mark("preflight", "ok")

        try:
            # --- stage 2: gates（runbook §2.2） -----------------------------
            _run([sys.executable, "-m", "pip", "install", "--no-cache-dir",
                  *NUMERIC_STACK_PIN], label="gates/pin-install")
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

            _run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "gdown"],
                 label="materials/gdown-install")
            pjs_zip = dl / "PJS_corpus_ver1.1.zip"
            _run(["gdown",
                  f"https://drive.google.com/uc?id={materials['pjs_corpus_zip']['gdown_id']}",
                  "-O", pjs_zip], label="materials/pjs-zip")
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
                _run(["rclone", "--config", rclone_conf, "copy",
                      "run5drive:user_sources", user_src_dir,
                      "--drive-root-folder-id", drive_folder_id],
                     label="materials/user-sources-rclone")

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
            if "libavformat" not in version_out or "60.16.100" not in version_out:
                raise StageFailure(
                    "ffmpeg static build does not report libavformat 60.16.100 "
                    "(環境契約 1・s3_record 2026-08-17)"
                )

            # user 原本 17/17 照合（source_sha256・台帳は改変しない。取得は
            # 上の分岐で user_src_dir へ済んでいる — 欠落・取り違えはここで
            # fail-closed になる）
            ledger = json.loads(USER_DONOR_LEDGER_PATH.read_text(encoding="utf-8"))
            ledger_entries = ledger["entries"]
            source_files = {p.name: p for p in user_src_dir.rglob("*") if p.is_file()}
            diffs: List[str] = []
            for entry in ledger_entries:
                src_name = entry["source_filename"]
                if src_name not in source_files:
                    diffs.append(f"user source missing: {src_name}")
                    continue
                actual = sha256_file(source_files[src_name])
                if actual != entry["source_sha256"]:
                    diffs.append(
                        f"user source {src_name}: {actual} != ledger source_sha256"
                    )
            if diffs:
                raise PinMismatchError(diffs)
            heartbeat.mark("materials", "ok")

            # --- stage 4: datasets -----------------------------------------
            out.mkdir(exist_ok=True)

            # D3 再生成（tripwire + 全数照合は run_d3_cells.py 自身が行う）
            voicebank_roots = sorted((work / "ritsu_extracted").glob("*波音リツ*"))
            voicebank_root = (
                voicebank_roots[0] if voicebank_roots else (work / "ritsu_extracted")
            )
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

            # user replay 正規化（runbook §2.3: intake.py は再実行しない —
            # ffmpeg 直接変換で台帳 normalized_path のファイル名へ再生成し、
            # 台帳 sha256 と照合する）
            user_norm_dir = work / "user_normalized"
            user_norm_dir.mkdir(exist_ok=True)
            norm_diffs: List[str] = []
            for entry in ledger_entries:
                norm_name = Path(entry["normalized_path"]).name
                norm_path = user_norm_dir / norm_name
                _run(["ffmpeg", "-y", "-i", source_files[entry["source_filename"]],
                      "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", norm_path],
                     label=f"datasets/user-normalize/{norm_name}")
                actual = sha256_file(norm_path)
                if actual != entry["sha256"]:
                    norm_diffs.append(
                        f"user normalized {norm_name}: {actual} != ledger sha256"
                    )
            if norm_diffs:
                raise PinMismatchError(norm_diffs)

            dsdict_candidates = sorted(
                (work / "ritsu_diffsinger_extracted").glob("**/dsdur/dsdict.yaml")
            )
            if not dsdict_candidates:
                raise StageFailure("dsdict.yaml not found in NamineRitsu_DiffSinger zip")
            dsdict = dsdict_candidates[0]

            user_dataset = out / "user_dataset"
            _run([sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_user.py",
                  "--normalized-dir", user_norm_dir,
                  "--ledger", USER_DONOR_LEDGER_PATH,
                  "--dsdict", dsdict,
                  "--out-dir", user_dataset], label="datasets/convert-user")
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

            # --- stage 5: assemble（4 話者・spk_id map v2） ------------------
            assemble_script = FOUNDRY_DIR / "s1_dataprep" / "assemble_run4.py"
            _run([sys.executable, assemble_script,
                  "--ritsu-raw-dir", out / "ritsu_diffsinger_db",
                  "--d3-raw-dir", d3_dataset,
                  "--pjs-raw-dir", out / "pjs_staging" / "diffsinger_db",
                  "--user-raw-dir", user_dataset,
                  "--out-dir", run5_raw], label="assemble/assemble")
            live_config = run5_raw / "run4_config_datasets.yaml"
            _run([sys.executable, assemble_script, "refresh-config-pin",
                  "--config", live_config], label="assemble/refresh-config-pin")
            assembly_manifest = json.loads(
                (run5_raw / "assembly_manifest.json").read_text(encoding="utf-8")
            )
            asm_diffs = verify_assembly_against_run4_pins(assembly_manifest, dataset_pins)
            if asm_diffs:
                raise PinMismatchError(asm_diffs)
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
            _run([sys.executable, ds_repo / "scripts" / "binarize.py",
                  "--config", live_config],
                 cwd=ds_repo, label="binarize/binarize")
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
                （NaN と誤記しない）。push はスキャン成功後のみ。"""
                ckpt_dir = ds_repo / "checkpoints" / exp_name
                proc = subprocess.Popen(
                    [sys.executable, str(ds_repo / "scripts" / "train.py"),
                     "--config", str(config_path), "--exp_name", exp_name],
                    cwd=str(ds_repo),
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
                            )
                        return  # seen に入れず次ポーリングで再試行
                    seen.append(step)
                    if rc == 0:
                        pusher(ckpt)
                        heartbeat.mark(f"{stage}_step_{step}", "ok")
                    else:
                        heartbeat.mark(f"{stage}_step_{step}", "nan")
                        proc.kill()
                        raise StageFailure(
                            f"[{stage}] NaN detected at step {step} — fail-closed "
                            "(run 4 と同じ 5K 節目 NaN スキャン規律)"
                        )

                try:
                    while True:
                        if remaining_seconds(start, time.monotonic()) <= 0:
                            proc.kill()
                            raise StageFailure(
                                f"[{stage}] wall-clock limit "
                                f"({WALL_CLOCK_LIMIT_SECONDS}s) reached — killing "
                                "training and salvaging latest checkpoints "
                                "(DESIGN_S4 §3.4)"
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
                                raise StageFailure(f"[{stage}] train.py exit {rc_proc}")
                            return ckpt_dir
                        time.sleep(POLL_INTERVAL_SECONDS)
                finally:
                    if proc.poll() is None:
                        proc.kill()

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
            exit_code = 1

        # --- stage 9: salvage（成功・失敗どちらでも必ず通る） -----------------
        # review セルフレビュー #4/#5: 成果物はこの時点のディスク実態から収集
        # し（失敗地点非依存）、push は致命側（失敗を exit code / heartbeat に
        # 表面化させる）で行う。
        artifacts = collect_salvage_artifacts(run5_raw, ds_repo)
        salvage_failures = 0
        for path in artifacts:
            if not _rclone_push_strict(rclone_conf, drive_folder_id, path):
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
