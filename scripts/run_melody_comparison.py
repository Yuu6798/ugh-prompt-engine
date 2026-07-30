"""run_melody_comparison.py — M3d: 校正ハーネス（run/evaluate 二相）。

`svp_rpe.melody.comparison.compare_melodies`（M3c）を pair manifest に沿って走らせ
report を書き出す（run phase）。複数 report（n>=2 repeats）を突き合わせて
軌跡レベル決定論（sequence hash pin）を確認し、tuning split のみからマージン表
（positive 最小類似 − negative 最大類似）を機械導出する（evaluate phase）。

standalone script（`svprpe` サブコマンド化しない・sys.path 注入は他スクリプトと
同流儀）。`scripts/run_melody_accuracy.py` の 7000 行級 anti-tamper 要塞は複製
しない——本ハーネスが踏襲するのは以下 4 点のみ（`docs/DESIGN_M3_melody_comparator.md`
§6 / M3 実装 memo「## M3d」節）:

1. atomic write（`_atomic_write_text`）
2. レジストリ sha256 pin（`load_m3_registry` / `_load_m1_registry`）
3. route_runner 注入 seam（既定は実抽出器・注入時は `route_runner_injected: true` を
   記録し、evaluate は calibration verdict の発行を拒否する — M2 と同じ流儀）
4. protected-path（`--out` が manifest / registry / report / manifest が指す音声入力を
   上書きしない）

適用帯域: 本ハーネスの既定 route は **clear_lead 経路限定**（User 決裁 2026-07-30・
単離済み clean lead 帯）。実音声・実 crepe による slow-lane 実測は本セッションでは
実行しない——run phase の既定 route_runner は実抽出器を呼ぶが、テストは fake
route_runner を注入して run/evaluate の二相メカニズムのみを検証する。

route_runner 非注入（実測・publishable）の run は **crepe 系経路限定**（設計 §6.1
「対の両側とも実 crepe 抽出を通す」）——pyin_direct 等の非 crepe 経路を指定すると
fail-closed で拒否する。melodia 系経路は注入の有無に関わらず常に拒否する（#222
裁定前の混入禁止・設計 §8）。この規律は evaluate phase でも report 自身の
`route` フィールドに対して独立に enforce する（レビュー対応 2026-07-30 第 9
ラウンド）——run phase のチェックは生成時点の一度きりであり、report を直接
書き換えて `route` を差し替えれば run 側のガードをすり抜けられるため、
publishable report が crepe 系経路であること・melodia 系経路が injected でも
拒否されることを evaluate 側でも独立に検証する。

使い方::

    python scripts/run_melody_comparison.py --pairs pairs.yaml --out run1.json
    python scripts/run_melody_comparison.py --pairs pairs.yaml --out run2.json
    python scripts/run_melody_comparison.py --evaluate run1.json run2.json --out verdict.json

pairs manifest（YAML）の形:

    schema: m3-comparison-pairs/0.1
    pairs:
      - pair_id: p001
        kind: positive_transform   # positive_transform|negative_cross|negative_rhythm|negative_interval
        split: tuning              # tuning|holdout
        audio_a: /path/to/a.wav
        audio_b: /path/to/b.wav
        expected: same             # same|different
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

import svp_rpe.melody.alignment as _m3_alignment_module  # noqa: E402
import svp_rpe.melody.comparison as _m3_comparison_module  # noqa: E402
import svp_rpe.melody.extractors as _m3_extractors_module  # noqa: E402
import svp_rpe.melody.observability as _m3_observability_module  # noqa: E402
import svp_rpe.melody.representation as _m3_representation_module  # noqa: E402
import svp_rpe.melody.routing as _m3_routing_module  # noqa: E402
from svp_rpe.melody.comparison import compare_melodies  # noqa: E402
from svp_rpe.melody.observability import (  # noqa: E402
    MelodyObservation,
    ObservabilityThresholds,
)
from svp_rpe.melody.representation import load_m3_registry  # noqa: E402
from svp_rpe.melody.routing import select_routes  # noqa: E402
from svp_rpe.utils.hashing import sha256_of_files  # noqa: E402

M3_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3_comparison_registry.yaml"
M1_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "registry.yaml"

_EXPECTED_RUN_SCHEMA = "m3-comparison-harness/0.1"
_EXPECTED_MANIFEST_SCHEMA = "m3-comparison-pairs/0.1"
_VALID_KINDS = {
    "positive_transform",
    "negative_cross",
    "negative_rhythm",
    "negative_interval",
}
_VALID_SPLITS = {"tuning", "holdout"}
_VALID_EXPECTED = {"same", "different"}
_REQUIRED_PAIR_KEYS = {"pair_id", "kind", "split", "audio_a", "audio_b", "expected"}
_AXES: Tuple[str, ...] = ("contour", "interval", "rhythm")
# `comparison.coverage`（`AlignmentCoverage.to_dict`。`alignment.py`）が持つ 4
# フィールド。freeze proposal 発行前の完全性検証（`_validate_tuning_coverage_
# completeness`）が対象にする（レビュー対応 2026-07-30 第 15 ラウンド）。
_COVERAGE_FIELDS: Tuple[str, ...] = (
    "aligned_note_fraction_a",
    "aligned_note_fraction_b",
    "phrase_coverage_a",
    "phrase_coverage_b",
)

# route_runner: (audio_path) -> (MelodyObservation, provenance dict)。
RouteRunner = Callable[[str], Tuple[MelodyObservation, Dict[str, Any]]]


# --------------------------------------------------------------------------- #
# 小道具（atomic write / dup-key safe loader / registry hash pin と同型）
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """`text` を `path` へ atomic に書く（`run_melody_observability.py` と同型）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader（`representation.py` / M1 harness と同型）。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML mapping key {key!r}; last-wins で pre-registration block を "
                "隠す穴を弾く (fail-closed)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def _yaml_load_no_dup_keys(data: bytes, *, what: str) -> Any:
    try:
        return yaml.load(data, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"{what}: YAML parse error: {exc}") from exc


def _json_loads_no_dup_keys(data: bytes, *, what: str) -> Any:
    def _reject_dupes(pairs: "List[tuple]") -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"{what}: duplicate JSON object key {key!r}; 失敗レコードを last-wins で "
                    "隠す穴を弾く (fail-closed)"
                )
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=_reject_dupes)


def _load_m1_registry(path: Path) -> "Tuple[Dict[str, Any], str]":
    data = Path(path).read_bytes()
    mapping = _yaml_load_no_dup_keys(data, what="registry.yaml")
    return mapping, hashlib.sha256(data).hexdigest()


def _load_report(path: Path) -> Dict[str, Any]:
    data = Path(path).read_bytes()
    return _json_loads_no_dup_keys(data, what="run report")


# --------------------------------------------------------------------------- #
# manifest 検証
# --------------------------------------------------------------------------- #
def _validate_manifest(manifest: Any) -> List[Dict[str, Any]]:
    """pairs manifest（YAML）を検証し、pair dict のリストを返す（未知/欠落キー fail-closed）。"""
    if not isinstance(manifest, dict):
        raise ValueError("pairs manifest must be a mapping with 'schema' and 'pairs' keys")
    schema = manifest.get("schema")
    if schema != _EXPECTED_MANIFEST_SCHEMA:
        raise ValueError(
            f"unsupported pairs manifest schema {schema!r}; expected "
            f"{_EXPECTED_MANIFEST_SCHEMA!r} (fail-closed)"
        )
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("pairs manifest 'pairs' must be a non-empty list")

    seen_ids: set = set()
    validated: List[Dict[str, Any]] = []
    for idx, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"pairs[{idx}] must be a mapping")
        missing = _REQUIRED_PAIR_KEYS - set(pair)
        if missing:
            raise ValueError(f"pairs[{idx}] missing required keys: {sorted(missing)}")
        unknown = set(pair) - _REQUIRED_PAIR_KEYS
        if unknown:
            raise ValueError(f"pairs[{idx}] has unknown keys (fail-closed): {sorted(unknown)}")
        pair_id = pair["pair_id"]
        if pair_id in seen_ids:
            raise ValueError(f"duplicate pair_id {pair_id!r} in pairs manifest (fail-closed)")
        seen_ids.add(pair_id)
        if pair["kind"] not in _VALID_KINDS:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid kind {pair['kind']!r}; "
                f"expected one of {sorted(_VALID_KINDS)}"
            )
        if pair["split"] not in _VALID_SPLITS:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid split {pair['split']!r}; "
                f"expected one of {sorted(_VALID_SPLITS)}"
            )
        if pair["expected"] not in _VALID_EXPECTED:
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}) has invalid expected {pair['expected']!r}; "
                f"expected one of {sorted(_VALID_EXPECTED)}"
            )
        kind = pair["kind"]
        expected = pair["expected"]
        if kind == "positive_transform" and expected != "same":
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}): kind={kind!r} requires expected='same', got "
                f"{expected!r} (fail-closed; kind×expected 矛盾)"
            )
        if kind.startswith("negative_") and expected != "different":
            raise ValueError(
                f"pairs[{idx}] ({pair_id!r}): kind={kind!r} requires expected='different', got "
                f"{expected!r} (fail-closed; kind×expected 矛盾)"
            )
        validated.append(dict(pair))
    _validate_manifest_composition(validated)
    return validated


def _validate_manifest_composition(pairs: List[Dict[str, Any]]) -> None:
    """M3d 校正素材構成の事前登録検査（設計 §6.1 準拠、fail-closed）。

    レビュー対応 2026-07-30（第 10 ラウンド）: 従来の per-pair 検証（kind/split/
    expected の整形・kind×expected 矛盾）は個々の pair の妥当性しか見ておらず、
    manifest 全体としての素材構成（校正に足る組み合わせが揃っているか）は無検査
    だった——例えば positive pair が 1 件もない・holdout に negative が無い・
    狙い撃ち negative（軸別の単独弁別検証）が欠けている manifest でも run/evaluate
    が素通りしてしまう。設計 §6.1 の必須要件を以下として課す（新規チューナブル
    値は導入しない——全て "組み合わせごとに >= 1 件" の固定要求）:

    (a) tuning split に `positive_transform` が 1 件以上、かつ negative
        （`negative_cross`/`negative_rhythm`/`negative_interval` のいずれか）が
        1 件以上
    (b) 狙い撃ち negative（`negative_rhythm` と `negative_interval`）が
        **tuning split 内に** 各 1 件以上（レビュー対応 2026-07-30 第 11
        ラウンド: 従来は split 不問（holdout のみに置いても要件を満たせた）
        だったが、各軸の弁別を単独検証する趣旨上、校正そのものに使う tuning
        split に無ければ意味がない——holdout のみに置かれた場合は fail。
        holdout に**追加で**置くのは妨げない（tuning に必須本数を満たした上での
        holdout 追加は許可）)
    (c) holdout split に positive（`positive_transform`）が 1 件以上、かつ
        negative が 1 件以上

    欠落は欠けている要件を全て列挙して fail-closed で拒否する。
    """
    tuning_positive = sum(
        1 for p in pairs if p["split"] == "tuning" and p["kind"] == "positive_transform"
    )
    tuning_negative = sum(
        1 for p in pairs if p["split"] == "tuning" and p["kind"].startswith("negative_")
    )
    holdout_positive = sum(
        1 for p in pairs if p["split"] == "holdout" and p["kind"] == "positive_transform"
    )
    holdout_negative = sum(
        1 for p in pairs if p["split"] == "holdout" and p["kind"].startswith("negative_")
    )
    tuning_negative_rhythm = sum(
        1 for p in pairs if p["split"] == "tuning" and p["kind"] == "negative_rhythm"
    )
    tuning_negative_interval = sum(
        1 for p in pairs if p["split"] == "tuning" and p["kind"] == "negative_interval"
    )

    missing: List[str] = []
    if tuning_positive < 1:
        missing.append("tuning split に positive_transform が 0 件 (>= 1 必須)")
    if tuning_negative < 1:
        missing.append("tuning split に negative (いずれかの kind) が 0 件 (>= 1 必須)")
    if tuning_negative_rhythm < 1:
        missing.append(
            "tuning split に negative_rhythm (狙い撃ち negative) が 0 件 (>= 1 必須; "
            "holdout のみに置くのは不可、holdout への追加配置は可)"
        )
    if tuning_negative_interval < 1:
        missing.append(
            "tuning split に negative_interval (狙い撃ち negative) が 0 件 (>= 1 必須; "
            "holdout のみに置くのは不可、holdout への追加配置は可)"
        )
    if holdout_positive < 1:
        missing.append("holdout split に positive (positive_transform) が 0 件 (>= 1 必須)")
    if holdout_negative < 1:
        missing.append("holdout split に negative (いずれかの kind) が 0 件 (>= 1 必須)")

    if missing:
        raise ValueError(
            "pairs manifest の素材構成が事前登録要件 (設計 §6.1) を満たさない (fail-closed): "
            + "; ".join(missing)
        )


def _manifest_audio_paths(manifest_path: Path) -> "set[Path]":
    """pairs manifest 全 pair の audio_a/audio_b を絶対パスへ解決した集合を返す。

    `--out` protected-path チェック用（run phase preflight）: manifest 自体だけ
    でなく、manifest が指す音声入力を `--out` で上書きすることも防ぐ。
    """
    manifest_bytes = Path(manifest_path).read_bytes()
    manifest = _yaml_load_no_dup_keys(manifest_bytes, what="pairs manifest")
    pairs_spec = _validate_manifest(manifest)
    paths: "set[Path]" = set()
    for pair in pairs_spec:
        paths.add(Path(pair["audio_a"]).resolve())
        paths.add(Path(pair["audio_b"]).resolve())
    return paths


