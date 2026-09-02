"""C0 freeze manifest の dry-run 検証（設計正本 §3）。

**本モジュールは dry-run 検証のみを行う。** ファイル書込・secret 生成・
freeze event 記録のいずれも一切行わない（IMPLEMENTATION_MAP_v1.md §0
授権境界）。`validate_c0_manifest()` は manifest と検証対象 checkout の read-only identity
（Git HEAD / dirty state）を読むが、書込・secret 生成などの副作用は持たない。

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
    16 進文字列であることを検査する（`[UNDERSPEC-CAL-C10]`）。加えて、5 マップ
    の**合併集合**が `calibration_path_inventory()`（実リポジトリの
    `voice_genesis/calibration/**/*.py` 全件 ∪ B0 wrapper が実行する harness
    meter 実装。`[UNDERSPEC-CAL-D49]`）と厳密一致することを要求する
    （欠落 path・inventory に無い unknown/extra path をそれぞれ個別列挙。
    `[UNDERSPEC-CAL-C14]`。Codex レビュー 2026-09-01 P1: 従来は supplied
    entries の形状のみを検証しており、ファイルを丸ごと省略しても通過して
    しまっていた）。加えて、同一 path が 5 マップの複数カテゴリに重複して
    宣言されていないことも検査する（digest が一致していても重複は BLOCK
    する。Codex レビュー 2026-09-01 P1: 従来は 4 マップを `declared[path] =
    sha` で単純マージしており、同一 path が矛盾する digest で 2 カテゴリに
    宣言されていても後勝ちで silently 採用されていた）。
  - `frozen_design.meter_specs` は `candidates.registry.ALL_CANDIDATES` が
    定義する全 meter family をカバーする（欠落 meter は
    `frozen_design.meter_specs.<METER_ID>` として個別に列挙する。
    `[UNDERSPEC-CAL-C11]`）。加えて、各 meter エントリは
    `METER_SPEC_REQUIRED_KEYS`（construct/unit/domain/algorithm_family/
    parameter_grid/baseline/fallback/missing_failure_rule）を完全に持つ
    ことを要求する（欠落ネスト鍵は `frozen_design.meter_specs.<METER_ID>.
    <key>` として個別列挙。`[UNDERSPEC-CAL-C17]`）。
  - `frozen_design.fixture_spec` は `fixtures.axes.FixtureFamily` の全 7
    family をカバーし（欠落 family は `frozen_design.fixture_spec.<FAMILY>`
    として個別列挙）、各 family エントリは `FIXTURE_SPEC_REQUIRED_KEYS`
    （generator_version/generator_hash/known_truth_field/confound_axes/
    boundary_probes/negative_controls）を完全に持つことを要求する
    （`[UNDERSPEC-CAL-C17]`。Codex レビュー 2026-09-01 P1: 従来
    `fixture_spec={"family": "F0_CONTROL"}` のような hollow な
    placeholder manifest が素通りしていた finding の直接該当箇所）。
  - campaign-level セクション `frozen_design.split_spec` /
    `selection_spec` / `provenance_spec` / `cost_caps` はそれぞれ
    `SPLIT_SPEC_REQUIRED_KEYS`（ratios/seed_scheme/seal_commitment_rule）・
    `SELECTION_SPEC_REQUIRED_KEYS`（selection_rule/tie_rule/
    candidate_exhaustion_rule/holdout_fail_outcome）・
    `PROVENANCE_SPEC_REQUIRED_KEYS`（schema_version/artifact_layout）・
    `COST_CAPS_REQUIRED_KEYS`（compute/storage/budget）を完全に持つことを
    要求する（`[UNDERSPEC-CAL-C17]`）。`frozen_design.stop_rules` はネスト
    構造こそ規定しない（設計正本は個々の rule のスキーマまでは規定しない）が、
    非空 list であることは要求する（`[UNDERSPEC-CAL-C18]`）。旧
    `frozen_design.selection_rule`（単一 tie_rule のみを保持していた）は
    `selection_spec` へ改名・拡張した（§3.1「selection rule・tie rule・
    candidate exhaustion rule・holdout FAIL 後の固定 outcome」の 4 項目を
    1 セクションへ集約する方が他の frozen-design 項目と一貫するため）。
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
    検査する（`[UNDERSPEC-CAL-C13]`）。加えて、全エントリの `stream_name`
    集合は `streams.expected_rng_stream_names()`（§3.3 が定める凍結 closed
    set: family ごとの generator render stream 1 個 ∪ `"split/hmac"` ∪
    `"split/tiebreak"`）と厳密一致することを要求する（欠落・unknown/extra・
    重複をそれぞれ個別列挙。`[UNDERSPEC-CAL-C16]`。Codex レビュー 2026-09-01
    P1: 従来は 1 件の well-formed entry があれば通過しており、stream 集合が
    閉じているかを一切検査していなかった）。
  - **BOUNDED shape validation**（`[UNDERSPEC-CAL-C18]`。Codex レビュー
    2026-09-01 P1: `generator_hash="not-a-hash"`・`confound_axes="x"`・
    `parameter_grid=1` のような、非 hollow だが型として明らかに壊れた
    scalar 値が上記ネスト鍵の完全性検査を素通りしていた）: `meter_specs`/
    `fixture_spec`/campaign-level セクション（`split_spec`/`selection_spec`/
    `provenance_spec`/`cost_caps`）配下のネスト鍵、および `frozen_design.
    stop_rules` は、存在・非 hollow であることに加えてフィールド名から
    機械的に導出した最小限の「形状」も検査する（`_shape_violation`）:
    フィールド名が `*_hash`/`*_sha256` で終わるものは bare 64 桁小文字
    16 進 sha256 文字列、`confound_axes`/`boundary_probes`/
    `negative_controls`/`stop_rules` は非空 list、`parameter_grid` は
    非空 mapping、`generator_version`/`schema_version` は非空白 str で
    あることを要求する。違反は `"<section>.<key>: shape (<reason>)"`
    として個別列挙する（欠落/hollow の `"<section>.<key>"` と区別できる
    形式）。

    **本 validator が検査するのは値の「形状」までである。値の意味論的
    相互検証（registry/matrix との突合 — 例えば `parameter_grid` の中身が
    実際に `candidates.registry` の宣言と整合するか、`generator_hash` が
    実際に `fixtures/generators/*.py` の実装内容と一致するか）は、armed
    C0 freeze producer 実装時（§18 Gate 2 承認後の別 PR）の責務であり、
    本 dry-run validator の範囲外である。**
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

import ast
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import streams, vocab
from .candidates import registry as candidate_registry
from .fixtures import axes as fixture_axes
from .fixtures.matrix import build_matrix, declared_sweeps_by_family, truth_identity_for_row
from .gates import MIN_RESOLVABLE_PAIRS_PER_SWEEP

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
    "candidates.meter_implementation_paths_sha256",
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
    "sample_format.resampling_parameters",
    "frozen_design.claim_critical_set",
    "frozen_design.meter_specs",
    "frozen_design.fixture_spec",
    "frozen_design.split_spec",
    "frozen_design.selection_spec",
    "frozen_design.provenance_spec",
    "frozen_design.cost_caps",
    "frozen_design.stop_rules",
    "independence_ledger",
    "rng_ledger",
)

#: REQUIRED_BLOCKING のうち frozen environment / preprocessing identity を
#: 表す scalar string fields。`_is_hollow()` は意図的に `0`/`False` を
#: populated とみなすため、これらは別途 nonblank `str` を必須化する。
_REQUIRED_STRING_SCALAR_KEYS = frozenset(
    {
        "repo.url",
        "measurement_directory_status",
        "dependencies.python_version",
        "dependencies.numpy_version",
        "dependencies.scipy_version",
        "dependencies.librosa_version",
        "dependencies.soundfile_version",
        "sample_format.dtype",
        "sample_format.channel_policy",
        "sample_format.resampling_impl",
    }
)

#: `frozen_design.meter_specs.<METER_ID>` の各エントリが持つべき必須ネスト
#: キー（設計正本 §3.1「meter 別 construct/unit/domain/algorithm family/
#: 有限 parameter grid/baseline/fallback/missing・failure rule」。
#: `[UNDERSPEC-CAL-C17]`。Codex レビュー 2026-09-01 P1: 従来は
#: `frozen_design.*` を非空チェックのみで通過させており、
#: `meter_specs={meter_id: {"construct": "..."}}` のような hollow な
#: placeholder エントリでも REQUIRED_BLOCKING を通過してしまっていた）。
METER_SPEC_REQUIRED_KEYS: tuple[str, ...] = (
    "construct",
    "unit",
    "domain",
    "algorithm_family",
    "parameter_grid",
    "baseline",
    "fallback",
    "missing_failure_rule",
)

#: `frozen_design.fixture_spec.<FAMILY>` の各エントリが持つべき必須ネスト
#: キー（設計正本 §3.1「fixture family・generator version/hash・
#: known-truth field・confound 軸・boundary probes・negative controls」。
#: `[UNDERSPEC-CAL-C17]`）。
FIXTURE_SPEC_REQUIRED_KEYS: tuple[str, ...] = (
    "generator_version",
    "generator_hash",
    "known_truth_field",
    "confound_axes",
    "boundary_probes",
    "negative_controls",
)

#: `frozen_design.split_spec` の必須ネストキー（設計正本 §3.1「split・
#: seed・seal」。`[UNDERSPEC-CAL-C17]`）。
SPLIT_SPEC_REQUIRED_KEYS: tuple[str, ...] = ("ratios", "seed_scheme", "seal_commitment_rule")

#: `frozen_design.selection_spec` の必須ネストキー（設計正本 §3.1
#: 「selection rule・tie rule・candidate exhaustion rule・holdout FAIL
#: 後の固定 outcome」。`[UNDERSPEC-CAL-C17]` 設計正本は selection rule
#: 本体を独立キーとして列挙する記法までは規定しないため、他の frozen-design
#: 項目と一貫させ `selection_spec` 配下へネストした）。
SELECTION_SPEC_REQUIRED_KEYS: tuple[str, ...] = (
    "selection_rule",
    "tie_rule",
    "candidate_exhaustion_rule",
    "holdout_fail_outcome",
)

#: `frozen_design.provenance_spec` の必須ネストキー（設計正本 §3.1
#: 「provenance schema・artifact layout」。`[UNDERSPEC-CAL-C17]`）。
PROVENANCE_SPEC_REQUIRED_KEYS: tuple[str, ...] = ("schema_version", "artifact_layout")

#: `frozen_design.cost_caps` の必須ネストキー（設計正本 §3.1「cost cap」。
#: `[UNDERSPEC-CAL-C17]` 設計正本は cost cap の内訳次元までは規定しないため、
#: 最も基本的な 3 次元 compute/storage/budget に固定した）。
COST_CAPS_REQUIRED_KEYS: tuple[str, ...] = ("compute", "storage", "budget")

#: path+hash 系マップ（設計正本 §3.1: 「候補 meter・generator・schema・test の
#: 全 path + SHA-256」）。各マップは `path -> sha256_hex` の mapping。
#: `meter_implementation_paths_sha256` は `meter_paths_sha256`
#: （`voice_genesis/calibration/candidates/` 配下の候補実装）とは別カテゴリ
#: として、`candidates/impl/b0_wrappers.py` が無改変 import で実行する
#: `voice_genesis/harness/` 配下の meter 実装（B0 baseline の実体）を記録する
#: （Codex round 21 レビュー finding, ADOPT, `[UNDERSPEC-CAL-D49]`: 従来
#: harness 実装がどの path+hash マップにも含まれておらず、C0 freeze 後に
#: harness meter を改変しても `campaign/cli.py::_canonical_path_violations`
#: の canonical-path 照合を素通りしていた）。
HASH_MAP_KEYS: tuple[str, ...] = (
    "candidates.meter_paths_sha256",
    "candidates.meter_implementation_paths_sha256",
    "candidates.generator_paths_sha256",
    "candidates.schema_paths_sha256",
    "candidates.test_paths_sha256",
)

#: REQUIRED_BLOCKING キーのうち、値がトップレベルで特定のコンテナ型
#: （mapping または list）でなければならないもの（c0_validate.py:490 P1
#: finding、2026-09-01 レビュー: `meter_specs="x"` のようなスカラー値は
#: `_is_hollow` の非空チェックのみを通過してしまい、後続の deeper validator
#: （`_check_meter_specs_coverage` 等）は `isinstance(value, Mapping)` で
#: 早期 return する設計のため、非 Mapping/非 list な値は事実上まったく検証
#: されずに REQUIRED_BLOCKING を通過していた。`[UNDERSPEC-CAL-C19]`）。値は
#: `"mapping"` または `"list"`。`str`/`tuple` は `list` 判定から明示的に除外
#: するため `isinstance(value, list)` で厳密に検査する（`Sequence` 判定だと
#: 文字列も通ってしまう）。
_CONTAINER_TYPE_KEYS: dict[str, str] = {
    "candidates.meter_paths_sha256": "mapping",
    "candidates.meter_implementation_paths_sha256": "mapping",
    "candidates.generator_paths_sha256": "mapping",
    "candidates.schema_paths_sha256": "mapping",
    "candidates.test_paths_sha256": "mapping",
    "sample_format.resampling_parameters": "mapping",
    "frozen_design.meter_specs": "mapping",
    "frozen_design.fixture_spec": "mapping",
    "frozen_design.split_spec": "mapping",
    "frozen_design.selection_spec": "mapping",
    "frozen_design.provenance_spec": "mapping",
    "frozen_design.cost_caps": "mapping",
    "frozen_design.stop_rules": "list",
    "independence_ledger": "mapping",
    "rng_ledger": "list",
}

# ---------------------------------------------------------------------------
# BOUNDED shape validation（Codex レビュー 2026-09-01 P1: 従来
# `_missing_nested_keys` はキーの存在/hollow 判定のみを行い、値の「形状」を
# 一切検査していなかったため、`generator_hash="not-a-hash"` /
# `confound_axes="x"` / `parameter_grid=1` のような、型として明らかに壊れた
# 値でも REQUIRED_BLOCKING を素通りしていた。`[UNDERSPEC-CAL-C18]`）
#
# **本 validator が検査するのは値の「形状」までである。値の意味論的相互検証
# （registry/matrix との突合。例えば `parameter_grid` の中身が実際に
# `candidates.registry` の宣言と整合するか、`generator_hash` が実際に
# `fixtures/generators/*.py` の実装内容と一致するか）は、armed C0 freeze
# producer 実装時（§18 Gate 2 承認後の別 PR）の責務であり、本 dry-run
# validator の範囲外である。**
# ---------------------------------------------------------------------------

#: このサフィックスで終わるネストフィールド名は「ダイジェスト値」として、
#: bare 64 桁小文字 16 進 sha256 文字列（`_SHA256_HEX_RE`。他の sha256 系
#: フィールド — `HASH_MAP_KEYS` の value・`PYWORLD_WHEEL_HASH_KEY` —
#: と同一形式へ統一）であることを要求する。
_HASH_FIELD_SUFFIXES: tuple[str, ...] = ("_hash", "_sha256")

#: 非空 list であることを要求するネストフィールド名（設計正本が「軸」
#: 「probe」「control」「rule」の複数形を列挙で表現する箇所。単一 scalar
#: では複数性の要求を満たせないため list 型を要求する）。
_LIST_SHAPE_FIELDS: frozenset[str] = frozenset(
    {"confound_axes", "boundary_probes", "negative_controls", "stop_rules"}
)

#: 非空 mapping であることを要求するネストフィールド名。
_MAPPING_SHAPE_FIELDS: frozenset[str] = frozenset({"parameter_grid"})

#: 非空白 str であることを要求する「version」系ネストフィールド名（`_is_hollow`
#: の空文字列チェックに加え、意図せず数値・bool 等の非文字列型が入るのを防ぐ）。
_VERSION_SHAPE_FIELDS: frozenset[str] = frozenset({"generator_version", "schema_version"})


def _shape_violation(field_name: str, value: object) -> str | None:
    """`field_name`（ネストしたキー名。ドット区切りパスの最後の要素）が
    上記のいずれかの形状規則に該当する場合、`value` がその規則を満たすかを
    検査する。違反時は人間可読な理由文字列を返し、規則の対象外または規則を
    満たす場合は `None` を返す（＝missing/hollow 判定は呼び出し側の既存
    `_is_hollow` チェックが別途行うため、ここでは「存在し非 hollow な値の
    形状」のみを見る）。

    `[UNDERSPEC-CAL-C18]` 設計正本 §3.1 はネストしたリーフ値の型までは
    明示しないため、フィールド名の命名規則から機械的に導出した最小限の
    形状規則を採用する（最も単純で一貫した選択）。
    """
    if any(field_name.endswith(suffix) for suffix in _HASH_FIELD_SUFFIXES):
        if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
            return "must be a 64-char lowercase hex sha256 string"
        return None
    if field_name in _LIST_SHAPE_FIELDS:
        if not isinstance(value, list) or len(value) == 0:
            return "must be a non-empty list"
        return None
    if field_name in _MAPPING_SHAPE_FIELDS:
        if not isinstance(value, Mapping) or len(value) == 0:
            return "must be a non-empty mapping"
        return None
    if field_name in _VERSION_SHAPE_FIELDS:
        if not isinstance(value, str) or value.strip() == "":
            return "must be a non-blank string"
        return None
    return None


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

#: `c0_validate.py` 自身のパス（`voice_genesis/calibration/c0_validate.py`）から
#: 2 階層上がると repo root（本ファイルが `<repo_root>/voice_genesis/calibration/`
#: 直下にある前提）。
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _inspect_checkout_identity(
    repo_root: Path | None = None,
) -> tuple[str | None, bool | None, str | None]:
    """Return ``(HEAD, dirty, error)`` for the checkout being validated.

    This is deliberately read-only.  C0 path hashes are computed from this checkout,
    so accepting a caller-provided but unrelated commit SHA/dirty flag would make the
    provenance pin describe different bytes than those actually inspected.  Git
    inspection failure is returned as an error so the caller can fail closed.
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if head.returncode != 0:
            detail = head.stderr.strip() or head.stdout.strip() or f"exit {head.returncode}"
            return None, None, f"git rev-parse HEAD failed: {detail}"
        head_sha = head.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
            return None, None, f"git rev-parse HEAD returned malformed SHA: {head_sha!r}"

        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status.returncode != 0:
            detail = status.stderr.strip() or status.stdout.strip() or f"exit {status.returncode}"
            return None, None, f"git status failed: {detail}"
        return head_sha, bool(status.stdout.strip()), None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, f"git checkout inspection failed: {exc}"


