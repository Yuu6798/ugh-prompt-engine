"""Phase D1: C0 freeze manifest producer + armed freeze (設計正本 §3, §7, §18,
IMPLEMENTATION_MAP §6.3)。

## 授権境界

`dry_run()` は書込・secret 生成を一切行わない。`armed_freeze()` は三要素武装
（`approvals.check_armed`）が揃わなければ副作用ゼロで `AUTHORIZATION_REQUIRED`
を返す。テストは本モジュールの armed 経路を **常に** `tmp_path` 配下の
test-local な `approval_dir`/`secret_dir`/`campaigns_dir` + env に対してのみ
実行し、本リポジトリへの実 freeze は一切行わない（IMPLEMENTATION_MAP §0）。

## manifest_core_sha / manifest_sha の二層（PR レビュー第 2 巡採用）

`build_manifest()` が返す manifest（以下 "core manifest"）は `approvals` 節
（gate1/gate2 承認ファイルの content sha256）も `commitments` 節（secret の
sha256）も一切含まない。Gate 2 承認ファイルはこの core manifest の
`manifest_core_sha()` を束縛する。`armed_freeze()` はこの束縛を検証した
**後** に `approvals`/`commitments` 節を追加した "full manifest" を組み立て、
その sha を別途 `manifest_sha` として freeze event に記録する。これにより
「gate2 承認は gate2 自身の content hash を含む manifest を承認できない」
というハッシュ循環を避ける。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import secrets
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from voice_genesis.calibration import c0_validate, e_use_table, streams, vocab
from voice_genesis.calibration.approvals import (
    GATE_SHORT_NAME,
    ArmingDecision,
    ApprovalLoadResult,
    Gate,
    check_armed,
    default_approval_dir,
    load_all_approvals,
)
from voice_genesis.calibration.candidates import registry
from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.canonical import canonical_json
from voice_genesis.calibration.canonical import manifest_sha as _full_manifest_sha
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import ControlClass
from voice_genesis.calibration.fixtures.matrix import MatrixRow, _negative_applicable, build_matrix
from voice_genesis.calibration.provenance import Ledger
from voice_genesis.calibration.splitter import (
    RealizedSplitMap,
    RowInput,
    SwapRecord,
    realize_split,
    verify_split,
)
from voice_genesis.calibration.vocab import Split

#: `c0_freeze.py` から 2 階層上が repo root（`voice_genesis/calibration/c0_freeze.py`）。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: `VG_CAL_SECRET_DIR` の既定値（checkout 外。IMPLEMENTATION_MAP §6.2）。
DEFAULT_SECRET_DIR = Path.home() / ".vg_cal" / "secrets"
SECRET_DIR_ENV_VAR = "VG_CAL_SECRET_DIR"


def default_secret_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    override = source.get(SECRET_DIR_ENV_VAR)
    return Path(override) if override else DEFAULT_SECRET_DIR


def default_campaigns_dir(repo_root: Path | None = None) -> Path:
    root = repo_root if repo_root is not None else _REPO_ROOT
    return root / "voice_genesis" / "calibration" / "campaigns"


#: [UNDERSPEC-CAL-D10] E_use evidence table（設計正本 §10.2）は C0-frozen
#: input（Gate 1 の一部）として、repo 内・checkout 追跡下のこの既定 path から
#: 読む（承認ファイル自体とは異なり、これはユーザーが事前に記入・コミットする
#: データであり、dirty-tree 判定の対象内で構わない）。呼び出し側が明示的に
#: `e_use_table_path` を渡せば上書きできる。
DEFAULT_E_USE_TABLE_RELATIVE_PATH = "voice_genesis/calibration/config/e_use_table_v1.json"


def default_e_use_table_path(repo_root: Path | None = None) -> Path:
    root = repo_root if repo_root is not None else _REPO_ROOT
    return root / DEFAULT_E_USE_TABLE_RELATIVE_PATH


# ---------------------------------------------------------------------------
# frozen 宣言定数（[UNDERSPEC-CAL-D01]〜[UNDERSPEC-CAL-D05], README 台帳参照）
# ---------------------------------------------------------------------------

#: [UNDERSPEC-CAL-D01] 設計正本 §3.1 の「統合 measurement registry は不在 =
#: ABSENT。legacy 候補 path を別記」を機械的に一定文字列として転記した
#: （`tests/test_c0_validate.py::_MEASUREMENT_DIRECTORY_STATUS` と同一の
#: 慣例に揃える）。
_MEASUREMENT_DIRECTORY_STATUS = "ABSENT:legacy_path=voice_genesis/harness/measure_v3.py"

#: [UNDERSPEC-CAL-D02] `repo.url` の git remote 取得に失敗した場合のみ使う
#: 固定 fallback（CLAUDE.md に記載の canonical リポジトリ URL）。git remote が
#: 取得できる環境では常に実測値を優先する。
_FALLBACK_REPO_URL = "https://github.com/Yuu6798/ugh-prompt-engine"

#: [UNDERSPEC-CAL-D03] path+hash 系マップのカテゴリ分類規則。設計正本は
#: 4 カテゴリ（meter/generator/schema/test）の分類基準までは規定せず、
#: `c0_validate.py` も「合併集合の網羅性のみを要求し、カテゴリ単位の完全性
#: までは要求しない」（同モジュール docstring）。`tests/test_c0_validate.py`
#: の `_classify_path` と同一の最も単純な規則（`candidates/` 配下 → meter、
#: `fixtures/generators/` 配下 → generator、`tests/` 配下 → test、それ以外 →
#: schema）を producer 側にも採用し、テスト fixture と実 producer の分類基準
#: を一致させる。
def _classify_path(path: str) -> str:
    if path.startswith("voice_genesis/calibration/candidates/"):
        return "meter_paths_sha256"
    if path.startswith("voice_genesis/calibration/fixtures/generators/"):
        return "generator_paths_sha256"
    if path.startswith("voice_genesis/calibration/tests/"):
        return "test_paths_sha256"
    return "schema_paths_sha256"


def _path_hash_maps(root: Path) -> dict[str, dict[str, str]]:
    maps: dict[str, dict[str, str]] = {
        "meter_paths_sha256": {},
        "generator_paths_sha256": {},
        "schema_paths_sha256": {},
        "test_paths_sha256": {},
    }
    for rel_path in sorted(c0_validate.calibration_path_inventory(root)):
        sha = hashlib.sha256((root / rel_path).read_bytes()).hexdigest()
        maps[_classify_path(rel_path)][rel_path] = sha
    return maps


def _repo_url(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        url = result.stdout.strip()
        if result.returncode == 0 and url:
            return url
    except (OSError, subprocess.SubprocessError):
        pass
    return _FALLBACK_REPO_URL


def _dependency_version(name: str) -> str:
    return importlib_metadata.version(name)


def _pyworld_dependency_fields() -> tuple[str, str]:
    """pyworld guarded import（設計正本 §3.3 pyworld 特則）。未インストールでも
    campaign 全体は BLOCK しない（`c0_validate._check_pyworld` が D4C 候補のみ
    ineligible にする）。wheel hash はこの環境からは安価に取得できないため
    常に `ABSENT`（[UNDERSPEC-CAL-D04]。実 wheel hash の記録は運用時に
    別途行う）。"""
    try:
        version = importlib_metadata.version("pyworld")
    except importlib_metadata.PackageNotFoundError:
        return "ABSENT:not_installed", "ABSENT:not_installed"
    return version, "ABSENT:wheel_hash_not_recorded"


def _dependencies_section() -> dict[str, str]:
    pyworld_version, pyworld_wheel_hash = _pyworld_dependency_fields()
    return {
        "python_version": platform.python_version(),
        "numpy_version": _dependency_version("numpy"),
        "scipy_version": _dependency_version("scipy"),
        "librosa_version": _dependency_version("librosa"),
        "soundfile_version": _dependency_version("soundfile"),
        "pyworld_version": pyworld_version,
        "pyworld_wheel_hash": pyworld_wheel_hash,
    }


def _blas_fft_backend() -> str:
    try:
        import numpy as np

        cfg = np.show_config(mode="dicts")
        if isinstance(cfg, dict):
            build_deps = cfg.get("Build Dependencies")
            if isinstance(build_deps, dict):
                blas = build_deps.get("blas")
                if isinstance(blas, dict):
                    name = blas.get("name")
                    if isinstance(name, str) and name:
                        return name
    except Exception:  # noqa: BLE001 - best-effort RECORDED_OR_ABSENT probe
        pass
    return "ABSENT:cannot_determine_blas_fft_backend"


def _env_section() -> dict[str, object]:
    return {
        "container_image_digest": "ABSENT:no_container_used",
        "blas_fft_backend": _blas_fft_backend(),
        "os_kernel_cpu_arch": f"{platform.system()}-{platform.release()}-{platform.machine()}".lower(),
        "wheel_hashes": "ABSENT:wheel_hash_not_recorded",
        "world_build_flags": "ABSENT:wheel_used",
    }


#: [UNDERSPEC-CAL-D05] sample format policy。`fixtures/generators/common.py`
#: の実装が確定する事実（`quantize_pcm16` が最終 PCM int16 出力、全 generator
#: が 1 次元 = mono を返す）と、`candidates/impl/formant_burg.py` が使う
#: 唯一のリサンプラ実装を機械転記した。
_SAMPLE_FORMAT_SECTION: dict[str, object] = {
    "dtype": "int16",
    "channel_policy": "mono",
    "resampling_impl": "scipy.signal.resample_poly",
    "resampling_parameters": {"method": "rational_fraction_up_down_via_Fraction"},
}

_FALLBACK_POLICY = (
    "NO_DEGRADED_FALLBACK: missing/invalid triggers OUTPUT_MISSING per "
    "missing_failure_rule, never silent substitution"
)


def _meter_spec_for(candidates: Sequence[Candidate]) -> dict[str, object]:
    constructs = sorted({c.construct for c in candidates})
    units = sorted({c.unit for c in candidates})
    domains = sorted({c.domain for c in candidates})
    algo_families = sorted({c.algorithm_family for c in candidates})
    parameter_grid = {c.candidate_id: dict(c.parameters) for c in candidates}
    b0_ids = sorted(c.candidate_id for c in candidates if "-B0-" in c.candidate_id)
    baseline: object = (
        b0_ids if b0_ids else "ABSENT:no_B0_baseline_candidate_declared_for_this_meter"
    )
    missing_rules = sorted({c.missing_rule for c in candidates})
    return {
        "construct": constructs,
        "unit": units,
        "domain": domains,
        "algorithm_family": algo_families,
        "parameter_grid": parameter_grid,
        "baseline": baseline,
        "fallback": _FALLBACK_POLICY,
        "missing_failure_rule": missing_rules,
    }


def _meter_specs() -> dict[str, object]:
    return {
        meter.value: _meter_spec_for(registry.candidates_for_meter(meter))
        for meter in vocab.MeterId
        if registry.candidates_for_meter(meter)
    }


_KNOWN_TRUTH_FIELD: dict[str, str] = {
    FixtureFamily.F0_CONTROL.value: "f0_hz",
    FixtureFamily.FORMANT_GT.value: "pole_freqs_hz",
    FixtureFamily.TILT_GT.value: "slope_db_per_oct",
    FixtureFamily.APERIODICITY_GT.value: "injected_noise_fraction",
    FixtureFamily.RESONANCE_GT.value: "center_hz",
    FixtureFamily.TRANSITION_GT.value: "discontinuity_magnitude",
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: "delta",
}

_GENERATOR_MODULE_RELATIVE_PATH: dict[str, str] = {
    FixtureFamily.F0_CONTROL.value: "voice_genesis/calibration/fixtures/generators/f0_control.py",
    FixtureFamily.FORMANT_GT.value: "voice_genesis/calibration/fixtures/generators/formant.py",
    FixtureFamily.TILT_GT.value: "voice_genesis/calibration/fixtures/generators/tilt.py",
    FixtureFamily.APERIODICITY_GT.value: (
        "voice_genesis/calibration/fixtures/generators/aperiodicity.py"
    ),
    FixtureFamily.RESONANCE_GT.value: "voice_genesis/calibration/fixtures/generators/resonance.py",
    FixtureFamily.TRANSITION_GT.value: (
        "voice_genesis/calibration/fixtures/generators/transition.py"
    ),
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: (
        "voice_genesis/calibration/fixtures/generators/identity_sweep.py"
    ),
}

#: [UNDERSPEC-CAL-D06] `frozen_design.fixture_spec.<FAMILY>.confound_axes` /
#: `.boundary_probes` は、設計正本が C0 manifest 上のこの節に要求する粒度を
#: 明示しないため、matrix.py が既に厳密に持つ per-family targeted
#: interaction 実列挙（IMPLEMENTATION_MAP §2.7）を二重管理しない、より粗い
#: 「変動しうる primary/boundary 軸名」の宣言に留めた（非空 list 要求は満たす。
#: 値の意味論的相互検証は `c0_validate.py` docstring が明示する範囲外）。
_CONFOUND_AXES: tuple[str, ...] = ("f0_hz", "sr_hz", "gain_dbfs", "duration_s", "noise_snr_db", "context")
_BOUNDARY_PROBES: tuple[str, ...] = ("f0_hz", "sr_hz", "gain_dbfs", "duration_s", "noise_snr_db")


def _fixture_specs(root: Path) -> dict[str, object]:
    specs: dict[str, object] = {}
    for family in FixtureFamily:
        rel_path = _GENERATOR_MODULE_RELATIVE_PATH[family.value]
        generator_hash = hashlib.sha256((root / rel_path).read_bytes()).hexdigest()
        negative_controls = sorted(
            cc.value for cc in ControlClass if _negative_applicable(family, cc.value)
        )
        specs[family.value] = {
            "generator_version": "1",
            "generator_hash": generator_hash,
            "known_truth_field": _KNOWN_TRUTH_FIELD[family.value],
            "confound_axes": list(_CONFOUND_AXES),
            "boundary_probes": list(_BOUNDARY_PROBES),
            "negative_controls": negative_controls,
        }
    return specs


_SPLIT_SPEC: dict[str, str] = {
    "ratios": "50/25/25",
    "seed_scheme": "hkdf-sha256",
    "seal_commitment_rule": (
        "split_secret/render_root_secret generated at C0 freeze time; repo/manifest "
        "holds sha256 commitment only, plaintext never committed"
    ),
}

_SELECTION_SPEC: dict[str, str] = {
    "selection_rule": "per-family lexicographic: ABSOLUTE pool first, then DIRECTIONAL pool",
    "tie_rule": "candidate_id lexical order",
    "candidate_exhaustion_rule": "SELECTION_FAILED_CLOSED when eligible pool empty",
    "holdout_fail_outcome": "DIAGNOSTIC_ONLY",
}

_PROVENANCE_SPEC: dict[str, str] = {
    "schema_version": "vgcal-provenance/1",
    "artifact_layout": (
        "campaigns/<campaign_id>/{c0_manifest.json,realized_split.json,ledger.jsonl,"
        "events/*.json,renders/,measurements/}"
    ),
}

#: gate1 承認時に凍結される固定 stop rule 名（cost_caps 3 次元に 1:1 対応）。
DEFAULT_STOP_RULES: tuple[str, ...] = (
    "STOP_ON_COMPUTE_EXCEEDED",
    "STOP_ON_STORAGE_EXCEEDED",
    "STOP_ON_BUDGET_EXCEEDED",
)


def _declared_rng_ledger() -> list[dict[str, object]]:
    """[UNDERSPEC-CAL-D07] C0 manifest の `rng_ledger` は "declaration form"
    （`streams.expected_rng_stream_names()` の closed set、§3.3）であり、
    secret から実導出した OKM の digest ではない — `build_manifest()` は
    secret を一切受け取らない（dry-run/armed 双方から同一関数で呼ばれ、
    dry-run は secret を扱わない設計のため）。`public_seed_id` は
    `sha256("declared:"+stream_name)`（公開情報のみから導出される
    placeholder。secret 由来の実 OKM ではないため、armed freeze 後に
    `streams.RngLedger` で実際に導出される値とは一致しない — これは
    「このキャンペーンで実際にどの乱数が使われたか」の証跡ではなく、
    「9 stream が過不足なく宣言されている」ことの構造検証専用の値である）。
    """
    return [
        {
            "stream_name": name,
            "seeded": True,
            "public_seed_id": hashlib.sha256(f"declared:{name}".encode("utf-8")).hexdigest(),
        }
        for name in sorted(streams.expected_rng_stream_names())
    ]


def build_manifest(
    repo_root: Path,
    *,
    approvals: Mapping[Gate, ApprovalLoadResult],
    campaign_date_utc: str,
) -> dict[str, object]:
    """C0 manifest（"core" — `approvals`/`commitments` 節を含まない）を
    コードから生成する。secret は一切受け取らない・生成しない。

    `cost_caps`/`stop_rules`/`max_claim_scope` は Gate 1 承認が無ければ
    `"ABSENT:GATE1_NOT_APPROVED"`（REQUIRED_BLOCKING の型検査に違反し正しく
    BLOCK される。IMPLEMENTATION_MAP §6.3: 「validator will BLOCK — correct
    pre-approval」）。`max_claim_scope`（第 11 巡採用）は Gate 1 が承認する
    「このキャンペーンで claim してよい construct の上限範囲」を、
    `frozen_design`（**core payload の一部** — `approvals`/`commitments` の
    ような非-core 節ではなく、Gate 2 が署名する manifest_core_sha に含まれる
    設計値。結果を左右するため）へそのまま転記する。空/registry に存在しない
    construct を含むかどうかの意味論的検証は producer 側の追加ゲート
    （`_check_max_claim_scope()`、`dry_run()`/`armed_freeze()` が呼ぶ）が
    別途行う — `build_manifest()` 自体は Gate 1 record の値をそのまま転記
    するのみで検証しない（`cost_caps`/`e_use_table` と同じ責務分離）。
    """
    root = Path(repo_root)
    head_sha, dirty, _git_error = c0_validate._inspect_checkout_identity(root)

    gate1_result = approvals.get(Gate.GATE1_CAMPAIGN_EXECUTION)
    gate1_record = (
        gate1_result.record if gate1_result is not None and gate1_result.approved else None
    )
    if gate1_record is not None and gate1_record.cost_caps is not None:
        cost_caps_section: object = gate1_record.cost_caps.as_dict()
        stop_rules_section: object = list(DEFAULT_STOP_RULES)
        max_claim_scope_section: object = list(gate1_record.max_claim_scope)
    else:
        cost_caps_section = "ABSENT:GATE1_NOT_APPROVED"
        stop_rules_section = "ABSENT:GATE1_NOT_APPROVED"
        max_claim_scope_section = "ABSENT:GATE1_NOT_APPROVED"

    manifest: dict[str, object] = {
        "campaign_meta": {"campaign_date_utc": campaign_date_utc},
        "repo": {
            "url": _repo_url(root),
            "commit_sha": head_sha,
            "dirty_tree": dirty,
        },
        "measurement_directory_status": _MEASUREMENT_DIRECTORY_STATUS,
        "candidates": _path_hash_maps(root),
        "dependencies": _dependencies_section(),
        "sample_format": _SAMPLE_FORMAT_SECTION,
        "frozen_design": {
            "claim_critical_set": sorted(m.value for m in vocab.CLAIM_CRITICAL_SET),
            "meter_specs": _meter_specs(),
            "fixture_spec": _fixture_specs(root),
            "split_spec": _SPLIT_SPEC,
            "selection_spec": _SELECTION_SPEC,
            "provenance_spec": _PROVENANCE_SPEC,
            "cost_caps": cost_caps_section,
            "stop_rules": stop_rules_section,
            "max_claim_scope": max_claim_scope_section,
        },
        "independence_ledger": {
            c.candidate_id: c.independence_tier.value for c in registry.ALL_CANDIDATES
        },
        "rng_ledger": _declared_rng_ledger(),
        "env": _env_section(),
    }
    return manifest


#: `build_manifest()` の core 出力には決して現れないが、armed freeze が
#: full/frozen manifest へ後付けする節（PR レビュー第 4/5 巡:
#: manifest_core_sha の定義精緻化 + 承認の一回性）。`core_payload()` は
#: これらを取り除いた「事前承認 payload」を返す — Gate 2 承認はこの payload
#: の sha (`manifest_core_sha`) を束縛する。`authorization_nonce` も
#: `dry_run()` が呼び出しごとに新規発行する乱数でありコード自身とは無関係な
#: ため、core payload から除く（さもなくば `manifest_core_sha` が dry-run の
#: 呼び出しごとに変わってしまい determinism が壊れる）。`approvals`/
#: `commitments`/`realized_split`/`realized_split_sha`/`campaign_id` はいずれも
#: 承認そのものや armed freeze 時点で初めて確定する secret 由来 bookkeeping
#: であり、Gate 2 が署名する core manifest には含めない。
#:
#: `frozen_inputs`（E_use table の sha256 pin。Part A/D1b）は **第 12 巡採用で
#: core へ移した**（設計変更）: 従来は「armed freeze 時点で初めて確定する
#: freeze-time bookkeeping」として非-core 扱いだったが、E_use table の内容は
#: 「結果を左右する」frozen design 相当の入力であり、その pin を Gate 2 承認
#: の外に置くと、dry-run で承認した E_use table と armed freeze 時点で実際に
#: 使われる E_use table が食い違っていても検出できない（表を無断で差し替え
#: られても manifest_core_sha が変わらず Gate 2 承認をすり抜ける）。`dry_run()`
#: が load/validate/hash した sha256 を `frozen_inputs.e_use_table_sha256` として
#: manifest（`build_manifest()` の core 出力そのものではなく、`dry_run()`/
#: `armed_freeze()` がそこへ後付けする 1 キー）へ入れ、その状態で
#: `manifest_core_sha()` を計算するため、dry-run と armed の間で表が変われば
#: core_sha が不一致になり Gate 2 の束縛が無効化される（`campaign_id`/
#: `authorization_nonce` とは異なり、`frozen_inputs` はこの意味で `cost_caps`/
#: `max_claim_scope` などの他の frozen-design 入力と同じ扱いになった）。
_CORE_ONLY_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        "approvals",
        "commitments",
        "realized_split",
        "realized_split_sha",
        "campaign_id",
        "authorization_nonce",
    }
)


def core_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    """`manifest`（core 形式・frozen/full 形式のいずれでも可）から
    `_CORE_ONLY_EXCLUDED_KEYS` を取り除いた「事前承認 payload」を返す。
    `build_manifest()` の出力はそもそもこれらの節を持たないため、core
    manifest に適用しても no-op（恒等写像）。"""
    return {k: v for k, v in manifest.items() if k not in _CORE_ONLY_EXCLUDED_KEYS}


def manifest_core_sha(manifest: Mapping[str, object]) -> str:
    """`core_payload(manifest)` の正規形 sha。Gate 2 承認はこの値を束縛する。
    `manifest` は core 形式・frozen/full 形式のいずれで渡しても同じ値を返す
    （`core_payload()` が余剰節を剥がしてから hash するため）。"""
    return _full_manifest_sha(core_payload(manifest))


def campaign_id_for(manifest: Mapping[str, object]) -> str:
    """`RUN10-CAL-<YYYYMMDD>-<manifest_core_sha[:8]>`（IMPLEMENTATION_MAP §6.2）。"""
    meta = manifest.get("campaign_meta")
    if not isinstance(meta, Mapping) or not isinstance(meta.get("campaign_date_utc"), str):
        raise ValueError("campaign_id_for: manifest.campaign_meta.campaign_date_utc missing")
    date_str = meta["campaign_date_utc"]
    yyyymmdd = date_str.replace("-", "")
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise ValueError(f"campaign_id_for: campaign_date_utc must be YYYY-MM-DD, got {date_str!r}")
    sha = manifest_core_sha(manifest)
    return f"RUN10-CAL-{yyyymmdd}-{sha[:8]}"


def _attach_freeze_extras(
    core_manifest: Mapping[str, object],
    *,
    campaign_id: str,
    authorization_nonce: str,
    approval_digests: Mapping[str, str],
    commitments: Mapping[str, str],
    realized_split: Mapping[str, object],
    realized_split_sha: str,
) -> dict[str, object]:
    """core manifest から frozen/full manifest を組み立てる。設計正本 §7
    「正本は C0 manifest に列挙した実現済み row→split 表」に従い、
    `realized_split`（row_id→split の実現表そのもの）を manifest 本体へ
    インラインで含める（`realized_split.json` は同内容の便宜コピー）。
    ここで追加される節は `core_payload()`/`_CORE_ONLY_EXCLUDED_KEYS` と
    1 対 1 で対応する。`authorization_nonce` は Gate 2 承認ファイルに記録
    された値（`check_armed()` が既に Gate 1 との一致を検証済み）で、
    `armed_freeze()` の一回性チェックが後続の freeze 試行を拒否するために
    使う（PR レビュー第 5 巡）。

    `frozen_inputs`（E_use evidence table の sha256 pin,
    `frozen_inputs.e_use_table_sha256`）はもはやここでは付加しない — 第 12
    巡採用で core payload へ移ったため、呼び出し側（`armed_freeze()`）が
    `core_manifest` 自体へ（`manifest_core_sha()` を計算する前に）既に
    埋め込んでいる前提であり、`core_manifest` をコピーする `full = dict(...)`
    がそのまま引き継ぐ。"""
    full = dict(core_manifest)
    full["campaign_id"] = campaign_id
    full["authorization_nonce"] = authorization_nonce
    full["approvals"] = dict(approval_digests)
    full["commitments"] = dict(commitments)
    full["realized_split"] = dict(realized_split)
    full["realized_split_sha"] = realized_split_sha
    return full


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _gate1_e_use_bound_accepted(approvals: Mapping[Gate, ApprovalLoadResult]) -> bool:
    """Gate 1 が承認済みで `e_use_bound_accepted=true` を宣言しているか。
    未承認/未宣言はいずれも `False`（fail-closed: 明示受容がなければ
    `USER_ACCEPTED_USE_BOUND` 行は E_use 検証を通過しない）。"""
    result = approvals.get(Gate.GATE1_CAMPAIGN_EXECUTION)
    if result is None or not result.approved or result.record is None:
        return False
    return bool(result.record.e_use_bound_accepted)


def _parse_e_use_table_bytes(path: Path, data: bytes) -> list[e_use_table.EUseEvidenceRow]:
    """`e_use_table.load_e_use_table()` と同一の shape 検証・エラー整形を、
    既に読み込み済みの `data`（呼び出し側の 1 回きりの `path.read_bytes()`）に
    対して行う（bug fix P2 #1: `_check_e_use_table()` が独自にファイルを再度
    読まないようにするための下請け — `load_e_use_table(path)` を直接呼ぶと
    その内部で `path.read_text()` が再度走ってしまう）。`json.JSONDecodeError`/
    `UnicodeDecodeError` はいずれも `ValueError` のサブクラスであり、意図的に
    ここでは捕まえず呼び出し側 `_check_e_use_table()` の
    `except (ValueError, KeyError, TypeError)` へそのまま伝播させる
    （`load_e_use_table()` 自身が decode エラーを個別に捕まえないのと同じ
    挙動に揃える）。"""
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: must contain a JSON array of row objects")
    rows: list[e_use_table.EUseEvidenceRow] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}[{i}]: row must be a JSON object")
        try:
            rows.append(e_use_table.row_from_dict(entry))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
    return rows


def _check_e_use_table(
    path: Path, *, gate1_e_use_bound_accepted: bool
) -> tuple[list[str], bytes | None]:
    """E_use evidence table（設計正本 §10.2, `[UNDERSPEC-CAL-D10]`）の load +
    検証を dry-run/armed 双方が共有する。違反は `"e_use_table: <理由>"` 形式で
    返す（`c0_validate` 側の他の違反文字列と同じ prefix 慣例に揃え、
    `BLOCKED_C0_MANIFEST_INCOMPLETE` の一部として合流させる — この表自体は
    C0 manifest の 1 キーではないため `c0_validate.py` は関知しない、
    producer 側専用の追加ゲート）。読込失敗（ファイル不在・壊れた JSON・行の
    shape 違反）と、読込は成功したが横断制約違反（`e_use_table.
    validate_e_use_table`）の両方をここで一本化する。

    戻り値は `(violations, table_bytes)`。`table_bytes` は `path` から実際に
    読み込めた生バイト列で、読込+パースが成功した場合は常に非 None
    （`validate_e_use_table` が横断制約違反を返した場合でも非 None のまま —
    ファイル自体は読めているため）。`None` になるのは読込・パース自体が
    失敗した場合のみで、そのときは `violations` が必ず非空になる。bug fix P2
    #1: `armed_freeze()` はこの `table_bytes` を sha256 pin/staging コピーの
    双方にそのまま再利用し、`path.read_bytes()` を再度呼ばない — 検証に使った
    内容と実際に確定される内容が別読み取りになる TOCTOU（読込と読込の間に
    ファイルが差し替えられても検出できない）を構造的に排除する。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"e_use_table: cannot read {path}: {exc}"], None
    try:
        rows = _parse_e_use_table_bytes(path, data)
    except (ValueError, KeyError, TypeError) as exc:
        return [f"e_use_table: {path}: {exc}"], None
    violations = e_use_table.validate_e_use_table(
        rows, gate1_e_use_bound_accepted=gate1_e_use_bound_accepted
    )
    return [f"e_use_table: {v}" for v in violations], data


