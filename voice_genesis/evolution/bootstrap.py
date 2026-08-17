"""bootstrap.py — VG-E0: 創始個体の生成（DESIGN_VG_E0.md §7 AC 最終項目）。

創始3個体（3アンカー頂点 L-R/L-P/L-U）+ 中央1個体（重心 1/3,1/3,1/3、L-C）を
決定論的に生成し、台帳へ書き出す。

演奏 seed（`FOUNDER_SEED`）: 設計書 §1「Identity Freeze / Performance
Revision の分離」により、個体の Identity は coords が担い seed は
「同一 Identity の別演奏」を区別するためだけの軸である。創始個体の
Identity は3アンカー頂点と重心という coords 自体で既に一意に定まるため、
4個体とも同一の基準 seed（0）を割り当てる — 「創始個体は無演奏差分の基準
演奏」という設計判断（design が具体的な seed 値を凍結していないための
実装判断であり、design との矛盾ではない）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402
import simplex  # noqa: E402
from ledger import Ledger, LedgerConflictError  # noqa: E402

FOUNDER_SEED = 0

# (ラベル, raw coords) — raw は normalize() で6桁丸め・残差吸収を経る。
_FOUNDER_SPECS: Tuple[Tuple[str, dict], ...] = (
    ("ritsu_vertex", {"ritsu": 1.0, "pjs": 0.0, "user": 0.0}),
    ("pjs_vertex", {"ritsu": 0.0, "pjs": 1.0, "user": 0.0}),
    ("user_vertex", {"ritsu": 0.0, "pjs": 0.0, "user": 1.0}),
    ("center", {"ritsu": 1.0 / 3.0, "pjs": 1.0 / 3.0, "user": 1.0 / 3.0}),
)


def founder_genomes() -> Tuple[models.VoiceGenome, ...]:
    """創始4個体（3頂点 + 中央）を決定論的に構築する（台帳へは書かない）。"""
    genomes: List[models.VoiceGenome] = []
    for _label, raw_coords in _FOUNDER_SPECS:
        coords = simplex.normalize(raw_coords)
        lineage = simplex.assign_lineage(coords)
        genome = models.build_genome(
            coords=coords, seed=FOUNDER_SEED, lineage=lineage, generation=0,
            parents=(), operator="founder", operator_params={},
            anchors_provenance=None, notes="",
        )
        genomes.append(genome)
    return tuple(genomes)


def run_bootstrap(ledger_dir: Path) -> List[Path]:
    """創始4個体を `ledger_dir` へ決定論的に書き出す。既に同一内容で存在
    すれば冪等 no-op（`Ledger.write()` 参照）— 2回実行してもバイト同一。

    PR #267 Codex R11 指摘2（P2）, 2026-08-17 採用: `Ledger.write()` を単純に
    4回逐次呼ぶと、3/4件目が既存の衝突ファイル等で失敗した場合に先行分が
    published のまま例外送出され、部分 publish が恒久化していた。

    1. 事前検証: 4個体すべてを構築し、書き込み開始前に各 genome_id の
       既存ファイルの有無と内容一致を全数チェックする。全既存 + 全一致なら
       冪等成功として何も書かずに既存パスを返す。1件でも内容衝突があれば
       何も書かずに `LedgerConflictError` を送出する（append-only 台帳の
       意味論に揃える）。
    2. rollback: 事前検証を通過してもなお write 中に失敗した場合（並行書込み
       等の残余ケース）は、この実行で新規作成したファイルのみを unlink して
       実行前状態へ復元してから例外を再送出する（事前から存在したファイルは
       一切変更しない）。
    """
    ledger = Ledger(ledger_dir)
    genomes = founder_genomes()
    paths = [ledger.path_for(genome.genome_id) for genome in genomes]
    payloads = [(models.genome_to_json(genome) + "\n").encode("utf-8") for genome in genomes]

    conflicts: List[str] = []
    pre_existing: List[bool] = []
    for genome, path, payload in zip(genomes, paths, payloads):
        exists = path.exists()
        pre_existing.append(exists)
        if exists and path.read_bytes() != payload:
            conflicts.append(genome.genome_id)

    if conflicts:
        raise LedgerConflictError(
            "bootstrap pre-check: refusing to write any founder file — existing content conflicts "
            f"with founder genome(s) {conflicts!r} (append-only ledger — changes must go through a "
            "PR, not an overwrite; no file was written by this run)"
        )

    if all(pre_existing):
        return paths

    written_new: List[Path] = []
    try:
        result_paths: List[Path] = []
        for genome, was_pre_existing in zip(genomes, pre_existing):
            written_path = ledger.write(genome)
            if not was_pre_existing:
                written_new.append(written_path)
            result_paths.append(written_path)
        return result_paths
    except Exception:
        for p in written_new:
            try:
                p.unlink()
            except OSError:
                pass
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", required=True, help="創始個体 JSON の書き出し先ディレクトリ")
    return parser


def main(argv: List[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    paths = run_bootstrap(Path(args.ledger_dir))
    for genome, path in zip(founder_genomes(), paths):
        print(f"| {genome.lineage:8s} {genome.genome_id} {path}")


if __name__ == "__main__":
    main()
