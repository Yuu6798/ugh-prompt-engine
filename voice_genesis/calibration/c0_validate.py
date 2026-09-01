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
  `vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE` を発行する。単なる
  キー存在チェックに留めず、以下の内容検証も行う（Codex レビュー
  2026-09-01 P1: 空コンテナの hollow manifest が存在チェックのみでは
  通過してしまうため）:
  - 文字列値は非空（空白のみも不可）、mapping/list 値は非空（`{}`/`[]` は
    「未記録」と同義として missing 扱い）。
  - path+hash 系マップ（`candidates.*_paths_sha256`）は各エントリが
    `path -> sha256` 形状で、path は非空文字列、sha256 は 64 桁の小文字
    16 進文字列であることを検査する（`[UNDERSPEC-CAL-C10]`）。加えて、4 マップ
    の**合併集合**が `calibration_path_inventory()`（実リポジトリの
    `voice_genesis/calibration/**/*.py` 全件）と厳密一致することを要求する
    （欠落 path・inventory に無い unknown/extra path をそれぞれ個別列挙。
    `[UNDERSPEC-CAL-C14]`。Codex レビュー 2026-09-01 P1: 従来は supplied
    entries の形状のみを検証しており、ファイルを丸ごと省略しても通過して
    しまっていた）。
  - `frozen_design.meter_specs` は `candidates.registry.ALL_CANDIDATES` が
    定義する全 meter family をカバーする（欠落 meter は
    `frozen_design.meter_specs.<METER_ID>` として個別に列挙する。
    `[UNDERSPEC-CAL-C11]`）。
  - `independence_ledger` は非空 mapping であり、各エントリの値が
    `vocab.IndependenceTier` の閉語彙に属する文字列であることを検査する。
    加えて、ledger のキー集合は `candidates.registry.ALL_CANDIDATES` が定義
    する凍結 99 候補の candidate_id 全集合と完全一致することを要求する
    （欠落・unknown/extra はそれぞれ個別列挙）。各 entry の tier は registry
    が宣言する当該候補の tier と一致するかも cross-check する
    （`[UNDERSPEC-CAL-C12]`。Codex レビュー 2026-09-01 P1: 従来はキー集合の
    網羅性を一切検査していなかった）。
  - `rng_ledger` は非空 list であり、各エントリが `stream_name`（非空文字列）
    と `seeded`（bool）を持ち、`seeded=True` のエントリは非空の
    `public_seed_id`（seed 参照。§3.3「stream 列挙 + seed 参照」に対応。
    `streams.RngLedgerEntry.public_seed_id` と命名を揃えた）も持つことを
    検査する（`[UNDERSPEC-CAL-C13]`）。
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
いる場合、`vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG` を発行する
（entry の形状そのものが壊れている場合は上記 REQUIRED_BLOCKING の内容検証
側で `BLOCKED_C0_MANIFEST_INCOMPLETE` として捕捉する。両者は排他ではなく
併発しうる）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import vocab
from .candidates import registry as candidate_registry

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

