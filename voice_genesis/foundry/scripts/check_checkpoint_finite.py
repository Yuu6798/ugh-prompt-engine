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
- checkpoint は sha256 算出と torch.load のデシリアライズで同一バイト列を
  使う（Codex 第2巡 P1: TOCTOU）。`read_bytes()` で1回だけ読み、sha256は
  そのバッファから計算し、torch.load は `io.BytesIO(同バッファ)` から
  deserialize する。2回 read すると間でファイルが差し替わった場合に
  「旧バイトの hash × 新バイトの finite 判定」が対になり得た。
- `--expect` 指定時、渡された checkpoint 群の basename 集合（重複除去後）が
  期待集合と厳密一致しなければ fail-closed（Codex 第3巡 3-2）。checkpoint
  引数の重複指定（resolve 後同一パス）は `--expect` の有無に関わらず常に
  エラーにする。
- pin 照合は同一ファイル名に対する全マッチを収集し、矛盾する（distinct な）
  sha256 が2つ以上見つかれば `pin_conflict` として fail-closed にする
  （Codex 第3巡 3-3。従来は再帰探索の先勝ちで順序依存だった）。
- `--pins` 指定時、pin 照合は sha256 算出の直後・torch.load を呼ぶ**前**に
  行う（Codex 第4巡 4-1）。不一致・pin 未検出・pin_conflict のいずれも
  deserialize せず fail-closed にする。primary の torch.load は
  `weights_only=True`（安全側）を維持し、旧 torch の TypeError フォール
  バック経路（weights_only 非対応 = 事実上の unsafe pickle 実行）へは、
  pin 照合済み（または `--pins` 未指定）のバイト列に限って進む。