def _merge_e_use_table_violations(
    validation: c0_validate.C0ValidationResult, extra: Sequence[str]
) -> c0_validate.C0ValidationResult:
    """`extra`（producer 側の追加ゲートが返した prefixed violation 文字列群 —
    `_check_e_use_table()` の `"e_use_table: ..."` に加え、第 11 巡採用の
    `_check_max_claim_scope()` の `"max_claim_scope: ..."` もここへ合流する。
    名前は元の e_use_table 専用実装の名残だが、実装自体は既に汎用: manifest
    の 1 キーだけでは表現しきれない producer 側の横断検証すべてに使う）を
    既存の `C0ValidationResult` へ合流させる。空なら no-op（同一オブジェクトを
    返さない点のみ異なる — 呼び出し側は常にこの戻り値を使う）。"""
    if not extra:
        return validation
    blocked = validation.blocked_codes
    if vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE not in blocked:
        blocked = blocked + (vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE,)
    return replace(
        validation,
        blocked_codes=blocked,
        missing_required_keys=validation.missing_required_keys + tuple(extra),
    )


def _check_max_claim_scope(scope: object) -> list[str]:
    """Gate 1 `max_claim_scope`（設計正本 §18: このキャンペーンで claim して
    よい construct の上限範囲）の凍結時検証（第 11 巡採用）。`build_manifest()`
    は Gate 1 record の値をそのまま `frozen_design.max_claim_scope` へ転記
    するだけで検証しないため（`cost_caps`/`e_use_table` と同じ責務分離）、
    この producer 側の追加ゲートが意味論的検証を担う。

    `scope` が Gate 1 未承認時の `"ABSENT:GATE1_NOT_APPROVED"` センチネル
    文字列の場合は何もしない（空 list を返す）— その状態は
    `frozen_design.cost_caps`/`stop_rules` が REQUIRED_BLOCKING の型検査
    経由で既に BLOCK するため、ここで重複報告しない。

    `scope` が承認された Gate 1 の実 tuple（`ApprovalRecord.max_claim_scope`
    由来、または manifest から読み戻した list）の場合:

    - 空（`len(scope) == 0`）なら BLOCK（「何を claim してよいか一つも
      宣言されていない」承認は無限定の承認と区別がつかず、fail-closed で
      拒否する）。
    - `candidates.registry.ALL_CANDIDATES` が宣言するどの候補の
      `construct` にも一致しない id を含むなら、その id ごとに個別の
      violation を BLOCK（typo や廃止済み construct が承認スコープへ
      紛れ込むのを防ぐ）。

    違反は `e_use_table` と同じ prefix 慣例で `"max_claim_scope: <理由>"`
    形式で返す（`_merge_e_use_table_violations()` で
    `BLOCKED_C0_MANIFEST_INCOMPLETE` として合流させる）。
    """
    if isinstance(scope, str):
        # "ABSENT:GATE1_NOT_APPROVED" sentinel (or any other non-list string a
        # caller might pass defensively): already covered elsewhere, skip.
        return []
    if not isinstance(scope, (list, tuple)):
        return [f"max_claim_scope: must be a list, got {type(scope).__name__}"]
    if len(scope) == 0:
        return ["max_claim_scope: must be non-empty (Gate 1 approval names no construct)"]
    known_constructs = {c.construct for c in registry.ALL_CANDIDATES}
    violations: list[str] = []
    for construct_id in sorted({str(c) for c in scope} - known_constructs):
        violations.append(
            f"max_claim_scope: {construct_id!r} is not a construct declared by any "
            "candidate in the registry"
        )
    return violations