def _check_checkout_identity(manifest: Mapping[str, object]) -> list[str]:
    """Bind ``repo.commit_sha``/``repo.dirty_tree`` to the bytes being inspected."""
    head_sha, dirty, error = _inspect_checkout_identity()
    if error is not None or head_sha is None or dirty is None:
        return [f"repo.checkout_identity ({error or 'unavailable'})"]

    violations: list[str] = []
    found_sha, declared_sha = _resolve(manifest, "repo.commit_sha")
    if (
        found_sha
        and isinstance(declared_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", declared_sha) is not None
        and declared_sha != head_sha
    ):
        violations.append(
            "repo.commit_sha (does not match inspected checkout HEAD: "
            f"declared={declared_sha}, actual={head_sha})"
        )
    if dirty:
        violations.append("repo.dirty_tree (inspected checkout is actually dirty)")
    return violations


#: 版管理されたクローズド inventory ファイル名（`voice_genesis/calibration/` 直下）。
PATH_INVENTORY_FILENAME = "c0_path_inventory.json"


def _path_inventory_file(repo_root: Path | None = None) -> Path:
    root = repo_root if repo_root is not None else _REPO_ROOT
    return root / "voice_genesis" / "calibration" / PATH_INVENTORY_FILENAME


def calibration_path_inventory(repo_root: Path | None = None) -> frozenset[str]:
    """版管理されコミットされたクローズド inventory ファイル
    (`voice_genesis/calibration/c0_path_inventory.json`) を厳格パースして
    repo-relative path 全集合を返す。

    設計正本 §3.1「候補 meter・generator・schema・test の全 path + SHA-256」の
    "全" を機械的に判定できる正本として機能する（Codex レビュー 2026-09-01
    P1 (#1): 従来は supplied entries の形状のみ検証しており、ファイルを丸ごと
    省略しても通過してしまっていた）。

    Codex レビュー 2026-09-01 P1 (#2): 以前の実装は検証対象の checkout 自身に
    対して `rglob("*.py")` を実行しており circular だった — その checkout が
    ファイルを 1 件でも欠いていれば、inventory 側（rglob の結果）からも同じ
    ファイルが消え、manifest 側（同じ checkout をハッシュ化して作る）とも
    自動的に一致してしまうため、欠落を検出できなかった。本関数はこの循環を
    断つため、**検証対象の checkout の実ファイルツリーには一切依存しない**
    版管理済みの `c0_path_inventory.json` のみを読む。checkout が壊れていても
    (このファイル自体は Git 管理下にあるため通常存在する) inventory は正しい
    値を返し続け、manifest 側のみが欠落を反映するため
    `_check_path_inventory_coverage` が確実に検出できる。

    parse-strict: 中身は文字列のみからなる **ソート済み・重複なし** の JSON
    配列でなければならない。壊れていれば `ValueError` を送出する（fail-closed:
    inventory 自体が信頼できない状態で検証を通過させない）。

    inventory 自体のドリフト検知（実ファイルツリーと乖離していないか）は本
    関数の責務ではなく `tests/test_c0_path_inventory_sync.py`（`rglob` で
    再生成し本ファイルと厳密一致検査）が担う。
    """
    path = _path_inventory_file(repo_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"calibration_path_inventory: cannot read committed inventory {path}: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration_path_inventory: malformed JSON in {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(p, str) for p in data):
        raise ValueError(f"calibration_path_inventory: {path} must contain a JSON array of strings")
    if len(data) != len(set(data)):
        raise ValueError(f"calibration_path_inventory: {path} contains duplicate paths")
    if data != sorted(data):
        raise ValueError(f"calibration_path_inventory: {path} must be sorted")
    return frozenset(data)


def scan_calibration_tree_inventory(repo_root: Path | None = None) -> frozenset[str]:
    """実ファイルツリーを `rglob` で直接走査した inventory。

    **検証本体では使わない**（`validate_c0_manifest` / `calibration_path_inventory`
    からは呼ばれない — 検証対象 checkout の実ツリーに依存すると
    `calibration_path_inventory` が断ち切った circular dependency が復活する
    ため）。用途は 2 つ: (1) `c0_path_inventory.json` を生成・再生成するとき
    の基礎データ、(2) `tests/test_c0_path_inventory_sync.py` の sync test が
    「コミット済み inventory が実ツリーとドリフトしていないか」を確認する
    ときの比較対象。生成物には `c0_path_inventory.json` 自身の path も含める
    （inventory ファイル自体も版管理・監査対象であるため。§3.1「候補 meter・
    generator・schema・test の全 path」に含める必要はないが、inventory の
    自己完結性のため同じ集合に含めておく）。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    package_dir = root / "voice_genesis" / "calibration"
    paths = {p.relative_to(root).as_posix() for p in package_dir.rglob("*.py")}
    paths.add((package_dir / PATH_INVENTORY_FILENAME).relative_to(root).as_posix())
    return frozenset(paths)


#: `[UNDERSPEC-CAL-D49]` `candidates/impl/b0_wrappers.py` が無改変 import で
#: 実行する `voice_genesis/harness/` 配下のファイル名（拡張子抜き）。
_HARNESS_DIR_RELATIVE = "voice_genesis/harness"
_B0_WRAPPER_MODULE_RELATIVE = "voice_genesis/calibration/candidates/impl/b0_wrappers.py"


def _harness_local_import_names(module_path: Path, harness_dir: Path) -> frozenset[str]:
    """`module_path`（harness 側の 1 ファイル、または `b0_wrappers.py`）の
    top-level `import x` / `from x import ...` 文を AST で静的解析し、`x` が
    `harness_dir/x.py` として実在する（= bare import で解決される harness
    ローカルモジュールである）名前の集合を返す。third-party/stdlib import は
    `harness_dir` に同名 `.py` が存在しないため自動的に除外される。相対 import
    (`from . import x`, `node.level != 0`) は harness モジュールが使わない
    書式のためスキップする。
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top_names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level != 0:
                continue
            top_names = [node.module.split(".")[0]]
        else:
            continue
        for name in top_names:
            if (harness_dir / f"{name}.py").is_file():
                names.add(name)
    return frozenset(names)


def resolve_b0_wrapper_harness_paths(repo_root: Path | None = None) -> frozenset[str]:
    """`candidates/impl/b0_wrappers.py` が実行する harness meter 実装ファイルの
    transitive closure を静的解析（AST、実際の import 実行は行わない）で求め、
    repo-relative path の frozenset を返す（`[UNDERSPEC-CAL-D49]`）。

    Codex round 21 レビュー finding（ADOPT）: `b0_wrappers.py` は
    `voice_genesis/harness/measure.py` / `measure_v3.py` を実行し、
    `measure_v3.py` はさらに `measure_v2.py` を import するが、これらは
    どの path+hash マップにも記録されていなかったため、C0 freeze 後に
    harness meter 実装を改変しても `campaign/cli.py::_canonical_path_violations`
    の canonical-path 照合を素通りしていた。本関数の戻り値は
    `candidates.meter_implementation_paths_sha256`（`HASH_MAP_KEYS`）が
    カバーすべき最小集合であり、`tests/test_c0_path_inventory_sync.py` が
    `c0_path_inventory.json` との包含関係を静的に検査する（B0 wrapper が
    新たな harness import を増やしても、inventory への追記漏れがあれば
    このテストが検出する）。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    harness_dir = root / _HARNESS_DIR_RELATIVE
    wrapper_path = root / _B0_WRAPPER_MODULE_RELATIVE

    resolved: set[str] = set()
    frontier: set[str] = set(_harness_local_import_names(wrapper_path, harness_dir))
    while frontier:
        name = frontier.pop()
        if name in resolved:
            continue
        resolved.add(name)
        frontier |= _harness_local_import_names(harness_dir / f"{name}.py", harness_dir) - resolved
    return frozenset(f"{_HARNESS_DIR_RELATIVE}/{name}.py" for name in resolved)


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
    #: UNDERSPEC-CAL-D76 ruling (2)（D75 の `sweep_capacity_violations`/
    #: `_check_sweep_capacity()` を SUPERSEDE）: `_check_declared_sweep_
    #: truth_levels()` の violation 列（非空なら
    #: `BLOCKED_C0_SWEEP_DECLARATION_INVALID` が `blocked_codes` に入る）。
    sweep_declaration_violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_codes) > 0


def _check_required_blocking(manifest: Mapping[str, object]) -> list[str]:
    """REQUIRED_BLOCKING キーのうち欠落・hollow なものを返す。

    `repo.dirty_tree` は「値が False であること」自体が要求（§3.1:
    「dirty-tree=false」）のため、存在していても `True` なら欠落と同様に
    扱う（fail-closed。値が `False` 以外の型・欠落も含めて違反とする）。

    `frozen_design.stop_rules` はネストしたセクションではなく本関数が直接
    走査する REQUIRED_BLOCKING キーだが、BOUNDED shape validation
    （`_shape_violation`。`[UNDERSPEC-CAL-C18]`）の対象フィールド
    (`_LIST_SHAPE_FIELDS`) でもあるため、非空チェック通過後に追加で形状も
    検査する（例: `stop_rules="x"` のような非 list scalar は非空文字列と
    しては通過してしまうため）。他の REQUIRED_BLOCKING_KEYS には
    `_shape_violation` を汎用的に適用しない: leaf 名だけで判定すると
    `candidates.meter_paths_sha256` のような "path -> sha256 の mapping"
    フィールドが `_HASH_FIELD_SUFFIXES`（`*_sha256`）に誤ってマッチし
    （値自体は mapping であり単一 hash 文字列ではない）、正当な manifest を
    誤 BLOCK してしまうため、`stop_rules` のみ個別に検査する。

    **トップレベルのコンテナ型検査**（c0_validate.py:490 P1 finding、
    2026-09-01 レビュー: `meter_specs="x"` のような非空スカラー値は、この
    関数の非空チェックのみを通過し、`_check_meter_specs_coverage` 等の
    deeper validator が `isinstance(value, Mapping)` で早期 return する
    設計のため、実質まったく内容検証を受けずに REQUIRED_BLOCKING を素通り
    していた。`[UNDERSPEC-CAL-C19]`）。`_CONTAINER_TYPE_KEYS` に列挙した
    キーは、非空チェック通過後さらに期待コンテナ型（mapping/list）を
    検査し、型不一致は `"<key>: type (...)"` として個別に BLOCK する。
    型検査を通過した後続の deeper validator（`_check_meter_specs_coverage`
    等）は、以後 `isinstance` 前提を安全に置ける。
    """
    missing: list[str] = []
    for key in REQUIRED_BLOCKING_KEYS:
        found, value = _resolve(manifest, key)
        if not found or value is None or _is_hollow(value):
            missing.append(key)
            continue
        if key == "repo.dirty_tree" and value is not False:
            missing.append(f"{key} (must be exactly false, got {value!r})")
            continue
        if key == "repo.commit_sha" and (
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None
        ):
            missing.append(
                f"{key}: shape (must be a full 40-character lowercase hex commit SHA, "
                f"got {value!r})"
            )
            continue
        if key in _REQUIRED_STRING_SCALAR_KEYS and not isinstance(value, str):
            missing.append(
                f"{key}: type (must be a nonblank string, got {type(value).__name__})"
            )
            continue
        container_kind = _CONTAINER_TYPE_KEYS.get(key)
        if container_kind == "mapping" and not isinstance(value, Mapping):
            missing.append(f"{key}: type (must be a mapping, got {type(value).__name__})")
            continue
        if container_kind == "list" and not isinstance(value, list):
            missing.append(f"{key}: type (must be a list, got {type(value).__name__})")
            continue
        if key == "frozen_design.stop_rules":
            reason = _shape_violation("stop_rules", value)
            if reason is not None:
                missing.append(f"{key}: shape ({reason})")
    return missing


def _check_claim_critical_set(manifest: Mapping[str, object]) -> list[str]:
    """Require the C0 claim-critical declaration to equal the frozen D1 set.

    Presence/non-hollowness is insufficient: shrinking or extending this set would
    change which meter evidence later claim gates require.  The declaration is a
    list for manifest schema stability, but membership is a closed set: duplicate,
    missing, unknown, or non-string members all fail closed.
    """
    key = "frozen_design.claim_critical_set"
    found, value = _resolve(manifest, key)
    if not found or value is None or _is_hollow(value):
        return []  # required-key validation already reports absence/hollowness
    if not isinstance(value, list):
        return [f"{key}: type (must be a list, got {type(value).__name__})"]
    if any(not isinstance(member, str) for member in value):
        return [f"{key}: members (every member must be a meter-id string)"]

    expected = {meter.value for meter in vocab.CLAIM_CRITICAL_SET}
    declared = set(value)
    violations: list[str] = []
    if len(value) != len(declared):
        duplicates = sorted({member for member in value if value.count(member) > 1})
        violations.append(f"{key}: duplicate member(s) {duplicates!r}")
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    if missing:
        violations.append(f"{key}: missing frozen meter(s) {missing!r}")
    if extra:
        violations.append(f"{key}: unknown/extra meter(s) {extra!r}")
    return violations


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
    """5 つの path+hash 系マップ（`HASH_MAP_KEYS`）の**合併集合**が、実リポジトリの
    `calibration_path_inventory()` と厳密一致することを検査する（設計正本 §3.1
    「候補 meter・generator・schema・test の全 path + SHA-256」。Codex レビュー
    2026-09-01 P1: 従来は supplied entries の形状のみを検証しており、ファイルを
    丸ごと省略しても・関係ない phantom path を紛れ込ませても通過してしまっていた）。

    5 カテゴリの**各々**が inventory を個別にカバーする必要はない（meter/
    generator/schema/test の切り分けは記録上の分類であり、正本はカテゴリ単位の
    完全性までは要求しない）。missing（inventory にあるが 5 マップいずれにも
    無い）・unknown（5 マップのどこかにあるが inventory に無い）をそれぞれ個別
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
    violations = [f"candidates.*_paths_sha256 (missing required path: {p!r})" for p in missing]
    violations += [
        f"candidates.*_paths_sha256 (unknown/extra path not in repo inventory: {p!r})"
        for p in unknown
    ]
    return violations


def _check_hash_map_category_uniqueness(manifest: Mapping[str, object]) -> list[str]:
    """5 つの path+hash 系マップ（`HASH_MAP_KEYS`）間で同一 path が複数カテゴリに
    重複して宣言されていないことを検査する（Codex レビュー 2026-09-01 P1:
    `_check_hash_content_match` は 4 マップ（拡張前）を `declared[path] = sha` で単純に
    マージしており、同じ path が 2 カテゴリに異なる digest で宣言されていても
    後勝ちで silently 採用され検出できなかった）。

    digest が一致していても重複は BLOCK する: meter/generator/schema/test の
    カテゴリ分類は manifest が担う記録であり、1 つの path はちょうど 1 カテゴリに
    属する一意な分類を持つべきという manifest 側の整合性要求（§3.1）。digest が
    たまたま一致していても、どのカテゴリに属するかが曖昧な manifest は
    信頼できる分類記録として認められない（category assignment must be
    unique）。重複 path は個別に列挙する。
    """
    path_categories: dict[str, list[str]] = {}
    for key in HASH_MAP_KEYS:
        found, value = _resolve(manifest, key)
        if not found or _is_hollow(value) or not isinstance(value, Mapping):
            return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
        for path in value.keys():
            if isinstance(path, str):
                path_categories.setdefault(path, []).append(key)

    violations: list[str] = []
    for path in sorted(path_categories):
        categories = path_categories[path]
        if len(categories) > 1:
            violations.append(
                f"candidates.*_paths_sha256[{path!r}] (path declared in multiple "
                f"categories: {', '.join(categories)}; category assignment must be unique)"
            )
    return violations


def _check_hash_content_match(manifest: Mapping[str, object]) -> list[str]:
    """5 つの path+hash 系マップに宣言された sha256 を、実ファイルバイトの実測
    sha256 と比較する（設計正本 §3.1「候補 meter・generator・schema・test の全
    path + SHA-256」。Codex レビュー 2026-09-01 P1: 従来は宣言済みハッシュが
    64 桁小文字 16 進文字列という形状のみを検証しており、ファイル内容と無関係
    な任意のハッシュ値でも通過してしまっていた）。

    版管理されたクローズド inventory (`calibration_path_inventory()`) を一度だけ
    走査し（single pass）、各エントリについて実ファイルを 1 回読み sha256 を
    計算、5 マップの合併集合から得た宣言値と比較する。不一致・読込不能はそれぞれ
    path を個別に列挙する。inventory coverage 違反（欠落 path・5 マップに無い
    unknown path）は `_check_path_inventory_coverage` が別途捕捉するため、ここ
    では「inventory と宣言の双方に存在する path」のみを対象とし二重報告を避ける。
    """
    declared: dict[str, str] = {}
    for key in HASH_MAP_KEYS:
        found, value = _resolve(manifest, key)
        if not found or _is_hollow(value) or not isinstance(value, Mapping):
            return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
        for path, sha in value.items():
            if isinstance(path, str) and isinstance(sha, str) and _SHA256_HEX_RE.match(sha):
                declared[path] = sha
            # 形状違反 (非文字列 path・非 64桁hex sha) は _check_hash_maps が
            # 既に捕捉するため、ここでは形状不正なエントリを黙ってスキップする。

    violations: list[str] = []
    for rel_path in sorted(calibration_path_inventory()):
        declared_sha = declared.get(rel_path)
        if declared_sha is None:
            continue  # 欠落/unknown は _check_path_inventory_coverage 側で捕捉済み
        file_path = _REPO_ROOT / rel_path
        try:
            actual_bytes = file_path.read_bytes()
        except OSError as exc:
            violations.append(
                f"candidates.*_paths_sha256[{rel_path!r}] "
                f"(cannot read file for hash verification: {exc})"
            )
            continue
        actual_sha = hashlib.sha256(actual_bytes).hexdigest()
        if actual_sha != declared_sha:
            violations.append(
                f"candidates.*_paths_sha256[{rel_path!r}] (declared sha256 {declared_sha!r} "
                f"does not match actual file content sha256 {actual_sha!r})"
            )
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


def _nested_key_violations(
    entry: Mapping[str, object], required_keys: tuple[str, ...], prefix: str
) -> list[str]:
    """`entry` から `required_keys` の (1) 欠落/hollow なキーと (2) 値は存在
    するが `_shape_violation`（BOUNDED shape validation, `[UNDERSPEC-CAL-C18]`）
    に違反するキーの両方を、`prefix.<key>` 形式の violation 文字列として
    返す（`_is_hollow` を再利用。Codex レビュー 2026-09-01 P1: 従来の欠落
    判定のみでは、`generator_hash="not-a-hash"` のような形状が壊れた値は
    素通りしていた）。

    欠落/hollow は従来どおり `f"{prefix}.{key}"`、shape 違反は
    `f"{prefix}.{key}: shape ({reason})"` として区別できる形式で返す
    （後者は値そのものは記録されている＝§3.2 の「未記録」とは異なる違反種別
    であるため）。
    """
    violations: list[str] = []
    for key in required_keys:
        if key not in entry or _is_hollow(entry.get(key)):
            violations.append(f"{prefix}.{key}")
            continue
        reason = _shape_violation(key, entry[key])
        if reason is not None:
            violations.append(f"{prefix}.{key}: shape ({reason})")
    return violations


def _check_meter_spec_nested_keys(manifest: Mapping[str, object]) -> list[str]:
    """`frozen_design.meter_specs.<METER_ID>` の各エントリが `METER_SPEC_
    REQUIRED_KEYS` を完全に持つかを検査する（設計正本 §3.1「meter 別
    construct/unit/domain/algorithm family/有限 parameter grid/baseline/
    fallback/missing・failure rule」。`[UNDERSPEC-CAL-C17]`。Codex レビュー
    2026-09-01 P1: `meter_specs={meter_id: {"construct": "..."}}` のような
    hollow な placeholder エントリは、従来は `_check_meter_specs_coverage`
    の「meter family キーが存在するか」チェックのみを通過し、それ以上の
    内容検証を受けていなかった）。

    存在しない meter_id のエントリ自体は `_check_meter_specs_coverage` が
    別途捕捉するため、ここでは manifest に実際に供給されているエントリの
    ネスト鍵不備のみを検出する（二重報告回避）。
    """
    found, meter_specs = _resolve(manifest, "frozen_design.meter_specs")
    if not found or _is_hollow(meter_specs) or not isinstance(meter_specs, Mapping):
        return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
    violations: list[str] = []
    for meter_id in sorted(k for k in meter_specs.keys() if isinstance(k, str)):
        entry = meter_specs[meter_id]
        if not isinstance(entry, Mapping):
            violations.append(f"frozen_design.meter_specs.{meter_id} (entry must be a mapping)")
            continue
        violations += _nested_key_violations(
            entry, METER_SPEC_REQUIRED_KEYS, f"frozen_design.meter_specs.{meter_id}"
        )
    return violations


def _required_fixture_family_ids() -> frozenset[str]:
    """`fixtures.axes.FixtureFamily`（設計正本 §4.2 の 7 fixture family）の
    値集合。"""
    return frozenset(f.value for f in fixture_axes.FixtureFamily)


def _check_fixture_spec_coverage(manifest: Mapping[str, object]) -> list[str]:
    """`frozen_design.fixture_spec` が `fixtures.axes.FixtureFamily` の全 7
    family をカバーするかを検査する（`_check_meter_specs_coverage` と対をなす
    fixture 側の網羅性検査。`[UNDERSPEC-CAL-C17]`）。欠落 family を個別に
    列挙する。
    """
    found, fixture_spec = _resolve(manifest, "frozen_design.fixture_spec")
    if not found or _is_hollow(fixture_spec) or not isinstance(fixture_spec, Mapping):
        return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
    missing_families = sorted(_required_fixture_family_ids() - set(fixture_spec.keys()))
    return [f"frozen_design.fixture_spec.{family_id}" for family_id in missing_families]


def _check_fixture_spec_nested_keys(manifest: Mapping[str, object]) -> list[str]:
    """`frozen_design.fixture_spec.<FAMILY>` の各エントリが `FIXTURE_SPEC_
    REQUIRED_KEYS` を完全に持つかを検査する（設計正本 §3.1「fixture family・
    generator version/hash・known-truth field・confound 軸・boundary
    probes・negative controls」。`[UNDERSPEC-CAL-C17]`。Codex レビュー
    2026-09-01 P1: `fixture_spec={"family": "F0_CONTROL"}` のような hollow な
    placeholder manifest が REQUIRED_BLOCKING を素通りしていた本 finding の
    直接の再現ケース）。存在しない family のエントリ自体は
    `_check_fixture_spec_coverage` が別途捕捉する。
    """
    found, fixture_spec = _resolve(manifest, "frozen_design.fixture_spec")
    if not found or _is_hollow(fixture_spec) or not isinstance(fixture_spec, Mapping):
        return []  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
    violations: list[str] = []
    for family_id in sorted(k for k in fixture_spec.keys() if isinstance(k, str)):
        entry = fixture_spec[family_id]
        if not isinstance(entry, Mapping):
            violations.append(f"frozen_design.fixture_spec.{family_id} (entry must be a mapping)")
            continue
        violations += _nested_key_violations(
            entry, FIXTURE_SPEC_REQUIRED_KEYS, f"frozen_design.fixture_spec.{family_id}"
        )
    return violations


def _check_campaign_section_nested_keys(manifest: Mapping[str, object]) -> list[str]:
    """campaign-level frozen-design セクション（`split_spec` /
    `selection_spec` / `provenance_spec` / `cost_caps`）が、それぞれの必須
    ネストキーを完全に持つかを検査する（設計正本 §3.1「split・seed・seal、
    selection rule・tie rule・candidate exhaustion rule・holdout FAIL 後の
    固定 outcome、provenance schema・artifact layout・cost cap」。
    `[UNDERSPEC-CAL-C17]`）。`stop_rules` はネスト構造を設計正本が規定しない
    ため、本関数の対象外（`REQUIRED_BLOCKING_KEYS` 側の非空チェックのみ）。
    """
    violations: list[str] = []
    sections: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("frozen_design.split_spec", SPLIT_SPEC_REQUIRED_KEYS),
        ("frozen_design.selection_spec", SELECTION_SPEC_REQUIRED_KEYS),
        ("frozen_design.provenance_spec", PROVENANCE_SPEC_REQUIRED_KEYS),
        ("frozen_design.cost_caps", COST_CAPS_REQUIRED_KEYS),
    )
    for section_path, required_keys in sections:
        found, section = _resolve(manifest, section_path)
        if not found or _is_hollow(section) or not isinstance(section, Mapping):
            continue  # 欠落/非 mapping は _check_required_blocking 側で既に捕捉
        violations += _nested_key_violations(section, required_keys, section_path)
    return violations


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
    violations.extend(f"independence_ledger (missing candidate_id: {cid!r})" for cid in missing_ids)
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
            if not isinstance(seed_ref, str) or _SHA256_HEX_RE.fullmatch(seed_ref) is None:
                violations.append(
                    f"rng_ledger[{i}].public_seed_id (must be a 64-character lowercase sha256 hex digest when seeded=true)"
                )
    return violations


def _check_rng_ledger_closed_set(manifest: Mapping[str, object]) -> list[str]:
    """`rng_ledger` の `stream_name` 集合が §3.3 の凍結 closed set
    (`streams.expected_rng_stream_names()`) と厳密一致することを検査する
    （Codex レビュー 2026-09-01 P1 finding #2: 従来は entry 形状のみを検証し、
    stream 集合が閉じているか（欠落 family・unknown な余分 stream・重複宣言）
    は一切見ておらず、1 件の well-formed entry だけで通過してしまっていた）。

    C0 記録粒度（`streams.stream_name()`/`expected_rng_stream_names()`
    docstring も参照、`[UNDERSPEC-CAL-C16]`）: row/probe 単位の実 HKDF 導出は
    per-family render stream の sub-derivation であり C0 では個別列挙しない。
    欠落 stream・unknown/extra stream・重複宣言はそれぞれ個別に列挙する。
    """
    found, ledger = _resolve(manifest, "rng_ledger")
    if not found or _is_hollow(ledger) or not isinstance(ledger, Sequence):
        return []  # 欠落/空/非 list は _check_required_blocking 側で既に捕捉
    if isinstance(ledger, (str, bytes)):
        return []  # 形状違反は _check_rng_ledger_shape 側で既に捕捉

    names: list[str] = []
    for entry in ledger:
        if not isinstance(entry, Mapping):
            continue  # 形状違反は _check_rng_ledger_shape 側で既に捕捉
        name = entry.get("stream_name")
        if isinstance(name, str) and name.strip() != "":
            names.append(name)
        # 非文字列/空文字列は _check_rng_ledger_shape 側で既に捕捉

    expected = streams.expected_rng_stream_names()
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)

    missing = sorted(expected - seen)
    unknown = sorted(seen - expected)

    violations: list[str] = []
    violations += [f"rng_ledger (missing required stream: {n!r})" for n in missing]
    violations += [f"rng_ledger (unknown/extra stream: {n!r})" for n in unknown]
    violations += [f"rng_ledger (duplicate stream: {n!r})" for n in sorted(duplicates)]
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
    """pyworld 特則（§3.3）: exact version + wheel hash 欠落 → D4C のみ ineligible。

    Codex レビュー 2026-09-01 P1: 従来は「present かつ非 None かつ
    `ABSENT:` prefix でない」ことしか検査しておらず、`""`・`{}`・
    hash 形式を満たさない任意文字列であっても D4C eligible（=
    `d4c_ineligible=False`）と判定してしまっていた（hollow pin values
    enable D4C）。本実装は:

    - `pyworld_version`: 非空文字列（空白のみも不可）であることを要求する。
    - `pyworld_wheel_hash`: `^[0-9a-f]{64}$`（`_SHA256_HEX_RE`。
      `[UNDERSPEC-CAL-CXX]` 設計正本 §3.3 は wheel hash の具体的文字列形式
      までは規定しないため、`HASH_MAP_KEYS` など他の sha256 系フィールドが
      既に採用する bare 64 桁小文字 16 進形式へ統一する — 最も単純で
      一貫した選択。`sha256:<64hex>` のようなプレフィックス付き文字列は
      受理しない）にマッチすることを要求する。

    いずれかが欠落・hollow・形式不正なら `d4c_ineligible=True` とし、
    具体的にどちらが不正だったかを reason に列挙する（campaign 全体は
    引き続き BLOCK しない）。
    """
    found_version, version = _resolve(manifest, PYWORLD_VERSION_KEY)
    found_hash, wheel_hash = _resolve(manifest, PYWORLD_WHEEL_HASH_KEY)

    version_ok = (
        found_version
        and isinstance(version, str)
        and not _is_absent_marker(version)
        and version.strip() != ""
    )
    hash_ok = (
        found_hash
        and isinstance(wheel_hash, str)
        and not _is_absent_marker(wheel_hash)
        and _SHA256_HEX_RE.match(wheel_hash) is not None
    )
    if version_ok and hash_ok:
        return False, None

    details: list[str] = []
    if not version_ok:
        details.append("pyworld_version missing/blank")
    if not hash_ok:
        details.append("pyworld_wheel_hash missing or not a bare 64-hex sha256")
    return True, "; ".join(details)


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


