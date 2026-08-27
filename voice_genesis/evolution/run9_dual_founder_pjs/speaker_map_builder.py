"""speaker_map_builder.py — RUN9-L0-HARNESS-3a checkout-stable fixture:
speaker map 合成 embedding の repo-contained 再現ビルダー。

**背景（PR #328 Codex レビュー第1巡指摘1、P1、採用・Fable 確定方針）**:
HARNESS-3a の実測記録（`HARNESS3A_SPEAKER_MAP_RECORD.md`）が参照する
合成スクリプトと生成 embedding は session workdir（repo 外）にのみ存在し、
fresh checkout では `inputs/speaker_map_manifest.json` が pin する
出力 hash（`synthesized_embedding.sha256`、両 founder）を再構成・検証
できなかった。本モジュールはその合成ロジックを repo 内 checkout-stable
fixture として新設し、`builder_sha256` として manifest へ pin することで
「fresh checkout から実測を再現できる」契約を回復する
（`practice_split_builder.py` と同じ前例体裁: repo-contained ビルダー +
自己適用 validator + 単体テスト）。

**ロジック同一性**: 合成式・演算順序・dtype 処理は session workdir
`synth_speaker_map.py`（HARNESS-3a 実測に実際に使用したスクリプト）から
逐語移植した——`ritsu_vec`/`user_vec` = `np.frombuffer(bytes,
dtype=np.float32)`（再解釈のみ、値の変換・キャストなし）、`w_r`/`w_u` =
`np.float32(eval(expr, {"__builtins__": {}}))`、`synth = (w_r * ritsu_vec
+ w_u * user_vec)` の単一式・この順序で固定。L2正規化・摂動・ランダム
成分・重み調整は一切行わない（裁定「RUN9 User裁定 — AF0 runtime
mapping」逐語の禁止4項目、`inputs/speaker_map_manifest.json`
`synthesis_formula.prohibited` と同一）。

**重みの取得元**: 重み式（`w_ritsu_expr`/`w_user_expr`）は本 builder が
独自の定数として保持しない——pin 済み `inputs/speaker_map_manifest.json`
の `founders.<id>.renormalized_runtime_weights` からそのまま取得する。
単一の正本（manifest）から重みを取得することで、builder 側の重複定義・
将来の数値乖離リスクを構造的に排除する（`renormalized_runtime_weights`
自体は `run9_schema.validate_speaker_map_manifest()` が `coords_raw` から
の機械再導出一致を既に強制している——builder はその検証済みの値を
そのまま消費するだけで、独自に coords から再計算しない）。

**fail-closed 契約**:
  (i)  入力 ritsu/user emb の実測 sha256 を manifest の
       `input_embeddings.{ritsu,user}_emb_sha256` pin 値と照合する
       （不一致は拒否 — 別の emb を渡している/差し替えられている疑い）。
  (ii) 合成結果（384-dim float32 raw バイナリ）の実測 sha256 を manifest
       の `synthesized_embedding.sha256` pin 値と照合する（一致 = 再現
       成功、不一致 = fail-closed 非ゼロ終了）。
本 builder は `RUN9_CONTRACT.yaml` の `expected_speaker_map_sha` pin との
実バイト照合は行わない（それは `load_pinned_speaker_map_manifest()` の
責務——本 builder は checkout 直後の fixture 再現に特化した軽量ツールで
あり、contract/domain/rights_manifest の完全な取り回しは要求しない、
`practice_split_builder.py` と同じ責務分離）。

出力: 384-dim float32 raw バイナリ（`.tobytes()`、既存 emb と同形式）。
emb バイナリ自体は repo にコミットしない（rights 制約、session workdir /
`reexport_out/onnx_gate_40000/` 等のローカル入力に対してのみ実行する）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import run9_schema as m  # noqa: E402  (sibling import — repo-wide run9_* convention)

EMB_DIM = 384


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_local_speaker_map_manifest() -> Dict[str, Any]:
    """repo 内 `inputs/speaker_map_manifest.json` を読み、
    `run9_schema.validate_speaker_map_manifest()` で自己整合を検証してから
    返す（fail-closed — 壊れた/改竄された manifest からは合成しない）。
    """
    data = m._loads_strict_json(  # noqa: SLF001 - sibling module, see module docstring
        m.SPEAKER_MAP_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    m.validate_speaker_map_manifest(data)
    return data


def synthesize(
    founder_id: str,
    ritsu_emb_path: Path,
    user_emb_path: Path,
    *,
    manifest: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """`founder_id` 指定 + ritsu/user emb ファイルパスから合成 embedding を
    決定論的に再構築する純関数。`manifest` を省略すると
    `load_local_speaker_map_manifest()` で repo 内 pin 済み manifest を
    読む（テストは合成 manifest を明示的に渡せる）。

    戻り値は `(synth, report)`——`report` は入力/出力 sha256・使用した
    重み式・再現成否を含む機械可読レポート（`synth_speaker_map.py`
    workdir 原本のレポート形式を踏襲）。fail-closed 違反はすべて
    `run9_schema.Run9ValidationError` を送出する（部分出力を書かない）。
    """
    data = manifest if manifest is not None else load_local_speaker_map_manifest()
    if founder_id not in m.CONTRACT_FOUNDER_IDS:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): unknown founder_id {founder_id!r}, "
            f"expected one of {sorted(m.CONTRACT_FOUNDER_IDS)}"
        )
    founder = data["founders"][founder_id]

    ritsu_bytes = ritsu_emb_path.read_bytes()
    user_bytes = user_emb_path.read_bytes()
    ritsu_sha = _sha256_bytes(ritsu_bytes)
    user_sha = _sha256_bytes(user_bytes)

    expected_emb = founder["input_embeddings"]
    if ritsu_sha != expected_emb["ritsu_emb_sha256"]:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 入力 ritsu emb ({ritsu_emb_path}) "
            f"の実測 sha256 ({ritsu_sha!r}) が speaker_map_manifest.json の "
            f"input_embeddings.ritsu_emb_sha256 pin 値 ({expected_emb['ritsu_emb_sha256']!r}) と "
            "一致しない — fail-closed 拒否（別の emb を渡している/差し替えられている疑い）"
        )
    if user_sha != expected_emb["user_emb_sha256"]:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 入力 user emb ({user_emb_path}) "
            f"の実測 sha256 ({user_sha!r}) が speaker_map_manifest.json の "
            f"input_embeddings.user_emb_sha256 pin 値 ({expected_emb['user_emb_sha256']!r}) と "
            "一致しない — fail-closed 拒否（別の emb を渡している/差し替えられている疑い）"
        )

    ritsu_vec = np.frombuffer(ritsu_bytes, dtype=np.float32)
    user_vec = np.frombuffer(user_bytes, dtype=np.float32)
    if ritsu_vec.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): ritsu_vec shape {ritsu_vec.shape} != ({EMB_DIM},)"
        )
    if user_vec.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): user_vec shape {user_vec.shape} != ({EMB_DIM},)"
        )

    weights = founder["renormalized_runtime_weights"]
    # eval() は pin 済み manifest 由来の単純数式リテラル（'0.75' /
    # '1.0/3.0' 相当）のみに対して使う。__builtins__ を封鎖する（workdir
    # 原本 synth_speaker_map.py と同じ封鎖規約）。
    w_r = np.float32(eval(weights["w_ritsu_expr"], {"__builtins__": {}}))  # noqa: S307
    w_u = np.float32(eval(weights["w_user_expr"], {"__builtins__": {}}))  # noqa: S307

    # 裁定逐語の1式・この順序で固定（workdir 原本 synth_speaker_map.py と
    # 逐語同一）。L2正規化・摂動・ランダム成分・重み調整は行わない。
    synth = (w_r * ritsu_vec + w_u * user_vec)

    if synth.dtype != np.float32 or synth.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            "speaker_map_builder.synthesize(): synth dtype/shape invariant violated "
            f"(dtype={synth.dtype}, shape={synth.shape})"
        )
    if not bool(np.all(np.isfinite(synth))):
        raise m.Run9ValidationError(
            "speaker_map_builder.synthesize(): synth contains non-finite value(s)"
        )

    out_bytes = synth.tobytes()
    out_sha = _sha256_bytes(out_bytes)
    expected_sha = founder["synthesized_embedding"]["sha256"]
    report: Dict[str, Any] = {
        "founder_id": founder_id,
        "ritsu_emb_path": str(ritsu_emb_path),
        "user_emb_path": str(user_emb_path),
        "ritsu_emb_sha256": ritsu_sha,
        "user_emb_sha256": user_sha,
        "w_ritsu_expr": weights["w_ritsu_expr"],
        "w_user_expr": weights["w_user_expr"],
        "out_bytes_len": len(out_bytes),
        "out_sha256": out_sha,
        "expected_sha256_per_manifest": expected_sha,
        "reproduced": out_sha == expected_sha,
    }
    if out_sha != expected_sha:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 再構築した合成 embedding の sha256 "
            f"({out_sha!r}) が inputs/speaker_map_manifest.json の "
            f"founders.{founder_id}.synthesized_embedding.sha256 pin 値 ({expected_sha!r}) と "
            f"一致しない — fail-closed（再現失敗。report={report!r}）"
        )
    return synth, report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--founder", required=True, choices=sorted(m.CONTRACT_FOUNDER_IDS))
    ap.add_argument("--ritsu-emb", type=Path, required=True)
    ap.add_argument("--user-emb", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=None,
        help="合成 embedding の書き出し先（省略時は再現検証のみ行い、書き出さない）。",
    )
    args = ap.parse_args(argv)

    try:
        synth, report = synthesize(args.founder, args.ritsu_emb, args.user_emb)
    except m.Run9ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(synth.tobytes())
        report["out_path"] = str(args.out)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
