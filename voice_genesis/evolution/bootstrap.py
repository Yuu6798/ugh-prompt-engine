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
from ledger import Ledger  # noqa: E402

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
    """
    ledger = Ledger(ledger_dir)
    return [ledger.write(genome) for genome in founder_genomes()]


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
