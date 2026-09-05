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
    boundary_probes/negative_controls/declared_sweeps）を完全に持つことを
    要求する（`[UNDERSPEC-CAL-C17]`。Codex レビュー 2026-09-01 P1: 従来
    `fixture_spec={"family": "F0_CONTROL"}` のような hollow な
    placeholder manifest が素通りしていた finding の直接該当箇所）。
    `declared_sweeps` は非空 mapping であることに加え（`_MAPPING_SHAPE_
    FIELDS`）、宣言値そのものが凍結 matrix (`fixtures.matrix.build_matrix()`)
    から `declared_sweeps_by_family()` で導出される mapping と完全一致
    することを要求する（UNDERSPEC-CAL-D77 ruling (1)。不一致は
    `BLOCKED_C0_MANIFEST_INCOMPLETE`（detail:
    `SweepManifestViolationDetail(violation="sweep_declaration_mismatch")`）
    で個別に fail-closed する。`[UNDERSPEC-CAL-D77]` / `[UNDERSPEC-CAL-D78]`
    ——D78 ruling が専用 `BlockedCode` の新設を SUPERSEDE し、既存コードの
    detail フィールドへ表現を移した）。
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

import argparse
import ast
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import approvals, streams, vocab
from .candidates import registry as candidate_registry
from .fixtures import axes as fixture_axes
from .fixtures import uncertainty as fixture_uncertainty
#: R24-2 対応（Codex 第 24 巡 P2 採用, 2026-09-05）: `_legacy_v1_0_opt_in_
#: verified()` が aborted/closed 判定の実検証（gz+sidecar pair 検証・
#: ledger chain 検証）を `tools.archive_aborted_ledger`/`provenance.Ledger`
#: と共有するための import。`provenance.py`/`tools/archive_aborted_
#: ledger.py` はいずれも `c0_validate` を import しないため循環 import には
#: ならない。
from .provenance import Ledger
from .tools import archive_aborted_ledger
from .fixtures.matrix import (
    HoldoutPinDegradationExhausted,
    HoldoutPinInfeasible,
    build_matrix,
    claim_relevant_fields_by_family,
    declared_sweeps_by_family,
    holdout_pin_params_by_family,
    invariance_axes_by_family,
    truth_identity_for_row,
)
#: §V2.2 縮退規則（2026-09-04 追補）: `c0_freeze._fixture_specs()` は
#: `frozen_design.fixture_spec.<FAMILY>.declared_sweeps`/
#: `claim_relevant_fields` を、テストが `fixtures.matrix.build_matrix`
#: （このモジュールの `build_matrix` も同じ束縛）を差し替えても影響を受けない
#: 別名 `_canonical_build_matrix` から意図的に導出する（c0_freeze.py 自身の
#: 同名エイリアスと同じ規約——「差し替えから独立させる」）。本モジュールの
#: 3 検査（`_check_declared_sweep_truth_levels`/`_check_declared_sweep_
#: declaration_match`/`_check_claim_relevant_fields_match`）は manifest の
#: その frozen 節と突き合わせる比較器であるため、同じ「常に real matrix」の
#: 入口をこのエイリアス経由で使う。対して holdout sweep pin 関連の 2 検査
#: （`_check_holdout_pin_feasibility`/`_check_holdout_sweeps_declaration_
#: match`）は `armed_freeze()` が実際に pin/split した行集合と突き合わせる
#: ため、`build_matrix`（差し替え可能な束縛）を引き続き使う——`armed_freeze()`
#: 自身の pin 計算 (`c0_freeze.py` の `matrix_rows = build_matrix()`) も同じ
#: 差し替え可能な参照を使うため、対応関係が一致する。
from .fixtures.matrix import build_matrix as _canonical_build_matrix
from .gates import MIN_RESOLVABLE_PAIRS_PER_SWEEP
#: R10 対応（2026-09-05）: `armed_freeze()` が実際に holdout sweep を
#: pin/split するのと**同一の**縮退ループ入口（`splitter.
#: pin_and_realize_holdout()` — 段 1 `pin_holdout_sweeps_by_family()` +
#: 段 2 `splitter.realize_split()` の統合リトライ本体）を検証側から
#: 呼ぶための import。`c0_freeze.py` は `c0_validate` を import するため
#: 逆方向の import はできず、両者が依存できる中立モジュール `splitter.py`
#: から取る（生成と検証が二重実装に分岐しない構造 — 詳細は
#: `splitter.py` の該当節 docstring、および
#: `_check_holdout_sweeps_declaration_match()` の docstring を参照）。
from .splitter import STRATUM_FACTOR_NAMES, pin_and_realize_holdout, row_inputs_for_split

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
    #: R22-1 対応（Codex 第 22 巡 finding (1)、2026-09-05）: marker 自体を
    #: REQUIRED_BLOCKING 化する（旧: 欠落は legacy v1.0 として黙って許容して
    #: いたため、marker を削除/改変するだけで `_is_v1_1_manifest()` が False
    #: になり、bound/formula/unit 必須化 (R20-3/R21/R22-2) がまるごと無効化
    #: できてしまっていた）。値の閉語彙判定は `_check_required_blocking()`
    #: 内の専用分岐（`_ALLOWED_DESIGN_REVISIONS`）で行う——legacy v1.0
    #: manifest の検証は `validate_c0_manifest(..., allow_legacy_v1_0=True)`
    #: による明示 opt-in（かつ closed/aborted campaign の on-disk manifest に
    #: 限定）でのみ許容する。
    "frozen_design.design_revision",
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
#: `[UNDERSPEC-CAL-C17]`）。`declared_sweeps`
#: （UNDERSPEC-CAL-D77 ruling (1). #344 round 8 finding #1 ADOPT, 分類②）:
#: `c0_freeze._fixture_specs()` は既に `declared_sweeps`
#: （`fixtures.matrix.declared_sweeps_by_family()` の出力、def A）を
#: `manifest_core_sha` 対象として書き込んでいたが、本語彙には未列挙のため、
#: manifest がこのフィールドを欠落・矛盾させても REQUIRED_BLOCKING を素通り
#: していた（validator 側は `_check_declared_sweep_truth_levels()` で
#: manifest 非依存に凍結 matrix を直接再導出して検証するのみで、manifest の
#: 宣言値そのものは一度も読んでいなかった——「宣言と実体が食い違っていても
#: 検出できない」provenance artifact contamination）。追加により (a) 欠落/
#: hollow は他の必須ネスト鍵と同様に `missing_required_keys` へ、(b) 値が
#: mapping でない/空は `_shape_violation`（`_MAPPING_SHAPE_FIELDS`）経由で
#: 同じく `missing_required_keys` へ、(c) mapping ではあるが凍結 matrix
#: からの導出値と完全一致しない（sweep_id 集合・member row_id の並びの
#: いずれか）場合は `_check_declared_sweep_declaration_match()` が
#: `BLOCKED_C0_MANIFEST_INCOMPLETE`（detail:
#: `SweepManifestViolationDetail(violation="sweep_declaration_mismatch")`）
#: で個別に fail-closed する（UNDERSPEC-CAL-D78 ruling: 専用
#: `BLOCKED_C0_SWEEP_DECLARATION_MISMATCH` を SUPERSEDE）。
FIXTURE_SPEC_REQUIRED_KEYS: tuple[str, ...] = (
    "generator_version",
    "generator_hash",
    "known_truth_field",
    "confound_axes",
    "boundary_probes",
    "negative_controls",
    "declared_sweeps",
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

#: 非空 mapping であることを要求するネストフィールド名。`declared_sweeps`
#: （UNDERSPEC-CAL-D77 ruling (1)）はここでは外側 mapping の非空性のみを
#: 検査し、`sweep_id -> row_id 列` という内側の形状・凍結 matrix との完全
#: 一致は `_check_declared_sweep_declaration_match()` が別途検査する
#: （外側が mapping ですらない/空の場合はここで `missing_required_keys` 側
#: に倒し、mismatch チェック側での二重報告を避ける）。
_MAPPING_SHAPE_FIELDS: frozenset[str] = frozenset({"parameter_grid", "declared_sweeps"})

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

    v1.1 §V6（統合3, `[UNDERSPEC-CAL-D79]`, WP2d 報告の申し送り）: 統治設計
    文書 2 本（v1.1 統治正本 `approvals.DESIGN_DOC_RELATIVE_PATH` / 読み取り
    専用基底 `approvals.BASE_DESIGN_DOC_RELATIVE_PATH`）を scan 結果へ union
    する — どちらも `.py` ではないため `rglob("*.py")` からは構造的に漏れて
    おり、v1.1 §V6 が要求する「v1.0/v1.1 両文書を path inventory 検査対象へ」
    が未実施のままだった。文書パスに対する sha 検査等の意味論は追加しない
    （既存の inventory 項目と同じ「対象集合に含まれる」以上の扱いを増やさない
    ——過剰設計しない）。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    package_dir = root / "voice_genesis" / "calibration"
    paths = {p.relative_to(root).as_posix() for p in package_dir.rglob("*.py")}
    paths.add((package_dir / PATH_INVENTORY_FILENAME).relative_to(root).as_posix())
    paths.add(approvals.DESIGN_DOC_RELATIVE_PATH)
    paths.add(approvals.BASE_DESIGN_DOC_RELATIVE_PATH)
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


def _numbers_close(declared: object, derived: object) -> bool:
    """R22-2 対応: `declared`（manifest 宣言値）と `derived`（canonical
    再導出値）が両方とも non-bool numeric で、かつ数値的に一致するかを判定する
    （`math.isclose(rel_tol=1e-9, abs_tol=1e-12)`。過大申告も不一致として
    拒否する——freeze は決定論的なので厳密等値が正しい）。片方でも非 numeric
    （例: 型不正な宣言値）なら無条件で False（fail-closed）。"""
    if isinstance(declared, bool) or isinstance(derived, bool):
        return False
    if not isinstance(declared, (int, float)) or not isinstance(derived, (int, float)):
        return False
    return math.isclose(float(declared), float(derived), rel_tol=1e-9, abs_tol=1e-12)


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
class SweepManifestViolationDetail:
    """UNDERSPEC-CAL-D78 ruling（#344 round 9 ADOPT, 分類②。D76 ruling (2) の
    `BLOCKED_C0_SWEEP_DECLARATION_INVALID` と D77 ruling (1) の
    `BLOCKED_C0_SWEEP_DECLARATION_MISMATCH` はいずれも `vocab.BlockedCode`
    を凍結 6 値を超えて事後拡張する contract vocabulary contamination
    だったため撤去した。両者が検出していた fail-closed 事由は、代わりに
    既存の `vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE` を発行しつつ
    本 dataclass の tuple として運ぶ——downstream consumer は凍結 6 値の
    `BlockedCode` のみを見ればよく、診断に必要な情報は失われない。

    `violation` は次の 2 語彙のいずれか（閉語彙。本 dataclass 自体は
    `BlockedCode` ではなく detail 構造のため、値の追加は `BlockedCode` の
    事後追加禁止規約の対象外）:

    - ``"sweep_truth_level_insufficient"``: D76 ruling (2)。凍結 matrix
      (`fixtures.matrix.build_matrix()`) が manifest 非依存に §10.4 の
      truth-level 下限（`gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP`）を構造的に
      満たせない（`_check_declared_sweep_truth_levels()`）。
    - ``"sweep_declaration_mismatch"``: D77 ruling (1)。manifest の
      `frozen_design.fixture_spec.<FAMILY>.declared_sweeps` 宣言値が、凍結
      matrix からの導出値と完全一致しない
      （`_check_declared_sweep_declaration_match()`）。
    - ``"claim_relevant_field_mismatch"``: v1.1 §V2.2 5th bullet。manifest の
      `frozen_design.fixture_spec.<FAMILY>.claim_relevant_fields` 宣言値が、
      凍結 matrix からの機械導出値と一致しない
      （`_check_claim_relevant_fields_match()`）。
    - ``"holdout_pin_infeasible"``: v1.1 §V2.2。凍結 matrix 自体が k_hold の
      被覆要件を cap `floor((N_hold-1)/r)` 内で満たせない構造
      （`_check_holdout_pin_feasibility()`。456 セルでは発生しない）。
    - ``"holdout_pin_declaration_mismatch"``: v1.1 §V2.2/§V2.3。manifest の
      `frozen_design.fixture_spec.<FAMILY>.holdout_sweeps` 宣言値が、
      split_secret からの再導出（渡された場合）または凍結 matrix の
      declared_sweeps/k_hold との構造整合（渡されない場合）と一致しない
      （`_check_holdout_sweeps_declaration_match()`）。
    - ``"holdout_pin_not_in_holdout_split"``: v1.1 §V2.3。`holdout_sweeps`
      の member 行が `realized_split.assignment` 上で HOLDOUT に割当てられて
      いない（`_check_holdout_sweeps_realized_membership()`）。
    """

    violation: str
    family: str
    sweep_id: str
    expected_count: int
    actual_count: int
    detail: str


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
    #: truth_levels()` の violation 列（`SweepManifestViolationDetail`、
    #: `violation="sweep_truth_level_insufficient"`）。非空なら
    #: `vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE` が `blocked_codes`
    #: に入る（UNDERSPEC-CAL-D78 ruling: 旧 `BLOCKED_C0_SWEEP_DECLARATION_
    #: INVALID` を SUPERSEDE）。
    sweep_declaration_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: UNDERSPEC-CAL-D77 ruling (1)（#344 round 8 finding #1 ADOPT）:
    #: `_check_declared_sweep_declaration_match()` の violation 列
    #: （`SweepManifestViolationDetail`、`violation="sweep_declaration_
    #: mismatch"`）。非空なら `vocab.BlockedCode.BLOCKED_C0_MANIFEST_
    #: INCOMPLETE` が `blocked_codes` に入る（UNDERSPEC-CAL-D78 ruling: 旧
    #: `BLOCKED_C0_SWEEP_DECLARATION_MISMATCH` を SUPERSEDE）。
    sweep_declaration_mismatch_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V2.2 5th bullet: `_check_claim_relevant_fields_match()` の
    #: violation 列（`violation="claim_relevant_field_mismatch"`）。
    claim_relevant_field_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V2.2: `_check_holdout_pin_feasibility()` の violation 列
    #: （`violation="holdout_pin_infeasible"`）。manifest 非依存の構造検査
    #: （456 セルでは常に空）。
    holdout_pin_feasibility_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V2.2/§V2.3: `_check_holdout_sweeps_declaration_match()` の
    #: violation 列（`violation="holdout_pin_declaration_mismatch"`）。
    holdout_pin_declaration_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V2.3: `_check_holdout_sweeps_realized_membership()` の
    #: violation 列（`violation="holdout_pin_not_in_holdout_split"`）。
    holdout_pin_membership_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V3.5: `_check_invariance_axes_match()` の violation 列
    #: （`violation="invariance_axis_declaration_mismatch"`）。manifest の
    #: `frozen_design.fixture_spec.<FAMILY>.confound_axes` 宣言値が
    #: `fixtures.matrix.invariance_axes_by_family()` の機械導出値と一致
    #: しない場合に非空になる（D77/claim_relevant_fields 同型）。
    invariance_axis_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )
    #: v1.1 §V3.3 末尾: `_check_u_gt_u_num_bounds()` の violation 列
    #: （`violation="u_bound_missing_or_invalid"`）。非 ABSENT family の
    #: `u_gt_bound`/`u_num_bound`/`*_formula` の存在・有限非負・非空文字列を
    #: 検査する（キー自体が manifest に無い legacy manifest は対象外——
    #: version-aware。値はあるが欠陥がある場合のみ fail-closed）。
    u_gt_u_num_bound_violations: tuple[SweepManifestViolationDetail, ...] = field(
        default_factory=tuple
    )

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_codes) > 0