def _validate_frozen_axes(
    axes: Dict[str, Any], *, min_separation_margin: Optional[float] = None
) -> bool:
    """凍結 axes（`evidence_thresholds.axes`）の内容が有効か判定する。

    レビュー対応 2026-07-30（第 3 ラウンド）: `M3ComparisonConfig.from_registry` /
    `_build_section` は `evidence_thresholds` の型注釈（`Optional[Dict[str,
    Dict[str, float]]]`）を実行時には検証しない——dataclass の known/required
    キー集合しか見ないため、`axes` の中身自体（各軸の mapping 構造・値の型・
    値域・大小関係）は無検査で通ってしまう。

    レビュー対応 2026-07-30（第 4 ラウンド）: 第 3 ラウンドは「3 軸
    （contour/interval/rhythm）全てが揃わないと holdout を開かない」まで要求
    していたが、これは過剰だった——`compare_melodies` 側の `_derive_evidence`
    （`src/svp_rpe/melody/comparison.py`）はもともと軸別 threshold の欠落を
    許容し、閾値が無い軸は `axis_thresholds_missing:<axis>` という reason を
    積んで `axis_evidence` から単に除外するだけで fail はしない設計だった。
    M3d の校正は軸ごとに独立に進むため、1 軸だけ先にマージンが確保できても
    holdout を開けない設計は実測の足を引っ張る。以下へ緩和する:

    (a) `axes` が非空 mapping であること
    (b) 軸名は {contour, interval, rhythm} の**部分集合**であること（未知軸名は
        fail-closed で拒否——タイプミスや registry の書式崩れを見逃さない）
    (c) **存在する各軸**について以下が整形済みであること:
        - 値が mapping であり `strong_min`/`none_max` キーを持つ
        - 両値が数値（bool は除外）かつ 0.0〜1.0 の範囲内
        - `strong_min >= none_max`
        - `min_separation_margin` が指定された場合、
          `strong_min - none_max >= min_separation_margin`（レビュー対応
          2026-07-30 第 16 ラウンド。次項参照）

    レビュー対応 2026-07-30（第 16 ラウンド・マージン検証）: 上記 (c) の
    `strong_min >= none_max` は大小関係しか見ておらず、両者が僅差（例:
    0.50/0.50 のように差 0）でも通ってしまう——strong/none の判定境界が
    実質的に重なりかねない狭いマージンのまま holdout を開ける穴があった。
    `min_separation_margin`（呼び出し元 `_holdout_unlocked` が
    `config.separation_margin.min_same_minus_cross_margin`——M0/M1 registry
    から継承済みの値、`_validate_separation_margin_inherited_from_m1` で別途
    検証済み——を渡す）が指定された場合、各軸の `strong_min - none_max` が
    その値以上であることも要求する。`min_separation_margin` を渡さない
    直接呼び出し（既存テストの単体呼び出し）は従来どおり大小関係のみを検証し、
    この追加要求を課さない（後方互換）。
    """
    if not isinstance(axes, dict) or not axes:
        return False
    if not set(axes).issubset(_AXES):
        return False
    for axis_mapping in axes.values():
        if not isinstance(axis_mapping, dict):
            return False
        if "strong_min" not in axis_mapping or "none_max" not in axis_mapping:
            return False
        strong_min = axis_mapping["strong_min"]
        none_max = axis_mapping["none_max"]
        for value in (strong_min, none_max):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if not (0.0 <= float(value) <= 1.0):
                return False
        if not (float(strong_min) >= float(none_max)):
            return False
        if min_separation_margin is not None:
            if float(strong_min) - float(none_max) < float(min_separation_margin):
                return False
    return True


def _validate_coverage_floor(floor: Any) -> bool:
    """凍結 `coverage.floor`（`CoverageConfig.floor`）の値そのものが有効か判定する。

    レビュー対応 2026-07-30（第 14 ラウンド）: `CoverageConfig` は frozen dataclass
    であり `floor: float` という型注釈を持つが、`representation.py` の
    `_build_section` は `dc(**mapping)` で構築するだけで実行時の型検証は行わない
    ——registry.yaml に `floor: .nan` / `floor: -1` / `floor: true` を書いても
    `_holdout_unlocked` はこれまで `coverage.floor_status == "frozen"` という
    ステータス文字列しか見ていなかったため、被覆下限ゲート（`comparison.py` の
    `min_fraction < floor` 判定）が壊れた値のまま holdout を開けてしまう穴が
    あった。`_validate_frozen_axes` の値検証（非 bool の有限数値かつ 0.0〜1.0）と
    同型の検証をここで課す。
    """
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        return False
    fvalue = float(floor)
    if not math.isfinite(fvalue):
        return False
    return 0.0 <= fvalue <= 1.0


def _holdout_unlocked(config: Any) -> bool:
    """holdout を開いてよいか判定する（run/evaluate 両 phase 共有の cross-field 不変条件）。

    レビュー対応 2026-07-30: `evidence_thresholds.status == "frozen"` だけでは
    「部分凍結」（軸別閾値がまだ無い・被覆下限がまだ provisional）状態でも holdout
    が開いてしまう穴があった。以下 4 条件の **全て** を要求する（1 つでも欠けたら
    ロックのまま）:

    (a) `evidence_thresholds.status == "frozen"`
    (b) `evidence_thresholds.axes` が非空で、かつ**存在する各軸**の内容が有効
        （`_validate_frozen_axes`。レビュー対応 2026-07-30 第 3 ラウンド: キーが
        揃っているだけの「内容が壊れた」凍結軸を弾く。第 4 ラウンド: 3 軸全てが
        揃っている必要はなくなった——1 軸以上の整形済み凍結軸があれば足りる。
        第 16 ラウンド: 各軸の `strong_min - none_max` が
        `separation_margin.min_same_minus_cross_margin`（M0/M1 registry 継承値）
        以上であることも同関数内で追加要求する)
    (c) `coverage.floor_status == "frozen"`
    (d) `coverage.floor` の値そのものが有効（`_validate_coverage_floor`。レビュー
        対応 2026-07-30 第 14 ラウンド: (c) はステータス文字列しか見ておらず、
        floor の値自体が NaN・負値・bool 等でも凍結扱いになってしまう穴を塞ぐ）

    `M3ComparisonConfig`（`load_m3_registry` の戻り値）だけで判定に必要な値は
    全て読めるため、registry の生 mapping を別途読み直す必要はない。
    """
    thresholds = config.evidence_thresholds
    if thresholds.status != "frozen":
        return False
    min_separation_margin = config.separation_margin.min_same_minus_cross_margin
    if not _validate_frozen_axes(
        thresholds.axes or {}, min_separation_margin=min_separation_margin
    ):
        return False
    if config.coverage.floor_status != "frozen":
        return False
    if not _validate_coverage_floor(config.coverage.floor):
        return False
    return True


def _validate_separation_margin_inherited_from_m1(
    config: Any, m1_mapping: Dict[str, Any]
) -> None:
    """M3 registry の `separation_margin.min_same_minus_cross_margin` が M0/M1
    registry（`registry.yaml` の `separation_gate.min_same_minus_cross_margin`）
    から継承された値であることを検証する（run/evaluate 両 phase 共有、fail-closed）。

    レビュー対応 2026-07-30（第 15 ラウンド）: `M3ComparisonConfig.from_registry`
    （`representation.py` の `_build_section`）は `separation_margin` セクションを
    known-key 集合としてしか検証しておらず、`dc(**mapping)` で dataclass を構築する
    だけのため値そのもの（型・値域・M1 側との一致）は実行時無検査で通ってしまう。
    `tests/test_melody_representation.py::test_separation_margin_synced_with_m0_registry`
    は fixture 2 ファイルの静的な等値を確認する pytest テストに過ぎず、registry
    ファイルが実行時に直接改変された場合（本ハーネス自身の `--registry`/
    `--m1-registry` 引数で別ファイルを指した場合を含む）にこの不変条件を独立に
    enforce するものではない。ここで:

    (a) `config.separation_margin.min_same_minus_cross_margin` が非 bool の有限
        数値であり、かつ `0 < margin <= 1` であること
    (b) 上記の値が、既にロードした M1 registry（`registry.yaml`）の
        `separation_gate.min_same_minus_cross_margin` と等値であること

    を要求する。比較対象は常に呼び出し側がロードした M1 registry の実測値——
    0.15 のようなハードコード値は本関数にも導入しない（M0/M1 registry が source of
    truth）。
    """
    margin = config.separation_margin.min_same_minus_cross_margin
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        raise ValueError(
            "separation_margin.min_same_minus_cross_margin="
            f"{margin!r} は非 bool の数値でなければならない (fail-closed)"
        )
    fmargin = float(margin)
    if not math.isfinite(fmargin) or not (0.0 < fmargin <= 1.0):
        raise ValueError(
            "separation_margin.min_same_minus_cross_margin="
            f"{margin!r} は有限かつ 0 より大きく 1.0 以下でなければならない (fail-closed)"
        )
    separation_gate = m1_mapping.get("separation_gate")
    if not isinstance(separation_gate, dict):
        raise ValueError(
            "M1 registry (registry.yaml) に separation_gate セクションがない (fail-closed)"
        )
    m1_margin = separation_gate.get("min_same_minus_cross_margin")
    if isinstance(m1_margin, bool) or not isinstance(m1_margin, (int, float)):
        raise ValueError(
            "M1 registry の separation_gate.min_same_minus_cross_margin="
            f"{m1_margin!r} は非 bool の数値でなければならない (fail-closed)"
        )
    if float(m1_margin) != fmargin:
        raise ValueError(
            "M3 registry の separation_margin.min_same_minus_cross_margin="
            f"{fmargin!r} が M1 registry (registry.yaml) の "
            f"separation_gate.min_same_minus_cross_margin={m1_margin!r} と不一致; M3 の "
            "separation_margin は M0/M1 registry の値を継承しなければならず、新値を発明しては "
            "ならない (fail-closed)"
        )


# --------------------------------------------------------------------------- #
# run phase
# --------------------------------------------------------------------------- #
def _resolve_route(route_name: str) -> Any:
    """`route_name` を clear_lead 経路の中から解決する（適用帯域=clean lead 限定）。"""
    for route in select_routes("clear_lead"):
        if route.name == route_name:
            return route
    raise ValueError(
        f"unknown --route {route_name!r}; M3 の許可帯域は clear_lead 限定 "
        f"(候補: {[r.name for r in select_routes('clear_lead')]})"
    )


def _validate_route_for_run(route_name: str, route: Any, *, runner_injected: bool) -> None:
    """route が実測制約を満たすか検証する（設計 §6.1 / §8, fail-closed）。

    - melodia 系経路は route_runner 注入の有無に関わらず常に拒否（#222 裁定前の
      混入禁止・設計 §8「melodia の混入」）。
    - route_runner 非注入（実測・publishable な run）は crepe 系経路限定（設計
      §6.1「対の両側とも実 crepe 抽出を通す」）。route_runner 注入時（テスト）は
      任意経路可 — その事実は report 側 `route_runner_injected` に刻まれ、
      evaluate は calibration verdict 発行を別途拒否する。
    """
    if "melodia" in route_name.lower() or getattr(route, "extractor", "") == "melodia":
        raise ValueError(
            f"--route {route_name!r} は melodia 系経路; #222 裁定前は route_runner 注入の "
            "有無に関わらず禁止 (fail-closed, 設計 §8)"
        )
    if not runner_injected and getattr(route, "extractor", "") != "crepe":
        raise ValueError(
            f"--route {route_name!r} は実測 (route_runner 非注入) の run では crepe 系経路 "
            f"限定 (extractor={getattr(route, 'extractor', None)!r}); 設計 §6.1 "
            "「対の両側とも実 crepe 抽出を通す」要求 (fail-closed)"
        )


def _default_route_runner(route_name: str) -> RouteRunner:
    """既定の route_runner: 実抽出器（`observe_via_route_with_provenance`）。

    optional 依存（crepe/melodia）が未導入なら `LearnedModelUnavailable` が
    そのまま伝播する（実行時 DL 禁止・fail-closed。M1/M2 と同じ規律）。
    """
    from svp_rpe.melody.extractors import observe_via_route_with_provenance

    route = _resolve_route(route_name)

    def _runner(audio_path: str) -> "Tuple[MelodyObservation, Dict[str, Any]]":
        return observe_via_route_with_provenance(audio_path, route)

    return _runner


def _redact_unfrozen_axes_for_holdout(
    comparison_dict: Dict[str, Any], frozen_axes: "set"
) -> "Tuple[Dict[str, Any], List[str], List[str]]":
    """holdout pair の `comparison` dict から未凍結軸の値を serialize 前に間引く。

    レビュー対応 2026-07-30（第 6 ラウンド・正直会計）: `_holdout_unlocked` は
    第 4 ラウンド以降「1 軸以上の整形済み凍結軸」があれば holdout を開ける
    （3 軸全て揃う必要はない）。しかし `compare_melodies` の `axes`（軸別生 sim
    値）は凍結状態に関係なく全 3 軸を無条件で計算するため、部分凍結
    （例: contour のみ凍結）の holdout run では**未凍結の** interval/rhythm の
    生 sim 値まで report に書かれてしまう——校正が済んでいない軸の holdout 証拠を
    覗き見できる穴になる（`axis_evidence` は `_derive_evidence` が
    `axis_thresholds_missing:<axis>` として自然に除外するが、`axes` 自体は
    無条件で残る）。ここで凍結済み軸（`frozen_axes`）以外を `axes` /
    `axis_evidence` から削除し（キー自体を消す。`None` で残すと「計測できな
    かった」既存の意味と区別が付かなくなるため）、除外した軸名を返す——呼び出し
    側が pair 結果へ `unfrozen_axes_redacted` として明示記録する。tuning pair
    には適用しない（呼び出し側で `split == "holdout"` の場合のみ呼ぶ）。

    レビュー対応 2026-07-30（第 7 ラウンド・redaction の完全化）: `interval` が
    未凍結軸に含まれる場合、`octave_artifact_suspected`（`comparison.py` §7
    「オクターブ折返しガード」）は interval 軸の folded/raw 類似の乖離から機械
    導出される**派生診断**であり、`axes["interval"]` を隠しても
    `octave_artifact_suspected` フィールドと reasons 内の
    `octave_artifact_suspected(folded=…, raw=…)` 文字列（乖離の生数値を含む）を
    残すと、未校正の interval 軸の性質を間接的に覗き見できてしまう。ここで
    `interval` が redacted 対象なら両方とも取り除き、取り除いた事実を
    `redacted_diagnostics` として返す（呼び出し側が pair 結果へ明示記録する）。
    """
    redacted = sorted(axis for axis in _AXES if axis not in frozen_axes)
    if not redacted:
        return comparison_dict, redacted, []
    comparison_dict = dict(comparison_dict)
    comparison_dict["axes"] = {
        axis: value for axis, value in comparison_dict.get("axes", {}).items() if axis not in redacted
    }
    comparison_dict["axis_evidence"] = {
        axis: value
        for axis, value in comparison_dict.get("axis_evidence", {}).items()
        if axis not in redacted
    }
    redacted_diagnostics: List[str] = []
    if "interval" in redacted:
        comparison_dict.pop("octave_artifact_suspected", None)
        comparison_dict["reasons"] = [
            reason
            for reason in comparison_dict.get("reasons", [])
            if not reason.startswith("octave_artifact_suspected(")
        ]
        redacted_diagnostics.append("octave_artifact_suspected")
    return comparison_dict, redacted, redacted_diagnostics


