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

#: `truth_level == "TRUTH_CORE"` は他の coverage 軸と異なり、単なる存在保証
#: (>=1) では不十分（Codex レビュー 2026-09-01 P1 finding #3）: SELECTION/
#: HOLDOUT 側の truth-core 行が 1 件しかないと、`fixtures.controls.
#: positive_detection_instances()` の instance 数が `1 * PROBE_REPEATS(=5)`
#: にしかならず `N_pos>=10`（§10.1 要求）を割り込む。family 最小の truth
#: core 行数（12 件、F0_CONTROL）は 50/25/25 split の下で常に SELECTION/
#: HOLDOUT 側にも複数件を残す余地があるため（`CoverageRepairInfeasible` に
#: 陥るのは実 matrix では発生しない設計判断）、この 1 pair のみ下限 2 を課す。
_TRUTH_CORE_BLOCK_VALUE = "TRUTH_CORE"
_TRUTH_CORE_MIN_COUNT = 2


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
    """正本となる実現済み row→split 表。

    `pinned_holdout_row_ids`（v1.1 §V2.2 段 1: holdout sweep pinning）は
    `realize_split()` が段 1 で HOLDOUT へ確定的に事前割当てた行の集合を
    そのまま保持する（往復のため — `verify_split()` はこの値を読み戻して
    アルゴリズムへ渡し直す）。**意図的に `realized_sha` のハッシュ対象へは
    含めない**: v1.0 の既存 closed campaign（pin 機構自体が存在しなかった
    時代の `realized_split.json`）を読み戻して `verify_split()` にかけたとき、
    payload の形状が変わって sha が食い違う偽 tamper 検出を起こさないため
    （`assignment` 自体は従来どおりハッシュ対象であり、`pinned_holdout_
    row_ids` の値が実際の `assignment` と矛盾していれば `verify_split()` の
    再実行比較が別途検出する——`assignment` が唯一の正本という前提は崩さない）。
    """

    stratum_factor_names: tuple[str, ...]
    assignment: Mapping[str, Split]
    swaps: tuple[SwapRecord, ...]
    realized_sha: str
    pinned_holdout_row_ids: frozenset[str] = frozenset()


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


def _two_way_split_counts(n: int) -> dict[Split, int]:
    """v1.1 §V2.2 段 2: 段 1 の pin で HOLDOUT 枠が全量構成された stratum の
    残余（非 pin）行専用。largest-remainder 法で `n` を CALIBRATION:
    SELECTION = 2:1 の比で割当てる（HOLDOUT は常に 0）。

    closed-form 導出 (n = 3q + r): ideal(CAL) = 2n/3, ideal(SEL) = n/3 の
    端数は r=1 で (CAL 2/3, SEL 1/3) → CAL が大きい端数を取り +1、
    r=2 で (CAL 1/3, SEL 2/3) → SEL が大きい端数を取り +1。2/3 と 1/3 は
    代数的に等しくなり得ないため（`_stratum_split_counts` の r=2 と異なり）
    tie-break 機構は不要。
    """
    if n < 0:
        raise ValueError("_two_way_split_counts: n must be non-negative")
    q, r = divmod(n, 3)
    if r == 0:
        return {Split.CALIBRATION: 2 * q, Split.SELECTION: q, Split.HOLDOUT: 0}
    if r == 1:
        return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q, Split.HOLDOUT: 0}
    return {Split.CALIBRATION: 2 * q + 1, Split.SELECTION: q + 1, Split.HOLDOUT: 0}


def _axis_value(row: RowInput, axis: str) -> Any:
    return getattr(row, axis)


def _min_count_for_pair(axis: str, value: Any) -> int:
    """(axis, value) ペアの split 当たり被覆下限（通常は存在保証の 1。
    `truth_level="TRUTH_CORE"` のみ 2、finding #3 参照）。"""
    if axis == "truth_level" and value == _TRUTH_CORE_BLOCK_VALUE:
        return _TRUTH_CORE_MIN_COUNT
    return 1