#: path+hash 系マップ（設計正本 §3.1: 「候補 meter・generator・schema・test の
#: 全 path + SHA-256」）。各マップは `path -> sha256_hex` の mapping。
HASH_MAP_KEYS: tuple[str, ...] = (
    "candidates.meter_paths_sha256",
    "candidates.generator_paths_sha256",
    "candidates.schema_paths_sha256",
    "candidates.test_paths_sha256",
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

#: `c0_validate.py` 自身のパス（`voice_genesis/calibration/c0_validate.py`）から
#: 2 階層上がると repo root（本ファイルが `<repo_root>/voice_genesis/calibration/`
#: 直下にある前提）。
_REPO_ROOT = Path(__file__).resolve().parents[2]


def calibration_path_inventory(repo_root: Path | None = None) -> frozenset[str]:
    """`voice_genesis/calibration/**/*.py` の repo-relative path 全集合を返す。

    設計正本 §3.1「候補 meter・generator・schema・test の全 path + SHA-256」の
    "全" を機械的に判定できる正本として、実リポジトリのファイルツリーを直接
    列挙する（Codex レビュー 2026-09-01 P1: 従来は supplied entries の形状のみ
    検証しており、ファイルを丸ごと省略しても通過してしまっていた）。tests/
    配下も対象に含める（§3.1 は test path のカバレッジも要求する）。

    public 関数として公開する: 将来 C0 freeze を実際に実行する武装版スクリプト
    （§18 Gate 2 承認後）が、この dry-run 検証と同一の inventory 定義を再利用
    できるようにするため。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    package_dir = root / "voice_genesis" / "calibration"
    return frozenset(p.relative_to(root).as_posix() for p in package_dir.rglob("*.py"))

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


def _is_hollow(value: object) -> bool:
    """`None` 以外で「実質未記録」とみなす値（空文字列・空 mapping・空 list 等）。

    存在チェックだけでは `{}` や `""` を「記録済み」として通してしまうため
    （Codex レビュー 2026-09-01 P1: hollow manifest 問題）、内容の空虚さも
    missing 扱いにする。真偽値・数値の `0`/`False` は意図的な記録値
    でありうるため hollow とはみなさない。
    """
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return len(value) == 0
    return False


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
    """REQUIRED_BLOCKING キーのうち欠落・hollow なものを返す。

    `repo.dirty_tree` は「値が False であること」自体が要求（§3.1:
    「dirty-tree=false」）のため、存在していても `True` なら欠落と同様に
    扱う（fail-closed。値が `False` 以外の型・欠落も含めて違反とする）。
    """
    missing: list[str] = []
    for key in REQUIRED_BLOCKING_KEYS:
        found, value = _resolve(manifest, key)
        if not found or value is None or _is_hollow(value):
            missing.append(key)
            continue
        if key == "repo.dirty_tree" and value is not False:
            missing.append(f"{key} (must be exactly false, got {value!r})")
    return missing


def _check_hash_maps(manifest: Mapping[str, object]) -> list[str]:
    """path+hash 系マップの内容検証（設計正本 §3.1。`[UNDERSPEC-CAL-C10]`）。

    非空・欠落チェックは `_check_required_blocking` 側で既に行うため、ここでは
    「存在し非空だが形状が壊れている」ケースのみを追加で検出する。
    """
    violations: list[str] = []
    for key in HASH_MAP_KEYS:
        found, value = _resolve(manifest, key)
        if not found or _is_hollow(value) or not isinstance(value, Mapping):
            continue  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
        for path, sha in value.items():
            if not isinstance(path, str) or path.strip() == "":
                violations.append(f"{key}[{path!r}] (path must be a non-empty string)")
                continue
            if not isinstance(sha, str) or not _SHA256_HEX_RE.match(sha):
                violations.append(
                    f"{key}[{path!r}] (sha256 must be 64 lowercase hex chars, got {sha!r})"
                )
    return violations


def _check_path_inventory_coverage(manifest: Mapping[str, object]) -> list[str]:
    """4 つの path+hash 系マップ（`HASH_MAP_KEYS`）の**合併集合**が、実リポジトリの
    `calibration_path_inventory()` と厳密一致することを検査する（設計正本 §3.1
    「候補 meter・generator・schema・test の全 path + SHA-256」。Codex レビュー
    2026-09-01 P1: 従来は supplied entries の形状のみを検証しており、ファイルを
    丸ごと省略しても・関係ない phantom path を紛れ込ませても通過してしまっていた）。

    4 カテゴリの**各々**が inventory を個別にカバーする必要はない（meter/
    generator/schema/test の切り分けは記録上の分類であり、正本はカテゴリ単位の
    完全性までは要求しない）。missing（inventory にあるが 4 マップいずれにも
    無い）・unknown（4 マップのどこかにあるが inventory に無い）をそれぞれ個別
    に列挙する。いずれかのマップが欠落/空/非 mapping の場合は
    `_check_required_blocking`/`_check_hash_maps` 側で既に捕捉されるため、ここ
    では二重報告を避けてスキップする。
    """
    declared: set[str] = set()
    for key in HASH_MAP_KEYS:
        found, value = _resolve(manifest, key)
        if not found or _is_hollow(value) or not isinstance(value, Mapping):
            return []
        declared.update(p for p in value.keys() if isinstance(p, str))

    inventory = calibration_path_inventory()
    missing = sorted(inventory - declared)
    unknown = sorted(declared - inventory)
    violations = [
        f"candidates.*_paths_sha256 (missing required path: {p!r})" for p in missing
    ]
    violations += [
        f"candidates.*_paths_sha256 (unknown/extra path not in repo inventory: {p!r})"
        for p in unknown
    ]
    return violations


def _required_meter_ids() -> frozenset[str]:
    """`candidates.registry.ALL_CANDIDATES` が定義する全 meter family の値集合。"""
    return frozenset(c.meter.value for c in candidate_registry.ALL_CANDIDATES)


def _check_meter_specs_coverage(manifest: Mapping[str, object]) -> list[str]:
    """`frozen_design.meter_specs` が candidates.registry の全 meter family を
    カバーするかを検査する（設計正本 §3.1「frozen design 全項目」。
    `[UNDERSPEC-CAL-C11]`）。欠落 meter を個別に列挙する。
    """
    found, meter_specs = _resolve(manifest, "frozen_design.meter_specs")
    if not found or _is_hollow(meter_specs) or not isinstance(meter_specs, Mapping):
        return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
    missing_meters = sorted(_required_meter_ids() - set(meter_specs.keys()))
    return [f"frozen_design.meter_specs.{meter_id}" for meter_id in missing_meters]


def _check_independence_ledger(manifest: Mapping[str, object]) -> list[str]:
    """`independence_ledger` のエントリ形状検査（設計正本 §4。`[UNDERSPEC-CAL-C12]`）。

    各値が `vocab.IndependenceTier` の閉語彙に属する文字列であることを検査する。
    さらに、ledger のキー集合が凍結済み 99 候補registry（`candidates.registry.
    ALL_CANDIDATES`）の candidate_id 全集合と **完全一致** することを要求する
    （Codex レビュー 2026-09-01 P1: 従来は供給された値の形状のみ検査し、凍結
    candidate set 全体をカバーしているかを一切見ていなかった）。欠落
    candidate_id・registry に存在しない unknown/extra な candidate_id は、
    いずれも個別に列挙する。あわせて、各 entry の tier が registry が宣言する
    その候補の tier と一致するかも cross-check する（不一致は個別に列挙）。
    """
    found, ledger = _resolve(manifest, "independence_ledger")
    if not found or _is_hollow(ledger) or not isinstance(ledger, Mapping):
        return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
    valid_tiers = {t.value for t in vocab.IndependenceTier}
    violations: list[str] = []
    for entry_key, tier_value in ledger.items():
        if not isinstance(tier_value, str) or tier_value not in valid_tiers:
            violations.append(
                f"independence_ledger[{entry_key!r}] (must be one of {sorted(valid_tiers)}, "
                f"got {tier_value!r})"
            )

    registry_ids = {c.candidate_id for c in candidate_registry.ALL_CANDIDATES}
    ledger_ids = {k for k in ledger.keys() if isinstance(k, str)}
    missing_ids = sorted(registry_ids - ledger_ids)
    unknown_ids = sorted(ledger_ids - registry_ids)
    violations.extend(
        f"independence_ledger (missing candidate_id: {cid!r})" for cid in missing_ids
    )
    violations.extend(
        f"independence_ledger (unknown/extra candidate_id: {cid!r})" for cid in unknown_ids
    )

    registry_by_id = {c.candidate_id: c for c in candidate_registry.ALL_CANDIDATES}
    for entry_key, tier_value in ledger.items():
        candidate = registry_by_id.get(entry_key) if isinstance(entry_key, str) else None
        if candidate is None:
            continue  # unknown id は上で既に捕捉済み
        if not isinstance(tier_value, str) or tier_value not in valid_tiers:
            continue  # 形状違反は上で既に捕捉済み（tier 比較は形状が妥当な場合のみ）
        if tier_value != candidate.independence_tier.value:
            violations.append(
                f"independence_ledger[{entry_key!r}] (tier mismatch: registry declares "
                f"{candidate.independence_tier.value!r}, ledger has {tier_value!r})"
            )
    return violations


def _check_rng_ledger_shape(manifest: Mapping[str, object]) -> list[str]:
    """`rng_ledger` の entry 形状検査（設計正本 §3.3「stream 列挙 + seed 参照」。
    `[UNDERSPEC-CAL-C13]`）。

    各 entry は非空 `stream_name`（str）と `seeded`（bool）を必須とし、
    `seeded=True` の場合はさらに非空 `public_seed_id`（seed 参照。
    `streams.RngLedgerEntry.public_seed_id` と命名を揃えた）を必須とする。
    """
    found, ledger = _resolve(manifest, "rng_ledger")
    if not found or _is_hollow(ledger) or not isinstance(ledger, Sequence):
        return []  # 欠落/空/非 list は _check_required_blocking 側で既に捕捉
    if isinstance(ledger, (str, bytes)):
        return [f"rng_ledger (must be a list of entries, got {type(ledger).__name__})"]

    violations: list[str] = []
    for i, entry in enumerate(ledger):
        if not isinstance(entry, Mapping):
            violations.append(f"rng_ledger[{i}] (entry must be a mapping)")
            continue
        stream_name = entry.get("stream_name")
        if not isinstance(stream_name, str) or stream_name.strip() == "":
            violations.append(f"rng_ledger[{i}].stream_name (must be a non-empty string)")
        if not isinstance(entry.get("seeded"), bool):
            violations.append(f"rng_ledger[{i}].seeded (must be a bool)")
            continue
        if entry["seeded"] is True:
            seed_ref = entry.get("public_seed_id")
            if not isinstance(seed_ref, str) or seed_ref.strip() == "":
                violations.append(
                    f"rng_ledger[{i}].public_seed_id (required seed reference when seeded=true)"
                )
    return violations


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
        if not found or value is None or _is_hollow(value):
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


def _check_rng_ledger_unseeded(manifest: Mapping[str, object]) -> tuple[str, ...]:
    """`rng_ledger` 中で unseeded と明示宣言された stream 名のリストを返す。

    各 entry は `{"stream_name": str, "seeded": bool, ...}` を想定する
    ([UNDERSPEC-CAL-C08] 設計正本は entry のフィールド名までは規定しない。
    最も単純な bool フラグ方式を採った)。`rng_ledger` 自体が欠落・空・
    非 list の場合はここでは検出しない（欠落は REQUIRED_BLOCKING 側で
    既に捕捉される）。entry 自体の形状違反（`stream_name`/`seeded` 欠落等）は
    `_check_rng_ledger_shape` 側で `BLOCKED_C0_MANIFEST_INCOMPLETE` として
    別途捕捉する（本関数は `seeded=False` の明示宣言のみを見る）。
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
    missing_required += _check_hash_maps(manifest)
    missing_required += _check_path_inventory_coverage(manifest)
    missing_required += _check_meter_specs_coverage(manifest)
    missing_required += _check_independence_ledger(manifest)
    missing_required += _check_rng_ledger_shape(manifest)

    missing_recorded, downgrades = _check_recorded_or_absent(manifest)
    all_missing = tuple(missing_required + missing_recorded)

    d4c_ineligible, d4c_reason = _check_pyworld(manifest)
    unseeded_streams = _check_rng_ledger_unseeded(manifest)

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