def _check_required_blocking(
    manifest: Mapping[str, object], *, legacy_design_revision_ok: bool = False
) -> list[str]:
    """REQUIRED_BLOCKING キーのうち欠落・hollow なものを返す。

    `frozen_design.design_revision` は他の REQUIRED_BLOCKING キーと異なり、
    「非 hollow なら OK」ではなく閉語彙 `_ALLOWED_DESIGN_REVISIONS`（現状
    `{"1.1"}`）との厳密一致を要求する（R22-1、Codex 第 22 巡 finding (1)）。
    `legacy_design_revision_ok=True`（`validate_c0_manifest(...,
    allow_legacy_v1_0=True)` が on-disk の closed/aborted campaign manifest に
    対してのみ立てる）の場合に限り、marker の欠落・不一致を violation にしない
    ——それ以外は常に fail-closed（新規 freeze 経路が呼ぶ
    `validate_c0_manifest()` はこの引数を渡さないため、常に必須のまま）。

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
        if key == "frozen_design.design_revision":
            found, value = _resolve(manifest, key)
            revision_ok = (
                found and isinstance(value, str) and value.strip() in _ALLOWED_DESIGN_REVISIONS
            )
            if not revision_ok and not legacy_design_revision_ok:
                if not found or value is None or _is_hollow(value):
                    missing.append(key)
                else:
                    missing.append(
                        f"{key}: closed vocabulary (must be exactly one of "
                        f"{sorted(_ALLOWED_DESIGN_REVISIONS)!r}, got {value!r}; legacy v1.0 "
                        "manifests require validate_c0_manifest(allow_legacy_v1_0=True) "
                        "opt-in restricted to closed/aborted campaigns, R22-1)"
                    )
            continue
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


def _check_declared_sweep_truth_levels(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
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
    独立した意味論的欠陥として、`vocab.BlockedCode.
    BLOCKED_C0_MANIFEST_INCOMPLETE` を発行しつつ
    `SweepManifestViolationDetail(violation="sweep_truth_level_insufficient")`
    の tuple として返す（`missing_required_keys` とは排他ではなく併発しうる。
    UNDERSPEC-CAL-D78 ruling: 旧専用コード `BLOCKED_C0_SWEEP_DECLARATION_
    INVALID` を SUPERSEDE）。
    """
    del manifest  # 構造検査: 凍結 matrix 生成器自体から直接導出する
    # §V2.2 縮退規則: manifest の frozen_design 節と同じ「常に real matrix」
    # の入口（`_canonical_build_matrix`）を使う——`build_matrix`（差し替え
    # 可能）ではない。
    rows = _canonical_build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    declared = declared_sweeps_by_family(rows)
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        family_sweeps = declared.get(fam, {})
        for sweep_id in sorted(family_sweeps):
            member_row_ids = family_sweeps[sweep_id]
            n_levels = len({truth_identity_for_row(row_by_id[rid]) for rid in member_row_ids})
            if n_levels < MIN_RESOLVABLE_PAIRS_PER_SWEEP:
                violations.append(
                    SweepManifestViolationDetail(
                        violation="sweep_truth_level_insufficient",
                        family=fam,
                        sweep_id=sweep_id,
                        expected_count=MIN_RESOLVABLE_PAIRS_PER_SWEEP,
                        actual_count=n_levels,
                        detail=(
                            f"frozen_design.fixture_spec.{fam}.declared_sweeps[{sweep_id!r}] "
                            f"({n_levels} distinct truth level(s) in the frozen matrix, need >= "
                            f"{MIN_RESOLVABLE_PAIRS_PER_SWEEP})"
                        ),
                    )
                )
    return tuple(violations)