def _normalize_float_for_observation_hash(value: Optional[float]) -> Optional[str]:
    """float を厳密表現（`float(x).hex()`）へ正規化する。

    決定論 pin のため丸めなし（表示用の系列 hash とは目的が異なる）。

    レビュー対応 2026-07-30（第 12 ラウンド）: 従来は `representation.py` の
    `sequence_sha256` が使う `_normalize_float_for_hash` と同じ固定小数表記
    （`.4f`、小数点 4 桁への丸め）を踏襲していたが、これは軌跡レベル決定論の pin
    としては粗すぎる——1e-5 のような 4 桁未満の差異が丸めで消え、抽出器出力が
    実際には repeats 間でぶれていても pin 一致として見逃してしまう。`.hex()` は
    IEEE754 の bit 表現をそのまま文字列化するため、丸めによる情報損失なしに
    厳密な一致/不一致を判定できる。numpy スカラー型（`np.float32`/`np.float64`
    等）は `.hex()` を持たないため、Python の `float` へ変換してから呼ぶ。
    """
    if value is None:
        return None
    return float(value).hex()


def _observation_content_sha256(observation: MelodyObservation) -> str:
    """`MelodyObservation` の正規化前軌跡の content hash（レビュー対応 2026-07-30
    第 11 ラウンド・生観測軌跡の hash pin）。

    `sequence_sha256`（`representation.py`）は `build_sequences` 後の正規化系列
    （移調・変速不変な表現）を pin するのに対し、本関数は抽出器が返した
    **正規化前**の生軌跡（`frame_times`/`frame_hz`/`frame_confidence` と、
    `observation.notes` が非空ならその `(start_sec, end_sec, pitch_midi,
    confidence)` 列）を pin する——正規化系列の一致だけでは、正規化の過程で
    捨象される情報（例: フレーム confidence そのものの変動、note 系抽出器が
    直接供給した notes の生値）が repeats 間でぶれていても検出できない穴が
    残るため、両者は独立した pin として扱う。

    直列化は `json.dumps(sort_keys=True, separators=(",", ":"))`（決定論・環境
    依存の repr ブレを避ける）を使うが、各 float 値そのものは
    `_normalize_float_for_observation_hash`（丸めなしの厳密表現・`.hex()`）で
    正規化する——`sequence_sha256`（`representation.py`）の固定小数表記
    （`.4f`）とは異なり、本関数は丸めによる情報損失を許さない（軌跡レベル決定論
    の pin という目的上、丸めで潰れる差異こそ検出したい）。
    """
    payload: Dict[str, Any] = {
        "frame_times": [
            _normalize_float_for_observation_hash(v) for v in observation.frame_times
        ],
        "frame_hz": [_normalize_float_for_observation_hash(v) for v in observation.frame_hz],
        "frame_confidence": [
            _normalize_float_for_observation_hash(v) for v in observation.frame_confidence
        ],
    }
    if observation.notes:
        payload["notes"] = [
            [
                _normalize_float_for_observation_hash(note.start_sec),
                _normalize_float_for_observation_hash(note.end_sec),
                _normalize_float_for_observation_hash(note.pitch_midi),
                _normalize_float_for_observation_hash(note.confidence),
            ]
            for note in observation.notes
        ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _freeze_audio_copy(audio_path: "str | Path", staging_dir: str) -> "Tuple[str, str]":
    """`audio_path` の bytes を一度だけ読み、sha256 と同一 bytes の凍結コピーパスを返す。

    レビュー対応 2026-07-30（第 5 ラウンド・TOCTOU 解消）: 従来は `file_sha256(pair[...])`
    と `runner(pair[...])` が別々に同じ path を読んでいたため、pin した bytes と
    実際に抽出器が消費した bytes が同一である保証がなかった（2 回の読み取りの間に
    path の中身が差し替えられれば、pin は実消費バイトを追跡できない）。ここで読んだ
    1 回分の bytes をそのまま sha256 し、**同じ bytes** を `staging_dir`
    （呼び出し側が管理する一時ディレクトリ。system tempdir 配下で manifest / registry /
    音声入力の protected-path とは無関係）へ書き出す——抽出器にはその凍結コピーの
    パスだけを渡すことで、pin と実消費バイトを構造的に一致させる。呼び出し側が
    使用後に削除する（凍結コピーの寿命は 1 pair の抽出呼び出し限り）。
    """
    raw_bytes = Path(audio_path).read_bytes()
    sha256_digest = hashlib.sha256(raw_bytes).hexdigest()
    suffix = Path(audio_path).suffix
    fd, tmp_name = tempfile.mkstemp(prefix="m3freeze_", suffix=suffix, dir=staging_dir)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw_bytes)
    return sha256_digest, tmp_name


# --------------------------------------------------------------------------- #
# 第一者 M3 実装コードの content pin（レビュー対応 2026-07-30 第 12 ラウンド）
# --------------------------------------------------------------------------- #
_M3_CODE_MODULES: Tuple[Any, ...] = (
    _m3_representation_module,
    _m3_alignment_module,
    _m3_comparison_module,
    _m3_observability_module,
    # レビュー対応 2026-07-30（第 17 ラウンド）: route 実行モジュール（入力種別→
    # 抽出経路の候補列挙 `routing.py` / 波形→`MelodyObservation` 変換 `extractors.py`）
    # は run phase で実際に呼ばれる第一者コードであるにもかかわらず pin 対象から
    # 漏れていた——両モジュールが変わっても `m3_code_sha256` は追従しないままだった。
    _m3_extractors_module,
    _m3_routing_module,
)


def _m3_code_sha256(*, use_cache: bool = True) -> str:
    """実行された第一者 M3 実装コードの合成 content pin。

    対象は `representation.py` / `alignment.py` / `comparison.py` /
    `observability.py`（M3 実装本体）/ `routing.py`（入力種別→抽出経路の候補列挙）/
    `extractors.py`（波形→`MelodyObservation` 変換。既定 route_runner が呼ぶ
    実抽出器本体、レビュー対応 2026-07-30 第 17 ラウンドで追加）と本スクリプト自身
    （`scripts/run_melody_comparison.py`）。`svp_rpe.utils.hashing.sha256_of_files`
    （既存ヘルパー、再実装しない）で 1 本の digest に畳む。パスは各モジュールの
    `__file__`（本スクリプトは `__file__` 自身）から解決し、ハードコードしない
    ——モジュールの実体が動けば pin もそれに追従する。

    レビュー対応 2026-07-30（第 13 ラウンド・import 時 bind）: 従来は run/evaluate
    の各呼び出しごとにこの関数を遅延計算していたが、`_M3_CODE_SHA256_AT_IMPORT`
    （本関数直後でモジュールロード完了直後に一度だけ `use_cache=False` で計算し
    bind するモジュール定数）を run/evaluate 双方が参照する方式へ変更した——
    import 時 bind により実行コードと digest の対応を固定する。実行中の能動的
    ファイル差し替えへの事後再検証は本ハーネスの検証スコープ外（report 間 +
    import 時定数とのレコード整合性まで・M2 の 7000 行級 anti-tamper 要塞の複製
    はしない）。本関数自体はテスト（現在値の直接検証）向けに残す——既定
    （`use_cache=True`）で呼べば通常の memoized 計算、import 時 bind だけは
    `use_cache=False` で明示的にキャッシュを経由しない。
    """
    paths = [Path(module.__file__).resolve() for module in _M3_CODE_MODULES]
    paths.append(Path(__file__).resolve())
    return sha256_of_files(paths, use_cache=use_cache)


# import 完了直後に一度だけ計算してモジュール定数へ bind する（`use_cache=False`）。
# run/evaluate 双方はこの定数を参照し、呼び出しごとの遅延再計算はしない——import
# 時 bind により実行コードと digest の対応を固定する。実行中の能動的ファイル差し
# 替えへの事後再検証は本ハーネスの検証スコープ外（レコード整合性まで・M2 要塞の
# 複製はしない）。
_M3_CODE_SHA256_AT_IMPORT: str = _m3_code_sha256(use_cache=False)


def run_comparison(
    *,
    manifest_path: Path,
    route_name: str = "crepe_direct",
    route_runner: Optional[RouteRunner] = None,
    registry_path: Path = M3_REGISTRY_PATH,
    m1_registry_path: Path = M1_REGISTRY_PATH,
) -> Dict[str, Any]:
    """pairs manifest の全ペアを `compare_melodies` へ通し run report dict を返す。

    `route_runner` は抽出器非依存の注入 seam: ``(audio_path) -> (MelodyObservation,
    provenance dict)``。既定は `_default_route_runner`（実抽出器）。テストは
    フェイク抽出器（決定論の観測を返す）に差し替えて run/evaluate の二相
    メカニズムだけを検証する。注入した事実は report 自身に
    ``route_runner_injected: true`` として刻み、evaluate はそれを calibration
    verdict 発行の拒否条件にする（M2 `run_melody_accuracy.py` と同じ規律）。
    """
    runner_injected = route_runner is not None
    route = _resolve_route(route_name)
    _validate_route_for_run(route_name, route, runner_injected=runner_injected)
    runner: RouteRunner = route_runner or _default_route_runner(route_name)

    config, m3_registry_sha256 = load_m3_registry(registry_path)
    m1_mapping, m1_registry_sha256 = _load_m1_registry(m1_registry_path)
    # レビュー対応 2026-07-30（第 15 ラウンド）: registry を読み込んだ直後、
    # holdout ロック判定や pair 処理へ進む前に separation_margin の M0/M1 継承を
    # 検証する（fail-closed。run phase preflight でも evaluate と対称に enforce）。
    _validate_separation_margin_inherited_from_m1(config, m1_mapping)
    thresholds = ObservabilityThresholds.from_registry(m1_mapping["observation_gate"])

    manifest_bytes = Path(manifest_path).read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = _yaml_load_no_dup_keys(manifest_bytes, what="pairs manifest")
    pairs_spec = _validate_manifest(manifest)

    # holdout は `_holdout_unlocked` の全条件（status=frozen + axes 揃い +
    # coverage.floor_status=frozen + coverage.floor 値が有効）を満たすまで run
    # 時点でも開かない（evaluate 側のロックだけでは、report に holdout の
    # axes/hash が既に書かれてしまい閲覧可能になる穴が残るため。設計 §6.3
    # 「holdout は閾値凍結後に一度だけ開く」を run phase でも enforce する）。
    holdout_locked = not _holdout_unlocked(config)

    results: Dict[str, Any] = {
        "schema_version": _EXPECTED_RUN_SCHEMA,
        "mode": "run",
        "started_utc": _utc_now(),
        "run_id": uuid.uuid4().hex,
        "route": route_name,
        "route_runner_injected": runner_injected,
        "m3_registry_sha256": m3_registry_sha256,
        "m1_registry_sha256": m1_registry_sha256,
        "m3_code_sha256": _M3_CODE_SHA256_AT_IMPORT,
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest_sha256,
        "pairs": {},
    }
    with tempfile.TemporaryDirectory(prefix="m3_comparison_freeze_") as staging_dir:
        for pair in pairs_spec:
            if pair["split"] == "holdout" and holdout_locked:
                # 音声も読まず比較も実行しない。split は tuning/holdout フィルタ
                # （`_holdout_pair_ids` / `_margin_table` / `_coverage_floor_candidate`）
                # のために残すが、axes/hash（`comparison`）は一切書かない。
                results["pairs"][pair["pair_id"]] = {
                    "split": pair["split"],
                    "status": "holdout_locked_until_frozen",
                }
                continue
            # content pin + TOCTOU 解消（レビュー対応 2026-07-30 第 4/5 ラウンド）:
            # 音声 bytes を一度だけ読み、その bytes を sha256 で pin すると同時に
            # 同じ bytes の凍結コピーを staging へ書き、抽出（runner）にはその
            # コピーのパスを渡す——pin した bytes と実際に抽出器が消費した bytes を
            # 構造的に一致させる（従来は `file_sha256` と `runner()` が別々に同じ
            # 入力 path を読んでおり、読み取りの間の差し替えを検出できなかった）。
            # holdout ロック pair は上の continue で既に弾かれているため、ここに
            # 到達する pair は必ず音声を読む（開封回避の設計は崩さない）。
            audio_sha256_a, frozen_a = _freeze_audio_copy(pair["audio_a"], staging_dir)
            audio_sha256_b, frozen_b = _freeze_audio_copy(pair["audio_b"], staging_dir)
            try:
                obs_a, prov_a = runner(frozen_a)
                obs_b, prov_b = runner(frozen_b)
            finally:
                Path(frozen_a).unlink(missing_ok=True)
                Path(frozen_b).unlink(missing_ok=True)
            # 生観測軌跡の content hash（レビュー対応 2026-07-30 第 11 ラウンド）:
            # 正規化前（`build_sequences` を通す前）の軌跡を pin する。redaction
            # 対象ではない（axes の値そのものではなく hash であり、holdout の
            # 未凍結軸を覗き見る経路にならない）。
            observation_sha256_a = _observation_content_sha256(obs_a)
            observation_sha256_b = _observation_content_sha256(obs_b)
            comparison = compare_melodies(
                obs_a,
                obs_b,
                observability_thresholds=thresholds,
                config=config,
                provenance_extra={
                    "m3_registry_sha256": m3_registry_sha256,
                    "m1_registry_sha256": m1_registry_sha256,
                },
            )
            comparison_dict = comparison.to_dict()
            # レビュー対応 2026-07-30（第 6 ラウンド）: holdout pair（この時点で
            # 到達しているのは holdout unlock 済みのもののみ——ロック中は上の
            # continue で既に弾かれている）は、凍結済み軸のみを残し未凍結軸の
            # 値を serialize しない。tuning pair は対象外（redacted は None の
            # まま = pair 結果に `unfrozen_axes_redacted` を追加しない）。
            # 第 7 ラウンド: interval 未凍結の場合は `octave_artifact_suspected`
            # 派生診断も同様に除去し、事実を `redacted_diagnostics` として記録する。
            unfrozen_axes_redacted: Optional[List[str]] = None
            redacted_diagnostics: Optional[List[str]] = None
            if pair["split"] == "holdout":
                frozen_axes = set((config.evidence_thresholds.axes or {}).keys())
                comparison_dict, unfrozen_axes_redacted, redacted_diagnostics = (
                    _redact_unfrozen_axes_for_holdout(comparison_dict, frozen_axes)
                )
            row: Dict[str, Any] = {
                "kind": pair["kind"],
                "split": pair["split"],
                "expected": pair["expected"],
                "audio_a": pair["audio_a"],
                "audio_b": pair["audio_b"],
                "audio_sha256_a": audio_sha256_a,
                "audio_sha256_b": audio_sha256_b,
                "observation_sha256_a": observation_sha256_a,
                "observation_sha256_b": observation_sha256_b,
                "comparison": comparison_dict,
            }
            if unfrozen_axes_redacted is not None:
                row["unfrozen_axes_redacted"] = unfrozen_axes_redacted
            if redacted_diagnostics is not None:
                row["redacted_diagnostics"] = redacted_diagnostics
            if prov_a:
                row["route_provenance_a"] = prov_a
            if prov_b:
                row["route_provenance_b"] = prov_b
            results["pairs"][pair["pair_id"]] = row

    results["recorded_utc"] = _utc_now()
    return results