def _check_declared_sweep_truth_levels(manifest: Mapping[str, object]) -> tuple[str, ...]:
    """UNDERSPEC-CAL-D76 ruling (2)（`_check_sweep_capacity`/D75 を
    SUPERSEDE）, 設計正本 §10.4「resolvable pair は各 sweep で >= 3」: family
    ごとの宣言済み declared sweep（`fixtures.matrix.declared_sweeps_by_
    family()`、def A — truth-core block の nuisance-constant series）が、
    それぞれ相異なる truth level（`fixtures.matrix.truth_identity_for_row()`）
    を `gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP` (3) 種以上持つ（= `C(levels,
    2) >= 3` 個の resolvable pair 候補が理論上作れる）ことを検査する。

    `_check_hash_content_match` と同じ「宣言でなく実体を検査する」規約を
    採る: `manifest` の `frozen_design.fixture_spec.<FAMILY>.declared_sweeps`
    宣言値ではなく、`fixtures.matrix.build_matrix()` が返す実際の凍結
    matrix から直接再導出する（`manifest` 引数は他の `_check_*` と呼び出し
    規約を揃えるためだけに受け取り、内容は参照しない — 「matrix 生成ロジック
    自体が §10.4 の前提を構造的に満たせるか」という manifest 非依存の構造
    検査であり、`declared_sweeps_by_family()` が今後もこの `build_matrix()`
    を唯一の権威として使う限り、manifest の宣言値は定義上ここで数える値と
    一致する）。

    違反があれば `frozen_design.fixture_spec.*` の hollow/shape 違反とは
    独立した意味論的欠陥として、専用の `vocab.BlockedCode.
    BLOCKED_C0_SWEEP_DECLARATION_INVALID` で別途 fail-closed する
    （`BLOCKED_C0_MANIFEST_INCOMPLETE` とは排他ではなく併発しうる）。
    """
    del manifest  # 構造検査: 凍結 matrix 生成器自体から直接導出する
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    declared = declared_sweeps_by_family(rows)
    violations: list[str] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        family_sweeps = declared.get(fam, {})
        for sweep_id in sorted(family_sweeps):
            member_row_ids = family_sweeps[sweep_id]
            n_levels = len({truth_identity_for_row(row_by_id[rid]) for rid in member_row_ids})
            if n_levels < MIN_RESOLVABLE_PAIRS_PER_SWEEP:
                violations.append(
                    f"frozen_design.fixture_spec.{fam}.declared_sweeps[{sweep_id!r}] "
                    f"({n_levels} distinct truth level(s) in the frozen matrix, need >= "
                    f"{MIN_RESOLVABLE_PAIRS_PER_SWEEP})"
                )
    return tuple(violations)


