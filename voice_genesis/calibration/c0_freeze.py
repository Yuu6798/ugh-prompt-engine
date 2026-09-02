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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from voice_genesis.calibration import c0_validate, streams, vocab
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

    `cost_caps`/`stop_rules` は Gate 1 承認が無ければ `"ABSENT:GATE1_NOT_APPROVED"`
    （REQUIRED_BLOCKING の型検査に違反し正しく BLOCK される。IMPLEMENTATION_MAP
    §6.3: 「validator will BLOCK — correct pre-approval」）。
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
    else:
        cost_caps_section = "ABSENT:GATE1_NOT_APPROVED"
        stop_rules_section = "ABSENT:GATE1_NOT_APPROVED"

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
        },
        "independence_ledger": {
            c.candidate_id: c.independence_tier.value for c in registry.ALL_CANDIDATES
        },
        "rng_ledger": _declared_rng_ledger(),
        "env": _env_section(),
    }
    return manifest


#: `build_manifest()` の core 出力には決して現れないが、armed freeze が
#: full/frozen manifest へ後付けする 6 節（PR レビュー第 4/5 巡:
#: manifest_core_sha の定義精緻化 + 承認の一回性）。`core_payload()` は
#: これらを取り除いた「事前承認 payload」を返す — Gate 2 承認はこの payload
#: の sha (`manifest_core_sha`) を束縛する。`authorization_nonce` も
#: `dry_run()` が呼び出しごとに新規発行する乱数でありコード自身とは無関係
#: なため、core payload から除く（さもなくば `manifest_core_sha` が dry-run
#: の呼び出しごとに変わってしまい determinism が壊れる）。
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
    ここで追加される 6 節は `core_payload()`/`_CORE_ONLY_EXCLUDED_KEYS` と
    1 対 1 で対応する。`authorization_nonce` は Gate 2 承認ファイルに記録
    された値（`check_armed()` が既に Gate 1 との一致を検証済み）で、
    `armed_freeze()` の一回性チェックが後続の freeze 試行を拒否するために
    使う（PR レビュー第 5 巡）。"""
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
) -> FreezeReport:
    """manifest を生成・検証して報告するだけ（書込なし・secret なし）。"""
    root = Path(repo_root)
    all_approvals = load_all_approvals(approval_dir, repo_root=root)
    manifest = build_manifest(root, approvals=all_approvals, campaign_date_utc=_today_utc())
    core_sha = manifest_core_sha(manifest)
    campaign_id = campaign_id_for(manifest)
    validation = c0_validate.validate_c0_manifest(manifest)
    gate2_arming = check_armed(
        Gate.GATE2_C0_FREEZE, cli_armed, env, approval_dir, repo_root=root
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
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
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
#: 排他ロック。`secret_dir` 直下に置く（secret_dir は常に存在すると仮定できない
#: ため `mkdir(parents=True, exist_ok=True)` してから開く）。
_PUBLISH_LOCK_NAME = ".publish.lock"


@contextlib.contextmanager
def _publish_lock(secret_dir: Path, *, blocking: bool = True) -> Iterator[bool]:
    """`secret_dir/.publish.lock` 上の排他ロックを取る。`yield` される bool は
    ロックを実際に取得できたか（`blocking=True` では OS レベルの異常が無い限り
    常に `True` — 取得できるまで待つため）。`blocking=False`
    （`detect_orphans()` が使う）はロック競合時に即座に `False` を yield して
    何もせず戻る（PR レビュー第 4 巡: 生きた公開処理と競合させない）。
    """
    secret_dir = Path(secret_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    lock_path = secret_dir / _PUBLISH_LOCK_NAME
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


def _readback_verify(
    campaign_staging: Path,
    secret_staging: Path,
    row_inputs: Sequence[RowInput],
    split_secret_expected: bytes,
) -> tuple[bool, str]:
    try:
        manifest_readback = json.loads(
            (campaign_staging / "c0_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"cannot read back c0_manifest.json: {exc}"
    validation = c0_validate.validate_c0_manifest(manifest_readback)
    if validation.is_blocked:
        return False, f"read-back manifest failed validation: {validation.blocked_codes}"

    try:
        split_raw = json.loads((campaign_staging / "realized_split.json").read_text(encoding="utf-8"))
        realized_readback = _realized_split_from_dict(split_raw)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return False, f"cannot read back realized_split.json: {exc}"

    try:
        secret_bytes = (secret_staging / "split_secret.bin").read_bytes()
    except OSError as exc:
        return False, f"cannot read back split_secret.bin: {exc}"
    if secret_bytes != split_secret_expected:
        return False, "read-back split_secret.bin does not match generated secret"

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
    VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    PUBLISHED = "PUBLISHED"


def _find_existing_nonce_usage(campaigns_dir: Path, nonce: str) -> str | None:
    """PR レビュー第 5 巡: 承認の一回性。`campaigns_dir` 配下の公開済み
    campaign の `c0_manifest.json` を走査し、同じ `authorization_nonce` を
    持つものがあればその campaign_id を返す（無ければ `None`）。壊れた/
    読めないファイルは無視する（fail-open ではなく単に「この 1 件は判定材料
    にならない」— 他の正当な published campaign が既にヒットしていれば
    そちらで検出される）。"""
    campaigns_dir = Path(campaigns_dir)
    if not campaigns_dir.exists():
        return None
    for entry in sorted(campaigns_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".staging-"):
            continue
        manifest_path = entry / "c0_manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping) and data.get("authorization_nonce") == nonce:
            return entry.name
    return None


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
) -> ArmedFreezeResult:
    """武装 C0 freeze。副作用なしで拒否する経路が 3 つ（AUTHORIZATION_REQUIRED /
    MANIFEST_CORE_SHA_MISMATCH / VALIDATION_BLOCKED）、staging→read-back→
    `os.replace` の atomic 公開経路が 1 つ（失敗時は staging を全削除し何も
    公開しない。secret も残さない）。

    **公開順は secret dir → campaign dir に固定**（PR レビュー第 2 巡）。
    campaign dir の rename が失敗した場合、公開済み secret dir を削除して
    「何も公開されていない」状態へロールバックする。
    """
    root = Path(repo_root)
    gate2_arming = check_armed(Gate.GATE2_C0_FREEZE, cli_armed, env, approval_dir, repo_root=root)
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

    all_approvals = load_all_approvals(approval_dir, repo_root=root)
    core_manifest = build_manifest(root, approvals=all_approvals, campaign_date_utc=_today_utc())
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
    existing_campaign_id = _find_existing_nonce_usage(campaigns_dir, nonce)
    if existing_campaign_id is not None:
        return ArmedFreezeResult(
            outcome=FreezeOutcome.NONCE_ALREADY_USED,
            campaign_id=None,
            manifest_core_sha=core_sha,
            manifest_sha=None,
            campaign_dir=None,
            secret_dir=None,
            detail=(
                f"authorization_nonce already used by published campaign "
                f"{existing_campaign_id!r}; refusing (one-time-use authorization)"
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

    freeze_event_payload = {
        "kind": "c0_freeze",
        "campaign_id": campaign_id,
        "manifest_sha": full_sha,
        "manifest_core_sha": core_sha,
        "realized_split_sha": realized.realized_sha,
        "commitments": dict(commitments),
        "approvals": dict(approval_digests),
        "event_time_utc": datetime.now(timezone.utc).isoformat(),
    }

    campaigns_dir = Path(campaigns_dir)
    secret_dir = Path(secret_dir)
    campaign_staging = campaigns_dir / f".staging-{campaign_id}"
    secret_staging = secret_dir / f".staging-{campaign_id}"
    campaign_final = campaigns_dir / campaign_id
    secret_final = secret_dir / campaign_id

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

    ok, detail = _readback_verify(campaign_staging, secret_staging, row_inputs, split_secret)
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

    # PUBLISH: secret dir first, then campaign dir (PR review round 2), both
    # under the same lock `detect_orphans()` also takes (PR review round 3/4).
    # `blocking=True` (default): `acquired` is always True barring an OS-level
    # error opening/locking the lock file, handled defensively below.
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
                detail="could not acquire publish lock",
                gate2_arming=gate2_arming,
                validation=validation,
            )
        (secret_staging / _PUBLISHING_MARKER_NAME).write_text("", encoding="utf-8")
        try:
            os.replace(str(secret_staging), str(secret_final))
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
        except OSError as exc:
            # Secret already published; roll back to "nothing published" so the
            # two publications never disagree (no orphan secret dir left behind).
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

        # Both roots published and paired: the in-flight marker is no longer needed.
        marker = secret_final / _PUBLISHING_MARKER_NAME
        if marker.exists():
            marker.unlink()

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
    公開と同じ `secret_dir/.publish.lock` を**非 blocking** で取得する:
    取得できなければ「生きた公開処理が進行中かもしれない」とみなし、
    何もせず即座に空の `OrphanReport` を返す（ブロック/スキップ。fail-safe:
    誤って進行中の公開を孤児と誤認して壊さない）。

    ロックを取得できた場合、それ自体が「生きた公開処理は存在しない」ことの
    証明になる（`armed_freeze()` は公開区間全体でこのロックを保持し続ける
    ため）。したがって:

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
    return parser


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

    if not args.armed:
        report = dry_run(root, approval_dir, os.environ, cli_armed=False)
        print(f"manifest_core_sha: {report.manifest_core_sha}")
        print(f"campaign_id (if frozen today): {report.campaign_id}")
        print(f"blocked_codes: {[c.value for c in report.validation.blocked_codes]}")
        print(f"missing_required_keys: {list(report.validation.missing_required_keys)}")
        print(f"gate2.armed: {report.gate2_arming.armed}")
        if not report.gate2_arming.armed:
            print(f"gate2.missing_factors: {list(report.gate2_arming.missing_factors)}")
        orphans = detect_orphans(secret_dir, campaigns_dir)
        if orphans.orphan_campaign_ids:
            print(
                "WARNING orphan campaign dir(s) with no matching secret dir "
                f"(refuse to run): {list(orphans.orphan_campaign_ids)}"
            )
        if orphans.deleted_orphan_secret_ids:
            print(f"deleted orphan secret dir(s): {list(orphans.deleted_orphan_secret_ids)}")
        return 0 if not report.validation.is_blocked else 1

    result = armed_freeze(
        root,
        cli_armed=True,
        env=os.environ,
        approval_dir=approval_dir,
        secret_dir=secret_dir,
        campaigns_dir=campaigns_dir,
    )
    print(f"outcome: {result.outcome.value}")
    print(f"detail: {result.detail}")
    if result.campaign_id:
        print(f"campaign_id: {result.campaign_id}")
    return 0 if result.outcome == FreezeOutcome.PUBLISHED else 1


if __name__ == "__main__":
    raise SystemExit(main())
