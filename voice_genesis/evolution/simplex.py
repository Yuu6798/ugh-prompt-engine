"""simplex.py — VG-E0: 単体幾何（DESIGN_VG_E0.md §2/§3）。

`normalize()`（丸め規約の一元化）/ `l1_distance()`（近縁度）/
`assign_lineage()`（系統帰属、機械決定・凍結）/ `cell_id()`・`all_cell_ids()`
（N=5 三角格子の niche グリッド、MAP-Elites の behavior 空間）。

sibling import は `voice_genesis/foundry/s1_gate/forge_triangle.py` が
`convert_d3` を読み込む流儀（`_THIS_DIR` を `sys.path` へ挿入してから
`import <sibling>`）を踏襲する — `voice_genesis/evolution/` はパッケージ
（`__init__.py`）を持たない設計にリポジトリ全体で揃えているため。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import FrozenSet, Mapping, Tuple, Union

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402

GRID_N = 5
LINEAGE_THRESHOLD = 0.55

# normalize()/cell_id() の丸め・格子演算はすべて「小数6桁固定表記」を
# 1/1,000,000 単位の整数（マイクロ単位）で厳密に扱う。浮動小数点の乗算誤差
# （例: 0.4*5 が 5 進の格子境界でわずかに滲む）を排除するための共通定数。
_MICRO_PER_UNIT = 1_000_000

CoordsLike = Union["models.Coords", Mapping[str, float]]


def _as_triple(coords: CoordsLike) -> Tuple[float, float, float]:
    if isinstance(coords, models.Coords):
        return coords.ritsu, coords.pjs, coords.user
    if isinstance(coords, Mapping):
        try:
            return float(coords["ritsu"]), float(coords["pjs"]), float(coords["user"])
        except KeyError as exc:
            raise ValueError(f"coords mapping missing key: {exc}") from exc
    raise TypeError(f"coords must be models.Coords or a mapping, got {type(coords).__name__}")


def _require_finite_triple(r: float, p: float, u: float) -> None:
    for name, v in (("ritsu", r), ("pjs", p), ("user", u)):
        if not math.isfinite(v):
            raise ValueError(f"non-finite coordinate rejected: {name}={v}")


def normalize(coords: CoordsLike) -> "models.Coords":
    """coords を Δ²（3頂点 ritsu/pjs/user、成分非負・合計1）上へ射影し、小数
    6桁へ丸め、合計が厳密に1.000000になるよう最大成分（クランプ前の生値
    基準）へ残差を吸収する（DESIGN_VG_E0.md §2「丸め規約」）。

    NaN/inf は即例外。負の生値は0へクランプする（オペレータの「単体内へ
    射影」— §4 drift の記述 — を含む全演算の出力に対する共通の射影経路
    として一元化する。丸め誤差・クランプによる質量欠損は、クランプ**前**の
    生値が最大の成分へ残差吸収する — これにより中心点等の同値タイでも
    ritsu > pjs > user の決定論的な優先順位で常に同じ成分が吸収先になる）。
    """
    r, p, u = _as_triple(coords)
    _require_finite_triple(r, p, u)

    raw = {"ritsu": r, "pjs": p, "user": u}
    clamped = {k: max(0.0, v) for k, v in raw.items()}
    micros = {k: int(round(v * _MICRO_PER_UNIT)) for k, v in clamped.items()}
    total = sum(micros.values())
    residual = _MICRO_PER_UNIT - total

    max_key = max(raw, key=lambda k: raw[k])
    micros[max_key] += residual
    if micros[max_key] < 0:
        raise ValueError(
            "residual absorption drove the dominant component negative "
            f"(raw={raw!r}); refusing to emit an invalid simplex point"
        )
    # micros はすべて非負 int のため `micros[k] / _MICRO_PER_UNIT` は理論上
    # -0.0 を生まないが、genome_id ハッシュ・格納・整形の全経路で -0.0 を
    # 恒久的に排除する防御として `normalize_signed_zero()` を最終防衛として
    # 通す（PR #267 Codex R8 指摘。models._validate_coords_value の
    # normalize=True 経路でも重ねて正規化されるが、単一責務の関数として
    # ここでも保証しておく）。
    return models.Coords(**{
        k: models.normalize_signed_zero(micros[k] / _MICRO_PER_UNIT) for k in ("ritsu", "pjs", "user")
    })


def l1_distance(a: CoordsLike, b: CoordsLike) -> float:
    """近縁度 = 重心座標の L1 距離（DESIGN_VG_E0.md §2）。近縁交配抑制の
    閾値 d_min は本書では凍結しない（design §2 参照）— 本関数は距離の計算
    のみを提供する。
    """
    ar, ap, au = _as_triple(a)
    br, bp, bu = _as_triple(b)
    _require_finite_triple(ar, ap, au)
    _require_finite_triple(br, bp, bu)
    return round(abs(ar - br) + abs(ap - bp) + abs(au - bu), 6)


def assign_lineage(coords: CoordsLike) -> str:
    """系統帰属（DESIGN_VG_E0.md §3.1、機械決定・凍結。0.55 は「単一ドナー
    主導と呼べる最小の優勢度」として凍結された値 — 変更は設計書の改訂 PR
    による）。

    本関数は座標のみからの機械決定（L-R/L-P/L-U/L-C）だけを返す。NOVELTY
    （novelty_jump 由来個体の1世代限定隔離・系統間 vertex_pull の隔離）は
    座標から導出できない文脈情報（オペレータ種別・親の系統）に依存するため
    本関数の範囲外 — 台帳/operators.py の責務（§3.1「NOVELTY隔離」注記）。
    """
    r, p, u = _as_triple(coords)
    _require_finite_triple(r, p, u)
    if r >= LINEAGE_THRESHOLD:
        return "L-R"
    if p >= LINEAGE_THRESHOLD:
        return "L-P"
    if u >= LINEAGE_THRESHOLD:
        return "L-U"
    return "L-C"


def cell_id(coords: CoordsLike, n: int = GRID_N) -> Tuple[int, int, int]:
    """N分割三角格子のセル id（DESIGN_VG_E0.md §3.2）:
    `(floor(r*N), floor(p*N), floor(u*N))`。

    整数マイクロ単位（1e-6刻み）で計算し、浮動小数点の乗算誤差による境界
    滲み（例: 0.4*5 が 5 進の境界でわずかに 1.9999... 側へ滲む）を排除する。

    各軸の raw index は floor 由来で [0, n] を取りうる（座標が丁度 v=1.0 の
    とき raw index = n）。3軸の raw index 合計は幾何的に必ず {n-2, n-1, n}
    のいずれかになる（証明: 各軸の余り = micro_i - idx_i*micro_per_cell は
    [0, micro_per_cell) に収まり、3軸の余り合計は
    `MICRO_PER_UNIT - micro_per_cell*sum(idx) = micro_per_cell*(n - sum(idx))`
    かつ [0, 3*micro_per_cell) の範囲に収まるため `n - sum(idx) ∈ {0,1,2}`）。

    合計が `n` になるのは3軸が同時に格子線上（余りが全て0）にあるとき
    （三角形の頂点、または格子の内部交点）に限られ、これは design §3.2
    「境界は辞書順で下側セルへ割当」が想定するケースである: 辞書順
    （coords のフィールド順 ritsu→pjs→user）で最初に見つかった非ゼロの軸を
    1 だけ減じ、合計を有効範囲 `n-1` へ落とす。全セル網羅・非重複は
    `tests/test_simplex.py` のプロパティテストで担保する。
    """
    if _MICRO_PER_UNIT % n != 0:
        raise ValueError(f"grid size n={n} must evenly divide {_MICRO_PER_UNIT} (6-decimal micro-unit domain)")
    micro_per_cell = _MICRO_PER_UNIT // n

    r, p, u = _as_triple(coords)
    _require_finite_triple(r, p, u)

    micro_r = int(round(r * _MICRO_PER_UNIT))
    micro_p = int(round(p * _MICRO_PER_UNIT))
    micro_u = int(round(u * _MICRO_PER_UNIT))

    idx = [micro_r // micro_per_cell, micro_p // micro_per_cell, micro_u // micro_per_cell]
    total = sum(idx)
    if total == n:
        for i in range(3):
            if idx[i] > 0:
                idx[i] -= 1
                break
        total = sum(idx)

    if total not in (n - 1, n - 2):
        raise ValueError(  # pragma: no cover - 幾何的不変条件（設計書の証明どおり到達不能）
            f"internal invariant violated: cell index sum {total} is outside the expected "
            f"{{n-2, n-1}} range for coords={coords!r} (idx={idx!r})"
        )
    return (idx[0], idx[1], idx[2])


def all_cell_ids(n: int = GRID_N) -> FrozenSet[Tuple[int, int, int]]:
    """N分割三角格子の到達可能な全セル id（N^2 個。DESIGN_VG_E0.md §3.2）。

    重心座標 (i,j,k)（i,j,k∈[0,n-1]）は `i+j+k∈{n-1, n-2}` のときのみ
    幾何的に到達可能（「上向き」小三角形が `i+j+k=n-1` で
    `C(n+1,2)=n(n+1)/2` 個、「下向き」が `i+j+k=n-2` で
    `C(n,2)=n(n-1)/2` 個。合計 `n(n+1)/2 + n(n-1)/2 = n^2` 個 — N=5 なら
    15+10=25 個）。
    """
    cells = set()
    for i in range(n):
        for j in range(n):
            for k in range(n):
                if i + j + k in (n - 1, n - 2):
                    cells.add((i, j, k))
    return frozenset(cells)