def _normalize_declared_sweeps(value: object) -> dict[str, tuple[str, ...]] | None:
    """`value` を `sweep_id -> (member row_id, ...)` の正規形へ変換する。
    形状が「mapping[str, list[str]]（各 list は非空）」を満たさない場合は
    `None` を返す（呼び出し側はこれを「凍結 matrix からの導出値とは一致し
    得ない」として扱う——`_check_declared_sweep_declaration_match()` の
    「形状検証と一致検証を同一の不一致判定へ統合する」規約）。"""
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, tuple[str, ...]] = {}
    for sweep_id, members in value.items():
        if not isinstance(sweep_id, str):
            return None
        if not isinstance(members, (list, tuple)) or isinstance(members, (str, bytes)):
            return None
        if len(members) == 0:
            return None
        member_ids: list[str] = []
        for member in members:
            if not isinstance(member, str):
                return None
            member_ids.append(member)
        normalized[sweep_id] = tuple(member_ids)
    return normalized


def _check_declared_sweep_declaration_match(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """UNDERSPEC-CAL-D77 ruling (1)（#344 round 8 finding #1 ADOPT, 分類②）:
    `frozen_design.fixture_spec.<FAMILY>.declared_sweeps` の**宣言値**が、
    凍結 matrix (`fixtures.matrix.build_matrix()`) から
    `fixtures.matrix.declared_sweeps_by_family()` で直接導出される
    mapping と **完全一致**（sweep_id 集合・各 sweep の member row_id の
    並び順まで）することを検査する。

    `_check_declared_sweep_truth_levels()`（既存, D76 ruling (2)）は
    manifest の宣言値を一切読まず凍結 matrix から独立に再導出した値のみを
    検査する「構造がそもそも §10.4 の前提を満たせるか」チェックであり、
    manifest が実際に何を宣言しているか（あるいは全く宣言していないか）
    とは無関係だった——manifest がこのフィールドを省略しても
    `FIXTURE_SPEC_REQUIRED_KEYS` に列挙されていなかったため
    `missing_required_keys` は素通りし、`_check_declared_sweep_truth_
    levels()` は manifest 非依存に「正しい」判定を返すため、**宣言を
    欠いた/矛盾した manifest でも C0 全体が PASS し得た**（provenance
    artifact contamination）。本関数はその隙間を埋める: manifest の宣言値
    そのものを実体（凍結 matrix からの導出値）と直接突合する
    （`_check_hash_content_match` と同じ「宣言でなく実体との一致を検査
    する」規約）。

    欠落/hollow（=`declared_sweeps` キー自体が存在しない）は
    `FIXTURE_SPEC_REQUIRED_KEYS` 経由の `missing_required_keys`
    （既存の incomplete-manifest block）が既に捕捉するため、ここでは扱わ
    ない（二重報告回避）。値が mapping ですらない/空の場合も
    `_MAPPING_SHAPE_FIELDS` 経由で同じく `missing_required_keys` 側へ倒す
    ため、ここでは扱わない。**値が非空 mapping ではあるが**内側の形状が
    壊れている（sweep 値が非 list/空 list、member が非 str 等）場合は
    family 単位の 1 件（`sweep_id=""`）として、形状は正しいが導出値と
    内容が食い違う（sweep_id の過不足、member row_id の欠落/追加/並び違い）
    場合は sweep_id 単位で個別に、`vocab.BlockedCode.
    BLOCKED_C0_MANIFEST_INCOMPLETE` を発行しつつ
    `SweepManifestViolationDetail(violation="sweep_declaration_mismatch")`
    の tuple として返す（UNDERSPEC-CAL-D78 ruling: 旧専用コード
    `BLOCKED_C0_SWEEP_DECLARATION_MISMATCH` を SUPERSEDE。前者・後者とも
    「宣言が実体と一致しない」という同一の意味論のため `violation` 値は
    区別しない）。
    """
    # §V2.2 縮退規則: 上と同じ理由で `_canonical_build_matrix` を使う。
    rows = _canonical_build_matrix()
    derived = declared_sweeps_by_family(rows)
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        found, entry = _resolve(manifest, f"frozen_design.fixture_spec.{fam}")
        if not found or not isinstance(entry, Mapping):
            continue  # frozen_design.fixture_spec.<FAMILY> 自体の欠落は他の checker が捕捉
        declared_raw = entry.get("declared_sweeps")
        if declared_raw is None or _is_hollow(declared_raw) or not isinstance(declared_raw, Mapping):
            continue  # 欠落/hollow/非 mapping は missing_required_keys 側が既に捕捉
        expected = derived.get(fam, {})
        actual = _normalize_declared_sweeps(declared_raw)
        if actual == expected:
            continue
        if actual is None:
            # 内側の形状そのものが壊れており個々の sweep_id へ帰属できない
            # （例: sweep 値が非 list/空 list、member が非 str）。
            violations.append(
                SweepManifestViolationDetail(
                    violation="sweep_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=len(expected),
                    actual_count=len(declared_raw),
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.declared_sweeps "
                        "(inner shape malformed; does not exactly match the mapping "
                        "derived from the frozen matrix)"
                    ),
                )
            )
            continue
        for sweep_id in sorted(set(actual) | set(expected)):
            actual_members = actual.get(sweep_id)
            expected_members = expected.get(sweep_id)
            if actual_members == expected_members:
                continue
            expected_count = len(expected_members or ())
            actual_count = len(actual_members or ())
            violations.append(
                SweepManifestViolationDetail(
                    violation="sweep_declaration_mismatch",
                    family=fam,
                    sweep_id=sweep_id,
                    expected_count=expected_count,
                    actual_count=actual_count,
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.declared_sweeps[{sweep_id!r}] "
                        f"(expected {expected_count} member row_id(s), got {actual_count}; "
                        "does not exactly match the mapping derived from the frozen matrix)"
                    ),
                )
            )
    return tuple(violations)


# ---------------------------------------------------------------------------
# v1.1 §V2.2/§V2.3 — holdout sweep pinning validation (D77 同型)
# ---------------------------------------------------------------------------


def _check_claim_relevant_fields_match(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V2.2 5th bullet（Codex レビュー第 5 巡 P1 採用）:
    `frozen_design.fixture_spec.<FAMILY>.claim_relevant_fields` の宣言値が、
    凍結 matrix から `fixtures.matrix.claim_relevant_fields_by_family()` で
    機械導出される値と完全一致することを検査する
    （`_check_declared_sweep_declaration_match()` と同じ「宣言でなく実体
    との一致を検査する」規約）。

    `claim_relevant_fields` は v1.1 で新設したキーであり
    `FIXTURE_SPEC_REQUIRED_KEYS` には加えない——v1.1 以前に構築された
    manifest fixture（本テストスイートに大量に存在する）へ強制的に追加
    させる破壊的変更を避けるため。欠落/hollow/非 mapping な
    `frozen_design.fixture_spec.<FAMILY>` はここでは扱わない（他の checker
    が別途捕捉する）。宣言されていれば実体と一致しなければならない、という
    任意フィールドとして扱う。
    """
    # §V2.2 縮退規則: 上と同じ理由で `_canonical_build_matrix` を使う。
    rows = _canonical_build_matrix()
    derived = claim_relevant_fields_by_family(rows)
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        found, entry = _resolve(manifest, f"frozen_design.fixture_spec.{fam}")
        if not found or not isinstance(entry, Mapping):
            continue
        declared_raw = entry.get("claim_relevant_fields")
        if declared_raw is None:
            continue
        expected = tuple(sorted(derived.get(fam, ())))
        if not isinstance(declared_raw, (list, tuple)) or isinstance(declared_raw, (str, bytes)):
            violations.append(
                SweepManifestViolationDetail(
                    violation="claim_relevant_field_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=len(expected),
                    actual_count=0,
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.claim_relevant_fields must be a "
                        "list of field names"
                    ),
                )
            )
            continue
        actual = tuple(sorted(str(v) for v in declared_raw))
        if actual != expected:
            violations.append(
                SweepManifestViolationDetail(
                    violation="claim_relevant_field_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=len(expected),
                    actual_count=len(actual),
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.claim_relevant_fields declares "
                        f"{list(actual)!r}, expected {list(expected)!r} (machine-derived "
                        "from the frozen matrix)"
                    ),
                )
            )
    return tuple(violations)


def _check_invariance_axes_match(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V3.5（Codex レビュー第 12 巡 P1 採用）: `frozen_design.
    fixture_spec.<FAMILY>.confound_axes`（gate4' invariance 軸宣言）の宣言値
    が、凍結 matrix から `fixtures.matrix.invariance_axes_by_family()` で
    機械導出される値と完全一致することを検査する
    （`_check_declared_sweep_declaration_match()`/`_check_claim_relevant_
    fields_match()` と同じ「宣言でなく実体との一致を検査する」規約——D77
    同型）。`confound_axes` 自体の非空 list 形状は `_check_fixture_spec_
    nested_keys()`/`_shape_violation()`（`_LIST_SHAPE_FIELDS`）が既に検査
    済みのため、ここでは値そのものが family 固有の正しい導出値と一致するか
    のみを検査する（欠落/hollow/非 list はここでは扱わない）。
    """
    # §V2.2 縮退規則と同じ理由で `_canonical_build_matrix` を使う。
    rows = _canonical_build_matrix()
    derived = invariance_axes_by_family(rows)
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        found, entry = _resolve(manifest, f"frozen_design.fixture_spec.{fam}")
        if not found or not isinstance(entry, Mapping):
            continue
        declared_raw = entry.get("confound_axes")
        if declared_raw is None or _is_hollow(declared_raw):
            continue
        expected = tuple(sorted(derived.get(fam, ())))
        if not isinstance(declared_raw, (list, tuple)) or isinstance(declared_raw, (str, bytes)):
            violations.append(
                SweepManifestViolationDetail(
                    violation="invariance_axis_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=len(expected),
                    actual_count=0,
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.confound_axes must be a list of "
                        "axis names"
                    ),
                )
            )
            continue
        actual = tuple(sorted(str(v) for v in declared_raw))
        if actual != expected:
            violations.append(
                SweepManifestViolationDetail(
                    violation="invariance_axis_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=len(expected),
                    actual_count=len(actual),
                    detail=(
                        f"frozen_design.fixture_spec.{fam}.confound_axes declares "
                        f"{list(actual)!r}, expected {list(expected)!r} (machine-derived "
                        "gate4' invariance axes from the frozen matrix, v1.1 §V3.5)"
                    ),
                )
            )
    return tuple(violations)


