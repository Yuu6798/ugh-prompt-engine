"""C0 freeze manifest の dry-run 検証（設計正本 §3）。

**本モジュールは dry-run 検証のみを行う。** ファイル書込・secret 生成・
freeze event 記録のいずれも一切行わない（IMPLEMENTATION_MAP_v1.md §0
授権境界）。`validate_c0_manifest()` は与えられた manifest mapping を
読むだけの純関数であり、副作用を持たない。

設計正本 §18 は実行に先立つ 3 件のユーザー承認 Gate を要求する。本モジュールが
実装するのは、そのうち **Gate 2（C0 freeze の実行承認）にまだ到達していない
状態**での dry-run 事前検証のみである。武装版（実際に manifest/registry を
書き込み freeze event を記録する）freeze スクリプトは、Gate 2 承認後の
別 PR として実装される予定であり、本モジュールはそれを含まない。

## 二層判定（設計正本 §3.1 / §3.2）

- **REQUIRED_BLOCKING**（§3.1）: 欠落すると
  `vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE` を発行する。
- **RECORDED_OR_ABSENT**（§3.2）: 値または `"ABSENT:<理由>"` 文字列のいずれか
  が必須記録される。[UNDERSPEC-CAL-C07] 「必須記録」という文言を厳格に読み、
  キー自体が manifest に全く存在しない場合は REQUIRED_BLOCKING と同じ扱い
  （missing として記録）とする一方、`"ABSENT:<理由>"` という形で明示的に
  不在が記録されている場合は BLOCK せず claim ceiling 降格 annotation
  （`WEAK_ENV_LOCK`）を付与するのみに留める。設計正本の例示は
  container/image digest の 1 件のみだが、同一見出し (§3.2) 配下の
  5 項目すべてに同じ降格ルールを一律適用する（最も単純で一貫した選択。
  C0 freeze 承認時のレビュー対象）。

## pyworld 特則（§3.3）

D4C 系候補のみが必要とする `pyworld` の exact version + wheel hash が
欠落していても、**campaign 全体は BLOCK しない**。当該候補群のみ
ineligible とする（`d4c_ineligible=True` で表現。BLOCKED code は発行しない）。

## RNG 台帳（§3.3）

`rng_ledger` に列挙された stream のいずれかが unseeded と明示的に宣言されて
いる場合、`vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG` を発行する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from . import vocab

# ---------------------------------------------------------------------------
# 二層キー語彙（設計正本 §3.1 / §3.2 の機械可読な写像）
# ---------------------------------------------------------------------------

#: REQUIRED_BLOCKING（§3.1）。ドット区切りは manifest 内のネストした mapping
#: を辿るパス。値が `None` のキーも「欠落」とみなす。
REQUIRED_BLOCKING_KEYS: tuple[str, ...] = (
    "repo.url",
    "repo.commit_sha",
    "repo.dirty_tree",
    "measurement_directory_status",
    "candidates.meter_paths_sha256",
    "candidates.generator_paths_sha256",
    "candidates.schema_paths_sha256",
    "candidates.test_paths_sha256",
    "dependencies.python_version",
    "dependencies.numpy_version",
    "dependencies.scipy_version",
    "dependencies.librosa_version",
    "dependencies.soundfile_version",
    "sample_format.dtype",
    "sample_format.channel_policy",
    "sample_format.resampling_impl",
    "frozen_design.claim_critical_set",
    "frozen_design.meter_specs",
    "frozen_design.fixture_spec",
    "frozen_design.split_spec",
    "frozen_design.selection_rule",
    "frozen_design.provenance_spec",
    "independence_ledger",
    "rng_ledger",
)

#: RECORDED_OR_ABSENT（§3.2）。値 または `"ABSENT:<理由>"` のいずれかが必須。
RECORDED_OR_ABSENT_KEYS: tuple[str, ...] = (
    "env.container_image_digest",
    "env.blas_fft_backend",
    "env.os_kernel_cpu_arch",
    "env.wheel_hashes",
    "env.world_build_flags",
)

#: RECORDED_OR_ABSENT が ABSENT のとき付与する claim ceiling 降格 annotation。
WEAK_ENV_LOCK = "WEAK_ENV_LOCK"

#: pyworld 特則（§3.3）専用キー。D4C 候補群にのみ影響し、campaign 全体は BLOCK しない。
PYWORLD_VERSION_KEY = "dependencies.pyworld_version"
PYWORLD_WHEEL_HASH_KEY = "dependencies.pyworld_wheel_hash"

_ABSENT_PREFIX = "ABSENT:"


def _resolve(manifest: Mapping[str, object], dotted_path: str) -> tuple[bool, object]:
    """`dotted_path` を辿って `(found, value)` を返す。

    `found=False` はキーが存在しない、または途中の階層が mapping でない
    ため辿れなかったことを意味する。
    """
    node: object = manifest
    for part in dotted_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _is_absent_marker(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_ABSENT_PREFIX)


@dataclass(frozen=True)
class C0ValidationResult:
    """dry-run 検証結果。書込・secret 生成・freeze event のいずれも伴わない。"""

    blocked_codes: tuple[vocab.BlockedCode, ...] = ()
    missing_required_keys: tuple[str, ...] = ()
    downgrade_annotations: tuple[str, ...] = ()
    d4c_ineligible: bool = False
    d4c_ineligibility_reason: str | None = None
    unseeded_rng_streams: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_codes) > 0


def _check_required_blocking(manifest: Mapping[str, object]) -> list[str]:
    """REQUIRED_BLOCKING キーのうち欠落しているものを返す。

    `repo.dirty_tree` は「値が False であること」自体が要求（§3.1:
    「dirty-tree=false」）のため、存在していても `True` なら欠落と同様に
    扱う（fail-closed。値が `False` 以外の型・欠落も含めて違反とする）。
    """
    missing: list[str] = []
    for key in REQUIRED_BLOCKING_KEYS:
        found, value = _resolve(manifest, key)
        if not found or value is None:
            missing.append(key)
            continue
        if key == "repo.dirty_tree" and value is not False:
            missing.append(f"{key} (must be exactly false, got {value!r})")
    return missing


def _check_recorded_or_absent(
    manifest: Mapping[str, object],
) -> tuple[list[str], list[str]]:
    """`(missing_entirely, downgrade_annotations)` を返す。

    キーが manifest に全く存在しない場合は `missing_entirely` へ（§3.2 の
    「必須記録」を満たしていないため、[UNDERSPEC-CAL-C07] によりこれも
    missing 扱い）。`ABSENT:<理由>` として明示的に記録されている場合は
    BLOCK せず `WEAK_ENV_LOCK` の降格 annotation のみを積む。
    """
    missing_entirely: list[str] = []
    downgrades: list[str] = []
    for key in RECORDED_OR_ABSENT_KEYS:
        found, value = _resolve(manifest, key)
        if not found or value is None:
            missing_entirely.append(key)
            continue
        if _is_absent_marker(value):
            downgrades.append(f"{key}:{WEAK_ENV_LOCK}")
    return missing_entirely, downgrades


def _check_pyworld(manifest: Mapping[str, object]) -> tuple[bool, str | None]:
    """pyworld 特則（§3.3）: exact version + wheel hash 欠落 → D4C のみ ineligible。"""
    found_version, version = _resolve(manifest, PYWORLD_VERSION_KEY)
    found_hash, wheel_hash = _resolve(manifest, PYWORLD_WHEEL_HASH_KEY)
    version_ok = found_version and version is not None and not _is_absent_marker(version)
    hash_ok = found_hash and wheel_hash is not None and not _is_absent_marker(wheel_hash)
    if version_ok and hash_ok:
        return False, None
    return True, "pyworld exact version + wheel hash not recorded"


def _check_rng_ledger(manifest: Mapping[str, object]) -> tuple[str, ...]:
    """`rng_ledger` 中で unseeded と明示宣言された stream 名のリストを返す。

    各 entry は `{"stream_name": str, "seeded": bool, ...}` を想定する
    ([UNDERSPEC-CAL-C08] 設計正本は entry のフィールド名までは規定しない。
    最も単純な bool フラグ方式を採った)。`rng_ledger` 自体が欠落・空・
    非 list の場合はここでは検出しない（欠落は REQUIRED_BLOCKING 側で
    既に捕捉される）。
    """
    found, ledger = _resolve(manifest, "rng_ledger")
    if not found or not isinstance(ledger, Sequence) or isinstance(ledger, (str, bytes)):
        return ()
    unseeded: list[str] = []
    for entry in ledger:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("seeded") is False:
            stream_name = entry.get("stream_name", "<unnamed>")
            unseeded.append(str(stream_name))
    return tuple(unseeded)


def validate_c0_manifest(manifest: Mapping[str, object]) -> C0ValidationResult:
    """C0 freeze manifest を dry-run 検証する（書込・secret 生成・freeze event なし）。"""
    missing_required = _check_required_blocking(manifest)
    missing_recorded, downgrades = _check_recorded_or_absent(manifest)
    all_missing = tuple(missing_required + missing_recorded)

    d4c_ineligible, d4c_reason = _check_pyworld(manifest)
    unseeded_streams = _check_rng_ledger(manifest)

    blocked: list[vocab.BlockedCode] = []
    if all_missing:
        blocked.append(vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE)
    if unseeded_streams:
        blocked.append(vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG)

    return C0ValidationResult(
        blocked_codes=tuple(blocked),
        missing_required_keys=all_missing,
        downgrade_annotations=tuple(downgrades),
        d4c_ineligible=d4c_ineligible,
        d4c_ineligibility_reason=d4c_reason,
        unseeded_rng_streams=unseeded_streams,
    )
