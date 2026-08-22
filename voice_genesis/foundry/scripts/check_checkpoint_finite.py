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

fail-closed 契約（2026-08-22 セルフレビュー修正1-4 + Codex bot レビュー #8）:
- 検査した tensor 数が 0 の checkpoint は `all_finite: true` を騙らず
  `status: "error"`（reason: no_tensors_inspected）にする。
- `--pins` 指定時、pin 不一致 (`pin_sha256_match == False`) と pin 未検出
  (`pin_sha256_match is None`) はどちらも `status: "error"` にし exit を非ゼロに
  する。`--pins` 未指定時のみ照合をスキップでき、その旨を report の
  `pin_verification` に明記する。
- torch.load のフォールバック経路（旧 torch の `weights_only` 非対応）の例外、
  および checkpoint ファイル読み込み時の OSError 系（`PermissionError` /
  `IsADirectoryError` 含む）は必ず `CheckpointReadError` に包み、run() 側で
  error エントリとして記録した上で `--out` に report を書き切ってから exit
  する（クラッシュで報告が未生成にならない）。
- `--out` の解決済みパスが checkpoint 引数または `--pins` の解決済みパスと
  衝突する場合、読み込み前に即エラー終了し何も書き込まない（Codex bot
  レビュー #8: 入力の上書き事故防止）。report JSON の書き出しは同一
  ディレクトリの一時ファイル + `os.replace` による atomic 方式。
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
from typing import Any, Dict, List, Optional


class CheckpointReadError(RuntimeError):
    """checkpoint が読めない、または state_dict を持たない場合に送出する。

    fail-closed: 読めない checkpoint は黙って結果から除外せず、error として
    記録したうえで CLI 全体を非ゼロ終了させる。`reason` は report JSON の
    error エントリへそのまま転記する機械可読な短い理由コード（任意）。
    """

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason = reason


# state_dict そのものではなく wrapper dict の場合に探索する既知キー。
# `{"state_dict": {...}}` が最有力だが、`{"model": {...}}` 形式の checkpoint
# もある（修正1: docstring が謳う「tensor-like のときのみ state_dict とみなす」
# 判定の一部）。
_STATE_DICT_WRAPPER_KEYS = ("state_dict", "model")