def _required_pairs(rows: Sequence[RowInput]) -> dict[tuple[str, Any], int]:
    """coverage 制約対象の (axis, value) ペア → split 当たり被覆下限。

    通常のペアは `count>=3`（3 split 全てへ最低 1 件ずつ配れる可能性がある
    水準のみを対象とする、従来通り）を条件に下限 1 を要求する。
    `truth_level="TRUTH_CORE"` は下限 2 を要求するため、閾値も
    `3 * 下限`（=6）へ引き上げる（残り 2 split 双方に 2 件ずつ配れる余地が
    ない水準を対象外にする、との整合）。
    """
    counts: dict[tuple[str, Any], int] = {}
    for r in rows:
        for axis in _COVERAGE_AXES:
            v = _axis_value(r, axis)
            if v is None:
                continue
            counts[(axis, v)] = counts.get((axis, v), 0) + 1
    required: dict[tuple[str, Any], int] = {}
    for k, c in counts.items():
        axis, value = k
        min_count = _min_count_for_pair(axis, value)
        if c >= 3 * min_count:
            required[k] = min_count
    return required


def _pair_count(
    rows_by_id: Mapping[str, RowInput],
    assignment: Mapping[str, Split],
    axis: str,
    value: Any,
    split: Split,
) -> int:
    return sum(
        1
        for rid, s in assignment.items()
        if s == split and _axis_value(rows_by_id[rid], axis) == value
    )


def _coverage_violations(
    rows_by_id: Mapping[str, RowInput],
    assignment: Mapping[str, Split],
    required_pairs: Mapping[tuple[str, Any], int],
) -> list[tuple[str, Any, Split]]:
    violations: list[tuple[str, Any, Split]] = []
    for (axis, value), min_count in sorted(
        required_pairs.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))
    ):
        for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
            if _pair_count(rows_by_id, assignment, axis, value, split) < min_count:
                violations.append((axis, value, split))
    return violations


class CoverageRepairInfeasible(RuntimeError):
    """coverage 制約を満たす決定論的な (donor, victim) 組が 1 つも存在しない場合
    に送出する typed error（Codex レビュー 2026-09-01 採用）。

    `_repair_coverage` は候補を全数走査してから諦めるため、この例外が送出された
    時点で `assignment` は **一切変更されていない**（fail-closed。walk 済みの
    候補が全滅した違反を無理に「まだ安全確認していない donors[0]」へ強制移動
    して振動・誤修復するのを防ぐ）。キャンペーン層では既存語彙
    (`voice_genesis.calibration.vocab.BlockedCode`) の fail-closed コードへ
    マップされる想定であり、本例外自体は新規 vocab code を発行しない。

    v1.1 §V2.2 段 2 採用: `_balance_family_totals()` の家族合計補正が
    pin 行を不動としたまま・非 pin TRUTH_CORE 行を HOLDOUT へ入れないまま
    donor を 1 件も見つけられない場合も、`axis="family_total"` として同型で
    送出する（修復不能は既存 `CoverageRepairInfeasible` 同型で fail-closed
    する、という v1.1 の指示に従う）。

    `family`（縮退規則採用、2026-09-04 追補）: この違反が発生した family 名。
    `axis="family_total"` では従来から `value` が family 名そのものだったが、
    `axis` が通常の coverage 軸（`truth_level`/`generator_impl`/
    `boundary_class`）の場合は `value` はその軸の値であり family 名ではない
    ため、呼び出し側（`realize_split()`）が現在処理中の family を明示的に
    渡す。`fixtures.matrix` の holdout sweep pin 縮退リトライ
    （`c0_freeze._pin_and_realize_holdout()`）が「どの family の k_hold を
    下げて再選抜すべきか」を一意に決定するために使う。省略時は `None`
    （既存呼び出し・テストの後方互換を壊さない）。
    """

    def __init__(
        self,
        axis: str,
        value: Any,
        target_split: Split,
        detail: str,
        *,
        family: str | None = None,
    ) -> None:
        self.axis = axis
        self.value = value
        self.target_split = target_split
        self.detail = detail
        self.family = family if family is not None else (value if axis == "family_total" else None)
        super().__init__(
            f"splitter: no feasible donor/victim assignment for {axis}={value!r} "
            f"-> {target_split.value}: {detail}"
        )