@dataclass(frozen=True)
class FreezeReport:
    """`dry_run()` の戻り値。書込・secret 生成は一切伴わない。

    `authorization_nonce`（PR レビュー第 5 巡: 承認の一回性）は呼び出しごとに
    新規発行する `secrets.token_hex(16)`（manifest 内容とは無関係な乱数）。
    ユーザーはこの値を Gate 1/Gate 2 承認ファイルの `authorization_nonce` へ
    転記する（両者が一致しなければ `check_armed(GATE2)` が拒否する）。
    """

    manifest: Mapping[str, object]
    manifest_core_sha: str
    campaign_id: str
    authorization_nonce: str
    validation: c0_validate.C0ValidationResult
    gate2_arming: ArmingDecision
    approvals: Mapping[Gate, ApprovalLoadResult]


def dry_run(
    repo_root: Path,
    approval_dir: Path,
    env: Mapping[str, str],
    *,
    cli_armed: bool = False,
    e_use_table_path: Path | None = None,
) -> FreezeReport:
    """manifest を生成・検証して報告するだけ（書込なし・secret なし）。

    承認ファイルは本関数内で `load_all_approvals()` を **1 回だけ** 呼び、
    その結果を `check_armed()` へ `preloaded=` として渡す（PR レビュー第 6 巡
    #5: 同一承認ファイルを二重に読まない）。
    """
    root = Path(repo_root)
    all_approvals = load_all_approvals(approval_dir, repo_root=root)
    manifest = build_manifest(root, approvals=all_approvals, campaign_date_utc=_today_utc())

    # 第 12 巡採用: E_use table の sha256 pin (`frozen_inputs.e_use_table_sha256`)
    # は now part of the *core* payload -- it must be read/hashed and attached
    # to `manifest` *before* `manifest_core_sha()`/`campaign_id_for()` run
    # below, so that a table swapped in between this dry-run and a later
    # `armed_freeze()` call changes `manifest_core_sha` (and therefore
    # invalidates whatever `manifest_core_sha` a stale Gate 2 approval pins).
    table_path = (
        e_use_table_path if e_use_table_path is not None else default_e_use_table_path(root)
    )
    e_use_violations, e_use_table_bytes = _check_e_use_table(
        table_path, gate1_e_use_bound_accepted=_gate1_e_use_bound_accepted(all_approvals)
    )
    e_use_table_sha256 = (
        hashlib.sha256(e_use_table_bytes).hexdigest() if e_use_table_bytes is not None else None
    )
    manifest["frozen_inputs"] = {
        "e_use_table_sha256": e_use_table_sha256 or "ABSENT:e_use_table_invalid"
    }

    core_sha = manifest_core_sha(manifest)
    campaign_id = campaign_id_for(manifest)
    validation = c0_validate.validate_c0_manifest(manifest)
    validation = _merge_e_use_table_violations(validation, e_use_violations)

    frozen_design = manifest.get("frozen_design")
    max_claim_scope_value = (
        frozen_design.get("max_claim_scope") if isinstance(frozen_design, Mapping) else None
    )
    validation = _merge_e_use_table_violations(
        validation, _check_max_claim_scope(max_claim_scope_value)
    )

    gate2_arming = check_armed(
        Gate.GATE2_C0_FREEZE, cli_armed, env, approval_dir, repo_root=root,
        preloaded=all_approvals,
    )
    return FreezeReport(
        manifest=manifest,
        manifest_core_sha=core_sha,
        campaign_id=campaign_id,
        authorization_nonce=secrets.token_hex(16),
        validation=validation,
        gate2_arming=gate2_arming,
        approvals=all_approvals,
    )