def _sha256_of_file(path: Path) -> str:
    """ファイルをストリーム読み込みして sha256 を算出する（手打ちなし）。

    権限エラー・ディレクトリを渡された場合等の OSError 系は fail-closed で
    `CheckpointReadError` に包む（修正3）。
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        raise CheckpointReadError(
            f"checkpoint の読み込みに失敗しました: {path}: {exc}",
            reason="checkpoint_read_os_error",
        ) from exc
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


def _looks_like_tensor(value: Any) -> bool:
    """torch.Tensor らしさの軽量判定（`.numel()` / `.dtype` を両方持つ）。"""
    return hasattr(value, "numel") and hasattr(value, "dtype")


def _extract_state_dict(loaded: Any) -> Dict[str, Any]:
    """`torch.load()` の戻り値から state_dict を取り出す。

    DiffSinger 系 checkpoint は `{"state_dict": {...}, ...}` の dict、または
    state_dict そのものの 2 通りがあり得る。`"state_dict"` キーが無い dict は
    既知 wrapper キー（`_STATE_DICT_WRAPPER_KEYS`）を探索し、見つからなければ
    dict 全体を候補にする。どちらの場合も tensor-like value が 1 つも
    無ければ state_dict とはみなさず fail-closed で `CheckpointReadError` を
    送出する（修正1: 「全 value が tensor-like のときのみ state_dict とみなす」
    という docstring の主張を、epoch 等のメタデータが混在する実運用に合わせ
    「1 つ以上」へ具体化して実装）。
    """
    if not isinstance(loaded, dict):
        raise CheckpointReadError(
            "state_dict を持つ dict、または state_dict そのものの dict ではありません"
        )

    if "state_dict" in loaded and isinstance(loaded["state_dict"], dict):
        return loaded["state_dict"]

    candidate: Optional[Dict[str, Any]] = None
    for key in _STATE_DICT_WRAPPER_KEYS:
        value = loaded.get(key)
        if isinstance(value, dict):
            candidate = value
            break
    if candidate is None:
        candidate = loaded

    if not any(_looks_like_tensor(v) for v in candidate.values()):
        raise CheckpointReadError(
            "state_dict 候補に tensor-like な value が1つもありません "
            f"(候補キー数={len(candidate)}, 既知 wrapper キー "
            f"{_STATE_DICT_WRAPPER_KEYS} も見つかりませんでした)",
            reason="no_state_dict_candidate",
        )
    return candidate


def check_one_checkpoint(path: Path, torch_module: Any) -> Dict[str, Any]:
    """1 checkpoint を検査し、結果 dict を返す。読み込み失敗時は
    `CheckpointReadError` を投げる（fail-closed。呼び出し側が捕捉して
    error エントリへ変換する）。
    """
    if not path.exists():
        raise CheckpointReadError(f"checkpoint が存在しません: {path}")

    sha256 = _sha256_of_file(path)

    try:
        loaded = torch_module.load(str(path), map_location="cpu", weights_only=False)
    except TypeError:
        # 旧 torch は weights_only 引数を持たない。フォールバック経路自体の
        # 例外も fail-closed で CheckpointReadError に包む（修正3: 従来は
        # ここが素通しで、run() の except CheckpointReadError に捕捉されず
        # --out 未書き込みのままクラッシュし得た）。
        try:
            loaded = torch_module.load(str(path), map_location="cpu")
        except Exception as exc:  # noqa: BLE001 - fail-closed で理由を記録するため捕捉
            raise CheckpointReadError(
                f"torch.load（フォールバック経路）に失敗しました: {exc}",
                reason="torch_load_fallback_failed",
            ) from exc
    except Exception as exc:  # noqa: BLE001 - fail-closed で理由を記録するため捕捉
        raise CheckpointReadError(
            f"torch.load に失敗しました: {exc}", reason="torch_load_failed"
        ) from exc

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

    if n_tensors_checked == 0:
        # ゼロ検査の偽成功防止（修正1）: 1 tensor も検査していないのに
        # all_finite: true を騙って report しない。
        raise CheckpointReadError(
            "state_dict 内に検査可能な float tensor が1つもありませんでした "
            f"(skipped={n_tensors_skipped})",
            reason="no_tensors_inspected",
        )

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


def _error_entry(ckpt_path: Path, exc: CheckpointReadError) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "path": str(ckpt_path),
        "status": "error",
        "error": str(exc),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if exc.reason:
        entry["reason"] = exc.reason
    return entry


def _collides_with_inputs(
    out_path: Path, checkpoints: List[Path], pins_path: Optional[Path]
) -> Optional[Path]:
    """`out_path` の解決済みパスが、いずれかの入力パス（checkpoint 引数 /
    `--pins`）と一致するかを調べる。一致した入力パスを返す（無ければ
    `None`）。Codex bot レビュー #8: --out が入力を上書きしてしまう事故の
    防止（読み込み前に判定する）。"""
    out_resolved = out_path.resolve()
    for ckpt in checkpoints:
        if ckpt.resolve() == out_resolved:
            return ckpt
    if pins_path is not None and pins_path.resolve() == out_resolved:
        return pins_path
    return None


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """`path` と同一ディレクトリの一時ファイルへ書いてから `os.replace` で
    atomic に置き換える（Codex bot レビュー #8）。voice_genesis は src/ の
    `utils/atomic_io.py` を import しない契約（CLAUDE.md）のためローカルに
    最小実装する。失敗時は staging tempfile を best-effort で削除してから
    re-raise する。
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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
            "照合用の pin JSON（例: results_s3/run4_anchor_provenance.json のように"
            " checkpoint の sha256 を含む pin 表。run4_dataset_pins.json は"
            " dataset/wav の sha256 のみで checkpoint hash を含まないため例に"
            " 適さない）。任意。指定した場合、各 checkpoint の実測 sha256 が"
            " pin と一致するか照合し、不一致・pin 未検出はいずれも fail-closed で"
            " error 扱いになる（--pins 未指定時のみ照合をスキップできる）。"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="結果 JSON の出力先パス",
    )
    args = parser.parse_args(argv)

    collision = _collides_with_inputs(args.out, args.checkpoints, args.pins)
    if collision is not None:
        print(
            f"error: --out ({args.out}) が入力パス ({collision}) と衝突しています。"
            "入力の上書きを防ぐため何も書き込まず終了します。",
            file=sys.stderr,
        )
        return 1

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
        except CheckpointReadError as exc:
            any_error = True
            results.append(_error_entry(ckpt_path, exc))
            continue
        except Exception as exc:  # noqa: BLE001 - 最終防波堤: 未知の例外でも report を必ず書く
            any_error = True
            results.append(
                _error_entry(
                    ckpt_path,
                    CheckpointReadError(f"予期しないエラー: {exc}", reason="unexpected_error"),
                )
            )
            continue

        if pins is not None:
            try:
                match = _match_pin(ckpt_path, result["sha256"], pins)
            except Exception as exc:  # noqa: BLE001 - pin 照合自体の予期しない失敗も fail-closed
                any_error = True
                result["status"] = "error"
                result["reason"] = "pin_check_unexpected_error"
                result["error"] = f"pin 照合中に予期しないエラー: {exc}"
                results.append(result)
                continue

            result["pin_sha256_match"] = match
            if match is None:
                # --pins 指定時に pin が見つからない checkpoint も fail-closed。
                any_error = True
                result["status"] = "error"
                result["reason"] = "pin_not_found"
                result["error"] = (
                    f"--pins に {ckpt_path.name} に対応する sha256 が見つかりません"
                )
            elif match is False:
                any_error = True
                result["status"] = "error"
                result["reason"] = "pin_sha256_mismatch"
                result["error"] = "pin と実測 sha256 が一致しません"

        results.append(result)

    report = {
        "schema": "voicegenesis-checkpoint-finite-report/0.1",
        "torch_version": str(torch_module.__version__),
        "pin_verification": "requested" if pins is not None else "not_requested",
        "checkpoints": results,
        "all_ok": (not any_error) and all(r.get("all_finite", False) for r in results if r["status"] == "ok"),
    }

    _atomic_write_text(args.out, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    if any_error:
        return 1
    if not all(r.get("all_finite", False) for r in results):
        return 3
    return 0


def _match_pin(ckpt_path: Path, actual_sha256: str, pins: Dict[str, Any]) -> Optional[bool]:
    """pins JSON から checkpoint ファイル名に対応する sha256 を探し、一致を
    返す（見つからなければ `None` = 照合不能。呼び出し側 run() が fail-closed
    で error 扱いにする）。

    2 通りの pin 形式を許容する（修正4: `results_s3/run4_anchor_provenance.json`
    の実構造を確認して追加）:
    - 平map: `{filename: sha256_hex}`（従来からの軽量マッチ）
    - subfield: `{"file": <filename>, "sha256": <hex>}` を持つ dict
      （`run4_anchor_provenance.json` の `checkpoints.<label>` エントリが
      この形。`{"5K": {"file": "model_ckpt_steps_5000.ckpt", "sha256": "..."}}`）

    どちらの形も再帰的に走査する（pins の構造は運用次第で揺れうるため）。
    """

    def _search(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            if ckpt_path.name in node and isinstance(node[ckpt_path.name], str):
                return node[ckpt_path.name]
            file_field = node.get("file")
            sha_field = node.get("sha256")
            if (
                isinstance(file_field, str)
                and file_field == ckpt_path.name
                and isinstance(sha_field, str)
            ):
                return sha_field
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
