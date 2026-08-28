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

1. `run10_schema` のモジュール定数は、いずれかの関数本体から load されるか、
   load される別の定数へモジュール階層で取り込まれていること（推移閉包）。
2. 公開検証器（`assert_*` / `verify_*`）は、パッケージ内の**非テスト**
   モジュールの関数本体から呼ばれていること。

例外は `UNWIRED_REGISTRY` に理由付きで登録する。登録簿に無い未配線が現れたら
落ちるし、配線済みになったのに登録簿へ残っている項目でも落ちる（陳腐化防止）。
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

# 実行経路とみなすモジュール（テストを含まない）。
ENFORCEMENT_MODULES: Tuple[str, ...] = (
    "run10_schema.py",
    "private_boundary.py",
    "af01_freeze_verifier.py",
    "pre_run/build_pre_run_inventory.py",
)

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


def _tree(rel: str) -> ast.Module:
    return ast.parse((_RUN_DIR / rel).read_text(encoding="utf-8"))


def _module_constants(tree: ast.Module) -> Dict[str, ast.expr]:
    """モジュール直下の ALL_CAPS 定数（名前 → 値の式）。"""
    out: Dict[str, ast.expr] = {}
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


def _names_loaded_in_functions(trees: Dict[str, ast.Module]) -> Set[str]:
    """関数本体で load される識別子（= 実行時に参照される名前）。"""
    used: Set[str] = set()
    for tree in trees.values():
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(func):
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                        used.add(node.id)
    return used


def _wired_constants(trees: Dict[str, ast.Module]) -> Set[str]:
    """実行時に参照される定数の推移閉包。

    `CORE_PIN_FIELDS` のように、関数からは直接触られず `_STAGE_FIELDS` 経由で
    効いている定数がある。モジュール階層での取り込みを辿って配線済みと数える。
    """
    constants = _module_constants(trees["run10_schema.py"])
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
    called: Set[str] = set()
    for tree in trees.values():
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call):
                        target = node.func
                        if isinstance(target, ast.Name):
                            called.add(target.id)
                        elif isinstance(target, ast.Attribute):
                            called.add(target.attr)
    return called


def _trees() -> Dict[str, ast.Module]:
    return {rel: _tree(rel) for rel in ENFORCEMENT_MODULES}


def _unwired() -> Set[str]:
    trees = _trees()
    constants = set(_module_constants(trees["run10_schema.py"]))
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