#: `splitter.realize_split` の stratum 化因子。[UNDERSPEC-CAL-D08] 設計正本
#: §7 は「stratum 因子を C0 で明示列挙」とのみ述べ具体軸は規定しないため、
#: `splitter._COVERAGE_AXES` のうち row 単位で常に定義される 2 軸
#: （`truth_level`/`boundary_class`）を採用した（`generator_impl` は
#: FORMANT_GT 以外では常に `None` — 定数軸を stratum に含めても意味が薄い
#: ため除外）。
STRATUM_FACTOR_NAMES: tuple[str, ...] = ("truth_level", "boundary_class")


def _row_inputs_for_split(
    matrix_rows: Sequence[MatrixRow], stratum_factor_names: Sequence[str]
) -> list[RowInput]:
    """`provenance.Ledger.check_leakage` の `canonical_split_inputs` 構築と
    同一の規約（`truth_level`→`row.block`、`boundary_class`/`domain`→
    `matrix_row.domain.value`）で `RowInput` を組み立てる。D2 の leakage 検査
    が独立に再構築する canonical row と一致しなければならないため。"""
    out: list[RowInput] = []
    for mrow in matrix_rows:
        fr = mrow.row
        stratum: dict[str, object] = {}
        for name in stratum_factor_names:
            if name == "truth_level":
                stratum[name] = fr.block
            elif name in ("boundary_class", "domain"):
                stratum[name] = mrow.domain.value
            elif name == "generator_impl":
                stratum[name] = fr.generator_impl
            else:
                stratum[name] = getattr(fr, name, None)
        out.append(
            RowInput(
                row_id=mrow.row_id,
                family=fr.family,
                stratum=stratum,
                truth_level=fr.block,
                generator_impl=fr.generator_impl,
                boundary_class=mrow.domain.value,
            )
        )
    return out


def _realized_split_to_dict(realized: RealizedSplitMap) -> dict[str, object]:
    return {
        "stratum_factor_names": list(realized.stratum_factor_names),
        "assignment": {rid: split.value for rid, split in sorted(realized.assignment.items())},
        "swaps": [
            {
                "row_id": s.row_id,
                "from_split": s.from_split.value,
                "to_split": s.to_split.value,
                "reason": s.reason,
                "hmac_key": s.hmac_key,
                "detail": s.detail,
            }
            for s in realized.swaps
        ],
        "realized_sha": realized.realized_sha,
    }


def _realized_split_from_dict(d: Mapping[str, Any]) -> RealizedSplitMap:
    assignment = {str(rid): Split(val) for rid, val in d["assignment"].items()}
    swaps = tuple(
        SwapRecord(
            row_id=s["row_id"],
            from_split=Split(s["from_split"]),
            to_split=Split(s["to_split"]),
            reason=s["reason"],
            hmac_key=s["hmac_key"],
            detail=s["detail"],
        )
        for s in d["swaps"]
    )
    return RealizedSplitMap(
        stratum_factor_names=tuple(d["stratum_factor_names"]),
        assignment=assignment,
        swaps=swaps,
        realized_sha=d["realized_sha"],
    )


def _write_secret_file(path: Path, data: bytes) -> None:
    """PR レビュー第 6 巡 #6: `os.write()` は POSIX 上、要求バイト数より少なく
    書いて正常終了しうる（短い書込み。パイプ/一部ファイルシステムで実際に
    起こりうる）。secret ファイルで静かに切り詰められた内容を残すのは
    fail-closed の逆なので、書けたバイト数を検査し不足があれば例外にする
    （呼び出し側の armed_freeze はこれを OSError として捕捉し、staging 全体を
    ロールバックする）。"""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(
                f"short write to {path}: wrote {written} of {len(data)} bytes"
            )
    finally:
        os.close(fd)


