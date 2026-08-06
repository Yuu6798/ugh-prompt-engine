"""L0b info-isolation payload composer (`docs/llm_adapter_planning.md` §2 D2 /
§3, AGENTS.md §8「情報遮断実験のペイロード組成は機械化する」2026-08-06 制定).

`compose_payload.py --manifest <manifest.yaml> --out-dir <dir>` reads a
declared payload manifest (a flat list of `{role, path, sha256}` entries) and
composes the author-visible payload for one L0b round as a machine transcript
of exactly those declared files — nothing else. The script defines no
free-text option (`--note`/`--comment`/...): the only inputs are `--manifest`
and `--out-dir`, so a coordinator has no place to attach prose to a composed
payload even if they wanted to.

出典: 事故実測 = PR #247（`docs/l0b_closed_loop_record.md` §3.2）——
コーディネーターが round 3/4/5 のペイロードへ添えた「ペイロード内から導出
可能な事実の要約注記」が著者のレバー選択を実際に分岐させ、唯一の改善実測が
off-contract 除外され、最後の未汚染周回から系列を丸ごと再実行するコストを
要した。この計器はその混入経路を API 面で塞ぐ——注記を書く自由文フィールド
自体を持たない。

Manifest schema (`l0b-payload-manifest/0.1`):

    schema_version: l0b-payload-manifest/0.1
    experiment_id: <slug, ^[a-z0-9][a-z0-9_-]{0,63}$>
    round_label: <slug, 同上>
    parts:
      - role: contract | task | own_score | own_intent | diff_report | validation_errors
        path: <manifest ファイルの親ディレクトリ基準の相対パス>
        sha256: <64 桁 hex — 事前 pin 必須>

多重度規則（`_validate_multiplicity`）: 各 role は高々 1 回。`contract`/
`task` は必須。`own_score`/`own_intent` は同時に存在するか同時に不在。
`diff_report`/`validation_errors` は同時に高々 1 つ（排他）で、存在する場合
`own_score`（および `own_intent`）の存在が前提——前周提出物なしに報告だけ
渡す組成は不正。

YAML は重複キーを拒否する（`svp_rpe.melody.representation._NoDupSafeLoader`
を流用——重複実装しない）。トップレベル非 dict・未知キー・必須キー欠落は
いずれも `PayloadManifestError`（AGENTS.md §8「parse 可能 ≠ 形状正しい」則:
parse 成功だけでは形状を保証しない）。`parts` の**不在**（キー自体が無い）
と**空リスト**（`parts: []`）は異なるエラーとして区別する——前者はトップ
レベルの必須キー欠落（`"parts" not in data` の不在チェック）、後者は
`parts` というキー自体は構造的に妥当なリストとして受理された上で、多重度
規則（`contract`/`task` 必須）に落ちて拒否される。不在チェックを truthy
判定（`if not data.get("parts")`）で書くと両者の区別が失われる（AGENTS.md
§8「truthy 判定を正規形ガードに使わない」則）ため、この関数群は一貫して
`in`/`is not None` の厳密比較で不在を判定する。

各 part の処理（`_process_part`）: `path` を manifest ファイルの親ディレク
トリ基準へ封じ込め検証（`svp_rpe.arrange.pathsafe.resolve_confined` を
流用——絶対パス・`../` 脱出はいずれもここで拒否）→ バイトを 1 回だけ読む
（single-read）→ sha256 を宣言値と照合（不一致は emit 前に拒否）→ UTF-8
strict デコード（失敗はバイナリ拒否）→ 区切り衝突チェック（content の行が
`=== PAYLOAD` / `=== PART ` / `=== END ` で始まれば fail-closed——ペイロード
境界の偽装・誤読を防ぐ）。全 part の検証が終わるまで `--out-dir` へは何も
書き込まない（sha256 不一致等のエラー時に部分的な出力が残らない）。

出力: `--out-dir` 直下に固定名 `payload.md`（逐字転写 + 固定テンプレート、
content が改行で終わらない part のみ改行を 1 つ補い `newline_appended: true`
を記録）と `payload.manifest.json`（`sort_keys=True`・`indent=2`・
`ensure_ascii=False`・末尾改行の決定論直列化、`payload_sha256` と各 part の
`{index, role, path, sha256, bytes, newline_appended}` を含む）を
`svp_rpe.utils.atomic_io.atomic_write_bytes` で publish する。どちらかが
既存なら書き込み前に `ProtectedPathError`（上書き禁止）。タイムスタンプ・
乱数・環境依存値は一切含めない——同一 manifest + 同一入力ファイル群は常に
バイト同一の `payload.md`/`payload.manifest.json` を生成する。

`payload.md` の publish 成功後に `payload.manifest.json` の publish が失敗
すると、pin manifest を欠いた `payload.md` だけが「完全なペイロード」に見
えて残留し、かつ再実行は上書き禁止チェックに拒否される（Codex review P2
指摘）。これを防ぐため、`compose_payload` は 2 本目の publish を
try/except で包み、失敗時は 1 本目（`payload.md`）を unlink してから元の
例外を再送出する——publish は「両方揃う」か「どちらも残らない」かの 2 状
態に限定する（rollback）。unlink 自体が失敗した場合は隠さず、元の例外へ
unlink 失敗の文脈を付けて送出する。

**境界宣言**: この rollback がカバーするのは「2 本目の書き込み失敗」のみ。
`os.replace` による 2 回の rename（`atomic_write_bytes` 内部、1 本目・2 本
目それぞれ）の**間**でプロセスが強制終了するウィンドウは、この rollback
の対象外のまま残る——構造的な 2 ファイル原子性（ディレクトリ単位の
swap 等）は本計器のスコープ外の再設計を要する。運用上、そのウィンドウで
の残留を検出した場合（`payload.md` のみ存在し `payload.manifest.json` が
無い）は「payload.md 単独残留 = 不完全公開」として手動で `payload.md` を
削除してから再実行する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from svp_rpe.arrange.pathsafe import PathConfinementError, resolve_confined
from svp_rpe.melody.representation import _NoDupSafeLoader
from svp_rpe.utils.atomic_io import atomic_write_bytes

SCHEMA_VERSION = "l0b-payload-manifest/0.1"
PIN_SCHEMA_VERSION = "l0b-payload-pin/0.1"
PAYLOAD_FILENAME = "payload.md"
MANIFEST_FILENAME = "payload.manifest.json"

# canonical 組成順（manifest 内の記載順は自由——組成時にこの順へ決定論ソートする）。
PART_ROLE_ORDER: tuple[str, ...] = (
    "contract",
    "task",
    "own_score",
    "own_intent",
    "diff_report",
    "validation_errors",
)
_PART_ROLES = frozenset(PART_ROLE_ORDER)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_TOP_LEVEL_KEYS: tuple[str, ...] = ("schema_version", "experiment_id", "round_label", "parts")
_PART_KEYS: tuple[str, ...] = ("role", "path", "sha256")

# ペイロード文書のテンプレート。experiment/round に依存しない部分（
# `_FOOTER_LINE`）は固定文字列、それ以外は format プレースホルダを含む文字列
# そのもの——`compose_payload` がここでのみ値を埋める。
_HEADER_TEMPLATE = (
    "=== PAYLOAD l0b-payload/0.1 experiment={experiment_id} round={round_label} "
    "parts={n_parts} ==="
)
_PART_HEADER_TEMPLATE = "=== PART {index}/{n_parts} role={role} path={path} sha256={sha256} ==="
_PART_FOOTER_TEMPLATE = "=== END PART {index}/{n_parts} ==="
_FOOTER_LINE = "=== END PAYLOAD ==="

# 区切り衝突チェック（fail-closed）。content 内の行がこれらのいずれかで
# 始まっていたら、ペイロード境界の偽装・誤読を防ぐため組成そのものを拒否する。
_DELIMITER_PREFIXES: tuple[str, ...] = ("=== PAYLOAD", "=== PART ", "=== END ")


class PayloadManifestError(ValueError):
    """宣言 manifest の形状・多重度違反、または part 検証（封じ込め・sha256・
    UTF-8・区切り衝突）違反。"""


class ProtectedPathError(RuntimeError):
    """`--out-dir` 直下の出力ファイルが既に存在する（上書き禁止）。"""


# 宣言 manifest 内の 1 part、組成中の内部状態（part 1 件分の検証済み転写
# 内容）、および解析済み manifest 全体は、いずれもプレーンな `dict` として
# 扱う（この standalone script は `pareto_eval.py`/`run_round.py` と同じ
# 「examples/ 側は plain dict、pydantic モデルは src/svp_rpe 側」という
# 分業を踏襲する——加えて、`importlib.util.module_from_spec` で
# `sys.modules` 未登録のまま `exec_module` するテストの読み込み方式
# （`tests/test_l0b_scripts.py`'s `_load_module`）は `dataclass` が参照する
# `cls.__module__` を `sys.modules` 上で解決できず壊れるため、この
# standalone script では frozen dataclass を使わない）。


def _require_mapping(data: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PayloadManifestError(f"{what} must be a mapping (YAML dict), got {type(data).__name__}")
    return data


def _load_manifest_mapping(path: Path) -> dict[str, Any]:
    """宣言 manifest YAML を読む。重複 mapping キーは `_NoDupSafeLoader`
    （`melody/representation.py`、重複実装禁止のため import のみ）が拒否する。"""

    with path.open(encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    return _require_mapping(data, what=f"payload manifest ({path})")


def _validate_top_level_keys(data: dict[str, Any]) -> None:
    unknown = sorted(set(data) - set(_TOP_LEVEL_KEYS))
    if unknown:
        raise PayloadManifestError(
            f"payload manifest has unknown key(s): {unknown!r} (allowed: {list(_TOP_LEVEL_KEYS)!r})"
        )
    # 不在チェックは `in`/`not in`（厳密な存在判定）で書く——truthy 判定
    # （例: `if not data.get("parts")`）だと「キー自体が無い」と「値が空の
    # リスト」を同一視してしまい、AGENTS.md §8「truthy 判定を正規形ガードに
    # 使わない」則が禁じる穴になる（`parts` 不在と `parts: []` を別エラーと
    # して区別する、というこのモジュールの契約が壊れる）。
    missing = [key for key in _TOP_LEVEL_KEYS if key not in data]
    if missing:
        raise PayloadManifestError(f"payload manifest is missing required key(s): {missing!r}")


def _validate_slug(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or _SLUG_PATTERN.fullmatch(value) is None:
        raise PayloadManifestError(
            f"{field_name} must match {_SLUG_PATTERN.pattern!r}, got {value!r}"
        )
    return value


def _validate_part_raw(index: int, raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise PayloadManifestError(f"manifest.parts[{index}] must be a mapping, got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(_PART_KEYS))
    if unknown:
        raise PayloadManifestError(
            f"manifest.parts[{index}] has unknown key(s): {unknown!r} (allowed: {list(_PART_KEYS)!r})"
        )
    missing = [key for key in _PART_KEYS if key not in raw]
    if missing:
        raise PayloadManifestError(f"manifest.parts[{index}] is missing required key(s): {missing!r}")

    role = raw["role"]
    if role not in _PART_ROLES:
        raise PayloadManifestError(
            f"manifest.parts[{index}].role must be one of {sorted(_PART_ROLES)!r}, got {role!r}"
        )
    path = raw["path"]
    if not isinstance(path, str) or not path:
        raise PayloadManifestError(
            f"manifest.parts[{index}].path must be a non-empty string, got {path!r}"
        )
    sha256 = raw["sha256"]
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise PayloadManifestError(
            f"manifest.parts[{index}].sha256 must match {_SHA256_PATTERN.pattern!r}, got {sha256!r}"
        )
    return {"role": role, "path": path, "sha256": sha256}


def _validate_multiplicity(parts: list[dict[str, str]]) -> None:
    roles_seen: dict[str, int] = {}
    for part in parts:
        roles_seen[part["role"]] = roles_seen.get(part["role"], 0) + 1

    duplicated = sorted(role for role, count in roles_seen.items() if count > 1)
    if duplicated:
        raise PayloadManifestError(f"role(s) declared more than once in manifest.parts: {duplicated!r}")

    if "contract" not in roles_seen:
        raise PayloadManifestError("manifest.parts must declare exactly one role='contract' part")
    if "task" not in roles_seen:
        raise PayloadManifestError("manifest.parts must declare exactly one role='task' part")

    has_score = "own_score" in roles_seen
    has_intent = "own_intent" in roles_seen
    if has_score != has_intent:
        raise PayloadManifestError(
            "role='own_score' and role='own_intent' must both be present or both be "
            f"absent (own_score present={has_score}, own_intent present={has_intent})"
        )

    has_diff = "diff_report" in roles_seen
    has_val_errors = "validation_errors" in roles_seen
    if has_diff and has_val_errors:
        raise PayloadManifestError(
            "role='diff_report' and role='validation_errors' are mutually exclusive"
        )
    if (has_diff or has_val_errors) and not has_score:
        raise PayloadManifestError(
            "role='diff_report'/role='validation_errors' require role='own_score' (and "
            "role='own_intent') to also be present — a report about a prior submission "
            "with no submission present is not a valid composition"
        )


def _parse_manifest(path: Path) -> dict[str, Any]:
    data = _load_manifest_mapping(path)
    _validate_top_level_keys(data)

    if data["schema_version"] != SCHEMA_VERSION:
        raise PayloadManifestError(
            f"payload manifest schema_version must be {SCHEMA_VERSION!r}, got "
            f"{data['schema_version']!r}"
        )
    experiment_id = _validate_slug(data["experiment_id"], field_name="experiment_id")
    round_label = _validate_slug(data["round_label"], field_name="round_label")

    parts_raw = data["parts"]
    if not isinstance(parts_raw, list):
        raise PayloadManifestError(f"manifest.parts must be a list, got {type(parts_raw).__name__}")
    parts = [_validate_part_raw(index, raw) for index, raw in enumerate(parts_raw)]
    _validate_multiplicity(parts)

    return {"experiment_id": experiment_id, "round_label": round_label, "parts": parts}


def _process_part(part: dict[str, str], *, base_dir: Path) -> dict[str, Any]:
    role = part["role"]
    declared_path = part["path"]
    declared_sha256 = part["sha256"]
    try:
        resolved = resolve_confined(declared_path, base_dir)
    except PathConfinementError as exc:
        raise PayloadManifestError(
            f"role={role!r} path={declared_path!r} failed containment under {base_dir}: {exc}"
        ) from exc

    # single-read: 生バイトを 1 回だけ読み、sha256 照合と UTF-8 デコードの
    # 両方に同一バイト列を使う。
    data = resolved.read_bytes()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != declared_sha256:
        raise PayloadManifestError(
            f"sha256 mismatch for role={role!r} path={declared_path!r}: "
            f"declared={declared_sha256} actual={actual_sha256} — refusing to compose "
            "(pin verification failed before any output is emitted)"
        )

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadManifestError(
            f"role={role!r} path={declared_path!r} is not valid UTF-8 text — binary "
            "parts are rejected (the payload document is text-only)"
        ) from exc

    for line in content.splitlines():
        if line.startswith(_DELIMITER_PREFIXES):
            raise PayloadManifestError(
                f"role={role!r} path={declared_path!r} contains a line colliding with "
                f"the payload delimiter grammar {_DELIMITER_PREFIXES!r}: {line!r} — "
                "refusing to compose (fail-closed against payload-boundary forgery)"
            )

    newline_appended = not content.endswith("\n")
    if newline_appended:
        content = content + "\n"

    return {
        "role": role,
        "path": declared_path,
        "sha256": actual_sha256,
        "content": content,
        "byte_count": len(data),
        "newline_appended": newline_appended,
    }


def compose_payload(manifest_path: Path, out_dir: Path) -> dict[str, Any]:
    """`manifest_path` を読み、`out_dir` 直下へ `payload.md` +
    `payload.manifest.json` を組成する（モジュール docstring が正本）。戻り
    値は `{payload_path, manifest_path, payload_sha256, parts}`。"""

    manifest_path = Path(manifest_path)
    out_dir = Path(out_dir)
    base_dir = manifest_path.resolve().parent

    manifest = _parse_manifest(manifest_path)

    payload_path = out_dir / PAYLOAD_FILENAME
    manifest_out_path = out_dir / MANIFEST_FILENAME
    # 上書き禁止は他のどの処理よりも先に確認する（fail-fast）。
    if payload_path.exists():
        raise ProtectedPathError(f"refusing to overwrite existing payload file: {payload_path}")
    if manifest_out_path.exists():
        raise ProtectedPathError(
            f"refusing to overwrite existing payload manifest file: {manifest_out_path}"
        )

    ordered_parts = sorted(manifest["parts"], key=lambda part: PART_ROLE_ORDER.index(part["role"]))
    processed = [_process_part(part, base_dir=base_dir) for part in ordered_parts]

    n_parts = len(processed)
    document = (
        _HEADER_TEMPLATE.format(
            experiment_id=manifest["experiment_id"],
            round_label=manifest["round_label"],
            n_parts=n_parts,
        )
        + "\n"
    )
    for index, part in enumerate(processed, start=1):
        document += (
            _PART_HEADER_TEMPLATE.format(
                index=index,
                n_parts=n_parts,
                role=part["role"],
                path=part["path"],
                sha256=part["sha256"],
            )
            + "\n"
        )
        document += part["content"]
        document += _PART_FOOTER_TEMPLATE.format(index=index, n_parts=n_parts) + "\n"
    document += _FOOTER_LINE + "\n"

    payload_bytes = document.encode("utf-8")
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    manifest_parts = [
        {
            "index": index,
            "role": part["role"],
            "path": part["path"],
            "sha256": part["sha256"],
            "bytes": part["byte_count"],
            "newline_appended": part["newline_appended"],
        }
        for index, part in enumerate(processed, start=1)
    ]
    pin_manifest = {
        "schema_version": PIN_SCHEMA_VERSION,
        "experiment_id": manifest["experiment_id"],
        "round_label": manifest["round_label"],
        "payload_file": PAYLOAD_FILENAME,
        "payload_sha256": payload_sha256,
        "parts": manifest_parts,
    }
    manifest_bytes = (
        json.dumps(pin_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    atomic_write_bytes(payload_path, payload_bytes)
    try:
        atomic_write_bytes(manifest_out_path, manifest_bytes)
    except BaseException as exc:
        # Rollback publish: 2 本目の書き込みが失敗した場合、1 本目
        # （`payload_path`）だけが残留すると pin manifest なしの「不完全な
        # ペイロード」が完全な公開物に見えてしまう上、再実行は上書き禁止
        # チェック（`ProtectedPathError`、本関数冒頭）で拒否される。
        # publish は「両方揃う」か「どちらも残らない」かの 2 状態に限定する。
        try:
            payload_path.unlink()
        except OSError as unlink_exc:
            raise OSError(
                f"payload manifest publish failed ({exc!r}) and rollback of the "
                f"already-published payload file {payload_path} also failed "
                f"({unlink_exc!r}) — payload.md may remain orphaned without a "
                "matching payload.manifest.json; manual cleanup required before "
                "re-running"
            ) from exc
        raise

    return {
        "payload_path": payload_path,
        "manifest_path": manifest_out_path,
        "payload_sha256": payload_sha256,
        "parts": manifest_parts,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    """引数は `--manifest`/`--out-dir` の 2 つのみ（+ argparse 既定の
    `-h`/`--help`）。自由記述の注入口（`--note`/`--comment`/`--extra` 類）を
    一切定義しない——これが本モジュールの中心的な安全特性であり、
    `tests/test_l0b_scripts.py` がこの parser を検査してオプション集合を
    直接 assert する。"""

    parser = argparse.ArgumentParser(description="L0b info-isolation payload composer")
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Path to the declared payload manifest YAML"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        dest="out_dir",
        help="Directory to write payload.md/payload.manifest.json into",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = compose_payload(args.manifest, args.out_dir)
    except (ValueError, ProtectedPathError, OSError) as exc:
        # `ValueError` (not just `PayloadManifestError`, one of its
        # subclasses) so a raw duplicate-mapping-key error raised directly by
        # `_NoDupSafeLoader`'s constructor (`melody/representation.py`,
        # imported rather than reimplemented) is also caught here as a clean
        # CLI error rather than an uncaught traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"wrote {result['payload_path']} ({len(result['parts'])} part(s), "
        f"sha256={result['payload_sha256']}) manifest={result['manifest_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