# --------------------------------------------------------------------------- #
# evaluate phase
# --------------------------------------------------------------------------- #
_REPORT_LEVEL_REPEAT_PINS: Tuple[str, ...] = ("manifest_sha256", "route")
_PAIR_LEVEL_PROVENANCE_PINS: Tuple[str, ...] = (
    "route_provenance_a",
    "route_provenance_b",
    # レビュー対応 2026-07-30（第 4 ラウンド）: 音声バイトの content pin。
    # 名前は "PROVENANCE" のままだが実体は「pair 単位で repeats 間一致を
    # 要求する pin 群」——route 由来 provenance と同じ汎用ループで検査できる。
    "audio_sha256_a",
    "audio_sha256_b",
    # レビュー対応 2026-07-30（第 11 ラウンド）: 生観測軌跡（正規化前）の
    # content hash。既存の汎用ループ（有無の一致 + 値の一致を要求）にそのまま
    # 乗せる——欠落・不一致は理由つきで fail、holdout ロック pair（"comparison"
    # を持たない）はこのループより前の分岐で対象外になる。
    "observation_sha256_a",
    "observation_sha256_b",
)
# pair ラベルの manifest 束縛（レビュー対応 2026-07-30 第 7 ラウンド）が検査する
# フィールド群。row に記録されている項目のみ検査する（holdout ロック pair は
# `split` しか記録しない既存構造をそのまま尊重し、欠落しているフィールドの
# 検査は単に skip する——なりすまし検知は「記録されている値が manifest と
# 食い違う」ことを見るためのもので、「本来記録すべきだったのに欠落した」こと
# 自体を fail 条件にはしない）。
_PAIR_LABEL_FIELDS: Tuple[str, ...] = ("split", "expected", "kind")
_PAIR_AUDIO_PATH_FIELDS: Tuple[str, ...] = ("audio_a", "audio_b")


def _validate_pair_labels_bound_to_manifest(reports: List[Dict[str, Any]]) -> None:
    """report の pair ラベル（split/expected/kind/audio パス）が manifest の
    content pin に束縛されていることを検証する（fail-closed）。

    レビュー対応 2026-07-30（第 7 ラウンド）: 従来の registry sha256 pin
    （`_validate_registry_pins`）は registry の同一性しか見ておらず、report
    自身の pair dict（`split`/`expected`/`kind`/`audio_a`/`audio_b`）は無検査
    だった——report をそのまま書き換えれば（例: 未校正の holdout pair の
    `split` を `tuning` に書き換えて margin 計算へ混入させる、`expected` を
    `different`→`same` に書き換えて陽性証拠を捏造する）検出できない穴が
    残っていた。ここで:

    (a) 各 report が記録している `manifest_path` から manifest ファイルを読み、
        その生バイトの sha256 が report 記録の `manifest_sha256` と一致する
        ことを検証する（`manifest_path` の欠落・evaluate 時点でのファイル不在
        も、ラベルを content pin に束縛できないため fail-closed）
    (b) 各 report の各 pair について、row に記録されている
        `split`/`expected`/`kind`/`audio_a`/`audio_b` が、manifest 上の同一
        `pair_id` の値と一致することを検証する（pair_id が manifest に存在
        しない場合も fail-closed）
    (c) 複数 report にまたがる同一 `pair_id` についても、上記フィールドが
        report 間で一致することを検証する（manifest 経由の (a)+(b) と独立の
        直接比較として、defense-in-depth で行う）
    """
    manifest_cache: Dict[str, "Tuple[Dict[str, Dict[str, Any]], str]"] = {}
    cross_report_labels: Dict[str, Dict[str, Any]] = {}

    for idx, report in enumerate(reports):
        manifest_path = report.get("manifest_path")
        if not manifest_path or not isinstance(manifest_path, str):
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に manifest_path が記録されていない; "
                "pair ラベルを content pin に束縛できない (fail-closed)"
            )
        path = Path(manifest_path)
        if not path.is_file():
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の manifest_path={manifest_path!r} が "
                "evaluate 時点のファイルシステム上に存在しない; pair ラベルを content pin に "
                "束縛できない (fail-closed)"
            )
        if manifest_path not in manifest_cache:
            data = path.read_bytes()
            actual_sha256 = hashlib.sha256(data).hexdigest()
            manifest = _yaml_load_no_dup_keys(data, what="pairs manifest")
            pairs_spec = _validate_manifest(manifest)
            pairs_by_id = {pair["pair_id"]: pair for pair in pairs_spec}
            manifest_cache[manifest_path] = (pairs_by_id, actual_sha256)
        pairs_by_id, actual_sha256 = manifest_cache[manifest_path]

        recorded_sha256 = report.get("manifest_sha256")
        if not recorded_sha256:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に manifest_sha256 が記録されていない "
                "(fail-closed)"
            )
        if recorded_sha256 != actual_sha256:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の manifest_sha256={recorded_sha256!r} が "
                f"manifest_path={manifest_path!r} の evaluate 時点の実バイト sha256="
                f"{actual_sha256!r} と不一致; manifest が run 後に差し替えられている疑い "
                "(fail-closed)"
            )

        # レビュー対応 2026-07-30（第 8 ラウンド・manifest 完全集合の要求）: 従来は
        # 「report の各 pair が manifest に存在するか」(過剰不可)しか見ておらず、
        # 「manifest の各 pair が report に存在するか」(欠落不可)は無検査だった——
        # report から都合の悪い pair（例: not_comparable になった negative pair）を
        # 単に削除すれば、manifest 完全集合を装わずに margin 計算をすり抜けられる
        # 穴が残っていた。ここで report の pair_id 集合と manifest の pair_id 完全
        # 集合の**一致**（部分集合・過剰集合のどちらも不可）を要求する——欠落は
        # ここで理由つき（欠落 pair_id を列挙して）fail、過剰は後続のループが
        # 個別 pair_id 単位で検出する。
        missing_pair_ids = sorted(set(pairs_by_id) - set(report["pairs"]))
        if missing_pair_ids:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] は manifest_path={manifest_path!r} の "
                f"pair_id 完全集合を欠いている; 欠落 pair_id={missing_pair_ids} (fail-closed; "
                "report の pair 集合は manifest の pair_id 完全集合と一致しなければならず、"
                "部分集合は許可しない)"
            )

        for pair_id, row in report["pairs"].items():
            manifest_pair = pairs_by_id.get(pair_id)
            if manifest_pair is None:
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} が "
                    f"manifest_path={manifest_path!r} に存在しない (fail-closed; report の "
                    "pair 集合は manifest の pair_id 完全集合と一致しなければならず、過剰は "
                    "許可しない)"
                )
            observed: Dict[str, Any] = {}
            for key in (*_PAIR_LABEL_FIELDS, *_PAIR_AUDIO_PATH_FIELDS):
                if key not in row:
                    # holdout ロック pair は `split` 以外を記録しない既存構造——
                    # 記録されていない項目は検査対象外(欠落は改ざんの痕跡ではない)。
                    continue
                if row[key] != manifest_pair[key]:
                    raise ValueError(
                        f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} の "
                        f"{key}={row[key]!r} が manifest 記録値={manifest_pair[key]!r} と "
                        "不一致; pair ラベルが改ざんされている疑い (fail-closed)"
                    )
                observed[key] = row[key]

            previous = cross_report_labels.setdefault(pair_id, {})
            for key, value in observed.items():
                if key in previous and previous[key] != value:
                    raise ValueError(
                        f"evaluate_comparison: pair {pair_id!r} の {key} が report 間で不一致 "
                        f"(reports[{idx}]={value!r} != 既知値={previous[key]!r}); 同一 pair は "
                        "全 report で同一ラベルでなければならない (fail-closed)"
                    )
                previous[key] = value


def _require_pair_pins_present_for_publishable_reports(reports: List[Dict[str, Any]]) -> None:
    """publishable（`route_runner_injected` でない）report の全 pair（holdout ロック
    pair を除く）に pair 単位 pin（`_PAIR_LEVEL_PROVENANCE_PINS`:
    `audio_sha256_a/b` / `route_provenance_a/b`）が存在することを要求する。

    レビュー対応 2026-07-30（第 6 ラウンド）: `_check_repeats_consistency` は
    repeats 間の**整合性**（有無が repeats 間で揃っているか・値が repeats 間で
    一致するか）しか見ていない——全 report が同じキーを揃って欠落させれば
    「有無が揃っている」ため素通りしてしまい、pin を全欠落させることで repeats
    決定論の見かけを偽装できるバイパスが残っていた。ここで report 単体の欠落
    （全欠落・部分欠落を問わない）を publishable report について理由つきで
    fail-closed 拒否する。`route_runner_injected` な report（テスト用フェイク
    抽出器）はこの要求から除外する（現行の扱いを維持——校正 verdict はどのみち
    `_validate_route_runner_injected_field` 後の判定で発行されない）。
    """
    for idx, report in enumerate(reports):
        if report.get("route_runner_injected"):
            continue
        for pair_id, pair in report["pairs"].items():
            if "comparison" not in pair:
                # holdout ロック pair（音声未読・比較未実行）は対象外。
                continue
            missing = [key for key in _PAIR_LEVEL_PROVENANCE_PINS if key not in pair]
            if missing:
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} に pair 単位 "
                    f"pin が欠落している: {missing}; publishable な report は全 pair "
                    "（holdout ロック pair を除く）で audio_sha256_a/b と "
                    "route_provenance_a/b を必須とする (fail-closed)"
                )


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256_hex(value: Any) -> bool:
    """`value` が 64 桁小文字 hex（sha256 digest の書式）であるかを判定する。"""
    return isinstance(value, str) and bool(_SHA256_HEX_RE.fullmatch(value))


_REPORT_LEVEL_SHA256_FIELDS: Tuple[str, ...] = (
    "manifest_sha256",
    "m3_registry_sha256",
    "m1_registry_sha256",
    # レビュー対応 2026-07-30（第 12 ラウンド）: 第一者 M3 実装コードの content pin。
    "m3_code_sha256",
)
# route_provenance_a/b の crepe 経路固有必須 pin（`extractors.py
# observe_via_route_with_provenance` が実測 crepe 観測に対して必ず記録するキー。
# extractor_weights_kind/files や extractor_code_packages は補助メタデータで
# あって hash pin ではないため、ここでは digest 系の 2 キーのみを必須とする）。
_CREPE_ROUTE_PROVENANCE_REQUIRED_PINS: Tuple[str, ...] = (
    "extractor_weights_sha256",
    "extractor_code_sha256",
)


def _route_requires_crepe_pins(route_name: Any) -> bool:
    """`route_name` が crepe 系経路（extractor == "crepe"）かを判定する。

    未知/非文字列の route_name は判定不能として False を返す（他の検証
    — `_validate_registry_pins` 等の repeats 整合チェックや `_resolve_route`
    を経由する run phase 側の fail-closed — が既に route の妥当性を担保して
    いるため、ここでは crepe 固有の追加要求を課すかどうかの判定に留める）。
    """
    if not isinstance(route_name, str):
        return False
    try:
        route = _resolve_route(route_name)
    except ValueError:
        return False
    return route.extractor == "crepe"


def _validate_pin_formats(reports: List[Dict[str, Any]]) -> None:
    """pin 値そのものの整形を検証する（レビュー対応 2026-07-30 第 8 ラウンド）。

    従来のキー存在検証（`_validate_pair_labels_bound_to_manifest` の
    manifest_sha256 有無 / `_validate_registry_pins` の registry sha256 有無 /
    `_require_pair_pins_present_for_publishable_reports` の pair 単位 pin 有無）
    は、キーが存在しさえすれば値そのもの（null・空文字列・型不正）を無検査で
    通していた——例えば `audio_sha256_a` が空文字列や非 hex 文字列でも「キーは
    存在する」ため従来の検査は素通りしてしまう。ここで:

    (a) report 単位の sha256 pin（`manifest_sha256` / `m3_registry_sha256` /
        `m1_registry_sha256`）が存在する場合、64 桁小文字 hex（sha256 digest の
        書式）であることを検証する
    (b) 各 pair の `audio_sha256_a` / `audio_sha256_b`（存在する場合）が同様に
        64 桁小文字 hex であることを検証する（レビュー対応 2026-07-30 第 16
        ラウンド: `observation_sha256_a` / `observation_sha256_b`——生観測軌跡の
        content hash、第 11 ラウンドで追加された pair 単位 pin——にも同じ書式
        検証を適用する。従来はキー存在（`_require_pair_pins_present_for_
        publishable_reports` は対象外）や repeats 間一致（`_check_repeats_
        consistency`）しか見ておらず、単一 report 内で観測軌跡 pin が `"x"` の
        ような非 hex 文字列でも単体では検出できない穴があった）
    (c) 各 pair の `route_provenance_a` / `route_provenance_b`（存在する場合）が
        mapping であること、かつ report の `route` が crepe 系経路の場合は
        crepe 固有の必須 pin（`extractor_weights_sha256` / `extractor_code_sha256`）
        が既存の `_is_sha256_hex` と同じ書式（64 桁小文字 hex）であることを検証
        する（レビュー対応 2026-07-30 第 9 ラウンド: 従来は非空文字列であること
        しか見ておらず、`"x"` のような非 hex 文字列でも素通りしていた——これらは
        名前どおり sha256 digest を名乗る pin であり、他の sha256 系 pin と同じ
        整形要求を課す）

    `route_runner_injected` な report にも同様に適用する（整形検証は
    injected/publishable を問わない——fake 抽出器であっても記録する pin 値は
    整形済みでなければならない。存在必須化のみ publishable report に限定する
    現行の扱い（`_require_pair_pins_present_for_publishable_reports`）とは
    直交する）。
    """
    for idx, report in enumerate(reports):
        for field in _REPORT_LEVEL_SHA256_FIELDS:
            value = report.get(field)
            if value is not None and not _is_sha256_hex(value):
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] の {field}={value!r} は 64 桁"
                    "小文字 hex（sha256 digest）の書式でない (fail-closed)"
                )

        route_requires_crepe_pins = _route_requires_crepe_pins(report.get("route"))
        for pair_id, pair in report["pairs"].items():
            for key in (
                "audio_sha256_a",
                "audio_sha256_b",
                "observation_sha256_a",
                "observation_sha256_b",
            ):
                if key in pair and not _is_sha256_hex(pair[key]):
                    raise ValueError(
                        f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} の "
                        f"{key}={pair[key]!r} は 64 桁小文字 hex（sha256 digest）の書式でない "
                        "(fail-closed)"
                    )
            for prov_key in ("route_provenance_a", "route_provenance_b"):
                if prov_key not in pair:
                    continue
                provenance = pair[prov_key]
                if not isinstance(provenance, dict):
                    raise ValueError(
                        f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} の "
                        f"{prov_key}={provenance!r} は mapping でなければならない (fail-closed)"
                    )
                if not route_requires_crepe_pins:
                    continue
                for req_key in _CREPE_ROUTE_PROVENANCE_REQUIRED_PINS:
                    req_value = provenance.get(req_key)
                    if not _is_sha256_hex(req_value):
                        raise ValueError(
                            f"evaluate_comparison: reports[{idx}] の pair {pair_id!r} の "
                            f"{prov_key}.{req_key}={req_value!r} は 64 桁小文字 hex（sha256 "
                            "digest）の書式でなければならない (crepe 経路の必須 pin, fail-closed)"
                        )