#: v1.1 §V3.3 末尾: `u_gt_bound`/`u_num_bound` が `"ABSENT:<reason>"` marker
#: のみ許容される 2 family（`c0_freeze._U_ABSENT_REASON` と同じ集合——物理
#: gate 入力を持たない）。
_U_GT_U_NUM_ABSENT_ONLY_FAMILIES: frozenset[str] = frozenset(
    {"RESONANCE_GT", "IDENTITY_CAUSAL_SWEEP"}
)

#: R20-3 対応（Codex 第 20 巡 finding (3)、2026-09-05）: `frozen_design.
#: design_revision` の値がこれと一致する manifest のみ「v1.1 manifest」
#: として扱う（`c0_freeze._DESIGN_REVISION` と同期）。キー自体が無い manifest
#: （既存 closed campaign 3 件を含む legacy v1.0 形式）は判別対象外のまま
#: 従来の後方互換経路（欠落キーは fail-closed にしない）を維持する。
_V1_1_DESIGN_REVISION: str = "1.1"

#: R22-1 対応（Codex 第 22 巡 finding (1)、2026-09-05）: `_check_required_
#: blocking()` が `frozen_design.design_revision` を照合する閉語彙。現状は
#: `_V1_1_DESIGN_REVISION` のみを含む単一要素集合だが、将来 v1.2 等が追加
#: された際に両バージョンを同時に許容できるよう set として持つ（`"1.0"` を
#: 含む他の値・欠落はすべて REQUIRED_BLOCKING violation。legacy v1.0 は
#: `allow_legacy_v1_0=True` opt-in 経由でのみ通す）。
_ALLOWED_DESIGN_REVISIONS: frozenset[str] = frozenset({_V1_1_DESIGN_REVISION})


def _is_v1_1_manifest(manifest: Mapping[str, object]) -> bool:
    found, value = _resolve(manifest, "frozen_design.design_revision")
    return found and isinstance(value, str) and value.strip() == _V1_1_DESIGN_REVISION


