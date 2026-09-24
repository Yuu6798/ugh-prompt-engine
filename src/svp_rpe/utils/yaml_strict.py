"""utils/yaml_strict.py — 重複 mapping キーを拒否する YAML SafeLoader の共通ファクトリ。

`_no_dup_construct_mapping`（mapping ノードを walk し、同一キーが 2 度現れたら
fail-closed で拒否する `construct_mapping` 実装）+ それを ``DEFAULT_MAPPING_TAG``
へ ``add_constructor`` する ``_NoDupSafeLoader`` サブクラス定義は、元々
``melody/representation.py`` と 6 本の ``scripts/*.py`` に独立複製されていた。
現在ここへ一本化しているのは ``melody/representation.py`` のみで、
``scripts/*.py`` 側の複製は意図的に独立のまま残す。

**``scripts/*.py`` を dedup しない理由**: これらのスクリプトは generator
provenance closure（自身の `.py` bytes や、AST import 走査で辿る first-party
コード一式の sha256）を自己計算し、その digest を実測結果に埋め込む
（`generator_code_sha256` / `generator_script_sha256`）。一部は committed な
測定記録（`docs/measurements/**/*.json`）にその digest を pin 済み。ここで
本モジュールを import させると (1) 既存 pin と bytes が食い違い記録が壊れるか、
(2) 新規 import が closure 探索に混入して hash が不完全に変わるかのいずれかに
なる。したがって dedup は「provenance closure に参加しないコード」に限定する
（本モジュール自身と ``melody/representation.py`` はそれに該当する）。

各呼び出しサイトの duplicate-key エラーメッセージ・例外型はサイトごとに異なる
（登録簿名の埋め込み・``ValueError``/``GenerationError``/``BuildM3dPairsError``
の使い分け）ため、本モジュールは walk ロジックのみを共有し、メッセージ文言・
例外型は呼び出し側が ``make_error`` コールバックでそのまま注入する
（既存の挙動をバイト単位で再現するため）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import yaml


def build_no_dup_construct_mapping(
    make_error: Callable[[Any], BaseException],
    *,
    deep: bool = False,
) -> Callable[[Any, Any], Dict[Any, Any]]:
    """duplicate 検出時に ``make_error(key)`` を raise する `construct_mapping` 実装を返す。

    ``deep`` は「key/value ノードの構築を即座に行うか（``construct_object`` の
    ``deep`` 引数）」を固定する。PyYAML は ``add_constructor`` 経由のカスタム
    コンストラクタを常に ``constructor(loader, node)`` の 2 引数で呼び出す
    （``deep`` を渡さない）ため、旧実装の「引数 ``deep: bool = False`` を持つが
    実質常にデフォルト値で呼ばれる」サイトと「``deep=True`` を内部で固定する」
    サイト（``scripts/make_vremix_fixtures.py``）の双方を、ここでの ``deep``
    キーワードで等価に再現できる。
    """

    def _construct_mapping(loader: "yaml.SafeLoader", node: Any) -> Dict[Any, Any]:
        mapping: Dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise make_error(key)
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    return _construct_mapping


def make_no_dup_safe_loader(
    make_error: Callable[[Any], BaseException],
    *,
    deep: bool = False,
) -> type:
    """重複 mapping キーを ``make_error(key)`` で拒否する ``yaml.SafeLoader`` サブクラスを返す。

    呼び出しごとに新しいクラスを生成する（``add_constructor`` はクラス単位の
    グローバル状態のため、複数サイトが同一クラスを共有すると互いの
    ``make_error`` を上書きしてしまう）。
    """

    class _NoDupSafeLoader(yaml.SafeLoader):
        pass

    _NoDupSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        build_no_dup_construct_mapping(make_error, deep=deep),
    )
    return _NoDupSafeLoader


def safe_load_no_duplicate_keys(data: "str | bytes", *, context: str) -> Any:
    """重複 mapping キーを拒否しつつ YAML を parse する汎用ヘルパー。

    既存の呼び出しサイト（``melody/representation.py`` と、独立複製のまま残る
    6 本の ``scripts/*.py``）はそれぞれ固有のエラーメッセージ/例外型を持つため
    ``make_no_dup_safe_loader`` を直接使うが、本関数は「サイト固有の文言を
    必要としない新規呼び出し元」向けの簡易版（``ValueError`` + ``context`` を
    埋め込んだ汎用メッセージ）。
    """

    def _dup_key_error(key: Any) -> ValueError:
        return ValueError(f"duplicate YAML mapping key {key!r} in {context} (fail-closed)")

    loader_cls = make_no_dup_safe_loader(_dup_key_error)
    return yaml.load(data, Loader=loader_cls)  # noqa: S506 (dup-key 拒否付き SafeLoader)