def _check_repeats_consistency(reports: List[Dict[str, Any]]) -> None:
    """repeats（n>=2 の run report）が同一 pair 集合について bit 一致することを確認する。

    軌跡レベル決定論の実測確立（M2d 残課題を閉じる測定・設計 §1）。`sequence_sha256_a/b`
    と `axes` を repeats 間で突き合わせるだけでは、repeats が本当に「同一 manifest ×
    同一 route」に対する実行であることまでは保証できない（レビュー対応 2026-07-30）。
    report 自身が記録している repeat 定義的 pin（`manifest_sha256` / `route` / pair
    単位の `route_provenance_a/b` / `audio_sha256_a/b` / `observation_sha256_a/b`）
    も突き合わせ、1 箇所でも食い違えば fail-closed で拒否する。`audio_sha256_a/b`
    （レビュー対応 2026-07-30 第 4 ラウンド）・`observation_sha256_a/b`（同第 11
    ラウンド・生観測軌跡の content hash）は holdout ロック pair では記録されない
    （`comparison` 自体が無い）ため、`ref_has_comparison` が False の pair は
    既存どおりこのループより前でスキップされる——開封回避の設計は崩さない。
    """
    if len(reports) < 2:
        return
    reference_report = reports[0]
    reference = reference_report["pairs"]
    for idx, report in enumerate(reports[1:], start=1):
        for pin_key in _REPORT_LEVEL_REPEAT_PINS:
            ref_val = reference_report.get(pin_key)
            cur_val = report.get(pin_key)
            if cur_val != ref_val:
                raise ValueError(
                    f"evaluate_comparison: reports[{idx}] の {pin_key}={cur_val!r} が "
                    f"reports[0]={ref_val!r} と不一致; repeats は同一 manifest × 同一 route "
                    "に対する実行でなければならない (fail-closed)"
                )
        pairs = report["pairs"]
        if set(pairs) != set(reference):
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] のペア集合が reports[0] と異なる; "
                "repeats は同一 manifest に対する実行でなければならない (fail-closed)"
            )
        for pair_id, ref_pair in reference.items():
            cur_pair = pairs[pair_id]
            ref_has_comparison = "comparison" in ref_pair
            cur_has_comparison = "comparison" in cur_pair
            if ref_has_comparison != cur_has_comparison:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の holdout ロック状態が repeats 間で"
                    f"異なる (reports[0] comparison_present={ref_has_comparison} != "
                    f"reports[{idx}] comparison_present={cur_has_comparison}); registry の "
                    "evidence_thresholds.status が実行間で変化した可能性 (fail-closed)"
                )
            if not ref_has_comparison:
                # holdout ロック済みペア同士 — axes/hash が存在しないため比較不要。
                continue
            for prov_key in _PAIR_LEVEL_PROVENANCE_PINS:
                ref_has_prov = prov_key in ref_pair
                cur_has_prov = prov_key in cur_pair
                if ref_has_prov != cur_has_prov:
                    raise ValueError(
                        f"evaluate_comparison: pair {pair_id!r} の {prov_key} 記録有無が "
                        f"repeats 間で異なる (reports[0]={ref_has_prov} != "
                        f"reports[{idx}]={cur_has_prov}) (fail-closed)"
                    )
                if ref_has_prov and ref_pair[prov_key] != cur_pair[prov_key]:
                    raise ValueError(
                        f"evaluate_comparison: pair {pair_id!r} の {prov_key} が repeats 間で"
                        f"不一致 (reports[0]={ref_pair[prov_key]!r} != "
                        f"reports[{idx}]={cur_pair[prov_key]!r}); route 由来の provenance pin "
                        "が実行間で変化している (fail-closed)"
                    )
            ref_comp = ref_pair["comparison"]
            cur_comp = cur_pair["comparison"]
            ref_sig = (
                ref_comp.get("provenance", {}).get("sequence_sha256_a"),
                ref_comp.get("provenance", {}).get("sequence_sha256_b"),
                ref_comp.get("axes"),
            )
            cur_sig = (
                cur_comp.get("provenance", {}).get("sequence_sha256_a"),
                cur_comp.get("provenance", {}).get("sequence_sha256_b"),
                cur_comp.get("axes"),
            )
            if ref_sig != cur_sig:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} が repeats 間で "
                    f"sequence_sha256/axes 不一致 (reports[0]={ref_sig!r} != "
                    f"reports[{idx}]={cur_sig!r}); 軌跡レベル決定論が崩れている (fail-closed)"
                )
            # レビュー対応 2026-07-30（第 14 ラウンド・comparison 全体の等値比較）:
            # 上の `ref_sig`/`cur_sig` は sequence_sha256/axes という個別フィールド
            # の列挙に過ぎず、`comparison` dict のうちそれ以外のキー（`evidence` /
            # `coverage` 等）が repeats 間で食い違っても検出できない穴が残って
            # いた。設計 §6.2 の repeats bit 一致要求は「comparison 全体」の一致
            # であるため、ここで dict 全体の等値（`==`）比較を追加する——個別
            # フィールドの pin チェックは診断メッセージが具体的で有用なため残し、
            # 最後にこの全体比較で締める。
            if ref_comp != cur_comp:
                diff_keys = sorted(
                    (set(ref_comp) ^ set(cur_comp))
                    | {key for key in set(ref_comp) & set(cur_comp) if ref_comp[key] != cur_comp[key]}
                )
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の comparison が repeats 間で "
                    f"全体として不一致 (差分キー={diff_keys}); repeats は同一 manifest × "
                    "同一 route に対する決定論的実行でなければならない (fail-closed; 設計 "
                    "§6.2「repeats n≥2 + comparison 全体の bit 一致」)"
                )


def _validate_registry_pins(
    reports: List[Dict[str, Any]],
    *,
    current_m3_sha256: str,
    current_m1_sha256: str,
) -> None:
    """report 間 + 現在ロードした registry との sha256 pin 整合を検証する (fail-closed)。

    (a) report に m3_registry_sha256 が欠落、(b) report 間で不一致、(c) 現在
    ロードした registry の sha256 と不一致 — のいずれかを検出したら拒否する。
    m1_registry_sha256 も同様に **全 report 必須**として検査する（レビュー対応
    2026-07-30: 「1 件も記録がなければ検査自体をスキップする」バイパスは、m1
    registry pin 不整合を全 report 揃って握りつぶすケースを見逃すため廃止した）。
    """
    for idx, report in enumerate(reports):
        m3_sha = report.get("m3_registry_sha256")
        if not m3_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に m3_registry_sha256 が記録されていない "
                "(fail-closed)"
            )
    reference_m3_sha = reports[0]["m3_registry_sha256"]
    for idx, report in enumerate(reports[1:], start=1):
        m3_sha = report["m3_registry_sha256"]
        if m3_sha != reference_m3_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の m3_registry_sha256={m3_sha!r} が "
                f"reports[0]={reference_m3_sha!r} と不一致 (fail-closed)"
            )
    if reference_m3_sha != current_m3_sha256:
        raise ValueError(
            f"evaluate_comparison: reports の m3_registry_sha256={reference_m3_sha!r} が現在"
            f"ロードした registry の sha256={current_m3_sha256!r} と不一致 (fail-closed)"
        )

    m1_shas = [report.get("m1_registry_sha256") for report in reports]
    for idx, sha in enumerate(m1_shas):
        if not sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に m1_registry_sha256 が記録されて"
                "いない (fail-closed)"
            )
    reference_m1_sha = m1_shas[0]
    for idx, sha in enumerate(m1_shas[1:], start=1):
        if sha != reference_m1_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の m1_registry_sha256={sha!r} が "
                f"reports[0]={reference_m1_sha!r} と不一致 (fail-closed)"
            )
    if reference_m1_sha != current_m1_sha256:
        raise ValueError(
            f"evaluate_comparison: reports の m1_registry_sha256={reference_m1_sha!r} が"
            f"現在ロードした m1 registry の sha256={current_m1_sha256!r} と不一致 "
            "(fail-closed)"
        )


def _validate_m3_code_sha256(
    reports: List[Dict[str, Any]],
    *,
    current_m3_code_sha256: str,
) -> None:
    """`m3_code_sha256`（第一者 M3 実装コードの content pin）の整合を検証する
    (fail-closed)。`_validate_registry_pins` の m3/m1 registry sha256 検証と同型の
    3 条件を課す:

    (a) 各 report に `m3_code_sha256` が記録されていること（欠落は拒否）
    (b) report 間で一致すること（レポート間不一致は拒否）
    (c) `current_m3_code_sha256`（呼び出し側が渡す現在値）と一致すること
        （report を直接書き換えて値を差し替えても不一致で検出する）

    レビュー対応 2026-07-30（第 12 ラウンド）: 従来 M3 実装コード
    （representation/alignment/comparison/observability + 本スクリプト自身）に
    対する content pin が一切なく、report が指す軌跡・holdout 判定がどのコード
    版で作られたものかを追跡できなかった。registry sha256 pin と対称の検証を
    課す。

    レビュー対応 2026-07-30（第 13 ラウンド・import 時 bind）: `current_m3_code_sha256`
    は呼び出し側（`evaluate_comparison`）が `_m3_code_sha256()` を都度再計算して
    渡すのではなく、import 完了直後にモジュール定数として一度だけ bind した
    `_M3_CODE_SHA256_AT_IMPORT` を渡す——import 時 bind により実行コードと digest
    の対応を固定する。実行中の能動的ファイル差し替えへの事後再検証は本ハーネスの
    検証スコープ外（report 間 + import 時定数とのレコード整合性まで・M2 要塞の
    複製はしない）。
    """
    for idx, report in enumerate(reports):
        m3_code_sha = report.get("m3_code_sha256")
        if not m3_code_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に m3_code_sha256 が記録されていない "
                "(fail-closed)"
            )
    reference_m3_code_sha = reports[0]["m3_code_sha256"]
    for idx, report in enumerate(reports[1:], start=1):
        m3_code_sha = report["m3_code_sha256"]
        if m3_code_sha != reference_m3_code_sha:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の m3_code_sha256={m3_code_sha!r} が "
                f"reports[0]={reference_m3_code_sha!r} と不一致 (fail-closed)"
            )
    if reference_m3_code_sha != current_m3_code_sha256:
        raise ValueError(
            f"evaluate_comparison: reports の m3_code_sha256={reference_m3_code_sha!r} が "
            f"現在実行中の evaluate 環境で計算した M3 実装コードの sha256="
            f"{current_m3_code_sha256!r} と不一致; M3 実装コードが run 後に変更されている疑い "
            "(fail-closed)"
        )


def _validate_route_runner_injected_field(reports: List[Dict[str, Any]]) -> None:
    """各 report に `route_runner_injected` が bool として記録されていることを要求する。

    レビュー対応 2026-07-30（第 3 ラウンド）: 従来は `any(bool(r.get(
    "route_runner_injected")) for r in reports)` で読んでいたため、キー欠落や
    非 bool 値（例: 文字列 `"false"` や整数 `0`）が `bool(None)`/`bool(...)` の
    暗黙変換で握りつぶされ、注入事実を隠した report が calibration verdict を
    素通りできる穴があった（M2 `_require_publishable_runs` と同じ規律の逆流）。
    欠落・非 bool は理由つきで fail-closed 拒否する。
    """
    for idx, report in enumerate(reports):
        if "route_runner_injected" not in report:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に route_runner_injected が"
                "記録されていない (fail-closed)"
            )
        value = report["route_runner_injected"]
        if not isinstance(value, bool):
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の route_runner_injected="
                f"{value!r} は bool でなければならない (fail-closed)"
            )


def _validate_route_for_evaluate(reports: List[Dict[str, Any]]) -> None:
    """report の `route` が evaluate phase でも実測制約を満たすか検証する（fail-closed）。

    レビュー対応 2026-07-30（第 9 ラウンド）: run phase の `_validate_route_for_run`
    （設計 §6.1 / §8）は report 生成時点の一度きりの検証——report を直接書き換えて
    `route` を差し替えれば run 側のガードは既にすり抜けられているため、evaluate
    phase でも独立に同じ規律を enforce する:

    - melodia 系経路は `route_runner_injected` の有無に関わらず常に拒否する
      （#222 裁定前の混入禁止・設計 §8。run 側と対称）。
    - publishable（`route_runner_injected` が False）な report は crepe 系経路
      （`extractor == "crepe"`）限定。非 crepe・未知 route は理由つきで拒否する
      （設計 §6.1「対の両側とも実 crepe 抽出を通す」）。
    - `route_runner_injected` な report（テスト用フェイク抽出器）は crepe 限定を
      課さない（現行どおり — calibration verdict はどのみち
      `_validate_route_runner_injected_field` 後の判定で発行されない）。

    `_validate_route_runner_injected_field` が既に bool 型であることを確認済み
    という前提で呼ぶ（evaluate_comparison 内の呼び出し順序で保証する）。
    """
    for idx, report in enumerate(reports):
        route_name = report.get("route")
        if not isinstance(route_name, str) or not route_name:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] に route が記録されていない、または"
                "非文字列/空文字列 (fail-closed)"
            )
        if "melodia" in route_name.lower():
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の route={route_name!r} は melodia 系"
                "経路; route_runner_injected の有無に関わらず #222 裁定前は禁止 (fail-closed; "
                "run phase の _validate_route_for_run と対称)"
            )
        if report["route_runner_injected"]:
            # injected report（テスト用フェイク抽出器）は crepe 限定を課さない。
            continue
        try:
            route = _resolve_route(route_name)
        except ValueError as exc:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の route={route_name!r} を解決できない; "
                f"publishable report は crepe 系経路限定 (fail-closed): {exc}"
            ) from exc
        if getattr(route, "extractor", "") != "crepe":
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の route={route_name!r} "
                f"(extractor={getattr(route, 'extractor', None)!r}) は crepe 系経路でない; "
                "publishable（route_runner_injected: false）な report は crepe 系経路限定 "
                "(fail-closed; 設計 §6.1「対の両側とも実 crepe 抽出を通す」・run phase の "
                "_validate_route_for_run と対称)"
            )