def _rmtree_if_exists(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


#: 公開途中を示すマーカーファイル名。secret dir 側の最初の `os.replace` 直前に
#: `secret_staging` へ書き、rename と共に `secret_final` へ移る。campaign 側の
#: `os.replace` が成功した後に削除する（PR レビュー第 3 巡採用）。PR レビュー
#: 第 4 巡: このマーカー自体はもはや「削除してよいか」の判定根拠ではない
#: （`_publish_lock` を保持できた時点で、生きた公開はあり得ないため）。
#: 対応する campaign dir が無い状態でこのマーカーを持つ secret dir が見つかれば
#: それは stale（クラッシュ等で中断した公開の残骸）であり削除してよい。
#: 対応する campaign dir が既にある状態でこのマーカーが残っていれば、それは
#: 「両根とも公開済みだがマーカー削除だけが未了だった」正常系であり、
#: マーカーのみを取り除く（`detect_orphans()` 参照）。
_PUBLISHING_MARKER_NAME = ".publishing"

#: 二根公開 (`armed_freeze` の os.replace 2 回) と `detect_orphans()` が共有する
#: 排他ロックのファイル名。**同一名で 2 か所** に置く（bug fix P2 #3）:
#: `campaigns_dir/.publish.lock` が **authoritative**（nonce 一意性の判定は
#: 必ずこちらで行う — `campaigns_dir` は複数プロセスが必ず共有する campaign
#: registry の実体であるのに対し、`secret_dir` は呼び出し側が任意に選べる値
#: であり、同じ `campaigns_dir` に対して異なる `secret_dir` を渡す 2 プロセスが
#: あれば互いにロックが無関係になってしまう）。`secret_dir/.publish.lock` は
#: 二次ロックとして残す（secret dir 自体の直列化には引き続き有効）。両ロックとも
#: 対象ディレクトリは常に存在すると仮定できないため `mkdir(parents=True,
#: exist_ok=True)` してから開く。
_PUBLISH_LOCK_NAME = ".publish.lock"


@contextlib.contextmanager
def _publish_lock(lock_dir: Path, *, blocking: bool = True) -> Iterator[bool]:
    """`lock_dir/.publish.lock` 上の排他ロックを取る（`lock_dir` は
    `campaigns_dir`（authoritative）・`secret_dir`（secondary）のいずれでも
    同じロジックで使う汎用ヘルパー — bug fix P2 #3）。`yield` される bool は
    ロックを実際に取得できたか（`blocking=True` では OS レベルの異常が無い限り
    常に `True` — 取得できるまで待つため）。`blocking=False`
    （`detect_orphans()` が使う）はロック競合時に即座に `False` を yield して
    何もせず戻る（PR レビュー第 4 巡: 生きた公開処理と競合させない）。
    """
    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / _PUBLISH_LOCK_NAME
    with open(lock_path, "a+", encoding="utf-8") as f:
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(f.fileno(), flags)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


#: `render_root_secret.bin`/`split_secret.bin` の期待バイト長（`secrets.token_bytes(32)`）。
_SECRET_BYTE_LENGTH = 32


def _readback_verify(
    campaign_staging: Path,
    secret_staging: Path,
    row_inputs: Sequence[RowInput],
    split_secret_expected: bytes,
    render_root_secret_expected: bytes,
    commitments: Mapping[str, str],
    *,
    gate1_e_use_bound_accepted: bool,
) -> tuple[bool, str]:
    """staging へ書いた内容を読み戻して独立に検証する（PR レビュー第 6 巡 #6:
    従来は `render_root_secret.bin` を一切読み戻さずに公開しており、部分書込み
    やファイル破損があっても検出できなかった — `split_secret.bin` 同様、
    長さ・生成値との一致・commitment ハッシュとの一致を検査してから
    `armed_freeze()` に公開して良いと伝える）。

    第 11 巡採用: `e_use_table.json`（staging へコピーされた E_use evidence
    table）も同様に読み戻す — sha256 が同じ staged manifest の
    `frozen_inputs.e_use_table_sha256` pin と一致すること、かつ再パース後の
    行が `e_use_table.validate_e_use_table()` を再び通ること（無違反）の
    両方を検査する。staging 書込み後（`_readback_verify()` 呼び出し前）に
    `e_use_table.json` が別プロセス/破損等で改変された場合、それを検出して
    公開を拒否する（この検証をすり抜けると、pin された sha256 と実際に
    campaign dir へ公開される内容が食い違ったまま freeze が完了してしまう）。
    """
    try:
        manifest_readback = json.loads(
            (campaign_staging / "c0_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"cannot read back c0_manifest.json: {exc}"
    validation = c0_validate.validate_c0_manifest(manifest_readback)
    if validation.is_blocked:
        return False, f"read-back manifest failed validation: {validation.blocked_codes}"

    frozen_inputs_readback = manifest_readback.get("frozen_inputs")
    expected_e_use_table_sha256 = (
        frozen_inputs_readback.get("e_use_table_sha256")
        if isinstance(frozen_inputs_readback, Mapping)
        else None
    )
    e_use_table_path_readback = campaign_staging / "e_use_table.json"
    try:
        e_use_table_bytes_readback = e_use_table_path_readback.read_bytes()
    except OSError as exc:
        return False, f"cannot read back e_use_table.json: {exc}"
    actual_e_use_table_sha256 = hashlib.sha256(e_use_table_bytes_readback).hexdigest()
    if actual_e_use_table_sha256 != expected_e_use_table_sha256:
        return False, (
            "read-back e_use_table.json sha256 "
            f"({actual_e_use_table_sha256!r}) does not match staged manifest's "
            f"frozen_inputs.e_use_table_sha256 pin ({expected_e_use_table_sha256!r})"
        )
    try:
        e_use_rows_readback = _parse_e_use_table_bytes(
            e_use_table_path_readback, e_use_table_bytes_readback
        )
    except (ValueError, KeyError, TypeError) as exc:
        return False, f"read-back e_use_table.json failed to parse: {exc}"
    e_use_violations_readback = e_use_table.validate_e_use_table(
        e_use_rows_readback, gate1_e_use_bound_accepted=gate1_e_use_bound_accepted
    )
    if e_use_violations_readback:
        return False, f"read-back e_use_table.json failed validation: {e_use_violations_readback}"

    try:
        split_raw = json.loads((campaign_staging / "realized_split.json").read_text(encoding="utf-8"))
        realized_readback = _realized_split_from_dict(split_raw)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return False, f"cannot read back realized_split.json: {exc}"

    try:
        secret_bytes = (secret_staging / "split_secret.bin").read_bytes()
    except OSError as exc:
        return False, f"cannot read back split_secret.bin: {exc}"
    if len(secret_bytes) != _SECRET_BYTE_LENGTH:
        return False, (
            f"read-back split_secret.bin has wrong length: "
            f"{len(secret_bytes)} != {_SECRET_BYTE_LENGTH}"
        )
    if secret_bytes != split_secret_expected:
        return False, "read-back split_secret.bin does not match generated secret"
    if hashlib.sha256(secret_bytes).hexdigest() != commitments.get("split_secret_sha256"):
        return False, "read-back split_secret.bin does not match its sha256 commitment"

    try:
        render_secret_bytes = (secret_staging / "render_root_secret.bin").read_bytes()
    except OSError as exc:
        return False, f"cannot read back render_root_secret.bin: {exc}"
    if len(render_secret_bytes) != _SECRET_BYTE_LENGTH:
        return False, (
            f"read-back render_root_secret.bin has wrong length: "
            f"{len(render_secret_bytes)} != {_SECRET_BYTE_LENGTH}"
        )
    if render_secret_bytes != render_root_secret_expected:
        return False, "read-back render_root_secret.bin does not match generated secret"
    if hashlib.sha256(render_secret_bytes).hexdigest() != commitments.get("render_root_secret_sha256"):
        return False, "read-back render_root_secret.bin does not match its sha256 commitment"

    if not verify_split(row_inputs, secret_bytes, realized_readback):
        return False, "verify_split failed on read-back realized_split.json"

    chain = Ledger(campaign_staging / "ledger.jsonl").verify_chain()
    if not chain.ok:
        return False, f"ledger chain verification failed: {chain.detail}"

    return True, "ok"


class FreezeOutcome(str, Enum):
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    MANIFEST_CORE_SHA_MISMATCH = "MANIFEST_CORE_SHA_MISMATCH"
    NONCE_ALREADY_USED = "NONCE_ALREADY_USED"
    NONCE_REGISTRY_UNINSPECTABLE = "NONCE_REGISTRY_UNINSPECTABLE"
    VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    PUBLISHED = "PUBLISHED"


@dataclass(frozen=True)
class NonceRegistryScan:
    """`_find_existing_nonce_usage()` の走査結果（第 12 巡採用）。

    `existing_campaign_id` が非 None なら、同じ `authorization_nonce` を
    使った公開済み campaign が見つかったことを示す。

    `uninspectable_dirs`（第 12 巡採用）が非空なら、`campaigns_dir` 配下に
    manifest が欠落・読取不能・JSON 不正・`authorization_nonce` 欄欠落の
    campaign dir（`.staging-*` は除く）が存在し、nonce 一意性を確実には
    判定できないことを示す。従来はそのような dir を単に無視していた（「この
    1 件は判定材料にならない」という設計だったが、その dir 自体が実は同じ
    nonce で公開されていた場合に検出できず、一回性保証が静かに破れる穴
    だった）。呼び出し側は `uninspectable_dirs` が非空なら
    `existing_campaign_id` の値に関わらず freeze を拒否しなければならない
    （fail-closed: 「見つからなかった」を「安全に見つからなかった」と混同
    しない）。
    """

    existing_campaign_id: str | None
    uninspectable_dirs: tuple[str, ...]


def _find_existing_nonce_usage(campaigns_dir: Path, nonce: str) -> NonceRegistryScan:
    """PR レビュー第 5 巡: 承認の一回性。`campaigns_dir` 配下の公開済み
    campaign の `c0_manifest.json` を走査し、同じ `authorization_nonce` を
    持つものがあればその campaign_id を `existing_campaign_id` として返す。

    第 12 巡採用: manifest が欠落・読取不能・JSON 不正・
    `authorization_nonce` 欄欠落の campaign dir は、もはや黙って無視しない
    — `uninspectable_dirs` へ個別に記録して呼び出し側へ fail-closed な
    拒否を促す（`NonceRegistryScan` docstring 参照）。`.staging-*` は
    従来どおり対象外（公開済み campaign ではないため）。走査は全 entry を
    見終えるまで続ける（先に match が見つかっても、後続の uninspectable な
    entry を見逃さないため）。
    """
    campaigns_dir = Path(campaigns_dir)
    if not campaigns_dir.exists():
        return NonceRegistryScan(existing_campaign_id=None, uninspectable_dirs=())
    uninspectable: list[str] = []
    existing_campaign_id: str | None = None
    for entry in sorted(campaigns_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".staging-"):
            continue
        manifest_path = entry / "c0_manifest.json"
        if not manifest_path.is_file():
            uninspectable.append(entry.name)
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            uninspectable.append(entry.name)
            continue
        if not isinstance(data, Mapping) or not isinstance(data.get("authorization_nonce"), str):
            uninspectable.append(entry.name)
            continue
        if data["authorization_nonce"] == nonce and existing_campaign_id is None:
            existing_campaign_id = entry.name
    return NonceRegistryScan(
        existing_campaign_id=existing_campaign_id, uninspectable_dirs=tuple(uninspectable)
    )


@dataclass(frozen=True)
class ArmedFreezeResult:
    outcome: FreezeOutcome
    campaign_id: str | None
    manifest_core_sha: str | None
    manifest_sha: str | None
    campaign_dir: Path | None
    secret_dir: Path | None
    detail: str
    gate2_arming: ArmingDecision | None = None
    validation: c0_validate.C0ValidationResult | None = None


def armed_freeze(
    repo_root: Path,
    *,
    cli_armed: bool,
    env: Mapping[str, str],
    approval_dir: Path,
    secret_dir: Path,
    campaigns_dir: Path,
    e_use_table_path: Path | None = None,
) -> ArmedFreezeResult:
    """武装 C0 freeze。副作用なしで拒否する経路が 3 つ（AUTHORIZATION_REQUIRED /
    MANIFEST_CORE_SHA_MISMATCH / VALIDATION_BLOCKED）、staging→read-back→
    `os.replace` の atomic 公開経路が 1 つ（失敗時は staging を全削除し何も
    公開しない。secret も残さない）。

    **公開順は secret dir → campaign dir に固定**（PR レビュー第 2 巡）。
    campaign dir の rename が失敗した場合、公開済み secret dir を削除して
    「何も公開されていない」状態へロールバックする。

    承認ファイルは本関数内で `load_all_approvals()` を **1 回だけ** 呼び、
    その結果を `check_armed()`/manifest 構築/freeze event の全てへ使い回す
    （PR レビュー第 6 巡 #5: 同一承認ファイルの二重読み排除）。
    """
    root = Path(repo_root)

    all_approvals = load_all_approvals(approval_dir, repo_root=root)
    gate2_arming = check_armed(
        Gate.GATE2_C0_FREEZE, cli_armed, env, approval_dir, repo_root=root,
        preloaded=all_approvals,
    )
    if not gate2_arming.armed:
        return ArmedFreezeResult(
            outcome=FreezeOutcome.AUTHORIZATION_REQUIRED,
            campaign_id=None,
            manifest_core_sha=None,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail="missing factors: " + "; ".join(gate2_arming.missing_factors),
            gate2_arming=gate2_arming,
        )

    core_manifest = build_manifest(root, approvals=all_approvals, campaign_date_utc=_today_utc())

    # E_use evidence table (Part A/D1b, `[UNDERSPEC-CAL-D10]`): `_check_e_use_table()`
    # reads `table_path` exactly once and hands back those same bytes — reused
    # below for both the sha256 pin and the staged copy (bug fix P2 #1: this used
    # to call `table_path.read_bytes()` a second time, so the bytes that were
    # actually validated and the bytes that ended up pinned/staged could differ
    # if the file was swapped in between). `e_use_table_bytes is None` implies
    # `e_use_violations` is non-empty, so `validation.is_blocked` further below
    # will already refuse the freeze before staging is ever attempted.
    #
    # 第 12 巡採用: this must run — and `frozen_inputs.e_use_table_sha256` must
    # be attached to `core_manifest` — *before* `manifest_core_sha()` is
    # computed just below, since `frozen_inputs` is now part of the *core*
    # payload (see `_CORE_ONLY_EXCLUDED_KEYS` docstring). This is what makes a
    # table swapped in between the `dry_run()` Gate 2 pinned and this
    # `armed_freeze()` call surface as `MANIFEST_CORE_SHA_MISMATCH` below,
    # rather than silently freezing a different table than the one Gate 2
    # actually approved.
    table_path = (
        e_use_table_path if e_use_table_path is not None else default_e_use_table_path(root)
    )
    e_use_violations, e_use_table_bytes = _check_e_use_table(
        table_path, gate1_e_use_bound_accepted=_gate1_e_use_bound_accepted(all_approvals)
    )
    e_use_table_sha256 = (
        hashlib.sha256(e_use_table_bytes).hexdigest() if e_use_table_bytes is not None else None
    )
    core_manifest["frozen_inputs"] = {
        "e_use_table_sha256": e_use_table_sha256 or "ABSENT:e_use_table_invalid"
    }

    core_sha = manifest_core_sha(core_manifest)

    gate2_record = gate2_arming.approval
    if gate2_record is None or gate2_record.manifest_core_sha != core_sha:
        declared = gate2_record.manifest_core_sha if gate2_record is not None else None
        return ArmedFreezeResult(
            outcome=FreezeOutcome.MANIFEST_CORE_SHA_MISMATCH,
            campaign_id=None,
            manifest_core_sha=core_sha,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail=(
                f"Gate 2 approval pins manifest_core_sha={declared!r}, freshly built "
                f"manifest has {core_sha!r}; refusing (approval is for a different manifest)"
            ),
            gate2_arming=gate2_arming,
        )

    campaign_id = campaign_id_for(core_manifest)

    nonce = gate2_record.authorization_nonce
    if nonce is None:
        # Shape validation in `_parse_gate2_payload` already requires this field
        # on any *approved* record, so this is defensive fail-closed only.
        return ArmedFreezeResult(
            outcome=FreezeOutcome.AUTHORIZATION_REQUIRED,
            campaign_id=None,
            manifest_core_sha=core_sha,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail="Gate 2 approval is missing authorization_nonce",
            gate2_arming=gate2_arming,
        )
    early_nonce_scan = _find_existing_nonce_usage(campaigns_dir, nonce)
    if early_nonce_scan.uninspectable_dirs:
        # 第 12 巡採用: nonce 一意性を保証できない registry 状態は
        # fail-closed で拒否する（`existing_campaign_id` の有無に関わらず —
        # uninspectable な dir 自体が同じ nonce を使っていた可能性を排除
        # できない）。
        return ArmedFreezeResult(
            outcome=FreezeOutcome.NONCE_REGISTRY_UNINSPECTABLE,
            campaign_id=None,
            manifest_core_sha=core_sha,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail=(
                "nonce_registry_uninspectable: cannot verify authorization_nonce "
                "uniqueness — campaign dir(s) with missing/unreadable/malformed/"
                f"nonce-less manifest: {list(early_nonce_scan.uninspectable_dirs)!r}; "
                "refusing"
            ),
            gate2_arming=gate2_arming,
        )
    if early_nonce_scan.existing_campaign_id is not None:
        return ArmedFreezeResult(
            outcome=FreezeOutcome.NONCE_ALREADY_USED,
            campaign_id=None,
            manifest_core_sha=core_sha,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail=(
                f"authorization_nonce already used by published campaign "
                f"{early_nonce_scan.existing_campaign_id!r}; refusing (one-time-use authorization)"
            ),
            gate2_arming=gate2_arming,
        )

    approval_digests = {
        f"{GATE_SHORT_NAME[gate]}_sha256": result.content_sha256
        for gate, result in all_approvals.items()
        if gate in (Gate.GATE1_CAMPAIGN_EXECUTION, Gate.GATE2_C0_FREEZE)
        and result.approved
        and result.content_sha256 is not None
    }

    split_secret = secrets.token_bytes(32)
    render_root_secret = secrets.token_bytes(32)
    commitments = {
        "split_secret_sha256": hashlib.sha256(split_secret).hexdigest(),
        "render_root_secret_sha256": hashlib.sha256(render_root_secret).hexdigest(),
    }

    # Realize the split *before* assembling the full manifest: §7 requires the
    # realized row->split table to be inlined into the manifest itself (PR
    # review round 4), so `full_manifest` cannot be built until this exists.
    matrix_rows = build_matrix()
    row_inputs = _row_inputs_for_split(matrix_rows, STRATUM_FACTOR_NAMES)
    realized = realize_split(row_inputs, split_secret, STRATUM_FACTOR_NAMES)
    realized_split_dict = _realized_split_to_dict(realized)

    full_manifest = _attach_freeze_extras(
        core_manifest,
        campaign_id=campaign_id,
        authorization_nonce=nonce,
        approval_digests=approval_digests,
        commitments=commitments,
        realized_split=realized_split_dict,
        realized_split_sha=realized.realized_sha,
    )
    full_sha = _full_manifest_sha(full_manifest)

    validation = c0_validate.validate_c0_manifest(full_manifest)
    validation = _merge_e_use_table_violations(validation, e_use_violations)
    frozen_design = core_manifest.get("frozen_design")
    max_claim_scope_value = (
        frozen_design.get("max_claim_scope") if isinstance(frozen_design, Mapping) else None
    )
    validation = _merge_e_use_table_violations(
        validation, _check_max_claim_scope(max_claim_scope_value)
    )
    if validation.is_blocked:
        return ArmedFreezeResult(
            outcome=FreezeOutcome.VALIDATION_BLOCKED,
            campaign_id=campaign_id,
            manifest_core_sha=core_sha,
            manifest_sha=full_sha,
            campaign_dir=None,
            secret_dir=None,
            detail="blocked_codes=" + ",".join(c.value for c in validation.blocked_codes),
            gate2_arming=gate2_arming,
            validation=validation,
        )
    # `validation.is_blocked` above would already have returned if the E_use
    # table failed to load/validate, so this bytes buffer is guaranteed
    # populated past this point (defensive assert only — never expected to fire).
    if e_use_table_bytes is None:
        raise AssertionError(
            "unreachable: e_use_table validation must have blocked before this point"
        )

    freeze_event_payload = {
        "kind": "c0_freeze",
        "campaign_id": campaign_id,
        "manifest_sha": full_sha,
        "manifest_core_sha": core_sha,
        "realized_split_sha": realized.realized_sha,
        "e_use_table_sha256": e_use_table_sha256,
        "commitments": dict(commitments),
        "approvals": dict(approval_digests),
        "event_time_utc": datetime.now(timezone.utc).isoformat(),
    }

    campaigns_dir = Path(campaigns_dir)
    secret_dir = Path(secret_dir)
    # 第 11 巡採用: staging パスをこの呼び出し (invocation) に一意な token で
    # 名前空間化する。同じ campaign_id を狙う 2 プロセスが同時に staging を
    # 構築した場合（同一 nonce の異なる 2 approval、あるいはリトライ）、旧
    # 実装は両者とも `.staging-<campaign_id>` という**同一パス**を使っていた
    # ため、一方の staging 書込み中/失敗時のロールバック（`_rmtree_if_exists`）
    # が **他プロセスの** staging を巻き添えで削除しうる（他プロセスの secret
    # が消える、または `mkdir(..., exist_ok=False)` で早期に衝突する）。
    # `invocation_token`（`secrets.token_hex(8)`, manifest 内容とは無関係な
    # 使い捨て乱数）を両側の staging パスへ付与することで、各呼び出しが
    # 自分専用の staging root を持つようにし、ロールバックが常に「自分が
    # 作った staging」だけを対象にすることを構造的に保証する
    # （`.staging-` prefix は変わらないため `detect_orphans()`/
    # `_find_existing_nonce_usage()` の `.staging-*` 除外はそのまま機能する）。
    invocation_token = secrets.token_hex(8)
    campaign_staging = campaigns_dir / f".staging-{campaign_id}-{invocation_token}"
    secret_staging = secret_dir / f".staging-{campaign_id}-{invocation_token}"
    campaign_final = campaigns_dir / campaign_id
    secret_final = secret_dir / campaign_id

    try:
        # bug fix 第10巡 #1: the inner `try`/`except OSError` below is the
        # normal, expected-failure path for staging construction (mkdir/
        # write/chmod failures) and keeps returning `PUBLICATION_FAILED` with
        # a specific detail exactly as before (`return` inside a `try` does
        # not raise, so it never reaches the outer handler). Anything else —
        # most importantly `KeyboardInterrupt`/`SystemExit` landing partway
        # through staging construction (e.g. right after the secret files are
        # written) — used to bypass cleanup entirely (only `OSError` was
        # caught), leaving a `.staging-<campaign_id>` dir behind that can
        # contain generated secret bytes. Orphan maintenance
        # (`detect_orphans()`) never touches `.staging-*` dirs
        # (`_published_ids()` excludes them by construction — they are never
        # "published"), so such a leftover would linger forever. Both staging
        # roots are always freshly created by *this* call
        # (`mkdir(..., exist_ok=False)` just below — a pre-existing dir at
        # either path would already have raised `OSError` and been handled
        # by the inner branch), so unconditional cleanup here is always safe.
        try:
            campaign_staging.mkdir(parents=True, exist_ok=False)
            secret_staging.mkdir(parents=True, exist_ok=False)
            os.chmod(secret_staging, 0o700)

            (campaign_staging / "c0_manifest.json").write_text(
                canonical_json(full_manifest), encoding="utf-8"
            )
            (campaign_staging / "realized_split.json").write_text(
                canonical_json(realized_split_dict), encoding="utf-8"
            )
            Ledger(campaign_staging / "ledger.jsonl").append(freeze_event_payload)
            (campaign_staging / "e_use_table.json").write_bytes(e_use_table_bytes)

            _write_secret_file(secret_staging / "split_secret.bin", split_secret)
            _write_secret_file(secret_staging / "render_root_secret.bin", render_root_secret)
        except OSError as exc:
            _rmtree_if_exists(campaign_staging)
            _rmtree_if_exists(secret_staging)
            return ArmedFreezeResult(
                outcome=FreezeOutcome.PUBLICATION_FAILED,
                campaign_id=campaign_id,
                manifest_core_sha=core_sha,
                manifest_sha=full_sha,
                campaign_dir=None,
                secret_dir=None,
                detail=f"staging write failed: {exc}",
                gate2_arming=gate2_arming,
                validation=validation,
            )
    except BaseException:
        # Restore "nothing staged" for every exception type (not just
        # OSError) and re-raise so interrupts/exits still propagate.
        _rmtree_if_exists(campaign_staging)
        _rmtree_if_exists(secret_staging)
        raise

    ok, detail = _readback_verify(
        campaign_staging,
        secret_staging,
        row_inputs,
        split_secret,
        render_root_secret,
        commitments,
        gate1_e_use_bound_accepted=_gate1_e_use_bound_accepted(all_approvals),
    )
    if not ok:
        _rmtree_if_exists(campaign_staging)
        _rmtree_if_exists(secret_staging)
        return ArmedFreezeResult(
            outcome=FreezeOutcome.PUBLICATION_FAILED,
            campaign_id=campaign_id,
            manifest_core_sha=core_sha,
            manifest_sha=full_sha,
            campaign_dir=None,
            secret_dir=None,
            detail=f"read-back verification failed: {detail}",
            gate2_arming=gate2_arming,
            validation=validation,
        )

    # PUBLISH: secret dir first, then campaign dir (PR review round 2), under
    # nested locks (bug fix P2 #3). `campaigns_dir/.publish.lock` is the
    # *authoritative* lock — `campaigns_dir` is the shared campaign registry
    # every process necessarily agrees on, whereas `secret_dir` is a
    # caller-selected value that can legitimately differ between two
    # processes racing to freeze against the same `campaigns_dir`; keying the
    # nonce-uniqueness decision off `secret_dir` alone would let such
    # processes acquire unrelated locks and both publish. `secret_dir` keeps
    # its own lock nested inside as a secondary lock (still serializes
    # writers to that particular secret dir). `detect_orphans()` takes both
    # locks in the same nesting order. `blocking=True` (default): `acquired`
    # is always True barring an OS-level error opening/locking the lock file,
    # handled defensively below at both levels.
    with _publish_lock(campaigns_dir) as campaigns_acquired:
        if not campaigns_acquired:
            _rmtree_if_exists(campaign_staging)
            _rmtree_if_exists(secret_staging)
            return ArmedFreezeResult(
                outcome=FreezeOutcome.PUBLICATION_FAILED,
                campaign_id=campaign_id,
                manifest_core_sha=core_sha,
                manifest_sha=full_sha,
                campaign_dir=None,
                secret_dir=None,
                detail="could not acquire campaigns_dir publish lock",
                gate2_arming=gate2_arming,
                validation=validation,
            )

        with _publish_lock(secret_dir) as acquired:
            if not acquired:
                _rmtree_if_exists(campaign_staging)
                _rmtree_if_exists(secret_staging)
                return ArmedFreezeResult(
                    outcome=FreezeOutcome.PUBLICATION_FAILED,
                    campaign_id=campaign_id,
                    manifest_core_sha=core_sha,
                    manifest_sha=full_sha,
                    campaign_dir=None,
                    secret_dir=None,
                    detail="could not acquire secret_dir publish lock",
                    gate2_arming=gate2_arming,
                    validation=validation,
                )

            # PR review round 6 #4: the early nonce-uniqueness check above
            # (before this lock was acquired) is a best-effort fast rejection
            # only — it cannot see a sibling process that published the same
            # nonce in the TOCTOU window between that check and reaching here.
            # This is the authoritative recheck, performed only once this
            # process is the sole holder of `campaigns_dir/.publish.lock`
            # (bug fix P2 #3: previously keyed off `secret_dir/.publish.lock`
            # only, which two processes sharing `campaigns_dir` but passing
            # different `secret_dir` values could each acquire independently
            # — the same lock `detect_orphans()` and both `os.replace()` calls
            # below use), so no concurrent publisher can race it.
            locked_nonce_scan = _find_existing_nonce_usage(campaigns_dir, nonce)
            if locked_nonce_scan.uninspectable_dirs:
                # 第 12 巡採用: 同じ fail-closed 拒否をロック内の権威的な
                # 再チェックでも行う（早期チェック通過後にレジストリが
                # uninspectable な状態へ変わった TOCTOU も塞ぐ）。
                _rmtree_if_exists(campaign_staging)
                _rmtree_if_exists(secret_staging)
                return ArmedFreezeResult(
                    outcome=FreezeOutcome.NONCE_REGISTRY_UNINSPECTABLE,
                    campaign_id=None,
                    manifest_core_sha=core_sha,
                    manifest_sha=full_sha,
                    campaign_dir=None,
                    secret_dir=None,
                    detail=(
                        "nonce_registry_uninspectable: cannot verify authorization_nonce "
                        "uniqueness — campaign dir(s) with missing/unreadable/malformed/"
                        f"nonce-less manifest: {list(locked_nonce_scan.uninspectable_dirs)!r}; "
                        "refusing (detected by locked recheck)"
                    ),
                    gate2_arming=gate2_arming,
                    validation=validation,
                )
            if locked_nonce_scan.existing_campaign_id is not None:
                _rmtree_if_exists(campaign_staging)
                _rmtree_if_exists(secret_staging)
                return ArmedFreezeResult(
                    outcome=FreezeOutcome.NONCE_ALREADY_USED,
                    campaign_id=None,
                    manifest_core_sha=core_sha,
                    manifest_sha=full_sha,
                    campaign_dir=None,
                    secret_dir=None,
                    detail=(
                        f"authorization_nonce already used by published campaign "
                        f"{locked_nonce_scan.existing_campaign_id!r}; refusing (one-time-use authorization, "
                        "detected by locked recheck)"
                    ),
                    gate2_arming=gate2_arming,
                    validation=validation,
                )

            (secret_staging / _PUBLISHING_MARKER_NAME).write_text("", encoding="utf-8")
            # bug fix P2 #2 (第 9 巡改訂): track, per rename, whether *this*
            # call is the one that actually published `secret_final`/
            # `campaign_final` — the `except BaseException` handler below must
            # roll back only destinations this invocation itself created,
            # never a pre-existing (already-valid) campaign/secret dir that
            # happens to share the same final path (e.g. `campaign_id`
            # collides with something already published out-of-band). Without
            # this tracking, an interrupt landing *before* either rename even
            # ran would otherwise `_rmtree_if_exists()` — and delete — dirs
            # this call never touched.
            secret_final_published_by_this_call = False
            campaign_final_published_by_this_call = False
            try:
                # This outer `try`/`except BaseException` is the authoritative
                # rollback for the publication transaction. The two
                # `except OSError` clauses immediately below remain the
                # normal, expected-failure path (they return
                # `PUBLICATION_FAILED` with a specific detail message and
                # never reach the outer handler, since `return` inside a
                # `try` does not raise). Anything else — most importantly
                # `KeyboardInterrupt`/`SystemExit` landing before/between the
                # secret-side rename and the campaign-side rename — used to
                # bypass rollback entirely (only `OSError` was caught), which
                # could leave a published secret dir with no matching
                # campaign dir. The outer handler below restores "nothing
                # published *by this call*" for every exception type and then
                # re-raises, so interrupts/exits still propagate.
                try:
                    os.replace(str(secret_staging), str(secret_final))
                    secret_final_published_by_this_call = True
                except OSError as exc:
                    _rmtree_if_exists(secret_staging)
                    _rmtree_if_exists(campaign_staging)
                    return ArmedFreezeResult(
                        outcome=FreezeOutcome.PUBLICATION_FAILED,
                        campaign_id=campaign_id,
                        manifest_core_sha=core_sha,
                        manifest_sha=full_sha,
                        campaign_dir=None,
                        secret_dir=None,
                        detail=f"secret publish failed: {exc}",
                        gate2_arming=gate2_arming,
                        validation=validation,
                    )

                try:
                    os.replace(str(campaign_staging), str(campaign_final))
                    campaign_final_published_by_this_call = True
                except OSError as exc:
                    # Secret already published *by this call*; roll back to
                    # "nothing published" so the two publications never
                    # disagree (no orphan secret dir left behind).
                    _rmtree_if_exists(secret_final)
                    _rmtree_if_exists(campaign_staging)
                    return ArmedFreezeResult(
                        outcome=FreezeOutcome.PUBLICATION_FAILED,
                        campaign_id=campaign_id,
                        manifest_core_sha=core_sha,
                        manifest_sha=full_sha,
                        campaign_dir=None,
                        secret_dir=None,
                        detail=f"campaign publish failed: {exc} (secret publish rolled back)",
                        gate2_arming=gate2_arming,
                        validation=validation,
                    )

                # Both roots published and paired: the in-flight marker is no
                # longer needed.
                marker = secret_final / _PUBLISHING_MARKER_NAME
                if marker.exists():
                    marker.unlink()
            except BaseException:
                if campaign_final_published_by_this_call:
                    # Both roots were fully, successfully published by *this*
                    # call before the exception landed (e.g. during the
                    # trailing marker cleanup) — that is a valid,
                    # self-consistent publication. Undoing it would destroy a
                    # real, already-verified publish just because a cosmetic
                    # cleanup step was interrupted; leave it published (any
                    # leftover marker is tidied up later by
                    # `detect_orphans()`) and simply propagate.
                    raise
                if secret_final_published_by_this_call:
                    # Secret published by this call, campaign not yet: a
                    # genuine partial state — roll back the secret dir this
                    # call itself just created (never a pre-existing one).
                    _rmtree_if_exists(secret_final)
                # Staging dirs are always created fresh by this call
                # (`mkdir(..., exist_ok=False)` above), so removing them is
                # always safe regardless of which stage the exception landed
                # in.
                _rmtree_if_exists(secret_staging)
                _rmtree_if_exists(campaign_staging)
                raise

    return ArmedFreezeResult(
        outcome=FreezeOutcome.PUBLISHED,
        campaign_id=campaign_id,
        manifest_core_sha=core_sha,
        manifest_sha=full_sha,
        campaign_dir=campaign_final,
        secret_dir=secret_final,
        detail="published",
        gate2_arming=gate2_arming,
        validation=validation,
    )


@dataclass(frozen=True)
class OrphanReport:
    """`detect_orphans()` の戻り値。`orphan_campaign_ids` は削除しない
    （runner が fail-closed で実行拒否する対象として報告するのみ）。
    `deleted_orphan_secret_ids` は本関数自身が削除した孤児 secret dir。"""

    orphan_campaign_ids: tuple[str, ...]
    deleted_orphan_secret_ids: tuple[str, ...]


def _published_ids(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {p.name for p in directory.iterdir() if p.is_dir() and not p.name.startswith(".staging-")}


def detect_orphans(secret_dir: Path, campaigns_dir: Path) -> OrphanReport:
    """公開済み campaign dir / secret dir の対応関係の破れを検出する
    （PR レビュー第 2〜4 巡: 二根公開の回復可能性）。`armed_freeze()` の二根
    公開と同じネストしたロック（bug fix P2 #3: `campaigns_dir/.publish.lock`
    が authoritative、`secret_dir/.publish.lock` が secondary）を**非
    blocking** で取得する: どちらか一方でも取得できなければ「生きた公開処理が
    進行中かもしれない」とみなし、何もせず即座に空の `OrphanReport` を返す
    （ブロック/スキップ。fail-safe: 誤って進行中の公開を孤児と誤認して
    壊さない）。`campaigns_dir` は本関数自身が registry（`_published_ids()`）
    を読む対象そのものであり、`armed_freeze()` と同じ authoritative ロックを
    取らなければ「生きた公開処理と同時に registry を読んでしまう」TOCTOU を
    防げないため、単独では取らず必ず `campaigns_dir` ロックの内側で
    `secret_dir` ロックを取る。

    両ロックを取得できた場合、それ自体が「生きた公開処理は存在しない」ことの
    証明になる（`armed_freeze()` は公開区間全体でこの 2 つのロックを保持し
    続けるため）。したがって:

    - campaign dir があり対応する secret dir が無い → fail-closed 報告のみ
      （runner はこの campaign_id を実行拒否すべき。本関数は削除しない）。
    - secret dir と対応する campaign dir が **両方ある** のに `.publishing`
      マーカーが残っている（両根とも公開済みだが、マーカー削除だけが未了
      だった正常系）→ マーカーのみを取り除く（dir 自体は削除しない）。
    - secret dir のみで対応する campaign dir が無い → 孤児。ロックを取得
      できた時点で「生きた公開の途中」ではあり得ない（`.publishing` の有無に
      関わらず stale）ため **削除する**（副作用あり。secret は campaign 抜き
      では無意味であり、漏洩面を縮小するため積極的に消す）。
    - `.staging-*` ディレクトリはそもそも `_published_ids()` が除外する
      （公開済み扱いにしない）。
    """
    secret_dir = Path(secret_dir)
    campaigns_dir = Path(campaigns_dir)
    with _publish_lock(campaigns_dir, blocking=False) as campaigns_acquired:
        if not campaigns_acquired:
            return OrphanReport(orphan_campaign_ids=(), deleted_orphan_secret_ids=())

        with _publish_lock(secret_dir, blocking=False) as acquired:
            if not acquired:
                return OrphanReport(orphan_campaign_ids=(), deleted_orphan_secret_ids=())

            campaign_ids = _published_ids(campaigns_dir)
            secret_ids = _published_ids(secret_dir)
            orphan_campaigns = tuple(sorted(campaign_ids - secret_ids))

            # Paired ids with a leftover marker: publish completed, just tidy up.
            for sid in sorted(secret_ids & campaign_ids):
                marker = secret_dir / sid / _PUBLISHING_MARKER_NAME
                if marker.exists():
                    marker.unlink()

            # Unpaired secret dirs: stale by construction (see docstring above).
            candidate_orphan_secrets = sorted(secret_ids - campaign_ids)
            deleted: list[str] = []
            for sid in candidate_orphan_secrets:
                deleted.append(sid)
                _rmtree_if_exists(secret_dir / sid)

            return OrphanReport(
                orphan_campaign_ids=orphan_campaigns, deleted_orphan_secret_ids=tuple(deleted)
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voice_genesis.calibration.c0_freeze",
        description=(
            "C0 freeze manifest producer. Default (no --armed) is dry-run: "
            "builds and validates the manifest, writes nothing."
        ),
    )
    parser.add_argument("--armed", action="store_true", default=False)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--approval-dir", type=Path, default=None)
    parser.add_argument("--secret-dir", type=Path, default=None)
    parser.add_argument("--campaigns-dir", type=Path, default=None)
    parser.add_argument(
        "--e-use-table-path",
        type=Path,
        default=None,
        help="override the E_use evidence table path (default: "
        f"{DEFAULT_E_USE_TABLE_RELATIVE_PATH!r} repo-relative)",
    )
    parser.add_argument(
        "--maintenance-orphans",
        action="store_true",
        default=False,
        help=(
            "Run orphan campaign/secret directory detection + stale-secret cleanup "
            "only, then exit (no manifest build, no freeze, no authorization needed). "
            "PR review round 6 #2: this replaces the old default-dry-run behavior of "
            "always scanning for orphans (which had the side effect of creating "
            "secret_dir/its lock file on every plain dry-run invocation)."
        ),
    )
    return parser


def _print_orphan_report(orphans: OrphanReport) -> None:
    if orphans.orphan_campaign_ids:
        print(
            "WARNING orphan campaign dir(s) with no matching secret dir "
            f"(refuse to run): {list(orphans.orphan_campaign_ids)}"
        )
    if orphans.deleted_orphan_secret_ids:
        print(f"deleted orphan secret dir(s): {list(orphans.deleted_orphan_secret_ids)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    root = args.repo_root if args.repo_root is not None else _REPO_ROOT
    approval_dir = (
        args.approval_dir if args.approval_dir is not None else default_approval_dir()
    )
    secret_dir = args.secret_dir if args.secret_dir is not None else default_secret_dir()
    campaigns_dir = (
        args.campaigns_dir if args.campaigns_dir is not None else default_campaigns_dir(root)
    )
    e_use_table_path = args.e_use_table_path

    if args.maintenance_orphans and not args.armed:
        # PR review round 6 #2: the only other place `detect_orphans()` runs is
        # inside the armed path below (after a real freeze attempt is actually
        # authorized) — plain `dry-run` (no flags) never touches secret_dir.
        _print_orphan_report(detect_orphans(secret_dir, campaigns_dir))
        return 0

    if not args.armed:
        # Side-effect-free: builds/validates the manifest only. Does NOT call
        # `detect_orphans()` (PR review round 6 #2) — that creates
        # `secret_dir`/its lock file as a side effect, which a plain dry-run
        # must never do (tests assert this).
        report = dry_run(root, approval_dir, os.environ, cli_armed=False, e_use_table_path=e_use_table_path)
        print(f"manifest_core_sha: {report.manifest_core_sha}")
        print(f"campaign_id (if frozen today): {report.campaign_id}")
        print(f"authorization_nonce: {report.authorization_nonce}")
        _frozen_design = report.manifest.get("frozen_design")
        _max_claim_scope = (
            _frozen_design.get("max_claim_scope")
            if isinstance(_frozen_design, Mapping)
            else None
        )
        print(f"max_claim_scope: {_max_claim_scope}")
        _frozen_inputs = report.manifest.get("frozen_inputs")
        _e_use_table_sha256 = (
            _frozen_inputs.get("e_use_table_sha256")
            if isinstance(_frozen_inputs, Mapping)
            else None
        )
        print(f"e_use_table_sha256: {_e_use_table_sha256}")
        print(f"blocked_codes: {[c.value for c in report.validation.blocked_codes]}")
        print(f"missing_required_keys: {list(report.validation.missing_required_keys)}")
        print(f"gate2.armed: {report.gate2_arming.armed}")
        if not report.gate2_arming.armed:
            print(f"gate2.missing_factors: {list(report.gate2_arming.missing_factors)}")
        return 0 if not report.validation.is_blocked else 1

    result = armed_freeze(
        root,
        cli_armed=True,
        env=os.environ,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
        e_use_table_path=e_use_table_path,
    )
    if result.outcome != FreezeOutcome.AUTHORIZATION_REQUIRED:
        # Orphan maintenance is part of the armed path only (PR review round 6
        # #2), and only once real authorization was actually established —
        # never for a rejected/unauthorized --armed attempt.
        _print_orphan_report(detect_orphans(secret_dir, campaigns_dir))
    print(f"outcome: {result.outcome.value}")
    print(f"detail: {result.detail}")
    if result.campaign_id:
        print(f"campaign_id: {result.campaign_id}")
    return 0 if result.outcome == FreezeOutcome.PUBLISHED else 1


if __name__ == "__main__":
    raise SystemExit(main())