def _repair_coverage(
    rows_by_id: Mapping[str, RowInput],
    assignment: dict[str, Split],
    secret: bytes,
    required_pairs: Mapping[tuple[str, Any], int],
    *,
    pinned_row_ids: frozenset[str] = frozenset(),
    hold_forbidden_row_ids: frozenset[str] = frozenset(),
    family: str | None = None,
) -> list[SwapRecord]:
    """v1.1 §V2.2 段 2 採用: `pinned_row_ids`（段 1 の pin 行。donor/victim
    いずれの候補からも常に除外し不動とする）と `hold_forbidden_row_ids`
    （非 pin TRUTH_CORE 行。`target_split == Split.HOLDOUT` の donor 候補
    からのみ除外——「pin 外の truth-core 行の holdout 割当は 0」を repair
    フェーズでも維持する）を新設。両集合とも既定は空 frozenset で、v1.0
    以来の呼び出し（pin 機構を使わない）はこれまでと完全に同一の挙動になる。
    `family`（縮退規則採用、2026-09-04 追補）は fail-closed 時に送出する
    `CoverageRepairInfeasible.family` へそのまま転記するためだけの引数
    （既定 `None` — 省略した既存呼び出しは挙動不変）。
    """
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
        current_violations = set(violations)
        target_count_before = _pair_count(rows_by_id, assignment, axis, value, target_split)
        donors = sorted(
            (
                rid
                for rid, s in assignment.items()
                if s != target_split
                and rid not in pinned_row_ids
                and not (target_split == Split.HOLDOUT and rid in hold_forbidden_row_ids)
                and _axis_value(rows_by_id[rid], axis) == value
            ),
            key=lambda rid: _hmac_hex(secret, rid),
        )

        # [UNDERSPEC-CAL-06] 設計正本は donor/victim 選択の安全性検査までは
        # 規定しない。donor 単独・victim 単独の局所的な安全確認
        # (`_safe_to_remove` 相当) では、両者の交互作用（donor が自分の元の
        # split から抜けた分を victim が偶然埋め合わせる/埋め合わせない）を
        # 見落とし、安全に見える手が実際には他の required pair 被覆を壊す
        # ケースを取りこぼす。さらに「安全な donor が 1 件も見つからなければ
        # donors[0] へ無条件フォールバック」する実装は、直前の周回で別の違反
        # を直した「唯一の担い手」を再び動かして自分の被覆を壊し、次の周回で
        # また逆方向に選ばれる…という振動を起こし得る（Codex レビュー
        # 2026-09-01 指摘）。
        #
        # 修正: (donor, victim) の組を HMAC 順で **同時に** 試し、実際に
        # swap した結果を `_coverage_violations` で再計算して確認する
        # （simulate-then-check）。得られる新しい違反集合が「元の違反集合の
        # 部分集合」かつ「対象ペアの split 内件数を実際に増やす（前進する）」
        # 場合のみ採用する（＝新しい被覆破壊を作らず、かつ着実に前進する）。
        # 下限が 1 の通常ペアでは「1 件増える」=「違反解消そのもの」なので
        # 従来の意味論と等価。下限 2 の `truth_level="TRUTH_CORE"`
        # （finding #3）では 1 回の swap で 0→1 までしか進まないことがあり、
        # その場合は violations に残ったまま while ループが次周回で同じ
        # 違反を再度拾い、2 件目の donor を探して 1→2 へ前進させる。この
        # 条件を満たす組が 1 つも見つからなければ、assignment を一切変更せず
        # `CoverageRepairInfeasible` で fail-closed する。候補数は有限
        # (donors x victims) であり、走査順は HMAC-rank に基づき決定的な
        # ので、同一入力からは常に同一の (donor, victim) が選ばれる。
        chosen: tuple[str, str] | None = None
        for cand_donor in donors:
            donor_split = assignment[cand_donor]
            victims = sorted(
                (
                    rid
                    for rid, s in assignment.items()
                    if s == target_split
                    and rid != cand_donor
                    and rid not in pinned_row_ids
                    # v1.1 §V2.2: the victim lands in `donor_split` (the
                    # donor's former split), so if `donor_split` is HOLDOUT
                    # (the donor was itself pulled *out of* HOLDOUT to
                    # satisfy `target_split`), a forbidden non-pinned
                    # TRUTH_CORE victim moving there would violate "pin 外の
                    # truth-core 行の holdout 割当は 0" just as surely as a
                    # forbidden donor moving straight into HOLDOUT would.
                    and not (donor_split == Split.HOLDOUT and rid in hold_forbidden_row_ids)
                ),
                key=lambda rid: _hmac_hex(secret, rid),
            )
            for cand_victim in victims:
                trial = dict(assignment)
                trial[cand_donor] = target_split
                trial[cand_victim] = donor_split
                trial_violations = _coverage_violations(rows_by_id, trial, required_pairs)
                trial_violations_set = set(trial_violations)
                target_count_after = _pair_count(rows_by_id, trial, axis, value, target_split)
                makes_progress = target_count_after > target_count_before
                introduces_nothing_new = trial_violations_set <= current_violations
                if makes_progress and introduces_nothing_new:
                    chosen = (cand_donor, cand_victim)
                    break
            if chosen is not None:
                break

        if chosen is None:
            raise CoverageRepairInfeasible(
                axis=axis,
                value=value,
                target_split=target_split,
                detail=(
                    f"no (donor, victim) pair among {len(donors)} donor candidate(s) "
                    "resolves this violation without breaking another coverage "
                    "constraint (assignment left unmodified)"
                ),
                family=family,
            )

        donor, victim = chosen
        donor_split = assignment[donor]
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
    *,
    pinned_row_ids: frozenset[str] = frozenset(),
    hold_forbidden_row_ids: frozenset[str] = frozenset(),
) -> list[SwapRecord]:
    """v1.1 §V2.2 段 2 採用: `pinned_row_ids`/`hold_forbidden_row_ids` は
    `_repair_coverage()` と同じ意味論（既定は空 frozenset、v1.0 の挙動を
    完全に保つ）。候補が 1 件も残らない場合は `CoverageRepairInfeasible`
    （`axis="family_total"`）で fail-closed する（`assignment` は候補探索の
    前に確定した `chosen` へ書込む直前まで一切変更しない）。
    """
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
            (
                rid
                for rid, s in assignment.items()
                if s == excess_split
                and rid not in pinned_row_ids
                and not (deficit_split == Split.HOLDOUT and rid in hold_forbidden_row_ids)
            ),
            key=lambda rid: _hmac_hex(secret, rid),
        )
        if not candidates:
            raise CoverageRepairInfeasible(
                axis="family_total",
                value=family,
                target_split=deficit_split,
                detail=(
                    f"no movable row available to balance family={family!r} "
                    f"{excess_split.value}->{deficit_split.value} without moving a "
                    "pinned holdout-sweep row (immovable) or placing a non-pinned "
                    "TRUTH_CORE row into HOLDOUT (forbidden by holdout sweep pinning)"
                ),
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
    *,
    pinned_holdout_row_ids: frozenset[str] = frozenset(),
) -> RealizedSplitMap:
    """rows を family ごとに 50/25/25 へ決定論的に割り当てる。

    v1.1 §V2.2 段 2 採用: `pinned_holdout_row_ids`（段 1 の holdout sweep
    pinning が確定した行集合。既定は空 frozenset — v1.0 以来の呼び出しは
    このパラメータを渡さず、以下の分岐は一切発火せず従来と完全に同一の
    3-way largest-remainder のみが動く）を含む stratum は:

    - `pinned_holdout_row_ids` に属する行を無条件で HOLDOUT へ確定する
      （HMAC 順位に関わらない）。
    - 残余（非 pin）行は CALIBRATION:SELECTION = 2:1 の largest-remainder
      のみで割当てる（`_two_way_split_counts`）。HOLDOUT 枠は pin 行のみで
      全量構成し、非 pin 行の HOLDOUT 割当は 0 のまま固定する
      （「TRUTH_CORE stratum の HOLDOUT 枠は段 1 の pin 行で全量を構成する」
      — pin される行は常に同一 family の TRUTH_CORE/PRIMARY 行のみに限られ、
      それらは常に単一 stratum に収まるため、stratum 単位で「pin 行を
      含むか」だけを見れば十分）。

    家族合計補正 (`_balance_family_totals`) と coverage 修復
    (`_repair_coverage`) は `pinned_holdout_row_ids`（不動）と、非 pin かつ
    `truth_level == "TRUTH_CORE"` の行（HOLDOUT への新規移動を禁止 —
    `hold_forbidden_row_ids`）を認識して動く。
    """
    rows_by_id: dict[str, RowInput] = {}
    for r in rows:
        if r.row_id in rows_by_id:
            raise ValueError(f"splitter: duplicate row_id {r.row_id!r}")
        rows_by_id[r.row_id] = r

    unknown_pins = pinned_holdout_row_ids - set(rows_by_id)
    if unknown_pins:
        raise ValueError(
            "splitter: pinned_holdout_row_ids references unknown row_id(s): "
            f"{sorted(unknown_pins)!r}"
        )

    families = sorted({r.family for r in rows})
    assignment: dict[str, Split] = {}
    all_swaps: list[SwapRecord] = []

    for family in families:
        family_rows = [r for r in rows if r.family == family]
        n = len(family_rows)
        assignment_local: dict[str, Split] = {}
        #: v1.1 §V2.2 段 2: 非 pin の TRUTH_CORE 行は HOLDOUT へ新規に移動して
        #: はならない——ただし「pin を実際に使った stratum」の非 pin 兄弟行
        #: のみが対象（pin 機構自体を使わない呼び出し (`pinned_holdout_
        #: row_ids=frozenset()`、v1.0 以来の全呼び出し) では、この集合は常に
        #: 空のまま従来の 3-way 割当を一切妨げない）。stratum ごとに判定
        #: するため、下の per-stratum ループの中で蓄積する。
        hold_forbidden_row_ids_local: set[str] = set()

        strata: dict[tuple[Any, ...], list[RowInput]] = {}
        for r in family_rows:
            key = tuple(r.stratum.get(name) for name in stratum_factor_names)
            strata.setdefault(key, []).append(r)

        for key in sorted(strata.keys(), key=lambda k: [repr(x) for x in k]):
            stratum_rows = strata[key]
            pinned_in_stratum = [
                r for r in stratum_rows if r.row_id in pinned_holdout_row_ids
            ]
            free_rows = [
                r for r in stratum_rows if r.row_id not in pinned_holdout_row_ids
            ]
            for r in pinned_in_stratum:
                assignment_local[r.row_id] = Split.HOLDOUT
            if pinned_in_stratum:
                hold_forbidden_row_ids_local.update(r.row_id for r in free_rows)
            free_sorted = sorted(free_rows, key=lambda r: _hmac_hex(split_secret, r.row_id))
            m = len(free_sorted)
            if pinned_in_stratum:
                counts = _two_way_split_counts(m)
            else:
                tie_bit = (
                    int(_hmac_hex(split_secret, free_sorted[-1].row_id)[-1], 16) % 2
                    if free_sorted
                    else 0
                )
                counts = _stratum_split_counts(m, tie_bit)
            idx = 0
            for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
                cnt = counts[split]
                for r in free_sorted[idx : idx + cnt]:
                    assignment_local[r.row_id] = split
                idx += cnt

        # constraint (a): family totals must match the family-level largest-remainder target
        family_tie_bit = int(
            _hmac_hex(split_secret, f"{family}:family_total_tiebreak")[-1], 16
        ) % 2
        targets = _stratum_split_counts(n, family_tie_bit)
        all_swaps.extend(
            _balance_family_totals(
                assignment_local,
                split_secret,
                family,
                targets,
                pinned_row_ids=pinned_holdout_row_ids,
                hold_forbidden_row_ids=frozenset(hold_forbidden_row_ids_local),
            )
        )

        # constraint (b): coverage of truth_level / generator_impl / boundary_class
        required_pairs = _required_pairs(family_rows)
        all_swaps.extend(
            _repair_coverage(
                rows_by_id,
                assignment_local,
                split_secret,
                required_pairs,
                pinned_row_ids=pinned_holdout_row_ids,
                hold_forbidden_row_ids=frozenset(hold_forbidden_row_ids_local),
                family=family,
            )
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
        pinned_holdout_row_ids=frozenset(pinned_holdout_row_ids),
    )


def verify_split(
    rows: Sequence[RowInput], secret: bytes, realized: RealizedSplitMap
) -> bool:
    """アルゴリズムを再実行し、既存の実現済み表と機械照合する（設計正本 §7:
    正本は実現済み row→split 表、検証器が必須）。v1.1: `realized.
    pinned_holdout_row_ids` を読み戻して同じ pin 集合でアルゴリズムを
    再実行する（`RealizedSplitMap` 自身が pin 入力を保持しているため
    呼び出し側は改めて渡す必要がない）。"""
    recomputed = realize_split(
        rows,
        secret,
        realized.stratum_factor_names,
        pinned_holdout_row_ids=realized.pinned_holdout_row_ids,
    )
    return (
        dict(recomputed.assignment) == dict(realized.assignment)
        and recomputed.swaps == realized.swaps
        and recomputed.realized_sha == realized.realized_sha
    )