def _validate_run_ids_distinct(reports: List[Dict[str, Any]]) -> None:
    """全 report の `run_id` が存在・非空・相互に distinct であることを要求する。

    レビュー対応 2026-07-30（第 4 ラウンド）: repeats（n>=2）は「別々の実行」で
    あることが前提——同一 report を誤って 2 度 `--evaluate` に渡す（同一 run の
    二重指定）と、`_check_repeats_consistency` の bit 一致検査は当然 pass して
    しまい、決定論の実測確立を偽装できてしまう。`run_id`（run phase で
    `uuid.uuid4().hex` として記録）の相互 distinct 性を、他の構造検査
    （registry pin / repeats consistency）が通った後に別途要求する（fail-closed）。
    欠落・空・非文字列も同様に拒否する。
    """
    seen: Dict[str, int] = {}
    for idx, report in enumerate(reports):
        run_id = report.get("run_id")
        if not run_id or not isinstance(run_id, str):
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の run_id が欠落または空/非文字列 "
                f"(run_id={run_id!r}); repeats は run_id を持つ report でなければならない "
                "(fail-closed)"
            )
        if run_id in seen:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の run_id={run_id!r} が "
                f"reports[{seen[run_id]}] と重複している; 同一 run の二重指定は repeats "
                "として扱えない (fail-closed)"
            )
        seen[run_id] = idx


def _holdout_pair_ids(pairs: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(pair_id for pair_id, pair in pairs.items() if pair["split"] == "holdout")


def _holdout_missing_comparison_pair_ids(
    pairs: Dict[str, Dict[str, Any]], *, unlocked: bool
) -> List[str]:
    """holdout split のうち `comparison` を持たない pair_id を返す（fail-closed 対象）。

    レビュー対応 2026-07-30（第 12 ラウンド・holdout 行 comparison 必須化）: holdout
    が凍結解除された評価（呼び出し側で `_holdout_unlocked(config)` が True）では、
    run phase の意図どおりに動いていれば holdout split の各 pair 行は必ず
    `comparison` を持つ（`holdout_locked_until_frozen` 状態の行は unlock 前にのみ
    現れる——`run_comparison` は holdout ロック中の pair に `{"split": ...,
    "status": "holdout_locked_until_frozen"}` だけを書き、音声すら読まない）。
    report が直接書き換えられて `comparison` が削除された（かつ `status` も
    `holdout_locked_until_frozen` に書き換えられていない）行は、この不整合を
    検出できないままだと holdout 検証が静かにスキップしてしまう
    （`_holdout_validation_table` は `comparison is None` の行を単に continue
    するため、正当なロック行と欠落行を区別しない）。ここで両者を区別し、欠落行の
    pair_id を fail-closed の理由として返す——呼び出し側（`evaluate_comparison`）
    はこれが非空なら `holdout_validation_status` を
    `calibration_not_confirmed_on_holdout` に倒す。

    レビュー対応 2026-07-30（第 13 ラウンド・unlocked 評価での locked 偽装行拒否）:
    第 12 ラウンドの実装は `status == "holdout_locked_until_frozen"` を申告する
    行を常に免除していたが、本関数の唯一の呼び出し元（`evaluate_comparison` の
    `if not holdout_locked:` 節）は評価が **unlocked**（`_holdout_unlocked(config)`
    が True）の場合にしか呼ばれない——run phase は holdout unlock 済みなら該当
    pair の音声を必ず読み `comparison` を書くため、unlocked 評価に
    `holdout_locked_until_frozen` を申告する行が現れること自体が「正当なロック
    行」ではなく「report が改ざんされ、comparison 欠落を locked 偽装で隠して
    いる」疑いを示す。免除をそのまま適用すると、この偽装行が fail-closed 検出を
    すり抜けてしまう。ここで `unlocked` 引数を追加し、`unlocked=True`（unlocked
    評価から呼ばれる現行の唯一の呼び出し）では locked status 申告による免除を
    適用しない——`comparison` を持たない holdout 行は申告 status に関わらず一律
    missing として fail 対象にする。`unlocked=False`（locked 評価コンテキストで
    呼ばれる場合に備えた分岐）は従来どおり `holdout_locked_until_frozen` 申告を
    免除する——locked 評価での locked 行の扱いは本ラウンドで変えない。
    """
    missing: List[str] = []
    for pair_id, pair in pairs.items():
        if pair["split"] != "holdout":
            continue
        if "comparison" in pair:
            continue
        if not unlocked and pair.get("status") == "holdout_locked_until_frozen":
            continue
        missing.append(pair_id)
    return sorted(missing)


def _validate_pair_axes_values(pairs: Dict[str, Dict[str, Any]]) -> None:
    """各 pair の `comparison.axes` 値が margin/holdout 集計に足る整形か検証する。

    レビュー対応 2026-07-30（第 14 ラウンド）: `_margin_table` / `_holdout_validation_table`
    は `comparison["axes"]` の値をそのまま `min()`/`max()` へ積むだけで、値そのもの
    （型・値域）を無検査で通していた——`comparison` dict が report 直接改ざんや
    比較器側の不具合で `axes` に `2.0`（域外）・`-1.0`（域外）・`True`（bool）・
    `"0.5"`（文字列）を持っていても、集計結果（マージン表・holdout 判定）が黙って
    壊れたまま fail しない穴があった。`evaluate_comparison` は margin/holdout 集計の
    **前** に、全 pair（tuning/holdout 問わず。`comparison` を持たない holdout
    ロック pair は対象外）について以下を要求する（fail-closed）:

    (a) 軸名は既知軸（`_AXES` = contour/interval/rhythm）のみであること
    (b) 各軸の値は `None`、または非 bool の有限数値（`math.isfinite`）かつ
        0.0〜1.0 の範囲内であること
    """
    for pair_id, pair in pairs.items():
        comparison = pair.get("comparison")
        if comparison is None:
            continue
        axes = comparison.get("axes", {})
        if not isinstance(axes, dict):
            raise ValueError(
                f"evaluate_comparison: pair {pair_id!r} の comparison.axes が mapping で"
                f"ない (axes={axes!r}) (fail-closed)"
            )
        for axis_name, value in axes.items():
            if axis_name not in _AXES:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の comparison.axes に未知軸名 "
                    f"{axis_name!r} (既知軸: {sorted(_AXES)}) (fail-closed)"
                )
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の axis {axis_name!r} の値 "
                    f"{value!r} は None または非 bool の数値でなければならない (fail-closed)"
                )
            fvalue = float(value)
            if not math.isfinite(fvalue) or not (0.0 <= fvalue <= 1.0):
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} の axis {axis_name!r} の値 "
                    f"{value!r} は有限かつ 0.0〜1.0 の範囲内でなければならない (fail-closed)"
                )


_VALID_EVIDENCE_VALUES: "set[str]" = {"strong", "weak", "none", "not_comparable"}


def _validate_evidence_values_and_axes_consistency(pairs: Dict[str, Dict[str, Any]]) -> None:
    """各 pair の `comparison.evidence` が既知の列挙値であること、かつ `axes` との
    整合を検証する（レビュー対応 2026-07-30 第 16 ラウンド、fail-closed）。

    `_validate_pair_axes_values` は `axes` 各値の型・値域しか見ておらず、
    `evidence` フィールド自体（列挙値かどうか）や、`evidence` と `axes` の
    組み合わせが自己矛盾していないか（例: report を直接書き換えて `evidence`
    だけを差し替える・`not_comparable` を別の値へ改名しつつ `axes` は
    `not_comparable` の痕跡である全 None のまま残す）は無検査だった。ここで
    以下を要求する:

    (a) `comparison.evidence` は `{"strong", "weak", "none", "not_comparable"}`
        のいずれかであること。未知値は pair_id・値つきで fail-closed 拒否する
        （`compare_melodies`/`_derive_evidence` が返し得る値はこの 4 つのみ
        ——`MelodyComparisonReport.evidence` の型注釈どおり）。
    (b) `evidence == "not_comparable"` の行は、`axes` に残る値が全て `None`
        であること（`_not_comparable` が `axes={axis: None for axis in _AXES}`
        を返す既存契約——非 None 値が残っていれば改ざんの疑いとして拒否する）。
    (c) `evidence != "not_comparable"` の行は、期待される軸キー（holdout の
        `unfrozen_axes_redacted` により意図的に間引かれた軸を除く——`_redact_
        unfrozen_axes_for_holdout` は未凍結軸のキー自体を `axes` から削除する
        正当な機構であり、これを「欠落」として誤検出しない）が全て存在すること
        （値は `None` を許容——`rhythm_not_computable` 等、計算不能を正直に
        記録する正当なケース）。
    (d) (c) の期待される軸キーのうち **1 つでも present** な状況で、それらが
        全て `None` であるにもかかわらず `evidence` が `"not_comparable"` で
        ない行は不整合として拒否する。`_derive_evidence` の設計上、`axes` が
        （redaction 対象外の範囲で）全て計算不能なら `computed` は空集合になり
        `evidence` は必ず `"none"`（`no_axes_evidence_computable`）に倒れる
        ——`strong`/`weak` は最低 1 軸の実測 sim 値なしには成立し得ない。加えて
        `axes` が全 None であること自体が `not_comparable` の唯一の生成経路
        （`_not_comparable`）の署名であるため、`"none"` も含めて
        `not_comparable` 以外の値と組み合わさっていること自体が改ざんの疑いを
        示す（coverage floor gate 通過後の正当な計算では発生し得ない）。
    """
    for pair_id, pair in pairs.items():
        comparison = pair.get("comparison")
        if comparison is None:
            # holdout ロック pair（音声未読・比較未実行）は対象外。
            continue
        evidence = comparison.get("evidence")
        if evidence not in _VALID_EVIDENCE_VALUES:
            raise ValueError(
                f"evaluate_comparison: pair {pair_id!r} の comparison.evidence={evidence!r} は "
                f"既知の列挙値でない (期待値: {sorted(_VALID_EVIDENCE_VALUES)}) (fail-closed)"
            )
        axes = comparison.get("axes", {})
        if not isinstance(axes, dict):
            # 型・mapping であることは `_validate_pair_axes_values` が既に検証済み
            # （呼び出し順序でそちらを先に通す前提）——ここでは isinstance を
            # 信頼して進む安全側の防御のみ残す。
            raise ValueError(
                f"evaluate_comparison: pair {pair_id!r} の comparison.axes が mapping でない "
                f"(axes={axes!r}) (fail-closed)"
            )

        if evidence == "not_comparable":
            non_none = {axis: value for axis, value in axes.items() if value is not None}
            if non_none:
                raise ValueError(
                    f"evaluate_comparison: pair {pair_id!r} は evidence=not_comparable だが "
                    f"axes に非 None 値が残っている ({non_none!r}); not_comparable の行は axes "
                    "全 None でなければならない (fail-closed; evidence 改ざんの疑い)"
                )
            continue

        redacted = set(pair.get("unfrozen_axes_redacted") or ())
        expected_axes = set(_AXES) - redacted
        missing_axes = sorted(axis for axis in expected_axes if axis not in axes)
        if missing_axes:
            raise ValueError(
                f"evaluate_comparison: pair {pair_id!r} は evidence={evidence!r} だが axes に "
                f"欠落軸がある: {missing_axes} (fail-closed; evidence != not_comparable の行は "
                "3 軸キー全て（holdout の unfrozen_axes_redacted で間引かれた軸を除く）が "
                "存在しなければならない)"
            )
        present_values = [axes.get(axis) for axis in expected_axes]
        if present_values and all(value is None for value in present_values):
            raise ValueError(
                f"evaluate_comparison: pair {pair_id!r} は evidence={evidence!r} だが axes "
                f"（{sorted(expected_axes)}）が全て None; not_comparable 以外の値は axes 全 None "
                "とは両立しない (fail-closed; evidence/axes 整合性違反の疑い)"
            )


def _margin_table(
    pairs: Dict[str, Dict[str, Any]], *, split: Optional[str], min_margin: float
) -> Dict[str, Any]:
    """`split`（None なら全件）の pair から軸別マージン（positive 最小 − negative 最大）を出す。

    `evidence_thresholds` を一切見ない——校正候補は生の axes 値の分布のみから導出し、
    holdout ロックは呼び出し側（`evaluate_comparison`）が別途課す。

    `not_comparable` で除外した pair は `skipped_pairs` に理由付きで積むだけでなく、
    positive（``expected == "same"``）/negative（``expected == "different"``）の
    内訳を `not_comparable_positive_count` / `not_comparable_negative_count` として
    別途会計する（レビュー対応 2026-07-30 第 5 ラウンド・正直会計）。tuning split の
    positive pair が測れなかった（=同一の証拠を出すべきペアで計器が沈黙した）ことは
    「negative が測れなかった」こととは意味が異なり、呼び出し側（`evaluate_comparison`）
    はこの区別を見て freeze proposal の発行可否を決める。
    """
    axis_positive: Dict[str, List[float]] = {axis: [] for axis in _AXES}
    axis_negative: Dict[str, List[float]] = {axis: [] for axis in _AXES}
    skipped: List[str] = []
    not_comparable_positive_count = 0
    not_comparable_negative_count = 0

    for pair_id, pair in pairs.items():
        if split is not None and pair["split"] != split:
            continue
        comparison = pair["comparison"]
        if comparison["evidence"] == "not_comparable":
            skipped.append(f"{pair_id}:not_comparable")
            if pair["expected"] == "same":
                not_comparable_positive_count += 1
            else:
                not_comparable_negative_count += 1
            continue
        bucket = axis_positive if pair["expected"] == "same" else axis_negative
        for axis, value in comparison["axes"].items():
            if value is None:
                continue
            bucket.setdefault(axis, []).append(value)

    axes_table: Dict[str, Any] = {}
    calibrated_axes: List[str] = []
    for axis in _AXES:
        positives = axis_positive.get(axis, [])
        negatives = axis_negative.get(axis, [])
        if not positives or not negatives:
            axes_table[axis] = {
                "positive_min": None,
                "negative_max": None,
                "margin": None,
                "calibrated_candidate": False,
                "reason": "insufficient_positive_or_negative_samples",
            }
            continue
        positive_min = min(positives)
        negative_max = max(negatives)
        margin = positive_min - negative_max
        calibrated = margin >= min_margin
        axes_table[axis] = {
            "positive_min": positive_min,
            "negative_max": negative_max,
            "margin": margin,
            "calibrated_candidate": calibrated,
        }
        if calibrated:
            calibrated_axes.append(axis)

    return {
        "axes": axes_table,
        "calibrated_axes": calibrated_axes,
        "skipped_pairs": skipped,
        "not_comparable_positive_count": not_comparable_positive_count,
        "not_comparable_negative_count": not_comparable_negative_count,
    }