- `--pins` 指定時、report に入力 pin ファイル自体の同一性を記録する
  （Codex 第9巡 9-2: `pins_path` = 解決済みパス、`pins_sha256` = pin
  ファイル実バイトの sha256。未指定時はどちらも null）。どの pin ファイルで
  照合したかを report 自体から検証できるようにし、自作 pin ファイルによる
  偽証拠を排除する（debt_ledger.yaml VG-DEBT-007 の証拠要件が正準
  `results_s3/run4_anchor_provenance.json` の sha256 一致を要求する）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def check_one_checkpoint(
    path: Path, torch_module: Any, *, pins: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """1 checkpoint を検査し、結果 dict を返す。読み込み失敗時は
    `CheckpointReadError` を投げる（fail-closed。呼び出し側が捕捉して
    error エントリへ変換する）。

    TOCTOU 対策（Codex 第2巡 P1）: sha256 算出対象と torch.load で
    deserialize する対象が同一バイト列であることを保証するため、checkpoint
    は `read_bytes()` で1回だけ読み、(a) sha256 はそのバッファから計算し、
    (b) torch.load は `io.BytesIO(同バッファ)` から deserialize する
    （weights_only 非対応の旧 torch フォールバック経路も同様）。str パスを
    2回目以降 open し直すと、間でファイルが差し替わった場合に「旧バイトの
    hash × 新バイトの finite 判定」が対になり得た。

    pin 照合の順序（Codex 第4巡 4-1）: `pins` が渡された場合、sha256 算出の
    直後・torch.load を呼ぶ**前**に pin 照合する。不一致
    （reason="pin_sha256_mismatch"）・pin 未検出（reason="pin_not_found"）・
    矛盾する重複 pin（reason="pin_conflict"、`_match_pin` 自身が送出）の
    いずれも deserialize せず fail-closed で送出する。pin と一致しない
    （＝信頼できない）バイト列に対して torch.load
    （weights_only 非対応の旧 torch フォールバック経路は事実上の unsafe
    pickle 実行）が一切呼ばれないようにするため。`pins` が `None`
    （`--pins` 未指定）の場合は従来どおり pin 照合なしで deserialize する。
    primary の torch.load は `weights_only=True`（安全側）を維持する。
    """
    if not path.exists():
        raise CheckpointReadError(f"checkpoint が存在しません: {path}")

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CheckpointReadError(
            f"checkpoint の読み込みに失敗しました: {path}: {exc}",
            reason="checkpoint_read_os_error",
        ) from exc

    sha256 = hashlib.sha256(data).hexdigest()

    pin_sha256_match: Optional[bool] = None
    if pins is not None:
        # deserialize（torch.load）より前に pin 照合する（Codex 第4巡 4-1）。
        # `_match_pin` は矛盾する重複 pin を見つけた場合
        # CheckpointReadError(reason="pin_conflict") を自ら送出する
        # （呼び出し元まで素通しで伝播させる）。
        match = _match_pin(path, sha256, pins)
        if match is None:
            raise CheckpointReadError(
                f"--pins に {path.name} (sha256={sha256}) に対応する pin が見つかりません",
                reason="pin_not_found",
            )
        if match is False:
            raise CheckpointReadError(
                f"pin と実測 sha256 ({sha256}) が一致しません", reason="pin_sha256_mismatch"
            )
        pin_sha256_match = True

    try:
        try:
            loaded = torch_module.load(io.BytesIO(data), map_location="cpu", weights_only=True)
        except TypeError:
            # 旧 torch は weights_only 引数を持たない。フォールバック経路自体の
            # 例外も fail-closed で CheckpointReadError に包む（修正3: 従来は
            # ここが素通しで、run() の except CheckpointReadError に捕捉されず
            # --out 未書き込みのままクラッシュし得た）。同一バッファから新規
            # BytesIO を作り直す（先の呼び出しでストリーム位置が進んでいる
            # 可能性があるため使い回さない。ディスクは一切再読込しない）。
            # ここに到達するのは pins が None、または pin 照合を通過した
            # バイト列に限る（4-1）。
            try:
                loaded = torch_module.load(io.BytesIO(data), map_location="cpu")
            except Exception as exc:  # noqa: BLE001 - fail-closed で理由を記録するため捕捉
                raise CheckpointReadError(
                    f"torch.load（フォールバック経路）に失敗しました: {exc}",
                    reason="torch_load_fallback_failed",
                ) from exc
        except Exception as exc:  # noqa: BLE001 - fail-closed で理由を記録するため捕捉
            raise CheckpointReadError(
                f"torch.load に失敗しました: {exc}", reason="torch_load_failed"
            ) from exc
    finally:
        # 大きいバッファを速やかに解放できるよう明示的に参照を切る
        # （関数スコープ終了でも解放されるが、この後の tensor 走査が
        # 続くため早期に手放す）。
        del data

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

    result: Dict[str, Any] = {
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
    if pin_sha256_match is not None:
        result["pin_sha256_match"] = pin_sha256_match
    return result


def _load_pins(pins_path: Optional[Path]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """`--pins` ファイルを読み `(parsed pins dict, sha256)` を返す
    （未指定なら `(None, None)`）。sha256 は `read_bytes()` の同一バッファ
    から算出する（TOCTOU 対策 = 修正1/Codex 第2巡 4-1 と同じ思想: パース対象
    と sha256 算出対象を分離しない）。Codex 第9巡 9-2: この sha256 は report
    の `pins_sha256` にそのまま転記し、どの pin ファイルで照合したかを
    report 自体から検証できるようにする。"""
    if pins_path is None:
        return None, None
    if not pins_path.exists():
        raise CheckpointReadError(f"--pins で指定されたファイルが存在しません: {pins_path}")
    try:
        data = pins_path.read_bytes()
    except OSError as exc:
        raise CheckpointReadError(
            f"--pins ファイルの読み込みに失敗しました: {pins_path}: {exc}",
            reason="pins_read_os_error",
        ) from exc
    pins_sha256 = hashlib.sha256(data).hexdigest()
    pins = json.loads(data.decode("utf-8"))
    return pins, pins_sha256


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


def _compute_checked_set(checkpoints: List[Path]) -> List[str]:
    """渡された checkpoint 引数の basename 集合（重複除去・ソート済み）。"""
    return sorted({ckpt.name for ckpt in checkpoints})


def _detect_duplicate_basenames(checkpoints: List[Path]) -> List[str]:
    """resolve 後同一パスとなる checkpoint 引数の重複を basename で報告する
    （Codex 第3巡 3-2。`--expect` の有無に関わらず常にエラー扱いにする —
    同一ファイルを2回検査しても意味のある追加情報は増えず、コマンドラインの
    typo/コピペミスの兆候であることが多いため）。"""
    resolved_counts: Dict[Path, int] = {}
    for ckpt in checkpoints:
        resolved = ckpt.resolve()
        resolved_counts[resolved] = resolved_counts.get(resolved, 0) + 1
    return sorted({resolved.name for resolved, count in resolved_counts.items() if count > 1})


def _compute_expect_diff(
    checked_set: List[str], expect: Optional[List[str]]
) -> Tuple[Optional[List[str]], Optional[List[str]], Optional[List[str]]]:
    """`--expect` との厳密一致検証（Codex 第3巡 3-2）。
    `(expected_set, missing, extra)` を返す（`expect` が `None`
    = `--expect` 未指定の場合は3つとも `None`）。`missing` は期待したが
    渡されなかった checkpoint、`extra` は渡されたが期待されていなかった
    checkpoint（どちらも basename・重複除去・ソート済み）。
    """
    if expect is None:
        return None, None, None
    expected_set = sorted(set(expect))
    expected_as_set = set(expected_set)
    checked_as_set = set(checked_set)
    missing = sorted(expected_as_set - checked_as_set)
    extra = sorted(checked_as_set - expected_as_set)
    return expected_set, missing, extra


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
    parser.add_argument(
        "--expect",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "期待する checkpoint ファイル名の集合（複数指定可: --expect a.ckpt --expect"
            " b.ckpt）。指定時、渡された checkpoint 群の basename 集合（重複除去後）が"
            " 期待集合と厳密一致しなければ all_ok: false・exit 非ゼロになる"
            "（report に expected_set/checked_set/missing/extra を記録）。未指定時は"
            " 従来動作（report の expected_set は null）。checkpoint 引数の重複指定"
            "（resolve 後同一パス）は --expect の有無に関わらず常にエラーになる。"
        ),
    )
    args = parser.parse_args(argv)

    # --expect / 重複指定の検証（Codex 第3巡 3-2）。torch を要さないため
    # 早期に計算し、report には常に記録する（--expect 未指定時も
    # expected_set: null / duplicates: [] の形で記録し形状を安定させる）。
    checked_set = _compute_checked_set(args.checkpoints)
    duplicate_basenames = _detect_duplicate_basenames(args.checkpoints)
    expected_set, missing, extra = _compute_expect_diff(checked_set, args.expect)
    expect_mismatch = expected_set is not None and bool(missing or extra)
    setup_error = bool(duplicate_basenames) or expect_mismatch

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
    pins_sha256: Optional[str] = None
    try:
        pins, pins_sha256 = _load_pins(args.pins)
    except CheckpointReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pins_path_str = str(args.pins.resolve()) if args.pins is not None else None

    results: List[Dict[str, Any]] = []
    any_error = setup_error
    for ckpt_path in args.checkpoints:
        try:
            # pin 照合（--pins 指定時）は check_one_checkpoint 内部で
            # torch.load の前に行われる（Codex 第4巡 4-1）。不一致・
            # pin 未検出・pin_conflict はすべて CheckpointReadError として
            # ここに伝播し、下の except 節が error エントリへ変換する
            # （run() 側での後付け pin 照合は行わない — deserialize 前に
            # 弾くのが目的のため）。
            result = check_one_checkpoint(ckpt_path, torch_module, pins=pins)
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

        results.append(result)

    report = {
        "schema": "voicegenesis-checkpoint-finite-report/0.1",
        "torch_version": str(torch_module.__version__),
        "pin_verification": "requested" if pins is not None else "not_requested",
        "pins_path": pins_path_str,
        "pins_sha256": pins_sha256,
        "expected_set": expected_set,
        "checked_set": checked_set,
        "missing": missing,
        "extra": extra,
        "duplicates": duplicate_basenames,
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

    pin_conflict 検出（Codex 第3巡 3-3）: 同一ファイル名に対する全マッチを
    収集し、distinct な sha256 が2つ以上見つかれば `CheckpointReadError`
    （reason="pin_conflict"）を送出する。従来は再帰探索の「先勝ち」で
    どちらの値を採用するかが走査順序に依存していた（矛盾する pin エントリ
    が黙って片方だけ使われ得た）。distinct な値が1つだけなら（同一 hash が
    複数箇所に重複記載されていても）通常どおり照合する。
    """

    def _collect(node: Any, found: List[str]) -> None:
        if isinstance(node, dict):
            if ckpt_path.name in node and isinstance(node[ckpt_path.name], str):
                found.append(node[ckpt_path.name])
            file_field = node.get("file")
            sha_field = node.get("sha256")
            if (
                isinstance(file_field, str)
                and file_field == ckpt_path.name
                and isinstance(sha_field, str)
            ):
                found.append(sha_field)
            for value in node.values():
                _collect(value, found)

    found: List[str] = []
    _collect(pins, found)
    distinct_sha256 = sorted(set(found))

    if not distinct_sha256:
        return None
    if len(distinct_sha256) > 1:
        raise CheckpointReadError(
            f"--pins 内に {ckpt_path.name} に対する矛盾する sha256 が複数見つかりました: "
            f"{distinct_sha256}",
            reason="pin_conflict",
        )
    return distinct_sha256[0] == actual_sha256


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
