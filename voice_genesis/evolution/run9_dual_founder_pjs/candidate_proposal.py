"""candidate_proposal.py — RUN9 HARNESS-3c: `candidate_generation_spec_v1.json`
`proposal` 節（candidate_ordering / exploratory_candidate_rule /
neighborhood_candidate_rule）の byte レベル参照実装。

設計根拠: PR #331 Codex bot レビュー第2巡指摘1（P1「Specify the complete
digest-to-candidate mapping」、採用）。`inputs/candidate_generation_spec_v1.json`
`proposal` 節が凍結する、digest のどのバイトが軸・note/phrase・delta/offset
値を選ぶか、および「近傍」の全順序を、実装が選べる余地なく機械的に再現する。

第3巡指摘1（P1「Make the scheduled neighborhood ratio achievable」、採用）
の追随修正を含む: 旧 `neighbors_of()` は現 best の値キーを ±1 量子化ステップ
した2候補のみを近傍として返しており、非恒等 best では最大2件しか近傍枠
（凍結 3:1 比率の3枠）を満たせず、trial 2-32 の近傍3枠が構造的に達成不能
だった。値キー ±1/±2 量子化ステップ + 隣接 index キー（L 順で1つ前/後）の
優先順位リストへ拡張し、内部領域（range 端でも index 端でもない current
best）で3件以上の近傍候補が構造的に存在するようにした。

第4巡指摘1（P2「shortfall 主張の正直是正」、採用）: 上段落と第3巡時点の
spec が述べていた「shortfall は端点の場合に限られる」は偽——優先順位
リスト6項目のうち3件以上が catalog 制約内で有効な内部領域の best でも、
それらが既に評価済み（`select_neighborhood_candidates()` の `is_evaluated`
フィルタ後）であれば3件に満たない shortfall が起こり得る
（`tests/test_candidate_proposal.py::test_select_neighborhood_candidates_
shortfall_when_all_evaluated` が実証）。3:1 は「近傍優先3スロット + 探査
1スロット」の決定論スロットテンプレートであり、近傍スロットが埋まらない
場合は探査規則で決定論的に補充される。各 trial の実際の内訳（近傍/探査/
NOT_PROPOSABLE）は探索 trace に必須記録する——宣言比率と実測内訳の乖離を
隠さない会計。

第4巡指摘2（P1「tie-break の実行可能な全順序凍結」、採用）: `selection.
tie_break` の旧定義「(objective, 軸ベクトルの辞書順)」は座標順・表現・
恒等候補の位置が未定義だったため、`candidate_ordinal()`（恒等=-1、非恒等
候補=L 内インデックス）ベースの tie-break キー `(objective,
candidate_ordinal)` へ置換凍結した。

第5巡指摘1（P2「proposal 定数の pinned catalog への束縛」、採用）: 旧実装は
AX-P1/AX-D1 の domain・quantization step・min-duration を本モジュール内に
ハードコード定数（`AX_P1_OFFSET_DOMAIN`/`AX_P1_QUANTIZATION_STEP`/
`AX_D1_QUANTIZATION_STEP`/`AX_D1_MIN_DURATION_BEATS`）として持っており、
`score_axis_catalog_v1.json`（catalog）が正当な理由で repin されて値が
変わっても、これらの定数は追随せず旧値のまま漂流し得た——`run9_schema.py`
の cross-check は `score_axis_transform` 側の consumption 経路
（`apply_ax_p1()`/`apply_ax_d1()`）が catalog 値を正しく使うことしか
検証しておらず、本モジュールのハードコード定数までは照合していなかった。
本巡でハードコード定数を全廃し、`score_axis_transform.py` と同型の
catalog 消費規約（呼び出し側が `catalog: Mapping[str, Any]` を都度渡す。
本番経路では `run9_schema.load_pinned_score_axis_catalog_manifest()` の
戻り値、テストでは `score_axis_catalog_v1.json` を直接読んだ実データ辞書
を注入する）へ置換した。L 構築（`build_ax_p1_ordering()`/
`build_ax_d1_ordering()`/`build_candidate_ordering()`）・近傍列挙
（`neighbors_of()`/`select_neighborhood_candidates()`）のいずれも catalog
から値を都度導出し、単一情報源（catalog）が正であることを構造的に保証
する。

**スコープ境界**: 本モジュールは spec の「候補列 L の構築」「digest→候補の
写像」「近傍列挙」という *決定論的で generator 非依存な部分* のみを実装
する。実際の探索ループ（PRACTICE actor 内での候補適用・render・loss
評価・trial を跨いだ best 更新・trace 保存）は本モジュールの対象外であり
別途配線する（レビュー指摘原文が述べる「次の PR で generator を実装する」
の対象）。本モジュールはその generator が満たすべき写像を曖昧さなく検査
可能にするための正本実装として提供する。

**catalog 消費**: offset/delta の量子化刻み・range・min-duration は
ハードコードせず、呼び出し側が渡す `catalog`（`score_axis_catalog_v1.json`
と同型の dict、`score_axis_transform.apply_ax_p1()`/`apply_ax_d1()` が
受け取るものと同一）から都度読む（`_ax_p1_offset_domain()`/
`_ax_p1_quantization_step()`/`_ax_d1_quantization_step()`/
`_ax_d1_min_duration_beats()`）。catalog が改訂されて凍結し直された場合、
本モジュールを変更せずに新しい値が反映される——`score_axis_transform.py`
冒頭 docstring が述べる設計方針と同一。

第8巡指摘1（P2「undersized L の run 前拒否ゲート」、採用）:
`require_sufficient_candidate_space()` を新設した。|L| が全提案スロット数
（`structure.units_per_founder_per_arm`）から恒等スロット1件を除いた
最小値を下回ると、NOT_PROPOSABLE の頻発により render 数が契約を下回った
まま run が完走し得る——run 開始前の前提条件として本関数が fail-closed
で停止する。`run_precondition.minimum_candidate_space`（spec）の参照実装。

第8巡指摘2（P1「同一 trial 内の予約集合の凍結」、採用）:
`propose_trial_candidates()` を新設した。重複回避が「評価済み」のみを
見る旧実装では batch 提案（trial 内 4 候補を一括計算）と逐次提案（1件
ずつ render/評価してから次を計算）で trace が分岐し得た。本関数は
candidate_index 0->1->2->3 の逐次順で提案し、各候補を**提案された時点で**
（render/評価を待たず）予約集合（reserved set = proposed-or-evaluated）
へ加える——近傍列挙・探査プロービングとも予約集合をスキップ対象とする
ため、batch/逐次いずれの呼び出しパターンでも同一の候補列が決定論的に
再現する。`proposal.reservation_semantics`（spec）の参照実装。

第8巡指摘3（P2「spec リテラル domain の catalog 連合 cross-check」、
採用）: `ax_p1_offset_domain_from_catalog()`（`_ax_p1_offset_domain()`
の公開ラッパー）を新設した。`run9_schema.load_pinned_candidate_
generation_spec_manifest()` が本関数経由で catalog から独立に offset
domain を導出し、`candidate_generation_spec_v1.json` 側のリテラル
`offset_domain` 記述と cross-check する——run9_schema 側が計算式を複製
しないための単一情報源化（`score_axis_transform.py`/本モジュールの
既存方針と同型）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

# L の要素型: AX-P1 = ("AX-P1", note_index, offset)
#             AX-D1 = ("AX-D1", phrase_index, (i, j), delta)
Candidate = Tuple[Union[str, int, float, Tuple[int, int]], ...]


def _round_grid(value: float, *, ndigits: int = 10) -> float:
    """浮動小数の累積誤差を格子値の比較に影響させないための丸め
    （量子化格子上の値であることは呼び出し側が担保する——本関数は表示・
    比較用の丸めのみ行う）。"""
    return round(value, ndigits)


# ---------------------------------------------------------------------------
# catalog 消費ヘルパー（第5巡指摘1、P2、採用: ハードコード定数を廃し
# catalog dict から都度導出する。`score_axis_transform._ax_p1_constraints()`
# / `_ax_d1_constraints()` と同型）
# ---------------------------------------------------------------------------


def _ax_p1_constraints(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    return catalog["axes"]["AX-P1"]


def _ax_d1_constraints(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    return catalog["axes"]["AX-D1"]


def _ax_p1_quantization_step(catalog: Mapping[str, Any]) -> float:
    return _ax_p1_constraints(catalog)["quantization_step_semitones"]


def _ax_d1_quantization_step(catalog: Mapping[str, Any]) -> float:
    return _ax_d1_constraints(catalog)["quantization_step_beats"]


def _ax_d1_min_duration_beats(catalog: Mapping[str, Any]) -> float:
    return _ax_d1_constraints(catalog)["min_duration_beats"]


def _ax_p1_offset_domain(catalog: Mapping[str, Any]) -> Tuple[float, ...]:
    """AX-P1 の 0 除外グリッド値を catalog の `range_semitones` /
    `quantization_step_semitones` から導出する（`candidate_generation_
    spec_v1.json` `proposal.candidate_ordering.ax_p1.offset_domain_note`
    の逐語手続き: 0 は単一軸候補としては非変換であり L に含めない）。"""
    constraints = _ax_p1_constraints(catalog)
    lo, hi = constraints["range_semitones"]
    step = constraints["quantization_step_semitones"]
    n_steps = round((hi - lo) / step)
    values = []
    for i in range(n_steps + 1):
        v = _round_grid(lo + i * step)
        if abs(v) > 1e-9:
            values.append(v)
    return tuple(values)


def ax_p1_offset_domain_from_catalog(catalog: Mapping[str, Any]) -> Tuple[float, ...]:
    """`_ax_p1_offset_domain()` の公開ラッパー（PR #331 Codex bot レビュー
    第8巡指摘3、P2、採用）: `run9_schema.load_pinned_candidate_generation_
    spec_manifest()` の catalog↔spec cross-check が本関数経由で offset
    domain を単一情報源（catalog）から導出する——run9_schema 側が本関数と
    同じ計算式を複製しないための公開エントリポイント。"""
    return _ax_p1_offset_domain(catalog)


def build_ax_p1_ordering(note_count: int, *, catalog: Mapping[str, Any]) -> List[Candidate]:
    """AX-P1 の単一軸候補を `(axis_id, note_index, offset)` の辞書順
    （note_index 昇順 → offset 昇順）で列挙する。offset domain は
    `catalog`（`score_axis_catalog_v1.json` と同型の dict）から導出する。
    """
    if note_count < 0:
        raise ValueError(f"note_count must be >= 0, got {note_count}")
    offset_domain = _ax_p1_offset_domain(catalog)
    return [
        ("AX-P1", note_index, offset)
        for note_index in range(note_count)
        for offset in offset_domain
    ]


def build_ax_d1_ordering(
    phrase_of_note: Sequence[int],
    original_duration_beats: Sequence[float],
    *,
    catalog: Mapping[str, Any],
) -> List[Candidate]:
    """AX-D1 の単一軸候補を `(axis_id, phrase_index, (i, j), delta)` の
    辞書順（phrase_index 昇順 → i 昇順 → j 昇順 → delta 昇順）で列挙する。

    delta は note i（失う側）から note j（得る側）への移動量。delta の
    上限は「note i の変換後 duration が min_duration_beats 以上」を満たす
    最大の量子化格子値（`catalog` の `quantization_step_beats` 刻み）。
    この上限を満たす delta が存在しない（original_duration_beats[i] <
    2 * min_duration_beats の場合など）(i, j) ペアは候補を生まない。
    `quantization_step_beats`/`min_duration_beats` はいずれも `catalog`
    （`score_axis_catalog_v1.json` と同型の dict）から導出する。
    """
    quantization_step_beats = _ax_d1_quantization_step(catalog)
    min_duration_beats = _ax_d1_min_duration_beats(catalog)
    if len(phrase_of_note) != len(original_duration_beats):
        raise ValueError(
            "phrase_of_note と original_duration_beats の長さが一致しない "
            f"({len(phrase_of_note)} != {len(original_duration_beats)})"
        )
    phrases: Dict[int, List[int]] = {}
    for note_index, phrase_index in enumerate(phrase_of_note):
        phrases.setdefault(phrase_index, []).append(note_index)

    ordering: List[Candidate] = []
    for phrase_index in sorted(phrases):
        note_indices = sorted(phrases[phrase_index])
        for i in note_indices:
            for j in note_indices:
                if i == j:
                    continue
                max_steps = int(
                    (original_duration_beats[i] - min_duration_beats + 1e-9)
                    // quantization_step_beats
                )
                for step in range(1, max_steps + 1):
                    delta = _round_grid(step * quantization_step_beats)
                    ordering.append(("AX-D1", phrase_index, (i, j), delta))
    return ordering


def build_candidate_ordering(
    *,
    note_count: int,
    phrase_of_note: Sequence[int],
    original_duration_beats: Sequence[float],
    catalog: Mapping[str, Any],
) -> List[Candidate]:
    """正準候補列 L（`candidate_generation_spec_v1.json`
    `proposal.candidate_ordering` の凍結定義）を、total_order（タプル
    比較の辞書順。axis_id 文字列比較で "AX-D1" < "AX-P1" のため AX-D1 群が
    先に並ぶ）で構築する。offset/delta の domain・quantization step・
    min-duration はすべて `catalog`（`score_axis_catalog_v1.json` と同型の
    dict。本番経路では `run9_schema.
    load_pinned_score_axis_catalog_manifest()` の戻り値を渡す）から導出し、
    本モジュール内にハードコードしない（第5巡指摘1、P2、採用）。"""
    ax_d1 = build_ax_d1_ordering(
        phrase_of_note,
        original_duration_beats,
        catalog=catalog,
    )
    ax_p1 = build_ax_p1_ordering(note_count, catalog=catalog)
    ordering = ax_d1 + ax_p1
    ordering.sort()
    return ordering


# ---------------------------------------------------------------------------
# digest -> index 写像 + 探査候補の線形プロービング
# ---------------------------------------------------------------------------


def digest_bytes(seed: int, arm: str, founder_id: str, trial: int, candidate: int) -> bytes:
    """`digest_formula`（`digest = sha256(UTF-8(f"{seed}:{arm}:{founder_id}:
    {trial}:{candidate}"))`）の逐語実装。"""
    template = f"{seed}:{arm}:{founder_id}:{trial}:{candidate}"
    return hashlib.sha256(template.encode("utf-8")).digest()


def digest_to_index(digest: bytes, *, list_length: int) -> int:
    """digest の先頭8バイトを big-endian uint64 として解釈し、
    `idx = u mod len(L)` を返す。"""
    if list_length <= 0:
        raise ValueError(f"list_length must be positive, got {list_length}")
    u = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return u % list_length


def select_exploratory_candidate(
    candidates: Sequence[Candidate],
    *,
    seed: int,
    arm: str,
    founder_id: str,
    trial: int,
    candidate_index: int,
    is_acceptable: Callable[[Candidate], bool],
) -> Optional[Candidate]:
    """`exploratory_candidate_rule` の逐語実装: digest 由来の初期 index から
    決定論線形プロービングで最初の未評価かつ有効な候補を返す。全滅時は
    None（NOT_PROPOSABLE を表す）を返す——架空の候補を発明しない。

    `is_acceptable(candidate)` は「未評価かつ catalog 制約を満たす」ことを
    呼び出し側が判定する述語（本モジュールは探索状態を保持しない）。
    """
    n = len(candidates)
    if n == 0:
        return None
    start = digest_to_index(
        digest_bytes(seed, arm, founder_id, trial, candidate_index), list_length=n
    )
    for step in range(n):
        idx = (start + step) % n
        cand = candidates[idx]
        if is_acceptable(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# 近傍候補列挙
# ---------------------------------------------------------------------------

# 恒等候補（全軸 0）を表すセンチネル。L の要素ではない。
IDENTITY: None = None


def _index_key(candidate: Candidate) -> Union[int, Tuple[int, Tuple[int, int]]]:
    """候補から index キー（AX-P1: note_index／AX-D1: (phrase_index, (i, j))）
    を取り出す。"""
    if candidate[0] == "AX-P1":
        return candidate[1]  # type: ignore[return-value]
    if candidate[0] == "AX-D1":
        return (candidate[1], candidate[2])  # type: ignore[return-value]
    raise ValueError(f"unknown axis_id in candidate: {candidate[0]!r}")


def _value_key(candidate: Candidate) -> float:
    """候補から値キー（AX-P1: offset／AX-D1: delta）を取り出す。"""
    if candidate[0] == "AX-P1":
        return candidate[2]  # type: ignore[return-value]
    if candidate[0] == "AX-D1":
        return candidate[3]  # type: ignore[return-value]
    raise ValueError(f"unknown axis_id in candidate: {candidate[0]!r}")


def _make_candidate(axis_id: str, index_key: Any, value: float) -> Candidate:
    """axis_id + index キー + 値キーから候補タプルを再構成する。"""
    if axis_id == "AX-P1":
        return ("AX-P1", index_key, value)
    if axis_id == "AX-D1":
        phrase_index, pair = index_key
        return ("AX-D1", phrase_index, pair, value)
    raise ValueError(f"unknown axis_id: {axis_id!r}")


def _sorted_index_keys(all_candidates: Sequence[Candidate], axis_id: str) -> List[Any]:
    """L 内の指定 axis の distinct index キーを、L の total_order に沿う
    昇順（AX-P1: note_index 昇順／AX-D1: (phrase_index, (i, j)) 昇順）で
    返す。"""
    keys = {_index_key(cand) for cand in all_candidates if cand[0] == axis_id}
    return sorted(keys)


def neighbors_of(
    current_best: Optional[Candidate],
    all_candidates: Sequence[Candidate],
    *,
    catalog: Mapping[str, Any],
) -> List[Candidate]:
    """`neighborhood_candidate_rule` の `neighbor_value_perturbation` /
    `identity_neighbor_rule` の逐語実装。量子化ステップは `catalog`
    （`score_axis_catalog_v1.json` と同型の dict）から都度導出する
    （ハードコードしない、第5巡指摘1、P2、採用）。

    現 best が L の要素（単一軸候補）の場合、以下の優先順位リストを順に
    評価し、L に存在し（catalog 制約を満たし）かつ未 dedup の候補を集める
    （呼び出し側 `select_neighborhood_candidates()` が未評価フィルタと
    limit=3 の先頭切り出しを行う）:

    1. 値キー v + 1 量子化ステップ
    2. 値キー v - 1 量子化ステップ
    3. 値キー v + 2 量子化ステップ
    4. 値キー v - 2 量子化ステップ
    5. 同 axis・同値キーで index キーが L 順で1つ前の候補
    6. 同 axis・同値キーで index キーが L 順で1つ後の候補

    この優先順位リストにより、内部領域（range 端でも index 端でもない
    current best）では常に3件以上の近傍候補が構造的に存在する
    （PR #331 Codex bot レビュー第3巡「Make the scheduled neighborhood
    ratio achievable」、P1、採用: 旧実装は ±1 量子化ステップの2方向のみ
    しか近傍候補を持たず、非恒等 best では最大2件しか近傍枠を満たせず
    凍結 3:1 比率が構造的に達成不能だった）。値キーが range/domain の
    端かつ index キーが列の端（同一値キーの前後 index が存在しない、
    または存在しても L に無い）という**端点の場合に限り**3件未満の
    shortfall が起こり得る——これは `shortfall_handling` が定める例外
    であり、探査規則で補充する。

    現 best が恒等（None）の場合は `identity_neighbor_rule` に従う:
    値キーの初期値は（全 index キーについて）0 であるため、優先順位
    リストの項目1・2（値キー ±1 量子化ステップ）を全 index キーに対して
    適用した結果が近傍候補集合となる（AX-D1 の値キーは正数のみが domain
    のため項目2 相当は L に存在せず自動的に除かれる——0.25 のみが残る)。
    項目3・4（±2 量子化ステップ）は恒等では新規のルールを持ち込まず、
    項目5・6（隣接 index キー）は基準となる単一 index キーが恒等には
    存在しないため意味を持たず適用しない。恒等の近傍候補数は通常
    note_count / phrase 構成に対して3件を大きく上回るため、この
    integration は既存挙動を変更しない。
    """
    candidate_set = set(all_candidates)
    ax_p1_step = _ax_p1_quantization_step(catalog)
    ax_d1_step = _ax_d1_quantization_step(catalog)

    if current_best is None:
        # identity_neighbor_rule: 恒等からちょうど1量子化ステップの候補
        # （全 index キー分）。優先順位リストの項目1・2 を、値キー初期値
        # 0 のベクトルへ全 index キーに対して適用した結果に等しい。
        neighbors: List[Candidate] = []
        for cand in all_candidates:
            if cand[0] == "AX-P1":
                _, _note_index, offset = cand
                if abs(abs(offset) - ax_p1_step) < 1e-9:
                    neighbors.append(cand)
            elif cand[0] == "AX-D1":
                _, _phrase_index, _pair, delta = cand
                if abs(delta - ax_d1_step) < 1e-9:
                    neighbors.append(cand)
        neighbors.sort()
        return neighbors

    axis_id = current_best[0]
    if axis_id not in ("AX-P1", "AX-D1"):
        raise ValueError(f"unknown axis_id in current_best: {axis_id!r}")

    step = ax_p1_step if axis_id == "AX-P1" else ax_d1_step
    index_key = _index_key(current_best)
    value = _value_key(current_best)

    priority: List[Candidate] = []
    seen = set()

    def _try_add(cand: Candidate) -> None:
        if cand in candidate_set and cand not in seen:
            seen.add(cand)
            priority.append(cand)

    # 項目1-4: 値キー +-1/+-2 量子化ステップ（同一 index キー）。
    for multiplier in (1, -1, 2, -2):
        _try_add(_make_candidate(axis_id, index_key, _round_grid(value + multiplier * step)))

    # 項目5-6: 同値キー・隣接 index キー（L 順で1つ前/後）。
    index_keys = _sorted_index_keys(all_candidates, axis_id)
    if index_key in index_keys:
        pos = index_keys.index(index_key)
        if pos > 0:
            _try_add(_make_candidate(axis_id, index_keys[pos - 1], value))
        if pos < len(index_keys) - 1:
            _try_add(_make_candidate(axis_id, index_keys[pos + 1], value))

    return priority


def candidate_ordinal(candidate: Optional[Candidate], all_candidates: Sequence[Candidate]) -> int:
    """`selection.tie_break` が凍結する tie-break キー第2要素
    （candidate_ordinal）の逐語実装（PR #331 Codex bot レビュー第4巡指摘2、
    P1、採用: 旧 tie_break「(objective, 軸ベクトルの辞書順)」は座標順・
    表現・恒等候補の位置が未定義だったため、実行可能な全順序へ置換凍結
    した）。

    候補が恒等（None、trial1_candidate0_rule の baseline）の場合 -1 を
    返す——L のどの要素のインデックス（0以上）よりも小さいため、tie_break
    では常に恒等が勝つ。候補が非恒等（`all_candidates`＝L、
    `build_candidate_ordering()` が返す凍結済み total_order の要素）の
    場合、L 内でのインデックス（0始まり）を返す。恒等・AX-P1・AX-D1 の
    全候補型を単一整数キーで被覆する。
    """
    if candidate is None:
        return -1
    for idx, cand in enumerate(all_candidates):
        if cand == candidate:
            return idx
    raise ValueError(
        f"candidate_ordinal(): candidate {candidate!r} not found in all_candidates (L) — "
        "candidate_ordinal is only defined for the identity candidate (None) or members of L"
    )


def select_neighborhood_candidates(
    current_best: Optional[Candidate],
    all_candidates: Sequence[Candidate],
    *,
    is_evaluated: Callable[[Candidate], bool],
    catalog: Mapping[str, Any],
    limit: int = 3,
) -> List[Candidate]:
    """`enumeration_order` の逐語実装: `neighbors_of()` が返す優先順位順
    （値キー +-1/+-2 量子化ステップ → 隣接 index キー、恒等 best では
    `identity_neighbor_rule` の L 辞書順）の近傍候補集合から、未評価の
    ものを先頭から `limit` 件返す。`neighbors_of()` の生の出力は内部領域
    （range 端でも index 端でもない current best）では構造的に3件以上
    存在するが、本関数はさらに `is_evaluated` で未評価フィルタするため、
    内部領域の best でも既評価候補が優先順位リストの上位を占めていれば
    3件未満に減り得る（PR #331 Codex bot レビュー第4巡指摘1「shortfall
    主張の正直是正」、P2、採用: 旧 docstring は「端点でのみ3件未満」と
    述べていたが、これは `neighbors_of()` の生出力にのみ当てはまる主張
    であり、本関数の実際の返り値（評価済みフィルタ後）には当てはまらない
    ——`test_select_neighborhood_candidates_shortfall_when_all_evaluated`
    が内部領域の best でも全評価済みなら0件になることを実証している）。
    `shortfall_handling` により、幾何的端点・評価済み枯渇いずれの理由の
    不足分も呼び出し側が `select_exploratory_candidate()` で補充する
    （本関数は補充を行わない）。"""
    neighbors = neighbors_of(current_best, all_candidates, catalog=catalog)
    selected = [cand for cand in neighbors if not is_evaluated(cand)]
    return selected[:limit]


# ---------------------------------------------------------------------------
# run 前提条件ゲート（PR #331 Codex bot レビュー第8巡指摘1、P2、採用）
# ---------------------------------------------------------------------------


def require_sufficient_candidate_space(
    all_candidates: Sequence[Candidate], *, render_budget: int,
) -> None:
    """`run_precondition.minimum_candidate_space` の逐語実装: run 開始前の
    前提条件として |L|（正準候補列 L の要素数、恒等候補を含まない）が
    `render_budget - 1`（全提案スロット数から trial1_candidate0_rule の
    恒等スロット1件を除いた必要最小値）以上であることを要求する。

    不足する場合は run を開始せず fail-closed で停止する——代替挙動の
    発明・予算追加・結果を見た range 拡張のいずれも行わない（`prohibited`
    が禁じる事項と対称）。undersized な L のまま run を完走させると、
    trial 2..32 の近傍・探査スロットが NOT_PROPOSABLE を頻発し、render
    数が契約（`units_per_founder_per_arm` = 128 units/Founder/arm）を
    下回ったまま完走し得る——本関数はその状態を run 開始前の検査で構造的
    に締め出す。

    呼び出し側（次 PR で配線される generator）は L 構築直後・trial ループ
    開始前に本関数を呼ぶことを想定する。本モジュールは実際の探索ループを
    持たないため、本関数自体は副作用を持たず検査のみ行う。
    """
    required_minimum = render_budget - 1
    if len(all_candidates) < required_minimum:
        raise ValueError(
            f"candidate space L (len={len(all_candidates)}) is smaller than the required "
            f"minimum ({required_minimum} = render_budget({render_budget}) - 1 identity slot) "
            "— refusing to start the run (fail-closed; no alternate behavior, budget addition, "
            "or range expansion is invented; see run_precondition.minimum_candidate_space)"
        )


# ---------------------------------------------------------------------------
# trial-level 逐次提案 + 予約集合（PR #331 Codex bot レビュー第8巡指摘2、
# P1、採用）
# ---------------------------------------------------------------------------


def propose_trial_candidates(
    *,
    trial: int,
    current_best: Optional[Candidate],
    all_candidates: Sequence[Candidate],
    catalog: Mapping[str, Any],
    seed: int,
    arm: str,
    founder_id: str,
    reserved: Iterable[Optional[Candidate]] = (),
    candidates_per_trial: int = 4,
    neighborhood_limit: int = 3,
) -> List[Optional[Candidate]]:
    """`proposal.reservation_semantics` の逐語実装: trial 内の
    candidate_index 0 -> 1 -> 2 -> 3 を逐次順で提案し、各候補は提案された
    時点で（render/評価の結果を待たず）予約集合（reserved set =
    proposed-or-evaluated）へ加える。近傍列挙（`neighbors_of()`）・探査
    プロービング（`select_exploratory_candidate()`）とも、この予約集合
    （引数 `reserved` で渡す過去 trial の評価済み候補 ∪ 当該 trial 内で
    既に提案済みの候補）をスキップ対象とする。

    trial==1: candidate 0 = 恒等（`trial1_candidate0_rule`、返り値の None
    がこれを表す）、candidate 1..3 = hash-derived exploratory。

    trial>=2: candidate 0..2 は `neighborhood_candidate_rule` の優先順位
    リストから未予約の先頭 `neighborhood_limit`（既定3）件、shortfall
    （不足）分と candidate 3 は `exploratory_candidate_rule` で決定論的に
    補充する（`shortfall_handling`）。近傍キューは各スロット処理直前に
    予約状態を再フィルタしてから消費するため、同一 trial 内の探査補充が
    後続スロットの近傍候補を予約済みにしても正しく除外される。

    NOT_PROPOSABLE（探査規則の線形プロービングが全滅した）スロットは
    None として返す——架空の候補を発明しない。呼び出し側は返り値の None
    を trial log へ NOT_PROPOSABLE として記録する。

    同一入力（trial・current_best・all_candidates・catalog・seed・arm・
    founder_id・reserved）からは常に同一の候補列が決定論的に再現する。
    予約が render/評価の完了を待たず提案時点で確定するため、本関数を
    1回呼ぶ（batch）のと、呼び出し側が candidate_index ごとに render/
    評価してから次を計算する（逐次）のとで、結果は常に一致する——digest
    由来の探査候補が同一 trial 内の既提案候補と衝突しても、線形
    プロービングで次候補へ進むため重複提案は起こらない。

    `reserved` は本関数内でローカルコピーへのみ追加され、呼び出し側の
    集合を破壊的に変更しない。
    """
    reserved_set = set(reserved)

    def _is_reserved(cand: Optional[Candidate]) -> bool:
        return cand in reserved_set

    proposed: List[Optional[Candidate]] = []

    if trial == 1:
        proposed.append(None)  # trial1_candidate0_rule: 恒等
        reserved_set.add(None)
        for candidate_index in range(1, candidates_per_trial):
            cand = select_exploratory_candidate(
                all_candidates,
                seed=seed,
                arm=arm,
                founder_id=founder_id,
                trial=trial,
                candidate_index=candidate_index,
                is_acceptable=lambda c: not _is_reserved(c),
            )
            proposed.append(cand)
            if cand is not None:
                reserved_set.add(cand)
        return proposed

    neighbor_queue = list(neighbors_of(current_best, all_candidates, catalog=catalog))

    for candidate_index in range(candidates_per_trial):
        if candidate_index < neighborhood_limit:
            while neighbor_queue and _is_reserved(neighbor_queue[0]):
                neighbor_queue.pop(0)
            if neighbor_queue:
                cand = neighbor_queue.pop(0)
                proposed.append(cand)
                reserved_set.add(cand)
                continue
        cand = select_exploratory_candidate(
            all_candidates,
            seed=seed,
            arm=arm,
            founder_id=founder_id,
            trial=trial,
            candidate_index=candidate_index,
            is_acceptable=lambda c: not _is_reserved(c),
        )
        proposed.append(cand)
        if cand is not None:
            reserved_set.add(cand)

    return proposed