def _validate_tuning_coverage_completeness(pairs: Dict[str, Dict[str, Any]]) -> List[str]:
    """freeze proposal 発行前に、比較可能な tuning pair 全ての `comparison.coverage`
    4 フィールドの完全性を検証し、違反した pair_id のリスト（sorted・重複なし）を
    返す（空リストは違反なし）。例外は投げない——呼び出し側 `evaluate_comparison`
    が freeze proposal / coverage floor 候補の発行可否として扱う（fail-closed の
    最終判断は呼び出し側）。

    レビュー対応 2026-07-30（第 15 ラウンド）: `_coverage_floor_candidate` は
    tuning split の positive pair から `aligned_note_fraction_a/b` の値を読むだけで、
    値の型・値域を無検査で `min()`/`max()`/`sum()` へ積んでいた——`comparison.coverage`
    が report 直接改ざんや比較器側の不具合で欠落・bool・域外値を持っていても、
    coverage floor 候補が黙って壊れたまま算出される穴があった。`_margin_table` の
    `not_comparable_positive_count` 会計（第 5 ラウンド）と同種の「正直会計」を
    coverage 側にも課す。

    対象は比較可能（`comparison` が存在し `evidence != "not_comparable"`）な
    **tuning split** の pair 全て（positive/negative 問わない——4 フィールドの
    整形は kind に関係なく計器が保証すべき値のため。positive pair の欠落は
    coverage floor 候補の分布不完全に直結し、negative pair の欠落も同じ
    `comparison.coverage` 構造の壊れを示すため区別しない）。各 pair について:

    (a) `comparison.coverage` が mapping であること
    (b) 4 フィールド（aligned_note_fraction_a/b・phrase_coverage_a/b）が存在し、
        非 bool の有限数値（`math.isfinite`）かつ 0.0〜1.0 の範囲内であること

    のいずれかが破れたら、その pair_id を違反として集める。holdout split の
    pair・`comparison` を持たない pair（holdout ロック行）・`not_comparable` な
    pair は対象外（floor 候補にも margin にも寄与しないため検証不要）。
    """
    violations: set = set()
    for pair_id, pair in pairs.items():
        if pair["split"] != "tuning":
            continue
        comparison = pair.get("comparison")
        if comparison is None:
            continue
        if comparison.get("evidence") == "not_comparable":
            continue
        coverage = comparison.get("coverage")
        if not isinstance(coverage, dict):
            violations.add(pair_id)
            continue
        for field in _COVERAGE_FIELDS:
            if field not in coverage:
                violations.add(pair_id)
                break
            value = coverage[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                violations.add(pair_id)
                break
            fvalue = float(value)
            if not math.isfinite(fvalue) or not (0.0 <= fvalue <= 1.0):
                violations.add(pair_id)
                break
    return sorted(violations)


def _coverage_floor_candidate(pairs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """tuning split の positive pair の被覆分布から floor 候補を出す（設計 §4.2 / §6.2）。

    凍結は行わない（人間の registry 更新 commit の仕事）——ここでは観測分布の
    記録のみを返す。
    """
    fractions: List[float] = []
    for pair in pairs.values():
        if pair["split"] != "tuning" or pair["expected"] != "same":
            continue
        coverage = pair["comparison"].get("coverage") or {}
        for key in ("aligned_note_fraction_a", "aligned_note_fraction_b"):
            value = coverage.get(key)
            if value is not None:
                fractions.append(value)
    if not fractions:
        return {"candidate": None, "reason": "no_tuning_positive_coverage_samples"}
    return {
        "candidate": min(fractions),
        "min": min(fractions),
        "max": max(fractions),
        "mean": sum(fractions) / len(fractions),
        "sample_count": len(fractions),
    }


def _holdout_validation_table(
    pairs: Dict[str, Dict[str, Any]], *, frozen_axes: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """凍結済み軸ごとに holdout split の positive 最小類似・negative 最大類似を集計し、
    凍結値（`strong_min`/`none_max`）からのみ機械判定する（新規チューナブル禁止）。

    レビュー対応 2026-07-30（第 10 ラウンド）: `_margin_table` は tuning split の
    生分布から校正候補（マージン閾値 `separation_margin`）を導出するもので、
    holdout の役目（凍結済み閾値が本当に holdout でも成立するかの事後検証）とは
    別物——ここでは `_margin_table` と異なり閾値を一切発明せず、registry に
    既に刻まれた凍結値 (`evidence_thresholds.axes[axis].strong_min`/`none_max`)
    とだけ比較する。

    軸ごとの判定:
    - `confirmed`: holdout positive 最小 >= 凍結 `strong_min` かつ
      holdout negative 最大 <= 凍結 `none_max`
    - `holdout_failed`: 上記のいずれかが破れた場合（破れた側の値を
      `violations` に記録）。positive/negative のどちらかの標本が 0 件の場合も
      判定不能として `holdout_failed`（fail-closed — 判定できないことを
      confirmed の側に倒さない）

    `pairs` の holdout 行は `_redact_unfrozen_axes_for_holdout` により未凍結軸の
    値が既に間引かれているため、`frozen_axes` に絞って集計すれば十分——未凍結軸
    はそもそも `comparison["axes"]` に存在しない。

    レビュー対応 2026-07-30（第 11 ラウンド）: holdout split の pair に 1 件でも
    `not_comparable`（観測ゲート不通過・被覆不足等でスキップされた比較）があれば、
    その pair_id を `not_comparable_pair_ids` として別途返す——呼び出し側
    （`evaluate_comparison`）はこの非空性だけで、軸別 verdict（`confirmed`/
    `holdout_failed`）に関わらず `holdout_validation_status` を
    `calibration_not_confirmed_on_holdout` に倒す（測れなかった pair が 1 件でも
    あれば「holdout で確認できた」とは主張しない）。
    """
    axis_positive: Dict[str, List[float]] = {axis: [] for axis in frozen_axes}
    axis_negative: Dict[str, List[float]] = {axis: [] for axis in frozen_axes}
    skipped: List[str] = []
    not_comparable_pair_ids: List[str] = []

    for pair_id, pair in pairs.items():
        if pair["split"] != "holdout":
            continue
        comparison = pair.get("comparison")
        if comparison is None:
            # holdout ロック pair（音声未読・比較未実行）— 対象外。
            continue
        if comparison.get("evidence") == "not_comparable":
            skipped.append(f"{pair_id}:not_comparable")
            not_comparable_pair_ids.append(pair_id)
            continue
        bucket = axis_positive if pair["expected"] == "same" else axis_negative
        for axis, value in comparison.get("axes", {}).items():
            if axis not in frozen_axes or value is None:
                continue
            bucket[axis].append(value)

    table: Dict[str, Any] = {}
    for axis, thresholds in frozen_axes.items():
        positives = axis_positive.get(axis, [])
        negatives = axis_negative.get(axis, [])
        strong_min = float(thresholds["strong_min"])
        none_max = float(thresholds["none_max"])
        if not positives or not negatives:
            table[axis] = {
                "holdout_positive_min": None,
                "holdout_negative_max": None,
                "frozen_strong_min": strong_min,
                "frozen_none_max": none_max,
                "verdict": "holdout_failed",
                "reason": "insufficient_holdout_positive_or_negative_samples",
            }
            continue
        holdout_positive_min = min(positives)
        holdout_negative_max = max(negatives)
        positive_ok = holdout_positive_min >= strong_min
        negative_ok = holdout_negative_max <= none_max
        entry: Dict[str, Any] = {
            "holdout_positive_min": holdout_positive_min,
            "holdout_negative_max": holdout_negative_max,
            "frozen_strong_min": strong_min,
            "frozen_none_max": none_max,
        }
        if positive_ok and negative_ok:
            entry["verdict"] = "confirmed"
        else:
            entry["verdict"] = "holdout_failed"
            violations: List[str] = []
            if not positive_ok:
                violations.append(
                    f"holdout_positive_min={holdout_positive_min!r} < "
                    f"frozen_strong_min={strong_min!r}"
                )
            if not negative_ok:
                violations.append(
                    f"holdout_negative_max={holdout_negative_max!r} > "
                    f"frozen_none_max={none_max!r}"
                )
            entry["violations"] = violations
        table[axis] = entry

    return {
        "axes": table,
        "skipped_pairs": skipped,
        "not_comparable_pair_ids": sorted(not_comparable_pair_ids),
    }


def evaluate_comparison(
    reports: List[Dict[str, Any]],
    *,
    registry_path: Path = M3_REGISTRY_PATH,
    m1_registry_path: Path = M1_REGISTRY_PATH,
) -> Dict[str, Any]:
    """n>=1 の run report から校正 verdict（マージン表 + holdout ロック）を導出する。

    処理順（M3 実装 memo「## M3d」節）:
    1. pair ラベル（split/expected/kind/audio パス）の manifest 束縛検証
       （レビュー対応 2026-07-30 第 7 ラウンド: manifest の content pin =
       `manifest_sha256` に対して report 自身の pair dict が改ざんされていない
       ことを、registry pin とは独立に検証する。第 8 ラウンド: 各 report の
       pair_id 集合が manifest の pair_id 完全集合と一致することも同時に要求
       する — 部分集合（pair の削除）・過剰集合のどちらも fail-closed）
    2. report の `route` が実測制約を満たすかの検証（レビュー対応 2026-07-30
       第 9 ラウンド）: melodia 系経路は injected の有無に関わらず常に拒否、
       publishable（route_runner_injected: false）な report は crepe 系経路
       （extractor == "crepe"）限定——report を直接書き換えて run phase の
       ガードをすり抜ける経路を evaluate 側でも独立に塞ぐ
    3. registry sha256 pin の整合検証（report 間 + 現在ロードした registry）
    4. `m3_code_sha256`（第一者 M3 実装コードの content pin）の整合検証（report 間 +
       import 完了直後にモジュール定数として bind した `_M3_CODE_SHA256_AT_IMPORT`
       との一致。レビュー対応 2026-07-30 第 12 ラウンド、第 13 ラウンドで遅延
       再計算から import 時 bind へ変更）: registry sha256 pin と対称に、report
       が指す軌跡・holdout 判定がどのコード版で作られたものかを追跡する
    4.5. `separation_margin.min_same_minus_cross_margin`（`_validate_separation_
        margin_inherited_from_m1`。レビュー対応 2026-07-30 第 15 ラウンド）が
        非 bool の有限数値かつ 0 < margin <= 1 であり、かつ現在ロードした M1
        registry（`registry.yaml`）の `separation_gate.min_same_minus_cross_margin`
        と等値であることを検証する（run phase preflight とも対称に enforce）
    5. pair 単位 pin（`audio_sha256_a/b` / `route_provenance_a/b`）の欠落検証 —
       publishable report は全 pair（holdout ロック pair を除く）で必須
       （レビュー対応 2026-07-30 第 6 ラウンド: 全欠落は repeats 整合性チェック
       だけでは検出できないバイパスを閉じる）
    6. pin 値そのものの整形検証（レビュー対応 2026-07-30 第 8/9 ラウンド）: sha256
       系 pin（`manifest_sha256` / `m3_registry_sha256` / `m1_registry_sha256` /
       `m3_code_sha256` / `audio_sha256_a/b`）が 64 桁小文字 hex であること、
       `route_provenance_a/b` が mapping であり crepe 系経路の必須 pin
       （`extractor_weights_sha256` / `extractor_code_sha256`）も同様に 64 桁
       小文字 hex であることを検証する（injected/publishable を問わず適用）
    7. repeats（sequence hash / axes / audio_sha256 / observation_sha256 含む pair
       単位 pin）の bit 一致検証
    8. 全 report の `run_id` が存在・非空・相互に distinct であることの検証
       （レビュー対応 2026-07-30 第 4 ラウンド: 同一 run の二重指定を検出する）
    9. route_runner_injected な report があれば calibration verdict 発行を拒否
    10. repeats が n>=2 でなければ calibration verdict 発行を拒否（n=1 は「決定論
        repeats 未検証」であり校正証拠にしない）
    10.5. margin/holdout 集計より前に、reference report の各 pair の
        `comparison.axes` 値を検証する（`_validate_pair_axes_values`。レビュー
        対応 2026-07-30 第 14 ラウンド）: 既知軸名（contour/interval/rhythm）
        のみ・値は None または非 bool の有限数値かつ 0.0〜1.0 であることを要求し、
        違反は pair_id・軸名・値を理由に fail-closed（`ValueError`）
    10.6. 続けて `comparison.evidence` の列挙値検証 + `axes` との整合検証を行う
        （`_validate_evidence_values_and_axes_consistency`。レビュー対応
        2026-07-30 第 16 ラウンド）: `evidence` が既知 4 値（strong/weak/none/
        not_comparable）のいずれかであること、`not_comparable` の行は axes 全
        None であること、それ以外の行は（holdout redaction を除く）3 軸キーが
        存在すること（値 None は許容）かつ全軸 None のまま not_comparable 以外
        を名乗るのは不整合として fail-closed
    11. tuning split のみで軸別マージン表を算出（`separation_margin` 閾値）。
        tuning positive pair に 1 件でも `not_comparable` があれば freeze
        proposal を発行しない（レビュー対応 2026-07-30 第 5 ラウンド。negative の
        `not_comparable` は除外 + カウント記録のみで発行を妨げない）
    11.5. freeze proposal 発行前に、比較可能（`comparison` あり・`not_comparable`
        でない）な tuning pair 全ての `comparison.coverage` 4 フィールド
        （aligned_note_fraction_a/b・phrase_coverage_a/b）の完全性を検証する
        （`_validate_tuning_coverage_completeness`。レビュー対応 2026-07-30 第 15
        ラウンド）: 存在し非 bool の有限数値かつ 0.0〜1.0 であることを要求し、
        違反 pair_id が 1 件でもあれば freeze proposal と coverage floor 候補の
        双方を emit しない（理由 `coverage_incomplete(<pair_id>...)` を
        `freeze_proposal_rejected_reason` に記録。margin_table 自体は出す）
    12. holdout は `_holdout_unlocked` の全条件（status=frozen + 1 軸以上の整形済み
        凍結軸 + coverage.floor_status=frozen + coverage.floor 値が有効）を満たす
        まで開かない
    13. tuning positive pair の被覆分布から coverage floor 候補を emit
    14. holdout unlock 済み（registry 完全凍結）かつ report に holdout 行（比較結果
        つき）が存在する場合、凍結済み軸ごとに holdout の positive 最小類似・
        negative 最大類似を凍結値（`strong_min`/`none_max`）とだけ比較し
        `holdout_table` / `holdout_validation` を emit する（レビュー対応
        2026-07-30 第 10 ラウンド）。`holdout_failed` の軸が 1 つでもあれば
        `holdout_validation_status` に `calibration_not_confirmed_on_holdout` を
        記録し、校正確定を主張しない。uncalibrated 時（holdout ロック中）は
        この節を一切 emit しない（既存動作を変えない）。holdout split の pair
        に 1 件でも `not_comparable`（観測ゲート不通過等でスキップ）があれば、
        軸別 verdict が全て `confirmed` でも `holdout_validation_status` を
        `calibration_not_confirmed_on_holdout` に倒し、`holdout_validation_reasons`
        に `holdout_pair_not_measurable(<pair_id>)` を列挙する（レビュー対応
        2026-07-30 第 11 ラウンド・全対測定の要求）。holdout unlock 済みなのに
        holdout split の pair 行が `comparison` を持たず、かつ
        `holdout_locked_until_frozen` 状態でもない（欠落行）場合も同様に
        `calibration_not_confirmed_on_holdout` へ倒し、`holdout_validation_reasons`
        に `holdout_pair_missing_comparison(<pair_id>)` を列挙する（レビュー対応
        2026-07-30 第 12 ラウンド・holdout 行 comparison 必須化）
    """
    if not reports:
        raise ValueError("evaluate_comparison: reports が空 (fail-closed)")
    for idx, report in enumerate(reports):
        schema = report.get("schema_version")
        if schema != _EXPECTED_RUN_SCHEMA:
            raise ValueError(
                f"evaluate_comparison: reports[{idx}] の schema_version {schema!r} が "
                f"未知; 期待値は {_EXPECTED_RUN_SCHEMA!r} (fail-closed)"
            )
    _validate_pair_labels_bound_to_manifest(reports)
    _validate_route_runner_injected_field(reports)
    _validate_route_for_evaluate(reports)
    _require_pair_pins_present_for_publishable_reports(reports)
    _validate_pin_formats(reports)

    config, m3_registry_sha256 = load_m3_registry(registry_path)
    m1_mapping, m1_registry_sha256 = _load_m1_registry(m1_registry_path)
    _validate_registry_pins(
        reports,
        current_m3_sha256=m3_registry_sha256,
        current_m1_sha256=m1_registry_sha256,
    )
    _validate_m3_code_sha256(reports, current_m3_code_sha256=_M3_CODE_SHA256_AT_IMPORT)
    # レビュー対応 2026-07-30（第 15 ラウンド）: separation_margin の M0/M1 継承を
    # run phase preflight と対称に evaluate 側でも独立に enforce する（fail-closed）。
    _validate_separation_margin_inherited_from_m1(config, m1_mapping)

    _check_repeats_consistency(reports)
    _validate_run_ids_distinct(reports)

    reference_pairs = reports[0]["pairs"]

    # `_validate_route_runner_injected_field` が既に bool 型であることを確認済み。
    route_runner_injected_any = any(r["route_runner_injected"] for r in reports)
    repeats_verified = len(reports) >= 2

    verdict: Dict[str, Any] = {
        "schema_version": "m3-comparison-verdict/0.1",
        "recorded_utc": _utc_now(),
        "m3_registry_sha256": m3_registry_sha256,
        "repeats_count": len(reports),
        "repeats_verified": repeats_verified,
        "route_runner_injected": route_runner_injected_any,
        "evidence_thresholds_status": config.evidence_thresholds.status,
    }
    if repeats_verified:
        # `_check_repeats_consistency` が食い違いを検知したら既に例外で fail
        # しているため、ここに到達した時点で bit 一致は確認済み。
        verdict["repeats_consistent"] = True
    else:
        # n=1 は「決定論 repeats」を測っていない——true と書かない(正直な表現)。
        verdict["repeats_consistent"] = False

    if route_runner_injected_any:
        # フェイク抽出器の出力は「実測」ではない——他の全チェックが通っても
        # calibration verdict（マージン表・凍結提案）は発行しない（M2 `_require_
        # publishable_runs` と同じ規律）。
        verdict["calibration_verdict_status"] = "rejected_route_runner_injected"
        verdict["reason"] = (
            "1 件以上の report が route_runner 注入で作られている; フェイク抽出器の "
            "出力を calibration 証拠として publish しない (fail-closed)"
        )
        return verdict

    if not repeats_verified:
        # repeats n>=2 が事前登録の要求（設計 §6.2「repeats n≥2 + 系列 hash pin」）。
        # n=1 ではマージン表・凍結提案・coverage floor 候補を一切 emit しない。
        verdict["calibration_verdict_status"] = "rejected_insufficient_repeats"
        verdict["reason"] = f"insufficient_repeats(n={len(reports)}, required>=2)"
        return verdict

    holdout_locked = not _holdout_unlocked(config)
    verdict["holdout_locked_until_frozen"] = holdout_locked
    verdict["holdout_pair_ids_skipped"] = _holdout_pair_ids(reference_pairs) if holdout_locked else []

    # レビュー対応 2026-07-30(第 14 ラウンド): margin/holdout 集計(`_margin_table` /
    # 後段の `_holdout_validation_table`)が axes 値をそのまま消費する前に、値の
    # 型・値域を検証する(fail-closed)。`_check_repeats_consistency` が既に
    # comparison 全体の repeats 間一致を確認済みのため、reference report
    # (`reports[0]`)の pairs だけを検証すれば他 report も同じ値であることは
    # 保証されている。
    _validate_pair_axes_values(reference_pairs)
    # レビュー対応 2026-07-30(第 16 ラウンド): 上の型・値域検証に続けて、
    # `evidence` 列挙値の検証 + `evidence`/`axes` 整合検証を行う(fail-closed)。
    # 同じ reference-report-only の前提(repeats 間一致は既に保証済み)で足りる。
    _validate_evidence_values_and_axes_consistency(reference_pairs)

    margin = _margin_table(
        reference_pairs,
        split="tuning",
        min_margin=config.separation_margin.min_same_minus_cross_margin,
    )
    verdict["margin_table"] = margin["axes"]
    verdict["calibrated_axes"] = margin["calibrated_axes"]
    verdict["skipped_pairs"] = margin["skipped_pairs"]
    verdict["not_comparable_positive_count"] = margin["not_comparable_positive_count"]
    verdict["not_comparable_negative_count"] = margin["not_comparable_negative_count"]

    if margin["not_comparable_positive_count"] > 0:
        # tuning split の positive pair（expected="same"）が 1 件でも
        # not_comparable なら freeze proposal を発行しない（レビュー対応
        # 2026-07-30 第 5 ラウンド・正直会計）: 同一の証拠を出すべきペアで
        # 計器が沈黙したことは、校正候補の分布そのものが偏っている疑いを示す
        # ——negative の not_comparable（除外 + カウント記録のみ）とは非対称に
        # 扱う。
        verdict["freeze_proposal"] = {}
        verdict["freeze_proposal_rejected_reason"] = "rejected_positive_not_comparable"
        verdict["coverage_floor_candidate"] = _coverage_floor_candidate(reference_pairs)
    else:
        # レビュー対応 2026-07-30（第 15 ラウンド）: 上の not_comparable 会計とは
        # 独立に、比較可能な tuning pair 全ての `comparison.coverage` 4 フィールド
        # の完全性を freeze proposal 発行前に検証する。1 件でも違反があれば
        # freeze proposal と coverage floor 候補の**双方**を emit しない
        # （margin_table 自体は出す — 発行判断のみを止める）。
        coverage_violations = _validate_tuning_coverage_completeness(reference_pairs)
        if coverage_violations:
            verdict["freeze_proposal"] = {}
            verdict["freeze_proposal_rejected_reason"] = (
                f"coverage_incomplete({', '.join(coverage_violations)})"
            )
        else:
            verdict["freeze_proposal"] = {
                axis: {
                    "strong_min": margin["axes"][axis]["positive_min"],
                    "none_max": margin["axes"][axis]["negative_max"],
                }
                for axis in margin["calibrated_axes"]
            }
            verdict["coverage_floor_candidate"] = _coverage_floor_candidate(reference_pairs)

    if not holdout_locked:
        # レビュー対応 2026-07-30（第 10 ラウンド）: registry が完全凍結
        # （`_holdout_unlocked`）かつ report に holdout 行（比較結果つき）が
        # 存在する場合のみ holdout 検証を行う——holdout ロック中（uncalibrated）
        # は holdout pair に `comparison` 自体が無いため、この節は自然に
        # スキップされる（既存動作を変えない）。
        # 第 12 ラウンド: unlock 済みなのに `comparison` を持たない欠落行が 1 件
        # でもあれば（`holdout_has_comparison` が False でも）この節に入り、
        # 欠落を fail-closed で報告する。第 13 ラウンド: この呼び出しは
        # unlocked 評価（この `if not holdout_locked:` 節）からのみ行われる
        # ため `unlocked=True` を渡す——`holdout_locked_until_frozen` を申告
        # する行があっても免除せず、comparison 欠落を一律 missing 扱いにする
        # （unlocked 評価での locked 偽装行の拒否）。
        missing_comparison_pair_ids = _holdout_missing_comparison_pair_ids(
            reference_pairs, unlocked=True
        )
        holdout_has_comparison = any(
            pair["split"] == "holdout" and "comparison" in pair
            for pair in reference_pairs.values()
        )
        if holdout_has_comparison or missing_comparison_pair_ids:
            frozen_axes = config.evidence_thresholds.axes or {}
            holdout_result = _holdout_validation_table(reference_pairs, frozen_axes=frozen_axes)
            verdict["holdout_table"] = holdout_result["axes"]
            verdict["holdout_validation"] = {
                axis: entry["verdict"] for axis, entry in holdout_result["axes"].items()
            }
            if holdout_result["skipped_pairs"]:
                verdict["holdout_skipped_pairs"] = holdout_result["skipped_pairs"]
            not_comparable_pair_ids = holdout_result.get("not_comparable_pair_ids", [])
            axis_failed = any(
                verdict_value == "holdout_failed"
                for verdict_value in verdict["holdout_validation"].values()
            )
            reasons: List[str] = [
                f"holdout_pair_not_measurable({pid})" for pid in not_comparable_pair_ids
            ] + [
                f"holdout_pair_missing_comparison({pid})" for pid in missing_comparison_pair_ids
            ]
            if reasons or axis_failed:
                # レビュー対応 2026-07-30（第 11/12 ラウンド）: holdout split に
                # 1 件でも not_comparable pair または comparison 欠落行があれば、
                # 軸別 verdict が全て confirmed でも holdout_validation_status を
                # 「未確認」に倒す (fail-closed — 測れなかった/記録されなかった
                # pair の存在を confirmed の側に倒さない)。
                verdict["holdout_validation_status"] = "calibration_not_confirmed_on_holdout"
            else:
                verdict["holdout_validation_status"] = "confirmed"
            if reasons:
                verdict["holdout_validation_reasons"] = reasons

    return verdict


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="出力 JSON の書き出し先")
    parser.add_argument("--pairs", type=Path, help="pairs manifest（YAML）— run phase")
    parser.add_argument(
        "--route",
        default="crepe_direct",
        help=(
            "既定 route_runner が使う clear_lead 経路名（既定: crepe_direct）。"
            "実測 run（--pairs 経由・route_runner 非注入）は crepe 系経路限定"
            "（既定の crepe_direct はこの制約を満たす）。melodia 系経路は常に拒否"
        ),
    )
    parser.add_argument("--registry", type=Path, default=M3_REGISTRY_PATH)
    parser.add_argument("--m1-registry", type=Path, default=M1_REGISTRY_PATH)
    parser.add_argument(
        "--evaluate",
        nargs="+",
        type=Path,
        metavar="REPORT.json",
        help="run report(s) から校正 verdict を出す（未指定なら run phase）",
    )
    args = parser.parse_args()

    if args.evaluate:
        if args.pairs:
            raise SystemExit("--pairs は run phase 専用（evaluate は report 側を評価する）")
        protected = {Path(p).resolve() for p in args.evaluate}
        protected.add(Path(args.registry).resolve())
        protected.add(Path(args.m1_registry).resolve())
        if Path(args.out).resolve() in protected:
            raise SystemExit(
                f"--out {args.out} は評価入力（report / registry）と同じパスを指している; "
                "入力を verdict で上書きしない (fail-closed)"
            )
        reports = [_load_report(p) for p in args.evaluate]
        verdict = evaluate_comparison(
            reports, registry_path=args.registry, m1_registry_path=args.m1_registry
        )
        _atomic_write_text(args.out, json.dumps(verdict, indent=2, sort_keys=True))
        print(f"wrote verdict to {args.out}")
        print(f"  holdout_locked_until_frozen: {verdict.get('holdout_locked_until_frozen')}")
        print(f"  calibrated_axes: {verdict.get('calibrated_axes')}")
        return 0

    if not args.pairs:
        raise SystemExit("--pairs は run phase に必須（--evaluate 未指定時）")
    protected = {
        Path(args.pairs).resolve(),
        Path(args.registry).resolve(),
        Path(args.m1_registry).resolve(),
    }
    # manifest が指す音声入力（audio_a/audio_b）も protected-path に含める——
    # --out がそのいずれかと一致したら書き込み前に fail する。
    protected |= _manifest_audio_paths(args.pairs)
    if Path(args.out).resolve() in protected:
        raise SystemExit(
            f"--out {args.out} は入力（pairs manifest / registry / manifest の音声入力）と "
            "同じパスを指している; これらを run report で上書きしない (fail-closed)"
        )
    result = run_comparison(
        manifest_path=args.pairs,
        route_name=args.route,
        registry_path=args.registry,
        m1_registry_path=args.m1_registry,
    )
    _atomic_write_text(args.out, json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote run report to {args.out} ({len(result['pairs'])} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
