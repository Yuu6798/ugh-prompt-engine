"""run4 checkpoint `state_dict` 非有限値検査 CLI（debt ledger VG-DEBT-007）。

`S3_RUN4_RUNBOOK.md:388-390` が定める節目ゲート（5K/10K/20K/40K の checkpoint
回収 + state_dict の非有限値チェック）を実施するスタンドアロンスクリプト。
`results_s3/s3_record_2026-08-17.md` §7.4-2 が「学習ログの `NaN`/`Inf` 文字列
走査までは実施済みだが、checkpoint `state_dict` 自体の非有限値検査は未実施」
と明記している未払いゲートに対応する。

checkpoint 実体は本リポジトリに同梱しない（voice_genesis 全体の方針。
`voice_genesis/README.md` の「WAV は非同梱」節と同型）。したがって本スクリプトの
実行自体は checkpoint 保管環境（machine-dependent）で行う。

torch は実行時にのみ import する（voice_genesis のオプショナル依存フォールバック
規約。CLI 自体は torch 不在でも import・`--help` 表示ができる）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class CheckpointReadError(RuntimeError):
    """checkpoint が読めない、または state_dict を持たない場合に送出する。

    fail-closed: 読めない checkpoint は黙って結果から除外せず、error として
    記録したうえで CLI 全体を非ゼロ終了させる。
    """


def _sha256_of_file(path: Path) -> str:
    """ファイルをストリーム読み込みして sha256 を算出する（手打ちなし）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_torch() -> Any:
    """torch を実行時にのみ import する。不在なら明確なエラーで exit 2。"""
    try:
        import torch  # noqa: F401 (実行時 import であることが目的)
    except ModuleNotFoundError as exc:
        print(
            "error: torch がインストールされていません "
            "(checkpoint の state_dict 検査には torch が必要です). "
            f"原因: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return torch


def _extract_state_dict(loaded: Any) -> Dict[str, Any]:
    """`torch.load()` の戻り値から state_dict を取り出す。

    DiffSinger 系 checkpoint は `{"state_dict": {...}, ...}` の dict、または
    state_dict そのものの 2 通りがあり得るため両方を許容する。どちらでもない
    場合は fail-closed で `CheckpointReadError` を送出する。
    """
    if isinstance(loaded, dict) and "state_dict" in loaded and isinstance(loaded["state_dict"], dict):
        return loaded["state_dict"]
    if isinstance(loaded, dict):
        # 全値が tensor-like（.dim を持つ等）なら state_dict そのものとみなす。
        return loaded
    raise CheckpointReadError(
        "state_dict を持つ dict、または state_dict そのものの dict ではありません"
    )


def check_one_checkpoint(path: Path, torch_module: Any) -> Dict[str, Any]:
    """1 checkpoint を検査し、結果 dict を返す。読み込み失敗時は例外を投げる
    （fail-closed。呼び出し側が捕捉して error エントリへ変換する）。
    """
    if not path.exists():
        raise CheckpointReadError(f"checkpoint が存在しません: {path}")

    sha256 = _sha256_of_file(path)

    try:
        loaded = torch_module.load(str(path), map_location="cpu", weights_only=False)
    except TypeError:
        # 旧 torch は weights_only 引数を持たない。
        loaded = torch_module.load(str(path), map_location="cpu")
    except Exception as exc:  # noqa: BLE001 - fail-closed で理由を記録するため捕捉
        raise CheckpointReadError(f"torch.load に失敗しました: {exc}") from exc

    state_dict = _extract_state_dict(loaded)

    non_finite_by_tensor: Dict[str, int] = {}
    total_non_finite = 0
    total_elements = 0
    n_tensors_checked = 0
    n_tensors_skipped = 0

    for name, value in state_dict.items():
        if not hasattr(value, "numel") or not hasattr(value, "dtype"):
            n_tensors_skipped += 1
            continue
        if not torch_module.is_floating_point(value):
            # 整数/bool テンソル（例: バッチカウンタ）は非有限値の定義対象外。
            n_tensors_skipped += 1
            continue
        n_tensors_checked += 1
        n_elements = int(value.numel())
        total_elements += n_elements
        n_non_finite = int((~torch_module.isfinite(value)).sum().item())
        total_non_finite += n_non_finite
        if n_non_finite > 0:
            non_finite_by_tensor[name] = n_non_finite

    return {
        "path": str(path),
        "sha256": sha256,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "torch_version": str(torch_module.__version__),
        "n_tensors_checked": n_tensors_checked,
        "n_tensors_skipped_non_float": n_tensors_skipped,
        "total_elements_checked": total_elements,
        "total_non_finite": total_non_finite,
        "non_finite_by_tensor": non_finite_by_tensor,
        "all_finite": total_non_finite == 0,
        "status": "ok",
    }


def _load_pins(pins_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if pins_path is None:
        return None
    if not pins_path.exists():
        raise CheckpointReadError(f"--pins で指定されたファイルが存在しません: {pins_path}")
    return json.loads(pins_path.read_text(encoding="utf-8"))


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "run4 checkpoint state_dict の非有限値検査（S3_RUN4_RUNBOOK.md:388-390 の"
            "節目ゲートに対応。実行は checkpoint 保管環境で行う）"
        )
    )
    parser.add_argument(
        "checkpoints",
        nargs="+",
        type=Path,
        help="検査対象の checkpoint パス（複数可・例: model_ckpt_steps_5000.ckpt ...）",
    )
    parser.add_argument(
        "--pins",
        type=Path,
        default=None,
        help=(
            "照合用の pin JSON（例: run4_dataset_pins.json 相当の checkpoint sha256 表）。"
            "任意。指定した場合、各 checkpoint の実測 sha256 が pin と一致するか報告に含める。"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="結果 JSON の出力先パス",
    )
    args = parser.parse_args(argv)

    if not args.out.parent.exists():
        print(
            f"error: --out の親ディレクトリが存在しません: {args.out.parent}",
            file=sys.stderr,
        )
        return 1

    torch_module = _load_torch()

    pins: Optional[Dict[str, Any]] = None
    try:
        pins = _load_pins(args.pins)
    except CheckpointReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    results: List[Dict[str, Any]] = []
    any_error = False
    for ckpt_path in args.checkpoints:
        try:
            result = check_one_checkpoint(ckpt_path, torch_module)
            if pins is not None:
                result["pin_sha256_match"] = _match_pin(ckpt_path, result["sha256"], pins)
            results.append(result)
        except CheckpointReadError as exc:
            any_error = True
            results.append(
                {
                    "path": str(ckpt_path),
                    "status": "error",
                    "error": str(exc),
                    "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )

    report = {
        "schema": "voicegenesis-checkpoint-finite-report/0.1",
        "torch_version": str(torch_module.__version__),
        "checkpoints": results,
        "all_ok": (not any_error) and all(r.get("all_finite", False) for r in results if r["status"] == "ok"),
    }

    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if any_error:
        return 1
    if not all(r.get("all_finite", False) for r in results):
        return 3
    return 0


def _match_pin(ckpt_path: Path, actual_sha256: str, pins: Dict[str, Any]) -> Optional[bool]:
    """pins JSON から checkpoint ファイル名に対応する sha256 を探し、一致を返す。
    pins の形状は呼び出し側の運用次第で揺れうるため、`ckpt_path.name` をキーに
    再帰的に走査する軽量マッチのみ行う（見つからなければ None = 照合不能）。
    """

    def _search(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            if ckpt_path.name in node and isinstance(node[ckpt_path.name], str):
                return node[ckpt_path.name]
            for value in node.values():
                found = _search(value)
                if found is not None:
                    return found
        return None

    pinned_sha256 = _search(pins)
    if pinned_sha256 is None:
        return None
    return pinned_sha256 == actual_sha256


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
