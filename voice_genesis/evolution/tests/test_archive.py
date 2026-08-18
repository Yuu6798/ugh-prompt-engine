"""test_archive.py — VG-E0 MAP-Elites 型 Archive（`archive.py`）のテスト。

DESIGN_VG_E0.md §7 AC「Archive の elite 更新・保護スロット・追い出し記録の
テスト」を直接検証する。

PR #267 Codex R14 指摘（P1）, 2026-08-18 採用: `Archive` は「台帳掲載済みの
個体のみを受理する」構造不変量を持つため（archive.py 参照）、`Archive()`
は `ledger.Ledger` を必須引数に取る。本ファイルの各テストは `ledger`
fixture（`tmp_path` 配下の使い捨て台帳）で `Archive` を構築し、`submit()`
に渡す genome は事前に `ledger.write()` で publish してから使う（親が
必要なオペレータ出力は親を先に publish する）。
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import archive as archive_mod  # noqa: E402
import bootstrap  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import models  # noqa: E402
import operators  # noqa: E402
import simplex  # noqa: E402


@pytest.fixture()
def founders():
    return bootstrap.founder_genomes()


@pytest.fixture()
def ledger(tmp_path):
    """使い捨ての台帳（`tmp_path` 配下）。founder は bootstrap 経由ではなく
    `ledger.write()` への直接 write で publish する（テストの意図を
    genome 単位で明示するため）。
    """
    return ledger_mod.Ledger(tmp_path / "ledger")


def _publish(lgr: ledger_mod.Ledger, *genomes: models.VoiceGenome) -> None:
    """`genomes` を宣言順に台帳へ publish するテストヘルパー（非 founder の
    親は呼び出し側が先に publish 済みであることが前提 — `Ledger.write()`
    の親存在検証）。
    """
    for g in genomes:
        lgr.write(g)


def test_submit_empty_cell_becomes_elite_no_eviction(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    result = arc.submit(ritsu, 0.8, quality_floor=0.5)
    assert result == "elite"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == ritsu.genome_id
    assert arc.eviction_log == []


def test_submit_higher_quality_evicts_previous_elite(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.5, quality_floor=0.3)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    _publish(ledger, challenger)
    assert simplex.cell_id(challenger.coords) == simplex.cell_id(ritsu.coords)
    result = arc.submit(challenger, 0.9, quality_floor=0.3)
    assert result == "elite"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == challenger.genome_id
    assert len(arc.eviction_log) == 1
    ev = arc.eviction_log[0]
    assert ev.slot == "elite"
    assert ev.evicted_genome_id == ritsu.genome_id
    assert ev.incoming_genome_id == challenger.genome_id
    assert ev.cell == cell


def test_submit_lower_quality_elite_rejected_no_eviction(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.9, quality_floor=0.3)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    _publish(ledger, challenger)
    result = arc.submit(challenger, 0.4, quality_floor=0.3)
    assert result == "rejected"
    assert arc.eviction_log == []
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == ritsu.genome_id


def test_submit_below_floor_lineage_unique_goes_to_protected(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    # ritsu (L-R) は elite が無いので below-floor でも lineage-unique →保護スロット
    result = arc.submit(ritsu, 0.1, quality_floor=0.5)
    assert result == "protected"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.protected_at(cell).genome_id == ritsu.genome_id
    assert arc.elite_at(cell) is None


def test_submit_below_floor_non_unique_lineage_rejected(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.9, quality_floor=0.5)  # L-R の elite を確立
    weak_sibling = operators.drift(ritsu, rng_seed=2, step=0.01)
    _publish(ledger, weak_sibling)
    assert weak_sibling.lineage == "L-R"
    result = arc.submit(weak_sibling, 0.1, quality_floor=0.5)
    assert result == "rejected"


def test_protected_slot_replacement_records_eviction(founders, ledger) -> None:
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    weak_a = operators.drift(ritsu, rng_seed=1, step=0.005)
    weak_b = operators.drift(ritsu, rng_seed=2, step=0.005)
    _publish(ledger, weak_a, weak_b)
    assert simplex.cell_id(weak_a.coords) == simplex.cell_id(weak_b.coords) == simplex.cell_id(ritsu.coords)
    arc.submit(weak_a, 0.1, quality_floor=0.9)
    result = arc.submit(weak_b, 0.2, quality_floor=0.9)
    assert result == "protected"
    assert len(arc.eviction_log) == 1
    ev = arc.eviction_log[0]
    assert ev.slot == "protected"
    assert ev.evicted_genome_id == weak_a.genome_id
    assert ev.incoming_genome_id == weak_b.genome_id


# --- PR #267 Codex R5 指摘2（P2）: 保護スロットの stale 占有 ----------------


def test_elite_acceptance_evicts_stale_protected_slot_of_same_lineage(founders, ledger) -> None:
    """L-R の保護個体が cell A のスロットを占有した後、L-R の elite が別の
    cell B に受理されると、L-R はもはや「唯一」ではないため cell A の
    保護個体は evict される（追い出しイベント記録付き）。evict 後は、
    それまで quality 比較で弾かれていた未代表系統（NOVELTY）の個体が
    より低品質でも cell A の保護スロットへ入れる。

    NOVELTY 個体は `pjs` founder から実際に `operators.novelty_jump()`
    （rng_seed=2）で導出した正規個体を使う（PR #267 Codex R14 修正後、
    Archive は台帳掲載済みの個体しか受理しない。cell A は ritsu 頂点付近
    の角セルで単一セル内の最大 L1 距離が `NOVELTY_JUMP_MIN_L1`=0.4 を
    構造的に下回るため、protected_occupant 自身を親にした「同一座標
    novelty_jump 子」は — 修正前に本テストが直接 build_genome() で構築
    していたもの — 実際には `operators.novelty_jump()` から到達不可能な
    偽装子だった。rng_seed=2 は cell A へ着地することを事前計算で確認
    済み — この事前計算自体が本 fix の核心である「宣言された operator が
    実際に親からその座標を生めるか」を体現する）。
    """
    _, pjs, _, _ = founders
    _publish(ledger, pjs)
    arc = archive_mod.Archive(ledger)

    # cell A の L-R 保護個体（elite が無いので below-floor でも保護スロットへ）。
    protected_occupant = models.build_genome(
        coords=models.Coords(0.9, 0.05, 0.05), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, protected_occupant)
    cell_a = simplex.cell_id(protected_occupant.coords)
    result = arc.submit(protected_occupant, 0.2, quality_floor=0.9)
    assert result == "protected"
    assert arc.protected_at(cell_a).genome_id == protected_occupant.genome_id

    # pjs から実際に novelty_jump() で導出した cell A 着地個体。まだ
    # protected_occupant (0.2) に quality で劣るため rejected（修正前後で
    # 不変の前提条件）。
    novelty_challenger = operators.novelty_jump(pjs, rng_seed=2)
    assert simplex.cell_id(novelty_challenger.coords) == cell_a
    _publish(ledger, novelty_challenger)
    assert arc.submit(novelty_challenger, 0.1, quality_floor=0.9) == "rejected"

    # 別 cell (B) で L-R の elite が受理される（quality >= floor）。
    lr_elite = models.build_genome(
        coords=models.Coords(0.6, 0.3, 0.1), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, lr_elite)
    cell_b = simplex.cell_id(lr_elite.coords)
    assert cell_b != cell_a
    result = arc.submit(lr_elite, 0.95, quality_floor=0.9)
    assert result == "elite"
    assert arc.elite_at(cell_b).genome_id == lr_elite.genome_id

    # cell A の L-R 保護個体は evict され、追い出しイベントが記録されている。
    assert arc.protected_at(cell_a) is None
    stale_evictions = [
        ev for ev in arc.eviction_log
        if ev.reason == "lineage_no_longer_unique" and ev.evicted_genome_id == protected_occupant.genome_id
    ]
    assert len(stale_evictions) == 1
    ev = stale_evictions[0]
    assert ev.slot == "protected"
    assert ev.cell == cell_a
    assert ev.incoming_genome_id == lr_elite.genome_id

    # 以後、NOVELTY 個体はより低品質のままでも cell A の保護スロットに入れる。
    result = arc.submit(novelty_challenger, 0.1, quality_floor=0.9)
    assert result == "protected"
    assert arc.protected_at(cell_a).genome_id == novelty_challenger.genome_id


# --- PR #267 Codex R6 指摘2（P2）: above-floor 敗退候補の保護スロット素通り ---


def test_above_floor_loser_falls_through_to_protected_slot(ledger) -> None:
    """Codex R6 指摘2実測: `LINEAGE_THRESHOLD`(0.55)は grid セル境界(0.2刻み)
    と揃っていないため、同一セル (r-index 2 = r∈[0.4,0.6)) の中に L-R
    (r=0.58>=0.55) と L-C (r=0.5<0.55) の両方の座標が存在しうる。この同一
    セルで L-R elite (q=0.9) が既に確立している状態で未代表 L-C 候補
    (q=0.8, floor=0.5) を提出すると、above-floor だが既存 elite に劣る
    という理由だけで即 "rejected" されていた（保護スロット判定への
    フォールスルーが無かった）。L-C は Archive 全体のどの elite にも代表
    されていない（唯一の系統）ため、above-floor の敗北者でも保護スロットの
    対象になるべき — 修正後は "protected" として受理される。
    """
    lr_elite = models.build_genome(
        coords=models.Coords(0.58, 0.21, 0.21), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    lc_candidate = models.build_genome(
        coords=models.Coords(0.5, 0.3, 0.2), seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, lr_elite, lc_candidate)
    cell = simplex.cell_id(lr_elite.coords)
    assert simplex.cell_id(lc_candidate.coords) == cell  # 同一セルであることが前提条件

    arc = archive_mod.Archive(ledger)
    assert arc.submit(lr_elite, 0.9, quality_floor=0.5) == "elite"

    result = arc.submit(lc_candidate, 0.8, quality_floor=0.5)
    assert result == "protected"
    assert arc.protected_at(cell).genome_id == lc_candidate.genome_id
    assert arc.elite_at(cell).genome_id == lr_elite.genome_id  # elite は上書きされない


def test_above_floor_loser_with_represented_lineage_still_rejected(founders, ledger) -> None:
    """above-floor 敗退のフォールスルーは「その lineage が elite として
    一切代表されていない」場合のみ保護スロットへ入る。lineage が既に
    elite で代表済みなら、above-floor 敗退はやはり "rejected"（保護スロット
    のフォールスルーが無条件の救済にならないことの回帰確認）。
    """
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.9, quality_floor=0.5)  # L-R の elite を確立(同一セル)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    _publish(ledger, challenger)
    assert challenger.lineage == "L-R"
    assert simplex.cell_id(challenger.coords) == simplex.cell_id(ritsu.coords)
    result = arc.submit(challenger, 0.6, quality_floor=0.5)  # above floor だが劣る
    assert result == "rejected"


# --- PR #267 Codex R7 指摘2（P2）: elite 追放時の系統保護の投入順依存 -------


def test_elite_eviction_routes_orphaned_lineage_to_protection_regardless_of_submit_order(ledger) -> None:
    """報告の実例: L-R (0.58,0.21,0.21) q=0.8 → L-C (0.5,0.3,0.2) q=0.9 の順と
    逆順で、最終状態が「L-C elite + L-R 保護」に一致する（投入順非依存）。

    順1（L-R 先→L-C 後）: L-R がまず elite になり、後から来た高品質な
    別系統 L-C が elite を奪う。追放された L-R はどの elite にも代表され
    なくなるため、保護スロット資格判定へルーティングされる（本 fix の
    対象経路）。
    順2（L-C 先→L-R 後）: L-C が先に elite になるため、後から来た L-R は
    quality で L-C に劣り即座に elite を奪えない。above-floor 敗退の
    フォールスルー（PR #267 Codex R6 指摘2、既存経路）で保護スロットへ入る。
    どちらの経路を通っても最終状態は同一でなければならない。
    """
    lr_coords = models.Coords(0.58, 0.21, 0.21)
    lc_coords = models.Coords(0.5, 0.3, 0.2)
    cell = simplex.cell_id(lr_coords)
    assert simplex.cell_id(lc_coords) == cell  # 同一セルであることが前提条件

    lr = models.build_genome(
        coords=lr_coords, seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    lc = models.build_genome(
        coords=lc_coords, seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    assert lr.lineage == "L-R"
    assert lc.lineage == "L-C"
    _publish(ledger, lr, lc)

    # 順1: L-R 先 → L-C 後。
    arc_order1 = archive_mod.Archive(ledger)
    assert arc_order1.submit(lr, 0.8, quality_floor=0.5) == "elite"
    result = arc_order1.submit(lc, 0.9, quality_floor=0.5)
    assert result == "elite"
    assert arc_order1.elite_at(cell).genome_id == lc.genome_id
    assert arc_order1.protected_at(cell) is not None
    assert arc_order1.protected_at(cell).genome_id == lr.genome_id

    # 順2: L-C 先 → L-R 後。
    arc_order2 = archive_mod.Archive(ledger)
    assert arc_order2.submit(lc, 0.9, quality_floor=0.5) == "elite"
    result = arc_order2.submit(lr, 0.8, quality_floor=0.5)
    assert result == "protected"
    assert arc_order2.elite_at(cell).genome_id == lc.genome_id
    assert arc_order2.protected_at(cell) is not None
    assert arc_order2.protected_at(cell).genome_id == lr.genome_id

    # 投入順に関わらず最終状態は一致する。
    assert arc_order1.elite_at(cell).genome_id == arc_order2.elite_at(cell).genome_id
    assert arc_order1.protected_at(cell).genome_id == arc_order2.protected_at(cell).genome_id


def test_elite_eviction_does_not_protect_same_lineage_replacement(founders, ledger) -> None:
    """elite が同一 lineage 内のより高品質な個体に置換された場合（通常の
    「より良い演奏への更新」）は、追放された個体の lineage が新 elite 自身
    によって引き続き代表されているため、保護スロットへはルーティングされ
    ない（回帰確認 — `test_submit_higher_quality_evicts_previous_elite` の
    前提と矛盾しないことの確認）。
    """
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.5, quality_floor=0.3)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    _publish(ledger, challenger)
    assert challenger.lineage == ritsu.lineage
    cell = simplex.cell_id(ritsu.coords)
    result = arc.submit(challenger, 0.9, quality_floor=0.3)
    assert result == "elite"
    assert arc.elite_at(cell).genome_id == challenger.genome_id
    assert arc.protected_at(cell) is None


def test_elite_eviction_protection_interacts_correctly_with_stale_protected_evict(ledger) -> None:
    """R5 修正（elite 受理で同系統保護スロットを evict する）と本 fix
    （追放された elite を別系統として保護スロットへルーティングする）が
    同一 `submit()` 呼び出し内で共存しても矛盾しないことを確認する:
    evict の対象は「新 elite と同一 lineage」の保護スロットのみであり、
    追放された旧 elite（新 elite とは別系統）の保護格納とは競合しない。
    """
    lr_coords = models.Coords(0.58, 0.21, 0.21)
    lc_coords = models.Coords(0.5, 0.3, 0.2)
    cell = simplex.cell_id(lr_coords)
    assert simplex.cell_id(lc_coords) == cell

    arc = archive_mod.Archive(ledger)

    # 別セルに L-C の保護個体を先に確立する（L-C の elite がまだ無いため
    # below-floor でも lineage-unique → 保護スロットへ）。
    stale_lc_coords = simplex.normalize({"ritsu": 0.2, "pjs": 0.4, "user": 0.4})
    stale_lc_protected = models.build_genome(
        coords=stale_lc_coords, seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, stale_lc_protected)
    stale_cell = simplex.cell_id(stale_lc_protected.coords)
    assert stale_cell != cell
    assert arc.submit(stale_lc_protected, 0.1, quality_floor=0.9) == "protected"

    # cell で L-R が先に elite になる。
    lr = models.build_genome(
        coords=lr_coords, seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, lr)
    assert arc.submit(lr, 0.8, quality_floor=0.5) == "elite"

    # L-C の高品質個体が cell で elite を奪う: (a) L-R は別系統として cell の
    # 保護スロットへ、(b) 別セルの stale L-C 保護個体は R5 の evict 対象。
    lc = models.build_genome(
        coords=lc_coords, seed=0, lineage="L-C", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    _publish(ledger, lc)
    result = arc.submit(lc, 0.9, quality_floor=0.5)
    assert result == "elite"

    assert arc.elite_at(cell).genome_id == lc.genome_id
    assert arc.protected_at(cell) is not None
    assert arc.protected_at(cell).genome_id == lr.genome_id
    assert arc.protected_at(stale_cell) is None  # R5 evict により消滅

    # cell の保護スロットは空きだったため lr の格納は「追い出し」ではなく初回
    # 充填（reason 付きイベントは記録されない）。elite スロットの置換
    # （higher_quality_elite）は記録される。
    reasons_at_cell = {ev.reason for ev in arc.eviction_log if ev.cell == cell}
    assert reasons_at_cell == {"higher_quality_elite"}
    stale_evictions = [
        ev for ev in arc.eviction_log
        if ev.reason == "lineage_no_longer_unique" and ev.evicted_genome_id == stale_lc_protected.genome_id
    ]
    assert len(stale_evictions) == 1


def test_occupancy_counts(founders, ledger) -> None:
    ritsu, pjs, usr, center = founders
    _publish(ledger, ritsu, pjs)
    arc = archive_mod.Archive(ledger)
    arc.submit(ritsu, 0.9, quality_floor=0.5)
    arc.submit(pjs, 0.9, quality_floor=0.5)
    occ = arc.occupancy()
    assert occ["n_cells"] == 25
    assert occ["elite_occupied"] == 2
    assert occ["protected_occupied"] == 0
    assert occ["elite_coverage"] == pytest.approx(2 / 25)
    assert occ["lineages_with_elite"] == ["L-P", "L-R"]


def test_submit_rejects_nonfinite_quality(founders, ledger) -> None:
    # quality の有限性検査は台帳照合より前に行われるため publish 不要
    # （submit() の判定順序 — archive.py 参照）。
    ritsu, *_ = founders
    arc = archive_mod.Archive(ledger)
    with pytest.raises(ValueError, match="non-finite"):
        arc.submit(ritsu, float("nan"), quality_floor=0.5)


# --- PR #267 Codex R10 指摘B（P2）: bool quality / quality_floor の黙認 ----


def test_submit_rejects_bool_quality(founders, ledger) -> None:
    """bool は int のサブクラスのため `math.isfinite(True)` は無検証で通り、
    従来 `quality=True` は `quality=1.0` として選抜を汚染していた。
    isinstance(x, bool) を有限性チェックより前に fail-closed 拒否する。
    （bool/finite 検査は台帳照合より前のため publish 不要。）"""
    ritsu, *_ = founders
    arc = archive_mod.Archive(ledger)
    with pytest.raises(ValueError, match="bool quality"):
        arc.submit(ritsu, True, quality_floor=0.5)


def test_submit_rejects_bool_quality_floor(founders, ledger) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive(ledger)
    with pytest.raises(ValueError, match="bool quality_floor"):
        arc.submit(ritsu, 0.8, quality_floor=False)


def test_submit_accepts_ordinary_float_quality_unchanged(founders, ledger) -> None:
    """通常の float quality/quality_floor の受理は不変（回帰確認）。"""
    ritsu, *_ = founders
    _publish(ledger, ritsu)
    arc = archive_mod.Archive(ledger)
    result = arc.submit(ritsu, 0.8, quality_floor=0.5)
    assert result == "elite"


# --- PR #267 Codex R14 指摘（P1）: Archive 受理は導出可能性を見ていなかった --


def test_forged_same_coords_novelty_child_rejected_by_both_ledger_and_archive(founders, ledger) -> None:
    """指摘の実例そのもの: `models.build_genome()` で「親と同一座標の
    novelty_jump 子」（L1=0 < `models.NOVELTY_JUMP_MIN_L1`=0.4 の凍結最小）
    を直接構築すると、genome 単体としては座標・lineage・operator 整合の
    検証を通る自己完結ペイロードになる — 修正前の Archive.submit() は旧
    R9 の自己完結 round-trip 検証しか行わなかったため、これを NOVELTY
    elite として受理していた。`Ledger.write()` の R7 再導出検証は同じ子を
    拒否するにもかかわらず（`operators.novelty_jump()` は棄却サンプリング
    で L1>=0.4 を保証するため、親と同一座標には原理的に到達し得ない）。

    修正後はどちらの経路からもこの子は Archive に入れないことを両面から
    確認する:
    (a) `Ledger.write()` は R7 再導出検証で `LedgerError` 拒否する。
    (b) `Archive.submit()` は (a) の結果としてそもそも publish され得ない
        ため、「台帳に未掲載」として `ArchiveAdmissionError` 拒否する。
    """
    ritsu, *_ = founders
    ledger.write(ritsu)

    forged_child = models.build_genome(
        coords=ritsu.coords, seed=1, lineage="NOVELTY", generation=ritsu.generation + 1,
        parents=(ritsu.genome_id,), operator="novelty_jump", operator_params={"rng_seed": 999999},
    )
    assert simplex.l1_distance(forged_child.coords, ritsu.coords) == 0.0
    assert forged_child.genome_id != ritsu.genome_id

    # (a) 台帳は R7 再導出検証で拒否する — publish されない。
    with pytest.raises(ledger_mod.LedgerError):
        ledger.write(forged_child)
    assert not ledger.exists(forged_child.genome_id)

    # (b) Archive は「未 publish」として拒否する（台帳に一切現れないため）。
    arc = archive_mod.Archive(ledger)
    with pytest.raises(archive_mod.ArchiveAdmissionError, match="not found in the ledger"):
        arc.submit(forged_child, 0.9, quality_floor=0.4)
    for cell in arc.cells():
        assert arc.elite_at(cell) is None
        assert arc.protected_at(cell) is None
    assert arc.eviction_log == []


def test_correct_route_operator_then_ledger_write_then_submit_succeeds(founders, ledger) -> None:
    """正規経路の回帰確認: オペレータで正しく子を生成 →
    `Ledger.write()` で publish → `Archive.submit()` が成功する（R14 修正
    がこの経路を壊していないことの確認）。"""
    ritsu, *_ = founders
    ledger.write(ritsu)
    child = operators.drift(ritsu, rng_seed=7, step=0.02)
    ledger.write(child)

    arc = archive_mod.Archive(ledger)
    result = arc.submit(child, 0.7, quality_floor=0.5)
    assert result == "elite"
    assert arc.elite_at(simplex.cell_id(child.coords)).genome_id == child.genome_id


def test_submit_rejects_tampered_field_after_publish_full_match_required(founders, ledger) -> None:
    """`genome_id` は `notes` を対象外とする（`models.compute_genome_id()`
    参照）ため、`notes` だけを差し替えた別インスタンスは `genome_id` が
    元 genome と一致したまま、シリアライズ後バイト列だけが異なる。
    `genome_id` の一致だけを検証条件にすると、これは検出できない —
    「シリアライズ後バイト完全一致」を Archive 受理条件にする設計判断
    （archive.py docstring 参照）の直接的な根拠。
    """
    ritsu, *_ = founders
    ledger.write(ritsu)
    arc = archive_mod.Archive(ledger)

    tampered = replace(ritsu, notes="silently tampered after publish")
    assert tampered.genome_id == ritsu.genome_id  # notes は genome_id 計算対象外
    assert tampered != ritsu  # だがフィールドは異なる

    with pytest.raises(archive_mod.ArchiveAdmissionError, match="does not.*byte-for-byte match"):
        arc.submit(tampered, 0.9, quality_floor=0.5)

    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell) is None
    assert arc.protected_at(cell) is None


# --- PR #267 Codex R9 指摘1（P2）: submit() の未検証受理 --------------------
# （R14 修正後は「台帳未掲載」として拒否される — 台帳照合が R9 の検証を
# 包含するため、この2テストは archive_mod.ArchiveAdmissionError を期待する。）


def test_submit_rejects_directly_constructed_genome_with_invalid_coords(ledger) -> None:
    """呼び出し側が `build_genome()`/`genome_from_dict()` を経由せず
    `models.VoiceGenome` を直接構築すると、simplex 上に存在しない座標
    （負の成分）を持つ genome を作れる。この genome は
    `models.genome_from_dict()` を通らないため `Ledger.write()` で publish
    され得ず、`Archive.submit()` は「台帳未掲載」として拒否する。
    """
    forged = models.VoiceGenome(
        schema=models.SCHEMA_GENOME,
        genome_id="0" * 16,
        coords=models.Coords(-0.1, 0.5, 0.6),
        seed=0,
        lineage="L-U",
        generation=0,
        parents=(),
        operator="founder",
        operator_params={},
        anchors_provenance=None,
        notes="",
    )
    # 台帳へ publish しようとしても genome_from_dict() round-trip 検証で
    # 拒否される（そもそも publish 経路が存在しないことの確認）。
    with pytest.raises(ledger_mod.LedgerError):
        ledger.write(forged)

    arc = archive_mod.Archive(ledger)
    with pytest.raises(archive_mod.ArchiveAdmissionError, match="not found in the ledger"):
        arc.submit(forged, 0.9, quality_floor=0.5)
    # cells() に無いセルへ汚染されていないこと。
    assert simplex.cell_id(forged.coords) not in arc.cells()
    for cell in arc.cells():
        assert arc.elite_at(cell) is None
        assert arc.protected_at(cell) is None
    assert arc.eviction_log == []


def test_submit_rejects_directly_constructed_genome_with_forged_lineage(founders, ledger) -> None:
    """座標自体は正規形だが、座標由来 lineage（L-R）と矛盾する偽装 lineage
    （L-U）を直接構築で宣言した genome は台帳の正規エントリと genome_id は
    一致するが内容が異なるため、Archive のバイト完全一致照合で拒否される。
    """
    ritsu, *_ = founders
    ledger.write(ritsu)
    forged = models.VoiceGenome(
        schema=models.SCHEMA_GENOME,
        genome_id=ritsu.genome_id,
        coords=ritsu.coords,
        seed=ritsu.seed,
        lineage="L-U",  # ritsu.coords は L-R 由来 — 座標と矛盾する偽装
        generation=ritsu.generation,
        parents=ritsu.parents,
        operator=ritsu.operator,
        operator_params=ritsu.operator_params,
        anchors_provenance=ritsu.anchors_provenance,
        notes=ritsu.notes,
    )
    arc = archive_mod.Archive(ledger)
    with pytest.raises(archive_mod.ArchiveAdmissionError, match="does not.*byte-for-byte match"):
        arc.submit(forged, 0.9, quality_floor=0.5)
    cell = simplex.cell_id(forged.coords)
    assert arc.elite_at(cell) is None
    assert arc.protected_at(cell) is None
    assert arc.eviction_log == []


def test_submit_accepts_genuine_genome_built_via_build_genome_unchanged(founders, ledger) -> None:
    """`build_genome()`/`bootstrap.founder_genomes()` 経由の正規 genome の
    受理挙動は台帳照合の追加後も不変（回帰確認）。
    """
    ritsu, *_ = founders
    ledger.write(ritsu)
    arc = archive_mod.Archive(ledger)
    result = arc.submit(ritsu, 0.8, quality_floor=0.5)
    assert result == "elite"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == ritsu.genome_id


def test_all_cells_initially_empty(ledger) -> None:
    arc = archive_mod.Archive(ledger)
    assert arc.cells() == simplex.all_cell_ids(5)
    for cell in arc.cells():
        assert arc.elite_at(cell) is None
        assert arc.protected_at(cell) is None


def test_construction_rejects_non_frozen_grid_size_n4(ledger) -> None:
    """PR #267 Codex R16 指摘（P2）, 2026-08-18 採用: n=4 は `_MICRO_PER_UNIT`
    (1,000,000) の約数のため `simplex.cell_id()` 側の整除チェックを素通り
    し、初回 submit まで拒否されずに無版番の16セル Archive が動いてしまって
    いた。コンストラクタ時点で fail-closed 拒否する。
    """
    with pytest.raises(ValueError, match="unsupported grid size"):
        archive_mod.Archive(ledger, n=4)


def test_construction_rejects_non_frozen_grid_size_n3(ledger) -> None:
    """n=3 は `_MICRO_PER_UNIT` の非約数のため、修正前も `simplex.cell_id()`
    側で初回 submit 時にのみ事後的に失敗していた（コンストラクタは無条件
    受理）。修正後はコンストラクタ時点で他の非既定 n と同様に即座に
    fail-closed 拒否される。
    """
    with pytest.raises(ValueError, match="unsupported grid size"):
        archive_mod.Archive(ledger, n=3)


def test_construction_default_grid_size_unchanged(ledger) -> None:
    """既定構築（`n` 省略、= `simplex.GRID_N` = 5）の動作は本修正で不変。"""
    arc = archive_mod.Archive(ledger)
    assert arc.n == simplex.GRID_N == 5
    assert arc.cells() == simplex.all_cell_ids(5)
    assert len(arc.cells()) == 25
