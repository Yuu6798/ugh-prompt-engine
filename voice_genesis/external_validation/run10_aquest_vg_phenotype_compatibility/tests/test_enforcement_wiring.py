"""test_enforcement_wiring.py — 「宣言されたが適用されていない検証器」の全数棚卸し。

PR #330 のレビューで同型が 3 度出た:

- 第 4 巡: `GENERATIVE_STATUS` が宣言だけされ、一度も照合に使われていなかった
- 第 22 巡: `verify_design_document()` がテストからしか呼ばれず、どの検収経路
  にも繋がっていなかった
- 第 23 巡: `_is_absent_evidence()` は在るのに compatibility 行へ適用漏れ

いずれも「検査の語彙・関数を書いたが、実行経路から参照されていない」ことが
原因である。個別に塞ぐと同型が再発するので、**未配線のまま追加できなくする**
ことで終端する。第 20 巡の `MAPPING_CLOSURE_INVENTORY` と同じ方式。

判定は静的解析で行う（実行時のフラグではなく、ソースの参照関係を見る）:

1. 走査対象モジュールの直下 ALL_CAPS 定数は、**到達可能な**関数本体から
   load されるか、load される別の定数へモジュール階層で取り込まれている
   こと（推移閉包）。
2. 公開検証器（`assert_*` / `verify_*`）は、パッケージ内の**非テスト**
   モジュールの関数本体から呼ばれていること。

例外は `UNWIRED_REGISTRY` に理由付きで登録する。登録簿に無い未配線が現れたら
落ちるし、配線済みになったのに登録簿へ残っている項目でも落ちる（陳腐化防止）。

【境界宣言】本監査は **必要条件**であって十分条件ではない。捕まえるのは
「宣言された名前がどの実行経路からも参照されていない」ことだけである。
到達可能性は AST の直接呼び出しだけを辿る近似で、Python の動的性
（属性経由・間接呼び出し・エイリアス・同名の多重定義）は解決しない。
限界は `AUDIT_LIMITATIONS` に列挙し、再入条件を添えてある。「宣言された
検証器が実際に**正しい分岐で**使われているか」は本監査の範囲外であり、
そちらは各検証器の個別テストが担う。

本監査の主張は「未配線の宣言を無言で増やせない」ことに限る。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, Set, Tuple

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

# 検査対象は RUN10 ツリーの**非テスト Python モジュール全数**を走査して求める。
#
# ハードコードした一覧にすると、§24 が予定する `measurement/` `calibration/`
# `evaluation/` が追加されたとき、そこに置かれた未配線の `assert_*` /
# `verify_*` が監査の視界に入らないまま全テストが通る — 「ファミリー全数掃討」
# の看板と実際の走査範囲が乖離する（PR #332 Codex 第 1 巡 P2）。
_EXCLUDED_DIRS = frozenset({"tests", "__pycache__", "results"})


def enforcement_modules() -> Tuple[str, ...]:
    """RUN10 ツリー配下の非テストモジュール（ツリー相対パス、安定順）。"""
    found = []
    for path in sorted(_RUN_DIR.rglob("*.py")):
        rel = path.relative_to(_RUN_DIR)
        if _EXCLUDED_DIRS & set(rel.parts):
            continue
        if rel.name.startswith("test_") or rel.name == "conftest.py":
            continue
        found.append(rel.as_posix())
    return tuple(found)


ENFORCEMENT_MODULES: Tuple[str, ...] = enforcement_modules()

# 未配線であることが正当な項目と、その理由。
#
# `PENDING_APPLICATION:` で始まる理由は「配線先のコードがまだ存在しない」
# 予約ガードであることを表す。当該 producer を実装したら配線し、ここから
# 行を消すこと（消さないと `test_registry_has_no_stale_entries` が落ちる）。
UNWIRED_REGISTRY: Dict[str, str] = {
    "MAPPING_CLOSURE_INVENTORY": (
        "第 20 巡の棚卸し表。消費者は test_every_validated_mapping_is_registered "
        "であり、テストが正しい適用先である（表そのものは実行時に参照しない）。"
    ),
    "verify_ledger_bytes": (
        "af01_freeze_verifier の公開 API（モジュール docstring 1.）。実行経路は "
        "第 17 巡で単一読みの read_and_verify_ledger へ一本化済みで、本関数は"
        "その薄いラッパとして外部呼び出し用に残す。"
    ),
    "assert_tracked_tree_clean": (
        "公開境界ガード。CI（tests/test_private_output_boundary.py）が唯一かつ"
        "正当な実行経路である — git 追跡ツリー全体を検査する性質上、"
        "アプリケーションコードからは呼ばれない。"
    ),
    "assert_private_staging_path": (
        "PENDING_APPLICATION: §26 private results bundle の書き出し実装時に配線する。"
        "現時点で staging へ書き出す producer が存在しないため呼び出し元が無い。"
    ),
    "assert_no_public_destination": (
        "PENDING_APPLICATION: 成果物の送出先を受け取るコードの実装時に配線する。"
        "現時点で destination を扱う producer が存在しないため呼び出し元が無い。"
    ),
}


# 本監査が**捕まえないもの**と、その再入条件（PR #332 Codex 第 2 巡 P2×3）。
#
# 監査の主張を実態より広く書くと、それ自体が将来の実装者を誤らせる
# （「このファミリーは終端済み」と読まれる）。捕捉できない経路を列挙して
# 主張を狭める。列挙を消すには、対応する解析を実装してから消すこと。
AUDIT_LIMITATIONS: Dict[str, str] = {
    "unreachable_public_entry": (
        "呼び出し元の無い公開関数そのものを起点に数えるため、未使用の "
        "`public_helper()` が定数を load していれば配線済みと判定する。"
        "再入条件: CLI / 公開 API の実エントリポイント一覧を宣言し、"
        "そこからのみ辿る方式へ切り替えるとき。"
    ),
    "private_predicate_application_sites": (
        "監査対象はモジュール直下の公開検証器（assert_* / verify_*）と "
        "ALL_CAPS 定数に限る。`_is_absent_evidence()` のような private 述語が"
        "「ある分岐では使われ、別の分岐では使われていない」ことは検出しない"
        "（PR #330 第 23 巡の欠陥そのもの）。"
        "再入条件: private 述語ごとに必須適用箇所を宣言する表を作るとき。"
    ),
    "module_qualified_call_resolution": (
        "呼び出しグラフのノードを関数名で同定するため、同名関数が複数モジュール"
        "にあると区別しない（現に `main` / `to_json` が重複している）。"
        "属性呼び出しは数えないので緩む側には倒れないが、同名の直接呼び出しは"
        "取り違え得る。再入条件: モジュール / クラスで修飾した解決を実装するとき。"
    ),
}


def _tree(rel: str) -> ast.Module:
    return ast.parse((_RUN_DIR / rel).read_text(encoding="utf-8"))


def _module_constants(trees: Dict[str, ast.Module]) -> Dict[str, ast.expr]:
    """走査対象**全モジュール**の直下 ALL_CAPS 定数（名前 → 値の式）。

    schema だけを見ると、将来 `measurement/` 等に置かれた検査語彙が監査の
    外に出る（PR #332 Codex 第 1 巡 P2 と同じ理由）。
    """
    out: Dict[str, ast.expr] = {}
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == target.id.upper():
                    out[target.id] = node.value
    return out


def _functions(trees: Dict[str, ast.Module]) -> Dict[str, list]:
    """関数名 → その定義ノード（同名は複数ありうるので list）。"""
    out: Dict[str, list] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, []).append(node)
    return out


def _calls_in(node: ast.AST) -> Set[str]:
    """直接呼び出し（`f(...)`）だけを数える。

    `obj.verify_x()` のような属性呼び出しを裸の名前へ潰すと、無関係な
    メソッド呼び出しが同名のモジュール関数を「配線済み」にしてしまう
    （PR #332 Codex 第 2 巡 P2）。本パッケージは `from ... import name`
    形式の直接呼び出しで統一されているため、属性呼び出しを落としても
    実際の配線は取りこぼさない — 取りこぼせば未配線として落ちるので、
    誤って緩む側には倒れない。
    """
    called: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            called.add(sub.func.id)
    return called


def _entry_points(trees: Dict[str, ast.Module]) -> Set[str]:
    """呼び出しグラフの起点。

    公開関数（`_` で始まらないモジュール直下の def）とメソッド、`main` を
    起点とする。private ヘルパは、起点から辿り着けたときだけ到達可能に
    なる — 「どこからも呼ばれない private ヘルパで定数に触れる」だけでは
    配線済みと数えない。
    """
    entries: Set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") or node.name == "__init__":
                    entries.add(node.name)
    return entries


def _reachable_functions(trees: Dict[str, ast.Module]) -> Set[str]:
    """起点から呼び出しグラフを辿って到達できる関数名。"""
    functions = _functions(trees)
    reachable = {name for name in _entry_points(trees) if name in functions}
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for node in functions.get(current, ()):
            for callee in _calls_in(node):
                if callee in functions and callee not in reachable:
                    reachable.add(callee)
                    frontier.append(callee)
    return reachable


def _names_loaded_in_functions(trees: Dict[str, ast.Module]) -> Set[str]:
    """**到達可能な**関数本体で load される識別子。

    到達可能性を見ないと、どこからも呼ばれない private ヘルパで新しい検査
    定数に触れるだけで「配線済み」と数えられ、実行経路が無いまま本テストが
    通る — 本テストが防ごうとしている当の退行である
    （PR #332 Codex 第 1 巡 P2）。
    """
    functions = _functions(trees)
    used: Set[str] = set()
    for name in _reachable_functions(trees):
        for node in functions.get(name, ()):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    used.add(sub.id)
    return used


def _wired_constants(trees: Dict[str, ast.Module]) -> Set[str]:
    """実行時に参照される定数の推移閉包。

    `CORE_PIN_FIELDS` のように、関数からは直接触られず `_STAGE_FIELDS` 経由で
    効いている定数がある。モジュール階層での取り込みを辿って配線済みと数える。
    """
    constants = _module_constants(trees)
    edges = {
        name: {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        for name, value in constants.items()
    }
    wired = _names_loaded_in_functions(trees)
    changed = True
    while changed:
        changed = False
        for name in list(wired):
            for dep in edges.get(name, ()):
                if dep not in wired:
                    wired.add(dep)
                    changed = True
    return wired


def _public_validators(trees: Dict[str, ast.Module]) -> Dict[str, str]:
    """モジュール直下の公開検証器（`assert_*` / `verify_*`）。"""
    out: Dict[str, str] = {}
    for rel, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and (
                node.name.startswith("assert_") or node.name.startswith("verify_")
            ):
                out[node.name] = rel
    return out


def _called_in_functions(trees: Dict[str, ast.Module]) -> Set[str]:
    """到達可能な関数本体から呼ばれている関数名。

    ここでも到達可能性を見る。到達不能なヘルパからの呼び出しは実行経路では
    ないため、検証器の「配線済み」判定には使えない。
    """
    functions = _functions(trees)
    called: Set[str] = set()
    for name in _reachable_functions(trees):
        for node in functions.get(name, ()):
            called |= _calls_in(node)
    return called


def _trees() -> Dict[str, ast.Module]:
    return {rel: _tree(rel) for rel in ENFORCEMENT_MODULES}


def _unwired() -> Set[str]:
    trees = _trees()
    constants = set(_module_constants(trees))
    validators = _public_validators(trees)
    unwired = constants - _wired_constants(trees)
    unwired |= set(validators) - _called_in_functions(trees)
    return unwired


def test_no_unregistered_unwired_declaration() -> None:
    """宣言したのに実行経路から参照されない検査語彙・検証器を増やせない。

    増やしたければ `UNWIRED_REGISTRY` へ理由付きで登録するしかない。
    「書いたが効いていない」状態を無言で持ち込めなくすることが目的である。
    """
    unregistered = sorted(_unwired() - set(UNWIRED_REGISTRY))
    assert not unregistered, (
        f"宣言されたが実行経路から参照されていない: {unregistered}\n"
        "検証器なら呼び出し元へ配線し、語彙なら照合に使うこと。"
        "正当な未配線なら UNWIRED_REGISTRY へ理由付きで登録する。"
    )


def test_registry_has_no_stale_entries() -> None:
    """配線済みになった項目が登録簿へ残っていない（例外の陳腐化を防ぐ）。"""
    stale = sorted(set(UNWIRED_REGISTRY) - _unwired())
    assert not stale, (
        f"配線済みなのに UNWIRED_REGISTRY に残っている: {stale}（行を削除すること）"
    )


def test_every_registry_entry_states_a_reason() -> None:
    """例外は理由を書く。空文字や仮置きで例外を作らせない。"""
    for name, reason in UNWIRED_REGISTRY.items():
        assert reason.strip(), f"{name}: 未配線の理由が空"
        assert len(reason) >= 20, f"{name}: 理由が短すぎる（実質的な説明を書く）"


def test_pending_application_entries_name_their_future_call_site() -> None:
    """予約ガードは「いつ配線するか」を明示する。"""
    pending = {
        name: reason
        for name, reason in UNWIRED_REGISTRY.items()
        if reason.startswith("PENDING_APPLICATION:")
    }
    assert pending, "予約ガードが 1 件も無いのは想定外（実装が進んだら本テストを畳む）"
    for name, reason in pending.items():
        assert "実装時に配線する" in reason, f"{name}: 配線の条件が書かれていない"


# --- 監査そのものの検証（PR #332 Codex 第 1 巡 P2×2） ----------------------


def test_module_discovery_covers_the_whole_tree() -> None:
    """走査範囲がツリーの非テストモジュール全数であること。

    ハードコード一覧だと、§24 が予定する `measurement/` などが追加された
    とき、そこの未配線検証器が監査の視界に入らないまま全テストが通る。
    """
    expected = {
        path.relative_to(_RUN_DIR).as_posix()
        for path in _RUN_DIR.rglob("*.py")
        if not (_EXCLUDED_DIRS & set(path.relative_to(_RUN_DIR).parts))
        and not path.name.startswith("test_")
        and path.name != "conftest.py"
    }
    assert set(ENFORCEMENT_MODULES) == expected
    assert expected, "非テストモジュールが 1 件も見つからないのは想定外"
    assert not any(rel.startswith("tests/") for rel in ENFORCEMENT_MODULES)


def _synthetic(source: str) -> Dict[str, ast.Module]:
    return {"synthetic.py": ast.parse(source)}


def test_unreachable_helper_does_not_wire_a_constant() -> None:
    """到達不能な private ヘルパで触れただけでは配線済みにしない。

    これを見ないと、新しい検査定数を使われないヘルパへ書くだけで本テストが
    通り、実行経路が無いまま「配線済み」になる — 本テストが防ごうとしている
    当の退行である。
    """
    trees = _synthetic(
        "VOCAB = ('X',)\n"
        "def _never_called():\n"
        "    return VOCAB\n"
    )
    assert "VOCAB" not in _wired_constants(trees)


def test_constant_reached_through_a_public_entry_point_is_wired() -> None:
    """起点から辿れるヘルパ経由なら配線済みと数える（偽陽性の確認）。"""
    trees = _synthetic(
        "VOCAB = ('X',)\n"
        "def _helper():\n"
        "    return VOCAB\n"
        "def public_entry():\n"
        "    return _helper()\n"
    )
    assert "VOCAB" in _wired_constants(trees)


def test_validator_called_only_from_dead_code_is_unwired() -> None:
    """到達不能なコードからの呼び出しは検証器の配線と数えない。"""
    trees = _synthetic(
        "def assert_guard(x):\n"
        "    pass\n"
        "def _never_called():\n"
        "    assert_guard(1)\n"
    )
    assert "assert_guard" not in _called_in_functions(trees)


def test_audit_limitations_are_declared() -> None:
    """監査の限界が列挙され、それぞれに再入条件が書かれていること。

    「ファミリーを終端した」という主張を実態より広く書くと、それ自体が
    将来の実装者を誤らせる。捕捉できない経路は消さずに宣言しておく。
    """
    assert AUDIT_LIMITATIONS, "限界の宣言が空になっている（主張が実態を超えていないか確認）"
    for name, text in AUDIT_LIMITATIONS.items():
        assert "再入条件:" in text, f"{name}: 再入条件が書かれていない"
        assert len(text) >= 40, f"{name}: 説明が短すぎる"


def test_attribute_calls_do_not_wire_a_validator() -> None:
    """`obj.verify_x()` が同名のモジュール関数を配線済みにしない。"""
    trees = _synthetic(
        "def verify_x(v):\n"
        "    pass\n"
        "def public_entry(obj):\n"
        "    return obj.verify_x()\n"
    )
    assert "verify_x" not in _called_in_functions(trees)