def validate_c0_manifest(manifest: Mapping[str, object]) -> C0ValidationResult:
    """C0 freeze manifest を dry-run 検証する（書込・secret 生成・freeze event なし）。"""
    missing_required = _check_required_blocking(manifest)
    missing_required += _check_claim_critical_set(manifest)
    missing_required += _check_checkout_identity(manifest)
    missing_required += _check_hash_maps(manifest)
    missing_required += _check_path_inventory_coverage(manifest)
    missing_required += _check_hash_map_category_uniqueness(manifest)
    missing_required += _check_hash_content_match(manifest)
    missing_required += _check_meter_specs_coverage(manifest)
    missing_required += _check_meter_spec_nested_keys(manifest)
    missing_required += _check_fixture_spec_coverage(manifest)
    missing_required += _check_fixture_spec_nested_keys(manifest)
    missing_required += _check_campaign_section_nested_keys(manifest)
    missing_required += _check_independence_ledger(manifest)
    missing_required += _check_rng_ledger_shape(manifest)
    missing_required += _check_rng_ledger_closed_set(manifest)

    missing_recorded, downgrades = _check_recorded_or_absent(manifest)
    all_missing = tuple(missing_required + missing_recorded)

    d4c_ineligible, d4c_reason = _check_pyworld(manifest)
    unseeded_streams = _check_rng_ledger_unseeded(manifest)
    sweep_violations = _check_declared_sweep_truth_levels(manifest)

    blocked: list[vocab.BlockedCode] = []
    if all_missing:
        blocked.append(vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE)
    if unseeded_streams:
        blocked.append(vocab.BlockedCode.BLOCKED_C0_UNSEEDED_RNG)
    if sweep_violations:
        blocked.append(vocab.BlockedCode.BLOCKED_C0_SWEEP_DECLARATION_INVALID)

    return C0ValidationResult(
        blocked_codes=tuple(blocked),
        missing_required_keys=all_missing,
        downgrade_annotations=tuple(downgrades),
        d4c_ineligible=d4c_ineligible,
        d4c_ineligibility_reason=d4c_reason,
        unseeded_rng_streams=unseeded_streams,
        sweep_declaration_violations=sweep_violations,
    )
