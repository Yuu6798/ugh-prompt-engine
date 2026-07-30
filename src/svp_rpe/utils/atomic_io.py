"""utils/atomic_io.py — atomic write / bundle publish の共通実装。

以下 7 箇所に独立実装されていた atomic write / bundle publish（一部は
docstring で「層依存回避のため意図的に複製」と明言されていた）を単一実装へ
集約する（`clamp.py` / `hashing.py` と同じ、utils/ が全レイヤーの依存リーフ
という前例に倣う）:

- `cli/observe_cmd.py:_write_observation_report_atomically`（単一ファイル, text）
- `cli/recast_cmd.py:_write_recast_plan_atomically`（単一ファイル, bytes）
- `cli/builds_root.py:_publish_artifacts_atomically`（bundle, rollback 付き）
- `recast/backend.py:atomic_write_bytes`（単一ファイル, bytes）
- `recast/backend.py:atomic_publish_bytes_bundle`（bundle, rollback 付き）
- `recast/state.py:_write_recast_state_atomically`（単一ファイル, bytes）
- `recast/plan.py:_atomic_publish_text_bundle`（bundle, rollback 付き）

既存 7 関数は名前・シグネチャ・挙動を変えずにここへ委譲する薄いラッパーに
なる（呼び出し側 import 経路は不変）。

## 単一ファイル atomic write

`atomic_write_bytes` / `atomic_write_text`: tempfile（目的ファイルと同一
ディレクトリ内、`prefix=f"{path.name}."`, `suffix=".tmp"`）へ書き、
`os.replace` で目的の path へ atomic rename する。失敗時
（`BaseException` — `KeyboardInterrupt`/`SystemExit` でも staging を
残さない）は staging tempfile を best-effort で削除してから re-raise する。
7 実装のうちこの形の 3 つ（`_write_recast_plan_atomically` /
`atomic_write_bytes` / `_write_recast_state_atomically`）は互いに完全に
同一挙動だった。`_write_observation_report_atomically`（text）は
`atomic_write_text` が `content.encode(encoding)` してから
`atomic_write_bytes` へ委譲するだけの薄い違い。

## 複数ファイル bundle publish

`atomic_publish_bundle`: 「全部揃って初めて意味を持つ 1 組」を snapshot +
rollback 付きで publish する。既存ターゲットは一旦 staging 側の専用
`prev/` サブディレクトリへ退避（`os.replace`）してから、staged payload
（`payload/` サブディレクトリ側）を新ファイル一式として rename する。途中
失敗時は rename 済み分を削除し、`prev/` へ退避した snapshot を元位置へ
復元して呼び出し前と同じ状態に戻す。rollback は常に `BaseException` を
拾う（Codex P2 review round 7, PR3 #208 指摘 14 → 本 review round で全
呼び出し元に統一 — `catch` パラメータは撤去済み。詳細は
`atomic_publish_bundle` の docstring 参照）。

3 実装（`_publish_artifacts_atomically` / `atomic_publish_bytes_bundle` /
`_atomic_publish_text_bundle`）はこの骨格を共有するが、1 点で正直に異なる
ため、無理に 1 通りの固定挙動へ寄せずパラメータで吸収する:

- `always_check_collision`: `_publish_artifacts_atomically` は
  `input_paths` が空でも `contents` 各ファイルの `is_dir()` チェックを
  常に行う。`atomic_publish_bytes_bundle` / `_atomic_publish_text_bundle`
  は `protected_inputs` が truthy のときのみ衝突/`is_dir()` チェックを行う
  （呼び出し側は 3 関数とも現状 non-empty しか渡さないため実害は出ないが、
  空リストで意図的にガードを無効化できる、という設計意図の違いは保持する）。

`stale_filenames`（`atomic_publish_bytes_bundle` のみが使う機能。`contents`
には含めないが `output_dir` に存在すれば bundle publish の一部として除去する
ファイル名の集合）もパラメータとしてそのまま吸収する。

`contents` の型は 3 実装とも本質的に bytes（`_publish_artifacts_atomically`
は歴史的に `dict[str, str]` を受け取り呼び出し側で `content.encode("utf-8")`
していた分だけの違い — ここでは bytes 統一 API とし、
`_publish_artifacts_atomically` 側のラッパーが 1 回だけ encode してから
委譲する）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def _validate_bundle_name(name: str) -> None:
    """`atomic_publish_bundle` の `contents` キー / `stale_filenames` は
    フラットなファイル名のみを許す契約（ネストした相対パスは対象外 — 呼び出し
    元 3 ラッパ（`cli/builds_root.py` / `recast/backend.py` / `recast/plan.py`）
    は全て `output_dir` 直下のリテラルファイル名しか渡さないため、この契約で
    十分・字句検証のみで足りる）。

    空文字 / `"."` / `".."` / 絶対パス / パス区切り文字（`/`, `\\`, `os.sep`,
    `os.altsep`）を含む名前は `output_dir / name` の join が staging/出力
    ルートの外へ脱出しうるため `ValueError` で拒否する（Codex P2 review 指摘:
    `"../victim"` や絶対パスを渡すと rename 前に外部ファイルを上書きし、失敗時
    rollback が「上書き後のバイト列」を復元してしまっていた）。
    """
    if not name or name in (".", ".."):
        raise ValueError(f"invalid bundle entry name: {name!r}")
    if os.path.isabs(name):
        raise ValueError(f"invalid bundle entry name (absolute path not allowed): {name!r}")
    if "/" in name or "\\" in name or os.sep in name or (os.altsep and os.altsep in name):
        raise ValueError(f"invalid bundle entry name (must be a flat filename): {name!r}")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """`path` と同一ディレクトリの tempfile へ `data` を書き、`os.replace` で
    atomic に publish する（親ディレクトリは `mkdir(parents=True, exist_ok=True)`
    で必要なら作成する）。失敗時は staging tempfile を best-effort で削除して
    から re-raise する（削除自体の失敗は無視する）。
    """
    path = Path(path)
    output_dir = path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """`atomic_write_bytes` のテキスト版: `content` を `encoding` で 1 回だけ
    encode してから委譲する。"""
    atomic_write_bytes(Path(path), content.encode(encoding))


def atomic_publish_bundle(
    output_dir: Path,
    contents: Dict[str, bytes],
    *,
    protected_inputs: Iterable[Path],
    stale_filenames: Tuple[str, ...] = (),
    always_check_collision: bool = False,
    collision_message: str = "output path collides with a protected input path",
) -> Path:
    """複数ファイルを「全部揃って初めて意味を持つ 1 組」として atomic publish
    する（snapshot + rollback 付き）。`output_dir` を返す。

    `protected_inputs` は keyword-only の必須引数（既定値なし）。省略すると
    衝突検出が黙って無効化されてしまう fail-closed 契約のため、呼び出し側は
    常に明示的に渡す必要がある — 意図的に検査しない場合も空タプル `()` を
    明示的に渡すこと。

    `contents` のキーおよび `stale_filenames` の各名前は `output_dir` 直下の
    フラットなファイル名のみを契約とする（`_validate_bundle_name` 参照）。
    空文字 / `"."` / `".."` / 絶対パス / パス区切り文字を含む名前は、書き込み・
    削除を一切行う前に `ValueError` で拒否する。

    衝突検査（`protected_inputs` があれば、または `always_check_collision=True`
    のとき）: `contents`（+ `stale_filenames` のうち `contents` に含まれない
    もの）の各ファイル名について、解決済みパスが `protected_inputs` のいずれか
    と一致すれば `ValueError`。`contents` に含まれるファイルについてはさらに
    既存の同名パスがディレクトリであれば同じく `ValueError`（`stale_filenames`
    専用のエントリはこの `is_dir` チェックの対象外 — 除去対象であって書き込み
    対象ではないため）。

    `collision_message`: 衝突エラーの文言（`: {target}` が続く）。
    `recast/backend.py:atomic_publish_bytes_bundle` /
    `recast/plan.py:_atomic_publish_text_bundle` は
    `"output path collides with a protected input path"`（このデフォルト）を
    使い、`cli/builds_root.py:_publish_artifacts_atomically` は
    `"input path collides with output artifact path"` を使う — 呼び出し元の
    既存テスト（`tests/test_recast_cli.py` / `tests/test_recast_backend.py` /
    `tests/test_builds_root_cli.py` / `tests/test_performance_package.py` /
    `tests/test_observe.py`）がこの文言に対して assert しているため、集約後も
    一言一句保つ必要がある（正直会計 — 意味は同じでも文言までは 1 つに
    寄せない）。`is_dir` エラー文言（`"output path is an existing directory"`）
    は 3 実装とも元から同一だったため据え置き。

    publish 本体: staging（`output_dir` 配下の `TemporaryDirectory`）内に
    `payload/`（staged payload）と `prev/`（snapshot）の 2 つの専用
    サブディレクトリを、いかなる書き込みより前に mkdir する（Codex P2
    review 指摘: `payload/` を用意せず `staging_dir` 直下へ直接
    payload を書いていた旧実装では、`contents = {"prev": ...}` のような
    正当なフラット名が `snapshot_dir = staging_dir / "prev"` の `mkdir()`
    と衝突し `FileExistsError` で publish 不能になっていた。bundle 名は
    `_validate_bundle_name` でパス区切り文字を拒否済み・フラット名のみの
    ため、`payload/` / `prev/` という固定名の予約語であっても
    `payload_dir / name` / `snapshot_dir / name` はそれぞれの区画の直下に
    閉じ、bundle 名自身（`"prev"` や `"payload"` を含む）と構造的に衝突
    しない）。`contents` を `payload/` へ書き切ってから、`contents` +
    除去対象の `stale_filenames` それぞれについて既存ターゲットを `prev/`
    へ snapshot し、その後 `payload/` の各ファイルを最終位置へ
    `os.replace` する。この過程で例外（`BaseException` — 常にこれを拾う。
    `KeyboardInterrupt`/`SystemExit` 系の中断シグナルで rollback をスキップ
    すると、`TemporaryDirectory` の unwind で snapshot ごと消え、旧
    artifact が失われた mixed bundle が `output_dir` に残ってしまうため、
    `catch` パラメータで例外型を選ばせる余地を撤去し全呼び出し元をこの
    挙動へ統一した）が飛べば、rename 済み分を削除し `prev/` snapshot を
    元位置へ復元して `output_dir` を呼び出し前と同じ状態に戻してから
    re-raise する。
    """
    for filename in list(contents) + list(stale_filenames):
        _validate_bundle_name(filename)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    protected_list = list(protected_inputs)
    stale_only = [name for name in stale_filenames if name not in contents]

    if protected_list or always_check_collision:
        resolved_inputs = {Path(candidate).resolve() for candidate in protected_list}
        for filename in list(contents) + stale_only:
            target = output_dir / filename
            if target.resolve() in resolved_inputs:
                raise ValueError(f"{collision_message}: {target}")
            if filename in contents and target.is_dir():
                raise ValueError(f"output path is an existing directory: {target}")

    with tempfile.TemporaryDirectory(dir=output_dir) as staging:
        staging_dir = Path(staging)
        # `payload/`（staged payload）と `prev/`（snapshot）を、いかなる
        # 書き込みより前に両方 mkdir する。bundle 名はフラット名検証済み
        # のため `staging_dir` 直下を占有せず、"prev"/"payload" を含む任意
        # のフラット名が両ディレクトリの固定名と衝突せず publish できる
        # （docstring 参照）。
        payload_dir = staging_dir / "payload"
        payload_dir.mkdir()
        snapshot_dir = staging_dir / "prev"
        snapshot_dir.mkdir()

        for filename, data in contents.items():
            (payload_dir / filename).write_bytes(data)

        snapshots: Dict[str, Path] = {}
        published: List[str] = []
        try:
            for filename in list(contents) + stale_only:
                target = output_dir / filename
                if target.exists():
                    previous = snapshot_dir / filename
                    os.replace(target, previous)
                    snapshots[filename] = previous
            for filename in contents:
                os.replace(payload_dir / filename, output_dir / filename)
                published.append(filename)
        except BaseException:
            for filename in published:
                try:
                    os.unlink(output_dir / filename)
                except OSError:
                    pass
            for filename, previous in snapshots.items():
                try:
                    os.replace(previous, output_dir / filename)
                except OSError:
                    pass
            raise
    return output_dir
