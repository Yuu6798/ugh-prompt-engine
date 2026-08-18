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
                       不一致なら停止）。user 宅録原本は
                       `RUN5_USER_SOURCES_URL`（環境変数注入 — スクリプト
                       本文に Drive リンクは書かない）から取得し、
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

- `RUN5_USER_SOURCES_URL` : user 宅録原本アーカイブ（17 本）の直リンク
- `RUN5_RCLONE_CONF_B64`  : rclone.conf の base64（成果物専用フォルダに
                            権限を限定したスコープ — Drive 全域トークン
                            不可 = DESIGN_S4 §3.3）
- `RUN5_DRIVE_FOLDER_ID`  : 退避先 Google Drive フォルダ ID
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

REQUIRED_ENV_VARS = (
    "RUN5_USER_SOURCES_URL", "RUN5_RCLONE_CONF_B64", "RUN5_DRIVE_FOLDER_ID",
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
    """pin 表を読み、PENDING（`sha256` キーを持つのに null のままの素材）が
    あれば `PinPendingError` で fail-closed する。戻り値は materials dict。
    `path` 省略時はモジュール属性 `MATERIAL_PINS_PATH` を**呼び出し時**に
    解決する（テストが monkeypatch で差し替えられるように — def 時束縛の
    既定引数にしない）。"""
    if path is None:
        path = MATERIAL_PINS_PATH
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    materials = data["materials"]
    pending = sorted(
        name for name, entry in materials.items()
        if "sha256" in entry and entry["sha256"] is None
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


def _rclone_pusher(rclone_conf: Path, drive_folder_id: str) -> Callable[[Path], None]:
    def push(path: Path) -> None:
        argv = [
            "rclone", "--config", str(rclone_conf), "copy", str(path),
            "run5drive:", "--drive-root-folder-id", drive_folder_id,
        ]
        # heartbeat push の失敗は run 全体を落とさない（進捗可視性の劣化で
        # あって成果物の毀損ではない。salvage 段の push 失敗は別 — そちらは
        # StageFailure で表面化させる）。
        try:
            subprocess.run([str(a) for a in argv], check=True, timeout=300)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"| run5_bootstrap: heartbeat push failed (non-fatal here): {exc}",
                  flush=True)
    return push


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    elif name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
        with tarfile.open(archive) as t:
            t.extractall(dest)
    else:
        raise StageFailure(f"unsupported archive format: {archive}")


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

    # --- stage 1: preflight -------------------------------------------------
    missing = check_required_env(dict(os.environ))
    if missing:
        print(f"error: missing required env var(s): {missing}", file=sys.stderr)
        return 1
    try:
        materials = load_material_pins()
    except PinPendingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rclone_conf = work / "rclone.conf"
    rclone_conf.write_bytes(base64.b64decode(os.environ["RUN5_RCLONE_CONF_B64"]))
    pusher = _rclone_pusher(rclone_conf, os.environ["RUN5_DRIVE_FOLDER_ID"])
    heartbeat = Heartbeat(work / "heartbeat", pusher)
    heartbeat.mark("preflight", "ok")

    dataset_pins = json.loads(DATASET_PINS_PATH.read_text(encoding="utf-8"))

    exit_code = 0
    salvage_paths: List[Path] = []
    try:
        # --- stage 2: gates（runbook §2.2） ---------------------------------
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

        # --- stage 3: materials --------------------------------------------
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
        _run(["gdown", f"https://drive.google.com/uc?id={materials['pjs_corpus_zip']['gdown_id']}",
              "-O", pjs_zip], label="materials/pjs-zip")
        verify_file_sha256(pjs_zip, materials["pjs_corpus_zip"]["sha256"], "pjs_corpus_zip")

        namine_zip = dl / "NamineRitsu_DiffSinger.zip"
        _run(["curl", "-L", "--fail", "-o", namine_zip,
              materials["namine_ritsu_diffsinger_zip"]["url"]], label="materials/namine-zip")
        verify_file_sha256(namine_zip, materials["namine_ritsu_diffsinger_zip"]["sha256"],
                           "namine_ritsu_diffsinger_zip")

        ffmpeg_tar = dl / "ffmpeg_static.tar.xz"
        _run(["curl", "-L", "--fail", "-o", ffmpeg_tar,
              materials["ffmpeg_static"]["url"]], label="materials/ffmpeg-static")
        verify_file_sha256(ffmpeg_tar, materials["ffmpeg_static"]["sha256"], "ffmpeg_static")

        # 学習側 vocoder（pc_nsf_hifigan zip + model.ckpt。run 4 実績 pin —
        # pins 表 `vocoder_pc_nsf_hifigan` の provenance 欄参照）。zip と
        # 内容物 model.ckpt の両方を照合し、配置は binarize 段（DiffSinger
        # clone 後）で run 4 実績の checkpoints/ 直下パスへ行う。
        vocoder_pin = materials["vocoder_pc_nsf_hifigan"]
        vocoder_zip = dl / "pc_nsf_hifigan.zip"
        _run(["curl", "-L", "--fail", "-o", vocoder_zip, vocoder_pin["url"]],
             label="materials/vocoder")
        verify_file_sha256(vocoder_zip, vocoder_pin["sha256"], "vocoder_pc_nsf_hifigan(zip)")
        vocoder_extract_dir = work / "vocoder_extracted"
        _extract_archive(vocoder_zip, vocoder_extract_dir)
        vocoder_ckpts = sorted(vocoder_extract_dir.glob("**/model.ckpt"))
        if len(vocoder_ckpts) != 1:
            raise StageFailure(
                f"vocoder zip must contain exactly one model.ckpt, found {len(vocoder_ckpts)}"
            )
        verify_file_sha256(vocoder_ckpts[0], vocoder_pin["model_ckpt_sha256"],
                           "vocoder_pc_nsf_hifigan(model.ckpt)")
        vocoder_dir = vocoder_ckpts[0].parent  # pc_nsf_hifigan_44.1k_hop512_128bin_2025.02/

        user_archive = dl / "user_sources_archive"
        _run(["curl", "-L", "--fail", "-o", user_archive,
              os.environ["RUN5_USER_SOURCES_URL"]], label="materials/user-sources")

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
        verify_file_sha256(ffmpeg_bins[0], materials["ffmpeg_static"]["ffmpeg_bin_sha256"],
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

        # user 原本 17/17 照合（source_sha256・台帳は改変しない）
        user_src_dir = work / "user_sources"
        _extract_archive(user_archive, user_src_dir)
        ledger = json.loads(USER_DONOR_LEDGER_PATH.read_text(encoding="utf-8"))
        ledger_entries = ledger["entries"] if isinstance(ledger, dict) and "entries" in ledger else ledger
        source_files = {p.name: p for p in user_src_dir.rglob("*") if p.is_file()}
        diffs: List[str] = []
        for entry in ledger_entries:
            src_name = entry["source_filename"]
            if src_name not in source_files:
                diffs.append(f"user source missing: {src_name}")
                continue
            actual = sha256_file(source_files[src_name])
            if actual != entry["source_sha256"]:
                diffs.append(f"user source {src_name}: {actual} != ledger source_sha256")
        if diffs:
            raise PinMismatchError(diffs)
        heartbeat.mark("materials", "ok")

        # --- stage 4: datasets ---------------------------------------------
        out = work / "s4_data"
        out.mkdir(exist_ok=True)

        # D3 再生成（tripwire + 全数照合は run_d3_cells.py 自身が行う）
        voicebank_roots = sorted((work / "ritsu_extracted").glob("*波音リツ*"))
        voicebank_root = voicebank_roots[0] if voicebank_roots else (work / "ritsu_extracted")
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
                norm_diffs.append(f"user normalized {norm_name}: {actual} != ledger sha256")
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
        user_diffs = verify_dataset_against_pins(user_dataset, dataset_pins["user"], "user")
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
        _run(["git", "-C", converter_dir, "checkout", materials["nnsvs_db_converter"]["commit"]],
             label="datasets/pin-nnsvs-db-converter")
        pjs_roots = sorted((work / "pjs_extracted").glob("*PJS*"))
        _run([sys.executable, FOUNDRY_DIR / "s1_dataprep" / "convert_pjs.py",
              "--pjs-root", pjs_roots[0] if pjs_roots else work / "pjs_extracted",
              "--converter-dir", converter_dir,
              "--staging-dir", out / "pjs_staging"], label="datasets/convert-pjs")
        heartbeat.mark("datasets", "ok")

        # --- stage 5: assemble（4 話者・spk_id map v2） ----------------------
        run5_raw = out / "run5_raw"
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
        salvage_paths += [
            run5_raw / "assembly_manifest.json",
            run5_raw / "run4_config_datasets.yaml.normalized.yaml",
            run5_raw / "dict.txt",
        ]

        # --- stage 6: binarize ---------------------------------------------
        ds_repo = work / "DiffSinger"
        _run(["git", "clone", materials["diffsinger_repo"]["url"], ds_repo],
             label="binarize/clone-diffsinger")
        _run(["git", "-C", ds_repo, "checkout", materials["diffsinger_repo"]["commit"]],
             label="binarize/pin-diffsinger")
        # vocoder 配置（run 4 実績の checkpoints/ 直下パスを逐語踏襲 —
        # pins 表 `vocoder_pc_nsf_hifigan.placement`）。
        vocoder_dest = ds_repo / "checkpoints" / vocoder_dir.name
        vocoder_dest.parent.mkdir(parents=True, exist_ok=True)
        _run(["cp", "-r", vocoder_dir, vocoder_dest], label="binarize/place-vocoder")
        _run([sys.executable, "-m", "pip", "install", "-r", ds_repo / "requirements.txt"],
             label="binarize/pip-requirements")
        _run([sys.executable, ds_repo / "scripts" / "binarize.py", "--config", live_config],
             cwd=ds_repo, label="binarize/binarize")
        heartbeat.mark("binarize", "ok")

        # --- stage 7/8: 学習 2 フェーズ -------------------------------------
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
            NaN スキャン + Drive push を行う。戻り値は checkpoint dir。"""
            ckpt_dir = ds_repo / "checkpoints" / exp_name
            proc = subprocess.Popen(
                [sys.executable, str(ds_repo / "scripts" / "train.py"),
                 "--config", str(config_path), "--exp_name", exp_name],
                cwd=str(ds_repo),
            )
            seen: List[int] = []
            try:
                while True:
                    if remaining_seconds(start, time.monotonic()) <= 0:
                        proc.kill()
                        raise StageFailure(
                            f"[{stage}] wall-clock limit "
                            f"({WALL_CLOCK_LIMIT_SECONDS}s) reached — killing training "
                            "and salvaging latest checkpoints (DESIGN_S4 §3.4)"
                        )
                    for step in new_milestones(seen, find_milestone_ckpts(ckpt_dir)):
                        seen.append(step)
                        ckpt = ckpt_dir / f"model_ckpt_steps_{step}.ckpt"
                        nan_check = subprocess.run(
                            [sys.executable, "-c",
                             "import sys, torch; "
                             f"sd = torch.load({str(ckpt)!r}, map_location='cpu')['state_dict']; "
                             "bad = [k for k, v in sd.items() if not torch.isfinite(v).all()]; "
                             "print(('NaN scan OK' if not bad else 'NaN scan NG:'), bad[:5]); "
                             "sys.exit(0 if not bad else 1)"],
                        )
                        pusher(ckpt)
                        heartbeat.mark(f"{stage}_step_{step}",
                                       "ok" if nan_check.returncode == 0 else "nan")
                        if nan_check.returncode != 0:
                            proc.kill()
                            raise StageFailure(
                                f"[{stage}] NaN detected at step {step} — fail-closed "
                                "(run 4 と同じ 5K 節目 NaN スキャン規律)"
                            )
                    rc = proc.poll()
                    if rc is not None:
                        if rc != 0:
                            raise StageFailure(f"[{stage}] train.py exit {rc}")
                        return ckpt_dir
                    time.sleep(60)
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
                derive_phase_config(live, phase="b", finetune_ckpt_path=str(phase_a_5k)),
                allow_unicode=True, sort_keys=False), encoding="utf-8")
        training_manifest["phase_b_config_sha256"] = sha256_file(phase_b_cfg_path)
        training_manifest["phase_a_5k_ckpt_sha256"] = sha256_file(phase_a_5k)
        (run5_raw / "run5_training_manifest.json").write_text(
            json.dumps(training_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        ckpt_dir_b = train_phase(phase_b_cfg_path, EXP_NAME_PHASE_B, "train_phase_b")
        heartbeat.mark("train_phase_b", "ok")

        salvage_paths += [
            phase_a_cfg_path, phase_b_cfg_path,
            run5_raw / "run5_training_manifest.json",
            phase_a_5k,
        ]
        for step, ckpt in sorted(find_milestone_ckpts(ckpt_dir_b).items()):
            salvage_paths.append(ckpt)
        for pattern in ("config.yaml", "*.log", "**/events.out.tfevents.*"):
            salvage_paths += sorted(ckpt_dir_b.glob(pattern))

    except (StageFailure, PinMismatchError, subprocess.SubprocessError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        heartbeat.mark("failure", "failed", detail=str(exc))
        exit_code = 1

    # --- stage 9: salvage（成功・失敗どちらでも必ず通る） ---------------------
    for path in salvage_paths:
        if Path(path).exists():
            pusher(Path(path))
    heartbeat.mark("salvage", "ok" if exit_code == 0 else "partial")

    # --- stage 10: self_stop -------------------------------------------------
    if not args.skip_self_stop:
        pod_id = os.environ.get("RUNPOD_POD_ID", "")
        if pod_id:
            subprocess.run(self_stop_command(pod_id))
        else:
            print("| run5_bootstrap: RUNPOD_POD_ID unset — self-stop skipped "
                  "(ローカル実行?)", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
