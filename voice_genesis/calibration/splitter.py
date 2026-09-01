"""row → split (CALIBRATION/SELECTION/HOLDOUT) の決定論的割当（設計正本 §7）。

アルゴリズム概要:

1. family ごとに、指定された stratum 因子タプルで行をグループ化する。
2. stratum 内で `HMAC-SHA256(split_secret, row_id)` の hex 昇順に並べ、
   largest-remainder 法で 50/25/25 の整数個数へ丸める。
3. family 全体で「family 合計が家族の largest-remainder 目標と厳密一致」する
   ことを保証する（不一致があれば決定的な最小 HMAC 行の付け替えで補正する）。
4. truth level / generator implementation / boundary class のカバレッジ制約
   （3 件以上存在する水準は各 split に最低 1 件）を検査し、違反があれば
   HMAC 順位最小のペアの決定的最小 swap で修復する。
5. 正本となる実現済み row→split 表 (`RealizedSplitMap`) を返す。`verify_split`
   はアルゴリズムを再実行し、既存の実現済み表と機械照合する検証器。

[UNDERSPEC-CAL-03] 設計正本 §7 は「stratum 内 largest-remainder」と「端数の
selection/holdout 偶奇交互配分」を述べるが、SEL と HOLD の理想個数
(`0.25*n` ずつ) は代数的に必ず同一の端数を持つため、「偶奇による交互配分」が
意味を持つのは stratum サイズ `n mod 4 == 2` の 1 unit のみである
（他の余り (0,1,3) では largest-remainder が一意に定まり曖昧さがない、後述の
`_stratum_split_counts` の导出を参照）。この 1 unit のみ、stratum 内で
HMAC 順位が最大の行（＝丸めの対象となる境界行）の HMAC hex 末尾ニブルの偶奇で
SEL/HOLD を決める。

[UNDERSPEC-CAL-04] 「family 合計の厳密一致」制約は、2 行を交換する
(swap) だけでは原理的に達成できない場合がある（pairwise swap は各 split の
件数を保存するため）。そのため family 合計の補正は 1 行の片道移動
(`reason="family_total"`) として実装し、真のカバレッジ swap
(`reason="coverage"`, 常に 2 行 1 組で記録) と区別する。いずれも
`SwapRecord` として実現済み表に記録され、`realized_sha` に含まれる。
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from voice_genesis.calibration.canonical import manifest_sha
from voice_genesis.calibration.vocab import Split

_COVERAGE_AXES: tuple[str, ...] = ("truth_level", "generator_impl", "boundary_class")


@dataclass(frozen=True)
class RowInput:
    """splitter への入力行。`stratum` は層別因子名→値のマッピング
    （呼び出し側が明示列挙した因子のみを含む想定）。"""

    row_id: str
    family: str
    stratum: Mapping[str, Any]
    truth_level: str | None = None
    generator_impl: str | None = None
    boundary_class: str | None = None


@dataclass(frozen=True)
class SwapRecord:
    """1 行の片道移動を記録する最小単位。真の 2 行交換 (`reason="coverage"`) は
    常に 2 件の `SwapRecord` が対で記録される。"""

    row_id: str
    from_split: Split
    to_split: Split
    reason: str  # "coverage" | "family_total"
    hmac_key: str
    detail: str


@dataclass(frozen=True)
class RealizedSplitMap:
    """正本となる実現済み row→split 表。"""

    stratum_factor_names: tuple[str, ...]
    assignment: Mapping[str, Split]
    swaps: tuple[SwapRecord, ...]
    realized_sha: str


def _hmac_hex(secret: bytes, message: str) -> str:
    return hmac_module.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _stratum_split_counts(n: int, tie_bit: int) -> dict[Split, int]:
    """largest-remainder 法で n を 50/25/25 の整数個数 (CAL, SEL, HOLD) へ丸める。

    ideal(SEL) == ideal(HOLD) == 0.25n は代数的に常に等しいため、端数
    (fractional remainder) も常に等しい。closed-form 導出 (n = 4q + r):
      r=0: (2q,   q,   q)    — 端数なし
      r=1: (2q+1, q,   q)    — CAL の端数 (.5) が SEL/HOLD の端数 (.25) より
                                大きいため CAL のみ +1
      r=2: (2q+1, q+?, q+?)  — CAL 端数 0、SEL=HOLD 端数 .5 (tie) が CAL の
                                端数より大きいため SEL/HOLD の片方のみ +1
                                (tie_bit で決定)
      r=3: (2q+1, q+1, q+1)  — SEL=HOLD 端数 .75 が上位 2 枠を占め両方 +1
                                (CAL 端数 .5 は選ばれない)
    """
    if n < 0:
        raise ValueError("_stratum_split_counts: n must be non-negative")
    q, r = divmod(n, 4)
    if r == 0:
        return {Split.CALIBRATION: 2 * q, Split.SELECTION: q, Split.HOLDOUT: q}
    if r == 1:
        return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q, Split.HOLDOUT: q}
    if r == 2:
        if tie_bit == 0:
            return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q + 1, Split.HOLDOUT: q}
        return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q, Split.HOLDOUT: q + 1}
    # r == 3
    return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q + 1, Split.HOLDOUT: q + 1}


def _axis_value(row: RowInput, axis: str) -> Any:
    return getattr(row, axis)


def _required_pairs(rows: Sequence[RowInput]) -> set[tuple[str, Any]]:
    counts: dict[tuple[str, Any], int] = {}
    for r in rows:
        for axis in _COVERAGE_AXES:
            v = _axis_value(r, axis)
            if v is None:
                continue
            counts[(axis, v)] = counts.get((axis, v), 0) + 1
    return {k for k, c in counts.items() if c >= 3}


def _coverage_violations(
    rows_by_id: Mapping[str, RowInput],
    assignment: Mapping[str, Split],
    required_pairs: set[tuple[str, Any]],
) -> list[tuple[str, Any, Split]]:
    violations: list[tuple[str, Any, Split]] = []
    for axis, value in sorted(required_pairs, key=lambda kv: (kv[0], str(kv[1]))):
        for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
            present = any(
                _axis_value(rows_by_id[rid], axis) == value
                for rid, s in assignment.items()
                if s == split
            )
            if not present:
                violations.append((axis, value, split))
    return violations


def _safe_to_remove(
    row_id: str,
    rows_by_id: Mapping[str, RowInput],
    assignment: Mapping[str, Split],
    split: Split,
    required_pairs: set[tuple[str, Any]],
) -> bool:
    row = rows_by_id[row_id]
    for axis in _COVERAGE_AXES:
        v = _axis_value(row, axis)
        if v is None or (axis, v) not in required_pairs:
            continue
        count_in_split = sum(
            1
            for rid, s in assignment.items()
            if s == split and _axis_value(rows_by_id[rid], axis) == v
        )
        if count_in_split <= 1:
            return False
    return True


def _repair_coverage(
    rows_by_id: Mapping[str, RowInput],
    assignment: dict[str, Split],
    secret: bytes,
    required_pairs: set[tuple[str, Any]],
) -> list[SwapRecord]:
    swaps: list[SwapRecord] = []
    guard = 0
    max_iters = len(assignment) * 3 + 10
    while True:
        violations = _coverage_violations(rows_by_id, assignment, required_pairs)
        if not violations:
            break
        guard += 1
        if guard > max_iters:
            raise RuntimeError(
                "splitter: coverage repair failed to converge "
                f"(remaining violations: {violations})"
            )
        axis, value, target_split = violations[0]
        donors = sorted(
            (
                rid
                for rid, s in assignment.items()
                if s != target_split and _axis_value(rows_by_id[rid], axis) == value
            ),
            key=lambda rid: _hmac_hex(secret, rid),
        )
        if not donors:
            raise RuntimeError(
                f"splitter: no donor row available for {axis}={value} -> {target_split}"
            )
        # [UNDERSPEC-CAL-06] 設計正本は donor 選択の安全性検査までは規定しない。
        # donor 自身の現在の split から抜いても、その split の他の required
        # pair 被覆を壊さない候補を優先する。この安全確認を怠ると、同一行が
        # 直前に別の違反を直した「唯一の担い手」として再度 donor に選ばれ、
        # 別の split へ移動して自分が直したばかりの被覆を壊し、次の周回で
        # また逆方向へ選ばれる…という振動が起きて repair が収束しない
        # (guard 上限で RuntimeError になる)。victim 側の `_safe_to_remove`
        # と対称的に donor 側にも同じ安全性検査を適用する。
        donor = next(
            (
                cand
                for cand in donors
                if _safe_to_remove(cand, rows_by_id, assignment, assignment[cand], required_pairs)
            ),
            None,
        )
        if donor is None:
            donor = donors[0]
        donor_split = assignment[donor]
        victims = sorted(
            (rid for rid, s in assignment.items() if s == target_split and rid != donor),
            key=lambda rid: _hmac_hex(secret, rid),
        )
        victim = next(
            (
                cand
                for cand in victims
                if _safe_to_remove(cand, rows_by_id, assignment, target_split, required_pairs)
            ),
            None,
        )
        if victim is None:
            victim = victims[0] if victims else None
        if victim is None:
            raise RuntimeError(f"splitter: no victim row available in {target_split}")

        assignment[donor], assignment[victim] = target_split, donor_split
        hk_a, hk_b = _hmac_hex(secret, donor), _hmac_hex(secret, victim)
        detail = f"{axis}={value}->{target_split.value}"
        swaps.append(
            SwapRecord(
                row_id=donor,
                from_split=donor_split,
                to_split=target_split,
                reason="coverage",
                hmac_key=hk_a,
                detail=detail,
            )
        )
        swaps.append(
            SwapRecord(
                row_id=victim,
                from_split=target_split,
                to_split=donor_split,
                reason="coverage",
                hmac_key=hk_b,
                detail=detail,
            )
        )
    return swaps


def _balance_family_totals(
    assignment: dict[str, Split],
    secret: bytes,
    family: str,
    targets: Mapping[Split, int],
) -> list[SwapRecord]:
    moves: list[SwapRecord] = []
    guard = 0
    max_iters = len(assignment) + 10
    while True:
        counts = {Split.CALIBRATION: 0, Split.SELECTION: 0, Split.HOLDOUT: 0}
        for s in assignment.values():
            counts[s] += 1
        diffs = {k: counts[k] - targets[k] for k in counts}
        excess = [k for k, v in diffs.items() if v > 0]
        deficit = [k for k, v in diffs.items() if v < 0]
        if not excess and not deficit:
            break
        guard += 1
        if guard > max_iters:
            raise RuntimeError("splitter: family total balancing failed to converge")
        excess_split = max(excess, key=lambda k: (diffs[k], k.value))
        deficit_split = min(deficit, key=lambda k: (diffs[k], k.value))
        candidates = sorted(
            (rid for rid, s in assignment.items() if s == excess_split),
            key=lambda rid: _hmac_hex(secret, rid),
        )
        chosen = candidates[0]
        assignment[chosen] = deficit_split
        moves.append(
            SwapRecord(
                row_id=chosen,
                from_split=excess_split,
                to_split=deficit_split,
                reason="family_total",
                hmac_key=_hmac_hex(secret, chosen),
                detail=f"family={family} balance",
            )
        )
    return moves


def _swap_to_dict(s: SwapRecord) -> dict[str, Any]:
    return {
        "row_id": s.row_id,
        "from_split": s.from_split.value,
        "to_split": s.to_split.value,
        "reason": s.reason,
        "hmac_key": s.hmac_key,
        "detail": s.detail,
    }


def realize_split(
    rows: Sequence[RowInput],
    split_secret: bytes,
    stratum_factor_names: Sequence[str],
) -> RealizedSplitMap:
    """rows を family ごとに 50/25/25 へ決定論的に割り当てる。"""
    rows_by_id: dict[str, RowInput] = {}
    for r in rows:
        if r.row_id in rows_by_id:
            raise ValueError(f"splitter: duplicate row_id {r.row_id!r}")
        rows_by_id[r.row_id] = r

    families = sorted({r.family for r in rows})
    assignment: dict[str, Split] = {}
    all_swaps: list[SwapRecord] = []

    for family in families:
        family_rows = [r for r in rows if r.family == family]
        n = len(family_rows)
        assignment_local: dict[str, Split] = {}

        strata: dict[tuple[Any, ...], list[RowInput]] = {}
        for r in family_rows:
            key = tuple(r.stratum.get(name) for name in stratum_factor_names)
            strata.setdefault(key, []).append(r)

        for key in sorted(strata.keys(), key=lambda k: [repr(x) for x in k]):
            stratum_rows = strata[key]
            stratum_sorted = sorted(
                stratum_rows, key=lambda r: _hmac_hex(split_secret, r.row_id)
            )
            m = len(stratum_sorted)
            tie_bit = int(_hmac_hex(split_secret, stratum_sorted[-1].row_id)[-1], 16) % 2
            counts = _stratum_split_counts(m, tie_bit)
            idx = 0
            for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
                cnt = counts[split]
                for r in stratum_sorted[idx : idx + cnt]:
                    assignment_local[r.row_id] = split
                idx += cnt

        # constraint (a): family totals must match the family-level largest-remainder target
        family_tie_bit = int(
            _hmac_hex(split_secret, f"{family}:family_total_tiebreak")[-1], 16
        ) % 2
        targets = _stratum_split_counts(n, family_tie_bit)
        all_swaps.extend(
            _balance_family_totals(assignment_local, split_secret, family, targets)
        )

        # constraint (b): coverage of truth_level / generator_impl / boundary_class
        required_pairs = _required_pairs(family_rows)
        all_swaps.extend(
            _repair_coverage(rows_by_id, assignment_local, split_secret, required_pairs)
        )

        assignment.update(assignment_local)

    payload = {
        "stratum_factor_names": list(stratum_factor_names),
        "assignment": {rid: assignment[rid].value for rid in sorted(assignment)},
        "swaps": [_swap_to_dict(s) for s in all_swaps],
    }
    realized_sha = manifest_sha(payload)
    return RealizedSplitMap(
        stratum_factor_names=tuple(stratum_factor_names),
        assignment=dict(assignment),
        swaps=tuple(all_swaps),
        realized_sha=realized_sha,
    )


def verify_split(
    rows: Sequence[RowInput], secret: bytes, realized: RealizedSplitMap
) -> bool:
    """アルゴリズムを再実行し、既存の実現済み表と機械照合する（設計正本 §7:
    正本は実現済み row→split 表、検証器が必須）。"""
    recomputed = realize_split(rows, secret, realized.stratum_factor_names)
    return (
        dict(recomputed.assignment) == dict(realized.assignment)
        and recomputed.swaps == realized.swaps
        and recomputed.realized_sha == realized.realized_sha
    )