def _check_u_gt_u_num_bounds(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V3.3 末尾（本 PR で新設。R20-3 で欠落キーの判別を version-aware
    化）: 非 ABSENT family（F0_CONTROL/FORMANT_GT/TILT_GT/APERIODICITY_GT/
    TRANSITION_GT）は `frozen_design.fixture_spec.<FAMILY>.u_gt_bound`/
    `.u_num_bound` が有限非負の number として存在し、対応する
    `.u_gt_bound_formula`/`.u_num_bound_formula` の導出式文字列も非空で
    存在することを要求する。RESONANCE_GT/IDENTITY_CAUSAL_SWEEP
    （`_U_GT_U_NUM_ABSENT_ONLY_FAMILIES`）は `"ABSENT:<reason>"` 文字列
    （存在する宣言）のみを許可する（`c0_freeze._U_ABSENT_REASON` と同じ
    2 family——物理 gate 入力を持たない）。

    **version-aware**（R20-3、Codex 第 20 巡 finding (3)）: `u_gt_bound`/
    `u_num_bound` は `FIXTURE_SPEC_REQUIRED_KEYS` に含まれない任意キーで
    あり続ける（v1.0 §V3.3 実装以前に構築された legacy manifest fixture・
    campaign を壊さないため）。判別は `frozen_design.design_revision`
    （`_is_v1_1_manifest()` — `c0_freeze._DESIGN_REVISION` と同期する
    machine-readable marker）で行う:

    - marker が `"1.1"` を宣言する manifest（v1.1 完全 manifest）では、
      `u_gt_bound`/`u_num_bound`/両 `*_formula` の**キー自体の欠落も**
      fail-closed の violation にする（本 finding: 両フィールドを削っても
      検証をすり抜け、C4 で全 real gate が NOT_EVALUABLE/INPUT_MISSING に
      なっていた穴を塞ぐ）。
    - marker が無い manifest（legacy v1.0 形式。既存 closed campaign 3 件を
      含む）はキー自体の欠落を引き続き fail-closed にしない——**キーが
      存在するのに値が欠陥**（型不正・負・非有限・formula 欠落）である
      場合のみ violation を積む（従来どおり）。

    **R21 追補**（Codex 第 21 巡採用、2026-09-05）: v1.1 manifest では
    `u_gt_bound_unit`/`u_num_bound_unit`（`campaign/holdout_stage.
    units_commensurate_for_family()` が §10.4 条件 (c) の可換性判定に
    直接消費する sibling キー）も検査対象にする——非 ABSENT family は
    `fixtures.axes.TRUTH_UNIT_BY_FAMILY`（producer 側 `c0_freeze.py` と
    同一の機械導出源）との厳密一致を、ABSENT-only family は `"n/a"` 固定を
    要求する。欠落・改変を素通しすると、候補宣言 unit と偶然一致する
    forged unit が条件 (c) を成立させ偽の `CALIBRATED_DIRECTIONAL` を
    許してしまう。legacy manifest（marker 無し）はこの検査の対象外。
    """
    is_v1_1 = _is_v1_1_manifest(manifest)
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        found, entry = _resolve(manifest, f"frozen_design.fixture_spec.{fam}")
        if not found or not isinstance(entry, Mapping):
            continue
        for base_key in ("u_gt_bound", "u_num_bound"):
            formula_key = f"{base_key}_formula"
            unit_key = f"{base_key}_unit"
            if base_key not in entry:
                if not is_v1_1:
                    continue  # legacy manifest predating v1.1 §V3.3 -- not blocked here.
                for missing_key in (base_key, formula_key, unit_key):
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=missing_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{missing_key} is required "
                                "for v1.1 manifests (frozen_design.design_revision="
                                f"{_V1_1_DESIGN_REVISION!r}) but is missing (v1.1 §V3.3; R20-3)"
                            ),
                        )
                    )
                continue
            value = entry.get(base_key)
            formula = entry.get(formula_key)
            if fam in _U_GT_U_NUM_ABSENT_ONLY_FAMILIES:
                if not _is_absent_marker(value):
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=base_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{base_key} must be an "
                                f"{_ABSENT_PREFIX!r}-prefixed marker for this family "
                                "(v1.1 §V3.3), got " + repr(value)
                            ),
                        )
                    )
                    continue
            else:
                value_ok = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and float(value) >= 0.0
                )
                if not value_ok:
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=base_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{base_key} must be a "
                                f"non-negative finite number, got {value!r} (v1.1 §V3.3)"
                            ),
                        )
                    )
            if not isinstance(formula, str) or formula.strip() == "":
                violations.append(
                    SweepManifestViolationDetail(
                        violation="u_bound_missing_or_invalid",
                        family=fam,
                        sweep_id=formula_key,
                        expected_count=0,
                        actual_count=0,
                        detail=(
                            f"frozen_design.fixture_spec.{fam}.{formula_key} must be a "
                            "non-empty derivation-formula string (v1.1 §V3.3)"
                        ),
                    )
                )
            # R21 対応（Codex 第 21 巡採用、2026-09-05）: `campaign/holdout_stage.
            # units_commensurate_for_family()` が §10.4 条件 (c) の可換性判定に
            # 直接消費する `u_gt_bound_unit`/`u_num_bound_unit` を、v1.1 manifest
            # に限り `fixtures.axes.TRUTH_UNIT_BY_FAMILY`（producer 側と同一の
            # 機械導出源）に照合する。欠落・改変（例: 別 family/候補の unit へ
            # すり替え）を検出せずに通すと、正規化後に候補宣言 unit と偶然
            # 一致する自己整合な forged manifest が条件 (c) を成立させ、偽の
            # CALIBRATED_DIRECTIONAL を許してしまう（本 finding）。ABSENT-only
            # family（RESONANCE_GT/IDENTITY_CAUSAL_SWEEP）は `"n/a"` 固定を
            # 期待する（gate4' 対象外であり、`units_commensurate_for_family()`
            # 自体もこの sentinel を「非 string 相当」として保守側 False へ
            # 落とす契約——`campaign/holdout_stage.py` は本 PR の対象外のため
            # 無改変）。
            if is_v1_1:
                expected_unit = (
                    "n/a"
                    if fam in _U_GT_U_NUM_ABSENT_ONLY_FAMILIES
                    else fixture_axes.TRUTH_UNIT_BY_FAMILY.get(fam)
                )
                unit_value = entry.get(unit_key)
                unit_ok = (
                    unit_key in entry
                    and isinstance(unit_value, str)
                    and expected_unit is not None
                    and unit_value.strip() == expected_unit
                )
                if not unit_ok:
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=unit_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{unit_key} must equal "
                                f"{expected_unit!r} (v1.1 §V3.3 truth-unit machine "
                                "derivation, fixtures.axes.TRUTH_UNIT_BY_FAMILY; R21), got "
                                f"{unit_value!r}"
                            ),
                        )
                    )
        # R22-2 対応（Codex 第 22 巡 finding (2)、2026-09-05）: 値の形状検査
        # （有限非負・formula 非空）だけでは、独立生成の v1.1 manifest が
        # bound を 0 にして formula 文字列だけ残しても通過してしまう（過小
        # bound を C4 が消費して偽の CALIBRATED_DIRECTIONAL を出す）。非
        # ABSENT family に限り、producer (`c0_freeze._fixture_specs()`) と
        # 同一の canonical 関数 (`fixtures.uncertainty.derive_u_gt_bound()`/
        # `derive_u_num_bound()`) を manifest 自身に記録された入力
        # (`u_bound_inputs`) から**再実行**し、宣言済みの value/formula と
        # 一致することを要求する（manifest 自己完結の原則: `fixtures.axes`
        # の現在値は一切読まない——`fixtures/uncertainty.py` モジュール
        # docstring 参照）。legacy manifest（marker 無し）はこの検査の対象外。
        if is_v1_1 and fam not in _U_GT_U_NUM_ABSENT_ONLY_FAMILIES:
            inputs_key = "u_bound_inputs"
            if inputs_key not in entry or _is_hollow(entry.get(inputs_key)):
                violations.append(
                    SweepManifestViolationDetail(
                        violation="u_bound_missing_or_invalid",
                        family=fam,
                        sweep_id=inputs_key,
                        expected_count=0,
                        actual_count=0,
                        detail=(
                            f"frozen_design.fixture_spec.{fam}.{inputs_key} is required "
                            "for v1.1 manifests (frozen_design.design_revision="
                            f"{_V1_1_DESIGN_REVISION!r}) but is missing (v1.1 §V3.3; R22-2)"
                        ),
                    )
                )
            else:
                raw_inputs = entry.get(inputs_key)

                # R24-1 対応（Codex 第 24 巡 P1 採用, 2026-09-05）:
                # 上のブロックの再導出は manifest 自身が記録した
                # `raw_inputs` から value/formula を再計算するだけで、
                # `raw_inputs` そのものが弱められていないかは一切検証して
                # いなかった——`u_bound_inputs.truth_scale_max`/
                # `.float64_eps` を 0 にし、対応する `u_gt_bound`/
                # `u_num_bound`/両 formula もその偽入力から再計算した値に
                # 揃えた「入力ごと自己整合な」manifest は、この再導出照合
                # （入力→出力の内部整合性しか見ない）を素通りしてしまう。
                # `fixtures/uncertainty.py` モジュール docstring は
                # 「validator は `gather_u_bound_inputs()` を呼び直しては
                # ならない」という自己完結原則を掲げるが、これは
                # `candidates.*_paths_sha256`（path inventory の content-
                # hash 照合、`_check_hash_content_match()`）が REQUIRED_
                # BLOCKING で通っている前提の下では、検証対象 checkout の
                # `fixtures/axes.py`/`fixtures/uncertainty.py` の内容が
                # 凍結時点と一致することは既に別途保証されている——
                # つまり「将来 axes.py が変わっても過去の manifest は
                # 揺れ動かない」という自己完結原則が守ろうとした性質は、
                # そもそも hash 照合が通っている間は不変であり、
                # `raw_inputs` そのものの真正性を確認しない限り、その
                # 自己完結性は偽入力による自己整合な改竄を防げない。
                # よってこの一点に限り、producer と同一の canonical 関数
                # `fixtures.uncertainty.gather_u_bound_inputs(family)` を
                # validator 側でも live に再実行し、manifest 宣言済み
                # `u_bound_inputs` と完全一致することを追加で要求する
                # （数値は `_numbers_close` で許容誤差付き比較、それ以外は
                # 厳密一致）。
                canonical_inputs = fixture_uncertainty.gather_u_bound_inputs(family)
                inputs_match = (
                    isinstance(raw_inputs, Mapping)
                    and set(raw_inputs) == set(canonical_inputs)
                    and all(
                        _numbers_close(raw_inputs.get(k), v)
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                        else raw_inputs.get(k) == v
                        for k, v in canonical_inputs.items()
                    )
                )
                if not inputs_match:
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=inputs_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{inputs_key} does not "
                                "match the canonical live re-derivation "
                                "(fixtures.uncertainty.gather_u_bound_inputs(); v1.1 "
                                f"§V3.3; R24-1): declared={raw_inputs!r}, "
                                f"canonical={canonical_inputs!r}"
                            ),
                        )
                    )
                try:
                    derived_gt_value, derived_gt_formula = fixture_uncertainty.derive_u_gt_bound(
                        family, raw_inputs
                    )
                    derived_num_value, derived_num_formula = (
                        fixture_uncertainty.derive_u_num_bound(family, raw_inputs)
                    )
                except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                    violations.append(
                        SweepManifestViolationDetail(
                            violation="u_bound_missing_or_invalid",
                            family=fam,
                            sweep_id=inputs_key,
                            expected_count=0,
                            actual_count=0,
                            detail=(
                                f"frozen_design.fixture_spec.{fam}.{inputs_key} could not be "
                                "used to canonically recompute u_gt_bound/u_num_bound "
                                f"(v1.1 §V3.3; R22-2): {exc!r}"
                            ),
                        )
                    )
                else:
                    for value_key, formula_key2, derived_value, derived_formula in (
                        ("u_gt_bound", "u_gt_bound_formula", derived_gt_value, derived_gt_formula),
                        (
                            "u_num_bound",
                            "u_num_bound_formula",
                            derived_num_value,
                            derived_num_formula,
                        ),
                    ):
                        declared_value = entry.get(value_key)
                        if not _numbers_close(declared_value, derived_value):
                            violations.append(
                                SweepManifestViolationDetail(
                                    violation="u_bound_missing_or_invalid",
                                    family=fam,
                                    sweep_id=value_key,
                                    expected_count=0,
                                    actual_count=0,
                                    detail=(
                                        f"frozen_design.fixture_spec.{fam}.{value_key} "
                                        f"declares {declared_value!r} but the canonical "
                                        f"re-derivation from {inputs_key} yields "
                                        f"{derived_value!r} (v1.1 §V3.3; R22-2)"
                                    ),
                                )
                            )
                        declared_formula = entry.get(formula_key2)
                        if declared_formula != derived_formula:
                            violations.append(
                                SweepManifestViolationDetail(
                                    violation="u_bound_missing_or_invalid",
                                    family=fam,
                                    sweep_id=formula_key2,
                                    expected_count=0,
                                    actual_count=0,
                                    detail=(
                                        f"frozen_design.fixture_spec.{fam}.{formula_key2} does "
                                        f"not match the canonical re-derivation from "
                                        f"{inputs_key} (v1.1 §V3.3; R22-2)"
                                    ),
                                )
                            )
    return tuple(violations)


def _check_holdout_pin_feasibility(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V2.2 の k_hold 被覆要件検査: cap `floor((N_hold-1)/r)` 内で
    `max_field_cardinality` 個の pin を確保できるか。
    `_check_declared_sweep_truth_levels()` と同じ「manifest 非依存に凍結
    matrix 自体の構造を検査する」規約（`manifest` は呼び出し規約を揃える
    ためだけの未使用引数）。456 セル canonical matrix では発生しない。
    """
    del manifest
    rows = build_matrix()
    params = holdout_pin_params_by_family(rows)
    violations: list[SweepManifestViolationDetail] = []
    for fam in sorted(params):
        p = params[fam]
        if p.feasible:
            continue
        violations.append(
            SweepManifestViolationDetail(
                violation="holdout_pin_infeasible",
                family=fam,
                sweep_id="",
                expected_count=p.max_field_cardinality,
                actual_count=p.cap,
                detail=(
                    f"family {fam!r}: holdout pin coverage requires "
                    f"max_field_cardinality={p.max_field_cardinality} pinned sweep(s) but "
                    f"cap floor((N_hold-1)/r)={p.cap} (N_hold={p.n_hold}, "
                    f"r={p.member_rows_per_sweep})"
                ),
            )
        )
    return tuple(violations)


def _check_holdout_sweeps_declaration_match(
    manifest: Mapping[str, object], split_secret: bytes | None = None
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V2.2/§V2.3 holdout sweep pin の宣言一致検査（D77 同型）。

    `holdout_sweeps`（manifest トップレベルの non-core キー、
    `c0_freeze._attach_freeze_extras()` 参照）は `split_secret` 依存
    （`fixtures.matrix.pin_holdout_sweeps_by_family()` が
    HMAC(split_secret, ...) で選抜する）ため、secret 非依存の
    `declared_sweeps` とは異なり、本関数は `split_secret` が渡された場合に
    限り完全再導出照合を行う。

    R10 対応（Codex 第 10 巡 P1 採用、2026-09-05）: 修正前は、宣言された
    pin 数が `degradation_floor <= 宣言数 < k_hold` の範囲に収まってさえ
    いれば、その**宣言値をそのまま** `k_hold_overrides` として段 1
    （`pin_holdout_sweeps_by_family()`）だけを再導出照合していた——「公称
    `k_hold` で段 2 (`realize_split()`) が本当に `CoverageRepairInfeasible`
    に落ちたか」という縮退の必要性そのものは一切検証しておらず、独立に
    構築した/改竄した manifest が縮退を自称するだけで宣言 pin 数を水減らし
    できる穴があった（宣言を信頼する側と実測する側の入力が同じ変数に
    癒着していたのが根本原因）。

    本対応は、`split_secret` が渡された場合に限り、**宣言値を一切入力に
    せず**公称 `k_hold` から始まる `splitter.pin_and_realize_holdout()`
    （段 1 `pin_holdout_sweeps_by_family()` → 段 2 `splitter.
    realize_split()` の縮退リトライループ本体。`c0_freeze.armed_freeze()`
    が実際の freeze 時に呼ぶのと**同一の関数**——検証と生成が二重実装に
    分岐しないよう `splitter.py` へ共有化した。`c0_freeze` は
    `c0_validate` を import するため逆方向の import ができず、両者が依存
    できる `splitter` へ実装を寄せた）を検証側で完全に再実行し、その結果
    (`full_pin`) を「真の」holdout sweep 集合として宣言と突き合わせる。
    段 2 の修復不能を経て初めて到達する縮退後の k は再実行結果に
    **そのまま**表れるため、宣言が正当な縮退の帰結であれば厳密一致し、
    水増し/水減らしされた偽の宣言は（同じ secret・matrix の下で本当に
    その縮退が起きない限り）per-sweep_id 照合で確実に不一致として検出
    される（fail-closed）。

    再実行自体が `HoldoutPinInfeasible`/`HoldoutPinDegradationExhausted`
    を送出した場合（構造的欠陥、または matrix 変更などにより freeze 時と
    再現できない異常事態）は、再導出結果を一切信頼できないため、宣言の
    ある家族すべてを無条件で mismatch 扱いにする（fail-closed。「検証
    不能」を「検証成功」として通さない）。

    `split_secret=None`（`dry_run()` — secret がまだ存在しない、または
    `holdout_sweeps` を持たない v1.1 以前の manifest を読む場合）では、
    secret 依存の段 2 再実行は原理的に行えないため、secret 非依存の構造
    検査のみ行う: 宣言 pin 数が `holdout_pin_params_by_family()` の
    **nominal** `k_hold` と一致し、各宣言 sweep_id が
    `declared_sweeps_by_family()` に実在しその member row_id が完全一致する
    こと。**この経路は宣言された pin 数を override として一切信頼しない**
    ——nominal `k_hold` との単純一致のみを見るため、縮退を自称する宣言は
    secret 無しでは「その縮退が正当だったか」を検査できず、単に
    `len(actual) != k_hold` として構造 mismatch になる（secret 依存の
    完全再導出照合でのみ縮退の正当性まで確認できる）。

    R11 対応（Codex 第 11 巡採用、2026-09-05）: `found_holdout`（v1.1+
    manifest の version marker）かつ `split_secret is not None`（secret
    依存の完全再導出経路）の場合に限り、pin 免除でない
    （`HoldoutPinParams.pin_exempt=False`、すなわち `k_hold>=1`）全 family
    について宣言 (`holdout_sweeps.<family>`) の存在と非空を必須にする——
    欠落/空はここで即 `continue` して以降の照合をすり抜けさせず、
    fail-closed の mismatch violation にする。pin 免除 family（`cap<1`）は
    空宣言 `{}` が正しい姿であり、非空の宣言（免除のはずの family が pin
    を騙る改竄）も同様に検出する。

    R23 対応（Codex 第 23 巡 P2 採用、2026-09-05、PRRT_kwDOSD2OOM6fgdGg）:
    R11 の必須化は `found_holdout=True` を前提としていたが、top-level
    `holdout_sweeps` キー自体を manifest から削除すれば `found_holdout=False`
    になり、R11 の必須化もそれ以降の per-family 照合も丸ごと沈黙していた
    （`_check_holdout_sweeps_realized_membership()` も同型で沈黙）。本関数は
    v1.1 manifest（`_is_v1_1_manifest()`）かつ **`realized_split` も存在する
    full/armed-shape manifest**（`c0_freeze._attach_freeze_extras()` が
    `realized_split`/`holdout_sweeps` を同一呼び出しで同時に付与するため、
    両者の有無は常に揃うはずという不変を利用する）に限り、top-level
    `holdout_sweeps` キー自体の存在を必須化する（欠落は関数冒頭で単独の
    violation を返して即 return し、以降の per-family 照合は実行しない）。
    `realized_split` を見ずに `is_v1_1` のみで必須化すると、
    `c0_freeze.dry_run()`（`build_manifest()` が返す secret 未生成の
    core-only manifest。`holdout_sweeps` が存在しないのが正当な設計不変
    ——`_CORE_ONLY_EXCLUDED_KEYS` docstring 参照）まで誤ってブロックして
    しまう（本 fix 実装時に発見: dry-run manifest は `realized_split` も
    同時に持たないため、この判別で正しく除外できる）。legacy (marker 無し)
    manifest は本チェックの対象外のまま従来の nested fallback 経路を維持
    する（後方互換）。
    """
    found_holdout, holdout_section = _resolve(manifest, "holdout_sweeps")
    is_v1_1 = _is_v1_1_manifest(manifest)
    found_realized_split, _realized_split_section = _resolve(manifest, "realized_split")

    # R23 対応（Codex 第 23 巡 P2 採用, 2026-09-05, PRRT_kwDOSD2OOM6fgdGg）:
    # top-level `holdout_sweeps` キー自体を manifest から削除すると
    # `found_holdout=False` になり、以下の per-family 照合ループは
    # `declared_raw_by_family` を全て `None`（nested fallback も未収載なら
    # 空）に落として `hollow` 判定で即 `continue` する——本関数もその下流の
    # `_check_holdout_sweeps_realized_membership()` も沈黙し、C4 側
    # (`campaign/cli.py::_run_c4`) の `expected_sweep_ids` フォールバックが
    # 全宣言 sweep（HOLDOUT 非常駐 sweep を含む）を使って偽の
    # `DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT` terminal を生み得た。
    # `frozen_design.design_revision` marker（`_is_v1_1_manifest()`）が
    # `"1.1"` を宣言し、かつ `realized_split`（`holdout_sweeps` と常に同時に
    # 付与される sibling 非-core キー）が存在する full/armed-shape manifest
    # に限り、top-level `holdout_sweeps` キー自体の存在を必須化する——
    # 欠落は他の宣言内容と無関係に単独の `holdout_pin_declaration_mismatch`
    # violation として即 fail-closed し、以降の per-family 照合（意味を
    # 持たないため）は実行しない。`realized_split` も無い manifest（v1.1
    # `dry_run()` の core-only manifest。secret 未生成で `holdout_sweeps`
    # 欠落が設計上正当）は本チェックの対象外のまま従来の nested fallback
    # 経路を維持する（後方互換・偽陽性防止）。
    if is_v1_1 and found_realized_split and not found_holdout:
        return (
            SweepManifestViolationDetail(
                violation="holdout_pin_declaration_mismatch",
                family="",
                sweep_id="",
                expected_count=0,
                actual_count=0,
                detail=(
                    "top-level holdout_sweeps section is required for v1.1 "
                    f"manifests (frozen_design.design_revision={_V1_1_DESIGN_REVISION!r}) "
                    "but is missing — without it, the per-family re-derivation "
                    "match and realized-split membership checks silently no-op "
                    "(found_holdout=False), and C4's expected_sweep_ids capacity "
                    "check falls back to the full declared-sweep set (including "
                    "sweeps never resident on HOLDOUT), which can manufacture a "
                    "false DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT terminal "
                    "(Codex round 23 finding, ADOPT; fail-closed, §V2.2)"
                ),
            ),
        )

    rows = build_matrix()
    declared = declared_sweeps_by_family(rows)
    params = holdout_pin_params_by_family(rows)

    declared_raw_by_family: dict[str, object] = {}
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        declared_raw: object = None
        if found_holdout and isinstance(holdout_section, Mapping):
            declared_raw = holdout_section.get(fam)
        else:
            found, entry = _resolve(manifest, f"frozen_design.fixture_spec.{fam}")
            if found and isinstance(entry, Mapping):
                declared_raw = entry.get("holdout_sweeps")
        declared_raw_by_family[fam] = declared_raw

    # secret 依存経路: 宣言値は一切参照せず、公称 k_hold から始まる正規の
    # 縮退ループを検証側で完全再実行する（`armed_freeze()` と同一関数）。
    full_pin: dict[str, dict[str, tuple[str, ...]]] | None = None
    canonical_rederivation_error: Exception | None = None
    if split_secret is not None:
        row_inputs = row_inputs_for_split(rows, STRATUM_FACTOR_NAMES)
        try:
            full_pin, _realized = pin_and_realize_holdout(
                rows, row_inputs, split_secret, STRATUM_FACTOR_NAMES
            )
        except (HoldoutPinInfeasible, HoldoutPinDegradationExhausted) as exc:
            canonical_rederivation_error = exc

    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        declared_raw = declared_raw_by_family[fam]
        hollow = (
            declared_raw is None or _is_hollow(declared_raw) or not isinstance(declared_raw, Mapping)
        )

        # R11 対応（Codex 第 11 巡採用、2026-09-05）: 修正前は
        # `declared_raw` が欠落/空（`{}`）だとここで即 `continue` し、以降の
        # 公称 k_hold 再導出比較にすら到達しなかった——非免除
        # （`pin_exempt=False`、すなわち `k_hold>=1`）family の pin 宣言を
        # manifest から丸ごと消しても、この検査が沈黙して secret 依存 C0
        # 検証を通過してしまう穴があった（membership 検査
        # `_check_holdout_sweeps_realized_membership()` も同じ hollow 判定で
        # skip するため二重に見逃す）。本対応は、`holdout_sweeps` トップ
        # レベルキー自体が manifest に存在する（`found_holdout` — v1.1+
        # 形式である version marker）かつ `split_secret` が渡る secret 依存
        # 完全再導出経路に限り、pin 免除でない全 family について宣言の
        # 存在と非空を必須にする：欠落/空は fail-closed。逆に pin 免除
        # family（`cap<1`）は空宣言 `{}` が正しい姿であり、非空の宣言は
        # （免除のはずの family が pin を騙る改竄）fail-closed で検出する。
        # `holdout_sweeps` キー自体が無い v1.0 形式 manifest（`found_holdout`
        # =False）は、この必須化の対象外のまま従来どおり「宣言があれば照合」
        # を維持する（後方互換）。
        #
        # R23 追補（Codex 第 23 巡 P2 採用、2026-09-05）: `split_secret is
        # None` の dry-run 経路も、`is_v1_1` の場合はこの必須化の対象に含める
        # ——「宣言の存在と構造」までは secret 無しでも検証できる（下の
        # `legitimately_empty` 判定は `full_pin`（secret 依存の再導出）が
        # 無ければ `rederivation_indicates_empty` が常に False になり、
        # `p.pin_exempt` のみで legitimacy を判定する——これは意図的な
        # fail-closed: 「本当に正当な縮退か」を secret 無しで確認できない
        # 以上、非免除 family の空宣言は疑わしいものとして扱う）。v1.0
        # legacy manifest（`is_v1_1=False`）は `split_secret is None` の場合
        # 引き続き対象外（後方互換）。
        if found_holdout and (split_secret is not None or is_v1_1) and fam in params:
            p = params[fam]
            # v1.1 §V3.5 実装時発見（2026-09-05）: `p.pin_exempt` は matrix
            # 構造のみで決まる静的概念（`cap<1`）であり、段 2 coverage repair
            # が secret 依存で `degradation_floor`（claim 非被覆 family では
            # 0）まで完全縮退した「実行時の」ゼロ pin（`p.pin_exempt=False`
            # のまま起こりうる——nuisance_axis coverage 制約導入後、
            # `TILT_GT` で実際に観測される）とは別軸である。空宣言の正当性は
            # 「再導出結果そのものが空か」（`full_pin` — 既に再実行済みの
            # 正規縮退ループの出力、静的 `pin_exempt` を包含する）で判定
            # しなければ、正当な完全縮退を fail-closed で誤検出する
            # （逆に、宣言と再導出のどちらも空でない/どちらも空、の不一致は
            # 後続の per-sweep_id 完全一致検査が別途捕捉するため、ここでの
            # 判定を緩めても改竄検出力は落ちない）。
            rederivation_indicates_empty = (
                canonical_rederivation_error is None
                and full_pin is not None
                and not full_pin.get(fam)
            )
            legitimately_empty = p.pin_exempt or rederivation_indicates_empty
            if not legitimately_empty and hollow:
                violations.append(
                    SweepManifestViolationDetail(
                        violation="holdout_pin_declaration_mismatch",
                        family=fam,
                        sweep_id="",
                        expected_count=p.k_hold,
                        actual_count=0,
                        detail=(
                            f"holdout_sweeps.{fam} declaration is missing or empty, but "
                            f"family is not pin-exempt (k_hold={p.k_hold} >= 1) and the "
                            "split_secret re-derivation does not itself produce an empty "
                            "pin set — a non-exempt family's holdout pin declaration must "
                            "be present and non-empty unless legitimately degraded to zero "
                            "(fail-closed, §V2.2)"
                        ),
                    )
                )
                continue
            if p.pin_exempt and not hollow:
                violations.append(
                    SweepManifestViolationDetail(
                        violation="holdout_pin_declaration_mismatch",
                        family=fam,
                        sweep_id="",
                        expected_count=0,
                        actual_count=(
                            len(declared_raw) if isinstance(declared_raw, Mapping) else 0
                        ),
                        detail=(
                            f"holdout_sweeps.{fam} declares pinned sweep(s) but the family "
                            "is pin-exempt (cap<1) — an exempt family must declare an empty "
                            "mapping (fail-closed, §V2.2)"
                        ),
                    )
                )
                continue

        if hollow:
            continue

        actual = _normalize_declared_sweeps(declared_raw)
        if actual is None:
            violations.append(
                SweepManifestViolationDetail(
                    violation="holdout_pin_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=0,
                    actual_count=len(declared_raw),
                    detail=f"holdout_sweeps.{fam} (inner shape malformed)",
                )
            )
            continue

        if canonical_rederivation_error is not None:
            # 公称 k_hold からの正規縮退ループそのものが再実行できない
            # （fail-closed）——宣言のどんな内容とも突き合わせようがない。
            violations.append(
                SweepManifestViolationDetail(
                    violation="holdout_pin_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=0,
                    actual_count=len(actual),
                    detail=(
                        f"holdout_sweeps.{fam}: canonical pin-and-realize degradation "
                        f"retry from nominal k_hold could not be re-executed "
                        f"({canonical_rederivation_error}) — declaration cannot be "
                        "trusted, refusing (fail-closed)"
                    ),
                )
            )
            continue

        expected = full_pin.get(fam, {}) if full_pin is not None else None
        family_declared = declared.get(fam, {})
        k_hold = params[fam].k_hold if fam in params else None

        # secret 非依存経路（`expected is None`）: nominal k_hold との単純
        # 一致のみを見る——宣言された pin 数を override として信頼しない
        # （縮退の正当性は段 2 の再実行なしには判定できないため、secret が
        # なければ「縮退している」という宣言そのものを検査対象にしない）。
        if expected is None and k_hold is not None and len(actual) != k_hold:
            violations.append(
                SweepManifestViolationDetail(
                    violation="holdout_pin_declaration_mismatch",
                    family=fam,
                    sweep_id="",
                    expected_count=k_hold,
                    actual_count=len(actual),
                    detail=(
                        f"holdout_sweeps.{fam} declares {len(actual)} pinned sweep(s), "
                        f"expected k_hold={k_hold}"
                    ),
                )
            )

        candidate_sweep_ids = set(actual) | (set(expected) if expected is not None else set())
        for sweep_id in sorted(candidate_sweep_ids):
            actual_members = actual.get(sweep_id)
            if expected is not None:
                expected_members = expected.get(sweep_id)
                mismatch = actual_members != expected_members
                detail_suffix = "does not match the split_secret re-derivation"
            else:
                if actual_members is None:
                    continue
                expected_members = family_declared.get(sweep_id)
                mismatch = expected_members is None or actual_members != expected_members
                detail_suffix = (
                    "is not a declared sweep of the frozen matrix (or its member row_id "
                    "set does not match declared_sweeps)"
                )
            if not mismatch:
                continue
            violations.append(
                SweepManifestViolationDetail(
                    violation="holdout_pin_declaration_mismatch",
                    family=fam,
                    sweep_id=sweep_id,
                    expected_count=len(expected_members or ()),
                    actual_count=len(actual_members or ()),
                    detail=f"holdout_sweeps.{fam}[{sweep_id!r}] {detail_suffix}",
                )
            )
    return tuple(violations)


def _check_holdout_sweeps_realized_membership(
    manifest: Mapping[str, object],
) -> tuple[SweepManifestViolationDetail, ...]:
    """v1.1 §V2.3: 実現済み split (`realized_split.assignment`) 上で
    `holdout_sweeps` の member 行が 1 行でも HOLDOUT 以外に割当てられて
    いれば fail-closed する（割当実装の欠陥）。`realized_split`/
    `holdout_sweeps` が共に存在する full manifest（armed freeze 後）でのみ
    意味を持つ——いずれかが欠落する manifest（dry-run 等）は対象外。
    """
    found_split, split_section = _resolve(manifest, "realized_split.assignment")
    found_holdout, holdout_section = _resolve(manifest, "holdout_sweeps")
    if not found_split or not isinstance(split_section, Mapping):
        return ()
    if not found_holdout or not isinstance(holdout_section, Mapping):
        return ()
    violations: list[SweepManifestViolationDetail] = []
    for family in fixture_axes.FixtureFamily:
        fam = family.value
        declared_raw = holdout_section.get(fam)
        if declared_raw is None or _is_hollow(declared_raw) or not isinstance(declared_raw, Mapping):
            continue
        normalized = _normalize_declared_sweeps(declared_raw)
        if normalized is None:
            continue
        for sweep_id, member_ids in normalized.items():
            offending = tuple(
                rid for rid in member_ids if split_section.get(rid) != vocab.Split.HOLDOUT.value
            )
            if offending:
                violations.append(
                    SweepManifestViolationDetail(
                        violation="holdout_pin_not_in_holdout_split",
                        family=fam,
                        sweep_id=sweep_id,
                        expected_count=len(member_ids),
                        actual_count=len(member_ids) - len(offending),
                        detail=(
                            f"holdout_sweeps.{fam}[{sweep_id!r}] has member row_id(s) not "
                            f"assigned to HOLDOUT in realized_split.assignment: "
                            f"{list(offending)!r}"
                        ),
                    )
                )
    return tuple(violations)


def _legacy_v1_0_opt_in_verified(manifest_path: Path | str | None) -> bool:
    """R22-1 対応（Codex 第 22 巡 finding (1)）: `allow_legacy_v1_0=True` を
    「campaign directory 上に既に存在する manifest ファイルであり、かつその
    campaign の ledger が closed（chain 検証が通り、末尾 event の
    `payload.kind == "campaign_closed"`）または aborted（`archive_
    aborted_ledger.ensure_archived()` が公開した `ledger.jsonl.gz` +
    sidecar のペアが実際に検証を通る）である」場合に限って有効化する。

    `manifest_path=None`（in-memory で組み立てた未書込 manifest——
    `c0_freeze.dry_run()`/`armed_freeze()` が呼ぶ経路）では常に `False` を
    返す。これにより、新規 freeze 経路は `allow_legacy_v1_0=True` を渡しても
    legacy 扱いにならず（そもそも渡していない——両呼び出しとも本引数を渡さない
    デフォルト False のまま）、opt-in は「既に確定した過去の campaign を
    後から検証し直す」用途のみに限定される。

    R24-2 対応（Codex 第 24 巡 P2 採用、2026-09-05、PRRT_kwDOSD2OOM6fgdGg）:
    修正前は (a) `ledger.jsonl.gz` という名前の**通常ファイルが存在するだけ**
    で aborted 扱いにしており、sidecar sha256 との一致・実伸長・chain 検証の
    いずれも行っていなかった（空ファイル/fabricated gz でも opt-in が
    通ってしまう）、(b) closed 判定も生の `ledger.jsonl` を行単位で JSON
    スキャンするだけで、chain 検証済みの正典であることも「末尾が
    campaign_closed か」も確認していなかった（改竄・途中の孤立した
    `campaign_closed` 行でも通ってしまう）。修正は両方とも
    `tools.archive_aborted_ledger`/`provenance.Ledger` が既に持つ検証実装を
    共有する: aborted は `archive_aborted_ledger._verify_gz_sidecar_pair()`
    （sidecar 形式・gz 実伸長・sidecar sha256 一致・伸長結果の chain 検証の
    4 点、`ensure_archived()` 自身が公開前に使うのと同一関数）、closed は
    `provenance.Ledger.load_with_verification()` の chain 検証 (`chain.ok`)
    に加え、entries の**末尾**（`entries[-1]`）が `campaign_closed` である
    ことを要求する（生スキャンでは「どこかに 1 行あれば真」だったのを、
    正規の閉鎖手順が必ず末尾に置く event の位置まで絞り込む）。
    """
    if manifest_path is None:
        return False
    path = Path(manifest_path)
    if not path.is_file():
        return False
    campaign_dir = path.parent

    gz_path = campaign_dir / archive_aborted_ledger.GZ_FILENAME
    sidecar_path = campaign_dir / archive_aborted_ledger.SIDECAR_FILENAME
    if gz_path.is_file():
        try:
            archive_aborted_ledger._verify_gz_sidecar_pair(gz_path, sidecar_path)
        except archive_aborted_ledger.ArchiveError:
            return False
        # sidecar sha256 一致・gz 実伸長・伸長結果の chain 検証まで通った
        # ——`ensure_archived()` が公開前に要求するのと同じ 4 点がすべて
        # 揃っている（D100/c0e466c の「公開は検証成功後にのみ」契約と整合）。
        return True

    ledger_path = campaign_dir / "ledger.jsonl"
    if not ledger_path.is_file():
        return False
    try:
        ledger, chain = Ledger.load_with_verification(ledger_path)
    except Exception:  # noqa: BLE001 - 検証不能も「legacy opt-in 不可」扱い
        return False
    if not chain.ok:
        return False
    entries = ledger.entries
    if not entries:
        return False
    tail_payload = entries[-1].payload
    return isinstance(tail_payload, Mapping) and tail_payload.get("kind") == "campaign_closed"


def validate_c0_manifest(
    manifest: Mapping[str, object],
    *,
    split_secret: bytes | None = None,
    allow_legacy_v1_0: bool = False,
    manifest_path: Path | str | None = None,
) -> C0ValidationResult:
    """C0 freeze manifest を dry-run 検証する（書込・secret 生成・freeze event なし）。

    `allow_legacy_v1_0`（R22-1、既定 False）: `frozen_design.design_revision`
    marker が無い/一致しない legacy (v1.0) manifest の検証を明示的に許可する
    opt-in。`_legacy_v1_0_opt_in_verified(manifest_path)` が「on-disk の
    closed/aborted campaign manifest である」ことを確認できた場合にのみ実際に
    有効化される——`manifest_path` を渡さない、または campaign が
    closed/aborted と確認できない場合は `True` を渡しても legacy 扱いに
    ならない（fail-closed）。`c0_freeze.dry_run()`/`armed_freeze()` が呼ぶ
    新規 freeze 経路はこの引数を一切渡さない（常に v1.1 必須のまま）。
    """
    legacy_design_revision_ok = allow_legacy_v1_0 and _legacy_v1_0_opt_in_verified(manifest_path)
    missing_required = _check_required_blocking(
        manifest, legacy_design_revision_ok=legacy_design_revision_ok
    )
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
    sweep_mismatch_violations = _check_declared_sweep_declaration_match(manifest)
    claim_relevant_violations = _check_claim_relevant_fields_match(manifest)
    invariance_axis_violations = _check_invariance_axes_match(manifest)
    u_gt_u_num_violations = _check_u_gt_u_num_bounds(manifest)
    holdout_pin_feasibility_violations = _check_holdout_pin_feasibility(manifest)
    holdout_pin_declaration_violations = _check_holdout_sweeps_declaration_match(
        manifest, split_secret
    )
    holdout_pin_membership_violations = _check_holdout_sweeps_realized_membership(manifest)

    # UNDERSPEC-CAL-D78 ruling（#344 round 9 ADOPT, 分類②）: sweep 関連の
    # fail-closed 事由（D76 ruling (2) の truth-level 不足 / D77 ruling (1)
    # の宣言不一致）は、凍結 `BlockedCode` を事後拡張する専用コードではなく
    # 既存の `BLOCKED_C0_MANIFEST_INCOMPLETE` で表現する（`all_missing` 経由
    # で既に発行済みなら二重追加しない）。診断詳細は
    # `sweep_declaration_violations`/`sweep_declaration_mismatch_violations`
    # の `SweepManifestViolationDetail` に残る。v1.1 §V2.2/§V2.3 の holdout
    # sweep pinning 関連 4 検査（claim-relevant field 照合・k_hold 被覆可能性・
    # holdout_sweeps 宣言一致・realized split 上の member 所属）も同じ規約で
    # 合流させる（新規 vocab code は発行しない）。
    blocked: list[vocab.BlockedCode] = []
    if (
        all_missing
        or sweep_violations
        or sweep_mismatch_violations
        or claim_relevant_violations
        or invariance_axis_violations
        or u_gt_u_num_violations
        or holdout_pin_feasibility_violations
        or holdout_pin_declaration_violations
        or holdout_pin_membership_violations
    ):
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
        sweep_declaration_violations=sweep_violations,
        sweep_declaration_mismatch_violations=sweep_mismatch_violations,
        claim_relevant_field_violations=claim_relevant_violations,
        invariance_axis_violations=invariance_axis_violations,
        u_gt_u_num_bound_violations=u_gt_u_num_violations,
        holdout_pin_feasibility_violations=holdout_pin_feasibility_violations,
        holdout_pin_declaration_violations=holdout_pin_declaration_violations,
        holdout_pin_membership_violations=holdout_pin_membership_violations,
    )


# ---------------------------------------------------------------------------
# CLI（R22-1 対応、Codex 第 22 巡 finding (1)）: 既存 campaign の
# `c0_manifest.json` を独立に dry-run 検証する薄いエントリポイント。
# `c0_freeze.py` の `dry_run()`/`armed_freeze()` は常に in-memory で新規
# manifest を組み立てて検証する（`--allow-legacy-v1-0` を持たない・常に
# v1.1 必須）ため、on-disk の既存 manifest（典型的には closed/aborted
# campaign）だけを検証したい場合の別入口として本 CLI を設ける。
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voice_genesis.calibration.c0_validate",
        description=(
            "既存の c0_manifest.json を dry-run 検証する（書込・secret 生成なし）。"
        ),
    )
    parser.add_argument("manifest_path", type=Path, help="検証対象の c0_manifest.json への path")
    parser.add_argument(
        "--allow-legacy-v1-0",
        action="store_true",
        default=False,
        help=(
            "frozen_design.design_revision marker が無い/一致しない legacy (v1.0) "
            "manifest の検証を許可する（R22-1 opt-in）。実際に有効化されるのは "
            "manifest_path の親 campaign directory の ledger が closed "
            "(payload.kind=='campaign_closed') または aborted "
            "(ledger.jsonl.gz が archive 済み) と確認できた場合のみ——それ以外は "
            "このフラグを渡しても fail-closed のまま。"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    manifest_path: Path = args.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = validate_c0_manifest(
        manifest,
        allow_legacy_v1_0=args.allow_legacy_v1_0,
        manifest_path=manifest_path,
    )
    if result.is_blocked:
        print(f"BLOCKED: {[code.value for code in result.blocked_codes]}")
        for key in result.missing_required_keys:
            print(f"  missing_required_keys: {key}")
        for violations, label in (
            (result.u_gt_u_num_bound_violations, "u_gt_u_num_bound_violations"),
            (result.sweep_declaration_violations, "sweep_declaration_violations"),
            (result.sweep_declaration_mismatch_violations, "sweep_declaration_mismatch_violations"),
            (result.claim_relevant_field_violations, "claim_relevant_field_violations"),
            (result.invariance_axis_violations, "invariance_axis_violations"),
            (result.holdout_pin_feasibility_violations, "holdout_pin_feasibility_violations"),
            (result.holdout_pin_declaration_violations, "holdout_pin_declaration_violations"),
            (result.holdout_pin_membership_violations, "holdout_pin_membership_violations"),
        ):
            for v in violations:
                print(f"  {label}: {v.family}.{v.sweep_id}: {v.detail}")
        return 1
    print("OK: no REQUIRED_BLOCKING violations")
    if result.downgrade_annotations:
        for annotation in result.downgrade_annotations:
            print(f"  downgrade_annotation: {annotation}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
